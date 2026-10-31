"""L-Q1 engineering feedback: strict records, copy capture, checks and history.

Plus owns every record here. The captured LAD graph command
`scripts/engineering_feedback.py` sequences phases and guards; this module
validates records, expands a new copy's graph before its first session, runs one
captured check through Core's public owned-process helper, compares evidence,
admits artifacts and publishes/exports immutable requirement history.

Shared path-policy, execution, environment, product-layer and completion
acceptance semantics come unchanged from `check_contracts`. Nothing here
dispatches a role, reads Core's private store or certifies its own invocation.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import cast

from . import check_contracts as cc

PROFILE = "millrace-lad-engineering-profile/v1"
SNAPSHOT = "millrace-lad-requirements-snapshot/v1"
INDEX = "millrace-lad-requirements-index/v1"
RESOLVED = "millrace-engineering-resolved-checks/v1"
CHECK_INPUT = "millrace-engineering-check-input/v1"
EVIDENCE = "millrace-engineering-check-evidence/v1"
NODE_MAP = "millrace-engineering-node-map/v1"
CONTEXT = "millrace-lad-engineering-context/v1"
FINDINGS = "millrace-lad-maintenance-findings/v1"
RETAINED = "millrace-lad-retained-verdict/v1"
ADMISSION = "millrace-lad-artifact-admission/v1"
BINDING_FACTS = "millrace-role-binding-facts/v1"
ACCEPTANCE = "millrace-lad-engineering-acceptance/v1"
EXPORT = "millrace-engineering-export/v1"
# L-Q1-internal supporting records.
CAPTURE = "millrace-lad-engineering-capture/v1"
SOURCE = "millrace-engineering-source/v1"
RECEIPT = "millrace-engineering-check-receipt/v1"
START = "millrace-engineering-check-start/v1"
OBSERVATION = "millrace-engineering-product-observation/v1"
GUARD = "millrace-engineering-check-guard/v1"
PHASE = "millrace-engineering-phase/v1"
PUBLICATION = "millrace-lad-requirements-publication/v1"
BUNDLE = "millrace-lad-engineering-evidence-bundle/v1"

PURPOSES = ("functional", "lint", "type", "architecture", "quality_observation")
STATUSES = ("pass", "fail", "unavailable", "not_selected")
CLASSIFICATIONS = (
    "unchanged_pass",
    "regression",
    "preexisting_failure",
    "improved",
    "unavailable",
    "not_comparable",
    "not_selected",
)
FINDING_KINDS = ("regression", "duplication", "nesting", "architecture", "artifact",
                 "other")
LIKELIHOODS = ("demonstrated", "plausible", "hypothetical")
ARTIFACT_CLASSES = ("deliverable", "evidence", "scratch", "retained_context")
DISPOSITIONS = ("admit", "retain", "remove_requested", "hold")
REVIEW_ROLES = ("builder", "checker", "doublechecker", "arbiter", "consultant")
FAMILY_POLICIES = ("none", "different_for_implementation_review")
ROOT_REFS = ("foreground/target", "lad_target")
FIXED_NODES = (
    "engineering_baseline",
    "engineering_candidate",
    "engineering_admit",
    "engineering_finalize",
)
# Existing LAD role-preparation entries that each enabled phase precedes.
REROUTES = {
    "prepare_builder": "engineering_baseline",
    "prepare_checker": "engineering_candidate",
    "prepare_doublechecker": "engineering_candidate",
    "prepare_arbiter": "engineering_admit",
}
SCRIPT = "scripts/engineering_feedback.py"
# Existing Troubleshooter prerequisite-repair acceptance; resumes a phase.
RESUME_NODE = "accept_troubleshooter_pass_prerequisite_repaired"
PROFILE_PATH = "context/engineering-profile.json"
CAPTURE_DIR = "context/engineering"
CAPTURE_PATH = CAPTURE_DIR + "/capture.json"
SNAPSHOT_COPY = CAPTURE_DIR + "/requirements-snapshot.json"
NODE_MAP_PATH = CAPTURE_DIR + "/node-map.json"
RESOLVED_PATH = CAPTURE_DIR + "/resolved-checks.json"
# The copy's immutable engineering record store (foreground-relative).
_ENGINEERING_RECORDS = "lad-state/engineering/records"
# Existing stock LAD deterministic command allowance (graph `timeout_seconds: 90`)
# reused for engineering bookkeeping/guard nodes; checks get their own budget.
GUARD_TIMEOUT_SECONDS = 90
# Retained stdout/stderr per check: the existing LAD record read bound
# (`lad_state.read`, 4 MiB). Digests/counts still cover every byte read.
OUTPUT_RETAIN_BYTES = 4 * 1024 * 1024
_RECORD_LIMIT = 4 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_QUALIFIED = re.compile(r"[^:\s]+:[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_WORK_NAMES = (
    "lad-state",
    "lad-results",
    "candidates",
    "lad-work-marker.json",
    "work",
    "occurrences",
    ".lad-init-session",
)


class EngineeringError(Exception):
    """Expected refusal with a stable code; callers hold or translate it."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------- primitives


def encode(value: object) -> bytes:
    try:
        return cc.encode(value)
    except cc.CheckContractError as exc:
        raise EngineeringError("invalid_json") from exc


def parse(raw: bytes) -> object:
    if not isinstance(raw, bytes) or len(raw) > _RECORD_LIMIT:
        raise EngineeringError("invalid_json")
    try:
        return cc.parse(raw)
    except cc.CheckContractError as exc:
        raise EngineeringError("invalid_json") from exc


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def record_bytes(record: Mapping[str, object]) -> bytes:
    return encode(record) + b"\n"


def with_integrity(record: dict[str, object]) -> dict[str, object]:
    """Add `integrity_sha256` over the canonical record without that field."""
    body = {k: v for k, v in record.items() if k != "integrity_sha256"}
    body["integrity_sha256"] = sha256(encode(body))
    return body


def check_integrity(record: Mapping[str, object], code: str) -> None:
    body = {k: v for k, v in record.items() if k != "integrity_sha256"}
    if record.get("integrity_sha256") != sha256(encode(body)):
        raise EngineeringError(code)


