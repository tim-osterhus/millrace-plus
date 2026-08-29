from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from millrace.adapters.millforge import MillforgeAdapter, MillforgeAdapterConfig
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterSuccessResult,
    RedactionPolicy,
    StartedSession,
)
from millrace.compiler import (
    authority_fingerprint,
    canonical_authority_bytes,
    compile_workflow,
)
from millrace.contracts import SelectedCompiledPlan
from millrace.contracts.compiled_plan import AuthorityValue, TerminalActionDeclaration
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.schema import validate_schema, validate_schema_declaration
from millrace.contracts.state import Activation, CounterRecord, RunRecord, RuntimeState
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    RunnerResultObserved,
    SelectDefaultPlan,
    TransitionContext,
    TransitionInput,
    artifact_payload_digest,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.kernel.projection import ProjectionContext, evaluate_projection
from millrace.kernel.terminal_actions import (
    _counter_record_id,
    _route_target_fields_or_refusal,
)
from millrace.testing import (
    decide_with_fake_runner_completion,
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
    fake_runner_observation_payload,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.3"
WORKFLOW_IDS = ("execution.lad", "execution.lad_integrator")
QA_SCOPE_IDS = (*WORKFLOW_IDS, "planning.lad", "lad.full")
SEMANTIC_WORKFLOW_ID = "execution.lad_codex_semantic_worktree"
_SEMANTIC_ASSET_PREFIX = (
    "execution.lad_codex_semantic_worktree"
)
_CODEX_ROUTER_ASSET_ID = f"{_SEMANTIC_ASSET_PREFIX}.context_router"
_CODEX_ENTRYPOINT_ASSET_IDS = {
    stage: f"{_SEMANTIC_ASSET_PREFIX}.entrypoints.{stage}"
    for stage in (
        "lad_builder",
        "lad_checker",
        "lad_fixer",
        "lad_doublechecker",
        "lad_updater",
    )
}
_CODEX_UPDATER_SKILL_ASSET_ID = (
    f"{_SEMANTIC_ASSET_PREFIX}.skills.updater_core"
)
_CODEX_CONTEXT_SCHEMA_ID = "execution.artifacts.context_update_report"
_CODEX_CONTEXT_BINDING_IDS = {
    stage: f"{SEMANTIC_WORKFLOW_ID}.{stage}_context"
    for stage in (
        "lad_builder",
        "lad_checker",
        "lad_fixer",
        "lad_doublechecker",
        "lad_troubleshooter",
        "lad_updater",
    )
}
_SEMANTIC_ACTION_TARGET_STAGES = {
    "execution.route_builder_complete": "lad_checker",
    "execution.route_checker_pass": "lad_updater",
    "execution.route_checker_fix_needed": "lad_fixer",
    "execution.route_fixer_complete": "lad_doublechecker",
    "execution.route_doublechecker_pass": "lad_updater",
    "execution.route_doublechecker_fix_needed": "lad_fixer",
    "execution.return_troubleshooter_complete": "lad_fixer",
    "execution.return_troubleshooter_baseline_invalidated": "lad_builder",
    "execution.return_troubleshooter_review_retry": "lad_checker",
}
_REFERENCE_LAD_CANONICAL_AUTHORITY_SHA256 = (
    "59d80b330996a3d27461acb15494fb52ad83d52aa80ea35b73199447c4e2aff4"
)
_REFERENCE_LAD_CANONICAL_AUTHORITY_BYTE_LENGTH = 67880
_REFERENCE_LAD_SELECTED_ASSET_PINS = (
    (
        "execution.entrypoints.lad_builder",
        "sha256:2ae327cd582353e2510de7dff31ee55eb70f4ffcdfa2d2303db6bd2201b92a8c",
    ),
    (
        "execution.skills.builder_core",
        "sha256:d789eb8a142451cffd3476b11671ab7b636a3e704c109d204c82db3e9d5d3dac",
    ),
    (
        "execution.entrypoints.lad_checker",
        "sha256:4444f19a996e9329f2ec0bda4c9b3c61600f29e91d362b05c1777713e2dc369a",
    ),
    (
        "execution.skills.checker_core",
        "sha256:81a4b59af7f82d91951dfa08d5d2d7f6fce548d8f2b370fe7c8dd99d26fc7140",
    ),
    (
        "execution.entrypoints.lad_fixer",
        "sha256:9af21092225299aab1ba99311fa3c32933ab9d5c44597a257004b0aed59a9917",
    ),
    (
        "execution.skills.fixer_core",
        "sha256:5834155f56f905c2a6344932cdaddd6ffd7f5d6f131e7027f33c6452c6acae75",
    ),
    (
        "execution.entrypoints.lad_doublechecker",
        "sha256:9af86c634a8159967489118ecba47a965b1685a62180cfa4789c079f196167d0",
    ),
    (
        "execution.skills.doublechecker_core",
        "sha256:0ce450272d17ca87534064c798a0aa8e63ec69703f91140e356e5b22a49b9026",
    ),
    (
        "execution.entrypoints.lad_updater",
        "sha256:f72aa2b7fe13d6eb78be2aa62184dc67d644e0a7834f0b1fefb6d232ab3f6544",
    ),
    (
        "execution.skills.updater_core",
        "sha256:728998443904834ab25ca2f05da420b7d8b74f738fe506523df91902fefdbee2",
    ),
    (
        "execution.entrypoints.lad_troubleshooter",
        "sha256:cc9c35e0a78e627e572cd2eb183275fc4c1a9c4964201cc5e267e6a2c2f86d3c",
    ),
    (
        "execution.skills.troubleshooter_core",
        "sha256:c483243abe2afa86c447c47b16ada4b8d1a160147a5e4f95afa602be2899dc1b",
    ),
    (
        "execution.entrypoints.lad_consultant",
        "sha256:af500ad1e542b4ad582931a12549c72544ac4709c819931daa7800c4f133952f",
    ),
    (
        "execution.skills.consultant_core",
        "sha256:ac23328c80c9445f5302ebd361b25fded1c4b36fd0bc8e6cebff563e808cfe55",
    ),
)
Record = dict[str, object]

_NORMALIZING_ROUTE_IDS = frozenset(
    {
        "execution.route_builder_complete",
        "execution.route_builder_blocked",
        "execution.route_checker_pass",
        "execution.route_checker_fix_needed",
        "execution.route_checker_blocked",
    }
)
_COMMON_RECOVERY_ROUTE_IDS = frozenset(
    {
        "execution.escalate_checker_fix_exhausted",
        "execution.escalate_doublechecker_fix_exhausted",
        "execution.escalate_builder_blocked_exhausted",
        "execution.recover_builder_runtime_failure",
        "execution.escalate_checker_blocked_exhausted",
        "execution.recover_checker_runtime_failure",
        "execution.escalate_fixer_blocked_exhausted",
        "execution.recover_fixer_runtime_failure",
        "execution.escalate_doublechecker_blocked_exhausted",
        "execution.recover_doublechecker_runtime_failure",
        "execution.escalate_updater_blocked_exhausted",
        "execution.recover_updater_runtime_failure",
        "execution.escalate_troubleshooter_blocked_exhausted",
        "execution.recover_troubleshooter_runtime_failure",
    }
)
_COMMON_RETURN_ACTION_IDS = frozenset(
    {
        "execution.return_troubleshooter_recovered",
        "execution.return_consultant_recovered",
    }
)


def _manifest() -> dict[str, Any]:
    return conformance.assert_packaged_asset_closure(PACKAGE_ROOT)


def _source(workflow_id: str = WORKFLOW_IDS[0]) -> dict[str, object]:
    return conformance.packaged_workflow_source(PACKAGE_ROOT, workflow_id)


def _records(source: dict[str, object], section: str) -> list[Record]:
    return cast(list[Record], source[section])


def _record(source: dict[str, object], section: str, record_id: str) -> Record:
    return next(item for item in _records(source, section) if item["id"] == record_id)


def _codex_source(workflow_id: str) -> dict[str, object]:
    manifest = _manifest()
    workflows = conformance.workflows_by_id(manifest)
    assert workflow_id in workflows, f"missing public Codex workflow {workflow_id}"
    return conformance.packaged_workflow_source(PACKAGE_ROOT, workflow_id)


def _source_context_bindings(source: dict[str, object]) -> list[Record]:
    return cast(list[Record], source.get("context_bindings", []))


def _codex_descriptor_digest(runner: Record) -> str:
    component = cast(Record, runner["component_pin"])
    stage_kind_id = cast(list[object], runner["stage_kind_ids"])[0]
    descriptor = {
        "record_kind": "millrace.codex.wrapper_component_descriptor",
        "wrapper_schema_version": 4,
        "provider_distribution": "@openai/codex",
        "provider_version": "0.147.0",
        "stage_kind_id": stage_kind_id,
        "required_capability_ids": component["required_capability_ids"],
        "legal_terminal_result_ids": component["legal_terminal_result_ids"],
    }
    return hashlib.sha256(_canonical_payload_bytes(descriptor)).hexdigest()


def _runner_records(source: dict[str, object]) -> tuple[Record, ...]:
    return tuple(_records(source, "runner_bindings"))


def _context_update_report_schema() -> dict[str, object]:
    string_schema = {"type": "string"}
    evidence_refs = {"type": "array", "items": string_schema}
    return {
        "type": "object",
        "required": ["changes", "proposals"],
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "path",
                        "change_kind",
                        "evidence_refs",
                        "classification",
                    ],
                    "properties": {
                        "path": string_schema,
                        "change_kind": {
                            "enum": ["create", "modify", "delete"]
                        },
                        "before_sha256": string_schema,
                        "after_sha256": string_schema,
                        "evidence_refs": evidence_refs,
                        "classification": {"const": "direct_write"},
                    },
                },
            },
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "path",
                        "proposed_content",
                        "proposed_content_sha256",
                        "evidence_refs",
                        "classification",
                    ],
                    "properties": {
                        "path": string_schema,
                        "proposed_content": string_schema,
                        "proposed_content_sha256": string_schema,
                        "evidence_refs": evidence_refs,
                        "classification": {"const": "protected_proposal"},
                    },
                },
            },
            "no_op_reason": string_schema,
        },
    }


def _json_example(text: str, heading: str) -> Record:
    start = text.index(heading)
    next_heading = text.find("\n## ", start + len(heading))
    section = text[start:] if next_heading == -1 else text[start:next_heading]
    match = re.search(r"```json\n(.*?)\n```", section, flags=re.DOTALL)
    assert match is not None, heading
    value = json.loads(match.group(1))
    assert isinstance(value, dict)
    return cast(Record, value)


def _error(result: object, code: str) -> object:
    return next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error" and diagnostic.code == code
    )


def _qa_source(workflow_id: str) -> dict[str, object]:
    return conformance.packaged_workflow_source(PACKAGE_ROOT, workflow_id)


def _nonblank() -> dict[str, object]:
    return {"min_length": 1, "type": "string"}


def _object(
    properties: dict[str, object],
    required: tuple[str, ...],
) -> dict[str, object]:
    return {"properties": properties, "required": list(required), "type": "object"}


def _array(
    items: dict[str, object],
    *,
    unique_by: str,
    min_items: int | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "items": items,
        "type": "array",
        "unique_by": unique_by,
    }
    if min_items is not None:
        result["min_items"] = min_items
    return result


def _id_ref(unique_by: str) -> dict[str, object]:
    return _object(
        {unique_by: _nonblank()},
        (unique_by,),
    )


def _observation_schema() -> dict[str, object]:
    item = _object(
        {"observation_id": _nonblank(), "summary": _nonblank()},
        ("observation_id", "summary"),
    )
    return _array(item, unique_by="observation_id")


def _check_schema() -> dict[str, object]:
    item = _object(
        {
            "check_id": _nonblank(),
            "command_or_method": _nonblank(),
            "result": {"enum": ["passed", "failed", "unavailable"]},
        },
        ("check_id", "command_or_method", "result"),
    )
    return _array(item, unique_by="check_id")


def _checker_schema() -> dict[str, object]:
    criterion = _object(
        {
            "criterion_id": _nonblank(),
            "requirement": _nonblank(),
            "evidence_rule": _nonblank(),
        },
        ("criterion_id", "requirement", "evidence_rule"),
    )
    finding = _object(
        {
            "criterion_refs": _array(
                _id_ref("criterion_id"),
                unique_by="criterion_id",
                min_items=1,
            ),
            "finding_id": _nonblank(),
            "impact": _nonblank(),
            "observed_gap": _nonblank(),
            "post_fix_check_refs": _array(
                _id_ref("check_id"),
                unique_by="check_id",
                min_items=1,
            ),
            "repair_surface": _nonblank(),
        },
        (
            "finding_id",
            "observed_gap",
            "impact",
            "repair_surface",
            "criterion_refs",
            "post_fix_check_refs",
        ),
    )
    return _object(
        {
            "artifact_kind": {"const": "execution.artifacts.checker_result"},
            "checks": _check_schema(),
            "criteria": _array(
                criterion,
                unique_by="criterion_id",
                min_items=1,
            ),
            "findings": _array(finding, unique_by="finding_id"),
            "observations": _observation_schema(),
            "summary": _nonblank(),
            "task_contract_digest": _nonblank(),
        },
        (
            "artifact_kind",
            "summary",
            "task_contract_digest",
            "criteria",
            "findings",
            "observations",
            "checks",
        ),
    )


def _doublechecker_schema() -> dict[str, object]:
    evidence_ref = _object(
        {"evidence_id": _nonblank(), "summary": _nonblank()},
        ("evidence_id", "summary"),
    )
    finding_status = _object(
        {
            "criterion_refs": _array(
                _id_ref("criterion_id"),
                unique_by="criterion_id",
                min_items=1,
            ),
            "evidence_refs": _array(evidence_ref, unique_by="evidence_id"),
            "finding_id": _nonblank(),
            "next_repair": _nonblank(),
            "status": {
                "enum": [
                    "resolved",
                    "unresolved",
                    "displaced",
                    "blocked",
                    "invalid_contract",
                ]
            },
        },
        (
            "finding_id",
            "status",
            "next_repair",
            "criterion_refs",
            "evidence_refs",
        ),
    )
    return _object(
        {
            "artifact_kind": {"const": "execution.artifacts.doublecheck_result"},
            "checker_baseline_digest": _nonblank(),
            "checks": _check_schema(),
            "finding_statuses": _array(
                finding_status,
                unique_by="finding_id",
                min_items=1,
            ),
            "observations": _observation_schema(),
            "summary": _nonblank(),
            "task_contract_digest": _nonblank(),
        },
        (
            "artifact_kind",
            "summary",
            "task_contract_digest",
            "checker_baseline_digest",
            "finding_statuses",
            "checks",
            "observations",
        ),
    )


def _checker_payload(
    kind: str = "execution.artifacts.checker_result",
) -> dict[str, object]:
    return {
        "artifact_kind": kind,
        "summary": "criteria reviewed",
        "task_contract_digest": "sha256:task",
        "criteria": [
            {
                "criterion_id": "criterion-1",
                "requirement": "the task is implemented",
                "evidence_rule": "run the named check",
            }
        ],
        "findings": [
            {
                "finding_id": "finding-1",
                "observed_gap": "the check is not green",
                "impact": "the task is not accepted",
                "repair_surface": "implementation",
                "criterion_refs": [{"criterion_id": "criterion-1"}],
                "post_fix_check_refs": [{"check_id": "check-1"}],
            }
        ],
        "observations": [
            {"observation_id": "observation-1", "summary": "unrelated note"}
        ],
        "checks": [
            {
                "check_id": "check-1",
                "command_or_method": "pytest -q",
                "result": "passed",
            }
        ],
    }


def _doublechecker_payload(
    kind: str = "execution.artifacts.doublecheck_result",
) -> dict[str, object]:
    return {
        "artifact_kind": kind,
        "summary": "original finding rechecked",
        "task_contract_digest": "sha256:task",
        "checker_baseline_digest": "sha256:baseline",
        "finding_statuses": [
            {
                "finding_id": "finding-1",
                "status": "resolved",
                "next_repair": "none",
                "criterion_refs": [{"criterion_id": "criterion-1"}],
                "evidence_refs": [
                    {"evidence_id": "evidence-1", "summary": "check passed"}
                ],
            }
        ],
        "checks": [
            {
                "check_id": "check-1",
                "command_or_method": "pytest -q",
                "result": "passed",
            }
        ],
        "observations": [],
    }


def _projection_fields(action: Record) -> dict[str, object]:
    projection = cast(Record, action["payload_projection"])
    assert projection["kind"] == "object"
    return cast(dict[str, object], projection["fields"])


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _canonical_payload_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _recorded_qa_context(tag: str) -> dict[str, object]:
    return {
        "task_contract": {
            "task_id": f"recorded-{tag}",
            "requirements": ["preserve this carrier"],
        },
        "task_contract_digest": f"sha256:task-{tag}",
        "checker_baseline": {
            "artifact_kind": "execution.artifacts.checker_result",
            "criteria": [{"criterion_id": f"criterion-{tag}"}],
        },
        "checker_baseline_digest": f"sha256:baseline-{tag}",
    }


