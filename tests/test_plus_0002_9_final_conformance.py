from __future__ import annotations

# ruff: noqa: E402
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from support.internal_conformance_gate import require_internal_conformance

require_internal_conformance()

from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.runner_bindings import RUNNER_ADAPTER_KIND_DEFAULTED
from millrace.compiler.workflow_package_sources import (
    read_archive_workflow_package_source,
    read_path_workflow_package_source,
)
from millrace.contracts.compiled_plan import canonical_authority_bytes
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
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
PACKAGE_VERSION = "0.0.0"
DIST_NAME = "millrace-plus"
IMPORT_PACKAGE = "millrace_plus"
RESOURCE_ROOT = "millrace_workflow_package"
REVIEW_PATH = PROJECT_ROOT / "docs" / "PLUS-0002.9-implementation-review.md"

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
        if binding.get("adapter_kind") != "codex"
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
    assert {runner.adapter_kind for runner in plan.runner_bindings} == {"codex"}
    authority_text = canonical_authority_bytes(plan).decode("utf-8")
    assert '"adapter_kind":"fake_local"' not in authority_text
    assert '"adapter_kind":"codex"' in authority_text


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
    package_source_kind = _package_source_kind(import_operation_id)

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
        _assert_runner_defaulting_warnings(
            verified,
            manifest=manifest,
            expectation=expectation,
            package_source_kind=package_source_kind,
        )
        _assert_runner_defaulting_warnings(
            selected,
            manifest=manifest,
            expectation=expectation,
            package_source_kind=package_source_kind,
        )
        warning_count = len(
            _invalid_selected_runner_bindings(manifest, expectation.workflow_id),
        )
        diagnostics_summary = (
            f"diagnostics:{warning_count} errors:0 warnings:{warning_count}"
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


def test_final_v021_asset_parity_review_closes_all_legacy_pairs() -> None:
    legacy_entrypoints = _source_file_paths("entrypoints/*/*.md")
    legacy_skills = _source_file_paths("skills/stage/*/*/SKILL.md")
    expectations = _legacy_pair_expectations()

    assert len(legacy_entrypoints) == 22
    assert len(legacy_skills) == 22
    assert {row.entrypoint_path for row in expectations} == legacy_entrypoints
    assert {row.skill_path for row in expectations} == legacy_skills
    assert REVIEW_PATH.is_file()

    review = REVIEW_PATH.read_text()
    matrix_rows = [
        line
        for line in review.splitlines()
        if "dev/source/millrace/src/millrace_ai/assets/entrypoints/" in line
    ]

    assert len(matrix_rows) == 22
    for expectation in expectations:
        row = next(
            line for line in matrix_rows if f"`{expectation.entrypoint_path}`" in line
        )
        assert expectation.stage_id in row
        assert f"`{expectation.skill_path}`" in row
        assert expectation.owner_packet in row
        assert expectation.selector in row
        assert expectation.disposition in row
        assert "boundary-clean" in row
        assert "PLUS-0002.9" in row
        if expectation.asset_ids is None:
            assert "not packaged" in row
            assert "exception" in row
            assert "no selected package asset text to lint" in row
        else:
            for asset_id in expectation.asset_ids:
                assert f"`{asset_id}`" in row


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
