"""Strict CI/CD configuration, event, artifact and report records. No execution."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast

from .local_intake import canonical, strict_json

__all__ = [
    "CicdError",
    "validate_config",
    "validate_event",
    "event_identity",
    "intake_key",
    "event_from_key",
    "read_bounded",
    "relative_path",
    "validate_diagnosis",
    "validate_repair",
    "validate_review",
    "validate_check",
    "validate_report",
    "digest",
    "sha256_text",
]

CONFIG = "millrace-plus-cicd-config/v1"
EVENT = "millrace-plus-cicd-event/v1"
DIAGNOSIS = "millrace-plus-cicd-diagnosis/v1"
REPAIR = "millrace-plus-cicd-repair/v1"
REVIEW = "millrace-plus-cicd-review/v1"
CHECK = "millrace-plus-cicd-check/v1"
REPORT = "millrace-plus-cicd-report/v1"

MAX_EVENT = 65536
MAX_LOG = 131072
MAX_TEXT = 4096
MAX_REFS = 32
# Per-event small-repair shape. These are not run, cost or invocation ceilings.
SMALL_FILES = 3
SMALL_LINES = 200
CHECK_BUDGET = 3600

SHA = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
CHECK_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9._-]{1,100}\Z")
SOURCES = ("github-actions", "generic-ci")
CONCLUSIONS = ("success", "failure", "timed_out", "cancelled", "skipped")
DIAGNOSES = (
    "SMALL_REPAIR",
    "LARGE_ISSUE",
    "UNREPRODUCED",
    "EXTERNAL_BLOCKER",
    "NO_ACTION",
)
VERDICTS = ("ACCEPT", "REJECT", "BLOCKED")
CHECK_STATUSES = ("passed", "failed", "timeout", "uncertain")
REPORT_STATUSES = (
    "no_action",
    "held_large",
    "held_unreproduced",
    "held_external",
    "held_repair",
    "checked_patch",
    "published",
    "cancelled",
    "uncertain",
)


class CicdError(Exception):
    """Expected refusal with a stable code; retained state stays inspectable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _fields(value: object, names: set[str], code: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != names:
        raise CicdError(code)
    return cast(dict[str, object], value)


def _text(value: object, code: str, *, maximum: int | None = MAX_TEXT) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or (maximum is not None and len(value) > maximum)
        or "\0" in value
    ):
        raise CicdError(code)
    return value


def _identifier(value: object, code: str, *, maximum: int | None = 256) -> str:
    text = _text(value, code, maximum=maximum)
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise CicdError(code)
    return text


def sha256_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise CicdError(code)
    return value


_sha = sha256_text


