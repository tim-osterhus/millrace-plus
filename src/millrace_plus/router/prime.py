"""One fresh Prime text print projection over Core's synchronous owned process API."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

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

from . import prime_configuration as c  # noqa: E402

if TYPE_CHECKING:
    from millrace.router.process import ProcessResult


def _run(
    argv: tuple[str, ...], cwd: Path, env: Mapping[str, str], deadline: float,
    poll: Callable[[], bool], started: Callable[[int], None], *, probe: bool,
) -> tuple[ProcessResult, str, str | None, str]:
    """Select finite capture policy; Core alone owns spawn/drain/TERM/KILL."""
    from millrace.router.process import ProcessRequest, run_owned_process

    selected_deadline = min(deadline, time.time() + 5) if probe else deadline
    result = run_owned_process(
        ProcessRequest(
            argv, cwd, env, None, selected_deadline, stdin_limit=1,
            stdout_capture_limit=c.PROBE_LIMIT if probe else c.STDOUT_LIMIT,
            stderr_capture_limit=1,
            stdout_fail_limit=c.PROBE_LIMIT if probe else c.STDOUT_LIMIT,
            stderr_fail_limit=c.PROBE_LIMIT if probe else c.STDERR_LIMIT,
        ),
        poll=poll, on_started=started,
    )
    transport = "completed"
    failure: str | None = None
    if result.stop_cause is not None:
        transport, suffix = {
            "cancelled": ("cancelled", "cancelled"),
            "timeout": ("timeout", "timeout"),
            "start_failed": ("execution_failure", "spawn_failed"),
            "unsupported_platform": ("execution_failure", "unsupported_platform"),
            "stdout_overflow": ("protocol_failure", "stdout_limit"),
            "stderr_overflow": ("protocol_failure", "stderr_limit"),
        }.get(result.stop_cause, ("execution_failure", "io_failed"))
        if probe:
            if result.stop_cause == "timeout" and selected_deadline < deadline:
                transport, suffix = "execution_failure", "version_timeout"
            elif result.stop_cause in {"stdout_overflow", "stderr_overflow"}:
                transport, suffix = "execution_failure", "version_protocol"
        failure = "prime_" + suffix
    elif result.exit_code != 0:
        transport, failure = "execution_failure", "prime_nonzero_exit"
    elif not result.cleanup_confirmed or result.cleanup_forced:
        transport, failure = "execution_failure", "prime_cleanup_unconfirmed"
    elif probe and result.stdout not in {b"0.10.2", b"0.10.2\n"}:
        transport, failure = "execution_failure", "prime_version_mismatch"
    writer = (
        "may_live" if result.pid is not None and (
            result.stop_cause is not None or result.cleanup_forced
            or not result.cleanup_confirmed
        ) else "stopped"
    )
    return result, transport, failure, writer


def _process_evidence(result: ProcessResult) -> dict[str, object]:
    return {
        "stdout_bytes": result.stdout_bytes,
        "stdout_sha256": result.stdout_sha256 if result.stdout_bytes else None,
        "stderr_bytes": result.stderr_bytes,
        "stderr_sha256": result.stderr_sha256 if result.stderr_bytes else None,
        "stdout_eof": result.stdout_eof, "stderr_eof": result.stderr_eof,
        "stdin_closed": result.stdin_closed, "root_reaped": result.root_reaped,
        "cleanup_forced": result.cleanup_forced, "group_state": result.group_state,
        "cleanup_confirmed": result.cleanup_confirmed,
        "stop_cause": result.stop_cause,
    }


def _result(
    evidence: dict[str, object], *, raw: bytes = b"", transport: str,
    writer: str, failure: str | None, exit_code: int | None,
) -> AdapterResult:
    return AdapterResult(
        raw, ("[prime] " + failure).encode() if failure else b"",
        transport, writer, failure or "", exit_code, evidence,
    )


class PrimePrintAdapter:
    """Official fresh text boundary with native permissions and unknown effects."""

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
        if str(cwd) != details["cwd"]:
            raise RouterError("invalid_launch_plan")
        evidence = c.evidence(plan.invocation_id)
        failure: str | None
        try:
            require_owned_process()
        except CoreContractError as exc:
            failure = "prime_" + exc.code
            evidence["failure_code"] = failure
            return _result(evidence, transport="execution_failure", writer="stopped",
                           failure=failure, exit_code=None)

        # Core supplies only the declared environment. Do not discover credentials,
        # change native policy/configuration, or install/upgrade a native runtime.
        probe, transport, failure, writer = _run(
            (plan.argv[0], "--version"), cwd, dict(env), deadline, poll,
            lambda pid: None, probe=True,
        )
        evidence.update(_process_evidence(probe), probe_pid=probe.pid)
        if transport != "completed":
            evidence["failure_code"] = failure
            return _result(evidence, transport=transport, writer=writer,
                           failure=failure, exit_code=probe.exit_code)

        evidence.update(observed_version=c.PRIME_VERSION, phase="invocation",
                        protocol_state="incomplete")

        def announced(pid: int) -> None:
            # Setup can fail after Popen but before this callback. Keep the
            # helper's observed PID distinct from the PID recorded by Core.
            evidence["pid"] = pid
            on_started(pid)

        process, transport, failure, writer = _run(
            plan.argv, cwd, dict(env), deadline, poll, announced, probe=False,
        )
        evidence.update(_process_evidence(process), process_pid=process.pid)
        semantic = b""
        # Only the complete text stream after successful foreground closure is
        # semantic input. Native text mode exposes no session ID or stop reason.
        if transport == "completed":
            try:
                semantic = c.native_result(process.stdout)
                evidence.update(
                    protocol_state="settled",
                    final={"size": len(semantic), "text_sha256": c.digest(semantic)},
                )
            except RouterError as exc:
                transport, failure = "protocol_failure", exc.code
                evidence["protocol_state"] = "invalid"
        evidence["failure_code"] = failure
        return _result(evidence, raw=semantic, transport=transport, writer=writer,
                       failure=failure, exit_code=process.exit_code)

    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts,
    ) -> None:
        c.validate_completion(plan, completion)
