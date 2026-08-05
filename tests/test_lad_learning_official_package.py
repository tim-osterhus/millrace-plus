from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from millrace.compiler import (
    CompiledPlanExportError,
    authority_fingerprint,
    canonical_authority_bytes,
    compile_workflow,
    compiled_plan_export_bytes,
    compiled_plan_export_record,
    verify_compiled_plan_export_bytes,
    verify_compiled_plan_export_record,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.2"
WORKFLOW_ID = "lad.full"
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


def _error(source: dict[str, object], code: str) -> object:
    return next(error for error in _compile_errors(source) if error.code == code)


def test_full_lad_authority_and_assets_are_package_owned() -> None:
    manifest = _manifest()
    workflow = conformance.workflows_by_id(manifest)[WORKFLOW_ID]
    selected = cast(dict[str, object], workflow["selected_authority"])

    assert workflow["workflow_version"] == "0.1"
    assert len(cast(list[object], selected["stage_kinds"])) == 17
    assert len(cast(list[object], workflow["required_assets"])) == 34
    assert "assets" not in selected


def test_full_lad_selects_through_installed_public_api(tmp_path: Path) -> None:
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


def test_full_lad_librarian_assets_keep_truthful_noop_contract() -> None:
    root = PACKAGE_ROOT / "assets/workflows/lad.full"
    entrypoint = (root / "entrypoints/librarian.md").read_text()
    skill = (root / "skills/librarian-core.md").read_text()
    combined = entrypoint + skill
    assert "index_unavailable" in combined
    assert "NOOP" in combined
    assert "skill_disposition" in combined
    assert "install authority" in combined


def test_full_lad_learning_route_is_selected_authority() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    route = next(
        route
        for route in plan.external_enqueue_routes
        if route.id == "learning_request"
    )

    assert (
        str(route.queue_family_id),
        route.graph_node_id,
        str(route.stage_kind_id),
        str(route.runner_binding_id),
        str(route.payload_schema_id),
    ) == (
        "learning_request",
        "learning.standard.analyst",
        "analyst",
        "analyst.millforge_runner",
        "learning.intake.request",
    )


def test_learning_topology_trigger_and_concurrency_authority_is_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    graphs = {str(graph.id): graph for graph in plan.graphs}
    assert graphs["learning.standard.graph"].node_ids == (
        "learning.standard.analyst",
        "learning.standard.professor",
        "learning.standard.curator",
        "learning.standard.librarian",
    )

    generated = {route.id: route for route in plan.generated_work_routes}
    assert {
        route_id: (
            str(route.queue_family_id),
            route.graph_node_id,
            str(route.stage_kind_id),
            str(route.runner_binding_id),
            str(route.payload_schema_id),
        )
        for route_id, route in generated.items()
    } == {
        "learning.trigger.analyst": (
            "learning_request",
            "learning.standard.analyst",
            "analyst",
            "analyst.millforge_runner",
            "learning.intake.request",
        ),
        "learning.trigger.librarian": (
            "learning_request",
            "learning.standard.librarian",
            "librarian",
            "librarian.millforge_runner",
            "learning.intake.request",
        ),
    }

    concurrency = {
        str(policy.partition_id): policy for policy in plan.concurrency_policies
    }
    assert {
        partition_id: (
            policy.max_active_runs,
            tuple(str(item) for item in policy.coexist_partition_ids),
        )
        for partition_id, policy in concurrency.items()
    } == {
        "execution": (1, ("learning",)),
        "planning": (1, ("learning",)),
        "learning": (1, ("planning", "execution")),
    }

    fanouts = {str(fanout.id): fanout for fanout in plan.fanout_declarations}
    learning_fanouts = {
        fanout_id: fanout
        for fanout_id, fanout in fanouts.items()
        if fanout_id.startswith("learning.trigger.")
    }
    assert len(learning_fanouts) == 7
    assert all(
        fanout.source_state_policy == "accepted_terminal_observation"
        and str(fanout.target_queue_family_id) == "learning_request"
        and str(fanout.target_payload_schema_id) == "learning.intake.request"
        and fanout.root_lineage_policy == "inherit_source_lineage"
        for fanout in learning_fanouts.values()
    )
    assert (
        str(
            learning_fanouts[
                "learning.trigger.execution.needs_planning"
            ].source_action_id
        ),
        learning_fanouts["learning.trigger.execution.needs_planning"].target_route_id,
    ) == ("execution.close_consultant_needs_plan", "learning.trigger.analyst")
    assert (
        learning_fanouts["learning.trigger.planning.planner_complete"].target_route_id
        == "learning.trigger.librarian"
    )


def test_learning_stage_graph_runner_and_artifact_authority_is_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    stages = {
        str(stage.id): stage
        for stage in plan.stage_kinds
        if str(stage.id) in {"analyst", "professor", "curator", "librarian"}
    }
    assert {
        stage_id: (
            str(stage.runner_binding_id),
            tuple(str(asset_id) for asset_id in stage.asset_ids),
        )
        for stage_id, stage in stages.items()
    } == {
        stage_id: (
            f"{stage_id}.millforge_runner",
            (
                f"learning.entrypoints.{stage_id}",
                f"learning.skills.{stage_id}_core",
            ),
        )
        for stage_id in ("analyst", "professor", "curator", "librarian")
    }

    expected_stage_schemas = {
        "analyst": {
            "learning.intake.request",
            "learning.artifacts.research_packet",
            "learning.artifacts.report",
        },
        "professor": {
            "learning.intake.request",
            "learning.artifacts.research_packet",
            "learning.artifacts.skill_candidate",
            "learning.artifacts.professor_notes",
            "learning.artifacts.report",
        },
        "curator": {
            "learning.intake.request",
            "learning.artifacts.skill_candidate",
            "learning.artifacts.skill_update",
            "learning.artifacts.curator_decision",
            "learning.artifacts.report",
        },
        "librarian": {
            "learning.intake.request",
            "learning.artifacts.skill_install_report",
            "learning.artifacts.skill_disposition",
            "learning.artifacts.report",
        },
    }
    assert {
        stage_id: {str(schema_id) for schema_id in stage.artifact_schema_ids}
        for stage_id, stage in stages.items()
    } == expected_stage_schemas
    assert all(
        "learning.artifacts.stage_result" not in schema_ids
        for schema_ids in expected_stage_schemas.values()
    )


def test_learning_terminal_outcomes_routes_closes_and_waits_are_exact() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    outcomes = {
        str(outcome.id): (str(outcome.stage_kind_id), outcome.marker)
        for outcome in plan.terminal_outcomes
        if str(outcome.id).startswith("learning.")
    }
    assert outcomes == {
        f"learning.{stage}.{suffix}": (stage, marker)
        for stage, markers in {
            "analyst": {
                "blocked": "BLOCKED",
                "complete": "ANALYST_COMPLETE",
                "noop": "ANALYST_NOOP",
            },
            "professor": {
                "blocked": "BLOCKED",
                "complete": "PROFESSOR_COMPLETE",
                "noop": "PROFESSOR_NOOP",
            },
            "curator": {
                "blocked": "BLOCKED",
                "complete": "CURATOR_COMPLETE",
                "noop": "CURATOR_NOOP",
            },
            "librarian": {
                "blocked": "BLOCKED",
                "complete": "LIBRARIAN_COMPLETE",
                "noop": "LIBRARIAN_NOOP",
            },
        }.items()
        for suffix, marker in markers.items()
    }

    actions = {
        str(action.id): action
        for action in plan.terminal_actions
        if str(action.id).startswith("learning.")
    }
    expected = {
        "learning.route_analyst_complete": (
            "route",
            "professor",
            "learning.standard.professor",
            "stage_result",
            "learning.artifacts.research_packet",
            "professor.millforge_runner",
        ),
        "learning.route_professor_complete": (
            "route",
            "curator",
            "learning.standard.curator",
            "stage_result",
            "learning.artifacts.skill_candidate",
            "curator.millforge_runner",
        ),
        "learning.close_analyst_noop": (
            "complete_work_item",
            None,
            None,
            None,
            "learning.artifacts.research_packet",
            None,
        ),
        "learning.close_professor_noop": (
            "complete_work_item",
            None,
            None,
            None,
            "learning.artifacts.professor_notes",
            None,
        ),
        "learning.close_curator_complete": (
            "complete_work_item",
            None,
            None,
            None,
            "learning.artifacts.skill_update",
            None,
        ),
        "learning.close_curator_noop": (
            "complete_work_item",
            None,
            None,
            None,
            "learning.artifacts.curator_decision",
            None,
        ),
        "learning.close_librarian_complete": (
            "complete_work_item",
            None,
            None,
            None,
            "learning.artifacts.skill_install_report",
            None,
        ),
        "learning.close_librarian_noop": (
            "complete_work_item",
            None,
            None,
            None,
            "learning.artifacts.skill_disposition",
            None,
        ),
    }
    for stage in ("analyst", "professor", "curator", "librarian"):
        expected[f"learning.close_{stage}_blocked"] = (
            "operator_wait",
            None,
            None,
            None,
            "learning.artifacts.report",
            None,
        )
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
            None if action.runner_binding_id is None else str(action.runner_binding_id),
        )
        for action_id, action in actions.items()
    } == expected

    waits = {str(wait.id): wait for wait in plan.operator_waits}
    assert set(waits) == {
        f"learning.{stage}_blocked_wait"
        for stage in ("analyst", "professor", "curator", "librarian")
    }
    for stage, wait in (
        (stage, waits[f"learning.{stage}_blocked_wait"])
        for stage in ("analyst", "professor", "curator", "librarian")
    ):
        assert tuple(str(action) for action in wait.source_action_ids) == (
            f"learning.close_{stage}_blocked",
        )
        assert tuple(wait.allowed_resolution_kinds) == (
            "resume_recorded_source",
            "close_recorded_source",
            "revise_recorded_source",
        )
        assert (
            str(wait.payload_schema_id),
            str(wait.target_queue_family_id),
            str(wait.target_stage_kind_id),
            wait.target_graph_node_id,
            str(wait.target_runner_binding_id),
            wait.actor_kind,
        ) == (
            "learning.intake.request",
            "learning_request",
            "analyst",
            "learning.standard.analyst",
            "analyst.millforge_runner",
            "local_operator",
        )


