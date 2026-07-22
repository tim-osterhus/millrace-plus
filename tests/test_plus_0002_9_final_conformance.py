from __future__ import annotations

# ruff: noqa: E402
import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from support.internal_conformance_gate import require_internal_conformance

require_internal_conformance()

from millforge import describe_millforge_base
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.runner_bindings import RUNNER_ADAPTER_KIND_DEFAULTED
from millrace.compiler.workflow_package_sources import (
    read_archive_workflow_package_source,
    read_path_workflow_package_source,
)
from millrace.contracts import AdmitPlan, InitializeWorkspace
from millrace.contracts.compiled_plan import canonical_authority_bytes
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.kernel import apply, decide, empty_runtime_state
from millrace.operator.packages import (
    PackageMutationCommand,
    PackageWorkflowSelectionCommand,
    PackageWorkflowVerifyCommand,
    execute_package_mutation_command,
    execute_package_verify_command,
    execute_package_workflow_selection_command,
)
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore
from millrace.substrate.package_archives import export_workflow_package_directory
from millrace.testing import deterministic_context
from millrace.workflows import (
    lad_execution,
    lad_learning,
    lad_planning,
    simple_loop,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
LEGACY_ASSET_ROOT = Path(os.environ["MILLRACE_LEGACY_ASSET_ROOT"])
LEGACY_ASSET_LABEL = "dev/source/millrace/src/millrace_ai/assets"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.0"
DIST_NAME = "millrace-plus"
IMPORT_PACKAGE = "millrace_plus"
RESOURCE_ROOT = "millrace_workflow_package"

SIMPLE_LOOP_STAGE_SKILL_ASSET_IDS = (
    "simple_loop.manager_core_skill",
    "simple_loop.worker_core_skill",
    "simple_loop.reviewer_core_skill",
    "simple_loop.troubleshooter_core_skill",
)
VENDOR_SELECTION_STAGE_IDS = (
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

OFFICIAL_WORKFLOW_IDS = (
    "simple_loop",
    "execution.lad",
    "execution.lad_integrator",
    "planning.lad",
    "lad.full",
    "vendor_selection",
)
MILLFORGE_COMPONENT_CAPABILITY_IDS = (
    "terminal.intent",
    "unrestricted.filesystem.read",
    "unrestricted.filesystem.write",
    "unrestricted.process.execute",
)
MILLFORGE_BINDING_CAPABILITY_IDS = (
    "capability.runner.invoke",
    *MILLFORGE_COMPONENT_CAPABILITY_IDS,
)
MILLFORGE_PROFILE_DIGESTS = {
    (
        "BLOCKED",
        "INCIDENT_TRIAGED",
        "INVALID_PROMPT",
        "NEEDS_OPERATOR_DETAIL",
        "PACKET_READY",
    ): "eac94d518fbca0d53afbc6d8e1052b80623b1a6ca054e98d1e20eb3206c8eaf1",
    (
        "BLOCKED",
        "FAILED",
        "INSUFFICIENT_SPEC",
        "WORK_DONE",
    ): "e2b8b8c6249d9c99b9dfb430b93be4ae5a645c240059ee78232f8f7eb7c55f77",
    (
        "ACCEPTED",
        "BLOCKED",
        "GAPS_FOUND",
        "INCIDENT_REQUIRED",
    ): "b21af8cbfe90184cac329f5101bbfd66760f78305ba39e88dce98ce072065185",
    (
        "OPERATOR_NEEDED",
        "RESOLVED",
        "UNRESOLVED",
    ): "1b851cf8208066bab668ef119f09de78f2063969bf71930106e325951c667448",
    (
        "BLOCKED",
        "BUILDER_COMPLETE",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
    ): "a9ba52eef071ad8b8399c3c36c1a63be814d791793cfc50d411aadc7b96309d6",
    (
        "BLOCKED",
        "CHECKER_PASS",
        "FIX_NEEDED",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
    ): "c1e6a75f2f12550ddff913f690657be55499819556e5afe6112cb4456dc21d55",
    (
        "BLOCKED",
        "FIXER_COMPLETE",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
    ): "5fb7380f9905cf7235b721e7e1ad7eb35b255a9308d29dbd9665d84aa6df1dca",
    (
        "BLOCKED",
        "DOUBLECHECK_PASS",
        "FIX_NEEDED",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
    ): "1544b8502dbe820bef5975324264dd95e7420c00090173fb7aa6a37ec77a2422",
    (
        "BLOCKED",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
        "UPDATE_COMPLETE",
    ): "3f0aa5bab7252ff24e49ca42574d421a10ccb7335f348882e33a7e888882d301",
    (
        "BLOCKED",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
        "TROUBLESHOOT_COMPLETE",
        "TROUBLESHOOT_QUARANTINE",
        "TROUBLESHOOT_RECOVERED",
    ): "120c5a874ea96cf2c60bbe9420dd922d788e7e26d4cd503eb8c4bea8fed2a99d",
    (
        "BLOCKED",
        "CONSULT_COMPLETE",
        "CONSULT_QUARANTINE",
        "CONSULT_RECOVERED",
        "NEEDS_PLANNING",
    ): "6a150470c2aa3015502d83cdfe0ebd398b3e95cd350e3e4e5f15623b6b4bccb8",
    (
        "BLOCKED",
        "INTEGRATION_COMPLETE",
        "RUNTIME_FAILURE",
        "RUNTIME_FAILURE_ESCALATE",
    ): "9b4956cca2113ccaf9fa01302755b4ce381f590256c81c1f79c7854cefb2fe5b",
    (
        "BLOCKED",
        "RECON_BLOCKED",
        "RECON_NOOP",
        "RECON_TO_EXECUTION",
        "RECON_TO_PLANNING",
    ): "ce6b1058d2cfb8f67ccc9f4039a7a8ee70450f7ecc69c17b7af2a9d80486b6a3",
    ("BLOCKED", "PLANNER_COMPLETE"): (
        "1c59c8e53b81d1964260861b30a3ba0a3a2b4b61a1ab6e300262e1de366fa917"
    ),
    ("BLOCKED", "MANAGER_COMPLETE"): (
        "976e6f78d905626a1a6ef221936424f3491d2fca6e1dfaba752bf8f010f22790"
    ),
    (
        "BLOCKED",
        "MECHANIC_COMPLETE",
        "MECHANIC_QUARANTINE",
        "MECHANIC_RECOVERED",
    ): "c8e7797c7cc4ede48baa0d9551bebda6f8cabd1c200284f9ce9ab8ae5695f080",
    ("AUDITOR_COMPLETE", "BLOCKED"): (
        "9cebe087de353d637aaa471f8ac409067e6a153df8476f5038723fe4b0bb52b3"
    ),
    ("ARBITER_COMPLETE", "BLOCKED", "REMEDIATION_NEEDED"): (
        "41654d29f17b94388685fdf610e9b48d42713d320dd56a0c42a58b4b5afebb14"
    ),
    ("ANALYST_COMPLETE", "ANALYST_NOOP", "BLOCKED"): (
        "aef310f93ff67a02aac7545b6c33a15bba0b76babf7200677ff3bf44746bb581"
    ),
    ("BLOCKED", "PROFESSOR_COMPLETE", "PROFESSOR_NOOP"): (
        "e3919a2a1b8b0d1fda41b525595abf9ddc573542555cadc82d2a906ba06da979"
    ),
    ("BLOCKED", "CURATOR_COMPLETE", "CURATOR_NOOP"): (
        "ee2b37f21fd2e47f8f80abcc5ec4788d4686b3a9966bcaae1fcb444f53903395"
    ),
    ("BLOCKED", "LIBRARIAN_COMPLETE", "LIBRARIAN_NOOP"): (
        "4b8f0a0d5bf2a662c496aa79b60248530eebf7c518c1c808b054db7bc805087c"
    ),
    ("REQUEST_NEEDS_CLARIFICATION", "REQUEST_READY"): (
        "230ea6502507df213a8afc7cfdb69aeaf209b5081466d1c6e0ba085d796a2689"
    ),
    ("POLICY_ALLOWED", "POLICY_BLOCKED"): (
        "6c7acd4ecd54e5921ec506206186459b6c1d7de884537c09767f758601ab57b6"
    ),
    ("REQUIREMENTS_READY",): (
        "3aeb3cef38d7ab68b378cdcdc661feb8e39f6b5a721f3a88c01b3438b55e3b28"
    ),
    ("CANDIDATES_READY", "NO_VIABLE_VENDOR"): (
        "bf79fdb51f1b84af97e6dfa9fef42eef252041290985a835d8f93038bfedbaa5"
    ),
    ("CANDIDATES_READY",): (
        "3da9b5b08d952cf8517afcd321ba46ddbd5f9582031be1e9237c7c1facdc6a57"
    ),
    ("RUBRIC_COMPLETE",): (
        "09b513739a96d7487fb88e6ed2d19ed2dc433671f028a4ecde78ce67824c00fc"
    ),
    ("CONFLICT_COMPLETE",): (
        "a37fb197dd8ee3f80d0c4d9c0bb2cf7a63c3b1d0b6e1f7d62b47462a87af011f"
    ),
    (
        "AWARD_READY",
        "BLOCKED",
        "NO_VIABLE_VENDOR",
        "OPERATOR_REQUIRED",
        "RESOURCE_REQUIRED",
    ): "f7124be9cd857d1edfb3aa92635c74025dc8eedcc8996774f5fdc229727d04da",
    ("DECISION_PACK_READY",): (
        "8f7497381b236baac490e8a71b4bef898f971781d79d8190f977b91d3ecde7cc"
    ),
}


@dataclass(frozen=True)
class WorkflowExpectation:
    workflow_id: str
    workflow_version: str
    expected_asset_ids: tuple[str, ...]
    owner_packet: str


@dataclass(frozen=True)
class LegacyPairExpectation:
    stage_id: str
    entrypoint_path: str
    skill_path: str
    owner_packet: str
    selector: str
    asset_ids: tuple[str, str] | None
    disposition: str


def _load_manifest(package_root: Path = PACKAGE_ROOT) -> dict[str, Any]:
    return conformance.load_manifest_source(package_root)


def _workflows_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        str(workflow["workflow_id"]): workflow
        for workflow in cast(list[dict[str, object]], manifest["workflows"])
    }


def _selected_authority(workflow: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], workflow["selected_authority"])


