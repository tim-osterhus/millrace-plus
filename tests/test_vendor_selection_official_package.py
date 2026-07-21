from __future__ import annotations

# ruff: noqa: E402
import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pytest

from support.internal_conformance_gate import require_internal_conformance

require_internal_conformance()

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import canonical_authority_bytes
from millrace.contracts.workflow_package import manifest_digest_for_manifest
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
from millrace.workflows import vendor_selection

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.0.0"
WORKFLOW_ID = "vendor_selection"
WORKFLOW_VERSION = "0.1"
EXISTING_WORKFLOW_IDS = (
    "simple_loop",
    "execution.lad",
    "execution.lad_integrator",
    "planning.lad",
    "lad.full",
)
VENDOR_STAGE_IDS = (
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
AFFECTED_DECISION_CONTEXT_STAGE_IDS = (
    "requirement_freezer",
    "catalog_sourcer",
    "candidate_packager",
    "rubric_evaluator",
    "conflict_checker",
    "award_decider",
)
ENTRYPOINT_HEADINGS = (
    "Role:",
    "Scope:",
    "Inputs from dispatch:",
    "Readable assets:",
    "Writable artifacts:",
    "Required evidence:",
    "Legal terminal markers rendered by runtime:",
    "Forbidden claims:",
    "How to return evidence:",
    "When to stop:",
)
CORE_SKILL_HEADINGS = (
    "## Stage Contract",
    "## Artifact Schemas",
    "## Marker Artifact Protocol",
    "## Handoff Format",
    "## Valid Example",
    "## Invalid Example",
    "## Validation Checklist",
    "## Completion Criteria",
)
MARKER_PROTOCOL = {
    "request_intake": (
        (
            "REQUEST_READY",
            "vendor_selection.request_intake.request_ready",
            "PurchaseRequest",
        ),
        (
            "REQUEST_NEEDS_CLARIFICATION",
            "vendor_selection.request_intake.needs_clarification",
            "DecisionPack",
        ),
    ),
    "policy_screener": (
        (
            "POLICY_ALLOWED",
            "vendor_selection.policy_screener.policy_allowed",
            "PurchaseRequest",
        ),
        (
            "POLICY_BLOCKED",
            "vendor_selection.policy_screener.policy_blocked",
            "PolicyDecision",
        ),
    ),
    "requirement_freezer": (
        (
            "REQUIREMENTS_READY",
            "vendor_selection.requirement_freezer.requirements_ready",
            "RequirementPacket",
        ),
    ),
    "catalog_sourcer": (
        (
            "CANDIDATES_READY",
            "vendor_selection.catalog_sourcer.candidates_ready",
            "CandidateBundle",
        ),
        (
            "NO_VIABLE_VENDOR",
            "vendor_selection.catalog_sourcer.no_viable_vendor",
            "DecisionPack",
        ),
    ),
    "candidate_packager": (
        (
            "CANDIDATES_READY",
            "vendor_selection.candidate_packager.candidates_ready",
            "CandidateBundle",
        ),
    ),
    "rubric_evaluator": (
        (
            "RUBRIC_COMPLETE",
            "vendor_selection.rubric_evaluator.rubric_complete",
            "RubricReport",
        ),
    ),
    "conflict_checker": (
        (
            "CONFLICT_COMPLETE",
            "vendor_selection.conflict_checker.conflict_complete",
            "ConflictReport",
        ),
    ),
    "award_decider": (
        ("AWARD_READY", "vendor_selection.award_decider.award_ready", "AwardDecision"),
        (
            "RESOURCE_REQUIRED",
            "vendor_selection.award_decider.resource_required",
            "RequirementPacket",
        ),
        (
            "OPERATOR_REQUIRED",
            "vendor_selection.award_decider.operator_required",
            "AwardDecision",
        ),
        (
            "NO_VIABLE_VENDOR",
            "vendor_selection.award_decider.no_viable_vendor",
            "DecisionPack",
        ),
        ("BLOCKED", "vendor_selection.award_decider.blocked", "DecisionPack"),
    ),
    "decision_packager": (
        (
            "DECISION_PACK_READY",
            "vendor_selection.decision_packager.decision_pack_ready",
            "DecisionPack",
        ),
    ),
}
MARKER_PROTOCOL_PATTERN = re.compile(
    r"^- (?P<marker>[A-Z_]+): selected action `(?P<action_id>[^`]+)`; "
    r"action kind `(?P<action_kind>[^`]+)`; artifact schema "
    r"`(?P<artifact_schema_id>[^`]+)`; emitted queue `(?P<emitted_queue>[^`]+)`; "
    r"target stage `(?P<target_stage>[^`]+)`\.$",
)
CATALOG_RECORDS = (
    (
        "vendor_alpha",
        "Alpha Stationery",
        "standard_office_supplies, net30_invoice",
        "low",
        "clear",
    ),
    (
        "vendor_beta",
        "Beta Supplies",
        "standard_office_supplies, rush_delivery",
        "medium",
        "blocked",
    ),
    (
        "vendor_gamma",
        "Gamma Office",
        "standard_office_supplies, net30_invoice, rush_delivery",
        "medium",
        "clear",
    ),
)


def _load_manifest(package_root: Path = PACKAGE_ROOT) -> dict[str, Any]:
    return conformance.load_manifest_source(package_root)


def _workflows_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        str(workflow["workflow_id"]): workflow
        for workflow in cast(list[dict[str, object]], manifest["workflows"])
    }


def _assets_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        str(asset["asset_id"]): asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
    }


def _source_as_selected_authority(source: dict[str, object]) -> dict[str, object]:
    selected = cast(dict[str, object], json.loads(json.dumps(source)))
    selected.pop("assets")
    return selected


