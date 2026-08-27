from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import millrace.kernel._closure_lifecycle as _closure
import pytest
from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.contracts.compiled_plan import AuthorityValue
from millrace.contracts.runner import (
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
    runner_result_payload,
)
from millrace.contracts.schema import validate_schema
from millrace.contracts.state import RunRecord, RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    EvaluateCompletionBehavior,
    InitializeWorkspace,
    OpenClosureTarget,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionContext,
    TransitionInput,
    canonical_authority_mapping_bytes,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.kernel.decision import _completion_request_payload
from millrace.testing import (
    decide_with_fake_runner_completion,
    deterministic_context,
    fake_runner_observation_payload,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.3"
WORKFLOW_ID = "planning.lad"
RECON_WORKFLOW_IDS = ("planning.lad", "lad.full")
ARBITER_WORKFLOW_IDS = ("planning.lad", "lad.full")
Record = dict[str, object]

UNRELATED_ARBITER_OBSERVATION = {
    "observation_id": "unrelated-observation",
    "summary": "The repository contains no additional closure-relevant finding.",
}

EXPECTED_CLOSURE_EVIDENCE_SCHEMA_IDS = {
    "planning.lad": (
        "planning.artifacts.task_cards",
        "planning.artifacts.report",
        "planning.artifacts.incident_report",
        "execution.artifacts.checker_result",
        "execution.artifacts.doublecheck_result",
        "execution.artifacts.report",
        "execution.artifacts.incident_report",
    ),
    "lad.full": (
        "planning.artifacts.task_cards",
        "planning.artifacts.report",
        "planning.artifacts.incident_report",
        "execution.artifacts.checker_result",
        "execution.artifacts.doublecheck_result",
        "execution.artifacts.report",
        "execution.artifacts.incident_report",
        "learning.artifacts.curator_decision",
        "learning.artifacts.skill_install_report",
        "learning.artifacts.skill_disposition",
        "learning.artifacts.report",
    ),
}

EXPECTED_RECON_AUTHORITY = {
    "RECON_TO_EXECUTION": (
        "planning.recon.to_execution",
        "route",
        "planning.recon_enqueue_task",
        (
            "lad_builder.millforge_runner",
            "lad_builder",
            "execution.lad.builder.start",
            "task",
        ),
        "execution.artifacts.task",
    ),
    "RECON_TO_PLANNING": (
        "planning.recon.to_planning",
        "route",
        "planning.recon_enqueue_spec",
        (
            "lad_planner.millforge_runner",
            "lad_planner",
            "planning.lad.planner.start",
            "spec",
        ),
        "planning.artifacts.generated_spec",
    ),
    "RECON_NOOP": (
        "planning.recon.noop",
        "complete_work_item",
        "planning.recon_noop",
        (None, None, None, None),
        "planning.artifacts.recon_packet",
    ),
    "RECON_BLOCKED": (
        "planning.recon.recon_blocked",
        "block_work_item",
        "planning.recon_block_work_item",
        (None, None, None, None),
        "planning.artifacts.report",
    ),
    "BLOCKED": (
        "planning.recon.blocked",
        "block_work_item",
        "planning.recon_blocked",
        (None, None, None, None),
        "planning.artifacts.report",
    ),
}


def _manifest() -> dict[str, Any]:
    return conformance.assert_packaged_asset_closure(PACKAGE_ROOT)


def _source() -> dict[str, object]:
    return conformance.packaged_workflow_source(PACKAGE_ROOT, WORKFLOW_ID)


def _records(source: dict[str, object], section: str) -> list[Record]:
    return cast(list[Record], source[section])


def _record(source: dict[str, object], section: str, record_id: str) -> Record:
    return next(item for item in _records(source, section) if item["id"] == record_id)


def _error(result: object, code: str, path_suffix: str | None = None) -> object:
    return next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
        and diagnostic.code == code
        and (path_suffix is None or diagnostic.declaration_path.endswith(path_suffix))
    )


def _json_block_after(text: str, heading: str) -> object:
    section = text.split(heading, maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    json_block = section.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return json.loads(json_block)


def _arbiter_authority(
    plan: object,
) -> tuple[object, object, object, dict[str, object], dict[str, object]]:
    stage = next(stage for stage in plan.stage_kinds if str(stage.id) == "lad_arbiter")
    binding = next(
        binding
        for binding in plan.runner_bindings
        if str(binding.id) == "lad_arbiter.millforge_runner"
    )
    behavior = next(
        behavior
        for behavior in plan.completion_behaviors
        if str(behavior.id) == "planning.closure.completion"
    )
    outcomes = {
        str(outcome.id): outcome
        for outcome in plan.terminal_outcomes
        if str(outcome.stage_kind_id) == "lad_arbiter"
    }
    actions = {
        str(action.outcome_id): action
        for action in plan.terminal_actions
        if str(action.stage_kind_id) == "lad_arbiter"
    }
    return stage, binding, behavior, outcomes, actions


def _closure_verdict_payload() -> dict[str, Any]:
    return {
        "artifact_kind": "planning.artifacts.verdict",
        "summary": "The selected closure criteria were evaluated.",
        "closure_target_id": "closure-1",
        "root_contract_digest": "sha256:" + "a" * 64,
        "freshness_anchor_digest": "sha256:" + "b" * 64,
        "rubric": {
            "criteria": [
                {
                    "criterion_id": "criterion-1",
                    "requirement": "The selected requirement is satisfied.",
                    "evidence_rule": "Use current evidence.",
                }
            ]
        },
        "criterion_results": [
            {
                "criterion_id": "criterion-1",
                "status": "passed",
                "provenance": "fresh",
                "evidence_refs": [
                    {
                        "evidence_id": "evidence-1",
                        "summary": "Current evidence supports the criterion.",
                    }
                ],
            }
        ],
        "observations": [],
        "remediation_guidance": [],
        "confidence": "high",
        "residual_uncertainty": "none",
    }


def _arbiter_json_examples(text: str) -> dict[str, Any]:
    pattern = re.compile(
        r"^### (?P<label>[^\n]+)\n+```json\n(?P<payload>.*?)\n```",
        re.MULTILINE | re.DOTALL,
    )
    return {
        match.group("label"): json.loads(match.group("payload"))
        for match in pattern.finditer(text)
    }


def _apply_planning_input(
    state: RuntimeState,
    transition_input: TransitionInput,
    context: TransitionContext,
) -> RuntimeState:
    decision = decide(state, transition_input, context)
    assert decision.accepted, decision.refusal
    return apply(state, decision)


def _arbiter_target_id(state: RuntimeState) -> str:
    assert len(state.closure_targets) == 1
    return next(iter(state.closure_targets))


def _arbiter_evaluate_transition(
    state: RuntimeState,
    behavior: Any,
) -> tuple[EvaluateCompletionBehavior, TransitionContext]:
    target = state.closure_targets[_arbiter_target_id(state)]
    target_key = _closure.closure_target_key_for(target)
    progress = _closure.closure_target_progress(
        state,
        target=target,
        behavior=behavior,
    )
    assert progress.status == "ready", progress
    readiness = _closure.assess_closure_readiness(
        state,
        lineage_id=target_key.lineage_id,
        plan_ref=target_key.selected_plan_ref,
        target_key=target_key,
    )
    assert readiness.status == "settled"
    input_id, context = _closure.closure_lifecycle_identity(
        "evaluate",
        target_key,
        readiness.anchor_digest,
        progress.evidence_anchor,
    )
    return (
        EvaluateCompletionBehavior(
            input_id,
            selected_plan_ref=target.selected_plan_ref,
            completion_behavior_id=str(behavior.id),
            closure_target_id=target.closure_target_id,
        ),
        context,
    )


def _arbiter_root_state(
    plan: object,
    *,
    body: str = "Evaluate this closure target against the selected contract.",
) -> tuple[RuntimeState, str, object, str]:
    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace("initialize-arbiter-proof"),
        AdmitPlan(
            "admit-arbiter-proof",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(
            "select-arbiter-proof",
            authority_fingerprint=fingerprint,
        ),
    ):
        state = _apply_planning_input(
            state,
            transition_input,
            deterministic_context(
                transition_id=f"{transition_input.input_id}-transition"
            ),
        )

    external_route = next(
        route
        for route in plan.external_enqueue_routes
        if str(route.queue_family_id) == "spec"
    )
    root_payload: dict[str, object] = {
        "title": "Arbiter proof root contract",
        "body": body,
        "root_source": {"kind": "spec", "source_id": "arbiter-proof-spec"},
    }
    state = _apply_planning_input(
        state,
        EnqueueWork(
            "enqueue-arbiter-proof-root",
            queue_family_id=external_route.queue_family_id,
            payload=cast(Mapping[str, AuthorityValue], root_payload),
        ),
        deterministic_context(
            transition_id="transition-enqueue-arbiter-proof-root",
            work_item_id="arbiter-proof-root",
            activation_id="arbiter-proof-root-activation",
        ),
    )
    root = state.work_items["arbiter-proof-root"]
    assert root.lineage_id is not None
    state, root_run = _claim_planning_activation(
        state,
        "arbiter-proof-root-activation",
        tag="arbiter-proof-root",
    )
    planner_decision = _arbiter_action_decision(
        state,
        plan,
        fingerprint,
        run=root_run,
        action_id="planning.route_planner_complete",
        tag="arbiter-proof-root",
        artifact_payload={
            "artifact_kind": "planning.artifacts.stage_result",
            "summary": "The planner completed the root contract.",
        },
    )
    assert planner_decision.accepted, planner_decision.refusal
    state = apply(state, planner_decision)
    state, manager_run = _claim_planning_activation(
        state,
        "activation-target-arbiter-proof-root",
        tag="arbiter-proof-manager",
    )
    manager_decision = _arbiter_action_decision(
        state,
        plan,
        fingerprint,
        run=manager_run,
        action_id="planning.close_manager_complete",
        tag="arbiter-proof-manager",
        artifact_payload={
            "artifact_kind": "task_cards",
            "cards": [
                {
                    "task_card_id": "arbiter-proof-card",
                    "title": "Arbiter proof",
                    "body": "The root contract is ready for closure evaluation.",
                }
            ],
        },
    )
    assert manager_decision.accepted, manager_decision.refusal
    state = apply(state, manager_decision)
    behavior = next(
        behavior
        for behavior in plan.completion_behaviors
        if str(behavior.id) == "planning.closure.completion"
    )
    plan_ref = state.default_plan_ref
    assert plan_ref is not None
    open_target = OpenClosureTarget(
        "open-arbiter-proof",
        selected_plan_ref=plan_ref,
        completion_behavior_id=str(behavior.id),
        closure_target_id="arbiter-proof-target",
        lineage_id=root.lineage_id,
        root_source_kind="spec",
        root_source_id="arbiter-proof-spec",
        closure_root_work_item_id=root.ref.work_item_id,
        request_kind=behavior.request_kind,
        target_graph_node_id=behavior.target_graph_node_id,
        evidence_window={"kind": "lineage", "lineage_id": root.lineage_id},
    )
    target_key = _closure.closure_target_key_for(open_target)
    readiness = _closure.assess_closure_readiness(
        state,
        lineage_id=target_key.lineage_id,
        plan_ref=target_key.selected_plan_ref,
        target_key=target_key,
    )
    assert readiness.status == "settled"
    open_input_id, open_context = _closure.closure_lifecycle_identity(
        "open",
        target_key,
        readiness.anchor_digest,
    )
    state = _apply_planning_input(
        state,
        replace(
            open_target,
            input_id=open_input_id,
            closure_target_id=_closure.closure_target_id(target_key),
        ),
        open_context,
    )
    return state, fingerprint, behavior, root.ref.work_item_id


def _claim_planning_activation(
    state: RuntimeState,
    activation_id: str,
    *,
    tag: str,
) -> tuple[RuntimeState, RunRecord]:
    activation = state.activations[activation_id]
    run_id = f"run-{tag}"
    state = _apply_planning_input(
        state,
        ClaimWork(f"claim-{tag}", activation_id=activation_id),
        deterministic_context(
            transition_id=f"transition-claim-{tag}",
            work_item_id=activation.work_item_id,
            activation_id=activation_id,
            run_id=run_id,
            claim_id=f"claim-{tag}",
            fencing_token=f"fence-{tag}",
        ),
    )
    return state, state.runs[run_id]


def _arbiter_marker(plan: object, action_id: str) -> str:
    action = next(
        action for action in plan.terminal_actions if str(action.id) == str(action_id)
    )
    return next(
        str(outcome.marker)
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )


def _arbiter_action_decision(
    state: RuntimeState,
    plan: object,
    fingerprint: str,
    *,
    run: RunRecord,
    action_id: str,
    tag: str,
    artifact_payload: Mapping[str, AuthorityValue],
) -> object:
    activation = state.activations[run.activation_id]
    observation = RunnerResultObserved(
        f"observe-{tag}",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker=_arbiter_marker(plan, action_id),
            artifact_payload=artifact_payload,
        ),
        observed_at=0,
    )
    return decide_with_fake_runner_completion(
        state,
        observation,
        deterministic_context(
            transition_id=f"transition-observe-{tag}",
            work_item_id=f"work-target-{tag}",
            activation_id=f"activation-target-{tag}",
            run_id=run.run_ref.run_id,
            claim_id=run.run_ref.claim_id,
            fencing_token=run.run_ref.fencing_token,
        ),
    )


