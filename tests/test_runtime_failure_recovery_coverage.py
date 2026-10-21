from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from millrace.contracts.compiled_plan import SelectedCompiledPlan

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
FAILURE_KINDS = (
    "invocation_failed",
    "output_too_large",
    "result_parse_failed",
    "result_rejected",
    "timeout",
)

_EXECUTION_ACTIONS = {
    "lad_builder": "execution.recover_builder_runtime_failure",
    "lad_checker": "execution.recover_checker_runtime_failure",
    "lad_fixer": "execution.recover_fixer_runtime_failure",
    "lad_doublechecker": "execution.recover_doublechecker_runtime_failure",
    "lad_updater": "execution.recover_updater_runtime_failure",
    "lad_troubleshooter": "execution.recover_troubleshooter_runtime_failure",
    "lad_consultant": "execution.recover_consultant_runtime_failure",
    "lad_integrator": "execution.recover_integrator_runtime_failure",
}
_PLANNING_ACTIONS = {
    "recon": "planning.recover_recon_runtime_failure",
    "lad_planner": "planning.recover_planner_runtime_failure",
    "lad_manager": "planning.recover_manager_runtime_failure",
    "lad_mechanic": "planning.recover_mechanic_runtime_failure",
    "lad_auditor": "planning.recover_auditor_runtime_failure",
    "lad_arbiter": "planning.recover_arbiter_runtime_failure",
}
_WORKFLOWS = {
    "execution.lad": (
        _EXECUTION_ACTIONS.keys() - {"lad_integrator"},
        "lad_troubleshooter",
    ),
    "execution.lad_integrator": (_EXECUTION_ACTIONS.keys(), "lad_troubleshooter"),
    "planning.lad": (
        _PLANNING_ACTIONS.keys() | (_EXECUTION_ACTIONS.keys() - {"lad_integrator"}),
        None,
    ),
    "lad.full": (
        _PLANNING_ACTIONS.keys() | (_EXECUTION_ACTIONS.keys() - {"lad_integrator"}),
        None,
    ),
    "lad.full.pi": (
        _PLANNING_ACTIONS.keys() | (_EXECUTION_ACTIONS.keys() - {"lad_integrator"}),
        None,
    ),
}


