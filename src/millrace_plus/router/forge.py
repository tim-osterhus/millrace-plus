"""The v1 and v2 ``forge_runner`` adapters run one SDK worker per invocation.

The parent probes the worker interpreter, then runs one ``forge-worker.py``
process through Core's public owned-process helper. It never imports ``forge``
or the worker script. Remote effects (backend receipt, continued generation,
tool-output egress) stay unknown in every outcome.
"""

from __future__ import annotations

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

from . import forge_configuration as c  # noqa: E402

if TYPE_CHECKING:
    from millrace.router.process import ProcessResult


def _run(
    argv: tuple[str, ...],
    cwd: Path,
    env: Mapping[str, str],
    deadline: float,
    poll: Callable[[], bool],
    started: Callable[[int], None],
    *,
    stdin: bytes | None,
    stdout_limit: int,
    stderr_limit: int,
) -> ProcessResult:
    """Finite capture policy only. Core owns spawn, drain, TERM and KILL."""
    from millrace.router.process import ProcessRequest, run_owned_process

    # A callback exception propagates as OwnedProcessError after cleanup.
    return run_owned_process(
        ProcessRequest(
            argv,
            cwd,
            env,
            stdin,
            deadline,
            stdin_limit=c.START_LIMIT if stdin is not None else 1,
            stdout_capture_limit=stdout_limit,
            stderr_capture_limit=1,
            stdout_fail_limit=stdout_limit,
            stderr_fail_limit=stderr_limit,
            cleanup_on_root_exit=True,
        ),
        poll=poll,
        on_started=started,
    )


class ForgeRunnerAdapter:
    """Read-only Forge SDK worker with parent-owned process lifecycle."""

    adapter_id = c.ADAPTER_ID
    adapter_contract = c.ADAPTER_CONTRACT

    def validate_configuration(self, config: AdapterConfiguration) -> None:
        c.validate_configuration(config, contract=self.adapter_contract)

    def plan(self, request: InvocationRequest) -> LaunchPlan:
        return c.plan(request, contract=self.adapter_contract)

    def invoke(
        self,
        plan: LaunchPlan,
        *,
        cwd: Path,
        env: Mapping[str, str],
        deadline: float,
        poll: Callable[[], bool],
        on_started: Callable[[int], None],
    ) -> AdapterResult:
        details = c.launch_details(plan, contract=self.adapter_contract)
        if str(cwd) != details["cwd"]:
            raise RouterError("invalid_launch_plan")
        responses = self.adapter_contract == c.ADAPTER_CONTRACT_V3
        evidence: dict[str, object] = {
            "format": c.RESULT_FORMAT_V3 if responses else c.RESULT_FORMAT,
            "invocation_id": plan.invocation_id,
            "launch_sha256": c.digest(c.encoded(details)),
            "pid": None,
            "refusal": None,
            "probe": None,
            "probe_report": None,
            "main": None,
            "start": None,
            "envelope": None,
            "semantic": None,
            "failure_code": None,
        }
        semantic: str | None = None

        def finish() -> AdapterResult:
            transport, code, writer = c.disposition(
                evidence["refusal"],
                cast("Mapping[str, object] | None", evidence["probe"]),
                cast("Mapping[str, object] | None", evidence["probe_report"]),
                cast("Mapping[str, object] | None", evidence["main"]),
                cast("Mapping[str, object] | None", evidence["envelope"]),
            )
            stdout = b""
            if transport == "completed":
                stdout = cast(str, semantic).encode("utf-8")
                evidence["semantic"] = {"size": len(stdout), "sha256": c.digest(stdout)}
            evidence["failure_code"] = code
            main = cast("Mapping[str, object] | None", evidence["main"])
            return AdapterResult(
                stdout,
                ("[forge] " + code).encode("ascii") if code else b"",
                transport,
                writer,
                code or "",
                cast("int | None", main["exit_code"]) if main is not None else None,
                c.seal(evidence),
            )

        try:
            require_owned_process()
        except CoreContractError:
            evidence["refusal"] = "forge_owned_process_unavailable"
            return finish()
        if deadline - time.time() < c.PROBE_RESERVE:
            evidence["refusal"] = "forge_deadline_too_short"
            return finish()

        # Core supplies only the declared environment. The probe never announces
        # a pid to Core, and its line is parsed only after a clean exit 0.
        executable = plan.argv[0]
        root = str(Path(cast(str, details["worker_path"])).parent)
        probe = _run(
            c.probe_argv(executable, root),
            cwd,
            {} if responses else dict(env),
            min(deadline - c.CLEANUP_RESERVE, time.time() + 5),
            poll,
            lambda pid: None,
            stdin=None,
            stdout_limit=c.PROBE_LIMIT,
            stderr_limit=c.PROBE_LIMIT,
        )
        probe_facts = c.observed(probe)
        evidence["probe"] = probe_facts
        report = c.parse_probe(probe.stdout) if c.probe_parsable(probe_facts) else None
        evidence["probe_report"] = report
        if report is None or not c.probe_accepted(report):
            return finish()
        if deadline - time.time() < c.MAIN_RESERVE:
            evidence["refusal"] = "forge_deadline_too_short"
            return finish()

        hard = deadline - c.CLEANUP_RESERVE
        soft = hard - c.SOFT_RESERVE
        start = c.start_record(plan.invocation_id, hard, soft)
        evidence["start"] = {
            "process_deadline_unix": float(hard),
            "soft_deadline_unix": float(soft),
            "sha256": c.digest(start),
        }

        def announced(pid: int) -> None:
            # Setup can fail after Popen and before this callback, so the
            # announced pid stays separate from the observed main.pid.
            evidence["pid"] = pid
            on_started(pid)

        if responses:
            key_name = cast(str, c.mapping(details["auth"])["name"])
            worker_env = (
                {"MILLRACE_FORGE_RESPONSES_KEY": env[key_name]}
                if key_name in env
                else {}
            )
        else:
            worker_env = dict(env)
        main = _run(
            plan.argv,
            cwd,
            worker_env,
            hard,
            poll,
            announced,
            stdin=start,
            stdout_limit=c.STDOUT_LIMIT,
            stderr_limit=c.STDERR_LIMIT,
        )
        main_facts = c.observed(main)
        evidence["main"] = main_facts
        # W-EN-08: the envelope is parsed only for a clean main that exited 0 or 3.
        if c.clean(main_facts) and main.exit_code in {0, 3}:
            evidence["envelope"], semantic = c.parse_envelope(
                main.stdout,
                plan.invocation_id,
                main.exit_code,
                responses=details if responses else None,
            )
        return finish()

    def validate_completion(
        self,
        plan: LaunchPlan,
        completion: CompletionFacts,
    ) -> None:
        c.validate_completion(plan, completion, contract=self.adapter_contract)


class ForgeRunnerV2Adapter(ForgeRunnerAdapter):
    """Forge runner with independent null defaults for read-file windows."""

    adapter_contract = c.ADAPTER_CONTRACT_V2


class ForgeRunnerV3Adapter(ForgeRunnerAdapter):
    """Forge runner using the bounded OpenAI Responses client."""

    adapter_contract = c.ADAPTER_CONTRACT_V3
