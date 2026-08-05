from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from millrace.compiler import compile_workflow
from millrace.contracts.schema import validate_schema

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.2"
WORKFLOW_ID = "planning.lad"
RECON_WORKFLOW_IDS = ("planning.lad", "lad.full")
Record = dict[str, object]

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
        "rubric",
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
