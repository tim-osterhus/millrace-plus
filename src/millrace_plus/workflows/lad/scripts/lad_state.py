"""LAD's local retained ledger and strict filesystem/data boundaries (stdlib only)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
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
SESSION_KEYS = {
    "accepted",
    "active",
    "baselines",
    "continuation",
    "criteria",
    "events",
    "findings",
    "format",
    "identity_sha256",
    "inputs_sha256",
    "intake",
    "integrity_sha256",
    "last_fixer",
    "learning",
    "legacy",
    "migration",
    "occurrences",
    "packet_index",
    "pairs",
    "pending",
    "plan",
    "replacement_intents",
    "request",
    "reviews",
    "seam_seen",
    "session_id",
    "specification",
    "terminal",
}

SESSION_ROLES = {"recon", "planner", "manager", "consultant", "arbiter"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
POLICY_PATH = "context/routing-policy.json"
POLICY = {
    "format": "millrace-lad-routing-policy/v1",
    "revision": 1,
    "presentation_revision": "millrace-lad-presentation/v1",
    "learning": "once-per-packet",
    "provider_recovery": "disabled",
}


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
        else uuid.uuid5(uuid.NAMESPACE_URL, "millrace-lad-session/v2/" + identity)
    )
    state = {
        "format": "millrace-lad-session/v2",
        "session_id": str(sid),
        "identity_sha256": identity,
        "inputs_sha256": inputs,
        "legacy": None,
        "migration": None,
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
    return _validate_state(
        foreground, load_json(read(foreground, "lad-state/session.json"))
    )


def _validate_state(
    foreground: Path,
    state: dict,
    *,
    migration_data: tuple[bytes, bytes] | None = None,
    baseline_projection: tuple[str, bytes] | None = None,
) -> dict:
    if state.get("format") == "millrace-lad-session/v1":
        from session_compat import validate_v1

        return validate_v1(foreground, state)
    expected = SESSION_KEYS
    fields(state, expected)
    content = dict(state)
    checksum = content.pop("integrity_sha256")
    if checksum != digest(canonical(content)):
        raise LadError("session_integrity")
    if state["format"] != "millrace-lad-session/v2":
        raise LadError("session_contract")
    uuid.UUID(state["session_id"])
    for name in (
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
    reserved = set()
    role_events = set()
    for event in state["events"]:
        fields(event, {"id", "kind"})
        if event["kind"] not in {"role", "planning_repair", "remediation"}:
            raise LadError("unknown_history_event")
        if not isinstance(event["id"], str) or event["id"] in reserved:
            raise LadError("duplicate_history_event")
        reserved.add(event["id"])
        if event["kind"] == "role":
            role_events.add(event["id"])
            if event["id"] not in state["occurrences"]:
                raise LadError("missing_occurrence")
    if role_events != set(state["occurrences"]):
        raise LadError("occurrence_role_history_mismatch")
    for oid, occurrence in state["occurrences"].items():
        if (
            oid != occurrence.get("occurrence_id")
            or occurrence.get("session_id") != state["session_id"]
            or oid not in role_events
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
                    # A failed assessment closes its owner without acceptance.
                    or (plane == "learning" and previous is not None and any(
                        isinstance(row, dict) and row.get("occurrence_id") == previous
                        for row in state["learning"].values()
                    ))
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
        raw_bytes = read(workspace, f"lad-results/{oid}/result.json")
        raw = load_json(raw_bytes)
        if raw != accepted["result"]:
            raise LadError("accepted_output_changed")
        artifacts = []
        for artifact in raw["artifacts"]:
            data = _baseline_read(
                workspace, f"lad-results/{oid}/" + artifact["path"], baseline_projection
            )
            if digest(data) != artifact["sha256"]:
                raise LadError("accepted_artifact_changed")
            artifacts.append((artifact["path"], data))
        if occurrence["format"] == "millrace-lad-input/v2":
            _presentation_pin(workspace, occurrence, raw_bytes, raw, artifacts)
    if state["occurrences"] and not state["continuation"]:
        raise LadError("missing_continuation")
    validate_migration(foreground, state, migration_data=migration_data)
    validate_baselines(
        foreground,
        state,
        legacy_raw=migration_data[0] if migration_data else None,
        baseline_projection=baseline_projection,
    )
    return state


def record_event(state: dict, event_id: str, kind: str) -> None:
    if state["format"] != "millrace-lad-session/v2":
        raise LadError("explicit_session_upgrade_required")
    if kind not in {"role", "planning_repair", "remediation"} or any(
        row["id"] == event_id for row in state["events"]
    ):
        raise LadError("duplicate_history_event")
    state["events"].append({"id": event_id, "kind": kind})


def packet(state: dict) -> dict | None:
    index = state["packet_index"]
    if type(index) is not int or not 0 <= index <= len(state["plan"]):
        raise LadError("packet_cursor")
    return state["plan"][index] if index < len(state["plan"]) else None


def scope(state: dict) -> str:
    current = packet(state)
    return current["lineage"] if current else "unpacketized"


_SOURCE_EXCLUDES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
}
_PROTECTED_NAMES = {".env", ".env.local", "credentials.json", "secrets.json"}


def shared_target(
    foreground: Path, *, bootstrap: bool = False, **kwargs
) -> dict | None:
    """Only an explicit shared input can select an external implementation root."""
    project = foreground.parent
    captured, _, _ = manifest(project)
    supplied = captured is not None and "lad_target" in captured["inputs"]
    saved = project / "lad-target.json"
    if not supplied:
        if saved.exists() or saved.is_symlink():
            raise LadError("shared_input_missing")
        return None
    # Standalone copied scripts remain stdlib-only; shared mode requires Plus.
    try:
        from millrace_plus.lad_service import validate_shared_target
        from millrace_plus.local_intake import IntakeError
        from millrace_plus.subworkflow_records import SubworkflowError
    except ImportError as exc:
        raise LadError("shared_target_requires_plus") from exc
    try:
        return validate_shared_target(
            captured["inputs"]["lad_target"],
            project=project,
            bootstrap=bootstrap,
            **kwargs,
        )
    except (IntakeError, SubworkflowError) as exc:
        raise LadError(exc.code) from exc


def target_root(foreground: Path) -> Path:
    selected = shared_target(foreground)
    return (
        Path(selected["target_root"])
        if selected is not None
        else safe(foreground, "target", missing=True)
    )


def _prepare_source(
    foreground: Path, selected_target: dict | None
) -> tuple[dict, dict]:
    """Collect one operation's source/context from its already validated root.

    The caller must fully revalidate shared authority before returning the
    observation or publishing an occurrence. No locator survives the operation.
    """
    target = (
        Path(selected_target["target_root"])
        if selected_target is not None
        else safe(foreground, "target", missing=True)
    )
    snapshot = _source_at(foreground, target)
    context = _source_context_at(target, _outside_source_at(foreground, target))
    return snapshot, context


def _owned_marker(foreground: Path, target: Path, relative: str) -> bool:
    # The resolver already verified the exact owner marker and active claim.
    return (
        relative == ".millrace-plus-target-owner.json"
        and target != foreground / "target"
    )


def outside_source(foreground: Path) -> dict:
    """Retain outside-path identities without hashing all target content."""
    return _outside_source_at(foreground, target_root(foreground))


def _outside_source_at(foreground: Path, target: Path) -> dict:
    if not target.exists():
        return {}
    session = foreground / "lad-state/session.json"
    state = (
        load_json(read(foreground, "lad-state/session.json"))
        if session.exists()
        else None
    )
    current = packet(state) if state is not None else None
    paths = current["paths"] if current else []
    if not paths:
        return {}
    result = {}
    for folder, dirs, files in os.walk(target, followlinks=False):
        root = Path(folder)
        for name in list(dirs):
            if (root / name).is_symlink():
                files.append(name)
                dirs.remove(name)
        dirs[:] = [
            name
            for name in dirs
            if name != ".git"
            and (
                name not in _SOURCE_EXCLUDES
                or any(
                    scope == str((root / name).relative_to(target))
                    or scope.startswith(str((root / name).relative_to(target)) + "/")
                    or str((root / name).relative_to(target)).startswith(
                        scope.rstrip("/") + "/"
                    )
                    for scope in paths
                )
            )
        ]
        for name in files:
            path = root / name
            relative = str(path.relative_to(target))
            if _owned_marker(foreground, target, relative):
                continue
            if name not in _PROTECTED_NAMES and any(
                relative == scope or relative.startswith(scope.rstrip("/") + "/")
                for scope in paths
            ):
                continue
            info = path.lstat()
            identity = [
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_ino,
            ]
            if stat.S_ISLNK(info.st_mode):
                identity.append(os.readlink(path))
            elif not stat.S_ISREG(info.st_mode):
                raise LadError("unsafe_source_file")
            result[relative] = identity
    return result


def source_context(foreground: Path) -> dict:
    """Record the Git base/index without reading Git storage as source files."""
    # Preserve the public helper's fresh authority checks for other callers.
    outside = outside_source(foreground)
    return _source_context_at(target_root(foreground), outside)


def _source_context_at(target: Path, outside: dict) -> dict:
    if not target.exists():
        return {"git": None, "outside": outside}
    command = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-C",
        str(target),
    ]
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        top = subprocess.run(
            command + ["rev-parse", "--show-toplevel"],
            env=environment,
            capture_output=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {"git": None, "outside": outside}
    if top.returncode != 0 or Path(os.fsdecode(top.stdout).strip()).resolve() != target:
        return {"git": None, "outside": outside}
    facts = {}
    for key, args in {
        "base": ["rev-parse", "HEAD"],
        "index": ["ls-files", "--stage", "-z"],
        "worktree": ["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv"],
        "untracked": ["ls-files", "--others", "--exclude-standard", "-z"],
    }.items():
        result = subprocess.run(
            command + args, env=environment, capture_output=True, timeout=10
        )
        if result.returncode != 0:
            raise LadError("source_git_observation_failed")
        facts[key] = (
            os.fsdecode(result.stdout).strip()
            if key == "base"
            else digest(result.stdout)
        )
    return {"git": facts, "outside": outside}


def source(foreground: Path, paths: list[str] | None = None) -> dict[str, str]:
    """Stream scoped source identities; do not follow links or parse artifacts."""
    return _source_at(foreground, target_root(foreground), paths)


def _source_at(
    foreground: Path, target: Path, paths: list[str] | None = None
) -> dict[str, str]:
    if not target.exists():
        return {}
    if not target.is_dir():
        raise LadError("target_directory_required")
    if paths is None:
        file = foreground / "lad-state/session.json"
        state = (
            load_json(read(foreground, "lad-state/session.json"))
            if file.exists()
            else None
        )
        current = packet(state) if state is not None else None
        paths = current["paths"] if current else []
    if any(Path(name).is_absolute() or ".." in Path(name).parts for name in paths):
        raise LadError("source_scope_escape")
    observed = {}
    for folder, dirs, files in os.walk(target, followlinks=False):
        root = Path(folder)
        for name in list(dirs):
            if (root / name).is_symlink():
                files.append(name)
                dirs.remove(name)
        dirs[:] = [
            name
            for name in dirs
            if name != ".git"
            and (
                name not in _SOURCE_EXCLUDES
                or any(
                    scope == str((root / name).relative_to(target))
                    or scope.startswith(str((root / name).relative_to(target)) + "/")
                    or str((root / name).relative_to(target)).startswith(
                        scope.rstrip("/") + "/"
                    )
                    for scope in paths
                )
            )
        ]
        for name in files:
            path = root / name
            relative = str(path.relative_to(target))
            if _owned_marker(foreground, target, relative):
                continue
            if (
                paths
                and name not in _PROTECTED_NAMES
                and not any(
                    relative == scope or relative.startswith(scope.rstrip("/") + "/")
                    for scope in paths
                )
            ):
                continue
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                observed[relative] = "symlink:" + digest(os.fsencode(os.readlink(path)))
                continue
            if not stat.S_ISREG(mode):
                raise LadError("unsafe_source_file")
            fd = os.open(
                path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
            )
            checksum = hashlib.sha256()
            with os.fdopen(fd, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise LadError("unsafe_source_file")
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    checksum.update(block)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ino,
                ):
                    raise LadError("source_changed_during_observation")
            observed[relative] = checksum.hexdigest()
    return dict(sorted(observed.items()))


def routing_policy() -> dict:
    """Strict captured new-copy policy; any missing or unknown form refuses."""
    root = Path(__file__).resolve().parent.parent
    try:
        raw = read(root, POLICY_PATH)
        policy = fields(load_json(raw), set(POLICY))
    except (LadError, ValueError):
        raise LadError("routing_policy_invalid") from None
    for key, expected in POLICY.items():
        if type(policy[key]) is not type(expected) or policy[key] != expected:
            raise LadError("routing_policy_unsupported_" + key)
    return {"path": POLICY_PATH, "sha256": digest(raw),
            "revision": policy["revision"]}


def presentation_path(oid: str, raw: bytes) -> str:
    return f"lad-state/presentation/{oid}/{digest(raw)}.json"


_PRESENTATION = None


def presentation_module():
    """This copy's own self-contained stdlib view module, loaded by file path.

    It imports no sibling script and registers no global module name, so it
    works however this module was loaded and never reuses another copy's view.
    """
    global _PRESENTATION
    if _PRESENTATION is None:
        import importlib.util

        path = Path(__file__).resolve().parent / "presentation.py"
        spec = importlib.util.spec_from_file_location("presentation", path)
        if spec is None or spec.loader is None:
            raise LadError("presentation_module_missing")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _PRESENTATION = module
    return _PRESENTATION


def development_check(raw: bytes) -> tuple[str, list[str]]:
    """The one finite packet development check, as this module's LadError codes."""
    view = presentation_module()
    try:
        return view.development_check(raw)
    except view.LadError as exc:
        raise LadError(str(exc)) from None


