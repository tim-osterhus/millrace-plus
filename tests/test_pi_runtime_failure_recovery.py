"""Full selected-package recurrence proof for adapter-origin timeout recovery.

The session seam returns the typed Pi adapter's canonical timeout result with
complete cleanup. It does not start Pi, contact a provider, or invent runner
output. A real local Pi deadline can leave cleanup at orphan_risk and is
therefore a different, ineligible lifecycle.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import MethodType
from typing import Any, cast

from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.pi_rpc import PiRpcAdapter
from millrace.adapters.pi_rpc_config import PiRpcConfig
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterLocalConfig,
    DispatchEcho,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    StartedSession,
    runner_cancellation_diagnostic_digest,
)
from millrace.contracts import QueueFamilyId
from millrace.contracts.compiled_plan import SelectedCompiledPlan, authority_fingerprint
from millrace.contracts.runner import (
    runner_session_completion_diagnostic_from_payload,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_diagnostic_matches,
    runtime_failure_eligible,
)
from millrace.contracts.state import RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
    TimerDue,
    TransitionInput,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.sqlite import SQLiteRuntimeStore

from support.public_runtime_fakes import deterministic_context
from test_pi_workflow import (
    PACKAGE_ID,
    PACKAGE_VERSION,
    PI_WORKFLOW_ID,
    _select_and_verify_package,
)

_EXPECTED_FULL_PI_FINGERPRINT = (
    "sha256:2dbacd9857ab2d93220185f4adbaeb488d3bc6a85b00bb8ebec78d4d7e6dff7d"
)
_TASK_PAYLOAD = {
    "task_id": "c05-timeout-recurrence",
    "body": "Complete the selected task and verify its stated acceptance criteria.",
}


class _ImmediateFailureHandle:
    def __init__(self, outcome: object) -> None:
        self._outcome = outcome

    def poll_completion(self) -> object:
        return self._outcome

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return _operation("cooperative_cancel")

    def terminate(self) -> RunnerCancellationOperationResult:
        return _operation("terminate")

    def kill(self) -> RunnerCancellationOperationResult:
        return _operation("kill")

    def cleanup(self) -> RunnerCleanupResult:
        diagnostic = {"cleanup": "complete"}
        return RunnerCleanupResult(
            "complete",
            1,
            1,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


def _operation(name: str) -> RunnerCancellationOperationResult:
    diagnostic = {"operation": name, "fixture": "not_requested"}
    return RunnerCancellationOperationResult(
        name,
        "succeeded",
        1,
        1,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


def _timeout_adapter(
    monkeypatch: Any,
) -> tuple[AdapterLocalConfig, PiRpcAdapter, list[str]]:
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
        outcome = self.error(
            request,
            "timeout",
            diagnostics={
                "failure_phase": "invocation",
                "failure_reason": "deadline",
            },
        )
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        return StartedSession(
            echo,
            _ImmediateFailureHandle(outcome),
            f"pi-timeout-fixture:{request.session_id}",
            {"protocol": "pi-0.87.0-jsonl.v1"},
        )

    adapter.start_session = MethodType(start_session, adapter)
    return AdapterLocalConfig(adapters={"pi_rpc": adapter}), adapter, stage_calls


def _accepted(
    state: RuntimeState, transition_input: TransitionInput, **ids: str
) -> RuntimeState:
    decision = decide(
        state,
        transition_input,
        deterministic_context(
            transition_id=f"transition-{transition_input.input_id}",
            **ids,
        ),
    )
    assert decision.accepted, decision.refusal
    return apply(state, decision)


def _selected_state(tmp_path: Path) -> tuple[RuntimeState, SelectedCompiledPlan, str]:
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
    assert fingerprint == _EXPECTED_FULL_PI_FINGERPRINT
    builder_route = next(
        route
        for route in plan.external_enqueue_routes
        if str(route.stage_kind_id) == "lad_builder"
    )
    assert str(builder_route.queue_family_id) == "task"

    state = empty_runtime_state()
    state = _accepted(state, InitializeWorkspace("c05-init"))
    state = _accepted(
        state,
        AdmitPlan("c05-admit", selected_plan=plan, authority_fingerprint=fingerprint),
    )
    state = _accepted(
        state,
        SelectDefaultPlan("c05-select", authority_fingerprint=fingerprint),
    )
    state = _accepted(
        state,
        EnqueueWork(
            "c05-enqueue",
            queue_family_id=QueueFamilyId("task"),
            payload=_TASK_PAYLOAD,
        ),
        work_item_id="work-c05-task",
        activation_id="activation-c05-builder",
    )
    activation = state.activations["activation-c05-builder"]
    assert str(activation.stage_kind_id) == "lad_builder"
    assert activation.graph_node_id == builder_route.graph_node_id
    assert state.work_items[activation.work_item_id].payload == _TASK_PAYLOAD
    return state, plan, fingerprint


def _runtime(tmp_path: Path, state: RuntimeState) -> OpenRuntimeContext:
    workspace = tmp_path / "runtime"
    workspace.mkdir()
    db_path = workspace / "runtime.sqlite3"
    cas_path = workspace / "cas"
    cas_path.mkdir()
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(cas_path)
    store.persist_runtime_state(state, cas_store)
    return OpenRuntimeContext(
        CliWorkspacePaths(workspace, db_path, cas_path), store, cas_store
    )


def _reopen(runtime: OpenRuntimeContext) -> OpenRuntimeContext:
    runtime.close()
    return OpenRuntimeContext(
        runtime.paths,
        SQLiteRuntimeStore.open(runtime.paths.db_path),
        ContentAddressedByteStore(runtime.paths.cas_path),
    )


def _failure_records(
    runtime: OpenRuntimeContext,
    result: object,
    *,
    expected_stage: str,
    fingerprint: str,
) -> tuple[RuntimeState, object, object]:
    state = runtime.store.load_runtime_state(runtime.cas_store)
    run_id = cast(str, result.run_id)
    run = state.runs[run_id]
    session_id = cast(str, run.current_session_id)
    session = state.runner_sessions[session_id]
    completion = state.runner_session_completions[session_id]
    assert str(run.stage_kind_id) == expected_stage
    assert str(run.run_ref.plan_ref.authority_fingerprint) == fingerprint
    assert run.work_item_id == "work-c05-task"
    assert run.run_ref.claim_id and run.run_ref.fencing_token
    assert session.state == completion.terminal_state == "failed"
    assert session.cleanup_disposition == completion.cleanup_disposition == "complete"
    assert completion.adapter_error_kind == "timeout"
    assert completion.runner_result_evidence_digest is None
    assert runtime_failure_eligible(state, completion)
    payload = runtime.cas_store.get_bytes(completion.diagnostic_digest)
    decoded = json.loads(payload)
    diagnostic = runner_session_completion_diagnostic_from_payload(decoded)
    assert diagnostic.run_id == run_id
    assert diagnostic.stage_kind_id == expected_stage
    assert diagnostic.plan_fingerprint == fingerprint
    assert diagnostic.diagnostic["error_kind"] == "timeout"
    assert runtime_failure_diagnostic_matches(state, completion, payload)
    assert runtime_failure_applied(state, run_id)
    assert state.runner_observations == {}
    assert state.artifacts == {}
    return state, run, completion


def test_full_selected_pi_plan_routes_timeout_through_cooldown_and_quarantine(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    state, plan, fingerprint = _selected_state(tmp_path)
    initial_counters = state.counters
    runtime = _runtime(tmp_path, state)
    local_config, adapter, stage_calls = _timeout_adapter(monkeypatch)
    try:
        builder = run_bounded_execution_unit(
            runtime,
            activation_id="activation-c05-builder",
            local_config=local_config,
        )
        assert builder.code == "runtime_failure_recovery_applied", builder
        assert builder.accepted is True
        after_builder, builder_run, builder_completion = _failure_records(
            runtime,
            builder,
            expected_stage="lad_builder",
            fingerprint=fingerprint,
        )
        assert after_builder.admitted_plans[fingerprint].selected_plan == plan
        assert after_builder.counters == initial_counters
        builder_attempt = next(iter(after_builder.recovery_attempts.values()))
        assert (builder_attempt.attempt_count, builder_attempt.phase) == (
            1,
            "active_recovery",
        )
        builder_route = next(
            route
            for route in after_builder.activation_routes
            if route.created_by_input_id == builder_completion.application_input_id
        )
        assert (
            str(builder_route.action_id) == "execution.recover_builder_runtime_failure"
        )
        troubleshooter_one = after_builder.activations[
            builder_route.target_activation_id
        ]
        assert troubleshooter_one.work_item_id == builder_run.work_item_id
        assert str(troubleshooter_one.stage_kind_id) == "lad_troubleshooter"
        assert str(troubleshooter_one.plan_ref.authority_fingerprint) == fingerprint
        assert (
            after_builder.work_items[builder_run.work_item_id].payload == _TASK_PAYLOAD
        )
        runtime = _reopen(runtime)
        reloaded_builder = runtime.store.load_runtime_state(runtime.cas_store)
        assert reloaded_builder == after_builder
        assert runtime_failure_applied(reloaded_builder, builder_run.run_ref.run_id)
        request_count = len(stage_calls)
        builder_replay = run_bounded_execution_unit(
            runtime,
            activation_id="activation-c05-builder",
            local_config=local_config,
        )
        assert builder_replay.code == "runtime_failure_recovery_applied"
        assert builder_replay.transition_disposition == "replayed"
        assert len(stage_calls) == request_count
        assert runtime.store.load_runtime_state(runtime.cas_store) == after_builder

        second = run_bounded_execution_unit(
            runtime,
            activation_id=troubleshooter_one.activation_id,
            local_config=local_config,
        )
        assert second.code == "runtime_failure_recovery_applied", second
        assert second.accepted is True
        after_second, second_run, second_completion = _failure_records(
            runtime,
            second,
            expected_stage="lad_troubleshooter",
            fingerprint=fingerprint,
        )
        assert second_run.work_item_id == builder_run.work_item_id
        assert after_second.counters == initial_counters
        attempt = next(iter(after_second.recovery_attempts.values()))
        assert (attempt.attempt_count, attempt.phase) == (2, "pending_cooldown")
        wait = next(iter(after_second.cooldown_waits.values()))
        policy = next(
            policy
            for policy in plan.recovery_policies
            if str(policy.id) == "execution.runtime_failure_recovery"
        )
        assert str(policy.cooldown_wait_state_id) == (
            "execution.runtime_failure_recovery.cooldown"
        )
        assert str(wait.policy_id) == str(policy.id)
        assert wait.attempt_count == 2
        assert wait.created_at + policy.default_cooldown_seconds == wait.due_at
        assert wait.source_run_id == second_run.run_ref.run_id
        assert wait.source_activation_id == second_run.activation_id
        assert str(wait.recovery_action_id) == (
            "execution.recover_troubleshooter_runtime_failure"
        )
        assert str(wait.target_stage_kind_id) == "lad_troubleshooter"
        assert (
            str(wait.target_runner_binding_id) == "lad_troubleshooter.millforge_runner"
        )
        assert wait.consumed_input_id is None
        assert not any(
            route.created_by_input_id == second_completion.application_input_id
            for route in after_second.activation_routes
        )
        assert runtime_failure_applied(after_second, second_run.run_ref.run_id)
        request_count = len(stage_calls)
        second_replay = run_bounded_execution_unit(
            runtime,
            activation_id=troubleshooter_one.activation_id,
            local_config=local_config,
        )
        assert second_replay.code == "runtime_failure_recovery_applied"
        assert second_replay.transition_disposition == "replayed"
        assert len(stage_calls) == request_count
        assert runtime.store.load_runtime_state(runtime.cas_store) == after_second

        after_timer = _accepted(
            after_second,
            TimerDue(
                "c05-runtime-failure-cooldown-due",
                wait_id=wait.wait_id,
                observed_at=wait.due_at,
            ),
            activation_id="activation-c05-troubleshooter-after-cooldown",
        )
        runtime.store.persist_runtime_state(after_timer, runtime.cas_store)
        runtime = _reopen(runtime)
        after_timer = runtime.store.load_runtime_state(runtime.cas_store)
        assert after_timer.counters == initial_counters
        assert runtime_failure_applied(after_timer, builder_run.run_ref.run_id)
        assert runtime_failure_applied(after_timer, second_run.run_ref.run_id)
        timer_wait = after_timer.cooldown_waits[wait.wait_id]
        assert timer_wait.consumed_input_id == "c05-runtime-failure-cooldown-due"
        troubleshooter_three = after_timer.activations[
            cast(str, timer_wait.resulting_recovery_activation_id)
        ]
        assert (
            troubleshooter_three.activation_id
            == "activation-c05-troubleshooter-after-cooldown"
        )
        assert troubleshooter_three.work_item_id == builder_run.work_item_id
        assert str(troubleshooter_three.stage_kind_id) == "lad_troubleshooter"
        assert str(troubleshooter_three.plan_ref.authority_fingerprint) == fingerprint

        third = run_bounded_execution_unit(
            runtime,
            activation_id=troubleshooter_three.activation_id,
            local_config=local_config,
        )
        assert third.code == "runtime_failure_recovery_applied", third
        assert third.accepted is True
        after_third, third_run, _third_completion = _failure_records(
            runtime,
            third,
            expected_stage="lad_troubleshooter",
            fingerprint=fingerprint,
        )
        assert third_run.work_item_id == builder_run.work_item_id
        final_attempt = next(iter(after_third.recovery_attempts.values()))
        assert (final_attempt.attempt_count, final_attempt.phase) == (
            3,
            "quarantine_eligible",
        )
        assert len(after_third.lineage_quarantines) == 1
        assert after_third.counters == initial_counters
        for run in (builder_run, second_run, third_run):
            assert runtime_failure_applied(after_third, run.run_ref.run_id)
        assert stage_calls == [
            "lad_builder",
            "lad_troubleshooter",
            "lad_troubleshooter",
        ]
        assert (
            authority_fingerprint(plan) == fingerprint == _EXPECTED_FULL_PI_FINGERPRINT
        )
        assert after_third.admitted_plans[fingerprint].selected_plan == plan
    finally:
        runtime.close()