def _expected_recovery_route_ids(workflow_id: str) -> frozenset[str]:
    if workflow_id == "execution.lad_integrator":
        return _COMMON_RECOVERY_ROUTE_IDS | {
            "execution.escalate_integrator_blocked_exhausted",
            "execution.recover_integrator_runtime_failure",
        }
    return _COMMON_RECOVERY_ROUTE_IDS


def _passing_checker_payload() -> dict[str, object]:
    payload = _checker_payload()
    payload["findings"] = []
    payload["observations"] = [
        {
            "observation_id": "observation-only",
            "summary": "A supplementary observation is outside the frozen finding set.",
        }
    ]
    return payload


def _passing_doublechecker_payload() -> dict[str, object]:
    payload = _doublechecker_payload()
    payload["observations"] = [
        {
            "observation_id": "observation-only",
            "summary": "A supplementary observation is outside the frozen finding set.",
        }
    ]
    return payload


def _projected_payload(
    action: Record,
    *,
    work_item_payload: dict[str, object],
    artifact_payload: dict[str, object],
    work_item_digest: str = "sha256:work-item",
    artifact_digest: str = "sha256:artifact",
) -> dict[str, object]:
    result = evaluate_projection(
        action["payload_projection"],
        ProjectionContext(
            work_item_payload=work_item_payload,
            artifact_payload=artifact_payload,
            observation_payload={},
            run_metadata={
                "work_item_payload_digest": work_item_digest,
                "artifact_payload_digest": artifact_digest,
            },
            plan_metadata={},
        ),
    )
    assert result.accepted is True, result.error
    return cast(dict[str, object], result.value)


def _execution_action(
    plan: SelectedCompiledPlan,
    action_id: str,
) -> TerminalActionDeclaration:
    return next(
        action for action in plan.terminal_actions if str(action.id) == action_id
    )


def _execution_stage(plan: SelectedCompiledPlan, stage_kind_id: str) -> object:
    return next(stage for stage in plan.stage_kinds if str(stage.id) == stage_kind_id)


def _execution_graph_node(plan: SelectedCompiledPlan, stage_kind_id: str) -> str:
    for route in plan.external_enqueue_routes:
        if str(route.stage_kind_id) == stage_kind_id:
            return route.graph_node_id
    for action in plan.terminal_actions:
        if (
            action.target_stage_kind_id is not None
            and str(action.target_stage_kind_id) == stage_kind_id
            and action.target_graph_node_id is not None
        ):
            return action.target_graph_node_id
        selector = action.dynamic_target_selector
        if not isinstance(selector, Mapping):
            continue
        targets = selector.get("targets")
        if not isinstance(targets, Mapping):
            continue
        for target in targets.values():
            if not isinstance(target, Mapping):
                continue
            if (
                str(target.get("target_stage_kind_id")) == stage_kind_id
                and isinstance(target.get("target_graph_node_id"), str)
            ):
                return cast(str, target["target_graph_node_id"])
    raise AssertionError(f"missing graph node for {stage_kind_id}")


def _apply_accepted_input(
    state: RuntimeState,
    transition_input: TransitionInput,
    context: TransitionContext,
) -> RuntimeState:
    decision = decide(state, transition_input, context)
    assert decision.accepted, decision.refusal
    return apply(state, decision)


def _claimed_execution_stage(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    stage_kind_id: str,
    payload: Mapping[str, AuthorityValue],
    tag: str,
) -> tuple[RuntimeState, RunRecord, Activation]:
    external_route = next(
        route
        for route in plan.external_enqueue_routes
        if str(route.stage_kind_id) == "lad_builder"
    )
    work_item_id = f"work-{tag}"
    activation_id = f"activation-{tag}"
    run_id = f"run-{tag}"
    state = empty_runtime_state()
    for transition_input in (
        InitializeWorkspace(f"initialize-{tag}"),
        AdmitPlan(
            f"admit-{tag}",
            selected_plan=plan,
            authority_fingerprint=fingerprint,
        ),
        SelectDefaultPlan(f"select-{tag}", authority_fingerprint=fingerprint),
    ):
        state = _apply_accepted_input(
            state,
            transition_input,
            deterministic_context(transition_id=f"{tag}-{transition_input.input_id}"),
        )
    state = _apply_accepted_input(
        state,
        EnqueueWork(
            f"enqueue-{tag}",
            queue_family_id=external_route.queue_family_id,
            payload={
                "task_id": cast(AuthorityValue, payload["task_id"]),
                "body": cast(AuthorityValue, payload["body"]),
            },
        ),
        deterministic_context(
            transition_id=f"transition-enqueue-{tag}",
            work_item_id=work_item_id,
            activation_id=activation_id,
        ),
    )
    queued_work_item = state.work_items[work_item_id]
    state = replace(
        state,
        work_items={
            **state.work_items,
            work_item_id: replace(queued_work_item, payload=payload),
        },
    )
    state = _apply_accepted_input(
        state,
        ClaimWork(f"claim-{tag}", activation_id=activation_id),
        deterministic_context(
            transition_id=f"transition-claim-{tag}",
            work_item_id=work_item_id,
            activation_id=activation_id,
            run_id=run_id,
            claim_id=f"claim-{tag}",
            fencing_token=f"fence-{tag}",
        ),
    )

    stage = _execution_stage(plan, stage_kind_id)
    graph_node_id = _execution_graph_node(plan, stage_kind_id)
    activation = replace(
        state.activations[activation_id],
        graph_node_id=graph_node_id,
        stage_kind_id=stage.id,
        runner_binding_id=stage.runner_binding_id,
    )
    run = replace(
        state.runs[run_id],
        stage_kind_id=stage.id,
        runner_binding_id=stage.runner_binding_id,
    )
    state = replace(
        state,
        activations={**state.activations, activation_id: activation},
        runs={**state.runs, run_id: run},
    )
    return state, run, activation


def _claim_existing_execution_activation(
    state: RuntimeState,
    activation_id: str,
    *,
    tag: str,
) -> tuple[RuntimeState, RunRecord]:
    activation = state.activations[activation_id]
    run_id = f"run-{tag}"
    state = _apply_accepted_input(
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


def _trusted_task_contract() -> dict[str, object]:
    return {
        "task_id": "trusted-task-1",
        "body": "Implement the selected task and verify the result.",
        "requirements": [
            {
                "criterion_id": "criterion-contract-1",
                "requirement": "The implementation satisfies the trusted contract.",
                "evidence_rule": "Run the named deterministic check.",
            }
        ],
    }


def _checker_payload_for_contract(
    contract: Mapping[str, object],
    *,
    fix_needed: bool,
) -> dict[str, object]:
    requirements = cast(list[dict[str, object]], contract["requirements"])
    criteria = [
        {
            "criterion_id": str(requirement["criterion_id"]),
            "requirement": str(requirement["requirement"]),
            "evidence_rule": str(requirement["evidence_rule"]),
        }
        for requirement in requirements
    ]
    findings = (
        [
            {
                "finding_id": "finding-contract-1",
                "observed_gap": "The deterministic check is not green.",
                "impact": "The trusted contract is not yet accepted.",
                "repair_surface": "implementation",
                "criterion_refs": [
                    {"criterion_id": criteria[0]["criterion_id"]}
                ],
                "post_fix_check_refs": [{"check_id": "check-contract-1"}],
            }
        ]
        if fix_needed
        else []
    )
    return {
        "artifact_kind": "execution.artifacts.checker_result",
        "summary": "The trusted task contract was reviewed.",
        "task_contract_digest": artifact_payload_digest(contract),
        "criteria": criteria,
        "findings": findings,
        "observations": [],
        "checks": [
            {
                "check_id": "check-contract-1",
                "command_or_method": "deterministic fake runner check",
                "result": "failed" if fix_needed else "passed",
            }
        ],
    }


def _doublechecker_payload_for_contract(
    contract: Mapping[str, object],
    *,
    baseline_digest: str,
    status: str,
) -> dict[str, object]:
    criterion_id = str(
        cast(list[dict[str, object]], contract["requirements"])[0]["criterion_id"]
    )
    return {
        "artifact_kind": "execution.artifacts.doublecheck_result",
        "summary": "The original checker finding was rechecked.",
        "task_contract_digest": artifact_payload_digest(contract),
        "checker_baseline_digest": baseline_digest,
        "finding_statuses": [
            {
                "finding_id": "finding-contract-1",
                "status": status,
                "next_repair": (
                    "none" if status == "resolved" else "repair implementation"
                ),
                "criterion_refs": [{"criterion_id": criterion_id}],
                "evidence_refs": [
                    {
                        "evidence_id": f"evidence-{status}",
                        "summary": "The deterministic check produced current evidence.",
                    }
                ],
            }
        ],
        "checks": [
            {
                "check_id": "check-contract-1",
                "command_or_method": "deterministic fake runner check",
                "result": "passed" if status == "resolved" else "failed",
            }
        ],
        "observations": [],
    }


def _terminal_marker(
    plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
) -> str:
    return next(
        str(outcome.marker)
        for outcome in plan.terminal_outcomes
        if outcome.id == action.outcome_id
    )


def _artifact_payload_for_action(
    action: TerminalActionDeclaration,
) -> dict[str, object]:
    if action.artifact_schema_id is None:
        return {}
    schema_id = str(action.artifact_schema_id)
    if schema_id == "execution.artifacts.checker_result":
        return _checker_payload()
    if schema_id == "execution.artifacts.doublecheck_result":
        return _doublechecker_payload()
    return {
        "artifact_kind": schema_id,
        "summary": f"evidence for {action.id}",
    }


def _run_terminal_action(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    run_id: str,
    action_id: str,
    tag: str,
    artifact_payload: Mapping[str, AuthorityValue],
) -> tuple[RuntimeState, object, str]:
    action = _execution_action(plan, action_id)
    run = state.runs[run_id]
    activation = state.activations[run.activation_id]
    observation = RunnerResultObserved(
        f"observe-{tag}",
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker=_terminal_marker(plan, action),
            artifact_payload=artifact_payload,
        ),
        observed_at=0,
    )
    decision = decide_with_fake_runner_completion(
        state,
        observation,
        deterministic_context(
            transition_id=f"transition-observe-{tag}",
            work_item_id=f"work-target-{tag}",
            activation_id=f"activation-target-{tag}",
            run_id=run_id,
            claim_id=run.run_ref.claim_id,
            fencing_token=run.run_ref.fencing_token,
        ),
    )
    assert decision.accepted, decision.refusal
    return apply(state, decision), decision, f"activation-target-{tag}"


def _seed_counter_at_threshold(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    *,
    action_id: str,
) -> RuntimeState:
    counter = next(
        counter
        for counter in plan.counters
        if str(counter.increment_action_id) == action_id
        or str(counter.threshold_action_id) == action_id
    )
    run = next(iter(state.runs.values()))
    work_item = state.work_items[run.work_item_id]
    assert work_item.lineage_id is not None
    record_id = _counter_record_id(
        plan_ref=run.run_ref.plan_ref,
        counter_id=str(counter.id),
        lineage_id=work_item.lineage_id,
    )
    record = CounterRecord(
        record_id=record_id,
        counter_id=counter.id,
        selected_plan_ref=run.run_ref.plan_ref,
        lineage_id=work_item.lineage_id,
        value=counter.threshold_count - 1,
        updated_by_input_id=f"seed-counter-{action_id}",
    )
    return replace(state, counters={**state.counters, record_id: record})


def _assert_recorded_reactivation(
    state: RuntimeState,
    *,
    source_work_item_id: str,
    recorded_qa_context: Mapping[str, object],
    target_activation_id: str,
    action_id: str,
) -> None:
    recorded_bytes = _canonical_payload_bytes(recorded_qa_context)
    source_work_item = state.work_items[source_work_item_id]
    assert _canonical_payload_bytes(
        _thaw(source_work_item.payload["qa_context"])
    ) == recorded_bytes
    target_activation = state.activations[target_activation_id]
    assert target_activation.work_item_id == source_work_item_id
    route = next(
        route
        for route in state.activation_routes
        if route.target_activation_id == target_activation_id
    )
    assert str(route.action_id) == action_id
    assert route.target_work_item_id == source_work_item_id


def _apply_selected_recovery_action(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    action_id: str,
    recorded_payload: Mapping[str, AuthorityValue],
    tag: str,
) -> RuntimeState:
    action = _execution_action(plan, action_id)
    if action_id.startswith("execution.escalate_"):
        counter = next(
            counter
            for counter in plan.counters
            if str(counter.threshold_action_id) == action_id
        )
        source_action_id = str(counter.increment_action_id)
        source_stage_kind_id = str(counter.stage_kind_id)
    else:
        source_action_id = action_id
        source_stage_kind_id = str(action.stage_kind_id)
    state, run, _ = _claimed_execution_stage(
        plan,
        fingerprint,
        stage_kind_id=source_stage_kind_id,
        payload=recorded_payload,
        tag=tag,
    )
    if action_id.startswith("execution.escalate_"):
        state = _seed_counter_at_threshold(
            state,
            plan,
            action_id=source_action_id,
        )
    after, _decision, target_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=run.run_ref.run_id,
        action_id=source_action_id,
        tag=tag,
        artifact_payload=cast(
            Mapping[str, AuthorityValue],
            _artifact_payload_for_action(_execution_action(plan, source_action_id)),
        ),
    )
    _assert_recorded_reactivation(
        after,
        source_work_item_id=run.work_item_id,
        recorded_qa_context=cast(Mapping[str, object], recorded_payload["qa_context"]),
        target_activation_id=target_activation_id,
        action_id=action_id,
    )
    target_activation = after.activations[target_activation_id]
    assert target_activation.stage_kind_id == action.target_stage_kind_id
    assert target_activation.graph_node_id == action.target_graph_node_id
    assert target_activation.runner_binding_id == action.runner_binding_id
    return after


def _apply_selected_return_action(
    plan: SelectedCompiledPlan,
    fingerprint: str,
    *,
    action_id: str,
    recorded_payload: Mapping[str, AuthorityValue],
    tag: str,
) -> RuntimeState:
    if action_id == "execution.return_consultant_recovered":
        source_action_id = "execution.route_builder_blocked"
    else:
        source_action_id = "execution.recover_builder_runtime_failure"
    source_stage_kind_id = str(
        _execution_action(plan, source_action_id).stage_kind_id
    )
    state, source_run, source_activation = _claimed_execution_stage(
        plan,
        fingerprint,
        stage_kind_id=source_stage_kind_id,
        payload=recorded_payload,
        tag=tag,
    )
    if action_id == "execution.return_consultant_recovered":
        state = _seed_counter_at_threshold(
            state,
            plan,
            action_id=source_action_id,
        )
    recovered, _decision, recovered_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=source_run.run_ref.run_id,
        action_id=source_action_id,
        tag=f"{tag}-recover",
        artifact_payload=cast(
            Mapping[str, AuthorityValue],
            _artifact_payload_for_action(_execution_action(plan, source_action_id)),
        ),
    )
    return_run_id = f"run-{tag}-return"
    returned_stage = recovered.activations[recovered_activation_id]
    recovered = _apply_accepted_input(
        recovered,
        ClaimWork(
            f"claim-{tag}-return",
            activation_id=recovered_activation_id,
        ),
        deterministic_context(
            transition_id=f"transition-claim-{tag}-return",
            work_item_id=source_run.work_item_id,
            activation_id=recovered_activation_id,
            run_id=return_run_id,
            claim_id=f"claim-{tag}-return",
            fencing_token=f"fence-{tag}-return",
        ),
    )
    assert str(returned_stage.stage_kind_id) == str(
        recovered.runs[return_run_id].stage_kind_id
    )
    after, _decision, target_activation_id = _run_terminal_action(
        recovered,
        plan,
        fingerprint,
        run_id=return_run_id,
        action_id=action_id,
        tag=f"{tag}-return",
        artifact_payload=cast(
            Mapping[str, AuthorityValue],
            _artifact_payload_for_action(_execution_action(plan, action_id)),
        ),
    )
    _assert_recorded_reactivation(
        after,
        source_work_item_id=source_run.work_item_id,
        recorded_qa_context=cast(Mapping[str, object], recorded_payload["qa_context"]),
        target_activation_id=target_activation_id,
        action_id=action_id,
    )
    target_activation = after.activations[target_activation_id]
    assert target_activation.stage_kind_id == source_activation.stage_kind_id
    assert target_activation.graph_node_id == source_activation.graph_node_id
    assert target_activation.runner_binding_id == source_activation.runner_binding_id
    return after


def test_execution_lad_authority_and_assets_are_package_owned() -> None:
    manifest = _manifest()
    workflows = conformance.workflows_by_id(manifest)
    assets = conformance.assets_by_id(manifest)
    execution_assets = {
        asset_id for asset_id in assets if asset_id.startswith("execution.")
    }

    assert len(
        execution_assets
        - {
            asset_id
            for asset_id in execution_assets
            if asset_id.startswith(f"{_SEMANTIC_ASSET_PREFIX}.")
        }
    ) == 16
    for workflow_id in WORKFLOW_IDS:
        workflow = workflows[workflow_id]
        selected = cast(dict[str, object], workflow["selected_authority"])
        assert workflow["workflow_version"] == "0.1"
        assert workflow["visibility"] == "public"
        assert "assets" not in selected
        assert {
            str(required["asset_id"])
            for required in cast(list[dict[str, object]], workflow["required_assets"])
        } <= execution_assets


