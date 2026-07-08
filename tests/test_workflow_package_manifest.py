from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from millrace.compiler.workflow_package_sources import (
    read_archive_workflow_package_source,
    read_path_workflow_package_source,
)
from millrace.operator.packages import (
    PackageReadExportCommand,
    execute_package_read_export_command,
)
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore
from millrace.substrate.package_archives import (
    export_workflow_package_directory,
    read_workflow_package_archive_bytes,
)

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.0.0"
WORKFLOW_ID = "simple_loop"
WORKFLOW_VERSION = "0.1"


def _manifest_source() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((PACKAGE_ROOT / "manifest.json").read_text()),
    )


def _workflow_record(manifest: dict[str, object]) -> dict[str, object]:
    workflows = cast(list[object], manifest["workflows"])
    for workflow in workflows:
        workflow_record = cast(dict[str, object], workflow)
        if workflow_record["workflow_id"] == WORKFLOW_ID:
            return workflow_record
    raise AssertionError(f"missing workflow {WORKFLOW_ID}")


def _assets(manifest: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], manifest["assets"])


def _member_paths(manifest: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                "manifest.json",
                *(str(asset["package_path"]) for asset in _assets(manifest)),
            )
        )
    )


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _inspect_imported_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    command_prefix: str,
):
    listed = execute_package_read_export_command(
        store,
        cas_store,
        PackageReadExportCommand(
            command_id=f"{command_prefix}-list",
            operation_id="package.list",
            actor_id="operator:test",
        ),
    )
    inspected = execute_package_read_export_command(
        store,
        cas_store,
        PackageReadExportCommand(
            command_id=f"{command_prefix}-inspect",
            operation_id="package.inspect",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
        ),
    )

    manifest = _manifest_source()
    expected_paths = tuple(
        str(asset["package_path"])
        for asset in sorted(_assets(manifest), key=lambda item: str(item["asset_id"]))
    )

    assert listed.outcome == "succeeded"
    assert [package.package_id for package in listed.packages] == [PACKAGE_ID]
    assert inspected.outcome == "succeeded"
    assert inspected.package is not None
    assert inspected.package.package_id == PACKAGE_ID
    assert inspected.package.package_version == PACKAGE_VERSION
    assert tuple(asset.package_path for asset in inspected.package.assets) == (
        expected_paths
    )
    return inspected.package


def test_official_manifest_and_declared_assets_match_shipped_bytes() -> None:
    assert (PACKAGE_ROOT / "manifest.json").is_file()

    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    package = cast(dict[str, object], manifest["package"])
    workflow = _workflow_record(manifest)
    assets = _assets(manifest)

    assert package["package_id"] == PACKAGE_ID
    assert package["package_version"] == PACKAGE_VERSION
    assert workflow["workflow_id"] == WORKFLOW_ID
    assert workflow["workflow_version"] == WORKFLOW_VERSION
    assert workflow["visibility"] == "public"
    assert workflow["entrypoints"] == ["default"]
    assert "assets" not in cast(dict[str, object], workflow["selected_authority"])
    assert len(assets) == 36
    assert {asset["asset_kind"] for asset in assets} == {
        "entrypoint_prompt",
        "stage_skill",
    }


def test_public_path_and_archive_readers_accept_official_layout() -> None:
    manifest = _manifest_source()
    path_source = read_path_workflow_package_source(PACKAGE_ROOT)
    archive_bytes = export_workflow_package_directory(PACKAGE_ROOT)
    archive_members = read_workflow_package_archive_bytes(archive_bytes)
    archive_source = read_archive_workflow_package_source(archive_bytes)

    assert path_source.diagnostics == ()
    assert path_source.manifest is not None
    assert path_source.manifest.package.package_id == PACKAGE_ID
    assert path_source.member_paths == _member_paths(manifest)
    assert archive_members.member_paths == _member_paths(manifest)
    assert archive_source.diagnostics == ()
    assert archive_source.manifest is not None
    assert (
        archive_source.manifest.manifest_digest
        == path_source.manifest.manifest_digest
    )


def test_path_source_imports_and_projects_through_public_operator_surface(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    source = read_path_workflow_package_source(PACKAGE_ROOT)

    record = store.import_workflow_package_source(
        cas_store,
        source,
        actor_id="operator:test",
    )
    projection = _inspect_imported_package(
        store,
        cas_store,
        command_prefix="path",
    )

    assert record.package_id == PACKAGE_ID
    assert projection.manifest_digest == record.manifest_digest
    assert projection.source_kind == "path"
    assert projection.unselectable_reason == "package_status_imported"


def test_archive_source_imports_and_projects_through_public_operator_surface(
    tmp_path: Path,
) -> None:
    store, cas_store = _store(tmp_path)
    archive_source = read_archive_workflow_package_source(
        export_workflow_package_directory(PACKAGE_ROOT),
        source_uri="memory://millrace-plus-official.mrpkg.tar",
    )

    record = store.import_workflow_package_source(
        cas_store,
        archive_source,
        actor_id="operator:test",
    )
    projection = _inspect_imported_package(
        store,
        cas_store,
        command_prefix="archive",
    )

    assert record.package_id == PACKAGE_ID
    assert projection.manifest_digest == record.manifest_digest
    assert projection.source_kind == "archive"
