"""Opt-in generic native diagnostic bridge: strict records and immutable export.

A native node may opt in with ``diagnostic_bridge: {context_path, outbox_path}``.
Both paths are relative to the node's actual working directory and must stay
beneath it. Only ``invoke`` touches the filesystem: it reads one strict
``millrace-native-context/v1`` record before any native process starts, and it
publishes one ``millrace-native-diagnostic/v2`` record before returning its
result. Planning and completion validation stay pure over retained launch and
evidence data. Later consumers verify the actual published bytes themselves.

Historical ``millrace-native-diagnostic/v1`` records stay readable with their
exact original bytes and meaning; ``load_record`` dispatches on the exact format
and never upgrades, migrates or fills v2 facts into a v1 record.

The bridge exports facts and does not route. It knows no workflow, role or
service, and it never derives a status from prose or stderr.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import cast

from . import require_core

require_core()

from millrace.router.execution import (  # noqa: E402
    AdapterResult,
    LaunchPlan,
    RouterError,
)

from .configuration import (  # noqa: E402
    CONTROL_LIMIT,
    fields,
    mapping,
    relative,
    strict_json,
    text,
)

CONTEXT_FORMAT = "millrace-native-context/v1"
DIAGNOSTIC_FORMAT = "millrace-native-diagnostic/v1"
DIAGNOSTIC_V2_FORMAT = "millrace-native-diagnostic/v2"
CONFIG_KEY = "diagnostic_bridge"
CLAUDE_PROTOCOL = "claude-print-json/v1"
CODEX_PROTOCOL = "codex-exec-jsonl/v1"
BRIDGE_FIELDS = {"context_path", "outbox_path"}
CONTEXT_FIELDS = {"format", "context_id", "input_sha256"}
REF_FIELDS = {"path", "sha256"}
STATUS_FIELDS = {"protocol", "api_error_status"}
RECORD_FIELDS = {
    "format",
    "context_id",
    "input_sha256",
    "invocation_id",
    "plane_id",
    "node_id",
    "producing_revision",
    "adapter_id",
    "adapter_contract",
    "evidence_format",
    "launch_sha256",
    "transport",
    "writer_state",
    "failure_code",
    "native_diagnostic",
}
# The seven required members v2 adds to the exact v1 field set. Each is an actual
# producer fact: digests of the actual plan settings and selected host route, the
# actual terminal observed version, and the actual requested/effective
# model/effort configuration (not a provider-reported model attestation).
BINDING_FIELDS = {
    "settings_sha256",
    "observed_version",
    "requested_model",
    "requested_effort",
    "resolved_model",
    "resolved_effort",
    "host_route_sha256",
}
RECORD_V2_FIELDS = RECORD_FIELDS | BINDING_FIELDS
# Exact member names of the canonical object whose digest is host_route_sha256.
HOST_ROUTE_FIELDS = {"adapter_id", "adapter_contract", "executable", "native_home"}
# Extra launch-details members carried only by bridge-enabled v2 launch formats.
LAUNCH_FIELDS = {CONFIG_KEY, "plane_id", "node_id", "producing_revision"}
# Extra evidence members carried only by bridge-enabled v2 evidence formats.
EVIDENCE_FIELDS = {CONFIG_KEY, "native_diagnostic"}
# Adapter-local failure suffixes for a refused bridge preflight (no native start).
PREFLIGHT_FAILURES = (
    "bridge_context_missing",
    "bridge_context_invalid",
    "bridge_outbox_invalid",
)
TRANSPORTS = {
    "completed",
    "execution_failure",
    "protocol_failure",
    "timeout",
    "cancelled",
}
WRITER_STATES = {"stopped", "may_live", "unknown"}
# The context is a small workflow-written control input. Reading it uses the
# router's existing 1 MiB captured-control bound so a hostile or mistaken file can
# never force an unbounded read; a valid context is a few hundred bytes.
CONTEXT_LIMIT = CONTROL_LIMIT
# context_id and invocation_id each become one path component. This reuses the
# existing Claude adapter safe-id form (session_id): portable characters only, so
# no separator, dot component or control character, and short enough that
# "<id>.json" stays below the common 255-byte file-name limit.
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
# Same shape as the existing adapter failure-code validators (lowercase, <= 128).
FAILURE_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")


def canonical(value: object) -> bytes:
    """Core-compatible canonical JSON: sorted keys, compact, UTF-8, finite."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise RouterError("invalid_native_diagnostic") from exc


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_id(value: object, code: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise RouterError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise RouterError(code)
    return value


def _label(value: object, code: str) -> str:
    """A non-empty NUL-free string; Core owns these identifiers' own formats."""
    try:
        return text(value)
    except RouterError as exc:
        raise RouterError(code) from exc


def _path(value: object) -> str:
    """One contained, normalized relative path naming at least one component."""
    try:
        result = relative(value)
    except RouterError as exc:
        raise RouterError("invalid_diagnostic_bridge") from exc
    parts = PurePosixPath(result).parts
    if (
        not parts
        or PurePosixPath(*parts).as_posix() != result
        or any(part in {".", ".."} for part in parts)
        or "\\" in result
        or any(ord(char) < 0x20 for char in result)
    ):
        raise RouterError("invalid_diagnostic_bridge")
    return result


def typed_status(value: object) -> int | None:
    """Any non-bool integer is retained as structured data; anything else is null.

    Retention is not classification: unknown integers carry no positive meaning,
    and a consumer applies its own finite table.
    """
    if type(value) is int:
        return value
    return None


@dataclass(frozen=True)
class BridgeConfig:
    context_path: str
    outbox_path: str

    @classmethod
    def load(cls, value: object) -> BridgeConfig:
        try:
            obj = fields(value, BRIDGE_FIELDS)
        except RouterError as exc:
            raise RouterError("invalid_diagnostic_bridge") from exc
        context, outbox = _path(obj["context_path"]), _path(obj["outbox_path"])
        a, b = PurePosixPath(context), PurePosixPath(outbox)
        if a == b or a.is_relative_to(b) or b.is_relative_to(a):
            raise RouterError("invalid_diagnostic_bridge")
        return cls(context, outbox)

    def to_json(self) -> dict[str, object]:
        return {"context_path": self.context_path, "outbox_path": self.outbox_path}

    def record_path(self, context_id: str, invocation_id: str) -> str:
        return f"{self.outbox_path}/{context_id}/{invocation_id}.json"


@dataclass(frozen=True)
class NativeContext:
    context_id: str
    input_sha256: str

    @classmethod
    def load(cls, raw: bytes) -> NativeContext:
        try:
            obj = fields(strict_json(raw), CONTEXT_FIELDS)
        except RouterError as exc:
            raise RouterError("bridge_context_invalid") from exc
        if obj["format"] != CONTEXT_FORMAT:
            raise RouterError("bridge_context_invalid")
        return cls(
            _safe_id(obj["context_id"], "bridge_context_invalid"),
            _sha(obj["input_sha256"], "bridge_context_invalid"),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "format": CONTEXT_FORMAT,
            "context_id": self.context_id,
            "input_sha256": self.input_sha256,
        }


@dataclass(frozen=True)
class DiagnosticBridgeRef:
    path: str
    sha256: str

    def to_json(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class NativeDiagnostic:
    """The exact published terminal record for one invocation and context."""

    context_id: str
    input_sha256: str
    invocation_id: str
    plane_id: str
    node_id: str
    producing_revision: str
    adapter_id: str
    adapter_contract: str
    evidence_format: str
    launch_sha256: str
    transport: str
    writer_state: str
    failure_code: str | None
    protocol: str
    api_error_status: int | None

    def __post_init__(self) -> None:
        code = "invalid_native_diagnostic"
        _safe_id(self.context_id, code)
        _safe_id(self.invocation_id, code)
        _sha(self.input_sha256, code)
        _sha(self.launch_sha256, code)
        for value in (
            self.plane_id,
            self.node_id,
            self.producing_revision,
            self.adapter_id,
            self.adapter_contract,
            self.evidence_format,
            self.protocol,
        ):
            _label(value, code)
        if (
            not isinstance(self.transport, str)
            or not isinstance(self.writer_state, str)
            or self.transport not in TRANSPORTS
            or self.writer_state not in WRITER_STATES
            or (
                self.failure_code is not None
                and (
                    not isinstance(self.failure_code, str)
                    or not FAILURE_CODE.fullmatch(self.failure_code)
                )
            )
            or (
                self.api_error_status is not None
                and typed_status(self.api_error_status) is None
            )
        ):
            raise RouterError(code)

    def status_json(self) -> dict[str, object]:
        return {"protocol": self.protocol, "api_error_status": self.api_error_status}

    def to_json(self) -> dict[str, object]:
        return {
            "format": DIAGNOSTIC_FORMAT,
            "context_id": self.context_id,
            "input_sha256": self.input_sha256,
            "invocation_id": self.invocation_id,
            "plane_id": self.plane_id,
            "node_id": self.node_id,
            "producing_revision": self.producing_revision,
            "adapter_id": self.adapter_id,
            "adapter_contract": self.adapter_contract,
            "evidence_format": self.evidence_format,
            "launch_sha256": self.launch_sha256,
            "transport": self.transport,
            "writer_state": self.writer_state,
            "failure_code": self.failure_code,
            "native_diagnostic": self.status_json(),
        }

    def to_bytes(self) -> bytes:
        return canonical(self.to_json())

    @classmethod
    def load(cls, raw: bytes) -> NativeDiagnostic:
        """Strict canonical reload; any other byte form is a different record."""
        try:
            obj = fields(strict_json(raw), RECORD_FIELDS)
            status = fields(obj["native_diagnostic"], STATUS_FIELDS)
            if obj["format"] != DIAGNOSTIC_FORMAT:
                raise RouterError("invalid_native_diagnostic")
            failure = obj["failure_code"]
            code = status["api_error_status"]
            record = cls(
                context_id=cast(str, obj["context_id"]),
                input_sha256=cast(str, obj["input_sha256"]),
                invocation_id=cast(str, obj["invocation_id"]),
                plane_id=cast(str, obj["plane_id"]),
                node_id=cast(str, obj["node_id"]),
                producing_revision=cast(str, obj["producing_revision"]),
                adapter_id=cast(str, obj["adapter_id"]),
                adapter_contract=cast(str, obj["adapter_contract"]),
                evidence_format=cast(str, obj["evidence_format"]),
                launch_sha256=cast(str, obj["launch_sha256"]),
                transport=cast(str, obj["transport"]),
                writer_state=cast(str, obj["writer_state"]),
                failure_code=cast("str | None", failure),
                protocol=cast(str, status["protocol"]),
                api_error_status=cast("int | None", code),
            )
        except (RouterError, TypeError) as exc:
            raise RouterError("invalid_native_diagnostic") from exc
        if record.to_bytes() != raw:
            raise RouterError("invalid_native_diagnostic")
        return record


def _optional_label(value: object, code: str) -> str | None:
    return None if value is None else _label(value, code)


@dataclass(frozen=True)
class NativeDiagnosticV2:
    """A v2 terminal record: one exact v1 record plus seven binding facts.

    Composition, not inheritance: a v2 record is never a v1 record, and the v1
    loader and bytes are untouched. ``resolved_*`` is effective adapter
    configuration; ``host_route_sha256`` fences the selected executable and
    native home only. Neither attests an account, binary, endpoint or a
    provider-reported model.
    """

    diagnostic: NativeDiagnostic
    settings_sha256: str
    observed_version: str | None
    requested_model: str | None
    requested_effort: str | None
    resolved_model: str | None
    resolved_effort: str | None
    host_route_sha256: str

    def __post_init__(self) -> None:
        code = "invalid_native_diagnostic"
        if type(self.diagnostic) is not NativeDiagnostic:
            raise RouterError(code)
        _sha(self.settings_sha256, code)
        _sha(self.host_route_sha256, code)
        for value in (
            self.observed_version,
            self.requested_model,
            self.requested_effort,
            self.resolved_model,
            self.resolved_effort,
        ):
            _optional_label(value, code)

    @property
    def context_id(self) -> str:
        return self.diagnostic.context_id

    @property
    def invocation_id(self) -> str:
        return self.diagnostic.invocation_id

    def to_json(self) -> dict[str, object]:
        result = self.diagnostic.to_json()
        result.update(
            format=DIAGNOSTIC_V2_FORMAT,
            settings_sha256=self.settings_sha256,
            observed_version=self.observed_version,
            requested_model=self.requested_model,
            requested_effort=self.requested_effort,
            resolved_model=self.resolved_model,
            resolved_effort=self.resolved_effort,
            host_route_sha256=self.host_route_sha256,
        )
        return result

    def to_bytes(self) -> bytes:
        return canonical(self.to_json())

    @classmethod
    def load(cls, raw: bytes) -> NativeDiagnosticV2:
        """Strict canonical v2 reload: exact keys, format, types and bytes."""
        try:
            obj = fields(strict_json(raw), RECORD_V2_FIELDS)
            if obj["format"] != DIAGNOSTIC_V2_FORMAT:
                raise RouterError("invalid_native_diagnostic")
            base = {key: obj[key] for key in RECORD_FIELDS}
            base["format"] = DIAGNOSTIC_FORMAT
            record = cls(
                diagnostic=NativeDiagnostic.load(canonical(base)),
                settings_sha256=cast(str, obj["settings_sha256"]),
                observed_version=cast("str | None", obj["observed_version"]),
                requested_model=cast("str | None", obj["requested_model"]),
                requested_effort=cast("str | None", obj["requested_effort"]),
                resolved_model=cast("str | None", obj["resolved_model"]),
                resolved_effort=cast("str | None", obj["resolved_effort"]),
                host_route_sha256=cast(str, obj["host_route_sha256"]),
            )
        except (RouterError, TypeError) as exc:
            raise RouterError("invalid_native_diagnostic") from exc
        if record.to_bytes() != raw:
            raise RouterError("invalid_native_diagnostic")
        return record


DiagnosticRecord = NativeDiagnostic | NativeDiagnosticV2


def load_record(raw: bytes) -> DiagnosticRecord:
    """Exact-format dispatch to the strict v1 or v2 reader; nothing is upgraded."""
    try:
        obj = mapping(strict_json(raw))
    except RouterError as exc:
        raise RouterError("invalid_native_diagnostic") from exc
    selected = obj.get("format")
    if selected == DIAGNOSTIC_FORMAT:
        return NativeDiagnostic.load(raw)
    if selected == DIAGNOSTIC_V2_FORMAT:
        return NativeDiagnosticV2.load(raw)
    raise RouterError("invalid_native_diagnostic")


# Pure launch and evidence shapes.


def node_bridge(config: object) -> BridgeConfig | None:
    """The optional node opt-in; absence keeps the legacy launch format."""
    obj = mapping(config)
    if CONFIG_KEY not in obj:
        return None
    return BridgeConfig.load(obj[CONFIG_KEY])


def launch_extension(
    bridge: BridgeConfig, plane_id: str, node_id: str, producing_revision: str
) -> dict[str, object]:
    """Members a bridge-enabled plan adds to its unchanged v1 launch details."""
    return {
        CONFIG_KEY: bridge.to_json(),
        "plane_id": _label(plane_id, "invalid_launch_plan"),
        "node_id": _label(node_id, "invalid_launch_plan"),
        "producing_revision": _label(producing_revision, "invalid_launch_plan"),
    }


def launch_bridge(details: Mapping[str, object]) -> BridgeConfig:
    """Validate the v2 launch extension and return its pinned bridge setting."""
    try:
        bridge = BridgeConfig.load(details[CONFIG_KEY])
        for key in ("plane_id", "node_id", "producing_revision"):
            _label(details[key], "invalid_launch_plan")
    except (RouterError, KeyError) as exc:
        raise RouterError("invalid_launch_plan") from exc
    return bridge


def launch_sha256(plan: LaunchPlan) -> str:
    """Digest of the retained launch details, recomputable from public reads."""
    return digest(canonical(mapping(plan.details)))


def settings_sha256(plan: LaunchPlan) -> str:
    """Digest of the complete actual ``plan.settings`` (public ``launch.settings``).

    The adapter settings are hashed as planned, top-level requested/effective/
    provenance/warnings included. Core's separate common settings and its
    ``launch_settings`` wrapper are not part of this value.
    """
    return digest(canonical(mapping(plan.settings)))


def _setting(settings: Mapping[str, object], layer: str, key: str) -> str | None:
    """One actual configured value; null only when the key is genuinely absent.

    ``layer`` is a top-level member of ``plan.settings`` (``requested`` or
    ``effective``); there is no wrapped-form fallback. A present value is never
    replaced by null: a non-string present value refuses instead.
    """
    if layer not in settings:
        raise RouterError("invalid_native_diagnostic")
    values = mapping(settings[layer])
    if key not in values:
        return None
    return _label(values[key], "invalid_native_diagnostic")


def binding_facts(plan: LaunchPlan) -> dict[str, str | None]:
    """The four requested/effective model and effort projections of the plan."""
    settings = mapping(plan.settings)
    return {
        "requested_model": _setting(settings, "requested", "model"),
        "requested_effort": _setting(settings, "requested", "reasoning_effort"),
        "resolved_model": _setting(settings, "effective", "model"),
        "resolved_effort": _setting(settings, "effective", "reasoning_effort"),
    }


def host_route_sha256(plan: LaunchPlan, protocol: str) -> str:
    """Digest of the selected executable and native home; neither is exported.

    ``native_home`` is the actual Codex ``codex_home`` launch detail for the Codex
    protocol and null otherwise. This fences a route, not an account or binary.
    """
    code = "invalid_native_diagnostic"
    details = mapping(plan.details)
    native_home = None
    if protocol == CODEX_PROTOCOL:
        if "codex_home" not in details:
            raise RouterError(code)
        native_home = _label(details["codex_home"], code)
    if not plan.argv:
        raise RouterError(code)
    return digest(
        canonical(
            {
                "adapter_id": _label(plan.adapter_id, code),
                "adapter_contract": _label(plan.adapter_contract, code),
                "executable": _label(plan.argv[0], code),
                "native_home": native_home,
            }
        )
    )


def evidence_links(
    evidence: Mapping[str, object],
    *,
    bridge: BridgeConfig,
    invocation_id: str,
    protocol: str,
) -> tuple[DiagnosticBridgeRef | None, int | None, bool]:
    """Pure shape and link checks for the v2 evidence members.

    Returns ``(ref, api_error_status, admitted)``. ``admitted`` is false only for a
    refused bridge preflight, in which case both members are explicit null.
    """
    native = evidence["native_diagnostic"]
    value = evidence[CONFIG_KEY]
    if native is None:
        if value is not None:
            raise RouterError("completion_integrity")
        return None, None, False
    status = fields(native, STATUS_FIELDS)
    code = status["api_error_status"]
    if status["protocol"] != protocol or (
        code is not None and typed_status(code) is None
    ):
        raise RouterError("completion_integrity")
    if value is None:
        return None, typed_status(code), True
    ref = fields(value, REF_FIELDS)
    path = ref["path"]
    prefix, suffix = bridge.outbox_path + "/", "/" + invocation_id + ".json"
    if (
        not isinstance(path, str)
        or not path.startswith(prefix)
        or not path.endswith(suffix)
        or len(path) <= len(prefix) + len(suffix)
    ):
        raise RouterError("completion_integrity")
    _safe_id(path[len(prefix) : -len(suffix)], "completion_integrity")
    return (
        DiagnosticBridgeRef(path, _sha(ref["sha256"], "completion_integrity")),
        typed_status(code),
        True,
    )


def verify_record(
    raw: bytes,
    ref: DiagnosticBridgeRef,
    bridge: BridgeConfig,
    expected: Mapping[str, object],
) -> DiagnosticRecord:
    """Pure check of actual published bytes against a retained ref and facts.

    The bytes must be an exact v1 or v2 record (``load_record``). ``expected``
    names record members (for example ``context_id``, ``input_sha256``,
    ``launch_sha256`` or ``native_diagnostic``) that a consumer has independently
    established; every one must be present and match exactly, so a v2-only
    member never matches a v1 record.
    """
    if digest(raw) != ref.sha256:
        raise RouterError("native_diagnostic_mismatch")
    record = load_record(raw)
    if ref.path != bridge.record_path(record.context_id, record.invocation_id):
        raise RouterError("native_diagnostic_mismatch")
    actual = record.to_json()
    for key, value in expected.items():
        if key not in actual or actual[key] != value:
            raise RouterError("native_diagnostic_mismatch")
    return record


# Invoke-only filesystem access beneath the actual node working directory.


def _walk(root: Path, rel: str, *, create: bool) -> Path:
    """Reject links and special files at every component; optionally mkdir."""
    current = root
    for part in PurePosixPath(rel).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if not create:
                raise
            try:
                os.mkdir(current)
            except FileExistsError:
                pass
            mode = current.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise RouterError("unsafe_bridge_path")
    resolved = root.resolve()
    if resolved != current.resolve() and resolved not in current.resolve().parents:
        raise RouterError("unsafe_bridge_path")
    return current


def _read(path: Path, limit: int) -> bytes:
    """One bounded regular-file read that never follows a link or waits on a FIFO."""
    # Both flags are POSIX; where a platform lacks one, the explicit link and
    # regular-file checks around this read remain the defined behavior.
    flags = getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, os.O_RDONLY | flags)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RouterError("unsafe_bridge_path")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise RouterError("bridge_file_too_large")
    return raw


def _file(root: Path, rel: str, *, create: bool) -> Path:
    parent = PurePosixPath(rel).parent
    directory = root if str(parent) == "." else _walk(root, str(parent), create=create)
    return directory / PurePosixPath(rel).name


def preflight(cwd: Path, bridge: BridgeConfig) -> NativeContext:
    """Read the strict context before native launch; raise a bridge failure code."""
    root = Path(cwd)
    try:
        if not root.is_dir():
            raise RouterError("bridge_context_invalid")
        path = _file(root, bridge.context_path, create=False)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise RouterError("bridge_context_invalid")
        context = NativeContext.load(_read(path, CONTEXT_LIMIT))
    except FileNotFoundError as exc:
        raise RouterError("bridge_context_missing") from exc
    except RouterError as exc:
        if exc.code in PREFLIGHT_FAILURES:
            raise
        raise RouterError("bridge_context_invalid") from exc
    except (OSError, ValueError) as exc:
        raise RouterError("bridge_context_invalid") from exc
    try:
        current = root
        for part in PurePosixPath(bridge.outbox_path).parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                break
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise RouterError("bridge_outbox_invalid")
    except OSError as exc:
        raise RouterError("bridge_outbox_invalid") from exc
    return context


def publish(
    cwd: Path, bridge: BridgeConfig, record: DiagnosticRecord
) -> DiagnosticBridgeRef:
    """Immutable no-overwrite publication; matching bytes replay, others refuse."""
    raw = record.to_bytes()
    rel = bridge.record_path(record.context_id, record.invocation_id)
    ref = DiagnosticBridgeRef(rel, digest(raw))
    path = _file(Path(cwd), rel, create=True)

    def existing() -> DiagnosticBridgeRef:
        # Reading one byte past the expected size is enough to prove equality.
        try:
            same = _read(path, len(raw)) == raw
        except RouterError as exc:
            if exc.code != "bridge_file_too_large":
                raise
            same = False
        if not same:
            raise RouterError("native_diagnostic_conflict")
        return ref

    if os.path.lexists(path):
        return existing()
    fd, name = tempfile.mkstemp(prefix=".native-diagnostic-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            return existing()
    finally:
        Path(name).unlink(missing_ok=True)
    return ref


def preflight_evidence(
    evidence: dict[str, object], evidence_format: str
) -> dict[str, object]:
    """v2 members for a refused bridge preflight: both explicit null."""
    evidence.update(
        format=evidence_format, diagnostic_bridge=None, native_diagnostic=None
    )
    return evidence


def export_result(
    result: AdapterResult,
    *,
    plan: LaunchPlan,
    cwd: Path,
    bridge: BridgeConfig,
    context: NativeContext,
    evidence_format: str,
    protocol: str,
    api_error_status: int | None,
) -> AdapterResult:
    """Attach v2 evidence and publish the terminal v2 record before returning.

    The record's binding facts come from this same actual ``plan`` and terminal
    ``result.adapter_evidence``; nothing is reconstructed from graph defaults.
    Native transport, writer state, diagnostic and bytes are never changed, and a
    publication failure never raises into Core uncertainty. If publication cannot
    complete (a conflicting record, which is never overwritten, any I/O failure, or
    no file at all), ``diagnostic_bridge`` stays null while ``native_diagnostic``
    is kept. That combination means the auxiliary handoff is unavailable: it is not
    verified bridge evidence and grants no retry authority. A consumer that needs
    the record must hold or reconcile; it must never infer anything from the
    native status alone or blindly resend.
    """
    details = mapping(plan.details)
    evidence = dict(mapping(result.adapter_evidence))
    failure = evidence.get("failure_code")
    evidence["format"] = evidence_format
    evidence["native_diagnostic"] = {
        "protocol": protocol,
        "api_error_status": typed_status(api_error_status),
    }
    evidence[CONFIG_KEY] = None
    try:
        diagnostic = NativeDiagnostic(
            context_id=context.context_id,
            input_sha256=context.input_sha256,
            invocation_id=plan.invocation_id,
            plane_id=str(details["plane_id"]),
            node_id=str(details["node_id"]),
            producing_revision=str(details["producing_revision"]),
            adapter_id=plan.adapter_id,
            adapter_contract=plan.adapter_contract,
            evidence_format=evidence_format,
            launch_sha256=launch_sha256(plan),
            transport=result.classification,
            writer_state=result.writer_state,
            failure_code=failure if isinstance(failure, str) else None,
            protocol=protocol,
            api_error_status=typed_status(api_error_status),
        )
        facts = binding_facts(plan)
        record = NativeDiagnosticV2(
            diagnostic=diagnostic,
            settings_sha256=settings_sha256(plan),
            observed_version=cast("str | None", evidence.get("observed_version")),
            requested_model=facts["requested_model"],
            requested_effort=facts["requested_effort"],
            resolved_model=facts["resolved_model"],
            resolved_effort=facts["resolved_effort"],
            host_route_sha256=host_route_sha256(plan, protocol),
        )
        evidence[CONFIG_KEY] = publish(cwd, bridge, record).to_json()
    except (RouterError, OSError, ValueError, TypeError):
        pass
    return replace(result, adapter_evidence=evidence)