def test_existing_execution_lad_authority_and_asset_pins_are_byte_frozen() -> None:
    manifest = _manifest()
    workflow = conformance.workflows_by_id(manifest)["execution.lad"]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    canonical_bytes = canonical_authority_bytes(selected_authority)

    assert len(canonical_bytes) == _REFERENCE_LAD_CANONICAL_AUTHORITY_BYTE_LENGTH
    assert hashlib.sha256(canonical_bytes).hexdigest() == (
        _REFERENCE_LAD_CANONICAL_AUTHORITY_SHA256
    )
    assert conformance.selected_asset_pins(manifest, "execution.lad") == (
        tuple(sorted(_REFERENCE_LAD_SELECTED_ASSET_PINS))
    )


def test_public_semantic_workflow_selector_is_present() -> None:
    manifest = _manifest()
    selectors = {
        (str(workflow["workflow_id"]), str(workflow["workflow_version"]))
        for workflow in cast(list[Record], manifest["workflows"])
    }

    assert (SEMANTIC_WORKFLOW_ID, "0.2") in selectors


def test_semantic_codex_runner_components_and_mappings_are_exact() -> None:
    reference = _source()
    semantic = _codex_source(SEMANTIC_WORKFLOW_ID)
    reference_runners = {
        str(runner["stage_kind_ids"][0]): runner
        for runner in _runner_records(reference)
    }
    semantic_runners = _runner_records(semantic)
    assert {
        str(runner["id"]) for runner in semantic_runners
    } == {
        f"{stage}.codex_runner"
        for stage in (
            "lad_builder",
            "lad_checker",
            "lad_fixer",
            "lad_doublechecker",
            "lad_updater",
            "lad_troubleshooter",
            "lad_consultant",
        )
    }
    for runner in semantic_runners:
        stage = str(cast(list[object], runner["stage_kind_ids"])[0])
        reference_runner = reference_runners[stage]
        component = cast(Record, runner["component_pin"])
        reference_component = cast(Record, reference_runner["component_pin"])

        assert runner["adapter_kind"] == "codex"
        assert runner["required_capability_ids"] == (
            reference_runner["required_capability_ids"]
        )
        if stage != "lad_troubleshooter":
            assert runner["terminal_result_mappings"] == (
                reference_runner["terminal_result_mappings"]
            )
        assert component["component_kind"] == "runner"
        assert component["component_id"] == "millrace-codex-wrapper"
        assert component["component_version"] == "4"
        assert component["provider_distribution"] == "@openai/codex"
        assert component["provider_version"] == "0.147.0"
        assert component["descriptor_media_type"] == "application/json"
        assert "wrapper_protocol_version" not in runner
        assert "wrapper_protocol_version" not in component
        assert component["required_capability_ids"] == (
            reference_component["required_capability_ids"]
        )
        if stage == "lad_troubleshooter":
            assert component["legal_terminal_result_ids"] == [
                mapping["runner_result_id"]
                for mapping in cast(list[Record], runner["terminal_result_mappings"])
            ]
        else:
            assert component["legal_terminal_result_ids"] == (
                reference_component["legal_terminal_result_ids"]
            )
        assert component["descriptor_sha256"] == _codex_descriptor_digest(runner)


def test_semantic_terminal_actions_select_assets_for_their_target_stage() -> None:
    semantic = _codex_source(SEMANTIC_WORKFLOW_ID)
    stage_assets = {
        str(stage["id"]): tuple(cast(list[object], stage["asset_ids"]))
        for stage in _records(semantic, "stage_kinds")
    }
    actions = {
        str(action["id"]): action
        for action in _records(semantic, "terminal_actions")
    }

    for action_id, target_stage in _SEMANTIC_ACTION_TARGET_STAGES.items():
        assert tuple(cast(list[object], actions[action_id]["asset_ids"])) == (
            stage_assets[target_stage]
        )

    for action_id in (
        "execution.return_troubleshooter_complete",
        "execution.return_troubleshooter_baseline_invalidated",
        "execution.return_troubleshooter_review_retry",
    ):
        assert "dynamic_target_selector" not in actions[action_id]


def test_semantic_worktree_context_bindings_are_exact() -> None:
    semantic = _codex_source(SEMANTIC_WORKFLOW_ID)

    roots = (
        "millrace-agents/shared/conventions",
        "millrace-agents/shared/decisions",
        "millrace-agents/shared/references",
        "millrace-agents/shared/workspace-map/wiki",
        "docs",
    )
    common_required = [
        {
            "source_kind": "dispatch_material",
            "source_ref": "current",
            "max_files": 1,
            "max_bytes": 1048576,
        },
        {
            "source_kind": "workspace_relative_root",
            "source_ref": "millrace-agents/MILLRACE.md",
            "max_files": 1,
            "max_bytes": 65536,
        },
        {
            "source_kind": "workspace_relative_root",
            "source_ref": "millrace-agents/shared/CONTEXT.md",
            "max_files": 1,
            "max_bytes": 262144,
        },
    ]
    expected: dict[str, Record] = {}
    for stage in _CODEX_CONTEXT_BINDING_IDS:
        required = deepcopy(common_required)
        if stage != "lad_builder":
            required.append(
                {
                    "source_kind": "accepted_lineage_artifacts",
                    "source_ref": "current_lineage",
                    "max_files": 64,
                    "max_bytes": 1048576,
                }
            )
        if stage in {"lad_fixer", "lad_doublechecker", "lad_updater"}:
            required.append(
                {
                    "source_kind": "lineage_attempt_history",
                    "source_ref": "current_lineage",
                    "max_files": 64,
                    "max_bytes": 1048576,
                }
            )
        if stage == "lad_updater":
            required.extend(
                [
                    {
                        "source_kind": "workspace_relative_root",
                        "source_ref": root,
                        "max_files": 256,
                        "max_bytes": 4194304,
                    }
                    for root in roots
                ]
            )
            required.extend(
                [
                    {
                        "source_kind": "workspace_relative_root",
                        "source_ref": "README.md",
                        "max_files": 1,
                        "max_bytes": 262144,
                    },
                    {
                        "source_kind": "workspace_relative_root",
                        "source_ref": "millrace-agents/shared/skills",
                        "max_files": 256,
                        "max_bytes": 4194304,
                    },
                ]
            )
            discoverable = []
            write_rules = [
                {"relative_root": root, "disposition": "direct_write"}
                for root in (
                    "README.md",
                    "docs",
                    "millrace-agents/shared/conventions",
                    "millrace-agents/shared/decisions",
                    "millrace-agents/shared/references",
                    "millrace-agents/shared/workspace-map/wiki",
                    "millrace-agents/shared/CONTEXT.md",
                )
            ] + [
                {
                    "relative_root": root,
                    "disposition": "protected_proposal",
                }
                for root in (
                    "millrace-agents/MILLRACE.md",
                    "millrace-agents/shared/skills",
                )
            ]
        else:
            discoverable = [
                {
                    "source_kind": "workspace_relative_root",
                    "source_ref": root,
                    "max_files": 256,
                    "max_bytes": 4194304,
                }
                for root in roots
            ]
            write_rules = None
        binding = {
            "id": _CODEX_CONTEXT_BINDING_IDS[stage],
            "stage_kind_id": stage,
            "router_asset_id": _CODEX_ROUTER_ASSET_ID,
            "checkout_root": "millrace-agents/checkouts",
            "required_sources": required,
            "discoverable_sources": discoverable,
        }
        if write_rules is not None:
            binding["write_rules"] = write_rules
            binding["writeback_terminal_action_id"] = (
                "execution.close_updater_complete"
            )
            binding["writeback_artifact_schema_id"] = _CODEX_CONTEXT_SCHEMA_ID
        expected[stage] = binding

    actual = {
        str(binding["stage_kind_id"]): binding
        for binding in _source_context_bindings(semantic)
    }
    assert set(actual) == set(expected)
    assert set(actual) == set(_CODEX_CONTEXT_BINDING_IDS)
    for stage, binding in actual.items():
        if stage != "lad_updater":
            assert "writeback_terminal_action_id" not in binding
            assert "writeback_artifact_schema_id" not in binding


def test_context_bindings_are_absent_from_reference_and_unrelated_workflows() -> None:
    manifest = _manifest()
    workflows = conformance.workflows_by_id(manifest)
    workflow_ids = (
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
        "lad.full",
        "vendor_selection",
    )
    for workflow_id in workflow_ids:
        selected = cast(
            dict[str, object], workflows[workflow_id]["selected_authority"]
        )
        assert selected.get("context_bindings", []) == []


def test_semantic_assets_and_context_update_schema_are_content_contracts() -> None:
    manifest = _manifest()
    assets = conformance.assets_by_id(manifest)
    expected_paths = {
        _CODEX_ROUTER_ASSET_ID: (
            "assets/workflows/execution.lad_codex_semantic_worktree/context/router.md"
        ),
        **{
            asset_id: (
                "assets/workflows/execution.lad_codex_semantic_worktree/"
                f"entrypoints/{stage}.md"
            )
            for stage, asset_id in _CODEX_ENTRYPOINT_ASSET_IDS.items()
        },
        _CODEX_UPDATER_SKILL_ASSET_ID: (
            "assets/workflows/execution.lad_codex_semantic_worktree/skills/updater-core.md"
        ),
    }
    assert set(expected_paths) <= set(assets)
    semantic = conformance.workflows_by_id(manifest)[SEMANTIC_WORKFLOW_ID]
    required_ids = {
        str(asset["asset_id"])
        for asset in cast(list[Record], semantic["required_assets"])
    }
    assert set(expected_paths) <= required_ids
    semantic_source = _codex_source(SEMANTIC_WORKFLOW_ID)
    assert _CODEX_ROUTER_ASSET_ID not in {
        str(asset_id)
        for stage in _records(semantic_source, "stage_kinds")
        for asset_id in cast(list[object], stage["asset_ids"])
    }
    for asset_id, package_path in expected_paths.items():
        asset = assets[asset_id]
        assert asset["package_path"] == package_path
        assert asset["encoding"] == "utf-8"
        assert asset["selection"] == "required"
    router_asset = assets[_CODEX_ROUTER_ASSET_ID]
    router_bytes = (
        PACKAGE_ROOT / str(router_asset["package_path"])
    ).read_bytes()
    assert router_asset["asset_kind"] == "template"
    assert router_asset["byte_length"] == 1617
    assert router_asset["byte_length"] == len(router_bytes)
    assert router_asset["content_digest"] == (
        "sha256:00053a2572581acd9441ebc8c1199accee49073afe87190a60bf847fec25d129"
    )
    assert router_asset["content_digest"] == conformance.asset_digest(router_bytes)

    texts = conformance.asset_texts(PACKAGE_ROOT, manifest, set(expected_paths))
    required_headings = (
        "Role:",
        "Scope:",
        "Inputs from dispatch:",
        "Readable assets:",
        "Writable artifacts:",
        "Required evidence:",
        "Legal terminal markers rendered by runtime:",
        "Forbidden claims:",
        "How to return evidence:",
        "When to stop:",
    )
    entrypoint_texts = {
        asset_id: text
        for asset_id, text in texts.items()
        if ".entrypoints." in asset_id
    }
    assert len(entrypoint_texts) == 5
    for text in entrypoint_texts.values():
        lower_text = text.lower()
        for heading in required_headings:
            assert sum(
                line.startswith(heading) for line in text.splitlines()
            ) == 1
        for phrase in (
            "immutable checkout evidence",
            "runtime authority",
            "read all required material first",
            "live project root",
            "selected runner protocol",
            ".millrace/",
            "generated projections",
            "checkouts",
            "queues",
            "work items",
            "accepted artifacts",
            "executable skills",
            "protected policy",
        ):
            assert phrase.lower() in lower_text
    assert "assigned project source" in texts[
        _CODEX_ENTRYPOINT_ASSET_IDS["lad_builder"]
    ]
    assert "assigned project source" in texts[_CODEX_ENTRYPOINT_ASSET_IDS["lad_fixer"]]
    for stage in ("lad_checker", "lad_doublechecker"):
        assert "review-only" in texts[_CODEX_ENTRYPOINT_ASSET_IDS[stage]]
    updater = texts[_CODEX_ENTRYPOINT_ASSET_IDS["lad_updater"]]
    for path in (
        "README.md",
        "docs",
        "millrace-agents/shared/conventions",
        "millrace-agents/shared/decisions",
        "millrace-agents/shared/references",
        "millrace-agents/shared/workspace-map/wiki",
        "millrace-agents/shared/CONTEXT.md",
        "millrace-agents/MILLRACE.md",
        "millrace-agents/shared/skills",
    ):
        assert path in updater
    router = texts[_CODEX_ROUTER_ASSET_ID]
    assert "required material" in router
    assert "immutable checkout evidence" in router
    assert "live project root" in router
    assert "selected runner protocol" in router
    conformance.assert_no_runtime_authority_claims(texts)

    updater_core = texts[_CODEX_UPDATER_SKILL_ASSET_ID]
    assert "## Artifact Schema" in updater_core
    assert "## Valid Example" in updater_core
    assert "### Invalid: extra field" in updater_core
    assert "### Invalid: missing required field" in updater_core
    assert "### Invalid: wrong type" in updater_core
    assert "## Completion Criteria" in updater_core
    json_examples = re.findall(
        r"```json\n(.*?)\n```", updater_core, flags=re.DOTALL
    )
    assert len(json_examples) >= 4
    assert all(isinstance(json.loads(example), dict) for example in json_examples)

    schema = _record(
        semantic_source, "artifact_schemas", _CODEX_CONTEXT_SCHEMA_ID
    )
    assert schema["schema"] == _context_update_report_schema()
    valid = {"changes": [], "proposals": []}
    assert validate_schema(schema["schema"], valid).accepted
    invalid_extra = {
        "changes": [
            {
                "path": "docs/example.md",
                "change_kind": "modify",
                "evidence_refs": [],
                "classification": "direct_write",
                "extra": True,
            }
        ],
        "proposals": [],
    }
    assert not validate_schema(schema["schema"], invalid_extra).accepted
    assert not validate_schema(schema["schema"], {"proposals": []}).accepted
    assert not validate_schema(
        schema["schema"], {"changes": {}, "proposals": []}
    ).accepted


def test_semantic_updater_examples_are_schema_and_digest_exact() -> None:
    manifest = _manifest()
    semantic_source = _codex_source(SEMANTIC_WORKFLOW_ID)
    updater_core = conformance.asset_texts(
        PACKAGE_ROOT,
        manifest,
        {_CODEX_UPDATER_SKILL_ASSET_ID},
    )[_CODEX_UPDATER_SKILL_ASSET_ID]
    schema = _record(
        semantic_source, "artifact_schemas", _CODEX_CONTEXT_SCHEMA_ID
    )["schema"]

    valid = _json_example(updater_core, "## Valid Example")
    assert cast(list[object], valid["changes"])
    assert cast(list[object], valid["proposals"])
    assert "no_op_reason" not in valid

    digest_pattern = re.compile(r"^sha256:[0-9a-f]{64}$")
    for change in cast(list[Record], valid["changes"]):
        for field in ("before_sha256", "after_sha256"):
            if field in change:
                assert digest_pattern.fullmatch(str(change[field]))
    for proposal in cast(list[Record], valid["proposals"]):
        digest = str(proposal["proposed_content_sha256"])
        content = str(proposal["proposed_content"])
        assert digest_pattern.fullmatch(digest)
        assert digest == "sha256:" + hashlib.sha256(
            content.encode("utf-8")
        ).hexdigest()
    assert validate_schema(schema, valid).accepted

    no_op = _json_example(updater_core, "## Valid No-op Example")
    assert no_op["changes"] == []
    assert no_op["proposals"] == []
    assert isinstance(no_op.get("no_op_reason"), str)
    assert str(no_op["no_op_reason"]).strip()
    assert validate_schema(schema, no_op).accepted

    for heading in (
        "### Invalid: extra field",
        "### Invalid: missing required field",
        "### Invalid: wrong type",
    ):
        invalid = _json_example(updater_core, heading)
        assert not validate_schema(schema, invalid).accepted


def test_semantic_workflow_compiles_declared_selected_authority() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, SEMANTIC_WORKFLOW_ID)

    assert str(plan.workflow.workflow_id) == SEMANTIC_WORKFLOW_ID
    bindings = {
        str(binding.stage_kind_id): binding for binding in plan.context_bindings
    }
    assert set(bindings) == set(_CODEX_CONTEXT_BINDING_IDS)
    updater = bindings["lad_updater"]
    assert str(updater.router_asset_id) == _CODEX_ROUTER_ASSET_ID
    assert str(updater.writeback_terminal_action_id) == (
        "execution.close_updater_complete"
    )
    assert str(updater.writeback_artifact_schema_id) == _CODEX_CONTEXT_SCHEMA_ID


