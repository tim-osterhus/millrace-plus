from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from millrace.compiler.workflow_package_sources import (
    read_installed_workflow_package_source,
)
from millrace.operator.packages import (
    PackageMutationCommand,
    PackageReadExportCommand,
    execute_package_mutation_command,
    execute_package_read_export_command,
)
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ID = "millrace.plus.scaffold"
PACKAGE_VERSION = "0.0.0"
DIST_NAME = "millrace-plus"
IMPORT_PACKAGE = "millrace_plus"
RESOURCE_ROOT = "millrace_workflow_package"
ASSET_PATH = "assets/scaffold_prompt.md"


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    return env


def _build_wheel(tmp_path: Path) -> Path:
    dist_dir = tmp_path / "dist"
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(dist_dir),
            "--clear",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        env=_subprocess_env(),
    )
    wheels = sorted(dist_dir.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _install_wheel(wheel_path: Path, target: Path) -> None:
    target.mkdir(parents=True)
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(target)


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def test_built_wheel_contains_scaffold_package_data_and_no_runtime_package(
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
    assert f"{RESOURCE_ROOT}/manifest.json" in names
    assert f"{RESOURCE_ROOT}/{ASSET_PATH}" in names
    assert f"{IMPORT_PACKAGE}/py.typed" in names
    assert not any(name.startswith("millrace/") for name in names)
    assert not any(name.endswith("entry_points.txt") for name in names)


def test_installed_discovery_reads_bytes_without_importing_package_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import importlib.metadata
    import importlib.resources

    wheel_path = _build_wheel(tmp_path)
    target = tmp_path / "site"
    _install_wheel(wheel_path, target)
    monkeypatch.syspath_prepend(str(target))
    sys.modules.pop(IMPORT_PACKAGE, None)

    def forbidden_entry_points(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("installed discovery must not load entry points")

    def forbidden_resource_files(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("installed discovery must not import package resources")

    monkeypatch.setattr(importlib.metadata, "entry_points", forbidden_entry_points)
    monkeypatch.setattr(importlib.resources, "files", forbidden_resource_files)

    source = read_installed_workflow_package_source(
        DIST_NAME,
        installed_resource_root=RESOURCE_ROOT,
    )

    assert source.diagnostics == ()
    assert source.manifest is not None
    assert source.source_kind == "installed_python_package"
    assert source.member_paths == (ASSET_PATH, "manifest.json")
    assert source.asset_bytes_by_path == {
        ASSET_PATH: (PROJECT_ROOT / RESOURCE_ROOT / ASSET_PATH).read_bytes()
    }
    assert IMPORT_PACKAGE not in sys.modules


def test_installed_package_imports_and_projects_through_public_operator_surface(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel_path = _build_wheel(tmp_path)
    target = tmp_path / "site"
    _install_wheel(wheel_path, target)
    monkeypatch.syspath_prepend(str(target))
    sys.modules.pop(IMPORT_PACKAGE, None)
    store, cas_store = _store(tmp_path)

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
    listed = execute_package_read_export_command(
        store,
        cas_store,
        PackageReadExportCommand(
            command_id="cmd-list-installed",
            operation_id="package.list",
            actor_id="operator:test",
        ),
    )
    inspected = execute_package_read_export_command(
        store,
        cas_store,
        PackageReadExportCommand(
            command_id="cmd-inspect-installed",
            operation_id="package.inspect",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
        ),
    )

    assert imported.outcome == "succeeded"
    assert imported.package_record is not None
    assert imported.package_record.source_kind == "installed_python_package"
    assert listed.outcome == "succeeded"
    assert [package.package_id for package in listed.packages] == [PACKAGE_ID]
    assert inspected.outcome == "succeeded"
    assert inspected.package is not None
    assert inspected.package.package_id == PACKAGE_ID
    assert inspected.package.assets[0].package_path == ASSET_PATH
    assert IMPORT_PACKAGE not in sys.modules
