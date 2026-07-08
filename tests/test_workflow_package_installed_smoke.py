from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import cast

from millrace.compiler.workflow_package_sources import (
    read_installed_workflow_package_source,
)
from millrace.operator.packages import (
    PackageMutationCommand,
    PackageReadExportCommand,
    PackageWorkflowSelectionCommand,
    PackageWorkflowVerifyCommand,
    execute_package_mutation_command,
    execute_package_read_export_command,
    execute_package_verify_command,
    execute_package_workflow_selection_command,
)
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.0.0"
DIST_NAME = "millrace-plus"
IMPORT_PACKAGE = "millrace_plus"
RESOURCE_ROOT = "millrace_workflow_package"
WORKFLOW_SELECTORS = (
    ("simple_loop", "0.1"),
    ("execution.lad", "0.1"),
    ("execution.lad_integrator", "0.1"),
    ("planning.lad", "0.1"),
    ("lad.full", "0.1"),
    ("vendor_selection", "0.1"),
)


def _member_paths() -> tuple[str, ...]:
    manifest = conformance.load_manifest_source(PACKAGE_ROOT)
    return tuple(
        sorted(
            (
                "manifest.json",
                *(
                    str(asset["package_path"])
                    for asset in manifest["assets"]
                ),
            )
        )
    )


def _workflow_record(
    manifest: dict[str, object],
    workflow_id: str,
) -> dict[str, object]:
    for workflow in cast(list[dict[str, object]], manifest["workflows"]):
        workflow_record = dict(workflow)
        if workflow_record["workflow_id"] == workflow_id:
            return workflow_record
    raise AssertionError(f"missing workflow {workflow_id}")


def _asset_records_by_id(
    manifest: dict[str, object],
) -> dict[str, dict[str, object]]:
    return {
        str(asset["asset_id"]): asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
    }


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _build_wheel(tmp_path: Path) -> Path:
    return conformance.build_project_wheel(PROJECT_ROOT, tmp_path / "dist")


def _install_wheel(wheel_path: Path, target: Path) -> None:
    target.mkdir(parents=True)
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(target)


def test_built_wheel_contains_official_package_data_and_no_runtime_package(
    tmp_path: Path,
) -> None:
    wheel_path = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = wheel.read(metadata_name).decode("utf-8")

    assert "Name: millrace-plus\n" in metadata
    assert "Version: 0.0.0\n" in metadata
    for member_path in _member_paths():
        assert f"{RESOURCE_ROOT}/{member_path}" in names
    assert f"{IMPORT_PACKAGE}/py.typed" in names
    assert not any(name.startswith("millrace/") for name in names)
    assert not any(name.endswith("entry_points.txt") for name in names)


def test_installed_discovery_reads_bytes_without_importing_package_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel_path = _build_wheel(tmp_path)
    source = conformance.assert_installed_discovery_is_byte_only(
        monkeypatch,
        wheel_path=wheel_path,
        target=tmp_path / "site",
        distribution_name=DIST_NAME,
        import_package=IMPORT_PACKAGE,
        installed_resource_root=RESOURCE_ROOT,
        expected_package_root=PACKAGE_ROOT,
    )

    assert source.source_kind == "installed_python_package"
    assert source.member_paths == _member_paths()


def _expected_selected_asset_pins(
    manifest: dict[str, object],
    workflow_id: str,
) -> tuple[tuple[object, str], ...]:
    workflow = _workflow_record(manifest, workflow_id)
    assets_by_id = _asset_records_by_id(manifest)
    return tuple(
        (
            required_asset["asset_id"],
            conformance.asset_digest_for_package_path(
                PACKAGE_ROOT,
                str(assets_by_id[str(required_asset["asset_id"])]["package_path"]),
            ),
        )
        for required_asset in sorted(
            workflow["required_assets"],
            key=lambda item: str(item["asset_id"]),
        )
    )


def test_installed_package_imports_selects_and_verifies_all_workflows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel_path = _build_wheel(tmp_path)
    target = tmp_path / "site"
    _install_wheel(wheel_path, target)
    monkeypatch.syspath_prepend(str(target))
    sys.modules.pop(IMPORT_PACKAGE, None)
    store, cas_store = _store(tmp_path)

    source = read_installed_workflow_package_source(
        DIST_NAME,
        installed_resource_root=RESOURCE_ROOT,
    )
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import-installed",
            operation_id="package.import_installed",
            actor_id="operator:test",
            installed_distribution_name=DIST_NAME,
            installed_resource_root=RESOURCE_ROOT,
        ),
    )
    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-enable-installed",
            operation_id="package.enable",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
        ),
    )
    listed = execute_package_read_export_command(
        store,
        cas_store,
        PackageReadExportCommand(
            command_id="cmd-list-installed",
            operation_id="package.list",
            actor_id="operator:test",
        ),
    )

    assert source.diagnostics == ()
    assert imported.outcome == "succeeded"
    assert imported.package_record is not None
    assert imported.package_record.source_kind == "installed_python_package"
    assert enabled.outcome == "succeeded"
    manifest = conformance.load_manifest_source(PACKAGE_ROOT)
    for workflow_id, workflow_version in WORKFLOW_SELECTORS:
        safe_id = workflow_id.replace(".", "-")
        verified = execute_package_verify_command(
            store,
            cas_store,
            PackageWorkflowVerifyCommand(
                command_id=f"cmd-verify-installed-{safe_id}",
                actor_id="operator:test",
                package_id=PACKAGE_ID,
                package_version=PACKAGE_VERSION,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
            )
        )
        selected = execute_package_workflow_selection_command(
            store,
            cas_store,
            PackageWorkflowSelectionCommand(
                command_id=f"cmd-select-installed-{safe_id}",
                actor_id="operator:test",
                package_id=PACKAGE_ID,
                package_version=PACKAGE_VERSION,
                workflow_id=workflow_id,
                workflow_version=workflow_version,
            ),
        )

        assert verified.outcome == "succeeded"
        assert verified.plan_ready
        assert selected.outcome == "succeeded"
        assert selected.plan is not None
        conformance.assert_selected_package_pin(
            selected.plan,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            selected_asset_pins=_expected_selected_asset_pins(
                manifest,
                workflow_id,
            ),
        )
    assert listed.outcome == "succeeded"
    assert [package.package_id for package in listed.packages] == [PACKAGE_ID]
    assert IMPORT_PACKAGE not in sys.modules