def test_learning_effect_declarations_are_fake_local_selected_authority() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    effects = {
        str(effect.effect_declaration_id): effect for effect in plan.effect_declarations
    }
    assert {
        effect_id: (
            str(effect.terminal_action_id),
            str(effect.artifact_schema_id),
            effect.provider_ref,
            effect.capability_policy_ref,
            effect.target_ref_kind,
            effect.target_ref_schema,
            effect.allowed_reconciliation_statuses,
            effect.real_side_effects_allowed,
        )
        for effect_id, effect in effects.items()
    } == {
        "learning.effect.curator.workspace_skill_update": (
            "learning.close_curator_complete",
            "learning.artifacts.skill_update",
            "provider.fake_local.workspace",
            "policy.fake_local.no_real_side_effects",
            "workspace_skill_update",
            "learning.effects.target.workspace_skill_update.v1",
            ("applied", "no_op", "refused"),
            False,
        ),
        "learning.effect.librarian.workspace_skill_install_report": (
            "learning.close_librarian_complete",
            "learning.artifacts.skill_install_report",
            "provider.fake_local.workspace",
            "policy.fake_local.no_real_side_effects",
            "workspace_skill_install_report",
            "learning.effects.target.workspace_skill_install_report.v1",
            ("applied", "no_op", "refused"),
            False,
        ),
    }