def captured_policy(occurrence: dict) -> dict:
    """The routing policy captured in the occurrence's pinned guidance snapshot."""
    checksum = occurrence["guidance_files"].get(POLICY_PATH)
    if not isinstance(checksum, str) or not SHA.fullmatch(checksum):
        raise LadError("routing_policy_not_captured")
    return {"path": POLICY_PATH, "sha256": checksum, "revision": POLICY["revision"]}


def presentation_record(
    workspace: Path, occurrence: dict, result: dict, raw_result: bytes,
    artifacts: list[tuple[str, bytes]],
) -> tuple[str, bytes]:
    """Exact expected record from raw, input-projection and captured-policy facts."""
    oid = occurrence["occurrence_id"]
    input_raw = canonical(occurrence) + b"\n"
    if read(workspace, f"lad-state/occurrences/{oid}/input.json") != input_raw:
        raise LadError("occurrence_projection_conflict")
    view = presentation_module()
    try:
        raw = view.expected_record(
            occurrence=occurrence, result=result, raw_result=raw_result,
            artifacts=artifacts, input_raw=input_raw,
            policy=captured_policy(occurrence),
        )
    except view.LadError as exc:
        raise LadError(str(exc)) from None
    return presentation_path(oid, raw_result), raw


def _presentation_pin(workspace: Path, occurrence: dict, raw: bytes, result: dict,
                      artifacts: list[tuple[str, bytes]]) -> None:
    """Every load compares the complete retained record with its recomputation."""
    try:
        retained = read(workspace, presentation_path(occurrence["occurrence_id"], raw))
    except LadError as exc:
        raise LadError(
            "accepted_presentation_missing" if str(exc) == "missing_file"
            else "accepted_presentation_" + str(exc)
        ) from None
    try:
        _, expected = presentation_record(workspace, occurrence, result, raw, artifacts)
    except LadError:
        raise LadError("accepted_presentation_conflict") from None
    if retained != expected:
        raise LadError("accepted_presentation_conflict")


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


