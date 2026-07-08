from __future__ import annotations

import copy
import json
import re
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
from millrace.workflows import lad_learning

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.0.0"
WORKFLOW_ID = "lad.full"
REVIEW_PATH = PROJECT_ROOT / "docs" / "PLUS-0002E-implementation-review.md"

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
_LEARNING_STAGE_PAIRS = (
    (
        "analyst",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/"
        "analyst.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/"
        "analyst-core/SKILL.md",
        "learning.entrypoints.analyst",
        "learning.skills.analyst_core",
    ),
    (
        "professor",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/"
        "professor.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/"
        "professor-core/SKILL.md",
        "learning.entrypoints.professor",
        "learning.skills.professor_core",
    ),
    (
        "curator",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/"
        "curator.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/"
        "curator-core/SKILL.md",
        "learning.entrypoints.curator",
        "learning.skills.curator_core",
    ),
    (
        "librarian",
        "dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/"
        "librarian.md",
        "dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/"
        "librarian-core/SKILL.md",
        "learning.entrypoints.librarian",
        "learning.skills.librarian_core",
    ),
)
_PROVIDER_EFFECT_REFS = (
    "learning.effect.curator.workspace_skill_update",
    "learning.effect.librarian.workspace_skill_install_report",
    "provider.fake_local.workspace",
    "policy.fake_local.no_real_side_effects",
)
_SECRET_VALUE_PATTERN = re.compile(
    r"\b(?:api[_-]?key|oauth[_-]?token|provider[_-]?secret|"
    r"client[_-]?secret|password)\b\s*[:=]\s*['\"][^'\"]+['\"]",
    re.IGNORECASE,
)
_PROVIDER_CODE_PATTERNS = (
    "subprocess.run(",
    "requests.",
    "httpx.",
    "import requests",
    "import httpx",
    "mcp.server",
    "native_runner",
    "provider_adapter",
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


def _learning_owned_asset_ids(source: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        asset_id
        for asset_id in _donor_asset_ids(source)
        if asset_id.startswith("learning.")
    )


def _inherited_planning_asset_ids(source: dict[str, object]) -> tuple[str, ...]:
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


def _required_asset_digests(workflow: dict[str, object]) -> dict[str, str]:
    return {
        str(asset["asset_id"]): str(asset["content_digest"])
        for asset in cast(list[dict[str, object]], workflow["required_assets"])
    }


def _package_path_for_full_lad_asset(asset_id: str) -> str:
    if asset_id.startswith("learning.entrypoints."):
        stage_id = asset_id.removeprefix("learning.entrypoints.")
        return f"assets/workflows/lad.full/entrypoints/{stage_id}.md"
    if asset_id.startswith("learning.skills."):
        skill_id = asset_id.removeprefix("learning.skills.")
        skill_name = skill_id.removesuffix("_core").replace("_", "-")
        return f"assets/workflows/lad.full/skills/{skill_name}-core.md"
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
    raise AssertionError(f"unexpected full LAD asset id: {asset_id}")


def _expected_full_lad_asset_pins(
    package_root: Path,
    source: dict[str, object],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            asset_id,
            conformance.asset_digest_for_package_path(
                package_root,
                _package_path_for_full_lad_asset(asset_id),
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


def _copy_pruned_package_without_learning(tmp_path: Path) -> Path:
    pruned_root = tmp_path / "without-learning"
    shutil.copytree(PACKAGE_ROOT, pruned_root)
    manifest = _load_manifest(pruned_root)
    workflows = _workflows_by_id(manifest)
    source = lad_learning.workflow_source()
    learning_asset_ids = set(_learning_owned_asset_ids(source))

    manifest["workflows"] = [
        workflow
        for workflow in workflows.values()
        if workflow["workflow_id"] != WORKFLOW_ID
    ]
    manifest["assets"] = [
        asset
        for asset in cast(list[dict[str, object]], manifest["assets"])
        if str(asset["asset_id"]) not in learning_asset_ids
    ]
    metadata = cast(dict[str, object], manifest["non_authoritative_metadata"])
    metadata["plus_packet"] = "PLUS-0002D"
    metadata["status"] = (
        "official_simple_loop_lad_execution_and_lad_planning_workflow_package"
    )
    learning_asset_dir = pruned_root / "assets" / "workflows" / "lad.full"
    if learning_asset_dir.exists():
        shutil.rmtree(learning_asset_dir)

    (pruned_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )
    _refresh_manifest_digests(pruned_root)
    return pruned_root


def _asset_texts_by_id(
    manifest: dict[str, Any],
    asset_ids: tuple[str, ...],
) -> dict[str, str]:
    assets_by_id = _assets_by_id(manifest)
    return {
        asset_id: (
            PACKAGE_ROOT / str(assets_by_id[asset_id]["package_path"])
        ).read_text()
        for asset_id in asset_ids
    }


def _asset_texts_by_path(
    manifest: dict[str, Any],
    asset_ids: tuple[str, ...],
) -> dict[str, str]:
    assets_by_id = _assets_by_id(manifest)
    return {
        str(assets_by_id[asset_id]["package_path"]): (
            PACKAGE_ROOT / str(assets_by_id[asset_id]["package_path"])
        ).read_text()
        for asset_id in asset_ids
    }


def test_full_lad_workflow_identity_matches_learning_donor_source() -> None:
    manifest = _load_manifest()
    workflows = _workflows_by_id(manifest)
    source_identity = cast(
        dict[str, object],
        lad_learning.workflow_source()["workflow"],
    )
    workflow = workflows[WORKFLOW_ID]

    assert set(workflows) == {
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
        WORKFLOW_ID,
    }
    assert workflow["workflow_id"] == source_identity["id"]
    assert workflow["workflow_version"] == source_identity["version"]
    assert workflow["visibility"] == "public"
    assert workflow["entrypoints"] == ["default"]


def test_full_lad_selected_authority_matches_donor_without_assets() -> None:
    manifest = _load_manifest()
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    selected_authority = cast(dict[str, object], workflow["selected_authority"])
    source = lad_learning.workflow_source()

    assert "assets" not in selected_authority
    assert selected_authority == _source_as_selected_authority(source)


def test_full_lad_assets_required_assets_and_digests_match_donor_closure() -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    assets_by_id = _assets_by_id(manifest)
    source = lad_learning.workflow_source()
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
        assert asset["package_path"] == _package_path_for_full_lad_asset(asset_id)
        assert asset["selected_authority_participation"] == "yes"
        if ".entrypoints." in asset_id:
            assert asset["asset_kind"] == "entrypoint_prompt"
        else:
            assert asset["asset_kind"] == "stage_skill"


def test_full_lad_inherited_assets_reuse_planning_and_execution_package_bytes() -> None:
    manifest = conformance.assert_manifest_and_asset_digests(PACKAGE_ROOT)
    workflows = _workflows_by_id(manifest)
    assets_by_id = _assets_by_id(manifest)
    source = lad_learning.workflow_source()
    full_required = _required_asset_digests(workflows[WORKFLOW_ID])
    planning_required = _required_asset_digests(workflows["planning.lad"])
    execution_required = _required_asset_digests(workflows["execution.lad"])

    for asset_id in _inherited_planning_asset_ids(source):
        asset = assets_by_id[asset_id]
        package_path = str(asset["package_path"])
        asset_bytes = (PACKAGE_ROOT / package_path).read_bytes()

        assert package_path == _package_path_for_full_lad_asset(asset_id)
        assert full_required[asset_id] == planning_required[asset_id]
        assert asset["content_digest"] == planning_required[asset_id]
        assert asset["content_digest"] == asset_digest_for_bytes(asset_bytes)
        assert asset["byte_length"] == len(asset_bytes)

    for asset_id in _inherited_execution_asset_ids(source):
        asset = assets_by_id[asset_id]
        package_path = str(asset["package_path"])
        asset_bytes = (PACKAGE_ROOT / package_path).read_bytes()

        assert package_path == _package_path_for_full_lad_asset(asset_id)
        assert full_required[asset_id] == planning_required[asset_id]
        assert full_required[asset_id] == execution_required[asset_id]
        assert asset["content_digest"] == execution_required[asset_id]
        assert asset["content_digest"] == asset_digest_for_bytes(asset_bytes)
        assert asset["byte_length"] == len(asset_bytes)


def test_full_lad_path_archive_selection_compiles_and_selects_asset_pins(
    tmp_path: Path,
) -> None:
    source = lad_learning.workflow_source()
    path_result, archive_result = conformance.select_package_from_path_and_archive(
        tmp_path / "selection",
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=str(cast(dict[str, object], source["workflow"])["version"]),
    )
    expected_asset_pins = _expected_full_lad_asset_pins(PACKAGE_ROOT, source)

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
        authority_text = canonical_authority_bytes(result.plan).decode("utf-8")
        for selected_ref in _PROVIDER_EFFECT_REFS:
            assert selected_ref in authority_text
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


def test_full_lad_effect_provider_refs_are_selected_data_only() -> None:
    manifest = _load_manifest()
    workflow = _workflows_by_id(manifest)[WORKFLOW_ID]
    source = lad_learning.workflow_source()
    learning_asset_ids = _learning_owned_asset_ids(source)
    asset_text = "\n".join(
        _asset_texts_by_id(manifest, learning_asset_ids).values(),
    )
    manifest_without_authority = copy.deepcopy(manifest)
    for record in cast(
        list[dict[str, object]],
        manifest_without_authority["workflows"],
    ):
        record.pop("selected_authority")

    selected_authority_text = json.dumps(
        workflow["selected_authority"],
        sort_keys=True,
    )
    non_authority_manifest_text = json.dumps(
        manifest_without_authority,
        sort_keys=True,
    )

    for selected_ref in _PROVIDER_EFFECT_REFS:
        assert selected_ref in selected_authority_text
        assert selected_ref not in non_authority_manifest_text
        assert selected_ref not in asset_text

    assert not any(PACKAGE_ROOT.rglob("*.py"))
    assert _SECRET_VALUE_PATTERN.search(asset_text) is None
    for provider_code_pattern in _PROVIDER_CODE_PATTERNS:
        assert provider_code_pattern not in asset_text


def test_full_lad_assets_follow_entrypoint_authoring_boundaries() -> None:
    manifest = _load_manifest()
    source = lad_learning.workflow_source()
    donor_asset_ids = _donor_asset_ids(source)
    asset_texts_by_path = _asset_texts_by_path(manifest, donor_asset_ids)
    asset_texts_by_id = _asset_texts_by_id(manifest, donor_asset_ids)

    for asset_id in donor_asset_ids:
        headings = (
            _ENTRYPOINT_HEADINGS
            if ".entrypoints." in asset_id
            else _CORE_SKILL_HEADINGS
        )
        for heading in headings:
            assert heading in asset_texts_by_id[asset_id]

    conformance.assert_no_runtime_authority_claims(
        {
            **asset_texts_by_path,
            "manifest.json": (PACKAGE_ROOT / "manifest.json").read_text(),
        },
    )
    conformance.assert_no_unscoped_selected_artifact_kind_mentions(
        asset_texts_by_id,
        declared_artifact_schema_ids_by_asset_id=(
            conformance.selected_artifact_schema_ids_by_asset_id(manifest)
        ),
    )


@pytest.mark.parametrize(
    "text",
    (
        "This package grants provider credentials.",
        "The manifest ships provider execution code.",
        "This skill runs MCP tool execution.",
        "This prompt invokes native runner behavior.",
        "The asset text persists durable state.",
        "This package reconciles effects.",
        "The manifest grants capability grants.",
        "Return `CURATOR_COMPLETE` to approve effects.",
        "Return `LIBRARIAN_COMPLETE` to mutate runtime state.",
    ),
)
def test_boundary_lint_refuses_full_lad_runtime_authority_claims(text: str) -> None:
    with pytest.raises(AssertionError):
        conformance.assert_no_runtime_authority_claims({"bad-learning.md": text})


def test_parity_exception_matrix_documents_v021_learning_pairs() -> None:
    review = REVIEW_PATH.read_text()

    for (
        stage_id,
        entrypoint_path,
        skill_path,
        entrypoint_asset_id,
        skill_asset_id,
    ) in _LEARNING_STAGE_PAIRS:
        row = next(line for line in review.splitlines() if entrypoint_path in line)

        assert stage_id in row
        assert f"`{skill_path}`" in row
        assert f"`{entrypoint_asset_id}`" in row
        assert f"`{skill_asset_id}`" in row
        assert WORKFLOW_ID in row
        assert "packaged_rewritten" in row
        assert "boundary-clean" in row
        assert "tests/test_lad_learning_official_package.py" in row


def test_existing_workflow_fingerprints_stay_stable_when_learning_is_added(
    tmp_path: Path,
) -> None:
    pruned_root = _copy_pruned_package_without_learning(tmp_path)
    for workflow_id in (
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
    ):
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
