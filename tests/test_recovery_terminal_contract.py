"""Selected Pi recovery completion returns useful Builder work durably."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MethodType
from typing import cast

import pytest
from millrace.adapters.cli.context import transition_context
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.pi_rpc import PiRpcAdapter
from millrace.adapters.pi_rpc_config import PiRpcConfig
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterSuccessResult,
    DispatchEcho,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    runner_cancellation_diagnostic_digest,
)
from millrace.compiler import authority_fingerprint
from millrace.contracts import QueueFamilyId
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
    TimerDue,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.kernel.projection import artifact_payload_digest

from support.public_runtime_fakes import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
)
from test_pi_runtime_failure_recovery import (
    _TASK_PAYLOAD,
    _accepted,
    _reopen,
    _runtime,
    _timeout_adapter,
)
from test_pi_workflow import (
    PACKAGE_ID,
    PACKAGE_VERSION,
    PI_WORKFLOW_ID,
    _select_and_verify_package,
)

_REPORT = {
    "artifact_kind": "execution.artifacts.report",
    "summary": "The recovered Troubleshooter confirmed the task is ready to resume.",
}


class _ImmediateSuccessHandle:
    def __init__(self, outcome: AdapterSuccessResult) -> None:
        self._outcome: AdapterSuccessResult | None = outcome

    def poll_completion(self) -> AdapterSuccessResult | None:
        outcome = self._outcome
        self._outcome = None
        return outcome

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return _operation("cooperative_cancel")

    def terminate(self) -> RunnerCancellationOperationResult:
        return _operation("terminate")

    def kill(self) -> RunnerCancellationOperationResult:
        return _operation("kill")

    def cleanup(self) -> RunnerCleanupResult:
        diagnostic = {"cleanup": "not_required"}
        return RunnerCleanupResult(
            "not_required",
            0,
            0,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


def _operation(name: str) -> RunnerCancellationOperationResult:
    diagnostic = {"operation": name, "fixture": "not_requested"}
    return RunnerCancellationOperationResult(
        name,
        "unsupported",
        0,
        0,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


def _success_adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    marker: str = "TROUBLESHOOT_COMPLETE",
    artifact_payload: Mapping[str, object] | None = _REPORT,
    observation_payload: Mapping[str, object] | None = None,
) -> tuple[AdapterLocalConfig, list[str]]:
    monkeypatch.setenv("PI_RPC_API_KEY", "local-test-placeholder")
    monkeypatch.setattr(PiRpcConfig, "verify", lambda _self: {})
    adapter = PiRpcAdapter(
        PiRpcConfig(
            {"adapter_id": "pi-local", "timeout_seconds": 9600},
            {"schema_version": "pi-rpc-profile.v1"},
            b"",
        )
    )
    stage_calls: list[str] = []

    def start_session(
        self: PiRpcAdapter,
        request: AdapterInvocationRequest,
    ) -> StartedSession:
        admitted = self._admitted.pop(id(request), None)
        assert admitted is not None and admitted[0] is request
        stage_calls.append(request.dispatch_envelope.stage_kind_id)
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=echo,
            redaction_policy=request.redaction_policy,
            marker=marker,
            artifact_payload_candidate=artifact_payload,
            observation_payload_candidate=observation_payload,
        )
        return StartedSession(
            echo,
            _ImmediateSuccessHandle(outcome),
            f"pi-recovery-complete:{request.session_id}",
            {"protocol": "pi-0.87.0-jsonl.v1"},
        )

    adapter.start_session = MethodType(start_session, adapter)
    return AdapterLocalConfig(adapters={"pi_rpc": adapter}), stage_calls


def _selected_initial_state(tmp_path: Path, payload: dict[str, object]):
    plan = cast(
        SelectedCompiledPlan,
        _select_and_verify_package(
            tmp_path / "package-registry",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=PI_WORKFLOW_ID,
            workflow_version="0.1",
        ),
    )
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()
    state = _accepted(state, InitializeWorkspace("recovery-init"))
    state = _accepted(
        state,
        AdmitPlan(
            "recovery-admit",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
    )
    state = _accepted(
        state,
        SelectDefaultPlan("recovery-select", authority_fingerprint=fingerprint),
    )
    state = _accepted(
        state,
        EnqueueWork(
            "recovery-enqueue",
            queue_family_id=QueueFamilyId("task"),
            payload=payload,
        ),
        work_item_id="work-c19-task",
        activation_id="activation-c19-builder",
    )
    return state, plan, fingerprint


def _assert_selected_troubleshooter_prompt_contract(
    plan: SelectedCompiledPlan,
) -> None:
    assets = {str(asset.id): asset.body for asset in plan.assets}
    entrypoint = assets["lad.full.pi.entrypoints.lad_troubleshooter"]
    core_skill = assets["execution.skills.troubleshooter_core"]
    assert "integrator" in entrypoint
    assert "integrator" in core_skill
    assert "TROUBLESHOOT_COMPLETE" in entrypoint
    assert "execution.artifacts.report" in entrypoint
    assert "different selected workflow renders that marker" not in entrypoint

    governed_repair = core_skill.split(
        "## Governed Troubleshooter Repair Plan\n", maxsplit=1
    )[1].split("\n## Handoff Format", maxsplit=1)[0]
    assert "TROUBLESHOOT_NARROW_REPAIR" in governed_repair
    assert "execution.artifacts.troubleshooter_repair_plan" in governed_repair
    repair_example = json.loads(
        governed_repair.split("```json\n", maxsplit=1)[1].split("\n```", maxsplit=1)[0]
    )
    assert repair_example["artifact_kind"] == (
        "execution.artifacts.troubleshooter_repair_plan"
    )


def test_selected_pi_complete_resumes_builder_with_durable_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = dict(_TASK_PAYLOAD)
    state, plan, fingerprint = _selected_initial_state(tmp_path, payload)
    assert str(plan.workflow.workflow_id) == PI_WORKFLOW_ID
    _assert_selected_troubleshooter_prompt_contract(plan)
    assert state.work_items["work-c19-task"].payload == payload
    initial_counters = state.counters
    runtime = _runtime(tmp_path, state)
    failure_config, _failure_adapter, failed_stages = _timeout_adapter(monkeypatch)
    try:
        builder_failure = run_bounded_execution_unit(
            runtime,
            activation_id="activation-c19-builder",
            local_config=failure_config,
        )
        assert builder_failure.code == "runtime_failure_recovery_applied"
        assert builder_failure.accepted is True
        after_builder_failure = runtime.store.load_runtime_state(runtime.cas_store)
        assert after_builder_failure.counters == initial_counters
        builder_attempt = next(iter(after_builder_failure.recovery_attempts.values()))
        assert (builder_attempt.attempt_count, builder_attempt.phase) == (
            1,
            "active_recovery",
        )
        assert after_builder_failure.work_items["work-c19-task"].payload == payload
        builder_route = next(
            route
            for route in after_builder_failure.activation_routes
            if route.created_by_input_id
            == after_builder_failure.runner_session_completions[
                cast(
                    str,
                    after_builder_failure.runs[
                        cast(str, builder_failure.run_id)
                    ].current_session_id,
                )
            ].application_input_id
        )
        troubleshooter_one = after_builder_failure.activations[
            builder_route.target_activation_id
        ]
        assert str(troubleshooter_one.stage_kind_id) == "lad_troubleshooter"

        runtime = _reopen(runtime)
        after_builder_failure = runtime.store.load_runtime_state(runtime.cas_store)
        runtime_failure_config, agent_failure_stages = _success_adapter(
            monkeypatch,
            marker="RUNTIME_FAILURE",
            artifact_payload=None,
            observation_payload=None,
        )
        agent_runtime_failure = run_bounded_execution_unit(
            runtime,
            activation_id=troubleshooter_one.activation_id,
            local_config=runtime_failure_config,
        )
        assert (
            agent_runtime_failure.code == "observation_accepted"
            and agent_runtime_failure.accepted
        ), agent_runtime_failure
        after_agent_failure = runtime.store.load_runtime_state(runtime.cas_store)
        assert len(after_agent_failure.counters) == len(initial_counters) + 1
        assert next(iter(after_agent_failure.counters.values())).value == 1
        attempt = next(iter(after_agent_failure.recovery_attempts.values()))
        assert (attempt.attempt_count, attempt.phase) == (2, "pending_cooldown")
        wait = next(iter(after_agent_failure.cooldown_waits.values()))
        timer_context = deterministic_context(
            transition_id="recovery-cooldown-transition",
            activation_id="activation-c19-troubleshooter-after-cooldown",
        )
        timer_decision = decide(
            after_agent_failure,
            TimerDue(
                "recovery-cooldown-due",
                wait_id=wait.wait_id,
                observed_at=wait.due_at,
            ),
            timer_context,
        )
        assert timer_decision.accepted, timer_decision.refusal
        after_timer = apply(after_agent_failure, timer_decision)
        runtime.store.persist_runtime_state(after_timer, runtime.cas_store)
        runtime = _reopen(runtime)
        after_timer = runtime.store.load_runtime_state(runtime.cas_store)
        assert after_timer.cooldown_waits[wait.wait_id].consumed_input_id == (
            "recovery-cooldown-due"
        )
        troubleshooter_after_cooldown = after_timer.activations[
            cast(
                str,
                after_timer.cooldown_waits[
                    wait.wait_id
                ].resulting_recovery_activation_id,
            )
        ]
        assert str(troubleshooter_after_cooldown.stage_kind_id) == "lad_troubleshooter"

        contract_run_id = "recovery-contract-troubleshooter-run"
        contract_claim = decide(
            after_timer,
            ClaimWork(
                "recovery-contract-troubleshooter-claim",
                activation_id=troubleshooter_after_cooldown.activation_id,
            ),
            deterministic_context(
                transition_id="recovery-contract-troubleshooter-claim-transition",
                run_id=contract_run_id,
                claim_id="recovery-contract-troubleshooter-claim",
                fencing_token="recovery-contract-troubleshooter-fence",
            ),
        )
        assert contract_claim.accepted, contract_claim.refusal
        contract_state = apply(after_timer, contract_claim)
        fake_dispatch = fake_runner_dispatch_envelope_for_run(
            state=contract_state,
            run_id=contract_run_id,
        )
        terminal_contract = next(
            option
            for option in fake_dispatch.terminal_options
            if option["action_id"] == "execution.return_troubleshooter_complete"
        )
        assert terminal_contract["marker"] == "TROUBLESHOOT_COMPLETE"
        assert terminal_contract["artifact_schema_id"] == "execution.artifacts.report"

        success_config, successful_stages = _success_adapter(
            monkeypatch,
            marker="TROUBLESHOOT_COMPLETE",
            artifact_payload=_REPORT,
            observation_payload={"resume_stage": "builder"},
        )
        complete = run_bounded_execution_unit(
            runtime,
            activation_id=troubleshooter_after_cooldown.activation_id,
            local_config=success_config,
        )
        assert complete.code == "observation_accepted" and complete.accepted, complete
        assert failed_stages == ["lad_builder"]
        assert agent_failure_stages == ["lad_troubleshooter"]
        assert successful_stages == ["lad_troubleshooter"]

        completed_state = runtime.store.load_runtime_state(runtime.cas_store)
        assert completed_state.counters == after_agent_failure.counters
        attempt = next(iter(completed_state.recovery_attempts.values()))
        assert attempt.phase == "resolved"
        complete_observation = next(
            observation
            for observation in completed_state.runner_observations.values()
            if observation.run_id == complete.run_id
        )
        complete_route = next(
            route
            for route in completed_state.activation_routes
            if route.created_by_input_id == complete_observation.created_by_input_id
        )
        assert str(complete_route.action_id) == (
            "execution.return_troubleshooter_complete"
        )
        builder_activation = completed_state.activations[
            complete_route.target_activation_id
        ]
        assert str(builder_activation.stage_kind_id) == "lad_builder"
        builder_payload = completed_state.work_items[
            builder_activation.work_item_id
        ].payload
        expected_qa_context = {
            "task_contract": payload,
            "task_contract_digest": artifact_payload_digest(payload),
            "checker_baseline": None,
            "checker_baseline_digest": None,
        }
        assert builder_payload["qa_context"] == expected_qa_context
        assert builder_payload["troubleshooter_evidence"] == _REPORT

        runtime = _reopen(runtime)
        after_complete_reload = runtime.store.load_runtime_state(runtime.cas_store)
        assert after_complete_reload == completed_state
        preview_claim = decide(
            after_complete_reload,
            ClaimWork(
                "recovery-next-builder-preview-claim",
                activation_id=builder_activation.activation_id,
            ),
            deterministic_context(
                transition_id="recovery-next-builder-preview-claim-transition",
                run_id="recovery-next-builder-preview-run",
                claim_id="recovery-next-builder-preview-claim",
                fencing_token="recovery-next-builder-preview-fence",
            ),
        )
        assert preview_claim.accepted, preview_claim.refusal
        builder_preview_state = apply(after_complete_reload, preview_claim)
        builder_dispatch = fake_runner_dispatch_envelope_for_run(
            state=builder_preview_state,
            run_id="recovery-next-builder-preview-run",
        )
        assert builder_dispatch.stage_kind_id == "lad_builder"
        assert builder_dispatch.work_item_payload["qa_context"] == expected_qa_context

        builder_success_config, builder_success_stages = _success_adapter(
            monkeypatch,
            marker="BUILDER_COMPLETE",
            artifact_payload={
                "artifact_kind": "execution.artifacts.stage_result",
                "summary": "The task implementation is ready for review.",
            },
            observation_payload=None,
        )
        builder_success = run_bounded_execution_unit(
            runtime,
            activation_id=builder_activation.activation_id,
            local_config=builder_success_config,
        )
        assert (
            builder_success.code == "observation_accepted" and builder_success.accepted
        ), builder_success
        assert builder_success_stages == ["lad_builder"]
        runtime = _reopen(runtime)
        after_builder_success = runtime.store.load_runtime_state(runtime.cas_store)
        builder_success_route = next(
            route
            for route in after_builder_success.activation_routes
            if route.source_run_id == builder_success.run_id
        )
        checker_activation = after_builder_success.activations[
            builder_success_route.target_activation_id
        ]
        assert str(checker_activation.stage_kind_id) == "lad_checker"

        replay_command = RunnerResultObserved(
            complete_observation.created_by_input_id,
            run_id=complete_observation.run_id,
            payload=complete_observation.payload,
            observed_at=complete_observation.observed_at,
        )
        replay = decide(
            after_builder_success,
            replay_command,
            transition_context(
                command="run.session",
                input_id_value=complete_observation.created_by_input_id,
            ),
        )
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(after_builder_success, replay) == after_builder_success

        checker_artifact = {
            "artifact_kind": "execution.artifacts.checker_result",
            "summary": "All declared task criteria passed review.",
            "task_contract_digest": expected_qa_context["task_contract_digest"],
            "criteria": [
                {
                    "criterion_id": "task-contract",
                    "requirement": "Satisfy the supplied task contract.",
                    "evidence_rule": "Review the completed implementation.",
                }
            ],
            "findings": [],
            "observations": [
                {
                    "observation_id": "criteria-reviewed",
                    "summary": "The declared task criteria were checked.",
                }
            ],
            "checks": [
                {
                    "check_id": "review-check",
                    "command_or_method": "Selected Checker review.",
                    "result": "passed",
                }
            ],
        }
        checker_config, checker_stages = _success_adapter(
            monkeypatch,
            marker="CHECKER_PASS",
            artifact_payload=checker_artifact,
            observation_payload=None,
        )
        checker_pass = run_bounded_execution_unit(
            runtime,
            activation_id=checker_activation.activation_id,
            local_config=checker_config,
        )
        assert checker_pass.code == "observation_accepted" and checker_pass.accepted
        assert checker_stages == ["lad_checker"]
        runtime = _reopen(runtime)
        after_checker = runtime.store.load_runtime_state(runtime.cas_store)
        checker_pass_route = next(
            route
            for route in after_checker.activation_routes
            if route.source_run_id == checker_pass.run_id
        )
        updater_activation = after_checker.activations[
            checker_pass_route.target_activation_id
        ]
        assert str(updater_activation.stage_kind_id) == "lad_updater"

        updater_config, updater_stages = _success_adapter(
            monkeypatch,
            marker="UPDATE_COMPLETE",
            artifact_payload=_REPORT,
            observation_payload=None,
        )
        updater_complete = run_bounded_execution_unit(
            runtime,
            activation_id=updater_activation.activation_id,
            local_config=updater_config,
        )
        assert (
            updater_complete.code == "observation_accepted"
            and updater_complete.accepted
        )
        assert updater_stages == ["lad_updater"]
        closed_state = runtime.store.load_runtime_state(runtime.cas_store)
        runtime = _reopen(runtime)
        assert runtime.store.load_runtime_state(runtime.cas_store) == closed_state
        assert updater_complete.run_id is not None
        assert closed_state.runs[updater_complete.run_id].work_item_id in (
            closed_state.closed_work_items
        )
    finally:
        runtime.close()


def test_selected_pi_complete_preserves_authenticated_existing_qa_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _plan, _fingerprint = _selected_initial_state(
        tmp_path,
        dict(_TASK_PAYLOAD),
    )
    runtime = _runtime(tmp_path, state)
    try:
        builder_config, _ = _success_adapter(
            monkeypatch,
            marker="BUILDER_COMPLETE",
            artifact_payload={
                "artifact_kind": "execution.artifacts.stage_result",
                "summary": "The task implementation is ready for review.",
            },
            observation_payload=None,
        )
        builder_complete = run_bounded_execution_unit(
            runtime,
            activation_id="activation-c19-builder",
            local_config=builder_config,
        )
        assert (
            builder_complete.code == "observation_accepted"
            and builder_complete.accepted
        )
        runtime = _reopen(runtime)
        after_builder = runtime.store.load_runtime_state(runtime.cas_store)
        builder_route = next(
            route
            for route in after_builder.activation_routes
            if route.source_run_id == builder_complete.run_id
        )
        checker_activation = after_builder.activations[
            builder_route.target_activation_id
        ]
        source_payload = after_builder.work_items[
            checker_activation.work_item_id
        ].payload
        builder_qa_context = source_payload["qa_context"]
        assert builder_qa_context["task_contract"] == _TASK_PAYLOAD
        assert builder_qa_context["task_contract_digest"] == artifact_payload_digest(
            _TASK_PAYLOAD
        )

        checker_artifact = {
            "artifact_kind": "execution.artifacts.checker_result",
            "summary": "The task needs one correction before it can pass.",
            "task_contract_digest": builder_qa_context["task_contract_digest"],
            "criteria": [
                {
                    "criterion_id": "task-contract",
                    "requirement": "Satisfy the supplied task contract.",
                    "evidence_rule": "Review the completed implementation.",
                }
            ],
            "findings": [],
            "observations": [
                {
                    "observation_id": "criteria-reviewed",
                    "summary": "The declared task criteria were checked.",
                }
            ],
            "checks": [
                {
                    "check_id": "review-check",
                    "command_or_method": "Selected Checker review.",
                    "result": "passed",
                }
            ],
        }
        checker_config, _ = _success_adapter(
            monkeypatch,
            marker="BLOCKED",
            artifact_payload=checker_artifact,
            observation_payload=None,
        )
        checker_blocked = run_bounded_execution_unit(
            runtime,
            activation_id=checker_activation.activation_id,
            local_config=checker_config,
        )
        assert (
            checker_blocked.code == "observation_accepted" and checker_blocked.accepted
        ), checker_blocked
        runtime = _reopen(runtime)
        after_checker = runtime.store.load_runtime_state(runtime.cas_store)
        checker_route = next(
            route
            for route in after_checker.activation_routes
            if route.source_run_id == checker_blocked.run_id
        )
        troubleshooter_activation = after_checker.activations[
            checker_route.target_activation_id
        ]
        troubleshooter_payload = after_checker.work_items[
            troubleshooter_activation.work_item_id
        ].payload
        source_qa_context = troubleshooter_payload["qa_context"]
        assert artifact_payload_digest(source_qa_context["checker_baseline"]) == (
            artifact_payload_digest(checker_artifact)
        )
        assert source_qa_context["checker_baseline_digest"] == artifact_payload_digest(
            checker_artifact
        )

        runtime_failure_config, _ = _success_adapter(
            monkeypatch,
            marker="RUNTIME_FAILURE",
            artifact_payload=None,
            observation_payload=None,
        )
        runtime_failure = run_bounded_execution_unit(
            runtime,
            activation_id=troubleshooter_activation.activation_id,
            local_config=runtime_failure_config,
        )
        assert (
            runtime_failure.code == "observation_accepted"
            and runtime_failure.accepted
        )
        runtime = _reopen(runtime)
        after_failure = runtime.store.load_runtime_state(runtime.cas_store)
        failure_observation = next(
            observation
            for observation in after_failure.runner_observations.values()
            if observation.run_id == runtime_failure.run_id
        )
        failure_route = next(
            route
            for route in after_failure.activation_routes
            if route.created_by_input_id == failure_observation.created_by_input_id
        )
        recovery_activation = after_failure.activations[
            failure_route.target_activation_id
        ]
        complete_config, _ = _success_adapter(
            monkeypatch,
            marker="TROUBLESHOOT_COMPLETE",
            artifact_payload=_REPORT,
            observation_payload={"resume_stage": "builder"},
        )
        complete = run_bounded_execution_unit(
            runtime,
            activation_id=recovery_activation.activation_id,
            local_config=complete_config,
        )
        assert complete.code == "observation_accepted" and complete.accepted
        runtime = _reopen(runtime)
        after_complete = runtime.store.load_runtime_state(runtime.cas_store)
        complete_observation = next(
            observation
            for observation in after_complete.runner_observations.values()
            if observation.run_id == complete.run_id
        )
        complete_route = next(
            route
            for route in after_complete.activation_routes
            if route.created_by_input_id == complete_observation.created_by_input_id
        )
        assert str(complete_route.action_id) == (
            "execution.return_troubleshooter_complete"
        )
        builder_activation = after_complete.activations[
            complete_route.target_activation_id
        ]
        returned_payload = after_complete.work_items[
            builder_activation.work_item_id
        ].payload
        assert returned_payload["qa_context"] == source_qa_context
        assert returned_payload["troubleshooter_evidence"] == _REPORT
    finally:
        runtime.close()
