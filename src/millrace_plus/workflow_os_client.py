"""Pinned local OS profiles and bounded, authenticated public router requests."""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import socket
import stat
import time
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from . import catalog
from .integrations.paperclip_records import BridgeError, _origin, safe_path
from .subworkflow_records import (
    ID,
    SHA,
    SubworkflowError,
    _fields,
    canonical,
    mapping,
    public_request_digest,
)

__all__ = [
    "WorkflowOSClient",
    "load_profile",
    "validate_call",
    "validate_capabilities",
    "validate_read",
    "request_body",
]
PROFILE_FORMAT = "millrace-plus-workflow-os-profile/v1"
CALL_FORMAT = "millrace-os-router-call/v2"
HOME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
CALL_FIELDS = {
    "contract",
    "home",
    "request_id",
    "request_digest",
    "instance_id",
    "run_id",
    "source",
    "phase",
    "core_receipt",
    "observed_at",
    "holder_joined",
}


def _utf8_json(raw: bytes, limit: int) -> object:
    """Reject encoding autodetection while retaining exact encoded-byte bounds."""
    if len(raw) > limit:
        raise SubworkflowError("record_too_large")
    try:
        return catalog.strict_json(raw.decode("utf-8"))
    except (UnicodeError, catalog.CatalogError) as exc:
        raise SubworkflowError("invalid_json") from exc


