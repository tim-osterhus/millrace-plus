"""Strict LAD provider-wait records, conservative classification and due checks.

This is a small workflow record store beneath the LAD foreground workspace
(``lad-state/provider/``), not a database or event platform. Every record is
canonical UTF-8 JSON (``local_intake.canonical``) with SHA-256 integrity; files are
published immutably through the existing ``engineering_feedback`` no-link
primitives and the index alone is replaced atomically.

Time is never read here: every function that needs "now" takes an explicit
timezone-aware UTC ``datetime``. Nothing here sleeps, wakes, launches a native
process or calls a Core mutator. Only the observation path imports Core, lazily:
``observation_facts`` uses the C1 ``router.native_diagnostic`` record reader and
``observe_snapshot`` reads one exclusive public snapshot through the normal Plus
``router.control`` facade.

Trust boundary: these functions validate record shape, digests, presence of
referenced bytes, chain links and the fixed policy table. The caller (the C3
graph helpers) owns the semantic proof of the facts it supplies: occurrence and
replacement lineage, reconciled source, scoped file-delta/effects reconciliation,
guidance and continuation revalidation, and applied control lineage.

Public functions (exact arguments are their keyword signatures):

- ``initialize_index(foreground, *, session_id, policy_raw)``
- ``operation_id(authority)`` / ``authority_record(**fields)``
- ``classify(**facts)`` - pure finite table, returns ``Classification``
- ``classify_failure(foreground, *, authority, classification, occurrence_id,
  diagnostic_ref, failed_at, current_source_sha256, policy, provider_floor=None)``
- ``observe_provider(foreground, *, operation_id, facts)`` with ``facts`` from
  ``observation_facts(view, ...)``; ``observe_snapshot`` is the CLI entry.
- ``check_provider_due(foreground, *, operation_id, observation_id, now, policy,
  restoration=None)``
- ``reserve_provider_attempt(foreground, *, operation_id, kind,
  previous_occurrence, new_occurrence, input_sha256, current_source_sha256,
  file_delta_ref, effects_ref)``
- ``record_diagnosis(foreground, *, operation_id, occurrence_id, resolved,
  restoration_ref)``
- ``reconcile_provider_success(foreground, *, operation_id, success)``
- ``load_operation(foreground, operation_id)`` for strict reload.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from . import engineering_feedback as ef

ROOT = "lad-state/provider"
INDEX = "millrace-lad-provider-index/v1"
AUTHORITY = "millrace-lad-provider-authority/v1"
EVENT = "millrace-lad-provider-event/v1"
OBSERVATION = "millrace-lad-provider-observation/v1"
RESTORATION = "millrace-lad-provider-restoration/v1"
PREPARATION = "millrace-lad-provider-preparation/v1"
POLICY = "millrace-lad-provider-policy/v1"
CLASSIFIER = "millrace-lad-provider-classifier/v1"
# The fixed v1 policy format selects diagnosis at the third known failure.
DIAGNOSIS_STREAK = 3

INDEX_FIELDS = ("format", "session_id", "policy_sha256", "operations")
AUTHORITY_FIELDS = (
    "format",
    "operation_id",
    "session_id",
    "service",
    "packet_lineage",
    "role",
    "pending_route",
    "original_occurrence",
    "input_sha256",
    "request_sha256",
    "source_sha256",
    "guidance_sha256",
    "policy_sha256",
    "binding",
    "native_owner",
    "integrity_sha256",
)
SERVICE_FIELDS = ("service_id", "task_id", "claim_id")
BINDING_FIELDS = (
    "adapter_id",
    "contract",
    "config_sha256",
    "requested_model",
    "requested_effort",
    "resolved_model",
    "resolved_effort",
    "host_route_sha256",
)
OWNER_FIELDS = ("invocation_id", "plane_id", "node_id", "producing_revision")
EVENT_FIELDS = (
    "format",
    "operation_id",
    "sequence",
    "previous_sha256",
    "kind",
    "attempt",
    "occurrence_id",
    "observation_ref",
    "diagnostic_ref",
    "classifier_revision",
    "classification",
    "failed_at",
    "streak",
    "provider_floor",
    "due_at",
    "phase",
    "current_source_sha256",
    "file_delta_ref",
    "effects_ref",
    "restoration_ref",
    "preparation_ref",
    "core_locator",
    "control_refs",
    "integrity_sha256",
)
OBSERVATION_FIELDS = (
    "format",
    "observation_id",
    "operation_id",
    "authority_sha256",
    "instance_id",
    "run_id",
    "state_home",
    "workspace",
    "expected_state_version",
    "observed_state_version",
    "active_revision",
    "plane_id",
    "invocation_id",
    "completion_ref",
    "transition_ref",
    "bridge_ref",
    "claim_ref",
    "writer_state",
    "effects_ref",
    "observed_at",
    "integrity_sha256",
)
RESTORATION_FIELDS = (
    "format",
    "operation_id",
    "policy_sha256",
    "kind",
    "actor",
    "request_id",
    "observed_at",
    "evidence_ref",
    "statement",
    "integrity_sha256",
)
PREPARATION_FIELDS = (
    "format",
    "preparation_id",
    "operation_id",
    "authority_sha256",
    "attempt",
    "kind",
    "previous_occurrence",
    "new_occurrence",
    "input_sha256",
    "event_ref",
    "integrity_sha256",
)
POLICY_FIELDS = (
    "format",
    "first_retry_seconds",
    "second_retry_seconds",
    "troubleshooter_binding",
    "automatic_wait",
)
LOCATOR_FIELDS = ("instance_id", "run_id", "state_home", "plane_id")
KINDS = frozenset(
    {
        "failure_classified",
        "observed",
        "due_checked",
        "restoration_recorded",
        "retry_reserved",
        "diagnosis_reserved",
        "diagnosis_recorded",
        "human_required",
        "uncertainty_recorded",
        "succeeded",
    }
)
CLASSIFICATIONS = frozenset(
    {
        "provider_usage",
        "provider_availability",
        "provider_access",
        "permission_required",
        "ordinary_error",
        "unclassified",
        "timeout",
        "cancelled",
        "unknown_writer",
        "unknown_effects",
    }
)
PHASES = frozenset(
    {
        "reconcile",
        "waiting",
        "retry_ready",
        "retry_reserved",
        "diagnosis_required",
        "diagnosis_reserved",
        "human_required",
        "succeeded",
        "cancelled",
    }
)
TIMED = frozenset({"provider_usage", "provider_availability"})
HUMAN = frozenset({"provider_access", "permission_required"})
OUTCOMES = frozenset(
    {"ORDINARY_ERROR", "PROVIDER_WAIT", "PROVIDER_HUMAN_REQUIRED", "UNCERTAIN"}
)
# Foreground native roles; Learning is excluded by the selected L-R1 precedence.
ROLES = frozenset(
    {
        "recon",
        "planner",
        "manager",
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "troubleshooter",
        "consultant",
        "updater",
        "arbiter",
    }
)
# Exact positive table (spec §1.1/§7.1). Anything else is unclassified.
CLAUDE = ("claude_print", "millrace-claude-print/v1", "claude-print-json/v1")
CLAUDE_VERSIONS = frozenset({"2.1.296", "2.1.305"})
CLAUDE_FAILURES = frozenset({"claude_native_exit", "claude_native_error"})
CLAUDE_RULES = {
    429: "provider_usage",
    500: "provider_availability",
    502: "provider_availability",
    503: "provider_availability",
    504: "provider_availability",
    529: "provider_availability",
    401: "provider_access",
    403: "permission_required",
}
# Core completion columns retained as a public fact. Raw native streams
# (``stdout``, ``stderr``, ``stdout_base64``) are never copied into provider state.
COMPLETION_FACT_KEYS = (
    "invocation_id",
    "run_id",
    "revision_id",
    "plane_id",
    "node",
    "transport",
    "writer",
    "effective_writer",
    "diagnostic",
    "exit_code",
    "classification",
    "outcome",
)
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class ProviderWaitError(Exception):
    """Expected refusal with a stable code; callers hold or translate it."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- primitives


def _encode(value: object) -> bytes:
    try:
        return ef.encode(value)
    except ef.EngineeringError as exc:
        raise ProviderWaitError("provider_invalid_json") from exc


def _sha(raw: bytes) -> str:
    return ef.sha256(raw)


