from __future__ import annotations

import builtins
import importlib
import importlib.metadata
import importlib.resources
import json
import os
import re
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from millrace.compiler.workflow_package_sources import (
    read_archive_workflow_package_source,
    read_installed_workflow_package_source,
    read_path_workflow_package_source,
)
from millrace.contracts.schema import validate_schema
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.operator.packages import (
    PackageMutationCommand,
    PackageWorkflowSelectionCommand,
    execute_package_mutation_command,
    execute_package_workflow_selection_command,
)
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore
from millrace.substrate.package_archives import export_workflow_package_directory

GENERIC_ARTIFACT_ENVELOPE_FIELDS = frozenset(
    {
        "artifact_id",
        "produced_by_stage",
        "source_work_item_id",
        "source_run_id",
        "terminal_marker",
        "fields",
        "evidence",
        "assumptions",
        "next_stage_context",
    },
)

_RUNTIME_AUTHORITY_PATTERNS = (
    re.compile(
        r"\breturn\s+`?[A-Z_]+`?\s+to\s+"
        r"(?:route|move|close|retry|quarantine|select|authorize|approve|"
        r"enable|grant|mutate|update)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this prompt|this skill|the prompt|the skill|filename|marker)\s+"
        r"(?:routes|moves|closes|retries|selects|authorizes|enables|grants|"
        r"approves|quarantines|mutates|updates)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this prompt|this skill|the prompt|the skill|planning assets?|"
        r"asset text|marker)\s+"
        r"(?:creates?|becomes?|become|acts? as)\s+"
        r"(?:hidden\s+)?(?:queue aliases?|default global inbox(?: router)?|"
        r"(?:default\s+)?task-kind router)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:will|shall)\s+"
        r"(?:route|move queues?|close|retry|quarantine|select packages?|"
        r"authorize effects?|approve effects?|enable capabilities?|"
        r"grant capabilities?|mutate durable state|update durable state)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this package|this manifest|this prompt|this skill|"
        r"the package|the manifest|the prompt|the skill|package assets?|"
        r"asset text)\s+"
        r"(?:provides?|ships?|contains?|bundles?|grants?|supplies?)\s+"
        r"(?:provider\s+)?credentials?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this package|this manifest|this prompt|this skill|"
        r"the package|the manifest|the prompt|the skill|package assets?|"
        r"asset text)\s+"
        r"(?:executes?|runs?|invokes?|implements?|ships?|contains?|bundles?)\s+"
        r"(?:provider execution|providers?|plugin execution|plugins?|"
        r"MCP(?:\s+server|\s+tool|\s+execution)?|native runners?|"
        r"tool execution)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this package|this manifest|this prompt|this skill|"
        r"the package|the manifest|the prompt|the skill|package assets?|"
        r"asset text|marker)\s+"
        r"(?:performs?|applies?|approves?|reconciles?|persists?|grants?|"
        r"enables?)\s+"
        r"(?:effects?|effect approval|effect reconciliation|"
        r"provider execution|capabilities?|capability grants?|"
        r"runtime state|durable state|persistence authority)\b",
        re.IGNORECASE,
    ),
)
_ARTIFACT_KIND_LINE_PATTERN = re.compile(
    r"\bartifact[_ -]?kind\b",
    re.IGNORECASE,
)
_DOTTED_IDENTIFIER_PATTERN = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+\b",
)


def load_manifest_source(package_root: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((package_root / "manifest.json").read_text()),
    )


def asset_digest_for_package_path(package_root: Path, package_path: str) -> str:
    return asset_digest_for_bytes((package_root / package_path).read_bytes())


def assert_manifest_and_asset_digests(package_root: Path) -> dict[str, Any]:
    manifest = load_manifest_source(package_root)
    assets = _assets(manifest)
    asset_digests = {
        cast(str, asset["asset_id"]): cast(str, asset["content_digest"])
        for asset in assets
    }

    assert manifest["manifest_digest"] == manifest_digest_for_manifest(manifest)
    for asset in assets:
        package_path = cast(str, asset["package_path"])
        asset_bytes = (package_root / package_path).read_bytes()
        assert asset["content_digest"] == asset_digest_for_bytes(asset_bytes)
        assert asset["byte_length"] == len(asset_bytes)

    for workflow in _workflows(manifest):
        required_assets = cast(
            list[dict[str, object]],
            workflow["required_assets"],
        )
        for required_asset in required_assets:
            asset_id = cast(str, required_asset["asset_id"])
            assert required_asset["content_digest"] == asset_digests[asset_id]

    return manifest