def _one_of(value: object, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _home(value: object) -> str:
    if not isinstance(value, str) or not HOME.fullmatch(value):
        raise SubworkflowError("invalid_os_home")
    return value


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SubworkflowError("invalid_id")
    return value


def load_profile(
    reference: object,
    *,
    home: str | None = None,
) -> dict[str, object]:
    """Verify exact pinned bytes without opening the referenced credential."""
    ref = _fields(reference, {"path", "sha256"})
    if not isinstance(ref["sha256"], str) or not SHA.fullmatch(ref["sha256"]):
        raise SubworkflowError("invalid_os_profile")
    try:
        path = safe_path(ref["path"])
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SubworkflowError("invalid_os_profile")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(65537)
        finally:
            os.close(descriptor)
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise SubworkflowError("os_profile_changed")
        value = _fields(
            _utf8_json(raw, 65536),
            {"format", "os_origin", "os_home", "os_credential_file"},
        )
        selected_home = _home(value["os_home"])
        if value["format"] != PROFILE_FORMAT:
            raise SubworkflowError("invalid_os_profile")
        if home is not None and selected_home != _home(home):
            raise SubworkflowError("os_home_mismatch")
        _origin(value["os_origin"], loopback=True)
        safe_path(value["os_credential_file"])
        return value
    except (BridgeError, OSError, ValueError) as exc:
        raise SubworkflowError("invalid_os_profile") from exc


def _credential(path: object) -> str:
    try:
        descriptor = os.open(
            safe_path(path),
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise SubworkflowError("invalid_os_credential")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                value = _fields(_utf8_json(stream.read(65537), 65536), {"token"})
            token = value["token"]
            if (
                not isinstance(token, str)
                or not 32 <= len(token) <= 8192
                or any(c in token for c in "\r\n\0")
            ):
                raise SubworkflowError("invalid_os_credential")
            return token
        finally:
            os.close(descriptor)
    except (BridgeError, OSError) as exc:
        raise SubworkflowError("invalid_os_credential") from exc


def request_body(home: str, envelope: dict[str, object]) -> bytes:
    raw = canonical({"home": _home(home), "request": envelope})
    if len(raw) > 4096:
        raise SubworkflowError("os_request_limit")
    return raw


def validate_capabilities(value: object, home: str) -> dict[str, object]:
    row = _fields(
        value,
        {
            "contract",
            "home",
            "observation_contracts",
            "holder_joined_supported",
        },
    )
    contracts = row["observation_contracts"]
    if (
        row["contract"] != "millrace-os-router-observation-capabilities/v1"
        or _home(row["home"]) != home
        or not isinstance(contracts, list)
        or any(not isinstance(x, str) or not x for x in contracts)
        or CALL_FORMAT not in contracts
        or row["holder_joined_supported"] is not True
    ):
        raise SubworkflowError("os_v2_unsupported")
    return row


def validate_call(
    value: object, home: str, envelope: dict[str, object]
) -> dict[str, object]:
    """Validate an exact public response, including its original request target."""
    row = _fields(value, CALL_FIELDS)
    if (
        row["contract"] != CALL_FORMAT
        or _home(row["home"]) != home
        or row["request_id"] != _identifier(envelope["request_id"])
        or not _one_of(row["source"], {"os_owned_call", "core_receipt"})
        or not _one_of(
            row["phase"],
            {
                "admission_pending",
                "call_active",
                "call_returned",
                "call_failed",
                "receipt_observed",
            },
        )
        or not isinstance(row["observed_at"], str)
        or len(row["observed_at"]) > 40
        or (row["holder_joined"] is not None and type(row["holder_joined"]) is not bool)
    ):
        raise SubworkflowError("invalid_os_call")
    for name in ("instance_id", "run_id"):
        if row[name] is not None:
            _identifier(row[name])
        if envelope[name] is not None and row[name] != envelope[name]:
            raise SubworkflowError("wrong_os_target")
    sha = row["request_digest"]
    if sha is not None and (not isinstance(sha, str) or not SHA.fullmatch(sha)):
        raise SubworkflowError("invalid_os_call")
    if row["source"] == "os_owned_call":
        if sha != public_request_digest(envelope):
            raise SubworkflowError("wrong_os_digest")
    elif row["holder_joined"] is not None:
        raise SubworkflowError("invalid_os_call")
    if row["core_receipt"] is not None:
        receipt = mapping(row["core_receipt"])
        validate_receipt(receipt, envelope)
        for name in ("instance_id", "run_id"):
            if receipt.get(name) is not None and row[name] != receipt[name]:
                raise SubworkflowError("wrong_os_target")
    canonical(row)
    return row


def validate_receipt(value: object, envelope: dict[str, object]) -> dict[str, object]:
    row = mapping(value)
    if (
        row.get("request_id") != envelope["request_id"]
        or row.get("operation") != envelope["operation"]
        or row.get("request_digest") != public_request_digest(envelope)
        or not _one_of(row.get("status"), {"queued", "applied", "refused"})
    ):
        raise SubworkflowError("invalid_core_receipt")
    if row.get("plane_id") != envelope["plane_id"]:
        raise SubworkflowError("wrong_core_target")
    if envelope["operation"] == "create_instance" and row.get("run_id") is not None:
        raise SubworkflowError("wrong_core_target")
    for name in ("instance_id", "run_id"):
        if row.get(name) is not None:
            _identifier(row[name])
        if envelope[name] is not None and row.get(name) != envelope[name]:
            raise SubworkflowError("wrong_core_target")
    canonical(row)
    return row


def validate_read(
    value: object, home: str, instance: str, run: str | None = None
) -> dict[str, object]:
    row = _fields(
        value,
        {
            "contract",
            "home",
            "data",
            "source_provider",
            "authority",
            "observed_at",
        },
    )
    if (
        row["contract"] != "millrace-os-router-read/v1"
        or row["home"] != home
        or row["source_provider"] != "core"
        or row["authority"] != "core-state-home"
        or not isinstance(row["observed_at"], str)
        or len(row["observed_at"]) > 40
    ):
        raise SubworkflowError("invalid_os_read")
    data = mapping(row["data"])
    if data.get("id" if run else "instance_id") != (run or instance) or (
        run is not None and data.get("instance_id") != instance
    ):
        raise SubworkflowError("wrong_os_target")
    canonical(data)
    return data


class _DeadlineSocket(socket.socket):
    """Enforce one deadline even when the peer continually drips header bytes."""

    deadline: float
    clock: Callable[[], float]

    def sendall(self, data, flags=0):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError
        self.settimeout(remaining)
        return super().sendall(data, flags)

    def recv_into(self, buffer, nbytes=0, flags=0):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError
        self.settimeout(remaining)
        return super().recv_into(buffer, nbytes, flags)


class _DeadlineConnection(http.client.HTTPConnection):
    def __init__(self, host, *, deadline, clock, **kwargs):
        super().__init__(host, **kwargs)
        self.deadline, self.clock = deadline, clock

    def connect(self):
        # Origins are loopback-only; avoid an unbounded resolver for localhost.
        host = "127.0.0.1" if self.host == "localhost" else self.host
        family = socket.AF_INET6 if host == "::1" else socket.AF_INET
        stream = _DeadlineSocket(family, socket.SOCK_STREAM)
        stream.deadline, stream.clock = self.deadline, self.clock
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            stream.close()
            raise TimeoutError
        stream.settimeout(remaining)
        try:
            stream.connect((host, self.port))
        except BaseException:
            stream.close()
            raise
        self.sock = stream


class _DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline: float, clock: Callable[[], float]):
        super().__init__()
        self.deadline, self.clock = deadline, clock

    def http_open(self, req):
        def connection(host, **kwargs):
            return _DeadlineConnection(
                host, deadline=self.deadline, clock=self.clock, **kwargs
            )

        return self.do_open(connection, req)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SubworkflowError("os_redirect_refused")


class WorkflowOSClient:
    """Reread pinned profile and bearer per request; retain no credential values."""

    def __init__(
        self,
        reference: object,
        home: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.reference, self.home, self.clock = reference, _home(home), clock
        self.profile = load_profile(reference, home=self.home)

    def _request(
        self,
        method: str,
        path: str,
        *,
        deadline: float,
        body: bytes | None = None,
        call: bool = False,
    ) -> dict[str, object]:
        limit = min(deadline, self.clock() + 2)
        window_limited = limit == deadline
        if limit <= self.clock():
            raise SubworkflowError(
                "observation_window_expired" if window_limited else "os_timeout"
            )
        profile = load_profile(self.reference, home=self.home)
        if profile != self.profile:
            raise SubworkflowError("os_profile_changed")
        token = _credential(profile["os_credential_file"])
        headers = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        }
        if call:
            headers["Accept"] = "application/vnd.millrace.router-call.v2+json"
        request = Request(
            str(profile["os_origin"]) + "/api/v1/router/" + path,
            data=body,
            headers=headers,
            method=method,
        )
        opener = build_opener(
            ProxyHandler({}),
            _NoRedirect(),
            _DeadlineHTTPHandler(limit, self.clock),
        )
        try:
            remaining = limit - self.clock()
            if remaining <= 0:
                raise SubworkflowError(
                    "observation_window_expired" if window_limited else "os_timeout"
                )
            with opener.open(request, timeout=remaining) as response:
                if response.status not in ({200, 202} if call else {200}):
                    raise SubworkflowError("os_http_status")
                raw = response.read(2097153)
                if self.clock() > limit:
                    raise SubworkflowError(
                        "observation_window_expired" if window_limited else "os_timeout"
                    )
                return mapping(_utf8_json(raw, 2097152))
        except HTTPError as exc:
            raise SubworkflowError("os_http_" + str(exc.code)) from None
        except (OSError, URLError, ValueError, http.client.HTTPException) as exc:
            timed_out = isinstance(exc, TimeoutError) or (
                isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)
            )
            if timed_out and window_limited and self.clock() >= deadline:
                raise SubworkflowError("observation_window_expired") from None
            raise SubworkflowError("os_unavailable") from None

    def capabilities(self, *, deadline: float) -> dict[str, object]:
        value = self._request(
            "OPTIONS",
            "requests?" + urlencode({"home": self.home}),
            deadline=deadline,
        )
        return validate_capabilities(value, self.home)

    def submit(
        self,
        envelope: dict[str, object],
        *,
        deadline: float,
    ) -> dict[str, object]:
        request_body(self.home, envelope)
        self.capabilities(deadline=deadline)
        return self._submit_prevalidated(envelope, deadline=deadline)

    def _submit_prevalidated(
        self,
        envelope: dict[str, object],
        *,
        deadline: float,
    ) -> dict[str, object]:
        """Send after the durable caller has already verified capabilities."""
        body = request_body(self.home, envelope)
        value = self._request(
            "POST",
            "requests",
            deadline=deadline,
            body=body,
            call=True,
        )
        return validate_call(value, self.home, envelope)

    def observe(
        self,
        envelope: dict[str, object],
        *,
        deadline: float,
    ) -> dict[str, object]:
        query = {"home": self.home}
        for name in ("instance_id", "run_id"):
            if envelope[name] is not None:
                query[name] = _identifier(envelope[name])
        value = self._request(
            "GET",
            "requests/" + _identifier(envelope["request_id"]) + "?" + urlencode(query),
            deadline=deadline,
            call=True,
        )
        return validate_call(value, self.home, envelope)

    def read(
        self,
        instance: str,
        run: str | None = None,
        *,
        deadline: float,
    ) -> dict[str, object]:
        path = "instances/" + _identifier(instance)
        if run is not None:
            path += "/runs/" + _identifier(run)
        value = self._request(
            "GET",
            path + "?" + urlencode({"home": self.home}),
            deadline=deadline,
        )
        return validate_read(value, self.home, instance, run)