def test_full_lad_selected_export_and_cross_plane_authority_are_deterministic() -> None:
    plan_a = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    plan_b = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    export = compiled_plan_export_bytes(plan_a)
    verified = verify_compiled_plan_export_bytes(export)

    assert plan_a == plan_b
    assert canonical_authority_bytes(plan_a) == canonical_authority_bytes(plan_b)
    assert compiled_plan_export_bytes(plan_b) == export
    assert verified.authority_fingerprint == authority_fingerprint(plan_a)
    assert {str(graph.id) for graph in plan_a.graphs} == {
        "execution.lad.graph",
        "learning.standard.graph",
        "planning.lad.graph",
    }
    assert {str(partition.id) for partition in plan_a.partitions} == {
        "execution",
        "learning",
        "planning",
    }

    close_action = next(
        action
        for action in plan_a.terminal_actions
        if str(action.id) == "execution.close_consultant_needs_plan"
    )
    assert (
        close_action.action_kind,
        close_action.target_stage_kind_id,
        close_action.target_graph_node_id,
        close_action.emitted_queue_family_id,
    ) == ("close_with_escalation", None, None, None)


def test_full_lad_export_verification_refuses_selected_authority_drift() -> None:
    plan = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    record = dict(compiled_plan_export_record(plan))
    selected = dict(cast(dict[str, object], record["selected_authority"]))
    workflow = dict(cast(dict[str, object], selected["workflow"]))
    workflow["workflow_name"] = "Full LAD drifted"
    selected["workflow"] = workflow
    record["selected_authority"] = selected

    with pytest.raises(CompiledPlanExportError, match="authority fingerprint mismatch"):
        verify_compiled_plan_export_record(record)


