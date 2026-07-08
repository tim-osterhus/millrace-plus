from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tomllib
from importlib import import_module
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_REVIEW_DOCS = (
    "docs/PLUS-0002C-implementation-review.md",
    "docs/PLUS-0002D-implementation-review.md",
    "docs/PLUS-0002E-implementation-review.md",
    "docs/PLUS-0002F-implementation-review.md",
    "docs/PLUS-0002.9-implementation-review.md",
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

    assert module.__version__ == "0.0.0"
    assert module.__all__ == ("__version__",)
    assert "millrace" not in sys.modules
    assert all(name.startswith("_") for name in vars(module) if name != "annotations")


def test_pyproject_declares_narrow_distribution_metadata() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())

    assert pyproject["build-system"]["build-backend"] == "hatchling.build"
    assert pyproject["project"]["name"] == "millrace-plus"
    assert pyproject["project"]["version"] == "0.0.0"
    assert pyproject["project"]["requires-python"] == ">=3.11"
    assert pyproject["project"].get("dependencies", []) == []
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/millrace_plus"
    ]
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ] == {"millrace_workflow_package": "millrace_workflow_package"}
    assert "docs/*.md" in pyproject["tool"]["hatch"]["build"]["targets"][
        "sdist"
    ]["include"]

    project_metadata = pyproject["project"]
    assert "scripts" not in project_metadata
    assert "gui-scripts" not in project_metadata
    assert "entry-points" not in project_metadata
    assert "entry-points" not in pyproject.get("project", {})


def test_sdist_includes_package_local_review_docs(tmp_path: Path) -> None:
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
        ],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )
    sdists = sorted(tmp_path.glob("*.tar.gz"))
    assert len(sdists) == 1

    with tarfile.open(sdists[0]) as archive:
        names = set(archive.getnames())

    for doc_path in IMPLEMENTATION_REVIEW_DOCS:
        assert any(name.endswith(f"/{doc_path}") for name in names)


def test_source_tree_does_not_shadow_runtime_or_import_private_runtime_code() -> None:
    assert not (PROJECT_ROOT / "src" / "millrace").exists()

    source_files = list((PROJECT_ROOT / "src").rglob("*.py"))
    source_text = "\n".join(path.read_text() for path in source_files)

    assert "import millrace" not in source_text
    assert "from millrace." not in source_text
    for forbidden in (
        "millrace.kernel",
        "millrace.substrate._",
        "millrace.operator.package_doctor",
        "workflow_package_registry",
        "load_entry_point",
    ):
        assert forbidden not in source_text


def test_official_package_data_declares_asset_free_vendor_selection() -> None:
    manifest_path = PROJECT_ROOT / "millrace_workflow_package" / "manifest.json"
    package_data_text = ""
    package_root = PROJECT_ROOT / "millrace_workflow_package"
    for path in (manifest_path, *package_root.rglob("*.md")):
        package_data_text += path.read_text()

    assert "vendor_selection" in package_data_text
    assert not (package_root / "assets" / "workflows" / "vendor_selection").exists()
