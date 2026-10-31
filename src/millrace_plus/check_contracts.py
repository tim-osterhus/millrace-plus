"""Shared pure check contracts for CI v2, L-Q1 engineering feedback and the factory.

Strict validators and deterministic constructors for the records frozen by the
CI v2 specification section 7.2 ("Shared check execution, environment and
product policy") and reused unchanged by L-Q1 and the local software factory:

- `millrace-check-path-policy/v1` and path classification;
- the shared check execution object and its `execution_sha256`;
- `millrace-check-environment/v1` opaque environment observations;
- `millrace-check-products/v1` owned reversible product layers and their links;
- `millrace-check-completion-acceptance/v1`, one fixed tag per consumer;
- `millrace-check-command-map/v1`, exact L-Q1 check to CI command identity.

Everything here is pure: no filesystem, process, environment, clock or Core
access, and no dispatch. Consumers read and hash Ref targets, observe paths, run
commands, own their graph guards and translate refusals into their own records.
"""

from __future__ import annotations

import hashlib
import math
import re
import stat
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Protocol, cast

from .local_intake import IntakeError, canonical, strict_json

__all__ = [
    "CheckContractError",
    "PATH_POLICY",
    "ENVIRONMENT",
    "PRODUCTS",
    "COMPLETION_ACCEPTANCE",
    "COMMAND_MAP",
    "SURFACES",
    "CACHE_TOKENS",
    "EXECUTION_FIELDS",
    "PRODUCT_STATUSES",
    "ACCEPTANCE_KINDS",
    "FALLBACK_TIMEOUT_SECONDS",
    "TIMEOUT_DEFAULTED",
    "OWNER_MARKER",
    "PathPolicy",
    "parse",
    "encode",
    "make_ref",
    "validate_ref",
    "validate_path_policy",
    "stock_path_policy",
    "classify",
    "require_source",
    "validate_execution",
    "execution_of",
    "execution_sha256",
    "product_write_allowed",
    "resolve_timeout",
    "node_budget",
    "environment_entry",
    "validate_environment",
    "validate_products",
    "validate_product_transition",
    "validate_completion_acceptance",
    "validate_command_map",
    "verify_command_map",
]

PATH_POLICY = "millrace-check-path-policy/v1"
ENVIRONMENT = "millrace-check-environment/v1"
PRODUCTS = "millrace-check-products/v1"
COMPLETION_ACCEPTANCE = "millrace-check-completion-acceptance/v1"
COMMAND_MAP = "millrace-check-command-map/v1"

SURFACES = ("environment_paths", "product_paths")
# The four existing CI source-snapshot cache exclusions (cicd_source.CACHE_DIRS)
# are the only recursive selectors; each matches its basename at any depth.
CACHE_TOKENS = (
    "**/__pycache__",
    "**/.pytest_cache",
    "**/.mypy_cache",
    "**/.ruff_cache",
)
EXECUTION_FIELDS = (
    "argv",
    "cwd",
    "environment",
    "timeout_seconds",
    "timeout_policy",
    "expected_exit_code",
    "path_policy_ref",
    "expected_products",
)
PRODUCT_STATUSES = ("reserved", "observed", "accepted", "restored", "unknown")
ENVIRONMENT_KINDS = ("absent", "file", "directory", "symlink")
ACCEPTANCE_KINDS = ("selected_stopped_route", "public_core_completion")
# Existing Core effective-timeout fallback and warning (router/graph.py
# `_common_settings`). Mirrored for resolution provenance, not a new bound.
FALLBACK_TIMEOUT_SECONDS = 1800
TIMEOUT_DEFAULTED = "timeout_defaulted"
# The cooperative target owner marker (`lad_service.OWNER`, `cicd_source.OWNER`).
OWNER_MARKER = ".millrace-plus-target-owner.json"