def _official_terminal_rows(manifest: dict[str, Any]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for workflow_id, workflow in _workflows_by_id(manifest).items():
        authority = _selected_authority(workflow)
        stages = cast(list[dict[str, object]], authority["stage_kinds"])
        outcomes = cast(list[dict[str, object]], authority["terminal_outcomes"])
        actions = cast(list[dict[str, object]], authority["terminal_actions"])
        for stage in stages:
            stage_id = str(stage["id"])
            declared_outcome_ids = {
                str(outcome_id)
                for outcome_id in cast(list[object], stage["declared_outcome_ids"])
            }
            for outcome in outcomes:
                if outcome["stage_kind_id"] != stage_id:
                    continue
                outcome_id = str(outcome["id"])
                matching_actions = [
                    action
                    for action in actions
                    if action["stage_kind_id"] == stage_id
                    and action["outcome_id"] == outcome_id
                ]
                assert len(matching_actions) == 1
                action = matching_actions[0]
                marker = str(outcome.get("marker", ""))
                declared = outcome_id in declared_outcome_ids
                rows.append(
                    {
                        "workflow_id": workflow_id,
                        "stage_id": stage_id,
                        "outcome_id": outcome_id,
                        "declared": declared,
                        "marker": marker,
                        "action_id": str(action["id"]),
                        "action_kind": str(action["kind"]),
                        "artifact_schema_id": action.get("artifact_schema_id"),
                        "classification": (
                            "runner_selectable"
                            if declared and marker.strip()
                            else "runtime_owned_non_runner"
                        ),
                    }
                )
    return rows


def _rows_by_stage(
    manifest: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    rows_by_stage: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in _official_terminal_rows(manifest):
        key = (str(row["workflow_id"]), str(row["stage_id"]))
        rows_by_stage.setdefault(key, []).append(row)
    return rows_by_stage


def _bindings_by_id(authority: dict[str, object]) -> dict[str, dict[str, object]]:
    return {
        str(binding["id"]): binding
        for binding in cast(list[dict[str, object]], authority["runner_bindings"])
    }


def _walk_dicts(value: object) -> Iterator[dict[str, object]]:
    if isinstance(value, dict):
        record = cast(dict[str, object], value)
        yield record
        for child in record.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _constraint_sites(
    value: object,
    path: tuple[str, ...] = (),
) -> dict[tuple[str, ...], object]:
    sites: dict[tuple[str, ...], object] = {}
    if isinstance(value, dict):
        record = cast(dict[str, object], value)
        for key, child in record.items():
            child_path = (*path, key)
            if key == "const":
                sites[child_path] = child
            elif key == "enum":
                sites[child_path] = tuple(
                    sorted(
                        cast(list[object], child),
                        key=lambda item: json.dumps(
                            item,
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8"),
                    )
                )
            sites.update(_constraint_sites(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            sites.update(_constraint_sites(child, (*path, str(index))))
    return sites


def _expected_runner_display_name(workflow_id: str, stage_id: str) -> str:
    if workflow_id == "simple_loop":
        return "Default agent runner"
    if workflow_id == "vendor_selection":
        return "Deterministic vendor runner"
    if stage_id in {
        "recon",
        "lad_planner",
        "lad_manager",
        "lad_mechanic",
        "lad_auditor",
        "lad_arbiter",
    }:
        return "Local LAD Planning runner"
    if stage_id in {"analyst", "professor", "curator", "librarian"}:
        return "Learning runner"
    return "Local LAD runner"


def _assets_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        str(asset["asset_id"]): asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
    }


def _donor_asset_ids(source: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        str(asset["id"]) for asset in cast(list[dict[str, object]], source["assets"])
    )


def _simple_loop_expected_asset_ids() -> tuple[str, ...]:
    return (
        *_donor_asset_ids(simple_loop.workflow_source()),
        *SIMPLE_LOOP_STAGE_SKILL_ASSET_IDS,
    )


def _vendor_selection_expected_asset_ids() -> tuple[str, ...]:
    asset_ids: list[str] = []
    for stage_id in VENDOR_SELECTION_STAGE_IDS:
        asset_ids.append(f"vendor_selection.entrypoints.{stage_id}")
        asset_ids.append(f"vendor_selection.skills.{stage_id}_core")
    return tuple(asset_ids)


def _workflow_expectations() -> tuple[WorkflowExpectation, ...]:
    return (
        WorkflowExpectation(
            "simple_loop",
            "0.1",
            _simple_loop_expected_asset_ids(),
            "PLUS-0002B",
        ),
        WorkflowExpectation(
            "execution.lad",
            "0.1",
            _donor_asset_ids(lad_execution.workflow_source()),
            "PLUS-0002C",
        ),
        WorkflowExpectation(
            "execution.lad_integrator",
            "0.1",
            _donor_asset_ids(lad_execution.integrator_workflow_source()),
            "PLUS-0002C",
        ),
        WorkflowExpectation(
            "planning.lad",
            "0.1",
            _donor_asset_ids(lad_planning.workflow_source()),
            "PLUS-0002D",
        ),
        WorkflowExpectation(
            "lad.full",
            "0.1",
            _donor_asset_ids(lad_learning.workflow_source()),
            "PLUS-0002E",
        ),
        WorkflowExpectation(
            "vendor_selection",
            "0.1",
            _vendor_selection_expected_asset_ids(),
            "PLUS-0003D",
        ),
    )


def _expected_selected_asset_pins(
    manifest: dict[str, Any],
    workflow_id: str,
    package_root: Path = PACKAGE_ROOT,
) -> tuple[tuple[str, str], ...]:
    workflow = _workflows_by_id(manifest)[workflow_id]
    assets_by_id = _assets_by_id(manifest)
    return tuple(
        (
            asset_id,
            conformance.asset_digest_for_package_path(
                package_root,
                str(assets_by_id[asset_id]["package_path"]),
            ),
        )
        for asset_id in sorted(
            str(required_asset["asset_id"])
            for required_asset in cast(
                list[dict[str, object]],
                workflow["required_assets"],
            )
        )
    )


def _workflow_index(manifest: dict[str, Any], workflow_id: str) -> int:
    for index, workflow in enumerate(
        cast(list[dict[str, object]], manifest["workflows"]),
    ):
        if workflow["workflow_id"] == workflow_id:
            return index
    raise AssertionError(f"missing workflow {workflow_id}")


def _invalid_selected_runner_bindings(
    manifest: dict[str, Any],
    workflow_id: str,
) -> tuple[tuple[int, dict[str, object]], ...]:
    workflow = _workflows_by_id(manifest)[workflow_id]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    return tuple(
        (index, binding)
        for index, binding in enumerate(
            cast(list[dict[str, object]], selected_authority["runner_bindings"]),
        )
        if binding.get("adapter_kind") not in {"codex", "millforge"}
    )


def _package_source_kind(import_operation_id: str) -> str:
    if import_operation_id == "package.import_installed":
        return "installed_python_package"
    return import_operation_id.removeprefix("package.import_")


def _assert_runner_defaulting_warnings(
    result: object,
    *,
    manifest: dict[str, Any],
    expectation: WorkflowExpectation,
    package_source_kind: str,
) -> None:
    warnings = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ]
    expected_bindings = _invalid_selected_runner_bindings(
        manifest,
        expectation.workflow_id,
    )
    workflow_index = _workflow_index(manifest, expectation.workflow_id)

    assert len(warnings) == len(expected_bindings)
    assert len(warnings) > 0
    for warning, (binding_index, binding) in zip(
        warnings,
        expected_bindings,
        strict=True,
    ):
        assert warning.severity == "warning"
        assert warning.declaration_path == (
            f"workflows[{workflow_index}].selected_authority."
            f"runner_bindings[{binding_index}].adapter_kind"
        )
        assert warning.hint is not None
        assert "daemon will not remap" in warning.hint
        assert warning.context["runner_binding_id"] == binding["id"]
        assert warning.context["original_adapter_kind"] == binding["adapter_kind"]
        assert warning.context["default_adapter_kind"] == "codex"
        assert warning.context["workflow_id"] == expectation.workflow_id
        assert warning.context["workflow_version"] == expectation.workflow_version
        assert warning.context["source_kind"] == "workflow_package_selection"
        assert warning.context["package_id"] == PACKAGE_ID
        assert warning.context["package_version"] == PACKAGE_VERSION
        assert warning.context["entrypoint"] == "default"
        assert warning.context["package_source_kind"] == package_source_kind
        for key in (
            "package_source_digest",
            "package_import_digest",
            "package_manifest_digest",
            "package_digest",
        ):
            assert warning.context[key]


def _assert_selected_runner_authority(plan: object) -> None:
    assert plan is not None
    assert {runner.adapter_kind for runner in plan.runner_bindings} == {"millforge"}
    authority_text = canonical_authority_bytes(plan).decode("utf-8")
    assert '"adapter_kind":"fake_local"' not in authority_text
    assert '"adapter_kind":"codex"' not in authority_text
    assert '"adapter_kind":"millforge"' in authority_text


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _enable_select_verify_all_workflows(
    tmp_path: Path,
    *,
    command_label: str,
    import_operation_id: str,
    mutation_kwargs: dict[str, object],
) -> dict[str, object]:
    store, cas_store = _store(tmp_path)
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=f"{command_label}-import",
            operation_id=import_operation_id,
            actor_id="operator:test",
            **mutation_kwargs,
        ),
    )
    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=f"{command_label}-enable",
            operation_id="package.enable",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
        ),
    )

    assert imported.outcome == "succeeded"
    assert enabled.outcome == "succeeded"

    selected_by_workflow: dict[str, object] = {}
    package_root = cast(Path, mutation_kwargs.get("package_root", PACKAGE_ROOT))
    manifest = _load_manifest(package_root)

    for expectation in _workflow_expectations():
        safe_id = expectation.workflow_id.replace(".", "-")
        verified = execute_package_verify_command(
            store,
            cas_store,
            PackageWorkflowVerifyCommand(
                command_id=f"{command_label}-verify-{safe_id}",
                actor_id="operator:test",
                package_id=PACKAGE_ID,
                package_version=PACKAGE_VERSION,
                workflow_id=expectation.workflow_id,
                workflow_version=expectation.workflow_version,
            ),
        )
        selected = execute_package_workflow_selection_command(
            store,
            cas_store,
            PackageWorkflowSelectionCommand(
                command_id=f"{command_label}-select-{safe_id}",
                actor_id="operator:test",
                package_id=PACKAGE_ID,
                package_version=PACKAGE_VERSION,
                workflow_id=expectation.workflow_id,
                workflow_version=expectation.workflow_version,
            ),
        )

        assert verified.outcome == "succeeded"
        assert verified.plan_ready
        assert selected.outcome == "succeeded"
        assert selected.plan is not None
        assert not [
            diagnostic
            for diagnostic in verified.diagnostics
            if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
        ]
        assert not [
            diagnostic
            for diagnostic in selected.diagnostics
            if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
        ]
        warning_count = len(
            _invalid_selected_runner_bindings(manifest, expectation.workflow_id),
        )
        diagnostics_summary = (
            "diagnostics:0"
            if warning_count == 0
            else f"diagnostics:{warning_count} errors:0 warnings:{warning_count}"
        )
        assert selected.command_audit.diagnostics_summary == (diagnostics_summary)
        assert verified.command_audit.diagnostics_summary == (diagnostics_summary)
        _assert_selected_runner_authority(selected.plan)
        conformance.assert_selected_package_pin(
            selected.plan,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=expectation.workflow_id,
            workflow_version=expectation.workflow_version,
            selected_asset_pins=_expected_selected_asset_pins(
                manifest,
                expectation.workflow_id,
                package_root,
            ),
        )
        selected_by_workflow[expectation.workflow_id] = selected.plan

    return selected_by_workflow