def _without_runner_authority(authority: dict[str, object]) -> dict[str, object]:
    normalized = cast(dict[str, object], json.loads(json.dumps(authority)))
    normalized.pop("capabilities", None)
    normalized.pop("runner_bindings")

    def normalize_refs(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"runner_binding_id", "target_runner_binding_id"}:
                    value[key] = "<selected-runner-binding>"
                else:
                    normalize_refs(child)
        elif isinstance(value, list):
            for child in value:
                normalize_refs(child)

    normalize_refs(normalized)
    return normalized


def _expected_entrypoint_asset_id(stage_id: str) -> str:
    return f"vendor_selection.entrypoints.{stage_id}"


def _expected_core_skill_asset_id(stage_id: str) -> str:
    return f"vendor_selection.skills.{stage_id}_core"


def _expected_vendor_asset_ids() -> tuple[str, ...]:
    asset_ids: list[str] = []
    for stage_id in VENDOR_STAGE_IDS:
        asset_ids.append(_expected_entrypoint_asset_id(stage_id))
        asset_ids.append(_expected_core_skill_asset_id(stage_id))
    return tuple(asset_ids)


def _expected_vendor_asset_pins(
    manifest: dict[str, Any],
    package_root: Path = PACKAGE_ROOT,
) -> tuple[tuple[str, str], ...]:
    assets_by_id = _assets_by_id(manifest)
    return tuple(
        (
            asset_id,
            conformance.asset_digest_for_package_path(
                package_root,
                str(assets_by_id[asset_id]["package_path"]),
            ),
        )
        for asset_id in sorted(_expected_vendor_asset_ids())
    )


def _asset_texts_by_id(manifest: dict[str, Any]) -> dict[str, str]:
    assets_by_id = _assets_by_id(manifest)
    return {
        asset_id: (
            PACKAGE_ROOT / str(assets_by_id[asset_id]["package_path"])
        ).read_text()
        for asset_id in _expected_vendor_asset_ids()
    }


def _schemas_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    return {
        str(schema["id"]): cast(dict[str, object], schema["schema"])
        for schema in cast(
            list[dict[str, object]], selected_authority["artifact_schemas"]
        )
    }


def _vendor_selected_authority(manifest: dict[str, Any]) -> dict[str, object]:
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    return cast(dict[str, object], workflow["selected_authority"])


def _marker_schema_by_stage(manifest: dict[str, Any]) -> dict[str, dict[str, str]]:
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    outcomes = {
        str(outcome["id"]): (
            str(outcome["stage_kind_id"]),
            str(outcome["marker"]),
        )
        for outcome in cast(
            list[dict[str, object]],
            selected_authority["terminal_outcomes"],
        )
    }
    marker_schema_by_stage: dict[str, dict[str, str]] = {
        stage_id: {} for stage_id in VENDOR_STAGE_IDS
    }
    for action in cast(
        list[dict[str, object]],
        selected_authority["terminal_actions"],
    ):
        outcome_id = str(action["outcome_id"])
        stage_id, marker = outcomes[outcome_id]
        marker_schema_by_stage[stage_id][marker] = str(action["artifact_schema_id"])
    return marker_schema_by_stage


def _expected_marker_protocol_rows(
    manifest: dict[str, Any],
) -> dict[str, tuple[dict[str, str], ...]]:
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    outcomes = {
        str(outcome["id"]): (
            str(outcome["stage_kind_id"]),
            str(outcome["marker"]),
        )
        for outcome in cast(
            list[dict[str, object]],
            selected_authority["terminal_outcomes"],
        )
    }
    rows_by_stage: dict[str, list[dict[str, str]]] = {
        stage_id: [] for stage_id in VENDOR_STAGE_IDS
    }
    for action in cast(
        list[dict[str, object]],
        selected_authority["terminal_actions"],
    ):
        stage_id, marker = outcomes[str(action["outcome_id"])]
        rows_by_stage[stage_id].append(
            {
                "marker": marker,
                "action_id": str(action["id"]),
                "action_kind": str(action["kind"]),
                "artifact_schema_id": str(action["artifact_schema_id"]),
                "emitted_queue": str(action.get("emitted_queue_family_id", "none")),
                "target_stage": str(action.get("target_stage_kind_id", "none")),
            }
        )
    return {stage_id: tuple(rows) for stage_id, rows in rows_by_stage.items()}


def _marker_protocol_rows_from_text(text: str) -> tuple[dict[str, str], ...]:
    rows = []
    for line in text.splitlines():
        match = MARKER_PROTOCOL_PATTERN.match(line)
        if match is None:
            continue
        rows.append(match.groupdict())
    return tuple(rows)


def _core_skill_examples(
    text: str,
) -> tuple[tuple[dict[str, object], ...], tuple[tuple[str, dict[str, object]], ...]]:
    valid_match = re.search(
        r"## Valid Example\nValid examples:\n```json\n(?P<json>.*?)\n```",
        text,
        re.DOTALL,
    )
    invalid_match = re.search(
        r"## Invalid Example\nInvalid examples?:\n```json\n(?P<json>.*?)\n```",
        text,
        re.DOTALL,
    )
    assert valid_match is not None
    assert invalid_match is not None
    valid_examples = json.loads(valid_match.group("json"))
    invalid_examples = json.loads(invalid_match.group("json"))
    assert isinstance(valid_examples, list)
    if isinstance(invalid_examples, dict):
        invalid_cases = (
            (
                "legacy_undeclared_extra_field_wrapper",
                cast(dict[str, object], invalid_examples),
            ),
        )
    else:
        assert isinstance(invalid_examples, list)
        invalid_cases = tuple(
            (
                str(case["case"]),
                cast(dict[str, object], case["example"]),
            )
            for case in cast(list[dict[str, object]], invalid_examples)
        )
    return (
        tuple(cast(list[dict[str, object]], valid_examples)),
        invalid_cases,
    )