def assert_path_archive_source_parity(package_root: Path) -> tuple[object, object]:
    manifest = assert_manifest_and_asset_digests(package_root)
    first_archive = export_workflow_package_directory(package_root)
    second_archive = export_workflow_package_directory(package_root)
    assert first_archive == second_archive

    path_source = read_path_workflow_package_source(package_root)
    archive_source = read_archive_workflow_package_source(
        first_archive,
        source_uri=f"memory://{package_root.name}.mrpkg.tar",
    )

    assert path_source.diagnostics == ()
    assert archive_source.diagnostics == ()
    assert path_source.manifest is not None
    assert archive_source.manifest is not None
    assert path_source.member_paths == archive_source.member_paths
    assert path_source.asset_bytes_by_path == archive_source.asset_bytes_by_path
    assert path_source.manifest.manifest_digest == manifest["manifest_digest"]
    assert archive_source.manifest.manifest_digest == manifest["manifest_digest"]
    return path_source, archive_source


def assert_source_matches_package_root(source: object, package_root: Path) -> None:
    manifest = assert_manifest_and_asset_digests(package_root)
    expected_assets = {
        cast(str, asset["package_path"]): (
            package_root / cast(str, asset["package_path"])
        ).read_bytes()
        for asset in _assets(manifest)
    }

    assert source.diagnostics == ()
    assert source.manifest is not None
    assert source.asset_bytes_by_path == expected_assets
    assert source.member_paths == tuple(sorted(("manifest.json", *expected_assets)))
    assert source.manifest.manifest_digest == manifest["manifest_digest"]


def select_package_from_path(
    tmp_path: Path,
    package_root: Path,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
) -> object:
    store, cas_store = _store(tmp_path)
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-import-path",
            operation_id="package.import_path",
            actor_id="operator:test",
            package_root=package_root,
        ),
    )
    assert imported.outcome == "succeeded"
    _enable_package(store, cas_store, package_id, package_version)
    return _select_package(
        store,
        cas_store,
        package_id=package_id,
        package_version=package_version,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
    )


def select_package_from_path_and_archive(
    tmp_path: Path,
    package_root: Path,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
) -> tuple[object, object]:
    path_result = select_package_from_path(
        tmp_path / "path",
        package_root,
        package_id=package_id,
        package_version=package_version,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
    )

    archive_store, archive_cas = _store(tmp_path / "archive")
    imported = execute_package_mutation_command(
        archive_store,
        archive_cas,
        PackageMutationCommand(
            command_id="cmd-import-archive",
            operation_id="package.import_archive",
            actor_id="operator:test",
            archive_bytes=export_workflow_package_directory(package_root),
            source_uri=f"memory://{package_root.name}.mrpkg.tar",
        ),
    )
    assert imported.outcome == "succeeded"
    _enable_package(archive_store, archive_cas, package_id, package_version)
    archive_result = _select_package(
        archive_store,
        archive_cas,
        package_id=package_id,
        package_version=package_version,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
    )
    return path_result, archive_result


def assert_selected_package_pin(
    plan: object,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
    selected_asset_pins: tuple[tuple[str, str], ...],
) -> None:
    assert plan is not None
    pin = plan.workflow_package_pin
    assert pin is not None
    assert pin.package_id == package_id
    assert pin.package_version == package_version
    assert pin.package_format_version == "1"
    assert pin.workflow_id == workflow_id
    assert pin.workflow_version == workflow_version
    assert pin.entrypoint == "default"
    assert tuple(
        (asset_pin.asset_id, asset_pin.content_digest)
        for asset_pin in pin.selected_asset_pins
    ) == selected_asset_pins
    assert pin.selected_dependency_pins == ()