def test_semantic_workflow_selects_through_installed_public_api(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    plan = conformance.select_and_verify_package(
        tmp_path / SEMANTIC_WORKFLOW_ID,
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=SEMANTIC_WORKFLOW_ID,
        workflow_version="0.2",
    )

    conformance.assert_selected_package_pin(
        plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=SEMANTIC_WORKFLOW_ID,
        workflow_version="0.2",
        selected_asset_pins=conformance.selected_asset_pins(
            manifest, SEMANTIC_WORKFLOW_ID
        ),
    )


@pytest.mark.parametrize("workflow_id", WORKFLOW_IDS)
def test_execution_lad_selects_through_installed_public_api(
    tmp_path: Path, workflow_id: str
) -> None:
    manifest = _manifest()
    plan = conformance.select_and_verify_package(
        tmp_path / workflow_id,
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=workflow_id,
        workflow_version="0.1",
    )
    conformance.assert_selected_package_pin(
        plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=workflow_id,
        workflow_version="0.1",
        selected_asset_pins=conformance.selected_asset_pins(manifest, workflow_id),
    )


def test_execution_lad_assets_keep_authoring_boundaries() -> None:
    manifest = _manifest()
    asset_ids = {
        asset_id
        for asset_id in conformance.assets_by_id(manifest)
        if asset_id.startswith("execution.")
    }
    texts = conformance.asset_texts(PACKAGE_ROOT, manifest, asset_ids)
    for asset_id, text in texts.items():
        if ".entrypoints." in asset_id:
            headings = (
                "Role:",
                "Scope:",
                "Legal terminal markers rendered by runtime:",
            )
        elif asset_id == _CODEX_ROUTER_ASSET_ID:
            headings = (
                "# Context Router",
                "required material",
                "live project root",
            )
        else:
            headings = (
                "## Artifact Schema",
                "## Valid Example",
                "## Completion Criteria",
            )
        assert all(heading in text for heading in headings)
    conformance.assert_no_runtime_authority_claims(texts)


def test_execution_lad_compiles_packaged_selected_authority() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_IDS[0])

    assert str(plan.workflow.workflow_id) == WORKFLOW_IDS[0]
    assert plan.workflow.workflow_name == "LAD Execution"
    assert plan.lineage_policy == "root_from_external_enqueue"
    assert tuple(str(graph.id) for graph in plan.graphs) == ("execution.lad.graph",)
    assert plan.graphs[0].node_ids == (
        "execution.lad.builder.start",
        "execution.lad.checker.start",
        "execution.lad.fixer.start",
        "execution.lad.doublechecker.start",
        "execution.lad.updater.start",
        "execution.lad.troubleshooter.start",
        "execution.lad.consultant.start",
    )
    route = plan.external_enqueue_routes[0]
    assert (
        route.id,
        str(route.queue_family_id),
        route.graph_node_id,
        str(route.stage_kind_id),
        str(route.runner_binding_id),
        str(route.payload_schema_id),
    ) == (
        "execution.lad.task",
        "task",
        "execution.lad.builder.start",
        "lad_builder",
        "lad_builder.millforge_runner",
        "execution.artifacts.task",
    )
    assert {str(stage.id) for stage in plan.stage_kinds} == {
        "lad_builder",
        "lad_checker",
        "lad_fixer",
        "lad_doublechecker",
        "lad_updater",
        "lad_troubleshooter",
        "lad_consultant",
    }
    assert {
        str(stage.id): tuple(str(asset_id) for asset_id in stage.asset_ids)
        for stage in plan.stage_kinds
    } == {
        stage_id: (
            f"execution.entrypoints.{stage_id}",
            f"execution.skills.{stage_id.removeprefix('lad_')}_core",
        )
        for stage_id in (
            "lad_builder",
            "lad_checker",
            "lad_fixer",
            "lad_doublechecker",
            "lad_updater",
            "lad_troubleshooter",
            "lad_consultant",
        )
    }
    assert all(
        str(stage.runner_binding_id) == f"{stage.id}.millforge_runner"
        for stage in plan.stage_kinds
    )
    assert {str(capability.id) for capability in plan.capabilities} == {
        "capability.runner.invoke",
        "terminal.intent",
        "unrestricted.filesystem.read",
        "unrestricted.filesystem.write",
        "unrestricted.process.execute",
    }
    assert "lad_integrator" not in {str(stage.id) for stage in plan.stage_kinds}


def test_execution_lad_terminal_paths_are_exact_selected_actions() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_IDS[0])
    actions = {str(action.id): action for action in plan.terminal_actions}
    expected = {
        "execution.route_builder_complete": (
            "route",
            "lad_checker",
            "execution.lad.checker.start",
            "stage_result",
            "execution.artifacts.stage_result",
        ),
        "execution.route_checker_pass": (
            "route",
            "lad_updater",
            "execution.lad.updater.start",
            "stage_result",
            "execution.artifacts.checker_result",
        ),
        "execution.route_checker_fix_needed": (
            "route",
            "lad_fixer",
            "execution.lad.fixer.start",
            "stage_result",
            "execution.artifacts.checker_result",
        ),
        "execution.route_fixer_complete": (
            "route",
            "lad_doublechecker",
            "execution.lad.doublechecker.start",
            "stage_result",
            "execution.artifacts.stage_result",
        ),
        "execution.route_doublechecker_pass": (
            "route",
            "lad_updater",
            "execution.lad.updater.start",
            "stage_result",
            "execution.artifacts.doublecheck_result",
        ),
        "execution.route_doublechecker_fix_needed": (
            "route",
            "lad_fixer",
            "execution.lad.fixer.start",
            "stage_result",
            "execution.artifacts.doublecheck_result",
        ),
        "execution.close_updater_complete": (
            "complete_work_item",
            None,
            None,
            None,
            "execution.artifacts.report",
        ),
        "execution.close_consultant_needs_plan": (
            "close_with_escalation",
            None,
            None,
            None,
            "execution.artifacts.incident_report",
        ),
        "execution.close_consultant_blocked": (
            "block_work_item",
            None,
            None,
            None,
            "execution.artifacts.report",
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
    for stage in (
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "updater",
        "troubleshooter",
    ):
        assert actions[f"execution.route_{stage}_blocked"].action_kind == "route"
        assert (
            actions[f"execution.recover_{stage}_runtime_failure"].action_kind
            == "recovery_route"
        )
        assert (
            actions[f"execution.close_{stage}_runtime_failure_exhausted"].action_kind
            == "block_work_item"
        )


def test_execution_lad_recovery_wait_and_intervention_authority_is_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_IDS[0])
    actions = {str(action.id): action for action in plan.terminal_actions}
    policies = {str(policy.id): policy for policy in plan.recovery_policies}
    counters = {str(counter.id): counter for counter in plan.counters}
    interventions = {str(option.id): option for option in plan.intervention_options}

    fix_policy = policies["execution.fix_needed_recovery"]
    assert tuple(str(item) for item in fix_policy.source_recovery_action_ids) == (
        "execution.route_checker_fix_needed",
        "execution.route_doublechecker_fix_needed",
    )
    assert (
        str(fix_policy.recovery_stage_kind_id),
        fix_policy.immediate_recovery_limit,
        fix_policy.cooldown_starts_at_attempt,
        fix_policy.quarantine_threshold_attempt,
    ) == ("lad_troubleshooter", 1, 2, 3)
    blocked_policy = policies["execution.blocked_recovery"]
    assert tuple(str(item) for item in blocked_policy.source_recovery_action_ids) == (
        "execution.route_builder_blocked",
        "execution.route_checker_blocked",
        "execution.route_fixer_blocked",
        "execution.route_doublechecker_blocked",
        "execution.route_updater_blocked",
        "execution.route_troubleshooter_blocked",
    )
    assert (
        str(blocked_policy.recovery_stage_kind_id),
        blocked_policy.cooldown_starts_at_attempt,
        blocked_policy.quarantine_threshold_attempt,
    ) == ("lad_consultant", 2, 3)
    runtime_policy = policies["execution.runtime_failure_recovery"]
    assert str(runtime_policy.recovery_stage_kind_id) == "lad_troubleshooter"
    assert tuple(
        str(item) for item in runtime_policy.source_recovery_action_ids
    ) == tuple(
        f"execution.recover_{stage}_runtime_failure"
        for stage in (
            "builder",
            "checker",
            "fixer",
            "doublechecker",
            "updater",
            "troubleshooter",
        )
    )
    assert {str(wait.id) for wait in plan.wait_states} == {
        "execution.fix_needed_recovery.cooldown",
        "execution.blocked_recovery.cooldown",
        "execution.runtime_failure_recovery.cooldown",
    }
    assert counters["execution.fix_cycle_count.checker"].threshold_count == 2
    assert counters["execution.fix_cycle_count.doublechecker"].threshold_count == 2
    assert counters["execution.troubleshoot_attempt_count.builder"].threshold_count == 2
    assert counters["execution.runtime_failure_count.builder"].threshold_count == 2
    assert {
        option_id: option.option_kind for option_id, option in interventions.items()
    } == {
        "execution.blocked.resume_lineage": "resume_lineage",
        "execution.blocked.close_lineage": "close_lineage",
        "execution.blocked.revise_lineage": "revise_lineage",
        "execution.fix_needed.resume_lineage": "resume_lineage",
        "execution.fix_needed.close_lineage": "close_lineage",
        "execution.fix_needed.revise_lineage": "revise_lineage",
    }
    revise = interventions["execution.blocked.revise_lineage"]
    assert (
        str(revise.payload_schema_id),
        str(revise.target_queue_family_id),
        str(revise.target_stage_kind_id),
    ) == ("execution.artifacts.task", "task", "lad_builder")

    troubleshooter = actions["execution.return_troubleshooter_complete"]
    assert isinstance(troubleshooter.dynamic_target_selector, Mapping)
    assert troubleshooter.dynamic_target_selector["field_names"] == ("resume_stage",)
    assert set(troubleshooter.dynamic_target_selector["targets"]) == {
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "updater",
    }
    consultant = actions["execution.route_consultant_complete"]
    assert isinstance(consultant.dynamic_target_selector, Mapping)
    assert consultant.dynamic_target_selector["field_names"] == (
        "target_stage",
        "resume_stage",
    )
    assert "consultant" not in consultant.dynamic_target_selector["targets"]


def test_execution_lad_integrator_selects_only_integrator_delta() -> None:
    base = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_IDS[0])
    integrator = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_IDS[1])
    actions = {str(action.id): action for action in integrator.terminal_actions}
    stages = {str(stage.id): stage for stage in integrator.stage_kinds}
    policy = next(
        item
        for item in integrator.recovery_policies
        if str(item.id) == "execution.blocked_recovery"
    )

    assert integrator.graphs[0].node_ids[0:2] == (
        "execution.lad_integrator.builder.start",
        "execution.lad_integrator.integrator.start",
    )
    assert integrator.external_enqueue_routes[0].graph_node_id == (
        "execution.lad_integrator.builder.start"
    )
    assert set(stages) - {str(stage.id) for stage in base.stage_kinds} == {
        "lad_integrator"
    }
    assert str(actions["execution.route_builder_complete"].artifact_schema_id) == (
        "execution.artifacts.builder_summary"
    )
    assert str(actions["execution.route_integrator_complete"].artifact_schema_id) == (
        "execution.artifacts.integration_report"
    )
    assert "execution.route_integrator_blocked" in {
        str(item) for item in policy.source_recovery_action_ids
    }
    for action_id in (
        "execution.return_troubleshooter_complete",
        "execution.route_consultant_complete",
    ):
        selector = actions[action_id].dynamic_target_selector
        assert isinstance(selector, Mapping)
        assert "integrator" in selector["targets"]


def test_execution_lad_selected_authority_excludes_other_planes() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_IDS[0])

    stage_ids = {str(stage.id) for stage in plan.stage_kinds}
    assert not any(
        stage_id.startswith(("planning.", "learning.", "recon."))
        for stage_id in stage_ids
    )
    assert "FIX_NEEDED_ESCALATE" not in {
        outcome.marker for outcome in plan.terminal_outcomes
    }
    assert "BLOCKED_ESCALATE" not in {
        outcome.marker for outcome in plan.terminal_outcomes
    }


def test_execution_lad_rejects_old_plane_directed_terminal_action() -> None:
    source = _source()
    action = _record(
        source, "terminal_actions", "execution.close_consultant_needs_plan"
    )
    action["kind"] = "escalate_to_planning"

    error = _error(compile_workflow(source), "unsupported_terminal_action_kind")

    assert error.context["action_id"] == "execution.close_consultant_needs_plan"
    assert error.context["action_kind"] == "escalate_to_planning"


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("target_stage_kind_id", "lad_consultant"),
        ("target_graph_node_id", "execution.lad.consultant.start"),
        ("emitted_queue_family_id", "stage_result"),
        ("runner_binding_id", "lad_consultant.millforge_runner"),
        ("payload_projection", {"kind": "source", "path": ("artifact_payload",)}),
        (
            "dynamic_target_selector",
            {
                "kind": "observation_payload_route_target",
                "field_names": ("target_stage",),
                "targets": {},
            },
        ),
    ),
)
def test_execution_lad_close_with_escalation_rejects_route_authority(
    field_name: str, field_value: object
) -> None:
    source = _source()
    action = _record(
        source, "terminal_actions", "execution.close_consultant_needs_plan"
    )
    action[field_name] = field_value

    error = _error(
        compile_workflow(source), "terminal_close_with_escalation_route_authority"
    )

    assert error.context["action_id"] == "execution.close_consultant_needs_plan"
    assert error.context["field_name"] == field_name


@pytest.mark.parametrize(
    ("mutation", "diagnostic_code", "context_key", "context_value"),
    (
        (
            "threshold_as_source",
            "recovery_policy_threshold_action_source",
            "referenced_id",
            "execution.escalate_checker_fix_exhausted",
        ),
        (
            "missing_counter_source",
            "counter_recovery_policy_source_missing",
            "counter_id",
            "execution.fix_cycle_count.checker",
        ),
        (
            "duplicate_increment",
            "duplicate_counter_action",
            "existing_counter_id",
            "execution.fix_cycle_count.checker",
        ),
        (
            "duplicate_threshold",
            "duplicate_counter_action",
            "action_id",
            "execution.escalate_checker_fix_exhausted",
        ),
    ),
)
def test_execution_lad_refuses_invalid_recovery_counter_ownership(
    mutation: str,
    diagnostic_code: str,
    context_key: str,
    context_value: str,
) -> None:
    source = _source()
    if mutation in {"threshold_as_source", "missing_counter_source"}:
        policy = _record(source, "recovery_policies", "execution.fix_needed_recovery")
        policy["source_recovery_action_ids"] = (
            "execution.escalate_checker_fix_exhausted"
            if mutation == "threshold_as_source"
            else "execution.route_doublechecker_fix_needed",
        )
    else:
        counters = _records(source, "counters")
        duplicate = dict(counters[0])
        duplicate["id"] = f"execution.{mutation}"
        if mutation == "duplicate_threshold":
            duplicate["increment_action_id"] = "execution.route_checker_pass"
        source["counters"] = (*counters, duplicate)

    result = compile_workflow(source)
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error" and diagnostic.code == diagnostic_code
    )
    error = next(
        diagnostic
        for diagnostic in errors
        if mutation != "duplicate_threshold"
        or diagnostic.declaration_path.endswith(".threshold_action_id")
    )
    assert result.plan is None
    assert error.context[context_key] == context_value