def _refresh_manifest_digests(package_root: Path) -> None:
    manifest = _load_manifest(package_root)
    assets_by_id = _assets_by_id(manifest)
    for asset in assets_by_id.values():
        asset_bytes = (package_root / str(asset["package_path"])).read_bytes()
        asset["content_digest"] = asset_digest_for_bytes(asset_bytes)
        asset["byte_length"] = len(asset_bytes)

    for workflow in cast(list[dict[str, object]], manifest["workflows"]):
        for required_asset in cast(
            list[dict[str, object]],
            workflow["required_assets"],
        ):
            asset_id = str(required_asset["asset_id"])
            required_asset["content_digest"] = assets_by_id[asset_id]["content_digest"]
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )


def _copy_package_with_mutated_asset(
    tmp_path: Path,
    asset_id: str,
) -> Path:
    mutated_root = tmp_path / "mutated-asset"
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    manifest = _load_manifest(mutated_root)
    asset_path = mutated_root / str(_assets_by_id(manifest)[asset_id]["package_path"])
    asset_path.write_text(asset_path.read_text() + "\nPLUS-0002.9 mutation.\n")
    _refresh_manifest_digests(mutated_root)
    return mutated_root


def _copy_package_with_mutated_vendor_catalog(tmp_path: Path) -> Path:
    mutated_root = tmp_path / "mutated-vendor-catalog"
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    manifest = _load_manifest(mutated_root)
    workflow = _workflows_by_id(manifest)["vendor_selection"]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    catalog = cast(list[dict[str, object]], selected_authority["unselected_catalog"])
    catalog[0]["vendor_label"] = "PLUS-0002.9 Mutated Catalog Entry"
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    (mutated_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )
    return mutated_root


def _write_manifest(package_root: Path, manifest: dict[str, Any]) -> None:
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )


def _copy_package_with_test_local_codex_binding(tmp_path: Path) -> Path:
    fixture_root = tmp_path / "codex-defaulting-fixture"
    shutil.copytree(PACKAGE_ROOT, fixture_root)
    manifest = _load_manifest(fixture_root)
    workflow = _workflows_by_id(manifest)["simple_loop"]
    authority = _selected_authority(workflow)
    stage_ids = [
        str(stage["id"])
        for stage in cast(list[dict[str, object]], authority["stage_kinds"])
    ]
    fixture_binding_id = "test.fixture.fake_local_runner"
    authority["runner_bindings"] = [
        {
            "id": fixture_binding_id,
            "adapter_kind": "fake_local",
            "stage_kind_ids": stage_ids,
            "required_capability_ids": ["capability.runner.invoke"],
            "presentation": {"display_name": "Test-local defaulting runner"},
        }
    ]
    authority["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        }
    ]
    for record in _walk_dicts(authority):
        if "runner_binding_id" in record:
            record["runner_binding_id"] = fixture_binding_id
        if "target_runner_binding_id" in record:
            record["target_runner_binding_id"] = fixture_binding_id
    _write_manifest(fixture_root, manifest)
    return fixture_root