def build_project_wheel(project_root: Path, dist_root: Path) -> Path:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(dist_root),
            "--clear",
        ],
        cwd=project_root,
        check=True,
        env=env,
    )
    wheels = sorted(dist_root.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def assert_wheel_contains_byte_only_package_data(
    wheel_path: Path,
    *,
    resource_root: str,
    import_package: str,
    expected_member_paths: tuple[str, ...],
) -> None:
    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())

    for member_path in expected_member_paths:
        assert f"{resource_root}/{member_path}" in names
    assert f"{import_package}/py.typed" in names
    assert not any(name.startswith("millrace/") for name in names)
    assert not any(name.endswith("entry_points.txt") for name in names)
    assert not any(
        name.startswith(f"{resource_root}/") and name.endswith((".py", ".pyc"))
        for name in names
    )


def assert_installed_discovery_is_byte_only(
    monkeypatch,
    *,
    wheel_path: Path,
    target: Path,
    distribution_name: str,
    import_package: str,
    installed_resource_root: str,
    expected_package_root: Path,
) -> object:
    _install_wheel(wheel_path, target)
    monkeypatch.syspath_prepend(str(target))
    sys.modules.pop(import_package, None)

    original_import = builtins.__import__
    original_import_module = importlib.import_module

    def forbidden_import(name: str, *args: object, **kwargs: object) -> object:
        if name == import_package or name.startswith(f"{import_package}."):
            raise AssertionError("installed discovery must not import package code")
        return original_import(name, *args, **kwargs)

    def forbidden_import_module(name: str, package: str | None = None) -> object:
        if name == import_package or name.startswith(f"{import_package}."):
            raise AssertionError("installed discovery must not import package code")
        return original_import_module(name, package)

    def forbidden_entry_points(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("installed discovery must not load entry points")

    def forbidden_resource_files(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("installed discovery must not import package resources")

    monkeypatch.setattr(builtins, "__import__", forbidden_import)
    monkeypatch.setattr(importlib, "import_module", forbidden_import_module)
    monkeypatch.setattr(importlib.metadata, "entry_points", forbidden_entry_points)
    monkeypatch.setattr(importlib.resources, "files", forbidden_resource_files)

    source = read_installed_workflow_package_source(
        distribution_name,
        installed_resource_root=installed_resource_root,
    )

    assert_source_matches_package_root(source, expected_package_root)
    assert import_package not in sys.modules
    return source


def assert_no_runtime_authority_claims(asset_texts: Mapping[str, str]) -> None:
    violations: list[str] = []
    for asset_name, text in asset_texts.items():
        for pattern in _RUNTIME_AUTHORITY_PATTERNS:
            match = pattern.search(text)
            if match is not None:
                violations.append(f"{asset_name}: {match.group(0)}")
                break

    assert violations == [], violations


def selected_artifact_schema_ids_for_workflow(
    manifest: dict[str, Any],
    workflow_id: str,
) -> frozenset[str]:
    for workflow in _workflows(manifest):
        if workflow["workflow_id"] == workflow_id:
            return _selected_artifact_schema_ids_for_workflow(workflow)
    raise AssertionError(f"missing workflow {workflow_id}")


def selected_artifact_schema_ids_by_asset_id(
    manifest: dict[str, Any],
) -> dict[str, frozenset[str]]:
    schema_ids_by_asset_id: dict[str, set[str]] = {}
    for workflow in _workflows(manifest):
        workflow_schema_ids = _selected_artifact_schema_ids_for_workflow(workflow)
        for required_asset in cast(
            list[dict[str, object]],
            workflow["required_assets"],
        ):
            asset_id = str(required_asset["asset_id"])
            schema_ids_by_asset_id.setdefault(asset_id, set()).update(
                workflow_schema_ids,
            )
    return {
        asset_id: frozenset(schema_ids)
        for asset_id, schema_ids in schema_ids_by_asset_id.items()
    }


def selected_artifact_schema_ids(manifest: dict[str, Any]) -> frozenset[str]:
    schema_ids: set[str] = set()
    for workflow in _workflows(manifest):
        schema_ids.update(_selected_artifact_schema_ids_for_workflow(workflow))
    return frozenset(schema_ids)


def assert_no_unscoped_selected_artifact_kind_mentions(
    asset_texts_by_asset_id: Mapping[str, str],
    *,
    declared_artifact_schema_ids_by_asset_id: Mapping[str, frozenset[str]],
) -> None:
    violations: list[str] = []
    for asset_id, text in asset_texts_by_asset_id.items():
        declared_artifact_schema_ids = declared_artifact_schema_ids_by_asset_id[
            asset_id
        ]
        violations.extend(
            _undeclared_artifact_kind_mentions(
                text,
                asset_name=asset_id,
                declared_artifact_schema_ids=declared_artifact_schema_ids,
            )
        )

    assert violations == [], violations


def assert_no_undeclared_selected_artifact_kind_mentions(
    asset_texts: Mapping[str, str],
    *,
    declared_artifact_schema_ids: frozenset[str],
) -> None:
    violations: list[str] = []
    for asset_name, text in asset_texts.items():
        violations.extend(
            _undeclared_artifact_kind_mentions(
                text,
                asset_name=asset_name,
                declared_artifact_schema_ids=declared_artifact_schema_ids,
            )
        )

    assert violations == [], violations


def assert_schema_value(value: object, schema: dict[str, object]) -> None:
    result = validate_schema(schema, value)
    assert result.accepted, result.issues


def assert_marker_artifact_example_matches_selected_schema(
    example: Mapping[str, object],
    *,
    stage_id: str,
    marker_schema_by_stage: Mapping[str, Mapping[str, str]],
    schemas_by_id: Mapping[str, dict[str, object]],
) -> None:
    assert {"terminal_marker", "artifact"} <= set(example) <= {
        "terminal_marker",
        "artifact",
        "observation_payload",
    }
    marker = str(example["terminal_marker"])
    schema_id = marker_schema_by_stage[stage_id][marker]
    artifact = example["artifact"]
    assert isinstance(artifact, dict)
    schema = schemas_by_id[schema_id]
    assert_schema_value(artifact, schema)
    if "observation_payload" in example:
        observation_payload = example["observation_payload"]
        assert isinstance(observation_payload, dict)
        assert_schema_value(observation_payload, schema)


def assert_not_generic_artifact_envelope_body(artifact: Mapping[str, object]) -> None:
    assert GENERIC_ARTIFACT_ENVELOPE_FIELDS.isdisjoint(artifact), artifact


def markdown_json_examples(
    text: str,
    *,
    section_heading: str,
) -> tuple[dict[str, object], ...]:
    match = re.search(
        rf"{re.escape(section_heading)}\n(?:.*?\n)?```json\n(?P<json>.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None
    parsed = json.loads(match.group("json"))
    if isinstance(parsed, list):
        return tuple(cast(list[dict[str, object]], parsed))
    assert isinstance(parsed, dict)
    return (cast(dict[str, object], parsed),)


def _selected_artifact_schema_ids_for_workflow(
    workflow: dict[str, object],
) -> frozenset[str]:
    selected_authority = cast(
        dict[str, object],
        workflow["selected_authority"],
    )
    artifact_schemas = cast(
        list[dict[str, object]],
        selected_authority["artifact_schemas"],
    )
    return frozenset(str(schema["id"]) for schema in artifact_schemas)


def _undeclared_artifact_kind_mentions(
    text: str,
    *,
    asset_name: str,
    declared_artifact_schema_ids: frozenset[str],
) -> list[str]:
    violations: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _ARTIFACT_KIND_LINE_PATTERN.search(line) is None:
            continue
        for reference in _DOTTED_IDENTIFIER_PATTERN.findall(line):
            if reference not in declared_artifact_schema_ids:
                violations.append(f"{asset_name}:{line_number}: {reference}")
    return violations


def _assets(manifest: dict[str, Any]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], manifest["assets"])


def _workflows(manifest: dict[str, Any]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], manifest["workflows"])


def _store(tmp_path: Path) -> tuple[SQLiteRuntimeStore, ContentAddressedByteStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return (
        SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3"),
        ContentAddressedByteStore(tmp_path / "cas"),
    )


def _enable_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    package_id: str,
    package_version: str,
) -> None:
    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="cmd-enable",
            operation_id="package.enable",
            actor_id="operator:test",
            package_id=package_id,
            package_version=package_version,
        ),
    )
    assert enabled.outcome == "succeeded"


def _select_package(
    store: SQLiteRuntimeStore,
    cas_store: ContentAddressedByteStore,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
) -> object:
    result = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id="cmd-select",
            actor_id="operator:test",
            package_id=package_id,
            package_version=package_version,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
        ),
    )
    assert result.outcome == "succeeded"
    assert result.plan is not None
    return result


def _install_wheel(wheel_path: Path, target: Path) -> None:
    target.mkdir(parents=True)
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(target)
