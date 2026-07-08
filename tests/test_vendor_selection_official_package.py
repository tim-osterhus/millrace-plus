from __future__ import annotations

# ruff: noqa: E402
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

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
REVIEW_PATH = PROJECT_ROOT / "docs" / "PLUS-0002F-implementation-review.md"
EXISTING_WORKFLOW_IDS = (
    "simple_loop",
    "execution.lad",
    "execution.lad_integrator",
    "planning.lad",
    "lad.full",
)


def _load_manifest(package_root: Path = PACKAGE_ROOT) -> dict[str, Any]:
    return conformance.load_manifest_source(package_root)


def _workflows_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        str(workflow["workflow_id"]): workflow
        for workflow in cast(list[dict[str, object]], manifest["workflows"])
    }


def _source_as_selected_authority(source: dict[str, object]) -> dict[str, object]:
    selected = cast(dict[str, object], json.loads(json.dumps(source)))
    selected.pop("assets")
    return selected


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


def test_vendor_selection_selected_authority_matches_donor_without_assets() -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    source = vendor_selection.source()

    assert selected_authority == _source_as_selected_authority(source)
    assert "assets" not in selected_authority
    assert "unselected_catalog" in selected_authority
    assert len(cast(list[object], selected_authority["unselected_catalog"])) == 3
    assert tuple(cast(list[object], source["assets"])) == ()
    assert workflow["required_assets"] == []
    assert not (PACKAGE_ROOT / "assets" / "workflows" / WORKFLOW_ID).exists()


def test_vendor_selection_path_archive_selection_compiles_asset_free_four_plane_shape(
    tmp_path: Path,
) -> None:
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
            selected_asset_pins=(),
        )
        plan = result.plan
        assert plan is not None
        assert plan.assets == ()
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
        assert len(plan.runner_bindings) == 1
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
        selected_asset_pins=(),
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
    assert mutated_import.package_record.manifest_digest != _load_manifest()[
        "manifest_digest"
    ]
    conformance.assert_selected_package_pin(
        mutated_selected.plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        selected_asset_pins=(),
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

    assert workflow["required_assets"] == []
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


def test_plus_0002f_review_doc_documents_asset_free_vendor_selection() -> None:
    review = REVIEW_PATH.read_text()

    for required in (
        "PLUS-0002F",
        "vendor_selection",
        "asset-free",
        "selected_asset_pins=()",
        "unselected_catalog",
        "requirements, sourcing, evaluation, authorization",
        "fanout=2",
        "join=1",
        "operator_wait=1",
        "effect/provider refs absent",
        "tests/test_vendor_selection_official_package.py",
    ):
        assert required in review