def _award_decider_policy_cases(text: str) -> tuple[dict[str, object], ...]:
    match = re.search(
        r"## Decision Policy Cases\nPolicy cases:\n```json\n(?P<json>.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None
    parsed = json.loads(match.group("json"))
    assert isinstance(parsed, list)
    return tuple(cast(list[dict[str, object]], parsed))


def _assert_example_matches_selected_schema(
    example: dict[str, object],
    *,
    stage_id: str,
    marker_schema_by_stage: dict[str, dict[str, str]],
    schemas_by_id: dict[str, dict[str, object]],
) -> None:
    conformance.assert_marker_artifact_example_matches_selected_schema(
        example,
        stage_id=stage_id,
        marker_schema_by_stage=marker_schema_by_stage,
        schemas_by_id=schemas_by_id,
    )


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _import_enable_select_verify(
    tmp_path: Path,
    package_root: Path,
    *,
    command_label: str,
):
    store, cas_store = _store(tmp_path)
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=f"{command_label}-import-path",
            operation_id="package.import_path",
            actor_id="operator:test",
            package_root=package_root,
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
    verified = execute_package_verify_command(
        store,
        cas_store,
        PackageWorkflowVerifyCommand(
            command_id=f"{command_label}-verify",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=WORKFLOW_ID,
            workflow_version=WORKFLOW_VERSION,
        ),
    )
    selected = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id=f"{command_label}-select",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=WORKFLOW_ID,
            workflow_version=WORKFLOW_VERSION,
        ),
    )

    assert imported.outcome == "succeeded"
    assert enabled.outcome == "succeeded"
    assert verified.outcome == "succeeded"
    assert verified.plan_ready
    assert selected.outcome == "succeeded"
    assert selected.plan is not None
    return imported, verified, selected


def _import_archive_enable_select_verify(
    tmp_path: Path,
    package_root: Path,
    *,
    command_label: str,
):
    store, cas_store = _store(tmp_path)
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id=f"{command_label}-import-archive",
            operation_id="package.import_archive",
            actor_id="operator:test",
            archive_bytes=export_workflow_package_directory(package_root),
            source_uri=f"memory://{package_root.name}.mrpkg.tar",
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
    verified = execute_package_verify_command(
        store,
        cas_store,
        PackageWorkflowVerifyCommand(
            command_id=f"{command_label}-verify",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=WORKFLOW_ID,
            workflow_version=WORKFLOW_VERSION,
        ),
    )
    selected = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id=f"{command_label}-select",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=WORKFLOW_ID,
            workflow_version=WORKFLOW_VERSION,
        ),
    )

    assert imported.outcome == "succeeded"
    assert enabled.outcome == "succeeded"
    assert verified.outcome == "succeeded"
    assert verified.plan_ready
    assert selected.outcome == "succeeded"
    assert selected.plan is not None
    return imported, verified, selected


def _write_manifest(package_root: Path, manifest: dict[str, Any]) -> None:
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )


def _copy_pruned_package_without_vendor_selection(tmp_path: Path) -> Path:
    pruned_root = tmp_path / "without-vendor-selection"
    shutil.copytree(PACKAGE_ROOT, pruned_root)
    manifest = _load_manifest(pruned_root)

    manifest["workflows"] = [
        workflow
        for workflow in cast(list[dict[str, object]], manifest["workflows"])
        if workflow["workflow_id"] != WORKFLOW_ID
    ]
    metadata = cast(dict[str, object], manifest["non_authoritative_metadata"])
    metadata["plus_packet"] = "PLUS-0002E"
    metadata["status"] = (
        "official_simple_loop_lad_execution_lad_planning_and_full_lad_workflow_package"
    )
    _write_manifest(pruned_root, manifest)
    return pruned_root


def _copy_package_with_mutated_unselected_catalog(tmp_path: Path) -> Path:
    mutated_root = tmp_path / "mutated-unselected-catalog"
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    manifest = _load_manifest(mutated_root)
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    unselected_catalog = cast(
        list[dict[str, object]],
        selected_authority["unselected_catalog"],
    )
    unselected_catalog[0]["vendor_label"] = "Alpha Stationery Mutated"
    _write_manifest(mutated_root, manifest)
    return mutated_root


def _copy_package_with_mutated_selected_marker(tmp_path: Path) -> Path:
    mutated_root = tmp_path / "mutated-selected-marker"
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    manifest = _load_manifest(mutated_root)
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    terminal_outcomes = cast(
        list[dict[str, object]],
        selected_authority["terminal_outcomes"],
    )
    terminal_outcomes[0]["marker"] = "REQUEST_READY_MUTATED"
    _write_manifest(mutated_root, manifest)
    return mutated_root


def test_vendor_selection_workflow_identity_matches_donor_source() -> None:
    manifest = _load_manifest()
    workflows = _workflows_by_id(manifest)
    source_identity = cast(dict[str, object], vendor_selection.source()["workflow"])
    workflow = workflows[WORKFLOW_ID]

    assert set(workflows) == {*EXISTING_WORKFLOW_IDS, WORKFLOW_ID}
    assert workflow["workflow_id"] == source_identity["id"]
    assert workflow["workflow_version"] == source_identity["version"]
    assert workflow["visibility"] == "public"
    assert workflow["entrypoints"] == ["default"]


