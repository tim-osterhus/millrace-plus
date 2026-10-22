from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tarfile
import tomllib
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / "src" / "millrace_plus" / "skills"
RELEASE_IDENTITY = "0.23.1"
PACKAGE_DOCS = (
    "docs/authoring.md",
    "docs/public-validation.md",
    "docs/release.md",
    "docs/workflows.md",
)


def test_millrace_plus_import_is_metadata_only(monkeypatch) -> None:
    import importlib.metadata
    import importlib.resources

    sys.modules.pop("millrace", None)
    sys.modules.pop("millrace_plus", None)

    def forbidden_entry_points(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("millrace_plus import must not load entry points")

    def forbidden_resource_files(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("millrace_plus import must not read package resources")

    monkeypatch.setattr(importlib.metadata, "entry_points", forbidden_entry_points)
    monkeypatch.setattr(importlib.resources, "files", forbidden_resource_files)
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "src"))

    module = import_module("millrace_plus")

    assert module.__version__ == RELEASE_IDENTITY
    assert module.__all__ == ("__version__",)
    assert "millrace" not in sys.modules
    assert all(name.startswith("_") for name in vars(module) if name != "annotations")


def test_pyproject_declares_narrow_distribution_metadata() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert pyproject["build-system"]["build-backend"] == "hatchling.build"
    assert pyproject["project"]["name"] == "millrace-plus"
    assert pyproject["project"]["version"] == RELEASE_IDENTITY
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["project"].get("dependencies", []) == []
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/millrace_plus"
    ]
    assert (
        "force-include" not in pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]
    )
    assert (
        "docs/*.md"
        in pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    )

    project_metadata = pyproject["project"]
    # v0.23 successor: assert "scripts" not in project_metadata
    #   -> exact console script table
    assert project_metadata["scripts"] == {
        "millrace-plus": "millrace_plus.router.cli:cli",
    }
    # v0.23 successor: no optional-dependencies table -> exact router extra
    assert project_metadata["optional-dependencies"] == {
        "router": ["millrace-ai>=0.23.0,<0.24.0"],
    }
    assert "gui-scripts" not in project_metadata
    assert "entry-points" not in project_metadata
    assert "entry-points" not in pyproject.get("project", {})


def test_package_version_matches_release_identity() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    module = import_module("millrace_plus")

    assert pyproject["project"]["version"] == RELEASE_IDENTITY
    assert module.__version__ == RELEASE_IDENTITY


def test_sdist_includes_package_docs_and_skills(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    subprocess.run(
        [
            "uv",
            "build",
            "--sdist",
            "--out-dir",
            str(tmp_path),
            "--clear",
            # v0.23 successor: uv build argv without --offline -> with --offline
            "--offline",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )
    sdists = sorted(tmp_path.glob("*.tar.gz"))
    assert len(sdists) == 1

    with tarfile.open(sdists[0]) as archive:
        names = set(archive.getnames())
        skill_payloads = {}
        for skill_path in SKILL_ROOT.rglob("*"):
            if skill_path.is_file() and not skill_path.is_symlink():
                relative_path = skill_path.relative_to(PROJECT_ROOT).as_posix()
                archive_path = next(
                    name for name in names if name.endswith(f"/{relative_path}")
                )
                member = archive.extractfile(archive_path)
                assert member is not None
                skill_payloads[relative_path] = member.read()

    assert any(name.endswith("/.github/workflows/public-ci.yml") for name in names)
    for doc_path in PACKAGE_DOCS:
        assert any(name.endswith(f"/{doc_path}") for name in names)
    for skill_path in SKILL_ROOT.rglob("*"):
        if skill_path.is_file() and not skill_path.is_symlink():
            relative_path = skill_path.relative_to(PROJECT_ROOT).as_posix()
            assert skill_payloads[relative_path] == skill_path.read_bytes()


def test_source_tree_does_not_shadow_runtime_or_import_private_runtime_code() -> None:
    assert not (PROJECT_ROOT / "src" / "millrace").exists()

    source_files = list((PROJECT_ROOT / "src").rglob("*.py"))
    source_text = "\n".join(path.read_text() for path in source_files)

    # v0.23 successor: assert "import millrace" / "from millrace." not in source_text
    #   -> AST allowlist: no millrace imports outside router; inside router only
    #      millrace.router.{execution,control,cli}, no private "from ... import _"
    router_root = PROJECT_ROOT / "src" / "millrace_plus" / "router"
    allowed_router_core = {
        "millrace.router.execution",
        "millrace.router.control",
        "millrace.router.cli",
    }
    for path in source_files:
        text = path.read_text()
        imported: list[str] = []
        for node in ast.walk(ast.parse(text, filename=str(path))):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == "millrace.router":
                    imported.extend(
                        f"millrace.router.{alias.name}" for alias in node.names
                    )
                else:
                    imported.append(node.module)
        core_imports = {name for name in imported if name.split(".")[0] == "millrace"}
        if router_root in path.parents:
            assert core_imports <= allowed_router_core, path
            for forbidden_text in (
                "millrace.router.store",
                "millrace.router.records",
                "sqlite3",
            ):
                assert forbidden_text not in text, (path, forbidden_text)
            assert not re.search(
                r"from millrace\.router(?:\.[\w.]+)?\s+import\s+_", text
            ), path
        else:
            assert not core_imports, path
    for forbidden in (
        "millrace.kernel",
        "millrace.substrate._",
        "millrace.operator.package_doctor",
        "workflow_package_registry",
        "load_entry_point",
    ):
        assert forbidden not in source_text


def test_official_package_data_declares_selected_vendor_selection_assets() -> None:
    """P5 successor: ordinary new templates replace legacy vendor inventory."""
    root = PROJECT_ROOT / "src/millrace_plus/workflows"
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == [
        "document-review",
        "software-change",
    ]
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        graph = json.loads((folder / "workflow.json").read_text())
        assert graph["format"] == "millrace-router/v3"
        assert set(graph["planes"]) == {"main"}
        assert graph["planes"]["main"]["workspace"] == "."
        assert graph["limits"] == {"max_invocations": 16, "max_elapsed_seconds": 86400}
        assert graph["defaults"]["timeout_seconds"] == 1800
        for asset in graph["control_files"]:
            assert (folder / asset).is_file(), asset
        assert all("skill" not in name for name in graph["control_files"])


def test_plus_candidate_targets_reviewed_core_minimum_and_defers_experiment() -> None:
    """Candidate equality is insufficient; the execution contract remains required."""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["optional-dependencies"] == {
        "router": ["millrace-ai>=0.23.0,<0.24.0"]
    }
    assert (
        "millrace-router-execution/v2"
        in (PROJECT_ROOT / "src/millrace_plus/router/__init__.py").read_text()
    )
    for folder in (PROJECT_ROOT / "src/millrace_plus/workflows").iterdir():
        if folder.is_dir():
            profile = json.loads((folder / "profiles/claude.json").read_text())
            assert profile == {
                "format": "millrace-claude-print-profile/v1",
                "claude_version": "2.1.296",
            }
