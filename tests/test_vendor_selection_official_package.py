from __future__ import annotations

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
PACKAGE_VERSION = "0.22.0"
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