def test_vendor_selection_selected_authority_matches_donor_plus_selected_assets() -> (
    None
):
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    source = vendor_selection.source()
    expected_authority = _source_as_selected_authority(source)
    for stage in cast(list[dict[str, object]], expected_authority["stage_kinds"]):
        stage_id = str(stage["id"])
        stage["asset_ids"] = [
            _expected_entrypoint_asset_id(stage_id),
            _expected_core_skill_asset_id(stage_id),
        ]

    assert _without_runner_authority(selected_authority) == (
        _without_runner_authority(expected_authority)
    )
    assert "assets" not in selected_authority
    assert "unselected_catalog" in selected_authority
    assert len(cast(list[object], selected_authority["unselected_catalog"])) == 3
    assert tuple(cast(list[object], source["assets"])) == ()
    assert [
        str(required_asset["asset_id"])
        for required_asset in cast(list[dict[str, object]], workflow["required_assets"])
    ] == list(_expected_vendor_asset_ids())
    assert (PACKAGE_ROOT / "assets" / "workflows" / WORKFLOW_ID).is_dir()


def test_vendor_selection_path_archive_selection_compiles_selected_asset_shape(
    tmp_path: Path,
) -> None:
    manifest = _load_manifest()
    expected_asset_pins = _expected_vendor_asset_pins(manifest)
    path_result, archive_result = conformance.select_package_from_path_and_archive(
        tmp_path / "selection",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
    )

    for result in (path_result, archive_result):
        conformance.assert_selected_package_pin(
            result.plan,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=WORKFLOW_ID,
            workflow_version=WORKFLOW_VERSION,
            selected_asset_pins=expected_asset_pins,
        )
        plan = result.plan
        assert plan is not None
        assert len(plan.assets) == 18
        assert tuple(str(asset.id) for asset in plan.assets) == tuple(
            sorted(_expected_vendor_asset_ids()),
        )
        assert {str(partition.id) for partition in plan.partitions} == {
            "requirements",
            "sourcing",
            "evaluation",
            "authorization",
        }
        assert len(plan.queue_families) == 7
        assert len(plan.stage_kinds) == 9
        assert len(plan.terminal_outcomes) == 16
        assert len(plan.terminal_actions) == 16
        assert len(plan.fanout_declarations) == 2
        assert tuple(str(fanout.id) for fanout in plan.fanout_declarations) == (
            "vendor_selection.candidate_packager.conflict_fanout",
            "vendor_selection.candidate_packager.rubric_fanout",
        )
        assert len(plan.join_declarations) == 1
        assert str(plan.join_declarations[0].id) == "candidate_evidence_join"
        assert len(plan.operator_waits) == 1
        assert str(plan.operator_waits[0].id) == (
            "vendor_selection.award_operator_wait"
        )
        assert len(plan.runner_bindings) == len(VENDOR_STAGE_IDS)
        assert len(plan.effect_declarations) == 0
        authority_text = canonical_authority_bytes(plan).decode("utf-8")
        assert "unselected_catalog" not in authority_text
        assert "provider_ref" not in authority_text

    assert authority_fingerprint(path_result.plan) == authority_fingerprint(
        archive_result.plan,
    )
    _, archive_verified, archive_selected = _import_archive_enable_select_verify(
        tmp_path / "archive-verify",
        PACKAGE_ROOT,
        command_label="vendor-selection-archive",
    )
    assert archive_verified.package is not None
    assert archive_verified.package.package_id == PACKAGE_ID
    conformance.assert_selected_package_pin(
        archive_selected.plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        selected_asset_pins=expected_asset_pins,
    )
    assert authority_fingerprint(archive_selected.plan) == authority_fingerprint(
        path_result.plan,
    )
    _, verified, selected = _import_enable_select_verify(
        tmp_path / "verify",
        PACKAGE_ROOT,
        command_label="vendor-selection",
    )
    assert verified.package is not None
    assert verified.package.package_id == PACKAGE_ID
    assert authority_fingerprint(selected.plan) == authority_fingerprint(
        path_result.plan,
    )


def test_vendor_selection_declares_required_decision_context_handoffs() -> None:
    manifest = _load_manifest()
    schemas = _schemas_by_id(manifest)
    selected_authority = _vendor_selected_authority(manifest)

    requirement_schema = schemas["RequirementPacket"]
    assert cast(list[str], requirement_schema["required"]) == [
        "source_request_id",
        "approval_policy_hint",
        "frozen_requirements",
        "policy_status",
        "selection_rubric_id",
        "conflict_rules",
        "candidate_count_min",
        "candidate_count_max",
    ]
    requirement_properties = cast(dict[str, object], requirement_schema["properties"])
    assert requirement_properties["approval_policy_hint"] == {
        "enum": ["none", "operator_required"]
    }

    candidate_schema = schemas["CandidateBundle"]
    assert cast(list[str], candidate_schema["required"]) == [
        "source_requirement_id",
        "bundle_id",
        "candidate_vendors",
        "deterministic_source_refs",
        "approval_policy_hint",
        "conflict_rules",
    ]
    candidate_properties = cast(dict[str, object], candidate_schema["properties"])
    assert candidate_properties["approval_policy_hint"] == {
        "enum": ["none", "operator_required"]
    }
    assert candidate_properties["conflict_rules"] == {
        "items": {"min_length": 1, "type": "string"},
        "min_items": 1,
        "type": "array",
    }
    candidate_items = cast(
        dict[str, object],
        cast(dict[str, object], candidate_properties["candidate_vendors"])["items"],
    )
    assert cast(list[str], candidate_items["required"]) == [
        "candidate_id",
        "vendor_label",
        "capabilities",
        "budget_band",
        "catalog_ref",
        "conflict_status",
    ]
    candidate_item_properties = cast(dict[str, object], candidate_items["properties"])
    assert candidate_item_properties["conflict_status"] == {
        "enum": ["clear", "blocked"]
    }

    expected_mapping = {
        "source_requirement_id": ["source_requirement_id"],
        "bundle_id": ["bundle_id"],
        "candidate_vendors": ["candidate_vendors"],
        "deterministic_source_refs": ["deterministic_source_refs"],
        "approval_policy_hint": ["approval_policy_hint"],
        "conflict_rules": ["conflict_rules"],
    }
    fanouts = cast(
        list[dict[str, object]],
        selected_authority["fanout_declarations"],
    )
    assert [fanout["id"] for fanout in fanouts] == [
        "vendor_selection.candidate_packager.rubric_fanout",
        "vendor_selection.candidate_packager.conflict_fanout",
    ]
    assert len(fanouts) == 2
    assert {
        fanout["target_route_id"] for fanout in fanouts
    } == {
        "vendor_selection.rubric_work",
        "vendor_selection.conflict_work",
    }
    for fanout in fanouts:
        assert cast(dict[str, object], fanout["target_payload_mapping"]) == (
            expected_mapping
        )
        assert set(
            cast(dict[str, object], fanout["target_payload_mapping"])
        ) == set(expected_mapping)