@pytest.mark.parametrize("workflow_id", tuple(_WORKFLOWS))
def test_selected_historical_roles_declare_every_supported_mechanical_failure(
    workflow_id: str,
) -> None:
    expected_roles, _ = _WORKFLOWS[workflow_id]
    plan = cast(
        SelectedCompiledPlan,
        conformance.compile_packaged_workflow(
            PACKAGE_ROOT,
            workflow_id,
            include_pi=workflow_id == "lad.full.pi",
        ),
    )
    stages = {str(stage.id): stage for stage in plan.stage_kinds}
    bindings = {str(binding.id): binding for binding in plan.runner_bindings}
    actions = {str(action.id): action for action in plan.terminal_actions}
    outcomes = {str(outcome.id): outcome for outcome in plan.terminal_outcomes}
    policies_by_source_action: dict[str, list[Any]] = {}
    for policy in plan.recovery_policies:
        for action_id in policy.source_recovery_action_ids:
            policies_by_source_action.setdefault(str(action_id), []).append(policy)

    for stage_id in sorted(expected_roles):
        stage = stages[stage_id]
        action_id = (
            _PLANNING_ACTIONS[stage_id]
            if stage_id in _PLANNING_ACTIONS
            else _EXECUTION_ACTIONS[stage_id]
        )
        binding = bindings[str(stage.runner_binding_id)]
        actual_stage_mappings = {
            (str(mapping.stage_kind_id), mapping.failure_kind, str(mapping.action_id))
            for mapping in getattr(binding, "runtime_failure_mappings", ())
            if str(mapping.stage_kind_id) == stage_id
        }
        expected_mappings = {
            (stage_id, failure_kind, action_id) for failure_kind in FAILURE_KINDS
        }
        assert actual_stage_mappings == expected_mappings, f"{workflow_id}:{stage_id}"

        action = actions[action_id]
        assert str(action.stage_kind_id) == stage_id
        assert action.action_kind == "recovery_route"
        assert action.artifact_schema_id is None
        outcome = outcomes[str(action.outcome_id)]
        assert str(outcome.stage_kind_id) == stage_id
        assert outcome.marker == "RUNTIME_FAILURE"
        recovery_stage_id = (
            "lad_mechanic" if stage_id in _PLANNING_ACTIONS else "lad_troubleshooter"
        )
        assert str(action.target_stage_kind_id) == recovery_stage_id
        recovery_stage = stages[recovery_stage_id]
        assert action.runner_binding_id == recovery_stage.runner_binding_id
        assert action.target_graph_node_id in {
            node_id for graph in plan.graphs for node_id in graph.node_ids
        }
        matching_policies = policies_by_source_action.get(action_id, [])
        assert len(matching_policies) == 1, (workflow_id, stage_id, action_id)
        assert str(matching_policies[0].recovery_stage_kind_id) == recovery_stage_id

    execution_sources = {
        _EXECUTION_ACTIONS[stage_id]
        for stage_id in expected_roles
        if stage_id in _EXECUTION_ACTIONS
    }
    planning_sources = {
        _PLANNING_ACTIONS[stage_id]
        for stage_id in expected_roles
        if stage_id in _PLANNING_ACTIONS
    }
    for policy_id, sources, recovery_stage_id in (
        (
            "execution.runtime_failure_recovery",
            execution_sources,
            "lad_troubleshooter",
        ),
        (
            "planning.runtime_failure_recovery",
            planning_sources,
            "lad_mechanic",
        ),
    ):
        if not sources:
            continue
        policy = next(
            policy
            for policy in plan.recovery_policies
            if str(policy.id) == policy_id
        )
        assert set(map(str, policy.source_recovery_action_ids)) == sources
        assert str(policy.recovery_stage_kind_id) == recovery_stage_id
        assert tuple(map(str, policy.return_action_ids)) == (
            "execution.return_troubleshooter_recovered"
            if recovery_stage_id == "lad_troubleshooter"
            else "planning.return_mechanic_recovered",
        )
        assert tuple(map(str, policy.quarantine_action_ids)) == (
            "execution.quarantine_troubleshooter_blocked"
            if recovery_stage_id == "lad_troubleshooter"
            else "planning.quarantine_mechanic_blocked",
        )
        assert policy.recorded_source_selector == "latest_recovery_attempt_for_lineage"
        assert policy.attempt_scope == "lineage"
        assert policy.immediate_recovery_limit == 1
        assert policy.cooldown_starts_at_attempt == 2
        assert policy.default_cooldown_seconds == 900
        assert policy.quarantine_threshold_attempt == 3
        assert policy.threshold_behavior == "runtime_quarantine_at_threshold"
        assert tuple(policy.return_allowed_phases) == (
            "active_recovery",
            "quarantine_eligible",
        )
        expected_resets = (
            {
                "execution.route_builder_complete",
                "execution.route_checker_pass",
                "execution.route_fixer_complete",
                "execution.route_doublechecker_pass",
                "execution.close_updater_complete",
                "execution.return_troubleshooter_complete",
                "execution.route_consultant_complete",
                *(
                    {"execution.route_integrator_complete"}
                    if workflow_id == "execution.lad_integrator"
                    else set()
                ),
            }
            if recovery_stage_id == "lad_troubleshooter"
            else {
                "planning.recon_enqueue_task",
                "planning.recon_enqueue_spec",
                "planning.recon_noop",
                "planning.route_planner_complete",
                "planning.close_manager_complete",
                "planning.route_mechanic_complete",
                "planning.route_auditor_complete",
                "planning.close_arbiter_complete",
            }
        )
        assert set(map(str, policy.reset_trigger_action_ids)) == expected_resets
        wait = next(
            wait
            for wait in plan.wait_states
            if str(wait.id) == str(policy.cooldown_wait_state_id)
        )
        assert wait.wait_kind == "timer"
        assert wait.starts_at_attempt == 2
        assert wait.duration_seconds == 900
        assert str(wait.policy_id) == policy_id


@pytest.mark.parametrize("workflow_id", ("lad.full", "lad.full.pi"))
def test_learning_stages_keep_no_default_runtime_recovery(workflow_id: str) -> None:
    plan = cast(
        SelectedCompiledPlan,
        conformance.compile_packaged_workflow(
            PACKAGE_ROOT,
            workflow_id,
            include_pi=workflow_id == "lad.full.pi",
        ),
    )
    bindings = {str(binding.id): binding for binding in plan.runner_bindings}
    for stage_id in ("analyst", "professor", "curator", "librarian"):
        stage = next(stage for stage in plan.stage_kinds if str(stage.id) == stage_id)
        mappings = getattr(
            bindings[str(stage.runner_binding_id)], "runtime_failure_mappings", ()
        )
        assert not any(str(mapping.stage_kind_id) == stage_id for mapping in mappings)


