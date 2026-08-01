from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from millrace.compiler import authority_fingerprint, compile_workflow

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.0"
WORKFLOW_IDS = ("execution.lad", "execution.lad_integrator")
Record = dict[str, object]


def _manifest() -> dict[str, Any]:
    return conformance.assert_packaged_asset_closure(PACKAGE_ROOT)


def _source(workflow_id: str = WORKFLOW_IDS[0]) -> dict[str, object]:
    return conformance.packaged_workflow_source(PACKAGE_ROOT, workflow_id)


def _records(source: dict[str, object], section: str) -> list[Record]:
    return cast(list[Record], source[section])


def _record(source: dict[str, object], section: str, record_id: str) -> Record:
    return next(item for item in _records(source, section) if item["id"] == record_id)


def _error(result: object, code: str) -> object:
    return next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error" and diagnostic.code == code
    )


def test_execution_lad_authority_and_assets_are_package_owned() -> None:
    manifest = _manifest()
    workflows = conformance.workflows_by_id(manifest)
    assets = conformance.assets_by_id(manifest)
    execution_assets = {
        asset_id for asset_id in assets if asset_id.startswith("execution.")
    }

    assert len(execution_assets) == 16
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
        headings = (
            ("Role:", "Scope:", "Legal terminal markers rendered by runtime:")
            if ".entrypoints." in asset_id
            else ("## Artifact Schema", "## Valid Example", "## Completion Criteria")
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
            "execution.artifacts.stage_result",
        ),
        "execution.route_checker_fix_needed": (
            "route",
            "lad_fixer",
            "execution.lad.fixer.start",
            "stage_result",
            "execution.artifacts.stage_result",
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
            "execution.artifacts.stage_result",
        ),
        "execution.route_doublechecker_fix_needed": (
            "route",
            "lad_fixer",
            "execution.lad.fixer.start",
            "stage_result",
            "execution.artifacts.stage_result",
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