def _copy_package_with_runner_authority_mutation(
    tmp_path: Path,
    mutation_kind: str,
) -> Path:
    mutated_root = tmp_path / mutation_kind
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    manifest = _load_manifest(mutated_root)
    workflows = _workflows_by_id(manifest)
    workflow = workflows["simple_loop"]
    authority = _selected_authority(workflow)
    bindings = cast(list[dict[str, object]], authority["runner_bindings"])
    assert len(bindings) == 4, "PLUS-0005 per-stage bindings are missing"
    binding = bindings[0]
    pin = cast(dict[str, object], binding.get("component_pin"))
    mappings = cast(
        list[dict[str, object]],
        binding.get("terminal_result_mappings"),
    )
    assert pin and mappings, "PLUS-0005 component authority is missing"

    if mutation_kind == "pin_fields":
        pin["descriptor_sha256"] = "not-a-descriptor-digest"
    elif mutation_kind == "capabilities":
        binding["required_capability_ids"] = ["capability.runner.invoke"]
    elif mutation_kind == "result_ids":
        mappings[0]["runner_result_id"] = "UNKNOWN_PROVIDER_RESULT"
    elif mutation_kind == "target_outcomes":
        foreign_binding = bindings[1]
        foreign_mapping = cast(
            list[dict[str, object]],
            foreign_binding["terminal_result_mappings"],
        )[0]
        mappings[0]["outcome_id"] = foreign_mapping["outcome_id"]
    elif mutation_kind == "stage_ids":
        foreign_stage_ids = cast(list[object], bindings[1]["stage_kind_ids"])
        mappings[0]["stage_kind_id"] = foreign_stage_ids[0]
    elif mutation_kind == "blank_marker_mappings":
        planning = _selected_authority(workflows["planning.lad"])
        planning_stages = cast(list[dict[str, object]], planning["stage_kinds"])
        declared_by_stage = {
            str(stage["id"]): {
                str(outcome_id)
                for outcome_id in cast(list[object], stage["declared_outcome_ids"])
            }
            for stage in planning_stages
        }
        blank_outcome = next(
            outcome
            for outcome in cast(
                list[dict[str, object]], planning["terminal_outcomes"]
            )
            if not str(outcome.get("marker", "")).strip()
            and str(outcome["id"])
            not in declared_by_stage[str(outcome["stage_kind_id"])]
        )
        stage_id = str(blank_outcome["stage_kind_id"])
        planning_binding = next(
            item
            for item in cast(
                list[dict[str, object]], planning["runner_bindings"]
            )
            if item["stage_kind_ids"] == [stage_id]
        )
        planning_mappings = cast(
            list[dict[str, object]],
            planning_binding["terminal_result_mappings"],
        )
        planning_mappings[0]["outcome_id"] = blank_outcome["id"]
    elif mutation_kind == "binding_references":
        stages = cast(list[dict[str, object]], authority["stage_kinds"])
        stages[0]["runner_binding_id"] = bindings[1]["id"]
    else:
        raise AssertionError(f"unknown mutation kind: {mutation_kind}")

    _write_manifest(mutated_root, manifest)
    return mutated_root


def _assert_package_selection_refuses(
    tmp_path: Path,
    package_root: Path,
    workflow_id: str,
) -> None:
    store, cas_store = _store(tmp_path)
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="mutated-import",
            operation_id="package.import_path",
            actor_id="operator:test",
            package_root=package_root,
        ),
    )
    if imported.outcome != "succeeded":
        assert imported.outcome == "refused"
        assert any(
            diagnostic.severity == "error"
            for diagnostic in imported.diagnostics
        )
        return

    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="mutated-enable",
            operation_id="package.enable",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
        ),
    )
    assert enabled.outcome == "succeeded"
    selected = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id="mutated-select",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=workflow_id,
            workflow_version="0.1",
        ),
    )
    assert selected.outcome == "failed"
    assert selected.plan is None
    assert any(diagnostic.severity == "error" for diagnostic in selected.diagnostics)


def _copy_package_with_reordered_runner_authority(tmp_path: Path) -> Path:
    reordered_root = tmp_path / "reordered-runner-authority"
    shutil.copytree(PACKAGE_ROOT, reordered_root)
    manifest = _load_manifest(reordered_root)
    for workflow in _workflows_by_id(manifest).values():
        authority = _selected_authority(workflow)
        cast(list[object], authority["runner_bindings"]).reverse()
        assert "capabilities" in authority, "PLUS-0005 capabilities are missing"
        cast(list[object], authority["capabilities"]).reverse()
        for binding in cast(list[dict[str, object]], authority["runner_bindings"]):
            assert "terminal_result_mappings" in binding, (
                "PLUS-0005 terminal mappings are missing"
            )
            cast(list[object], binding["terminal_result_mappings"]).reverse()
    _write_manifest(reordered_root, manifest)
    return reordered_root


def _copy_package_with_semantic_runner_mutation(tmp_path: Path) -> Path:
    mutated_root = tmp_path / "semantic-runner-mutation"
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    manifest = _load_manifest(mutated_root)
    authority = _selected_authority(_workflows_by_id(manifest)["simple_loop"])
    binding = cast(list[dict[str, object]], authority["runner_bindings"])[0]
    pin = cast(dict[str, object], binding["component_pin"])
    pin["descriptor_sha256"] = "f" * 64
    _write_manifest(mutated_root, manifest)
    return mutated_root


def _fingerprints_by_workflow(plans_by_workflow: dict[str, object]) -> dict[str, str]:
    return {
        workflow_id: authority_fingerprint(plan)
        for workflow_id, plan in plans_by_workflow.items()
    }


def _asset_texts_by_path(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        str(asset["package_path"]): (
            PACKAGE_ROOT / str(asset["package_path"])
        ).read_text()
        for asset in cast(list[dict[str, object]], manifest["assets"])
    }


def _asset_texts_by_id(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        str(asset["asset_id"]): (PACKAGE_ROOT / str(asset["package_path"])).read_text()
        for asset in cast(list[dict[str, object]], manifest["assets"])
    }


def _legacy_pair_expectations() -> tuple[LegacyPairExpectation, ...]:
    packaged_execution = (
        ("lad_builder", "builder_core", "PLUS-0002C", "execution.lad"),
        ("lad_checker", "checker_core", "PLUS-0002C", "execution.lad"),
        ("lad_consultant", "consultant_core", "PLUS-0002C", "execution.lad"),
        (
            "lad_doublechecker",
            "doublechecker_core",
            "PLUS-0002C",
            "execution.lad",
        ),
        ("lad_fixer", "fixer_core", "PLUS-0002C", "execution.lad"),
        (
            "lad_integrator",
            "integrator_core",
            "PLUS-0002C",
            "execution.lad_integrator",
        ),
        (
            "lad_troubleshooter",
            "troubleshooter_core",
            "PLUS-0002C",
            "execution.lad",
        ),
        ("lad_updater", "updater_core", "PLUS-0002C", "execution.lad"),
    )
    packaged_planning = (
        ("recon", "recon_core"),
        ("lad_planner", "planner_core"),
        ("lad_manager", "manager_core"),
        ("lad_mechanic", "mechanic_core"),
        ("lad_auditor", "auditor_core"),
        ("lad_arbiter", "arbiter_core"),
    )
    deferred_blueprints = (
        ("contractor_blueprint", "contractor-blueprint-core"),
        ("evaluator_blueprint", "evaluator-blueprint-core"),
        ("manager_blueprint", "manager-blueprint-core"),
        ("mechanic_blueprint", "mechanic-blueprint-core"),
    )
    packaged_learning = (
        ("analyst", "analyst_core"),
        ("curator", "curator_core"),
        ("librarian", "librarian_core"),
        ("professor", "professor_core"),
    )

    expectations: list[LegacyPairExpectation] = []
    for stage_id, skill_id, owner_packet, selector in packaged_execution:
        skill_name = skill_id.removesuffix("_core").replace("_", "-")
        expectations.append(
            LegacyPairExpectation(
                stage_id=stage_id,
                entrypoint_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"entrypoints/execution/{stage_id}.md"
                ),
                skill_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"skills/stage/execution/{skill_name}-core/SKILL.md"
                ),
                owner_packet=owner_packet,
                selector=selector,
                asset_ids=(
                    f"execution.entrypoints.{stage_id}",
                    f"execution.skills.{skill_id}",
                ),
                disposition="hosted_workflow_or_package",
            )
        )
    for stage_id, skill_id in packaged_planning:
        skill_name = skill_id.removesuffix("_core").replace("_", "-")
        expectations.append(
            LegacyPairExpectation(
                stage_id=stage_id,
                entrypoint_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"entrypoints/planning/{stage_id}.md"
                ),
                skill_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"skills/stage/planning/{skill_name}-core/SKILL.md"
                ),
                owner_packet="PLUS-0002D",
                selector="planning.lad",
                asset_ids=(
                    f"planning.entrypoints.{stage_id}",
                    f"planning.skills.{skill_id}",
                ),
                disposition="hosted_workflow_or_package",
            )
        )
    for stage_id, skill_name in deferred_blueprints:
        expectations.append(
            LegacyPairExpectation(
                stage_id=stage_id,
                entrypoint_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"entrypoints/planning/{stage_id}.md"
                ),
                skill_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"skills/stage/planning/{skill_name}/SKILL.md"
                ),
                owner_packet="CKPT-0001",
                selector="planning.blueprint",
                asset_ids=None,
                disposition="defer_post_cutover",
            )
        )
    for stage_id, skill_id in packaged_learning:
        skill_name = skill_id.removesuffix("_core").replace("_", "-")
        expectations.append(
            LegacyPairExpectation(
                stage_id=stage_id,
                entrypoint_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"entrypoints/learning/{stage_id}.md"
                ),
                skill_path=(
                    "dev/source/millrace/src/millrace_ai/assets/"
                    f"skills/stage/learning/{skill_name}-core/SKILL.md"
                ),
                owner_packet="PLUS-0002E",
                selector="lad.full",
                asset_ids=(
                    f"learning.entrypoints.{stage_id}",
                    f"learning.skills.{skill_id}",
                ),
                disposition="hosted_workflow_or_package",
            )
        )
    return tuple(expectations)


