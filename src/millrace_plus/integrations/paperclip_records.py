"""Strict Paperclip contracts, derived request identities and local binding records."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from millrace_plus.subworkflow_records import (
    atomic_json,
    canonical,
    canonical_path,
    digest,
    overlaps,
)

__all__ = [
    "BridgeError",
    "Store",
    "parse_config",
    "parse_assignment",
    "freeze",
    "canonical",
    "digest",
    "mapping",
    "fields",
    "text",
    "identifier",
    "load_json",
    "safe_path",
    "request",
    "request_digest",
    "validate_record",
    "date",
    "now",
]

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
NAMESPACE = uuid.UUID("83028ae1-150f-5912-8048-fdc2079c7145")
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


class BridgeError(Exception):
    """Expected refusal with a stable diagnostic that contains no input values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise BridgeError("invalid_object")
    return cast(dict[str, object], value)


def fields(
    value: object, required: set[str], optional: set[str] = frozenset()
) -> dict[str, object]:
    obj = mapping(value)
    if not required <= obj.keys() or obj.keys() - required - optional:
        raise BridgeError("invalid_fields")
    return obj


def text(value: object, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\0" in value:
        raise BridgeError("invalid_string")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise BridgeError("invalid_string") from exc
    return value


def identifier(value: object) -> str:
    value = text(value, 96)
    if not ID.fullmatch(value):
        raise BridgeError("invalid_id")
    return value


def load_json(raw: bytes, limit: int = 65536) -> object:
    if len(raw) > limit:
        raise BridgeError("record_too_large")

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BridgeError("invalid_json") from exc


def safe_path(value: object) -> Path:
    """Reject aliases and unsafe existing ancestors before writing owned state."""
    try:
        path = canonical_path(text(value))
        for ancestor in path.parents:
            if not ancestor.exists():
                continue
            info = ancestor.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in {0, os.getuid()}
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
            ):
                raise BridgeError("unsafe_path")
        return path
    except (OSError, ValueError, RuntimeError) as exc:
        raise BridgeError("unsafe_path") from exc


def _origin(value: object, *, loopback: bool = False) -> str:
    value = text(value)
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ({"http"} if loopback else {"http", "https"})
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port == 0
            or value != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise ValueError
        if loopback and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError
    except ValueError as exc:
        raise BridgeError("invalid_origin") from exc
    return value


def parse_config(raw: bytes) -> dict[str, object]:
    """Normalize a bounded, explicit bridge configuration without reading secrets."""
    obj = fields(
        load_json(raw),
        {
            "format",
            "paperclip_origin",
            "os_origin",
            "os_home",
            "os_credential_file",
            "definition",
            "destination_root",
            "binding_state_root",
            "helper_executable",
            "instance_settings",
        },
        {"assignment_revisions", "wait_seconds", "cleanup_seconds"},
    )
    if obj["format"] != "millrace-paperclip-bridge-config/v1":
        raise BridgeError("invalid_config")
    _origin(obj["paperclip_origin"])
    _origin(obj["os_origin"], loopback=True)
    identifier(obj["os_home"])
    for name in (
        "os_credential_file",
        "destination_root",
        "binding_state_root",
        "helper_executable",
    ):
        safe_path(obj[name])
    if overlaps(
        Path(str(obj["destination_root"])), Path(str(obj["binding_state_root"]))
    ):
        raise BridgeError("root_overlap")
    definition = fields(obj["definition"], {"selector", "version", "sha256"})
    selector = text(definition["selector"], 256)
    if not re.fullmatch(r"(?:bundled|local):[A-Za-z][A-Za-z0-9_-]{0,63}", selector):
        raise BridgeError("invalid_selector")
    if not SHA.fullmatch(text(definition["sha256"], 64)):
        raise BridgeError("invalid_definition")
    text(definition["version"], 256)
    if selector.startswith("local:") and definition["version"] != "sha256:" + str(
        definition["sha256"]
    ):
        raise BridgeError("definition_mismatch")
    settings = fields(
        obj["instance_settings"],
        {"path", "env_allowlist", "host_paths"},
        {"runtime_defaults", "operator_policy"},
    )
    for name in ("path", "env_allowlist"):
        items = settings[name]
        if (
            not isinstance(items, list)
            or len(items) > 128
            or len(set(map(str, items))) != len(items)
        ):
            raise BridgeError("invalid_settings")
        for item in items:
            if name == "path":
                if not Path(text(item)).is_absolute():
                    raise BridgeError("invalid_settings")
            elif not ENV.fullmatch(text(item, 128)):
                raise BridgeError("invalid_settings")
    for key, value in mapping(settings["host_paths"]).items():
        identifier(key)
        if not Path(text(value)).is_absolute():
            raise BridgeError("invalid_settings")
    for name in ("runtime_defaults", "operator_policy"):
        if name in settings:
            mapping(settings[name])
    revisions = mapping(obj.get("assignment_revisions", {}))
    for key, revision in revisions.items():
        identifier(key)
        if type(revision) is not int or revision < 1 or revision > 2147483647:
            raise BridgeError("invalid_revision")
    result = {**obj, "assignment_revisions": revisions}
    for name, default, low, high in (
        ("wait_seconds", 300, 1, 86400),
        ("cleanup_seconds", 15, 5, 60),
    ):
        value = obj.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise BridgeError("invalid_budget")
        result[name] = value
    return result