_SHA = re.compile(r"[0-9a-f]{64}\Z")
# Existing Core graph identifier contract (`records.IDENTIFIER`) for node fields.
_NODE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_TOKENS = {token: token[3:] for token in CACHE_TOKENS}
# Git and owner authority components: never declared, never classified away.
_STRUCTURAL = frozenset({".git", OWNER_MARKER})
_POLICY_RULE = ("surface", "prefixes", "reason", "origin", "override")
_REASONED = ("reason", "origin", "override")
_ENVIRONMENT_FIELDS = (
    "format",
    "path_policy_ref",
    "target_root",
    "observed_at",
    "entries",
)
_ENTRY_FIELDS = ("prefix", "present", "kind", "metadata_sha256")
_MAP_FIELDS = ("lq_check_id", "ci_command_id", "execution_sha256")
_PRODUCT_FIELDS = (
    "format",
    "input_id",
    "materialization_id",
    "command_id",
    "path_policy_ref",
    "before_ref",
    "after_ref",
    "owned_paths",
    "completion_ref",
    "previous_ref",
    "status",
)
# Fixed for one product layer across every lifecycle link.
_LAYER_IDENTITY = (
    "input_id",
    "materialization_id",
    "command_id",
    "path_policy_ref",
    "before_ref",
)
# Per status: (after_ref, completion_ref, previous_ref); True required, False
# null, None either. Raw process facts never carry accepted completion evidence.
_PRODUCT_LINKS: dict[str, tuple[bool | None, bool, bool]] = {
    "reserved": (False, False, False),
    "observed": (True, False, True),
    "accepted": (True, True, True),
    "restored": (True, True, True),
    "unknown": (None, False, True),
}
_TRANSITIONS = frozenset(
    {
        ("reserved", "observed"),
        ("reserved", "unknown"),
        ("observed", "accepted"),
        ("observed", "unknown"),
        # Append-only re-observation once the consumer has proven closure; an
        # unknown layer still never reaches acceptance without a fresh observation.
        ("unknown", "observed"),
        ("accepted", "restored"),
    }
)
_ACCEPTANCE_FIELDS = (
    "format",
    "subject_ref",
    "command_id",
    "process_receipt_ref",
    "acceptance",
)
_ACCEPTANCE_TAGS = {
    "selected_stopped_route": (
        "kind",
        "from_node",
        "to_node",
        "reservation_ref",
        "guard_ref",
    ),
    "public_core_completion": ("kind", "invocation_ref", "completion_ref", "guard_ref"),
}
_INHERITED = {
    "plane": "plane defaults.timeout_seconds",
    "workflow": "workflow defaults.timeout_seconds",
    "runtime": "runtime_defaults.timeout_seconds",
}
_OVERRIDE = "author an explicit positive timeout_seconds with its own timeout_policy"


class CheckContractError(Exception):
    """Expected refusal with a stable code; callers hold or translate it."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LstatFacts(Protocol):
    """The `os.lstat` fields an opaque environment observation retains."""

    @property
    def st_mode(self) -> int: ...
    @property
    def st_uid(self) -> int: ...
    @property
    def st_gid(self) -> int: ...
    @property
    def st_size(self) -> int: ...
    @property
    def st_mtime_ns(self) -> int: ...
    @property
    def st_ino(self) -> int: ...
    @property
    def st_dev(self) -> int: ...


@dataclass(frozen=True)
class PathPolicy:
    """A validated path policy record and its pairwise-disjoint literal index."""

    record: dict[str, object]
    literals: tuple[tuple[str, str], ...]
    tokens: tuple[tuple[str, str], ...]


def parse(raw: bytes) -> object:
    """Strict UTF-8 JSON: duplicate keys, nonfinite numbers and bad Unicode refuse."""
    if not isinstance(raw, bytes):
        raise CheckContractError("invalid_json")
    try:
        return strict_json(raw)
    except IntakeError as exc:
        raise CheckContractError("invalid_json") from exc


def encode(value: object) -> bytes:
    """Canonical identity bytes (`local_intake.canonical`); originals stay retained."""
    try:
        return canonical(value)
    except IntakeError as exc:
        raise CheckContractError("invalid_json") from exc


def _fields(value: object, names: Sequence[str], code: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(names):
        raise CheckContractError(code)
    return cast(dict[str, object], value)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise CheckContractError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise CheckContractError(code)
    return value


def _relative(value: object, code: str) -> str:
    """A normalized contained POSIX path; never empty, `.`, absolute or dotted."""
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise CheckContractError(code)
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or str(path) != value
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise CheckContractError(code)
    return value


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _seconds(value: object, code: str, *, zero: bool = False) -> int | float:
    """A finite positive number (nonnegative with `zero`); bool never counts."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CheckContractError(code)
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise CheckContractError(code) from exc
    if not finite or value < 0 or (value == 0 and not zero):
        raise CheckContractError(code)
    return value


