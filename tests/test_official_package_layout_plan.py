from __future__ import annotations

import json
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
OFFICIAL_PACKAGE_ID = "millrace.plus.official"
TEMPORARY_SCAFFOLD_PACKAGE_ID = "millrace.plus.scaffold"
RELEASE_IDENTITY = "0.22.3"


def _shipped_manifest() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((PACKAGE_ROOT / "manifest.json").read_text()),
    )


def test_readme_documents_public_official_workflow_package() -> None:
    readme = " ".join((PROJECT_ROOT / "README.md").read_text().split())

    for required in (
        "`millrace_workflow_package/`",
        "`millrace.plus.official`",
        "`millrace-plus` 0.22.3 accompanies Core 0.22.3",
        "installed resource root is `millrace_workflow_package`",
        "`simple_loop`",
        "`execution.lad`",
        "`execution.lad_integrator`",
        "`planning.lad`",
        "`lad.full`",
        "`vendor_selection`",
        "package data is non-executable",
        "`millrace==0.22.3` convenience bundle installs Core and Plus 0.22.3",
    ):
        assert required in readme
    assert "PLUS-" not in readme


def test_release_notes_document_current_package_contents() -> None:
    release_notes = (PROJECT_ROOT / "docs" / "release.md").read_text()

    for required in (
        "`simple_loop`",
        "`execution.lad`",
        "`execution.lad_integrator`",
        "`planning.lad`",
        "`lad.full`",
        "`vendor_selection`",
        "| Runtime dependency | None |",
    ):
        assert required in release_notes
    assert "PLUS-0002C keeps" not in release_notes
    assert "PLUS-0002D keeps" not in release_notes
    assert "PLUS-0002E extends" not in release_notes
    assert "PLUS-0002F extends" not in release_notes


def test_public_docs_describe_six_ordinary_workflows_without_campaign_arms() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text()
    workflows = (PROJECT_ROOT / "docs" / "workflows.md").read_text()
    release = (PROJECT_ROOT / "docs" / "release.md").read_text()
    public_docs = "\n".join((readme, workflows, release))

    assert "contains six selectable workflow configurations" in workflows
    assert "six workflow entries" in release
    assert "`execution.lad_codex_semantic_worktree`" in readme
    assert "not selectable" in public_docs
    assert "inconclusive" in public_docs
    assert "execution.lad_codex_control" not in public_docs
    assert "Control/Treatment" not in public_docs
    assert "Control arm" not in public_docs
    assert "Treatment arm" not in public_docs
    assert "millrace==0.22.3" in public_docs


def test_shipped_package_root_is_no_longer_temporary_scaffold() -> None:
    manifest = _shipped_manifest()
    package = cast(dict[str, object], manifest["package"])
    workflows = cast(list[object], manifest["workflows"])
    metadata = cast(dict[str, object], manifest["non_authoritative_metadata"])

    assert package["package_id"] == OFFICIAL_PACKAGE_ID
    assert package["package_id"] != TEMPORARY_SCAFFOLD_PACKAGE_ID
    assert package["package_role"] == "workflow_package"
    assert workflows
    assert all(
        cast(dict[str, object], workflow)["visibility"] == "public"
        for workflow in workflows
    )
    assert "plus_packet" not in metadata
    assert metadata["status"] == (
        "package_release_live_qualification_not_claimed"
    )
    metadata_text = json.dumps(metadata, sort_keys=True)
    assert "PLUS-" not in metadata_text
    assert "pending_fresh_live_e2e" not in metadata_text
    assert "live qualification is not claimed" in metadata_text


def test_official_manifest_uses_v022_release_identity() -> None:
    manifest = _shipped_manifest()
    package = cast(dict[str, object], manifest["package"])

    assert package["package_version"] == RELEASE_IDENTITY