def test_vendor_selection_unselected_catalog_is_non_selected_authority(
    tmp_path: Path,
) -> None:
    base_import, _, base_selected = _import_enable_select_verify(
        tmp_path / "base",
        PACKAGE_ROOT,
        command_label="base",
    )
    mutated_root = _copy_package_with_mutated_unselected_catalog(tmp_path)
    mutated_import, _, mutated_selected = _import_enable_select_verify(
        tmp_path / "mutated",
        mutated_root,
        command_label="mutated",
    )

    assert base_import.package_record is not None
    assert mutated_import.package_record is not None
    assert base_selected.plan is not None
    assert mutated_selected.plan is not None
    assert (PACKAGE_ROOT / "manifest.json").read_bytes() != (
        mutated_root / "manifest.json"
    ).read_bytes()
    base_catalog = cast(
        list[dict[str, object]],
        _workflows_by_id(_load_manifest())[WORKFLOW_ID]["selected_authority"][
            "unselected_catalog"
        ],
    )
    mutated_catalog = cast(
        list[dict[str, object]],
        _workflows_by_id(_load_manifest(mutated_root))[WORKFLOW_ID][
            "selected_authority"
        ]["unselected_catalog"],
    )
    assert base_catalog != mutated_catalog
    assert base_import.package_record.manifest_digest != (
        mutated_import.package_record.manifest_digest
    )
    assert asdict(base_selected.plan.workflow_package_pin) == asdict(
        mutated_selected.plan.workflow_package_pin,
    )
    assert canonical_authority_bytes(base_selected.plan) == canonical_authority_bytes(
        mutated_selected.plan,
    )
    assert authority_fingerprint(base_selected.plan) == authority_fingerprint(
        mutated_selected.plan,
    )


def test_vendor_selection_selected_authority_mutation_changes_fingerprint(
    tmp_path: Path,
) -> None:
    _, _, base_selected = _import_enable_select_verify(
        tmp_path / "base",
        PACKAGE_ROOT,
        command_label="selected-base",
    )
    mutated_root = _copy_package_with_mutated_selected_marker(tmp_path)
    mutated_import, _, mutated_selected = _import_enable_select_verify(
        tmp_path / "mutated",
        mutated_root,
        command_label="selected-mutated",
    )

    assert base_selected.plan is not None
    assert mutated_import.package_record is not None
    assert mutated_selected.plan is not None
    assert (
        mutated_import.package_record.manifest_digest
        != _load_manifest()["manifest_digest"]
    )
    conformance.assert_selected_package_pin(
        mutated_selected.plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        selected_asset_pins=_expected_vendor_asset_pins(
            _load_manifest(mutated_root),
            mutated_root,
        ),
    )
    assert canonical_authority_bytes(base_selected.plan) != canonical_authority_bytes(
        mutated_selected.plan,
    )
    assert authority_fingerprint(base_selected.plan) != authority_fingerprint(
        mutated_selected.plan,
    )


def test_existing_workflow_fingerprints_stay_stable_when_vendor_selection_is_added(
    tmp_path: Path,
) -> None:
    pruned_root = _copy_pruned_package_without_vendor_selection(tmp_path)
    for workflow_id in EXISTING_WORKFLOW_IDS:
        full_result = conformance.select_package_from_path(
            tmp_path / f"full-{workflow_id.replace('.', '-')}",
            PACKAGE_ROOT,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=workflow_id,
            workflow_version="0.1",
        )
        pruned_result = conformance.select_package_from_path(
            tmp_path / f"pruned-{workflow_id.replace('.', '-')}",
            pruned_root,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=workflow_id,
            workflow_version="0.1",
        )

        assert authority_fingerprint(full_result.plan) == authority_fingerprint(
            pruned_result.plan,
        )


def test_vendor_selection_excludes_forbidden_distribution_boundaries() -> None:
    manifest = _load_manifest()
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    workflow_text = json.dumps(workflow, sort_keys=True)

    assert len(cast(list[object], workflow["required_assets"])) == 18
    assert "effect_declarations" not in workflow_text
    assert "provider_ref" not in workflow_text
    for forbidden in (
        "marketplace",
        "package registry",
        "remote install",
        "recommendation",
        "plugin",
        "MCP",
        "native_runner",
        "provider credentials",
        "provider execution",
    ):
        assert forbidden not in workflow_text


