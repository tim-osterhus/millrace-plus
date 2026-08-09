from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from millrace.compiler import (
    authority_fingerprint,
    canonical_authority_bytes,
    compile_workflow,
    compiled_plan_export_bytes,
    verify_compiled_plan_export_bytes,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.2"
WORKFLOW_ID = "vendor_selection"
Record = dict[str, object]


def _manifest() -> dict[str, Any]:
    return conformance.assert_packaged_asset_closure(PACKAGE_ROOT)


def _source() -> dict[str, object]:
    return conformance.packaged_workflow_source(PACKAGE_ROOT, WORKFLOW_ID)


def _records(source: dict[str, object], section: str) -> list[Record]:
    return cast(list[Record], source[section])


def _record(source: dict[str, object], section: str, record_id: str) -> Record:
    return next(item for item in _records(source, section) if item["id"] == record_id)


def _compile_errors(source: dict[str, object]) -> tuple[object, ...]:
    return tuple(
        diagnostic
        for diagnostic in compile_workflow(source).diagnostics
        if diagnostic.severity == "error"
    )


def _json_block_after(text: str, heading: str) -> object:
    section = text.split(heading, maxsplit=1)[1]
    json_block = section.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return json.loads(json_block)


def _schema_accepts(schema: Mapping[str, object], payload: object) -> bool:
    if "const" in schema and not _same_scalar(payload, schema["const"]):
        return False
    enum = schema.get("enum")
    if isinstance(enum, (list, tuple)) and not any(
        _same_scalar(payload, item) for item in enum
    ):
        return False

    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(payload, Mapping):
            return False
        required = schema.get("required", ())
        if isinstance(required, (list, tuple)) and any(
            field not in payload for field in required
        ):
            return False
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return False
        additional = schema.get("additionalProperties", False)
        for field, value in payload.items():
            field_schema = properties.get(field)
            if isinstance(field_schema, Mapping):
                if not _schema_accepts(field_schema, value):
                    return False
            elif additional is True:
                continue
            elif isinstance(additional, Mapping):
                if not _schema_accepts(additional, value):
                    return False
            else:
                return False
        return True
    if schema_type == "array":
        if not isinstance(payload, (list, tuple)):
            return False
        min_items = schema.get("min_items")
        if isinstance(min_items, int) and len(payload) < min_items:
            return False
        item_schema = schema.get("items")
        return not isinstance(item_schema, Mapping) or all(
            _schema_accepts(item_schema, item) for item in payload
        )
    if schema_type == "string":
        min_length = schema.get("min_length")
        return isinstance(payload, str) and (
            not isinstance(min_length, int) or len(payload) >= min_length
        )
    if schema_type == "integer":
        return type(payload) is int
    if schema_type == "boolean":
        return type(payload) is bool
    if schema_type == "null":
        return payload is None
    return schema_type is None


def _same_scalar(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def test_plus_local_schema_validator_covers_selected_closed_subset() -> None:
    schema = {
        "type": "object",
        "required": ("kind", "labels", "count", "enabled", "empty"),
        "properties": {
            "kind": {"const": "selected"},
            "labels": {
                "type": "array",
                "min_items": 1,
                "items": {"enum": ("alpha", "beta")},
            },
            "count": {"type": "integer"},
            "enabled": {"type": "boolean"},
            "empty": {"type": "null"},
        },
    }
    valid = {
        "kind": "selected",
        "labels": ["alpha"],
        "count": 1,
        "enabled": True,
        "empty": None,
    }
    assert _schema_accepts(schema, valid)
    assert not _schema_accepts(schema, {**valid, "machine_derived": "extra"})
    missing_required = dict(valid)
    missing_required.pop("kind")
    assert not _schema_accepts(schema, missing_required)
    assert not _schema_accepts(schema, {**valid, "labels": ["unknown"]})
    assert not _schema_accepts(schema, {**valid, "kind": "other"})

    typed_extras = {
        "type": "object",
        "properties": {},
        "additionalProperties": {"type": "string"},
    }
    assert _schema_accepts(typed_extras, {"note": "allowed"})
    assert not _schema_accepts(typed_extras, {"note": 1})


def test_vendor_selection_authority_and_assets_are_package_owned() -> None:
    manifest = _manifest()
    workflow = conformance.workflows_by_id(manifest)[WORKFLOW_ID]
    selected = cast(dict[str, object], workflow["selected_authority"])

    assert workflow["workflow_version"] == "0.1"
    assert len(cast(list[object], selected["stage_kinds"])) == 9
    assert len(cast(list[object], workflow["required_assets"])) == 18
    assert "assets" not in selected


def test_vendor_selection_selects_through_installed_public_api(tmp_path: Path) -> None:
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


def test_vendor_selection_assets_keep_decision_and_operator_wait_boundaries() -> None:
    manifest = _manifest()
    asset_ids = {
        asset_id
        for asset_id in conformance.assets_by_id(manifest)
        if asset_id.startswith("vendor_selection.")
    }
    texts = conformance.asset_texts(PACKAGE_ROOT, manifest, asset_ids)
    award = texts["vendor_selection.skills.award_decider_core"]
    decision = texts["vendor_selection.skills.decision_packager_core"]
    assert "vendor_selection.award_operator_wait" in award
    assert "operator_required" in award
    assert "DecisionPack" in decision
    conformance.assert_no_runtime_authority_claims(texts)


def test_vendor_selection_compiled_topology_route_and_runners_are_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    assert plan.lineage_policy == "root_from_external_enqueue"
    assert {str(partition.id) for partition in plan.partitions} == {
        "requirements",
        "sourcing",
        "evaluation",
        "authorization",
    }
    assert plan.graphs[0].node_ids == tuple(
        f"vendor_selection.{stage}.start"
        for stage in (
            "request_intake",
            "policy_screener",
            "requirement_freezer",
            "catalog_sourcer",
            "candidate_packager",
            "rubric_evaluator",
            "conflict_checker",
            "award_decider",
            "decision_packager",
        )
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
        "vendor_selection.purchase_request",
        "purchase_request",
        "vendor_selection.request_intake.start",
        "request_intake",
        "request_intake.millforge_runner",
        "PurchaseRequest",
    )
    assert {
        str(stage.id): (
            str(stage.partition_id),
            str(stage.runner_binding_id),
            tuple(str(asset_id) for asset_id in stage.asset_ids),
        )
        for stage in plan.stage_kinds
    } == {
        stage: (
            partition,
            f"{stage}.millforge_runner",
            (
                f"vendor_selection.entrypoints.{stage}",
                f"vendor_selection.skills.{stage}_core",
            ),
        )
        for partition, stages in {
            "requirements": (
                "request_intake",
                "policy_screener",
                "requirement_freezer",
            ),
            "sourcing": ("catalog_sourcer", "candidate_packager"),
            "evaluation": ("rubric_evaluator", "conflict_checker"),
            "authorization": ("award_decider", "decision_packager"),
        }.items()
        for stage in stages
    }
    assert all(
        runner.adapter_kind == "millforge"
        and runner.component_pin is not None
        and runner.component_pin.provider_distribution == "millforge"
        for runner in plan.runner_bindings
    )


def test_vendor_selection_terminal_actions_and_handoff_schemas_are_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    actions = {str(action.id): action for action in plan.terminal_actions}
    assert len(actions) == 16
    assert {action.action_kind for action in actions.values()} == {
        "route",
        "close",
        "complete_work_item",
        "operator_wait",
    }
    expected = {
        "vendor_selection.request_intake.request_ready": (
            "route",
            "policy_screener",
            "purchase_request",
            "PurchaseRequest",
        ),
        "vendor_selection.policy_screener.policy_allowed": (
            "route",
            "requirement_freezer",
            "purchase_request",
            "PurchaseRequest",
        ),
        "vendor_selection.requirement_freezer.requirements_ready": (
            "route",
            "catalog_sourcer",
            "requirement_packet",
            "RequirementPacket",
        ),
        "vendor_selection.catalog_sourcer.candidates_ready": (
            "route",
            "candidate_packager",
            "candidate_bundle",
            "CandidateBundle",
        ),
        "vendor_selection.award_decider.award_ready": (
            "route",
            "decision_packager",
            "authorization_decision",
            "AwardDecision",
        ),
        "vendor_selection.award_decider.operator_required": (
            "operator_wait",
            None,
            None,
            "AwardDecision",
        ),
        "vendor_selection.decision_packager.decision_pack_ready": (
            "complete_work_item",
            None,
            None,
            "DecisionPack",
        ),
    }
    for action_id, selected in expected.items():
        action = actions[action_id]
        assert (
            action.action_kind,
            None
            if action.target_stage_kind_id is None
            else str(action.target_stage_kind_id),
            None
            if action.emitted_queue_family_id is None
            else str(action.emitted_queue_family_id),
            str(action.artifact_schema_id),
        ) == selected

    schemas = {str(schema.id): schema.schema for schema in plan.artifact_schemas}
    assert set(schemas) == {
        "PurchaseRequest",
        "PolicyDecision",
        "RequirementPacket",
        "CandidateBundle",
        "RubricReport",
        "ConflictReport",
        "AwardDecision",
        "OperatorDecision",
        "DecisionPack",
    }
    assert tuple(schemas["PurchaseRequest"]["required"]) == (
        "request_id",
        "requester_label",
        "category",
        "budget_band",
        "required_capabilities",
        "disallowed_vendors",
        "approval_policy_hint",
    )
    award_candidate = schemas["AwardDecision"]["properties"]["selected_candidate_id"]
    assert None in award_candidate["enum"]


def test_vendor_selection_award_identity_is_required_and_examples_conform() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    schemas = {str(schema.id): schema.schema for schema in plan.artifact_schemas}
    award_schema = schemas["AwardDecision"]
    assert tuple(award_schema["required"])[0] == "source_request_id"
    assert award_schema["properties"]["source_request_id"] == {
        "type": "string",
        "min_length": 1,
    }

    award_text = (
        PACKAGE_ROOT
        / "assets/workflows/vendor_selection/skills/award_decider-core.md"
    ).read_text()
    examples = cast(
        list[dict[str, object]],
        _json_block_after(award_text, "## Valid Example"),
    )
    award_examples = [
        cast(dict[str, object], example["artifact"])
        for example in examples
        if example["terminal_marker"] in {"AWARD_READY", "OPERATOR_REQUIRED"}
    ]
    assert len(award_examples) == 2
    assert all(_schema_accepts(award_schema, example) for example in award_examples)

    missing_identity = dict(award_examples[0])
    missing_identity.pop("source_request_id")
    assert not _schema_accepts(award_schema, missing_identity)


def test_vendor_selection_identity_propagation_is_exact_and_lookup_free() -> None:
    manifest = _manifest()
    texts = conformance.asset_texts(
        PACKAGE_ROOT,
        manifest,
        {
            "vendor_selection.entrypoints.catalog_sourcer",
            "vendor_selection.skills.catalog_sourcer_core",
            "vendor_selection.entrypoints.award_decider",
            "vendor_selection.skills.award_decider_core",
        },
    )
    catalog = "\n".join(
        text for asset_id, text in texts.items() if "catalog_sourcer" in asset_id
    )
    award = "\n".join(
        text for asset_id, text in texts.items() if "award_decider" in asset_id
    )

    assert (
        "`CandidateBundle.source_requirement_id` must exactly equal "
        "`RequirementPacket.source_request_id`" in catalog
    )
    assert (
        "`AwardDecision.source_request_id` must exactly equal "
        "`CandidateBundle.source_requirement_id`" in award
    )
    assert "Do not look up or reconstruct this identity" in catalog
    assert "Do not look up or reconstruct this identity" in award


def test_vendor_selection_valid_examples_propagate_one_exact_source_id() -> None:
    skills_root = PACKAGE_ROOT / "assets/workflows/vendor_selection/skills"

    def examples(skill_name: str) -> list[dict[str, object]]:
        return cast(
            list[dict[str, object]],
            _json_block_after(
                (skills_root / skill_name).read_text(),
                "## Valid Example",
            ),
        )

    requirement = next(
        cast(dict[str, object], example["artifact"])
        for example in examples("requirement_freezer-core.md")
        if example["terminal_marker"] == "REQUIREMENTS_READY"
    )
    bundle = next(
        cast(dict[str, object], example["artifact"])
        for example in examples("catalog_sourcer-core.md")
        if example["terminal_marker"] == "CANDIDATES_READY"
    )
    awards = {
        str(example["terminal_marker"]): cast(dict[str, object], example["artifact"])
        for example in examples("award_decider-core.md")
        if example["terminal_marker"] in {"AWARD_READY", "OPERATOR_REQUIRED"}
    }

    source_id = requirement["source_request_id"]
    assert source_id == "e2e-vendor-selection-001"
    assert bundle["source_requirement_id"] == source_id
    assert awards["AWARD_READY"]["source_request_id"] == source_id
    assert awards["OPERATOR_REQUIRED"]["source_request_id"] == source_id


def test_vendor_selection_wait_projection_and_plan_admission_are_exact(
    tmp_path: Path,
) -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    wait = plan.operator_waits[0]
    assert plan.schema_version == 16
    assert wait.schema_version == 2
    assert wait.project_source_artifact is True

    selected = conformance.select_and_verify_package(
        tmp_path,
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version="0.1",
    )
    assert selected.schema_version == 16
    assert selected.operator_waits[0].project_source_artifact is True


def test_vendor_selection_decision_packager_truth_table_is_exact() -> None:
    decision_text = (
        PACKAGE_ROOT
        / "assets/workflows/vendor_selection/skills/decision_packager-core.md"
    ).read_text()
    cases = cast(
        list[dict[str, object]],
        _json_block_after(decision_text, "## Normative Decision Mapping"),
    )
    by_case = {str(case["case"]): case for case in cases}
    assert set(by_case) == {
        "catalog_no_viable_vendor_pass_through",
        "award_no_viable_vendor_pass_through",
        "award_blocked_pass_through",
        "direct_award",
        "revised_approve",
        "revised_reject",
        "gate_mismatch",
        "bundle_mismatch",
        "source_award_mismatch",
    }

    direct = by_case["direct_award"]
    assert direct["selected_input"] == "work_item_payload AwardDecision only"
    assert direct["selected_wait_evidence"] is None
    direct_source = cast(dict[str, object], direct["source_award"])
    assert direct_source["decision_kind"] == "award"
    assert direct_source["operator_gate_required"] is False

    awarded_result = {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "selected_candidate_id": "vendor_alpha",
        "final_refusal_reason": None,
        "evidence_refs": {
            "rubric_report_ref": "rubric-001",
            "conflict_report_ref": "conflict-001",
        },
        "selected_plan_id": "vendor_selection:0.1",
        "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
        "close_reason": "awarded",
    }

    approve = by_case["revised_approve"]
    reject = by_case["revised_reject"]
    for revised in (approve, reject):
        assert revised["required_identity_match"] == {
            "operator_gate_id_equals": "selected_wait_evidence.operator_wait_id",
            "operator_bundle_id_equals": (
                "selected_wait_evidence.source_artifact_payload.bundle_id"
            ),
        }
        wait_evidence = cast(dict[str, object], revised["selected_wait_evidence"])
        assert wait_evidence["operator_wait_id"] == (
            "vendor_selection.award_operator_wait"
        )
        source_award = cast(
            dict[str, object], wait_evidence["source_artifact_payload"]
        )
        assert source_award["decision_kind"] == "operator_required"
        assert source_award["operator_gate_required"] is True
        operator_decision = cast(dict[str, object], revised["operator_decision"])
        assert operator_decision["gate_id"] == wait_evidence["operator_wait_id"]
        assert operator_decision["bundle_id"] == source_award["bundle_id"]
    assert cast(dict[str, object], approve["operator_decision"])["decision"] == (
        "approve"
    )

    for successful in (direct, approve):
        source_award = cast(
            dict[str, object],
            successful["source_award"]
            if successful is direct
            else cast(dict[str, object], successful["selected_wait_evidence"])[
                "source_artifact_payload"
            ],
        )
        result = cast(dict[str, object], successful["result"])
        assert result == awarded_result
        assert result["source_request_id"] == source_award["source_request_id"]
        assert result["bundle_id"] == source_award["bundle_id"]
        assert result["selected_candidate_id"] == source_award[
            "selected_candidate_id"
        ]
        assert result["evidence_refs"] == source_award["required_evidence_refs"]

    assert cast(dict[str, object], reject["result"]) == {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "selected_candidate_id": None,
        "final_refusal_reason": "operator_rejected",
        "evidence_refs": {
            "rubric_report_ref": "rubric-001",
            "conflict_report_ref": "conflict-001",
        },
        "selected_plan_id": "vendor_selection:0.1",
        "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
        "close_reason": "operator_rejected",
    }

    blocked_result = {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "selected_candidate_id": None,
        "final_refusal_reason": "blocked",
        "evidence_refs": {
            "rubric_report_ref": "rubric-001",
            "conflict_report_ref": "conflict-001",
        },
        "selected_plan_id": "vendor_selection:0.1",
        "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
        "close_reason": "blocked",
    }
    for mismatch in ("gate_mismatch", "bundle_mismatch", "source_award_mismatch"):
        assert by_case[mismatch]["result"] == blocked_result
        assert by_case[mismatch]["terminal_marker"] == "DECISION_PACK_READY"
    assert cast(dict[str, object], by_case["gate_mismatch"]["operator_decision"])[
        "gate_id"
    ] != cast(dict[str, object], by_case["gate_mismatch"]["selected_wait_evidence"])[
        "operator_wait_id"
    ]
    bundle_mismatch = by_case["bundle_mismatch"]
    bundle_source = cast(
        dict[str, object],
        cast(dict[str, object], bundle_mismatch["selected_wait_evidence"])[
            "source_artifact_payload"
        ],
    )
    assert cast(dict[str, object], bundle_mismatch["operator_decision"])[
        "bundle_id"
    ] != bundle_source["bundle_id"]
    source_mismatch = cast(
        dict[str, object],
        cast(dict[str, object], by_case["source_award_mismatch"][
            "selected_wait_evidence"
        ])["source_artifact_payload"],
    )
    assert (
        source_mismatch["decision_kind"] != "operator_required"
        or source_mismatch["operator_gate_required"] is not True
    )

    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    decision_schema = next(
        schema.schema
        for schema in plan.artifact_schemas
        if str(schema.id) == "DecisionPack"
    )
    assert all(_schema_accepts(decision_schema, case["result"]) for case in cases)


def test_vendor_selection_existing_decision_pack_routes_are_exact_pass_throughs() -> (
    None
):
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    actions = {str(action.id): action for action in plan.terminal_actions}
    expected_routes = {
        "vendor_selection.catalog_sourcer.no_viable_vendor",
        "vendor_selection.award_decider.no_viable_vendor",
        "vendor_selection.award_decider.blocked",
    }
    for action_id in expected_routes:
        action = actions[action_id]
        assert action.action_kind == "route"
        assert str(action.target_stage_kind_id) == "decision_packager"
        assert str(action.artifact_schema_id) == "DecisionPack"

    decision_text = (
        PACKAGE_ROOT
        / "assets/workflows/vendor_selection/skills/decision_packager-core.md"
    ).read_text()
    cases = cast(
        list[dict[str, object]],
        _json_block_after(decision_text, "## Normative Decision Mapping"),
    )
    pass_throughs = {
        str(case["source_action_id"]): case
        for case in cases
        if case.get("mapping") == "exact_pass_through"
    }
    assert set(pass_throughs) == expected_routes
    for case in pass_throughs.values():
        assert case["selected_input"] == "work_item_payload DecisionPack"
        assert case["terminal_marker"] == "DECISION_PACK_READY"
        assert case["result"] == case["input_decision_pack"]

    entrypoint = (
        PACKAGE_ROOT
        / "assets/workflows/vendor_selection/entrypoints/decision_packager.md"
    ).read_text()
    assert "Stop only when no normative mapping can be constructed" in entrypoint
    assert "semantic mismatch" in entrypoint
    assert "`DECISION_PACK_READY`" in entrypoint


def test_vendor_selection_operator_decision_schema_is_exactly_five_fields() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    schema = next(
        declaration.schema
        for declaration in plan.artifact_schemas
        if str(declaration.id) == "OperatorDecision"
    )

    assert schema == {
        "type": "object",
        "required": (
            "gate_id",
            "bundle_id",
            "decision",
            "actor_kind",
            "audit_reason",
        ),
        "properties": {
            "gate_id": {"type": "string", "min_length": 1},
            "bundle_id": {"type": "string", "min_length": 1},
            "decision": {"enum": ("approve", "reject")},
            "actor_kind": {"const": "local_operator"},
            "audit_reason": {"type": "string", "min_length": 1},
        },
    }

    legal_payload = {
        "gate_id": "vendor_selection.award_operator_wait",
        "bundle_id": "bundle-001",
        "decision": "approve",
        "actor_kind": "local_operator",
        "audit_reason": "Approved after local review.",
    }
    assert _schema_accepts(schema, legal_payload)

    with_machine_derived_field = {
        **legal_payload,
        "source_request_id": "request-001",
    }
    assert not _schema_accepts(schema, with_machine_derived_field)


def test_vendor_selection_decision_pack_schema_forbids_operator_reference() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    schemas = {str(schema.id): schema.schema for schema in plan.artifact_schemas}
    schema = schemas["DecisionPack"]
    evidence_properties = schema["properties"]["evidence_refs"]["properties"]
    assert set(evidence_properties) == {"rubric_report_ref", "conflict_report_ref"}

    valid = {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "selected_candidate_id": "vendor_alpha",
        "final_refusal_reason": None,
        "evidence_refs": {
            "rubric_report_ref": "rubric-001",
            "conflict_report_ref": "conflict-001",
        },
        "selected_plan_id": "vendor_selection:0.1",
        "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
        "close_reason": "awarded",
    }
    assert _schema_accepts(schema, valid)
    with_operator_ref = deepcopy(valid)
    cast(dict[str, object], with_operator_ref["evidence_refs"])[
        "operator_decision_ref"
    ] = "operator-input-001"
    assert not _schema_accepts(schema, with_operator_ref)

    vendor_root = PACKAGE_ROOT / "assets/workflows/vendor_selection"
    assert all(
        "operator_decision_ref" not in path.read_text()
        for path in vendor_root.rglob("*.md")
    )


def test_vendor_selection_decision_packager_forbids_input_scavenging() -> None:
    decision_assets = (
        PACKAGE_ROOT
        / "assets/workflows/vendor_selection/entrypoints/decision_packager.md",
        PACKAGE_ROOT
        / "assets/workflows/vendor_selection/skills/decision_packager-core.md",
    )
    text = "\n".join(path.read_text() for path in decision_assets)
    assert "Use only one of these three selected input shapes" in text
    assert "complete direct `DecisionPack`" in text
    assert "direct `AwardDecision`" in text
    assert "`OperatorDecision` plus `selected_wait_evidence`" in text
    for forbidden_source in (
        "database",
        "filesystem",
        "runtime state",
        "lineage",
        "prior session",
        "retained evidence",
    ):
        assert forbidden_source in text


def test_vendor_selection_fanout_join_concurrency_and_wait_are_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    fanouts = {str(fanout.id): fanout for fanout in plan.fanout_declarations}
    assert set(fanouts) == {
        "vendor_selection.candidate_packager.rubric_fanout",
        "vendor_selection.candidate_packager.conflict_fanout",
    }
    assert {
        fanout_id: (
            fanout.target_route_id,
            str(fanout.target_stage_kind_id),
            str(fanout.target_payload_schema_id),
            fanout.source_state_policy,
            fanout.dependency_policy,
        )
        for fanout_id, fanout in fanouts.items()
    } == {
        "vendor_selection.candidate_packager.rubric_fanout": (
            "vendor_selection.rubric_work",
            "rubric_evaluator",
            "CandidateBundle",
            "source_closed",
            "depends_on_source_work_item",
        ),
        "vendor_selection.candidate_packager.conflict_fanout": (
            "vendor_selection.conflict_work",
            "conflict_checker",
            "CandidateBundle",
            "source_closed",
            "depends_on_source_work_item",
        ),
    }
    join = plan.join_declarations[0]
    assert (
        join.id,
        str(join.target_stage_kind_id),
        join.correlation_key,
        tuple(str(schema) for schema in join.required_artifact_schema_ids),
        join.missing_policy,
    ) == (
        "candidate_evidence_join",
        "award_decider",
        "bundle_id",
        ("RubricReport", "ConflictReport"),
        "wait",
    )
    policies = {
        str(policy.partition_id): policy for policy in plan.concurrency_policies
    }
    assert {
        partition: policy.max_active_runs for partition, policy in policies.items()
    } == {
        "requirements": 1,
        "sourcing": 1,
        "evaluation": 2,
        "authorization": 1,
    }
    assert all(len(policy.coexist_partition_ids) == 3 for policy in policies.values())

    wait = plan.operator_waits[0]
    assert (
        str(wait.id),
        tuple(str(action) for action in wait.source_action_ids),
        tuple(wait.allowed_resolution_kinds),
        str(wait.payload_schema_id),
        str(wait.target_queue_family_id),
        str(wait.target_stage_kind_id),
        wait.target_graph_node_id,
        str(wait.target_runner_binding_id),
    ) == (
        "vendor_selection.award_operator_wait",
        ("vendor_selection.award_decider.operator_required",),
        ("resume_recorded_source", "revise_recorded_source"),
        "OperatorDecision",
        "decision_pack",
        "decision_packager",
        "vendor_selection.decision_packager.start",
        "decision_packager.millforge_runner",
    )
    assert plan.effect_declarations == ()


def _mutate_vendor_source(source: dict[str, object], case: str) -> None:
    join = _records(source, "join_declarations")[0]
    wait = _records(source, "operator_waits")[0]
    if case == "compatibility":
        cast(Record, source["workflow"])["compatibility_profile"] = "lad_codex"
    elif case == "extension":
        cast(Record, source["workflow"])["required_extensions"] = ("vendor.x",)
    elif case == "missing_partition":
        _records(source, "partitions")[:] = [
            row
            for row in _records(source, "partitions")
            if row["id"] != "authorization"
        ]
    elif case == "extra_partition":
        _records(source, "partitions").append(
            {"id": "shadow", "kind": "plane", "presentation": {}}
        )
    elif case == "internal_external_route":
        _records(source, "external_enqueue_routes")[0]["queue_family_id"] = (
            "candidate_bundle"
        )
    elif case == "route_schema":
        _record(source, "generated_work_routes", "vendor_selection.rubric_work")[
            "payload_schema_id"
        ] = "RubricReport"
    elif case == "missing_runner":
        _records(source, "stage_kinds")[0]["runner_binding_id"] = "missing.runner"
    elif case == "action_kind":
        _records(source, "terminal_actions")[0]["kind"] = "provider_action"
    elif case == "fanout_duplicate_route":
        _records(source, "fanout_declarations")[0]["target_route_id"] = (
            "vendor_selection.conflict_work"
        )
    elif case == "join_stage":
        join["target_stage_kind_id"] = "missing_stage"
    elif case == "join_schema":
        join["required_artifact_schema_ids"] = ("RubricReport", "MissingReport")
    elif case == "join_duplicate_schema":
        join["required_artifact_schema_ids"] = ("RubricReport", "RubricReport")
    elif case == "join_empty_schema":
        join["required_artifact_schema_ids"] = ()
    elif case == "join_blank_key":
        join["correlation_key"] = ""
    elif case == "join_wrong_key":
        join["correlation_key"] = "request_id"
    elif case == "join_policy":
        join["missing_policy"] = "skip"
    elif case == "join_missing_field":
        join.pop("missing_policy")
    elif case == "join_target_schema":
        join["required_artifact_schema_ids"] = ("OperatorDecision",)
    elif case == "join_id_collision":
        join["id"] = "vendor_selection.request_intake.request_ready"
    elif case == "join_unknown":
        join["presentation"] = {"display_name": "Unexpected"}
    elif case == "concurrency_partition":
        _records(source, "concurrency_policies")[0]["partition_id"] = "missing"
    elif case == "wait_acl":
        wait["approval_labels"] = ("approve", "reject")
    else:
        wait["allowed_resolution_kinds"] = ("resume_recorded_source", "approve")


@pytest.mark.parametrize(
    ("case", "code"),
    (
        ("compatibility", "unsupported_compatibility_profile"),
        ("extension", "unsupported_required_extensions"),
        ("missing_partition", "missing_reference"),
        ("extra_partition", "unreferenced_partition"),
        ("internal_external_route", "external_enqueue_route_internal_queue"),
        ("route_schema", "invalid_fanout_declaration"),
        ("missing_runner", "missing_reference"),
        ("action_kind", "unsupported_terminal_action_kind"),
        ("fanout_duplicate_route", "invalid_fanout_declaration"),
        ("join_stage", "missing_reference"),
        ("join_schema", "missing_reference"),
        ("join_duplicate_schema", "invalid_join_declaration"),
        ("join_empty_schema", "invalid_join_declaration"),
        ("join_blank_key", "invalid_join_declaration"),
        ("join_wrong_key", "invalid_join_declaration"),
        ("join_policy", "invalid_join_declaration"),
        ("join_missing_field", "missing_join_declaration_field"),
        ("join_target_schema", "invalid_join_declaration"),
        ("join_id_collision", "invalid_join_declaration"),
        ("join_unknown", "unknown_join_declaration_field"),
        ("concurrency_partition", "missing_reference"),
        ("wait_acl", "unknown_operator_wait_field"),
        ("wait_resolution", "invalid_operator_wait_field"),
    ),
)
def test_vendor_selection_compiler_refuses_exact_selected_mutations(
    case: str,
    code: str,
) -> None:
    source = _source()
    _mutate_vendor_source(source, case)

    errors = _compile_errors(source)

    assert any(error.code == code for error in errors), errors


def test_vendor_selection_export_is_stable_and_selected_only() -> None:
    first = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    second = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    payload = compiled_plan_export_bytes(first)
    verified = verify_compiled_plan_export_bytes(payload)
    assert payload == compiled_plan_export_bytes(second)
    assert verified.authority_fingerprint == authority_fingerprint(first)
    assert canonical_authority_bytes(first) == canonical_authority_bytes(second)
    assert first.effect_declarations == ()
    assert first.recovery_policies == ()


def test_vendor_selection_unselected_catalog_does_not_change_fingerprint() -> None:
    source = _source()
    base = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    source["unselected_catalog"] = (
        {"id": "vendor.marketplace", "provider_effect": "not-selected"},
    )
    result = compile_workflow(deepcopy(source))
    assert result.plan is not None

    assert authority_fingerprint(result.plan) == authority_fingerprint(base)


_VENDOR_OWNER_LEDGER = (
    ("compiler/test_vendor_selection_compile.py", 14, 14, 0),
    ("compiler/test_vendor_selection_export.py", 4, 4, 0),
    ("compiler/test_vendor_selection_diagnostics.py", 25, 25, 0),
    ("kernel/test_vendor_selection_runtime.py", 12, 10, 2),
    ("kernel/test_vendor_selection_join_and_fanout.py", 19, 10, 9),
    ("operator/test_vendor_selection_status_projection.py", 2, 0, 2),
    ("operator/test_vendor_selection_operator_wait.py", 3, 1, 2),
    ("operator/test_vendor_selection_join_dispatch.py", 2, 2, 0),
    ("substrate/test_vendor_selection_restart.py", 8, 5, 3),
    ("e2e/test_vendor_selection_millforge_live_proof.py", 1, 0, 1),
)

_VENDOR_RESTART_LEDGER = (
    "receipt_and_work_item",
    "route_chain_provenance",
    "decision_pack_close",
    "operator_wait_after_join",
    "open_wait_computed_join_close_projection",
    "corrupt_decision_pack_schema",
    "corrupt_decision_pack_source_action",
    "corrupt_decision_pack_payload_digest",
)

_VENDOR_DISPATCH_LEDGER = (
    "join_created_exact_selected_evidence",
    "operator_required_selected_evidence",
)


def test_vendor_selection_source_owner_and_row_ledgers_are_complete() -> None:
    assert sum(rows for _owner, rows, _selected, _n_a in _VENDOR_OWNER_LEDGER) == 90
    assert sum(selected for _owner, _rows, selected, _n_a in _VENDOR_OWNER_LEDGER) == 71
    assert sum(n_a for _owner, _rows, _selected, n_a in _VENDOR_OWNER_LEDGER) == 19
    assert all(
        rows == selected + n_a for _owner, rows, selected, n_a in _VENDOR_OWNER_LEDGER
    )
    assert len(_VENDOR_RESTART_LEDGER) == 8
    assert len(_VENDOR_DISPATCH_LEDGER) == 2