def _run_arbiter_action(
    state: RuntimeState,
    plan: object,
    fingerprint: str,
    *,
    run: RunRecord,
    action_id: str,
    tag: str,
    artifact_payload: Mapping[str, AuthorityValue],
) -> tuple[RuntimeState, object, str]:
    decision = _arbiter_action_decision(
        state,
        plan,
        fingerprint,
        run=run,
        action_id=action_id,
        tag=tag,
        artifact_payload=artifact_payload,
    )
    assert decision.accepted, decision.refusal
    return apply(state, decision), decision, f"activation-target-{tag}"


def _arbiter_rubric() -> dict[str, object]:
    return {
        "criteria": [
            {
                "criterion_id": "root-contract-criterion",
                "requirement": "The closure target satisfies the root contract.",
                "evidence_rule": "Use current evidence produced after the anchor.",
            }
        ]
    }


def _arbiter_verdict(
    snapshot: Mapping[str, object],
    rubric: Mapping[str, object],
    *,
    marker: str,
    evidence_id: str,
    observations: tuple[Mapping[str, object], ...] = (),
) -> dict[str, object]:
    gap = marker == "gap"
    blocked = marker == "blocked"
    return {
        "artifact_kind": "planning.artifacts.verdict",
        "summary": "The selected closure contract was evaluated.",
        "closure_target_id": snapshot["closure_target_id"],
        "root_contract_digest": cast(
            Mapping[str, object], snapshot["root_contract"]
        )["payload_digest"],
        "freshness_anchor_digest": snapshot["freshness_anchor_digest"],
        "rubric": rubric,
        "criterion_results": [
            {
                "criterion_id": "root-contract-criterion",
                "status": "blocked" if blocked else "failed" if gap else "passed",
                "provenance": "missing" if blocked else "fresh",
                "evidence_refs": []
                if blocked
                else [{"evidence_id": evidence_id, "summary": "Current evidence."}],
            }
        ],
        "observations": [dict(observation) for observation in observations],
        "remediation_guidance": (
            [
                {
                    "guidance_id": "guidance-root-contract",
                    "summary": "Address the root-contract criterion.",
                    "criterion_refs": [
                        {"criterion_id": "root-contract-criterion"}
                    ],
                }
            ]
            if gap
            else []
        ),
        "confidence": "high",
        "residual_uncertainty": "none",
    }


def _settle_arbiter_returned_auditor(
    state: RuntimeState,
    plan: object,
    fingerprint: str,
    *,
    activation_id: str,
    tag: str,
) -> RuntimeState:
    state, auditor_run = _claim_planning_activation(
        state,
        activation_id,
        tag=f"{tag}-auditor",
    )
    state, _, planner_activation_id = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=auditor_run,
        action_id="planning.route_auditor_complete",
        tag=f"{tag}-auditor-complete",
        artifact_payload={
            "artifact_kind": "planning.artifacts.stage_result",
            "summary": "The auditor returned the recovered evidence.",
        },
    )
    state, planner_run = _claim_planning_activation(
        state,
        planner_activation_id,
        tag=f"{tag}-planner",
    )
    state, _, manager_activation_id = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=planner_run,
        action_id="planning.route_planner_complete",
        tag=f"{tag}-planner-complete",
        artifact_payload={
            "artifact_kind": "planning.artifacts.stage_result",
            "summary": "The planner completed the returned evidence.",
        },
    )
    state, manager_run = _claim_planning_activation(
        state,
        manager_activation_id,
        tag=f"{tag}-manager",
    )
    state, _, _ = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=manager_run,
        action_id="planning.close_manager_complete",
        tag=f"{tag}-manager-complete",
        artifact_payload={
            "artifact_kind": "task_cards",
            "cards": [
                {
                    "task_card_id": f"{tag}-card",
                    "title": "Recovered evidence",
                    "body": "The returned evidence is settled.",
                }
            ],
        },
    )
    return state