@pytest.mark.parametrize(
    ("mutation", "diagnostic_code", "context_key", "context_value"),
    (
        (
            "artifact_schema",
            "terminal_action_artifact_schema_mismatch",
            "artifact_schema_id",
            "execution.artifacts.incident_report",
        ),
        (
            "compatibility_profile",
            "unsupported_compatibility_profile",
            "compatibility_profile",
            "lad_codex",
        ),
        (
            "missing_capability",
            "missing_reference",
            "referenced_id",
            "missing.capability",
        ),
        (
            "missing_runner_invoke",
            "runner_binding_missing_runner_invoke",
            "required_capability_kind",
            "runner.invoke",
        ),
        (
            "external_graph",
            "missing_reference",
            "referenced_id",
            "execution.lad.missing.start",
        ),
        (
            "terminal_graph",
            "missing_reference",
            "referenced_id",
            "execution.lad.missing.start",
        ),
        (
            "recovery_stage_mismatch",
            "terminal_recovery_route_graph_node_stage_mismatch",
            "graph_node_stage_kind_id",
            "lad_consultant",
        ),
        (
            "empty_dynamic_fields",
            "invalid_dynamic_route_selector",
            "action_id",
            "execution.return_troubleshooter_complete",
        ),
        (
            "dynamic_contract",
            "terminal_dynamic_route_target_mismatch",
            "target_name",
            "builder",
        ),
        (
            "disallowed_dynamic_target",
            "invalid_dynamic_route_selector",
            "reason",
            "disallowed_target",
        ),
        (
            "missing_dynamic_graph",
            "missing_reference",
            "referenced_id",
            "execution.lad.missing.start",
        ),
    ),
)
def test_execution_lad_refuses_workflow_specific_authority_mutations(
    mutation: str,
    diagnostic_code: str,
    context_key: str,
    context_value: str,
) -> None:
    source = _source()
    if mutation == "artifact_schema":
        _record(source, "terminal_actions", "execution.close_updater_complete")[
            "artifact_schema_id"
        ] = "execution.artifacts.incident_report"
    elif mutation == "compatibility_profile":
        cast(Record, source["workflow"])["compatibility_profile"] = "lad_codex"
    elif mutation in {"missing_capability", "missing_runner_invoke"}:
        _records(source, "runner_bindings")[0]["required_capability_ids"] = (
            ("missing.capability",) if mutation == "missing_capability" else ()
        )
    elif mutation == "external_graph":
        _records(source, "external_enqueue_routes")[0]["graph_node_id"] = (
            "execution.lad.missing.start"
        )
    elif mutation == "terminal_graph":
        _records(source, "terminal_actions")[0]["target_graph_node_id"] = (
            "execution.lad.missing.start"
        )
    elif mutation == "recovery_stage_mismatch":
        _record(
            source,
            "terminal_actions",
            "execution.escalate_checker_fix_exhausted",
        )["target_graph_node_id"] = "execution.lad.consultant.start"
    else:
        action_id = (
            "execution.route_consultant_complete"
            if mutation == "disallowed_dynamic_target"
            else "execution.return_troubleshooter_complete"
        )
        selector = cast(
            Record,
            _record(source, "terminal_actions", action_id)["dynamic_target_selector"],
        )
        if mutation == "empty_dynamic_fields":
            selector["field_names"] = ()
        else:
            targets = cast(dict[str, Record], selector["targets"])
            if mutation == "dynamic_contract":
                targets["builder"]["emitted_queue_family_id"] = "task"
            elif mutation == "missing_dynamic_graph":
                targets["builder"]["target_graph_node_id"] = (
                    "execution.lad.missing.start"
                )
            else:
                targets["consultant"] = {
                    "target_stage_kind_id": "lad_consultant",
                    "target_graph_node_id": "execution.lad.consultant.start",
                    "emitted_queue_family_id": "stage_result",
                    "runner_binding_id": "lad_consultant.millforge_runner",
                }

    result = compile_workflow(source)
    error = _error(result, diagnostic_code)
    assert result.plan is None
    assert error.context[context_key] == context_value


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("kind", "runner.self_grant"),
        ("support_status", "maybe"),
        ("grant_status", "maybe"),
        ("approval_policy_id", "missing.policy"),
    ),
)
def test_execution_lad_refuses_unsupported_capability_values(
    field: str, value: str
) -> None:
    source = _source()
    _records(source, "capabilities")[0][field] = value

    error = _error(compile_workflow(source), "unsupported_capability_value")

    assert error.context["capability_id"] == "capability.runner.invoke"
    assert error.context["field_name"] == field


def test_execution_lad_graph_nodes_are_declaration_authority() -> None:
    source = _source()
    graph = _records(source, "graphs")[0]
    graph["node_ids"] = tuple(
        node
        for node in cast(list[str], graph["node_ids"])
        if node != "execution.lad.builder.start"
    )

    result = compile_workflow(source)
    errors = tuple(
        error
        for error in result.diagnostics
        if error.severity == "error"
        and error.context.get("referenced_id") == "execution.lad.builder.start"
    )

    assert result.plan is None
    assert errors
    assert all(error.code == "missing_reference" for error in errors)


def test_execution_lad_capability_policy_changes_fingerprint() -> None:
    granted = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_IDS[0])
    denied_source = _source()
    _records(denied_source, "capabilities")[0]["grant_status"] = "denied"
    denied_result = compile_workflow(denied_source)

    assert denied_result.plan is not None
    assert authority_fingerprint(granted) != authority_fingerprint(denied_result.plan)


# Rows are (Millrace source owner, collected rows, Plus replacements, generic N/A).
_EXECUTION_SOURCE_OWNER_LEDGER = (
    ("compiler/test_lad_execution_compile.py", 35, 35, 0),
    ("kernel/test_lad_execution_intake_dispatch.py", 5, 5, 0),
    ("kernel/test_lad_execution_dispatch.py", 17, 12, 5),
    ("kernel/test_lad_execution_recovery.py", 22, 17, 5),
    ("kernel/test_lad_execution_terminal_actions.py", 14, 9, 5),
)

_EXECUTION_GENERIC_N_A = (
    ("persisted activation authority corruption", 5),
    ("status/evidence/restart runtime mechanics", 5),
    ("payload/evidence/replay transition refusals", 5),
)


def test_execution_source_owner_row_disposition_ledger_is_complete() -> None:
    assert len(_EXECUTION_SOURCE_OWNER_LEDGER) == 5
    assert sum(row[1] for row in _EXECUTION_SOURCE_OWNER_LEDGER) == 93
    assert sum(row[2] for row in _EXECUTION_SOURCE_OWNER_LEDGER) == 78
    assert sum(row[3] for row in _EXECUTION_SOURCE_OWNER_LEDGER) == 15
    assert all(
        total == replacement + n_a
        for _, total, replacement, n_a in (_EXECUTION_SOURCE_OWNER_LEDGER)
    )
    assert sum(rows for _cluster, rows in _EXECUTION_GENERIC_N_A) == 15


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
def test_qa_result_schemas_are_exact_and_reject_nested_corruption(
    workflow_id: str,
) -> None:
    source = _qa_source(workflow_id)
    schemas = {
        str(schema["id"]): cast(Record, schema["schema"])
        for schema in _records(source, "artifact_schemas")
    }

    assert schemas["execution.artifacts.checker_result"] == _checker_schema()
    assert (
        schemas["execution.artifacts.doublecheck_result"]
        == _doublechecker_schema()
    )

    checker = _checker_payload()
    checker_schema = schemas["execution.artifacts.checker_result"]
    assert validate_schema(checker_schema, checker).accepted

    checker_with_unknown = deepcopy(checker)
    cast(Record, cast(list[object], checker_with_unknown["criteria"])[0])["extra"] = (
        "refused"
    )
    assert not validate_schema(checker_schema, checker_with_unknown).accepted

    checker_with_duplicate = deepcopy(checker)
    cast(list[object], checker_with_duplicate["criteria"]).append(
        deepcopy(cast(list[object], checker_with_duplicate["criteria"])[0])
    )
    assert not validate_schema(checker_schema, checker_with_duplicate).accepted

    doublechecker = _doublechecker_payload()
    doublechecker_schema = schemas["execution.artifacts.doublecheck_result"]
    assert validate_schema(doublechecker_schema, doublechecker).accepted

    doublechecker_with_unknown = deepcopy(doublechecker)
    cast(
        Record,
        cast(list[object], doublechecker_with_unknown["finding_statuses"])[0],
    )["extra"] = "refused"
    assert not validate_schema(
        doublechecker_schema,
        doublechecker_with_unknown,
    ).accepted

    doublechecker_with_duplicate = deepcopy(doublechecker)
    cast(list[object], doublechecker_with_duplicate["finding_statuses"]).append(
        deepcopy(
            cast(list[object], doublechecker_with_duplicate["finding_statuses"])[0]
        )
    )
    assert not validate_schema(
        doublechecker_schema,
        doublechecker_with_duplicate,
    ).accepted


def _builder_qa_context_projection() -> dict[str, object]:
    return {
        "kind": "object",
        "fields": {
            "task_contract": {
                "kind": "coalesce",
                "candidates": [
                    {
                        "kind": "source",
                        "path": [
                            "work_item_payload",
                            "qa_context",
                            "task_contract",
                        ],
                    }
                ],
                "default": {
                    "kind": "source",
                    "path": ["work_item_payload"],
                },
            },
            "task_contract_digest": {
                "kind": "coalesce",
                "candidates": [
                    {
                        "kind": "source",
                        "path": [
                            "work_item_payload",
                            "qa_context",
                            "task_contract_digest",
                        ],
                    }
                ],
                "default": {
                    "kind": "source",
                    "path": ["run_metadata", "work_item_payload_digest"],
                },
            },
            "checker_baseline": {
                "kind": "coalesce",
                "candidates": [
                    {
                        "kind": "source",
                        "path": [
                            "work_item_payload",
                            "qa_context",
                            "checker_baseline",
                        ],
                    }
                ],
                "default": {"kind": "literal", "value": None},
            },
            "checker_baseline_digest": {
                "kind": "coalesce",
                "candidates": [
                    {
                        "kind": "source",
                        "path": [
                            "work_item_payload",
                            "qa_context",
                            "checker_baseline_digest",
                        ],
                    }
                ],
                "default": {"kind": "literal", "value": None},
            },
        },
    }


def _checker_qa_context_projection() -> dict[str, object]:
    return {
        "kind": "object",
        "fields": {
            "task_contract": {
                "kind": "source",
                "path": ["work_item_payload", "qa_context", "task_contract"],
            },
            "task_contract_digest": {
                "kind": "source",
                "path": [
                    "work_item_payload",
                    "qa_context",
                    "task_contract_digest",
                ],
            },
            "checker_baseline": {
                "kind": "coalesce",
                "candidates": [
                    {
                        "kind": "source",
                        "path": [
                            "work_item_payload",
                            "qa_context",
                            "checker_baseline",
                        ],
                    }
                ],
                "default": {"kind": "source", "path": ["artifact_payload"]},
            },
            "checker_baseline_digest": {
                "kind": "coalesce",
                "candidates": [
                    {
                        "kind": "source",
                        "path": [
                            "work_item_payload",
                            "qa_context",
                            "checker_baseline_digest",
                        ],
                    }
                ],
                "default": {
                    "kind": "source",
                    "path": ["run_metadata", "artifact_payload_digest"],
                },
            },
        },
    }


def test_builder_and_checker_projection_priorities_are_exact() -> None:
    for workflow_id in QA_SCOPE_IDS:
        source = _qa_source(workflow_id)
        actions = {
            str(action["id"]): action
            for action in _records(source, "terminal_actions")
            if str(action["id"]).startswith("execution.")
        }
        assert _projection_fields(actions["execution.route_builder_complete"])[
            "qa_context"
        ] == _builder_qa_context_projection()
        assert _projection_fields(actions["execution.route_builder_blocked"])[
            "qa_context"
        ] == _builder_qa_context_projection()
        for action_id in (
            "execution.route_checker_pass",
            "execution.route_checker_fix_needed",
            "execution.route_checker_blocked",
        ):
            assert _projection_fields(actions[action_id])["qa_context"] == (
                _checker_qa_context_projection()
            )


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
def test_all_non_normalizing_execution_routes_project_original_qa_context(
    workflow_id: str,
) -> None:
    source = _qa_source(workflow_id)
    route_actions = [
        action
        for action in _records(source, "terminal_actions")
        if str(action["id"]).startswith("execution.")
        and action["kind"] == "route"
    ]
    assert route_actions
    route_ids = {str(action["id"]) for action in route_actions}
    assert _NORMALIZING_ROUTE_IDS <= route_ids
    non_normalizing_stage_ids = {
        str(action["stage_kind_id"])
        for action in route_actions
        if str(action["id"]) not in _NORMALIZING_ROUTE_IDS
    }
    assert {
        "lad_fixer",
        "lad_doublechecker",
        "lad_updater",
        "lad_troubleshooter",
        "lad_consultant",
    } <= non_normalizing_stage_ids
    if workflow_id == "execution.lad_integrator":
        assert "lad_integrator" in non_normalizing_stage_ids

    artifact_payload = {
        "implementation_evidence": {"summary": "implementation"},
        "integration_evidence": {"summary": "integration"},
        "checker_evidence": {"summary": "checker"},
        "fixer_evidence": {"summary": "fixer"},
        "doublechecker_evidence": {"summary": "doublechecker"},
        "updater_evidence": {"summary": "updater"},
        "troubleshooter_evidence": {"summary": "troubleshooter"},
        "consultant_evidence": {"summary": "consultant"},
    }
    for action in route_actions:
        fields = _projection_fields(action)
        if str(action["id"]) in _NORMALIZING_ROUTE_IDS:
            continue
        assert fields["qa_context"] == {
            "kind": "source",
            "path": ["work_item_payload", "qa_context"],
        }
        recorded_context = _recorded_qa_context(str(action["id"]))
        projected = _projected_payload(
            action,
            work_item_payload={
                "qa_context": recorded_context,
                "carrier_sentinel": str(action["id"]),
                "implementation_evidence": artifact_payload[
                    "implementation_evidence"
                ],
                "integration_evidence": artifact_payload["integration_evidence"],
            },
            artifact_payload=artifact_payload,
        )
        assert _canonical_payload_bytes(_thaw(projected["qa_context"])) == (
            _canonical_payload_bytes(recorded_context)
        )
        evidence_fields = set(fields) - {"qa_context"}
        assert len(evidence_fields) <= 2
        assert all(field.endswith("_evidence") for field in evidence_fields)


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
def test_normal_qa_routes_use_typed_results_and_runtime_failures_only_use_stage_result(
    workflow_id: str,
) -> None:
    source = _qa_source(workflow_id)
    actions = {
        str(action["id"]): action
        for action in _records(source, "terminal_actions")
        if str(action["id"]).startswith("execution.")
    }
    for marker in ("pass", "fix_needed", "blocked"):
        assert actions[f"execution.route_checker_{marker}"]["artifact_schema_id"] == (
            "execution.artifacts.checker_result"
        )
        assert actions[
            f"execution.route_doublechecker_{marker}"
        ]["artifact_schema_id"] == "execution.artifacts.doublecheck_result"
    assert actions[
        "execution.close_checker_runtime_failure_exhausted"
    ]["artifact_schema_id"] == "execution.artifacts.stage_result"
    assert actions[
        "execution.close_doublechecker_runtime_failure_exhausted"
    ]["artifact_schema_id"] == "execution.artifacts.stage_result"
    assert all(
        action.get("artifact_schema_id") != "execution.artifacts.stage_result"
        for action in actions.values()
        if action["stage_kind_id"] in {"lad_checker", "lad_doublechecker"}
        and action["id"]
        not in {
            "execution.close_checker_runtime_failure_exhausted",
            "execution.close_doublechecker_runtime_failure_exhausted",
        }
    )


