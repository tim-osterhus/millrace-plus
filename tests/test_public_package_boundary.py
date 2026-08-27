from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TESTS = (
    "tests/test_package_metadata.py",
    "tests/test_manifest_authoring_policy.py",
    "tests/test_official_package_layout_plan.py",
    "tests/test_workflow_package_installed_smoke.py",
    "tests/test_public_package_boundary.py",
    "tests/test_agent_skill_assets.py",
)
NON_PUBLIC_REGRESSION_TESTS = {
    "tests/test_lad_execution_official_package.py",
    "tests/test_lad_planning_official_package.py",
    "tests/test_workflow_package_manifest.py",
}
PUBLIC_DOCS = (
    "README.md",
    "docs/workflows.md",
    "docs/authoring.md",
    "docs/manifest-authoring-policy.md",
    "docs/release.md",
    "docs/public-validation.md",
)
RELEASE_IDENTITY = "0.22.3"
META_RELEASE_PIN = "`millrace==0.22.3`"
BUNDLE_MEMBER_PINS = (
    "`millrace-ai==0.22.3`",
    "`millrace-plus==0.22.3`",
    "`millforge==0.1.0`",
)
VERSIONING_RULES = (
    "Member distributions version independently.",
    "Each `millrace` meta-distribution release pins one tested combination",
    "may reuse an unchanged compatible member.",
)
STALE_PUBLIC_RELEASE_PHRASES = (
    "0.0.0",
    "staging version",
    "staging package version",
    "staging metadata",
    "not yet published",
    "being prepared",
    "replace staging metadata",
    "install both tested distributions together",
    "`millrace-ai` and `millrace-plus` together",
    "only `millrace-ai` and `millrace-plus`",
    "both `millrace-ai` and `millrace-plus`",
)


def _project_text(path: str) -> str:
    return (PROJECT_ROOT / path).read_text()


def _normalized_text(text: str) -> str:
    return " ".join(text.lower().split())


def _imports_runtime_module(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "millrace" or alias.name.startswith("millrace."):
                    return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "millrace" or module.startswith("millrace."):
                return True
    return False


def test_public_validation_selection_has_no_runtime_or_legacy_path_imports() -> None:
    for public_test in PUBLIC_TESTS:
        test_path = PROJECT_ROOT / public_test
        assert test_path.is_file(), public_test
        assert not _imports_runtime_module(test_path), public_test


def test_current_docs_preserve_public_package_and_evidence_boundaries() -> None:
    readme = " ".join(_project_text("README.md").split())
    current_docs = "\n".join(_project_text(path) for path in PUBLIC_DOCS)

    for required in (
        "official collection of ready-to-run Millrace workflows",
        "`millrace.plus.official`",
        "source and package are on the v0.22.3 release line",
        "installed resource root is `millrace_workflow_package`",
        "package data is non-executable",
        "A direct installation contains package metadata and data only",
        "does not transitively install `millrace-ai`",
        "does not copy them into Codex, Claude Code",
        "no daemon, CLI, runner, provider",
        "does not import `millrace_plus` modules or execute package code",
        "See [Validation](docs/public-validation.md)",
    ):
        assert required in readme

    assert "Source-Conformance Checks" not in current_docs


def test_public_workflow_boundary_exposes_only_the_verdict_arbiter_contract() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "millrace_workflow_package/manifest.json").read_text()
    )
    for workflow in manifest["workflows"]:
        if workflow["workflow_id"] not in {"planning.lad", "lad.full"}:
            continue
        selected = workflow["selected_authority"]
        arbiter = next(
            stage
            for stage in selected["stage_kinds"]
            if stage["id"] == "lad_arbiter"
        )
        assert arbiter["artifact_schema_ids"] == ["planning.artifacts.verdict"]
        assert arbiter["output_queue_family_ids"] == ["verdict"]
        assert "planning.artifacts.rubric" not in {
            schema["id"] for schema in selected["artifact_schemas"]
        }
        assert "rubric" not in {
            queue["id"] for queue in selected["queue_families"]
        }
        assert "marathon-qa-audit" not in json.dumps(selected)

        asset_paths = {
            asset["asset_id"]: asset["package_path"]
            for asset in manifest["assets"]
        }
        entrypoint = (
            PROJECT_ROOT / "millrace_workflow_package" / asset_paths[
                "planning.entrypoints.lad_arbiter"
            ]
        ).read_text()
        skill = (
            PROJECT_ROOT / "millrace_workflow_package" / asset_paths[
                "planning.skills.arbiter_core"
            ]
        ).read_text()
        arbiter_text = (entrypoint + "\n" + skill).lower()
        assert "read-only" in arbiter_text
        assert "runtime-owned" in arbiter_text
        assert "new observations remain non-blocking" in arbiter_text


def test_public_release_text_has_no_staging_or_prepublication_claims() -> None:
    for public_doc in PUBLIC_DOCS:
        normalized_text = _normalized_text(_project_text(public_doc))
        for stale_phrase in STALE_PUBLIC_RELEASE_PHRASES:
            assert stale_phrase not in normalized_text, (
                public_doc,
                stale_phrase,
            )


def test_public_release_text_names_exact_initial_bundle_members() -> None:
    for public_release_doc in ("README.md", "docs/release.md"):
        release_text = _project_text(public_release_doc)
        normalized_release_text = _normalized_text(release_text)

        assert META_RELEASE_PIN in release_text
        for member_pin in BUNDLE_MEMBER_PINS:
            assert member_pin in release_text
        for versioning_rule in VERSIONING_RULES:
            assert _normalized_text(versioning_rule) in normalized_release_text