def _fields(value: object, names: Sequence[str], code: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(names):
        raise EngineeringError(code)
    return cast(dict[str, object], value)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise EngineeringError(code)
    return value


def _optional_text(value: object, code: str) -> str | None:
    return None if value is None else _text(value, code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise EngineeringError(code)
    return value


def _bool(value: object, code: str) -> bool:
    if type(value) is not bool:
        raise EngineeringError(code)
    return value


def _relative(value: object, code: str, *, root: bool = False) -> str:
    """A contained POSIX path; `.` only where the whole root is meant."""
    if root and value == ".":
        return "."
    try:
        return cc._relative(value, code)
    except cc.CheckContractError as exc:
        raise EngineeringError(code) from exc


def _prefixes(value: object, code: str, *, empty: bool = True,
              root: bool = False) -> list[str]:
    """Distinct literal contained prefixes (no wildcard engine)."""
    if not isinstance(value, list) or (not empty and not value):
        raise EngineeringError(code)
    items = [_relative(item, code, root=root) for item in value]
    if len(set(items)) != len(items):
        raise EngineeringError(code)
    return items


def ref(value: object, code: str = "invalid_ref") -> dict[str, object]:
    try:
        return cc.validate_ref(value, code)
    except cc.CheckContractError as exc:
        raise EngineeringError(code) from exc


def make_ref(path: str, raw: bytes) -> dict[str, object]:
    return {"path": path, "sha256": sha256(raw)}


def within(path: str, prefix: str) -> bool:
    return prefix == "." or path == prefix or path.startswith(prefix + "/")


def node_digest(check_id: str) -> tuple[str, str]:
    """Full SHA-256 hex and its unpadded uppercase RFC 4648 base32 (52 chars)."""
    try:
        raw = check_id.encode("utf-8")
    except UnicodeError as exc:
        raise EngineeringError("invalid_check_id") from exc
    digest = hashlib.sha256(raw).digest()
    return digest.hex(), base64.b32encode(digest).decode("ascii").rstrip("=")


def node_names(check_id: str) -> dict[str, str]:
    digest, encoded = node_digest(check_id)
    return {
        "check_id": check_id,
        "digest": digest,
        "baseline_node": "eb_" + encoded,
        "candidate_node": "ec_" + encoded,
        "baseline_accept_node": "ab_" + encoded,
        "candidate_accept_node": "ac_" + encoded,
    }


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


# -------------------------------------------------------------- file helpers


def read_regular(path: Path, *, limit: int = _RECORD_LIMIT) -> bytes:
    """Read one regular file without following a final link or blocking on FIFOs."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError as exc:
        raise EngineeringError("missing_file") from exc
    except OSError as exc:
        raise EngineeringError("unsafe_file") from exc
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise EngineeringError("regular_file_required")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise EngineeringError("file_too_large")
    return raw


def contained(root: Path, relative: str, *, missing: bool = False) -> Path:
    """Resolve a contained relative path refusing links at every component."""
    _relative(relative, "invalid_relative_path")
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if missing:
                continue
            raise EngineeringError("missing_file") from None
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise EngineeringError("unsafe_file")
    return current


def write_immutable(root: Path, relative: str, raw: bytes) -> dict[str, object]:
    """Create an exact immutable file, or verify an identical earlier write."""
    path = contained(root, relative, missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        if read_regular(path) != raw:
            raise EngineeringError("immutable_conflict")
        return make_ref(relative, raw)
    fd, name = tempfile.mkstemp(prefix=".lq-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            if read_regular(path) != raw:
                raise EngineeringError("immutable_conflict") from None
    finally:
        Path(name).unlink(missing_ok=True)
    return make_ref(relative, raw)


def replace_file(root: Path, relative: str, raw: bytes) -> None:
    path = contained(root, relative, missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".lq-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_ref(root: Path, value: object, code: str) -> tuple[dict[str, object], bytes]:
    pinned = ref(value, code)
    raw = read_regular(contained(root, cast(str, pinned["path"])))
    if sha256(raw) != pinned["sha256"]:
        raise EngineeringError(code)
    return pinned, raw


def store_record(root: Path, directory: str, record: Mapping[str, object]
                 ) -> dict[str, object]:
    """Content-addressed immutable record under `directory`; returns its Ref."""
    raw = record_bytes(record)
    return write_immutable(root, f"{directory}/{sha256(raw)}.json", raw)


def load_record(root: Path, value: object, fmt: str, code: str) -> dict[str, object]:
    _, raw = load_ref(root, value, code)
    obj = parse(raw)
    if not isinstance(obj, dict) or obj.get("format") != fmt:
        raise EngineeringError(code)
    return obj


# ------------------------------------------------------------------- profile


def _reasoned(value: object, code: str) -> dict[str, object]:
    obj = _fields(value, ("reason", "origin", "override"), code)
    for key in ("reason", "origin", "override"):
        _text(obj[key], code)
    return obj


def _check(value: object, policy_ref: dict[str, object]) -> dict[str, object]:
    code = "invalid_check_definition"
    obj = _fields(
        value,
        (
            "id", "purpose", "argv", "cwd", "environment", "timeout_seconds",
            "timeout_policy", "expected_exit_code", "path_policy_ref",
            "expected_products", "inputs", "requirement_ids", "selection",
            "required", "origin", "override",
        ),
        code,
    )
    _text(obj["id"], code)
    node_digest(cast(str, obj["id"]))
    if obj["purpose"] not in PURPOSES:
        raise EngineeringError(code)
    timeout = obj["timeout_seconds"]
    if timeout is None:
        if obj["timeout_policy"] is not None:
            _reasoned(obj["timeout_policy"], code)
    else:
        _reasoned(obj["timeout_policy"], code)
    # Shape-check the shared execution fields with a placeholder deadline;
    # the actual deadline is resolved before dispatch, never null at execution.
    probe = {key: obj[key] for key in cc.EXECUTION_FIELDS}
    probe["timeout_seconds"] = 1 if timeout is None else timeout
    probe["timeout_policy"] = obj["timeout_policy"] or {
        "reason": "shape", "origin": "shape", "override": "shape",
    }
    try:
        cc.validate_execution(probe, policy_ref=policy_ref)
    except cc.CheckContractError as exc:
        raise EngineeringError(
            "check_policy_ref_mismatch"
            if exc.code == "execution_policy_mismatch" else code
        ) from exc
    _prefixes(obj["inputs"], code, root=True)
    ids = obj["requirement_ids"]
    if not isinstance(ids, list) or len(set(map(str, ids))) != len(ids):
        raise EngineeringError(code)
    for item in ids:
        if not isinstance(item, str) or not (
            _IDENT.fullmatch(item) or _QUALIFIED.fullmatch(item)
        ):
            raise EngineeringError(code)
    selection = _fields(obj["selection"], ("when", "paths"), code)
    if selection["when"] == "always":
        if selection["paths"] != []:
            raise EngineeringError(code)
    elif selection["when"] == "paths_changed":
        _prefixes(selection["paths"], code, empty=False, root=True)
    else:
        raise EngineeringError(code)
    _bool(obj["required"], code)
    if obj["required"]:
        _text(obj["origin"], code)
        _text(obj["override"], code)
    else:
        _optional_text(obj["origin"], code)
        _optional_text(obj["override"], code)
    return obj


def load_profile(raw: bytes) -> dict[str, object]:
    """Strict `millrace-lad-engineering-profile/v1`; unknown fields refuse."""
    code = "invalid_engineering_profile"
    obj = _fields(
        parse(raw),
        (
            "format", "profile_id", "revision", "repository", "requirements_snapshot",
            "checks", "guidelines", "metrics", "artifact_policy", "review_bindings",
            "path_policy_ref", "check_bookkeeping", "family_policy",
            "evaluation_export",
        ),
        code,
    )
    if obj["format"] != PROFILE:
        raise EngineeringError("unsupported_engineering_profile")
    _text(obj["profile_id"], code)
    _text(obj["revision"], code)
    repository = _fields(
        obj["repository"], ("target_id", "root_ref", "source_scope", "excluded_paths"),
        code,
    )
    _text(repository["target_id"], code)
    if repository["root_ref"] not in ROOT_REFS:
        raise EngineeringError(code)
    _prefixes(repository["source_scope"], code, empty=False, root=True)
    _prefixes(repository["excluded_paths"], code)
    pinned = _fields(
        obj["requirements_snapshot"], ("history_id", "sequence", "path", "sha256"), code
    )
    history_id(pinned["history_id"])
    if type(pinned["sequence"]) is not int or pinned["sequence"] < 1:
        raise EngineeringError(code)
    _relative(pinned["path"], code)
    _sha(pinned["sha256"], code)
    policy_ref = ref(obj["path_policy_ref"], code)
    checks = obj["checks"]
    if not isinstance(checks, list):
        raise EngineeringError(code)
    seen: dict[str, str] = {}
    for item in checks:
        check = _check(item, policy_ref)
        names = node_names(cast(str, check["id"]))
        if check["id"] in seen or names["digest"] in seen.values():
            raise EngineeringError("duplicate_check_id")
        seen[cast(str, check["id"])] = names["digest"]
    guidelines = obj["guidelines"]
    if not isinstance(guidelines, list):
        raise EngineeringError(code)
    guideline_ids: set[str] = set()
    for item in guidelines:
        row = _fields(item, ("id", "text", "source_ref", "mode"), code)
        _text(row["id"], code)
        _text(row["text"], code)
        _text(row["source_ref"], code)
        if row["mode"] not in ("advisory", "required") or row["id"] in guideline_ids:
            raise EngineeringError(code)
        guideline_ids.add(cast(str, row["id"]))
    metrics = _fields(obj["metrics"], ("changed_functions", "duplication", "nesting"),
                      code)
    for value in metrics.values():
        _bool(value, code)
    policy = _fields(
        obj["artifact_policy"],
        ("deliverable_paths", "evidence_root", "scratch_root", "retention_root"),
        code,
    )
    _prefixes(policy["deliverable_paths"], code, root=True)
    roots = [
        _relative(policy[key], code)
        for key in ("evidence_root", "scratch_root", "retention_root")
    ]
    for index, first in enumerate(roots):
        if first == "target" or first.startswith("target/") or first.split("/")[0] in {
            "lad-state", ".lad-init-session",
        }:
            raise EngineeringError("artifact_root_in_product_or_control")
        for second in roots[index + 1:]:
            if within(first, second) or within(second, first):
                raise EngineeringError("artifact_roots_overlap")
    bindings = obj["review_bindings"]
    if bindings is not None:
        if not isinstance(bindings, dict) or not set(bindings) <= set(REVIEW_ROLES):
            raise EngineeringError(code)
        for value in bindings.values():
            entry = _fields(value, ("binding_ref", "configured_family"), code)
            if not isinstance(entry["binding_ref"], str) or not _IDENT.fullmatch(
                entry["binding_ref"]
            ):
                raise EngineeringError(code)
            _text(entry["configured_family"], code)
    bookkeeping = _fields(
        obj["check_bookkeeping"], ("seconds", "reason", "origin", "override"), code
    )
    try:
        cc._seconds(bookkeeping["seconds"], code)
    except cc.CheckContractError as exc:
        raise EngineeringError(code) from exc
    for key in ("reason", "origin", "override"):
        _text(bookkeeping[key], code)
    if obj["family_policy"] not in FAMILY_POLICIES:
        raise EngineeringError(code)
    export = _fields(obj["evaluation_export"], ("enabled", "destination_ref"), code)
    if _bool(export["enabled"], code):
        destination = _text(export["destination_ref"], code)
        if not Path(destination).is_absolute():
            raise EngineeringError(code)
    elif export["destination_ref"] is not None:
        raise EngineeringError(code)
    return obj


def history_id(value: object) -> str:
    """One safe history directory component; no length policy beyond the host's."""
    if not isinstance(value, str) or not _COMPONENT.fullmatch(value) or value in {
        ".", ".."
    }:
        raise EngineeringError("invalid_history_id")
    return value


def required_guidelines(profile: Mapping[str, object],
                        snapshot: Mapping[str, object]) -> None:
    """A required guideline must cite an accepted active repository requirement."""
    active = {
        row["id"] for row in cast(list[dict[str, object]], snapshot["requirements"])
        if row["status"] == "active"
    }
    for row in cast(list[dict[str, object]], profile["guidelines"]):
        if row["mode"] == "required" and row["source_ref"] not in active:
            raise EngineeringError("required_guideline_without_requirement")


# ------------------------------------------------------- requirements history


def load_requirements(raw: bytes) -> dict[str, object]:
    """Strict `millrace-lad-requirements-snapshot/v1` with integrity."""
    code = "invalid_requirements_snapshot"
    obj = _fields(
        parse(raw),
        (
            "format", "history_id", "sequence", "repository_id", "previous_ref",
            "accepted_tasks", "requirements", "source_ref", "integrity_sha256",
        ),
        code,
    )
    if obj["format"] != SNAPSHOT:
        raise EngineeringError(code)
    history_id(obj["history_id"])
    sequence = obj["sequence"]
    if type(sequence) is not int or sequence < 1:
        raise EngineeringError(code)
    _text(obj["repository_id"], code)
    if (sequence == 1) != (obj["previous_ref"] is None):
        raise EngineeringError(code)
    if obj["previous_ref"] is not None:
        ref(obj["previous_ref"], code)
    source_ref(obj["source_ref"], code)
    tasks = obj["accepted_tasks"]
    if not isinstance(tasks, list):
        raise EngineeringError(code)
    task_ids: set[str] = set()
    for item in tasks:
        row = _fields(
            item,
            ("task_id", "session_id", "request_ref", "criteria_ref", "acceptance_ref",
             "candidate_source_ref"),
            code,
        )
        _text(row["task_id"], code)
        _text(row["session_id"], code)
        for key in ("request_ref", "criteria_ref", "acceptance_ref",
                    "candidate_source_ref"):
            ref(row[key], code)
        if row["task_id"] in task_ids or ":" in cast(str, row["task_id"]):
            raise EngineeringError(code)
        task_ids.add(cast(str, row["task_id"]))
    requirements = obj["requirements"]
    if not isinstance(requirements, list):
        raise EngineeringError(code)
    ids: dict[str, dict[str, object]] = {}
    for item in requirements:
        row = _fields(
            item,
            ("id", "text", "request_quote", "origin_task_id", "status", "supersedes",
             "evidence_refs"),
            code,
        )
        identity = row["id"]
        if not isinstance(identity, str) or not _QUALIFIED.fullmatch(identity):
            raise EngineeringError(code)
        _text(row["text"], code)
        _text(row["request_quote"], code)
        if row["origin_task_id"] not in task_ids or not identity.startswith(
            cast(str, row["origin_task_id"]) + ":"
        ):
            raise EngineeringError("requirement_without_accepted_origin")
        if row["status"] not in ("active", "superseded") or identity in ids:
            raise EngineeringError(code)
        supersedes = row["supersedes"]
        if not isinstance(supersedes, list) or any(
            item not in ids for item in supersedes
        ):
            raise EngineeringError("supersession_without_prior_requirement")
        evidence = row["evidence_refs"]
        if not isinstance(evidence, list) or not evidence:
            raise EngineeringError(code)
        for item in evidence:
            ref(item, code)
        ids[identity] = row
    superseded_by = {old for row in ids.values()
                     for old in cast(list[str], row["supersedes"])}
    for identity, row in ids.items():
        # A superseded requirement needs an explicit later superseding row; a
        # failing prior requirement is never silently deleted or relabelled.
        if (row["status"] == "superseded") != (identity in superseded_by):
            raise EngineeringError("supersession_not_recorded")
    check_integrity(obj, "requirements_snapshot_integrity")
    return obj


def source_ref(value: object, code: str = "invalid_source_ref") -> dict[str, object]:
    obj = _fields(
        value,
        ("target_id", "git_base", "index_sha256", "scoped_files_sha256",
         "outside_sha256"),
        code,
    )
    _text(obj["target_id"], code)
    if (obj["git_base"] is None) != (obj["index_sha256"] is None):
        raise EngineeringError(code)
    if obj["git_base"] is not None:
        _text(obj["git_base"], code)
        _sha(obj["index_sha256"], code)
    _sha(obj["scoped_files_sha256"], code)
    _sha(obj["outside_sha256"], code)
    return obj


def active_requirements(snapshot: Mapping[str, object]) -> list[dict[str, object]]:
    return [
        row for row in cast(list[dict[str, object]], snapshot["requirements"])
        if row["status"] == "active"
    ]


def snapshot_relative(history: str, sequence: int, digest: str) -> str:
    return f"{history}/snapshots/{sequence:06d}-{digest}.json"


def _history_dir(home: Path, history: str) -> Path:
    if not home.is_absolute() or not home.is_dir() or home.is_symlink():
        raise EngineeringError("invalid_history_home")
    return home / history_id(history)


def load_index(home: Path, history: str) -> dict[str, object]:
    code = "invalid_requirements_index"
    obj = _fields(
        parse(read_regular(_history_dir(home, history) / "index.json")),
        ("format", "history_id", "repository_id", "head"),
        code,
    )
    if obj["format"] != INDEX or obj["history_id"] != history:
        raise EngineeringError(code)
    head = _fields(obj["head"], ("sequence", "path", "sha256"), code)
    if type(head["sequence"]) is not int or head["sequence"] < 1:
        raise EngineeringError(code)
    _sha(head["sha256"], code)
    if head["path"] != snapshot_relative(history, head["sequence"], head["sha256"]):
        raise EngineeringError(code)
    return obj


def read_snapshot(home: Path, path: str, digest: str
                  ) -> tuple[dict[str, object], bytes]:
    raw = read_regular(contained(home, path))
    if sha256(raw) != digest:
        raise EngineeringError("requirements_snapshot_changed")
    snapshot = load_requirements(raw)
    if path != snapshot_relative(
        cast(str, snapshot["history_id"]), cast(int, snapshot["sequence"]), digest
    ):
        raise EngineeringError("requirements_snapshot_locator")
    return snapshot, raw


def chain(home: Path, history: str) -> list[tuple[dict[str, object], bytes]]:
    """Validated snapshots from the indexed head back to sequence 1."""
    index = load_index(home, history)
    head = cast(dict[str, object], index["head"])
    rows: list[tuple[dict[str, object], bytes]] = []
    path, digest = cast(str, head["path"]), cast(str, head["sha256"])
    while True:
        snapshot, raw = read_snapshot(home, path, digest)
        if snapshot["repository_id"] != index["repository_id"] or (
            rows and snapshot["sequence"] != cast(int, rows[-1][0]["sequence"]) - 1
        ):
            raise EngineeringError("requirements_chain_broken")
        rows.append((snapshot, raw))
        previous = snapshot["previous_ref"]
        if previous is None:
            break
        pinned = ref(previous)
        path, digest = cast(str, pinned["path"]), cast(str, pinned["sha256"])
    if rows[0][0]["sequence"] != head["sequence"]:
        raise EngineeringError("requirements_chain_broken")
    return rows


@contextmanager
def history_lock(home: Path, history: str) -> Iterator[None]:
    """Nonblocking exclusive history lock; a busy publisher refuses, never waits."""
    directory = _history_dir(home, history)
    directory.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(directory / ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                         0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EngineeringError("engineering_history_busy") from exc
        yield
    finally:
        os.close(descriptor)


def initialize_history(home: Path, pinned: Mapping[str, object]) -> str:
    """Index an explicit operator-admitted initial snapshot once, or verify it."""
    history = cast(str, pinned["history_id"])
    path, digest = cast(str, pinned["path"]), cast(str, pinned["sha256"])
    snapshot, _ = read_snapshot(home, path, digest)
    if snapshot["history_id"] != history or snapshot["sequence"] != pinned["sequence"]:
        raise EngineeringError("requirements_pin_mismatch")
    for row in cast(list[dict[str, object]], snapshot["accepted_tasks"]):
        for key in ("request_ref", "criteria_ref", "acceptance_ref",
                    "candidate_source_ref"):
            load_ref(_history_dir(home, history), row[key],
                     "requirements_origin_unadmitted")
    with history_lock(home, history):
        index_path = _history_dir(home, history) / "index.json"
        if not os.path.lexists(index_path):
            if snapshot["sequence"] != 1:
                raise EngineeringError("requirements_index_missing")
            index = {
                "format": INDEX, "history_id": history,
                "repository_id": snapshot["repository_id"],
                "head": {"sequence": 1, "path": path, "sha256": digest},
            }
            write_immutable(home, history + "/index.json", record_bytes(index))
            return "initialized"
    if not any(sha256(raw) == digest for _, raw in chain(home, history)):
        raise EngineeringError("requirements_snapshot_not_indexed")
    head = cast(dict[str, object], load_index(home, history)["head"])
    return "head" if head["sha256"] == digest else "pinned_not_head"


def successor(previous: Mapping[str, object], previous_raw: bytes, previous_path: str,
              task: Mapping[str, object]) -> dict[str, object]:
    """Deterministic next snapshot appending one accepted task's requirements."""
    task_id = _text(task["task_id"], "invalid_publication")
    if cast(dict[str, object], task["source_ref"])["target_id"] != previous[
        "repository_id"
    ]:
        raise EngineeringError("engineering_history_target_mismatch")
    rows = [dict(row) for row in cast(list[dict[str, object]],
                                      previous["requirements"])]
    known = {cast(str, row["id"]): row for row in rows}
    if any(row["task_id"] == task_id for row in cast(
        list[dict[str, object]], previous["accepted_tasks"]
    )):
        raise EngineeringError("task_already_published")
    new_rows: list[dict[str, object]] = []
    for criterion in cast(list[dict[str, object]], task["criteria"]):
        identity = f"{task_id}:{criterion['id']}"
        supersedes = sorted(cast(list[str], criterion.get("supersedes", [])))
        for old in supersedes:
            if old not in known or known[old]["status"] != "active":
                raise EngineeringError("supersession_without_active_requirement")
            known[old]["status"] = "superseded"
        new_rows.append({
            "id": identity, "text": criterion["text"],
            "request_quote": criterion["request_quote"], "origin_task_id": task_id,
            "status": "active", "supersedes": supersedes,
            "evidence_refs": [task["acceptance_ref"]] + (
                [task["bundle_ref"]] if task.get("bundle_ref") is not None else []
            ),
        })
    accepted = list(cast(list[dict[str, object]], previous["accepted_tasks"]))
    accepted.append({
        key: task[key] for key in (
            "task_id", "session_id", "request_ref", "criteria_ref", "acceptance_ref",
            "candidate_source_ref",
        )
    })
    record = with_integrity({
        "format": SNAPSHOT,
        "history_id": previous["history_id"],
        "sequence": cast(int, previous["sequence"]) + 1,
        "repository_id": previous["repository_id"],
        "previous_ref": make_ref(previous_path, previous_raw),
        "accepted_tasks": accepted,
        "requirements": rows + new_rows,
        "source_ref": task["source_ref"],
    })
    load_requirements(record_bytes(record))
    return record


def publish_requirements(home: Path, expected: Mapping[str, object],
                         task: Mapping[str, object]) -> dict[str, object]:
    """Lock, compare the expected prior head, publish once, then CAS the index.

    Crash replay recomputes the same successor digest and reuses it. A different
    head means another task already published: `engineering_history_stale`.
    """
    history = cast(str, expected["history_id"])
    directory = _history_dir(home, history)
    with history_lock(home, history):
        rows = chain(home, history)
        head_snapshot, head_raw = rows[0]
        index = load_index(home, history)
        head = cast(dict[str, object], index["head"])
        prior = next(
            ((snap, raw) for snap, raw in rows if sha256(raw) == expected["sha256"]),
            None,
        )
        if prior is None:
            raise EngineeringError("requirements_snapshot_not_indexed")
        prior_path = snapshot_relative(history, cast(int, prior[0]["sequence"]),
                                       sha256(prior[1]))
        record = successor(prior[0], prior[1], prior_path, task)
        raw = record_bytes(record)
        digest = sha256(raw)
        path = snapshot_relative(history, cast(int, record["sequence"]), digest)
        if head["sha256"] == digest:
            return {"status": "replayed", "path": path, "sha256": digest,
                    "sequence": record["sequence"]}
        if head["sha256"] != expected["sha256"]:
            raise EngineeringError("engineering_history_stale")
        for name, body in cast(dict[str, bytes], task["records"]).items():
            write_immutable(directory, "records/" + name, body)
        write_immutable(home, path, raw)
        current = load_index(home, history)
        if current != index:
            raise EngineeringError("engineering_history_stale")
        index_body = {
            "format": INDEX, "history_id": history,
            "repository_id": head_snapshot["repository_id"],
            "head": {"sequence": record["sequence"], "path": path, "sha256": digest},
        }
        replace_file(home, history + "/index.json", record_bytes(index_body))
        return {"status": "published", "path": path, "sha256": digest,
                "sequence": record["sequence"]}


# ------------------------------------------------- resolution and expansion


def resolve_checks(
    profile: Mapping[str, object],
    profile_ref: Mapping[str, object],
    graph: Mapping[str, object],
    policy: cc.PathPolicy,
    *,
    runtime_timeout: object = None,
    ceiling: object = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Resolve each check deadline and its one-check node budget.

    Order: explicit profile value, then the foreground plane and workflow
    defaults of this graph, then an explicitly supplied runtime default, then
    Core's 1,800-second fallback with `timeout_defaulted`. The generated check
    node and its binding are outputs and never inputs. A known operator ceiling
    is reported; one that would clip a derived check budget refuses.
    """
    plane = cast(dict[str, object], cast(dict[str, object], graph["planes"])[
        "foreground"
    ])
    plane_default = cast(dict[str, object], plane.get("defaults", {})).get(
        "timeout_seconds"
    )
    workflow_default = cast(dict[str, object], graph.get("defaults", {})).get(
        "timeout_seconds"
    )
    bookkeeping = cast(dict[str, object], profile["check_bookkeeping"])
    resolved: list[dict[str, object]] = []
    budgets: list[dict[str, object]] = []
    for check in cast(list[dict[str, object]], profile["checks"]):
        try:
            timeout = cc.resolve_timeout(
                check["timeout_seconds"], check["timeout_policy"],
                plane=plane_default, workflow=workflow_default, runtime=runtime_timeout,
            )
            definition = dict(check)
            definition["timeout_seconds"] = timeout["timeout_seconds"]
            definition["timeout_policy"] = timeout["timeout_policy"]
            execution = cc.execution_of(
                definition, policy=policy, policy_ref=profile["path_policy_ref"]
            )
            budget = cc.node_budget(
                [timeout["timeout_seconds"]], bookkeeping["seconds"],
                ceiling_seconds=ceiling,
            )
        except cc.CheckContractError as exc:
            raise EngineeringError(exc.code) from exc
        if budget["clipped"]:
            raise EngineeringError("operator_ceiling_clips_check_budget")
        names = node_names(cast(str, check["id"]))
        resolved.append({
            "definition": definition,
            "timeout_source": timeout["source"],
            "timeout_warning": timeout["warning"],
            "execution_sha256": cc.execution_sha256(execution),
            "node_budget": budget,
        })
        budgets.append({"check_id": check["id"], "nodes": [
            names["baseline_node"], names["candidate_node"]
        ], **budget})
    return resolved, budgets


def resolved_record(profile_ref: Mapping[str, object], profile: Mapping[str, object],
                    checks: list[dict[str, object]], graph_raw: bytes
                    ) -> dict[str, object]:
    return {
        "format": RESOLVED,
        "profile_ref": dict(profile_ref),
        "path_policy_ref": profile["path_policy_ref"],
        "checks": checks,
        "bookkeeping": profile["check_bookkeeping"],
        "graph_ref": make_ref("workflow.json", graph_raw),
    }


def validate_resolved(value: object, profile: Mapping[str, object]
                      ) -> dict[str, object]:
    code = "invalid_resolved_checks"
    obj = _fields(value, ("format", "profile_ref", "path_policy_ref", "checks",
                          "bookkeeping", "graph_ref"), code)
    if obj["format"] != RESOLVED or obj["path_policy_ref"] != profile[
        "path_policy_ref"
    ] or obj["bookkeeping"] != profile["check_bookkeeping"]:
        raise EngineeringError(code)
    ref(obj["profile_ref"], code)
    ref(obj["graph_ref"], code)
    checks = obj["checks"]
    authored = cast(list[dict[str, object]], profile["checks"])
    if not isinstance(checks, list) or len(checks) != len(authored):
        raise EngineeringError(code)
    for row, check in zip(checks, authored, strict=True):
        entry = _fields(row, ("definition", "timeout_source", "timeout_warning",
                              "execution_sha256", "node_budget"), code)
        definition = cast(dict[str, object], entry["definition"])
        if not isinstance(definition, dict) or {
            k: v for k, v in definition.items()
            if k not in ("timeout_seconds", "timeout_policy")
        } != {k: v for k, v in check.items()
              if k not in ("timeout_seconds", "timeout_policy")}:
            raise EngineeringError(code)
        if check["timeout_seconds"] is not None and (
            definition["timeout_seconds"] != check["timeout_seconds"]
            or definition["timeout_policy"] != check["timeout_policy"]
        ):
            raise EngineeringError(code)
        try:
            execution = cc.execution_of(definition,
                                        policy_ref=profile["path_policy_ref"])
            digest = cc.execution_sha256(execution)
        except cc.CheckContractError as exc:
            raise EngineeringError(code) from exc
        if entry["execution_sha256"] != digest:
            raise EngineeringError(code)
    return obj


def node_map(profile_ref: Mapping[str, object], profile: Mapping[str, object]
             ) -> dict[str, object]:
    return {
        "format": NODE_MAP,
        "profile_ref": dict(profile_ref),
        "entries": [node_names(cast(str, check["id"])) for check in cast(
            list[dict[str, object]], profile["checks"]
        )],
    }


def validate_node_map(value: object, profile: Mapping[str, object]
                      ) -> dict[str, object]:
    obj = _fields(value, ("format", "profile_ref", "entries"), "invalid_node_map")
    expected = [node_names(cast(str, check["id"])) for check in cast(
        list[dict[str, object]], profile["checks"]
    )]
    if obj["format"] != NODE_MAP or obj["entries"] != expected:
        raise EngineeringError("invalid_node_map")
    ref(obj["profile_ref"], "invalid_node_map")
    return obj


def _command(args: list[str], timeout: int | float, outcomes: dict[str, object],
             *, failure: object = None) -> dict[str, object]:
    default_error = {"pause": {"reason": "lad_deterministic_failure"}}
    default_unknown = {"pause": {"reason": "lad_unknown_deterministic_outcome"}}
    return {
        "invoke": {
            "binding": "command",
            "timeout_seconds": timeout,
            "config": {"script": SCRIPT, "args": args},
        },
        "result": "json-v1",
        "outcomes": list(outcomes),
        "on": outcomes,
        "on_error": failure if failure is not None else default_error,
        "on_unknown": failure if failure is not None else default_unknown,
    }


def _pause(reason: str, resume: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {"reason": reason}
    if resume is not None:
        body["resume"] = resume
    return {"pause": body}


def expand_graph(graph: dict[str, object], entries: list[dict[str, str]],
                 budgets: Mapping[str, Mapping[str, object]],
                 control_files: list[str]) -> dict[str, object]:
    """Insert the declared deterministic L-Q1 nodes into one LAD copy graph.

    Existing `goto` entries to role preparation are routed through their phase,
    and Arbiter CLOSE through finalization. Duplicate or conflicting node IDs
    refuse; nothing is truncated or renamed.
    """
    planes = cast(dict[str, dict[str, object]], graph.get("planes"))
    if (
        graph.get("format") != "millrace-router/v4"
        or not isinstance(planes, dict)
        or set(planes) != {"foreground", "learning"}
        or SCRIPT not in cast(list[str], graph.get("control_files", []))
    ):
        raise EngineeringError("engineering_graph_unsupported")
    nodes = cast(dict[str, dict[str, object]], planes["foreground"]["nodes"])
    generated = list(FIXED_NODES)
    for entry in entries:
        generated += [entry[key] for key in (
            "baseline_node", "candidate_node", "baseline_accept_node",
            "candidate_accept_node",
        )]
    existing = {name for plane in planes.values() for name in cast(
        dict[str, object], plane["nodes"]
    )}
    if len(set(generated)) != len(generated) or set(generated) & existing:
        raise EngineeringError("engineering_node_conflict")
    for target in (*REROUTES, "emit_learning_request", "prepare_troubleshooter",
                   "prepare_consultant", RESUME_NODE):
        if target not in nodes:
            raise EngineeringError("engineering_graph_unsupported")
    close = cast(dict[str, object], cast(dict[str, object], nodes.get(
        "accept_arbiter_pass", {}
    )).get("on", {})).get("CLOSE")
    if close != {"goto": "emit_learning_request"}:
        raise EngineeringError("engineering_graph_unsupported")
    for plane in planes.values():
        for node in cast(dict[str, dict[str, object]], plane["nodes"]).values():
            routes = [*cast(dict[str, object], node["on"]).values(), node["on_error"],
                      node["on_unknown"]]
            for route in routes:
                if not isinstance(route, dict) or set(route) - {"goto", "pause",
                                                                 "finish"}:
                    raise EngineeringError("engineering_graph_unsupported")
                resume = cast(dict[str, object], route.get("pause") or {}).get("resume")
                if resume in REROUTES:
                    raise EngineeringError("engineering_graph_unsupported")
    for node in nodes.values():
        on = cast(dict[str, object], node["on"])
        for outcome, route in list(on.items()):
            target = cast(dict[str, object], route).get("goto")
            if target in REROUTES:
                on[outcome] = {"goto": REROUTES[cast(str, target)]}
        for key in ("on_error", "on_unknown"):
            target = cast(dict[str, object], node[key]).get("goto")
            if target in REROUTES:
                node[key] = {"goto": REROUTES[cast(str, target)]}
    cast(dict[str, object], nodes["accept_arbiter_pass"]["on"])["CLOSE"] = {
        "goto": "engineering_finalize"
    }
    refused = {"pause": {"reason": "lad_reconciliation_required"}}
    reconcile = _pause("lad_engineering_reconciliation_required")
    baseline: dict[str, object] = {"READY": {"goto": "prepare_builder"}}
    candidate: dict[str, object] = {
        "CHECKED": {"goto": "prepare_checker"},
        "FINDINGS": {"goto": "prepare_checker"},
        "REPAIR_CHECKED": {"goto": "prepare_doublechecker"},
        "REPAIR_FINDINGS": {"goto": "prepare_doublechecker"},
    }
    for entry in entries:
        encoded = entry["baseline_node"][3:]
        baseline["RUN_" + encoded] = {"goto": entry["baseline_node"]}
        candidate["RUN_" + encoded] = {"goto": entry["candidate_node"]}
    resume = cast(dict[str, object], nodes[RESUME_NODE])
    for phase, outcomes in (("baseline", baseline), ("candidate", candidate)):
        # Protected foreign work needs explicit human authority; an ordinary
        # missing technical prerequisite uses existing technical recovery.
        outcomes["HUMAN_REQUIRED"] = _pause(
            "lad_engineering_human_decision_required", "engineering_" + phase
        )
        outcomes["TECHNICAL_RECOVERY"] = {"goto": "prepare_troubleshooter"}
        outcomes["CONSULTANT"] = {"goto": "prepare_consultant"}
        route = "RESUME_ENGINEERING_" + phase.upper()
        if route in cast(dict[str, object], resume["on"]):
            raise EngineeringError("engineering_node_conflict")
        cast(list[str], resume["outcomes"]).append(route)
        cast(dict[str, object], resume["on"])[route] = {"goto": "engineering_" + phase}
        outcomes["RECONCILE"] = reconcile
        outcomes["REFUSED"] = refused
        nodes["engineering_" + phase] = _command(
            ["phase", phase], GUARD_TIMEOUT_SECONDS, outcomes
        )
    for entry in entries:
        encoded = entry["baseline_node"][3:]
        budget = cast(dict[str, object], budgets[entry["check_id"]])
        for phase, check_node, accept_node in (
            ("baseline", entry["baseline_node"], entry["baseline_accept_node"]),
            ("candidate", entry["candidate_node"], entry["candidate_accept_node"]),
        ):
            to_guard = {"goto": accept_node}
            nodes[check_node] = _command(
                ["check", phase, encoded],
                cast(int | float, budget["budget_seconds"]),
                {outcome: to_guard for outcome in (
                    "PASS", "FAIL", "UNAVAILABLE", "NOT_SELECTED", "UNKNOWN",
                )} | {"REFUSED": refused},
                failure=to_guard,
            )
            nodes[accept_node] = _command(
                ["accept", phase, encoded], GUARD_TIMEOUT_SECONDS,
                {
                    "ACCEPTED": {"goto": "engineering_" + phase},
                    "RECONCILE": _pause("lad_engineering_check_closure_unknown",
                                        accept_node),
                    "REFUSED": refused,
                },
            )
    nodes["engineering_admit"] = _command(["admit"], GUARD_TIMEOUT_SECONDS, {
        "ADMITTED": {"goto": "prepare_arbiter"},
        "FINDINGS": _pause("lad_engineering_admission_hold", "engineering_admit"),
        "RECONCILE": reconcile,
        "REFUSED": refused,
    })
    nodes["engineering_finalize"] = _command(["finalize"], GUARD_TIMEOUT_SECONDS, {
        "FINALIZED": {"goto": "emit_learning_request"},
        "REFUSED": refused,
    })
    files = cast(list[str], graph["control_files"])
    for name in control_files:
        if name in files:
            raise EngineeringError("engineering_control_conflict")
        files.append(name)
    return graph


def graph_nodes_match(graph: Mapping[str, object], entries: list[dict[str, str]],
                      checks: list[dict[str, object]]) -> None:
    """Runtime check that the captured graph still carries the generated nodes."""
    nodes = cast(dict[str, dict[str, object]], cast(dict[str, dict[str, object]], graph[
        "planes"
    ])["foreground"]["nodes"])
    for name in FIXED_NODES:
        if name not in nodes:
            raise EngineeringError("engineering_graph_changed")
    for phase in ("baseline", "candidate"):
        guard = cast(dict[str, object], nodes["engineering_" + phase]["on"])
        resume = cast(dict[str, object], nodes[RESUME_NODE]["on"])
        if (guard.get("TECHNICAL_RECOVERY") != {"goto": "prepare_troubleshooter"}
                or guard.get("CONSULTANT") != {"goto": "prepare_consultant"}
                or resume.get("RESUME_ENGINEERING_" + phase.upper())
                != {"goto": "engineering_" + phase}):
            raise EngineeringError("engineering_graph_changed")
    for entry, row in zip(entries, checks, strict=True):
        budget = cast(dict[str, object], row["node_budget"])["budget_seconds"]
        encoded = entry["baseline_node"][3:]
        for phase, check_node, accept_node in (
            ("baseline", entry["baseline_node"], entry["baseline_accept_node"]),
            ("candidate", entry["candidate_node"], entry["candidate_accept_node"]),
        ):
            check = nodes.get(check_node)
            guard = nodes.get(accept_node)
            if check is None or guard is None:
                raise EngineeringError("engineering_graph_changed")
            invoke = cast(dict[str, object], check["invoke"])
            routes = cast(dict[str, object], check["on"])
            # The check's only successor is its guard; a changed budget, argv or
            # alternate edge would bypass the selected stopped route.
            if (
                invoke.get("timeout_seconds") != budget
                or cast(dict[str, object], invoke["config"]).get("args")
                != ["check", phase, encoded]
                or check["on_error"] != {"goto": accept_node}
                or check["on_unknown"] != {"goto": accept_node}
                or any(route != {"goto": accept_node} for outcome, route in
                       routes.items() if outcome != "REFUSED")
                or cast(dict[str, object], cast(dict[str, object], guard["invoke"])[
                    "config"
                ]).get("args") != ["accept", phase, encoded]
            ):
                raise EngineeringError("engineering_graph_changed")


# ------------------------------------------------------------ copy capture


def capture(project: Path, profile_file: Path, history_home: Path, *,
            core_settings: Path | None = None) -> dict[str, object]:
    """`engineering-init`: capture one profile/snapshot into a new quiescent copy.

    Refuses any copy with session/work state. A repeated call with identical
    inputs replays; differing captured bytes refuse instead of editing guidance.
    """
    project = project.expanduser().absolute()
    history_home = history_home.expanduser().absolute()
    if not project.is_dir() or project.is_symlink():
        raise EngineeringError("invalid_project_root")
    for workspace in ("foreground", "learning"):
        root = project / workspace
        if not root.is_dir() or root.is_symlink():
            raise EngineeringError("workspace_layout")
        if any(os.path.lexists(root / name) for name in _WORK_NAMES):
            raise EngineeringError("engineering_init_requires_quiescent_new_copy")
    if os.path.lexists(project / "subworkflow-inputs.json") or os.path.lexists(
        project / "lad-target.json"
    ):
        raise EngineeringError("engineering_init_requires_quiescent_new_copy")
    if _overlaps(history_home, project):
        raise EngineeringError("history_home_inside_copy")
    profile_raw = read_regular(profile_file)
    profile = load_profile(profile_raw)
    policy_pin = cast(dict[str, object], profile["path_policy_ref"])
    policy_raw = read_regular(profile_file.parent / cast(str, policy_pin["path"]))
    if sha256(policy_raw) != policy_pin["sha256"]:
        raise EngineeringError("path_policy_ref_mismatch")
    try:
        policy = cc.validate_path_policy(cc.parse(policy_raw))
        repository = cast(dict[str, object], profile["repository"])
        cc.require_source(policy, [
            p for p in [
                *cast(list[str], repository["source_scope"]),
                *cast(list[str], cast(dict[str, object], profile["artifact_policy"])[
                    "deliverable_paths"
                ]),
            ] if p != "."
        ])
    except cc.CheckContractError as exc:
        raise EngineeringError(exc.code) from exc
    protected = {".env", ".env.local", "credentials.json", "secrets.json"}
    if any(protected & set(PurePosixPath(prefix).parts)
           for prefix, _ in policy.literals):
        # LAD's protected input names stay observed source authority.
        raise EngineeringError("path_policy_masks_protected_name")
    policy_copy = "context/" + cast(str, policy_pin["path"])
    if policy_copy == PROFILE_PATH or within(policy_copy, CAPTURE_DIR):
        raise EngineeringError("engineering_control_conflict")
    pinned = cast(dict[str, object], profile["requirements_snapshot"])
    if not history_home.is_dir():
        raise EngineeringError("invalid_history_home")
    snapshot, snapshot_raw = read_snapshot(
        history_home, cast(str, pinned["path"]), cast(str, pinned["sha256"])
    )
    if (
        snapshot["history_id"] != pinned["history_id"]
        or snapshot["sequence"] != pinned["sequence"]
        or snapshot["repository_id"] != repository["target_id"]
    ):
        raise EngineeringError("requirements_pin_mismatch")
    required_guidelines(profile, snapshot)
    history_state = initialize_history(history_home, pinned)
    settings = _core_settings(core_settings)
    workflow = project / "workflow.json"
    current_raw = read_regular(workflow)
    capture_file = project / CAPTURE_PATH
    stock_raw = current_raw
    if os.path.lexists(capture_file):
        previous = parse(read_regular(capture_file))
        if not isinstance(previous, dict) or previous.get("format") != CAPTURE:
            raise EngineeringError("engineering_capture_conflict")
        source_digest = previous.get("source_graph_sha256")
        expanded_digest = previous.get("expanded_graph_sha256")
        if sha256(current_raw) == expanded_digest:
            stock_raw = None  # Already expanded; recompute from retained stock.
        elif sha256(current_raw) != source_digest:
            raise EngineeringError("engineering_capture_conflict")
    if stock_raw is None:
        stock_raw = read_regular(project / CAPTURE_DIR / "source-workflow.json")
    graph = cast(dict[str, object], parse(stock_raw))
    profile_ref = make_ref(PROFILE_PATH, profile_raw)
    _validate_review_bindings(profile, graph)
    resolved, budget_rows = resolve_checks(
        profile, profile_ref, graph, policy,
        runtime_timeout=settings["runtime_timeout_seconds"],
        ceiling=settings["timeout_ceiling_seconds"],
    )
    mapping = node_map(profile_ref, profile)
    entries = cast(list[dict[str, str]], mapping["entries"])
    budgets = {row["definition"]["id"]: row["node_budget"] for row in cast(
        list[dict[str, dict[str, object]]], resolved
    )}
    control = [PROFILE_PATH, policy_copy, SNAPSHOT_COPY, NODE_MAP_PATH, RESOLVED_PATH,
               CAPTURE_PATH, CAPTURE_DIR + "/source-workflow.json"]
    expanded = expand_graph(cast(dict[str, object], parse(stock_raw)), entries,
                            budgets, control)
    expanded_raw = encode(expanded) + b"\n"
    resolved_raw = record_bytes(resolved_record(profile_ref, profile, resolved,
                                                expanded_raw))
    map_raw = record_bytes(mapping)
    capture_record = {
        "format": CAPTURE,
        "profile_ref": profile_ref,
        "path_policy_ref": make_ref(policy_copy, policy_raw),
        "requirements_ref": make_ref(SNAPSHOT_COPY, snapshot_raw),
        "node_map_ref": make_ref(NODE_MAP_PATH, map_raw),
        "resolved_checks_ref": make_ref(RESOLVED_PATH, resolved_raw),
        "history": {"home": str(history_home), "history_id": pinned["history_id"],
                    "path": pinned["path"], "sha256": pinned["sha256"],
                    "sequence": pinned["sequence"]},
        "core_settings": settings,
        "source_graph_sha256": sha256(stock_raw),
        "expanded_graph_sha256": sha256(expanded_raw),
    }
    for relative, raw in (
        (CAPTURE_DIR + "/source-workflow.json", stock_raw),
        (PROFILE_PATH, profile_raw),
        (policy_copy, policy_raw),
        (SNAPSHOT_COPY, snapshot_raw),
        (NODE_MAP_PATH, map_raw),
        (RESOLVED_PATH, resolved_raw),
        (CAPTURE_PATH, record_bytes(capture_record)),
    ):
        write_immutable(project, relative, raw)
    if current_raw != expanded_raw:
        replace_file(project, "workflow.json", expanded_raw)
    return {
        "status": "captured",
        "profile_sha256": profile_ref["sha256"],
        "requirements": {"history_id": pinned["history_id"],
                         "sequence": pinned["sequence"], "sha256": pinned["sha256"],
                         "history_state": history_state},
        "checks": budget_rows,
        "operator_ceiling_seconds": settings["timeout_ceiling_seconds"],
        "operator_ceiling_known": settings["ref"] is not None,
        "warnings": [
            {"check_id": row["definition"]["id"], "code": row["timeout_warning"]}
            for row in cast(list[dict[str, dict[str, object]]], resolved)
            if row["timeout_warning"] is not None
        ],
        "node_map": NODE_MAP_PATH,
        "graph_sha256": sha256(expanded_raw),
    }


def _overlaps(first: Path, second: Path) -> bool:
    a, b = first.resolve(), second.resolve()
    return a == b or a in b.parents or b in a.parents


def _core_settings(path: Path | None) -> dict[str, object]:
    """Optional `instance create --settings` JSON, read for Q3 reporting only."""
    if path is None:
        return {"ref": None, "runtime_timeout_seconds": None,
                "timeout_ceiling_seconds": None}
    raw = read_regular(path.expanduser().absolute())
    obj = parse(raw)
    if not isinstance(obj, dict):
        raise EngineeringError("invalid_core_settings")
    runtime = obj.get("runtime_defaults", {})
    policy = obj.get("operator_policy", {})
    if not isinstance(runtime, dict) or not isinstance(policy, dict):
        raise EngineeringError("invalid_core_settings")
    values: dict[str, object] = {}
    for key, layer, name in (("runtime_timeout_seconds", runtime, "timeout_seconds"),
                             ("timeout_ceiling_seconds", policy,
                              "timeout_ceiling_seconds")):
        value = layer.get(name)
        if value is not None:
            try:
                cc._seconds(value, "invalid_core_settings")
            except cc.CheckContractError as exc:
                raise EngineeringError("invalid_core_settings") from exc
        values[key] = value
    return {"ref": {"path": str(path), "sha256": sha256(raw)}, **values}


def _validate_review_bindings(profile: Mapping[str, object],
                              graph: Mapping[str, object]) -> None:
    """Declared bindings must be the roles' actual graph bindings."""
    nodes = cast(dict[str, dict[str, object]], cast(dict[str, dict[str, object]], graph[
        "planes"
    ])["foreground"]["nodes"])
    declared = cast(dict[str, dict[str, object]] | None, profile["review_bindings"])
    bindings = cast(dict[str, object], graph["bindings"])
    families: dict[str, str] = {}
    for role, entry in (declared or {}).items():
        actual = cast(dict[str, object], nodes[role]["invoke"]).get("binding")
        if entry["binding_ref"] not in bindings or actual != entry["binding_ref"]:
            raise EngineeringError("review_binding_mismatch")
        families[role] = cast(str, entry["configured_family"])
    if profile["family_policy"] == "different_for_implementation_review":
        if not {"builder", "checker", "doublechecker"} <= set(families):
            raise EngineeringError("configured_family_unknown")
        if families["builder"] in {families["checker"], families["doublechecker"]}:
            raise EngineeringError("configured_family_not_different")
        fixer = cast(dict[str, object], nodes["fixer"]["invoke"]).get("binding")
        if fixer != cast(dict[str, dict[str, object]], declared)["builder"][
            "binding_ref"
        ]:
            raise EngineeringError("configured_family_unknown")


def binding_facts(graph: Mapping[str, object], profile: Mapping[str, object],
                  *, session_id: str, occurrence_id: str, role: str,
                  graph_revision: str) -> dict[str, object]:
    """Requested/resolved configuration facts; reported facts stay null here."""
    nodes = cast(dict[str, dict[str, object]], cast(dict[str, dict[str, object]], graph[
        "planes"
    ])["learning" if role == "learning" else "foreground"]["nodes"])
    invoke = cast(dict[str, object], nodes[role]["invoke"])
    binding_id = cast(str, invoke.get("binding"))
    binding = cast(dict[str, object], cast(dict[str, object], graph["bindings"])[
        binding_id
    ])
    node_config = cast(dict[str, object], invoke.get("config", {}))
    binding_config = cast(dict[str, object], binding.get("config", {}))
    requested_model = node_config.get("model", binding_config.get("model"))
    requested_effort = node_config.get("effort", binding_config.get("effort"))
    declared = cast(dict[str, dict[str, object]] | None, profile["review_bindings"])
    entry = (declared or {}).get(role)
    if entry is not None and entry["binding_ref"] != binding_id:
        # A declared family belongs only to its pinned binding; never to a
        # different actual binding after a graph edit.
        raise EngineeringError("review_binding_mismatch")
    family = None if entry is None else entry["configured_family"]
    return with_integrity({
        "format": BINDING_FACTS,
        "session_id": session_id,
        "occurrence_id": occurrence_id,
        "role": role,
        "graph_revision": graph_revision,
        "binding_id": binding_id,
        "adapter_contract": binding.get("contract"),
        "requested_model": requested_model,
        "requested_effort": requested_effort,
        # Captured node/binding precedence only; runtime adapter_settings are not
        # visible before dispatch, and provider effective effort is not attested.
        "resolved_model": requested_model,
        "resolved_effort": requested_effort,
        "configured_family": family,
        "reported_model": None,
        "reported_effort": None,
        "reported_family": None,
        "native_evidence_ref": None,
    })


def validate_binding_facts(value: object) -> dict[str, object]:
    code = "invalid_binding_facts"
    obj = _fields(value, (
        "format", "session_id", "occurrence_id", "role", "graph_revision", "binding_id",
        "adapter_contract", "requested_model", "requested_effort", "resolved_model",
        "resolved_effort", "configured_family", "reported_model", "reported_effort",
        "reported_family", "native_evidence_ref", "integrity_sha256",
    ), code)
    if obj["format"] != BINDING_FACTS:
        raise EngineeringError(code)
    for key in ("session_id", "occurrence_id", "role", "graph_revision", "binding_id",
                "adapter_contract"):
        _text(obj[key], code)
    for key in ("requested_model", "requested_effort", "resolved_model",
                "resolved_effort", "configured_family", "reported_model",
                "reported_effort", "reported_family"):
        _optional_text(obj[key], code)
    if obj["native_evidence_ref"] is not None:
        ref(obj["native_evidence_ref"], code)
    check_integrity(obj, code)
    return obj


# ----------------------------------------------------------- source records


def source_record(target_id: str, snapshot: Mapping[str, str],
                  context: Mapping[str, object], paths: list[str]) -> dict[str, object]:
    """Full selected source authority: scoped files, outside identities, Git.

    `outside` identities are taken over the same explicit scope as `files`, and
    `git` keeps the exact base/index plus policy-filtered worktree/untracked
    digests, so any undeclared write or drift is visible.
    """
    git = cast(dict[str, str] | None, context.get("git"))
    return {
        "format": SOURCE,
        "target_id": target_id,
        "git_base": None if git is None else git["base"],
        "index_sha256": None if git is None else git["index"],
        "scoped_files_sha256": sha256(encode(dict(snapshot))),
        "outside_sha256": sha256(encode(context.get("outside", {}))),
        "git": None if git is None else dict(git),
        "paths": sorted(paths),
        "files": dict(sorted(snapshot.items())),
    }


def authority_drift(reserved: Mapping[str, object], current: Mapping[str, object]
                    ) -> dict[str, object]:
    """Differences between two source records; empty `kinds` means identical."""
    before = cast(dict[str, str], reserved["files"])
    after = cast(dict[str, str], current["files"])
    changed = sorted(name for name in set(before) | set(after)
                     if before.get(name) != after.get(name))
    kinds = []
    if reserved["paths"] != current["paths"]:
        kinds.append("scope")
    if changed:
        kinds.append("files")
    if reserved["outside_sha256"] != current["outside_sha256"]:
        kinds.append("outside")
    if reserved["git"] != current["git"]:
        kinds.append("git")
    if reserved["target_id"] != current["target_id"]:
        kinds.append("target")
    return {"kinds": kinds, "files": changed[:64], "file_count": len(changed)}


def source_identity(record: Mapping[str, object]) -> dict[str, object]:
    """The five-field `source_ref` used by requirements snapshots."""
    return {key: record[key] for key in (
        "target_id", "git_base", "index_sha256", "scoped_files_sha256",
        "outside_sha256",
    )}


# ---------------------------------------------------------- product layers


def observe_products(target: Path, policy: cc.PathPolicy
                     ) -> dict[str, dict[str, object]]:
    """Exact product-surface identities; links/specials hold, `.git` is pruned."""
    entries: dict[str, dict[str, object]] = {}
    if not target.exists():
        return entries
    for folder, dirs, files in os.walk(target, followlinks=False):
        root = Path(folder)
        keep = []
        for name in sorted(dirs):
            path = root / name
            relative = path.relative_to(target).as_posix()
            if name == ".git":
                continue
            surface = _surface(policy, relative)
            info = path.lstat()
            if surface == "environment_paths":
                continue
            if stat.S_ISLNK(info.st_mode):
                if surface == "product_paths":
                    raise EngineeringError("product_link_or_special")
                continue
            if surface == "product_paths":
                entries[relative] = {"kind": "directory",
                                     "mode": stat.S_IMODE(info.st_mode)}
            keep.append(name)
        dirs[:] = keep
        for name in sorted(files):
            path = root / name
            relative = path.relative_to(target).as_posix()
            if _surface(policy, relative) != "product_paths":
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise EngineeringError("product_link_or_special")
            entries[relative] = {
                "kind": "file", "mode": stat.S_IMODE(info.st_mode),
                "size": info.st_size, "sha256": file_sha256(path),
            }
    return dict(sorted(entries.items()))


def file_sha256(path: Path) -> str:
    """Streamed digest of one regular file opened without following links."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    checksum = hashlib.sha256()
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise EngineeringError("product_link_or_special")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _surface(policy: cc.PathPolicy, relative: str) -> str:
    try:
        return cc.classify(policy, relative)
    except cc.CheckContractError as exc:
        raise EngineeringError(exc.code) from exc


def observation_record(target_id: str, entries: Mapping[str, object]
                       ) -> dict[str, object]:
    return {"format": OBSERVATION, "target_id": target_id, "entries": dict(entries)}


def observe_environment(target: Path, policy: cc.PathPolicy,
                        policy_ref: Mapping[str, object]) -> dict[str, object]:
    """Opaque presence/lstat metadata only; contents never hashed or exported."""
    entries = []
    literal = [prefix for prefix, surface in policy.literals
               if surface == "environment_paths"]
    for prefix in literal:
        path = target / prefix
        info = path.lstat() if os.path.lexists(path) else None
        link = os.readlink(path) if info is not None and stat.S_ISLNK(
            info.st_mode
        ) else None
        entries.append(_environment_entry(prefix, info, link))
    tokens = {name for name, surface in policy.tokens if surface == "environment_paths"}
    if tokens and target.exists():
        for folder, dirs, _ in os.walk(target, followlinks=False):
            root = Path(folder)
            keep = []
            for name in sorted(dirs):
                path = root / name
                relative = path.relative_to(target).as_posix()
                if name == ".git" or path.is_symlink():
                    continue
                if name in tokens and all(
                    _surface(policy, "/".join(PurePosixPath(relative).parts[:index]))
                    == "source"
                    for index in range(1, len(PurePosixPath(relative).parts))
                ):
                    entries.append(_environment_entry(relative, path.lstat(), None))
                    continue
                if _surface(policy, relative) == "source":
                    keep.append(name)
            dirs[:] = keep
    record = {
        "format": cc.ENVIRONMENT,
        "path_policy_ref": dict(policy_ref),
        "target_root": str(target),
        "observed_at": utc_now(),
        "entries": entries,
    }
    try:
        return cc.validate_environment(record, policy=policy, policy_ref=policy_ref)
    except cc.CheckContractError as exc:
        raise EngineeringError(exc.code) from exc


def _environment_entry(prefix: str, info: os.stat_result | None,
                       link: str | None) -> dict[str, object]:
    try:
        return cc.environment_entry(prefix, info, link_target=link)
    except cc.CheckContractError as exc:
        raise EngineeringError(exc.code) from exc


def changed_products(before: Mapping[str, object], after: Mapping[str, object]
                     ) -> list[str]:
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def products_record(*, input_id: str, command_id: str,
                    policy_ref: Mapping[str, object], before_ref: Mapping[str, object],
                    after_ref: Mapping[str, object] | None, owned: list[str],
                    completion_ref: Mapping[str, object] | None,
                    previous_ref: Mapping[str, object] | None, status: str,
                    policy: cc.PathPolicy | None = None,
                    execution: Mapping[str, object] | None = None) -> dict[str, object]:
    record = {
        "format": cc.PRODUCTS,
        "input_id": input_id,
        "materialization_id": None,
        "command_id": command_id,
        "path_policy_ref": dict(policy_ref),
        "before_ref": dict(before_ref),
        "after_ref": None if after_ref is None else dict(after_ref),
        "owned_paths": owned,
        "completion_ref": None if completion_ref is None else dict(completion_ref),
        "previous_ref": None if previous_ref is None else dict(previous_ref),
        "status": status,
    }
    try:
        return cc.validate_products(record, policy=policy, policy_ref=policy_ref,
                                    execution=execution)
    except cc.CheckContractError as exc:
        raise EngineeringError(exc.code) from exc


def transition(previous_raw: bytes, previous_ref: Mapping[str, object],
               current: Mapping[str, object]) -> None:
    try:
        cc.validate_product_transition(previous_raw, previous_ref, current)
    except cc.CheckContractError as exc:
        raise EngineeringError(exc.code) from exc


def acceptance_record(*, subject_ref: Mapping[str, object], command_id: str,
                      receipt_ref: Mapping[str, object], from_node: str, to_node: str,
                      reservation_ref: Mapping[str, object],
                      guard_ref: Mapping[str, object]) -> dict[str, object]:
    record = {
        "format": cc.COMPLETION_ACCEPTANCE,
        "subject_ref": dict(subject_ref),
        "command_id": command_id,
        "process_receipt_ref": dict(receipt_ref),
        "acceptance": {
            "kind": "selected_stopped_route",
            "from_node": from_node,
            "to_node": to_node,
            "reservation_ref": dict(reservation_ref),
            "guard_ref": dict(guard_ref),
        },
    }
    try:
        return cc.validate_completion_acceptance(record, "selected_stopped_route")
    except cc.CheckContractError as exc:
        raise EngineeringError(exc.code) from exc


# --------------------------------------------------------- check execution


def check_input(*, session_id: str, task_id: str, phase: str, check_id: str,
                profile_ref: Mapping[str, object], definition_ref: Mapping[str, object],
                source: Mapping[str, object], policy_ref: Mapping[str, object],
                previous: Mapping[str, object] | None) -> dict[str, object]:
    body = {
        "format": CHECK_INPUT,
        "session_id": session_id,
        "task_id": task_id,
        "phase": phase,
        "check_id": check_id,
        "profile_ref": dict(profile_ref),
        "definition_ref": dict(definition_ref),
        "source_ref": dict(source),
        "path_policy_ref": dict(policy_ref),
        "previous_check_ref": None if previous is None else dict(previous),
    }
    return {"input_id": sha256(encode(body)), **body}


def validate_check_input(value: object) -> dict[str, object]:
    code = "invalid_check_input"
    obj = _fields(value, ("format", "input_id", "session_id", "task_id", "phase",
                          "check_id", "profile_ref", "definition_ref", "source_ref",
                          "path_policy_ref", "previous_check_ref"), code)
    if obj["format"] != CHECK_INPUT or obj["phase"] not in ("baseline", "candidate"):
        raise EngineeringError(code)
    for key in ("session_id", "task_id", "check_id"):
        _text(obj[key], code)
    for key in ("profile_ref", "definition_ref", "source_ref", "path_policy_ref"):
        ref(obj[key], code)
    if obj["previous_check_ref"] is not None:
        ref(obj["previous_check_ref"], code)
    body = {k: v for k, v in obj.items() if k != "input_id"}
    if obj["input_id"] != sha256(encode(body)):
        raise EngineeringError("check_input_identity")
    return obj


def selected(definition: Mapping[str, object], changed: Sequence[str] | None
             ) -> tuple[bool, str]:
    """Selection only from captured definitions and actual scoped paths."""
    selection = cast(dict[str, object], definition["selection"])
    if selection["when"] == "always":
        return True, "always"
    prefixes = cast(list[str], selection["paths"])
    if changed is None:
        return False, "paths_changed_without_scope"
    hits = sorted(path for path in changed if any(within(path, p) for p in prefixes))
    if hits:
        return True, "paths_changed:" + ",".join(hits[:16]) + (
            f" (+{len(hits) - 16})" if len(hits) > 16 else ""
        )
    return False, "no_selected_path_changed"


def resolved_environment(definition: Mapping[str, object],
                         base: Mapping[str, str]) -> tuple[dict[str, str], str]:
    """Permitted inherited names present in this invocation plus explicit values."""
    environment = cast(dict[str, object], definition["environment"])
    names = cast(list[str], environment["inherit_names"])
    values = {name: base[name] for name in names if name in base}
    values.update(cast(dict[str, str], environment["set"]))
    digest = sha256(encode({"names": sorted(names), "values": values}))
    return values, digest


def resolve_executable(argv0: str, environment: Mapping[str, str], cwd: Path
                       ) -> str | None:
    if "/" in argv0:
        path = (cwd / argv0) if not argv0.startswith("/") else Path(argv0)
        return str(path) if os.access(path, os.X_OK) and path.is_file() else None
    search = environment.get("PATH")
    if not search:
        return None
    return shutil.which(argv0, path=search)


def run_check(argv: list[str], *, cwd: Path, environment: Mapping[str, str],
              deadline: float, cancelled: Callable[[], bool],
              on_started: Callable[[int], None]) -> dict[str, object]:
    """Run one captured argv through Core's owned-process group helper."""
    try:
        from .router.process import ProcessRequest, run_owned_process
    except ImportError as exc:  # CoreContractError is an ImportError.
        raise EngineeringError("check_runtime_requires_core") from exc
    request = ProcessRequest(
        tuple(argv), cwd, dict(environment), None, deadline,
        stdin_limit=1,
        stdout_capture_limit=OUTPUT_RETAIN_BYTES,
        stderr_capture_limit=OUTPUT_RETAIN_BYTES,
        stdout_fail_limit=None,
        stderr_fail_limit=None,
    )
    result = run_owned_process(request, poll=cancelled, on_started=on_started)
    return {
        "pid": result.pid,
        "exit_code": result.exit_code,
        "stop_cause": result.stop_cause,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_bytes": result.stdout_bytes,
        "stderr_bytes": result.stderr_bytes,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "group_state": result.group_state,
        "cleanup_confirmed": result.cleanup_confirmed,
    }


def group_absent(pid: int) -> bool:
    """True only when the recorded process group has no member (ESRCH)."""
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def evidence_record(**values: object) -> dict[str, object]:
    record: dict[str, object] = {"format": EVIDENCE, **values}
    record = with_integrity(record)
    validate_evidence(record)
    return record


def validate_evidence(value: object, *,
                      definition: Mapping[str, object] | None = None,
                      execution_sha256: str | None = None,
                      identity: Mapping[str, object] | None = None
                      ) -> dict[str, object]:
    """Strict `millrace-engineering-check-evidence/v1` for every declared field.

    Where evidence is admitted or reloaded the caller also passes the exact
    resolved `definition`, its execution digest and the expected identity
    (session/task/phase/profile/source); any disagreement refuses. Integrity
    authenticates bytes only and never substitutes for these checks.
    `invocation_deadline` is null: Core's absolute invocation deadline is not
    handed to command scripts, so it is never fabricated here.
    """
    code = "invalid_check_evidence"
    obj = _fields(value, (
        "format", "check_id", "definition_sha256", "profile_sha256", "task_id",
        "session_id", "occurrence_id", "phase", "source_ref", "execution_sha256",
        "argv", "cwd", "environment_sha256", "path_policy_ref", "product_layer_refs",
        "invocation_deadline", "timeout_seconds", "tool_version", "started_at",
        "finished_at", "exit_code", "status", "stdout_ref", "stderr_ref",
        "requirement_ids", "selection_reason", "unrun_reason", "integrity_sha256",
    ), code)
    if obj["format"] != EVIDENCE or obj["phase"] not in ("baseline", "candidate"):
        raise EngineeringError(code)
    if obj["status"] not in STATUSES:
        raise EngineeringError(code)
    for key in ("check_id", "task_id", "session_id", "occurrence_id",
                "selection_reason"):
        _text(obj[key], code)
    for key in ("definition_sha256", "profile_sha256", "execution_sha256",
                "environment_sha256"):
        _sha(obj[key], code)
    ref(obj["source_ref"], code)
    ref(obj["path_policy_ref"], code)
    argv = obj["argv"]
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(item, str) or "\0" in item for item in argv)
            or not argv[0].strip()):
        raise EngineeringError(code)
    if obj["cwd"] != ".":
        _relative(obj["cwd"], code)
    try:
        cc._seconds(obj["timeout_seconds"], code)
    except cc.CheckContractError as exc:
        raise EngineeringError(code) from exc
    if obj["invocation_deadline"] is not None:
        raise EngineeringError(code)
    ids = obj["requirement_ids"]
    if not isinstance(ids, list) or len(set(map(repr, ids))) != len(ids) or any(
        not isinstance(item, str)
        or not (_IDENT.fullmatch(item) or _QUALIFIED.fullmatch(item))
        for item in ids
    ):
        raise EngineeringError(code)
    refs = obj["product_layer_refs"]
    if not isinstance(refs, list) or not refs:
        raise EngineeringError(code)
    for item in refs:
        ref(item, code)
    ran = obj["exit_code"] is not None
    if ran:
        exit_code = obj["exit_code"]
        if (type(exit_code) is not int or not -127 <= exit_code <= 255
                or obj["unrun_reason"] is not None
                or obj["status"] not in ("pass", "fail")):
            raise EngineeringError(code)
        try:
            started = datetime.fromisoformat(cc._utc(obj["started_at"], code))
            finished = datetime.fromisoformat(cc._utc(obj["finished_at"], code))
        except cc.CheckContractError as exc:
            raise EngineeringError(code) from exc
        if finished < started:
            raise EngineeringError(code)
        ref(obj["stdout_ref"], code)
        ref(obj["stderr_ref"], code)
    else:
        _text(obj["unrun_reason"], code)
        if obj["status"] not in ("unavailable", "not_selected") or any(
            obj[key] is not None
            for key in ("stdout_ref", "stderr_ref", "started_at", "finished_at")
        ):
            raise EngineeringError(code)
        if (obj["status"] == "not_selected") != str(obj["unrun_reason"]).startswith(
            "not_selected"
        ):
            raise EngineeringError(code)
    _optional_text(obj["tool_version"], code)
    check_integrity(obj, code)
    if definition is not None:
        if (
            obj["check_id"] != definition["id"]
            or obj["definition_sha256"] != sha256(encode(definition))
            or obj["argv"] != definition["argv"]
            or obj["cwd"] != definition["cwd"]
            or obj["timeout_seconds"] != definition["timeout_seconds"]
            or obj["requirement_ids"] != definition["requirement_ids"]
            or obj["path_policy_ref"] != definition["path_policy_ref"]
        ):
            raise EngineeringError("check_evidence_definition_mismatch")
        # The captured pass predicate: pass only at the definition's expected
        # exit (zero or a configured nonzero/signal code). Fail stays valid at any
        # exit, since an unconfirmed stop or a cancellation fails at that exit.
        if obj["status"] == "pass" and obj["exit_code"] != definition[
            "expected_exit_code"
        ]:
            raise EngineeringError("check_evidence_status_mismatch")
    if execution_sha256 is not None and obj["execution_sha256"] != execution_sha256:
        raise EngineeringError("check_evidence_definition_mismatch")
    for key, expected in (identity or {}).items():
        if obj[key] != expected:
            raise EngineeringError("check_evidence_identity_mismatch")
    return obj


def classify_status(definition: Mapping[str, object], exit_code: int | None,
                    stop_cause: str | None, cleanup_confirmed: bool) -> str:
    """pass only for the captured expected exit with a proven-stopped group."""
    if exit_code is None:
        return "unavailable"
    if stop_cause is not None or not cleanup_confirmed:
        return "fail"
    return "pass" if exit_code == definition["expected_exit_code"] else "fail"


def compare_checks(baseline: Mapping[str, object] | None,
                   baseline_ref: Mapping[str, object] | None,
                   candidate: Mapping[str, object],
                   candidate_ref: Mapping[str, object]) -> dict[str, object]:
    """Strict CheckComparison; missing evidence is never a pass."""
    status = candidate["status"]

    def row(classification: str, reason: str) -> dict[str, object]:
        return {
            "check_id": candidate["check_id"],
            "baseline_ref": None if baseline_ref is None else dict(baseline_ref),
            "candidate_ref": dict(candidate_ref),
            "classification": classification,
            "reason": reason,
        }

    if status == "not_selected":
        return row("not_selected", "candidate_not_selected")
    if status == "unavailable":
        return row("unavailable", "candidate_unavailable")
    if baseline is None or baseline["status"] in ("not_selected", "unavailable"):
        return row("not_comparable", "no_baseline_observation")
    if baseline["check_id"] != candidate["check_id"]:
        raise EngineeringError("comparison_check_mismatch")
    for key, reason in (("definition_sha256", "definition_changed"),
                        ("execution_sha256", "execution_changed"),
                        ("environment_sha256", "environment_changed")):
        if baseline[key] != candidate[key]:
            return row("not_comparable", reason)
    return row({
        ("pass", "pass"): "unchanged_pass",
        ("pass", "fail"): "regression",
        ("fail", "fail"): "preexisting_failure",
        ("fail", "pass"): "improved",
    }[(cast(str, baseline["status"]), cast(str, status))], "same_definition")


def validate_comparison(value: object) -> dict[str, object]:
    code = "invalid_check_comparison"
    obj = _fields(value, ("check_id", "baseline_ref", "candidate_ref",
                          "classification", "reason"), code)
    _text(obj["check_id"], code)
    if obj["baseline_ref"] is not None:
        ref(obj["baseline_ref"], code)
    ref(obj["candidate_ref"], code)
    if obj["classification"] not in CLASSIFICATIONS:
        raise EngineeringError(code)
    _text(obj["reason"], code)
    return obj


# --------------------------------------------------- role artifact records


def validate_findings(value: object, *, occurrence_id: str, profile_sha256: str,
                      source_sha256: str, criteria: set[str],
                      guidelines: Mapping[str, str]) -> dict[str, object]:
    """Maintenance findings never invent a criterion or block on aesthetics."""
    code = "invalid_maintenance_findings"
    obj = _fields(value, ("format", "occurrence_id", "profile_sha256",
                          "source_sha256", "findings"), code)
    if (obj["format"] != FINDINGS or obj["occurrence_id"] != occurrence_id
            or obj["profile_sha256"] != profile_sha256
            or obj["source_sha256"] != source_sha256):
        raise EngineeringError("maintenance_findings_identity")
    rows = obj["findings"]
    if not isinstance(rows, list):
        raise EngineeringError(code)
    ids: set[str] = set()
    for item in rows:
        row = _fields(item, ("id", "criterion_id", "guideline_id", "kind", "location",
                             "evidence_refs", "likelihood", "impact",
                             "proposed_action", "blocking"), code)
        _text(row["id"], code)
        if row["id"] in ids or row["kind"] not in FINDING_KINDS or row[
            "likelihood"
        ] not in LIKELIHOODS:
            raise EngineeringError(code)
        ids.add(cast(str, row["id"]))
        _text(row["location"], code)
        _text(row["impact"], code)
        _text(row["proposed_action"], code)
        evidence = row["evidence_refs"]
        if not isinstance(evidence, list):
            raise EngineeringError(code)
        for entry in evidence:
            ref(entry, code)
        if row["criterion_id"] is not None and row["criterion_id"] not in criteria:
            raise EngineeringError("finding_unknown_criterion")
        if row["guideline_id"] is not None and row["guideline_id"] not in guidelines:
            raise EngineeringError("finding_unknown_guideline")
        # Blocking needs a demonstrated violation of an explicit criterion or
        # guideline with evidence; metrics and aesthetics stay advisory.
        if _bool(row["blocking"], code) and (
            row["likelihood"] != "demonstrated"
            or not evidence
            or (row["criterion_id"] is None and (
                row["guideline_id"] is None
                or guidelines[cast(str, row["guideline_id"])] != "required"
            ))
        ):
            raise EngineeringError("speculative_blocking_finding")
    return obj


def validate_retained(value: object, *, occurrence_id: str, requirements_sha256: str,
                      source_sha256: str, retained: set[str]) -> dict[str, object]:
    code = "invalid_retained_verdict"
    obj = _fields(value, ("format", "occurrence_id", "requirements_sha256",
                          "source_sha256", "verdict"), code)
    if (obj["format"] != RETAINED or obj["occurrence_id"] != occurrence_id
            or obj["requirements_sha256"] != requirements_sha256
            or obj["source_sha256"] != source_sha256):
        raise EngineeringError("retained_verdict_identity")
    verdict = obj["verdict"]
    if not isinstance(verdict, dict) or set(verdict) != retained:
        raise EngineeringError("retained_verdict_requirements")
    for entry in verdict.values():
        row = _fields(entry, ("status", "evidence_refs"), code)
        if row["status"] not in ("pass", "gap", "unavailable"):
            raise EngineeringError(code)
        evidence = row["evidence_refs"]
        if not isinstance(evidence, list) or not evidence:
            raise EngineeringError(code)
        for item in evidence:
            ref(item, code)
    return obj


def validate_admission(value: object, *, session_id: str, task_id: str,
                       source_sha256: str, profile_sha256: str) -> dict[str, object]:
    code = "invalid_artifact_admission"
    obj = _fields(value, ("format", "session_id", "task_id", "source_sha256",
                          "profile_sha256", "entries", "integrity_sha256"), code)
    if (obj["format"] != ADMISSION or obj["session_id"] != session_id
            or obj["task_id"] != task_id or obj["source_sha256"] != source_sha256
            or obj["profile_sha256"] != profile_sha256):
        raise EngineeringError("artifact_admission_identity")
    rows = obj["entries"]
    if not isinstance(rows, list):
        raise EngineeringError(code)
    seen: set[tuple[str, str]] = set()
    for item in rows:
        row = _fields(item, ("path", "sha256", "class", "authority_ref", "disposition",
                             "reason"), code)
        _relative(row["path"], code)
        if row["sha256"] is not None:
            _sha(row["sha256"], code)
        if row["class"] not in ARTIFACT_CLASSES or row["disposition"] not in (
            DISPOSITIONS
        ):
            raise EngineeringError(code)
        _text(row["authority_ref"], code)
        _text(row["reason"], code)
        key = (cast(str, row["class"]), cast(str, row["path"]))
        if key in seen:
            raise EngineeringError(code)
        seen.add(key)
    check_integrity(obj, code)
    return obj


def admit_delta(record: Mapping[str, object], delta: Mapping[str, str | None],
                artifact_policy: Mapping[str, object], *, session_id: str,
                workspace: Path) -> tuple[list[str], list[dict[str, object]]]:
    """Compare the actual scoped product delta with one admission manifest.

    Returns (findings, removals). Deliverables must list every changed product
    path with its exact current digest under an admitted deliverable prefix.
    Evidence/scratch/context entries must be outside the product tree under their
    captured roots; only owned scratch under `<scratch_root>/<session>/` with a
    verified digest may be removed. Nothing foreign is deleted.
    """
    findings: list[str] = []
    removals: list[dict[str, object]] = []
    deliverable = cast(list[str], artifact_policy["deliverable_paths"])
    roots = {
        "evidence": cast(str, artifact_policy["evidence_root"]),
        "scratch": cast(str, artifact_policy["scratch_root"]),
        "retained_context": cast(str, artifact_policy["retention_root"]),
    }
    listed: dict[str, dict[str, object]] = {}
    for row in cast(list[dict[str, object]], record["entries"]):
        path = cast(str, row["path"])
        if row["class"] == "deliverable":
            listed[path] = row
            if not any(within(path, prefix) for prefix in deliverable):
                findings.append("deliverable_outside_admitted_paths:" + path)
            if row["disposition"] not in ("admit", "hold"):
                findings.append("deliverable_disposition:" + path)
            if row["disposition"] == "hold":
                findings.append("deliverable_held:" + path)
            continue
        root = roots[cast(str, row["class"])]
        if not within(path, root):
            findings.append("artifact_outside_class_root:" + path)
            continue
        if row["disposition"] == "remove_requested":
            if row["class"] != "scratch" or not within(path, f"{root}/{session_id}"):
                findings.append("removal_not_owned_scratch:" + path)
                continue
            location = workspace / path
            if os.path.lexists(location):
                try:
                    current = sha256(read_regular(contained(workspace, path)))
                except EngineeringError:
                    findings.append("removal_unsafe:" + path)
                    continue
                if current != row["sha256"]:
                    findings.append("removal_changed:" + path)
                    continue
                removals.append(row)
        elif row["disposition"] == "hold":
            findings.append("artifact_held:" + path)
    for path, digest in sorted(delta.items()):
        row = listed.get(path)
        if row is None:
            findings.append("unlisted_product_change:" + path)
        elif row["sha256"] != digest:
            findings.append("admission_digest_mismatch:" + path)
    for path in listed:
        if path not in delta:
            findings.append("admitted_path_not_changed:" + path)
    return findings, removals


def acceptance_projection(**values: object) -> dict[str, object]:
    record = with_integrity({"format": ACCEPTANCE, **values})
    validate_acceptance(record)
    return record


def validate_acceptance(value: object) -> dict[str, object]:
    code = "invalid_engineering_acceptance"
    obj = _fields(value, ("format", "session_id", "task_id", "profile_ref",
                          "requirements_ref", "candidate_source_ref",
                          "original_verdict_ref", "retained_verdict_ref",
                          "artifact_admission_ref", "integrity_sha256"), code)
    if obj["format"] != ACCEPTANCE:
        raise EngineeringError(code)
    _text(obj["session_id"], code)
    _text(obj["task_id"], code)
    for key in ("profile_ref", "requirements_ref", "candidate_source_ref",
                "original_verdict_ref", "retained_verdict_ref",
                "artifact_admission_ref"):
        ref(obj[key], code)
    check_integrity(obj, code)
    return obj


def context_record(**values: object) -> dict[str, object]:
    record = with_integrity({"format": CONTEXT, **values})
    validate_context(record)
    return record


def validate_context(value: object) -> dict[str, object]:
    code = "invalid_engineering_context"
    obj = _fields(value, ("format", "occurrence_id", "profile_ref", "requirements_ref",
                          "baseline_checks", "candidate_checks", "binding_facts_ref",
                          "artifact_policy_ref", "integrity_sha256"), code)
    if obj["format"] != CONTEXT:
        raise EngineeringError(code)
    _text(obj["occurrence_id"], code)
    for key in ("profile_ref", "requirements_ref", "binding_facts_ref",
                "artifact_policy_ref"):
        ref(obj[key], code)
    for key in ("baseline_checks", "candidate_checks"):
        rows = obj[key]
        if not isinstance(rows, list):
            raise EngineeringError(code)
        for item in rows:
            ref(item, code)
    check_integrity(obj, code)
    return obj


# ------------------------------------------------------------------ export


def export_evaluation(project: Path, history_home: Path, snapshot_sha: str,
                      output: Path) -> dict[str, object]:
    """`engineering-export`: copy selected accepted records and an honest report.

    Reads only the explicit indexed snapshot and records it references. It
    performs no inference, test, run, session mining or adoption. Values that
    were not recorded (cost, tokens, elapsed time, reported effort) are listed as
    omissions, never zero.
    """
    project = project.expanduser().absolute()
    history_home = history_home.expanduser().absolute()
    output = output.expanduser().absolute()
    capture_record = cast(dict[str, object],
                          parse(read_regular(project / CAPTURE_PATH)))
    if capture_record.get("format") != CAPTURE:
        raise EngineeringError("engineering_capture_missing")
    profile = load_profile(read_regular(project / PROFILE_PATH))
    export = cast(dict[str, object], profile["evaluation_export"])
    if export["enabled"] is not True:
        raise EngineeringError("engineering_export_not_enabled")
    if str(output) != export["destination_ref"]:
        raise EngineeringError("engineering_export_destination")
    history = cast(dict[str, object], capture_record["history"])
    if str(history_home) != history["home"]:
        raise EngineeringError("history_home_mismatch")
    return _export_snapshot(history_home, cast(str, history["history_id"]),
                            snapshot_sha, output)


def _export_snapshot(history_home: Path, history_id: str, snapshot_sha: str,
                     output: Path) -> dict[str, object]:
    """Export one indexed snapshot's selected accepted records and report.

    Reads only the history; only `export_evaluation`, after validating the
    copy's capture, opt-in and destination, calls it. Each accepted check's
    selected provenance is copied with its exact bytes and its links are
    verified; every other ref inside an exported record that does not resolve
    here is listed as an `unexported_ref` omission with its origin and reason.
    """
    _sha(snapshot_sha, "invalid_snapshot_ref")
    rows = chain(history_home, history_id)
    match = next(((snap, raw) for snap, raw in rows if sha256(raw) == snapshot_sha),
                 None)
    if match is None:
        raise EngineeringError("requirements_snapshot_not_indexed")
    snapshot, raw = match
    if os.path.lexists(output) and (not output.is_dir() or any(output.iterdir())):
        raise EngineeringError("engineering_export_output_not_empty")
    output.mkdir(parents=True, exist_ok=True)
    directory = _history_dir(history_home, history_id)
    record_refs: list[dict[str, object]] = []
    output_refs: list[dict[str, object]] = []
    omissions: list[dict[str, object]] = []
    write_immutable(output, "snapshot.json", raw)
    output_refs.append(make_ref("snapshot.json", raw))
    report_tasks = []
    copied: dict[str, tuple[object, bytes]] = {}
    origins: dict[str, str] = {}
    current: dict[str, object] = {"task_id": None}

    def copy(value: object) -> bytes:
        pinned, body = load_ref(directory, value, "export_record_changed")
        name = "records/" + PurePosixPath(cast(str, pinned["path"])).name
        if name not in copied:
            write_immutable(output, name, body)
            copied[name] = (current["task_id"], body)
            record_refs.append(dict(pinned))
            output_refs.append(make_ref(name, body))
        return body

    for task in cast(list[dict[str, object]], snapshot["accepted_tasks"]):
        current["task_id"] = task["task_id"]
        entry: dict[str, object] = {"task_id": task["task_id"],
                                    "session_id": task["session_id"]}
        for key in ("request_ref", "criteria_ref", "acceptance_ref",
                    "candidate_source_ref"):
            body = copy(task[key])
            if key == "acceptance_ref":
                entry["acceptance"] = parse(body)
        rows = [row for row in cast(list[dict[str, object]], snapshot["requirements"])
                if row["origin_task_id"] == task["task_id"]]
        entry["requirements"] = [row["id"] for row in rows]
        bundle = None
        for row in rows:
            for value in cast(list[object], row["evidence_refs"]):
                body = copy(value)
                parsed = parse(body)
                if isinstance(parsed, dict) and parsed.get("format") == BUNDLE:
                    bundle = parsed
        entry.update(_bundle_report(bundle, copy, task["task_id"], omissions))
        origins.update(cast(dict[str, str], entry["ref_map"] or {}))
        for name in ("task_elapsed_seconds", "cost", "tokens"):
            omissions.append({"task_id": task["task_id"], "value": name,
                              "reason": "not_recorded"})
            entry[name] = None
        report_tasks.append(entry)
    # Honest accounting: a ref inside an exported record either resolves here by
    # content digest or is listed with its origin and reason; none is followed.
    present = {sha256(body) for _, body in copied.values()} | {snapshot_sha}
    owners: list[tuple[str, object, str | None, bytes]] = [(
        "snapshot.json", None, snapshot_relative(history_id, cast(
            int, snapshot["sequence"]), snapshot_sha), raw,
    )] + [(name, task_id, origins.get(name.removeprefix("records/")), body)
          for name, (task_id, body) in copied.items()]
    for owner, task_id, origin, body in owners:
        try:
            value = parse(body)
        except EngineeringError:
            continue  # Opaque bytes such as the original request carry no refs.
        fmt = value.get("format") if isinstance(value, dict) else None
        for trail, pinned in _refs_in(value):
            if pinned["sha256"] in present:
                continue
            field = re.match(r"\.?([^.\[]*)", trail)
            omissions.append({
                "task_id": task_id, "value": "unexported_ref", "owner": owner,
                "owner_origin": origin, "field": trail, "ref": pinned,
                "reason": _UNEXPORTED.get(
                    (fmt, field.group(1) if field else ""), "not_selected"),
            })
    report = {
        "format": "millrace-engineering-export-report/v1",
        "snapshot_sha256": snapshot_sha,
        "tasks": report_tasks,
        "comparability": "Tasks differ in request, source and check conditions; "
        "no causal ranking or improvement claim is made.",
        "ref_resolution": "Exported refs resolve here by content digest as "
        "records/<sha256>; bundle `origins` maps each digest to its original "
        "copy-relative path. Each accepted check's evidence, tagged acceptance, "
        "raw process receipt, reservation, guard, definition, path policy, source "
        "and product-layer records are included. Refs listed as `unexported_ref` "
        "omissions (raw check output, role prose, graph assets, native evidence, "
        "earlier snapshots) are not exported; the export is not self-contained "
        "beyond these selected records. An omission's owner_origin is relative to "
        "the history home for snapshot.json and to its task copy otherwise; the "
        "omitted ref keeps the record-local path exactly as recorded.",
    }
    report_raw = record_bytes(report)
    write_immutable(output, "report.json", report_raw)
    output_refs.append(make_ref("report.json", report_raw))
    manifest = with_integrity({
        "format": EXPORT,
        "snapshot_ref": make_ref(snapshot_relative(
            cast(str, snapshot["history_id"]), cast(int, snapshot["sequence"]),
            snapshot_sha,
        ), raw),
        "record_refs": record_refs,
        "output_refs": output_refs,
        "omissions": omissions,
    })
    write_immutable(output, "manifest.json", record_bytes(manifest))
    return manifest


def _bundle_report(bundle: dict[str, object] | None,
                   copy: Callable[[object], bytes], task_id: object,
                   omissions: list[dict[str, object]]) -> dict[str, object]:
    """Report hooks from recorded facts only; unrecorded values stay null."""
    if bundle is None:
        omissions.append({"task_id": task_id, "value": "evidence_bundle",
                          "reason": "not_published_with_this_record"})
        return {"bindings": None, "checks": None, "comparisons": None,
                "source_conditions": None, "ref_map": None}
    check_integrity(bundle, "export_bundle_integrity")
    provenance = bundle.get("provenance_refs")
    if not isinstance(provenance, list):
        raise EngineeringError("export_bundle_invalid")
    for value in provenance:
        copy(value)

    def body_of(digest: str) -> bytes:
        return copy({"path": "records/" + digest, "sha256": digest})

    profile_sha = cast(dict[str, object], cast(dict[str, object], bundle[
        "projected_refs"
    ])["profile_ref"])["sha256"]
    resolved = parse(copy(bundle["resolved_checks_ref"]))
    rows = cast(dict[str, list[dict[str, object]]], resolved)["checks"]
    definitions = {cast(dict[str, object], row["definition"])["id"]: row
                   for row in rows}
    bindings = []
    for fact in cast(list[dict[str, object]], bundle["binding_facts"]):
        facts = cast(dict[str, object], parse(copy(fact["ref"])))
        bindings.append({key: facts[key] for key in (
            "occurrence_id", "role", "binding_id", "adapter_contract",
            "requested_model", "requested_effort", "resolved_model",
            "resolved_effort", "configured_family", "reported_model",
            "reported_effort", "reported_family",
        )})
        for key in ("reported_model", "reported_effort", "reported_family"):
            if facts[key] is None:
                omissions.append({"task_id": task_id, "value": key,
                                  "occurrence_id": facts["occurrence_id"],
                                  "reason": "not_reported"})
    checks: dict[str, dict[str, object]] = {}
    comparisons = []
    sources = {}
    for phase in cast(list[dict[str, object]], bundle["phases"]):
        kind = cast(str, phase["phase"])
        copy(phase["ref"])
        source = cast(dict[str, object], parse(copy(phase["source_ref"])))
        sources[kind] = {key: source[key] for key in (
            "target_id", "git_base", "index_sha256", "scoped_files_sha256",
            "outside_sha256", "paths",
        )}
        for row in cast(list[dict[str, object]], phase["checks"]):
            definition = cast(dict[str, object], definitions[cast(str, row[
                "check_id"
            ])])
            # Reload with the exact definition and verify the selected links.
            evidence, _ = _check_links(body_of, row, definition, {
                "session_id": bundle["session_id"], "task_id": bundle["task_id"],
                "phase": kind, "check_id": row["check_id"],
                "profile_sha256": profile_sha,
            })
            body = cast(dict[str, object], definition["definition"])
            item = checks.setdefault(cast(str, row["check_id"]), {
                "check_id": row["check_id"], "purpose": body["purpose"],
                "required": body["required"],
                "timeout_seconds": body["timeout_seconds"],
                "timeout_source": definition["timeout_source"],
                "execution_sha256": definition["execution_sha256"],
            })
            elapsed = None
            if evidence["started_at"] is not None:
                elapsed = (datetime.fromisoformat(cast(str, evidence["finished_at"]))
                           - datetime.fromisoformat(cast(str, evidence["started_at"]))
                           ).total_seconds()
            item[kind] = {
                "status": evidence["status"], "exit_code": evidence["exit_code"],
                "elapsed_seconds": elapsed,
                "environment_sha256": evidence["environment_sha256"],
                "selection_reason": evidence["selection_reason"],
                "unrun_reason": evidence["unrun_reason"],
                "links": {key: row[key] for key in (
                    "evidence_ref", "acceptance_ref", "process_receipt_ref",
                    "reservation_ref", "guard_ref",
                )},
            }
        if kind == "candidate":
            comparisons = [
                {key: row[key] for key in ("check_id", "classification", "reason")}
                for row in cast(list[dict[str, object]], phase["comparisons"])
            ]
    for value in cast(dict[str, object], bundle["projected_refs"]).values():
        copy(value)
    return {"bindings": bindings, "checks": list(checks.values()),
            "comparisons": comparisons, "source_conditions": sources,
            "ref_map": bundle["origins"]}


def monotonic_deadline(budget_seconds: float) -> float:
    """Absolute wall-clock bound from this invocation's start and captured budget."""
    return time.time() + budget_seconds


# ------------------------------------------------------------ publication


def _terminal_view(view: Mapping[str, object], instance_id: str, run_id: str) -> None:
    """Public Core facts: the exact run succeeded, is quiescent, writers stopped."""
    invocations = cast(list[dict[str, object]], view.get("invocations", []))
    completions = cast(list[dict[str, object]], view.get("completions", []))
    if (
        view.get("id") != run_id or view.get("instance_id") != instance_id
        or view.get("status") != "succeeded" or view.get("quiescent") is not True
        or view.get("inflight_invocations") != []
        or any(row.get("writer_state") != "stopped" or row.get("status")
               != "completed" for row in invocations)
        or any(row.get("effective_writer") != "stopped" for row in completions)
    ):
        raise EngineeringError("engineering_publish_requires_stopped_success")


# Finite selected provenance of the accepted check evidence: for each record
# format, the declared link fields that publication gathers by content digest
# ("*" iterates a list; None is the resolved CheckDefinition record).
# path_policy_ref resolves through the captured policy pin and profile_ref
# through its own copy-relative path; every other link names an immutable
# engineering record. Nothing else is followed.
_LINKS: dict[object, tuple[tuple[str, ...], ...]] = {
    PHASE: (("source_ref",), ("products_start_ref",), ("products_end_ref",),
            ("environment_start_ref",), ("environment_end_ref",),
            ("restored_layer_refs", "*"), ("checks", "*", "input_ref"),
            ("checks", "*", "receipt_ref"), ("checks", "*", "layers", "*"),
            ("checks", "*", "evidence_ref"), ("checks", "*", "acceptance_ref")),
    cc.COMPLETION_ACCEPTANCE: (("subject_ref",), ("process_receipt_ref",),
                               ("acceptance", "reservation_ref"),
                               ("acceptance", "guard_ref")),
    RECEIPT: (("evidence_ref",), ("layer_ref",)),
    GUARD: (("reservation_ref",), ("process_receipt_ref",), ("observed_layer_ref",)),
    CHECK_INPUT: (("definition_ref",), ("source_ref",), ("previous_check_ref",),
                  ("path_policy_ref",), ("profile_ref",)),
    EVIDENCE: (("source_ref",), ("path_policy_ref",), ("product_layer_refs", "*")),
    cc.PRODUCTS: (("before_ref",), ("after_ref",), ("previous_ref",),
                  ("completion_ref",), ("path_policy_ref",)),
    cc.ENVIRONMENT: (("path_policy_ref",),),
    OBSERVATION: (),
    SOURCE: (),
    None: (("path_policy_ref",),),
    RESOLVED: (("path_policy_ref",), ("checks", "*", "definition", "path_policy_ref"),
               ("profile_ref",)),
    PROFILE: (("path_policy_ref",), ("checks", "*", "path_policy_ref")),
}
# Refs deliberately not exported, by (owner format, field); the export lists
# each as an omission with its origin instead of claiming it resolves.
_UNEXPORTED = {
    (RECEIPT, "stdout_ref"): "raw_check_output",
    (RECEIPT, "stderr_ref"): "raw_check_output",
    (EVIDENCE, "stdout_ref"): "raw_check_output",
    (EVIDENCE, "stderr_ref"): "raw_check_output",
    (RESOLVED, "graph_ref"): "graph_asset",
    ("millrace-lad-result/v2", "artifacts"): "role_artifact",
    (BINDING_FACTS, "native_evidence_ref"): "native_evidence",
    (SNAPSHOT, "previous_ref"): "earlier_history_snapshot",
}


def _linked(value: object, path: tuple[str, ...]) -> Iterator[object]:
    """Non-null values at one declared link path; a missing field refuses."""
    code = "engineering_provenance_link"
    if not path:
        if value is not None:
            yield value
        return
    if path[0] == "*":
        if not isinstance(value, list):
            raise EngineeringError(code)
        for item in value:
            yield from _linked(item, path[1:])
        return
    if not isinstance(value, dict) or path[0] not in value:
        raise EngineeringError(code)
    yield from _linked(value[path[0]], path[1:])


def _refs_in(value: object, trail: str = ""
             ) -> Iterator[tuple[str, dict[str, object]]]:
    """Each Ref-shaped `{path, sha256}` inside one record, with its field path."""
    if isinstance(value, dict):
        if (set(value) == {"path", "sha256"} and isinstance(value["path"], str)
                and isinstance(value["sha256"], str)
                and _SHA.fullmatch(value["sha256"])):
            yield trail, {"path": value["path"], "sha256": value["sha256"]}
            return
        for key, child in value.items():
            yield from _refs_in(child, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _refs_in(child, f"{trail}[{index}]")


def _check_links(body: Callable[[str], bytes], row: Mapping[str, object],
                 resolved_row: Mapping[str, object], identity: Mapping[str, object]
                 ) -> tuple[dict[str, object], dict[str, str]]:
    """One accepted check's selected links, verified by content digest.

    The tagged acceptance names this row's reservation (its subject), raw
    process receipt and guard; the guard (same route) and receipt name that same
    reservation, receipt and evidence; the reservation pins the exact definition,
    and the evidence satisfies it, including the captured pass predicate. Product
    layers and observations are gathered by digest, not cross-checked here.
    Returns the validated evidence and the linked digests.
    """
    code = "engineering_provenance_mismatch"

    def digest_of(value: object) -> str:
        return cast(str, ref(value, code)["sha256"])

    def load(value: object) -> dict[str, object]:
        record = parse(body(digest_of(value)))
        if not isinstance(record, dict):
            raise EngineeringError(code)
        return record

    check_id, phase = identity["check_id"], identity["phase"]
    definition = cast(dict[str, object], resolved_row["definition"])
    evidence = validate_evidence(
        load(row["evidence_ref"]), definition=definition,
        execution_sha256=cast(str, resolved_row["execution_sha256"]),
        identity=identity,
    )
    try:
        accepted = cc.validate_completion_acceptance(load(row["acceptance_ref"]),
                                                     "selected_stopped_route")
    except cc.CheckContractError as exc:
        raise EngineeringError(code) from exc
    tagged = cast(dict[str, object], accepted["acceptance"])
    reservation_sha = digest_of(accepted["subject_ref"])
    receipt_sha = digest_of(accepted["process_receipt_ref"])
    guard = load(tagged["guard_ref"])
    receipt = load(accepted["process_receipt_ref"])
    reservation = validate_check_input(load(accepted["subject_ref"]))
    if (
        accepted["command_id"] != check_id
        or digest_of(tagged["reservation_ref"]) != reservation_sha
        or reservation_sha != digest_of(row["reservation_ref"])
        or receipt_sha != digest_of(row["process_receipt_ref"])
        or ("guard_ref" in row and digest_of(row["guard_ref"])
            != digest_of(tagged["guard_ref"]))
        or guard.get("format") != GUARD or guard.get("check_id") != check_id
        or (guard.get("from_node"), guard.get("to_node"))
        != (tagged["from_node"], tagged["to_node"])
        or guard.get("phase") != phase or guard.get("input_id")
        != reservation["input_id"]
        or digest_of(guard.get("reservation_ref")) != reservation_sha
        or digest_of(guard.get("process_receipt_ref")) != receipt_sha
        or guard.get("products_verified") is not True
        or guard.get("source_verified") is not True
        or receipt.get("format") != RECEIPT or receipt.get("check_id") != check_id
        or receipt.get("phase") != phase
        or receipt.get("input_id") != reservation["input_id"]
        or digest_of(receipt.get("evidence_ref")) != digest_of(row["evidence_ref"])
        or any(reservation[key] != identity[key]
               for key in ("session_id", "task_id", "phase", "check_id"))
        or digest_of(reservation["profile_ref"]) != identity["profile_sha256"]
        or load(reservation["definition_ref"]) != definition
    ):
        raise EngineeringError(code)
    return evidence, {"reservation_ref": reservation_sha,
                      "process_receipt_ref": receipt_sha,
                      "guard_ref": digest_of(tagged["guard_ref"])}


def accepted_task(project: Path) -> dict[str, object]:
    """Exact durable LAD/engineering acceptance of one finished copy.

    Also gathers the selected accepted evidence (phase records, check evidence and
    acceptance, comparisons, resolved definitions, source records and every
    accepted occurrence's binding facts) into one bundle whose refs all resolve
    inside the history directory by content address. The finite `_LINKS`
    provenance of each accepted check (raw receipt, reservation, guard,
    definition, policy, source, product layers and observations) is gathered
    with its exact original bytes; `_check_links` verifies its tagged
    acceptance, receipt, reservation, guard, definition and evidence links.
    """
    foreground = project / "foreground"
    capture_raw = read_regular(project / CAPTURE_PATH)
    capture_record = parse(capture_raw)
    if not isinstance(capture_record, dict) or capture_record.get("format") != CAPTURE:
        raise EngineeringError("engineering_capture_missing")
    session_raw = read_regular(foreground / "lad-state/session.json")
    state = cast(dict[str, object], parse(session_raw))
    check_integrity(state, "session_integrity")
    continuation = cast(dict[str, object] | None, state.get("continuation"))
    if (state.get("format") != "millrace-lad-session/v2"
            or state.get("terminal") != "succeeded" or continuation is None
            or continuation.get("cleanup_required") is not False):
        raise EngineeringError("engineering_publish_requires_task_acceptance")
    sidecar = cast(dict[str, object], parse(read_regular(
        foreground / "lad-state/engineering.json"
    )))
    if sidecar.get("session_id") != state["session_id"] or sidecar.get(
        "capture_sha256"
    ) != sha256(capture_raw):
        raise EngineeringError("engineering_capture_changed")
    estate = cast(dict[str, object], parse(read_regular(
        foreground / "lad-state/engineering/state.json"
    )))
    check_integrity(estate, "engineering_state_integrity")
    acceptance = cast(dict[str, object] | None, estate.get("acceptance"))
    if acceptance is None:
        raise EngineeringError("engineering_acceptance_missing")
    projection_ref, projection_raw = load_ref(
        foreground, acceptance["ref"], "engineering_acceptance_changed"
    )
    projection = validate_acceptance(parse(projection_raw))
    if (projection["session_id"], projection["task_id"]) != (
        state["session_id"], sidecar["task_id"]
    ):
        raise EngineeringError("engineering_acceptance_identity")
    request = read_regular(project / "request.md")
    intake = cast(dict[str, object], state["intake"])
    if sha256(request) != intake["sha256"]:
        raise EngineeringError("original_request_changed")
    records: dict[str, bytes] = {}
    origins: dict[str, str] = {}

    def take(root: Path, value: object, prefix: str = "") -> dict[str, object]:
        pinned, raw = load_ref(root, value, "engineering_acceptance_changed")
        digest = sha256(raw)
        records[digest] = raw
        origins.setdefault(digest, prefix + cast(str, pinned["path"]))
        return {"path": "records/" + digest, "sha256": digest}

    def take_fg(value: object) -> dict[str, object]:
        return take(foreground, value, "foreground/")

    projection_hist = take(foreground, projection_ref, "foreground/")
    projected = {key: take(project, projection[key]) for key in (
        "candidate_source_ref", "original_verdict_ref", "retained_verdict_ref",
        "artifact_admission_ref", "profile_ref", "requirements_ref",
    )}
    _, source_raw = load_ref(project, projection["candidate_source_ref"],
                             "engineering_acceptance_changed")
    source = cast(dict[str, object], parse(source_raw))
    resolved = take(project, make_ref(RESOLVED_PATH,
                                      read_regular(project / RESOLVED_PATH)))
    phases = []
    phase_rows = []
    for kind, phase_value in (("baseline", estate["baseline"]),
                              ("candidate", acceptance.get("candidate_phase"))):
        if phase_value is None:
            raise EngineeringError("engineering_acceptance_phase_missing")
        phase_hist = take_fg(phase_value)
        phase = cast(dict[str, object], parse(records[cast(str, phase_hist["sha256"])]))
        phase_rows.append(cast(list[dict[str, object]], phase["checks"]))
        checks = [
            {"check_id": row["check_id"], "evidence_ref": take_fg(row["evidence_ref"]),
             "acceptance_ref": take_fg(row["acceptance_ref"])}
            for row in cast(list[dict[str, object]], phase["checks"])
        ]
        by_sha = {cast(str, row["acceptance_ref"]["sha256"]): row["acceptance_ref"]
                  for row in checks}
        comparisons = []
        for row in cast(list[dict[str, object]], phase["comparisons"]):
            baseline_ref = cast(dict[str, object] | None, row["baseline_ref"])
            comparisons.append({
                **row,
                "baseline_ref": None if baseline_ref is None
                else {"path": "records/" + cast(str, baseline_ref["sha256"]),
                      "sha256": baseline_ref["sha256"]},
                "candidate_ref": by_sha[cast(str, cast(dict[str, object], row[
                    "candidate_ref"
                ])["sha256"])],
            })
        phases.append({"phase": kind, "ref": phase_hist,
                       "source_ref": take_fg(phase["source_ref"]), "checks": checks,
                       "comparisons": comparisons})
    # The finite selected provenance (`_LINKS`), gathered with exact bytes.
    policy_pin = ref(capture_record["path_policy_ref"])
    profile_sha = cast(str, projected["profile_ref"]["sha256"])
    provenance: dict[str, dict[str, object]] = {}
    queue = [cast(str, row["ref"]["sha256"]) for row in phases] + [
        cast(str, resolved["sha256"]), profile_sha]
    seen: set[str] = set()
    while queue:
        digest = queue.pop()
        if digest in seen:
            continue
        seen.add(digest)
        record = parse(records[digest])
        fmt = record.get("format") if isinstance(record, dict) else ""
        if fmt not in _LINKS:
            raise EngineeringError("engineering_provenance_link")
        for path in _LINKS[fmt]:
            field = path[-2] if path[-1] == "*" else path[-1]
            for value in _linked(record, path):
                if field == "path_policy_ref":
                    if ref(value)["sha256"] != policy_pin["sha256"]:
                        raise EngineeringError("engineering_policy_changed")
                    target = take(project, policy_pin)
                elif field == "profile_ref":
                    target = take(project, value)
                else:
                    pinned = ref(value)
                    if not within(cast(str, pinned["path"]), _ENGINEERING_RECORDS):
                        raise EngineeringError("engineering_provenance_link")
                    target = take_fg(pinned)
                    queue.append(cast(str, target["sha256"]))
                provenance[cast(str, target["sha256"])] = target

    def body(digest: str) -> bytes:
        raw = records.get(digest)
        if raw is None:
            raise EngineeringError("engineering_provenance_missing")
        return raw

    resolved_rows = {
        cast(dict[str, object], row["definition"])["id"]: row
        for row in cast(list[dict[str, object]], cast(dict[str, object], parse(
            body(cast(str, resolved["sha256"]))
        ))["checks"])
    }
    for entry, rows in zip(phases, phase_rows, strict=True):
        for out_row, row in zip(cast(list[dict[str, object]], entry["checks"]), rows,
                                strict=True):
            _, links = _check_links(
                body,
                {"check_id": row["check_id"], "evidence_ref": row["evidence_ref"],
                 "acceptance_ref": row["acceptance_ref"],
                 "reservation_ref": row["input_ref"],
                 "process_receipt_ref": row["receipt_ref"]},
                resolved_rows[cast(str, row["check_id"])],
                {"session_id": state["session_id"], "task_id": sidecar["task_id"],
                 "phase": entry["phase"], "check_id": row["check_id"],
                 "profile_sha256": profile_sha},
            )
            out_row.update({key: {"path": "records/" + digest, "sha256": digest}
                            for key, digest in links.items()})
    facts = []
    for oid in sorted(cast(dict[str, object], state["accepted"])):
        relative = f"lad-state/engineering/occurrences/{oid}.json"
        if not os.path.lexists(foreground / relative):
            raise EngineeringError("engineering_occurrence_sidecar_missing")
        occurrence = cast(dict[str, object], parse(read_regular(foreground / relative)))
        context = cast(dict[str, object], occurrence["context"])
        facts.append({"occurrence_id": oid, "role": occurrence["role"],
                      "ref": take(project, context["binding_facts_ref"])})
    bundle = with_integrity({
        "format": BUNDLE,
        "task_id": sidecar["task_id"],
        "session_id": state["session_id"],
        "acceptance_ref": projection_hist,
        "projected_refs": projected,
        "resolved_checks_ref": resolved,
        "phases": phases,
        "binding_facts": facts,
        "provenance_refs": [provenance[key] for key in sorted(provenance)],
        # Exact original copy-relative location of every content-addressed record.
        "origins": dict(sorted(origins.items())),
    })
    bundle_raw = record_bytes(bundle)
    criteria_raw = record_bytes({"format": "millrace-lad-accepted-criteria/v1",
                                 "criteria": state["criteria"]})
    superseded = cast(dict[str, list[str]], acceptance.get("superseded", {}))
    criteria = [
        {**row, "supersedes": superseded.get(cast(str, row["id"]), [])}
        for row in cast(list[dict[str, object]], state["criteria"])
    ]
    for raw in (request, criteria_raw, bundle_raw):
        records[sha256(raw)] = raw
    return {
        "task_id": sidecar["task_id"],
        "session_id": state["session_id"],
        "request_ref": make_ref("records/" + sha256(request), request),
        "criteria_ref": make_ref("records/" + sha256(criteria_raw), criteria_raw),
        "acceptance_ref": projection_hist,
        "candidate_source_ref": projected["candidate_source_ref"],
        "bundle_ref": make_ref("records/" + sha256(bundle_raw), bundle_raw),
        "criteria": criteria,
        "source_ref": source_identity(source),
        "records": records,
        "projection_ref": projection_ref,
        "capture": capture_record,
    }


_TARGET_FIELDS = (
    "format", "service_id", "task_id", "request_sha256", "target_root",
    "target_identity", "claim_sha256", "parent_instance_id", "parent_run_id",
    "worker_definition_sha256",
)


def service_subject(project: Path) -> dict[str, object] | None:
    """The copy's exact captured `lad_target` service authority, or None.

    Ownership comes only from the immutable subworkflow input manifest and its
    matching `lad-target.json`; optional caller selectors never decide it.
    """
    manifest_path = project / "subworkflow-inputs.json"
    target_path = project / "lad-target.json"
    record: object = None
    if os.path.lexists(manifest_path):
        manifest = parse(read_regular(manifest_path))
        inputs = manifest.get("inputs") if isinstance(manifest, dict) else None
        if not isinstance(inputs, dict):
            raise EngineeringError("engineering_service_subject_invalid")
        record = inputs.get("lad_target")
    if record is None:
        if os.path.lexists(target_path):
            raise EngineeringError("engineering_service_subject_conflict")
        return None
    subject = _fields(record, _TARGET_FIELDS, "engineering_service_subject_invalid")
    if subject["format"] != "millrace-lad-target/v1":
        raise EngineeringError("engineering_service_subject_invalid")
    if os.path.lexists(target_path) and read_regular(target_path) != encode(
        subject
    ) + b"\n":
        raise EngineeringError("engineering_service_subject_conflict")
    return subject


def _service_settled(subject: Mapping[str, object], *, service_home: Path | None,
                     task: str | None, instance_id: str, run_id: str,
                     project: Path) -> None:
    """Require the subject's normal settled terminal CLOSE/ACK under target_lock.

    The service is resolved from the captured target's owner marker; supplied
    selectors must name that same service and task. Publication never
    substitutes for settlement.
    """
    from .lad_service import (
        OWNER,
        Service,
        _owner_check,
        _validate_identity,
        target_lock,
    )
    from .local_intake import IntakeError, fields, strict_json

    try:
        target = Path(cast(str, subject["target_root"]))
        _validate_identity(subject["target_identity"], target)
        marker = fields(
            strict_json(read_regular(target / OWNER)),
            {"format", "workflow_kind", "service_id", "service_home",
             "service_home_identity"},
        )
        service = Service(Path(cast(str, marker["service_home"])))
        _owner_check(marker, service.config, "lad")
        if (service.config["service_id"] != subject["service_id"]
                or service.config["target_root"] != subject["target_root"]
                or service.config["target_identity"] != subject["target_identity"]):
            raise EngineeringError("engineering_service_subject_conflict")
        if service_home is not None and service_home.expanduser().absolute() != (
            service.home
        ):
            raise EngineeringError("engineering_service_selector_mismatch")
        if task is not None and task != subject["task_id"]:
            raise EngineeringError("engineering_service_selector_mismatch")
        with target_lock(service.config, protected_roots=(service.home,)):
            rows = service.intake.tasks(cast(str, subject["task_id"]))
            if len(rows) != 1:
                raise EngineeringError("engineering_service_task_missing")
            row = rows[0]
            if (row["status"] != "succeeded" or row["terminal_receipt"] is None
                    or row["claim"] is None):
                raise EngineeringError("engineering_publish_requires_settled_task")
            claim = cast(dict[str, object], row["claim"])
            if (sha256(encode(claim)) != subject["claim_sha256"]
                    or row["request_sha256"] != subject["request_sha256"]):
                raise EngineeringError("engineering_service_subject_conflict")
            try:
                service.intake.lifecycle()
            except IntakeError as exc:
                if exc.code != "lifecycle_missing":
                    raise
                from . import subworkflows

                call = subworkflows.inspect(Path(str(claim["call_home"])),
                                            str(claim["call_key"]))
            else:
                from .lad_service_runtime import Runtime

                call = Runtime(service).call(row)[1]
            paths = cast(dict[str, object], call["paths"])
            if (call["instance_id"] != instance_id or call["run_id"] != run_id
                    or paths["project"] != str(project)):
                raise EngineeringError("engineering_service_call_mismatch")
    except EngineeringError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        raise EngineeringError(code if isinstance(code, str)
                               else "engineering_service_settlement_unproven") from exc


def publish_task(project: Path, history_home: Path, snapshot_sha: str, *,
                 state_home: Path, instance_id: str, run_id: str,
                 expected_state_version: int, service_home: Path | None = None,
                 task: str | None = None) -> dict[str, object]:
    """`engineering-publish` after actual normal task acceptance only.

    Public terminal/quiescent/stopped-writer facts come from Core's
    `exclusive_run_snapshot`. A copy whose captured inputs name a service
    `lad_target` additionally needs that exact task's settled terminal receipt
    under `target_lock`, whether or not selectors were supplied. No Core
    mutator, resume or wake runs.
    """
    project = project.expanduser().absolute()
    history_home = history_home.expanduser().absolute()
    _sha(snapshot_sha, "invalid_snapshot_ref")
    if (service_home is None) != (task is None):
        raise EngineeringError("engineering_service_task_pair")
    subject = service_subject(project)
    task_record = accepted_task(project)
    if subject is None:
        if service_home is not None:
            raise EngineeringError("engineering_service_selector_mismatch")
    elif task_record["task_id"] != subject["task_id"]:
        raise EngineeringError("engineering_service_subject_conflict")
    capture_record = cast(dict[str, object], task_record["capture"])
    history = cast(dict[str, object], capture_record["history"])
    if str(history_home) != history["home"]:
        raise EngineeringError("history_home_mismatch")
    if snapshot_sha != history["sha256"]:
        raise EngineeringError("engineering_snapshot_not_captured")
    expected = {"history_id": history["history_id"], "sha256": snapshot_sha}
    try:
        from .router import control
    except ImportError as exc:
        raise EngineeringError("engineering_publish_requires_core") from exc
    if subject is not None:
        _service_settled(subject, service_home=service_home, task=task,
                         instance_id=instance_id, run_id=run_id, project=project)
    try:
        with control.exclusive_run_snapshot(
            instance_id, run_id, state_home=state_home,
            expected_state_version=expected_state_version,
        ) as view:
            _terminal_view(view, instance_id, run_id)
            identity = control.inspect_instance(instance_id, state_home=state_home)
            if (Path(cast(str, identity["project_root"])) != project
                    or Path(cast(str, identity["workflow_file"]))
                    != project / "workflow.json"):
                raise EngineeringError("engineering_project_identity")
            if accepted_task(project)["bundle_ref"] != task_record["bundle_ref"]:
                raise EngineeringError("engineering_acceptance_changed")
            return publish_requirements(history_home, expected, task_record)
    except EngineeringError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        raise EngineeringError(code if isinstance(code, str)
                               else "engineering_publish_public_facts_refused"
                               ) from exc