def test_checker_reads_integrator_output_without_claiming_ownership() -> None:
    source = _qa_source("execution.lad_integrator")
    checker = _record(source, "stage_kinds", "lad_checker")
    assert "execution.artifacts.integration_report" in cast(
        list[object], checker["artifact_schema_ids"]
    )
    assert all(
        action.get("artifact_schema_id") != "execution.artifacts.integration_report"
        for action in _records(source, "terminal_actions")
        if action["stage_kind_id"] == "lad_checker"
    )


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
def test_selected_execution_qa_decisions_use_trusted_contract_and_frozen_baseline(
    workflow_id: str,
) -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, workflow_id)
    fingerprint = authority_fingerprint(plan)
    source_actions = {
        str(action["id"]): action
        for action in _records(_qa_source(workflow_id), "terminal_actions")
        if str(action["id"]).startswith("execution.")
    }
    contract = _trusted_task_contract()
    direct_task = cast(dict[str, AuthorityValue], contract)
    builder_payload = _projected_payload(
        source_actions["execution.route_builder_complete"],
        work_item_payload=direct_task,
        artifact_payload={"summary": "implementation evidence"},
        work_item_digest=artifact_payload_digest(contract),
    )
    builder_work_item_payload = {
        "task_id": direct_task["task_id"],
        "body": direct_task["body"],
        **builder_payload,
    }
    qa_context = cast(Record, builder_payload["qa_context"])
    assert _thaw(qa_context["task_contract"]) == direct_task
    assert qa_context["task_contract_digest"] == artifact_payload_digest(contract)

    pass_state, pass_run, _ = _claimed_execution_stage(
        plan,
        fingerprint,
        stage_kind_id="lad_checker",
        payload=cast(Mapping[str, AuthorityValue], builder_work_item_payload),
        tag=f"{workflow_id}-checker-pass",
    )
    checker_pass = _checker_payload_for_contract(contract, fix_needed=False)
    passed_state, _decision, passed_activation_id = _run_terminal_action(
        pass_state,
        plan,
        fingerprint,
        run_id=pass_run.run_ref.run_id,
        action_id="execution.route_checker_pass",
        tag=f"{workflow_id}-checker-pass",
        artifact_payload=cast(Mapping[str, AuthorityValue], checker_pass),
    )
    passed_activation = passed_state.activations[passed_activation_id]
    assert str(passed_activation.stage_kind_id) == "lad_updater"
    passed_work_item = passed_state.work_items[passed_activation.work_item_id]
    passed_context = cast(Record, passed_work_item.payload["qa_context"])
    assert _thaw(passed_context["task_contract"]) == direct_task
    assert passed_context["task_contract_digest"] == artifact_payload_digest(contract)
    assert _thaw(passed_context["checker_baseline"]) == checker_pass
    assert passed_context["checker_baseline_digest"] == artifact_payload_digest(
        checker_pass
    )

    state, checker_run, _ = _claimed_execution_stage(
        plan,
        fingerprint,
        stage_kind_id="lad_checker",
        payload=cast(Mapping[str, AuthorityValue], builder_work_item_payload),
        tag=f"{workflow_id}-checker-fix",
    )
    checker_fix = _checker_payload_for_contract(contract, fix_needed=True)
    state, _decision, fixer_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=checker_run.run_ref.run_id,
        action_id="execution.route_checker_fix_needed",
        tag=f"{workflow_id}-checker-fix",
        artifact_payload=cast(Mapping[str, AuthorityValue], checker_fix),
    )
    fixer_activation = state.activations[fixer_activation_id]
    assert str(fixer_activation.stage_kind_id) == "lad_fixer"
    first_fixer_payload = state.work_items[fixer_activation.work_item_id].payload
    first_context = cast(Record, first_fixer_payload["qa_context"])
    assert _thaw(first_context["checker_baseline"]) == checker_fix
    assert first_context["checker_baseline_digest"] == artifact_payload_digest(
        checker_fix
    )
    baseline_bytes = _canonical_payload_bytes(_thaw(first_context))
    baseline_digest = str(first_context["checker_baseline_digest"])
    findings = cast(list[object], checker_fix["findings"])
    assert [cast(Record, finding)["criterion_refs"] for finding in findings] == [
        [{"criterion_id": "criterion-contract-1"}]
    ]

    state, fixer_run = _claim_existing_execution_activation(
        state,
        fixer_activation_id,
        tag=f"{workflow_id}-fixer-one",
    )
    state, _decision, doublechecker_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=fixer_run.run_ref.run_id,
        action_id="execution.route_fixer_complete",
        tag=f"{workflow_id}-fixer-one",
        artifact_payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "The first repair was applied.",
        },
    )
    state, doublechecker_run = _claim_existing_execution_activation(
        state,
        doublechecker_activation_id,
        tag=f"{workflow_id}-doublechecker-one",
    )
    state, _decision, fixer_again_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=doublechecker_run.run_ref.run_id,
        action_id="execution.route_doublechecker_fix_needed",
        tag=f"{workflow_id}-doublechecker-one",
        artifact_payload=cast(
            Mapping[str, AuthorityValue],
            _doublechecker_payload_for_contract(
                contract,
                baseline_digest=baseline_digest,
                status="unresolved",
            ),
        ),
    )
    fixer_again_payload = state.work_items[
        state.activations[fixer_again_activation_id].work_item_id
    ].payload
    assert _canonical_payload_bytes(
        _thaw(cast(Record, fixer_again_payload["qa_context"]))
    ) == baseline_bytes

    state, fixer_again_run = _claim_existing_execution_activation(
        state,
        fixer_again_activation_id,
        tag=f"{workflow_id}-fixer-two",
    )
    state, _decision, doublechecker_again_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=fixer_again_run.run_ref.run_id,
        action_id="execution.route_fixer_complete",
        tag=f"{workflow_id}-fixer-two",
        artifact_payload={
            "artifact_kind": "execution.artifacts.stage_result",
            "summary": "The repeated repair was applied.",
        },
    )
    state, doublechecker_again_run = _claim_existing_execution_activation(
        state,
        doublechecker_again_activation_id,
        tag=f"{workflow_id}-doublechecker-two",
    )
    resolved_payload = _doublechecker_payload_for_contract(
        contract,
        baseline_digest=baseline_digest,
        status="resolved",
    )
    state, _decision, updater_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=doublechecker_again_run.run_ref.run_id,
        action_id="execution.route_doublechecker_pass",
        tag=f"{workflow_id}-doublechecker-two",
        artifact_payload=cast(
            Mapping[str, AuthorityValue],
            resolved_payload,
        ),
    )
    updater_activation = state.activations[updater_activation_id]
    assert str(updater_activation.stage_kind_id) == "lad_updater"
    final_context = cast(
        Record,
        state.work_items[updater_activation.work_item_id].payload["qa_context"],
    )
    assert _canonical_payload_bytes(_thaw(final_context)) == baseline_bytes
    statuses = cast(list[object], resolved_payload["finding_statuses"])
    assert [cast(Record, status)["finding_id"] for status in statuses] == [
        "finding-contract-1"
    ]


def _git_porcelain(repo: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _git_names(repo: Path, *arguments: str) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def _dispatch_selected_qa_stage(
    repo: Path,
    plan: SelectedCompiledPlan,
    state: RuntimeState,
    *,
    run_id: str,
    stage_kind_id: str,
    artifact_payload: Mapping[str, AuthorityValue],
) -> AdapterSuccessResult:
    import millforge

    before_status = _git_porcelain(repo)
    before_files = _git_names(repo, "ls-files")
    stage = _execution_stage(plan, stage_kind_id)
    run = state.runs[run_id]
    work_item = state.work_items[run.work_item_id]
    activation = state.activations[run.activation_id]
    stage_queue_family_id = stage.input_queue_family_ids[0]
    dispatch_state = replace(
        state,
        work_items={
            **state.work_items,
            run.work_item_id: replace(
                work_item,
                queue_family_id=stage_queue_family_id,
            ),
        },
        activations={
            **state.activations,
            run.activation_id: replace(
                activation,
                queue_family_id=stage_queue_family_id,
            ),
        },
    )
    dispatch: RunnerDispatchEnvelope = fake_runner_dispatch_envelope_for_run(
        state=dispatch_state,
        run_id=run_id,
    )
    assert dispatch.stage_kind_id == stage_kind_id
    binding = next(
        binding
        for binding in plan.runner_bindings
        if str(binding.id) == dispatch.runner_binding_id
    )
    assert binding.adapter_kind == "millforge"
    pin = binding.component_pin
    assert pin is not None
    selected_schema_ids = {
        str(option["artifact_schema_id"])
        for option in dispatch.terminal_options
        if option["artifact_schema_id"] is not None
    }
    selected_artifact_schemas = tuple(
        schema
        for schema in plan.artifact_schemas
        if str(schema.id) in selected_schema_ids
    )
    assert {
        str(schema.id) for schema in selected_artifact_schemas
    } == selected_schema_ids
    marker = {
        "lad_checker": "CHECKER_PASS",
        "lad_doublechecker": "DOUBLECHECK_PASS",
    }[stage_kind_id]
    selected_schema_id = next(
        str(option["artifact_schema_id"])
        for option in dispatch.terminal_options
        if option["marker"] == marker
    )
    selected_schema = next(
        schema
        for schema in selected_artifact_schemas
        if str(schema.id) == selected_schema_id
    )
    assert validate_schema(selected_schema.schema, artifact_payload).accepted
    selected_asset_ids = (
        (dispatch.entrypoint_asset_id,)
        if dispatch.entrypoint_asset_id is not None
        else ()
    ) + dispatch.skill_asset_ids
    assets_by_id = {str(asset.id): asset for asset in plan.assets}
    assert set(selected_asset_ids) <= set(assets_by_id)
    selected_asset_material = {
        asset_id: {"body": assets_by_id[asset_id].body}
        for asset_id in selected_asset_ids
    }
    redaction_policy = RedactionPolicy(
        policy_id="controlled-qa-dispatch",
        secret_tokens=(),
    )

    class DeterministicFacade:
        def __init__(self) -> None:
            self.calls = 0
            self.requests: list[object] = []
            self.descriptor = SimpleNamespace(
                runner_id=pin.component_id,
                runner_version=pin.component_version,
                harness_id="millforge.base.unrestricted_agent.v1",
                harness_version=1,
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
                    harness_id="millforge.base.unrestricted_agent.v1",
                    harness_version=1,
                    compiled_sha256="c" * 64,
                ),
                capability_envelope=SimpleNamespace(
                    grants=tuple(
                        SimpleNamespace(capability_id=str(value))
                        for value in pin.required_capability_ids
                    )
                ),
                model_profile=SimpleNamespace(profile_id="controlled-qa-profile"),
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
                descriptor_sha256=pin.descriptor_sha256,
                context_file_count=0,
                selected_output_requirements_sha256=requirements_digest,
            )

        async def execute(self, request: object) -> object:
            self.calls += 1
            self.requests.append(request)
            decoded = cast(
                dict[str, object],
                json.loads(request.task.instruction),
            )
            assert decoded["entrypoint_asset_id"] == dispatch.entrypoint_asset_id
            assert decoded["skill_asset_ids"] == list(dispatch.skill_asset_ids)
            assert decoded["selected_asset_material"] == _thaw(
                selected_asset_material
            )
            dispatch_identity = cast(
                dict[str, object], decoded["dispatch_identity"]
            )
            assert dispatch_identity["stage_kind_id"] == stage_kind_id
            assert dispatch_identity["runner_binding_id"] == dispatch.runner_binding_id
            assert decoded["terminal_options"] == _thaw(dispatch.terminal_options)
            requirement = next(
                item
                for item in request.selected_output_requirements
                if item.terminal_result == marker
            )
            selected_output = millforge.SelectedOutputPresent(
                value=artifact_payload
            )
            schema_digest = requirement.selected_output.schema_sha256
            intent = SimpleNamespace(
                request_id=request.request_id,
                run_id=request.run_id,
                stage=request.stage,
                terminal_result=marker,
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
                usage=None,
            )

    facade = DeterministicFacade()
    request = AdapterInvocationRequest(
        adapter_id="controlled-qa-dispatch",
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind=binding.adapter_kind,
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=float(binding.invocation_timeout_seconds),
        correlation_id=f"correlation-{stage_kind_id}",
        redaction_policy=redaction_policy,
        selected_asset_material=selected_asset_material,
        selected_component_pin=pin,
        selected_terminal_result_mappings=binding.terminal_result_mappings,
        selected_artifact_schemas=selected_artifact_schemas,
    )
    adapter = MillforgeAdapter(
        MillforgeAdapterConfig(
            adapter_id="controlled-qa-dispatch",
            facade=facade,
            workspace_root=repo,
            timeout_seconds=10,
            redaction_policy=redaction_policy,
        )
    )
    started = adapter.start_session(request)
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
    assert outcome.marker == marker
    assert _thaw(outcome.artifact_payload_candidate) == _thaw(artifact_payload)
    assert facade.calls == 1
    assert len(facade.requests) == 1
    received_request = facade.requests[0]
    assert received_request.run_id == dispatch.run_id
    assert received_request.work_item_id == dispatch.work_item_id
    started.handle.cleanup()

    assert _git_porcelain(repo) == before_status == ""
    assert _git_names(repo, "diff", "--name-only") == ()
    assert _git_names(repo, "diff", "--cached", "--name-only") == ()
    assert _git_names(repo, "ls-files") == before_files
    return outcome


def test_checker_entrypoint_constructs_criteria_before_judging_implementation() -> None:
    checker_entrypoint = (
        PACKAGE_ROOT / "assets/workflows/execution.lad/entrypoints/lad_checker.md"
    ).read_text()
    criteria_phase = checker_entrypoint.index(
        "derive criteria from the trusted task contract and return them in the "
        "Checker baseline"
    )
    judgment_phase = checker_entrypoint.index(
        "Check each criterion against reproducible evidence."
    )
    assert criteria_phase < judgment_phase


