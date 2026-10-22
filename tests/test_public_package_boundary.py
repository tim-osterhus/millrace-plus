"""P5 successors to the historical package-surface tests (stable test IDs)."""

from __future__ import annotations

import ast
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
    "tests/test_public_package_boundary.py",
    "tests/test_agent_skill_assets.py",
    "tests/test_public_test_support_boundary.py",
    "tests/router/test_catalog.py",
)
NON_PUBLIC_REGRESSION_TESTS = {
    "tests/test_lad_execution_official_package.py",
    "tests/test_lad_planning_official_package.py",
    "tests/test_simple_loop_governed_context_lifecycle.py",
    "tests/test_workflow_package_manifest.py",
}
ACTIVE = ["tests/router", *PUBLIC_TESTS[:4]]


def _text(path: str) -> str:
    return (PROJECT_ROOT / path).read_text()


def test_public_validation_selection_has_no_runtime_or_legacy_path_imports() -> None:
    for name in PUBLIC_TESTS:
        for node in ast.walk(ast.parse(_text(name))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("millrace.")
            if isinstance(node, ast.Import):
                assert not any(
                    a.name == "millrace" or a.name.startswith("millrace.")
                    for a in node.names
                )


def test_current_docs_preserve_public_package_and_evidence_boundaries() -> None:
    readme = _text("README.md")
    assert "Millrace 0.23" in readme
    assert "millrace_plus/workflows/" in readme
    assert "resource/selection API is retired" in readme
    assert "without a Plus catalog service" in readme
    assert "historical" in _text("docs/workflows.md").lower()


def test_public_workflow_boundary_exposes_only_the_verdict_arbiter_contract() -> None:
    """Successor: workflow-owned outcomes replace the old domain Arbiter schema."""
    for path in (PROJECT_ROOT / "src/millrace_plus/workflows").glob("*/workflow.json"):
        graph = json.loads(path.read_text())
        assert "selected_authority" not in graph
        for node in graph["planes"]["main"]["nodes"].values():
            assert node["result"] == "json-v1"
            assert set(node["outcomes"]) == set(node["on"])
            assert "on_error" in node and "on_unknown" in node


def test_public_release_text_has_no_staging_or_prepublication_claims() -> None:
    """Successor: retain the truthful unpublished candidate boundary."""
    for name in ("README.md", "docs/release.md"):
        text = _text(name).lower()
        assert "0.23.0" in text and "unpublished" not in text
        assert "release" in text and "equal version" in text
        assert "0.0.0" not in text


def test_public_release_text_identifies_exact_bundle_and_independent_members() -> None:
    """Successor: exact source-bound component pairing replaces old bundle pins."""
    for name in ("README.md", "docs/release.md"):
        text = _text(name)
        assert "millrace-ai==0.23.0" in text
        assert "matched" in text and "publication" in text
    assert "millrace-router-execution/v2" in _text("README.md")


def test_dependency_policy_is_dependency_free_and_documented() -> None:
    project = tomllib.loads(_text("pyproject.toml"))["project"]
    assert project["dependencies"] == []
    assert project["optional-dependencies"] == {
        "router": ["millrace-ai>=0.23.0,<0.24.0"]
    }
    assert "does not transitively install" in _text("README.md")
    assert "| Runtime dependency | None |" in _text("docs/release.md")


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
    workflow = _text(".github/workflows/public-ci.yml")
    for name in PUBLIC_TESTS:
        assert name in workflow
    assert "env -u PYTHONPATH" in workflow
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" in workflow
    assert "millrace_plus/workflows/" in workflow
    assert "not any(name.startswith('millrace_workflow_package/')" in workflow
    assert "dev/source/" not in workflow


def test_built_artifacts_have_durable_v022_public_text(
    tmp_path: Path, monkeypatch
) -> None:
    """P5 payload successor: one graph-v2 tree, no v0.22 resource API."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        ["uv", "build", "--offline", "--out-dir", str(tmp_path)],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        names = wheel.namelist()
        assert not any(
            n.startswith("millrace_workflow_package/") or n.startswith("millrace/")
            for n in names
        )
        for template in ("document-review", "software-change"):
            graph = json.loads(
                wheel.read(f"millrace_plus/workflows/{template}/workflow.json")
            )
            for asset in graph["control_files"]:
                assert f"millrace_plus/workflows/{template}/{asset}" in names
        metadata = wheel.read(
            next(n for n in names if n.endswith(".dist-info/METADATA"))
        ).decode()
        assert "Version: 0.23.0" in metadata and "unpublished" not in metadata.lower()
    with tarfile.open(next(tmp_path.glob("*.tar.gz"))) as archive:
        names = archive.getnames()
        assert not any("/millrace_workflow_package/" in n for n in names)
        assert any(
            n.endswith("/src/millrace_plus/workflows/document-review/workflow.json")
            for n in names
        )
        assert any(n.endswith("/docs/catalog.md") for n in names)


def test_all_conformance_modules_are_in_normal_pytest_collection() -> None:
    """Successor: current tests active, historical manifest/release gates explicit."""
    settings = tomllib.loads(_text("pyproject.toml"))["tool"]["pytest"]["ini_options"]
    assert settings["testpaths"] == ACTIVE
    assert "Historical" in _text("tests/README.md") or "historical" in _text(
        "tests/README.md"
    )
    assert "test_manifest_authoring_policy.py" not in settings["testpaths"]


def test_installed_catalog_without_core_from_public_selection(tmp_path: Path) -> None:
    """The standalone CI selection proves installed catalog use without Core."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    artifacts = tmp_path / "artifacts"
    subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(artifacts)],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    venv = tmp_path / "standalone"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)],
        env=env,
        check=True,
        capture_output=True,
    )
    python = venv / "bin/python"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--no-cache",
            "--python",
            str(python),
            str(next(artifacts.glob("*.whl"))),
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    isolated = {
        "PATH": str(venv / "bin"),
        "HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert not outside.is_relative_to(PROJECT_ROOT)
    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import importlib.util; "
            "assert importlib.util.find_spec('millrace') is None; "
            "from millrace_plus import catalog; "
            "from pathlib import Path; import sys; "
            "assert Path(catalog.__file__).is_relative_to(Path(sys.prefix))",
        ],
        cwd=outside,
        env=isolated,
        check=True,
        capture_output=True,
    )
    assert probe.returncode == 0
    console = str(venv / "bin/millrace-plus")
    listed = subprocess.run(
        [console, "catalog", "list"],
        cwd=outside,
        env=isolated,
        check=True,
        capture_output=True,
        text=True,
    )
    selectors = [v["selector"] for v in json.loads(listed.stdout)["templates"]]
    assert selectors == ["bundled:document-review", "bundled:software-change"]
    for selector in selectors:
        destination = outside / selector.split(":")[1]
        subprocess.run(
            [console, "catalog", "init", selector, str(destination)],
            cwd=outside,
            env=isolated,
            check=True,
            capture_output=True,
        )
        assert json.loads((destination / "workflow.json").read_text())["format"] == (
            "millrace-router/v3"
        )
