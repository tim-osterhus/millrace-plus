from __future__ import annotations

# ruff: noqa: E402
import json
import shutil
from collections.abc import Callable
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
from millrace.workflows import lad_execution

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.0.0"
REVIEW_PATH = PROJECT_ROOT / "docs" / "PLUS-0002C-implementation-review.md"

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
_LAD_DONORS: dict[str, Callable[[], dict[str, object]]] = {
    "execution.lad": lad_execution.workflow_source,
    "execution.lad_integrator": lad_execution.integrator_workflow_source,
}
_LEGACY_STAGE_PAIRS = (
    (
        "lad_builder",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_builder.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "builder-core/SKILL.md",
        "execution.entrypoints.lad_builder",
        "execution.skills.builder_core",
        "execution.lad, execution.lad_integrator",
    ),
    (
        "lad_integrator",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_integrator.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "integrator-core/SKILL.md",
        "execution.entrypoints.lad_integrator",
        "execution.skills.integrator_core",
        "execution.lad_integrator",
    ),
    (
        "lad_checker",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_checker.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "checker-core/SKILL.md",
        "execution.entrypoints.lad_checker",
        "execution.skills.checker_core",
        "execution.lad, execution.lad_integrator",
    ),
    (
        "lad_fixer",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_fixer.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "fixer-core/SKILL.md",
        "execution.entrypoints.lad_fixer",
        "execution.skills.fixer_core",
        "execution.lad, execution.lad_integrator",
    ),
    (
        "lad_doublechecker",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_doublechecker.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "doublechecker-core/SKILL.md",
        "execution.entrypoints.lad_doublechecker",
        "execution.skills.doublechecker_core",
        "execution.lad, execution.lad_integrator",
    ),
    (
        "lad_updater",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_updater.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "updater-core/SKILL.md",
        "execution.entrypoints.lad_updater",
        "execution.skills.updater_core",
        "execution.lad, execution.lad_integrator",
    ),
    (
        "lad_troubleshooter",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_troubleshooter.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "troubleshooter-core/SKILL.md",
        "execution.entrypoints.lad_troubleshooter",
        "execution.skills.troubleshooter_core",
        "execution.lad, execution.lad_integrator",
    ),
    (
        "lad_consultant",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        "lad_consultant.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/"
        "consultant-core/SKILL.md",
        "execution.entrypoints.lad_consultant",
        "execution.skills.consultant_core",
        "execution.lad, execution.lad_integrator",
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


def _all_lad_asset_ids() -> tuple[str, ...]:
    ordered: dict[str, None] = {}
    for donor in _LAD_DONORS.values():
        for asset_id in _donor_asset_ids(donor()):
            ordered.setdefault(asset_id, None)
    return tuple(ordered)


def _package_path_for_lad_asset(asset_id: str) -> str:
    if asset_id.startswith("execution.entrypoints."):
        stage_id = asset_id.removeprefix("execution.entrypoints.")
        owner = (
            "execution.lad_integrator"
            if stage_id == "lad_integrator"
            else "execution.lad"
        )
        return f"assets/workflows/{owner}/entrypoints/{stage_id}.md"
    if asset_id.startswith("execution.skills."):
        skill_id = asset_id.removeprefix("execution.skills.")
        owner = (
            "execution.lad_integrator"
            if skill_id == "integrator_core"
            else "execution.lad"
        )
        skill_name = skill_id.removesuffix("_core").replace("_", "-")
        return f"assets/workflows/{owner}/skills/{skill_name}-core.md"
    raise AssertionError(f"unexpected LAD asset id: {asset_id}")


def _expected_lad_asset_pins(
    package_root: Path,
    source: dict[str, object],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            asset_id,
            conformance.asset_digest_for_package_path(
                package_root,
                _package_path_for_lad_asset(asset_id),
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


def _copy_pruned_simple_loop_package(tmp_path: Path) -> Path:
    pruned_root = tmp_path / "simple-loop-only"
    shutil.copytree(PACKAGE_ROOT, pruned_root)
    manifest = _load_manifest(pruned_root)
    workflows = _workflows_by_id(manifest)
    simple_loop_workflow = workflows["simple_loop"]
    simple_loop_asset_ids = {
        str(required_asset["asset_id"])
        for required_asset in cast(
            list[dict[str, object]],
            simple_loop_workflow["required_assets"],
        )
    }
    manifest["workflows"] = [simple_loop_workflow]
    manifest["assets"] = [
        asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
        if str(asset["asset_id"]) in simple_loop_asset_ids
    ]
    metadata = cast(dict[str, object], manifest["non_authoritative_metadata"])
    metadata["plus_packet"] = "PLUS-0002B"
    metadata["status"] = "official_simple_loop_workflow_package"
    (pruned_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )
    for path in (
        pruned_root / "assets" / "workflows" / "execution.lad",
        pruned_root / "assets" / "workflows" / "execution.lad_integrator",
    ):
        if path.exists():
            shutil.rmtree(path)
    _refresh_manifest_digests(pruned_root)
    return pruned_root


def test_lad_workflow_identities_match_donor_sources() -> None:
    manifest = _load_manifest()
    workflows = _workflows_by_id(manifest)

    assert set(workflows) == {
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
        "lad.full",
        "vendor_selection",
    }
    for workflow_id, donor in _LAD_DONORS.items():
        source = donor()
        source_identity = cast(dict[str, object], source["workflow"])
        workflow = workflows[workflow_id]

        assert workflow["workflow_id"] == source_identity["id"]
        assert workflow["workflow_version"] == source_identity["version"]
        assert workflow["visibility"] == "public"
        assert workflow["entrypoints"] == ["default"]


def test_lad_selected_authority_matches_donor_sources_without_inline_assets() -> None:
    manifest = _load_manifest()
    workflows = _workflows_by_id(manifest)

    for workflow_id, donor in _LAD_DONORS.items():
        workflow = workflows[workflow_id]
        selected_authority = cast(
            dict[str, object],
            workflow["selected_authority"],
        )

        assert "assets" not in selected_authority
        assert selected_authority == _source_as_selected_authority(donor())


def test_lad_assets_and_required_assets_match_donor_closures() -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflows = _workflows_by_id(manifest)
    assets_by_id = _assets_by_id(manifest)

    assert tuple(
        asset_id
        for asset_id in assets_by_id
        if asset_id.startswith("execution.")
    ) == _all_lad_asset_ids()

    for asset_id in _all_lad_asset_ids():
        asset = assets_by_id[asset_id]
        assert asset["package_path"] == _package_path_for_lad_asset(asset_id)
        assert asset["selected_authority_participation"] == "yes"
        if asset_id.startswith("execution.entrypoints."):
            assert asset["asset_kind"] == "entrypoint_prompt"
        else:
            assert asset["asset_kind"] == "stage_skill"

    for workflow_id, donor in _LAD_DONORS.items():
        source = donor()
        required_assets = cast(
            list[dict[str, object]],
            workflows[workflow_id]["required_assets"],
        )
        assert required_assets == [
            {
                "asset_id": asset_id,
                "content_digest": assets_by_id[asset_id]["content_digest"],
            }
            for asset_id in _donor_asset_ids(source)
        ]


def test_lad_package_selection_compiles_and_selects_asset_pins(
    tmp_path: Path,
) -> None:
    for workflow_id, donor in _LAD_DONORS.items():
        source = donor()
        path_result, archive_result = conformance.select_package_from_path_and_archive(
            tmp_path / workflow_id.replace(".", "-"),
            PACKAGE_ROOT,
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=workflow_id,
            workflow_version=str(
                cast(dict[str, object], source["workflow"])["version"],
            ),
        )

        expected_asset_pins = _expected_lad_asset_pins(PACKAGE_ROOT, source)
        for result in (path_result, archive_result):
            conformance.assert_selected_package_pin(
                result.plan,
                package_id=PACKAGE_ID,
                package_version=PACKAGE_VERSION,
                workflow_id=workflow_id,
                workflow_version=str(
                    cast(dict[str, object], source["workflow"])["version"],
                ),
                selected_asset_pins=expected_asset_pins,
            )
        assert authority_fingerprint(path_result.plan) == authority_fingerprint(
            archive_result.plan,
        )


def test_simple_loop_fingerprint_stays_stable_when_lad_entries_are_added(
    tmp_path: Path,
) -> None:
    full_result = conformance.select_package_from_path(
        tmp_path / "full",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id="simple_loop",
        workflow_version="0.1",
    )
    pruned_result = conformance.select_package_from_path(
        tmp_path / "pruned",
        _copy_pruned_simple_loop_package(tmp_path),
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id="simple_loop",
        workflow_version="0.1",
    )

    assert authority_fingerprint(full_result.plan) == authority_fingerprint(
        pruned_result.plan,
    )


def test_lad_entrypoints_and_skills_follow_authoring_boundaries() -> None:
    manifest = _load_manifest()
    assets_by_id = _assets_by_id(manifest)
    asset_texts_by_path = {
        str(assets_by_id[asset_id]["package_path"]): (
            PACKAGE_ROOT / str(assets_by_id[asset_id]["package_path"])
        ).read_text()
        for asset_id in _all_lad_asset_ids()
    }
    asset_texts_by_id = {
        asset_id: (
            PACKAGE_ROOT / str(assets_by_id[asset_id]["package_path"])
        ).read_text()
        for asset_id in _all_lad_asset_ids()
    }

    for asset_id in _all_lad_asset_ids():
        text = asset_texts_by_id[asset_id]
        headings = (
            _ENTRYPOINT_HEADINGS
            if asset_id.startswith("execution.entrypoints.")
            else _CORE_SKILL_HEADINGS
        )
        for heading in headings:
            assert heading in text

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
        "Return `WORK_DONE` to route the item.",
        "Return `WORK_DONE` to retry the item.",
        "Return `WORK_DONE` to close the item.",
        "Return `BLOCKED` to quarantine the lineage.",
        "This skill grants capability authority.",
        "This prompt approves effects.",
        "The prompt mutates package state.",
        "The skill mutates runtime state.",
    ),
)
def test_boundary_lint_refuses_prompt_or_skill_runtime_authority_claims(
    text: str,
) -> None:
    with pytest.raises(AssertionError):
        conformance.assert_no_runtime_authority_claims({"bad-asset.md": text})


def test_parity_exception_matrix_documents_each_v021_execution_pair() -> None:
    review = REVIEW_PATH.read_text()
    matrix_rows = [
        line
        for line in review.splitlines()
        if "`dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/"
        in line
    ]

    assert len(matrix_rows) == len(_LEGACY_STAGE_PAIRS)
    for (
        stage_id,
        entrypoint_path,
        skill_path,
        entrypoint_asset_id,
        skill_asset_id,
        owning_selector,
    ) in _LEGACY_STAGE_PAIRS:
        row = next(line for line in matrix_rows if entrypoint_path in line)

        assert stage_id in row
        assert f"`{skill_path}`" in row
        assert f"`{entrypoint_asset_id}`" in row
        assert f"`{skill_asset_id}`" in row
        assert owning_selector in row
        assert "packaged_rewritten" in row
        assert "boundary-clean" in row
        assert "tests/test_lad_execution_official_package.py" in row
