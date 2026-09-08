from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from millrace.adapters.cli.context import CliWorkspacePaths, OpenRuntimeContext
from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.cli.status import _attribution_projection, _cleanup_projection
from millrace.adapters.runner_contract import (
    AdapterAttribution,
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
from millrace.contracts.context_checkout import decode_context_checkout_manifest
from millrace.contracts.transition import (
    AdmitPlan,
    EnqueueWork,
    InitializeWorkspace,
    SelectDefaultPlan,
    TransitionInput,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore

from support import package_conformance as conformance
from support.public_runtime_fakes import (
    deterministic_context,
    materialize_fake_runner_session_cas,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"


def _operation(operation: str) -> RunnerCancellationOperationResult:
    diagnostic = {"operation": operation}
    return RunnerCancellationOperationResult(
        operation,
        "unsupported",
        0,
        0,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


class _ImmediateHandle:
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


class _SimpleLoopAdapter:
    adapter_kind = "millforge"

    def __init__(
        self,
        workspace: Path,
        *,
        block_manager: bool = False,
        mutate_worker_root: bool = False,
    ) -> None:
        self.workspace = workspace
        self.block_manager = block_manager
        self.mutate_worker_root = mutate_worker_root
        self.requests: list[AdapterInvocationRequest] = []

    def start_session(self, request: AdapterInvocationRequest) -> StartedSession:
        self.requests.append(request)
        stage = request.dispatch_envelope.stage_kind_id
        marker, artifact = _result_for_stage(stage)
        if self.block_manager and stage == "simple_loop.manager":
            marker = "BLOCKED"
            artifact = None
        if self.mutate_worker_root and stage == "simple_loop.worker":
            (self.workspace / "docs" / "context.md").write_text(
                "mutated by forbidden worker\n",
                encoding="utf-8",
            )
            marker = "BLOCKED"
            artifact = None
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
            artifact_payload_candidate=artifact,
            attribution=AdapterAttribution(
                wrapper_input_bytes=23,
                retained_result_bytes=31,
                runner_wall_milliseconds=47,
            ),
        )
        return StartedSession(echo, _ImmediateHandle(outcome), "simple-loop", {})

    def reconcile_session(self, request: object) -> object:
        raise AssertionError(f"unexpected reconciliation: {request!r}")


def _result_for_stage(stage: str) -> tuple[str, Mapping[str, object]]:
    if stage == "simple_loop.manager":
        return (
            "PACKET_READY",
            {
                "artifact_kind": "simple_loop.work_packet",
                "source_prompt_id": "prompt-1",
                "title": "Lifecycle proof",
                "objective": "Exercise the packaged simple loop.",
                "completion_definition": "The governed lifecycle is durable.",
                "evidence": ["The source prompt was checked."],
                "assumptions": [],
            },
        )
    if stage == "simple_loop.worker":
        return (
            "WORK_DONE",
            {
                "artifact_kind": "simple_loop.work_result",
                "summary": "The lifecycle proof is complete.",
                "evidence": ["The completion definition was checked."],
                "assumptions": [],
            },
        )
    if stage == "simple_loop.reviewer":
        return (
            "ACCEPTED",
            {
                "changes": [],
                "proposals": [],
                "no_op_reason": "No selected documentation write was required.",
            },
        )
    if stage == "simple_loop.troubleshooter":
        return (
            "UNRESOLVED",
            {
                "artifact_kind": "simple_loop.troubleshooting_report",
                "result": "The required source remains absent.",
                "blocker_cause": "The required source was not supplied.",
                "attempted_repair": "No repair was attempted.",
                "next_route": "unresolved_return",
                "evidence": ["Manager returned BLOCKED."],
                "assumptions": [],
            },
        )
    raise AssertionError(f"unexpected stage: {stage}")


def _accepted(state: object, transition_input: TransitionInput) -> object:
    context = deterministic_context(
        transition_id=f"transition:{transition_input.input_id}",
        work_item_id="work-prompt",
        activation_id="activation-manager",
    )
    decision = decide(state, transition_input, context)
    assert decision.accepted, decision.refusal
    return apply(state, decision)


def _runtime(tmp_path: Path) -> OpenRuntimeContext:
    plan = conformance.select_and_verify_package(
        tmp_path / "selected-package",
        PACKAGE_ROOT,
        package_id="millrace.plus.official",
        package_version="0.22.3",
        workflow_id="simple_loop",
        workflow_version="0.1",
    )
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("initialize"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        EnqueueWork(
            "enqueue",
            queue_family_id=QueueFamilyId("work_prompt"),
            payload={"prompt_id": "prompt-1", "body": "Prove the lifecycle."},
        ),
    ):
        state = _accepted(state, transition_input)

    workspace = tmp_path / "workspace"
    db_path = workspace / ".millrace" / "runtime.sqlite3"
    cas_path = workspace / ".millrace" / "cas"
    db_path.parent.mkdir(parents=True)
    cas_path.mkdir(parents=True)
    store = SQLiteRuntimeStore.initialize(db_path)
    cas_store = ContentAddressedByteStore(cas_path)
    state = materialize_fake_runner_session_cas(state=state, cas_store=cas_store)
    store.persist_runtime_state(state, cas_store)
    docs = workspace / "docs"
    docs.mkdir()
    (docs / "context.md").write_text("governed source\n", encoding="utf-8")
    return OpenRuntimeContext(
        CliWorkspacePaths(workspace, db_path, cas_path),
        store,
        cas_store,
    )


def _config(adapter: _SimpleLoopAdapter) -> AdapterLocalConfig:
    return AdapterLocalConfig(adapters={"millforge": adapter})


def test_packaged_simple_loop_persists_public_status_and_cleanup_evidence(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    adapter = _SimpleLoopAdapter(runtime.paths.workspace_path)

    results = [
        run_bounded_execution_unit(runtime, local_config=_config(adapter))
        for _ in range(3)
    ]
    state = runtime.store.load_runtime_state(runtime.cas_store)
    assert [result.code for result in results] == ["observation_accepted"] * 3, [
        (result.code, result.adapter_error_kind, result.diagnostics)
        for result in results
    ] + [(record.input_kind, record.reason) for record in state.refusals]
    assert [
        request.dispatch_envelope.stage_kind_id for request in adapter.requests
    ] == ["simple_loop.manager", "simple_loop.worker", "simple_loop.reviewer"]
    reviewer_request = next(
        request
        for request in adapter.requests
        if request.dispatch_envelope.stage_kind_id == "simple_loop.reviewer"
    )
    session = state.runner_sessions[reviewer_request.session_id]
    manifest_digest = session.context_manifest_digest

    assert manifest_digest is not None
    manifest_bytes = runtime.cas_store.get_bytes(manifest_digest)
    manifest = decode_context_checkout_manifest(manifest_bytes)
    checkout = (
        runtime.paths.workspace_path
        / "simple-loop-reviewer"
        / session.session_id
        / str(session.dispatch_generation)
    )
    attribution = _attribution_projection(runtime, session)
    cleanup = _cleanup_projection(runtime, session)

    assert attribution["status"] == "available"
    assert attribution["final"] is True
    assert attribution["metrics"]["wrapper_input_bytes"] == {
        "value": 23,
        "source": "adapter.direct",
        "availability": "observed",
    }
    assert cleanup["status"] == "available"
    assert cleanup["removed_path_classes"] == ("context_checkout",)
    assert not checkout.exists()
    assert runtime.cas_store.get_bytes(manifest_digest) == manifest_bytes
    assert manifest.files
    assert state.runner_session_completions[session.session_id].terminal_state == (
        "completed"
    )
    application_input_id = state.runner_session_completions[
        session.session_id
    ].application_input_id
    assert application_input_id in state.receipts
    assert "session_fencing_token" not in json.dumps(attribution, sort_keys=True)
    runtime.close()


def test_packaged_simple_loop_refuses_forbidden_recovery_root_mutation(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    adapter = _SimpleLoopAdapter(
        runtime.paths.workspace_path,
        mutate_worker_root=True,
    )
    first = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    before_worker = runtime.store.load_runtime_state(runtime.cas_store)
    second = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    after = runtime.store.load_runtime_state(runtime.cas_store)
    worker_request = next(
        request
        for request in adapter.requests
        if request.dispatch_envelope.stage_kind_id == "simple_loop.worker"
    )

    assert first.code == "observation_accepted"
    assert second.code == "adapter_failure"
    assert second.adapter_error_kind == "context_mutation_refused"
    assert after.runner_observations == before_worker.runner_observations
    before_refusals = {record.record_id for record in before_worker.refusals}
    assert any(
        record.record_id not in before_refusals
        and record.reason == "context_mutation_refused"
        for record in after.refusals
    )
    completion = after.runner_session_completions[worker_request.session_id]
    assert completion.terminal_state == "failed"
    assert completion.adapter_error_kind == "context_mutation_refused"
    attribution = _attribution_projection(
        runtime,
        after.runner_sessions[worker_request.session_id],
    )
    assert attribution["status"] == "available"
    assert attribution["final"] is True
    metrics = attribution["metrics"]
    assert metrics["manifest_bytes"] == {
        "value": 1581,
        "source": "runtime.context_manifest",
        "availability": "observed",
    }
    assert metrics["catalog_bytes"] == {
        "value": 16,
        "source": "runtime.context_manifest",
        "availability": "derived",
    }
    assert metrics["catalog_file_count"] == {
        "value": 1,
        "source": "runtime.context_manifest",
        "availability": "derived",
    }
    assert metrics["hydrated_bytes"] == {
        "value": 0,
        "source": "runtime.hydration_receipts",
        "availability": "derived",
    }
    assert metrics["hydrated_file_count"] == {
        "value": 0,
        "source": "runtime.hydration_receipts",
        "availability": "derived",
    }
    assert metrics["distinct_content_digest_count"] == {
        "value": 0,
        "source": "runtime.hydration_receipts",
        "availability": "derived",
    }
    assert metrics["wrapper_input_bytes"] == {
        "value": 23,
        "source": "adapter.direct",
        "availability": "observed",
    }
    assert metrics["retained_result_bytes"] == {
        "value": 31,
        "source": "adapter.direct",
        "availability": "observed",
    }
    assert metrics["runner_wall_milliseconds"] == {
        "value": 47,
        "source": "adapter.direct",
        "availability": "observed",
    }
    runtime.close()


def test_packaged_simple_loop_prepares_troubleshooter_after_artifactless_block(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    troubleshooting = runtime.paths.workspace_path / "troubleshooting"
    troubleshooting.mkdir()
    (troubleshooting / "guide.md").write_text(
        "Report evidence without inventing missing inputs.\n",
        encoding="utf-8",
    )
    adapter = _SimpleLoopAdapter(
        runtime.paths.workspace_path,
        block_manager=True,
    )

    manager = run_bounded_execution_unit(runtime, local_config=_config(adapter))
    troubleshooter = run_bounded_execution_unit(
        runtime,
        local_config=_config(adapter),
    )
    state = runtime.store.load_runtime_state(runtime.cas_store)

    assert manager.code == "observation_accepted"
    assert troubleshooter.code == "observation_accepted", (
        troubleshooter.code,
        troubleshooter.adapter_error_kind,
        troubleshooter.diagnostics,
        [(record.input_kind, record.reason) for record in state.refusals],
    )
    assert [
        request.dispatch_envelope.stage_kind_id for request in adapter.requests
    ] == ["simple_loop.manager", "simple_loop.troubleshooter"]
    request = adapter.requests[-1]
    session = state.runner_sessions[request.session_id]
    manifest = decode_context_checkout_manifest(
        runtime.cas_store.get_bytes(session.context_manifest_digest)
    )
    assert (
        "selected_artifacts",
        "direct_predecessors",
        "source_missing",
    ) in {
        (omission.source_kind, omission.source_ref, omission.reason)
        for omission in manifest.omissions
    }
    runtime.close()