@pytest.mark.parametrize("role", ("checker", "doublechecker"))
def test_checker_and_doublechecker_dispatches_leave_a_fresh_git_repo_unchanged(
    tmp_path: Path,
    role: str,
) -> None:
    repo = tmp_path / "qa-repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src/app.py").write_text("def answer():\n    return 42\n")
    (repo / "tests/test_app.py").write_text(
        "from src.app import answer\n\n\ndef test_answer():\n"
        "    assert answer() == 42\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "qa-conformance@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "QA Conformance"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "add", "src/app.py", "tests/test_app.py"], cwd=repo, check=True
    )
    subprocess.run(["git", "commit", "-qm", "seed repository"], cwd=repo, check=True)
    before_status = _git_porcelain(repo)
    before_files = _git_names(repo, "ls-files")

    workflow_id = "execution.lad"
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, workflow_id)
    fingerprint = authority_fingerprint(plan)
    contract = _trusted_task_contract()
    source_actions = {
        str(action["id"]): action
        for action in _records(_qa_source(workflow_id), "terminal_actions")
        if str(action["id"]).startswith("execution.")
    }
    builder_payload = _projected_payload(
        source_actions["execution.route_builder_complete"],
        work_item_payload=cast(dict[str, object], contract),
        artifact_payload={"summary": "implementation evidence"},
        work_item_digest=artifact_payload_digest(contract),
    )
    builder_work_item_payload = {
        "task_id": contract["task_id"],
        "body": contract["body"],
        **builder_payload,
    }
    state, checker_run, _ = _claimed_execution_stage(
        plan,
        fingerprint,
        stage_kind_id="lad_checker",
        payload=cast(Mapping[str, AuthorityValue], builder_work_item_payload),
        tag=f"git-{role}-checker",
    )
    if role == "checker":
        checker_dispatch = _dispatch_selected_qa_stage(
            repo,
            plan,
            state,
            run_id=checker_run.run_ref.run_id,
            stage_kind_id="lad_checker",
            artifact_payload=cast(
                Mapping[str, AuthorityValue],
                _checker_payload_for_contract(contract, fix_needed=False),
            ),
        )
        state, _decision, _target_activation_id = _run_terminal_action(
            state,
            plan,
            fingerprint,
            run_id=checker_run.run_ref.run_id,
            action_id="execution.route_checker_pass",
            tag="git-checker-pass",
            artifact_payload=cast(
                Mapping[str, AuthorityValue],
                checker_dispatch.artifact_payload_candidate,
            ),
        )
    else:
        state, _decision, fixer_activation_id = _run_terminal_action(
            state,
            plan,
            fingerprint,
            run_id=checker_run.run_ref.run_id,
            action_id="execution.route_checker_fix_needed",
            tag="git-doublechecker-checker",
            artifact_payload=cast(
                Mapping[str, AuthorityValue],
                _checker_payload_for_contract(contract, fix_needed=True),
            ),
        )
        state, fixer_run = _claim_existing_execution_activation(
            state,
            fixer_activation_id,
            tag="git-doublechecker-fixer",
        )
        state, _decision, doublechecker_activation_id = _run_terminal_action(
            state,
            plan,
            fingerprint,
            run_id=fixer_run.run_ref.run_id,
            action_id="execution.route_fixer_complete",
            tag="git-doublechecker-fixer-result",
            artifact_payload={
                "artifact_kind": "execution.artifacts.stage_result",
                "summary": "The controlled repair was applied.",
            },
        )
        state, doublechecker_run = _claim_existing_execution_activation(
            state,
            doublechecker_activation_id,
            tag="git-doublechecker-result",
        )
        doublechecker_payload = _doublechecker_payload_for_contract(
            contract,
            baseline_digest=artifact_payload_digest(
                _checker_payload_for_contract(contract, fix_needed=True)
            ),
            status="resolved",
        )
        doublechecker_dispatch = _dispatch_selected_qa_stage(
            repo,
            plan,
            state,
            run_id=doublechecker_run.run_ref.run_id,
            stage_kind_id="lad_doublechecker",
            artifact_payload=cast(
                Mapping[str, AuthorityValue],
                doublechecker_payload,
            ),
        )
        state, _decision, _target_activation_id = _run_terminal_action(
            state,
            plan,
            fingerprint,
            run_id=doublechecker_run.run_ref.run_id,
            action_id="execution.route_doublechecker_pass",
            tag="git-doublechecker-pass",
            artifact_payload=cast(
                Mapping[str, AuthorityValue],
                doublechecker_dispatch.artifact_payload_candidate,
            ),
        )

    assert _git_porcelain(repo) == before_status == ""
    assert _git_names(repo, "diff", "--name-only") == ()
    assert _git_names(repo, "diff", "--cached", "--name-only") == ()
    assert _git_names(repo, "ls-files") == before_files
    assert state.remediation_work_records == {}
    assert all(
        not {"queue", "remediation", "incident"}.intersection(
            cast(Record, _thaw(artifact.payload))
        )
        for artifact in state.artifacts.values()
    )


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
def test_builder_reentry_and_two_fix_cycles_keep_the_original_baseline(
    workflow_id: str,
) -> None:
    source = _qa_source(workflow_id)
    actions = {
        str(action["id"]): action
        for action in _records(source, "terminal_actions")
        if str(action["id"]).startswith("execution.")
    }
    direct_task = {"task_id": "task-1", "body": "implement the task"}
    implementation = {"summary": "implemented"}
    builder_payload = _projected_payload(
        actions["execution.route_builder_complete"],
        work_item_payload=direct_task,
        artifact_payload=implementation,
        work_item_digest="sha256:trusted-task",
    )
    qa_context = cast(Record, builder_payload["qa_context"])
    assert qa_context == {
        "task_contract": direct_task,
        "task_contract_digest": "sha256:trusted-task",
        "checker_baseline": None,
        "checker_baseline_digest": None,
    }

    reentry_payload = _projected_payload(
        actions["execution.route_builder_complete"],
        work_item_payload=builder_payload,
        artifact_payload={"summary": "re-entry"},
        work_item_digest="sha256:must-not-win",
    )
    assert reentry_payload["qa_context"] == qa_context

    checker_one = _checker_payload()
    checked_payload = _projected_payload(
        actions["execution.route_checker_fix_needed"],
        work_item_payload=builder_payload,
        artifact_payload=checker_one,
        artifact_digest="sha256:baseline-one",
    )
    first_context = cast(Record, checked_payload["qa_context"])
    assert _thaw(first_context["checker_baseline"]) == checker_one
    assert first_context["checker_baseline_digest"] == "sha256:baseline-one"
    assert first_context["task_contract_digest"] == "sha256:trusted-task"

    later_checker = _projected_payload(
        actions["execution.route_checker_pass"],
        work_item_payload=checked_payload,
        artifact_payload=_checker_payload(),
        artifact_digest="sha256:baseline-two",
    )
    assert _thaw(later_checker["qa_context"]) == _thaw(first_context)

    fixer_one = _projected_payload(
        actions["execution.route_fixer_complete"],
        work_item_payload=checked_payload,
        artifact_payload={"summary": "fixed once"},
    )
    doublechecker_one = _projected_payload(
        actions["execution.route_doublechecker_fix_needed"],
        work_item_payload=fixer_one,
        artifact_payload=_doublechecker_payload(),
    )
    assert _thaw(doublechecker_one["qa_context"]) == _thaw(first_context)

    fixer_two = _projected_payload(
        actions["execution.route_fixer_complete"],
        work_item_payload=doublechecker_one,
        artifact_payload={"summary": "fixed twice"},
    )
    doublechecker_two = _projected_payload(
        actions["execution.route_doublechecker_pass"],
        work_item_payload=fixer_two,
        artifact_payload=_doublechecker_payload(),
    )
    assert _thaw(doublechecker_two["qa_context"]) == _thaw(first_context)


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
def test_every_recorded_source_action_preserves_original_qa_context(
    workflow_id: str,
) -> None:
    source = _qa_source(workflow_id)
    actions = {
        str(action["id"]): action
        for action in _records(source, "terminal_actions")
        if str(action["id"]).startswith("execution.")
    }
    recovery_actions = {
        action_id: action
        for action_id, action in actions.items()
        if action["kind"] == "recovery_route"
    }
    return_actions = {
        action_id: action
        for action_id, action in actions.items()
        if action["kind"] == "return_to_recorded_source"
    }
    assert set(recovery_actions) == _expected_recovery_route_ids(workflow_id)
    assert set(return_actions) == _COMMON_RETURN_ACTION_IDS
    assert {
        action_id
        for action_id in recovery_actions
        if action_id.startswith("execution.escalate_")
    } == {
        action_id
        for action_id in _expected_recovery_route_ids(workflow_id)
        if action_id.startswith("execution.escalate_")
    }
    assert {
        action_id
        for action_id in actions
        if action_id.startswith("execution.escalate_")
    } == {
        action_id
        for action_id in _expected_recovery_route_ids(workflow_id)
        if action_id.startswith("execution.escalate_")
    }

    policies = {
        str(policy["id"]): policy
        for policy in _records(source, "recovery_policies")
    }
    policy_by_action: dict[str, set[str]] = {}
    for policy_id, policy in policies.items():
        for action_id in (
            cast(list[object], policy["source_recovery_action_ids"])
            + cast(list[object], policy["return_action_ids"])
        ):
            action_name = str(action_id)
            policy_by_action.setdefault(action_name, set()).add(policy_id)
    for counter in _records(source, "counters"):
        threshold_id = str(counter["threshold_action_id"])
        if threshold_id not in recovery_actions:
            continue
        counter_id = str(counter["id"])
        assert counter_id.startswith(
            ("execution.fix_cycle_count.", "execution.troubleshoot_attempt_count.")
        )
        policy_by_action.setdefault(threshold_id, set()).add(
            "execution.fix_needed_recovery"
            if counter_id.startswith("execution.fix_cycle_count.")
            else "execution.blocked_recovery"
        )

    recorded_payload = {
        "task_id": f"recorded-{workflow_id}",
        "body": "recorded source payload",
        "qa_context": _recorded_qa_context(workflow_id),
        "recorded_source_sentinel": "do-not-rebuild-this-payload",
    }
    plan_workflow_id = (
        workflow_id if workflow_id in WORKFLOW_IDS else WORKFLOW_IDS[0]
    )
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, plan_workflow_id)
    fingerprint = authority_fingerprint(plan)
    for action_id, action in recovery_actions.items():
        assert action["kind"] in {"recovery_route", "return_to_recorded_source"}
        assert action.get("payload_projection") is None
        assert policy_by_action[action_id]
        for policy_id in policy_by_action[action_id]:
            policy = policies[policy_id]
            assert policy["attempt_scope"] == "lineage"
            assert policy["recorded_source_selector"] == (
                "latest_recovery_attempt_for_lineage"
            )
        after = _apply_selected_recovery_action(
            plan,
            fingerprint,
            action_id=action_id,
            recorded_payload=cast(
                Mapping[str, AuthorityValue],
                recorded_payload,
            ),
            tag=f"{workflow_id}-{action_id}",
        )
        assert len(after.work_items) == 1

    for action_id in return_actions:
        after = _apply_selected_return_action(
            plan,
            fingerprint,
            action_id=action_id,
            recorded_payload=cast(
                Mapping[str, AuthorityValue],
                recorded_payload,
            ),
            tag=f"{workflow_id}-{action_id}",
        )
        assert len(after.work_items) == 1


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
def test_every_dynamic_execution_target_projects_original_qa_context(
    workflow_id: str,
) -> None:
    source = _qa_source(workflow_id)
    plan = conformance.compile_packaged_workflow(
        PACKAGE_ROOT,
        workflow_id if workflow_id in WORKFLOW_IDS else WORKFLOW_IDS[0],
    )
    source_actions = {
        str(action["id"]): action
        for action in _records(source, "terminal_actions")
        if str(action["id"]).startswith("execution.")
    }
    compiled_actions = {str(action.id): action for action in plan.terminal_actions}
    dynamic_action_ids = (
        "execution.return_troubleshooter_complete",
        "execution.route_consultant_complete",
    )
    expected_troubleshooter_targets = {
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "updater",
    }
    if workflow_id == "execution.lad_integrator":
        expected_troubleshooter_targets.add("integrator")
    expected_consultant_targets = {
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "troubleshooter",
        "updater",
    }
    if workflow_id == "execution.lad_integrator":
        expected_consultant_targets.add("integrator")

    artifact_payload = {
        "implementation_evidence": {"summary": "implementation"},
        "integration_evidence": {"summary": "integration"},
        "checker_evidence": {"summary": "checker"},
        "fixer_evidence": {"summary": "fixer"},
        "doublechecker_evidence": {"summary": "doublechecker"},
        "updater_evidence": {"summary": "updater"},
        "troubleshooter_evidence": {"summary": "troubleshooter"},
        "consultant_evidence": {"summary": "consultant"},
    }
    for action_id in dynamic_action_ids:
        source_action = source_actions[action_id]
        compiled_action = compiled_actions[action_id]
        fields = _projection_fields(source_action)
        assert fields["qa_context"] == {
            "kind": "source",
            "path": ["work_item_payload", "qa_context"],
        }
        selector = cast(Record, source_action["dynamic_target_selector"])
        targets = cast(dict[str, Record], selector["targets"])
        expected_targets = (
            expected_troubleshooter_targets
            if action_id == "execution.return_troubleshooter_complete"
            else expected_consultant_targets
        )
        assert set(targets) == expected_targets
        field_names = cast(list[object], selector["field_names"])
        selection_field = str(field_names[0])
        for target_name, target in targets.items():
            resolution = _route_target_fields_or_refusal(
                action=compiled_action,
                observation_payload={selection_field: target_name},
            )
            assert isinstance(resolution, tuple)
            assert tuple(str(value) for value in resolution) == tuple(
                str(target[field_name])
                for field_name in (
                    "target_stage_kind_id",
                    "target_graph_node_id",
                    "emitted_queue_family_id",
                    "runner_binding_id",
                )
            )
            recorded_context = _recorded_qa_context(f"{action_id}-{target_name}")
            projected = _projected_payload(
                source_action,
                work_item_payload={"qa_context": recorded_context},
                artifact_payload=artifact_payload,
            )
            assert _canonical_payload_bytes(_thaw(projected["qa_context"])) == (
                _canonical_payload_bytes(recorded_context)
            )


@pytest.mark.parametrize("workflow_id", QA_SCOPE_IDS)
@pytest.mark.parametrize(
    ("result_kind", "payload_factory", "pass_action_id", "fix_action_id"),
    (
        (
            "checker",
            _passing_checker_payload,
            "execution.route_checker_pass",
            "execution.route_checker_fix_needed",
        ),
        (
            "doublechecker",
            _passing_doublechecker_payload,
            "execution.route_doublechecker_pass",
            "execution.route_doublechecker_fix_needed",
        ),
    ),
)
def test_supplementary_observation_only_result_stays_passing(
    workflow_id: str,
    result_kind: str,
    payload_factory: object,
    pass_action_id: str,
    fix_action_id: str,
) -> None:
    source = _qa_source(workflow_id)
    schemas = {
        str(schema["id"]): cast(Record, schema["schema"])
        for schema in _records(source, "artifact_schemas")
    }
    payload = cast(dict[str, object], cast(Any, payload_factory)())
    schema_id = (
        "execution.artifacts.checker_result"
        if result_kind == "checker"
        else "execution.artifacts.doublecheck_result"
    )
    assert validate_schema(schemas[schema_id], payload).accepted
    assert payload["observations"] == [
        {
            "observation_id": "observation-only",
            "summary": "A supplementary observation is outside the frozen finding set.",
        }
    ]
    if result_kind == "checker":
        assert payload["findings"] == []
    else:
        statuses = cast(list[object], payload["finding_statuses"])
        assert statuses
        assert all(cast(Record, item)["status"] == "resolved" for item in statuses)

    actions = {
        str(action["id"]): action
        for action in _records(source, "terminal_actions")
        if str(action["id"]).startswith("execution.")
    }
    pass_action = actions[pass_action_id]
    fix_action = actions[fix_action_id]
    blocked_action_id = (
        "execution.route_checker_blocked"
        if result_kind == "checker"
        else "execution.route_doublechecker_blocked"
    )
    assert pass_action["target_stage_kind_id"] == "lad_updater"
    assert fix_action["target_stage_kind_id"] == "lad_fixer"
    assert pass_action_id not in {fix_action_id, blocked_action_id}
    assert pass_action["artifact_schema_id"] == schema_id
    assert fix_action["artifact_schema_id"] == schema_id
    assert pass_action["target_stage_kind_id"] != "lad_fixer"

    plan_workflow_id = (
        workflow_id if workflow_id in WORKFLOW_IDS else WORKFLOW_IDS[0]
    )
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, plan_workflow_id)
    fingerprint = authority_fingerprint(plan)
    recorded_payload = {
        "task_id": f"observation-{workflow_id}-{result_kind}",
        "body": "recorded source payload",
        "qa_context": _recorded_qa_context(
            f"{workflow_id}-{result_kind}"
        ),
    }
    source_action = _execution_action(plan, pass_action_id)
    state, run, _ = _claimed_execution_stage(
        plan,
        fingerprint,
        stage_kind_id=str(source_action.stage_kind_id),
        payload=cast(Mapping[str, AuthorityValue], recorded_payload),
        tag=f"{workflow_id}-{result_kind}",
    )
    before = state
    after, _decision, target_activation_id = _run_terminal_action(
        state,
        plan,
        fingerprint,
        run_id=run.run_ref.run_id,
        action_id=pass_action_id,
        tag=f"{workflow_id}-{result_kind}",
        artifact_payload=cast(Mapping[str, AuthorityValue], payload),
    )
    new_activations = [
        activation
        for activation_id, activation in after.activations.items()
        if activation_id not in before.activations
    ]
    assert len(new_activations) == 1
    assert new_activations[0].activation_id == target_activation_id
    assert str(new_activations[0].stage_kind_id) == "lad_updater"
    compiled_pass_action = _execution_action(plan, pass_action_id)
    assert (
        new_activations[0].stage_kind_id == compiled_pass_action.target_stage_kind_id
    )
    assert (
        new_activations[0].graph_node_id == compiled_pass_action.target_graph_node_id
    )
    assert (
        new_activations[0].runner_binding_id
        == compiled_pass_action.runner_binding_id
    )

    new_work_items = [
        work_item
        for work_item_id, work_item in after.work_items.items()
        if work_item_id not in before.work_items
    ]
    assert len(new_work_items) == 1
    new_artifacts = [
        artifact
        for artifact_id, artifact in after.artifacts.items()
        if artifact_id not in before.artifacts
    ]
    assert len(new_artifacts) == 1
    artifact = new_artifacts[0]
    assert str(artifact.schema_id) == schema_id
    assert str(artifact.source_action_id) == pass_action_id
    assert _thaw(artifact.payload) == payload
    assert all(
        str(activation.stage_kind_id) != "lad_fixer"
        for activation in new_activations
    )
    assert all(
        str(candidate.schema_id) != "execution.artifacts.incident_report"
        for candidate in new_artifacts
    )
    assert after.closed_work_items == before.closed_work_items
    assert after.cooldown_waits == before.cooldown_waits
    assert after.lineage_quarantines == before.lineage_quarantines
    assert after.quarantines == before.quarantines
    assert after.remediation_work_records == before.remediation_work_records
    assert after.operator_waits == before.operator_waits


def test_selected_qa_assets_encode_read_only_marker_and_observation_rules() -> None:
    asset_paths = (
        "assets/workflows/execution.lad/entrypoints/lad_checker.md",
        "assets/workflows/execution.lad/skills/checker-core.md",
        "assets/workflows/execution.lad/entrypoints/lad_doublechecker.md",
        "assets/workflows/execution.lad/skills/doublechecker-core.md",
    )
    text = "\n".join((PACKAGE_ROOT / path).read_text() for path in asset_paths).lower()
    for required in (
        "qa_context",
        "source/tests",
        "integrator evidence is read-only",
        "observations never determine",
        "terminal markers are evidence candidates",
        "does not mutate git",
        "does not mutate queues",
    ):
        assert required in text


def test_selected_checker_authoring_contracts_and_json_refusals_are_explicit() -> None:
    entrypoint_paths = (
        PACKAGE_ROOT
        / "assets/workflows/execution.lad/entrypoints/lad_checker.md",
        PACKAGE_ROOT
        / "assets/workflows/execution.lad/entrypoints/lad_doublechecker.md",
    )
    for path in entrypoint_paths:
        text = path.read_text()
        for heading in (
            "## Readable assets",
            "## Writable artifacts",
            "## Required evidence",
            "## Forbidden claims",
            "## How to return evidence",
        ):
            assert heading in text

    core_paths = (
        PACKAGE_ROOT / "assets/workflows/execution.lad/skills/checker-core.md",
        PACKAGE_ROOT
        / "assets/workflows/execution.lad/skills/doublechecker-core.md",
    )
    for path in core_paths:
        text = path.read_text()
        assert "## Validation Checklist" in text
        assert (
            "The invalid examples below are refusal cases, not templates to emit."
            in text
        )
        examples = re.findall(r"```json\n(.*?)\n```", text, flags=re.DOTALL)
        assert len(examples) >= 4
        for example in examples:
            json.loads(example)




def test_runtime_supports_required_maximum_collection_bounds() -> None:
    bounded_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "max_items": 8,
            }
        },
        "required": ["items"],
    }
    result = validate_schema_declaration(bounded_schema)
    assert result.accepted, result.issues


_GOVERNED_BUILDER_SCHEMA_ID = "execution.artifacts.builder_result"
_GOVERNED_FIXER_SCHEMA_ID = "execution.artifacts.fixer_result"
_GOVERNED_REPAIR_PLAN_SCHEMA_ID = "execution.artifacts.troubleshooter_repair_plan"
_GOVERNED_CONTEXT_STAGES = (
    "lad_builder",
    "lad_checker",
    "lad_fixer",
    "lad_doublechecker",
    "lad_troubleshooter",
    "lad_updater",
)
_GOVERNED_CATALOG_ROOTS = (
    "millrace-agents/shared/conventions",
    "millrace-agents/shared/decisions",
    "millrace-agents/shared/references",
    "millrace-agents/shared/workspace-map/wiki",
)


def _assert_closed_bounded_schema(node: Record) -> None:
    assert node["type"] == "object"
    properties = cast(Record, node["properties"])
    assert set(cast(list[object], node["required"])) == set(properties)
    for value in properties.values():
        child = cast(Record, value)
        if child.get("type") == "object":
            _assert_closed_bounded_schema(child)
        elif child.get("type") == "array":
            assert type(child.get("max_items")) is int
            assert 0 <= int(child["max_items"]) <= 256
            item = child.get("items")
            if isinstance(item, dict) and item.get("type") == "object":
                _assert_closed_bounded_schema(cast(Record, item))


