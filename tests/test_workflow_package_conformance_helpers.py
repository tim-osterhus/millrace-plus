from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "compileable_workflow_package"
PACKAGE_ID = "millrace.plus.test_fixture"
PACKAGE_VERSION = "0.0.0"
WORKFLOW_ID = "millrace.plus.test_fixture.workflow"
WORKFLOW_VERSION = "1"
ASSET_ID = "millrace.plus.test_fixture.prompt"
ASSET_PATH = "assets/test_prompt.md"
DIST_NAME = "millrace-plus"
IMPORT_PACKAGE = "millrace_plus"
RESOURCE_ROOT = "millrace_workflow_package"
PROJECT_PACKAGE_ROOT = PROJECT_ROOT / RESOURCE_ROOT


def _conformance():
    try:
        spec = importlib.util.find_spec("support.package_conformance")
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, (
        "PLUS-0002A requires reusable test-local workflow package "
        "conformance helpers."
    )
    return importlib.import_module("support.package_conformance")


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "package"
    shutil.copytree(FIXTURE_ROOT, target)
    return target


def _load_manifest(package_root: Path) -> dict[str, object]:
    return json.loads((package_root / "manifest.json").read_text())


def _write_manifest(package_root: Path, manifest: dict[str, object]) -> None:
    (package_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
    )


def _project_member_paths() -> tuple[str, ...]:
    manifest = _load_manifest(PROJECT_PACKAGE_ROOT)
    assets = manifest["assets"]
    assert isinstance(assets, list)
    return tuple(
        sorted(
            (
                "manifest.json",
                *(str(asset["package_path"]) for asset in assets),
            )
        )
    )


def test_helpers_validate_fixture_digests_and_path_archive_parity() -> None:
    conformance = _conformance()

    manifest = conformance.assert_manifest_and_asset_digests(FIXTURE_ROOT)
    path_source, archive_source = conformance.assert_path_archive_source_parity(
        FIXTURE_ROOT,
    )

    assert manifest["package"]["package_id"] == PACKAGE_ID
    assert path_source.manifest is not None
    assert archive_source.manifest is not None
    assert path_source.manifest.manifest_digest == (
        archive_source.manifest.manifest_digest
    )
    assert path_source.member_paths == (ASSET_PATH, "manifest.json")


def test_helpers_refuse_stale_manifest_digest(tmp_path: Path) -> None:
    conformance = _conformance()
    package_root = _copy_fixture(tmp_path)
    manifest = _load_manifest(package_root)
    package = manifest["package"]
    assert isinstance(package, dict)
    package["package_id"] = "mutated.without.digest"
    _write_manifest(package_root, manifest)

    with pytest.raises(AssertionError):
        conformance.assert_manifest_and_asset_digests(package_root)


def test_helpers_refuse_stale_asset_digest_and_byte_length(tmp_path: Path) -> None:
    conformance = _conformance()
    package_root = _copy_fixture(tmp_path)
    (package_root / ASSET_PATH).write_text("mutated prompt bytes\n")

    with pytest.raises(AssertionError):
        conformance.assert_manifest_and_asset_digests(package_root)


def test_helpers_refuse_undeclared_selected_artifact_kind_mentions() -> None:
    conformance = _conformance()

    with pytest.raises(AssertionError) as exc_info:
        conformance.assert_no_undeclared_selected_artifact_kind_mentions(
            {
                "reviewer-core.md": (
                    "| `artifact_kind` | yes | string | "
                    "Use `simple_loop.review_result`. |"
                )
            },
            declared_artifact_schema_ids=frozenset({"simple_loop.gap_packet"}),
        )

    assert "simple_loop.review_result" in str(exc_info.value)


def test_helpers_select_compileable_fixture_and_assert_selected_pin(
    tmp_path: Path,
) -> None:
    conformance = _conformance()

    result = conformance.select_package_from_path(
        tmp_path,
        FIXTURE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
    )
    conformance.assert_selected_package_pin(
        result.plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
        selected_asset_pins=(
            (
                ASSET_ID,
                conformance.asset_digest_for_package_path(FIXTURE_ROOT, ASSET_PATH),
            ),
        ),
    )


def test_helpers_refuse_wrong_selected_pin(tmp_path: Path) -> None:
    conformance = _conformance()

    result = conformance.select_package_from_path(
        tmp_path,
        FIXTURE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
    )

    with pytest.raises(AssertionError):
        conformance.assert_selected_package_pin(
            result.plan,
            package_id="wrong.package",
            package_version=PACKAGE_VERSION,
            workflow_id=WORKFLOW_ID,
            workflow_version=WORKFLOW_VERSION,
            selected_asset_pins=(
                (
                    ASSET_ID,
                    conformance.asset_digest_for_package_path(
                        FIXTURE_ROOT,
                        ASSET_PATH,
                    ),
                ),
            ),
        )


def test_helpers_assert_path_archive_selection_pin_parity(tmp_path: Path) -> None:
    conformance = _conformance()

    path_result, archive_result = conformance.select_package_from_path_and_archive(
        tmp_path,
        FIXTURE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=WORKFLOW_ID,
        workflow_version=WORKFLOW_VERSION,
    )

    assert path_result.plan is not None
    assert archive_result.plan is not None
    assert path_result.plan.workflow_package_pin == (
        archive_result.plan.workflow_package_pin
    )


def test_helpers_prove_installed_discovery_is_byte_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conformance = _conformance()
    wheel_path = conformance.build_project_wheel(PROJECT_ROOT, tmp_path / "dist")

    conformance.assert_wheel_contains_byte_only_package_data(
        wheel_path,
        resource_root=RESOURCE_ROOT,
        import_package=IMPORT_PACKAGE,
        expected_member_paths=_project_member_paths(),
    )
    source = conformance.assert_installed_discovery_is_byte_only(
        monkeypatch,
        wheel_path=wheel_path,
        target=tmp_path / "site",
        distribution_name=DIST_NAME,
        import_package=IMPORT_PACKAGE,
        installed_resource_root=RESOURCE_ROOT,
        expected_package_root=PROJECT_PACKAGE_ROOT,
    )

    assert source.source_kind == "installed_python_package"
    assert source.member_paths == _project_member_paths()