def _reasoned(value: object, code: str) -> dict[str, object]:
    obj = _fields(value, _REASONED, code)
    for key in _REASONED:
        _text(obj[key], code)
    return obj


def validate_ref(value: object, code: str = "invalid_ref") -> dict[str, object]:
    """`{path, sha256}`; its owner resolves the path against its own record root."""
    ref = _fields(value, ("path", "sha256"), code)
    _relative(ref["path"], code)
    _sha(ref["sha256"], code)
    return ref


def make_ref(path: str, raw: bytes) -> dict[str, object]:
    """The Ref of exact raw record bytes stored at a contained relative path."""
    if not isinstance(raw, bytes):
        raise CheckContractError("invalid_ref")
    return validate_ref({"path": path, "sha256": hashlib.sha256(raw).hexdigest()})


def _literal(value: object) -> str:
    prefix = _relative(value, "invalid_path_policy")
    # Any other contained path is matched literally (`*`, `?` and `[` are ordinary
    # name characters, never expanded); Git/owner structure cannot be declared.
    if _STRUCTURAL & set(PurePosixPath(prefix).parts):
        raise CheckContractError("invalid_path_policy")
    return prefix


def validate_path_policy(value: object) -> PathPolicy:
    """Validate one captured policy; its identity is the Ref of its raw bytes.

    Each prefix appears once. Literal prefixes are pairwise disjoint and may not
    contain a selected cache basename, so no path has two literal owners and a
    literal never restates a selector; overlapping classifications refuse.
    """
    code = "invalid_path_policy"
    record = _fields(value, ("format", "rules"), code)
    if record["format"] != PATH_POLICY or not isinstance(record["rules"], list):
        raise CheckContractError(code)
    literals: list[tuple[str, str]] = []
    tokens: dict[str, str] = {}
    seen: set[str] = set()
    for item in record["rules"]:
        rule = _fields(item, _POLICY_RULE, code)
        surface = rule["surface"]
        prefixes = rule["prefixes"]
        if surface not in SURFACES or not isinstance(prefixes, list) or not prefixes:
            raise CheckContractError(code)
        for key in _REASONED:
            _text(rule[key], code)
        for prefix in prefixes:
            if not isinstance(prefix, str) or prefix in seen:
                raise CheckContractError(code)
            seen.add(prefix)
            if prefix in _TOKENS:
                tokens[_TOKENS[prefix]] = cast(str, surface)
            else:
                literals.append((_literal(prefix), cast(str, surface)))
    owners = {prefix for prefix, _ in literals}
    for prefix, _ in literals:
        parts = PurePosixPath(prefix).parts
        if set(parts) & set(tokens) or any(
            "/".join(parts[:index]) in owners for index in range(1, len(parts))
        ):
            raise CheckContractError("path_policy_overlap")
    return PathPolicy(record, tuple(sorted(literals)), tuple(sorted(tokens.items())))


