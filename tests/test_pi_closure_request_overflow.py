"""Pi closure request overflow authority for the private lad.full.pi workflow.

Offline compile and prompt-shape checks only. The tests use the packaged Plus
source, the real arbiter assets, and the Core prompt builder. They start no
runtime, provider, or Pi process, and they write no files.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from millrace.adapters.pi_rpc import prompt_for_request
from millrace.adapters.runner_contract import AdapterInvocationRequest, RedactionPolicy
from millrace.compiler import compile_workflow
from millrace.contracts.compiled_plan import (
    CompletionBehaviorDeclaration,
    CompletionBehaviorWithRequestOverflowDeclaration,
)
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.transition import canonical_authority_mapping_bytes

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PI_WORKFLOW_ID = "lad.full.pi"
PLANNING_WORKFLOW_ID = "planning.lad"
FULL_WORKFLOW_ID = "lad.full"
ARBITER_STAGE_ID = "lad_arbiter"
ARBITER_BINDING_ID = "lad_arbiter.millforge_runner"
ARBITER_ENTRY_ID = "lad.full.pi.entrypoints.lad_arbiter"
ARBITER_SKILL_ID = "planning.skills.arbiter_core"
VERDICT_SCHEMA_ID = "planning.artifacts.verdict"
INLINE_BODY_BYTES = 131072
PROMPT_BOUND_BYTES = 262144
OVERFLOW_DECLARATION = {
    "policy": "materialized_evidence_pages",
    "materialization_root": "millrace-closure-evidence",
    "page_byte_limit": 32768,
    "max_snapshot_bytes": 8388608,
    "max_page_count": 1024,
}
ARBITER_OPTIONS = (
    {
        "outcome_id": "planning.lad_arbiter.complete",
        "marker": "ARBITER_COMPLETE",
        "action_id": "planning.close_arbiter_complete",
        "action_kind": "complete_work_item",
        "artifact_schema_id": VERDICT_SCHEMA_ID,
    },
    {
        "outcome_id": "planning.lad_arbiter.remediation_needed",
        "marker": "REMEDIATION_NEEDED",
        "action_id": "planning.closure_gap",
        "action_kind": "closure_gap",
        "artifact_schema_id": VERDICT_SCHEMA_ID,
    },
    {
        "outcome_id": "planning.lad_arbiter.blocked",
        "marker": "BLOCKED",
        "action_id": "planning.close_arbiter_blocked",
        "action_kind": "block_work_item",
        "artifact_schema_id": VERDICT_SCHEMA_ID,
    },
    {
        "outcome_id": "planning.lad_arbiter.runtime_failed",
        "marker": "RUNTIME_FAILURE",
        "action_id": "planning.recover_arbiter_runtime_failure",
        "action_kind": "recovery_route",
        "artifact_schema_id": None,
    },
)
PROMPT_SECTION_KEYS = frozenset(
    {
        "instructions",
        "work_item_payload",
        "governance_context",
        "selected_join_evidence",
        "selected_wait_evidence",
        "context_checkout",
        "entrypoint_asset_id",
        "skill_asset_ids",
        "selected_assets",
        "terminal_options",
        "artifact_schemas",
    }
)


def _source(workflow_id: str) -> dict[str, Any]:
    return conformance.packaged_workflow_source(
        PACKAGE_ROOT, workflow_id, include_pi=workflow_id == PI_WORKFLOW_ID
    )


def _compile_result(source: dict[str, Any]) -> Any:
    return compile_workflow(source)


def _compile(source: dict[str, Any]) -> Any:
    result = _compile_result(source)
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert errors == []
    assert result.plan is not None
    return result.plan


def _behavior(source: dict[str, Any]) -> dict[str, Any]:
    behaviors = source["completion_behaviors"]
    assert len(behaviors) == 1
    return behaviors[0]


def _overflow(source: dict[str, Any]) -> dict[str, Any]:
    return _behavior(source)["request_overflow"]


def _arbiter_terminal_options(source: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Derive the arbiter options from the source graph, not from hand-typed ids."""

    options: list[dict[str, Any]] = []
    for outcome in source["terminal_outcomes"]:
        if outcome["stage_kind_id"] != ARBITER_STAGE_ID:
            continue
        matched = [
            a for a in source["terminal_actions"] if a["outcome_id"] == outcome["id"]
        ]
        assert len(matched) == 1
        action = matched[0]
        options.append(
            {
                "outcome_id": outcome["id"],
                "marker": outcome["marker"],
                "action_id": action["id"],
                "action_kind": action["kind"],
                "artifact_schema_id": action.get("artifact_schema_id"),
            }
        )
    return tuple(options)