def _source_file_paths(pattern: str) -> set[str]:
    return {
        f"{LEGACY_ASSET_LABEL}/{path.relative_to(LEGACY_ASSET_ROOT).as_posix()}"
        for path in LEGACY_ASSET_ROOT.glob(pattern)
    }


def test_official_millforge_compatibility_matrix_is_total() -> None:
    manifest = _load_manifest()
    rows = _official_terminal_rows(manifest)
    rows_by_stage = _rows_by_stage(manifest)
    runner_rows = [
        row for row in rows if row["classification"] == "runner_selectable"
    ]
    runtime_rows = [
        row
        for row in rows
        if row["classification"] == "runtime_owned_non_runner"
    ]
    multi_schema_stage_keys = {
        key
        for key, stage_rows in rows_by_stage.items()
        if len(
            {
                row["artifact_schema_id"]
                for row in stage_rows
                if row["classification"] == "runner_selectable"
                and row["artifact_schema_id"] is not None
            }
        )
        > 1
    }
    profiles = {
        tuple(
            sorted(
                (
                    str(row["marker"])
                    for row in stage_rows
                    if row["classification"] == "runner_selectable"
                ),
                key=lambda value: value.encode("utf-8"),
            )
        )
        for stage_rows in rows_by_stage.values()
    }

    assert tuple(_workflows_by_id(manifest)) == OFFICIAL_WORKFLOW_IDS
    assert len(rows_by_stage) == 58
    assert len(rows) == 257
    assert len(runner_rows) == 216
    assert len(runtime_rows) == 41
    assert len([row for row in runtime_rows if not row["declared"]]) == 33
    assert len([row for row in runtime_rows if row["declared"]]) == 8
    assert len([row for row in runner_rows if row["artifact_schema_id"]]) == 177
    assert len([row for row in runner_rows if not row["artifact_schema_id"]]) == 39
    assert len(multi_schema_stage_keys) == 31
    assert (
        len(
            [
                row
                for row in runner_rows
                if (str(row["workflow_id"]), str(row["stage_id"]))
                in multi_schema_stage_keys
            ]
        )
        == 128
    )
    assert profiles == set(MILLFORGE_PROFILE_DIGESTS)
    assert len(profiles) == 31


def test_official_workflows_select_exact_millforge_stage_bindings() -> None:
    manifest = _load_manifest()
    rows_by_stage = _rows_by_stage(manifest)
    selected_stage_bindings = 0
    selected_profile_digests: set[str] = set()

    for workflow_id, workflow in _workflows_by_id(manifest).items():
        authority = _selected_authority(workflow)
        stages = cast(list[dict[str, object]], authority["stage_kinds"])
        bindings = cast(list[dict[str, object]], authority["runner_bindings"])
        bindings_by_id = _bindings_by_id(authority)
        assert len(bindings) == len(stages)
        selected_stage_bindings += len(bindings)

        for stage in stages:
            stage_id = str(stage["id"])
            binding_id = f"{stage_id}.millforge_runner"
            assert stage["runner_binding_id"] == binding_id
            binding = bindings_by_id[binding_id]
            runner_markers = tuple(
                sorted(
                    (
                        str(row["marker"])
                        for row in rows_by_stage[(workflow_id, stage_id)]
                        if row["classification"] == "runner_selectable"
                    ),
                    key=lambda value: value.encode("utf-8"),
                )
            )
            expected_digest = MILLFORGE_PROFILE_DIGESTS[runner_markers]
            current_descriptor = describe_millforge_base(
                legal_terminal_results=runner_markers,
            )
            assert expected_digest == current_descriptor.descriptor_sha256
            expected_pin = {
                "component_kind": "runner",
                "component_id": "millforge-base",
                "component_version": "2",
                "provider_distribution": "millforge",
                "provider_version": "0.1.0",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": expected_digest,
                "required_capability_ids": list(
                    MILLFORGE_COMPONENT_CAPABILITY_IDS
                ),
                "legal_terminal_result_ids": list(runner_markers),
            }
            assert binding["id"] == binding_id
            assert binding["adapter_kind"] == "millforge"
            assert binding["stage_kind_ids"] == [stage_id]
            assert "invocation_timeout_seconds" not in binding
            assert binding["presentation"] == {
                "display_name": _expected_runner_display_name(
                    workflow_id,
                    stage_id,
                )
            }
            assert binding["component_pin"] == expected_pin
            selected_profile_digests.add(expected_digest)

    assert selected_stage_bindings == 58
    assert selected_profile_digests == set(MILLFORGE_PROFILE_DIGESTS.values())
    assert len(selected_profile_digests) == 31


def test_official_millforge_terminal_mappings_are_total_and_stage_scoped() -> None:
    manifest = _load_manifest()
    rows_by_stage = _rows_by_stage(manifest)
    mapping_count = 0

    for workflow_id, workflow in _workflows_by_id(manifest).items():
        authority = _selected_authority(workflow)
        bindings_by_id = _bindings_by_id(authority)
        binding_id_by_stage = {
            str(stage["id"]): str(stage["runner_binding_id"])
            for stage in cast(list[dict[str, object]], authority["stage_kinds"])
        }
        for stage_id, binding_id in binding_id_by_stage.items():
            binding = bindings_by_id[binding_id]
            expected_mappings = [
                {
                    "stage_kind_id": stage_id,
                    "runner_result_id": str(row["marker"]),
                    "outcome_id": str(row["outcome_id"]),
                }
                for row in sorted(
                    rows_by_stage[(workflow_id, stage_id)],
                    key=lambda row: str(row["marker"]).encode("utf-8"),
                )
                if row["classification"] == "runner_selectable"
            ]
            assert "terminal_result_mappings" in binding, (
                "PLUS-0005 terminal mappings are missing"
            )
            assert binding["terminal_result_mappings"] == expected_mappings
            assert len(
                {
                    mapping["runner_result_id"]
                    for mapping in expected_mappings
                }
            ) == len(expected_mappings)
            assert len(
                {mapping["outcome_id"] for mapping in expected_mappings}
            ) == len(expected_mappings)
            mapping_count += len(expected_mappings)

        for record in _walk_dicts(authority):
            if "runner_binding_id" in record:
                target_stage_id = record.get("target_stage_kind_id")
                if target_stage_id is None:
                    target_stage_id = record.get("stage_kind_id", record.get("id"))
                assert record["runner_binding_id"] == binding_id_by_stage[
                    str(target_stage_id)
                ]
            if "target_runner_binding_id" in record:
                target_stage_id = str(record["target_stage_kind_id"])
                assert record["target_runner_binding_id"] == binding_id_by_stage[
                    target_stage_id
                ]

    assert mapping_count == 216