def validate_migration(
    foreground: Path, state: dict, *, migration_data: tuple[bytes, bytes] | None = None
) -> None:
    legacy, migration = state["legacy"], state["migration"]
    if legacy is None or migration is None:
        if legacy is not None or migration is not None:
            raise LadError("migration_reference_pair")
        old_ids = set()
    else:
        fields(legacy, {"path", "sha256", "format"})
        fields(migration, {"path", "sha256"})
        if (
            legacy["format"] != "millrace-lad-session/v1"
            or legacy["path"] != ("lad-state/legacy/" + legacy["sha256"] + ".json")
            or migration["path"] != "lad-state/migrations/v1-to-v2.json"
        ):
            raise LadError("migration_locator")
        old_raw, record_raw = (
            migration_data
            if migration_data is not None
            else (read(foreground, legacy["path"]), read(foreground, migration["path"]))
        )
        if (
            digest(old_raw) != legacy["sha256"]
            or digest(record_raw) != migration["sha256"]
        ):
            raise LadError("migration_digest")
        from session_compat import validate_v1

        old = validate_v1(foreground, load_json(old_raw), active_projection=False)
        record = fields(
            load_json(record_raw),
            {
                "format",
                "session_id",
                "source",
                "destination_format",
                "preserved_occurrence_ids",
                "source_mappings",
            },
        )
        old_ids = set(old["occurrences"])
        old_events = [{"id": row["id"], "kind": row["kind"]} for row in old["events"]]
        if state["events"][: len(old_events)] != old_events or any(
            state["accepted"].get(oid) != accepted
            for oid, accepted in old["accepted"].items()
        ):
            raise LadError("legacy_history_or_accepted_changed")
        if (
            record["format"] != "millrace-lad-migration/v1"
            or (
                record["session_id"] != old["session_id"]
                or old["session_id"] != state["session_id"]
            )
            or record["source"] != legacy
            or (record["destination_format"] != "millrace-lad-session/v2")
            or record["preserved_occurrence_ids"] != sorted(old_ids)
        ):
            raise LadError("migration_identity")
        mappings = record["source_mappings"]
        if not isinstance(mappings, list) or len(mappings) != len(old_ids):
            raise LadError("migration_mappings")
        for row in mappings:
            fields(
                row,
                {
                    "occurrence_id",
                    "input_sha256",
                    "source_sha256",
                    "guidance_sha256",
                    "guidance_root",
                    "requires_revalidation",
                },
            )
            oid = row["occurrence_id"]
            if (
                oid not in old_ids
                or state["occurrences"].get(oid) != old["occurrences"][oid]
            ):
                raise LadError("legacy_occurrence_changed")
            if (
                any(
                    row[k] != old["occurrences"][oid][k]
                    for k in (
                        "input_sha256",
                        "source_sha256",
                        "guidance_sha256",
                        "guidance_root",
                    )
                )
                or row["requires_revalidation"] is not True
            ):
                raise LadError("legacy_source_reinterpreted")
        if len({row["occurrence_id"] for row in mappings}) != len(old_ids):
            raise LadError("migration_duplicate_mapping")
    for oid, occurrence in state["occurrences"].items():
        if oid in old_ids:
            continue
        if occurrence.get("format") != "millrace-lad-input/v2":
            raise LadError("occurrence_contract")
        if occurrence.get("helper_mode") == "direct":
            if (
                "helper_qualification" not in occurrence
                or occurrence["helper_qualification"] is not None
            ):
                raise LadError("direct_helper_qualification")
        elif occurrence.get("helper_mode") != "native_helpers":
            raise LadError("helper_mode")