def _arbiter_boundary_fixture(
    plan: object,
    *,
    target_bytes: int,
) -> tuple[RuntimeState, str, object, object, Mapping[str, AuthorityValue], str]:
    stage, _binding, behavior, _outcomes, _actions = _arbiter_authority(plan)
    _base_state, fingerprint, base_behavior, _root_work_item_id = (
        _arbiter_root_state(plan)
    )

    def with_body(body: str) -> RuntimeState:
        state, _fingerprint, _behavior, _root_id = _arbiter_root_state(
            plan,
            body=body,
        )
        return state

    for suffix_length in range(4):
        empty_body = "x" * (suffix_length + 1)
        empty_state = with_body(empty_body)
        empty_target = empty_state.closure_targets[_arbiter_target_id(empty_state)]
        empty_payload, refusal = _completion_request_payload(
            state=empty_state,
            target=empty_target,
            behavior=base_behavior,
            stage=stage,
        )
        assert refusal is None
        assert empty_payload is not None
        remaining = target_bytes - len(canonical_authority_mapping_bytes(empty_payload))
        if remaining < 4 or remaining % 4:
            continue
        body = "𐀀" * (remaining // 4) + empty_body
        candidate_state = with_body(body)
        candidate_target = candidate_state.closure_targets[
            _arbiter_target_id(candidate_state)
        ]
        payload, refusal = _completion_request_payload(
            state=candidate_state,
            target=candidate_target,
            behavior=base_behavior,
            stage=stage,
        )
        assert refusal is None
        assert payload is not None
        if len(canonical_authority_mapping_bytes(payload)) != target_bytes:
            continue

        final_state, final_fingerprint, final_behavior, final_root_id = (
            _arbiter_root_state(plan, body=body)
        )
        final_target = final_state.closure_targets[_arbiter_target_id(final_state)]
        final_root = final_state.work_items[final_root_id]
        final_payload, final_refusal = _completion_request_payload(
            state=final_state,
            target=final_target,
            behavior=final_behavior,
            stage=stage,
        )
        assert final_refusal is None
        assert final_payload is not None
        assert len(canonical_authority_mapping_bytes(final_payload)) == target_bytes
        assert final_root.payload["body"] == body
        return (
            final_state,
            final_fingerprint,
            final_behavior,
            stage,
            final_payload,
            body,
        )
    raise AssertionError("could not materialize exact closure request boundary")


def _arbiter_selected_asset_material(stage: object) -> dict[str, object]:
    manifest = _manifest()
    assets = conformance.assets_by_id(manifest)
    return {
        str(asset_id): {
            "body": (
                PACKAGE_ROOT / str(assets[str(asset_id)]["package_path"])
            ).read_text()
        }
        for asset_id in stage.asset_ids
    }


def _arbiter_terminal_options(
    plan: object,
    binding: object,
) -> tuple[dict[str, object], ...]:
    _stage, _binding, _behavior, outcomes, actions = _arbiter_authority(plan)
    return tuple(
        {
            "outcome_id": str(outcome.id),
            "marker": outcome.marker,
            "action_id": str(action.id),
            "action_kind": action.action_kind,
            "artifact_schema_id": (
                None
                if action.artifact_schema_id is None
                else str(action.artifact_schema_id)
            ),
        }
        for mapping in binding.terminal_result_mappings
        for outcome in (outcomes[str(mapping.outcome_id)],)
        for action in (actions[str(outcome.id)],)
    )


class _ArbiterSelectedOutputRequirement(SimpleNamespace):
    def __init__(
        self,
        *,
        required: bool,
        json_schema: dict[str, object],
    ) -> None:
        canonical = json.dumps(
            json_schema,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        super().__init__(
            required=required,
            json_schema=json_schema,
            canonical_schema_bytes=canonical,
            schema_sha256=hashlib.sha256(canonical).hexdigest(),
        )


class _ArbiterTerminalSelectedOutputRequirement(SimpleNamespace):
    pass


class _ArbiterSelectedOutputAbsent(SimpleNamespace):
    def __init__(self) -> None:
        super().__init__(present=False)


class _ArbiterSelectedOutputPresent(SimpleNamespace):
    def __init__(self, value: object) -> None:
        super().__init__(present=True, value=value)


def _arbiter_boundary_provider() -> ModuleType:
    provider = ModuleType("millforge")
    for provider_record_name in (
        "StageIdentity",
        "HarnessTaskInput",
        "CompiledHarnessIdentity",
        "CompiledHarnessHash",
        "CompiledHarnessRef",
        "CapabilityGrant",
        "CapabilityEnvelope",
        "RunDirRef",
        "TimeoutRef",
        "CancellationRef",
        "ModelProfileRef",
    ):
        setattr(
            provider,
            provider_record_name,
            lambda **kwargs: SimpleNamespace(**kwargs),
        )
    provider.HarnessExecutionRequest = lambda **kwargs: SimpleNamespace(**kwargs)
    provider.SelectedOutputRequirement = _ArbiterSelectedOutputRequirement
    provider.TerminalSelectedOutputRequirement = (
        _ArbiterTerminalSelectedOutputRequirement
    )
    provider.SelectedOutputAbsent = _ArbiterSelectedOutputAbsent
    provider.SelectedOutputPresent = _ArbiterSelectedOutputPresent
    return provider


class _ArbiterBoundaryFacade:
    def __init__(
        self,
        pin: object,
        *,
        observations: tuple[Mapping[str, object], ...] = (),
    ) -> None:
        self.calls = 0
        self.instructions: list[str] = []
        self.requests: list[object] = []
        self._pin = pin
        self._observations = observations
        self.descriptor = SimpleNamespace(
            runner_id=pin.component_id,
            runner_version=pin.component_version,
            package_name=pin.provider_distribution,
            package_version=pin.provider_version,
            descriptor_sha256=pin.descriptor_sha256,
            required_capability_ids=tuple(
                str(value) for value in pin.required_capability_ids
            ),
            legal_terminal_result_ids=tuple(pin.legal_terminal_result_ids),
        )
        self.components = SimpleNamespace(
            options=SimpleNamespace(load_context_files=False),
            metadata=SimpleNamespace(context_file_count=0),
            compiled_plan=SimpleNamespace(
                harness_id=pin.component_id,
                harness_version=pin.component_version,
                compiled_sha256="sha256:" + "c" * 64,
            ),
            capability_envelope=SimpleNamespace(
                grants=tuple(
                    SimpleNamespace(capability_id=str(value))
                    for value in pin.required_capability_ids
                )
            ),
            model_profile=SimpleNamespace(profile_id="boundary-profile"),
        )

    def invocation_evidence_for(self, request: object) -> object:
        records = [
            {
                "required": item.selected_output.required,
                "schema_sha256": item.selected_output.schema_sha256,
                "terminal_result": item.terminal_result,
            }
            for item in request.selected_output_requirements
        ]
        records.sort(key=lambda item: item["terminal_result"].encode("utf-8"))
        requirements_digest = hashlib.sha256(
            json.dumps(
                records,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return SimpleNamespace(
            request_id=request.request_id,
            run_id=request.run_id,
            descriptor_sha256=self._pin.descriptor_sha256,
            context_file_count=0,
            selected_output_requirements_sha256=requirements_digest,
        )

    async def execute(self, request: object) -> object:
        self.calls += 1
        self.requests.append(request)
        instruction = request.task.instruction
        self.instructions.append(instruction)
        decoded = cast(dict[str, object], json.loads(instruction))
        work_item_payload = cast(
            Mapping[str, object], decoded["work_item_payload"]
        )
        snapshot = cast(
            Mapping[str, object],
            work_item_payload["closure_evidence_snapshot"],
        )
        verdict = _arbiter_verdict(
            snapshot,
            _arbiter_rubric(),
            marker="pass",
            evidence_id="boundary-evidence",
            observations=self._observations,
        )
        requirement = next(
            item
            for item in request.selected_output_requirements
            if item.terminal_result == "ARBITER_COMPLETE"
        )
        selected_output = _ArbiterSelectedOutputPresent(verdict)
        schema_digest = requirement.selected_output.schema_sha256
        intent = SimpleNamespace(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            terminal_result="ARBITER_COMPLETE",
            summary="boundary result",
            artifact_refs=(),
            selected_output=selected_output,
            selected_output_schema_sha256=schema_digest,
        )
        return SimpleNamespace(
            status="completed",
            result_class="domain_terminal",
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            terminal_intent=intent,
            compiled_harness=request.compiled_harness,
            selected_output=selected_output,
            selected_output_schema_sha256=schema_digest,
            diagnostic=SimpleNamespace(code="safe", message="safe"),
            usage=None,
        )

    async def aclose(self) -> None:
        return None


def _arbiter_millforge_dispatch(
    plan: object,
    *,
    closure_payload: Mapping[str, AuthorityValue],
    workspace_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    observations: tuple[Mapping[str, object], ...] = (),
    run_id: str = "run-arbiter-boundary",
    session_id: str = "session-arbiter-boundary",
    dispatch_generation: int = 1,
    session_fencing_token: str = "session-fence-arbiter-boundary",
    work_item_id: str = "work-arbiter-boundary",
    activation_id: str = "activation-arbiter-boundary",
    claim_id: str = "claim-arbiter-boundary",
    generation: int = 0,
    fencing_token: str = "fence-arbiter-boundary",
    correlation_id: str = "correlation-arbiter-boundary",
    adapter_id: str = "arbiter-boundary",
) -> SimpleNamespace:
    from millrace.adapters.millforge import MillforgeAdapter, MillforgeAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )

    stage, binding, behavior, _outcomes, _actions = _arbiter_authority(plan)
    fingerprint = authority_fingerprint(plan)
    pin = binding.component_pin
    assert pin is not None
    verdict_schema = next(
        schema
        for schema in plan.artifact_schemas
        if str(schema.id) == "planning.artifacts.verdict"
    )
    selected_asset_material = _arbiter_selected_asset_material(stage)
    terminal_options = _arbiter_terminal_options(plan, binding)
    facade = _ArbiterBoundaryFacade(pin, observations=observations)
    monkeypatch.setitem(sys.modules, "millforge", _arbiter_boundary_provider())
    redaction_policy = RedactionPolicy(policy_id="boundary", secret_tokens=())
    dispatch = RunnerDispatchEnvelope(
        run_id=run_id,
        session_id=session_id,
        dispatch_generation=dispatch_generation,
        session_fencing_token=session_fencing_token,
        work_item_id=work_item_id,
        activation_id=activation_id,
        plan_fingerprint=fingerprint,
        plan_id=f"{plan.workflow.workflow_id}:{plan.workflow.workflow_version}",
        workflow_id=str(plan.workflow.workflow_id),
        workflow_version=str(plan.workflow.workflow_version),
        graph_id="planning.lad.graph",
        claim_id=claim_id,
        generation=generation,
        fencing_token=fencing_token,
        queue_family_id=str(behavior.request_queue_family_id),
        stage_kind_id=str(stage.id),
        graph_node_id=behavior.target_graph_node_id,
        runner_binding_id=str(binding.id),
        external_enqueue_route_id=None,
        entrypoint_asset_id=str(stage.asset_ids[0]),
        skill_asset_ids=tuple(str(asset_id) for asset_id in stage.asset_ids[1:]),
        artifact_schema_ids=tuple(
            str(schema_id) for schema_id in stage.artifact_schema_ids
        ),
        work_item_payload=closure_payload,
        governance_context={
            "capabilities": tuple(
                {
                    "id": str(capability_id),
                    "support_status": "supported",
                    "grant_status": "granted",
                }
                for capability_id in binding.required_capability_ids
            )
        },
        terminal_options=terminal_options,
    )
    request = AdapterInvocationRequest(
        adapter_id=adapter_id,
        selected_runner_binding_id=str(binding.id),
        selected_adapter_kind="millforge",
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=float(binding.invocation_timeout_seconds),
        correlation_id=correlation_id,
        redaction_policy=redaction_policy,
        selected_asset_material=selected_asset_material,
        selected_component_pin=pin,
        selected_terminal_result_mappings=binding.terminal_result_mappings,
        selected_artifact_schemas=(verdict_schema,),
    )
    adapter = MillforgeAdapter(
        MillforgeAdapterConfig(
            adapter_id=adapter_id,
            facade=facade,
            workspace_root=workspace_root,
            timeout_seconds=10,
            redaction_policy=redaction_policy,
        )
    )
    return SimpleNamespace(
        adapter=adapter,
        request=request,
        dispatch=dispatch,
        facade=facade,
        fingerprint=fingerprint,
        stage=stage,
        binding=binding,
        behavior=behavior,
        pin=pin,
        verdict_schema=verdict_schema,
        selected_asset_material=selected_asset_material,
        terminal_options=terminal_options,
    )


@pytest.mark.parametrize("workflow_id", ARBITER_WORKFLOW_IDS)
def test_selected_arbiter_evaluations_reuse_rubric_and_bound_post_anchor_evidence(
    workflow_id: str,
    tmp_path: Path,
) -> None:
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    state, fingerprint, behavior, root_work_item_id = _arbiter_root_state(plan)
    root = state.work_items[root_work_item_id]
    assert root.lineage_id is not None

    evaluate_input, evaluate_context = _arbiter_evaluate_transition(
        state, behavior
    )
    state = _apply_planning_input(state, evaluate_input, evaluate_context)
    first_evaluation = next(iter(state.closure_evaluations.values()))
    state, first_run = _claim_planning_activation(
        state,
        first_evaluation.target_activation_id,
        tag="arbiter-first",
    )
    first_snapshot = cast(
        Mapping[str, object],
        state.work_items[first_run.work_item_id].payload[
            "closure_evidence_snapshot"
        ],
    )
    assert first_snapshot["prior_verdict"] is None
    rubric = _arbiter_rubric()
    first_verdict = _arbiter_verdict(
        first_snapshot,
        rubric,
        marker="gap",
        evidence_id="first-gap-evidence",
    )
    state, _first_decision, remediation_activation_id = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=first_run,
        action_id=behavior.gap_action_id,
        tag="arbiter-first-gap",
        artifact_payload=first_verdict,
    )
    assert len(state.remediation_work_records) == 1
    first_verdict_artifact = next(
        artifact
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == "planning.artifacts.verdict"
    )
    assert canonical_authority_mapping_bytes(
        cast(Mapping[str, AuthorityValue], first_verdict_artifact.payload["rubric"])
    ) == canonical_authority_mapping_bytes(rubric)
    assert first_verdict_artifact.payload["criterion_results"][0]["criterion_id"] == (
        "root-contract-criterion"
    )

    state, auditor_run = _claim_planning_activation(
        state,
        remediation_activation_id,
        tag="arbiter-auditor",
    )
    state, _blocked_decision, mechanic_activation_id = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=auditor_run,
        action_id="planning.route_auditor_blocked",
        tag="arbiter-auditor-blocked",
        artifact_payload={},
    )
    state, mechanic_run = _claim_planning_activation(
        state,
        mechanic_activation_id,
        tag="arbiter-mechanic",
    )
    post_anchor_report = {
        "artifact_kind": "planning.artifacts.report",
        "summary": "The remediation returned current evidence to the recorded source.",
    }
    state, _recovered_decision, returned_activation_id = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=mechanic_run,
        action_id="planning.return_mechanic_recovered",
        tag="arbiter-mechanic-recovered",
        artifact_payload=post_anchor_report,
    )
    state = _settle_arbiter_returned_auditor(
        state,
        plan,
        fingerprint,
        activation_id=returned_activation_id,
        tag="arbiter-returned",
    )
    report_artifacts = [
        artifact
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == "planning.artifacts.report"
    ]
    assert len(report_artifacts) == 1
    assert report_artifacts[0].payload == post_anchor_report
    assert state.work_items[report_artifacts[0].work_item_id].lineage_id == (
        root.lineage_id
    )
    transition_ids = tuple(transition.record_id for transition in state.transitions)
    assert transition_ids.index(
        report_artifacts[0].transition_id
    ) > transition_ids.index(first_verdict_artifact.transition_id)

    evaluate_input, evaluate_context = _arbiter_evaluate_transition(
        state, behavior
    )
    state = _apply_planning_input(state, evaluate_input, evaluate_context)
    second_evaluation = tuple(
        record
        for record in state.closure_evaluations.values()
        if record.record_id != first_evaluation.record_id
    )
    assert len(second_evaluation) == 1
    state, second_run = _claim_planning_activation(
        state,
        second_evaluation[0].target_activation_id,
        tag="arbiter-second",
    )
    second_snapshot = cast(
        Mapping[str, object],
        state.work_items[second_run.work_item_id].payload[
            "closure_evidence_snapshot"
        ],
    )
    prior_verdict = cast(Mapping[str, object], second_snapshot["prior_verdict"])
    assert canonical_authority_mapping_bytes(
        cast(Mapping[str, AuthorityValue], prior_verdict["payload"])
    ) == canonical_authority_mapping_bytes(first_verdict)
    assert canonical_authority_mapping_bytes(
        cast(Mapping[str, AuthorityValue], prior_verdict["payload"])["rubric"]
    ) == canonical_authority_mapping_bytes(rubric)
    assert (
        second_snapshot["freshness_anchor_digest"]
        == first_verdict_artifact.payload_digest
    )
    assert [
        canonical_authority_mapping_bytes(
            cast(Mapping[str, AuthorityValue], evidence["payload"])
        )
        for evidence in cast(
            tuple[Mapping[str, object], ...], second_snapshot["evidence_artifacts"]
        )
    ] == [
        canonical_authority_mapping_bytes(post_anchor_report),
        canonical_authority_mapping_bytes(
            {
                "artifact_kind": "task_cards",
                "cards": [
                    {
                        "task_card_id": "arbiter-returned-card",
                        "title": "Recovered evidence",
                        "body": "The returned evidence is settled.",
                    }
                ],
            }
        ),
    ]

    stale_verdict = _arbiter_verdict(
        second_snapshot,
        rubric,
        marker="pass",
        evidence_id="post-anchor-evidence",
    )
    stale_verdict["freshness_anchor_digest"] = cast(
        Mapping[str, object], second_snapshot["root_contract"]
    )["payload_digest"]
    stale_decision = _arbiter_action_decision(
        state,
        plan,
        fingerprint,
        run=second_run,
        action_id=behavior.pass_action_id,
        tag="arbiter-second-stale-anchor",
        artifact_payload=stale_verdict,
    )
    assert not stale_decision.accepted
    assert stale_decision.refusal is not None
    assert stale_decision.refusal.reason == "closure_freshness_anchor_mismatch"

    state, _second_decision, _ = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=second_run,
        action_id=behavior.pass_action_id,
        tag="arbiter-second-pass",
        artifact_payload=_arbiter_verdict(
            second_snapshot,
            rubric,
            marker="pass",
            evidence_id="post-anchor-evidence",
        ),
    )
    assert state.closure_targets[_arbiter_target_id(state)].status == "closed"
    assert len(state.remediation_work_records) == 1
    assert len(state.closure_terminal_records) == 1


