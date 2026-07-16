from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TESTS = (
    "tests/test_package_metadata.py",
    "tests/test_manifest_authoring_policy.py",
    "tests/test_official_package_layout_plan.py",
    "tests/test_workflow_package_manifest.py",
    "tests/test_workflow_package_installed_smoke.py",
    "tests/test_public_package_boundary.py",
    "tests/test_agent_skill_assets.py",
)
PUBLIC_DOCS = (
    "README.md",
    "docs/workflows.md",
    "docs/authoring.md",
    "docs/manifest-authoring-policy.md",
    "docs/release.md",
    "docs/public-validation.md",
)
INTERNAL_CONFORMANCE_ENV = "MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE"
RUNTIME_SOURCE_ENV = "MILLRACE_RUNTIME_SOURCE"
LEGACY_ASSET_ENV = "MILLRACE_LEGACY_ASSET_ROOT"
HIDDEN_REWRITE_PYTHONPATH = "PYTHONPATH=../../source/" "millrace-rewrite/src"
LEGACY_ASSET_PATH = "dev/source/millrace/src/" "millrace_ai/assets"


def _project_text(path: str) -> str:
    return (PROJECT_ROOT / path).read_text()


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

    public_text = "\n".join(
        _project_text(path) for path in (*PUBLIC_DOCS, *PUBLIC_TESTS)
    )
    assert HIDDEN_REWRITE_PYTHONPATH not in public_text
    assert LEGACY_ASSET_PATH not in public_text


def test_current_docs_preserve_public_package_and_evidence_boundaries() -> None:
    readme = " ".join(_project_text("README.md").split())
    current_docs = "\n".join(_project_text(path) for path in PUBLIC_DOCS)

    for required in (
        "official collection of ready-to-run Millrace workflows",
        "`millrace.plus.official`",
        "staging package version `0.0.0`",
        "installed resource root is `millrace_workflow_package`",
        "package data is non-executable",
        "not yet published",
        "A direct installation contains package metadata and data only",
        "does not transitively install `millrace-ai`",
        "does not copy them into Codex, Claude Code",
        "no daemon, CLI, runner, provider",
        "does not import `millrace_plus` modules or execute package code",
        "See [Validation](docs/public-validation.md)",
    ):
        assert required in readme

    for required in (
        INTERNAL_CONFORMANCE_ENV,
        RUNTIME_SOURCE_ENV,
        LEGACY_ASSET_ENV,
    ):
        assert required in current_docs


def test_dependency_policy_is_dependency_free_and_documented() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    readme = " ".join(_project_text("README.md").split())
    release_notes = _project_text("docs/release.md")

    assert pyproject["project"].get("dependencies", []) == []
    assert "dependencies = []" in _project_text("pyproject.toml")
    assert (
        "A direct installation contains package metadata and data only"
        in readme
    )
    assert "does not transitively install `millrace-ai`" in readme
    assert "| Runtime dependency | None |" in release_notes


def test_internal_conformance_tests_are_explicitly_gated() -> None:
    conftest = _project_text("tests/conftest.py")
    for required in (
        INTERNAL_CONFORMANCE_ENV,
        RUNTIME_SOURCE_ENV,
        LEGACY_ASSET_ENV,
        "test_simple_loop_official_package.py",
        "test_plus_0002_9_final_conformance.py",
    ):
        assert required in conftest


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
    assert "source/" "millrace-rewrite" not in workflow
    assert LEGACY_ASSET_PATH not in workflow


def test_internal_conformance_direct_selection_skips_without_runtime_import() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop(INTERNAL_CONFORMANCE_ENV, None)
    env.pop(RUNTIME_SOURCE_ENV, None)
    env.pop(LEGACY_ASSET_ENV, None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_plus_0002_9_final_conformance.py",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    combined_output = result.stdout + result.stderr
    assert result.returncode in {0, 5}
    assert "skipped" in combined_output
    assert "ModuleNotFoundError" not in combined_output
