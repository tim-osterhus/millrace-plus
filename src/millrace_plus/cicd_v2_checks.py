"""CI v2 trusted command execution: one configured command, one raw observation.

`run_command` executes exactly one captured command (reproduction, probe or
check) through Core's public owned-process helper, the same facade L-Q1 uses.
There is no shell, and no argv ever comes from a log, input or model. It
observes the classified target before and after, the environment provenance
and the process facts, and classifies the raw functional status.

Acceptance is not decided here. The raw observation becomes a
`millrace-plus-cicd-check/v2` process receipt. Only the following
deterministic guard may publish `millrace-check-completion-acceptance/v1`
with the fixed `public_core_completion` tag, after the step's own Core
completion shows a stopped writer. A raw `passed` never qualifies by itself.

Capture limits are the operator's `resource_policy` byte guards for the
`command.stdout` and `command.stderr` surfaces. They must be explicit, and an
exceeded guard is uncertain, never pass. No v1 65,536-byte or 3,600-second
constant is inherited.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import signal
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from . import check_contracts as cc
from .cicd_records import CicdError
from .cicd_source import snapshot_policy, source_identity
from .local_intake import canonical

__all__ = [
    "STATUSES",
    "environment_for",
    "matched_failures",
    "product_changes",
    "status_of",
    "run_command",
    "product_ownership",
    "product_preflight",
    "reverse_products",
    "completion_acceptance",
    "product_layer",
    "TermPropagation",
]

STATUSES = ("passed", "failed", "timeout", "infrastructure", "uncertain")
GROUP_STATES = ("closed", "may_live", "unknown")
# Core stop causes that prevented the experiment from starting or feeding it.
_INFRASTRUCTURE = frozenset({"start_failed", "unsupported_platform", "stdin_failed"})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def environment_for(
    command: Mapping[str, object], base: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, object]]:
    """The child environment, plus provenance that never retains inherited values.

    Only the captured `inherit_names` that are actually present are passed
    through, followed by the captured nonsecret `set` literals. Provenance keeps
    the inherited names and digests only. Path observations are attached by
    the caller.
    """
    environment = cast(dict[str, object], command["environment"])
    names = cast(list[str], environment["inherit_names"])
    literals = cast(dict[str, str], environment["set"])
    inherited = {name: base[name] for name in names if name in base}
    values = inherited | literals
    provenance: dict[str, object] = {
        "inherited_names": sorted(inherited),
        "inherited_value_sha256": _sha(canonical(inherited)),
        "set_value_sha256": _sha(canonical(literals)),
        "path_observation_refs": [],
    }
    return values, provenance


def matched_failures(
    stdout: bytes,
    stderr: bytes,
    pattern_ids: list[str],
    patterns: Mapping[str, object],
) -> list[str]:
    """Selected literal failure signatures observed in their named stream.

    Matching is literal bytes, never a model-supplied regular expression.
    """
    streams = {"stdout": stdout, "stderr": stderr}
    by_id = {
        str(item["id"]): item
        for item in cast(list[dict[str, object]], patterns["patterns"])
    }
    return sorted(
        pattern
        for pattern in pattern_ids
        if str(by_id[pattern]["literal"]).encode("utf-8")
        in streams[str(by_id[pattern]["stream"])]
    )


def product_changes(
    policy: cc.PathPolicy,
    command: Mapping[str, object],
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, list[str]]:
    """Product paths this command changed, split by `expected_products` ownership.

    `undeclared` holds when nonempty: it is output this command did not
    declare. A product path present before is reported in `preexisting`. The
    caller decides adoption only from proven earlier ownership; foreign
    products are never adopted or deleted here.
    """
    old = cast(dict[str, object], before["products"])
    new = cast(dict[str, object], after["products"])
    changed = sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))
    declared = [p for p in changed if cc.product_write_allowed(policy, command, p)]
    return {
        "declared": declared,
        "undeclared": [p for p in changed if p not in declared],
        "preexisting": sorted(p for p in declared if p in old),
    }


def status_of(
    result: object | None,
    expected_exit: int,
    *,
    source_unchanged: bool,
    undeclared_output: bool,
    launch_refusal: str | None = None,
) -> tuple[str, str | None]:
    """Raw functional status and diagnostic from observed facts only.

    Passed needs the expected exit, a reaped root, both EOFs, a closed and
    confirmed group, untruncated output, unchanged source and no undeclared
    product output. Root exit alone never proves descendant closure.
    """
    if result is None:
        return "infrastructure", launch_refusal or "not_launched"
    facts = cast(object, result)
    stop = getattr(facts, "stop_cause")
    code = getattr(facts, "exit_code")
    if stop in _INFRASTRUCTURE and code is None:
        return "infrastructure", str(stop)
    if stop == "timeout":
        return "timeout", "timeout"
    closed = (
        getattr(facts, "root_reaped")
        and getattr(facts, "stdout_eof")
        and getattr(facts, "stderr_eof")
        and getattr(facts, "group_state") == "closed"
        and getattr(facts, "cleanup_confirmed")
    )
    if not closed:
        return "uncertain", "writer_not_closed"
    if getattr(facts, "stdout_truncated") or getattr(facts, "stderr_truncated"):
        return "uncertain", "output_guard_exceeded"
    if stop is not None:
        return "uncertain", str(stop)
    if not source_unchanged:
        return "uncertain", "source_changed"
    if undeclared_output:
        return "uncertain", "undeclared_output"
    if code == expected_exit:
        return "passed", None
    return "failed", None


def _executable(argv0: str, environment: Mapping[str, str], cwd: Path) -> str | None:
    if "/" in argv0:
        path = Path(argv0) if argv0.startswith("/") else cwd / argv0
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    search = environment.get("PATH")
    return shutil.which(argv0, path=search) if search else None


def run_command(
    target: Path,
    policy: cc.PathPolicy,
    command: Mapping[str, object],
    guards: Mapping[str, int | float],
    *,
    base_environment: Mapping[str, str],
    deadline: float,
    cancelled: Callable[[], bool],
    on_started: Callable[[int], None],
) -> dict[str, object]:
    """Run one captured command and return its raw observation (no acceptance).

    `deadline` is the absolute wall-clock time Core's helper expects. The caller
    derives it from the command's captured effective timeout and clips it to
    its own invocation deadline. An unavailable executable is infrastructure
    and nothing is spawned.
    """
    stdout_cap, stderr_cap = guards.get("command.stdout"), guards.get("command.stderr")
    if (
        not isinstance(stdout_cap, int)
        or not isinstance(stderr_cap, int)
        or isinstance(stdout_cap, bool)
        or isinstance(stderr_cap, bool)
    ):
        raise CicdError("resource_guard_required")
    cwd = target / str(command["cwd"])
    environment, provenance = environment_for(command, base_environment)
    before = snapshot_policy(target, policy, guards)
    started = _now()
    argv = [str(item) for item in cast(list[object], command["argv"])]
    result = None
    refusal = None
    if _executable(argv[0], environment, cwd) is None:
        refusal = "executable_unavailable"
    else:
        try:
            from .router.process import ProcessRequest, run_owned_process
        except ImportError as exc:  # CoreContractError is an ImportError.
            raise CicdError("check_runtime_requires_core") from exc
        request = ProcessRequest(
            tuple(argv),
            cwd,
            environment,
            None,
            deadline,
            stdin_limit=1,
            stdout_capture_limit=stdout_cap,
            stderr_capture_limit=stderr_cap,
            stdout_fail_limit=None,
            stderr_fail_limit=None,
        )
        result = run_owned_process(request, poll=cancelled, on_started=on_started)
    finished = _now()
    after = snapshot_policy(target, policy, guards)
    products = product_changes(policy, command, before, after)
    unchanged = source_identity(before) == source_identity(after)
    status, diagnostic = status_of(
        result,
        cast(int, command["expected_exit_code"]),
        source_unchanged=unchanged,
        undeclared_output=bool(products["undeclared"]),
        launch_refusal=refusal,
    )
    facts = (
        {
            "root_reaped": False,
            "stdout_eof": False,
            "stderr_eof": False,
            "group_state": "closed",
        }
        if result is None
        else {
            "root_reaped": result.root_reaped,
            "stdout_eof": result.stdout_eof,
            "stderr_eof": result.stderr_eof,
            "group_state": (
                result.group_state if result.group_state in GROUP_STATES else "unknown"
            ),
        }
    )
    return {
        "argv": argv,
        "cwd": str(command["cwd"]),
        "command_sha256": cc.execution_sha256(cc.execution_of(command, policy=policy)),
        "environment_provenance": provenance,
        "before": before,
        "after": after,
        "products": products,
        "source_before_sha256": source_identity(before),
        "source_after_sha256": source_identity(after),
        "started_at": started,
        "finished_at": finished,
        "exit_code": None if result is None else result.exit_code,
        "status": status,
        "diagnostic": diagnostic,
        "process_facts": facts,
        "pid": None if result is None else result.pid,
        "stdout": b"" if result is None else result.stdout,
        "stderr": b"" if result is None else result.stderr,
    }


def product_ownership(
    observed: Mapping[str, object], prior_owned: Iterable[str] = ()
) -> dict[str, object]:
    """Which declared product writes this layer may own, from observed facts only.

    A declared write is owned when its path was absent before (proven absence)
    or belongs to an earlier unchanged layer of the same subject
    (`prior_owned`), whose beforeimages the caller must already have saved. A
    declared write over a preexisting foreign product is `foreign`, and any
    undeclared write is reported as such. Both hold. Nothing is adopted or
    deleted.
    """
    products = cast(dict[str, list[str]], observed["products"])
    earlier = set(prior_owned)
    preexisting = set(products["preexisting"])
    foreign = sorted(p for p in preexisting if p not in earlier)
    owned = sorted(
        p for p in products["declared"] if p not in preexisting or p in earlier
    )
    hold = None
    if products["undeclared"]:
        hold = "undeclared_output"
    elif foreign:
        hold = "foreign_product_overlap"
    return {
        "owned_paths": owned,
        "foreign": foreign,
        "undeclared": list(products["undeclared"]),
        "hold": hold,
    }


def _temporary(target: Path, path: str) -> Path:
    return (target / path).with_name("." + Path(path).name + ".cicd-tmp")


def reverse_products(
    target: Path,
    owned_paths: Iterable[str],
    after_products: Mapping[str, Mapping[str, object]],
    before_products: Mapping[str, Mapping[str, object]],
    current_products: Mapping[str, Mapping[str, object]],
    beforeimages: Mapping[str, bytes] | None = None,
    *,
    allow_already_reversed: bool = False,
) -> dict[str, object]:
    """Exactly reverse one accepted layer's products, or hold with nothing changed.

    Every owned path must equal its recorded after image. The explicit
    `allow_already_reversed` option also admits the exact before image for one
    active partial layer; its caller must validate the cumulative layer boundary.
    Ordinary facade restoration keeps the strict default. A third value holds
    `product_drift`. A path that existed before the layer (an earlier
    owned layer's output) is restored from its saved `beforeimages` bytes,
    which must hash to the recorded before entry; otherwise it holds
    `beforeimage_required`. After every path verifies, overwritten files are
    restored, created files removed, then created directories once empty,
    deepest first. Environment paths and source are never touched.
    """
    images = beforeimages or {}
    owned = sorted(set(owned_paths))
    for path in owned:
        if current_products.get(path) != after_products.get(path) and not (
            allow_already_reversed
            and current_products.get(path) == before_products.get(path)
        ):
            return {
                "status": "held",
                "reason": "product_drift",
                "path": path,
                "removed": [],
            }
        prior = before_products.get(path)
        if (
            prior is not None
            and prior.get("kind") == "file"
            and (
                path not in images
                or hashlib.sha256(images[path]).hexdigest() != prior.get("sha256")
            )
        ):
            return {
                "status": "held",
                "reason": "beforeimage_required",
                "path": path,
                "removed": [],
            }
        if prior is not None and _temporary(target, path).exists():
            # A leftover temporary from an interrupted restore is never reused.
            return {
                "status": "held",
                "reason": "stale_write_intent",
                "path": path,
                "removed": [],
            }
    for path in owned:
        # A created directory holding any unowned entry is not this layer's alone.
        if after_products[path].get("kind") == "directory" and any(
            other.startswith(path + "/") and other not in owned
            for other in current_products
        ):
            return {
                "status": "held",
                "reason": "directory_not_empty",
                "path": path,
                "removed": [],
            }
    restored = [
        p
        for p in owned
        if p in before_products
        and before_products[p].get("kind") == "file"
        and current_products.get(p) != before_products.get(p)
    ]
    files = [
        p
        for p in owned
        if after_products[p].get("kind") == "file"
        and p not in before_products
        and p in current_products
    ]
    folders = sorted(
        (
            p
            for p in owned
            if after_products[p].get("kind") == "directory"
            and p not in before_products
            and p in current_products
        ),
        key=lambda item: item.count("/"),
        reverse=True,
    )
    for path in restored:
        temporary = _temporary(target, path)
        with open(temporary, "xb") as stream:
            stream.write(images[path])
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, cast(int, before_products[path]["mode"]))
        os.replace(temporary, target / path)
    for path in files:
        (target / path).unlink()
    for path in folders:
        try:
            (target / path).rmdir()
        except OSError:
            # Only an outside writer racing this cooperative step reaches here.
            return {
                "status": "held",
                "reason": "directory_not_empty",
                "path": path,
                "removed": files,
            }
    return {
        "status": "reversed",
        "reason": None,
        "path": None,
        "removed": [*files, *folders],
        "restored": restored,
    }


def completion_acceptance(
    *,
    subject_ref: Mapping[str, object],
    command_id: str,
    process_receipt_ref: Mapping[str, object],
    invocation_ref: Mapping[str, object],
    completion_ref: Mapping[str, object],
    guard_ref: Mapping[str, object],
) -> dict[str, object]:
    """The following guard's tagged acceptance of one raw process receipt.

    CI always uses the fixed `public_core_completion` tag; no other variant or
    fallback exists. The raw receipt can never also serve as the invocation,
    completion or guard evidence that accepts it.
    """
    record = {
        "format": cc.COMPLETION_ACCEPTANCE,
        "subject_ref": dict(subject_ref),
        "command_id": command_id,
        "process_receipt_ref": dict(process_receipt_ref),
        "acceptance": {
            "kind": "public_core_completion",
            "invocation_ref": dict(invocation_ref),
            "completion_ref": dict(completion_ref),
            "guard_ref": dict(guard_ref),
        },
    }
    return cc.validate_completion_acceptance(record, "public_core_completion")


def product_layer(
    *,
    input_id: str | None,
    materialization_id: str | None,
    command: Mapping[str, object],
    policy: cc.PathPolicy,
    policy_ref: Mapping[str, object],
    before_ref: Mapping[str, object],
    after_ref: Mapping[str, object] | None,
    owned_paths: list[str],
    completion_ref: Mapping[str, object] | None,
    previous_ref: Mapping[str, object] | None,
    status: str,
) -> dict[str, object]:
    """One `millrace-check-products/v1` layer under the shared validator.

    Exactly one subject: a standalone input or a candidate materialization.
    Every owned path must be a declared product of this exact command.
    """
    record = {
        "format": cc.PRODUCTS,
        "input_id": input_id,
        "materialization_id": materialization_id,
        "command_id": str(command["id"]),
        "path_policy_ref": dict(policy_ref),
        "before_ref": dict(before_ref),
        "after_ref": None if after_ref is None else dict(after_ref),
        "owned_paths": list(owned_paths),
        "completion_ref": None if completion_ref is None else dict(completion_ref),
        "previous_ref": None if previous_ref is None else dict(previous_ref),
        "status": status,
    }
    return cc.validate_products(
        record, policy=policy, policy_ref=policy_ref, execution=command
    )


class TermPropagation:
    """The accepted L-Q1 D2 cancellation contract for one nested check group.

    Core cancels or times out the wrapper step with TERM, then KILLs it shortly
    after. The check runs in its own group (Core's helper starts a new session),
    so the handler kills that recorded group at once rather than racing a
    graceful schedule against the wrapper's death. TERM that arrives before the
    PID callback is applied as soon as the callback records the PID. A wrapper
    killed by arbitrary SIGKILL without TERM cannot run this. That case stays
    unknown and needs exact-group operator cleanup (L-Q1 D2), never a rerun.
    """

    def __init__(self) -> None:
        self.pid: int | None = None
        self.cancel = False
        self._previous: object = None

    def __enter__(self) -> TermPropagation:
        self._previous = signal.signal(signal.SIGTERM, self.on_signal)
        return self

    def __exit__(self, *_: object) -> None:
        self.pid = None  # Never signal a reaped, possibly reused group.
        signal.signal(signal.SIGTERM, cast(signal.Handlers, self._previous))

    def on_signal(self, *_: object) -> None:
        self.cancel = True
        if self.pid is not None:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except OSError:
                pass

    def started(self, pid: int) -> None:
        self.pid = pid
        if self.cancel:  # TERM arrived between spawn and this callback.
            self.on_signal()

    def cancelled(self) -> bool:
        return self.cancel


def product_preflight(
    policy: cc.PathPolicy,
    command: Mapping[str, object],
    current: Mapping[str, object],
    earlier_owned: Mapping[str, object],
) -> None:
    """Refuse known foreign writes and drift before launching a product writer.

    Earlier ownership is valid only for the exact unchanged after image. A
    configured write prefix does not authorize adopting preexisting products.
    """
    if any(current.get(path) != expected for path, expected in earlier_owned.items()):
        raise CicdError("product_drift")
    if any(
        cc.product_write_allowed(policy, command, path) and path not in earlier_owned
        for path in current
    ):
        raise CicdError("foreign_product_overlap")