def _git_porcelain(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.mark.parametrize("workflow_id", ARBITER_WORKFLOW_IDS)
def test_arbiter_cooperative_dispatch_leaves_a_fresh_git_repo_unchanged(
    workflow_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace.adapters.runner_contract import AdapterSuccessResult, StartedSession

    repo = tmp_path / "arbiter-repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "app.py").write_text("def answer() -> int:\n    return 42\n")
    (repo / "tests" / "test_app.py").write_text(
        "from src.app import answer\n\n"
        "def test_answer() -> None:\n    assert answer() == 42\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "qa@example.test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "QA"], cwd=repo, check=True)
    subprocess.run(
        ["git", "add", "src/app.py", "tests/test_app.py"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "seed repository"], cwd=repo, check=True)
    before_status = _git_porcelain(repo, "status", "--porcelain=v1")
    before_staged_diff = _git_porcelain(repo, "diff", "--cached", "--name-only")
    before_unstaged_diff = _git_porcelain(repo, "diff", "--name-only")
    before_files = _git_porcelain(repo, "ls-files")

    plan = _selected_workflow_plan(tmp_path, workflow_id)
    state, fingerprint, behavior, _root_work_item_id = _arbiter_root_state(plan)
    evaluate_input, evaluate_context = _arbiter_evaluate_transition(
        state, behavior
    )
    state = _apply_planning_input(state, evaluate_input, evaluate_context)
    evaluation = next(iter(state.closure_evaluations.values()))
    state, run = _claim_planning_activation(
        state,
        evaluation.target_activation_id,
        tag="arbiter-no-mutation",
    )
    harness = _arbiter_millforge_dispatch(
        plan,
        closure_payload=cast(
            Mapping[str, AuthorityValue], state.work_items[run.work_item_id].payload
        ),
        workspace_root=repo,
        monkeypatch=monkeypatch,
        run_id=run.run_ref.run_id,
        session_id=f"session-{run.run_ref.run_id}",
        session_fencing_token=f"session-fence-{run.run_ref.run_id}",
        work_item_id=run.work_item_id,
        activation_id=run.activation_id,
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        correlation_id=f"correlation-{run.run_ref.run_id}",
    )
    assert harness.fingerprint == fingerprint
    assert harness.adapter.config.workspace_root == repo.resolve()
    started = harness.adapter.start_session(harness.request)
    assert isinstance(started, StartedSession), (
        getattr(started, "adapter_error", None),
        getattr(getattr(started, "adapter_error", None), "diagnostics", None),
    )
    deadline = time.monotonic() + 2
    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = started.handle.poll_completion()
        if outcome is None:
            time.sleep(0.001)
    assert isinstance(outcome, AdapterSuccessResult)
    assert outcome.marker == "ARBITER_COMPLETE"
    assert harness.facade.calls == 1
    assert len(harness.facade.requests) == 1
    provider_request = harness.facade.requests[0]
    assert provider_request.run_id == run.run_ref.run_id
    assert provider_request.work_item_id == run.work_item_id
    assert provider_request.selected_output_requirements
    selected_schema_digest = (
        provider_request.selected_output_requirements[0].selected_output.schema_sha256
    )
    assert {
        item.terminal_result for item in provider_request.selected_output_requirements
    } == {
        str(mapping.runner_result_id)
        for mapping in harness.binding.terminal_result_mappings
    }
    assert all(
        item.selected_output.schema_sha256 == selected_schema_digest
        for item in provider_request.selected_output_requirements
    )
    assert harness.request.selected_artifact_schemas == (harness.verdict_schema,)
    assert harness.dispatch.entrypoint_asset_id == "planning.entrypoints.lad_arbiter"
    assert harness.dispatch.skill_asset_ids == ("planning.skills.arbiter_core",)
    assert harness.dispatch.artifact_schema_ids == ("planning.artifacts.verdict",)
    assert harness.dispatch.terminal_options == harness.terminal_options
    assert {
        option["marker"] for option in harness.terminal_options
    } == {"ARBITER_COMPLETE", "REMEDIATION_NEEDED", "BLOCKED"}
    assert all(
        option["artifact_schema_id"] == "planning.artifacts.verdict"
        for option in harness.terminal_options
    )
    decoded_instruction = cast(
        dict[str, object], json.loads(provider_request.task.instruction)
    )
    assert decoded_instruction["entrypoint_asset_id"] == (
        "planning.entrypoints.lad_arbiter"
    )
    assert decoded_instruction["skill_asset_ids"] == ["planning.skills.arbiter_core"]
    assert decoded_instruction["selected_asset_material"] == (
        harness.selected_asset_material
    )
    assert decoded_instruction["terminal_options"] == list(harness.terminal_options)

    assert outcome.artifact_payload_candidate is not None
    echo = outcome.dispatch_echo
    evidence = RunnerResultEvidence(
        run_id=echo.run_id,
        session_id=echo.session_id,
        dispatch_generation=echo.dispatch_generation,
        session_fencing_token=echo.session_fencing_token,
        plan_fingerprint=echo.plan_fingerprint,
        claim_id=echo.claim_id,
        generation=echo.generation,
        fencing_token=echo.fencing_token,
        stage_kind_id=echo.stage_kind_id,
        graph_node_id=echo.graph_node_id,
        runner_binding_id=echo.runner_binding_id,
        marker=cast(str, outcome.marker),
        adapter_provenance=outcome.adapter_provenance,
        observation_payload={},
        artifact_payload=outcome.artifact_payload_candidate,
    )
    observed = RunnerResultObserved(
        "observe-arbiter-no-mutation-pass",
        run_id=run.run_ref.run_id,
        payload=runner_result_payload(evidence),
        observed_at=0,
    )
    before_remediation = state.remediation_work_records
    decision = decide_with_fake_runner_completion(
        state,
        observed,
        deterministic_context(
            transition_id="transition-observe-arbiter-no-mutation-pass",
            work_item_id="work-target-arbiter-no-mutation-pass",
            activation_id="activation-target-arbiter-no-mutation-pass",
            run_id=run.run_ref.run_id,
            claim_id=run.run_ref.claim_id,
            fencing_token=run.run_ref.fencing_token,
        ),
    )
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    assert state.closure_targets[_arbiter_target_id(state)].status == "closed"
    assert state.remediation_work_records == before_remediation == {}
    assert _git_porcelain(repo, "status", "--porcelain=v1") == before_status == ""
    assert _git_porcelain(repo, "diff", "--cached", "--name-only") == (
        before_staged_diff
    )
    assert _git_porcelain(repo, "diff", "--name-only") == before_unstaged_diff
    assert _git_porcelain(repo, "ls-files") == before_files
    for artifact in state.artifacts.values():
        assert not {"queue", "remediation", "incident"}.intersection(artifact.payload)


@pytest.mark.parametrize("workflow_id", ARBITER_WORKFLOW_IDS)
def test_arbiter_complete_unrelated_observation_is_non_blocking(
    workflow_id: str,
    tmp_path: Path,
) -> None:
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    state, fingerprint, behavior, _root_work_item_id = _arbiter_root_state(plan)
    assert fingerprint in state.admitted_plans
    evaluate_input, evaluate_context = _arbiter_evaluate_transition(
        state, behavior
    )
    state = _apply_planning_input(state, evaluate_input, evaluate_context)
    evaluation = next(iter(state.closure_evaluations.values()))
    state, run = _claim_planning_activation(
        state,
        evaluation.target_activation_id,
        tag="arbiter-unrelated-observation",
    )
    snapshot = cast(
        Mapping[str, object],
        state.work_items[run.work_item_id].payload["closure_evidence_snapshot"],
    )
    verdict = _arbiter_verdict(
        snapshot,
        _arbiter_rubric(),
        marker="pass",
        evidence_id="unrelated-observation-evidence",
        observations=(UNRELATED_ARBITER_OBSERVATION,),
    )
    before_remediation = state.remediation_work_records
    state, decision, _ = _run_arbiter_action(
        state,
        plan,
        fingerprint,
        run=run,
        action_id=behavior.pass_action_id,
        tag="arbiter-unrelated-observation-pass",
        artifact_payload=cast(Mapping[str, AuthorityValue], verdict),
    )
    assert decision.accepted
    assert state.closure_targets[_arbiter_target_id(state)].status == "closed"
    assert len(state.closure_terminal_records) == 1
    assert next(iter(state.closure_terminal_records.values())).terminal_kind == "passed"
    assert state.remediation_work_records == before_remediation == {}

    verdict_artifacts = [
        artifact
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == "planning.artifacts.verdict"
    ]
    assert len(verdict_artifacts) == 1
    assert len(state.artifacts) == 3
    assert canonical_authority_mapping_bytes(
        cast(Mapping[str, AuthorityValue], verdict_artifacts[0].payload)
    ) == canonical_authority_mapping_bytes(
        cast(Mapping[str, AuthorityValue], verdict)
    )
    stored_observations = cast(
        tuple[Mapping[str, AuthorityValue], ...],
        verdict_artifacts[0].payload["observations"],
    )
    assert len(stored_observations) == 1
    assert canonical_authority_mapping_bytes(stored_observations[0]) == (
        canonical_authority_mapping_bytes(UNRELATED_ARBITER_OBSERVATION)
    )
    assert all(
        artifact.payload.get("observations") != [UNRELATED_ARBITER_OBSERVATION]
        for artifact in state.artifacts.values()
        if artifact.artifact_id != verdict_artifacts[0].artifact_id
    )


@pytest.mark.parametrize("workflow_id", ARBITER_WORKFLOW_IDS)
def test_selected_arbiter_boundary_uses_actual_millforge_serializer(
    workflow_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    _state, fingerprint, behavior, stage, closure_payload, astral_body = (
        _arbiter_boundary_fixture(plan, target_bytes=16384)
    )
    harness = _arbiter_millforge_dispatch(
        plan,
        closure_payload=closure_payload,
        workspace_root=tmp_path,
        monkeypatch=monkeypatch,
    )
    assert harness.fingerprint == fingerprint
    assert harness.behavior.request_payload_byte_limit == 16384
    assert tuple(str(asset_id) for asset_id in stage.asset_ids) == (
        "planning.entrypoints.lad_arbiter",
        "planning.skills.arbiter_core",
    )
    assert set(harness.selected_asset_material) == {
        "planning.entrypoints.lad_arbiter",
        "planning.skills.arbiter_core",
    }
    from millrace.adapters.runner_contract import AdapterSuccessResult, StartedSession

    started = harness.adapter.start_session(harness.request)
    assert isinstance(started, StartedSession), (
        getattr(started, "adapter_error", None),
        getattr(getattr(started, "adapter_error", None), "diagnostics", None),
    )
    deadline = time.monotonic() + 2
    outcome = None
    while outcome is None and time.monotonic() < deadline:
        outcome = started.handle.poll_completion()
        if outcome is None:
            time.sleep(0.001)
    assert isinstance(outcome, AdapterSuccessResult)
    assert harness.facade.calls == 1
    assert len(harness.facade.instructions) == 1
    assert len(harness.facade.requests) == 1
    instruction = harness.facade.instructions[0]
    assert len(instruction.encode("utf-8")) < 65536
    assert "\\ud800\\udc00" in instruction
    decoded_instruction = cast(dict[str, object], json.loads(instruction))
    assert (
        decoded_instruction["entrypoint_asset_id"]
        == "planning.entrypoints.lad_arbiter"
    )
    assert decoded_instruction["skill_asset_ids"] == ["planning.skills.arbiter_core"]
    assert decoded_instruction["selected_asset_material"] == (
        harness.selected_asset_material
    )
    assert decoded_instruction["work_item_payload"]
    assert astral_body in cast(
        str,
        decoded_instruction["work_item_payload"]["closure_evidence_snapshot"][
            "root_contract"
        ]["payload"]["body"],
    )

    over_state, over_fingerprint, over_behavior, over_root_id = _arbiter_root_state(
        plan,
        body=astral_body + "x",
    )
    over_evaluate_input, over_evaluate_context = _arbiter_evaluate_transition(
        over_state, over_behavior
    )
    over_decision = decide(
        over_state,
        over_evaluate_input,
        over_evaluate_context,
    )
    assert over_fingerprint == fingerprint
    assert over_root_id in over_state.work_items
    assert not over_decision.accepted
    assert over_decision.refusal is not None
    assert over_decision.refusal.reason == "closure_request_payload_limit_exceeded"
    assert harness.facade.calls == 1


def test_recon_proof_does_not_restate_package_authority() -> None:
    test_source = Path(__file__).read_text()

    for symbol in (
        "_" + "RECON_MARKER_TO_ACTION",
        "_" + "RECON_MARKER_TO_SCHEMA",
        "_" + "RECON_SCHEMAS",
        "def _" + "schema_accepts",
    ):
        assert symbol not in test_source


def _selected_workflow_plan(tmp_path: Path, workflow_id: str) -> object:
    return conformance.select_and_verify_package(
        tmp_path / workflow_id.replace(".", "-"),
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=workflow_id,
        workflow_version="0.1",
    )


def _recon_selected_authority(
    plan: object,
) -> tuple[object, object, tuple[Record, ...], dict[str, object]]:
    stage = next(stage for stage in plan.stage_kinds if str(stage.id) == "recon")
    binding = next(
        binding
        for binding in plan.runner_bindings
        if str(binding.id) == "recon.millforge_runner"
    )
    outcomes = {
        str(outcome.id): outcome
        for outcome in plan.terminal_outcomes
        if str(outcome.stage_kind_id) == "recon"
    }
    actions = {
        str(action.outcome_id): action
        for action in plan.terminal_actions
        if str(action.stage_kind_id) == "recon"
    }
    options = []
    for mapping in binding.terminal_result_mappings:
        outcome = outcomes[str(mapping.outcome_id)]
        action = actions[str(outcome.id)]
        options.append(
            {
                "outcome_id": str(outcome.id),
                "marker": outcome.marker,
                "action_id": str(action.id),
                "action_kind": action.action_kind,
                "artifact_schema_id": (
                    None
                    if action.artifact_schema_id is None
                    else str(action.artifact_schema_id)
                ),
            }
        )
    stage_schema_ids = {str(schema_id) for schema_id in stage.artifact_schema_ids}
    schemas = {
        str(schema.id): schema
        for schema in plan.artifact_schemas
        if str(schema.id) in stage_schema_ids
    }
    return stage, binding, tuple(options), schemas


@pytest.mark.parametrize("workflow_id", RECON_WORKFLOW_IDS)
def test_official_recon_workflows_preserve_independent_terminal_authority(
    workflow_id: str,
    tmp_path: Path,
) -> None:
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    expected = EXPECTED_RECON_AUTHORITY
    outcomes = {
        str(outcome.marker): outcome
        for outcome in plan.terminal_outcomes
        if str(outcome.stage_kind_id) == "recon"
    }
    actions = {
        str(action.outcome_id): action
        for action in plan.terminal_actions
        if str(action.stage_kind_id) == "recon"
    }

    assert set(outcomes) == set(expected)
    for marker, expected_authority in expected.items():
        outcome = outcomes[marker]
        action = actions[str(outcome.id)]
        observed_authority = (
            str(outcome.id),
            action.action_kind,
            str(action.id),
            (
                None
                if action.runner_binding_id is None
                else str(action.runner_binding_id),
                None
                if action.target_stage_kind_id is None
                else str(action.target_stage_kind_id),
                action.target_graph_node_id,
                None
                if action.emitted_queue_family_id is None
                else str(action.emitted_queue_family_id),
            ),
            None
            if action.artifact_schema_id is None
            else str(action.artifact_schema_id),
        )
        assert observed_authority == expected_authority


def _recon_provider_bundle(plan: object, tmp_path: Path) -> dict[str, Any]:
    from millrace.adapters.codex import CodexAdapter, CodexAdapterConfig
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )
    from millrace.contracts.runner import RunnerDispatchEnvelope

    stage, binding, options, schemas = _recon_selected_authority(plan)
    projected_schema_ids = {
        str(option["artifact_schema_id"])
        for option in options
        if option["artifact_schema_id"] is not None
    }
    policy = RedactionPolicy(policy_id="recon-test", secret_tokens=())
    dispatch = RunnerDispatchEnvelope(
        run_id="run.recon.test",
        session_id="session.recon.test",
        dispatch_generation=1,
        session_fencing_token="session-fence.recon.test",
        work_item_id="work.recon.test",
        activation_id="activation.recon.test",
        plan_fingerprint="sha256:" + "a" * 64,
        plan_id=f"{plan.workflow.workflow_id}:0.1",
        workflow_id=str(plan.workflow.workflow_id),
        workflow_version=str(plan.workflow.workflow_version),
        graph_id="planning.lad.graph",
        claim_id="claim.recon.test",
        generation=0,
        fencing_token="fence.recon.test",
        queue_family_id="probe",
        stage_kind_id="recon",
        graph_node_id="planning.lad.recon.start",
        runner_binding_id=str(binding.id),
        external_enqueue_route_id="probe",
        entrypoint_asset_id=str(stage.asset_ids[0]),
        skill_asset_ids=tuple(str(asset_id) for asset_id in stage.asset_ids[1:]),
        artifact_schema_ids=tuple(
            str(schema_id) for schema_id in stage.artifact_schema_ids
        ),
        work_item_payload={"source": "package-test"},
        governance_context={},
        terminal_options=options,
    )
    request = AdapterInvocationRequest(
        adapter_id="recon-test",
        selected_runner_binding_id=str(binding.id),
        selected_adapter_kind="codex",
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=float(binding.invocation_timeout_seconds),
        correlation_id="correlation.recon.test",
        redaction_policy=policy,
        selected_component_pin=binding.component_pin,
        selected_terminal_result_mappings=binding.terminal_result_mappings,
        selected_artifact_schemas=tuple(
            schemas[schema_id] for schema_id in sorted(projected_schema_ids)
        ),
    )
    config = CodexAdapterConfig(
        adapter_id="recon-test",
        wrapper_mode="offline_fake",
        wrapper_argv=("python", "-c", "pass"),
        cwd=tmp_path,
        env_allowlist={},
        timeout_seconds=float(binding.invocation_timeout_seconds),
        max_input_bundle_bytes=1_000_000,
        max_stdout_bytes=1_000,
        max_stderr_diagnostic_bytes=1_000,
        redaction_policy=policy,
    )
    prepared = CodexAdapter(config)._transport_request(request)
    stdin_bytes = getattr(prepared, "stdin_bytes", None)
    assert isinstance(stdin_bytes, bytes)
    return cast(dict[str, Any], json.loads(stdin_bytes))


def test_planning_lad_authority_and_assets_are_package_owned() -> None:
    manifest = _manifest()
    workflow = conformance.workflows_by_id(manifest)[WORKFLOW_ID]
    selected = cast(dict[str, object], workflow["selected_authority"])

    assert workflow["workflow_version"] == "0.1"
    assert len(cast(list[object], selected["stage_kinds"])) == 13
    assert len(cast(list[object], workflow["required_assets"])) == 26
    assert "assets" not in selected


def test_planning_lad_selects_through_installed_public_api(tmp_path: Path) -> None:
    manifest = _manifest()
    plan = conformance.select_and_verify_package(
        tmp_path,
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version="0.1",
    )
    conformance.assert_selected_package_pin(
        plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version="0.1",
        selected_asset_pins=conformance.selected_asset_pins(manifest, WORKFLOW_ID),
    )


def test_planning_lad_assets_keep_task_card_handoff_contract() -> None:
    root = PACKAGE_ROOT / "assets/workflows/planning.lad"
    planner = (root / "skills/planner-core.md").read_text()
    manager = (root / "skills/manager-core.md").read_text()
    assert "planning.artifacts.stage_result" in planner
    assert "planning.artifacts.task_cards" in manager
    assert "## Completion Criteria" in planner
    assert "## Completion Criteria" in manager


def test_planning_lad_recon_entrypoint_has_exact_inputs_and_handoffs() -> None:
    entrypoint = (
        PACKAGE_ROOT / "assets/workflows/planning.lad/entrypoints/recon.md"
    ).read_text()
    input_section = entrypoint.split("Inputs from dispatch:", maxsplit=1)[1].split(
        "Readable assets:", maxsplit=1
    )[0]
    normalized_input = " ".join(input_section.split())
    assert re.findall(r"`([^`]+)`", input_section) == ["probe", "stage_result"]
    assert "No other input or queue family" in normalized_input
    assert "never accept arbitrary payloads" in normalized_input
    assert "missing, contradictory, or unsafe" in entrypoint.lower()
    assert "schema-valid" in entrypoint


@pytest.mark.parametrize("workflow_id", RECON_WORKFLOW_IDS)
def test_official_recon_workflows_select_pins_and_project_terminal_authority(
    workflow_id: str,
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    selected_pin = plan.workflow_package_pin
    expected_pins = dict(conformance.selected_asset_pins(manifest, workflow_id))
    recon_pins = {
        asset_pin.asset_id: asset_pin.content_digest
        for asset_pin in selected_pin.selected_asset_pins
        if asset_pin.asset_id
        in {"planning.entrypoints.recon", "planning.skills.recon_core"}
    }
    assert set(recon_pins) == {
        "planning.entrypoints.recon",
        "planning.skills.recon_core",
    }
    assert recon_pins == {
        asset_id: digest
        for asset_id, digest in expected_pins.items()
        if asset_id in recon_pins
    }

    stage, binding, options, schemas = _recon_selected_authority(plan)
    assert tuple(str(queue_id) for queue_id in stage.input_queue_family_ids) == (
        "probe",
        "stage_result",
    )
    assert set(binding.component_pin.legal_terminal_result_ids) == {
        "BLOCKED",
        "RECON_BLOCKED",
        "RECON_NOOP",
        "RECON_TO_EXECUTION",
        "RECON_TO_PLANNING",
    }
    assert len(options) == 5
    projected_schema_ids = {
        str(option["artifact_schema_id"])
        for option in options
        if option["artifact_schema_id"] is not None
    }
    assert projected_schema_ids == {
        "execution.artifacts.task",
        "planning.artifacts.generated_spec",
        "planning.artifacts.recon_packet",
        "planning.artifacts.report",
    }
    assert len(projected_schema_ids) == 4
    assert set(schemas) >= projected_schema_ids

    bundle = _recon_provider_bundle(plan, tmp_path)
    contracts = cast(
        list[Record],
        cast(Record, bundle["prompt"])["terminal_artifact_contracts"],
    )
    assert len(contracts) == len(options) == 5
    contracts_by_marker = {
        str(contract["marker"]): contract for contract in contracts
    }
    outcomes = {
        str(outcome.id): outcome
        for outcome in plan.terminal_outcomes
        if str(outcome.stage_kind_id) == "recon"
    }
    actions = {
        str(action.outcome_id): action
        for action in plan.terminal_actions
        if str(action.stage_kind_id) == "recon"
    }
    projected_schemas = {
        str(schema["id"]): schema["schema"]
        for schema in cast(list[Record], bundle["selected_artifact_schemas"])
    }
    for option in options:
        marker = str(option["marker"])
        contract = contracts_by_marker[marker]
        outcome = outcomes[str(option["outcome_id"])]
        action = actions[str(outcome.id)]
        assert (
            contract["outcome_id"],
            contract["marker"],
            contract["action_id"],
            contract["action_kind"],
            contract["artifact_schema_id"],
        ) == (
            str(outcome.id),
            outcome.marker,
            str(action.id),
            action.action_kind,
            None
            if action.artifact_schema_id is None
            else str(action.artifact_schema_id),
        )
        if action.artifact_schema_id is not None:
            schema_id = str(action.artifact_schema_id)
            assert contract["json_schema"] == projected_schemas[schema_id]


@pytest.mark.parametrize("workflow_id", RECON_WORKFLOW_IDS)
def test_recon_core_valid_examples_use_selected_terminal_schemas(
    workflow_id: str,
    tmp_path: Path,
) -> None:
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    _stage, _binding, options, schemas = _recon_selected_authority(plan)
    bundle = _recon_provider_bundle(plan, tmp_path)
    contracts = cast(
        list[Record],
        cast(Record, bundle["prompt"])["terminal_artifact_contracts"],
    )
    contracts_by_marker = {
        str(contract["marker"]): contract for contract in contracts
    }
    core = (
        PACKAGE_ROOT / "assets/workflows/planning.lad/skills/recon-core.md"
    ).read_text()
    examples = cast(list[Record], _json_block_after(core, "## Valid Branch Examples"))

    assert len(examples) == len(options) == len(contracts)
    assert {str(example["terminal_marker"]) for example in examples} == set(
        contracts_by_marker
    )
    for example in examples:
        marker = str(example["terminal_marker"])
        schema = schemas[str(contracts_by_marker[marker]["artifact_schema_id"])]
        artifact = example["artifact"]
        observation = example["observation_payload"]
        assert artifact == observation
        assert validate_schema(schema.schema, artifact).accepted
        assert validate_schema(schema.schema, observation).accepted

        branch = core.split(f"### {marker}", maxsplit=1)[1].split(
            "\n### ", maxsplit=1
        )[0]
        assert "Artifact candidate:" in branch
        assert "Observation candidate:" in branch
        assert "Completion condition:" in branch
        assert "Evidence placement:" in branch
        for field in schema.schema["required"]:
            assert f"`{field}`" in branch


@pytest.mark.parametrize("workflow_id", RECON_WORKFLOW_IDS)
def test_recon_core_invalid_examples_use_selected_terminal_schemas(
    workflow_id: str,
    tmp_path: Path,
) -> None:
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    _stage, _binding, _options, schemas = _recon_selected_authority(plan)
    bundle = _recon_provider_bundle(plan, tmp_path)
    contracts = cast(
        list[Record],
        cast(Record, bundle["prompt"])["terminal_artifact_contracts"],
    )
    contracts_by_marker = {
        str(contract["marker"]): contract for contract in contracts
    }
    core = (
        PACKAGE_ROOT / "assets/workflows/planning.lad/skills/recon-core.md"
    ).read_text()
    examples = cast(
        list[Record], _json_block_after(core, "## Invalid Branch Examples")
    )
    assert {
        "extra_field",
        "missing_field",
        "type_mismatch",
        "marker_schema_mismatch",
    } <= {str(example["case"]) for example in examples}
    for example in examples:
        marker = str(example["terminal_marker"])
        schema = schemas[str(contracts_by_marker[marker]["artifact_schema_id"])]
        assert not validate_schema(schema.schema, example["artifact"]).accepted
        assert not validate_schema(
            schema.schema,
            example["observation_payload"],
        ).accepted


def test_planning_lad_compiles_packaged_selected_authority() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)

    assert str(plan.workflow.workflow_id) == WORKFLOW_ID
    assert plan.workflow.workflow_name == "LAD Planning"
    assert plan.lineage_policy == "root_from_external_enqueue"
    graphs = {str(graph.id): graph for graph in plan.graphs}
    assert graphs["planning.lad.graph"].node_ids == (
        "planning.lad.recon.start",
        "planning.lad.planner.start",
        "planning.lad.manager.start",
        "planning.lad.mechanic.start",
        "planning.lad.auditor.start",
        "planning.lad.arbiter.start",
    )
    assert graphs["execution.lad.graph"].node_ids == (
        "execution.lad.builder.start",
        "execution.lad.checker.start",
        "execution.lad.fixer.start",
        "execution.lad.doublechecker.start",
        "execution.lad.updater.start",
        "execution.lad.troubleshooter.start",
        "execution.lad.consultant.start",
    )
    routes = {route.id: route for route in plan.external_enqueue_routes}
    assert {
        route_id: (
            str(route.queue_family_id),
            route.graph_node_id,
            str(route.stage_kind_id),
            str(route.runner_binding_id),
            str(route.payload_schema_id),
        )
        for route_id, route in routes.items()
    } == {
        "spec": (
            "spec",
            "planning.lad.planner.start",
            "lad_planner",
            "lad_planner.millforge_runner",
            "planning.intake.spec",
        ),
        "probe": (
            "probe",
            "planning.lad.recon.start",
            "recon",
            "recon.millforge_runner",
            "planning.intake.probe",
        ),
        "incident": (
            "incident",
            "planning.lad.auditor.start",
            "lad_auditor",
            "lad_auditor.millforge_runner",
            "planning.intake.incident",
        ),
        "execution.lad.task": (
            "task",
            "execution.lad.builder.start",
            "lad_builder",
            "lad_builder.millforge_runner",
            "execution.artifacts.task",
        ),
    }
    assert {str(queue.id) for queue in plan.queue_families} == {
        "spec",
        "probe",
        "incident",
        "task",
        "stage_result",
        "recon_packet",
        "generated_task",
        "generated_spec",
        "planner_disposition",
            "task_cards",
            "incident_report",
            "report",
            "verdict",
        }
    planning_stages = {
        "recon",
        "lad_planner",
        "lad_manager",
        "lad_mechanic",
        "lad_auditor",
        "lad_arbiter",
    }
    stage_assets = {
        str(stage.id): {str(asset_id) for asset_id in stage.asset_ids}
        for stage in plan.stage_kinds
        if str(stage.id) in planning_stages
    }
    assert stage_assets == {
        "recon": {"planning.entrypoints.recon", "planning.skills.recon_core"},
        "lad_planner": {
            "planning.entrypoints.lad_planner",
            "planning.skills.planner_core",
        },
        "lad_manager": {
            "planning.entrypoints.lad_manager",
            "planning.skills.manager_core",
        },
        "lad_mechanic": {
            "planning.entrypoints.lad_mechanic",
            "planning.skills.mechanic_core",
        },
        "lad_auditor": {
            "planning.entrypoints.lad_auditor",
            "planning.skills.auditor_core",
        },
        "lad_arbiter": {
            "planning.entrypoints.lad_arbiter",
            "planning.skills.arbiter_core",
        },
    }
    assert all(
        str(stage.runner_binding_id) == f"{stage.id}.millforge_runner"
        for stage in plan.stage_kinds
    )


def test_planning_lad_work_shaping_and_downstream_routes_are_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    actions = {str(action.id): action for action in plan.terminal_actions}
    expected = {
        "planning.recon_enqueue_task": (
            "route",
            "lad_builder",
            "execution.lad.builder.start",
            "task",
            "execution.artifacts.task",
        ),
        "planning.recon_enqueue_spec": (
            "route",
            "lad_planner",
            "planning.lad.planner.start",
            "spec",
            "planning.artifacts.generated_spec",
        ),
        "planning.recon_noop": (
            "complete_work_item",
            None,
            None,
            None,
            "planning.artifacts.recon_packet",
        ),
        "planning.recon_block_work_item": (
            "block_work_item",
            None,
            None,
            None,
            "planning.artifacts.report",
        ),
        "planning.recon_blocked": (
            "block_work_item",
            None,
            None,
            None,
            "planning.artifacts.report",
        ),
        "planning.route_planner_complete": (
            "route",
            "lad_manager",
            "planning.lad.manager.start",
            "stage_result",
            "planning.artifacts.stage_result",
        ),
        "planning.close_manager_complete": (
            "complete_work_item",
            None,
            None,
            None,
            "planning.artifacts.task_cards",
        ),
        "planning.route_auditor_complete": (
            "route",
            "lad_planner",
            "planning.lad.planner.start",
            "stage_result",
            "planning.artifacts.stage_result",
        ),
    }
    assert {
        action_id: (
            action.action_kind,
            None
            if action.target_stage_kind_id is None
            else str(action.target_stage_kind_id),
            action.target_graph_node_id,
            None
            if action.emitted_queue_family_id is None
            else str(action.emitted_queue_family_id),
            None
            if action.artifact_schema_id is None
            else str(action.artifact_schema_id),
        )
        for action_id, action in actions.items()
        if action_id in expected
    } == expected

    planner = actions["planning.route_planner_complete"]
    assert planner.payload_projection == {
        "kind": "object",
        "fields": {
            "planning_result": {
                "kind": "source",
                "path": ("artifact_payload",),
            },
            "source_request": {
                "kind": "source",
                "path": ("work_item_payload",),
            },
        },
    }
    fanout = plan.fanout_declarations[0]
    assert (
        str(fanout.id),
        str(fanout.source_action_id),
        str(fanout.source_artifact_schema_id),
        fanout.target_route_id,
        str(fanout.target_payload_schema_id),
        fanout.root_lineage_policy,
        fanout.dependency_policy,
        fanout.duplicate_policy,
    ) == (
        "planning.manager.task_cards_to_execution",
        "planning.close_manager_complete",
        "planning.artifacts.task_cards",
        "execution.lad.task",
        "execution.artifacts.task",
        "inherit_source_lineage",
        "depends_on_source_work_item",
        "refuse",
    )


def test_planning_lad_recovery_and_operator_interventions_are_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    actions = {str(action.id): action for action in plan.terminal_actions}
    policy = next(
        item
        for item in plan.recovery_policies
        if str(item.id) == "planning.blocked.recovery"
    )
    counters = {
        str(item.id): item
        for item in plan.counters
        if str(item.id).startswith("planning.")
    }
    interventions = {
        str(item.id): item
        for item in plan.intervention_options
        if str(item.id).startswith("planning.")
    }

    for stage in ("planner", "manager", "mechanic", "auditor"):
        assert actions[f"planning.route_{stage}_blocked"].action_kind == (
            "recovery_route"
        )
        assert actions[f"planning.escalate_{stage}_blocked_exhausted"].action_kind == (
            "recovery_route"
        )
    assert actions["planning.return_mechanic_recovered"].action_kind == (
        "return_to_recorded_source"
    )
    assert actions["planning.quarantine_mechanic_blocked"].action_kind == (
        "quarantine_lineage"
    )
    mechanic = actions["planning.route_mechanic_complete"]
    assert isinstance(mechanic.dynamic_target_selector, Mapping)
    assert mechanic.dynamic_target_selector["field_names"] == ("resume_stage",)
    assert "mechanic" not in mechanic.dynamic_target_selector["targets"]
    assert tuple(str(item) for item in policy.source_recovery_action_ids) == (
        "planning.route_planner_blocked",
        "planning.route_manager_blocked",
        "planning.route_mechanic_blocked",
        "planning.route_auditor_blocked",
    )
    assert (
        str(policy.recovery_stage_kind_id),
        policy.immediate_recovery_limit,
        policy.cooldown_starts_at_attempt,
        policy.quarantine_threshold_attempt,
        policy.default_cooldown_seconds,
        str(policy.cooldown_wait_state_id),
    ) == (
        "lad_mechanic",
        1,
        2,
        2,
        900,
        "planning.blocked.recovery.cooldown",
    )
    assert set(counters) == {
        "planning.mechanic_attempt_count.planner",
        "planning.mechanic_attempt_count.manager",
        "planning.mechanic_attempt_count.mechanic",
        "planning.mechanic_attempt_count.auditor",
    }
    assert all(counter.threshold_count == 2 for counter in counters.values())
    assert {
        option_id: option.option_kind for option_id, option in interventions.items()
    } == {
        "planning.blocked.resume_lineage": "resume_lineage",
        "planning.blocked.close_lineage": "close_lineage",
        "planning.blocked.revise_lineage": "revise_lineage",
    }
    revise = interventions["planning.blocked.revise_lineage"]
    assert (
        str(revise.payload_schema_id),
        str(revise.target_queue_family_id),
        str(revise.target_stage_kind_id),
        revise.target_graph_node_id,
        str(revise.target_runner_binding_id),
    ) == (
        "planning.intake.spec",
        "spec",
        "lad_planner",
        "planning.lad.planner.start",
        "lad_planner.millforge_runner",
    )


def test_planning_lad_closure_and_remediation_authority_is_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    behavior = plan.completion_behaviors[0]
    remediation = plan.remediation_policies[0]
    actions = {str(action.id): action for action in plan.terminal_actions}

    assert (
        str(behavior.id),
        behavior.trigger,
        behavior.readiness_rule,
        behavior.request_kind,
        behavior.target_selector,
        str(behavior.target_stage_kind_id),
        behavior.target_graph_node_id,
        str(behavior.runner_binding_id),
        str(behavior.request_queue_family_id),
        str(behavior.pass_action_id),
        str(behavior.gap_action_id),
        str(behavior.blocked_action_id),
        str(behavior.verdict_artifact_schema_id),
        str(behavior.remediation_policy_id),
    ) == (
        "planning.closure.completion",
        "backlog_drained",
        "no_open_lineage_work",
        "closure_target",
        "active_closure_target",
        "lad_arbiter",
        "planning.lad.arbiter.start",
        "lad_arbiter.millforge_runner",
        "stage_result",
        "planning.close_arbiter_complete",
        "planning.closure_gap",
        "planning.close_arbiter_blocked",
        "planning.artifacts.verdict",
        "planning.closure.remediation",
    )
    assert behavior.accepted_root_source_kinds == (
        "idea",
        "probe",
        "manual",
        "spec",
        "incident",
    )
    assert {
        action_id: actions[action_id].action_kind
        for action_id in (
            "planning.close_arbiter_complete",
            "planning.closure_gap",
            "planning.close_arbiter_blocked",
        )
    } == {
        "planning.close_arbiter_complete": "complete_work_item",
        "planning.closure_gap": "closure_gap",
        "planning.close_arbiter_blocked": "block_work_item",
    }
    assert (
        str(remediation.id),
        str(remediation.source_action_id),
        str(remediation.target_queue_family_id),
        str(remediation.target_stage_kind_id),
        remediation.target_graph_node_id,
        str(remediation.target_runner_binding_id),
        str(remediation.payload_schema_id),
        remediation.dedupe_key,
        remediation.duplicate_policy,
    ) == (
        "planning.closure.remediation",
        "planning.closure_gap",
        "incident",
        "lad_auditor",
        "planning.lad.auditor.start",
        "lad_auditor.millforge_runner",
        "planning.intake.incident",
        "closure_target_and_source_artifact",
        "refuse",
    )


@pytest.mark.parametrize("workflow_id", ARBITER_WORKFLOW_IDS)
def test_arbiter_selected_authority_is_one_strict_verdict_contract(
    workflow_id: str,
    tmp_path: Path,
) -> None:
    plan = _selected_workflow_plan(tmp_path, workflow_id)
    stage, binding, behavior, outcomes, actions = _arbiter_authority(plan)
    verdict_schema = next(
        schema
        for schema in plan.artifact_schemas
        if str(schema.id) == "planning.artifacts.verdict"
    )
    stage_schema_ids = tuple(str(schema_id) for schema_id in stage.artifact_schema_ids)
    stage_output_queue_ids = tuple(
        str(queue_id) for queue_id in stage.output_queue_family_ids
    )
    assert stage_schema_ids == ("planning.artifacts.verdict",)
    assert stage_output_queue_ids == ("verdict",)
    assert "planning.artifacts.incident_report" in {
        str(schema.id) for schema in plan.artifact_schemas
    }
    assert "planning.artifacts.rubric" not in {
        str(schema.id) for schema in plan.artifact_schemas
    }
    assert "rubric" not in {str(queue.id) for queue in plan.queue_families}

    expected_top_level = {
        "artifact_kind",
        "summary",
        "closure_target_id",
        "root_contract_digest",
        "freshness_anchor_digest",
        "rubric",
        "criterion_results",
        "observations",
        "remediation_guidance",
        "confidence",
        "residual_uncertainty",
    }
    properties = verdict_schema.schema["properties"]
    assert isinstance(properties, Mapping)
    assert set(properties) == expected_top_level
    assert set(verdict_schema.schema["required"]) == expected_top_level
    assert validate_schema(verdict_schema.schema, _closure_verdict_payload()).accepted

    invalid_nested = deepcopy(_closure_verdict_payload())
    invalid_nested["rubric"]["criteria"][0]["extra"] = "undeclared"
    assert not validate_schema(verdict_schema.schema, invalid_nested).accepted

    duplicate_criteria = deepcopy(_closure_verdict_payload())
    duplicate_criteria["rubric"]["criteria"].append(
        duplicate_criteria["rubric"]["criteria"][0].copy()
    )
    assert not validate_schema(verdict_schema.schema, duplicate_criteria).accepted

    actions_by_marker = {
        str(outcome.marker): actions[str(outcome.id)]
        for outcome in outcomes.values()
    }
    assert set(actions_by_marker) == {
        "ARBITER_COMPLETE",
        "REMEDIATION_NEEDED",
        "BLOCKED",
    }
    assert {
        marker: str(action.artifact_schema_id)
        for marker, action in actions_by_marker.items()
    } == {
        "ARBITER_COMPLETE": "planning.artifacts.verdict",
        "REMEDIATION_NEEDED": "planning.artifacts.verdict",
        "BLOCKED": "planning.artifacts.verdict",
    }
    assert (
        tuple(str(schema_id) for schema_id in behavior.evidence_artifact_schema_ids)
        == EXPECTED_CLOSURE_EVIDENCE_SCHEMA_IDS[workflow_id]
    )
    assert behavior.evidence_item_limit == (64 if workflow_id == "planning.lad" else 96)
    assert behavior.request_payload_byte_limit == 16384
    assert binding.component_pin is not None
    assert binding.component_pin.max_work_item_payload_bytes == 16384


@pytest.mark.parametrize("workflow_id", ARBITER_WORKFLOW_IDS)
def test_arbiter_completion_schema_mutation_is_refused_by_compiler(
    workflow_id: str,
) -> None:
    source = conformance.packaged_workflow_source(PACKAGE_ROOT, workflow_id)
    schema_record = _record(
        source,
        "artifact_schemas",
        "planning.artifacts.verdict",
    )
    schema = cast(Record, schema_record["schema"])
    properties = cast(dict[str, object], schema["properties"])
    summary = cast(Record, properties["summary"])
    summary["min_length"] = 2

    result = compile_workflow(source)

    assert result.plan is None
    diagnostic = _error(
        result,
        "invalid_completion_behavior_declaration",
        "completion_behaviors[0].verdict_artifact_schema_id",
    )
    assert diagnostic.context["reason"] == "invalid_verdict_artifact_schema"


@pytest.mark.parametrize("workflow_id", ARBITER_WORKFLOW_IDS)
def test_arbiter_assets_require_frozen_rubric_fresh_provenance_and_runtime_aftermath(
    workflow_id: str,
) -> None:
    root = PACKAGE_ROOT / "assets/workflows/planning.lad"
    entrypoint = (root / "entrypoints/lad_arbiter.md").read_text()
    core = (root / "skills/arbiter-core.md").read_text()
    text = (entrypoint + "\n" + core).lower()

    for required in (
        "implementation source/tests are read-only",
        "qa does not fix code or tests",
        "qa does not format, refactor, clean up, upgrade dependencies, edit docs",
        "qa does not route work, mutate queues, close targets, retry work",
        "optional skills cannot expand acceptance scope",
        "every blocking finding cites frozen criterion ids",
        "new observations remain non-blocking",
        "terminal markers are evidence candidates whose aftermath is runtime-owned",
        "current unrestricted runner capabilities do not grant the qa role permission",
        "expanded review is bounded and inline",
        "fresh or revalidated",
        "historical context",
        "one exact selected verdict artifact",
    ):
        assert required in text

    assert "marathon-qa-audit" not in text

    ordered_steps = (
        "read closure target, root contract, and trusted digests",
        "read " + chr(96) + "prior_verdict" + chr(96) + " before current evidence",
        "on first evaluation, derive the rubric solely from the root contract",
        "on later evaluations, copy the prior rubric exactly",
        "inspect the fresh evidence list and current repository state",
        "label every criterion result with its provenance",
        "use old evidence only as historical context unless explicitly revalidated",
        "return one exact selected verdict artifact and one legal marker",
    )
    positions = [text.find(step) for step in ordered_steps]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)

    assert "runtime continues" in text
    assert "canonical remediation work" in text
    assert "incident/task/spec/probe/learning queue files" in text


