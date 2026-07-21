from __future__ import annotations

# ruff: noqa: E402
import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from support.internal_conformance_gate import require_internal_conformance

require_internal_conformance()

from millrace.compiler.canonical import authority_fingerprint
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
from millrace.workflows import simple_loop

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
SCAFFOLD_PACKAGE_ID = "millrace.plus.scaffold"
PACKAGE_VERSION = "0.0.0"
WORKFLOW_ID = "simple_loop"
WORKFLOW_VERSION = "0.1"

_STAGE_PROMPT_ASSET_IDS = {
    "simple_loop.manager": "simple_loop.manager_prompt",
    "simple_loop.worker": "simple_loop.worker_prompt",
    "simple_loop.reviewer": "simple_loop.reviewer_prompt",
    "simple_loop.troubleshooter": "simple_loop.troubleshooter_prompt",
}
_STAGE_SKILL_ASSET_IDS = {
    "simple_loop.manager": "simple_loop.manager_core_skill",
    "simple_loop.worker": "simple_loop.worker_core_skill",
    "simple_loop.reviewer": "simple_loop.reviewer_core_skill",
    "simple_loop.troubleshooter": "simple_loop.troubleshooter_core_skill",
}
_ENTRYPOINT_HEADINGS = (
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
_CORE_SKILL_HEADINGS = (
    "## Artifact Schema",
    "## Handoff Format",
    "## Valid Example",
    "## Invalid Examples",
    "## Validation Checklist",
    "## Completion Criteria",
)


def _load_manifest(package_root: Path = PACKAGE_ROOT) -> dict[str, Any]:
    return conformance.load_manifest_source(package_root)


def _workflow_record(manifest: dict[str, Any]) -> dict[str, object]:
    workflows = cast(list[dict[str, object]], manifest["workflows"])
    for workflow in workflows:
        if workflow["workflow_id"] == WORKFLOW_ID:
            return workflow
    raise AssertionError(f"missing workflow {WORKFLOW_ID}")


def _asset_records(manifest: dict[str, Any]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], manifest["assets"])


def _simple_loop_asset_records(manifest: dict[str, Any]) -> list[dict[str, object]]:
    expected_asset_ids = set(_expected_asset_ids())
    return [
        asset
        for asset in _asset_records(manifest)
        if str(asset["asset_id"]) in expected_asset_ids
    ]


def _donor_prompt_assets() -> list[dict[str, object]]:
    source = simple_loop.workflow_source()
    assets = cast(list[dict[str, object]], source["assets"])
    assert all(asset["kind"] == "prompt" for asset in assets)
    return assets


def _donor_prompt_asset_ids() -> tuple[str, ...]:
    return tuple(str(asset["id"]) for asset in _donor_prompt_assets())


def _expected_asset_ids() -> tuple[str, ...]:
    return (
        *_donor_prompt_asset_ids(),
        *_STAGE_SKILL_ASSET_IDS.values(),
    )


def _entrypoint_package_path(asset_id: str) -> str:
    stage = asset_id.removeprefix("simple_loop.").removesuffix("_prompt")
    return f"assets/workflows/simple_loop/entrypoints/{stage}.md"


def _skill_package_path(asset_id: str) -> str:
    stage = asset_id.removeprefix("simple_loop.").removesuffix("_core_skill")
    return f"assets/workflows/simple_loop/skills/{stage}-core.md"


def _expected_asset_paths_by_id() -> dict[str, str]:
    paths = {
        asset_id: _entrypoint_package_path(asset_id)
        for asset_id in _donor_prompt_asset_ids()
    }
    paths.update(
        {
            asset_id: _skill_package_path(asset_id)
            for asset_id in _STAGE_SKILL_ASSET_IDS.values()
        }
    )
    return paths


def _expected_selected_authority() -> dict[str, object]:
    source = simple_loop.workflow_source()
    source.pop("assets")
    for stage in cast(list[dict[str, object]], source["stage_kinds"]):
        stage_id = str(stage["id"])
        if stage_id in _STAGE_SKILL_ASSET_IDS:
            stage["asset_ids"] = (
                *_sequence_as_tuple(stage["asset_ids"]),
                _STAGE_SKILL_ASSET_IDS[stage_id],
            )
    return cast(dict[str, object], json.loads(json.dumps(source)))


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


