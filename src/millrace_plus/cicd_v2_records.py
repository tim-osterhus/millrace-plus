"""Strict CI v2 records for new explicitly initialized v2 copies. No execution.

Pure validators and deterministic helpers for the record formats frozen by the
CI v2 specification section 7 (sections 7.2-7.5 and the report of 7.6). Every
listed field is required, including nullable ones; unknown keys, bool-as-int,
nonfinite numbers, invalid enums and broken in-record links refuse. Duplicate
keys refuse at `parse_record`. No list-length or prose-length quota exists in
v2: the v1 text/ref/file/line/size limits stay in `cicd_records.py` only.
Arrays hold distinct identities.

CI-R1 subset implemented here:

- config v2 with failure patterns v1, input v2, attempt v2, assessment v2,
  repair v2, check v2, review v2, state v2, check-plan v1, review-plan v1,
  decision v2 and report v2;
- local-authority v1 and actor-binding v1;
- the local candidate facade records: source-binding, base-preparation,
  anchor, candidate, tree, delta, before, export-basis, candidate-request,
  candidate-result, role-basis, active-role v2, subject-projection,
  review-projection, materialization, materialization-operation and
  candidate-verification.

CI-R1 config admits only the hold external policy (`unsupported_until_ci_d1`),
a null health policy (`unsupported_until_ci_d2`) and null wait-node deadlines.
Effect, permission, wait, health and publication-chain (CI-D1/CI-D2) records
remain outside this module. CI-F1 feedback/correlation validators and local
evidence admission live in `cicd_feedback`.

Everything is pure: no filesystem, process, environment, clock or Core access.
Consumers read and hash Ref targets, resolve cross-file links and translate
refusals. Shared path/execution/product records come from `check_contracts`.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
import stat
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import contextmanager
from datetime import datetime
from pathlib import PurePosixPath
from typing import cast

from .check_contracts import (
    EXECUTION_FIELDS,
    OWNER_MARKER,
    CheckContractError,
    PathPolicy,
    encode,
    node_budget,
    parse,
    require_source,
    validate_execution,
    validate_ref,
)

__all__ = [
    "CicdV2Error",
    "CONFIG",
    "FAILURE_PATTERNS",
    "INPUT",
    "ATTEMPT",
    "ASSESSMENT",
    "REPAIR",
    "CHECK",
    "REVIEW",
    "STATE",
    "CHECK_PLAN",
    "REVIEW_PLAN",
    "DECISION",
    "REPORT",
    "LOCAL_AUTHORITY",
    "ACTOR_BINDING",
    "SOURCE_BINDING",
    "BASE_PREPARATION",
    "ANCHOR",
    "CANDIDATE",
    "TREE",
    "DELTA",
    "BEFORE",
    "EXPORT_BASIS",
    "CANDIDATE_REQUEST",
    "CANDIDATE_RESULT",
    "ROLE_BASIS",
    "ACTIVE_ROLE",
    "SUBJECT_PROJECTION",
    "REVIEW_PROJECTION",
    "MATERIALIZATION",
    "MATERIALIZATION_OPERATION",
    "CANDIDATE_VERIFICATION",
    "CHANGE_CLASSES",
    "NATIVE_BINDINGS",
    "READ_ONLY_ADAPTERS",
    "PHASES",
    "CONTINUE_STAGES",
    "SELECTION_REASONS",
    "CANDIDATE_OPERATIONS",
    "TARGET_WRITING_OPERATIONS",
    "FORMATS",
    "hold_external_policy",
    "structural_forbidden",
    "detect_change_classes",
    "effective_change_classes",
    "idle_wait_policy",
    "IDLE_WAIT_DEFAULT",
    "parse_record",
    "config_digest",
    "digest",
    "validate_config",
    "validate_failure_patterns",
    "verify_reproduction_patterns",
    "check_node_budget",
    "reproduction_node_budget",
    "select_checks",
    "failure_fingerprint",
    "validate_input",
    "input_identity",
    "validate_attempt",
    "validate_assessment",
    "validate_repair",
    "validate_check",
    "validate_review",
    "validate_state",
    "validate_check_plan",
    "verify_check_plan",
    "validate_review_plan",
    "verify_review_plan",
    "validate_decision",
    "require_ci_r1_decision",
    "validate_report",
    "validate_local_authority",
    "validate_actor_binding",
    "verify_actor_binding",
    "validate_source_binding",
    "validate_base_preparation",
    "validate_anchor",
    "validate_candidate",
    "candidate_identity",
    "validate_tree",
    "tree_digest",
    "validate_delta",
    "validate_before",
    "validate_export_basis",
    "validate_candidate_request",
    "validate_candidate_result",
    "validate_role_basis",
    "validate_active_role",
    "validate_subject_projection",
    "validate_review_projection",
    "validate_materialization",
    "validate_materialization_operation",
    "validate_candidate_verification",
]

CONFIG = "millrace-plus-cicd-config/v2"
FAILURE_PATTERNS = "millrace-plus-cicd-failure-patterns/v1"
INPUT = "millrace-plus-cicd-input/v2"
ATTEMPT = "millrace-plus-cicd-attempt/v2"
ASSESSMENT = "millrace-plus-cicd-assessment/v2"
REPAIR = "millrace-plus-cicd-repair/v2"
CHECK = "millrace-plus-cicd-check/v2"
REVIEW = "millrace-plus-cicd-review/v2"
STATE = "millrace-plus-cicd-state/v2"
CHECK_PLAN = "millrace-plus-cicd-check-plan/v1"
REVIEW_PLAN = "millrace-plus-cicd-review-plan/v1"
DECISION = "millrace-plus-cicd-decision/v2"
REPORT = "millrace-plus-cicd-report/v2"
LOCAL_AUTHORITY = "millrace-plus-cicd-local-authority/v1"
ACTOR_BINDING = "millrace-plus-cicd-actor-binding/v1"
SOURCE_BINDING = "millrace-plus-cicd-source-binding/v1"
BASE_PREPARATION = "millrace-plus-cicd-base-preparation/v1"
ANCHOR = "millrace-plus-cicd-anchor/v1"
CANDIDATE = "millrace-plus-cicd-candidate/v1"
TREE = "millrace-plus-cicd-tree/v1"
DELTA = "millrace-plus-cicd-delta/v1"
BEFORE = "millrace-plus-cicd-before/v1"
EXPORT_BASIS = "millrace-plus-cicd-export-basis/v1"
CANDIDATE_REQUEST = "millrace-plus-cicd-candidate-request/v1"
CANDIDATE_RESULT = "millrace-plus-cicd-candidate-result/v1"
ROLE_BASIS = "millrace-plus-cicd-role-basis/v1"
ACTIVE_ROLE = "millrace-plus-cicd-active-role/v2"
SUBJECT_PROJECTION = "millrace-plus-cicd-subject-projection/v1"
REVIEW_PROJECTION = "millrace-plus-cicd-review-projection/v1"
MATERIALIZATION = "millrace-plus-cicd-materialization/v1"
MATERIALIZATION_OPERATION = "millrace-plus-cicd-materialization-operation/v1"
CANDIDATE_VERIFICATION = "millrace-plus-cicd-candidate-verification/v1"

CHANGE_CLASSES = (
    "auth",
    "authorization",
    "credentials",
    "dependency",
    "migration",
    "ci_definition",
    "release",
    "deploy",
    "binary",
    "rename",
    "delete",
    "mode_change",
)
# Existing Core binding identities only, compared as strings; no adapter import.
NATIVE_BINDINGS = {
    "command": "millrace-command/v1",
    "codex_exec": "millrace-codex-exec/v1",
    "opencode_run": "millrace-opencode-run/v1",
    "claude_print": "millrace-claude-print/v1",
    "droid_exec": "millrace-droid-exec/v1",
}
# Finite read-only contracts: may investigate or review, never claim write repair.
READ_ONLY_ADAPTERS = frozenset({"droid_exec"})
# Declared section 7.4 graph nodes; a consuming graph may pass its own subset.
PHASES = (
    "reconcile",
    "claim",
    "wait_event",
    "freeze",
    "reproduce",
    "guard_reproduction",
    "investigate",
    "guard_assessment",
    "probe",
    "repair",
    "guard_patch",
    "checks",
    "guard_checks",
    "review",
    "guard_review",
    "guard_delivery",
    "prepare_effect",
    "execute_effect",
    "inspect_effect",
    "wait_effect",
    "guard_health",
    "health",
    "guard_rollback",
    "prepare_rollback",
    "rollback",
    "inspect_rollback",
    "wait_rollback",
    "guard_health_after_rollback",
    "health_after_rollback",
    "hold",
    "settle",
)
# Section 7.3 changed-cause continuation targets.
CONTINUE_STAGES = ("reproduce", "investigate", "probe", "repair", "checks", "review")
SELECTION_REASONS = (
    "path_rules",
    "shared_contract_path",
    "unmatched_path",
    "uncertain",
    "empty_change_set",
)
CANDIDATE_OPERATIONS = (
    "export",
    "prepare-base",
    "import",
    "project-role",
    "check",
    "review",
    "verify",
    "restore",
)
# Operations that write validation-target bytes (check writes owned products).
TARGET_WRITING_OPERATIONS = frozenset({"prepare-base", "import", "check", "restore"})

INPUT_KINDS = ("ci_event", "bug_report", "production_incident")
CI_SOURCES = ("github-actions", "generic-ci")
ATTEMPT_KINDS = (
    "reproduce",
    "investigate",
    "probe",
    "repair",
    "checks",
    "review",
    "health",
)
ATTEMPT_STATUSES = ("reserved", "running", "completed", "unknown", "refused")
HYPOTHESIS_STATUSES = ("supported", "refuted", "unresolved")
ROUTES = ("REPAIR_ALLOWED", "LAD_CANDIDATE", "INVESTIGATE", "HUMAN_HOLD", "NO_ACTION")
CHECK_STATUSES = ("passed", "failed", "timeout", "infrastructure", "uncertain")
GROUP_STATES = ("closed", "may_live", "unknown")
VERDICTS = ("ACCEPT", "REJECT", "BLOCKED")
FINDING_STATUSES = ("unresolved", "resolved", "withdrawn")
DECISION_KINDS = ("continue", "close", "cancel", "approve_delivery", "approve_rollback")
REPORT_STATUSES = (
    "no_action",
    "held_unreproduced",
    "held_infrastructure",
    "held_stale",
    "held_risk",
    "lad_candidate",
    "held_repair",
    "checked_patch",
    "published",
    "healthy",
    "rolled_back",
    "cancelled",
    "uncertain",
)
SOURCE_MODES = ("standalone", "factory")
BINDING_STATUSES = ("bound", "held", "uncertain")
WRITER_STATES = ("stopped", "may_live", "unknown")
RESULT_STATUSES = ("completed", "held", "refused", "uncertain")
ROLES = ("investigate", "repair")
SUBJECT_KINDS = ("ci_input", "factory_input")
MATERIALIZATION_STATES = (
    "reserved",
    "base_ready",
    "repair_reserved",
    "partial",
    "sealed",
    "restored",
    "unknown",
)
LAYER_OPERATIONS = ("prepare_base", "import", "repair", "check_products", "restore")
LAYER_STATUSES = ("reserved", "applied", "unknown", "restored")
VERIFICATION_STATUSES = ("verified", "stale", "refused", "uncertain")
STREAMS = ("stdout", "stderr")
CAP_KINDS = ("files", "changed_lines", "cost")
MEASURES = ("bytes", "entries", "seconds")

SHA = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_EXPANDED_REVIEW = re.compile(r"(?:guard_)?review_[1-9][0-9]*\Z")
# The v1 `owner/name` repository shape without its v1 length bounds.
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+\Z")

_REASONED = ("reason", "origin", "override")
_CONFIG_FIELDS = (
    "format",
    "target_root",
    "repository",
    "source_policy",
    "path_policy_ref",
    "local_authority_ref",
    "node_deadlines",
    "repair_paths",
    "protected_paths",
    "protected_change_classes",
    "class_exceptions",
    "commands",
    "reproduction",
    "investigation",
    "check_policy",
    "role_bindings",
    "repair_policy",
    "resource_policy",
    "external_policy",
    "health_policy",
    "quality_policy",
)
_COMMAND_FIELDS = ("id", *EXECUTION_FIELDS)
_SELECTION_FIELDS = (
    "id",
    "binding",
    "profile_ref",
    "prompt_ref",
    "skill_ref",
    "result_schema_ref",
)
_BINDING_FIELDS = ("adapter", "contract", "executable", "config")
_HOLD = {
    "mode": "hold",
    "effects": [],
    "adapters": {},
    "authority_ref": None,
    "wait_policy_ref": None,
}
# Git, owner-marker and credential components are structurally forbidden: never
# repairable, exceptable or source-tree content. Mirrors the v1 credential subset.
_STRUCTURAL_NAMES = frozenset({".git", OWNER_MARKER, ".netrc", ".npmrc", ".pypirc"})
_CREDENTIAL_PATTERNS = (
    re.compile(r"\.env(?:\..*)?\Z"),
    re.compile(r".*\.(?:pem|key|p12|pfx)\Z"),
    re.compile(r"id_(?:rsa|dsa|ecdsa|ed25519).*\Z"),
    re.compile(r".*(?:credential|secret).*\Z", re.IGNORECASE),
)
_TREE_ENTRY = ("path", "mode", "sha256", "payload_ref")
_LINK = ("path", "mode", "target", "sha256")
_TARGET = ("root", "filesystem_identity", "owner_ref")
_WRITER = ("core_invocation_id", "writer_state")


class CicdV2Error(Exception):
    """Expected refusal with a stable code; retained state stays inspectable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@contextmanager