def test_arbiter_entrypoint_has_selected_public_authoring_sections() -> None:
    entrypoint = (
        PACKAGE_ROOT
        / "assets/workflows/planning.lad/entrypoints/lad_arbiter.md"
    ).read_text()
    assert all(
        heading in entrypoint
        for heading in (
            "## Inputs from dispatch",
            "## Readable assets",
            "## Writable artifacts",
            "## Required evidence",
            "## Legal terminal markers rendered by runtime",
            "## Forbidden claims",
        )
    )


def test_arbiter_assets_allow_null_prior_verdict_only_on_first_evaluation() -> None:
    root = PACKAGE_ROOT / "assets/workflows/planning.lad"
    expected = (
        "A first evaluation legally receives `prior_verdict: null` and creates "
        "the rubric from `root_contract`. Only a later evaluation is blocked "
        "when its required prior verdict or freshness anchor is absent or "
        "contradictory."
    )
    for asset in (
        root / "entrypoints/lad_arbiter.md",
        root / "skills/arbiter-core.md",
    ):
        assert expected in asset.read_text()


def test_arbiter_core_validation_examples_are_parseable_and_schema_valid_or_invalid(
) -> None:
    source = conformance.packaged_workflow_source(PACKAGE_ROOT, "planning.lad")
    verdict_schema = cast(
        dict[str, object],
        _record(source, "artifact_schemas", "planning.artifacts.verdict")["schema"],
    )
    core = (
        PACKAGE_ROOT / "assets/workflows/planning.lad/skills/arbiter-core.md"
    ).read_text()
    examples = _arbiter_json_examples(core)
    assert set(examples) == {
        "Valid JSON example",
        "Invalid JSON example: extra field",
        "Invalid JSON example: missing required field",
        "Invalid JSON example: wrong type",
    }
    assert validate_schema(verdict_schema, examples["Valid JSON example"]).accepted
    assert not validate_schema(
        verdict_schema,
        examples["Invalid JSON example: extra field"],
    ).accepted
    assert not validate_schema(
        verdict_schema,
        examples["Invalid JSON example: missing required field"],
    ).accepted
    assert not validate_schema(
        verdict_schema,
        examples["Invalid JSON example: wrong type"],
    ).accepted