def _mutate_learning_source(source: dict[str, object], case: str) -> None:
    effect = _record(
        source,
        "effect_declarations",
        "learning.effect.curator.workspace_skill_update",
    )
    generated = _record(source, "generated_work_routes", "learning.trigger.analyst")
    analyst_wait = _record(source, "operator_waits", "learning.analyst_blocked_wait")
    analyst_route = _record(
        source, "terminal_actions", "learning.route_analyst_complete"
    )
    professor = _record(source, "stage_kinds", "professor")
    analyst_runner = _record(source, "runner_bindings", "analyst.millforge_runner")
    mutations: dict[str, Callable[[], None]] = {
        "effect_provider": lambda: effect.__setitem__(
            "provider_ref", "provider.real.network"
        ),
        "effect_capability": lambda: effect.__setitem__(
            "capability_policy_ref", "policy.real.install"
        ),
        "effect_real": lambda: effect.__setitem__("real_side_effects_allowed", True),
        "effect_statuses": lambda: effect.__setitem__(
            "allowed_reconciliation_statuses", ("applied", "refused")
        ),
        "effect_action": lambda: effect.__setitem__(
            "terminal_action_id", "learning.close_curator_noop"
        ),
        "effect_artifact": lambda: effect.__setitem__(
            "artifact_schema_id", "learning.artifacts.curator_decision"
        ),
        "effect_target_kind": lambda: effect.__setitem__("target_ref_kind", ""),
        "effect_target_schema": lambda: effect.pop("target_ref_schema"),
        "route_missing_artifact": lambda: analyst_route.__setitem__(
            "artifact_schema_id", "learning.artifacts.missing"
        ),
        "route_stage_result": lambda: analyst_route.__setitem__(
            "artifact_schema_id", "learning.artifacts.stage_result"
        ),
        "route_missing_node": lambda: analyst_route.pop("target_graph_node_id"),
        "route_wrong_owner": lambda: analyst_route.__setitem__(
            "target_graph_node_id", "learning.standard.analyst"
        ),
        "route_queue_contract": lambda: professor.__setitem__(
            "input_queue_family_ids", ()
        ),
        "route_stage_runner": lambda: analyst_route.__setitem__(
            "runner_binding_id", "lad_planner.millforge_runner"
        ),
        "route_runner_stage": lambda: _record(
            source, "runner_bindings", "professor.millforge_runner"
        ).__setitem__("stage_kind_ids", ("analyst",)),
        "generated_queue": lambda: generated.__setitem__(
            "queue_family_id", "missing-learning-queue"
        ),
        "generated_ambiguous": lambda: generated.__setitem__("id", "learning_request"),
        "generated_node": lambda: generated.__setitem__(
            "graph_node_id", "missing.learning.node"
        ),
        "generated_stage": lambda: generated.__setitem__(
            "stage_kind_id", "missing_learning_stage"
        ),
        "generated_schema": lambda: generated.__setitem__(
            "payload_schema_id", "missing.learning.payload"
        ),
        "generated_input": lambda: generated.update(
            {
                "stage_kind_id": "professor",
                "graph_node_id": "learning.standard.professor",
            }
        ),
        "generated_stage_runner": lambda: generated.__setitem__(
            "runner_binding_id", "lad_planner.millforge_runner"
        ),
        "generated_runner_stage": lambda: analyst_runner.__setitem__(
            "stage_kind_ids", ("professor", "curator", "librarian")
        ),
        "concurrency_self": lambda: _record(
            source, "concurrency_policies", "learning.standard"
        ).__setitem__("coexist_partition_ids", ("learning",)),
        "concurrency_duplicate": lambda: _record(
            source, "concurrency_policies", "learning.standard"
        ).__setitem__("coexist_partition_ids", ("planning", "planning")),
        "concurrency_asymmetric": lambda: _record(
            source, "concurrency_policies", "foreground.execution"
        ).__setitem__("coexist_partition_ids", ()),
        "concurrency_missing": lambda: _record(
            source, "concurrency_policies", "foreground.execution"
        ).__setitem__("coexist_partition_ids", ("missing",)),
        "wait_resolution": lambda: analyst_wait.__setitem__(
            "allowed_resolution_kinds",
            ("resume_recorded_source", "delegate_to_learning"),
        ),
        "wait_queue": lambda: analyst_wait.__setitem__(
            "target_queue_family_id", "stage_result"
        ),
        "wait_schema": lambda: analyst_wait.__setitem__(
            "payload_schema_id", "learning.artifacts.report"
        ),
        "wait_action": lambda: _record(
            source, "terminal_actions", "learning.close_analyst_blocked"
        ).__setitem__("kind", "block_work_item"),
        "trigger_contract": lambda: _record(
            source, "generated_work_routes", "learning.trigger.librarian"
        ).__setitem__("payload_schema_id", "learning.artifacts.stage_result"),
        "needs_planning_route": lambda: _record(
            source, "terminal_actions", "execution.close_consultant_needs_plan"
        ).__setitem__("target_graph_node_id", "planning.lad.planner.start"),
        "needs_planning_old_kind": lambda: _record(
            source, "terminal_actions", "execution.close_consultant_needs_plan"
        ).__setitem__("kind", "escalate_to_planning"),
    }
    mutations[case]()