def _integer(value: object, code: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise CicdError(code)
    return value


def relative_path(value: object, code: str = "invalid_path") -> str:
    """A normalized contained POSIX path; never absolute, dotted or empty."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or "\0" in value
        or "\\" in value
    ):
        raise CicdError(code)
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise CicdError(code)
    return value


def _paths(value: object, code: str, *, low: int, high: int) -> list[str]:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise CicdError(code)
    paths = [relative_path(item, code) for item in value]
    if len(set(paths)) != len(paths):
        raise CicdError(code)
    return paths


def read_bounded(path: Path, limit: int, code: str) -> bytes:
    """Read one regular unlinked-alias file without following its final link."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CicdError(code) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CicdError(code)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) > limit:
        raise CicdError(code)
    return raw


def _json(raw: bytes, code: str) -> object:
    try:
        return strict_json(raw)
    except Exception as exc:
        raise CicdError(code) from exc


# Fixed protected set. Operator protected paths add to it; events cannot remove it.
_PROTECTED_NAMES = frozenset(
    {
        ".git",
        ".millrace-plus-target-owner.json",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".gitlab-ci.yml",
        ".circleci",
        ".github",
        "Jenkinsfile",
        # Dependency manifests and lockfiles.
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
        # Schema/data migrations and deploy configuration.
        "migrations",
        "alembic",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }
)
_PROTECTED_PATTERNS = (
    re.compile(r"\.env(?:\..*)?\Z"),
    re.compile(r"requirements.*\.txt\Z"),
    re.compile(r".*\.(?:pem|key|p12|pfx)\Z"),
    re.compile(r"id_(?:rsa|dsa|ecdsa|ed25519).*\Z"),
    re.compile(r".*(?:credential|secret).*\Z", re.IGNORECASE),
)


def fixed_protected(path: str) -> bool:
    for part in PurePosixPath(path).parts:
        if part in _PROTECTED_NAMES or any(
            pattern.fullmatch(part) for pattern in _PROTECTED_PATTERNS
        ):
            return True
    return False


def within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def protected(path: str, config: dict[str, object]) -> bool:
    """True when stock repair must not touch this target-relative path."""
    return fixed_protected(path) or any(
        within(path, str(item)) for item in cast(list[str], config["protected_paths"])
    )


def repairable(path: str, config: dict[str, object]) -> bool:
    return not protected(path, config) and any(
        within(path, str(item)) for item in cast(list[str], config["repair_paths"])
    )


def _check(value: object) -> dict[str, object]:
    obj = _fields(
        value,
        {"id", "argv", "cwd", "timeout_seconds", "expected_exit_code"},
        "invalid_check_command",
    )
    if not isinstance(obj["id"], str) or not CHECK_ID.fullmatch(obj["id"]):
        raise CicdError("invalid_check_command")
    argv = obj["argv"]
    if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
        raise CicdError("invalid_check_command")
    for item in argv:
        _text(item, "invalid_check_command")
    if obj["cwd"] != ".":
        relative_path(obj["cwd"], "invalid_check_command")
    _integer(obj["timeout_seconds"], "invalid_check_command", 1, 3600)
    _integer(obj["expected_exit_code"], "invalid_check_command", 0, 255)
    return obj


def validate_config(raw: bytes) -> dict[str, object]:
    """Validate exact operator bytes. Only hold-mode external policy is supported."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_EVENT:
        raise CicdError("invalid_config")
    obj = _fields(
        _json(raw, "invalid_config"),
        {
            "format",
            "target_root",
            "repository",
            "repair_paths",
            "protected_paths",
            "check_commands",
            "repair_binding",
            "external_policy",
        },
        "invalid_config",
    )
    if obj["format"] != CONFIG:
        raise CicdError("invalid_config")
    target = _text(obj["target_root"], "invalid_config", maximum=1024)
    if not Path(target).is_absolute() or os.path.normpath(target) != target:
        raise CicdError("invalid_config")
    if not isinstance(obj["repository"], str) or not REPOSITORY.fullmatch(
        obj["repository"]
    ):
        raise CicdError("invalid_config")
    repair = _paths(obj["repair_paths"], "invalid_repair_paths", low=1, high=32)
    if any(fixed_protected(path) for path in repair):
        raise CicdError("invalid_repair_paths")
    _paths(obj["protected_paths"], "invalid_protected_paths", low=0, high=64)
    checks = obj["check_commands"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 16:
        raise CicdError("invalid_check_command")
    ids = [_check(item)["id"] for item in checks]
    # The stock checks node has one finite deadline covering the whole batch.
    total = sum(cast(int, item["timeout_seconds"]) for item in checks)
    if len(set(ids)) != len(ids) or total > CHECK_BUDGET:
        raise CicdError("invalid_check_command")
    binding = _fields(
        obj["repair_binding"], {"adapter", "executable"}, "invalid_repair_binding"
    )
    executable = _text(binding["executable"], "invalid_repair_binding", maximum=1024)
    if (
        binding["adapter"] != "command"
        or not Path(executable).is_absolute()
        or os.path.normpath(executable) != executable
    ):
        raise CicdError("invalid_repair_binding")
    policy = obj["external_policy"]
    if isinstance(policy, dict) and policy.get("mode") == "authorized":
        # C3 external effects are not part of this hold-only slice.
        raise CicdError("external_mode_unsupported")
    if policy != {"mode": "hold", "effects": []}:
        raise CicdError("invalid_external_policy")
    return obj


def _timestamp(value: object, *, legacy_limits: bool = True) -> str:
    text = _identifier(value, "invalid_event", maximum=256 if legacy_limits else None)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CicdError("invalid_event") from exc
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise CicdError("invalid_event")
    return text


def validate_event(raw: bytes, repository: str) -> dict[str, object]:
    """Validate one bounded event object. Its text is evidence, never authority."""
    return _validate_event(raw, repository, legacy_limits=True)


def _validate_event(
    raw: bytes, repository: str, *, legacy_limits: bool
) -> dict[str, object]:
    """Shared exact event shape; only new v2 homes supply their captured byte guards."""
    if not isinstance(raw, bytes) or not raw or legacy_limits and len(raw) > MAX_EVENT:
        raise CicdError("invalid_event_size")
    obj = _fields(
        _json(raw, "invalid_event"),
        {
            "format",
            "source",
            "repository",
            "run_id",
            "attempt",
            "job_id",
            "head_sha",
            "conclusion",
            "summary",
            "log",
            "occurred_at",
        },
        "invalid_event",
    )
    if obj["format"] != EVENT or obj["source"] not in SOURCES:
        raise CicdError("invalid_event")
    if obj["repository"] != repository:
        raise CicdError("repository_mismatch")
    _identifier(obj["run_id"], "invalid_event", maximum=256 if legacy_limits else None)
    _identifier(obj["job_id"], "invalid_event", maximum=256 if legacy_limits else None)
    if legacy_limits:
        _integer(obj["attempt"], "invalid_event", 1, 2**31 - 1)
    elif type(obj["attempt"]) is not int or obj["attempt"] < 1:
        raise CicdError("invalid_event")
    if not isinstance(obj["head_sha"], str) or not REVISION.fullmatch(obj["head_sha"]):
        raise CicdError("invalid_event")
    if obj["conclusion"] not in CONCLUSIONS:
        raise CicdError("invalid_event")
    _text(obj["summary"], "invalid_event", maximum=MAX_TEXT if legacy_limits else None)
    if obj["log"] is not None:
        log = _fields(obj["log"], {"path", "sha256"}, "invalid_event_log")
        if legacy_limits:
            relative_path(log["path"], "invalid_event_log")
        else:
            from .check_contracts import CheckContractError, validate_ref

            try:
                validate_ref(log)
            except CheckContractError as exc:
                raise CicdError("invalid_event_log") from exc
        _sha(log["sha256"], "invalid_event_log")
    _timestamp(obj["occurred_at"], legacy_limits=legacy_limits)
    return obj


def event_identity(event: dict[str, object]) -> str:
    return digest(
        {
            name: event[name]
            for name in (
                "source",
                "repository",
                "run_id",
                "attempt",
                "job_id",
                "head_sha",
            )
        }
    )


def intake_key(event_id: str) -> str:
    """Injective storage key: the full 32-byte identity, never a truncation."""
    _sha(event_id, "invalid_event_id")
    encoded = base64.urlsafe_b64encode(bytes.fromhex(event_id)).decode().rstrip("=")
    return "ci_" + encoded


def event_from_key(key: str) -> str:
    if not isinstance(key, str) or not key.startswith("ci_") or len(key) != 46:
        raise CicdError("invalid_intake_key")
    try:
        raw = base64.urlsafe_b64decode(key[3:] + "=")
    except (ValueError, TypeError) as exc:
        raise CicdError("invalid_intake_key") from exc
    if len(raw) != 32 or intake_key(raw.hex()) != key:
        raise CicdError("invalid_intake_key")
    return raw.hex()


def read_log(event_file: Path, event: dict[str, object]) -> bytes | None:
    """Read the referenced log inside the event directory, exact digest only."""
    if event["log"] is None:
        return None
    log = cast(dict[str, object], event["log"])
    base = event_file.parent
    current = base
    for part in PurePosixPath(str(log["path"])).parts:
        current = current / part
        if current.is_symlink():
            raise CicdError("invalid_event_log")
    if not current.resolve().is_relative_to(base.resolve()):
        raise CicdError("invalid_event_log")
    raw = read_bounded(current, MAX_LOG, "invalid_event_log")
    try:
        raw.decode("utf-8")
    except UnicodeError as exc:
        raise CicdError("invalid_event_log") from exc
    if hashlib.sha256(raw).hexdigest() != log["sha256"]:
        raise CicdError("event_log_digest_mismatch")
    return raw


def _refs(value: object, code: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > MAX_REFS:
        raise CicdError(code)
    refs = [_fields(item, {"path", "sha256"}, code) for item in value]
    for ref in refs:
        relative_path(ref["path"], code)
        _sha(ref["sha256"], code)
    return refs


def ref(value: object, code: str) -> dict[str, object]:
    return _refs([value], code)[0]


def validate_diagnosis(
    value: object, event_id: str, head_sha: str
) -> dict[str, object]:
    obj = _fields(
        value,
        {
            "format",
            "event_id",
            "head_sha",
            "classification",
            "cause",
            "proposed_paths",
            "repair_summary",
            "evidence_refs",
        },
        "invalid_diagnosis",
    )
    if (
        obj["format"] != DIAGNOSIS
        or obj["event_id"] != event_id
        or obj["head_sha"] != head_sha
        or obj["classification"] not in DIAGNOSES
    ):
        raise CicdError("invalid_diagnosis")
    _text(obj["cause"], "invalid_diagnosis")
    paths = _paths(obj["proposed_paths"], "invalid_diagnosis", low=0, high=32)
    if obj["repair_summary"] is not None:
        _text(obj["repair_summary"], "invalid_diagnosis")
    refs = _refs(obj["evidence_refs"], "invalid_diagnosis")
    if obj["classification"] == "SMALL_REPAIR" and (
        not paths or obj["repair_summary"] is None or not refs
    ):
        raise CicdError("unsupported_small_repair")
    return obj


def validate_repair(
    value: object, event_id: str, config_sha256: str, before: str
) -> dict[str, object]:
    obj = _fields(
        value,
        {
            "format",
            "event_id",
            "config_sha256",
            "source_before_sha256",
            "source_after_sha256",
            "changed_paths",
            "summary",
            "evidence_refs",
        },
        "invalid_repair",
    )
    if (
        obj["format"] != REPAIR
        or obj["event_id"] != event_id
        or obj["config_sha256"] != config_sha256
        or obj["source_before_sha256"] != before
    ):
        raise CicdError("invalid_repair")
    _sha(obj["source_after_sha256"], "invalid_repair")
    _paths(obj["changed_paths"], "invalid_repair", low=1, high=SMALL_FILES)
    _text(obj["summary"], "invalid_repair")
    _refs(obj["evidence_refs"], "invalid_repair")
    return obj


def validate_review(
    value: object,
    event_id: str,
    config_sha256: str,
    patch_sha256: str,
    checks_ref: dict[str, object],
) -> dict[str, object]:
    obj = _fields(
        value,
        {
            "format",
            "event_id",
            "config_sha256",
            "patch_sha256",
            "checks_ref",
            "verdict",
            "findings",
        },
        "invalid_review",
    )
    if (
        obj["format"] != REVIEW
        or obj["event_id"] != event_id
        or obj["config_sha256"] != config_sha256
        or obj["patch_sha256"] != patch_sha256
        or ref(obj["checks_ref"], "invalid_review") != checks_ref
        or obj["verdict"] not in VERDICTS
    ):
        raise CicdError("invalid_review")
    findings = obj["findings"]
    if not isinstance(findings, list) or len(findings) > MAX_REFS:
        raise CicdError("invalid_review")
    for item in findings:
        finding = _fields(item, {"id", "text", "evidence_refs"}, "invalid_review")
        _identifier(finding["id"], "invalid_review")
        _text(finding["text"], "invalid_review")
        _refs(finding["evidence_refs"], "invalid_review")
    if obj["verdict"] == "ACCEPT" and findings:
        raise CicdError("review_unresolved_findings")
    return obj


def validate_check(value: object) -> dict[str, object]:
    obj = _fields(
        value,
        {
            "format",
            "event_id",
            "check_id",
            "argv",
            "cwd",
            "source_sha256",
            "started_at",
            "finished_at",
            "exit_code",
            "status",
            "stdout_ref",
            "stderr_ref",
        },
        "invalid_check_receipt",
    )
    if obj["format"] != CHECK or obj["status"] not in CHECK_STATUSES:
        raise CicdError("invalid_check_receipt")
    _sha(obj["event_id"], "invalid_check_receipt")
    _sha(obj["source_sha256"], "invalid_check_receipt")
    if obj["exit_code"] is not None:
        _integer(obj["exit_code"], "invalid_check_receipt", -255, 255)
    _timestamp(obj["started_at"])
    _timestamp(obj["finished_at"])
    ref(obj["stdout_ref"], "invalid_check_receipt")
    ref(obj["stderr_ref"], "invalid_check_receipt")
    return obj


def validate_report(value: object) -> dict[str, object]:
    obj = _fields(
        value,
        {
            "format",
            "event_id",
            "head_sha",
            "config_sha256",
            "status",
            "diagnosis_ref",
            "patch_ref",
            "changed_paths",
            "checks",
            "review_ref",
            "external_effects",
            "hold_reason",
            "source_before_sha256",
            "source_after_sha256",
        },
        "invalid_report",
    )
    if obj["format"] != REPORT or obj["status"] not in REPORT_STATUSES:
        raise CicdError("invalid_report")
    _sha(obj["event_id"], "invalid_report")
    _sha(obj["config_sha256"], "invalid_report")
    for name in ("diagnosis_ref", "patch_ref", "review_ref"):
        if obj[name] is not None:
            ref(obj[name], "invalid_report")
    _paths(obj["changed_paths"], "invalid_report", low=0, high=20000)
    _refs(obj["checks"], "invalid_report")
    if obj["external_effects"] != []:
        raise CicdError("invalid_report")
    if obj["hold_reason"] is not None:
        _text(obj["hold_reason"], "invalid_report")
    for name in ("source_before_sha256", "source_after_sha256"):
        if obj[name] is not None:
            _sha(obj[name], "invalid_report")
    return obj