@pytest.mark.parametrize(
    ("mutation", "diagnostic_code", "path_suffix", "context_key", "context_value"),
    (
        (
            "route_graph",
            "missing_reference",
            "external_enqueue_routes[0].graph_node_id",
            "referenced_id",
            "planning.lad.missing.start",
        ),
        (
            "route_schema",
            "missing_reference",
            "external_enqueue_routes[0].payload_schema_id",
            "referenced_id",
            "planning.intake.missing",
        ),
        ("duplicate_queue", "duplicate_id", None, "duplicate_id", "spec"),
        (
            "missing_asset",
            "missing_reference",
            ".asset_ids[1]",
            "referenced_id",
            "planning.skills.missing",
        ),
        (
            "missing_outcome",
            "missing_reference",
            ".outcome_id",
            "referenced_id",
            "planning.lad_planner.MISSING",
        ),
        (
            "invalid_schema",
            "invalid_artifact_schema",
            None,
            "schema_id",
            "planning.artifacts.task_cards",
        ),
        (
            "request_kind",
            "invalid_completion_behavior_declaration",
            "completion_behaviors[0].request_kind",
            "reason",
            "unsupported_request_kind",
        ),
        (
            "completion_graph",
            "missing_reference",
            "completion_behaviors[0].target_graph_node_id",
            "referenced_id",
            "planning.lad.missing.start",
        ),
        (
            "guidance_source",
            "invalid_remediation_policy_declaration",
            "remediation_policies[0].guidance_source",
            "reason",
            "unsupported_guidance_source",
        ),
        (
            "remediation_action",
            "invalid_remediation_policy_declaration",
            "remediation_policies[0].source_action_id",
            "reason",
            "unsupported_source_action_kind",
        ),
        (
            "compatibility_profile",
            "unsupported_compatibility_profile",
            "workflow.compatibility_profile",
            "compatibility_profile",
            "lad_codex",
        ),
        (
            "old_loop_override",
            "unknown_source_section",
            "old_loop_config_override",
            "section",
            "old_loop_config_override",
        ),
    ),
)
def test_planning_lad_refuses_workflow_specific_authority_mutations(
    mutation: str,
    diagnostic_code: str,
    path_suffix: str | None,
    context_key: str,
    context_value: str,
) -> None:
    source = _source()
    if mutation in {"route_graph", "route_schema"}:
        route = _records(source, "external_enqueue_routes")[0]
        if mutation == "route_graph":
            route["graph_node_id"] = "planning.lad.missing.start"
        else:
            route["payload_schema_id"] = "planning.intake.missing"
    elif mutation == "duplicate_queue":
        families = _records(source, "queue_families")
        source["queue_families"] = (*families, dict(families[0]))
    elif mutation == "missing_asset":
        _record(source, "stage_kinds", "recon")["asset_ids"] = (
            "planning.entrypoints.recon",
            "planning.skills.missing",
        )
    elif mutation == "missing_outcome":
        _record(source, "terminal_actions", "planning.route_planner_complete")[
            "outcome_id"
        ] = "planning.lad_planner.MISSING"
    elif mutation == "invalid_schema":
        schema = cast(
            Record,
            _record(source, "artifact_schemas", "planning.artifacts.task_cards")[
                "schema"
            ],
        )
        schema["type"] = "number"
    elif mutation in {"request_kind", "completion_graph"}:
        behavior = _records(source, "completion_behaviors")[0]
        if mutation == "request_kind":
            behavior["request_kind"] = "wrong_request"
        else:
            behavior["target_graph_node_id"] = "planning.lad.missing.start"
    elif mutation in {"guidance_source", "remediation_action"}:
        policy = _records(source, "remediation_policies")[0]
        if mutation == "guidance_source":
            policy["guidance_source"] = "static"
        else:
            policy["source_action_id"] = "planning.close_arbiter_complete"
    elif mutation == "compatibility_profile":
        cast(Record, source["workflow"])["compatibility_profile"] = "lad_codex"
    else:
        source["old_loop_config_override"] = {"path": "assets/loops/planning/lad.json"}

    result = compile_workflow(source)
    error = _error(result, diagnostic_code, path_suffix)
    assert result.plan is None
    assert error.context[context_key] == context_value


