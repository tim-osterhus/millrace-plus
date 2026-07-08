from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import pytest
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.compiled_plan import canonical_authority_bytes
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.workflows import lad_planning

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.0.0"
WORKFLOW_ID = "planning.lad"
REVIEW_PATH = PROJECT_ROOT / "docs" / "PLUS-0002D-implementation-review.md"

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
_PLANNING_STAGE_PAIRS = (
    (
        "recon",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/recon.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "recon-core/SKILL.md",
        "planning.entrypoints.recon",
        "planning.skills.recon_core",
    ),
    (
        "lad_planner",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "lad_planner.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "planner-core/SKILL.md",
        "planning.entrypoints.lad_planner",
        "planning.skills.planner_core",
    ),
    (
        "lad_manager",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "lad_manager.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "manager-core/SKILL.md",
        "planning.entrypoints.lad_manager",
        "planning.skills.manager_core",
    ),
    (
        "lad_mechanic",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "lad_mechanic.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "mechanic-core/SKILL.md",
        "planning.entrypoints.lad_mechanic",
        "planning.skills.mechanic_core",
    ),
    (
        "lad_auditor",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "lad_auditor.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "auditor-core/SKILL.md",
        "planning.entrypoints.lad_auditor",
        "planning.skills.auditor_core",
    ),
    (
        "lad_arbiter",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "lad_arbiter.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "arbiter-core/SKILL.md",
        "planning.entrypoints.lad_arbiter",
        "planning.skills.arbiter_core",
    ),
)
_BLUEPRINT_PAIRS = (
    (
        "contractor_blueprint",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "contractor_blueprint.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "contractor-blueprint-core/SKILL.md",
    ),
    (
        "evaluator_blueprint",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "evaluator_blueprint.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "evaluator-blueprint-core/SKILL.md",
    ),
    (
        "manager_blueprint",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "manager_blueprint.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "manager-blueprint-core/SKILL.md",
    ),
    (
        "mechanic_blueprint",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/"
        "mechanic_blueprint.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/"
        "mechanic-blueprint-core/SKILL.md",
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


def _donor_assets(source: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], source["assets"])


def _donor_asset_ids(source: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(asset["id"]) for asset in _donor_assets(source))


def _planning_owned_asset_ids(source: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        asset_id
        for asset_id in _donor_asset_ids(source)
        if asset_id.startswith("planning.")
    )


def _inherited_execution_asset_ids(source: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        asset_id
        for asset_id in _donor_asset_ids(source)
        if asset_id.startswith("execution.")
    )


def _package_path_for_planning_asset(asset_id: str) -> str:
    if asset_id.startswith("planning.entrypoints."):
        stage_id = asset_id.removeprefix("planning.entrypoints.")
        return f"assets/workflows/planning.lad/entrypoints/{stage_id}.md"
    if asset_id.startswith("planning.skills."):
        skill_id = asset_id.removeprefix("planning.skills.")
        skill_name = skill_id.removesuffix("_core").replace("_", "-")
        return f"assets/workflows/planning.lad/skills/{skill_name}-core.md"
    if asset_id.startswith("execution.entrypoints."):
        stage_id = asset_id.removeprefix("execution.entrypoints.")
        return f"assets/workflows/execution.lad/entrypoints/{stage_id}.md"
    if asset_id.startswith("execution.skills."):
        skill_id = asset_id.removeprefix("execution.skills.")
        skill_name = skill_id.removesuffix("_core").replace("_", "-")
        return f"assets/workflows/execution.lad/skills/{skill_name}-core.md"
    raise AssertionError(f"unexpected Planning asset id: {asset_id}")


def _expected_planning_asset_pins(
    package_root: Path,
    source: dict[str, object],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            asset_id,
            conformance.asset_digest_for_package_path(
                package_root,
                _package_path_for_planning_asset(asset_id),
            ),
        )
        for asset_id in sorted(_donor_asset_ids(source))
    )


def _refresh_manifest_digests(package_root: Path) -> None:
    manifest = _load_manifest(package_root)
    for asset in cast(list[dict[str, object]], manifest["assets"]):
        asset_bytes = (package_root / str(asset["package_path"])).read_bytes()
        asset["content_digest"] = asset_digest_for_bytes(asset_bytes)
        asset["byte_length"] = len(asset_bytes)

    assets_by_id = _assets_by_id(manifest)
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