@pytest.mark.parametrize(
    ("case", "diagnostic_code"),
    (
        ("effect_provider", "invalid_effect_declaration"),
        ("effect_capability", "invalid_effect_declaration"),
        ("effect_real", "invalid_effect_declaration"),
        ("effect_statuses", "invalid_effect_declaration"),
        ("effect_action", "invalid_effect_declaration"),
        ("effect_artifact", "invalid_effect_declaration"),
        ("effect_target_kind", "invalid_effect_declaration"),
        ("effect_target_schema", "invalid_effect_declaration"),
        ("route_missing_artifact", "missing_reference"),
        ("route_stage_result", "terminal_route_artifact_schema_mismatch"),
        ("route_missing_node", "terminal_route_missing_field"),
        ("route_wrong_owner", "terminal_route_graph_node_stage_mismatch"),
        ("route_queue_contract", "terminal_route_stage_input_mismatch"),
        ("route_stage_runner", "terminal_route_stage_runner_mismatch"),
        ("route_runner_stage", "terminal_route_runner_stage_mismatch"),
        ("generated_queue", "missing_reference"),
        ("generated_ambiguous", "ambiguous_selected_enqueue_route"),
        ("generated_node", "missing_reference"),
        ("generated_stage", "missing_reference"),
        ("generated_schema", "missing_reference"),
        ("generated_input", "generated_work_route_stage_input_mismatch"),
        ("generated_stage_runner", "generated_work_route_stage_runner_mismatch"),
        ("generated_runner_stage", "generated_work_route_runner_stage_mismatch"),
        ("concurrency_self", "invalid_concurrency_policy"),
        ("concurrency_duplicate", "invalid_concurrency_policy"),
        ("concurrency_asymmetric", "invalid_concurrency_policy"),
        ("concurrency_missing", "missing_reference"),
        ("wait_resolution", "invalid_operator_wait_field"),
        ("wait_queue", "intervention_target_route_mismatch"),
        ("wait_schema", "intervention_target_payload_schema_mismatch"),
        ("wait_action", "invalid_operator_wait_action_kind"),
        ("trigger_contract", "invalid_fanout_declaration"),
        ("needs_planning_route", "terminal_close_with_escalation_route_authority"),
        ("needs_planning_old_kind", "unsupported_terminal_action_kind"),
    ),
)
def test_learning_compiler_refuses_exact_authority_mutations(
    case: str,
    diagnostic_code: str,
) -> None:
    source = _source()
    _mutate_learning_source(source, case)

    error = _error(source, diagnostic_code)

    assert error.severity == "error"


