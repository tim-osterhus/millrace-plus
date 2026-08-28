from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from millrace.compiler import (
    authority_fingerprint,
    canonical_authority_bytes,
    compile_workflow,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.3"
WORKFLOW_ID = "simple_loop"
WORKFLOW_VERSION = "0.1"
Record = dict[str, object]

_EXPECTED_STAGE_ASSETS = {
    "simple_loop.manager": (
        "simple_loop.manager_prompt",
        "simple_loop.manager_core_skill",
    ),
    "simple_loop.worker": (
        "simple_loop.worker_prompt",
        "simple_loop.worker_core_skill",
    ),
    "simple_loop.reviewer": (
        "simple_loop.reviewer_prompt",
        "simple_loop.reviewer_core_skill",
    ),
    "simple_loop.troubleshooter": (
        "simple_loop.troubleshooter_prompt",
        "simple_loop.troubleshooter_core_skill",
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
                        "change_kind": {"enum": ["create", "modify", "delete"]},
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


def test_simple_loop_manifest_is_complete_package_authority() -> None:
    manifest = _manifest()
    workflow = conformance.workflows_by_id(manifest)[WORKFLOW_ID]
    selected = cast(dict[str, object], workflow["selected_authority"])
    stages = {
        str(stage["id"]): stage
        for stage in cast(list[dict[str, object]], selected["stage_kinds"])
    }

    assert workflow["workflow_version"] == WORKFLOW_VERSION
    assert workflow["visibility"] == "public"
    assert "assets" not in selected
    assert set(stages) == set(_EXPECTED_STAGE_ASSETS)
    for stage_id, expected_assets in _EXPECTED_STAGE_ASSETS.items():
        assert tuple(stages[stage_id]["asset_ids"]) == expected_assets


def test_simple_loop_selects_through_installed_public_api(tmp_path: Path) -> None:
    manifest = _manifest()
    first = conformance.select_and_verify_package(
        tmp_path / "first",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
    )
    second = conformance.select_and_verify_package(
        tmp_path / "second",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
    )
    conformance.assert_selected_package_pin(
        first,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        selected_asset_pins=conformance.selected_asset_pins(manifest, WORKFLOW_ID),
    )
    assert authority_fingerprint(first) == authority_fingerprint(second)


def test_simple_loop_assets_keep_literal_completion_contract() -> None:
    root = PACKAGE_ROOT / "assets/workflows/simple_loop"
    manager = (root / "skills/manager-core.md").read_text()
    worker = (root / "skills/worker-core.md").read_text()
    reviewer = (root / "skills/reviewer-core.md").read_text()

    assert "preserve it character-for-character" in manager
    assert "compare its exact bytes or text" in worker
    assert "Do not rely on the Worker's summary" in reviewer


def test_simple_loop_external_route_is_selected_authority() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    route = plan.external_enqueue_routes[0]

    assert (
        route.id,
        str(route.queue_family_id),
        route.graph_node_id,
        str(route.stage_kind_id),
        str(route.runner_binding_id),
        str(route.payload_schema_id),
    ) == (
        "simple_loop.external_work_prompt",
        "work_prompt",
        "simple_loop.manager.start",
        "simple_loop.manager",
        "simple_loop.manager.millforge_runner",
        "simple_loop.work_prompt",
    )


def test_simple_loop_compiled_topology_and_runner_authority_is_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    assert plan.lineage_policy == "root_from_external_enqueue"
    assert {str(partition.id) for partition in plan.partitions} == {
        "management",
        "implementation",
        "review",
    }
    assert plan.graphs[0].node_ids == (
        "simple_loop.manager.start",
        "simple_loop.worker.start",
        "simple_loop.reviewer.start",
        "simple_loop.manager.detail_request",
        "simple_loop.worker.gaps",
        "simple_loop.manager.incident",
        "simple_loop.troubleshooter.start",
    )
    stages = {str(stage.id): stage for stage in plan.stage_kinds}
    assert {
        stage_id: (
            None if stage.partition_id is None else str(stage.partition_id),
            str(stage.runner_binding_id),
        )
        for stage_id, stage in stages.items()
    } == {
        "simple_loop.manager": (
            "management",
            "simple_loop.manager.millforge_runner",
        ),
        "simple_loop.worker": (
            "implementation",
            "simple_loop.worker.millforge_runner",
        ),
        "simple_loop.reviewer": (
            "review",
            "simple_loop.reviewer.millforge_runner",
        ),
        "simple_loop.troubleshooter": (
            None,
            "simple_loop.troubleshooter.millforge_runner",
        ),
    }
    assert all(
        runner.adapter_kind == "millforge"
        and runner.component_pin is not None
        and runner.component_pin.provider_distribution == "millforge"
        for runner in plan.runner_bindings
    )


def test_simple_loop_terminal_routes_projection_and_counter_are_selected() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    actions = {str(action.id): action for action in plan.terminal_actions}
    expected = {
        "simple_loop.manager.packet_ready": (
            "route",
            "simple_loop.worker",
            "simple_loop.worker.start",
            "work_packet",
            "simple_loop.work_packet",
        ),
        "simple_loop.worker.work_done": (
            "route",
            "simple_loop.reviewer",
            "simple_loop.reviewer.start",
            "work_packet",
            "simple_loop.work_result",
        ),
        "simple_loop.reviewer.gaps_found": (
            "route",
            "simple_loop.worker",
            "simple_loop.worker.gaps",
            "gap_packet",
            "simple_loop.gap_packet",
        ),
        "simple_loop.worker.insufficient_spec": (
            "route",
            "simple_loop.manager",
            "simple_loop.manager.detail_request",
            "work_packet",
            "simple_loop.detail_request",
        ),
        "simple_loop.reviewer.incident_required": (
            "route",
            "simple_loop.manager",
            "simple_loop.manager.incident",
            "incident_report",
            "simple_loop.incident_report",
        ),
    }
    for action_id, selected in expected.items():
        action = actions[action_id]
        assert (
            action.action_kind,
            str(action.target_stage_kind_id),
            action.target_graph_node_id,
            str(action.emitted_queue_family_id),
            str(action.artifact_schema_id),
        ) == selected
        assert action.payload_projection is not None

    counter = plan.counters[0]
    assert (
        str(counter.id),
        str(counter.stage_kind_id),
        str(counter.increment_action_id),
        counter.threshold_count,
        str(counter.threshold_action_id),
    ) == (
        "simple_loop.reviewer_gap_counter",
        "simple_loop.reviewer",
        "simple_loop.reviewer.gaps_found",
        4,
        "simple_loop.reviewer.incident_required",
    )


def test_simple_loop_recovery_wait_and_intervention_authority_is_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    recovery = plan.recovery_policies[0]
    assert (
        str(recovery.id),
        tuple(str(action) for action in recovery.source_recovery_action_ids),
        tuple(str(action) for action in recovery.return_action_ids),
        tuple(str(action) for action in recovery.quarantine_action_ids),
        str(recovery.recovery_stage_kind_id),
        recovery.immediate_recovery_limit,
        recovery.cooldown_starts_at_attempt,
        recovery.quarantine_threshold_attempt,
        recovery.default_cooldown_seconds,
    ) == (
        "simple_loop.blocked_recovery",
        (
            "simple_loop.manager.blocked",
            "simple_loop.worker.blocked",
            "simple_loop.worker.failed",
            "simple_loop.reviewer.blocked",
        ),
        (
            "simple_loop.troubleshooter.resolved",
            "simple_loop.troubleshooter.unresolved",
        ),
        ("simple_loop.troubleshooter.operator_needed",),
        "simple_loop.troubleshooter",
        1,
        2,
        3,
        900,
    )
    wait_state = plan.wait_states[0]
    assert (
        str(wait_state.id),
        wait_state.wait_kind,
        wait_state.duration_seconds,
        wait_state.starts_at_attempt,
    ) == ("simple_loop.blocked_recovery.cooldown", "timer", 900, 2)

    options = {str(option.id): option for option in plan.intervention_options}
    assert {option.option_kind for option in options.values()} == {
        "resume_lineage",
        "close_lineage",
        "revise_lineage",
    }
    revise = options["simple_loop.revise_lineage"]
    assert (
        str(revise.payload_schema_id),
        str(revise.target_queue_family_id),
        str(revise.target_stage_kind_id),
        revise.target_graph_node_id,
        str(revise.target_runner_binding_id),
    ) == (
        "simple_loop.work_packet",
        "work_packet",
        "simple_loop.worker",
        "simple_loop.worker.start",
        "simple_loop.worker.millforge_runner",
    )

    waits = {str(wait.id): wait for wait in plan.operator_waits}
    detail = waits["simple_loop.manager_detail_wait"]
    incident = waits["simple_loop.manager_incident_wait"]
    assert tuple(detail.allowed_resolution_kinds) == (
        "resume_recorded_source",
        "close_recorded_source",
        "revise_recorded_source",
    )
    assert tuple(incident.allowed_resolution_kinds) == ("close_recorded_source",)
    assert detail.source_work_item_behavior == "leave_open"
    assert incident.source_work_item_behavior == "close_on_create"


@pytest.mark.parametrize(
    ("case", "code"),
    (
        ("route_missing_node", "terminal_route_missing_field"),
        ("route_bad_projection", "invalid_terminal_projection"),
        ("route_bad_queue", "terminal_route_stage_output_mismatch"),
        ("recovery_missing_stage", "terminal_recovery_route_missing_field"),
        ("recovery_bad_scope", "unsupported_recovery_policy_value"),
        ("recovery_bad_threshold", "invalid_recovery_policy_threshold"),
        ("intervention_missing_policy", "missing_intervention_option_field"),
        ("intervention_unknown", "unknown_intervention_option_field"),
        ("revise_missing_schema", "missing_intervention_option_field"),
        ("compatibility", "unsupported_compatibility_profile"),
        ("required_extension", "unsupported_required_extensions"),
    ),
)
def test_simple_loop_compiler_refuses_selected_authority_mutations(
    case: str,
    code: str,
) -> None:
    source = _source()
    action = _record(source, "terminal_actions", "simple_loop.manager.packet_ready")
    recovery_action = _record(source, "terminal_actions", "simple_loop.manager.blocked")
    policy = _records(source, "recovery_policies")[0]
    option = _record(source, "intervention_options", "simple_loop.revise_lineage")
    if case == "route_missing_node":
        action.pop("target_graph_node_id")
    elif case == "route_bad_projection":
        action["payload_projection"] = {"kind": "source", "path": ["unknown_root"]}
    elif case == "route_bad_queue":
        action["emitted_queue_family_id"] = "incident_report"
    elif case == "recovery_missing_stage":
        recovery_action.pop("target_stage_kind_id")
    elif case == "recovery_bad_scope":
        policy["attempt_scope"] = "workspace"
    elif case == "recovery_bad_threshold":
        policy["quarantine_threshold_attempt"] = 1
    elif case == "intervention_missing_policy":
        option.pop("policy_id")
    elif case == "intervention_unknown":
        option["payload_schema"] = {"type": "object"}
    elif case == "revise_missing_schema":
        option.pop("payload_schema_id")
    elif case == "compatibility":
        cast(Record, source["workflow"])["compatibility_profile"] = "lad_codex"
    else:
        cast(Record, source["workflow"])["required_extensions"] = ("example",)

    errors = tuple(
        diagnostic
        for diagnostic in compile_workflow(source).diagnostics
        if diagnostic.severity == "error"
    )

    assert any(error.code == code for error in errors), errors


_SIMPLE_OWNER_LEDGER = (
    ("compiler/test_simple_loop_compile.py", 8, 8, 0),
    ("compiler/test_simple_loop_diagnostics.py", 94, 11, 83),
    ("kernel/test_simple_loop_manager_to_worker.py", 8, 4, 4),
    ("kernel/test_simple_loop_operator_wait.py", 8, 2, 6),
    ("kernel/test_simple_loop_gap_incident.py", 9, 6, 3),
    ("kernel/test_simple_loop_runtime_admission.py", 1, 1, 0),
    ("kernel/test_simple_loop_happy_path.py", 5, 2, 3),
    ("kernel/test_simple_loop_recovery.py", 32, 14, 18),
    ("operator/test_simple_loop_status_projection.py", 8, 0, 8),
    ("substrate/test_simple_loop_restart.py", 7, 7, 0),
    ("e2e/test_simple_loop_millforge_live_proof.py", 1, 0, 1),
)

_SIMPLE_RESTART_LEDGER = (
    "manager_to_worker_ready",
    "worker_to_reviewer_ready",
    "partitionless_troubleshooter_and_manager_claim",
    "reviewer_accepted_close",
    "detail_request_to_manager_ready",
    "gap_route_history",
    "incident_route_history",
)


def test_simple_loop_source_owner_and_restart_ledgers_are_complete() -> None:
    assert sum(rows for _owner, rows, _selected, _n_a in _SIMPLE_OWNER_LEDGER) == 181
    assert sum(selected for _owner, _rows, selected, _n_a in _SIMPLE_OWNER_LEDGER) == 55
    assert sum(n_a for _owner, _rows, _selected, n_a in _SIMPLE_OWNER_LEDGER) == 126
    assert all(
        rows == selected + n_a for _owner, rows, selected, n_a in _SIMPLE_OWNER_LEDGER
    )
    assert len(_SIMPLE_RESTART_LEDGER) == 7


def test_simple_loop_unselected_schema_does_not_change_authority() -> None:
    source = _source()
    base = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    source["artifact_schemas"] = (
        *_records(source, "artifact_schemas"),
        {
            "id": "simple_loop.unselected",
            "schema": {"type": "object"},
            "presentation": {},
        },
    )
    result = compile_workflow(deepcopy(source))
    assert result.plan is not None

    assert canonical_authority_bytes(result.plan) == canonical_authority_bytes(base)


def test_simple_loop_context_bindings_are_scoped_and_typed() -> None:
    source = _source()
    bindings = _records(source, "context_bindings")
    by_stage = {str(binding["stage_kind_id"]): binding for binding in bindings}

    assert set(by_stage) == {
        "simple_loop.worker",
        "simple_loop.reviewer",
        "simple_loop.troubleshooter",
    }
    assert all(str(binding["id"]).startswith("simple_loop.") for binding in bindings)
    assert all(
        binding["router_asset_id"] == "simple_loop.context_router"
        for binding in bindings
    )
    assert {
        stage_id: binding["mutation_policy"] for stage_id, binding in by_stage.items()
    } == {
        "simple_loop.worker": "forbid_selected_roots",
        "simple_loop.reviewer": "reconcile_selected_writes",
        "simple_loop.troubleshooter": "forbid_selected_roots",
    }
    assert all(
        binding["materialization_retention"]
        == "until_session_durable_terminal"
        for binding in bindings
    )
    for stage_id in ("simple_loop.worker", "simple_loop.troubleshooter"):
        binding = by_stage[stage_id]
        assert binding["write_rules"] == []
        assert binding["writeback_terminal_action_id"] is None
        assert binding["writeback_artifact_schema_id"] is None

    reviewer_binding = by_stage["simple_loop.reviewer"]
    assert reviewer_binding["write_rules"] == [
        {"relative_root": "docs/reviews", "disposition": "direct_write"}
    ]
    assert reviewer_binding["writeback_terminal_action_id"] == (
        "simple_loop.reviewer.accepted"
    )
    assert reviewer_binding["writeback_artifact_schema_id"] == (
        "simple_loop.context_update_report"
    )

    required_pairs = {
        stage_id: {
            (item["source_kind"], item["source_ref"])
            for item in by_stage[stage_id]["required_sources"]
        }
        for stage_id in by_stage
    }
    assert ("selected_artifacts", "direct_predecessors") in required_pairs[
        "simple_loop.worker"
    ]
    assert ("selected_artifacts", "direct_predecessors") in required_pairs[
        "simple_loop.troubleshooter"
    ]
    troubleshooter_predecessors = next(
        item
        for item in by_stage["simple_loop.troubleshooter"]["required_sources"]
        if (
            item["source_kind"],
            item["source_ref"],
        )
        == ("selected_artifacts", "direct_predecessors")
    )
    assert troubleshooter_predecessors["empty_policy"] == "omit_if_absent"
    assert (
        "selected_attempts",
        "since_last_accepted_transition",
    ) in required_pairs["simple_loop.troubleshooter"]
    assert (
        "selected_artifacts",
        "current_lineage",
    ) in required_pairs["simple_loop.reviewer"]
    assert (
        "selected_attempts",
        "current_lineage",
    ) in {
        (item["source_kind"], item["source_ref"])
        for item in by_stage["simple_loop.troubleshooter"]["discoverable_sources"]
    }
    assert (
        "workspace_relative_root",
        "docs",
    ) in required_pairs["simple_loop.reviewer"]

    troubleshooter_stage = _record(source, "stage_kinds", "simple_loop.troubleshooter")
    assert troubleshooter_stage["artifact_schema_ids"] == [
        "simple_loop.troubleshooting_report"
    ]
    troubleshooter_actions = {
        action["id"]: action
        for action in _records(source, "terminal_actions")
        if str(action["id"]).startswith("simple_loop.troubleshooter.")
    }
    assert set(troubleshooter_actions) == {
        "simple_loop.troubleshooter.operator_needed",
        "simple_loop.troubleshooter.resolved",
        "simple_loop.troubleshooter.unresolved",
    }
    assert all(
        action["artifact_schema_id"] == "simple_loop.troubleshooting_report"
        for action in troubleshooter_actions.values()
    )
    writeback_schema = _record(
        source,
        "artifact_schemas",
        "simple_loop.context_update_report",
    )
    assert writeback_schema["schema"] == _context_update_report_schema()
    reviewer_stage = _record(source, "stage_kinds", "simple_loop.reviewer")
    assert "simple_loop.context_update_report" in reviewer_stage[
        "artifact_schema_ids"
    ]
    reviewer_accepted = _record(
        source,
        "terminal_actions",
        "simple_loop.reviewer.accepted",
    )
    assert reviewer_accepted["artifact_schema_id"] == (
        "simple_loop.context_update_report"
    )


@pytest.mark.parametrize(
    "schema_id",
    (
        "simple_loop.work_packet",
        "simple_loop.detail_request",
        "simple_loop.incident_report",
        "simple_loop.work_result",
        "simple_loop.gap_packet",
        "simple_loop.troubleshooting_report",
    ),
)
def test_simple_loop_evidence_contract_is_selected_schema_authority(
    schema_id: str,
) -> None:
    schema = cast(
        dict[str, Any],
        _record(_source(), "artifact_schemas", schema_id)["schema"],
    )
    properties = cast(dict[str, Any], schema["properties"])

    assert {"evidence", "assumptions"}.issubset(
        cast(list[str], schema["required"])
    )
    item_schema = {"min_length": 1, "type": "string"}
    assert properties["evidence"] == {
        "items": item_schema,
        "min_items": 1,
        "type": "array",
    }
    assert properties["assumptions"] == {
        "items": item_schema,
        "type": "array",
    }

def test_simple_loop_context_catalog_is_selected_by_one_named_stage() -> None:
    source = _source()
    bindings = _records(source, "context_bindings")
    catalog_items = [
        (binding, item)
        for binding in bindings
        for item in binding.get("discoverable_sources", ())
        if item["source_kind"] == "workspace_relative_root"
    ]

    assert len(catalog_items) == 1
    catalog_binding, catalog_item = catalog_items[0]
    assert catalog_binding["stage_kind_id"] == "simple_loop.worker"
    assert catalog_item["source_ref"] == "docs"

    entrypoint_text = "\n".join(
        (PACKAGE_ROOT / "assets/workflows/simple_loop/entrypoints" / name).read_text()
        for name in ("worker.md", "reviewer.md", "troubleshooter.md")
    )
    assert entrypoint_text.count("millrace context select docs") == 1


def test_context_selector_names_are_canonical() -> None:
    manifest = _manifest()
    source_pairs = {
        (item["source_kind"], item["source_ref"])
        for workflow in conformance.workflows_by_id(manifest).values()
        for item in (
            source
            for binding in cast(
                list[dict[str, object]],
                cast(dict[str, object], workflow["selected_authority"]).get(
                    "context_bindings", []
                ),
            )
            for source in (
                *cast(list[dict[str, object]], binding["required_sources"]),
                *cast(list[dict[str, object]], binding["discoverable_sources"]),
            )
        )
    }

    assert ("selected_artifacts", "current_lineage") in source_pairs
    assert ("selected_attempts", "current_lineage") in source_pairs
    assert not source_pairs & {
        ("accepted_lineage_artifacts", "current_lineage"),
        ("lineage_attempt_history", "current_lineage"),
    }


def test_simple_loop_context_router_is_a_required_packaged_asset() -> None:
    manifest = _manifest()
    workflow = conformance.workflows_by_id(manifest)[WORKFLOW_ID]
    required_assets = {
        str(asset["asset_id"]) for asset in workflow["required_assets"]
    }
    assets = {
        str(asset["asset_id"]): asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
    }

    assert "simple_loop.context_router" in required_assets
    router = assets["simple_loop.context_router"]
    assert router["asset_kind"] == "template"
    assert router["encoding"] == "utf-8"
    assert router["media_type"] == "text/markdown; charset=utf-8"
    assert router["package_path"] == "assets/workflows/simple_loop/context/router.md"
    assert router["selected_authority_participation"] == "yes"
    assert router["selection"] == "required"