@pytest.fixture(scope="module")
def arbiter_inputs() -> dict[str, Any]:
    source = _source(PI_WORKFLOW_ID)
    plan = _compile(copy.deepcopy(source))
    schemas = tuple(s for s in plan.artifact_schemas if str(s.id) == VERDICT_SCHEMA_ID)
    assert len(schemas) == 1
    assets = {asset["id"]: asset["body"] for asset in source["assets"]}
    return {
        "options": _arbiter_terminal_options(source),
        "schemas": schemas,
        "material": {
            ARBITER_ENTRY_ID: assets[ARBITER_ENTRY_ID],
            ARBITER_SKILL_ID: assets[ARBITER_SKILL_ID],
        },
    }


def _body(filler: str) -> dict[str, Any]:
    return {
        "closure_evidence_overflow": {
            "closure_target_id": "target-p2-overflow-test",
            "root_contract_digest": "sha256:" + "1" * 64,
            "freshness_anchor_digest": "sha256:" + "2" * 64,
            "index_pages": ["millrace-closure-evidence/index-0001.json"],
        },
        "padding": filler,
    }


def _padded_body(unit: str) -> dict[str, Any]:
    """Return a body whose canonical authority bytes are exactly the inline limit."""

    room = INLINE_BODY_BYTES - len(canonical_authority_mapping_bytes(_body("")))
    unit_bytes = len(unit.encode("utf-8"))
    count, tail = divmod(room, unit_bytes)
    body = _body(unit * count + "a" * tail)
    assert len(canonical_authority_mapping_bytes(body)) == INLINE_BODY_BYTES
    return body


def _dispatch(
    body: dict[str, Any], options: tuple[dict[str, Any], ...]
) -> RunnerDispatchEnvelope:
    # Identifiers other than the stage, binding, and asset fields are synthetic.
    # prompt_for_request reads only the dispatch fields listed in the prompt.
    return RunnerDispatchEnvelope(
        run_id="run-p2-overflow-test",
        session_id="session-p2-overflow-test",
        dispatch_generation=1,
        session_fencing_token="fence-p2-overflow-test",
        work_item_id="work-p2-overflow-test",
        activation_id="activation-p2-overflow-test",
        plan_fingerprint="sha256:" + "0" * 64,
        plan_id="plan-p2-overflow-test",
        workflow_id=PI_WORKFLOW_ID,
        workflow_version="0.1",
        graph_id="planning.lad-p2-overflow-test",
        claim_id="claim-p2-overflow-test",
        generation=1,
        fencing_token="work-fence-p2-overflow-test",
        queue_family_id="stage_result",
        stage_kind_id=ARBITER_STAGE_ID,
        graph_node_id="planning.lad.arbiter-p2-overflow-test",
        runner_binding_id=ARBITER_BINDING_ID,
        external_enqueue_route_id=None,
        entrypoint_asset_id=ARBITER_ENTRY_ID,
        skill_asset_ids=(ARBITER_SKILL_ID,),
        artifact_schema_ids=(VERDICT_SCHEMA_ID,),
        work_item_payload=body,
        terminal_options=options,
    )