def test_learning_compiler_refuses_duplicate_effect_action_binding() -> None:
    source = _source()
    effects = _records(source, "effect_declarations")
    duplicate = dict(effects[1])
    duplicate.update(
        {
            "id": "learning.effect.duplicate.curator",
            "terminal_action_id": "learning.close_curator_complete",
            "artifact_schema_id": "learning.artifacts.skill_update",
            "target_ref_kind": "workspace_skill_update",
            "target_ref_schema": "learning.effects.target.workspace_skill_update.v1",
        }
    )
    source["effect_declarations"] = (*effects, duplicate)

    error = _error(source, "invalid_effect_declaration")

    assert error.declaration_path.endswith(".terminal_action_id")


@pytest.mark.parametrize(
    "compatibility_profile",
    (
        "lad_codex",
        "learning_lad_codex",
        "blueprint_lad_codex",
        "blueprint_learning_lad_codex",
    ),
)
def test_full_lad_compiler_refuses_legacy_alias_authority(
    compatibility_profile: str,
) -> None:
    source = _source()
    workflow = cast(Record, source["workflow"])
    workflow["compatibility_profile"] = compatibility_profile

    error = _error(source, "unsupported_compatibility_profile")

    assert error.declaration_path == "workflow.compatibility_profile"
    assert error.context["compatibility_profile"] == compatibility_profile


_RESTART_LEDGER = (
    ("trigger_generated_learning_work_and_active_learning", 1, "selected_authority"),
    ("two_active_learning_runs", 1, "selected_authority"),
    ("generated_route_missing_queue", 1, "compiler_refusal"),
    ("generated_route_structural_corruption", 6, "compiler_refusal"),
    ("concurrency_policy_shape_corruption", 3, "compiler_refusal"),
    ("learning_recovery_authority_drift", 1, "compiler_refusal"),
    ("operator_wait_revise_schema_drift", 1, "compiler_refusal"),
    ("operator_wait_revise_target_drift", 7, "compiler_refusal"),
    ("c3_cross_record_persistence_corruption", 33, "generic_runtime_n_a"),
    ("stage_artifact_route_close_and_block", 8, "selected_authority"),
    ("route_artifact_schema_contract_drift", 2, "compiler_refusal"),
    ("stage_result_terminal_reselection", 2, "compiler_refusal"),
    ("static_route_authority_drift", 6, "compiler_refusal"),
    ("learning_with_active_foreground", 2, "selected_authority"),
    ("coexist_policy_drift", 1, "compiler_refusal"),
)

_STATUS_LEDGER = (
    ("trigger_and_concurrency_context", 1, "selected_authority"),
    ("terminal_action_source_action_and_input_context", 1, "selected_authority"),
    ("c3_family_combined_after_restart", 1, "selected_authority"),
    ("closure_root_learning_aftermath", 8, "selected_authority"),
)

_FULL_RESTART_LEDGER = (
    ("selected_plan_drift", 1, "export_fingerprint_refusal"),
    ("learning_closure_effect_and_intervention", 1, "selected_authority"),
    ("closure_root_persistence_corruption", 64, "generic_runtime_n_a"),
)

_FULL_STATUS_LEDGER = (
    ("full_lad_projection", 1, "selected_authority"),
    ("closure_root_learning_aftermath", 8, "selected_authority"),
)