def _copy_pruned_package_without_planning(tmp_path: Path) -> Path:
    pruned_root = tmp_path / "without-planning"
    shutil.copytree(PACKAGE_ROOT, pruned_root)
    manifest = _load_manifest(pruned_root)
    workflows = _workflows_by_id(manifest)
    planning_source = lad_planning.workflow_source()
    planning_owned_asset_ids = set(_planning_owned_asset_ids(planning_source))

    manifest["workflows"] = [
        workflow
        for workflow in workflows.values()
        if workflow["workflow_id"] != WORKFLOW_ID
    ]
    manifest["assets"] = [
        asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
        if str(asset["asset_id"]) not in planning_owned_asset_ids
    ]
    metadata = cast(dict[str, object], manifest["non_authoritative_metadata"])
    metadata["plus_packet"] = "PLUS-0002C"
    metadata["status"] = "official_simple_loop_and_lad_execution_workflow_package"
    planning_asset_dir = pruned_root / "assets" / "workflows" / "planning.lad"
    if planning_asset_dir.exists():
        shutil.rmtree(planning_asset_dir)

    (pruned_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )
    _refresh_manifest_digests(pruned_root)
    return pruned_root


def test_planning_workflow_identity_matches_donor_source() -> None:
    manifest = _load_manifest()
    workflows = _workflows_by_id(manifest)
    source_identity = cast(
        dict[str, object],
        lad_planning.workflow_source()["workflow"],
    )
    workflow = workflows[WORKFLOW_ID]

    assert set(workflows) == {
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        WORKFLOW_ID,
    }
    assert workflow["workflow_id"] == source_identity["id"]
    assert workflow["workflow_version"] == source_identity["version"]
    assert workflow["visibility"] == "public"
    assert workflow["entrypoints"] == ["default"]


def test_planning_selected_authority_matches_donor_without_assets_or_catalog() -> None:
    manifest = _load_manifest()
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    source = lad_planning.workflow_source()

    assert "assets" not in selected_authority
    assert "unselected_catalog" not in selected_authority
    assert selected_authority == _source_as_selected_authority(source)
    assert "unselected_catalog" in (
        lad_planning.workflow_source_with_unselected_catalog()
    )


def test_planning_assets_required_assets_and_digests_match_donor_closure() -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    assets_by_id = _assets_by_id(manifest)
    source = lad_planning.workflow_source()
    donor_asset_ids = _donor_asset_ids(source)

    assert {
        asset_id for asset_id in assets_by_id if asset_id in donor_asset_ids
    } == set(donor_asset_ids)
    assert workflow["required_assets"] == [
        {
            "asset_id": asset_id,
            "content_digest": assets_by_id[asset_id]["content_digest"],
        }
        for asset_id in donor_asset_ids
    ]

    for asset_id in donor_asset_ids:
        asset = assets_by_id[asset_id]
        assert asset["package_path"] == _package_path_for_planning_asset(asset_id)
        assert asset["selected_authority_participation"] == "yes"
        if ".entrypoints." in asset_id:
            assert asset["asset_kind"] == "entrypoint_prompt"
        else:
            assert asset["asset_kind"] == "stage_skill"


def test_planning_inherited_execution_assets_reuse_plus_0002c_package_bytes() -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflows = _workflows_by_id(manifest)
    assets_by_id = _assets_by_id(manifest)
    planning_required = {
        str(asset["asset_id"]): str(asset["content_digest"])
        for asset in cast(
            list[dict[str, object]],
            workflows[WORKFLOW_ID]["required_assets"],
        )
    }
    execution_required = {
        str(asset["asset_id"]): str(asset["content_digest"])
        for asset in cast(
            list[dict[str, object]],
            workflows["execution.lad"]["required_assets"],
        )
    }

    for asset_id in _inherited_execution_asset_ids(lad_planning.workflow_source()):
        asset = assets_by_id[asset_id]
        package_path = str(asset["package_path"])
        asset_bytes = (PACKAGE_ROOT / package_path).read_bytes()

        assert package_path == _package_path_for_planning_asset(asset_id)
        assert planning_required[asset_id] == execution_required[asset_id]
        assert asset["content_digest"] == execution_required[asset_id]
        assert asset["content_digest"] == asset_digest_for_bytes(asset_bytes)
        assert asset["byte_length"] == len(asset_bytes)


