"""User catalog defaults persist independently of package files and Core."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from millrace_plus import catalog
from millrace_plus.router import cli


@pytest.fixture(autouse=True)
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _flow(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "workflow.json").write_text('{"format":"millrace-router/v3"}')
    return path


def test_cli_default_survives_fresh_processes_and_catalog_operations(
    tmp_path: Path, user_home: Path
) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    override = tmp_path / "override"
    _flow(saved / "saved-flow")
    _flow(override / "override-flow")
    source = _flow(tmp_path / "source")
    env = dict(os.environ, PYTHONPATH=str(Path(catalog.__file__).parents[1]))

    def call(*args: str) -> dict[str, object]:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from millrace_plus.router.cli import cli; cli()",
                "catalog",
                *args,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stderr == ""
        return json.loads(result.stdout)

    settings = user_home / ".config/millrace-plus/catalog.json"
    assert call("home", "show")["source"] == "default"
    assert not settings.exists()
    assert call("home", "set", str(saved))["catalog_home"] == str(saved)
    before = settings.read_bytes()
    assert call("home", "show")["source"] == "saved"
    listed = call("list")["templates"]
    assert any(row["selector"] == "local:saved-flow" for row in listed)
    listed = call("list", "--catalog-home", str(override))["templates"]
    assert any(row["selector"] == "local:override-flow" for row in listed)
    assert not any(row["selector"] == "local:saved-flow" for row in listed)
    call("init", "local:saved-flow", str(tmp_path / "initialized"))
    call("copy", str(source), str(saved / "copied"))
    assert (saved / "copied/workflow.json").is_file()
    assert settings.read_bytes() == before
    assert call("home", "reset")["source"] == "default"
    assert call("home", "show")["catalog_home"] == str(catalog.default_catalog())
    assert not settings.exists()
    assert (saved / "saved-flow/workflow.json").is_file()
    assert not any(row["source"] == "local" for row in call("list")["templates"])
    assert call("home", "reset")["source"] == "default"


def test_api_override_and_all_catalog_roots_remain_protected(tmp_path: Path) -> None:
    saved = tmp_path / "saved"
    override = tmp_path / "override"
    _flow(saved / "flow")
    _flow(override / "flow")
    _flow(catalog.default_catalog() / "flow")
    catalog.set_catalog_home(saved)
    assert next(v for v in catalog.list_templates() if v["source"] == "local")[
        "path"
    ] == str(saved / "flow")
    assert next(
        v
        for v in catalog.list_templates(catalog_home=override)
        if v["source"] == "local"
    )["path"] == str(override / "flow")
    roots = (catalog.bundled_root(), catalog.default_catalog(), saved, override)
    for root in roots:
        with pytest.raises(catalog.CatalogError) as error:
            catalog.check_ownership(
                root / "flow/workflow.json", tmp_path / "project", catalog_home=override
            )
        assert error.value.code == "catalog_ownership"
    with pytest.raises(catalog.CatalogError) as error:
        catalog.init_template("local:flow", saved / "other", catalog_home=override)
    assert error.value.code == "forbidden_destination"
    assert not (saved / "other").exists()


def test_invalid_set_and_failed_save_preserve_previous_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved, replacement = tmp_path / "saved", tmp_path / "replacement"
    saved.mkdir()
    replacement.mkdir()
    catalog.set_catalog_home(saved)
    settings = catalog.catalog_settings_path()
    before = settings.read_bytes()
    for invalid in (tmp_path / "missing", settings):
        with pytest.raises(catalog.CatalogError) as error:
            catalog.set_catalog_home(invalid)
        assert error.value.code == "invalid_catalog_root"
        assert settings.read_bytes() == before
    entries = set(settings.parent.iterdir())

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("owned test write failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.set_catalog_home(replacement)
    assert error.value.code == "io_error"
    assert settings.read_bytes() == before
    assert set(settings.parent.iterdir()) == entries


def test_cli_settings_error_is_read_only_and_reset_repairs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = catalog.catalog_settings_path()
    settings.parent.mkdir(parents=True)
    settings.write_text('{"catalog_home":"relative"}')
    before = settings.read_bytes()
    monkeypatch.setattr(sys, "argv", ["millrace-plus", "catalog", "home", "show"])
    with pytest.raises(SystemExit) as error:
        cli.cli()
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["code"] == "invalid_catalog_settings"
    assert settings.read_bytes() == before
    assert catalog.reset_catalog_home()["source"] == "default"
    assert not settings.exists()


def test_setting_resolves_relative_path_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = tmp_path / "saved"
    saved.mkdir()
    monkeypatch.chdir(tmp_path)
    catalog.set_catalog_home(Path("saved"))
    monkeypatch.chdir(tmp_path.parent)
    assert catalog.catalog_home_status()["catalog_home"] == str(saved)
