from __future__ import annotations

import json
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
OFFICIAL_PACKAGE_ID = "millrace.plus.official"
TEMPORARY_SCAFFOLD_PACKAGE_ID = "millrace.plus.scaffold"


def _shipped_manifest() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((PACKAGE_ROOT / "manifest.json").read_text()),
    )


def test_readme_documents_plus_0002c_official_workflow_package_plan() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text()

    for required in (
        "`millrace_workflow_package/`",
        "`millrace.plus.official`",
        "package version `0.0.0`",
        "installed resource root `millrace_workflow_package`",
        "`simple_loop`",
        "`execution.lad`",
        "`execution.lad_integrator`",
        "package data is non-executable",
    ):
        assert required in readme


def test_shipped_package_root_is_no_longer_temporary_scaffold() -> None:
    manifest = _shipped_manifest()
    package = cast(dict[str, object], manifest["package"])
    workflows = cast(list[object], manifest["workflows"])
    metadata = cast(dict[str, object], manifest["non_authoritative_metadata"])

    assert package["package_id"] == OFFICIAL_PACKAGE_ID
    assert package["package_id"] != TEMPORARY_SCAFFOLD_PACKAGE_ID
    assert package["package_version"] == "0.0.0"
    assert package["package_role"] == "workflow_package"
    assert workflows
    assert all(
        cast(dict[str, object], workflow)["visibility"] == "public"
        for workflow in workflows
    )
    assert metadata["plus_packet"] == "PLUS-0002C"
    assert metadata["status"] == (
        "official_simple_loop_and_lad_execution_workflow_package"
    )
