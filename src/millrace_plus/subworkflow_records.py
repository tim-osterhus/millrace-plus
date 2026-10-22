"""Strict local call records and short filesystem locks; no Core dependency."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from . import catalog

__all__ = [
    "SubworkflowError",
    "Journal",
    "parse_request",
    "atomic_json",
    "canonical",
    "digest",
    "request_id",
    "envelope",
    "timestamp",
    "seconds_left",
    "canonical_path",
    "overlaps",
    "mapping",
    "validate_journal",
]

# Fixed forever for v1. Operation IDs bind canonical home/key and operation.
NAMESPACE = uuid.UUID("d0ad2e51-b41f-5a75-886a-a17623fb5db4")
REQUEST_FORMAT = "millrace-plus-subworkflow-request/v1"
JOURNAL_FORMAT = "millrace-plus-subworkflow-call/v1"
PHASES = {
    "prepared",
    "creating",
    "created",
    "starting",
    "observing",
    "attention_required",
    "cancel_requested",
    "terminal",
    "cleanup_required",
}
KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")


class SubworkflowError(Exception):
    """Expected helper refusal with a stable, token-free diagnostic code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise SubworkflowError("invalid_object")
    return cast(dict[str, object], value)


def canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise SubworkflowError("invalid_json") from exc


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def public_request_digest(value: object) -> str:
    """Core request/v2 uses its existing ASCII-escaped canonical JSON digest."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _json(raw: bytes, limit: int) -> object:
    if len(raw) > limit:
        raise SubworkflowError("record_too_large")
    try:
        return catalog.strict_json(raw)
    except catalog.CatalogError as exc:
        raise SubworkflowError("invalid_json") from exc


def _fields(
    value: object, required: set[str], optional: set[str] = frozenset()
) -> dict[str, object]:
    obj = mapping(value)
    if not required <= obj.keys() or obj.keys() - required - optional:
        raise SubworkflowError("invalid_fields")
    return obj


def _string(value: object, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\0" in value:
        raise SubworkflowError("invalid_string")
    return value


def canonical_path(value: object) -> Path:
    text = _string(value)
    path = Path(text)
    try:
        if not path.is_absolute() or path != path.resolve() or ".." in path.parts:
            raise SubworkflowError("noncanonical_path")
        return path
    except (OSError, ValueError, RuntimeError) as exc:
        raise SubworkflowError("invalid_path") from exc


def overlaps(first: Path, second: Path) -> bool:
    if first == second or first in second.parents or second in first.parents:
        return True
    # Resolve is not enough for case-insensitive filesystem aliases.
    for left, right in ((first, second), (second, first)):
        for ancestor in (left, *left.parents):
            try:
                if ancestor.samefile(right):
                    return True
            except OSError:
                continue
    return False


def _id(value: object) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise SubworkflowError("invalid_id")
    return value


def parse_request(raw: bytes, *, cwd: Path) -> dict[str, object]:
    """Normalize a bounded strict v1 request, including its derived output root."""
    obj = _fields(
        _json(raw, 65536),
        {"format", "call_key", "call_home", "parent", "definition", "output_ref"},
        {"wait_seconds", "cleanup_seconds", "inputs"},
    )
    if (
        obj["format"] != REQUEST_FORMAT
        or not isinstance(obj["call_key"], str)
        or not KEY.fullmatch(obj["call_key"])
    ):
        raise SubworkflowError("invalid_request")
    home = canonical_path(obj["call_home"])
    parent = _fields(
        obj["parent"], {"state_home", "instance_id"}, {"run_id", "plane_id", "node_id"}
    )
    canonical_path(parent["state_home"])
    _id(parent["instance_id"])
    if "run_id" in parent:
        _id(parent["run_id"])
    for key in ("plane_id", "node_id"):
        if key in parent and not KEY.fullmatch(_string(parent[key], maximum=64)):
            raise SubworkflowError("invalid_parent")
    definition = _fields(obj["definition"], {"selector", "version", "sha256"})
    selector = _string(definition["selector"], maximum=256)
    if not re.fullmatch(r"(?:bundled|local):[A-Za-z][A-Za-z0-9_-]{0,63}", selector):
        raise SubworkflowError("invalid_selector")
    if not SHA.fullmatch(_string(definition["sha256"], maximum=64)):
        raise SubworkflowError("invalid_definition")
    _string(definition["version"], maximum=256)
    if selector.startswith("local:") and definition["version"] != "sha256:" + str(
        definition["sha256"]
    ):
        raise SubworkflowError("definition_mismatch")
    budgets = {}
    for key, default, low, high in (
        ("wait_seconds", 300, 1, 86400),
        ("cleanup_seconds", 15, 5, 60),
    ):
        value = obj.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise SubworkflowError("invalid_budget")
        budgets[key] = value
    output = _string(obj["output_ref"])
    relative = Path(output)
    if (
        relative.is_absolute()
        or any(p in {".", ".."} for p in output.split("/"))
        or "\\" in output
    ):
        raise SubworkflowError("invalid_output_ref")
    root = canonical_path(str(cwd))
    canonical_path(str(root / relative))
    inputs = mapping(obj.get("inputs", {}))
    canonical(inputs)
    return {
        **obj,
        "call_home": str(home),
        **budgets,
        "inputs": inputs,
        "output_root": str(root),
    }


def timestamp(*, after: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=after)).isoformat()


def _date(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(_string(value))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError
        return parsed
    except ValueError as exc:
        raise SubworkflowError("invalid_deadline") from exc


def seconds_left(value: object) -> float:
    return (_date(value) - datetime.now(UTC)).total_seconds()


def request_id(request: dict[str, object], operation: str) -> str:
    return uuid.uuid5(
        NAMESPACE,
        str(request["call_home"]) + "/" + str(request["call_key"]) + "/" + operation,
    ).hex


def envelope(
    operation: str,
    request: dict[str, object],
    *,
    instance_id: str | None,
    run_id: str | None = None,
    payload: dict[str, object] | None = None,
    explicit_id: str | None = None,
    expected: int | None = None,
) -> dict[str, object]:
    return {
        "format": "millrace-router-request/v2",
        "operation": operation,
        "instance_id": instance_id,
        "run_id": run_id,
        "plane_id": None,
        "request_id": _id(explicit_id)
        if explicit_id
        else request_id(request, operation),
        "expected_state_version": expected,
        "payload": payload or {},
    }


def atomic_json(path: Path, value: object, *, limit: int = 1048576) -> None:
    """Replace a bounded ordinary record and fsync file and containing directory."""
    raw = canonical(value) + b"\n"
    if len(raw) > limit:
        raise SubworkflowError("record_too_large")
    canonical_path(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def validate_journal(value: object, home: Path, key: str) -> dict[str, object]:
    obj = _fields(
        value,
        {
            "format",
            "request",
            "request_sha256",
            "definition",
            "parent",
            "paths",
            "created_at",
            "deadline_at",
            "cleanup_deadline_at",
            "settings",
            "parent_state_version",
            "envelopes",
            "receipts",
            "instance_id",
            "run_id",
            "observation",
            "phase",
            "diagnostic",
            "output_manifest",
            "controllers",
            "cancel_intent",
            "cancellations",
            "copy_prepared",
            "saved_result",
            "authority_sha256",
            "record_sha256",
        },
    )
    if obj["record_sha256"] != digest(
        {k: v for k, v in obj.items() if k != "record_sha256"}
    ):
        raise SubworkflowError("invalid_journal")
    if obj["format"] != JOURNAL_FORMAT or obj["phase"] not in PHASES:
        raise SubworkflowError("invalid_journal")
    if type(obj["copy_prepared"]) is not bool:
        raise SubworkflowError("invalid_journal")
    req = mapping(obj["request"])
    original = {k: v for k, v in req.items() if k != "output_root"}
    if (
        parse_request(canonical(original), cwd=canonical_path(req["output_root"]))
        != req
    ):
        raise SubworkflowError("invalid_journal")
    if (
        req["call_home"] != str(home)
        or req["call_key"] != key
        or digest(req) != obj["request_sha256"]
    ):
        raise SubworkflowError("invalid_journal")
    root = home / key
    paths = {name: str(root / name) for name in ("workflow", "project", "state")}
    if (
        obj["paths"] != paths
        or obj["definition"] != req["definition"]
        or obj["parent"] != req["parent"]
    ):
        raise SubworkflowError("invalid_journal")
    for path in paths.values():
        canonical_path(path)
    if obj["output_manifest"] != str(
        Path(str(req["output_root"])) / str(req["output_ref"])
    ):
        raise SubworkflowError("invalid_journal")
    created, deadline = _date(obj["created_at"]), _date(obj["deadline_at"])
    if (deadline - created).total_seconds() != req["wait_seconds"]:
        raise SubworkflowError("invalid_journal")
    if obj["cleanup_deadline_at"] is not None:
        _date(obj["cleanup_deadline_at"])
    if type(obj["parent_state_version"]) is not int or obj["parent_state_version"] < 1:
        raise SubworkflowError("invalid_journal")
    settings = _fields(
        obj["settings"],
        {"path", "env_allowlist", "host_paths", "runtime_defaults", "operator_policy"},
    )
    for name in ("path", "env_allowlist"):
        if not isinstance(settings[name], list) or any(
            not isinstance(v, str) for v in settings[name]
        ):
            raise SubworkflowError("invalid_journal")
    for name in ("host_paths", "runtime_defaults", "operator_policy"):
        mapping(settings[name])
    envs = _fields(obj["envelopes"], {"create", "start", "cancel"})
    create = envelope(
        "create_instance",
        req,
        instance_id=None,
        expected=0,
        payload={
            "workflow_file": paths["workflow"] + "/workflow.json",
            "project_root": paths["project"],
            **settings,
        },
    )
    if envs["create"] != create:
        raise SubworkflowError("invalid_journal")
    receipts = mapping(obj["receipts"])
    instance, run = obj["instance_id"], obj["run_id"]
    if instance is not None:
        _id(instance)
        receipt = mapping(receipts.get("create"))
        if receipt.get("instance_id") != instance or receipt.get("status") != "applied":
            raise SubworkflowError("invalid_journal")
    if envs["start"] is not None:
        expected_start = envelope(
            "start_run",
            req,
            instance_id=_id(instance),
            expected=1,
            payload={"edit_mode": "locked"},
        )
        if envs["start"] != expected_start:
            raise SubworkflowError("invalid_journal")
    if run is not None:
        _id(run)
        start = mapping(receipts.get("start"))
        if (
            start.get("run_id") != run
            or start.get("instance_id") != instance
            or start.get("status") != "applied"
        ):
            raise SubworkflowError("invalid_journal")
    for op, receipt in receipts.items():
        if op not in envs or envs[op] is None:
            raise SubworkflowError("invalid_journal")
        row, sent = mapping(receipt), mapping(envs[op])
        if (
            row.get("request_id") != sent["request_id"]
            or row.get("operation") != sent["operation"]
            or row.get("request_digest") != public_request_digest(sent)
            or row.get("status") not in {"queued", "applied", "refused"}
        ):
            raise SubworkflowError("invalid_journal")
    if obj["cancel_intent"] is not None:
        intent = _fields(obj["cancel_intent"], {"request_id", "created_at"})
        _id(intent["request_id"])
        _date(intent["created_at"])
        if envs["cancel"] is not None and envs["cancel"] != envelope(
            "cancel_run",
            req,
            instance_id=_id(instance),
            run_id=_id(run),
            explicit_id=str(intent["request_id"]),
        ):
            raise SubworkflowError("invalid_journal")
    elif envs["cancel"] is not None:
        raise SubworkflowError("invalid_journal")
    cancellations = mapping(obj["cancellations"])
    for identifier, item in cancellations.items():
        _id(identifier)
        row = _fields(item, {"created_at", "envelope", "receipt"})
        _date(row["created_at"])
        if row["envelope"] is not None:
            expected_cancel = envelope(
                "cancel_run",
                req,
                instance_id=_id(instance),
                run_id=_id(run),
                explicit_id=identifier,
            )
            if row["envelope"] != expected_cancel:
                raise SubworkflowError("invalid_journal")
            if row["receipt"] is not None:
                receipt = mapping(row["receipt"])
                if receipt.get("request_id") != identifier or receipt.get(
                    "request_digest"
                ) != public_request_digest(expected_cancel):
                    raise SubworkflowError("invalid_journal")
        elif row["receipt"] is not None:
            raise SubworkflowError("invalid_journal")
    if (
        obj["cancel_intent"] is not None
        and str(mapping(obj["cancel_intent"])["request_id"]) not in cancellations
    ):
        raise SubworkflowError("invalid_journal")
    observation = mapping(obj["observation"])
    if observation:
        _fields(
            observation,
            {
                "id",
                "status",
                "state_version",
                "quiescent",
                "pause_reason",
                "inflight_invocations",
                "invocations",
                "completions",
            },
        )
        if (
            observation["id"] != run
            or observation["status"]
            not in {
                "ready",
                "running",
                "waiting",
                "paused",
                "succeeded",
                "failed",
                "cancelled",
            }
            or type(observation["quiescent"]) is not bool
        ):
            raise SubworkflowError("invalid_journal")
        if (
            type(observation["state_version"]) is not int
            or observation["state_version"] < 1
        ):
            raise SubworkflowError("invalid_journal")
        for name in ("inflight_invocations", "invocations", "completions"):
            if not isinstance(observation[name], list):
                raise SubworkflowError("invalid_journal")
        known = set()
        for item in observation["invocations"]:
            row = _fields(item, {"id", "status", "writer_state"})
            identifier = _id(row["id"])
            if identifier in known or row["writer_state"] not in {
                "stopped",
                "may_live",
                "running",
                "unknown",
            }:
                raise SubworkflowError("invalid_journal")
            known.add(identifier)
            _string(row["status"], maximum=64)
        for item in observation["completions"]:
            row = _fields(item, {"invocation_id", "effective_writer"})
            if row["invocation_id"] not in known or row["effective_writer"] not in {
                "stopped",
                "may_live",
                "running",
                "unknown",
            }:
                raise SubworkflowError("invalid_journal")
        if any(item not in known for item in observation["inflight_invocations"]):
            raise SubworkflowError("invalid_journal")
    if obj["saved_result"] is not None:
        result = _fields(obj["saved_result"], {"outcome", "summary", "refs"})
        if result["outcome"] not in {
            "CHILD_SUCCEEDED",
            "CHILD_FAILED",
            "CHILD_CANCELLED",
            "CHILD_ATTENTION",
            "CHILD_REFUSED",
            "CHILD_UNCERTAIN",
        }:
            raise SubworkflowError("invalid_journal")
        _string(result["summary"], maximum=4096)
        if result["refs"] != [req["output_ref"]]:
            raise SubworkflowError("invalid_journal")
    if not isinstance(obj["controllers"], list):
        raise SubworkflowError("invalid_journal")
    for item in obj["controllers"]:
        row = _fields(item, {"pid", "operation", "joined"})
        if (
            (row["pid"] is not None and (type(row["pid"]) is not int or row["pid"] < 1))
            or row["operation"] not in {"create", "start", "cancel"}
            or type(row["joined"]) is not bool
        ):
            raise SubworkflowError("invalid_journal")
    authority = {
        k: obj[k]
        for k in (
            "request",
            "definition",
            "parent",
            "paths",
            "created_at",
            "deadline_at",
            "settings",
            "parent_state_version",
            "output_manifest",
        )
    }
    if digest(authority) != obj["authority_sha256"]:
        raise SubworkflowError("invalid_journal")
    return obj


class Journal:
    """One canonical call location; locks never cover long public operations."""

    def __init__(self, home: Path, key: str) -> None:
        self.home = canonical_path(str(home))
        if not KEY.fullmatch(key):
            raise SubworkflowError("invalid_call_key")
        self.key, self.root = key, home / key
        self.path = self.root / "call.json"

    @contextmanager
    def lock(self, *, observer: bool = False) -> Iterator[None]:
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.home / (
            "." + self.key + (".observer.lock" if observer else ".journal.lock")
        )
        canonical_path(str(path))
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(
                    descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if observer else 0)
                )
            except BlockingIOError as exc:
                raise SubworkflowError("call_busy") from exc
            yield
        finally:
            os.close(descriptor)

    def load(self) -> dict[str, object]:
        canonical_path(str(self.path))
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(1048577)
        except FileNotFoundError as exc:
            raise SubworkflowError("call_missing") from exc
        try:
            return validate_journal(_json(raw, 1048576), self.home, self.key)
        except (TypeError, KeyError, ValueError) as exc:
            raise SubworkflowError("invalid_journal") from exc

    def save(self, value: dict[str, object]) -> None:
        value["record_sha256"] = digest(
            {k: v for k, v in value.items() if k != "record_sha256"}
        )
        validate_journal(value, self.home, self.key)
        atomic_json(self.path, value)