def _sealed(record: dict[str, object]) -> dict[str, object]:
    body = {k: v for k, v in record.items() if k != "integrity_sha256"}
    body["integrity_sha256"] = _sha(_encode(body))
    return body


def _strict(raw: bytes, names: Sequence[str], fmt: str, code: str) -> dict[str, object]:
    """Exact field set, format, integrity and canonical byte form."""
    try:
        value = ef.parse(raw)
    except ef.EngineeringError as exc:
        raise ProviderWaitError(code) from exc
    if not isinstance(value, dict) or set(value) != set(names):
        raise ProviderWaitError(code)
    record = cast(dict[str, object], value)
    if record["format"] != fmt or _sealed(dict(record)) != record:
        raise ProviderWaitError(code)
    if _encode(record) != raw:
        raise ProviderWaitError(code)
    return record


def _fields(value: object, names: Sequence[str], code: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise ProviderWaitError(code)
    return dict(cast(Mapping[str, object], value))


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ProviderWaitError(code)
    return value


def _opt_text(value: object, code: str) -> str | None:
    return None if value is None else _text(value, code)


def _id(value: object, code: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ProviderWaitError(code)
    return value


def _digest(value: object, code: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProviderWaitError(code)
    return value


def _int(value: object, code: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ProviderWaitError(code)
    return value


def _ref(value: object, code: str) -> dict[str, object] | None:
    if value is None:
        return None
    ref = _fields(value, ("path", "sha256"), code)
    try:
        ef.ref(ref, code)
    except ef.EngineeringError as exc:
        raise ProviderWaitError(code) from exc
    return ref


def _time(value: object, code: str) -> datetime:
    """A canonical aware UTC ISO-8601 string (``datetime.isoformat``)."""
    if not isinstance(value, str):
        raise ProviderWaitError(code)
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProviderWaitError(code) from exc
    if moment.utcoffset() != timedelta(0) or moment.isoformat() != value:
        raise ProviderWaitError(code)
    return moment


def stamp(moment: datetime) -> str:
    """Serialize an injected clock reading; naive or non-UTC readings refuse."""
    if not isinstance(moment, datetime) or moment.utcoffset() != timedelta(0):
        raise ProviderWaitError("provider_clock_invalid")
    return moment.astimezone(UTC).isoformat()


def _read(root: Path, relative: str, code: str) -> bytes:
    try:
        return ef.read_regular(ef.contained(root, relative))
    except ef.EngineeringError as exc:
        raise ProviderWaitError(code) from exc


def _exists(root: Path, relative: str) -> bool:
    try:
        return os.path.lexists(ef.contained(root, relative, missing=True))
    except ef.EngineeringError as exc:
        raise ProviderWaitError("provider_unsafe_path") from exc


def _write(root: Path, relative: str, raw: bytes) -> dict[str, object]:
    try:
        return ef.write_immutable(root, relative, raw)
    except ef.EngineeringError as exc:
        raise ProviderWaitError(
            "provider_record_conflict"
            if exc.code == "immutable_conflict"
            else "provider_unsafe_path"
        ) from exc


def _load_ref(
    root: Path, ref: object, names: Sequence[str], fmt: str, code: str
) -> dict[str, object]:
    pinned = _ref(ref, code)
    if pinned is None:
        raise ProviderWaitError(code)
    raw = _read(root, cast(str, pinned["path"]), code)
    if _sha(raw) != pinned["sha256"]:
        raise ProviderWaitError(code)
    return _strict(raw, names, fmt, code)


# ------------------------------------------------------------------- policy


def load_policy(raw: bytes) -> dict[str, object]:
    """Strict ``millrace-lad-provider-policy/v1``; delays are positive integers."""
    try:
        value = ef.parse(raw)
    except ef.EngineeringError as exc:
        raise ProviderWaitError("provider_policy_invalid") from exc
    policy = _fields(value, POLICY_FIELDS, "provider_policy_invalid")
    if (
        policy["format"] != POLICY
        or not isinstance(policy["automatic_wait"], str)
        or policy["automatic_wait"] not in {"optional", "required"}
        or not isinstance(policy["troubleshooter_binding"], str)
        or not SAFE_ID.fullmatch(policy["troubleshooter_binding"])
    ):
        raise ProviderWaitError("provider_policy_invalid")
    for key in ("first_retry_seconds", "second_retry_seconds"):
        _int(policy[key], "provider_policy_invalid", minimum=1)
    return {**policy, "sha256": _sha(raw)}


# ---------------------------------------------------------------- authority


def _authority_body(value: Mapping[str, object]) -> dict[str, object]:
    code = "provider_authority_invalid"
    record = _fields(value, AUTHORITY_FIELDS, code)
    if record["format"] != AUTHORITY:
        raise ProviderWaitError(code)
    _id(record["session_id"], code)
    if record["service"] is not None:
        service = _fields(record["service"], SERVICE_FIELDS, code)
        _id(service["service_id"], code)
        _id(service["task_id"], code)
        _digest(service["claim_id"], code)
    if not isinstance(record["packet_lineage"], dict) or not isinstance(
        record["pending_route"], dict
    ):
        raise ProviderWaitError(code)
    if not isinstance(record["role"], str) or record["role"] not in ROLES:
        raise ProviderWaitError("provider_role_excluded")
    _id(record["original_occurrence"], code)
    for key in (
        "input_sha256",
        "request_sha256",
        "source_sha256",
        "guidance_sha256",
        "policy_sha256",
    ):
        _digest(record[key], code)
    binding = _fields(record["binding"], BINDING_FIELDS, code)
    for key in ("adapter_id", "contract"):
        _text(binding[key], code)
    for key in ("config_sha256", "host_route_sha256"):
        _digest(binding[key], code)
    for key in (
        "requested_model",
        "requested_effort",
        "resolved_model",
        "resolved_effort",
    ):
        _opt_text(binding[key], code)
    owner = _fields(record["native_owner"], OWNER_FIELDS, code)
    for key in OWNER_FIELDS:
        _text(owner[key], code)
    return record


def operation_id(authority: Mapping[str, object]) -> str:
    """SHA-256 of the canonical authority without its ID and checksum."""
    body = {
        k: v
        for k, v in authority.items()
        if k not in {"operation_id", "integrity_sha256"}
    }
    return _sha(_encode(body))


def authority_record(**values: object) -> dict[str, object]:
    """Build the sealed authority from every field except format/ID/checksum."""
    record: dict[str, object] = {"format": AUTHORITY, **values}
    record["operation_id"] = operation_id(record)
    record["integrity_sha256"] = ""
    record = _sealed(record)
    _authority_body(record)
    return record


def _paths(op: str) -> tuple[str, str]:
    return f"{ROOT}/operations/{op}/authority.json", f"{ROOT}/operations/{op}/events"


# ------------------------------------------------------------------- index


def initialize_index(
    foreground: Path, *, session_id: str, policy_raw: bytes
) -> dict[str, object]:
    """Create the empty index once; an identical earlier creation is reused."""
    policy = load_policy(policy_raw)
    record = {
        "format": INDEX,
        "session_id": _id(session_id, "provider_index_invalid"),
        "policy_sha256": policy["sha256"],
        "operations": {},
    }
    relative = f"{ROOT}/index.json"
    if _exists(foreground, relative):
        index = _index(foreground)
        if (index["session_id"], index["policy_sha256"]) != (
            record["session_id"],
            record["policy_sha256"],
        ):
            raise ProviderWaitError("provider_index_conflict")
        return index
    operations = f"{ROOT}/operations"
    if _exists(foreground, operations):
        try:
            folder = ef.contained(foreground, operations)
        except ef.EngineeringError as exc:
            raise ProviderWaitError("provider_unsafe_path") from exc
        if not folder.is_dir():
            raise ProviderWaitError("provider_unsafe_path")
        if os.listdir(folder):
            # A lost index over existing history is never recreated as empty.
            raise ProviderWaitError("provider_index_missing")
    _write(foreground, relative, _encode(record))
    return record


def _index(foreground: Path) -> dict[str, object]:
    relative = f"{ROOT}/index.json"
    if not _exists(foreground, relative):
        raise ProviderWaitError("provider_index_missing")
    raw = _read(foreground, relative, "provider_index_invalid")
    try:
        value = ef.parse(raw)
    except ef.EngineeringError as exc:
        raise ProviderWaitError("provider_index_invalid") from exc
    index = _fields(value, INDEX_FIELDS, "provider_index_invalid")
    if index["format"] != INDEX or _encode(index) != raw:
        raise ProviderWaitError("provider_index_invalid")
    _id(index["session_id"], "provider_index_invalid")
    _digest(index["policy_sha256"], "provider_index_invalid")
    operations = index["operations"]
    if not isinstance(operations, dict):
        raise ProviderWaitError("provider_index_invalid")
    for key, entry in operations.items():
        _digest(key, "provider_index_invalid")
        tip = _fields(entry, ("path", "sha256", "sequence"), "provider_index_invalid")
        sequence = _int(tip["sequence"], "provider_index_invalid", minimum=1)
        if tip["path"] != f"{_paths(key)[1]}/{sequence}.json":
            raise ProviderWaitError("provider_index_invalid")
        _digest(tip["sha256"], "provider_index_invalid")
    return index


def _publish_index(
    foreground: Path,
    index: dict[str, object],
    op: str,
    event: Mapping[str, object],
    raw: bytes,
) -> None:
    operations = dict(cast(dict[str, object], index["operations"]))
    operations[op] = {
        "path": f"{_paths(op)[1]}/{event['sequence']}.json",
        "sha256": _sha(raw),
        "sequence": event["sequence"],
    }
    index["operations"] = operations
    try:
        ef.replace_file(foreground, f"{ROOT}/index.json", _encode(index))
    except ef.EngineeringError as exc:
        raise ProviderWaitError("provider_unsafe_path") from exc


# ------------------------------------------------------------ chain reload


@dataclass(frozen=True)
class Operation:
    """A fully verified operation: authority, ordered events and raw tip bytes."""

    authority: dict[str, object]
    authority_sha256: str
    events: tuple[dict[str, object], ...]
    tip_raw: bytes

    @property
    def tip(self) -> dict[str, object]:
        return self.events[-1]

    @property
    def id(self) -> str:
        return cast(str, self.authority["operation_id"])


def _event(
    raw: bytes, op: str, sequence: int, previous: bytes | None
) -> dict[str, object]:
    code = "provider_chain_invalid"
    event = _strict(raw, EVENT_FIELDS, EVENT, code)
    if (
        event["operation_id"] != op
        or event["sequence"] != sequence
        or event["previous_sha256"] != (None if previous is None else _sha(previous))
        or not isinstance(event["kind"], str)
        or event["kind"] not in KINDS
        or not isinstance(event["phase"], str)
        or event["phase"] not in PHASES
        or event["classifier_revision"] != CLASSIFIER
        or (
            event["classification"] is not None
            and (
                not isinstance(event["classification"], str)
                or event["classification"] not in CLASSIFICATIONS
            )
        )
    ):
        raise ProviderWaitError(code)
    _int(event["attempt"], code, minimum=1)
    _int(event["streak"], code, minimum=0)
    _opt_text(event["occurrence_id"], code)
    for key in (
        "observation_ref",
        "diagnostic_ref",
        "file_delta_ref",
        "effects_ref",
        "restoration_ref",
        "preparation_ref",
    ):
        _ref(event[key], code)
    for key in ("failed_at", "provider_floor", "due_at"):
        if event[key] is not None:
            _time(event[key], code)
    if event["current_source_sha256"] is not None:
        _digest(event["current_source_sha256"], code)
    if event["core_locator"] is not None:
        locator = _fields(event["core_locator"], LOCATOR_FIELDS, code)
        for key in LOCATOR_FIELDS:
            _text(locator[key], code)
    refs = event["control_refs"]
    if not isinstance(refs, list):
        raise ProviderWaitError(code)
    for item in refs:
        if _ref(item, code) is None:
            raise ProviderWaitError(code)
    if previous is not None:
        prior = cast(dict[str, object], ef.parse(previous))
        if (
            cast(int, event["attempt"]) < cast(int, prior["attempt"])
            or (
                prior["core_locator"] is not None
                and event["core_locator"] != prior["core_locator"]
            )
            or prior["phase"] == "succeeded"
        ):
            raise ProviderWaitError(code)
    elif event["kind"] != "failure_classified" or event["attempt"] != 1:
        raise ProviderWaitError(code)
    return event


def _chain(
    foreground: Path, op: str, tip: int | None
) -> tuple[list[dict[str, object]], list[bytes]]:
    """Events 1..tip, plus a unique valid unpublished successor if present."""
    folder = _paths(op)[1]
    events: list[dict[str, object]] = []
    raws: list[bytes] = []
    sequence = 1
    while _exists(foreground, f"{folder}/{sequence}.json"):
        raw = _read(foreground, f"{folder}/{sequence}.json", "provider_chain_invalid")
        events.append(_event(raw, op, sequence, raws[-1] if raws else None))
        raws.append(raw)
        sequence += 1
    names = (
        os.listdir(ef.contained(foreground, folder))
        if _exists(foreground, folder)
        else []
    )
    expected = {f"{n}.json" for n in range(1, len(raws) + 1)}
    if set(name for name in names if not name.startswith(".lq-write-")) != expected:
        raise ProviderWaitError("provider_chain_forked")
    if tip is not None and not (tip <= len(raws) <= tip + 1):
        raise ProviderWaitError("provider_chain_forked")
    return events, raws


def _authority(
    foreground: Path, op: str
) -> tuple[dict[str, object], dict[str, object], bytes, dict[str, object]]:
    """Index, published tip entry and verified authority; reads only."""
    _digest(op, "provider_operation_invalid")
    index = _index(foreground)
    entry = cast(dict[str, object], index["operations"]).get(op)
    if entry is None:
        raise ProviderWaitError("provider_operation_missing")
    authority_raw = _read(foreground, _paths(op)[0], "provider_authority_invalid")
    authority = _strict(
        authority_raw, AUTHORITY_FIELDS, AUTHORITY, "provider_authority_invalid"
    )
    _authority_body(authority)
    if (
        operation_id(authority) != op
        or authority["operation_id"] != op
        or authority["session_id"] != index["session_id"]
        or authority["policy_sha256"] != index["policy_sha256"]
    ):
        raise ProviderWaitError("provider_authority_invalid")
    return index, cast(dict[str, object], entry), authority_raw, authority


def load_operation(foreground: Path, op: str, *, recover: bool = True) -> Operation:
    """Strict reload; publishes only one unique matching unpublished next event."""
    index, tip, authority_raw, authority = _authority(foreground, op)
    events, raws = _chain(foreground, op, cast(int, tip["sequence"]))
    published = cast(int, tip["sequence"])
    if _sha(raws[published - 1]) != tip["sha256"]:
        raise ProviderWaitError("provider_chain_invalid")
    if len(raws) == published + 1:
        if not recover:
            raise ProviderWaitError("provider_index_behind")
        _publish_index(foreground, index, op, events[-1], raws[-1])
    return Operation(authority, _sha(authority_raw), tuple(events), raws[-1])


def _append(
    foreground: Path, operation: Operation, **changes: object
) -> tuple[Operation, dict[str, object]]:
    """Publish the next immutable event then name it in the index."""
    tip = operation.tip
    event = {**{k: tip[k] for k in EVENT_FIELDS}, **changes}
    event.update(
        sequence=cast(int, tip["sequence"]) + 1,
        previous_sha256=_sha(operation.tip_raw),
        integrity_sha256="",
    )
    event = _sealed(event)
    raw = _encode(event)
    _event(raw, operation.id, cast(int, event["sequence"]), operation.tip_raw)
    _write(foreground, f"{_paths(operation.id)[1]}/{event['sequence']}.json", raw)
    _publish_index(foreground, _index(foreground), operation.id, event, raw)
    return Operation(
        operation.authority, operation.authority_sha256, (*operation.events, event), raw
    ), event


# ----------------------------------------------------------- classification


@dataclass(frozen=True)
class Classification:
    """Finite classifier result. ``outcome`` is the declared graph outcome name."""

    classification: str | None
    outcome: str


def classify(
    *,
    adapter_id: str,
    adapter_contract: str,
    observed_version: object,
    transport: str,
    writer_state: str,
    failure_code: object,
    native_diagnostic: Mapping[str, object] | None,
    bridge_ref: Mapping[str, object] | None,
    bridge_record: Mapping[str, object] | None,
    effects_resolved: bool,
) -> Classification:
    """Pure conservative classifier over retained, already-validated evidence.

    ``bridge_record`` is the verified published bridge JSON when ``bridge_ref``
    is nonnull, else None. No prose, stderr or message field is ever consulted.
    """
    if transport == "completed":
        raise ProviderWaitError("provider_not_failure")
    if writer_state != "stopped":
        return Classification("unknown_writer", "UNCERTAIN")
    if not effects_resolved:
        return Classification("unknown_effects", "UNCERTAIN")
    if transport == "timeout":
        return Classification("timeout", "ORDINARY_ERROR")
    if transport == "cancelled":
        return Classification("cancelled", "ORDINARY_ERROR")
    if native_diagnostic is None:
        if bridge_ref is not None or bridge_record is not None:
            return Classification(None, "UNCERTAIN")
        return Classification("ordinary_error", "ORDINARY_ERROR")
    if bridge_ref is None:
        # Native facts retained, handoff unavailable: no provider count/retry.
        return Classification("unclassified", "ORDINARY_ERROR")
    if bridge_record is None or (
        bridge_record.get("native_diagnostic") != dict(native_diagnostic)
        or bridge_record.get("transport") != transport
        or bridge_record.get("writer_state") != writer_state
        or bridge_record.get("failure_code") != failure_code
        or bridge_record.get("adapter_id") != adapter_id
        or bridge_record.get("adapter_contract") != adapter_contract
    ):
        return Classification(None, "UNCERTAIN")
    status = native_diagnostic.get("api_error_status")
    if (
        (adapter_id, adapter_contract, native_diagnostic.get("protocol")) != CLAUDE
        or not isinstance(observed_version, str)
        or observed_version not in CLAUDE_VERSIONS
        or not isinstance(failure_code, str)
        or failure_code not in CLAUDE_FAILURES
        or type(status) is not int
        or status not in CLAUDE_RULES
    ):
        return Classification("unclassified", "ORDINARY_ERROR")
    found = CLAUDE_RULES[status]
    return Classification(
        found, "PROVIDER_WAIT" if found in TIMED else "PROVIDER_HUMAN_REQUIRED"
    )


def _due(
    failed_at: str, streak: int, policy: Mapping[str, object], floor: str | None
) -> str | None:
    if streak >= DIAGNOSIS_STREAK:
        return None
    key = "first_retry_seconds" if streak == 1 else "second_retry_seconds"
    due = _time(failed_at, "provider_clock_invalid") + timedelta(
        seconds=cast(int, policy[key])
    )
    if floor is not None:
        due = max(due, _time(floor, "provider_floor_invalid"))
    return due.isoformat()


def _recorded_outcome(event: Mapping[str, object]) -> str:
    """Graph outcome implied by an already recorded failure fact (exact replay)."""
    if event["kind"] == "uncertainty_recorded":
        return "UNCERTAIN"
    if event["kind"] == "human_required" or event["phase"] == "human_required":
        return "PROVIDER_HUMAN_REQUIRED"
    return "PROVIDER_WAIT"


def _diagnosed(operation: Operation) -> bool:
    return any(e["kind"] == "diagnosis_reserved" for e in operation.events)


def classify_failure(
    foreground: Path,
    *,
    authority: Mapping[str, object],
    classification: Classification,
    occurrence_id: str,
    diagnostic_ref: Mapping[str, object] | None,
    failed_at: datetime,
    current_source_sha256: str,
    policy: Mapping[str, object],
    provider_floor: datetime | None = None,
) -> tuple[str, dict[str, object] | None]:
    """Record one exact terminal failure; returns ``(outcome, event_or_None)``.

    Ordinary fallback without an operation creates nothing. A positive provider
    failure creates the operation or counts once per exact ``diagnostic_ref``.
    Intervening ordinary/unclassified failures preserve the streak unchanged;
    uncertainty is recorded once and never counted.
    """
    sealed = _authority_body(authority)
    op = operation_id(sealed)
    if sealed["operation_id"] != op or _sealed(dict(sealed)) != dict(sealed):
        raise ProviderWaitError("provider_authority_invalid")
    if sealed["policy_sha256"] != policy.get("sha256"):
        raise ProviderWaitError("provider_authority_stale")
    _id(occurrence_id, "provider_occurrence_invalid")
    _digest(current_source_sha256, "provider_source_invalid")
    when = stamp(failed_at)
    floor = None if provider_floor is None else stamp(provider_floor)
    found = classification.classification
    if (
        found is not None
        and (not isinstance(found, str) or found not in CLASSIFICATIONS)
    ) or (
        not isinstance(classification.outcome, str)
        or classification.outcome not in OUTCOMES
    ):
        raise ProviderWaitError("provider_classification_invalid")
    positive = found in TIMED or found in HUMAN
    if positive and diagnostic_ref is None:
        raise ProviderWaitError("provider_bridge_required")
    index = _index(foreground)
    exists = op in cast(dict[str, object], index["operations"])
    if not exists:
        if not positive:
            return classification.outcome, None
        if (
            index["session_id"] != sealed["session_id"]
            or index["policy_sha256"] != sealed["policy_sha256"]
        ):
            raise ProviderWaitError("provider_authority_invalid")
        authority_path, folder = _paths(op)
        _write(foreground, authority_path, _encode(sealed))
        events, raws = _chain(foreground, op, None)
        if events:
            # Crash after event 1, before the index: recover only that unique
            # matching first fact, keeping its original time and outcome.
            prior = events[0]
            if len(events) != 1 or (
                prior["diagnostic_ref"],
                prior["occurrence_id"],
                prior["classification"],
                prior["current_source_sha256"],
            ) != (
                _ref(diagnostic_ref, "provider_ref_invalid"),
                occurrence_id,
                found,
                current_source_sha256,
            ):
                raise ProviderWaitError("provider_unindexed_conflict")
            _publish_index(foreground, index, op, prior, raws[0])
            return _recorded_outcome(prior), prior
        streak = 1 if found in TIMED else 0
        first = _sealed(
            {
                "format": EVENT,
                "operation_id": op,
                "sequence": 1,
                "previous_sha256": None,
                "kind": "failure_classified",
                "attempt": 1,
                "occurrence_id": occurrence_id,
                "observation_ref": None,
                "diagnostic_ref": _ref(diagnostic_ref, "provider_ref_invalid"),
                "classifier_revision": CLASSIFIER,
                "classification": found,
                "failed_at": when,
                "streak": streak,
                "provider_floor": floor,
                "due_at": _due(when, streak, policy, floor) if streak else None,
                "phase": "reconcile" if found in TIMED else "human_required",
                "current_source_sha256": current_source_sha256,
                "file_delta_ref": None,
                "effects_ref": None,
                "restoration_ref": None,
                "preparation_ref": None,
                "core_locator": None,
                "control_refs": [],
                "integrity_sha256": "",
            }
        )
        raw = _encode(first)
        _write(foreground, f"{folder}/1.json", raw)
        _publish_index(foreground, index, op, first, raw)
        return classification.outcome, first
    operation = load_operation(foreground, op)
    for event in operation.events:
        if (
            diagnostic_ref is not None
            and event["diagnostic_ref"] == dict(diagnostic_ref)
            and event["kind"]
            in {"failure_classified", "human_required", "uncertainty_recorded"}
        ):
            return _recorded_outcome(event), event
    tip = operation.tip
    if tip["phase"] in {"succeeded", "cancelled"}:
        raise ProviderWaitError("provider_operation_closed")
    if classification.outcome == "UNCERTAIN":
        if tip["kind"] == "uncertainty_recorded" and tip["occurrence_id"] == (
            occurrence_id
        ):
            return "UNCERTAIN", tip
        for event in operation.events:
            # Exact replay of a null-ref uncertainty already recorded earlier.
            if (
                diagnostic_ref is None
                and event["kind"] == "uncertainty_recorded"
                and event["diagnostic_ref"] is None
                and event["occurrence_id"] == occurrence_id
                and (found is None or event["classification"] == found)
            ):
                return "UNCERTAIN", event
        return "UNCERTAIN", _append(
            foreground,
            operation,
            kind="uncertainty_recorded",
            occurrence_id=occurrence_id,
            diagnostic_ref=_ref(diagnostic_ref, "provider_ref_invalid"),
            observation_ref=None,
            phase="reconcile",
            classification=found if found is not None else tip["classification"],
        )[1]
    if not positive:
        return classification.outcome, None
    if tip["phase"] not in {"retry_reserved", "reconcile"} or tip["kind"] == (
        "failure_classified"
    ):
        raise ProviderWaitError("provider_attempt_unreserved")
    if tip["failed_at"] is not None and _time(when, "provider_clock_invalid") < _time(
        tip["failed_at"], "provider_clock_invalid"
    ):
        raise ProviderWaitError("provider_clock_regressed")
    streak = cast(int, tip["streak"]) + (1 if found in TIMED else 0)
    after_diagnosis = _diagnosed(operation) and found in TIMED
    human = found in HUMAN or after_diagnosis
    kind = "human_required" if human else "failure_classified"
    return ("PROVIDER_HUMAN_REQUIRED" if human else classification.outcome), _append(
        foreground,
        operation,
        kind=kind,
        occurrence_id=occurrence_id,
        diagnostic_ref=_ref(diagnostic_ref, "provider_ref_invalid"),
        observation_ref=None,
        classification=found,
        failed_at=when,
        streak=streak,
        provider_floor=floor,
        due_at=None if human else _due(when, streak, policy, floor),
        phase="human_required" if human else "reconcile",
        current_source_sha256=current_source_sha256,
        file_delta_ref=None,
        effects_ref=None,
        restoration_ref=None,
        preparation_ref=None,
    )[1]


# --------------------------------------------------------------- observation


def _retain(foreground: Path, value: object) -> dict[str, object]:
    """Content-addressed immutable copy of one public fact the record cites."""
    raw = _encode(value)
    return _write(foreground, f"{ROOT}/facts/{_sha(raw)}.json", raw)


def observation_facts(
    view: Mapping[str, object],
    *,
    authority: Mapping[str, object],
    failure: Mapping[str, object],
    instance_id: str,
    run_id: str,
    state_home: str,
    plane_id: str,
    invocation_id: str,
    expected_state_version: int,
    foreground: Path,
) -> dict[str, object]:
    """Validate the full public Core view against the operation; return facts.

    Reads the actual bridge bytes named by Core's completion evidence beneath the
    invocation's recorded ``cwd``. Writes nothing. Any mismatch refuses.
    """
    from .router import native_diagnostic as nd

    def one(rows: object, key: str) -> dict[str, object]:
        if not isinstance(rows, list):
            raise ProviderWaitError("provider_observation_mismatch")
        found = [
            r
            for r in cast(list[dict[str, object]], rows)
            if isinstance(r, dict) and r.get(key) == invocation_id
        ]
        if len(found) != 1:
            raise ProviderWaitError("provider_observation_mismatch")
        return found[0]

    owner = cast(dict[str, object], authority["native_owner"])
    binding = cast(dict[str, object], authority["binding"])
    node = cast(str, owner["node_id"])
    if (
        view.get("state_version") != expected_state_version
        or view.get("quiescent") is not True
        or not isinstance(view.get("planes"), dict)
        or plane_id != owner["plane_id"]
    ):
        raise ProviderWaitError("provider_observation_mismatch")
    plane = cast(dict[str, dict[str, object]], view["planes"]).get(plane_id)
    if (
        not isinstance(plane, dict)
        or plane.get("status") != "paused"
        or plane.get("active_invocation") is not None
        or plane.get("resume_target") != f"provider_due_{node}"
    ):
        raise ProviderWaitError("provider_continuation_mismatch")
    invocation = one(view.get("invocations"), "id")
    completion = one(view.get("completions"), "invocation_id")
    transition = one(view.get("transitions"), "completion_id")
    launch = invocation.get("launch")
    evidence = invocation.get("adapter_evidence")
    if not isinstance(launch, dict) or not isinstance(evidence, dict):
        raise ProviderWaitError("provider_observation_mismatch")
    details = launch.get("details")
    if (
        invocation.get("plane_id") != plane_id
        or invocation.get("node") != node
        or invocation.get("revision_id") != launch.get("producing_revision")
        or launch.get("adapter_id") != binding["adapter_id"]
        or launch.get("adapter_contract") != binding["contract"]
        or _sha(_encode(launch.get("settings"))) != binding["config_sha256"]
        or not isinstance(details, dict)
        or completion.get("node") != node
        or completion.get("transport") == "completed"
        or transition.get("node") != node
        or transition.get("target") != f"classify_provider_{node}"
        or transition.get("revision_id") != invocation.get("revision_id")
        or type(transition.get("version")) is not int
    ):
        raise ProviderWaitError("provider_observation_mismatch")
    if completion.get("effective_writer") != "stopped":
        raise ProviderWaitError("provider_writer_unresolved")
    if Path(str(invocation.get("cwd"))) != foreground:
        raise ProviderWaitError("provider_workspace_mismatch")
    bridge_ref = evidence.get("diagnostic_bridge")
    if bridge_ref is None or failure.get("diagnostic_ref") != bridge_ref:
        raise ProviderWaitError("provider_bridge_required")
    try:
        bridge = nd.launch_bridge(details)
        ref = nd.DiagnosticBridgeRef(
            cast(str, cast(dict[str, object], bridge_ref)["path"]),
            cast(str, cast(dict[str, object], bridge_ref)["sha256"]),
        )
        raw = _read(foreground, ref.path, "provider_bridge_required")
        record = nd.verify_record(
            raw,
            ref,
            bridge,
            {
                "context_id": failure.get("occurrence_id"),
                "invocation_id": invocation_id,
                "plane_id": plane_id,
                "node_id": node,
                "producing_revision": invocation.get("revision_id"),
                "adapter_id": binding["adapter_id"],
                "adapter_contract": binding["contract"],
                "launch_sha256": nd.digest(nd.canonical(details)),
                "transport": completion.get("transport"),
                "writer_state": completion.get("writer"),
                "native_diagnostic": evidence.get("native_diagnostic"),
                "evidence_format": evidence.get("format"),
            },
        )
    except (KeyError, TypeError) as exc:
        raise ProviderWaitError("provider_bridge_required") from exc
    except ProviderWaitError:
        raise
    except Exception as exc:
        if isinstance(getattr(exc, "code", None), str):
            raise ProviderWaitError("provider_bridge_mismatch") from exc
        raise
    del record
    if any(key not in completion for key in COMPLETION_FACT_KEYS):
        raise ProviderWaitError("provider_observation_mismatch")
    public = {key: completion[key] for key in COMPLETION_FACT_KEYS}
    return {
        "instance_id": instance_id,
        "run_id": run_id,
        "state_home": state_home,
        "workspace": str(foreground),
        "expected_state_version": expected_state_version,
        "observed_state_version": view["state_version"],
        "active_revision": view.get("active_revision"),
        "plane_id": plane_id,
        "invocation_id": invocation_id,
        "completion": public,
        "transition": dict(transition),
        "bridge_ref": dict(cast(dict[str, object], bridge_ref)),
        "writer_state": "stopped",
    }


def observe_provider(
    foreground: Path,
    *,
    operation_id: str,
    facts: Mapping[str, object],
    claim: object = None,
    observed_at: datetime,
) -> dict[str, object]:
    """Publish one immutable observation plus an ``observed`` event (replayable).

    Returns ``{"observation", "event", "tip"}``: the exact observation and its
    ``observed`` event, plus the operation's current tip for status reporting.
    """
    operation = load_operation(foreground, operation_id)
    tip = operation.tip
    failure = _latest_failure(operation)
    if tip["phase"] in {"succeeded", "cancelled"}:
        raise ProviderWaitError("provider_operation_closed")
    if tip["phase"] in {"retry_reserved", "diagnosis_reserved"}:
        raise ProviderWaitError("provider_attempt_reserved")
    locator = {k: facts[k] for k in LOCATOR_FIELDS}
    if tip["core_locator"] is not None and tip["core_locator"] != locator:
        raise ProviderWaitError("provider_locator_mismatch")
    if facts["bridge_ref"] != failure["diagnostic_ref"]:
        raise ProviderWaitError("provider_bridge_mismatch")
    if any(
        event["kind"] == "uncertainty_recorded"
        and cast(int, event["sequence"]) > cast(int, failure["sequence"])
        for event in operation.events
    ):
        # Never relabel a later unresolved uncertainty as waiting.
        raise ProviderWaitError("provider_reconciliation_required")
    body: dict[str, object] = {
        "format": OBSERVATION,
        "observation_id": "",
        "operation_id": operation.id,
        "authority_sha256": operation.authority_sha256,
        **{
            k: facts[k]
            for k in (
                "instance_id",
                "run_id",
                "state_home",
                "workspace",
                "expected_state_version",
                "observed_state_version",
                "active_revision",
                "plane_id",
                "invocation_id",
                "bridge_ref",
                "writer_state",
            )
        },
        "completion_ref": _retain(foreground, facts["completion"]),
        "transition_ref": _retain(foreground, facts["transition"]),
        "claim_ref": None if claim is None else _retain(foreground, claim),
        # A Core snapshot proves writer closure only; effects stay unproven here.
        "effects_ref": None,
        "observed_at": stamp(observed_at),
        "integrity_sha256": "",
    }
    # The identity deliberately excludes time, so a retry after a crash between
    # publication and its event reuses the published bytes and their time.
    body["observation_id"] = _sha(_encode({**body, "observed_at": None}))
    relative = f"{ROOT}/observations/{body['observation_id']}.json"
    if _exists(foreground, relative):
        raw = _read(foreground, relative, "provider_observation_invalid")
        observation = _strict(
            raw, OBSERVATION_FIELDS, OBSERVATION, "provider_observation_invalid"
        )
        keep = {"observed_at", "integrity_sha256"}
        if {k: v for k, v in observation.items() if k not in keep} != {
            k: v for k, v in body.items() if k not in keep
        }:
            raise ProviderWaitError("provider_observation_conflict")
        ref: dict[str, object] = {"path": relative, "sha256": _sha(raw)}
        for event in operation.events:
            if event["kind"] == "observed" and event["observation_ref"] == ref:
                return {"observation": observation, "event": event, "tip": tip}
    else:
        observation = _sealed(body)
        ref = _write(foreground, relative, _encode(observation))
    phase = "waiting"
    if failure["phase"] == "human_required":
        phase = "human_required"
    elif cast(int, failure["streak"]) >= DIAGNOSIS_STREAK:
        phase = "human_required" if _diagnosed(operation) else "diagnosis_required"
    _, event = _append(
        foreground,
        operation,
        kind="observed",
        observation_ref=ref,
        diagnostic_ref=failure["diagnostic_ref"],
        core_locator=locator,
        phase=phase,
        occurrence_id=failure["occurrence_id"],
    )
    return {"observation": observation, "event": event, "tip": event}


def _latest_failure(operation: Operation) -> dict[str, object]:
    for event in reversed(operation.events):
        if (
            event["kind"] in {"failure_classified", "human_required"}
            and event["diagnostic_ref"] is not None
        ):
            return event
    raise ProviderWaitError("provider_failure_missing")


def observe_snapshot(
    project: Path,
    *,
    state_home: Path,
    instance_id: str,
    run_id: str,
    plane_id: str,
    operation_id: str,
    invocation_id: str,
    expected_state_version: int,
    observed_at: datetime,
    service_home: Path | None = None,
    task: str | None = None,
) -> dict[str, object]:
    """``lad provider-observe``: exclusive public snapshot, then publication.

    Service-owned copies first hold the existing nonblocking ``target_lock`` and
    revalidate the active task/claim; standalone copies refuse service selectors.
    Inside the snapshot only reads and Plus record publication happen.
    """
    project = project.expanduser().absolute()
    state_home = state_home.expanduser().absolute()
    foreground = project / "foreground"
    if (service_home is None) != (task is None):
        raise ProviderWaitError("provider_service_task_pair")
    try:
        subject = ef.service_subject(project)
    except ef.EngineeringError as exc:
        raise ProviderWaitError(exc.code) from exc
    # Read-only before the snapshot: no index recovery or other publication.
    authority = _authority(foreground, operation_id)[3]
    service = authority["service"]
    if (subject is None) != (service is None) or (subject is None) != (
        service_home is None
    ):
        raise ProviderWaitError("provider_service_selector_mismatch")
    if subject is not None:
        expected = {
            "service_id": subject["service_id"],
            "task_id": subject["task_id"],
            "claim_id": subject["claim_sha256"],
        }
        if service != expected or task != subject["task_id"]:
            raise ProviderWaitError("provider_service_selector_mismatch")
        from .lad_service import Service, target_lock, validate_shared_target

        owner = Service(cast(Path, service_home))
        if owner.service_id != subject["service_id"]:
            raise ProviderWaitError("provider_service_selector_mismatch")
        with target_lock(owner.config, protected_roots=(owner.home,)):
            validate_shared_target(subject, project=project)
            return _observe_locked(
                project,
                foreground,
                state_home,
                instance_id,
                run_id,
                plane_id,
                authority,
                invocation_id,
                expected_state_version,
                observed_at,
                subject,
            )
    return _observe_locked(
        project,
        foreground,
        state_home,
        instance_id,
        run_id,
        plane_id,
        authority,
        invocation_id,
        expected_state_version,
        observed_at,
        None,
    )


def _observe_locked(
    project: Path,
    foreground: Path,
    state_home: Path,
    instance_id: str,
    run_id: str,
    plane_id: str,
    authority: dict[str, object],
    invocation_id: str,
    expected_state_version: int,
    observed_at: datetime,
    claim: object,
) -> dict[str, object]:
    from .router import control

    policy = load_policy(
        _read(project, "context/provider-policy.json", "provider_policy_invalid")
    )
    if policy["sha256"] != authority["policy_sha256"]:
        raise ProviderWaitError("provider_authority_stale")
    with control.exclusive_run_snapshot(
        instance_id,
        run_id,
        state_home=state_home,
        expected_state_version=expected_state_version,
    ) as view:
        identity = control.inspect_instance(instance_id, state_home=state_home)
        if (
            Path(str(identity["project_root"])) != project
            or Path(str(identity["workflow_file"])) != project / "workflow.json"
        ):
            raise ProviderWaitError("provider_project_identity")
        operation = load_operation(foreground, cast(str, authority["operation_id"]))
        facts = observation_facts(
            view,
            authority=operation.authority,
            failure=_latest_failure(operation),
            instance_id=instance_id,
            run_id=run_id,
            state_home=str(state_home),
            plane_id=plane_id,
            invocation_id=invocation_id,
            expected_state_version=expected_state_version,
            foreground=foreground,
        )
        result = observe_provider(
            foreground,
            operation_id=operation.id,
            facts=facts,
            claim=claim,
            observed_at=observed_at,
        )
    tip = cast(dict[str, object], result["tip"])
    observation = cast(dict[str, object], result["observation"])
    return {
        "operation_id": operation.id,
        "observation_id": observation["observation_id"],
        "observed_state_version": facts["observed_state_version"],
        "phase": tip["phase"],
        "due_at": tip["due_at"],
        "operator_return_required": True,
    }


# ----------------------------------------------------------------------- due


def restoration_record(**values: object) -> dict[str, object]:
    """Seal an explicit restoration (fields except format/integrity)."""
    record = _sealed({"format": RESTORATION, **values, "integrity_sha256": ""})
    _restoration(_encode(record))
    return record


def _restoration(raw: bytes) -> dict[str, object]:
    code = "provider_restoration_invalid"
    record = _strict(raw, RESTORATION_FIELDS, RESTORATION, code)
    _digest(record["operation_id"], code)
    _digest(record["policy_sha256"], code)
    if not isinstance(record["kind"], str) or record["kind"] not in {
        "public_observation",
        "operator_confirmation",
    }:
        raise ProviderWaitError(code)
    _text(record["actor"], code)
    _id(record["request_id"], code)
    _time(record["observed_at"], code)
    _text(record["statement"], code)
    if record["kind"] == "public_observation" and record["evidence_ref"] is None:
        raise ProviderWaitError(code)
    _ref(record["evidence_ref"], code)
    return record


def check_provider_due(
    foreground: Path,
    *,
    operation_id: str,
    observation_id: str,
    now: datetime,
    policy: Mapping[str, object],
    restoration: bytes | None = None,
) -> dict[str, object]:
    """Read/record-only due check; no native call and no Core control."""
    operation = load_operation(foreground, operation_id)
    when = _time(stamp(now), "provider_clock_invalid")
    if operation.authority["policy_sha256"] != policy.get("sha256"):
        raise ProviderWaitError("provider_authority_stale")
    tip = operation.tip
    observed = [e for e in operation.events if e["kind"] == "observed"]
    if not observed or tip["core_locator"] is None:
        raise ProviderWaitError("provider_observation_required")
    current = observed[-1]
    observation_ref = cast(dict[str, object], current["observation_ref"])
    if observation_ref["path"] != f"{ROOT}/observations/{observation_id}.json":
        raise ProviderWaitError("provider_observation_stale")
    _load_ref(
        foreground,
        observation_ref,
        OBSERVATION_FIELDS,
        OBSERVATION,
        "provider_observation_invalid",
    )
    failure = _latest_failure(operation)
    if current["diagnostic_ref"] != failure["diagnostic_ref"]:
        raise ProviderWaitError("provider_observation_stale")
    if tip["phase"] in {
        "succeeded",
        "cancelled",
        "retry_reserved",
        "diagnosis_reserved",
    }:
        # Duplicate resume after reservation/closure: no new eligibility.
        return _due_result(operation, tip, observation_id, eligible=False)
    if tip["phase"] == "reconcile":
        raise ProviderWaitError("provider_reconciliation_required")
    failed_at = _time(failure["failed_at"], "provider_clock_invalid")
    if restoration is not None:
        record = _restoration(restoration)
        if (
            record["operation_id"] != operation.id
            or record["policy_sha256"] != operation.authority["policy_sha256"]
            or _time(record["observed_at"], "provider_restoration_invalid") < failed_at
        ):
            raise ProviderWaitError("provider_restoration_mismatch")
        evidence = record["evidence_ref"]
        if evidence is not None:
            pinned = cast(dict[str, object], evidence)
            raw = _read(
                foreground, cast(str, pinned["path"]), "provider_restoration_mismatch"
            )
            if _sha(raw) != pinned["sha256"]:
                raise ProviderWaitError("provider_restoration_mismatch")
        relative = f"{ROOT}/restorations/{_sha(restoration)}.json"
        ref: dict[str, object] = {"path": relative, "sha256": _sha(restoration)}
        if not any(
            e["restoration_ref"] == ref
            for e in operation.events
            if cast(int, e["sequence"]) > cast(int, failure["sequence"])
        ):
            # A new restoration cannot claim a time after the injected reading.
            if _time(record["observed_at"], "provider_restoration_invalid") > when:
                raise ProviderWaitError("provider_restoration_mismatch")
            _write(foreground, relative, restoration)
            operation, tip = _append(
                foreground, operation, kind="restoration_recorded", restoration_ref=ref
            )
    # The latest restoration fact since the failure applies. The first
    # diagnosis_recorded found ends the scan even when its ref is null, so an
    # older restoration cannot outlive an unresolved diagnosis; only a
    # restoration recorded after it can release that hold.
    restoration_ref: object = None
    for event in reversed(operation.events):
        if cast(int, event["sequence"]) <= cast(int, failure["sequence"]):
            break
        if event["kind"] == "diagnosis_recorded":
            restoration_ref = event["restoration_ref"]
            break
        if event["kind"] == "restoration_recorded":
            restoration_ref = event["restoration_ref"]
            break
    floor = tip["provider_floor"]
    # A restoration can waive the local delay, never a later provider floor.
    floor_ok = floor is None or when >= _time(floor, "provider_floor_invalid")
    phase = cast(str, tip["phase"])
    if phase == "diagnosis_required":
        eligible = not _diagnosed(operation)
        phase = "diagnosis_required" if eligible else "human_required"
    elif phase == "human_required":
        eligible = restoration_ref is not None and floor_ok
        phase = "retry_ready" if eligible else "human_required"
    else:
        # A backward clock reading never reaches the persisted absolute due time.
        timed = tip["due_at"] is not None and when >= _time(
            tip["due_at"], "provider_clock_invalid"
        )
        eligible = floor_ok and (
            timed or restoration_ref is not None or phase == "retry_ready"
        )
        phase = "retry_ready" if eligible else "waiting"
    if (
        tip["kind"] == "due_checked"
        and tip["phase"] == phase
        and tip["observation_ref"] == observation_ref
    ):
        return _due_result(operation, tip, observation_id, eligible=eligible)
    _, event = _append(
        foreground,
        operation,
        kind="due_checked",
        phase=phase,
        observation_ref=observation_ref,
        restoration_ref=restoration_ref,
    )
    return _due_result(operation, event, observation_id, eligible=eligible)


def _due_result(
    operation: Operation,
    event: Mapping[str, object],
    observation_id: str,
    *,
    eligible: bool,
) -> dict[str, object]:
    return {
        "operation_id": operation.id,
        "observation_id": observation_id,
        "phase": event["phase"],
        "due_at": event["due_at"],
        "operator_return_required": True,
        "eligible": eligible,
    }


def provider_due(
    project: Path,
    *,
    operation_id: str,
    observation_id: str,
    now: datetime,
    restoration: Path | None = None,
) -> dict[str, object]:
    """``lad provider-due`` with the copy's captured policy bytes."""
    project = project.expanduser().absolute()
    policy = load_policy(
        _read(project, "context/provider-policy.json", "provider_policy_invalid")
    )
    raw = None
    if restoration is not None:
        try:
            raw = ef.read_regular(restoration.expanduser().absolute())
        except ef.EngineeringError as exc:
            raise ProviderWaitError("provider_restoration_invalid") from exc
    return check_provider_due(
        project / "foreground",
        operation_id=operation_id,
        observation_id=observation_id,
        now=now,
        policy=policy,
        restoration=raw,
    )


# --------------------------------------------------------------- reservation


def reserve_provider_attempt(
    foreground: Path,
    *,
    operation_id: str,
    kind: str,
    previous_occurrence: str,
    new_occurrence: str,
    input_sha256: str,
    current_source_sha256: str,
    file_delta_ref: Mapping[str, object],
    effects_ref: Mapping[str, object],
) -> dict[str, object]:
    """Reserve exactly one attempt from an eligible due check; replay reuses it.

    ``previous_occurrence`` must be the latest recorded failure's occurrence and
    ``new_occurrence`` must be unused by this operation. ``file_delta_ref`` and
    ``effects_ref`` are existing ``{path,sha256}`` refs beneath ``foreground``;
    C2 checks only that the bytes exist with that digest. Their semantic
    reconciliation (stopped writer, scoped delta, resolved external effects) is
    the C3 caller's proof. Eligibility from a due check is not that clearance.
    """
    if not isinstance(kind, str) or kind not in {"retry", "diagnosis"}:
        raise ProviderWaitError("provider_preparation_invalid")
    operation = load_operation(foreground, operation_id)
    _id(previous_occurrence, "provider_preparation_invalid")
    _id(new_occurrence, "provider_preparation_invalid")
    _digest(input_sha256, "provider_preparation_invalid")
    _digest(current_source_sha256, "provider_source_invalid")
    delta = _ref(file_delta_ref, "provider_effects_unresolved")
    effects = _ref(effects_ref, "provider_effects_unresolved")
    if delta is None or effects is None:
        raise ProviderWaitError("provider_effects_unresolved")
    for pinned in (delta, effects):
        try:
            ef.load_ref(foreground, pinned, "provider_effects_unresolved")
        except ef.EngineeringError as exc:
            raise ProviderWaitError("provider_effects_unresolved") from exc
    if previous_occurrence != _latest_failure(operation)["occurrence_id"]:
        raise ProviderWaitError("provider_reservation_conflict")
    tip = operation.tip
    reserved = "retry_reserved" if kind == "retry" else "diagnosis_reserved"
    if tip["kind"] == reserved:
        prior = _load_ref(
            foreground,
            tip["preparation_ref"],
            PREPARATION_FIELDS,
            PREPARATION,
            "provider_preparation_invalid",
        )
        if (
            prior["new_occurrence"],
            prior["previous_occurrence"],
            prior["input_sha256"],
            tip["current_source_sha256"],
            tip["file_delta_ref"],
            tip["effects_ref"],
        ) != (
            new_occurrence,
            previous_occurrence,
            input_sha256,
            current_source_sha256,
            delta,
            effects,
        ):
            raise ProviderWaitError("provider_reservation_conflict")
        return {"preparation": prior, "event": tip}
    ready = "retry_ready" if kind == "retry" else "diagnosis_required"
    if tip["kind"] != "due_checked" or tip["phase"] != ready:
        raise ProviderWaitError("provider_not_ready")
    if kind == "diagnosis" and _diagnosed(operation):
        raise ProviderWaitError("provider_diagnosis_used")
    used = {operation.authority["original_occurrence"]} | {
        event["occurrence_id"] for event in operation.events
    }
    if new_occurrence in used:
        raise ProviderWaitError("provider_reservation_conflict")
    attempt = cast(int, tip["attempt"]) + 1
    body: dict[str, object] = {
        "format": PREPARATION,
        "preparation_id": "",
        "operation_id": operation.id,
        "authority_sha256": operation.authority_sha256,
        "attempt": attempt,
        "kind": kind,
        "previous_occurrence": previous_occurrence,
        "new_occurrence": new_occurrence,
        "input_sha256": input_sha256,
        "event_ref": {
            "path": f"{_paths(operation.id)[1]}/{tip['sequence']}.json",
            "sha256": _sha(operation.tip_raw),
        },
        "integrity_sha256": "",
    }
    body["preparation_id"] = _sha(_encode(body))
    preparation = _sealed(body)
    ref = _write(
        foreground,
        f"{ROOT}/preparations/{body['preparation_id']}.json",
        _encode(preparation),
    )
    _, event = _append(
        foreground,
        operation,
        kind=reserved,
        attempt=attempt,
        occurrence_id=new_occurrence,
        phase=reserved,
        preparation_ref=ref,
        file_delta_ref=delta,
        effects_ref=effects,
        current_source_sha256=current_source_sha256,
    )
    return {"preparation": preparation, "event": event}


def record_diagnosis(
    foreground: Path,
    *,
    operation_id: str,
    occurrence_id: str,
    resolved: bool,
    restoration_ref: Mapping[str, object] | None,
) -> dict[str, object]:
    """Record the one diagnosis outcome; unresolved/unavailable holds for a human."""
    operation = load_operation(foreground, operation_id)
    tip = operation.tip
    if tip["kind"] == "diagnosis_recorded" and tip["occurrence_id"] == occurrence_id:
        return tip
    if tip["kind"] != "diagnosis_reserved" or tip["occurrence_id"] != occurrence_id:
        raise ProviderWaitError("provider_diagnosis_unreserved")
    restored = _ref(restoration_ref, "provider_restoration_invalid")
    if type(resolved) is not bool:
        raise ProviderWaitError("provider_diagnosis_invalid")
    if resolved and restored is None:
        raise ProviderWaitError("provider_restoration_required")
    if not resolved and restored is not None:
        # An unresolved diagnosis never installs restoration authority.
        raise ProviderWaitError("provider_diagnosis_invalid")
    if restored is not None:
        record = _load_ref(
            foreground,
            restored,
            RESTORATION_FIELDS,
            RESTORATION,
            "provider_restoration_invalid",
        )
        failure = _latest_failure(operation)
        if (
            record["operation_id"] != operation.id
            or record["policy_sha256"] != operation.authority["policy_sha256"]
            or _time(record["observed_at"], "provider_restoration_invalid")
            < _time(failure["failed_at"], "provider_clock_invalid")
        ):
            raise ProviderWaitError("provider_restoration_mismatch")
    return _append(
        foreground,
        operation,
        kind="diagnosis_recorded",
        phase="retry_ready" if resolved else "human_required",
        restoration_ref=restored,
        file_delta_ref=None,
        effects_ref=None,
        preparation_ref=None,
    )[1]


# ------------------------------------------------------------------- success


SUCCESS_FIELDS = (
    "role",
    "packet_lineage",
    "pending_route",
    "binding",
    "source_sha256",
    "current_source_sha256",
    "occurrence_id",
    "input_sha256",
    "lineage",
    "native_owner",
    "transport",
    "writer_state",
    "accepted",
)


def reconcile_provider_success(
    foreground: Path, *, operation_id: str, success: Mapping[str, object]
) -> dict[str, object]:
    """Publish ``succeeded`` once from validated same-operation success facts.

    ``success`` has exactly ``SUCCESS_FIELDS``. ``lineage`` is either
    ``{"kind":"retry","preparation_ref":REF}`` or ``{"kind":"replacement",
    "chain":[OCCURRENCE,...]}`` whose head is a generation occurrence of this
    operation and whose tail is ``occurrence_id`` (the exact existing
    ``replacement_of`` links). Diagnosis, Learning or unrelated success refuses
    and leaves the operation unchanged.

    ``source_sha256`` is the operation's original captured source and must equal
    the authority. ``current_source_sha256`` is the successful occurrence's
    reconciled scoped source digest. For ``retry`` it must be the original source
    or one recorded in this operation's chain. For ``replacement`` an intervening
    nonprovider failure may legitimately have changed scoped source, so C2 checks
    only its digest form; the C3 caller proves it through the same existing
    replacement-intent links that prove ``chain``.
    """
    facts = _fields(success, SUCCESS_FIELDS, "provider_success_invalid")
    operation = load_operation(foreground, operation_id)
    authority = operation.authority
    tip = operation.tip
    occurrence = _id(facts["occurrence_id"], "provider_success_invalid")
    if tip["kind"] == "succeeded":
        if tip["occurrence_id"] != occurrence:
            raise ProviderWaitError("provider_success_conflict")
        return tip
    if any(
        facts[k] != authority[k]
        for k in ("role", "packet_lineage", "pending_route", "binding", "source_sha256")
    ):
        raise ProviderWaitError("provider_success_unrelated")
    current = _digest(facts["current_source_sha256"], "provider_success_invalid")
    sources = {authority["source_sha256"]} | {
        e["current_source_sha256"] for e in operation.events
    }
    if (
        facts["transport"] != "completed"
        or facts["writer_state"] != "stopped"
        or facts["accepted"] is not True
    ):
        raise ProviderWaitError("provider_success_unverified")
    owner = _fields(facts["native_owner"], OWNER_FIELDS, "provider_success_invalid")
    authority_owner = cast(dict[str, object], authority["native_owner"])
    if (owner["node_id"], owner["plane_id"]) != (
        authority_owner["node_id"],
        authority_owner["plane_id"],
    ):
        raise ProviderWaitError("provider_success_unrelated")
    preparations: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    for event in operation.events:
        if event["preparation_ref"] is not None and event["kind"] in {
            "retry_reserved",
            "diagnosis_reserved",
        }:
            prep = _load_ref(
                foreground,
                event["preparation_ref"],
                PREPARATION_FIELDS,
                PREPARATION,
                "provider_preparation_invalid",
            )
            preparations[cast(str, prep["new_occurrence"])] = (prep, event)
    diagnosis = {k for k, (p, _) in preparations.items() if p["kind"] == "diagnosis"}
    generations = {cast(str, authority["original_occurrence"])} | {
        k for k, (p, _) in preparations.items() if p["kind"] == "retry"
    }
    lineage = facts["lineage"]
    if not isinstance(lineage, dict):
        raise ProviderWaitError("provider_success_invalid")
    if lineage.get("kind") == "retry" and set(lineage) == {"kind", "preparation_ref"}:
        found = preparations.get(occurrence)
        if (
            current not in sources
            or found is None
            or found[0]["kind"] != "retry"
            or found[1]["preparation_ref"] != lineage["preparation_ref"]
            or found[0]["input_sha256"] != facts["input_sha256"]
        ):
            raise ProviderWaitError("provider_success_unrelated")
    elif lineage.get("kind") == "replacement" and set(lineage) == {"kind", "chain"}:
        chain = lineage["chain"]
        if not isinstance(chain, list):
            raise ProviderWaitError("provider_success_invalid")
        for item in chain:
            _id(item, "provider_success_invalid")
        if (
            len(chain) < 2
            or len(set(map(str, chain))) != len(chain)
            or chain[0] not in generations
            # Only the head may be a generation: a retry occurrence later in the
            # chain is a mislabelled retry and must use the retry fences.
            or any(item in generations for item in chain[1:])
            or chain[-1] != occurrence
            or any(item in diagnosis for item in chain)
        ):
            raise ProviderWaitError("provider_success_unrelated")
    else:
        raise ProviderWaitError("provider_success_invalid")
    if occurrence in diagnosis:
        raise ProviderWaitError("provider_success_unrelated")
    return _append(
        foreground,
        operation,
        kind="succeeded",
        phase="succeeded",
        occurrence_id=occurrence,
        file_delta_ref=None,
        effects_ref=None,
        preparation_ref=None,
        restoration_ref=None,
        current_source_sha256=current,
    )[1]