def test_governed_lad_result_schemas_are_closed_and_bounded() -> None:
    source = _codex_source(SEMANTIC_WORKFLOW_ID)
    schemas = {
        str(schema["id"]): cast(Record, schema["schema"])
        for schema in _records(source, "artifact_schemas")
    }
    expected = {
        _GOVERNED_BUILDER_SCHEMA_ID: {
            "artifact_kind",
            "summary",
            "task_contract_digest",
            "dispatch_digest",
            "canonical_changed_paths",
            "checks",
            "assumptions",
            "unavailable_evidence",
            "remaining_work",
        },
        _GOVERNED_FIXER_SCHEMA_ID: {
            "artifact_kind",
            "summary",
            "original_finding_ids",
            "baseline_digest",
            "canonical_changed_paths",
            "before_checks",
            "after_checks",
            "preserved_contract_digests",
            "preserved_artifact_digests",
            "assumptions",
            "unavailable_evidence",
            "remaining_findings",
        },
        _GOVERNED_REPAIR_PLAN_SCHEMA_ID: {
            "artifact_kind",
            "summary",
            "failed_session_id",
            "failed_stage_id",
            "failure_classification",
            "evidence_refs",
            "diagnosed_scope",
            "baseline_invalidated",
            "reentry_stage",
            "artifact_dependencies",
            "context_dependencies",
            "repair_instructions",
            "stop_conditions",
            "unrecoverable_reason",
        },
    }
    for schema_id, property_names in expected.items():
        schema = schemas[schema_id]
        assert set(cast(Record, schema["properties"])) == property_names
        _assert_closed_bounded_schema(schema)

    builder = {
        "artifact_kind": _GOVERNED_BUILDER_SCHEMA_ID,
        "summary": "built",
        "task_contract_digest": "sha256:task",
        "dispatch_digest": "sha256:dispatch",
        "canonical_changed_paths": ["src/example.py"],
        "checks": [
            {
                "command": "pytest -q",
                "result_classification": "passed",
                "evidence": ["evidence:pytest"],
            }
        ],
        "assumptions": [],
        "unavailable_evidence": [],
        "remaining_work": [],
    }
    fixer = {
        "artifact_kind": _GOVERNED_FIXER_SCHEMA_ID,
        "summary": "fixed",
        "original_finding_ids": ["finding-1"],
        "baseline_digest": "sha256:baseline",
        "canonical_changed_paths": ["src/example.py"],
        "before_checks": [],
        "after_checks": [],
        "preserved_contract_digests": ["sha256:task"],
        "preserved_artifact_digests": ["sha256:checker"],
        "assumptions": [],
        "unavailable_evidence": [],
        "remaining_findings": [],
    }
    repair_plan = {
        "artifact_kind": _GOVERNED_REPAIR_PLAN_SCHEMA_ID,
        "summary": "repair plan",
        "failed_session_id": "session-1",
        "failed_stage_id": "lad_fixer",
        "failure_classification": "narrow_repair",
        "evidence_refs": ["evidence:failure"],
        "diagnosed_scope": "one finding",
        "baseline_invalidated": False,
        "reentry_stage": "fixer",
        "artifact_dependencies": ["execution.artifacts.checker_result"],
        "context_dependencies": ["selected_artifacts/direct_predecessors"],
        "repair_instructions": ["apply the finding"],
        "stop_conditions": ["stop when the finding check passes"],
        "unrecoverable_reason": "none",
    }
    for schema_id, payload in (
        (_GOVERNED_BUILDER_SCHEMA_ID, builder),
        (_GOVERNED_FIXER_SCHEMA_ID, fixer),
        (_GOVERNED_REPAIR_PLAN_SCHEMA_ID, repair_plan),
    ):
        assert validate_schema(schemas[schema_id], payload).accepted
        corrupted = deepcopy(payload)
        corrupted["unexpected"] = True
        assert not validate_schema(schemas[schema_id], corrupted).accepted
    bad_check = deepcopy(builder)
    cast(list[Record], bad_check["checks"])[0]["unexpected"] = True
    assert not validate_schema(schemas[_GOVERNED_BUILDER_SCHEMA_ID], bad_check).accepted


def test_semantic_lad_context_bindings_use_exact_stage_table_and_finite_limits(
) -> None:
    source = _codex_source(SEMANTIC_WORKFLOW_ID)
    bindings = _source_context_bindings(source)
    assert tuple(str(binding["stage_kind_id"]) for binding in bindings) == (
        _GOVERNED_CONTEXT_STAGES
    )
    assert len(bindings) == 6
    expected_required = {
        "lad_builder": {
            ("dispatch_material", "current"),
            ("workspace_relative_root", "millrace-agents/MILLRACE.md"),
            ("workspace_relative_root", "millrace-agents/shared/CONTEXT.md"),
            ("selected_artifacts", "direct_predecessors"),
        },
        "lad_checker": {
            ("dispatch_material", "current"),
            ("selected_artifacts", "direct_predecessors"),
        },
        "lad_fixer": {
            ("dispatch_material", "current"),
            ("selected_artifacts", "direct_predecessors"),
        },
        "lad_doublechecker": {
            ("selected_artifacts", "direct_predecessors"),
        },
        "lad_troubleshooter": {
            ("dispatch_material", "current"),
            ("selected_artifacts", "direct_predecessors"),
        },
        "lad_updater": {
            ("dispatch_material", "current"),
            ("selected_artifacts", "current_lineage"),
            ("workspace_relative_root", "millrace-agents/MILLRACE.md"),
            ("workspace_relative_root", "millrace-agents/shared/CONTEXT.md"),
            *(
                ("workspace_relative_root", root)
                for root in (
                    *_GOVERNED_CATALOG_ROOTS,
                    "docs",
                    "README.md",
                    "millrace-agents/shared/skills",
                )
            ),
        },
    }
    expected_discoverable = {
        "lad_builder": set(
            ("workspace_relative_root", root) for root in _GOVERNED_CATALOG_ROOTS
        ),
        "lad_checker": {
            ("workspace_relative_root", root)
            for root in (
                "millrace-agents/shared/conventions",
                "millrace-agents/shared/decisions",
                "millrace-agents/shared/references",
                "docs",
            )
        },
        "lad_fixer": {
            ("selected_attempts", "since_last_accepted_transition"),
            *(
                ("workspace_relative_root", root)
                for root in (
                    "millrace-agents/shared/conventions",
                    "millrace-agents/shared/decisions",
                    "millrace-agents/shared/references",
                )
            ),
        },
        "lad_doublechecker": {
            ("selected_attempts", "since_last_accepted_transition"),
            *(
                ("workspace_relative_root", root)
                for root in ("millrace-agents/shared/references", "docs")
            ),
        },
        "lad_troubleshooter": {
            ("selected_attempts", "since_last_accepted_transition"),
            *(
                ("workspace_relative_root", root)
                for root in (
                    "millrace-agents/shared/conventions",
                    "millrace-agents/shared/decisions",
                    "millrace-agents/shared/references",
                )
            ),
        },
        "lad_updater": {("selected_attempts", "current_lineage")},
    }
    for binding in bindings:
        stage = str(binding["stage_kind_id"])
        assert binding["router_asset_id"] == _CODEX_ROUTER_ASSET_ID
        assert binding["mutation_policy"] == (
            "reconcile_selected_writes"
            if stage == "lad_updater"
            else "forbid_selected_roots"
        )
        assert binding["materialization_retention"] == (
            "until_session_durable_terminal"
        )
        assert 0 < binding["max_hydrated_files"] <= 256
        assert 0 < binding["max_hydrated_bytes"] <= 4 * 1024 * 1024
        required = cast(list[Record], binding["required_sources"])
        discoverable = cast(list[Record], binding["discoverable_sources"])
        required_pairs = {
            (str(item["source_kind"]), str(item["source_ref"]))
            for item in required
        }
        assert required_pairs == expected_required[stage]
        if stage in expected_discoverable:
            discoverable_pairs = {
                (str(item["source_kind"]), str(item["source_ref"]))
                for item in discoverable
            }
            assert discoverable_pairs == expected_discoverable[stage]
        else:
            assert discoverable == []
        for item in (*required, *discoverable):
            assert item["source_kind"] != "catalog"
            assert 0 < item["max_files"] <= 256
            assert 0 < item["max_bytes"] <= 4 * 1024 * 1024
    updater = next(
        binding
        for binding in bindings
        if binding["stage_kind_id"] == "lad_updater"
    )
    builder = next(
        binding
        for binding in bindings
        if binding["stage_kind_id"] == "lad_builder"
    )
    builder_predecessors = next(
        source
        for source in cast(list[Record], builder["required_sources"])
        if source["source_kind"] == "selected_artifacts"
        and source["source_ref"] == "direct_predecessors"
    )
    assert builder_predecessors["empty_policy"] == "omit_if_absent"
    for binding in bindings:
        for source in (
            *cast(list[Record], binding["required_sources"]),
            *cast(list[Record], binding["discoverable_sources"]),
        ):
            if source is builder_predecessors:
                continue
            assert source.get("empty_policy", "require_nonempty") == (
                "require_nonempty"
            )
    assert updater["writeback_artifact_schema_id"] == _CODEX_CONTEXT_SCHEMA_ID
    assert updater["writeback_terminal_action_id"] == "execution.close_updater_complete"


def test_semantic_troubleshooter_direct_blocked_route_does_not_require_attempt(
) -> None:
    source = _codex_source(SEMANTIC_WORKFLOW_ID)
    blocked_route = _record(
        source, "terminal_actions", "execution.route_checker_blocked"
    )
    assert blocked_route["target_stage_kind_id"] == "lad_troubleshooter"

    binding = next(
        item
        for item in _source_context_bindings(source)
        if item["stage_kind_id"] == "lad_troubleshooter"
    )
    required = {
        (item["source_kind"], item["source_ref"])
        for item in cast(list[Record], binding["required_sources"])
    }
    discoverable = {
        (item["source_kind"], item["source_ref"])
        for item in cast(list[Record], binding["discoverable_sources"])
    }
    attempt_source = ("selected_attempts", "since_last_accepted_transition")
    assert attempt_source not in required
    assert attempt_source in discoverable


def test_semantic_normal_routes_never_require_optional_attempt_history() -> None:
    source = _codex_source(SEMANTIC_WORKFLOW_ID)
    normal_route_targets = {
        str(action["target_stage_kind_id"])
        for action in _records(source, "terminal_actions")
        if action["kind"] == "route" and action.get("target_stage_kind_id")
    }
    bindings = {
        str(binding["stage_kind_id"]): binding
        for binding in _source_context_bindings(source)
    }
    stages_with_attempt_context = {
        stage
        for stage, binding in bindings.items()
        if any(
            item["source_kind"] == "selected_attempts"
            for item in (
                *cast(list[Record], binding["required_sources"]),
                *cast(list[Record], binding["discoverable_sources"]),
            )
        )
    }
    assert stages_with_attempt_context == {
        "lad_fixer",
        "lad_doublechecker",
        "lad_troubleshooter",
        "lad_updater",
    }

    for stage in stages_with_attempt_context & normal_route_targets:
        required_kinds = {
            item["source_kind"]
            for item in cast(list[Record], bindings[stage]["required_sources"])
        }
        discoverable_kinds = {
            item["source_kind"]
            for item in cast(list[Record], bindings[stage]["discoverable_sources"])
        }
        assert "selected_attempts" not in required_kinds
        assert "selected_attempts" in discoverable_kinds


def test_mutating_stages_do_not_select_project_docs_as_immutable_context() -> None:
    bindings = {
        str(binding["stage_kind_id"]): binding
        for binding in _source_context_bindings(_codex_source(SEMANTIC_WORKFLOW_ID))
    }

    for stage in ("lad_builder", "lad_fixer", "lad_troubleshooter"):
        binding = bindings[stage]
        assert binding["mutation_policy"] == "forbid_selected_roots"
        selected_roots = {
            str(source["source_ref"])
            for source in (
                *cast(list[Record], binding["required_sources"]),
                *cast(list[Record], binding["discoverable_sources"]),
            )
            if source["source_kind"] == "workspace_relative_root"
        }
        assert "docs" not in selected_roots


def test_troubleshooter_reentry_uses_typed_conditions_and_exact_graph_targets() -> None:
    source = _codex_source(SEMANTIC_WORKFLOW_ID)
    expected_routes = {
        "execution.return_troubleshooter_complete": (
            "narrow_repair",
            False,
            "fixer",
            "lad_fixer",
            "execution.lad_codex_semantic_worktree.fixer.start",
        ),
        "execution.return_troubleshooter_baseline_invalidated": (
            "baseline_invalidated",
            True,
            "builder",
            "lad_builder",
            "execution.lad_codex_semantic_worktree.builder.start",
        ),
        "execution.return_troubleshooter_review_retry": (
            "unchanged_source_review_execution_failure",
            False,
            "checker",
            "lad_checker",
            "execution.lad_codex_semantic_worktree.checker.start",
        ),
    }
    for action_id, (
        classification,
        baseline_invalidated,
        reentry_stage,
        stage,
        graph_node,
    ) in expected_routes.items():
        action = _record(source, "terminal_actions", action_id)
        assert action["artifact_schema_id"] == _GOVERNED_REPAIR_PLAN_SCHEMA_ID
        assert "dynamic_target_selector" not in action
        assert action["kind"] == "route"
        assert action["target_stage_kind_id"] == stage
        assert action["target_graph_node_id"] == graph_node
        assert action["emitted_queue_family_id"] == "stage_result"
        assert action["runner_binding_id"] == f"{stage}.codex_runner"
        assert action["artifact_field_conditions"] == {
            "failure_classification": classification,
            "baseline_invalidated": baseline_invalidated,
            "reentry_stage": reentry_stage,
        }

    repair_schema = _record(
        source, "artifact_schemas", _GOVERNED_REPAIR_PLAN_SCHEMA_ID
    )["schema"]
    failure_classification = cast(
        Record, cast(Record, repair_schema)["properties"]
    )["failure_classification"]
    assert failure_classification["enum"] == [
        "narrow_repair",
        "baseline_invalidated",
        "unchanged_source_review_execution_failure",
        "unrecoverable",
    ]
    assert cast(Record, cast(Record, repair_schema)["properties"])[
        "baseline_invalidated"
    ]["type"] == "boolean"
    assert cast(Record, cast(Record, repair_schema)["properties"])[
        "reentry_stage"
    ]["enum"] == ["fixer", "builder", "checker", "none"]

    blocked = _record(
        source,
        "terminal_actions",
        "execution.route_troubleshooter_blocked",
    )
    assert blocked["kind"] == "block_work_item"
    assert blocked["artifact_field_conditions"] == {
        "failure_classification": "unrecoverable",
        "baseline_invalidated": False,
        "reentry_stage": "none",
    }
    assert "target_graph_node_id" not in blocked
    assert "runner_binding_id" not in blocked
    outcomes = {
        str(outcome["marker"]): str(outcome["id"])
        for outcome in cast(list[Record], source["terminal_outcomes"])
        if outcome["stage_kind_id"] == "lad_troubleshooter"
    }
    assert outcomes["TROUBLESHOOT_NARROW_REPAIR"] == (
        "execution.lad_troubleshooter.complete"
    )
    assert outcomes["TROUBLESHOOT_BASELINE_INVALIDATED"] == (
        "execution.lad_troubleshooter.baseline_invalidated"
    )
    assert outcomes["TROUBLESHOOT_REVIEW_RETRY"] == (
        "execution.lad_troubleshooter.review_retry"
    )
    assert outcomes["TROUBLESHOOT_UNRECOVERABLE"] == (
        "execution.lad_troubleshooter.blocked"
    )
    assert (
        _record(
            source,
            "terminal_actions",
            "execution.close_troubleshooter_runtime_failure_exhausted",
        )["kind"]
        == "block_work_item"
    )

    semantic_assets = conformance.asset_texts(
        PACKAGE_ROOT,
        _manifest(),
        {
            _CODEX_ROUTER_ASSET_ID,
            *_CODEX_ENTRYPOINT_ASSET_IDS.values(),
            _CODEX_UPDATER_SKILL_ASSET_ID,
        },
    )
    troubleshooter_assets = conformance.asset_texts(
        PACKAGE_ROOT,
        _manifest(),
        {
            "execution.entrypoints.lad_troubleshooter",
            "execution.skills.troubleshooter_core",
        },
    )
    prompts = "\n".join(
        (*semantic_assets.values(), *troubleshooter_assets.values())
    ).lower()
    assert "named" in prompts
    assert "on demand" in prompts
    assert "review execution failure" in prompts
    assert "baseline_invalidated" in prompts
    assert "narrow repair" in prompts
    assert "unrecoverable" in prompts
    assert "read the entire catalog" not in prompts
    assert "read all catalog" not in prompts