_OFFICIAL_OWNER_LEDGER = {
    "compiler/test_lad_learning_compile.py": (
        "selected topology/schema/action/effect/wait authority",
        "exact compiler negative mutations",
    ),
    "compiler/test_lad_full_conformance.py": (
        "deterministic export and fingerprint",
        "C3 cross-plane selected authority",
        "legacy alias and export drift refusals",
    ),
    "kernel/test_lad_learning_intake_dispatch.py": (
        "selected external/generated/static route authority",
        "compiler refusals replace duplicate admission mutations",
    ),
    "kernel/test_lad_learning_triggers_concurrency.py": (
        "selected trigger, close source, and coexist authority",
    ),
    "kernel/test_lad_learning_artifacts_effects.py": (
        "selected artifact/action and fake-local effect authority",
        "generic effect transition/replay behavior is N/A",
    ),
    "kernel/test_lad_learning_recovery.py": (
        "selected operator-wait/revise authority",
        "generic intervention transition/provenance behavior is N/A",
    ),
    "kernel/test_lad_full_conformance.py": (
        "selected full-LAD cross-plane combinations",
    ),
    "operator/test_lad_learning_status_projection.py": (
        "11-row Learning status ledger",
    ),
    "operator/test_lad_full_status_projection.py": ("9-row full-LAD status ledger",),
    "substrate/test_lad_learning_restart.py": ("75-row Learning restart ledger",),
    "substrate/test_lad_full_restart.py": ("66-row full-LAD restart ledger",),
    "guardrails/test_lad_learning_compatibility_refusal.py": (
        "package-owned selected data",
        "generic source scan is N/A",
    ),
    "guardrails/test_lad_full_compatibility_refusal.py": (
        "package-owned selected data",
        "generic source scan is N/A",
    ),
}

_C3_GENERIC_RUNTIME_N_A_ROWS = frozenset(
    {
        "resolved_wait_missing_closed_source",
        "artifact_source_action",
        "fanout_target_lineage",
        "fanout_target_queue",
        "artifact_schema",
        "artifact_source_input",
        "artifact_payload_digest",
        "artifact_source_run",
        "effect_plan_id",
        "effect_plan_fingerprint",
        "effect_artifact_payload_digest",
        "effect_source_run",
        "effect_source_work_item",
        "effect_source_activation",
        "effect_graph_node",
        "effect_stage_kind",
        "effect_runner_binding",
        "effect_queue_family",
        "effect_provider",
        "effect_capability_policy",
        "effect_target_skill",
        "effect_status",
        "reconciliation_status",
        "reconciliation_effect_id",
        "reconciliation_provider",
        "wait_operator_id",
        "wait_source_action",
        "wait_source_graph_node",
        "wait_actor_on_active",
        "wait_status",
        "resolved_wait_actor_kind",
        "resolved_wait_resolution",
        "closed_source_created_input",
    }
)


def test_learning_restart_and_status_source_to_plus_ledgers_are_complete() -> None:
    assert sum(rows for _owner, rows, _disposition in _RESTART_LEDGER) == 75
    assert sum(rows for _owner, rows, _disposition in _STATUS_LEDGER) == 11
    assert {
        owner
        for owner, _rows, disposition in _RESTART_LEDGER
        if disposition == "generic_runtime_n_a"
    } == {"c3_cross_record_persistence_corruption"}
    assert len(_C3_GENERIC_RUNTIME_N_A_ROWS) == 33
    assert all(
        disposition == "selected_authority"
        for _owner, _rows, disposition in _STATUS_LEDGER
    )
    assert sum(rows for _owner, rows, _disposition in _FULL_RESTART_LEDGER) == 66
    assert sum(rows for _owner, rows, _disposition in _FULL_STATUS_LEDGER) == 9
    assert _FULL_RESTART_LEDGER[-1] == (
        "closure_root_persistence_corruption",
        64,
        "generic_runtime_n_a",
    )
    assert len(_OFFICIAL_OWNER_LEDGER) == 13


def test_unselected_catalog_does_not_change_full_lad_authority() -> None:
    source = _source()
    base = conformance.compile_packaged_workflow(PACKAGE_ROOT, WORKFLOW_ID)
    source["unselected_catalog"] = (
        {
            "id": "legacy-blueprint-learning-evidence",
            "catalog_payload": {
                "mode_id": "blueprint_learning_lad_codex",
                "legacy_alias": "learning_lad_codex",
            },
        },
    )
    result = compile_workflow(deepcopy(source))
    assert result.plan is not None

    assert authority_fingerprint(result.plan) == authority_fingerprint(base)
    assert canonical_authority_bytes(result.plan) == canonical_authority_bytes(base)