def test_selected_recovery_prompts_inspect_bounded_runtime_failure_evidence() -> None:
    expected_assets = {
        "execution.lad": "execution.entrypoints.lad_troubleshooter",
        "planning.lad": "planning.entrypoints.lad_mechanic",
        "lad.full.pi": "lad.full.pi.entrypoints.lad_mechanic",
    }
    for workflow_id, recovery_asset_id in expected_assets.items():
        plan = cast(
            SelectedCompiledPlan,
            conformance.compile_packaged_workflow(
                PACKAGE_ROOT,
                workflow_id,
                include_pi=workflow_id == "lad.full.pi",
            ),
        )
        assets = {str(asset.id): asset.body for asset in plan.assets}
        recovery_prompt = " ".join(assets[recovery_asset_id].split())
        assert "governance_context.runtime_failure_source" in recovery_prompt
        assert "bounded `diagnostic`" in recovery_prompt
        assert "evidence about the failed invocation only" in recovery_prompt
        assert "Checker baseline" in recovery_prompt
        recovered_marker = (
            "TROUBLESHOOT_RECOVERED"
            if "troubleshooter" in recovery_asset_id
            else "MECHANIC_RECOVERED"
        )
        assert recovered_marker in recovery_prompt
        troubleshooter_asset_id = (
            "lad.full.pi.entrypoints.lad_troubleshooter"
            if workflow_id == "lad.full.pi"
            else "execution.entrypoints.lad_troubleshooter"
        )
        troubleshooter = " ".join(assets[troubleshooter_asset_id].split())
        assert "governance_context.runtime_failure_source" in troubleshooter
        assert "bounded `diagnostic`" in troubleshooter
        assert "not a terminal result, accepted artifact" in troubleshooter
        assert "TROUBLESHOOT_RECOVERED" in troubleshooter


@pytest.mark.parametrize("workflow_id", tuple(_WORKFLOWS))
def test_existing_blocked_and_fix_needed_policies_remain_unchanged(
    workflow_id: str,
) -> None:
    plan = cast(
        SelectedCompiledPlan,
        conformance.compile_packaged_workflow(
            PACKAGE_ROOT,
            workflow_id,
            include_pi=workflow_id == "lad.full.pi",
        ),
    )
    policies = {str(policy.id): policy for policy in plan.recovery_policies}
    fix_needed = policies["execution.fix_needed_recovery"]
    assert tuple(map(str, fix_needed.source_recovery_action_ids)) == (
        "execution.route_checker_fix_needed",
        "execution.route_doublechecker_fix_needed",
    )
    assert str(fix_needed.recovery_stage_kind_id) == "lad_troubleshooter"
    assert tuple(map(str, fix_needed.return_action_ids)) == (
        "execution.return_troubleshooter_recovered",
    )
    assert tuple(map(str, fix_needed.quarantine_action_ids)) == (
        "execution.quarantine_troubleshooter_blocked",
    )
    assert (
        fix_needed.immediate_recovery_limit,
        fix_needed.cooldown_starts_at_attempt,
        fix_needed.default_cooldown_seconds,
        fix_needed.quarantine_threshold_attempt,
    ) == (1, 2, 900, 3)

    blocked = policies["execution.blocked_recovery"]
    blocked_sources = [
        "execution.route_builder_blocked",
    ]
    if workflow_id == "execution.lad_integrator":
        blocked_sources.append("execution.route_integrator_blocked")
    blocked_sources.extend(
        (
            "execution.route_checker_blocked",
            "execution.route_fixer_blocked",
            "execution.route_doublechecker_blocked",
            "execution.route_updater_blocked",
            "execution.route_troubleshooter_blocked",
        )
    )
    assert tuple(map(str, blocked.source_recovery_action_ids)) == tuple(blocked_sources)
    assert str(blocked.recovery_stage_kind_id) == "lad_consultant"
    assert tuple(map(str, blocked.return_action_ids)) == (
        "execution.return_consultant_recovered",
    )
    assert tuple(map(str, blocked.quarantine_action_ids)) == (
        "execution.quarantine_consultant_blocked",
    )
    assert (
        blocked.immediate_recovery_limit,
        blocked.cooldown_starts_at_attempt,
        blocked.default_cooldown_seconds,
        blocked.quarantine_threshold_attempt,
    ) == (1, 2, 900, 3)

    if workflow_id in ("planning.lad", "lad.full", "lad.full.pi"):
        planning_blocked = policies["planning.blocked.recovery"]
        assert tuple(map(str, planning_blocked.source_recovery_action_ids)) == (
            "planning.route_planner_blocked",
            "planning.route_manager_blocked",
            "planning.route_mechanic_blocked",
            "planning.route_auditor_blocked",
        )
        assert str(planning_blocked.recovery_stage_kind_id) == "lad_mechanic"
        assert tuple(map(str, planning_blocked.return_action_ids)) == (
            "planning.return_mechanic_recovered",
        )
        assert tuple(map(str, planning_blocked.quarantine_action_ids)) == (
            "planning.quarantine_mechanic_blocked",
        )
        assert (
            planning_blocked.immediate_recovery_limit,
            planning_blocked.cooldown_starts_at_attempt,
            planning_blocked.default_cooldown_seconds,
            planning_blocked.quarantine_threshold_attempt,
        ) == (1, 2, 900, 2)