def test_vendor_selection_each_stage_has_entrypoint_prompt_and_core_skill() -> None:
    manifest = _load_manifest()
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    assets_by_id = _assets_by_id(manifest)
    stage_records = {
        str(stage["id"]): stage
        for stage in cast(list[dict[str, object]], selected_authority["stage_kinds"])
    }

    assert (
        tuple(
            asset_id
            for asset_id in assets_by_id
            if asset_id.startswith("vendor_selection.")
        )
        == _expected_vendor_asset_ids()
    )

    for stage_id in VENDOR_STAGE_IDS:
        prompt_id = _expected_entrypoint_asset_id(stage_id)
        skill_id = _expected_core_skill_asset_id(stage_id)
        prompt_path = f"assets/workflows/vendor_selection/entrypoints/{stage_id}.md"
        skill_path = f"assets/workflows/vendor_selection/skills/{stage_id}-core.md"

        assert stage_records[stage_id]["asset_ids"] == [prompt_id, skill_id]
        assert assets_by_id[prompt_id]["asset_kind"] == "entrypoint_prompt"
        assert assets_by_id[skill_id]["asset_kind"] == "stage_skill"
        assert assets_by_id[prompt_id]["package_path"] == prompt_path
        assert assets_by_id[skill_id]["package_path"] == skill_path


def test_vendor_selection_asset_text_uses_two_layer_contract() -> None:
    manifest = _load_manifest()
    asset_texts = _asset_texts_by_id(manifest)
    asset_paths = {
        str(_assets_by_id(manifest)[asset_id]["package_path"]): text
        for asset_id, text in asset_texts.items()
    }

    for stage_id in VENDOR_STAGE_IDS:
        prompt_text = asset_texts[_expected_entrypoint_asset_id(stage_id)]
        skill_text = asset_texts[_expected_core_skill_asset_id(stage_id)]
        for heading in ENTRYPOINT_HEADINGS:
            assert heading in prompt_text
        for heading in CORE_SKILL_HEADINGS:
            assert heading in skill_text
        for text in (prompt_text, skill_text):
            assert "exact selected artifact JSON object" in text
            assert "runner evidence/report" in text
            assert "Use this envelope for every artifact" not in text
            assert "Include `artifact_id`" not in text
            assert "`next_stage_context`" not in text
            assert "`fields`" not in text

    conformance.assert_no_runtime_authority_claims(asset_paths)
    conformance.assert_no_unscoped_selected_artifact_kind_mentions(
        asset_texts,
        declared_artifact_schema_ids_by_asset_id=(
            conformance.selected_artifact_schema_ids_by_asset_id(manifest)
        ),
    )


def test_policy_screener_assets_separate_request_policy_from_later_evidence() -> None:
    manifest = _load_manifest()
    asset_texts = _asset_texts_by_id(manifest)
    policy_assets = (
        asset_texts["vendor_selection.entrypoints.policy_screener"],
        asset_texts["vendor_selection.skills.policy_screener_core"],
    )

    for text in policy_assets:
        assert "e2e" not in text.lower()
        for required in (
            "synthetic_office_supplies",
            "budget band `low`",
            "standard_office_supplies",
            "net30_invoice",
            "`disallowed_vendors`",
            "`none` and `operator_required` are allowed",
            "Only category and budget are request-policy gates",
            "Missing catalog or vendor evidence is not a policy violation",
            "`operator_required` is allowed",
            "later selected operator gate",
            "explicit request-policy violation",
        ):
            assert required in text

    schemas = _schemas_by_id(manifest)
    policy_schema = schemas["PolicyDecision"]
    assert "evidence_refs" not in cast(
        dict[str, object],
        policy_schema["properties"],
    )
    assert cast(list[str], policy_schema["required"]) == [
        "source_request_id",
        "policy_status",
        "violated_policy_facts",
        "reason",
    ]


def test_vendor_selection_marker_artifact_protocol_is_exact() -> None:
    manifest = _load_manifest()
    asset_texts = _asset_texts_by_id(manifest)
    expected_rows_by_stage = _expected_marker_protocol_rows(manifest)

    all_markers = {
        marker
        for stage_protocol in MARKER_PROTOCOL.values()
        for marker, _, _ in stage_protocol
    }
    for stage_id, protocol in MARKER_PROTOCOL.items():
        prompt_text = asset_texts[_expected_entrypoint_asset_id(stage_id)]
        skill_text = asset_texts[_expected_core_skill_asset_id(stage_id)]
        stage_text = prompt_text + "\n" + skill_text
        expected_rows = expected_rows_by_stage[stage_id]
        assert _marker_protocol_rows_from_text(prompt_text) == expected_rows
        assert _marker_protocol_rows_from_text(skill_text) == expected_rows

        stage_markers = {marker for marker, _, _ in protocol}
        for marker, action_id, artifact_schema_id in protocol:
            assert marker in stage_text
            assert action_id in stage_text
            assert artifact_schema_id in stage_text
        for undeclared_marker in all_markers - stage_markers:
            assert (
                re.search(rf"(?<![A-Z_]){undeclared_marker}(?![A-Z_])", stage_text)
                is None
            )
        if stage_id != "award_decider":
            assert re.search(r"(?<![A-Z_])BLOCKED(?![A-Z_])", stage_text) is None

    corrupted = next(iter(expected_rows_by_stage["request_intake"])).copy()
    corrupted["artifact_schema_id"] = "DecisionPack"
    assert (corrupted, *expected_rows_by_stage["request_intake"][1:]) != (
        expected_rows_by_stage["request_intake"]
    )