def _request(
    body: dict[str, Any],
    inputs: dict[str, Any],
) -> AdapterInvocationRequest:
    dispatch = _dispatch(body, inputs["options"])
    return AdapterInvocationRequest(
        adapter_id="pi_rpc",
        selected_runner_binding_id=ARBITER_BINDING_ID,
        selected_adapter_kind="pi_rpc",
        dispatch_envelope=dispatch,
        session_id=dispatch.session_id,
        dispatch_generation=dispatch.dispatch_generation,
        session_fencing_token=dispatch.session_fencing_token,
        timeout_seconds=900.0,
        correlation_id="corr-p2-overflow-test",
        redaction_policy=RedactionPolicy(
            policy_id="p2-overflow-redaction", secret_tokens=()
        ),
        selected_asset_material=inputs["material"],
        selected_artifact_schemas=inputs["schemas"],
    )


# ---------------------------------------------------------------- compile authority


def test_lad_full_pi_compiles_exact_overflow_authority() -> None:
    plan = _compile(_source(PI_WORKFLOW_ID))
    behavior = plan.completion_behaviors[0]

    assert isinstance(behavior, CompletionBehaviorWithRequestOverflowDeclaration)
    assert behavior.schema_version == 3
    assert behavior.request_payload_byte_limit == 131072
    assert behavior.evidence_item_limit == 96
    assert dataclasses.asdict(behavior.request_overflow) == OVERFLOW_DECLARATION


@pytest.mark.parametrize(
    ("workflow_id", "evidence_item_limit"),
    [(PLANNING_WORKFLOW_ID, 64), (FULL_WORKFLOW_ID, 96)],
)
def test_planning_lad_and_lad_full_keep_schema_2_and_inline_16384(
    workflow_id: str, evidence_item_limit: int
) -> None:
    plan = _compile(_source(workflow_id))
    behavior = plan.completion_behaviors[0]

    assert type(behavior) is CompletionBehaviorDeclaration
    assert behavior.schema_version == 2
    assert behavior.request_payload_byte_limit == 16384
    assert behavior.evidence_item_limit == evidence_item_limit
    assert not hasattr(behavior, "request_overflow")


def test_arbiter_terminal_options_are_exact_source_linkage() -> None:
    assert _arbiter_terminal_options(_source(PI_WORKFLOW_ID)) == ARBITER_OPTIONS


# Each mutation must be refused by the compiler with the named predicate reason.
def _set(key: str, value: object) -> Any:
    def mutate(source: dict[str, Any]) -> None:
        _overflow(source)[key] = value

    return mutate


def _replace_overflow(value: object) -> Any:
    def mutate(source: dict[str, Any]) -> None:
        _behavior(source)["request_overflow"] = value

    return mutate


def _extra_key(source: dict[str, Any]) -> None:
    _overflow(source)["unexpected_key"] = True


def _set_request_limit(value: object) -> Any:
    def mutate(source: dict[str, Any]) -> None:
        _behavior(source)["request_payload_byte_limit"] = value

    return mutate


REFUSAL_CASES = [
    pytest.param(_extra_key, "request_overflow_fields", id="extra-key"),
    pytest.param(
        _replace_overflow("materialized_evidence_pages"),
        "request_overflow_shape",
        id="non-mapping-shape",
    ),
    pytest.param(
        _set("policy", "inline_pages"), "request_overflow_policy", id="policy"
    ),
    pytest.param(
        _set("materialization_root", "../escape-root"),
        "request_overflow_materialization_root",
        id="unsafe-materialization-root",
    ),
    pytest.param(
        _set("page_byte_limit", 4095),
        "request_overflow_page_byte_limit",
        id="page-below-min",
    ),
    pytest.param(
        _set("page_byte_limit", 1048577),
        "request_overflow_page_byte_limit",
        id="page-above-max",
    ),
    pytest.param(
        _set_request_limit(4096),
        "request_overflow_page_byte_limit",
        id="page-above-request-limit",
    ),
    pytest.param(
        _set("max_snapshot_bytes", 131072),
        "request_overflow_max_snapshot_bytes",
        id="snapshot-at-request-limit",
    ),
    pytest.param(
        _set("max_snapshot_bytes", 67108865),
        "request_overflow_max_snapshot_bytes",
        id="snapshot-above-ceiling",
    ),
    pytest.param(
        _set("max_page_count", 0), "request_overflow_max_page_count", id="pages-zero"
    ),
    pytest.param(
        _set("max_page_count", 16385),
        "request_overflow_max_page_count",
        id="pages-above-ceiling",
    ),
]


