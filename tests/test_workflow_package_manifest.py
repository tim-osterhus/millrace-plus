from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from millrace.compiler.workflow_package_sources import (
    read_archive_workflow_package_source,
    read_path_workflow_package_source,
)
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.scaffold"
PACKAGE_VERSION = "0.0.0"
ASSET_PATH = "assets/scaffold_prompt.md"


def _manifest_source() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((PACKAGE_ROOT / "manifest.json").read_text()),
    )


def _asset_record(manifest: dict[str, object]) -> dict[str, object]:
    assets = cast(list[object], manifest["assets"])
    assert len(assets) == 1
    return cast(dict[str, object], assets[0])


def _workflow_record(manifest: dict[str, object]) -> dict[str, object]:
    workflows = cast(list[object], manifest["workflows"])
    assert len(workflows) == 1
    return cast(dict[str, object], workflows[0])


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

    assert listed.outcome == "succeeded"
    assert [package.package_id for package in listed.packages] == [PACKAGE_ID]
    assert inspected.outcome == "succeeded"
    assert inspected.package is not None
    assert inspected.package.package_id == PACKAGE_ID
    assert inspected.package.package_version == PACKAGE_VERSION
    assert inspected.package.assets[0].asset_id == "millrace.plus.scaffold.prompt"
    return inspected.package


def test_scaffold_manifest_and_declared_asset_match_shipped_bytes() -> None:
    assert (PACKAGE_ROOT / "manifest.json").is_file()
    assert (PACKAGE_ROOT / ASSET_PATH).is_file()

    manifest = _manifest_source()
    asset = _asset_record(manifest)
    workflow = _workflow_record(manifest)
    asset_bytes = (PACKAGE_ROOT / ASSET_PATH).read_bytes()

    assert manifest["manifest_digest"] == manifest_digest_for_manifest(manifest)
    assert asset["content_digest"] == asset_digest_for_bytes(asset_bytes)
    assert asset["byte_length"] == len(asset_bytes)
    assert asset["package_path"] == ASSET_PATH
    assert asset["selected_authority_participation"] == "yes"
    assert workflow["workflow_id"] == PACKAGE_ID
    assert workflow["workflow_version"] == PACKAGE_VERSION
    assert workflow["visibility"] == "test_only"
    assert workflow["entrypoints"] == ["default"]
    assert workflow["required_assets"] == [
        {
            "asset_id": "millrace.plus.scaffold.prompt",
            "content_digest": asset["content_digest"],
        }
    ]


def test_public_path_and_archive_readers_accept_scaffold_layout() -> None:
    path_source = read_path_workflow_package_source(PACKAGE_ROOT)
    archive_bytes = export_workflow_package_directory(PACKAGE_ROOT)
    archive_members = read_workflow_package_archive_bytes(archive_bytes)
    archive_source = read_archive_workflow_package_source(archive_bytes)

    assert path_source.diagnostics == ()
    assert path_source.manifest is not None
    assert path_source.manifest.package.package_id == PACKAGE_ID
    assert path_source.member_paths == (ASSET_PATH, "manifest.json")
    assert archive_members.member_paths == (ASSET_PATH, "manifest.json")
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
        source_uri="memory://millrace-plus-scaffold.mrpkg.tar",
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