# Rows are (Millrace source owner, collected rows, Plus replacements, generic N/A).
_PLANNING_SOURCE_OWNER_LEDGER = (
    ("compiler/test_lad_planning_compile.py", 20, 20, 0),
    ("kernel/test_lad_planning_intake_dispatch.py", 16, 13, 3),
    ("kernel/test_lad_planning_work_shaping.py", 15, 11, 4),
    ("kernel/test_lad_planning_downstream_routing.py", 11, 6, 5),
    ("kernel/test_lad_planning_recovery.py", 9, 9, 0),
    ("kernel/test_lad_planning_closure.py", 26, 9, 17),
    ("operator/test_lad_planning_status_projection.py", 5, 0, 5),
)

_PLANNING_GENERIC_N_A = (
    ("plan selection/fingerprint runtime refusals", 3),
    ("payload/source/override transition mechanics", 4),
    ("restart persistence mechanics", 5),
    ("closure root relation and persistence corruption", 17),
    ("status-projection mechanics", 5),
)


def test_planning_source_owner_row_disposition_ledger_is_complete() -> None:
    assert len(_PLANNING_SOURCE_OWNER_LEDGER) == 7
    assert sum(row[1] for row in _PLANNING_SOURCE_OWNER_LEDGER) == 102
    assert sum(row[2] for row in _PLANNING_SOURCE_OWNER_LEDGER) == 68
    assert sum(row[3] for row in _PLANNING_SOURCE_OWNER_LEDGER) == 34
    assert all(
        total == replacement + n_a
        for _, total, replacement, n_a in _PLANNING_SOURCE_OWNER_LEDGER
    )
    assert sum(rows for _cluster, rows in _PLANNING_GENERIC_N_A) == 34