def stock_path_policy() -> bytes:
    """The canonical stock capture: only the four existing CI cache exclusions.

    Every consumer that selects the stock policy captures these exact bytes, so
    CI, L-Q1 and the factory pin one identical raw Ref. No other directory is
    excluded; `.venv`, `node_modules`, `build` and the like need explicit rules.
    """
    return encode(
        {
            "format": PATH_POLICY,
            "rules": [
                {
                    "surface": "environment_paths",
                    "prefixes": list(CACHE_TOKENS),
                    "reason": (
                        "Python tool caches regenerated by ordinary checks; opaque"
                        " environment provenance, never candidate source"
                    ),
                    "origin": (
                        "existing CI source-snapshot exclusion cicd_source.CACHE_DIRS"
                        " (__pycache__, .pytest_cache, .mypy_cache, .ruff_cache)"
                    ),
                    "override": (
                        "capture a different explicit millrace-check-path-policy/v1"
                        " before init; a captured policy is never edited in place"
                    ),
                }
            ],
        }
    )


def classify(policy: PathPolicy, path: str) -> str:
    """Return `source`, `environment_paths` or `product_paths` for one path.

    A path with a Git or owner-marker component is always `source`: no literal or
    cache selector masks that authority, so it is never product-writable. A path
    matched by rules of both surfaces refuses `path_policy_conflict`.
    """
    _relative(path, "invalid_path")
    parts = set(PurePosixPath(path).parts)
    if _STRUCTURAL & parts:
        return "source"
    surfaces = {surface for prefix, surface in policy.literals if _within(path, prefix)}
    surfaces |= {surface for name, surface in policy.tokens if name in parts}
    if len(surfaces) > 1:
        raise CheckContractError("path_policy_conflict")
    return surfaces.pop() if surfaces else "source"


def require_source(policy: PathPolicy, paths: Iterable[str]) -> None:
    """Refuse when a rule would mask tracked, deliverable, repair or control paths."""
    if isinstance(paths, str | bytes):
        raise CheckContractError("invalid_path")
    for path in paths:
        if classify(policy, path) != "source":
            raise CheckContractError("path_policy_masks_source")