def parse_assignment(raw: bytes) -> dict[str, object]:
    obj = fields(
        load_json(raw),
        {"format", "company_id", "agent_id", "issue_id", "attempt_id", "description"},
    )
    if obj["format"] != "millrace-paperclip-context/v1":
        raise BridgeError("invalid_assignment")
    for name in ("company_id", "agent_id", "issue_id", "attempt_id"):
        identifier(obj[name])
    if not text(obj["description"], 65536).strip():
        raise BridgeError("missing_description")
    return obj


def freeze(config: dict[str, object], context: dict[str, object]) -> dict[str, object]:
    """Derive issue-scoped immutable authority; attempt/agent are report metadata."""
    revision = mapping(config["assignment_revisions"]).get(str(context["issue_id"]), 1)
    identity = {
        "paperclip_origin": config["paperclip_origin"],
        "company_id": context["company_id"],
        "issue_id": context["issue_id"],
        "assignment_revision": revision,
    }
    key = digest(
        [
            identity[name]
            for name in (
                "paperclip_origin",
                "company_id",
                "issue_id",
                "assignment_revision",
            )
        ]
    )
    root = Path(str(config["destination_root"])) / key
    return {
        "format": "millrace-paperclip-assignment/v1",
        "identity": identity,
        "binding_digest": key,
        "configuration": {
            k: v for k, v in config.items() if k != "assignment_revisions"
        },
        "description": context["description"],
        "description_sha256": hashlib.sha256(
            str(context["description"]).encode()
        ).hexdigest(),
        "paths": {
            "root": str(root),
            "workflow": str(root / "workflow"),
            "project": str(root / "project"),
        },
    }


def request(
    assignment: dict[str, object],
    operation: str,
    instance: object = None,
    run: object = None,
) -> dict[str, object]:
    config, paths = mapping(assignment["configuration"]), mapping(assignment["paths"])
    payload = (
        {
            "workflow_file": str(paths["workflow"]) + "/workflow.json",
            "project_root": paths["project"],
            **mapping(config["instance_settings"]),
        }
        if operation == "create"
        else {"edit_mode": "locked"}
        if operation == "start"
        else {}
    )
    return {
        "format": "millrace-router-request/v2",
        "operation": {
            "create": "create_instance",
            "start": "start_run",
            "cancel": "cancel_run",
        }[operation],
        "instance_id": instance,
        "run_id": run,
        "plane_id": None,
        "request_id": uuid.uuid5(
            NAMESPACE, str(assignment["binding_digest"]) + "/" + operation
        ).hex,
        "expected_state_version": 0
        if operation == "create"
        else 1
        if operation == "start"
        else None,
        "payload": payload,
    }


