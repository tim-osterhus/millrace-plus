"""One finite Millforge CLI invocation followed by public saved-record inspection."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, cast

from . import CoreContractError, require_core, require_owned_process

require_core()
from millrace.router.execution import (  # noqa: E402
    AdapterConfiguration,
    AdapterResult,
    CompletionFacts,
    InvocationRequest,
    LaunchPlan,
    RouterError,
)

from . import millforge_configuration as c  # noqa: E402
from .configuration import fields, mapping, number, strict_json  # noqa: E402

if TYPE_CHECKING:
    from millrace.router.process import ProcessResult


def _observed(result: ProcessResult) -> dict[str, object]:
    return {name: getattr(result, name) for name in c.PROCESS_FIELDS}


def _run(argv: tuple[str, ...], cwd: Path, env: Mapping[str, str], deadline: float,
         poll: Callable[[], bool], started: Callable[[int], None]) -> ProcessResult:
    from millrace.router.process import ProcessRequest, run_owned_process

    # Callback exceptions propagate with the helper's cleanup acknowledgment.
    return run_owned_process(
        ProcessRequest(
            argv, cwd, env, None, deadline, stdin_limit=1,
            stdout_capture_limit=c.STDOUT_LIMIT,
            stderr_capture_limit=c.STDERR_CAPTURE,
            stdout_fail_limit=c.STDOUT_LIMIT, stderr_fail_limit=c.STDERR_LIMIT,
            cleanup_on_root_exit=True,
        ), poll=poll, on_started=started,
    )


def _saved_request(directory: Path) -> bytes:
    """Read one bounded regular record after inspection, without following a link."""
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise RouterError("millforge_record_invalid")
    file = directory / "request.json"
    before = file.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > c.REQUEST_LIMIT:
        raise RouterError("millforge_record_invalid")
    fd = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RouterError("millforge_record_invalid")
        raw = stream.read(c.REQUEST_LIMIT + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) > c.REQUEST_LIMIT or len(raw) != opened.st_size
            or (opened.st_size, opened.st_mtime_ns)
            != (after.st_size, after.st_mtime_ns)):
        raise RouterError("millforge_record_invalid")
    return raw


def _inspect(plan: LaunchPlan, details: Mapping[str, object], raw: bytes,
             *, failed: bool) -> tuple[dict[str, object], bytes]:
    """Public inspect owns native schemas; this boundary binds selected identities."""
    try:
        inspection = fields(strict_json(raw), {"result", "capture", "trace_records"})
        result, capture = mapping(inspection["result"]), mapping(inspection["capture"])
        native = mapping(details["request"])
        if (result.get("schema") != "millforge-result/v3"
                or capture.get("schema") != "millforge-capture/v3"):
            raise RouterError("millforge_record_invalid")
        if (result.get("request_id") != plan.invocation_id
                or capture.get("request_id") != plan.invocation_id
                or any(result.get(key) != details[key] for key in (
                    "profile_id", "profile_digest", "mode"))
                or capture.get("profile_digest") != details["profile_digest"]
                or capture.get("cwd") != native["workspace"]
                or capture.get("limits") != mapping(details["profile"])["limits"]):
            raise RouterError("millforge_identity_mismatch")
        versions = mapping(capture.get("versions"))
        if versions.get("millforge") != "0.2.0" or versions.get("pi") != "1.4.0":
            raise RouterError("millforge_record_invalid")
        number(inspection["trace_records"], integer=True)
        saved = _saved_request(Path(cast(str, details["output_path"])))
        saved_value = mapping(strict_json(saved))
        if saved_value != native:
            raise RouterError("millforge_identity_mismatch")
        artifacts = result.get("artifacts")
        if not isinstance(artifacts, list):
            raise RouterError("millforge_record_invalid")
        request_artifacts = [mapping(item) for item in artifacts
                             if mapping(item).get("path") == "request.json"]
        if (len(request_artifacts) != 1
                or request_artifacts[0].get("size") != len(saved)
                or request_artifacts[0].get("sha256") != c.digest(saved)):
            raise RouterError("millforge_record_invalid")
        if failed:
            if (result.get("status") != "failed" or result.get("output") is not None
                    or result.get("domain_outcome") is not None
                    or not isinstance(result.get("diagnostic"), Mapping)):
                raise RouterError("millforge_record_invalid")
            semantic = b""
            final = None
        else:
            if (result.get("status") != "completed"
                    or result.get("diagnostic") is not None):
                raise RouterError("millforge_record_invalid")
            if not c.closed(result.get("closure"), completed=True):
                raise RouterError("millforge_cleanup_unconfirmed")
            semantic = c.semantic(result.get("output"), result.get("domain_outcome"),
                                  details["outcomes"])
            final = {"size": len(semantic), "sha256": c.digest(semantic)}
        # Bounded projection, not raw native output, capture or diagnostic text.
        accepted = {
            "request_id": result["request_id"], "profile_id": result["profile_id"],
            "profile_digest": result["profile_digest"], "mode": result["mode"],
            "status": result["status"], "domain_outcome": result["domain_outcome"],
            "closure": fields(result.get("closure"), {
                "worker", "tools", "writers", "inference", "detached_effects",
            }), "versions": {"millforge": versions["millforge"], "pi": versions["pi"]},
            "trace_records": inspection["trace_records"],
            "result_sha256": c.digest(c.encoded(result)),
            "capture_sha256": c.digest(c.encoded(capture)),
            "saved_request_sha256": c.digest(saved),
            "request_value_sha256": c.digest(c.encoded(saved_value)), "final": final,
        }
        return accepted, semantic
    except RouterError as exc:
        if exc.code in {
            "millforge_record_invalid", "millforge_identity_mismatch",
            "millforge_semantic_invalid", "millforge_cleanup_unconfirmed",
        }:
            raise
        raise RouterError("millforge_record_invalid") from exc
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise RouterError("millforge_record_invalid") from exc


def _result(evidence: dict[str, object], transport: str, failure: str | None,
            *, writer: str, raw: bytes = b"") -> AdapterResult:
    evidence["failure_code"] = failure
    main = evidence["main"]
    return AdapterResult(
        raw, ("[millforge] " + failure).encode() if failure else b"",
        transport, writer, failure or "",
        cast(int | None, mapping(main)["exit_code"]) if main is not None else None,
        c.seal(evidence),
    )


class MillforgeRunAdapter:
    """A fresh native run; cancellation never adopts a detached worker PID."""

    adapter_id = c.ADAPTER_ID
    adapter_contract = c.ADAPTER_CONTRACT

    def validate_configuration(self, config: AdapterConfiguration) -> None:
        c.validate_configuration(config)

    def plan(self, request: InvocationRequest) -> LaunchPlan:
        return c.plan(request)

    def invoke(
        self, plan: LaunchPlan, *, cwd: Path, env: Mapping[str, str], deadline: float,
        poll: Callable[[], bool], on_started: Callable[[int], None],
    ) -> AdapterResult:
        details = c.launch_details(plan)
        evidence: dict[str, object] = {
            "format": c.RESULT_FORMAT, "invocation_id": plan.invocation_id,
            "pid": None, "main": None, "inspection": None, "accepted": None,
            "failure_code": None,
        }

        def refuse(code: str) -> AdapterResult:
            return _result(evidence, "execution_failure", code, writer="stopped")

        try:
            require_owned_process()
        except CoreContractError:
            return refuse("millforge_owned_process_unavailable")
        home = details["auth_home"]
        if home is not None and not Path(cast(str, home)).is_dir():
            return refuse("millforge_auth_home_unavailable")
        if os.path.lexists(cast(str, details["output_path"])):
            return refuse("millforge_output_exists")
        elapsed = cast(int, mapping(mapping(details["profile"])["limits"])[
            "elapsed_seconds"])
        if deadline - time.time() < elapsed + c.RESERVE:
            return refuse("millforge_insufficient_timeout")

        def announced(pid: int) -> None:
            evidence["pid"] = pid
            on_started(pid)

        main = _run(plan.argv, cwd, dict(env), deadline - c.RESERVE, poll, announced)
        observed = _observed(main)
        evidence["main"] = observed
        transport, failure = c.disposition(observed)
        writer = "may_live" if main.pid is not None else "stopped"
        if not c.clean_process(observed) or main.exit_code not in {0, 3}:
            return _result(evidence, transport, failure, writer=writer)
        # A cancellation observed after root exit must not launch another process.
        if poll():
            if main.exit_code == 3:
                return _result(evidence, transport, failure, writer=writer)
            return _result(evidence, "cancelled", "millforge_cancelled", writer=writer)
        inspection_deadline = min(deadline - 5, time.time() + 5)
        if inspection_deadline <= time.time():
            if main.exit_code == 3:
                return _result(evidence, transport, failure, writer=writer)
            return _result(evidence, "timeout", "millforge_inspect_timeout",
                           writer=writer)
        inspected = _run(
            (plan.argv[0], "inspect", "--run", cast(str, details["output_path"])),
            cwd, {"PATH": env.get("PATH", "")}, inspection_deadline, poll,
            lambda pid: None,
        )
        evidence["inspection"] = _observed(inspected)
        inspect_transport, inspect_failure = c.disposition(
            mapping(evidence["inspection"]), inspection=True,
        )
        if inspect_failure is not None:
            if main.exit_code == 3:
                return _result(evidence, transport, failure, writer=writer)
            return _result(evidence, inspect_transport, inspect_failure, writer=writer)
        try:
            accepted, semantic = _inspect(
                plan, details, inspected.stdout, failed=main.exit_code == 3,
            )
        except RouterError as exc:
            if main.exit_code == 3:
                return _result(evidence, transport, failure, writer=writer)
            classification = (
                "execution_failure" if exc.code == "millforge_cleanup_unconfirmed"
                else "protocol_failure"
            )
            return _result(evidence, classification, exc.code, writer=writer)
        if main.exit_code == 0:
            if poll():
                return _result(evidence, "cancelled", "millforge_cancelled",
                               writer=writer)
            if time.time() >= deadline:
                return _result(evidence, "timeout", "millforge_inspect_timeout",
                               writer=writer)
        evidence["accepted"] = accepted
        if c.closed(accepted["closure"], completed=main.exit_code == 0):
            writer = "stopped"
        return _result(evidence, transport, failure, writer=writer, raw=semantic)

    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts,
    ) -> None:
        c.validate_completion(plan, completion)