def _sequence_as_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in cast(list[object] | tuple[object, ...], value))


def _expected_selected_asset_pins(package_root: Path) -> tuple[tuple[str, str], ...]:
    paths_by_id = _expected_asset_paths_by_id()
    return tuple(
        (
            asset_id,
            conformance.asset_digest_for_package_path(
                package_root,
                paths_by_id[asset_id],
            ),
        )
        for asset_id in sorted(_expected_asset_ids())
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


def _refresh_manifest_digests(package_root: Path) -> None:
    manifest = _load_manifest(package_root)
    assets_by_id = {str(asset["asset_id"]): asset for asset in _asset_records(manifest)}
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
            required_asset["content_digest"] = assets_by_id[asset_id][
                "content_digest"
            ]

    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )


def test_shipped_package_root_is_official_simple_loop_package() -> None:
    manifest = _load_manifest()
    package = cast(dict[str, object], manifest["package"])
    workflow = _workflow_record(manifest)

    assert package["package_id"] == PACKAGE_ID
    assert package["package_id"] != SCAFFOLD_PACKAGE_ID
    assert package["package_version"] == PACKAGE_VERSION
    assert package["package_role"] == "workflow_package"
    assert package["publication_scope"] == "public"
    assert workflow["workflow_id"] == WORKFLOW_ID
    assert workflow["workflow_version"] == WORKFLOW_VERSION
    assert workflow["visibility"] == "public"
    assert workflow["entrypoints"] == ["default"]
    assert _without_runner_authority(
        cast(dict[str, object], workflow["selected_authority"])
    ) == _without_runner_authority(
        _expected_selected_authority()
    )


def test_manifest_assets_match_donor_prompts_plus_core_stage_skills() -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflow = _workflow_record(manifest)
    assets = _simple_loop_asset_records(manifest)
    asset_ids = tuple(str(asset["asset_id"]) for asset in assets)
    paths_by_id = _expected_asset_paths_by_id()

    assert asset_ids == _expected_asset_ids()
    assert "assets" not in cast(dict[str, object], workflow["selected_authority"])
    assert workflow["required_assets"] == [
        {
            "asset_id": asset["asset_id"],
            "content_digest": asset["content_digest"],
        }
        for asset in assets
    ]

    for asset in assets:
        asset_id = str(asset["asset_id"])
        assert asset["package_path"] == paths_by_id[asset_id]
        assert asset["selected_authority_participation"] == "yes"
        if asset_id in _donor_prompt_asset_ids():
            assert asset["asset_kind"] == "entrypoint_prompt"
        else:
            assert asset["asset_kind"] == "stage_skill"

    donor_bodies = {
        str(asset["id"]): str(asset["body"]) for asset in _donor_prompt_assets()
    }
    asset_texts_by_path = {
        str(asset["package_path"]): (
            PACKAGE_ROOT / str(asset["package_path"])
        ).read_text()
        for asset in assets
    }
    asset_texts_by_id = {
        str(asset["asset_id"]): (
            PACKAGE_ROOT / str(asset["package_path"])
        ).read_text()
        for asset in assets
    }
    for asset_id, donor_body in donor_bodies.items():
        prompt_text = asset_texts_by_path[paths_by_id[asset_id]]
        assert donor_body in prompt_text
        for heading in _ENTRYPOINT_HEADINGS:
            assert heading in prompt_text

    for asset_id in _STAGE_SKILL_ASSET_IDS.values():
        skill_text = asset_texts_by_path[paths_by_id[asset_id]]
        for heading in _CORE_SKILL_HEADINGS:
            assert heading in skill_text

    conformance.assert_no_runtime_authority_claims(asset_texts_by_path)
    conformance.assert_no_unscoped_selected_artifact_kind_mentions(
        asset_texts_by_id,
        declared_artifact_schema_ids_by_asset_id=(
            conformance.selected_artifact_schema_ids_by_asset_id(manifest)
        ),
    )