def test_vendor_selection_core_skill_examples_match_selected_schemas() -> None:
    manifest = _load_manifest()
    asset_texts = _asset_texts_by_id(manifest)
    schemas_by_id = _schemas_by_id(manifest)
    marker_schema_by_stage = _marker_schema_by_stage(manifest)

    for stage_id in VENDOR_STAGE_IDS:
        skill_text = asset_texts[_expected_core_skill_asset_id(stage_id)]
        valid_examples, invalid_cases = _core_skill_examples(skill_text)

        seen_markers: set[str] = set()
        for valid_example in valid_examples:
            _assert_example_matches_selected_schema(
                valid_example,
                stage_id=stage_id,
                marker_schema_by_stage=marker_schema_by_stage,
                schemas_by_id=schemas_by_id,
            )
            seen_markers.add(str(valid_example["terminal_marker"]))
            artifact = cast(dict[str, object], valid_example["artifact"])
            conformance.assert_not_generic_artifact_envelope_body(artifact)

        assert seen_markers == set(marker_schema_by_stage[stage_id])
        if stage_id in AFFECTED_DECISION_CONTEXT_STAGE_IDS:
            assert {case for case, _example in invalid_cases} == {
                "undeclared_extra_field_wrapper",
                "missing_required_field",
                "wrong_type",
            }
        for case, invalid_example in invalid_cases:
            with pytest.raises(AssertionError):
                _assert_example_matches_selected_schema(
                    invalid_example,
                    stage_id=stage_id,
                    marker_schema_by_stage=marker_schema_by_stage,
                    schemas_by_id=schemas_by_id,
                )
            if case == "undeclared_extra_field_wrapper":
                invalid_artifact = cast(
                    dict[str, object],
                    invalid_example["artifact"],
                )
                assert "fields" in invalid_artifact

    request_examples, _ = _core_skill_examples(
        asset_texts["vendor_selection.skills.request_intake_core"],
    )
    request_ready = next(
        example
        for example in request_examples
        if example["terminal_marker"] == "REQUEST_READY"
    )
    assert request_ready["artifact"] == {
        "request_id": "e2e-vendor-selection-001",
        "requester_label": "local-e2e-operator",
        "category": "synthetic_office_supplies",
        "budget_band": "low",
        "required_capabilities": [
            "standard_office_supplies",
            "net30_invoice",
        ],
        "disallowed_vendors": ["Beta Supplies"],
        "approval_policy_hint": "operator_required",
    }

    award_examples, _ = _core_skill_examples(
        asset_texts["vendor_selection.skills.award_decider_core"],
    )
    operator_required = next(
        example
        for example in award_examples
        if example["terminal_marker"] == "OPERATOR_REQUIRED"
    )
    assert operator_required["artifact"] == {
        "bundle_id": "bundle-e2e-vendor-selection-001",
        "decision_kind": "operator_required",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
            "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
            "conflict_report_ref": "conflict-report-e2e-vendor-selection-001",
        },
        "operator_gate_required": True,
        "reason": "Selected evidence requires local-operator confirmation.",
    }

    selected_schema_canary = {
        "terminal_marker": "REQUEST_READY",
        "artifact": {
            "artifact_id": "bad-selected-schema-canary",
            "artifact_kind": "PurchaseRequest",
            "fields": {"selected_schema": "PurchaseRequest"},
            "evidence": [],
        },
    }
    with pytest.raises(AssertionError):
        _assert_example_matches_selected_schema(
            selected_schema_canary,
            stage_id="request_intake",
            marker_schema_by_stage=marker_schema_by_stage,
            schemas_by_id=schemas_by_id,
        )


def test_package_example_schema_helper_rejects_unique_by_duplicates() -> None:
    schema = {
        "type": "array",
        "min_items": 1,
        "unique_by": "candidate_id",
        "items": {
            "type": "object",
            "required": ["candidate_id", "vendor_label"],
            "properties": {
                "candidate_id": {"type": "string", "min_length": 1},
                "vendor_label": {"type": "string", "min_length": 1},
            },
        },
    }

    with pytest.raises(AssertionError):
        conformance.assert_schema_value(
            [
                {"candidate_id": "vendor_alpha", "vendor_label": "Alpha"},
                {"candidate_id": "vendor_alpha", "vendor_label": "Alpha duplicate"},
            ],
            schema,
        )


def test_vendor_selection_core_skill_schema_sections_include_constraints() -> None:
    manifest = _load_manifest()
    asset_texts = _asset_texts_by_id(manifest)
    core_skill_text = "\n".join(
        asset_texts[_expected_core_skill_asset_id(stage_id)]
        for stage_id in VENDOR_STAGE_IDS
    )

    for required_snippet in (
        "enum [none, operator_required]",
        "enum [award, re_source, reject, operator_required, blocked]",
        "enum [vendor_alpha, vendor_beta, vendor_gamma, null]",
        "const `rubric`",
        "const `conflict`",
        "const `local_operator`",
        "array; min_items 1",
        "unique_by `candidate_id`",
        (
            "`required_evidence_refs` | yes | object; required "
            "[rubric_report_ref, conflict_report_ref]"
        ),
        "`evidence_refs` | yes | object; required "
        "[rubric_report_ref, conflict_report_ref]",
    ):
        assert required_snippet in core_skill_text


