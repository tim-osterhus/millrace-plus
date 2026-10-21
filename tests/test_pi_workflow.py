from __future__ import annotations

import copy
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from millrace.adapters.runner_contract import RedactionPolicy
from millrace.compiler import (
    authority_fingerprint,
    compile_workflow,
    compiled_plan_export_bytes,
    verify_compiled_plan_export_bytes,
)
from millrace.contracts.compiled_plan import (
    RunnerBindingWithPayloadCapacityDeclaration,
    RunnerBindingWithRuntimeFailuresAndPayloadCapacityDeclaration,
)
from millrace.contracts.runner import (
    RunnerResultEvidence,
    runner_result_evidence_bytes,
    runner_result_evidence_from_payload,
    runner_result_payload,
)
from millrace.contracts.runner_payload_capacity import (
    installed_payload_capacity_pin,
    payload_capacity_pin_record,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.4"
CORE_COMPATIBILITY = ">=0.22.4,<0.23"
PI_WORKFLOW_ID = "lad.full.pi"
OLD_WORKFLOW_ID = "lad.full"
REQUIRED_PI_CAPABILITIES = {
    "unrestricted.filesystem.read",
    "unrestricted.filesystem.write",
    "unrestricted.process.execute",
}
PI_SKILL_REMAP = {
    "planning.skills.planner_core": "lad.full.pi.skills.planner_core",
    "planning.skills.manager_core": "lad.full.pi.skills.manager_core",
    "planning.skills.mechanic_core": "lad.full.pi.skills.mechanic_core",
    "planning.skills.auditor_core": "lad.full.pi.skills.auditor_core",
    "learning.skills.analyst_core": "lad.full.pi.skills.analyst_core",
    "learning.skills.professor_core": "lad.full.pi.skills.professor_core",
    "learning.skills.curator_core": "lad.full.pi.skills.curator_core",
}
PI_RUNTIME_FAILURE_ACTIONS = {
    "recon": "planning.recover_recon_runtime_failure",
    "lad_planner": "planning.recover_planner_runtime_failure",
    "lad_manager": "planning.recover_manager_runtime_failure",
    "lad_mechanic": "planning.recover_mechanic_runtime_failure",
    "lad_auditor": "planning.recover_auditor_runtime_failure",
    "lad_arbiter": "planning.recover_arbiter_runtime_failure",
    "lad_builder": "execution.recover_builder_runtime_failure",
    "lad_checker": "execution.recover_checker_runtime_failure",
    "lad_fixer": "execution.recover_fixer_runtime_failure",
    "lad_doublechecker": "execution.recover_doublechecker_runtime_failure",
    "lad_updater": "execution.recover_updater_runtime_failure",
    "lad_troubleshooter": "execution.recover_troubleshooter_runtime_failure",
    "lad_consultant": "execution.recover_consultant_runtime_failure",
}
PI_RUNTIME_FAILURE_KINDS = (
    "invocation_failed",
    "output_too_large",
    "result_parse_failed",
    "result_rejected",
    "timeout",
)
PI_SCHEMA_V6_STAGES = frozenset(
    {
        "lad_builder",
        "lad_checker",
        "lad_fixer",
        "lad_doublechecker",
        "lad_updater",
        "lad_troubleshooter",
    }
)


def _manifest() -> dict[str, Any]:
    return conformance.assert_packaged_asset_closure(PACKAGE_ROOT, include_pi=True)


def _capacity_pin() -> dict[str, object]:
    return payload_capacity_pin_record(installed_payload_capacity_pin())


def _select_and_verify_package(
    tmp_path: Path,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
) -> object:
    from millrace.operator import (
        PackageMutationCommand,
        PackageWorkflowSelectionCommand,
        PackageWorkflowVerifyCommand,
        execute_package_mutation_command,
        execute_package_verify_command,
        execute_package_workflow_selection_command,
    )
    from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore

    tmp_path.mkdir(parents=True, exist_ok=True)
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="test-import",
            operation_id="package.import_path",
            actor_id="operator:test",
            package_root=PACKAGE_ROOT,
        ),
    )
    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="test-enable",
            operation_id="package.enable",
            actor_id="operator:test",
            package_id=package_id,
            package_version=package_version,
        ),
    )
    verified = execute_package_verify_command(
        store,
        cas_store,
        PackageWorkflowVerifyCommand(
            command_id="test-verify",
            actor_id="operator:test",
            package_id=package_id,
            package_version=package_version,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
        ),
    )
    selected = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id="test-select",
            actor_id="operator:test",
            package_id=package_id,
            package_version=package_version,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
        ),
    )
    assert imported.outcome == "succeeded"
    assert enabled.outcome == "succeeded"
    assert verified.outcome == "succeeded", verified.diagnostics
    assert verified.plan_ready
    assert selected.outcome == "succeeded", selected.diagnostics
    assert selected.plan is not None
    return selected.plan