def _shared() -> Iterator[None]:
    """Translate a shared check-contract refusal, preserving its stable code."""
    try:
        yield
    except CheckContractError as exc:
        raise CicdV2Error(exc.code) from exc


def hold_external_policy() -> dict[str, object]:
    """A fresh copy of the only external policy CI-R1 admits."""
    return {
        "mode": "hold",
        "effects": [],
        "adapters": {},
        "authority_ref": None,
        "wait_policy_ref": None,
    }


def digest(value: object) -> str:
    """SHA-256 of canonical identity bytes (`local_intake.canonical` semantics)."""
    with _shared():
        return hashlib.sha256(encode(value)).hexdigest()


def config_digest(raw: bytes) -> str:
    """Config identity is the SHA-256 of its exact retained bytes, never re-encoded."""
    if not isinstance(raw, bytes):
        raise CicdV2Error("invalid_config_v2")
    return hashlib.sha256(raw).hexdigest()


# Common types.


def _fields(value: object, names: Sequence[str], code: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(names):
        raise CicdV2Error(code)
    return cast(dict[str, object], value)


def _record(
    value: object, names: Sequence[str], fmt: str, code: str
) -> dict[str, object]:
    obj = _fields(value, names, code)
    if obj["format"] != fmt:
        raise CicdV2Error(code)
    return obj


def _text(value: object, code: str) -> str:
    """`Id` and factual text: nonblank, without NUL; no length quota."""
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise CicdV2Error(code)
    return value


def _optional_text(value: object, code: str) -> str | None:
    return None if value is None else _text(value, code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise CicdV2Error(code)
    return value


def _optional_sha(value: object, code: str) -> str | None:
    return None if value is None else _sha(value, code)


def _revision(value: object, code: str) -> str:
    if not isinstance(value, str) or not REVISION.fullmatch(value):
        raise CicdV2Error(code)
    return value


def _optional_revision(value: object, code: str) -> str | None:
    return None if value is None else _revision(value, code)


def _repository(value: object, code: str) -> str:
    if not isinstance(value, str) or not REPOSITORY.fullmatch(value):
        raise CicdV2Error(code)
    return value


def _time(value: object, code: str) -> str:
    """A UTC timestamp; `Z` or a zero offset, never a naive or other-zone time."""
    if not isinstance(value, str) or "\0" in value:
        raise CicdV2Error(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CicdV2Error(code) from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise CicdV2Error(code)
    return value


def _optional_time(value: object, code: str) -> str | None:
    return None if value is None else _time(value, code)


def _integer(value: object, code: str, *, low: int | None = None) -> int:
    if type(value) is not int or (low is not None and value < low):
        raise CicdV2Error(code)
    return value


def _positive(value: object, code: str) -> int:
    return _integer(value, code, low=1)


def _bool(value: object, code: str) -> bool:
    if type(value) is not bool:
        raise CicdV2Error(code)
    return value


def _seconds(value: object, code: str, *, zero: bool = False) -> int | float:
    """A finite positive number (nonnegative with `zero`); bool never counts."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CicdV2Error(code)
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise CicdV2Error(code) from exc
    if not finite or value < 0 or (value == 0 and not zero):
        raise CicdV2Error(code)
    return value


def _exit_code(value: object, code: str) -> int:
    # The shared execution object's POSIX return-code range (-N for signal N).
    if type(value) is not int or not -127 <= value <= 255:
        raise CicdV2Error(code)
    return value


def _optional_exit(value: object, code: str) -> int | None:
    return None if value is None else _exit_code(value, code)


def _enum(value: object, choices: Collection[str], code: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise CicdV2Error(code)
    return value


def _relative(value: object, code: str) -> str:
    """A normalized contained POSIX path; never empty, `.`, absolute or dotted."""
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise CicdV2Error(code)
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or str(path) != value
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise CicdV2Error(code)
    return value


def _absolute(value: object, code: str) -> str:
    """A normalized absolute POSIX root; validated separately from contained refs."""
    if not isinstance(value, str) or "\0" in value or value.startswith("//"):
        raise CicdV2Error(code)
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise CicdV2Error(code)
    return value


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _list(value: object, code: str, *, nonempty: bool = False) -> list[object]:
    if not isinstance(value, list) or (nonempty and not value):
        raise CicdV2Error(code)
    return cast(list[object], value)


def _ids(value: object, code: str, *, nonempty: bool = False) -> list[str]:
    ids = [_text(item, code) for item in _list(value, code, nonempty=nonempty)]
    if len(set(ids)) != len(ids):
        raise CicdV2Error(code)
    return ids


def _paths(value: object, code: str, *, nonempty: bool = False) -> list[str]:
    paths = [_relative(item, code) for item in _list(value, code, nonempty=nonempty)]
    if len(set(paths)) != len(paths):
        raise CicdV2Error(code)
    return paths


def _ref(value: object, code: str) -> dict[str, object]:
    with _shared():
        return validate_ref(value, code)


def _optional_ref(value: object, code: str) -> dict[str, object] | None:
    return None if value is None else _ref(value, code)


def _refs(
    value: object, code: str, *, nonempty: bool = False
) -> list[dict[str, object]]:
    """Distinct Refs; one exact file identity is never listed twice."""
    refs = [_ref(item, code) for item in _list(value, code, nonempty=nonempty)]
    keys = {(ref["path"], ref["sha256"]) for ref in refs}
    if len(keys) != len(refs):
        raise CicdV2Error(code)
    return refs


def _reasoned(value: object, code: str, extra: Sequence[str] = ()) -> dict[str, object]:
    obj = _fields(value, (*extra, *_REASONED), code)
    for key in _REASONED:
        _text(obj[key], code)
    return obj


def _classes(value: object, code: str) -> list[str]:
    classes = [_enum(item, CHANGE_CLASSES, code) for item in _list(value, code)]
    if len(set(classes)) != len(classes):
        raise CicdV2Error(code)
    return classes


def _subject(input_id: object, candidate_id: object, code: str) -> str:
    """Exactly one of an input `Id` or a candidate `Sha`; returns which."""
    if (input_id is None) == (candidate_id is None):
        raise CicdV2Error(code)
    if input_id is not None:
        _text(input_id, code)
        return "input"
    _sha(candidate_id, code)
    return "candidate"


def _paired(first: object, second: object, code: str) -> bool:
    """Both null (False) or both nonnull (True)."""
    if (first is None) != (second is None):
        raise CicdV2Error(code)
    return first is not None


def _writer(value: object, code: str) -> dict[str, object]:
    obj = _fields(value, _WRITER, code)
    _optional_text(obj["core_invocation_id"], code)
    _enum(obj["writer_state"], WRITER_STATES, code)
    return obj


def _sorted_unique(paths: list[str], code: str) -> None:
    if paths != sorted(set(paths)):
        raise CicdV2Error(code)


def structural_forbidden(path: str) -> bool:
    """True for Git, owner-marker or credential components of a contained path.

    These are never repairable, exceptable, protected-away or tree content.
    """
    for part in PurePosixPath(path).parts:
        if part in _STRUCTURAL_NAMES or any(
            pattern.fullmatch(part) for pattern in _CREDENTIAL_PATTERNS
        ):
            return True
    return False


# Deterministic change classes from names or bytes. These are at least every v1
# `cicd_records` protected name and pattern outside the structural set. Semantic
# auth/authorization/release classes have no byte rule: the effective set is
# this detection ∪ the accepted assessment's declared classes (source-fit E7).
_CLASS_NAMES = {
    "ci_definition": frozenset(
        {".github", ".gitlab-ci.yml", ".circleci", "Jenkinsfile"}
    ),
    "dependency": frozenset(
        {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "Pipfile",
            "Pipfile.lock",
            "poetry.lock",
            "uv.lock",
            "package.json",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "Cargo.toml",
            "Cargo.lock",
            "go.mod",
            "go.sum",
            "Gemfile",
            "Gemfile.lock",
        }
    ),
    "migration": frozenset({"migrations", "alembic"}),
    "deploy": frozenset({"Dockerfile", "docker-compose.yml", "docker-compose.yaml"}),
}
_CLASS_PATTERNS = {"dependency": (re.compile(r"requirements.*\.txt\Z"),)}


_Entry = Mapping[str, object] | None


def detect_change_classes(
    changes: Mapping[str, tuple[_Entry, _Entry]],
    *,
    binary_paths: Collection[str] = (),
) -> dict[str, list[str]]:
    """Classes each actual change shows by itself, never by worker assertion.

    `changes` maps a path to its (before, after) snapshot entries; None means
    absent. A structural path is `credentials`. Removed bytes that reappear
    unchanged at another added path are a `rename` on both sides.
    """
    removed = {
        str(before["sha256"]): path
        for path, (before, after) in changes.items()
        if before is not None and after is None and "sha256" in before
    }
    added = {
        str(after["sha256"]): path
        for path, (before, after) in changes.items()
        if before is None and after is not None and "sha256" in after
    }
    renamed = {
        path
        for digest_ in set(removed) & set(added)
        for path in (removed[digest_], added[digest_])
    }
    result: dict[str, list[str]] = {}
    for path, (before, after) in sorted(changes.items()):
        parts = PurePosixPath(path).parts
        found = {kind for kind, names in _CLASS_NAMES.items() if set(parts) & names}
        found |= {
            kind
            for kind, patterns in _CLASS_PATTERNS.items()
            if any(pattern.fullmatch(part) for part in parts for pattern in patterns)
        }
        if structural_forbidden(path):
            found.add("credentials")
        if path in renamed:
            found.add("rename")
        elif after is None:
            found.add("delete")
        if path in binary_paths:
            found.add("binary")
        if (
            before is not None
            and after is not None
            and (
                before.get("mode") != after.get("mode")
                or before.get("kind") != after.get("kind")
            )
        ):
            found.add("mode_change")
        result[path] = sorted(found)
    return result


# Amendment A3 defaults; effective bookkeeping is resolved against the timeout.
IDLE_WAIT_DEFAULT = {
    "poll_seconds": 1,
    "bookkeeping_seconds": 1,
    "reason": (
        "idle responsiveness for committed-intake and control wake; operational"
        " cadence, not a work, quality or overall limit"
    ),
    "origin": (
        "one-second responsiveness poll; result-emission reserve is min(poll,"
        " effective invocation timeout / 2), resolved before create"
    ),
    "override": "set node_deadlines.wait_event before init; no in-place edit",
}


def idle_wait_policy(
    config: Mapping[str, object], *, timeout_seconds: float | None = None
) -> dict[str, object]:
    """The captured idle cadence, or the documented defaults with their provenance.

    The idle window is the wait_event invocation's own effective deadline,
    taken from the existing timeout hierarchy, minus `bookkeeping_seconds`.
    This adds no separate window or overall limit.
    """
    deadlines = cast(dict[str, object], config["node_deadlines"])
    selected = deadlines.get("wait_event")
    if selected is None:
        policy = dict(IDLE_WAIT_DEFAULT) | {"defaulted": True}
        if timeout_seconds is not None:
            _seconds(timeout_seconds, "idle_wait_budget_incompatible")
            policy["bookkeeping_seconds"] = min(
                float(policy["poll_seconds"]), timeout_seconds / 2
            )
    else:
        _wait_event(selected)
        policy = dict(cast(dict[str, object], selected)) | {"defaulted": False}
    if timeout_seconds is not None and policy["bookkeeping_seconds"] >= timeout_seconds:
        raise CicdV2Error("idle_wait_budget_incompatible")
    return policy


def effective_change_classes(
    detected: Iterable[str], declared: Iterable[str]
) -> list[str]:
    """Amendment A5: detected byte/path classes ∪ accepted declarations (add-only).

    A declaration can add a class but never removes a detected one.
    Completeness for semantic classes is never claimed; protected paths still
    apply independently.
    """
    found = list(detected)
    stated = list(declared)
    if any(item not in CHANGE_CLASSES for item in (*found, *stated)):
        raise CicdV2Error("invalid_change_classes")
    return sorted(set(found) | set(stated))


# Config v2 and its captured failure patterns.


def _commands(
    value: object, policy_ref: dict[str, object], policy: PathPolicy | None
) -> dict[str, dict[str, object]]:
    commands: dict[str, dict[str, object]] = {}
    for item in _list(value, "invalid_command", nonempty=True):
        command = _fields(item, _COMMAND_FIELDS, "invalid_command")
        identifier = _text(command["id"], "invalid_command")
        if identifier in commands:
            raise CicdV2Error("duplicate_command_id")
        # Captured commands hold resolved positive timeouts: authored null is
        # resolved at init before hashing, so it never reaches this validator.
        with _shared():
            validate_execution(
                {key: command[key] for key in EXECUTION_FIELDS},
                policy=policy,
                policy_ref=policy_ref,
            )
        commands[identifier] = command
    return commands


def _known(
    value: object, known: Collection[str], code: str, *, nonempty: bool = False
) -> list[str]:
    ids = _ids(value, code, nonempty=nonempty)
    if any(identifier not in known for identifier in ids):
        raise CicdV2Error("unknown_command_id")
    return ids


def _source_policy(value: object, known: Collection[str]) -> None:
    code = "invalid_source_policy"
    obj = _fields(
        value, ("mode", "known_binding_refs", "trusted_preparer_command_id"), code
    )
    _enum(obj["mode"], SOURCE_MODES, code)
    _refs(obj["known_binding_refs"], code)
    preparer = obj["trusted_preparer_command_id"]
    if preparer is not None and _text(preparer, code) not in known:
        raise CicdV2Error("unknown_command_id")


def _deadline(value: object) -> dict[str, object]:
    obj = _reasoned(value, "invalid_node_deadlines", ("bookkeeping_seconds",))
    _seconds(obj["bookkeeping_seconds"], "invalid_node_deadlines", zero=True)
    return obj


def _wait_event(value: object) -> None:
    """Amendment A3: optional idle-wait cadence; the window is the node deadline."""
    code = "invalid_node_deadlines"
    obj = _reasoned(value, code, ("poll_seconds", "bookkeeping_seconds"))
    _seconds(obj["poll_seconds"], code)
    _seconds(obj["bookkeeping_seconds"], code, zero=True)


def _node_deadlines(value: object) -> None:
    obj = _fields(
        value,
        ("checks", "wait_effect", "wait_rollback", "wait_event"),
        "invalid_node_deadlines",
    )
    _deadline(obj["checks"])
    if obj["wait_event"] is not None:
        _wait_event(obj["wait_event"])
    # Wait nodes exist only with selected effects (CI-D1) or rollback (CI-D2).
    if obj["wait_effect"] is not None:
        _deadline(obj["wait_effect"])
        raise CicdV2Error("unsupported_until_ci_d1")
    if obj["wait_rollback"] is not None:
        _deadline(obj["wait_rollback"])
        raise CicdV2Error("unsupported_until_ci_d2")


def _class_exceptions(
    value: object,
    repair: list[str],
    protected: list[str],
    protected_classes: list[str],
) -> list[str]:
    code = "invalid_class_exceptions"
    pairs: set[tuple[str, str]] = set()
    paths: list[str] = []
    for item in _list(value, code):
        obj = _reasoned(item, code, ("class", "paths", "authority_ref"))
        kind = _enum(obj["class"], CHANGE_CLASSES, code)
        # An exception only relaxes a selected protected class.
        if kind not in protected_classes:
            raise CicdV2Error(code)
        _ref(obj["authority_ref"], code)
        for path in _paths(obj["paths"], code, nonempty=True):
            if structural_forbidden(path):
                raise CicdV2Error("structural_not_exceptable")
            if not any(_within(path, prefix) for prefix in repair) or any(
                _within(path, prefix) for prefix in protected
            ):
                raise CicdV2Error(code)
            if (kind, path) in pairs:
                raise CicdV2Error(code)
            pairs.add((kind, path))
            paths.append(path)
    return paths


def _reproduction(
    value: object, commands: dict[str, dict[str, object]]
) -> dict[str, object]:
    code = "invalid_reproduction"
    obj = _fields(
        value, ("command_ids", "failure_patterns_ref", "failure_matcher"), code
    )
    ids = _known(obj["command_ids"], commands, code, nonempty=True)
    _ref(obj["failure_patterns_ref"], code)
    matcher = _fields(
        obj["failure_matcher"],
        ("kind", "failing_exit_codes", "evidence_pattern_ids"),
        code,
    )
    if matcher["kind"] != "exit_and_evidence":
        raise CicdV2Error(code)
    exits = [
        _exit_code(item, code)
        for item in _list(matcher["failing_exit_codes"], code, nonempty=True)
    ]
    if len(set(exits)) != len(exits):
        raise CicdV2Error(code)
    patterns = _ids(matcher["evidence_pattern_ids"], code)
    # Exit-only matching cannot distinguish a failure from an expected exit.
    if not patterns and any(
        commands[identifier]["expected_exit_code"] in exits for identifier in ids
    ):
        raise CicdV2Error(code)
    return obj


def _check_policy(
    value: object, known: Collection[str], quality: list[str]
) -> dict[str, object]:
    code = "invalid_check_policy"
    obj = _fields(
        value,
        ("mandatory_ids", "path_rules", "shared_contract_paths", "full_ids"),
        code,
    )
    mandatory = _known(obj["mandatory_ids"], known, code)
    covered = set(mandatory) | set(quality)
    prefixes: set[str] = set()
    for item in _list(obj["path_rules"], code):
        rule = _fields(item, ("prefix", "command_ids"), code)
        prefix = _relative(rule["prefix"], code)
        if prefix in prefixes:
            raise CicdV2Error(code)
        prefixes.add(prefix)
        covered |= set(_known(rule["command_ids"], known, code))
    _paths(obj["shared_contract_paths"], code)
    full = _known(obj["full_ids"], known, code)
    if not covered <= set(full):
        raise CicdV2Error("full_ids_incomplete")
    return obj


def _selection(value: object, role: str) -> dict[str, object]:
    code = "invalid_role_bindings"
    obj = _fields(value, _SELECTION_FIELDS, code)
    _text(obj["id"], code)
    binding = _fields(obj["binding"], _BINDING_FIELDS, code)
    adapter = binding["adapter"]
    if not isinstance(adapter, str) or adapter not in NATIVE_BINDINGS:
        raise CicdV2Error("unsupported_binding")
    if binding["contract"] != NATIVE_BINDINGS[adapter]:
        raise CicdV2Error("unsupported_binding")
    _text(binding["executable"], code)
    if not isinstance(binding["config"], dict):
        raise CicdV2Error(code)
    if role == "repair" and adapter in READ_ONLY_ADAPTERS:
        raise CicdV2Error("binding_cannot_write")
    command = adapter == "command"
    # A native binding pins its captured profile; the command peer has none.
    if command:
        if obj["profile_ref"] is not None:
            raise CicdV2Error(code)
    else:
        _ref(obj["profile_ref"], code)
    for key in ("prompt_ref", "skill_ref"):
        if obj[key] is None and not command:
            raise CicdV2Error(code)
        _optional_ref(obj[key], code)
    _ref(obj["result_schema_ref"], code)
    return obj


def _role_bindings(value: object) -> list[str]:
    code = "invalid_role_bindings"
    obj = _fields(value, ("investigate", "repair", "review"), code)
    selections = [
        _selection(obj["investigate"], "investigate"),
        _selection(obj["repair"], "repair"),
        *(
            _selection(item, "review")
            for item in _list(obj["review"], code, nonempty=True)
        ),
    ]
    ids = [cast(str, selection["id"]) for selection in selections]
    if len(set(ids)) != len(ids):
        raise CicdV2Error(code)
    return ids


def _repair_policy(value: object, protected_classes: list[str]) -> None:
    code = "invalid_repair_policy"
    obj = _fields(value, ("allowed_change_classes", "optional_caps"), code)
    allowed = _classes(obj["allowed_change_classes"], code)
    # Classes are permitted only when not protected; exceptions are per path.
    if set(allowed) & set(protected_classes):
        raise CicdV2Error(code)
    kinds: set[str] = set()
    for item in _list(obj["optional_caps"], code):
        cap = _reasoned(item, code, ("kind", "value"))
        kind = _enum(cap["kind"], CAP_KINDS, code)
        if kind in kinds:
            raise CicdV2Error(code)
        kinds.add(kind)
        # File and line caps count; a cost cap may be fractional.
        if kind == "cost":
            _seconds(cap["value"], code)
        else:
            _positive(cap["value"], code)


def _resource_policy(value: object) -> None:
    code = "invalid_resource_policy"
    obj = _fields(value, ("guards",), code)
    keys: set[tuple[str, str]] = set()
    for item in _list(obj["guards"], code):
        guard = _reasoned(item, code, ("surface", "measure", "value"))
        surface = _text(guard["surface"], code)
        measure = _enum(guard["measure"], MEASURES, code)
        if (surface, measure) in keys:
            raise CicdV2Error(code)
        keys.add((surface, measure))
        if measure == "seconds":
            _seconds(guard["value"], code)
        else:
            _positive(guard["value"], code)


def _quality_policy(value: object, known: Collection[str]) -> list[str]:
    if value is None:
        return []
    code = "invalid_quality_policy"
    obj = _fields(value, ("command_ids", "evidence_schema_refs"), code)
    _refs(obj["evidence_schema_refs"], code)
    return _known(obj["command_ids"], known, code)


def validate_config(
    value: object, *, policy: PathPolicy | None = None
) -> dict[str, object]:
    """Validate one captured `millrace-plus-cicd-config/v2` object.

    With `policy` (the validated record behind `path_policy_ref`), expected
    products must be declared product paths, and repair, protected and
    exception prefixes must remain source. Identity is `config_digest` of the
    exact retained bytes.
    """
    code = "invalid_config_v2"
    obj = _record(value, _CONFIG_FIELDS, CONFIG, code)
    _absolute(obj["target_root"], code)
    _repository(obj["repository"], code)
    policy_ref = _ref(obj["path_policy_ref"], code)
    _ref(obj["local_authority_ref"], code)
    commands = _commands(obj["commands"], policy_ref, policy)
    _source_policy(obj["source_policy"], commands)
    _node_deadlines(obj["node_deadlines"])
    repair = _paths(obj["repair_paths"], "invalid_repair_paths", nonempty=True)
    if any(structural_forbidden(path) for path in repair):
        raise CicdV2Error("invalid_repair_paths")
    protected = _paths(obj["protected_paths"], "invalid_protected_paths")
    protected_classes = _classes(
        obj["protected_change_classes"], "invalid_change_classes"
    )
    excepted = _class_exceptions(
        obj["class_exceptions"], repair, protected, protected_classes
    )
    _reproduction(obj["reproduction"], commands)
    investigation = _fields(
        obj["investigation"], ("probe_command_ids",), "invalid_investigation"
    )
    _known(investigation["probe_command_ids"], commands, "invalid_investigation")
    quality = _quality_policy(obj["quality_policy"], commands)
    _check_policy(obj["check_policy"], commands, quality)
    _role_bindings(obj["role_bindings"])
    _repair_policy(obj["repair_policy"], protected_classes)
    _resource_policy(obj["resource_policy"])
    if obj["external_policy"] != _HOLD:
        raise CicdV2Error("unsupported_until_ci_d1")
    if obj["health_policy"] is not None:
        raise CicdV2Error("unsupported_until_ci_d2")
    if policy is not None:
        with _shared():
            require_source(policy, [*repair, *protected, *excepted])
    return obj


def validate_failure_patterns(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-failure-patterns/v1`: literal signatures, never regex."""
    code = "invalid_failure_patterns"
    obj = _record(value, ("format", "patterns"), FAILURE_PATTERNS, code)
    seen: set[str] = set()
    for item in _list(obj["patterns"], code):
        pattern = _fields(item, ("id", "stream", "literal"), code)
        identifier = _text(pattern["id"], code)
        if identifier in seen:
            raise CicdV2Error(code)
        seen.add(identifier)
        _enum(pattern["stream"], STREAMS, code)
        _text(pattern["literal"], code)
    return obj


def verify_reproduction_patterns(
    config: object, patterns: object
) -> dict[str, dict[str, object]]:
    """Resolve every selected evidence pattern ID in the captured pattern asset."""
    checked = validate_config(config)
    record = validate_failure_patterns(patterns)
    index = {
        cast(str, item["id"]): item
        for item in cast(list[dict[str, object]], record["patterns"])
    }
    matcher = cast(
        dict[str, object],
        cast(dict[str, object], checked["reproduction"])["failure_matcher"],
    )
    selected = cast(list[str], matcher["evidence_pattern_ids"])
    if any(identifier not in index for identifier in selected):
        raise CicdV2Error("unknown_failure_pattern")
    return {identifier: index[identifier] for identifier in selected}


# Init-static budgets and deterministic check selection.


def _policy_ids(config: dict[str, object]) -> tuple[set[str], set[str], set[str]]:
    policy = cast(dict[str, object], config["check_policy"])
    reproduction = cast(dict[str, object], config["reproduction"])
    return (
        set(cast(list[str], policy["mandatory_ids"])),
        set(cast(list[str], policy["full_ids"])),
        set(cast(list[str], reproduction["command_ids"])),
    )


def _budget(
    config: dict[str, object], ids: list[str], ceiling_seconds: object
) -> dict[str, object]:
    timeouts = {
        cast(str, command["id"]): command["timeout_seconds"]
        for command in cast(list[dict[str, object]], config["commands"])
    }
    deadline = cast(
        dict[str, object], cast(dict[str, object], config["node_deadlines"])["checks"]
    )
    with _shared():
        budget = node_budget(
            [timeouts[identifier] for identifier in ids],
            deadline["bookkeeping_seconds"],
            ceiling_seconds=ceiling_seconds,
        )
    return {
        "command_ids": ids,
        **budget,
        "bookkeeping_seconds": deadline["bookkeeping_seconds"],
        "reason": deadline["reason"],
        "origin": deadline["origin"],
        "override": deadline["override"],
    }


def check_node_budget(
    config: object, *, ceiling_seconds: object = None
) -> dict[str, object]:
    """Standalone checks node budget over the unique maximum selectable set.

    Sums effective timeouts of `full_ids ∪ mandatory_ids ∪ reproduction` plus
    `node_deadlines.checks.bookkeeping_seconds`, with that allowance's
    reason/origin/override. A runtime subset never changes this captured value.
    """
    checked = validate_config(config)
    mandatory, full, reproduction = _policy_ids(checked)
    return _budget(checked, sorted(full | mandatory | reproduction), ceiling_seconds)


def reproduction_node_budget(
    config: object, *, ceiling_seconds: object = None
) -> dict[str, object]:
    """Reproduction-ID timeout sum plus the same captured checks bookkeeping."""
    checked = validate_config(config)
    _, _, reproduction = _policy_ids(checked)
    return _budget(checked, sorted(reproduction), ceiling_seconds)


def select_checks(
    config: object, changed_paths: Iterable[str], *, uncertain: bool
) -> dict[str, object]:
    """Deterministic selection; models may explain relevance, never shrink it.

    Mandatory ∪ every matching path rule ∪ reproduction. A shared-contract
    path, a path no rule matches, an empty change set or uncertain scope uses
    `full_ids` ∪ mandatory ∪ reproduction instead.
    """
    code = "invalid_selection"
    checked = validate_config(config)
    if type(uncertain) is not bool or isinstance(changed_paths, str | bytes):
        raise CicdV2Error(code)
    try:
        paths = [_relative(path, code) for path in changed_paths]
    except TypeError as exc:
        raise CicdV2Error(code) from exc
    mandatory, full, reproduction = _policy_ids(checked)
    policy = cast(dict[str, object], checked["check_policy"])
    shared = cast(list[str], policy["shared_contract_paths"])
    rules = cast(list[dict[str, object]], policy["path_rules"])
    base = mandatory | reproduction
    selected = set(base)
    reason = "path_rules"
    if uncertain:
        reason = "uncertain"
    elif not paths:
        reason = "empty_change_set"
    elif any(_within(path, prefix) for path in paths for prefix in shared):
        reason = "shared_contract_path"
    else:
        for path in paths:
            matches = [
                rule for rule in rules if _within(path, cast(str, rule["prefix"]))
            ]
            if not matches:
                reason = "unmatched_path"
                break
            for rule in matches:
                selected |= set(cast(list[str], rule["command_ids"]))
    if reason != "path_rules":
        selected = full | base
    return {"command_ids": sorted(selected), "selection_reason": reason}


def failure_fingerprint(
    *,
    config_sha256: str,
    source_sha256: str,
    checks: Sequence[object],
    finding_ids: Iterable[str] = (),
) -> str:
    """Section 7.3 unchanged-cause fingerprint; wording is excluded.

    Comprises config/source identities, each command's identity with its
    normalized status/exit and matched failure-pattern IDs, and finding IDs.
    Every check receipt must bind the given source; one entry per command.
    """
    code = "invalid_fingerprint"
    _sha(config_sha256, code)
    _sha(source_sha256, code)
    if isinstance(checks, str | bytes) or isinstance(finding_ids, str | bytes):
        raise CicdV2Error(code)
    entries: dict[str, dict[str, object]] = {}
    for item in checks:
        check = validate_check(item)
        command = cast(str, check["command_id"])
        if command in entries:
            raise CicdV2Error(code)
        if check["source_before_sha256"] != source_sha256:
            raise CicdV2Error("fingerprint_basis_mismatch")
        entries[command] = {
            "command_id": command,
            "command_sha256": check["command_sha256"],
            "status": check["status"],
            "exit_code": check["exit_code"],
            "matched_failure_ids": sorted(
                set(cast(list[str], check["matched_failure_ids"]))
            ),
        }
    findings = sorted({_text(item, code) for item in finding_ids})
    return digest(
        {
            "config_sha256": config_sha256,
            "source_sha256": source_sha256,
            "commands": [entries[key] for key in sorted(entries)],
            "finding_ids": findings,
        }
    )


# Input, attempt, assessment, repair, check and review records.

_CI_IDENTITY = ("source", "repository", "run_id", "attempt", "job_id", "head_sha")
_FEEDBACK_IDENTITY = ("kind", "producer", "report_id", "repository")


def input_identity(kind: str, source_identity: object) -> str:
    """SHA-256 of the exact typed original identity object (v1 event or feedback)."""
    code = "invalid_input_v2"
    _enum(kind, INPUT_KINDS, code)
    if kind == "ci_event":
        obj = _fields(source_identity, _CI_IDENTITY, code)
        _enum(obj["source"], CI_SOURCES, code)
        _text(obj["run_id"], code)
        _positive(obj["attempt"], code)
        _text(obj["job_id"], code)
        _revision(obj["head_sha"], code)
    else:
        obj = _fields(source_identity, _FEEDBACK_IDENTITY, code)
        if obj["kind"] != kind:
            raise CicdV2Error(code)
        _text(obj["producer"], code)
        _text(obj["report_id"], code)
    _repository(obj["repository"], code)
    return digest(obj)


def validate_input(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-input/v2`; FIFO order is the local acceptance sequence."""
    code = "invalid_input_v2"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "kind",
            "original_ref",
            "evidence_refs",
            "accepted_sequence",
            "repository",
            "source_identity",
        ),
        INPUT,
        code,
    )
    kind = _enum(obj["kind"], INPUT_KINDS, code)
    _sha(obj["input_id"], code)
    _ref(obj["original_ref"], code)
    _refs(obj["evidence_refs"], code)
    _positive(obj["accepted_sequence"], code)
    repository = _repository(obj["repository"], code)
    identity = cast(dict[str, object], obj["source_identity"])
    if input_identity(kind, identity) != obj["input_id"]:
        raise CicdV2Error("input_identity_mismatch")
    if identity["repository"] != repository:
        raise CicdV2Error("repository_mismatch")
    return obj


def validate_attempt(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-attempt/v2`: a durable, never reused reservation."""
    code = "invalid_attempt"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "subject_ref",
            "attempt_id",
            "parent_attempt_id",
            "kind",
            "basis_sha256",
            "source_sha256",
            "config_sha256",
            "changed_cause_ref",
            "reservation",
            "completion_ref",
            "artifact_ref",
            "status",
        ),
        ATTEMPT,
        code,
    )
    _text(obj["input_id"], code)
    _ref(obj["subject_ref"], code)
    attempt = _text(obj["attempt_id"], code)
    parent = _optional_text(obj["parent_attempt_id"], code)
    if parent == attempt:
        raise CicdV2Error(code)
    _enum(obj["kind"], ATTEMPT_KINDS, code)
    for key in ("basis_sha256", "source_sha256", "config_sha256"):
        _sha(obj[key], code)
    # A changed-cause continuation is always linked to its predecessor.
    if _optional_ref(obj["changed_cause_ref"], code) is not None and parent is None:
        raise CicdV2Error(code)
    reservation = _fields(
        obj["reservation"],
        ("core_invocation_id", "core_attempt_id", "request_id"),
        code,
    )
    _text(reservation["request_id"], code)
    dispatched = _paired(
        reservation["core_invocation_id"], reservation["core_attempt_id"], code
    )
    if dispatched:
        _text(reservation["core_invocation_id"], code)
        _positive(reservation["core_attempt_id"], code)
    status = _enum(obj["status"], ATTEMPT_STATUSES, code)
    completion = _optional_ref(obj["completion_ref"], code)
    artifact = _optional_ref(obj["artifact_ref"], code)
    if status in {"running", "completed"} and not dispatched:
        raise CicdV2Error(code)
    if status == "completed" and completion is None:
        raise CicdV2Error(code)
    if status in {"reserved", "running"} and (
        completion is not None or artifact is not None
    ):
        raise CicdV2Error(code)
    return obj


def validate_assessment(
    value: object, *, allow_empty_reproduction: bool = False
) -> dict[str, object]:
    """`millrace-plus-cicd-assessment/v2`.

    `allow_empty_reproduction` is set only by a consumer that has verified an
    explicit factory `source_kind:task` with immutable criteria; every failure,
    bug or incident repair keeps reproduction before repair.
    """
    code = "invalid_assessment"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "subject_ref",
            "attempt_id",
            "reproduction_refs",
            "hypotheses",
            "proposed_paths",
            "change_classes",
            "risk",
            "route",
            "evidence_refs",
        ),
        ASSESSMENT,
        code,
    )
    _text(obj["input_id"], code)
    _ref(obj["subject_ref"], code)
    _text(obj["attempt_id"], code)
    reproduction = _refs(obj["reproduction_refs"], code)
    supported = False
    seen: set[str] = set()
    for item in _list(obj["hypotheses"], code):
        hypothesis = _fields(item, ("id", "claim", "status", "evidence_refs"), code)
        identifier = _text(hypothesis["id"], code)
        if identifier in seen:
            raise CicdV2Error(code)
        seen.add(identifier)
        _text(hypothesis["claim"], code)
        status = _enum(hypothesis["status"], HYPOTHESIS_STATUSES, code)
        _refs(hypothesis["evidence_refs"], code)
        supported = supported or status == "supported"
    paths = _paths(obj["proposed_paths"], code)
    _classes(obj["change_classes"], code)
    risk = _fields(
        obj["risk"], ("impact", "likelihood", "scenario", "evidence_refs"), code
    )
    for key in ("impact", "likelihood", "scenario"):
        _text(risk[key], code)
    _refs(risk["evidence_refs"], code)
    route = _enum(obj["route"], ROUTES, code)
    _refs(obj["evidence_refs"], code)
    if type(allow_empty_reproduction) is not bool:
        raise CicdV2Error(code)
    if route == "REPAIR_ALLOWED" and (
        not supported
        or not paths
        or (not reproduction and not allow_empty_reproduction)
    ):
        raise CicdV2Error("unsupported_repair")
    return obj


def validate_repair(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-repair/v2`; the scope guard still compares actual bytes."""
    code = "invalid_repair_v2"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "attempt_id",
            "assessment_ref",
            "source_before_sha256",
            "source_after_sha256",
            "patch_ref",
            "changed_paths",
            "change_classes",
            "summary",
            "evidence_refs",
        ),
        REPAIR,
        code,
    )
    _text(obj["input_id"], code)
    _text(obj["attempt_id"], code)
    _ref(obj["assessment_ref"], code)
    before = _sha(obj["source_before_sha256"], code)
    if _sha(obj["source_after_sha256"], code) == before:
        raise CicdV2Error(code)
    _ref(obj["patch_ref"], code)
    _paths(obj["changed_paths"], code, nonempty=True)
    _classes(obj["change_classes"], code)
    _text(obj["summary"], code)
    _refs(obj["evidence_refs"], code)
    return obj


_CHECK_FIELDS = (
    "format",
    "input_id",
    "candidate_id",
    "materialization_id",
    "attempt_id",
    "command_id",
    "command_sha256",
    "argv",
    "cwd",
    "environment_provenance",
    "path_policy_ref",
    "environment_before_ref",
    "environment_after_ref",
    "product_refs",
    "head_sha",
    "source_before_sha256",
    "source_after_sha256",
    "started_at",
    "finished_at",
    "exit_code",
    "status",
    "process_facts",
    "stdout_ref",
    "stderr_ref",
    "matched_failure_ids",
    "diagnostic",
)


def _environment_name(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or "=" in value or "\0" in value:
        raise CicdV2Error(code)
    return value


def validate_check(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-check/v2`: a raw process receipt, never acceptance.

    Raw `passed` requires an exit, complete logs and environment observations,
    proven root/stream/group closure, unchanged source and no matched failure;
    accepted evidence is the separate completion-acceptance record.
    """
    code = "invalid_check_v2"
    obj = _record(value, _CHECK_FIELDS, CHECK, code)
    subject = _subject(obj["input_id"], obj["candidate_id"], code)
    if subject == "candidate":
        _text(obj["materialization_id"], code)
    elif obj["materialization_id"] is not None:
        raise CicdV2Error(code)
    _text(obj["attempt_id"], code)
    _text(obj["command_id"], code)
    _sha(obj["command_sha256"], code)
    argv = _list(obj["argv"], code, nonempty=True)
    if (
        any(not isinstance(item, str) or "\0" in item for item in argv)
        or not cast(str, argv[0]).strip()
    ):
        raise CicdV2Error(code)
    if obj["cwd"] != ".":
        _relative(obj["cwd"], code)
    provenance = _fields(
        obj["environment_provenance"],
        (
            "inherited_names",
            "inherited_value_sha256",
            "set_value_sha256",
            "path_observation_refs",
        ),
        code,
    )
    names = [
        _environment_name(item, code)
        for item in _list(provenance["inherited_names"], code)
    ]
    if len(set(names)) != len(names):
        raise CicdV2Error(code)
    _sha(provenance["inherited_value_sha256"], code)
    _sha(provenance["set_value_sha256"], code)
    _refs(provenance["path_observation_refs"], code)
    _ref(obj["path_policy_ref"], code)
    before = _optional_ref(obj["environment_before_ref"], code)
    after = _optional_ref(obj["environment_after_ref"], code)
    if after is not None and before is None:
        raise CicdV2Error(code)
    _refs(obj["product_refs"], code)
    _revision(obj["head_sha"], code)
    source_before = _sha(obj["source_before_sha256"], code)
    source_after = _optional_sha(obj["source_after_sha256"], code)
    _time(obj["started_at"], code)
    finished = _optional_time(obj["finished_at"], code)
    exit_code = _optional_exit(obj["exit_code"], code)
    status = _enum(obj["status"], CHECK_STATUSES, code)
    facts = _fields(
        obj["process_facts"],
        ("root_reaped", "stdout_eof", "stderr_eof", "group_state"),
        code,
    )
    closed = all(
        _bool(facts[key], code) for key in ("root_reaped", "stdout_eof", "stderr_eof")
    )
    group = _enum(facts["group_state"], GROUP_STATES, code)
    stdout = _optional_ref(obj["stdout_ref"], code)
    stderr = _optional_ref(obj["stderr_ref"], code)
    matched = _ids(obj["matched_failure_ids"], code)
    _optional_text(obj["diagnostic"], code)
    if status in {"passed", "failed"} and (exit_code is None or finished is None):
        raise CicdV2Error(code)
    if status == "passed" and (
        not closed
        or group != "closed"
        or stdout is None
        or stderr is None
        or after is None
        or source_after != source_before
        or matched
    ):
        raise CicdV2Error("check_pass_unproven")
    return obj


def validate_review(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-review/v2`; ACCEPT requires zero unresolved findings."""
    code = "invalid_review_v2"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "candidate_id",
            "materialization_id",
            "attempt_id",
            "candidate_sha256",
            "patch_ref",
            "checks_refs",
            "verdict",
            "findings",
            "evidence_refs",
        ),
        REVIEW,
        code,
    )
    subject = _subject(obj["input_id"], obj["candidate_id"], code)
    if subject == "candidate":
        _text(obj["materialization_id"], code)
    elif obj["materialization_id"] is not None:
        raise CicdV2Error(code)
    _text(obj["attempt_id"], code)
    _sha(obj["candidate_sha256"], code)
    _ref(obj["patch_ref"], code)
    checks = _refs(obj["checks_refs"], code)
    verdict = _enum(obj["verdict"], VERDICTS, code)
    unresolved = 0
    seen: set[str] = set()
    for item in _list(obj["findings"], code):
        finding = _fields(
            item,
            (
                "id",
                "scenario",
                "evidence_refs",
                "impact",
                "likelihood",
                "status",
                "resolution_ref",
            ),
            code,
        )
        identifier = _text(finding["id"], code)
        if identifier in seen:
            raise CicdV2Error(code)
        seen.add(identifier)
        _text(finding["scenario"], code)
        _refs(finding["evidence_refs"], code, nonempty=True)
        _text(finding["impact"], code)
        _text(finding["likelihood"], code)
        status = _enum(finding["status"], FINDING_STATUSES, code)
        resolution = _optional_ref(finding["resolution_ref"], code)
        if (status == "unresolved") != (resolution is None):
            raise CicdV2Error(code)
        if status == "unresolved":
            unresolved += 1
    _refs(obj["evidence_refs"], code)
    if verdict == "ACCEPT" and unresolved:
        raise CicdV2Error("review_unresolved_findings")
    # ACCEPT binds current check evidence; it never stands without checks.
    if verdict == "ACCEPT" and not checks:
        raise CicdV2Error(code)
    return obj


# State, plans, decision and report.


def validate_state(
    value: object, *, phases: Collection[str] | None = None
) -> dict[str, object]:
    """`millrace-plus-cicd-state/v2`; phase alone never grants continuation.

    `phases` restricts the phase to the consuming graph's declared nodes; the
    default admits every section 7.4 node.
    """
    code = "invalid_state_v2"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "version",
            "phase",
            "source_binding_ref",
            "attempt_refs",
            "current_attempt_id",
            "basis_sha256",
            "assessment_ref",
            "candidate_ref",
            "check_plan_ref",
            "review_plan_ref",
            "delivery_refs",
            "health_refs",
            "product_layer_refs",
            "hold_ref",
            "settlement_ref",
            "applied_decision_ids",
        ),
        STATE,
        code,
    )
    _text(obj["input_id"], code)
    _positive(obj["version"], code)
    allowed = PHASES if phases is None else phases
    # Amendment A4: ordered reviewer pairs expand to review_<k>/guard_review_<k>.
    if any(
        phase not in PHASES and not _EXPANDED_REVIEW.fullmatch(str(phase))
        for phase in allowed
    ):
        raise CicdV2Error(code)
    _enum(obj["phase"], allowed, code)
    for key in (
        "source_binding_ref",
        "assessment_ref",
        "candidate_ref",
        "check_plan_ref",
        "review_plan_ref",
        "hold_ref",
        "settlement_ref",
    ):
        _optional_ref(obj[key], code)
    for key in ("attempt_refs", "delivery_refs", "health_refs", "product_layer_refs"):
        _refs(obj[key], code)
    _optional_text(obj["current_attempt_id"], code)
    _optional_sha(obj["basis_sha256"], code)
    _ids(obj["applied_decision_ids"], code)
    return obj


def validate_check_plan(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-check-plan/v1`: an immutable captured ID sequence."""
    code = "invalid_check_plan"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "candidate_id",
            "basis_sha256",
            "command_ids",
            "selection_reason",
            "evidence_refs",
        ),
        CHECK_PLAN,
        code,
    )
    _subject(obj["input_id"], obj["candidate_id"], code)
    _sha(obj["basis_sha256"], code)
    _ids(obj["command_ids"], code, nonempty=True)
    _text(obj["selection_reason"], code)
    _refs(obj["evidence_refs"], code)
    return obj


def verify_check_plan(config: object, plan: object) -> dict[str, object]:
    """No plan adds a command outside the init-known set or omits a required one."""
    checked = validate_config(config)
    record = validate_check_plan(plan)
    mandatory, full, reproduction = _policy_ids(checked)
    ids = set(cast(list[str], record["command_ids"]))
    if not ids <= full | mandatory | reproduction:
        raise CicdV2Error("check_plan_unknown_command")
    if not mandatory | reproduction <= ids:
        raise CicdV2Error("check_plan_incomplete")
    return record


def validate_review_plan(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-review-plan/v1`: every selected reviewer must accept."""
    code = "invalid_review_plan"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "candidate_id",
            "basis_sha256",
            "binding_ids",
            "evidence_refs",
        ),
        REVIEW_PLAN,
        code,
    )
    _subject(obj["input_id"], obj["candidate_id"], code)
    _sha(obj["basis_sha256"], code)
    _ids(obj["binding_ids"], code, nonempty=True)
    _refs(obj["evidence_refs"], code)
    return obj


def verify_review_plan(config: object, plan: object) -> dict[str, object]:
    """Every planned binding ID is one of the captured review selections."""
    checked = validate_config(config)
    record = validate_review_plan(plan)
    bindings = cast(dict[str, object], checked["role_bindings"])
    selections = cast(list[dict[str, object]], bindings["review"])
    reviewers = {cast(str, item["id"]) for item in selections}
    if not set(cast(list[str], record["binding_ids"])) <= reviewers:
        raise CicdV2Error("review_plan_unknown_binding")
    return record


def validate_decision(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-decision/v2`; structural for every selected kind."""
    code = "invalid_decision_v2"
    obj = _record(
        value,
        (
            "format",
            "request_id",
            "input_id",
            "expected_state_version",
            "kind",
            "basis_sha256",
            "target_stage",
            "changed_cause_ref",
            "permission_ref",
            "evidence_refs",
        ),
        DECISION,
        code,
    )
    _text(obj["request_id"], code)
    _text(obj["input_id"], code)
    _positive(obj["expected_state_version"], code)
    kind = _enum(obj["kind"], DECISION_KINDS, code)
    _sha(obj["basis_sha256"], code)
    cause = _optional_ref(obj["changed_cause_ref"], code)
    permission = _optional_ref(obj["permission_ref"], code)
    if kind == "continue":
        _enum(obj["target_stage"], CONTINUE_STAGES, code)
        if cause is None:
            raise CicdV2Error(code)
    elif obj["target_stage"] is not None or cause is not None:
        raise CicdV2Error(code)
    if (kind in {"approve_delivery", "approve_rollback"}) != (permission is not None):
        raise CicdV2Error(code)
    _refs(obj["evidence_refs"], code)
    return obj


def require_ci_r1_decision(decision: object) -> dict[str, object]:
    """CI-R1 applies only continue/close/cancel; effect approvals wait for D1/D2."""
    record = validate_decision(decision)
    if record["kind"] == "approve_delivery":
        raise CicdV2Error("unsupported_until_ci_d1")
    if record["kind"] == "approve_rollback":
        raise CicdV2Error("unsupported_until_ci_d2")
    return record


def validate_report(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-report/v2`; null and empty record absent facts."""
    code = "invalid_report_v2"
    obj = _record(
        value,
        (
            "format",
            "input_id",
            "config_sha256",
            "status",
            "source_before_sha256",
            "source_after_sha256",
            "assessment_ref",
            "attempt_refs",
            "patch_ref",
            "checks_refs",
            "review_refs",
            "correlation_refs",
            "candidate_ref",
            "delivery_refs",
            "health_refs",
            "incident_refs",
            "hold_reason",
        ),
        REPORT,
        code,
    )
    _text(obj["input_id"], code)
    _sha(obj["config_sha256"], code)
    status = _enum(obj["status"], REPORT_STATUSES, code)
    _optional_sha(obj["source_before_sha256"], code)
    _optional_sha(obj["source_after_sha256"], code)
    for key in ("assessment_ref", "patch_ref", "candidate_ref"):
        _optional_ref(obj[key], code)
    refs = {
        key: _refs(obj[key], code)
        for key in (
            "attempt_refs",
            "checks_refs",
            "review_refs",
            "correlation_refs",
            "delivery_refs",
            "health_refs",
            "incident_refs",
        )
    }
    hold = _optional_text(obj["hold_reason"], code)
    if status.startswith("held_") and hold is None:
        raise CicdV2Error(code)
    if status == "checked_patch" and (
        obj["patch_ref"] is None or not refs["checks_refs"] or not refs["review_refs"]
    ):
        raise CicdV2Error(code)
    # Published proves selected effects only; health needs its own evidence.
    if status == "published" and not refs["delivery_refs"]:
        raise CicdV2Error(code)
    if status in {"healthy", "rolled_back"} and not refs["health_refs"]:
        raise CicdV2Error(code)
    return obj


# Local authority and bind-once actor projection.


def _actor(value: object, code: str) -> dict[str, object]:
    obj = _fields(value, ("execution_home", "instance_id", "definition_ref"), code)
    _absolute(obj["execution_home"], code)
    _optional_text(obj["instance_id"], code)
    _ref(obj["definition_ref"], code)
    return obj


def validate_local_authority(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-local-authority/v1`; an owner marker alone grants nothing."""
    code = "invalid_local_authority"
    obj = _record(
        value,
        (
            "format",
            "issuer",
            "target_root",
            "owner_ref",
            "actors",
            "operations",
            "repair_paths",
            "path_policy_ref",
            "origin",
            "override",
        ),
        LOCAL_AUTHORITY,
        code,
    )
    _text(obj["issuer"], code)
    _absolute(obj["target_root"], code)
    _ref(obj["owner_ref"], code)
    actors = [_actor(item, code) for item in _list(obj["actors"], code, nonempty=True)]
    keys = {
        (actor["execution_home"], digest(actor["definition_ref"])) for actor in actors
    }
    if len(keys) != len(actors):
        raise CicdV2Error(code)
    _ids(obj["operations"], code, nonempty=True)
    repair = _paths(obj["repair_paths"], code, nonempty=True)
    if any(structural_forbidden(path) for path in repair):
        raise CicdV2Error(code)
    _ref(obj["path_policy_ref"], code)
    _text(obj["origin"], code)
    _text(obj["override"], code)
    return obj


def validate_actor_binding(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-actor-binding/v1`: the exact post-create projection."""
    code = "invalid_actor_binding"
    obj = _record(
        value,
        (
            "format",
            "authority_ref",
            "execution_home",
            "instance_id",
            "definition_ref",
            "create_request_id",
            "create_receipt_ref",
        ),
        ACTOR_BINDING,
        code,
    )
    _ref(obj["authority_ref"], code)
    _absolute(obj["execution_home"], code)
    _text(obj["instance_id"], code)
    _ref(obj["definition_ref"], code)
    _text(obj["create_request_id"], code)
    _ref(obj["create_receipt_ref"], code)
    return obj


def verify_actor_binding(authority: object, binding: object) -> dict[str, object]:
    """The binding names exactly one captured actor; never the latest instance."""
    record = validate_local_authority(authority)
    bound = validate_actor_binding(binding)
    matches = [
        actor
        for actor in cast(list[dict[str, object]], record["actors"])
        if actor["execution_home"] == bound["execution_home"]
        and actor["definition_ref"] == bound["definition_ref"]
        and actor["instance_id"] in {None, bound["instance_id"]}
    ]
    if len(matches) != 1:
        raise CicdV2Error("actor_binding_mismatch")
    return matches[0]


# Source binding, base preparation and the candidate chain.


def validate_source_binding(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-source-binding/v1`; observations never report HEAD=C.

    Standalone binds only when actual HEAD equals the reported head; a
    preparer receipt never weakens that. Factory binds a known commit through
    publication and candidate/materialization evidence, an external revision
    through the
    preparer, or unpublished local work through the logical tree only.
    """
    code = "invalid_source_binding"
    obj = _record(
        value,
        (
            "format",
            "mode",
            "input_ref",
            "anchor_head",
            "reported_head",
            "candidate_ref",
            "publication_ref",
            "preparer_ref",
            "logical_base_id",
            "materialization_ref",
            "status",
        ),
        SOURCE_BINDING,
        code,
    )
    mode = _enum(obj["mode"], SOURCE_MODES, code)
    _ref(obj["input_ref"], code)
    anchor = _revision(obj["anchor_head"], code)
    reported = _optional_revision(obj["reported_head"], code)
    candidate = _optional_ref(obj["candidate_ref"], code)
    publication = _optional_ref(obj["publication_ref"], code)
    preparer = _optional_ref(obj["preparer_ref"], code)
    logical = _optional_sha(obj["logical_base_id"], code)
    materialization = _optional_ref(obj["materialization_ref"], code)
    status = _enum(obj["status"], BINDING_STATUSES, code)
    if publication is not None and (preparer is not None or reported is None):
        raise CicdV2Error(code)
    if mode == "standalone":
        factory = (candidate, publication, logical, materialization)
        if any(item is not None for item in factory):
            raise CicdV2Error(code)
        # Standalone admission always needs actual HEAD == reported head; a
        # preparer receipt never weakens it (spec 7.5 source binding).
        if status == "bound" and (reported is None or reported != anchor):
            raise CicdV2Error("source_binding_unproven")
        return obj
    if logical is None:
        raise CicdV2Error(code)
    if status == "bound":
        known = publication is not None and (
            candidate is not None and materialization is not None
        )
        external = preparer is not None and reported is not None
        unpublished = reported is None and preparer is None
        if not (known or external or unpublished):
            raise CicdV2Error("source_binding_unproven")
    return obj


def validate_base_preparation(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-base-preparation/v1`: requested equals observed revision."""
    code = "invalid_base_preparation"
    obj = _record(
        value,
        (
            "format",
            "request_id",
            "repository",
            "requested_revision",
            "observed_revision",
            "observed_tree_sha256",
            "target_before_sha256",
            "target_after_sha256",
            "authority_ref",
            "command_ref",
            "writer_facts",
            "evidence_refs",
        ),
        BASE_PREPARATION,
        code,
    )
    _text(obj["request_id"], code)
    _repository(obj["repository"], code)
    if _revision(obj["requested_revision"], code) != _revision(
        obj["observed_revision"], code
    ):
        raise CicdV2Error("revision_mismatch")
    for key in ("observed_tree_sha256", "target_before_sha256", "target_after_sha256"):
        _sha(obj[key], code)
    _ref(obj["authority_ref"], code)
    _ref(obj["command_ref"], code)
    _writer(obj["writer_facts"], code)
    _refs(obj["evidence_refs"], code)
    return obj


def _target(value: object, code: str) -> dict[str, object]:
    obj = _fields(value, _TARGET, code)
    root = _absolute(obj["root"], code)
    identity = _fields(obj["filesystem_identity"], ("path", "device", "inode"), code)
    if _absolute(identity["path"], code) != root:
        raise CicdV2Error(code)
    _integer(identity["device"], code, low=0)
    _integer(identity["inode"], code, low=0)
    _ref(obj["owner_ref"], code)
    return obj


def _workareas(source: object, verification: object, code: str) -> None:
    """Separate development and validation workareas with distinct identities."""
    first, second = _target(source, code), _target(verification, code)
    one = cast(dict[str, object], first["filesystem_identity"])
    two = cast(dict[str, object], second["filesystem_identity"])
    if first["root"] == second["root"] or (one["device"], one["inode"]) == (
        two["device"],
        two["inode"],
    ):
        raise CicdV2Error(code)


def validate_anchor(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-anchor/v1`: the operator-approved empty-delta start."""
    code = "invalid_anchor"
    obj = _record(
        value,
        (
            "format",
            "repository",
            "anchor_head",
            "tree_ref",
            "source_target",
            "verification_target",
            "config_ref",
            "authority_ref",
            "initialized_at",
        ),
        ANCHOR,
        code,
    )
    _repository(obj["repository"], code)
    _revision(obj["anchor_head"], code)
    _ref(obj["tree_ref"], code)
    _workareas(obj["source_target"], obj["verification_target"], code)
    _ref(obj["config_ref"], code)
    _ref(obj["authority_ref"], code)
    _time(obj["initialized_at"], code)
    return obj


_CANDIDATE_FIELDS = (
    "format",
    "candidate_id",
    "origin_refs",
    "repository",
    "anchor_head",
    "logical_base_id",
    "prior_candidate_id",
    "base_tree_sha256",
    "candidate_tree_sha256",
    "delta_ref",
    "tree_manifest_ref",
    "before_manifest_ref",
    "source_target",
    "verification_target",
    "config_ref",
    "assessment_ref",
)


def candidate_identity(value: object) -> str:
    """SHA-256 of the canonical candidate fields other than `candidate_id`."""
    obj = _fields(value, _CANDIDATE_FIELDS, "invalid_candidate")
    return digest({key: item for key, item in obj.items() if key != "candidate_id"})


def validate_candidate(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-candidate/v1`: provenance, never permission to write.

    Only the anchor candidate (no predecessor, equal trees and logical base)
    may omit assessment evidence; consumers verify that its origin pins the
    anchor record and its delta is empty.
    """
    code = "invalid_candidate"
    obj = _record(value, _CANDIDATE_FIELDS, CANDIDATE, code)
    identifier = _sha(obj["candidate_id"], code)
    _refs(obj["origin_refs"], code, nonempty=True)
    _repository(obj["repository"], code)
    _revision(obj["anchor_head"], code)
    logical = _sha(obj["logical_base_id"], code)
    prior = _optional_sha(obj["prior_candidate_id"], code)
    base = _sha(obj["base_tree_sha256"], code)
    tree = _sha(obj["candidate_tree_sha256"], code)
    # The base tree is the accepted cumulative tree before this job.
    if prior == identifier or base != logical:
        raise CicdV2Error(code)
    for key in ("delta_ref", "tree_manifest_ref", "before_manifest_ref", "config_ref"):
        _ref(obj[key], code)
    _workareas(obj["source_target"], obj["verification_target"], code)
    if _optional_ref(obj["assessment_ref"], code) is None and (
        prior is not None or tree != base
    ):
        raise CicdV2Error("candidate_assessment_required")
    if candidate_identity(obj) != identifier:
        raise CicdV2Error("candidate_identity_mismatch")
    return obj


def _entry(value: object, code: str) -> dict[str, object]:
    obj = _fields(value, _TREE_ENTRY, code)
    path = _relative(obj["path"], code)
    mode = obj["mode"]
    # Regular files only; gitlinks, links and special files are not entries,
    # and Git/owner structure is never source-tree content.
    if _structural_component(path):
        raise CicdV2Error(code)
    if type(mode) is not int or mode < 0 or not stat.S_ISREG(mode):
        raise CicdV2Error(code)
    content = _sha(obj["sha256"], code)
    # The retained payload is the exact entry bytes (spec 7.5 "exact retained
    # payloads"), so its ref pins the same content digest.
    if _ref(obj["payload_ref"], code)["sha256"] != content:
        raise CicdV2Error(code)
    return obj


def _structural_component(path: str) -> bool:
    return bool({".git", OWNER_MARKER} & set(PurePosixPath(path).parts))


def _link(value: object, code: str) -> dict[str, object]:
    """One opaque baseline link: lstat mode and exact raw readlink bytes."""
    obj = _fields(value, _LINK, code)
    path = _relative(obj["path"], code)
    mode = obj["mode"]
    if (
        _structural_component(path)
        or type(mode) is not int
        or mode < 0
        or not stat.S_ISLNK(mode)
    ):
        raise CicdV2Error(code)
    target = obj["target"]
    if not isinstance(target, str):
        raise CicdV2Error(code)
    try:
        raw = base64.b64decode(target.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise CicdV2Error(code) from exc
    if base64.b64encode(raw).decode("ascii") != target:
        raise CicdV2Error(code)
    if hashlib.sha256(raw).hexdigest() != _sha(obj["sha256"], code):
        raise CicdV2Error(code)
    return obj


def _manifest(entries_value: object, links_value: object, code: str) -> None:
    entries = [_entry(item, code) for item in _list(entries_value, code)]
    links = [_link(item, code) for item in _list(links_value, code)]
    paths = [cast(str, entry["path"]) for entry in entries]
    link_paths = [cast(str, link["path"]) for link in links]
    _sorted_unique(paths, code)
    _sorted_unique(link_paths, code)
    # No entry or link may sit at or beneath a baseline link (no write-through).
    for link in link_paths:
        if any(_within(path, link) for path in paths) or any(
            other != link and _within(other, link) for other in link_paths
        ):
            raise CicdV2Error(code)


def validate_tree(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-tree/v1`; its canonical SHA is the tree digest."""
    code = "invalid_tree"
    obj = _record(
        value,
        (
            "format",
            "repository",
            "anchor_head",
            "path_policy_ref",
            "entries",
            "baseline_links",
        ),
        TREE,
        code,
    )
    _repository(obj["repository"], code)
    _revision(obj["anchor_head"], code)
    _ref(obj["path_policy_ref"], code)
    _manifest(obj["entries"], obj["baseline_links"], code)
    return obj


def tree_digest(value: object) -> str:
    """Canonical whole-manifest SHA-256, including baseline links."""
    return digest(validate_tree(value))


def validate_delta(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-delta/v1`; empty exactly when the trees are equal."""
    code = "invalid_delta"
    obj = _record(
        value,
        ("format", "base_tree_sha256", "candidate_tree_sha256", "changes"),
        DELTA,
        code,
    )
    base = _sha(obj["base_tree_sha256"], code)
    tree = _sha(obj["candidate_tree_sha256"], code)
    paths: list[str] = []
    for item in _list(obj["changes"], code):
        change = _fields(item, ("path", "before", "after"), code)
        path = _relative(change["path"], code)
        sides = [change["before"], change["after"]]
        if all(side is None for side in sides) or sides[0] == sides[1]:
            raise CicdV2Error(code)
        for side in sides:
            if side is not None and _entry(side, code)["path"] != path:
                raise CicdV2Error(code)
        paths.append(path)
    _sorted_unique(paths, code)
    if (base == tree) != (not paths):
        raise CicdV2Error(code)
    return obj


def validate_before(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-before/v1`: exact task-start source facts."""
    code = "invalid_before"
    obj = _record(
        value,
        (
            "format",
            "target",
            "anchor_head",
            "logical_base_id",
            "path_policy_ref",
            "environment_ref",
            "source_sha256",
            "foreign_dirty_paths",
            "entries",
            "baseline_links",
        ),
        BEFORE,
        code,
    )
    _target(obj["target"], code)
    _revision(obj["anchor_head"], code)
    _sha(obj["logical_base_id"], code)
    _ref(obj["path_policy_ref"], code)
    _ref(obj["environment_ref"], code)
    _sha(obj["source_sha256"], code)
    _paths(obj["foreign_dirty_paths"], code)
    _manifest(obj["entries"], obj["baseline_links"], code)
    return obj


def validate_export_basis(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-export-basis/v1`; pins a stopped source writer."""
    code = "invalid_export_basis"
    obj = _record(
        value,
        (
            "format",
            "subject_ref",
            "source_target",
            "verification_target",
            "anchor_head",
            "logical_base_id",
            "prior_candidate_ref",
            "before_manifest_ref",
            "source_after_manifest_ref",
            "config_ref",
            "assessment_ref",
            "authority_ref",
            "writer_facts",
        ),
        EXPORT_BASIS,
        code,
    )
    _ref(obj["subject_ref"], code)
    _workareas(obj["source_target"], obj["verification_target"], code)
    _revision(obj["anchor_head"], code)
    _sha(obj["logical_base_id"], code)
    prior = _optional_ref(obj["prior_candidate_ref"], code)
    for key in ("before_manifest_ref", "source_after_manifest_ref", "config_ref"):
        _ref(obj[key], code)
    # Only the anchor bootstrap omits both predecessor and assessment; every
    # non-anchor export needs exact prior candidate evidence (spec 7.5).
    if (_optional_ref(obj["assessment_ref"], code) is None) != (prior is None):
        raise CicdV2Error(code)
    _ref(obj["authority_ref"], code)
    if _writer(obj["writer_facts"], code)["writer_state"] != "stopped":
        raise CicdV2Error("writer_not_stopped")
    return obj


# Candidate facade requests, results, projections and the materialization lease.


def validate_candidate_request(
    value: object, *, operation: str | None = None
) -> dict[str, object]:
    """`millrace-plus-cicd-candidate-request/v1` with operation-dependent nulls.

    `operation` is the CLI argument; a different recorded operation refuses.
    """
    code = "invalid_candidate_request"
    obj = _record(
        value,
        (
            "format",
            "request_id",
            "operation",
            "candidate_ref",
            "subject_ref",
            "export_basis_ref",
            "role",
            "materialization_id",
            "expected_materialization_version",
            "authority_ref",
            "approval_ref",
            "command_id",
            "role_binding_id",
            "expected_target_sha256",
            "evidence_refs",
        ),
        CANDIDATE_REQUEST,
        code,
    )
    _text(obj["request_id"], code)
    op = _enum(obj["operation"], CANDIDATE_OPERATIONS, code)
    if operation is not None and operation != op:
        raise CicdV2Error("operation_mismatch")
    candidate = _optional_ref(obj["candidate_ref"], code)
    if (candidate is None) != (op == "export") and op != "project-role":
        raise CicdV2Error(code)
    subject = _optional_ref(obj["subject_ref"], code)
    if (subject is not None) != (op in {"export", "project-role"}):
        raise CicdV2Error(code)
    if (_optional_ref(obj["export_basis_ref"], code) is not None) != (op == "export"):
        raise CicdV2Error(code)
    role = obj["role"]
    if op == "project-role":
        _enum(role, ROLES, code)
    elif role is not None:
        raise CicdV2Error(code)
    leased = _paired(
        obj["materialization_id"], obj["expected_materialization_version"], code
    )
    if leased:
        _text(obj["materialization_id"], code)
        _positive(obj["expected_materialization_version"], code)
    # Only the lease-creating prepare-base, investigation projection and a
    # pre-lease (LAD) export may omit the current lease identity.
    optional_lease = op in {"prepare-base", "export"} or (
        op == "project-role" and role == "investigate"
    )
    if not leased and not optional_lease:
        raise CicdV2Error(code)
    authorized = _paired(obj["authority_ref"], obj["approval_ref"], code)
    if authorized:
        _ref(obj["authority_ref"], code)
        _ref(obj["approval_ref"], code)
    writes = op in TARGET_WRITING_OPERATIONS or (
        op == "project-role" and role == "repair"
    )
    if (writes and not authorized) or (op == "verify" and authorized):
        raise CicdV2Error(code)
    if (_optional_text(obj["command_id"], code) is not None) != (op == "check"):
        raise CicdV2Error(code)
    binding = _optional_text(obj["role_binding_id"], code)
    if (binding is not None) != (op in {"review", "project-role"}):
        raise CicdV2Error(code)
    expected = _optional_sha(obj["expected_target_sha256"], code)
    if (expected is None) != (op == "export"):
        raise CicdV2Error(code)
    _refs(obj["evidence_refs"], code)
    return obj


def validate_candidate_result(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-candidate-result/v1`; never invents a future candidate.

    A target-writing receipt cannot complete while its writer state is unknown;
    a completed verify is read-only toward its target.
    """
    code = "invalid_candidate_result"
    obj = _record(
        value,
        (
            "format",
            "request_id",
            "request_sha256",
            "operation",
            "candidate_id",
            "materialization_id",
            "materialization_version",
            "status",
            "target_before_sha256",
            "target_after_sha256",
            "artifact_refs",
            "writer_facts",
            "refusal",
        ),
        CANDIDATE_RESULT,
        code,
    )
    _text(obj["request_id"], code)
    _sha(obj["request_sha256"], code)
    op = _enum(obj["operation"], CANDIDATE_OPERATIONS, code)
    _optional_sha(obj["candidate_id"], code)
    if _paired(obj["materialization_id"], obj["materialization_version"], code):
        _text(obj["materialization_id"], code)
        _positive(obj["materialization_version"], code)
    status = _enum(obj["status"], RESULT_STATUSES, code)
    before = _optional_sha(obj["target_before_sha256"], code)
    after = _optional_sha(obj["target_after_sha256"], code)
    _refs(obj["artifact_refs"], code)
    writer = _writer(obj["writer_facts"], code)
    refusal = _optional_text(obj["refusal"], code)
    if (status == "completed" and refusal is not None) or (
        status == "refused" and refusal is None
    ):
        raise CicdV2Error(code)
    if status == "completed":
        if op in TARGET_WRITING_OPERATIONS and writer["writer_state"] == "unknown":
            raise CicdV2Error("writer_unknown")
        if op == "verify" and before != after:
            raise CicdV2Error(code)
    return obj


def validate_role_basis(value: object, *, role: str | None = None) -> dict[str, object]:
    """`millrace-plus-cicd-role-basis/v1`; with `role="repair"` scope is required.

    Repair needs accepted assessment and scope evidence, plus reproduction
    unless a genuine task pins its criteria.
    """
    code = "invalid_role_basis"
    obj = _record(
        value,
        (
            "format",
            "subject_schema_ref",
            "source_binding_ref",
            "source_before_sha256",
            "config_sha256",
            "assessment_ref",
            "reproduction_refs",
            "criteria_ref",
            "scope_ref",
            "public_writer_refs",
        ),
        ROLE_BASIS,
        code,
    )
    _ref(obj["subject_schema_ref"], code)
    _ref(obj["source_binding_ref"], code)
    _sha(obj["source_before_sha256"], code)
    _sha(obj["config_sha256"], code)
    assessment = _optional_ref(obj["assessment_ref"], code)
    reproduction = _refs(obj["reproduction_refs"], code)
    criteria = _optional_ref(obj["criteria_ref"], code)
    scope = _optional_ref(obj["scope_ref"], code)
    _refs(obj["public_writer_refs"], code)
    if role is not None:
        _enum(role, ROLES, code)
    if role == "repair" and (
        assessment is None or scope is None or (not reproduction and criteria is None)
    ):
        raise CicdV2Error("repair_basis_incomplete")
    return obj


def validate_active_role(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-active-role/v2`: the atomic plane-workspace pointer."""
    code = "invalid_active_role"
    obj = _record(
        value,
        (
            "format",
            "subject_id",
            "role",
            "attempt_id",
            "input_ref",
            "projection_ref",
            "basis_sha256",
        ),
        ACTIVE_ROLE,
        code,
    )
    _text(obj["subject_id"], code)
    _enum(obj["role"], (*ROLES, "review"), code)
    _text(obj["attempt_id"], code)
    _ref(obj["input_ref"], code)
    _ref(obj["projection_ref"], code)
    _sha(obj["basis_sha256"], code)
    return obj


def validate_subject_projection(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-subject-projection/v1`; dispatches no worker."""
    code = "invalid_subject_projection"
    obj = _record(
        value,
        (
            "format",
            "subject_ref",
            "subject_kind",
            "subject_id",
            "role",
            "attempt_id",
            "binding_id",
            "input_ref",
            "prompt_ref",
            "skill_ref",
            "result_schema_ref",
            "basis_sha256",
            "criteria_ref",
            "source_binding_ref",
        ),
        SUBJECT_PROJECTION,
        code,
    )
    _ref(obj["subject_ref"], code)
    kind = _enum(obj["subject_kind"], SUBJECT_KINDS, code)
    _text(obj["subject_id"], code)
    _enum(obj["role"], ROLES, code)
    _text(obj["attempt_id"], code)
    _text(obj["binding_id"], code)
    _ref(obj["input_ref"], code)
    _optional_ref(obj["prompt_ref"], code)
    _optional_ref(obj["skill_ref"], code)
    _ref(obj["result_schema_ref"], code)
    _sha(obj["basis_sha256"], code)
    # Criteria pins an original factory task only.
    if _optional_ref(obj["criteria_ref"], code) is not None and kind != "factory_input":
        raise CicdV2Error(code)
    _ref(obj["source_binding_ref"], code)
    return obj


def validate_review_projection(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-review-projection/v1`; prepared is never reviewer ACCEPT."""
    code = "invalid_review_projection"
    obj = _record(
        value,
        (
            "format",
            "candidate_id",
            "materialization_id",
            "attempt_id",
            "binding_id",
            "input_ref",
            "prompt_ref",
            "skill_ref",
            "result_schema_ref",
            "basis_sha256",
        ),
        REVIEW_PROJECTION,
        code,
    )
    _sha(obj["candidate_id"], code)
    _text(obj["materialization_id"], code)
    _text(obj["attempt_id"], code)
    _text(obj["binding_id"], code)
    _ref(obj["input_ref"], code)
    _optional_ref(obj["prompt_ref"], code)
    _optional_ref(obj["skill_ref"], code)
    _ref(obj["result_schema_ref"], code)
    _sha(obj["basis_sha256"], code)
    return obj


def validate_materialization(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-materialization/v1`: the one lease per validation target.

    The final candidate is null during base preparation and native repair, bound
    once at seal and retained by restoration.
    """
    code = "invalid_materialization"
    obj = _record(
        value,
        (
            "format",
            "materialization_id",
            "version",
            "origin_base_candidate_ref",
            "candidate_ref",
            "request_sha256",
            "owner_ref",
            "target_before_sha256",
            "target_after_sha256",
            "beforeimages_ref",
            "operation_refs",
            "state",
            "restoration_ref",
        ),
        MATERIALIZATION,
        code,
    )
    _text(obj["materialization_id"], code)
    _positive(obj["version"], code)
    _ref(obj["origin_base_candidate_ref"], code)
    candidate = _optional_ref(obj["candidate_ref"], code)
    _sha(obj["request_sha256"], code)
    _ref(obj["owner_ref"], code)
    _sha(obj["target_before_sha256"], code)
    _optional_sha(obj["target_after_sha256"], code)
    state = _enum(obj["state"], MATERIALIZATION_STATES, code)
    if _optional_ref(obj["beforeimages_ref"], code) is None and state != "reserved":
        raise CicdV2Error(code)
    _refs(obj["operation_refs"], code)
    restoration = _optional_ref(obj["restoration_ref"], code)
    if state in {"reserved", "base_ready", "repair_reserved"} and candidate is not None:
        raise CicdV2Error(code)
    if state == "sealed" and candidate is None:
        raise CicdV2Error(code)
    if (state == "restored") != (restoration is not None):
        raise CicdV2Error(code)
    return obj


def validate_materialization_operation(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-materialization-operation/v1`: one append-only receipt.

    Only the initial reservation lacks a previous receipt; later observations
    link it. After tree, completion and restoration stay null until known.
    """
    code = "invalid_materialization_operation"
    obj = _record(
        value,
        (
            "format",
            "operation_id",
            "materialization_id",
            "operation",
            "request_sha256",
            "before_tree_sha256",
            "after_tree_sha256",
            "beforeimages_ref",
            "allowed_writes_ref",
            "write_intent_ref",
            "completion_ref",
            "previous_receipt_ref",
            "status",
            "restoration_ref",
        ),
        MATERIALIZATION_OPERATION,
        code,
    )
    _text(obj["operation_id"], code)
    _text(obj["materialization_id"], code)
    _enum(obj["operation"], LAYER_OPERATIONS, code)
    _sha(obj["request_sha256"], code)
    _sha(obj["before_tree_sha256"], code)
    after = _optional_sha(obj["after_tree_sha256"], code)
    for key in ("beforeimages_ref", "allowed_writes_ref", "write_intent_ref"):
        _ref(obj[key], code)
    completion = _optional_ref(obj["completion_ref"], code)
    previous = _optional_ref(obj["previous_receipt_ref"], code)
    status = _enum(obj["status"], LAYER_STATUSES, code)
    restoration = _optional_ref(obj["restoration_ref"], code)
    if (status == "reserved") != (previous is None):
        raise CicdV2Error(code)
    if status == "reserved" and (after is not None or completion is not None):
        raise CicdV2Error(code)
    if status in {"applied", "restored"} and (after is None or completion is None):
        raise CicdV2Error(code)
    if (status == "restored") != (restoration is not None):
        raise CicdV2Error(code)
    return obj


def validate_candidate_verification(value: object) -> dict[str, object]:
    """`millrace-plus-cicd-candidate-verification/v1`; refusal null only if verified."""
    code = "invalid_candidate_verification"
    obj = _record(
        value,
        (
            "format",
            "candidate_id",
            "materialization_id",
            "config_sha256",
            "observed_head",
            "observed_source_sha256",
            "checks_refs",
            "review_refs",
            "status",
            "evidence_refs",
            "refusal",
        ),
        CANDIDATE_VERIFICATION,
        code,
    )
    _sha(obj["candidate_id"], code)
    _text(obj["materialization_id"], code)
    _sha(obj["config_sha256"], code)
    head = _optional_revision(obj["observed_head"], code)
    source = _optional_sha(obj["observed_source_sha256"], code)
    checks = _refs(obj["checks_refs"], code)
    reviews = _refs(obj["review_refs"], code)
    status = _enum(obj["status"], VERIFICATION_STATUSES, code)
    _refs(obj["evidence_refs"], code)
    refusal = _optional_text(obj["refusal"], code)
    if (status == "verified") != (refusal is None):
        raise CicdV2Error(code)
    if status == "verified" and (
        head is None or source is None or not checks or not reviews
    ):
        raise CicdV2Error(code)
    return obj


FORMATS: dict[str, Callable[[object], dict[str, object]]] = {
    CONFIG: validate_config,
    FAILURE_PATTERNS: validate_failure_patterns,
    INPUT: validate_input,
    ATTEMPT: validate_attempt,
    ASSESSMENT: validate_assessment,
    REPAIR: validate_repair,
    CHECK: validate_check,
    REVIEW: validate_review,
    STATE: validate_state,
    CHECK_PLAN: validate_check_plan,
    REVIEW_PLAN: validate_review_plan,
    DECISION: validate_decision,
    REPORT: validate_report,
    LOCAL_AUTHORITY: validate_local_authority,
    ACTOR_BINDING: validate_actor_binding,
    SOURCE_BINDING: validate_source_binding,
    BASE_PREPARATION: validate_base_preparation,
    ANCHOR: validate_anchor,
    CANDIDATE: validate_candidate,
    TREE: validate_tree,
    DELTA: validate_delta,
    BEFORE: validate_before,
    EXPORT_BASIS: validate_export_basis,
    CANDIDATE_REQUEST: validate_candidate_request,
    CANDIDATE_RESULT: validate_candidate_result,
    ROLE_BASIS: validate_role_basis,
    ACTIVE_ROLE: validate_active_role,
    SUBJECT_PROJECTION: validate_subject_projection,
    REVIEW_PROJECTION: validate_review_projection,
    MATERIALIZATION: validate_materialization,
    MATERIALIZATION_OPERATION: validate_materialization_operation,
    CANDIDATE_VERIFICATION: validate_candidate_verification,
}


def parse_record(raw: bytes, fmt: str) -> dict[str, object]:
    """Strict-parse exact bytes and validate them as the expected format.

    Duplicate keys, nonfinite numbers and invalid UTF-8 refuse `invalid_json`;
    an unselected format refuses `unknown_format`. Validators with optional
    cross-check arguments run here with their defaults.
    """
    if not isinstance(fmt, str) or fmt not in FORMATS:
        raise CicdV2Error("unknown_format")
    with _shared():
        value = parse(raw)
    return FORMATS[fmt](value)