def test_award_decider_core_skill_declares_parseable_decision_policy_cases() -> None:
    manifest = _load_manifest()
    asset_text = _asset_texts_by_id(manifest)[
        "vendor_selection.skills.award_decider_core"
    ]
    marker_schema_by_stage = _marker_schema_by_stage(manifest)
    cases = _award_decider_policy_cases(asset_text)
    by_case = {str(case["case"]): case for case in cases}

    expected = {
        "operator_required_viable_clear": {
            "approval_policy_hint": "operator_required",
            "candidate_conflict_status": "clear",
            "selected_evidence_state": "complete",
            "expected_marker": "OPERATOR_REQUIRED",
            "selected_action_id": "vendor_selection.award_decider.operator_required",
            "artifact_schema_id": "AwardDecision",
            "operator_wait_aftermath": "selected_wait",
        },
        "none_viable_clear": {
            "approval_policy_hint": "none",
            "candidate_conflict_status": "clear",
            "selected_evidence_state": "complete",
            "expected_marker": "AWARD_READY",
            "selected_action_id": "vendor_selection.award_decider.award_ready",
            "artifact_schema_id": "AwardDecision",
            "operator_wait_aftermath": "no_wait",
        },
        "complete_no_viable_evidence": {
            "selected_evidence_state": "complete_no_viable",
            "expected_marker": "NO_VIABLE_VENDOR",
            "selected_action_id": "vendor_selection.award_decider.no_viable_vendor",
            "artifact_schema_id": "DecisionPack",
            "operator_wait_aftermath": "no_wait",
        },
        "missing_or_contradictory_evidence": {
            "selected_evidence_state": "missing_or_contradictory",
            "expected_marker": "BLOCKED",
            "selected_action_id": "vendor_selection.award_decider.blocked",
            "artifact_schema_id": "DecisionPack",
            "operator_wait_aftermath": "no_wait",
        },
        "blocked_candidate_rubric_recommended": {
            "candidate_id": "vendor_beta",
            "candidate_conflict_status": "blocked",
            "rubric_recommends_candidate": True,
            "candidate_viable": False,
        },
    }
    assert set(by_case) == set(expected)
    for case_id, expected_fields in expected.items():
        case = by_case[case_id]
        for field, value in expected_fields.items():
            assert case[field] == value
        marker = str(case["expected_marker"])
        schema_id = marker_schema_by_stage["award_decider"][marker]
        assert case["artifact_schema_id"] == schema_id
        assert case["runtime_policy_boundary"] == (
            "stage_agent_selects_marker_from_selected_evidence; "
            "runtime_applies_selected_marker_action_schema_wait"
        )


def test_vendor_selection_selected_catalog_is_pinned_stage_authority(
    tmp_path: Path,
) -> None:
    manifest = _load_manifest()
    catalog_text = _asset_texts_by_id(manifest)[
        "vendor_selection.skills.catalog_sourcer_core"
    ]
    for vendor_id, label, capabilities, budget_band, conflict_status in CATALOG_RECORDS:
        assert vendor_id in catalog_text
        assert label in catalog_text
        assert capabilities in catalog_text
        assert budget_band in catalog_text
        assert conflict_status in catalog_text
    assert "unselected_catalog" not in catalog_text

    mutated_root = _copy_package_with_mutated_unselected_catalog(tmp_path)
    base_text = (
        PACKAGE_ROOT
        / str(
            _assets_by_id(manifest)["vendor_selection.skills.catalog_sourcer_core"][
                "package_path"
            ],
        )
    ).read_text()
    mutated_manifest = _load_manifest(mutated_root)
    mutated_text = (
        mutated_root
        / str(
            _assets_by_id(mutated_manifest)[
                "vendor_selection.skills.catalog_sourcer_core"
            ]["package_path"],
        )
    ).read_text()

    assert base_text == mutated_text
    assert _expected_vendor_asset_pins(manifest) == _expected_vendor_asset_pins(
        mutated_manifest,
        mutated_root,
    )


def test_vendor_selection_operator_wait_is_not_model_approval() -> None:
    manifest = _load_manifest()
    asset_texts = _asset_texts_by_id(manifest)
    award_text = asset_texts["vendor_selection.entrypoints.award_decider"]
    award_text += "\n" + asset_texts["vendor_selection.skills.award_decider_core"]
    decision_text = asset_texts["vendor_selection.entrypoints.decision_packager"]
    decision_text += (
        "\n" + asset_texts["vendor_selection.skills.decision_packager_core"]
    )

    assert "vendor_selection.award_operator_wait" in award_text
    assert "vendor_selection.award_decider.operator_required" in award_text
    assert "OPERATOR_REQUIRED" in award_text
    assert "decision_kind` set to `operator_required`" in award_text
    assert "operator_gate_required` set to true" in award_text
    assert "runtime-provided" in decision_text
    assert "read-only input" in decision_text

    for text in (award_text, decision_text):
        lowered = text.lower()
        for forbidden in (
            "may approve",
            "may reject",
            "resolve the gate",
            "may settle local operator review",
            "can settle local operator review",
            "may fabricate `operatordecision`",
            "can fabricate `operatordecision`",
        ):
            assert forbidden not in lowered


def test_vendor_selection_schema_and_side_effect_canaries() -> None:
    manifest = _load_manifest()
    asset_text = "\n".join(_asset_texts_by_id(manifest).values())

    assert "policy_notes" not in asset_text
    asset_texts = _asset_texts_by_id(manifest)
    requirement_freezer_text = (
        asset_texts["vendor_selection.entrypoints.requirement_freezer"]
        + "\n"
        + asset_texts["vendor_selection.skills.requirement_freezer_core"]
    )
    assert "deterministic_source_refs" not in requirement_freezer_text
    for forbidden_request in (
        "may browse",
        "browse marketplaces",
        "contact vendors",
        "call providers",
        "use credentials",
        "make purchases",
        "make payments",
        "remote registry",
        "MCP",
    ):
        assert forbidden_request.lower() not in asset_text.lower()