def history_name(occurrence: dict) -> str:
    stamp = datetime.fromisoformat(occurrence["created_at"])
    if stamp.tzinfo != UTC:
        raise LadError("occurrence_time_must_be_utc")
    return occurrence["role"].title() + "_" + stamp.strftime("%H%M-%m%d%y") + ".md"


def _baseline_read(
    foreground: Path, relative: str, projection: tuple[str, bytes] | None
) -> bytes:
    # Used only by the baseline guard after verifying the original parent pin.
    if projection is not None and relative == projection[0]:
        return projection[1]
    return read(foreground, relative)


def validate_baselines(
    foreground: Path,
    state: dict,
    *,
    legacy_raw: bytes | None = None,
    baseline_projection: tuple[str, bytes] | None = None,
) -> None:
    pending_records = [state["pending"]] + [
        row.get("pending") for row in state["occurrences"].values()
    ]
    for pending in pending_records:
        if not pending or pending.get("kind") != "baseline_tamper":
            continue
        ref = fields(pending["restoration"], {"path", "sha256"})
        raw = read(foreground, ref["path"])
        record = load_json(raw)
        if (
            digest(raw) != ref["sha256"]
            or digest(canonical(record)) != pending["cause_id"]
            or record["session_id"] != state["session_id"]
            or record["owner"] != pending["occurrence_id"]
        ):
            raise LadError("baseline_observation_changed")
        observed = record["observed"]
        if observed is not None and (
            digest(read(foreground, observed["path"])) != observed["sha256"]
        ):
            raise LadError("baseline_observation_changed")
    for baseline in state["baselines"].values():
        if "parent_pin" not in baseline:
            if state["migration"] is None:
                raise LadError("baseline_parent_pin_required")
            old = load_json(
                legacy_raw
                if legacy_raw is not None
                else read(foreground, state["legacy"]["path"])
            )
            if baseline not in old["baselines"].values() or (
                digest(read(foreground, baseline["path"])) != baseline["sha256"]
            ):
                raise LadError("legacy_baseline_changed")
            continue
        pin = fields(baseline["parent_pin"], {"path", "sha256"})
        raw = read(foreground, pin["path"])
        if digest(raw) != pin["sha256"]:
            raise LadError("baseline_parent_pin_changed")
        expected = {k: v for k, v in baseline.items() if k != "parent_pin"}
        if (
            load_json(raw) != expected
            or digest(_baseline_read(foreground, baseline["path"], baseline_projection))
            != baseline["sha256"]
        ):
            raise LadError("baseline_changed")

        backup = "lad-state/baseline-pins/" + baseline["occurrence_id"] + ".md"
        if digest(read(foreground, backup)) != baseline["sha256"]:
            raise LadError("baseline_backup_changed")