@pytest.mark.parametrize(("mutate", "reason"), REFUSAL_CASES)
def test_invalid_request_overflow_variants_are_refused_by_compiler(
    mutate: Any, reason: str
) -> None:
    source = _source(PI_WORKFLOW_ID)
    mutate(source)

    result = _compile_result(source)

    assert result.plan is None
    matches = [
        d
        for d in result.diagnostics
        if d.severity == "error"
        and d.code == "invalid_completion_behavior_declaration"
        and d.context.get("reason") == reason
    ]
    assert matches, [(d.code, d.context) for d in result.diagnostics]
    assert matches[0].declaration_path == "completion_behaviors[0].request_overflow"


@pytest.mark.parametrize(
    "bad_limit", [0, -1, "131072"], ids=["zero", "negative", "string"]
)
def test_invalid_request_limit_is_refused_at_its_own_path(bad_limit: object) -> None:
    # A non-positive or non-int limit is reported first at its own path. The
    # request_overflow_request_limit predicate reason is therefore unreachable
    # through compile_workflow; it is documented here, not exercised.
    source = _source(PI_WORKFLOW_ID)
    _behavior(source)["request_payload_byte_limit"] = bad_limit

    result = _compile_result(source)

    assert result.plan is None
    matches = [
        d
        for d in result.diagnostics
        if d.severity == "error"
        and d.declaration_path == "completion_behaviors[0].request_payload_byte_limit"
        and d.context.get("reason") == "invalid_request_payload_byte_limit"
    ]
    assert matches, [(d.declaration_path, d.context) for d in result.diagnostics]


# ---------------------------------------------------------------- prompt bound


@pytest.mark.parametrize(
    ("unit", "multibyte"),
    [("x", False), ("証拠✓é", True)],
    ids=["ascii-body", "unicode-heavy-body"],
)
def test_inline_limit_body_keeps_pi_prompt_within_bound(
    arbiter_inputs: dict[str, Any], unit: str, multibyte: bool
) -> None:
    body = _padded_body(unit)
    canonical_body = canonical_authority_mapping_bytes(body)
    assert len(canonical_body) == INLINE_BODY_BYTES
    assert (len(canonical_body.decode("utf-8")) < INLINE_BODY_BYTES) is multibyte

    request = _request(body, arbiter_inputs)
    prompt = prompt_for_request(request)
    prompt_bytes = prompt.encode("utf-8")

    assert len(prompt_bytes) <= PROMPT_BOUND_BYTES
    # The work-item section is embedded with the same serializer, so the
    # canonical body bytes appear verbatim inside the prompt.
    assert canonical_body in prompt_bytes

    sections = json.loads(prompt)
    assert frozenset(sections) == PROMPT_SECTION_KEYS
    assert sections["work_item_payload"] == body
    assert sections["selected_assets"] == arbiter_inputs["material"]
    assert sections["entrypoint_asset_id"] == ARBITER_ENTRY_ID
    assert sections["skill_asset_ids"] == [ARBITER_SKILL_ID]
    assert sections["terminal_options"] == [dict(option) for option in ARBITER_OPTIONS]
    assert list(sections["artifact_schemas"]) == [VERDICT_SCHEMA_ID]
    assert sections["instructions"].startswith("Return only one complete JSON object")