def test_official_millforge_result_schemas_are_outcome_scoped() -> None:
    from millrace.adapters.millforge import _project_schema

    manifest = _load_manifest()
    rows_by_stage = _rows_by_stage(manifest)
    associations: dict[tuple[str, str, str], str | None] = {}
    schema_bearing_count = 0
    schema_less_count = 0

    for workflow_id, workflow in _workflows_by_id(manifest).items():
        authority = _selected_authority(workflow)
        schemas_by_id = {
            str(schema["id"]): cast(dict[str, object], schema["schema"])
            for schema in cast(
                list[dict[str, object]], authority["artifact_schemas"]
            )
        }
        for binding in cast(
            list[dict[str, object]], authority["runner_bindings"]
        ):
            assert "terminal_result_mappings" in binding, (
                "PLUS-0005 terminal mappings are missing"
            )
            stage_id = str(cast(list[object], binding["stage_kind_ids"])[0])
            rows_by_outcome = {
                str(row["outcome_id"]): row
                for row in rows_by_stage[(workflow_id, stage_id)]
            }
            for mapping in cast(
                list[dict[str, object]], binding["terminal_result_mappings"]
            ):
                result_id = str(mapping["runner_result_id"])
                row = rows_by_outcome[str(mapping["outcome_id"])]
                schema_id = cast(str | None, row["artifact_schema_id"])
                key = (workflow_id, stage_id, result_id)
                assert key not in associations
                associations[key] = schema_id
                if schema_id is None:
                    schema_less_count += 1
                    continue
                schema_bearing_count += 1
                original = schemas_by_id[schema_id]
                projected = _project_schema(original)
                assert _constraint_sites(projected) == _constraint_sites(original)

    assert len(associations) == 216
    assert schema_bearing_count == 177
    assert schema_less_count == 39


def test_official_millforge_provider_requirements_match_compiled_result_mappings(
    tmp_path: Path,
) -> None:
    import millforge
    from millforge import (
        SelectedOutputRequirement,
        TerminalSelectedOutputRequirement,
    )
    from millrace.adapters.cli.run import _selected_runner_authority_for_request
    from millrace.adapters.millforge import (
        _current_mappings,
        _options_by_outcome,
        _selected_output_requirements,
    )
    from millrace.adapters.runner_contract import (
        AdapterInvocationRequest,
        RedactionPolicy,
    )
    from millrace.contracts.compiled_plan import RunnerTerminalResultMapping
    from millrace.contracts.runner import RunnerDispatchEnvelope

    manifest = _load_manifest()
    plans = _enable_select_verify_all_workflows(
        tmp_path / "provider-contract",
        command_label="provider-contract",
        import_operation_id="package.import_path",
        mutation_kwargs={"package_root": PACKAGE_ROOT},
    )
    binding_count = 0
    mapping_count = 0
    schema_bearing_count = 0
    schema_less_count = 0
    const_site_count = 0
    enum_site_count = 0

    for workflow_id in _workflows_by_id(manifest):
        plan = cast(Any, plans[workflow_id])
        stages_by_id = {str(stage.id): stage for stage in plan.stage_kinds}
        actions_by_stage_outcome = {
            (str(action.stage_kind_id), str(action.outcome_id)): action
            for action in plan.terminal_actions
        }
        schemas_by_id = {
            str(schema.id): schema for schema in plan.artifact_schemas
        }

        for binding in plan.runner_bindings:
            binding_count += 1
            assert len(binding.stage_kind_ids) == 1
            stage_id = str(binding.stage_kind_ids[0])
            stage = stages_by_id[stage_id]
            terminal_options = tuple(
                {
                    "outcome_id": str(outcome.id),
                    "marker": outcome.marker,
                    "action_id": str(action.id),
                    "action_kind": action.action_kind,
                    "artifact_schema_id": (
                        str(action.artifact_schema_id)
                        if action.artifact_schema_id is not None
                        else None
                    ),
                }
                for outcome in plan.terminal_outcomes
                if str(outcome.stage_kind_id) == stage_id
                and outcome.id in stage.declared_outcome_ids
                and outcome.marker.strip()
                for action in (
                    actions_by_stage_outcome[(stage_id, str(outcome.id))],
                )
            )
            dispatch = RunnerDispatchEnvelope(
                run_id=f"provider-contract:{workflow_id}:{stage_id}",
                work_item_id="provider-contract-work",
                activation_id="provider-contract-activation",
                plan_fingerprint=authority_fingerprint(plan),
                plan_id=f"{workflow_id}:provider-contract",
                workflow_id=str(plan.workflow.workflow_id),
                workflow_version=str(plan.workflow.workflow_version),
                graph_id="provider-contract-graph",
                claim_id="provider-contract-claim",
                generation=1,
                fencing_token="provider-contract-fence",
                queue_family_id="provider-contract-queue",
                stage_kind_id=stage_id,
                graph_node_id="provider-contract-node",
                runner_binding_id=str(binding.id),
                external_enqueue_route_id=None,
                entrypoint_asset_id=None,
                skill_asset_ids=(),
                artifact_schema_ids=tuple(
                    str(schema_id) for schema_id in stage.artifact_schema_ids
                ),
                work_item_payload={},
                terminal_options=terminal_options,
            )
            pin, mappings, schemas = _selected_runner_authority_for_request(
                plan,
                dispatch,
            )
            assert pin == binding.component_pin
            assert mappings == binding.terminal_result_mappings
            assert all(
                isinstance(mapping, RunnerTerminalResultMapping)
                for mapping in mappings
            )

            request = AdapterInvocationRequest(
                adapter_id="provider-contract-proof",
                selected_runner_binding_id=str(binding.id),
                selected_adapter_kind="millforge",
                dispatch_envelope=dispatch,
                timeout_seconds=binding.invocation_timeout_seconds,
                correlation_id=f"provider-contract:{workflow_id}:{stage_id}",
                redaction_policy=RedactionPolicy(
                    policy_id="provider-contract-proof"
                ),
                selected_component_pin=pin,
                selected_terminal_result_mappings=mappings,
                selected_artifact_schemas=schemas,
            )
            options = _options_by_outcome(request)
            assert pin is not None
            current_mappings = _current_mappings(request, pin, options)
            selected_outputs, requirements = _selected_output_requirements(
                request,
                millforge,
                current_mappings,
                options,
            )
            requirements_by_result = {
                requirement.terminal_result: requirement
                for requirement in requirements
            }
            assert all(
                isinstance(requirement, TerminalSelectedOutputRequirement)
                for requirement in requirements
            )

            mapping_count += len(mappings)
            for mapping in mappings:
                result_id = mapping.runner_result_id
                schema_id = options[str(mapping.outcome_id)]["artifact_schema_id"]
                if schema_id is None:
                    schema_less_count += 1
                    assert result_id not in requirements_by_result
                    assert result_id not in selected_outputs
                    continue

                schema_bearing_count += 1
                requirement = requirements_by_result[result_id]
                assert requirement.terminal_result == result_id
                assert isinstance(
                    requirement.selected_output,
                    SelectedOutputRequirement,
                )
                original_schema = json.loads(
                    canonical_authority_bytes(schemas_by_id[str(schema_id)].schema)
                )
                provider_schema = json.loads(
                    canonical_authority_bytes(
                        requirement.selected_output.json_schema
                    )
                )
                original_constraints = _constraint_sites(original_schema)
                assert _constraint_sites(provider_schema) == original_constraints
                const_site_count += sum(
                    path[-1] == "const" for path in original_constraints
                )
                enum_site_count += sum(
                    path[-1] == "enum" for path in original_constraints
                )

    assert binding_count == 58
    assert mapping_count == 216
    assert schema_bearing_count == 177
    assert schema_less_count == 39
    assert (const_site_count, enum_site_count) == (162, 36)


def test_official_non_runner_outcomes_have_runtime_ownership_proof() -> None:
    manifest = _load_manifest()
    runtime_rows = [
        row
        for row in _official_terminal_rows(manifest)
        if row["classification"] == "runtime_owned_non_runner"
    ]
    mapped_outcomes: set[str] = set()
    for workflow in _workflows_by_id(manifest).values():
        for binding in cast(
            list[dict[str, object]],
            _selected_authority(workflow)["runner_bindings"],
        ):
            assert "terminal_result_mappings" in binding, (
                "PLUS-0005 terminal mappings are missing"
            )
            mapped_outcomes.update(
                str(mapping["outcome_id"])
                for mapping in cast(
                    list[dict[str, object]], binding["terminal_result_mappings"]
                )
            )

    assert len(runtime_rows) == 41
    assert all(not str(row["marker"]).strip() for row in runtime_rows)
    assert not mapped_outcomes.intersection(
        str(row["outcome_id"]) for row in runtime_rows
    )

    runtime_source = Path(os.environ["MILLRACE_RUNTIME_SOURCE"])
    dispatch_source = (runtime_source / "millrace/operator/dispatch.py").read_text()
    lookup_source = (runtime_source / "millrace/kernel/lookups.py").read_text()
    assert (
        "if not outcome.marker.strip() or outcome.id not in "
        "stage.declared_outcome_ids"
    ) in dispatch_source
    assert "str(outcome.stage_kind_id) == stage_kind_id" in lookup_source
    assert "outcome.id in stage.declared_outcome_ids" in lookup_source
    assert "outcome.marker == marker" in lookup_source