def _environment_name(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or "=" in value or "\0" in value:
        raise CheckContractError(code)
    return value


def validate_execution(
    value: object,
    *,
    policy: PathPolicy | None = None,
    policy_ref: object = None,
) -> dict[str, object]:
    """Validate one resolved execution object exactly; no null timeout reaches here.

    With `policy_ref`, the object must pin that exact captured Ref; with `policy`,
    each expected product must be a product-surface selector or path.
    """
    code = "invalid_execution"
    obj = _fields(value, EXECUTION_FIELDS, code)
    argv = obj["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or "\0" in item for item in argv)
        or not argv[0].strip()
    ):
        raise CheckContractError(code)
    if obj["cwd"] != ".":
        _relative(obj["cwd"], code)
    environment = _fields(obj["environment"], ("inherit_names", "set"), code)
    names, values = environment["inherit_names"], environment["set"]
    if not isinstance(names, list) or not isinstance(values, dict):
        raise CheckContractError(code)
    for name in (*names, *values):
        _environment_name(name, code)
    if len(set(names)) != len(names) or set(names) & set(values):
        raise CheckContractError(code)
    if any(not isinstance(item, str) or "\0" in item for item in values.values()):
        raise CheckContractError(code)
    _seconds(obj["timeout_seconds"], code)
    _reasoned(obj["timeout_policy"], code)
    exit_code = obj["expected_exit_code"]
    # A POSIX process return code as Popen reports it and Core keeps it: an 8-bit
    # exit status, or -N for termination signal N (a 7-bit wait-status field), so
    # a signal result such as -15 is a valid expectation. bool is not a code.
    if type(exit_code) is not int or not -127 <= exit_code <= 255:
        raise CheckContractError(code)
    validate_ref(obj["path_policy_ref"], code)
    products = obj["expected_products"]
    if not isinstance(products, list) or any(
        not isinstance(product, str) for product in products
    ):
        raise CheckContractError(code)
    for product in products:
        if product not in _TOKENS:
            _relative(product, code)
    if len(set(products)) != len(products):
        raise CheckContractError(code)
    if policy_ref is not None and obj["path_policy_ref"] != validate_ref(policy_ref):
        raise CheckContractError("execution_policy_mismatch")
    if policy is not None:
        for product in products:
            if product in _TOKENS:
                declared = (_TOKENS[product], "product_paths") in policy.tokens
            else:
                declared = classify(policy, product) == "product_paths"
            if not declared:
                raise CheckContractError("expected_product_not_declared")
    return obj


def execution_of(
    definition: Mapping[str, object],
    *,
    policy: PathPolicy | None = None,
    policy_ref: object = None,
) -> dict[str, object]:
    """Project the shared execution object from a CI command or L-Q1 CheckDefinition."""
    if not isinstance(definition, Mapping) or not set(EXECUTION_FIELDS) <= set(
        definition
    ):
        raise CheckContractError("invalid_execution")
    return validate_execution(
        {key: definition[key] for key in EXECUTION_FIELDS},
        policy=policy,
        policy_ref=policy_ref,
    )


def execution_sha256(execution: object) -> str:
    """Canonical SHA-256 of exactly the eight shared execution fields."""
    return hashlib.sha256(encode(validate_execution(execution))).hexdigest()


def product_write_allowed(
    policy: PathPolicy, execution: Mapping[str, object], path: str
) -> bool:
    """True only for a product-surface path inside this command's expected_products.

    `execution` may be the shared execution object or a full CI command or L-Q1
    CheckDefinition carrying it.
    """
    checked = execution_of(execution, policy=policy)
    if classify(policy, path) != "product_paths":
        return False
    parts = PurePosixPath(path).parts
    for product in cast(list[str], checked["expected_products"]):
        if product in _TOKENS:
            if _TOKENS[product] in parts:
                return True
        elif _within(path, product):
            return True
    return False


def resolve_timeout(
    explicit: object,
    timeout_policy: object,
    *,
    plane: object = None,
    workflow: object = None,
    runtime: object = None,
) -> dict[str, object]:
    """Resolve one check command's effective timeout from already gathered layers.

    Order: the explicit operator/profile value, then plane, workflow and runtime
    defaults, then the existing Core 1,800-second fallback with its
    `timeout_defaulted` warning. The consuming derived check node and its
    derived binding are deliberately not inputs: their budgets are outputs of
    this value. The authored timeout_policy is kept for an explicit value; an
    inherited value records its actual resolution path instead.
    """
    code = "invalid_timeout"
    layers = {"plane": plane, "workflow": workflow, "runtime": runtime}
    for value in layers.values():
        if value is not None:
            _seconds(value, code)
    if timeout_policy is not None:
        _reasoned(timeout_policy, code)
    if explicit is not None:
        if timeout_policy is None:
            raise CheckContractError(code)
        return {
            "timeout_seconds": _seconds(explicit, code),
            "timeout_policy": timeout_policy,
            "source": "explicit",
            "warning": None,
        }
    for source, value in layers.items():
        if value is not None:
            return {
                "timeout_seconds": value,
                "timeout_policy": {
                    "reason": "authored timeout_seconds is null; inherited the"
                    f" {source} default",
                    "origin": f"{_INHERITED[source]} via the existing Core settings"
                    " order, excluding the consuming derived check node and binding",
                    "override": _OVERRIDE,
                },
                "source": source,
                "warning": None,
            }
    return {
        "timeout_seconds": FALLBACK_TIMEOUT_SECONDS,
        "timeout_policy": {
            "reason": "authored timeout_seconds is null and no plane, workflow or"
            " runtime default exists; timeout_defaulted",
            "origin": "existing Core router graph _common_settings 1,800-second"
            " fallback",
            "override": _OVERRIDE,
        },
        "source": "fallback",
        "warning": TIMEOUT_DEFAULTED,
    }


def node_budget(
    timeouts: Sequence[object],
    bookkeeping_seconds: object,
    *,
    ceiling_seconds: object = None,
) -> dict[str, object]:
    """Init-static check node deadline: resolved timeout(s) plus captured bookkeeping.

    Per-check consumers pass one resolved timeout; standalone CI passes the
    effective timeouts of its unique maximum selectable command set. A known
    existing operator `timeout_ceiling_seconds` is reported against the budget,
    never introduced or hidden: `clipped` is true when it is lower.
    """
    code = "invalid_timeout"
    if isinstance(timeouts, str | bytes) or not isinstance(timeouts, Sequence):
        raise CheckContractError(code)
    if not timeouts:
        raise CheckContractError(code)
    total: int | float = _seconds(bookkeeping_seconds, code, zero=True)
    for value in timeouts:
        total += _seconds(value, code)
    _seconds(total, code)
    ceiling = None if ceiling_seconds is None else _seconds(ceiling_seconds, code)
    return {
        "budget_seconds": total,
        "ceiling_seconds": ceiling,
        "effective_seconds": total if ceiling is None else min(total, ceiling),
        "clipped": ceiling is not None and ceiling < total,
    }


def environment_entry(
    prefix: str, info: LstatFacts | None, *, link_target: str | None = None
) -> dict[str, object]:
    """One opaque observation from an lstat result (None when absent).

    Only presence and the path's own lstat metadata are retained; links are not
    followed and contents are never hashed. Special files cannot be observed.
    """
    code = "invalid_environment"
    _relative(prefix, code)
    if info is None:
        if link_target is not None:
            raise CheckContractError(code)
        return {
            "prefix": prefix,
            "present": False,
            "kind": "absent",
            "metadata_sha256": None,
        }
    mode = info.st_mode
    if stat.S_ISREG(mode):
        kind = "file"
    elif stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISLNK(mode):
        kind = "symlink"
    else:
        raise CheckContractError("environment_special_path")
    if (kind == "symlink") != (link_target is not None) or (
        link_target is not None
        and (not isinstance(link_target, str) or "\0" in link_target)
    ):
        raise CheckContractError(code)
    metadata = {
        "kind": kind,
        "mode": stat.S_IMODE(mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
        "link_target": link_target,
    }
    return {
        "prefix": prefix,
        "present": True,
        "kind": kind,
        "metadata_sha256": hashlib.sha256(encode(metadata)).hexdigest(),
    }


def _absolute_root(value: object, code: str) -> str:
    if not isinstance(value, str) or "\0" in value or value.startswith("//"):
        raise CheckContractError(code)
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or ".." in path.parts:
        raise CheckContractError(code)
    return value


def _utc(value: object, code: str) -> str:
    if not isinstance(value, str):
        raise CheckContractError(code)
    try:
        offset = datetime.fromisoformat(value).utcoffset()
    except ValueError as exc:
        raise CheckContractError(code) from exc
    if offset is None or offset.total_seconds() != 0:
        raise CheckContractError(code)
    return value


def validate_environment(
    value: object, *, policy: PathPolicy | None = None, policy_ref: object = None
) -> dict[str, object]:
    """Validate one opaque environment observation record.

    Entries are distinct and never nested. With `policy`, every literal
    environment prefix has exactly one entry and every other entry is a present
    path named by an environment selector whose ancestors are all source.
    """
    code = "invalid_environment"
    record = _fields(value, _ENVIRONMENT_FIELDS, code)
    if record["format"] != ENVIRONMENT or not isinstance(record["entries"], list):
        raise CheckContractError(code)
    validate_ref(record["path_policy_ref"], code)
    _absolute_root(record["target_root"], code)
    _utc(record["observed_at"], code)
    entries: dict[str, dict[str, object]] = {}
    for item in record["entries"]:
        entry = _fields(item, _ENTRY_FIELDS, code)
        prefix = _relative(entry["prefix"], code)
        present, kind = entry["present"], entry["kind"]
        if (
            prefix in entries
            or kind not in ENVIRONMENT_KINDS
            or not isinstance(present, bool)
            or present != (kind != "absent")
        ):
            raise CheckContractError(code)
        if present:
            _sha(entry["metadata_sha256"], code)
        elif entry["metadata_sha256"] is not None:
            raise CheckContractError(code)
        entries[prefix] = entry
    for prefix in entries:
        parts = PurePosixPath(prefix).parts
        if any("/".join(parts[:index]) in entries for index in range(1, len(parts))):
            raise CheckContractError(code)
    if policy_ref is not None and record["path_policy_ref"] != validate_ref(policy_ref):
        raise CheckContractError("environment_policy_mismatch")
    if policy is not None:
        literal = {
            prefix
            for prefix, surface in policy.literals
            if surface == "environment_paths"
        }
        for prefix, entry in entries.items():
            if prefix in literal:
                continue
            parts = PurePosixPath(prefix).parts
            if (
                not entry["present"]
                or (parts[-1], "environment_paths") not in policy.tokens
                or classify(policy, prefix) != "environment_paths"
                or any(
                    classify(policy, "/".join(parts[:index])) != "source"
                    for index in range(1, len(parts))
                )
            ):
                raise CheckContractError("environment_not_opaque")
        if not literal <= set(entries):
            raise CheckContractError("environment_incomplete")
    return record


def validate_products(
    value: object,
    *,
    policy: PathPolicy | None = None,
    policy_ref: object = None,
    execution: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate one product layer record and its status-dependent links.

    Exactly one subject: the prepared L-Q1 check input or the CI/factory
    materialization. Only accepted/restored layers pin completion evidence (the
    tagged acceptance record, never a raw process receipt). With `policy` (and
    `execution`), every owned path must be a declared product of that command.
    """
    code = "invalid_products"
    record = _fields(value, _PRODUCT_FIELDS, code)
    status = record["status"]
    if (
        record["format"] != PRODUCTS
        or not isinstance(status, str)
        or status not in _PRODUCT_LINKS
    ):
        raise CheckContractError(code)
    subjects = [record["input_id"], record["materialization_id"]]
    if sum(subject is not None for subject in subjects) != 1:
        raise CheckContractError(code)
    for subject in subjects:
        if subject is not None:
            _text(subject, code)
    _text(record["command_id"], code)
    validate_ref(record["path_policy_ref"], code)
    validate_ref(record["before_ref"], code)
    rules = _PRODUCT_LINKS[status]
    for key, required in zip(("after_ref", "completion_ref", "previous_ref"), rules):
        if record[key] is None:
            if required:
                raise CheckContractError(code)
        elif required is False:
            raise CheckContractError(code)
        else:
            validate_ref(record[key], code)
    owned = record["owned_paths"]
    if not isinstance(owned, list):
        raise CheckContractError(code)
    for path in owned:
        _relative(path, code)
    if len(set(owned)) != len(owned):
        raise CheckContractError(code)
    if policy_ref is not None and record["path_policy_ref"] != validate_ref(policy_ref):
        raise CheckContractError("products_policy_mismatch")
    if policy is not None:
        for path in cast(list[str], owned):
            if (
                not product_write_allowed(policy, execution, path)
                if execution is not None
                else classify(policy, path) != "product_paths"
            ):
                raise CheckContractError("undeclared_output")
    return record


def validate_product_transition(
    previous_raw: bytes, previous_ref: object, current: object
) -> None:
    """Check one lifecycle link: current.previous_ref pins exactly previous's bytes.

    Legal links are reserved→observed|unknown, observed→accepted|unknown,
    unknown→observed and accepted→restored. An unknown layer is retained and can
    reach acceptance only through a fresh observation appended after the consumer
    proves closure; this helper proves no closure itself. Subject, command, policy
    and before facts never change; acceptance and restoration keep the observed
    after/owned facts, and restoration pins the same acceptance evidence.
    """
    code = "invalid_product_transition"
    link = validate_ref(previous_ref, code)
    if not isinstance(previous_raw, bytes) or (
        hashlib.sha256(previous_raw).hexdigest() != link["sha256"]
    ):
        raise CheckContractError(code)
    before = validate_products(parse(previous_raw))
    after = validate_products(current)
    if after["previous_ref"] != link or (before["status"], after["status"]) not in (
        _TRANSITIONS
    ):
        raise CheckContractError(code)
    if any(before[key] != after[key] for key in _LAYER_IDENTITY):
        raise CheckContractError(code)
    if after["status"] in {"accepted", "restored"} and (
        before["after_ref"] != after["after_ref"]
        or before["owned_paths"] != after["owned_paths"]
    ):
        raise CheckContractError(code)
    if after["status"] == "restored" and (
        before["completion_ref"] != after["completion_ref"]
    ):
        raise CheckContractError(code)


def validate_completion_acceptance(value: object, kind: str) -> dict[str, object]:
    """Validate accepted completion evidence under the consumer's one fixed tag.

    L-Q1 passes `selected_stopped_route`; CI and the factory pass
    `public_core_completion`. The other variant never substitutes, and the raw
    process receipt can never also serve as reservation, guard, invocation or
    Core completion evidence.
    """
    if kind not in _ACCEPTANCE_TAGS:
        raise CheckContractError("invalid_acceptance_kind")
    code = "invalid_completion_acceptance"
    record = _fields(value, _ACCEPTANCE_FIELDS, code)
    if record["format"] != COMPLETION_ACCEPTANCE:
        raise CheckContractError(code)
    validate_ref(record["subject_ref"], code)
    _text(record["command_id"], code)
    receipt = validate_ref(record["process_receipt_ref"], code)
    acceptance = record["acceptance"]
    if not isinstance(acceptance, dict):
        raise CheckContractError(code)
    if acceptance.get("kind") != kind:
        raise CheckContractError("acceptance_kind_mismatch")
    tag = _fields(acceptance, _ACCEPTANCE_TAGS[kind], code)
    refs = [validate_ref(tag[key], code) for key in tag if key.endswith("_ref")]
    if kind == "selected_stopped_route":
        nodes = (tag["from_node"], tag["to_node"])
        if (
            any(not isinstance(n, str) or not _NODE.fullmatch(n) for n in nodes)
            or nodes[0] == nodes[1]
        ):
            raise CheckContractError(code)
    if any(
        ref["path"] == receipt["path"] or ref["sha256"] == receipt["sha256"]
        for ref in refs
    ):
        raise CheckContractError("acceptance_self_certified")
    return record


def validate_command_map(value: object) -> dict[str, object]:
    """One row per L-Q1 check; rows naming one CI command agree on its digest."""
    code = "invalid_command_map"
    record = _fields(value, ("format", "entries"), code)
    if record["format"] != COMMAND_MAP or not isinstance(record["entries"], list):
        raise CheckContractError(code)
    checks: set[str] = set()
    commands: dict[str, str] = {}
    for item in record["entries"]:
        entry = _fields(item, _MAP_FIELDS, code)
        check = _text(entry["lq_check_id"], code)
        command = _text(entry["ci_command_id"], code)
        digest = _sha(entry["execution_sha256"], code)
        if check in checks or commands.setdefault(command, digest) != digest:
            raise CheckContractError(code)
        checks.add(check)
    return record


def verify_command_map(
    record: object,
    lq_checks: Mapping[str, Mapping[str, object]],
    ci_commands: Mapping[str, Mapping[str, object]],
) -> dict[str, str]:
    """Resolve every selectable L-Q1 check to its exact CI command before dispatch.

    Inputs are resolved definitions keyed by their original IDs (L-Q1
    CheckDefinitions and CI commands). Returns `lq_check_id -> ci_command_id`.
    A missing or extra row, unknown command or any unequal execution object
    refuses; no required check disappears through an unmapped ID.
    """
    entries = cast(list[dict[str, str]], validate_command_map(record)["entries"])
    rows = {entry["lq_check_id"]: entry for entry in entries}
    if set(rows) != set(lq_checks):
        raise CheckContractError("command_map_incomplete")
    resolved: dict[str, str] = {}
    for check, entry in rows.items():
        command = entry["ci_command_id"]
        if command not in ci_commands:
            raise CheckContractError("command_map_unknown_command")
        digest = entry["execution_sha256"]
        if (
            execution_sha256(execution_of(lq_checks[check])) != digest
            or execution_sha256(execution_of(ci_commands[command])) != digest
        ):
            raise CheckContractError("command_map_execution_mismatch")
        resolved[check] = command
    return resolved
