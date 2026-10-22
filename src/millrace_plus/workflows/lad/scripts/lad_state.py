"""LAD's local retained ledger and strict filesystem/data boundaries (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path

ROLES = {
    "recon": ["PASS", "FINDINGS", "ERROR", "HUMAN_BLOCKED"],
    "planner": ["PASS", "FINDINGS", "ERROR", "HUMAN_BLOCKED"],
    "manager": ["PASS", "FINDINGS", "ERROR", "HUMAN_BLOCKED"],
    "builder": [
        "PASS",
        "FINDINGS_INSPECTABLE",
        "FINDINGS_UNINSPECTABLE",
        "ERROR",
        "HUMAN_BLOCKED",
    ],
    "checker": [
        "PASS",
        "FINDINGS_ACTIONABLE",
        "FINDINGS_CONTRACT",
        "ERROR",
        "HUMAN_BLOCKED",
    ],
    "fixer": [
        "PASS",
        "FINDINGS_INSPECTABLE",
        "FINDINGS_UNINSPECTABLE",
        "ERROR",
        "HUMAN_BLOCKED",
    ],
    "doublechecker": [
        "PASS",
        "FINDINGS_ACTIONABLE",
        "FINDINGS_CONTRACT",
        "ERROR",
        "HUMAN_BLOCKED",
    ],
    "troubleshooter": [
        "PASS_REVIEW_REPAIRED",
        "PASS_PREREQUISITE_REPAIRED",
        "FINDINGS",
        "ERROR",
        "HUMAN_BLOCKED",
    ],
    "consultant": [
        "REPAIR_SPEC",
        "REPAIR_PACKETS",
        "REPAIR_EXECUTION",
        "NO_SAFE_ROUTE",
        "ERROR_REPAIRABLE",
        "ERROR_REPLACEMENT",
        "HUMAN_BLOCKED",
    ],
    "updater": [
        "PASS",
        "REVIEW_INVALIDATED_NEW_BASELINE",
        "REVIEW_INVALIDATED_SAME_BASELINE",
        "FINDINGS_CONTRACT",
        "ERROR",
        "HUMAN_BLOCKED",
    ],
    "learning": [
        "PASS",
        "FINDINGS",
        "ERROR_TRANSIENT_RESOLVED",
        "ERROR",
        "HUMAN_BLOCKED",
    ],
    "arbiter": [
        "PASS",
        "FINDINGS",
        "ERROR_REPAIRABLE",
        "ERROR_REPLACEMENT",
        "HUMAN_BLOCKED",
    ],
}
ARTIFACTS = {
    "recon": ["intake.md"],
    "planner": ["specification.md"],
    "manager": ["plan-check.md"],
    "builder": ["implementation.md"],
    "checker": ["baseline.md", "review.md"],
    "fixer": ["repair.md"],
    "doublechecker": ["review.md"],
    "troubleshooter": ["diagnosis.md"],
    "consultant": ["diagnosis.md"],
    "updater": ["completion.md"],
    "learning": ["assessment.md"],
    "arbiter": ["verdict.md"],
}
CAPS = {
    "all": 60,
    "recon": 3,
    "planner": 3,
    "manager": 3,
    "builder": 4,
    "checker": 4,
    "doublechecker": 4,
    "updater": 4,
    "fixer": 3,
    "consultant": 2,
    "troubleshooter": 2,
    "learning": 2,
    "arbiter": 3,
    "planning_repair": 1,
    "local_repair": 1,
    "remediation": 1,
    "replacement": 1,
}
SESSION_ROLES = {"recon", "planner", "manager", "consultant", "arbiter"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


class LadError(Exception):
    """A deliberate fail-closed domain refusal; messages contain no file contents."""


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(raw: bytes) -> dict:
    def unique(pairs):
        item = {}
        for key, value in pairs:
            if key in item:
                raise LadError("duplicate_json_key")
            item[key] = value
        return item

    if len(raw) > 4 * 1024 * 1024:
        raise LadError("record_too_large")
    value = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(LadError("nonfinite_json")),
    )
    if not isinstance(value, dict):
        raise LadError("object_required")
    return value


def fields(value: object, keys: set[str], optional: set[str] = frozenset()) -> dict:
    if (
        not isinstance(value, dict)
        or not keys <= value.keys()
        or value.keys() - keys - optional
    ):
        raise LadError("invalid_fields")
    return value


def text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise LadError("nonblank_text_required")
    value.encode("utf-8")
    return value


def identifier(value: object) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise LadError("invalid_identifier")
    return value


def safe(root: Path, relative: str, *, missing: bool = False) -> Path:
    """Reject aliases at every component, including dangling symlinks and FIFOs."""
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or str(path) != relative
        or any(p in {"..", "."} for p in path.parts)
    ):
        raise LadError("invalid_relative_path")
    current = root
    for part in path.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if missing:
                continue
            raise LadError("missing_file") from None
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise LadError("unsafe_file")
    if root.resolve() not in current.resolve().parents:
        raise LadError("path_escape")
    return current


def read(root: Path, relative: str) -> bytes:
    path = safe(root, relative)
    if not path.is_file():
        raise LadError("regular_file_required")
    # O_NONBLOCK prevents a replaced FIFO from hanging a deterministic node.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise LadError("regular_file_required")
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise LadError("file_too_large")
    return raw


def atomic(root: Path, relative: str, raw: bytes, *, immutable: bool = False) -> None:
    path = safe(root, relative, missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if read(root, relative) != raw:
            raise LadError("immutable_conflict")
        return
    fd, name = tempfile.mkstemp(prefix=".lad-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.link(name, path)
        else:
            os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def emit(outcome: str, summary: str = "", refs: list[str] | None = None) -> None:
    print(json.dumps({"outcome": outcome, "summary": summary, "refs": refs or []}))


def run(function) -> None:
    try:
        function()
    except (LadError, OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, LadError) else type(exc).__name__
        emit("REFUSED", "LAD reconciliation required: " + code)


def roots(project_root: str = "..") -> tuple[Path, Path]:
    foreground = Path.cwd().resolve()
    if project_root != ".." or foreground.name != "foreground":
        raise LadError("explicit_parent_root_required")
    project = foreground.parent
    if (
        safe(project, "foreground") != foreground
        or not safe(project, "learning").is_dir()
    ):
        raise LadError("workspace_layout")
    return foreground, project


def manifest(project: Path) -> tuple[dict | None, str, str]:
    path = project / "subworkflow-inputs.json"
    if not path.exists() and not path.is_symlink():
        return None, "local", "local"
    raw = read(project, "subworkflow-inputs.json")
    obj = fields(load_json(raw), {"format", "identity", "inputs"})
    if (
        obj["format"] != "millrace-plus-subworkflow-inputs/v1"
        or not isinstance(obj["identity"], dict)
        or not obj["identity"]
        or not isinstance(obj["inputs"], dict)
    ):
        raise LadError("invalid_input_manifest")
    # Identity is the public caller's captured identity; it selects no local path.
    identity = digest(canonical(obj["identity"]))
    return obj, identity, digest(canonical(obj["inputs"]))


def new_state(identity: str, inputs: str) -> dict:
    sid = (
        uuid.uuid4()
        if identity == "local"
        else uuid.uuid5(uuid.NAMESPACE_URL, "millrace-lad-session/v1/" + identity)
    )
    state = {
        "format": "millrace-lad-session/v1",
        "session_id": str(sid),
        "identity_sha256": identity,
        "inputs_sha256": inputs,
        "limits": CAPS,
        "usage": {},
        "events": [],
        "occurrences": {},
        "active": {},
        "replacement_intents": {},
        "accepted": {},
        "intake": None,
        "criteria": [],
        "specification": None,
        "plan": [],
        "packet_index": 0,
        "baselines": {},
        "pairs": {},
        "last_fixer": {},
        "findings": {},
        "reviews": {},
        "learning": {},
        "pending": None,
        "request": None,
        "seam_seen": {},
        "continuation": None,
        "terminal": None,
    }
    state["integrity_sha256"] = digest(canonical(state))
    return state


def save(foreground: Path, state: dict) -> None:
    state.pop("integrity_sha256", None)
    state["integrity_sha256"] = digest(canonical(state))
    atomic(foreground, "lad-state/session.json", canonical(state) + b"\n")


def load(foreground: Path) -> dict:
    state = load_json(read(foreground, "lad-state/session.json"))
    expected = set(new_state("local", "local"))
    fields(state, expected)
    content = dict(state)
    checksum = content.pop("integrity_sha256")
    if checksum != digest(canonical(content)):
        raise LadError("session_integrity")
    if state["format"] != "millrace-lad-session/v1" or state["limits"] != CAPS:
        raise LadError("session_contract")
    uuid.UUID(state["session_id"])
    for name in (
        "usage",
        "occurrences",
        "active",
        "replacement_intents",
        "accepted",
        "baselines",
        "pairs",
        "last_fixer",
        "findings",
        "reviews",
        "learning",
        "seam_seen",
    ):
        if not isinstance(state[name], dict):
            raise LadError("corrupt_ledger")
    if (
        not isinstance(state["events"], list)
        or not isinstance(state["plan"], list)
        or not isinstance(state["criteria"], list)
    ):
        raise LadError("corrupt_ledger")
    actual = {}
    reserved = set()
    for event in state["events"]:
        fields(event, {"id", "debits", "kind"})
        if event["kind"] not in {"role", "planning_repair", "remediation"}:
            raise LadError("unknown_debit_event")
        if (
            event["id"] in reserved
            or not isinstance(event["debits"], list)
            or not event["debits"]
        ):
            raise LadError("corrupt_debits")
        reserved.add(event["id"])
        for key in event["debits"]:
            cap = CAPS[key.split(":", 1)[0]]
            actual[key] = actual.get(key, 0) + 1
            if actual[key] > cap:
                raise LadError("corrupt_debits")
        if event["kind"] == "role" and event["id"] not in state["occurrences"]:
            raise LadError("missing_occurrence")
    if state["usage"] != actual:
        raise LadError("usage_mismatch")
    for oid, occurrence in state["occurrences"].items():
        if (
            oid != occurrence.get("occurrence_id")
            or occurrence.get("session_id") != state["session_id"]
            or oid not in reserved
        ):
            raise LadError("occurrence_mismatch")
        stored = dict(occurrence)
        checksum = stored.pop("input_sha256")
        if checksum != digest(canonical(stored)):
            raise LadError("occurrence_digest")
        workspace = (
            foreground
            if occurrence["role"] != "learning"
            else foreground.parent / "learning"
        )
        relative = f"lad-state/occurrences/{oid}/input.json"
        projection = workspace / relative
        ready = workspace / f"lad-state/occurrences/{oid}/ready.json"
        if projection.exists() or projection.is_symlink():
            if load_json(read(workspace, relative)) != occurrence:
                raise LadError("occurrence_projection_conflict")
        elif ready.exists() or ready.is_symlink():
            raise LadError("ready_occurrence_missing_input")
        if ready.exists() or ready.is_symlink() or oid in state["accepted"]:
            expected_root = workspace / f"lad-state/occurrences/{oid}/guidance"
            if (
                occurrence["guidance_root"] != str(expected_root)
                or not ready.is_file()
                or ready.is_symlink()
            ):
                raise LadError("guidance_locator_or_ready_conflict")
            manifest = {
                "format": "millrace-lad-guidance/v1",
                "guidance_sha256": occurrence["guidance_sha256"],
                "files": occurrence["guidance_files"],
            }
            manifest_relative = f"lad-state/occurrences/{oid}/guidance/manifest.json"
            if (
                load_json(read(workspace, manifest_relative)) != manifest
                or digest(canonical(manifest)) != occurrence["guidance_snapshot_sha256"]
            ):
                raise LadError("guidance_snapshot_manifest")
            for name, checksum in manifest["files"].items():
                if (
                    digest(
                        read(workspace, f"lad-state/occurrences/{oid}/guidance/" + name)
                    )
                    != checksum
                ):
                    raise LadError("guidance_snapshot_changed")
    for plane, oid in state["active"].items():
        if plane not in {"foreground", "learning"} or oid not in state["occurrences"]:
            raise LadError("active_mismatch")
        workspace = (
            foreground if plane == "foreground" else foreground.parent / "learning"
        )
        projection = workspace / "lad-state/active-role.json"
        if projection.exists() or projection.is_symlink():
            active = load_json(read(workspace, "lad-state/active-role.json"))
            if active != state["occurrences"][oid]:
                previous = active.get("occurrence_id")
                ready = workspace / f"lad-state/occurrences/{oid}/ready.json"
                allowed_previous = (
                    previous in state["accepted"]
                    or state["occurrences"][oid]["replacement_of"] == previous
                )
                if (
                    ready.exists()
                    or not allowed_previous
                    or active != state["occurrences"].get(previous)
                ):
                    raise LadError("active_projection_conflict")
    for oid, accepted in state["accepted"].items():
        occurrence = state["occurrences"].get(oid)
        if not occurrence or accepted.get("sha256") != digest(
            canonical(accepted.get("result"))
        ):
            raise LadError("accepted_digest")
        workspace = (
            foreground
            if occurrence["role"] != "learning"
            else foreground.parent / "learning"
        )
        raw = load_json(read(workspace, f"lad-results/{oid}/result.json"))
        if raw != accepted["result"]:
            raise LadError("accepted_output_changed")
        for artifact in raw["artifacts"]:
            if (
                digest(read(workspace, f"lad-results/{oid}/" + artifact["path"]))
                != artifact["sha256"]
            ):
                raise LadError("accepted_artifact_changed")
    if state["occurrences"] and not state["continuation"]:
        raise LadError("missing_continuation")
    return state


def debit(state: dict, keys: list[str], event_id: str, kind: str) -> None:
    if len(keys) != len(set(keys)):
        raise LadError("duplicate_debit")
    if any(state["usage"].get(k, 0) >= CAPS[k.split(":", 1)[0]] for k in keys):
        raise LadError("budget_exhausted")
    for key in keys:
        state["usage"][key] = state["usage"].get(key, 0) + 1
    state["events"].append({"id": event_id, "debits": keys, "kind": kind})


def packet(state: dict) -> dict | None:
    index = state["packet_index"]
    if type(index) is not int or not 0 <= index <= len(state["plan"]):
        raise LadError("packet_cursor")
    return state["plan"][index] if index < len(state["plan"]) else None


def scope(state: dict) -> str:
    current = packet(state)
    return current["lineage"] if current else "unpacketized"


def source(foreground: Path) -> dict[str, str]:
    target = safe(foreground, "target", missing=True)
    if not target.exists():
        return {}
    if not target.is_dir():
        raise LadError("target_directory_required")
    return {
        str(p.relative_to(target)): digest(read(target, str(p.relative_to(target))))
        for p in sorted(target.rglob("*"))
        if not p.is_dir() or p.is_symlink()
    }


def guidance() -> str:
    root = Path(__file__).resolve().parent.parent
    names = ["workflow.json"] + [
        str(p.relative_to(root))
        for folder in ("prompts", "context", "templates", "profiles", "scripts")
        for p in sorted((root / folder).rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    ]
    return digest(canonical({name: digest(read(root, name)) for name in sorted(names)}))


def mark(state: dict, node: str, oid: str | None = None) -> None:
    state["continuation"] = {
        "node": node,
        "occurrence_id": oid,
        "lineage": scope(state),
        "cleanup_required": True,
    }


def assert_foreground(state: dict) -> None:
    if state["request"] is not None:
        raise LadError("learning_owns_ledger_until_ack")