def test_official_millforge_capabilities_are_explicit_and_exact() -> None:
    manifest = _load_manifest()
    expected_capabilities = [
        {
            "id": capability_id,
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
            "approval_policy_id": None,
        }
        for capability_id in MILLFORGE_BINDING_CAPABILITY_IDS
    ]

    for workflow in _workflows_by_id(manifest).values():
        authority = _selected_authority(workflow)
        assert "capabilities" in authority, "PLUS-0005 capabilities are missing"
        assert authority["capabilities"] == expected_capabilities
        for binding in cast(
            list[dict[str, object]], authority["runner_bindings"]
        ):
            assert binding["required_capability_ids"] == list(
                MILLFORGE_BINDING_CAPABILITY_IDS
            )
            pin = cast(dict[str, object], binding["component_pin"])
            assert pin["required_capability_ids"] == list(
                MILLFORGE_COMPONENT_CAPABILITY_IDS
            )


def test_codex_defaulting_fixture_remains_test_local(tmp_path: Path) -> None:
    fixture_root = _copy_package_with_test_local_codex_binding(tmp_path)
    fixture_manifest = _load_manifest(fixture_root)
    expectation = _workflow_expectations()[0]
    store, cas_store = _store(tmp_path / "runtime")
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="fixture-import",
            operation_id="package.import_path",
            actor_id="operator:test",
            package_root=fixture_root,
        ),
    )
    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="fixture-enable",
            operation_id="package.enable",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
        ),
    )
    selected = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id="fixture-select",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id="simple_loop",
            workflow_version="0.1",
        ),
    )

    assert imported.outcome == "succeeded"
    assert enabled.outcome == "succeeded"
    assert selected.outcome == "succeeded"
    _assert_runner_defaulting_warnings(
        selected,
        manifest=fixture_manifest,
        expectation=expectation,
        package_source_kind=_package_source_kind("package.import_path"),
    )
    assert selected.plan is not None
    assert {binding.adapter_kind for binding in selected.plan.runner_bindings} == {
        "codex"
    }
    production_bindings = [
        binding
        for workflow in _workflows_by_id(_load_manifest()).values()
        for binding in cast(
            list[dict[str, object]],
            _selected_authority(workflow)["runner_bindings"],
        )
    ]
    assert all(
        binding["adapter_kind"] not in {"fake_local", "codex"}
        for binding in production_bindings
    )


@pytest.mark.parametrize(
    ("mutation_kind", "workflow_id"),
    (
        ("pin_fields", "simple_loop"),
        ("capabilities", "simple_loop"),
        ("result_ids", "simple_loop"),
        ("target_outcomes", "simple_loop"),
        ("stage_ids", "simple_loop"),
        ("blank_marker_mappings", "planning.lad"),
        ("binding_references", "simple_loop"),
    ),
)
def test_official_millforge_authority_mutations_are_refused(
    tmp_path: Path,
    mutation_kind: str,
    workflow_id: str,
) -> None:
    mutated_root = _copy_package_with_runner_authority_mutation(
        tmp_path,
        mutation_kind,
    )
    _assert_package_selection_refuses(
        tmp_path / f"runtime-{mutation_kind}",
        mutated_root,
        workflow_id,
    )


def test_official_runner_authority_fingerprints_are_canonical_and_semantic(
    tmp_path: Path,
) -> None:
    base_result = conformance.select_package_from_path(
        tmp_path / "base",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id="simple_loop",
        workflow_version="0.1",
    )
    reordered_result = conformance.select_package_from_path(
        tmp_path / "reordered",
        _copy_package_with_reordered_runner_authority(tmp_path),
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id="simple_loop",
        workflow_version="0.1",
    )
    mutated_result = conformance.select_package_from_path(
        tmp_path / "mutated",
        _copy_package_with_semantic_runner_mutation(tmp_path),
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id="simple_loop",
        workflow_version="0.1",
    )

    assert authority_fingerprint(base_result.plan) == authority_fingerprint(
        reordered_result.plan
    )
    assert authority_fingerprint(base_result.plan) != authority_fingerprint(
        mutated_result.plan
    )


def test_official_runner_authority_adds_no_runtime_workflow_policy_branches() -> None:
    runtime_root = Path(os.environ["MILLRACE_RUNTIME_SOURCE"]) / "millrace"
    workflow_markers = OFFICIAL_WORKFLOW_IDS
    for relative_root in ("adapters", "compiler", "contracts"):
        for source_path in (runtime_root / relative_root).rglob("*.py"):
            source = source_path.read_text()
            assert not any(marker in source for marker in workflow_markers), source_path
    for relative_root in ("kernel", "substrate", "workflows"):
        for source_path in (runtime_root / relative_root).rglob("*.py"):
            assert "millforge" not in source_path.read_text().lower(), source_path


def test_path_archive_and_installed_sources_are_same_official_package_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    wheel_path = conformance.build_project_wheel(PROJECT_ROOT, tmp_path / "dist")
    member_paths = tuple(
        sorted(
            (
                "manifest.json",
                *(
                    str(asset["package_path"])
                    for asset in cast(
                        list[dict[str, object]],
                        manifest["assets"],
                    )
                ),
            )
        )
    )
    conformance.assert_wheel_contains_byte_only_package_data(
        wheel_path,
        resource_root=RESOURCE_ROOT,
        import_package=IMPORT_PACKAGE,
        expected_member_paths=member_paths,
    )

    path_source = read_path_workflow_package_source(PACKAGE_ROOT)
    archive_source = read_archive_workflow_package_source(
        export_workflow_package_directory(PACKAGE_ROOT),
    )
    installed_source = conformance.assert_installed_discovery_is_byte_only(
        monkeypatch,
        wheel_path=wheel_path,
        target=tmp_path / "site",
        distribution_name=DIST_NAME,
        import_package=IMPORT_PACKAGE,
        installed_resource_root=RESOURCE_ROOT,
        expected_package_root=PACKAGE_ROOT,
    )

    sources = (path_source, archive_source, installed_source)
    assert all(source.manifest is not None for source in sources)
    assert {
        source.manifest.manifest_digest
        for source in sources
        if source.manifest is not None
    } == {manifest["manifest_digest"]}
    assert path_source.member_paths == archive_source.member_paths
    assert archive_source.member_paths == installed_source.member_paths
    assert path_source.asset_bytes_by_path == archive_source.asset_bytes_by_path
    assert archive_source.asset_bytes_by_path == installed_source.asset_bytes_by_path


def test_final_package_pin_uses_v022_release_identity(tmp_path: Path) -> None:
    manifest = _load_manifest()
    package = cast(dict[str, object], manifest["package"])
    expectation = _workflow_expectations()[0]

    assert PACKAGE_VERSION == "0.22.0"
    assert package["package_version"] == PACKAGE_VERSION

    selected = conformance.select_package_from_path(
        tmp_path / "selection",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=expectation.workflow_id,
        workflow_version=expectation.workflow_version,
    )
    plan = selected.plan
    assert plan is not None
    selected_asset_pins = _expected_selected_asset_pins(
        manifest,
        expectation.workflow_id,
    )
    conformance.assert_selected_package_pin(
        plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=expectation.workflow_id,
        workflow_version=expectation.workflow_version,
        selected_asset_pins=selected_asset_pins,
    )

    fingerprint = authority_fingerprint(plan)
    state = empty_runtime_state()
    for transition_input, transition_id in (
        (InitializeWorkspace("v022-initialize"), "v022-initialize"),
        (
            AdmitPlan(
                "v022-admit",
                selected_plan=plan,
                authority_fingerprint=fingerprint,
            ),
            "v022-admit",
        ),
    ):
        decision = decide(
            state,
            transition_input,
            deterministic_context(transition_id=transition_id),
        )
        assert decision.accepted is True
        state = apply(state, decision)

    runtime_root = tmp_path / "runtime"
    store, cas_store = _store(runtime_root)
    store.persist_runtime_state(state, cas_store)
    store.close()

    reopened = SQLiteRuntimeStore.open(runtime_root / "runtime.sqlite3")
    try:
        reloaded = reopened.load_runtime_state(cas_store)
    finally:
        reopened.close()

    admitted = reloaded.admitted_plans[fingerprint]
    assert admitted.plan_ref.authority_fingerprint == fingerprint
    assert authority_fingerprint(admitted.selected_plan) == fingerprint
    conformance.assert_selected_package_pin(
        admitted.selected_plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=expectation.workflow_id,
        workflow_version=expectation.workflow_version,
        selected_asset_pins=selected_asset_pins,
    )