def _workflow(manifest: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    return next(
        workflow
        for workflow in manifest["workflows"]
        if workflow["workflow_id"] == workflow_id
    )


def _source(manifest: dict[str, Any], workflow_id: str) -> dict[str, object]:
    return conformance.packaged_workflow_source(
        PACKAGE_ROOT,
        workflow_id,
        include_pi=workflow_id == PI_WORKFLOW_ID,
    )


def _assert_pi_compiled_binding_types(bindings: Sequence[Any]) -> None:
    for binding in bindings:
        stage_ids = [str(stage_id) for stage_id in binding.stage_kind_ids]
        assert len(stage_ids) == 1
        expected_type = (
            RunnerBindingWithRuntimeFailuresAndPayloadCapacityDeclaration
            if stage_ids[0] in PI_RUNTIME_FAILURE_ACTIONS
            else RunnerBindingWithPayloadCapacityDeclaration
        )
        assert type(binding) is expected_type


def _map_strings(value: object, mapping: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _map_strings(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def test_pi_workflow_is_private_and_component_free() -> None:
    manifest = _manifest()
    workflow = _workflow(manifest, PI_WORKFLOW_ID)
    selected = workflow["selected_authority"]

    assert manifest["package"]["package_version"] == PACKAGE_VERSION
    assert (
        manifest["package"]["base_millrace_compatibility"]
        == CORE_COMPATIBILITY
    )
    assert workflow["workflow_version"] == "0.1"
    assert workflow["visibility"] == "private"
    assert selected["workflow"]["id"] == PI_WORKFLOW_ID
    assert len(selected["stage_kinds"]) == 17
    assert len(selected["runner_bindings"]) == 17

    declarations = {item["id"]: item for item in selected["capabilities"]}
    assert REQUIRED_PI_CAPABILITIES <= set(declarations)
    for binding in selected["runner_bindings"]:
        stage_id = binding["stage_kind_ids"][0]
        has_runtime_failure_mapping = stage_id in PI_RUNTIME_FAILURE_ACTIONS
        assert binding["adapter_kind"] == "pi_rpc"
        assert binding["component_pin"] is None
        assert binding["terminal_result_mappings"] == []
        assert binding["schema_version"] == (
            6 if stage_id in PI_SCHEMA_V6_STAGES else 4
        )
        assert binding["payload_capacity_pin"] == _capacity_pin()
        if has_runtime_failure_mapping:
            assert binding["runtime_failure_mappings"] == [
                {
                    "stage_kind_id": stage_id,
                    "failure_kind": failure_kind,
                    "action_id": PI_RUNTIME_FAILURE_ACTIONS[stage_id],
                }
                for failure_kind in PI_RUNTIME_FAILURE_KINDS
            ]
        else:
            assert "runtime_failure_mappings" not in binding
        assert REQUIRED_PI_CAPABILITIES <= set(binding["required_capability_ids"])
        for capability_id in binding["required_capability_ids"]:
            declaration = declarations[capability_id]
            assert declaration["support_status"] == "supported"
            assert declaration["grant_status"] == "granted"
            assert declaration["approval_policy_id"] is None


def test_pi_workflow_declares_followup_stage_timeouts() -> None:
    selected = _workflow(_manifest(), PI_WORKFLOW_ID)["selected_authority"]
    for binding in selected["runner_bindings"]:
        expected = 9_600 if binding["stage_kind_ids"] == ["lad_builder"] else 7_200
        assert binding["invocation_timeout_seconds"] == expected


def test_pi_runtime_failure_mappings_select_only_owned_recovery_routes() -> None:
    manifest = _manifest()
    selected = _workflow(manifest, PI_WORKFLOW_ID)["selected_authority"]
    bindings = {
        binding["stage_kind_ids"][0]: binding
        for binding in selected["runner_bindings"]
    }
    actions = {action["id"]: action for action in selected["terminal_actions"]}
    outcomes = {outcome["id"]: outcome for outcome in selected["terminal_outcomes"]}
    policies = selected["recovery_policies"]

    for stage_id, action_id in PI_RUNTIME_FAILURE_ACTIONS.items():
        binding = bindings[stage_id]
        assert binding["runtime_failure_mappings"] == [
            {
                "stage_kind_id": stage_id,
                "failure_kind": failure_kind,
                "action_id": action_id,
            }
            for failure_kind in PI_RUNTIME_FAILURE_KINDS
        ]
        action = actions[action_id]
        assert action["kind"] == "recovery_route"
        assert action["stage_kind_id"] == stage_id
        outcome = outcomes[action["outcome_id"]]
        assert outcome["stage_kind_id"] == stage_id
        assert outcome["marker"] == "RUNTIME_FAILURE"
        owners = [
            policy
            for policy in policies
            if action_id in policy["source_recovery_action_ids"]
        ]
        assert len(owners) == 1
        expected_policy_id = (
            "planning.runtime_failure_recovery"
            if PI_RUNTIME_FAILURE_ACTIONS[stage_id].startswith("planning.")
            else "execution.runtime_failure_recovery"
        )
        assert owners[0]["id"] == expected_policy_id
        assert owners[0]["recovery_stage_kind_id"] == action["target_stage_kind_id"]
        target_stage = next(
            item
            for item in selected["stage_kinds"]
            if item["id"] == action["target_stage_kind_id"]
        )
        assert target_stage["runner_binding_id"] == action["runner_binding_id"]

    for stage_id, binding in bindings.items():
        if stage_id in PI_RUNTIME_FAILURE_ACTIONS:
            continue
        assert "runtime_failure_mappings" not in binding

    mapped_action_ids = set(PI_RUNTIME_FAILURE_ACTIONS.values())
    for outcome in selected["terminal_outcomes"]:
        if outcome["marker"] != "RUNTIME_FAILURE_ESCALATE":
            continue
        action = next(
            item
            for item in selected["terminal_actions"]
            if item["stage_kind_id"] == outcome["stage_kind_id"]
            and item["outcome_id"] == outcome["id"]
        )
        assert action["kind"] == "block_work_item"
        assert action["id"] not in mapped_action_ids


def test_pi_entrypoints_preserve_original_bytes_and_add_only_transport_clause() -> None:
    manifest = _manifest()
    assets = conformance.assets_by_id(manifest, include_pi=True)
    old_workflow = _workflow(manifest, OLD_WORKFLOW_ID)
    pi_workflow = _workflow(manifest, PI_WORKFLOW_ID)
    old_assets = {
        item["asset_id"]: item for item in old_workflow["required_assets"]
    }
    pi_assets = {
        item["asset_id"]: item for item in pi_workflow["required_assets"]
    }
    for old_id, old_pin in old_assets.items():
        if ".entrypoints." not in old_id:
            continue
        suffix = old_id.rsplit(".", 1)[-1]
        pi_id = f"lad.full.pi.entrypoints.{suffix}"
        old_body = (
            PACKAGE_ROOT / str(assets[old_id]["package_path"])
        ).read_text()
        pi_body = (
            PACKAGE_ROOT / str(assets[pi_id]["package_path"])
        ).read_text()
        core_asset = next(
            (
                skill_id
                for skill_id in PI_SKILL_REMAP
                if f"`{skill_id}`" in old_body
            ),
            None,
        )
        if core_asset is not None:
            pi_body = pi_body.replace(
                f"`{PI_SKILL_REMAP[core_asset]}`", f"`{core_asset}`"
            )
        if old_id == "planning.entrypoints.lad_planner":
            pi_identity_clause = (
                "If selected workflow context says `lad.full` or `lad.full.pi`"
                " and you choose `PLANNER_COMPLETE`"
            )
            old_identity_clause = (
                "If selected workflow context says `lad.full`"
                " and you choose `PLANNER_COMPLETE`"
            )
            assert pi_identity_clause in pi_body
            pi_body = pi_body.replace(pi_identity_clause, old_identity_clause, 1)
        assert pi_body.startswith(old_body)
        assert (
            "replaces the preceding exact-observation-object requirement" in pi_body
        )
        assert pi_assets[pi_id]["content_digest"] == assets[pi_id]["content_digest"]


def test_pi_authority_matches_lad_full_except_explicit_pi_deltas() -> None:
    manifest = _manifest()
    old = _workflow(manifest, OLD_WORKFLOW_ID)
    pi = _workflow(manifest, PI_WORKFLOW_ID)
    old_selected = copy.deepcopy(old["selected_authority"])
    pi_selected = copy.deepcopy(pi["selected_authority"])
    entrypoint_map = {
        f"lad.full.pi.entrypoints.{asset_id.rsplit('.', 1)[-1]}": asset_id
        for asset_id in (
            item["asset_id"]
            for item in old["required_assets"]
            if ".entrypoints." in item["asset_id"]
        )
    }
    entrypoint_map.update(
        {
            pi_asset_id: original_asset_id
            for original_asset_id, pi_asset_id in PI_SKILL_REMAP.items()
        }
    )
    pi_selected = _map_strings(pi_selected, entrypoint_map)
    pi_selected["workflow"]["id"] = old_selected["workflow"]["id"]
    pi_selected["workflow"]["version"] = old_selected["workflow"]["version"]
    for binding in pi_selected["runner_bindings"]:
        binding.pop("payload_capacity_pin", None)
        binding.pop("schema_version", None)
    old_bindings = {item["id"]: item for item in old_selected["runner_bindings"]}
    for binding in pi_selected["runner_bindings"]:
        binding_id = binding["id"]
        binding.clear()
        binding.update(copy.deepcopy(old_bindings[binding_id]))
    # Explicit intended Pi delta: the only completion-behavior differences from
    # lad.full are the 131072-byte inline request limit and the request_overflow
    # declaration. Assert both exactly, then neutralize them to the lad.full values
    # so the final equality still covers every other field, including the 96-item
    # evidence limit.
    old_behaviors = old_selected["completion_behaviors"]
    pi_behaviors = pi_selected["completion_behaviors"]
    assert len(pi_behaviors) == len(old_behaviors)
    for pi_behavior, old_behavior in zip(pi_behaviors, old_behaviors, strict=True):
        assert pi_behavior["request_payload_byte_limit"] == 131072
        assert pi_behavior["request_overflow"] == {
            "policy": "materialized_evidence_pages",
            "materialization_root": "millrace-closure-evidence",
            "page_byte_limit": 32768,
            "max_snapshot_bytes": 8388608,
            "max_page_count": 1024,
        }
        assert pi_behavior["evidence_item_limit"] == 96
        pi_behavior["request_payload_byte_limit"] = old_behavior[
            "request_payload_byte_limit"
        ]
        del pi_behavior["request_overflow"]
    assert pi_selected == old_selected


def test_pi_workflow_compiles_exports_and_is_admission_ready_with_reviewed_core(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    result = compile_workflow(_source(manifest, PI_WORKFLOW_ID))
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    assert errors == []
    assert result.plan is not None
    assert authority_fingerprint(result.plan).startswith("sha256:")
    assert len(result.plan.completion_behaviors) == 1
    assert result.plan.completion_behaviors[0].request_payload_byte_limit == 131072
    _assert_pi_compiled_binding_types(result.plan.runner_bindings)
    assert all(
        payload_capacity_pin_record(binding.payload_capacity_pin) == _capacity_pin()
        for binding in result.plan.runner_bindings
    )

    exported = compiled_plan_export_bytes(result.plan)
    verify_compiled_plan_export_bytes(exported)

    selected = _select_and_verify_package(
        tmp_path / "admission",
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=PI_WORKFLOW_ID,
        workflow_version="0.1",
    )
    assert selected.workflow_package_pin.workflow_id == PI_WORKFLOW_ID
    _assert_pi_compiled_binding_types(selected.runner_bindings)


def test_opaque_fixture_compiles_and_is_admitted_with_artifact_outcome(
    tmp_path: Path,
) -> None:
    fixture_root = PROJECT_ROOT / "tests" / "fixtures" / "pi_workflow"
    fixture_manifest = conformance.assert_packaged_asset_closure(
        fixture_root, include_pi=True
    )
    fixture_result = compile_workflow(
        conformance.packaged_workflow_source(
            fixture_root, "opaque.workflow", include_pi=True
        )
    )
    assert fixture_result.plan is not None
    assert str(fixture_result.plan.workflow.workflow_id) == "opaque.workflow"
    action = fixture_result.plan.terminal_actions[0]
    assert str(action.artifact_schema_id) == "opaque.artifact"
    assert (
        fixture_manifest["package"]["base_millrace_compatibility"]
        == ">=0.22.4.dev3,<0.23"
    )

    selected = conformance.select_and_verify_package(
        tmp_path / "fixture",
        fixture_root,
        package_id="opaque.pi.fixture",
        package_version="0.1",
        workflow_id="opaque.workflow",
        workflow_version="0.1",
    )
    assert selected.workflow_package_pin.package_id == "opaque.pi.fixture"
    assert selected.workflow_package_pin.workflow_id == "opaque.workflow"


def test_ordinary_lad_is_admitted_and_pi_identity_is_distinct(tmp_path: Path) -> None:
    manifest = _manifest()
    selected = _select_and_verify_package(
        tmp_path / "ordinary",
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=OLD_WORKFLOW_ID,
        workflow_version="0.1",
    )
    conformance.assert_selected_package_pin(
        selected,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=OLD_WORKFLOW_ID,
        workflow_version="0.1",
        selected_asset_pins=conformance.selected_asset_pins(
            manifest, OLD_WORKFLOW_ID
        ),
    )
    assert selected.workflow_package_pin.workflow_id == OLD_WORKFLOW_ID
    assert selected.workflow_package_pin.package_version == PACKAGE_VERSION


def test_fixture_final_text_keeps_strict_three_key_envelope() -> None:
    entrypoint = (
        PROJECT_ROOT / "tests" / "fixtures" / "pi_workflow" / "assets" / "entrypoint.md"
    ).read_text()
    assert "marker" in entrypoint
    assert "artifact_payload_candidate" in entrypoint
    assert "observation_payload_candidate" in entrypoint
    assert "fourth" not in entrypoint.lower()


def test_existing_core_report_channel_retains_redacts_and_exposes_limits() -> None:
    artifact = {"artifact_kind": "opaque.artifact", "summary": "unchanged"}
    policy = RedactionPolicy(policy_id="pi-test", secret_tokens=("SECRET",))
    observation = {
        "resume_stage": "lad_planner",
        "runner_report": "check SECRET and preserve routing",
    }
    redacted = policy.redact_authority_value(observation)
    assert redacted["runner_report"] == "check [REDACTED] and preserve routing"

    evidence = RunnerResultEvidence(
        run_id="run-1",
        session_id="session-1",
        dispatch_generation=1,
        session_fencing_token="session-fence-1",
        plan_fingerprint="sha256:" + "a" * 64,
        claim_id="claim-1",
        generation=1,
        fencing_token="fence-1",
        stage_kind_id="lad_planner",
        graph_node_id="lad_planner.start",
        runner_binding_id="lad_planner.millforge_runner",
        marker="PLAN_READY",
        adapter_provenance=None,
        observation_payload=redacted,
        artifact_payload=artifact,
    )
    restored = runner_result_evidence_from_payload(runner_result_payload(evidence))
    assert restored.observation_payload == redacted
    assert restored.artifact_payload == artifact
    assert restored.observation_payload["resume_stage"] == "lad_planner"

    non_string = dict(observation)
    non_string["runner_report"] = {"marker": "UNDECLARED"}
    # The submitted generic codec accepts recursive authority values; Pi-specific
    # adapter validation must reject this shape before evidence retention.
    oversized = dict(observation)
    oversized["runner_report"] = "x" * 100_000
    oversized_evidence = RunnerResultEvidence(
        run_id=evidence.run_id,
        session_id=evidence.session_id,
        dispatch_generation=evidence.dispatch_generation,
        session_fencing_token=evidence.session_fencing_token,
        plan_fingerprint=evidence.plan_fingerprint,
        claim_id=evidence.claim_id,
        generation=evidence.generation,
        fencing_token=evidence.fencing_token,
        stage_kind_id=evidence.stage_kind_id,
        graph_node_id=evidence.graph_node_id,
        runner_binding_id=evidence.runner_binding_id,
        marker=evidence.marker,
        adapter_provenance=None,
        observation_payload=oversized,
        artifact_payload=artifact,
    )
    assert len(runner_result_evidence_bytes(oversized_evidence)) > 100_000
    assert isinstance(non_string["runner_report"], dict)


def test_manifest_has_no_executable_pi_assets() -> None:
    manifest = _manifest()
    for asset in manifest["assets"]:
        if (
            str(asset["asset_id"]).startswith("lad.full.pi.")
            or asset["asset_id"] == "runner.pi_rpc_terminal"
        ):
            package_path = PACKAGE_ROOT / str(asset["package_path"])
            assert package_path.suffix == ".md"
            assert not package_path.name.endswith((".py", ".pyc"))