def test_each_simple_loop_stage_has_entrypoint_prompt_and_core_skill() -> None:
    manifest = _load_manifest()
    workflow = _workflow_record(manifest)
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    stage_records = {
        str(stage["id"]): stage
        for stage in cast(list[dict[str, object]], selected_authority["stage_kinds"])
    }
    asset_kinds = {
        str(asset["asset_id"]): str(asset["asset_kind"])
        for asset in _asset_records(manifest)
    }

    for stage_id, prompt_asset_id in _STAGE_PROMPT_ASSET_IDS.items():
        skill_asset_id = _STAGE_SKILL_ASSET_IDS[stage_id]
        stage_asset_ids = _sequence_as_tuple(stage_records[stage_id]["asset_ids"])

        assert stage_asset_ids == (prompt_asset_id, skill_asset_id)
        assert asset_kinds[prompt_asset_id] == "entrypoint_prompt"
        assert asset_kinds[skill_asset_id] == "stage_skill"


def test_path_archive_sources_select_and_verify_simple_loop(tmp_path: Path) -> None:
    path_source, archive_source = conformance.assert_path_archive_source_parity(
        PACKAGE_ROOT,
    )
    expected_asset_pins = _expected_selected_asset_pins(PACKAGE_ROOT)

    assert path_source.manifest is not None
    assert archive_source.manifest is not None
    assert path_source.manifest.package.package_id == PACKAGE_ID
    assert archive_source.manifest.package.package_id == PACKAGE_ID

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

    _, verified, selected = _import_enable_select_verify(
        tmp_path / "verify",
        PACKAGE_ROOT,
        command_label="official",
    )
    assert verified.package is not None
    assert verified.package.package_id == PACKAGE_ID
    assert authority_fingerprint(path_result.plan) == authority_fingerprint(
        archive_result.plan
    )
    assert authority_fingerprint(selected.plan) == authority_fingerprint(
        path_result.plan
    )


def test_changing_prompt_changes_package_digest_pin_and_fingerprint(
    tmp_path: Path,
) -> None:
    base_import, _, base_selected = _import_enable_select_verify(
        tmp_path / "base",
        PACKAGE_ROOT,
        command_label="base",
    )
    mutated_root = tmp_path / "mutated-package"
    shutil.copytree(PACKAGE_ROOT, mutated_root)
    worker_prompt = mutated_root / _entrypoint_package_path(
        "simple_loop.worker_prompt",
    )
    worker_prompt.write_text(worker_prompt.read_text() + "\nMutation evidence.\n")
    _refresh_manifest_digests(mutated_root)

    mutated_import, _, mutated_selected = _import_enable_select_verify(
        tmp_path / "mutated",
        mutated_root,
        command_label="mutated",
    )
    assert base_import.package_record is not None
    assert mutated_import.package_record is not None
    assert base_selected.plan is not None
    assert mutated_selected.plan is not None

    base_pins = {
        pin.asset_id: pin.content_digest
        for pin in base_selected.plan.workflow_package_pin.selected_asset_pins
    }
    mutated_pins = {
        pin.asset_id: pin.content_digest
        for pin in mutated_selected.plan.workflow_package_pin.selected_asset_pins
    }

    assert base_import.package_record.package_digest != (
        mutated_import.package_record.package_digest
    )
    assert base_pins["simple_loop.worker_prompt"] != (
        mutated_pins["simple_loop.worker_prompt"]
    )
    assert authority_fingerprint(base_selected.plan) != authority_fingerprint(
        mutated_selected.plan
    )


def test_boundary_lint_refuses_detectable_runtime_authority_claims() -> None:
    with pytest.raises(AssertionError):
        conformance.assert_no_runtime_authority_claims(
            {
                "bad-prompt.md": (
                    "Return `WORK_DONE` to route to Review and close the queue item."
                )
            }
        )


def test_millrace_ai_included_workflow_inventory_remains_base_only() -> None:
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
    with pytest.raises(KeyError) as exc_info:
        included_workflow_source("simple_loop")

    assert exc_info.value.args == ("simple_loop",)
