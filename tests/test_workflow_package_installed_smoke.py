from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
DIST_NAME = "millrace-plus"
IMPORT_PACKAGE = "millrace_plus"
RESOURCE_ROOT = "millrace_workflow_package"
SKILL_ROOT = PROJECT_ROOT / "src" / "millrace_plus" / "skills"


def _member_paths() -> tuple[str, ...]:
    import json

    manifest = json.loads((PACKAGE_ROOT / "manifest.json").read_text())
    return tuple(
        sorted(
            (
                "manifest.json",
                *(
                    str(asset["package_path"])
                    for asset in manifest["assets"]
                ),
            )
        )
    )


def _skill_member_paths() -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(SKILL_ROOT).as_posix()
            for path in SKILL_ROOT.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    )


def _build_wheel(tmp_path: Path) -> Path:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(tmp_path),
            "--clear",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        env=env,
    )
    wheels = sorted(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _install_wheel(wheel_path: Path, target: Path) -> None:
    target.mkdir(parents=True)
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(target)


def _installed_distribution(target: Path) -> importlib.metadata.Distribution:
    distributions = [
        distribution
        for distribution in importlib.metadata.distributions(path=[str(target)])
        if distribution.metadata["Name"] == DIST_NAME
    ]
    assert len(distributions) == 1
    return distributions[0]


def test_built_wheel_contains_official_package_data_and_no_runtime_package(
    tmp_path: Path,
) -> None:
    wheel_path = _build_wheel(tmp_path)

    with zipfile.ZipFile(wheel_path) as wheel:
        names = set(wheel.namelist())
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = wheel.read(metadata_name).decode("utf-8")
        skill_payloads = {
            member_path: wheel.read(f"{IMPORT_PACKAGE}/skills/{member_path}")
            for member_path in _skill_member_paths()
        }

    assert "Name: millrace-plus\n" in metadata
    assert "Version: 0.22.0\n" in metadata
    assert "Requires-Dist:" not in metadata
    for member_path in _member_paths():
        assert f"{RESOURCE_ROOT}/{member_path}" in names
    assert f"{IMPORT_PACKAGE}/__init__.py" in names
    assert f"{IMPORT_PACKAGE}/py.typed" in names
    for member_path in _skill_member_paths():
        archive_path = f"{IMPORT_PACKAGE}/skills/{member_path}"
        assert archive_path in names
        assert skill_payloads[member_path] == (SKILL_ROOT / member_path).read_bytes()
    assert not any(name.startswith("millrace/") for name in names)
    assert not any(name.endswith("entry_points.txt") for name in names)
    assert not any(
        name.startswith(f"{RESOURCE_ROOT}/") and name.endswith((".py", ".pyc"))
        for name in names
    )


def test_installed_wheel_exposes_package_data_without_importing_package_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel_path = _build_wheel(tmp_path / "dist")
    target = tmp_path / "site"
    _install_wheel(wheel_path, target)
    monkeypatch.syspath_prepend(str(target))
    sys.modules.pop(IMPORT_PACKAGE, None)

    distribution = _installed_distribution(target)
    distribution_files = {str(path) for path in distribution.files or ()}

    for member_path in _member_paths():
        installed_path = f"{RESOURCE_ROOT}/{member_path}"
        assert installed_path in distribution_files
        assert (target / installed_path).read_bytes() == (
            PACKAGE_ROOT / member_path
        ).read_bytes()
    for member_path in _skill_member_paths():
        installed_path = f"{IMPORT_PACKAGE}/skills/{member_path}"
        assert installed_path in distribution_files
        assert (target / installed_path).read_bytes() == (
            SKILL_ROOT / member_path
        ).read_bytes()
    assert IMPORT_PACKAGE not in sys.modules


def test_installed_wheel_metadata_is_direct_install_data_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    wheel_path = _build_wheel(tmp_path / "dist")
    target = tmp_path / "site"
    _install_wheel(wheel_path, target)
    monkeypatch.syspath_prepend(str(target))

    metadata = _installed_distribution(target).metadata

    assert metadata["Name"] == DIST_NAME
    assert metadata["Version"] == "0.22.0"
    assert "Requires-Dist" not in metadata