def request_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def now(*, after: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=after)).isoformat()


def date(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(text(value))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError
        return parsed
    except ValueError as exc:
        raise BridgeError("invalid_deadline") from exc


def validate_record(value: object, state_root: Path, key: str) -> dict[str, object]:
    """Re-derive durable authority and every mutation envelope on every reload."""
    obj = fields(
        value,
        {
            "format",
            "assignment",
            "assignment_sha256",
            "created_at",
            "deadline_at",
            "cleanup_deadline_at",
            "cancelled_at",
            "authority_sha256",
            "cleanup_required",
            "envelopes",
            "receipts",
            "instance_id",
            "run_id",
            "observation",
            "phase",
            "diagnostic",
            "cancel_intent",
            "copy_prepared",
            "record_sha256",
        },
    )
    assignment = mapping(obj["assignment"])
    identity = fields(
        assignment.get("identity"),
        {"paperclip_origin", "company_id", "issue_id", "assignment_revision"},
    )
    config = parse_config(
        canonical(
            {
                **mapping(assignment.get("configuration")),
                "assignment_revisions": {
                    str(identity["issue_id"]): identity["assignment_revision"]
                },
            }
        )
    )
    context = parse_assignment(
        canonical(
            {
                "format": "millrace-paperclip-context/v1",
                "company_id": identity["company_id"],
                "issue_id": identity["issue_id"],
                "agent_id": "reload",
                "attempt_id": "reload",
                "description": assignment.get("description"),
            }
        )
    )
    if (
        obj["format"] != "millrace-paperclip-call/v1"
        or assignment != freeze(config, context)
        or config["binding_state_root"] != str(state_root)
        or assignment["binding_digest"] != key
        or obj["assignment_sha256"] != digest(assignment)
        or obj["record_sha256"]
        != digest({k: v for k, v in obj.items() if k != "record_sha256"})
        or obj["phase"] not in PHASES
        or type(obj["copy_prepared"]) is not bool
        or type(obj["cancel_intent"]) is not bool
        or type(obj["cleanup_required"]) is not bool
        or obj["authority_sha256"]
        != digest({k: obj[k] for k in ("assignment", "created_at", "deadline_at")})
    ):
        raise BridgeError("invalid_binding")
    if (date(obj["deadline_at"]) - date(obj["created_at"])).total_seconds() != config[
        "wait_seconds"
    ]:
        raise BridgeError("invalid_binding")
    if obj["cleanup_deadline_at"] is not None:
        if (
            not obj["cancel_intent"]
            or obj["cancelled_at"] is None
            or (
                date(obj["cleanup_deadline_at"]) - date(obj["cancelled_at"])
            ).total_seconds()
            != config["cleanup_seconds"]
        ):
            raise BridgeError("invalid_binding")
    elif obj["cancel_intent"] or obj["cancelled_at"] is not None:
        raise BridgeError("invalid_binding")
    envs = fields(obj["envelopes"], {"create", "start", "cancel"})
    receipts = mapping(obj["receipts"])
    instance, run = obj["instance_id"], obj["run_id"]
    for name in ("create", "start", "cancel"):
        if envs[name] is None:
            if name == "create" or name in receipts:
                raise BridgeError("invalid_binding")
            continue
        expected = request(
            assignment,
            name,
            None if name == "create" else identifier(instance),
            identifier(run) if name == "cancel" else None,
        )
        if envs[name] != expected or (name == "cancel" and not obj["cancel_intent"]):
            raise BridgeError("invalid_binding")
        if name in receipts:
            receipt = mapping(receipts[name])
            if (
                receipt.get("request_id") != expected["request_id"]
                or receipt.get("request_digest") != request_digest(expected)
                or receipt.get("operation") != expected["operation"]
                or receipt.get("status") not in {"queued", "applied", "refused"}
                or (name != "create" and receipt.get("instance_id") != instance)
                or (name == "cancel" and receipt.get("run_id") != run)
            ):
                raise BridgeError("invalid_binding")
    if receipts.keys() - envs.keys():
        raise BridgeError("invalid_binding")
    for name, target, field in (
        ("create", instance, "instance_id"),
        ("start", run, "run_id"),
    ):
        if target is not None:
            identifier(target)
            receipt = mapping(receipts.get(name))
            if receipt.get("status") != "applied" or receipt.get(field) != target:
                raise BridgeError("invalid_binding")
    observation = mapping(obj["observation"])
    if observation:
        fields(
            observation,
            {
                "id",
                "status",
                "state_version",
                "quiescent",
                "inflight_invocations",
                "invocations",
                "completions",
                "graph_revision",
                "terminal_outcome",
                "refs",
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
            or type(observation["state_version"]) is not int
            or observation["state_version"] < 1
        ):
            raise BridgeError("invalid_binding")
        if not SHA.fullmatch(text(observation["graph_revision"], 64)):
            raise BridgeError("invalid_binding")
        if observation["terminal_outcome"] is not None:
            identifier(observation["terminal_outcome"])
        for name in ("invocations", "completions"):
            if not isinstance(observation[name], list):
                raise BridgeError("invalid_binding")
        known = set()
        for row in cast(list[object], observation["invocations"]):
            item = fields(row, {"id", "status", "writer_state"})
            ident = identifier(item["id"])
            if ident in known or item["writer_state"] not in {
                "stopped",
                "may_live",
                "running",
                "unknown",
            }:
                raise BridgeError("invalid_binding")
            known.add(ident)
            text(item["status"], 96)
        for row in cast(list[object], observation["completions"]):
            item = fields(row, {"invocation_id", "effective_writer"})
            if item["invocation_id"] not in known or item["effective_writer"] not in {
                "stopped",
                "may_live",
                "running",
                "unknown",
            }:
                raise BridgeError("invalid_binding")
        if not isinstance(observation["inflight_invocations"], list) or any(
            v not in known for v in observation["inflight_invocations"]
        ):
            raise BridgeError("invalid_binding")
        if not isinstance(observation["refs"], list) or len(observation["refs"]) > 32:
            raise BridgeError("invalid_binding")
        for ref in observation["refs"]:
            name = text(ref)
            if (
                Path(name).is_absolute()
                or "\\" in name
                or any(v in {"", ".", ".."} for v in name.split("/"))
            ):
                raise BridgeError("invalid_binding")
    return obj


class Store:
    """One canonical binding with short issue and nonblocking observer locks."""

    def __init__(self, assignment: dict[str, object]) -> None:
        self.assignment = assignment
        self.home = safe_path(
            mapping(assignment["configuration"])["binding_state_root"]
        )
        self.key = str(assignment["binding_digest"])
        self.root = self.home / self.key
        self.path = self.root / "call.json"
        identity = mapping(assignment["identity"])
        self.issue = digest(
            [identity[name] for name in ("paperclip_origin", "company_id", "issue_id")]
        )

    @contextmanager
    def lock(self, *, observer: bool = False) -> Iterator[None]:
        safe_path(str(self.home))
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.home / (
            "."
            + (self.key if observer else self.issue)
            + (".observer" if observer else ".issue")
            + ".lock"
        )
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (
                info.st_uid != os.getuid()
                or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
            ):
                raise BridgeError("unsafe_lock")
            try:
                fcntl.flock(
                    descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if observer else 0)
                )
            except BlockingIOError as exc:
                raise BridgeError("attention_busy") from exc
            yield
        finally:
            os.close(descriptor)

    def load(self) -> dict[str, object]:
        safe_path(str(self.path))
        try:
            with self.path.open("rb") as stream:
                value = load_json(stream.read(1048577), 1048576)
            return validate_record(value, self.home, self.key)
        except (KeyError, TypeError, ValueError) as exc:
            raise BridgeError("invalid_binding") from exc

    def save(self, value: dict[str, object]) -> None:
        value["record_sha256"] = digest(
            {k: v for k, v in value.items() if k != "record_sha256"}
        )
        validate_record(value, self.home, self.key)
        atomic_json(self.path, value)
