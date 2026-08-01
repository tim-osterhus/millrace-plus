from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from millrace.compiler import compile_workflow
from millrace.operator import (
    PackageMutationCommand,
    PackageWorkflowSelectionCommand,
    PackageWorkflowVerifyCommand,
    execute_package_mutation_command,
    execute_package_verify_command,
    execute_package_workflow_selection_command,
)
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore

_ASSET_DIGEST_DOMAIN_BYTES = b"millrace.wpkg.asset.v1\0"
_RUNTIME_AUTHORITY_PATTERNS = (
    re.compile(
        r"\breturn\s+`?[A-Z_]+`?\s+to\s+"
        r"(?:route|move|close|retry|quarantine|select|authorize|approve|enable|"
        r"grant|mutate|update)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this prompt|this skill|the prompt|the skill|filename|marker)\s+"
        r"(?:routes|moves|closes|retries|selects|authorizes|enables|grants|"
        r"approves|quarantines|mutates|updates)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:this package|this manifest|this prompt|this skill|the package|"
        r"the manifest|the prompt|the skill|package assets?|asset text)\s+"
        r"(?:provides?|ships?|contains?|bundles?|grants?|supplies?)\s+"
        r"(?:provider\s+)?credentials?\b",
        re.IGNORECASE,
    ),
)


def load_manifest(package_root: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((package_root / "manifest.json").read_text()),
    )


def workflows_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        str(workflow["workflow_id"]): workflow
        for workflow in cast(list[dict[str, object]], manifest["workflows"])
    }


def assets_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, object]]:
    return {
        str(asset["asset_id"]): asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
    }


def asset_digest(payload: bytes) -> str:
    return "sha256:" + sha256(_ASSET_DIGEST_DOMAIN_BYTES + payload).hexdigest()


def assert_packaged_asset_closure(package_root: Path) -> dict[str, Any]:
    manifest = load_manifest(package_root)
    assets = assets_by_id(manifest)
    for asset_id, asset in assets.items():
        payload = (package_root / str(asset["package_path"])).read_bytes()
        assert asset["content_digest"] == asset_digest(payload), asset_id
        assert asset["byte_length"] == len(payload), asset_id
    for workflow in workflows_by_id(manifest).values():
        for required in cast(list[dict[str, object]], workflow["required_assets"]):
            asset_id = str(required["asset_id"])
            assert required["content_digest"] == assets[asset_id]["content_digest"]
    return manifest


def selected_asset_pins(
    manifest: dict[str, Any], workflow_id: str
) -> tuple[tuple[str, str], ...]:
    workflow = workflows_by_id(manifest)[workflow_id]
    return tuple(
        sorted(
            (
                str(required["asset_id"]),
                str(required["content_digest"]),
            )
            for required in cast(list[dict[str, object]], workflow["required_assets"])
        )
    )


def packaged_workflow_source(package_root: Path, workflow_id: str) -> dict[str, object]:
    manifest = load_manifest(package_root)
    workflow = workflows_by_id(manifest)[workflow_id]
    assets = assets_by_id(manifest)
    source = cast(
        dict[str, object],
        deepcopy(cast(dict[str, object], workflow["selected_authority"])),
    )
    source["assets"] = [
        {
            "id": asset_id,
            "kind": assets[asset_id]["asset_kind"],
            "body": (package_root / str(assets[asset_id]["package_path"])).read_text(),
        }
        for asset_id in sorted(
            str(required["asset_id"])
            for required in cast(list[dict[str, object]], workflow["required_assets"])
        )
    ]
    return source


def compile_packaged_workflow(package_root: Path, workflow_id: str) -> object:
    result = compile_workflow(packaged_workflow_source(package_root, workflow_id))
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )
    assert errors == ()
    assert result.plan is not None
    return result.plan


def select_and_verify_package(
    tmp_path: Path,
    package_root: Path,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
) -> object:
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
            package_root=package_root,
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
    assert imported.outcome == "succeeded", imported.diagnostics
    assert enabled.outcome == "succeeded", enabled.diagnostics
    assert verified.outcome == "succeeded", verified.diagnostics
    assert verified.plan_ready
    assert selected.outcome == "succeeded", selected.diagnostics
    assert selected.plan is not None
    return selected.plan


def assert_selected_package_pin(
    plan: object,
    *,
    package_id: str,
    package_version: str,
    workflow_id: str,
    workflow_version: str,
    selected_asset_pins: tuple[tuple[str, str], ...],
) -> None:
    pin = plan.workflow_package_pin
    assert pin.package_id == package_id
    assert pin.package_version == package_version
    assert pin.package_format_version == "1"
    assert pin.workflow_id == workflow_id
    assert pin.workflow_version == workflow_version
    assert pin.entrypoint == "default"
    assert (
        tuple(
            (asset_pin.asset_id, asset_pin.content_digest)
            for asset_pin in pin.selected_asset_pins
        )
        == selected_asset_pins
    )
    assert pin.selected_dependency_pins == ()


def asset_texts(
    package_root: Path, manifest: dict[str, Any], asset_ids: set[str]
) -> dict[str, str]:
    assets = assets_by_id(manifest)
    return {
        asset_id: (package_root / str(assets[asset_id]["package_path"])).read_text()
        for asset_id in sorted(asset_ids)
    }


def assert_no_runtime_authority_claims(asset_text_by_id: Mapping[str, str]) -> None:
    violations: list[str] = []
    for asset_id, text in asset_text_by_id.items():
        for pattern in _RUNTIME_AUTHORITY_PATTERNS:
            match = pattern.search(text)
            if match is not None:
                violations.append(f"{asset_id}: {match.group(0)}")
                break
    assert violations == [], violations