def test_dependency_policy_is_dependency_free_and_documented() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    readme = " ".join(_project_text("README.md").split())
    release_notes = _project_text("docs/release.md")

    assert pyproject["project"].get("dependencies", []) == []
    assert "dependencies = []" in _project_text("pyproject.toml")
    assert "A direct installation contains package metadata and data only" in readme
    assert "does not transitively install `millrace-ai`" in readme
    assert "| Runtime dependency | None |" in release_notes


def test_conformance_tests_are_ungated_and_use_public_module_roots() -> None:
    tests_root = PROJECT_ROOT / "tests"
    assert PROJECT_ROOT.is_dir()
    assert tests_root.is_dir()
    assert not (tests_root / "conftest.py").exists()
    assert not (tests_root / "support/internal_conformance_gate.py").exists()
    forbidden_literals = (
        "sys." + "path.insert",
        "MILLRACE_" + "RUNTIME_SOURCE",
        "MILLRACE_" + "LEGACY_ASSET_ROOT",
        "MILLRACE_PLUS_RUN_" + "INTERNAL_CONFORMANCE",
        "millrace" + ".testing",
        "dev/source/" + "millrace/src/",
        "planning." + "lad_review",
    )
    private_import = re.compile(r"from millrace\.[^\n]* import _")
    for test_path in tests_root.rglob("*.py"):
        relative_test_path = test_path.relative_to(PROJECT_ROOT).as_posix()
        text = test_path.read_text()
        for literal in forbidden_literals:
            if (
                literal == "millrace" + ".testing"
                and relative_test_path in NON_PUBLIC_REGRESSION_TESTS
            ):
                continue
            assert literal not in text, (test_path, literal)
        if relative_test_path not in NON_PUBLIC_REGRESSION_TESTS:
            assert private_import.search(text) is None, test_path


def test_public_ci_runs_clean_checkout_boundary_without_sibling_paths() -> None:
    workflow_path = PROJECT_ROOT / ".github" / "workflows" / "public-ci.yml"
    workflow = workflow_path.read_text()

    for required in (
        "env -u PYTHONPATH",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
        "tests/test_public_package_boundary.py",
        "uv build --out-dir /tmp/millrace-plus-build --force-pep517",
        "ruff check src tests",
        "git diff --check",
        "No millrace runtime package shipped",
        "millrace_plus/skills/",
    ):
        assert required in workflow
    for public_test in PUBLIC_TESTS:
        assert public_test in workflow
    assert "source/millrace-rewrite" not in workflow
    assert "sys." + "path.insert" not in workflow


def test_built_artifacts_have_durable_v022_public_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    build_env = os.environ.copy()
    build_env["PYTHONDONTWRITEBYTECODE"] = "1"
    build_env.pop("PYTHONPATH", None)
    subprocess.run(
        ["uv", "build", "--out-dir", str(tmp_path), "--clear"],
        cwd=PROJECT_ROOT,
        check=True,
        env=build_env,
    )
    wheel_path = next(tmp_path.glob("*.whl"))
    sdist_path = next(tmp_path.glob("*.tar.gz"))

    with zipfile.ZipFile(wheel_path) as wheel:
        wheel_metadata = wheel.read(
            next(
                name
                for name in wheel.namelist()
                if name.endswith(".dist-info/METADATA")
            )
        ).decode("utf-8")
        wheel_manifest = json.loads(
            wheel.read("millrace_workflow_package/manifest.json")
        )
        wheel.extractall(tmp_path / "site")

    with tarfile.open(sdist_path) as sdist:
        sdist_metadata = sdist.extractfile(
            next(
                member
                for member in sdist.getmembers()
                if member.name.endswith("/PKG-INFO")
            )
        )
        sdist_manifest = sdist.extractfile(
            next(
                member
                for member in sdist.getmembers()
                if member.name.endswith("/millrace_workflow_package/manifest.json")
            )
        )
        assert sdist_metadata is not None
        assert sdist_manifest is not None
        sdist_metadata_text = sdist_metadata.read().decode("utf-8")
        sdist_manifest_data = json.loads(sdist_manifest.read())

    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    module_name = "millrace_plus"
    had_previous_module = module_name in sys.modules
    previous_module = sys.modules.get(module_name)
    try:
        sys.modules.pop(module_name, None)
        installed_module = importlib.import_module(module_name)

        for metadata_text in (wheel_metadata, sdist_metadata_text):
            assert f"Version: {RELEASE_IDENTITY}\n" in metadata_text
            assert META_RELEASE_PIN in metadata_text
            for member_pin in BUNDLE_MEMBER_PINS:
                assert member_pin in metadata_text

            normalized_metadata = _normalized_text(metadata_text)
            for versioning_rule in VERSIONING_RULES:
                assert _normalized_text(versioning_rule) in normalized_metadata
            for stale_phrase in STALE_PUBLIC_RELEASE_PHRASES:
                assert stale_phrase not in normalized_metadata

        assert wheel_manifest["package"]["package_version"] == RELEASE_IDENTITY
        assert sdist_manifest_data["package"]["package_version"] == RELEASE_IDENTITY
        assert installed_module.__version__ == RELEASE_IDENTITY
        assert importlib.reload(installed_module).__version__ == RELEASE_IDENTITY
    finally:
        sys.modules.pop(module_name, None)
        if had_previous_module:
            sys.modules[module_name] = previous_module


def test_all_conformance_modules_are_in_normal_pytest_collection() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert pyproject["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]
    assert pyproject["tool"]["pytest"]["ini_options"].get("markers") == [
        "public: standalone public package tests"
    ]