def test_planning_path_archive_selection_compiles_and_selects_asset_pins(
    tmp_path: Path,
) -> None:
    source = lad_planning.workflow_source()
    path_result, archive_result = conformance.select_package_from_path_and_archive(
        tmp_path / "selection",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=str(cast(dict[str, object], source["workflow"])["version"]),
    )
    expected_asset_pins = _expected_planning_asset_pins(PACKAGE_ROOT, source)

    for result in (path_result, archive_result):
        conformance.assert_selected_package_pin(
            result.plan,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=WORKFLOW_ID,
            workflow_version="0.1",
            selected_asset_pins=expected_asset_pins,
        )
        assert result.plan is not None
        assert "unselected_catalog" not in (
            canonical_authority_bytes(result.plan).decode("utf-8")
        )
        assert asdict(result.plan.workflow_package_pin) == {
            "package_id": PACKAGE_ID,
            "package_version": PACKAGE_VERSION,
            "package_format_version": "1",
            "workflow_id": WORKFLOW_ID,
            "workflow_version": "0.1",
            "entrypoint": "default",
            "selected_asset_pins": tuple(
                {
                    "asset_id": asset_id,
                    "content_digest": content_digest,
                }
                for asset_id, content_digest in expected_asset_pins
            ),
            "selected_dependency_pins": (),
        }
    assert authority_fingerprint(path_result.plan) == authority_fingerprint(
        archive_result.plan,
    )


def test_planning_assets_follow_entrypoint_authoring_boundaries() -> None:
    manifest = _load_manifest()
    assets_by_id = _assets_by_id(manifest)
    donor_asset_ids = _donor_asset_ids(lad_planning.workflow_source())
    asset_texts_by_path = {
        str(assets_by_id[asset_id]["package_path"]): (
            PACKAGE_ROOT / str(assets_by_id[asset_id]["package_path"])
        ).read_text()
        for asset_id in donor_asset_ids
    }
    asset_texts_by_id = {
        asset_id: (
            PACKAGE_ROOT / str(assets_by_id[asset_id]["package_path"])
        ).read_text()
        for asset_id in donor_asset_ids
    }

    for asset_id in donor_asset_ids:
        headings = (
            _ENTRYPOINT_HEADINGS
            if ".entrypoints." in asset_id
            else _CORE_SKILL_HEADINGS
        )
        for heading in headings:
            assert heading in asset_texts_by_id[asset_id]

    conformance.assert_no_runtime_authority_claims(asset_texts_by_path)
    conformance.assert_no_unscoped_selected_artifact_kind_mentions(
        asset_texts_by_id,
        declared_artifact_schema_ids_by_asset_id=(
            conformance.selected_artifact_schema_ids_by_asset_id(manifest)
        ),
    )


@pytest.mark.parametrize(
    "text",
    (
        "This prompt creates queue aliases.",
        "This skill routes work.",
        "Return `MANAGER_COMPLETE` to close work.",
        "The marker retries work.",
        "This prompt mutates runtime state.",
        "This skill grants authority.",
        "This prompt approves effects.",
        "The skill selects packages.",
        "Planning assets become default global inbox router.",
        "Planning assets become default task-kind router.",
    ),
)
def test_boundary_lint_refuses_planning_runtime_authority_claims(text: str) -> None:
    with pytest.raises(AssertionError):
        conformance.assert_no_runtime_authority_claims({"bad-planning.md": text})


def test_parity_exception_matrix_documents_v021_planning_and_blueprint_pairs() -> None:
    review = REVIEW_PATH.read_text()

    for (
        stage_id,
        entrypoint_path,
        skill_path,
        entrypoint_asset_id,
        skill_asset_id,
    ) in _PLANNING_STAGE_PAIRS:
        row = next(line for line in review.splitlines() if entrypoint_path in line)

        assert stage_id in row
        assert f"`{skill_path}`" in row
        assert f"`{entrypoint_asset_id}`" in row
        assert f"`{skill_asset_id}`" in row
        assert "planning.lad" in row
        assert "packaged_rewritten" in row
        assert "boundary-clean" in row
        assert "tests/test_lad_planning_official_package.py" in row

    for stage_id, entrypoint_path, skill_path in _BLUEPRINT_PAIRS:
        row = next(line for line in review.splitlines() if entrypoint_path in line)

        assert stage_id in row
        assert f"`{skill_path}`" in row
        assert "planning.blueprint" in row
        assert "deferred_post_cutover" in row
        assert "CKPT-0001" in row
        assert "not packaged" in row


def test_existing_workflow_fingerprints_stay_stable_when_planning_is_added(
    tmp_path: Path,
) -> None:
    pruned_root = _copy_pruned_package_without_planning(tmp_path)
    for workflow_id in ("simple_loop", "execution.lad", "execution.lad_integrator"):
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