def test_every_official_workflow_selects_verifies_and_records_final_pins(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflows = _workflows_by_id(manifest)
    assets_by_id = _assets_by_id(manifest)

    assert tuple(workflows) == tuple(
        expectation.workflow_id for expectation in _workflow_expectations()
    )
    for expectation in _workflow_expectations():
        workflow = workflows[expectation.workflow_id]
        assert workflow["workflow_version"] == expectation.workflow_version
        assert workflow["visibility"] == "public"
        assert workflow["entrypoints"] == ["default"]
        assert (
            tuple(
                str(required_asset["asset_id"])
                for required_asset in cast(
                    list[dict[str, object]],
                    workflow["required_assets"],
                )
            )
            == expectation.expected_asset_ids
        )
        assert {
            asset_id
            for asset_id in assets_by_id
            if asset_id in expectation.expected_asset_ids
        } == set(expectation.expected_asset_ids)

    archive_bytes = export_workflow_package_directory(PACKAGE_ROOT)
    wheel_path = conformance.build_project_wheel(PROJECT_ROOT, tmp_path / "dist")
    conformance.assert_installed_discovery_is_byte_only(
        monkeypatch,
        wheel_path=wheel_path,
        target=tmp_path / "site",
        distribution_name=DIST_NAME,
        import_package=IMPORT_PACKAGE,
        installed_resource_root=RESOURCE_ROOT,
        expected_package_root=PACKAGE_ROOT,
    )

    path_plans = _enable_select_verify_all_workflows(
        tmp_path / "path",
        command_label="path",
        import_operation_id="package.import_path",
        mutation_kwargs={"package_root": PACKAGE_ROOT},
    )
    archive_plans = _enable_select_verify_all_workflows(
        tmp_path / "archive",
        command_label="archive",
        import_operation_id="package.import_archive",
        mutation_kwargs={
            "archive_bytes": archive_bytes,
            "source_uri": "memory://millrace-plus-official.mrpkg.tar",
        },
    )
    installed_plans = _enable_select_verify_all_workflows(
        tmp_path / "installed",
        command_label="installed",
        import_operation_id="package.import_installed",
        mutation_kwargs={
            "installed_distribution_name": DIST_NAME,
            "installed_resource_root": RESOURCE_ROOT,
        },
    )

    assert _fingerprints_by_workflow(path_plans) == _fingerprints_by_workflow(
        archive_plans,
    )
    assert _fingerprints_by_workflow(archive_plans) == _fingerprints_by_workflow(
        installed_plans,
    )


def test_unselected_package_content_does_not_affect_selected_fingerprints(
    tmp_path: Path,
) -> None:
    base_plans = _enable_select_verify_all_workflows(
        tmp_path / "base",
        command_label="base",
        import_operation_id="package.import_path",
        mutation_kwargs={"package_root": PACKAGE_ROOT},
    )
    mutated_root = _copy_package_with_mutated_vendor_catalog(tmp_path)
    mutated_plans = _enable_select_verify_all_workflows(
        tmp_path / "mutated",
        command_label="mutated",
        import_operation_id="package.import_path",
        mutation_kwargs={"package_root": mutated_root},
    )

    assert (
        _load_manifest(mutated_root)["manifest_digest"]
        != _load_manifest()["manifest_digest"]
    )
    for plan in mutated_plans.values():
        authority_text = canonical_authority_bytes(plan).decode("utf-8")
        assert "PLUS-0002.9 Mutated Catalog Entry" not in authority_text
        assert "unselected_catalog" not in authority_text
        assert "marketplace" not in authority_text
    vendor_authority_text = canonical_authority_bytes(
        mutated_plans["vendor_selection"],
    ).decode("utf-8")
    assert "provider_ref" not in vendor_authority_text
    assert _fingerprints_by_workflow(base_plans) == _fingerprints_by_workflow(
        mutated_plans,
    )


def test_changing_one_asset_only_changes_workflows_that_select_that_asset(
    tmp_path: Path,
) -> None:
    mutated_asset_id = "planning.entrypoints.recon"
    base_plans = _enable_select_verify_all_workflows(
        tmp_path / "base",
        command_label="base-asset",
        import_operation_id="package.import_path",
        mutation_kwargs={"package_root": PACKAGE_ROOT},
    )
    mutated_root = _copy_package_with_mutated_asset(tmp_path, mutated_asset_id)
    mutated_plans = _enable_select_verify_all_workflows(
        tmp_path / "mutated",
        command_label="mutated-asset",
        import_operation_id="package.import_path",
        mutation_kwargs={"package_root": mutated_root},
    )

    workflows_selecting_asset = {
        expectation.workflow_id
        for expectation in _workflow_expectations()
        if mutated_asset_id in expectation.expected_asset_ids
    }
    assert workflows_selecting_asset == {"planning.lad", "lad.full"}

    base_fingerprints = _fingerprints_by_workflow(base_plans)
    mutated_fingerprints = _fingerprints_by_workflow(mutated_plans)
    changed_workflows = {
        workflow_id
        for workflow_id, fingerprint in mutated_fingerprints.items()
        if base_fingerprints[workflow_id] != fingerprint
    }

    assert changed_workflows == workflows_selecting_asset


def test_boundary_lint_scans_every_shipped_prompt_and_skill_asset() -> None:
    manifest = _load_manifest()
    asset_texts_by_path = _asset_texts_by_path(manifest)
    asset_texts_by_id = _asset_texts_by_id(manifest)

    assert len(asset_texts_by_path) == 62
    conformance.assert_no_runtime_authority_claims(asset_texts_by_path)
    conformance.assert_no_unscoped_selected_artifact_kind_mentions(
        asset_texts_by_id,
        declared_artifact_schema_ids_by_asset_id=(
            conformance.selected_artifact_schema_ids_by_asset_id(manifest)
        ),
    )

    bad_claims = {
        "bad-route.md": "Return `DONE` to route and close the work item.",
        "bad-provider.md": "This package ships provider credentials.",
        "bad-state.md": "The skill mutates durable state.",
        "bad-plugin.md": "The manifest bundles MCP tool execution.",
    }
    for name, text in bad_claims.items():
        try:
            conformance.assert_no_runtime_authority_claims({name: text})
        except AssertionError:
            continue
        raise AssertionError(f"boundary lint accepted {name}")


def test_final_v021_asset_parity_inventory_covers_all_legacy_pairs() -> None:
    legacy_entrypoints = _source_file_paths("entrypoints/*/*.md")
    legacy_skills = _source_file_paths("skills/stage/*/*/SKILL.md")
    expectations = _legacy_pair_expectations()
    asset_ids = set(_assets_by_id(_load_manifest()))

    assert len(legacy_entrypoints) == 22
    assert len(legacy_skills) == 22
    assert {row.entrypoint_path for row in expectations} == legacy_entrypoints
    assert {row.skill_path for row in expectations} == legacy_skills
    for expectation in expectations:
        assert expectation.stage_id
        assert expectation.owner_packet
        assert expectation.selector
        assert expectation.disposition
        if expectation.asset_ids is None:
            assert expectation.disposition == "defer_post_cutover"
        else:
            assert len(expectation.asset_ids) == 2
            assert set(expectation.asset_ids) <= asset_ids


def test_millrace_ai_public_inventory_remains_kernel_ping_only() -> None:
    import millrace.workflows as workflows
    from millrace.workflows.inventory import (
        INCLUDED_WORKFLOW_IDS,
        included_workflow_source,
        included_workflows,
    )

    assert workflows.__all__ == (
        "kernel_ping",
        "IncludedWorkflow",
        "INCLUDED_WORKFLOW_IDS",
        "included_workflows",
        "included_workflow_source",
    )
    assert INCLUDED_WORKFLOW_IDS == ("kernel_ping",)
    assert tuple(workflow.workflow_id for workflow in included_workflows()) == (
        "kernel_ping",
    )
    for workflow_id in (
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
        "lad.full",
        "vendor_selection",
    ):
        try:
            included_workflow_source(workflow_id)
        except KeyError as exc:
            assert exc.args == (workflow_id,)
            continue
        raise AssertionError(f"{workflow_id} leaked into base inventory")
