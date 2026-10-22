"""Bounded standard-library client for the existing local OS router HTTP API."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .paperclip_records import (
    BridgeError,
    canonical,
    fields,
    identifier,
    load_json,
    mapping,
    safe_path,
)

__all__ = ["OSClient"]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeError("os_redirect_refused")


def _credential(path: Path) -> str:
    safe_path(str(path))
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise BridgeError("invalid_os_credential")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            value = fields(load_json(stream.read(65537)), {"token"})
        token = value["token"]
        if (
            not isinstance(token, str)
            or len(token) < 32
            or len(token) > 8192
            or any(c in token for c in "\r\n\0")
        ):
            raise BridgeError("invalid_os_credential")
        return token
    finally:
        os.close(descriptor)


class OSClient:
    """Read credentials per request and keep raw response bodies out of errors."""

    def __init__(self, config: dict[str, object]) -> None:
        self.origin = str(config["os_origin"])
        self.home = str(config["os_home"])
        self.credential = Path(str(config["os_credential_file"]))
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, path: str, body: object = None) -> dict[str, object]:
        try:
            token = _credential(self.credential)
            request = Request(
                self.origin + "/api/v1/router/" + path,
                data=canonical(body) if body is not None else None,
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                },
                method="POST" if body is not None else "GET",
            )
            with self.opener.open(request, timeout=2) as response:
                if response.status not in {200, 202}:
                    raise BridgeError("os_http_status")
                return mapping(load_json(response.read(2097153), 2097152))
        except HTTPError as exc:
            raise BridgeError("os_http_" + str(exc.code)) from None
        except (OSError, URLError, ValueError) as exc:
            raise BridgeError("os_unavailable") from exc

    def submit(self, envelope: dict[str, object]) -> dict[str, object]:
        return self._request("requests", {"home": self.home, "request": envelope})

    def observe(
        self, envelope: dict[str, object], instance: object, run: object
    ) -> dict[str, object]:
        query = {"home": self.home}
        if instance is not None:
            query["instance_id"] = identifier(instance)
        if run is not None:
            query["run_id"] = identifier(run)
        return self._request(
            "requests/" + identifier(envelope["request_id"]) + "?" + urlencode(query)
        )

    def read(self, instance: object, run: object = None) -> dict[str, object]:
        path = "instances/" + identifier(instance)
        if run is not None:
            path += "/runs/" + identifier(run)
        value = self._request(path + "?" + urlencode({"home": self.home}))
        if (
            value.get("contract") != "millrace-os-router-read/v1"
            or value.get("home") != self.home
            or value.get("source_provider") != "core"
            or value.get("authority") != "core-state-home"
        ):
            raise BridgeError("invalid_os_read")
        data = mapping(value.get("data"))
        if data.get("id" if run is not None else "instance_id") != (
            run if run is not None else instance
        ):
            raise BridgeError("wrong_os_target")
        if run is not None and data.get("instance_id") != instance:
            raise BridgeError("wrong_os_target")
        return data
