"""Pure filesystem proofs; these tests must also run without Core installed."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from millrace_plus import catalog


def _flow(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "workflow.json").write_text(
        json.dumps({"format": "millrace-router/v3", "control_files": ["step.py"]})
    )
    (path / "step.py").write_text("original\n")
    (path / "step.py").chmod(0o755)
    return path


def test_local_catalog_list_init_and_independent_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    local = tmp_path / "catalog"
    source = _flow(bundled / "same")
    _flow(local / "same")
    monkeypatch.setattr(catalog, "bundled_root", lambda: bundled)
    assert [v["selector"] for v in catalog.list_templates(catalog_home=local)] == [
        "bundled:same",
        "local:same",
    ]
    first, second = tmp_path / "first", tmp_path / "second"
    catalog.init_template("bundled:same", first, catalog_home=local)
    catalog.init_template("local:same", second, catalog_home=local)
    (first / "step.py").write_text("edited\n")
    assert (
        (source / "step.py").read_text()
        == (second / "step.py").read_text()
        == "original\n"
    )
    assert (second / "step.py").stat().st_mode & 0o111
    catalog.copy_workflow(first, local / "saved", catalog_home=local)
    assert (local / "saved" / "step.py").read_text() == "edited\n"
    assert not (first / "step.py").samefile(local / "saved" / "step.py")
    for selector in ("same", "local:../same", "local:unknown"):
        with pytest.raises(catalog.CatalogError):
            catalog.init_template(selector, tmp_path / "bad", catalog_home=local)


def test_copy_path_refusals_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _flow(tmp_path / "source")
    local = tmp_path / "catalog"
    local.mkdir()
    dest = tmp_path / "dest"
    dest.symlink_to(tmp_path / "missing")
    with pytest.raises(catalog.CatalogError, match="exists"):
        catalog.copy_workflow(source, dest, catalog_home=local)
    assert dest.is_symlink()
    dest.unlink()
    for target in (source / "child", local, local / "nested" / "child"):
        with pytest.raises(catalog.CatalogError):
            catalog.copy_workflow(source, target, catalog_home=local)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, tmp_path / "absent" / "dest", catalog_home=local)
    assert error.value.code == "destination_parent_missing"
    linked = source / "linked"
    linked.symlink_to(source / "step.py")
    with pytest.raises(catalog.CatalogError):
        catalog.copy_workflow(source, dest, catalog_home=local)
    assert not dest.exists()
    linked.unlink()
    os.mkfifo(source / "pipe")
    with pytest.raises(catalog.CatalogError):
        catalog.copy_workflow(source, dest, catalog_home=local)
    (source / "pipe").unlink()
    (source / "__pycache__").symlink_to(tmp_path / "absent")
    catalog.copy_workflow(source, dest, catalog_home=local)
    assert not (dest / "__pycache__").exists()
    import shutil

    shutil.rmtree(dest)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("owned test I/O failure")

    monkeypatch.setattr(catalog.shutil, "copy2", fail)
    with pytest.raises(catalog.CatalogError):
        catalog.copy_workflow(source, dest, catalog_home=local)
    assert not dest.exists() and source.is_dir()


@pytest.mark.parametrize(
    "raw",
    [
        '{"format":"millrace-router/v3","format":"old"}',
        '{"format":"millrace-router/v3","n":NaN}',
        '{"format":"old"}',
        '{"format":"millrace-router/v2"}',
        '{"format":"millrace-router/v3","control_files":["__pycache__/x"]}',
    ],
)
def test_invalid_graph_marker_refuses_before_destination(
    tmp_path: Path, raw: str
) -> None:
    source = _flow(tmp_path / "source")
    (source / "workflow.json").write_text(raw)
    with pytest.raises(catalog.CatalogError):
        catalog.copy_workflow(
            source, tmp_path / "copy", catalog_home=tmp_path / "catalog"
        )
    assert not (tmp_path / "copy").exists()


def test_invalid_list_entry_fails_whole_list(tmp_path: Path) -> None:
    _flow(tmp_path / "good")
    (tmp_path / "linked").symlink_to(tmp_path / "good", target_is_directory=True)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.list_templates(catalog_home=tmp_path)
    assert error.value.code == "invalid_template" and "linked" in str(error.value)


def test_catalog_override_never_permits_writing_bundled_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _flow(tmp_path / "source")
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr(catalog, "bundled_root", lambda: bundled)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, bundled / "new", catalog_home=bundled)
    assert error.value.code == "forbidden_destination"
    assert not (bundled / "new").exists()


def test_default_catalog_alias_and_hardlink_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = tmp_path / "default"
    source = _flow(default / "source")
    override = tmp_path / "override"
    override.mkdir()
    monkeypatch.setattr(catalog, "default_catalog", lambda: default)
    alias = tmp_path / "alias"
    alias.symlink_to(default, target_is_directory=True)
    with pytest.raises(catalog.CatalogError):
        catalog.check_ownership(
            alias / "source/workflow.json", tmp_path / "project", catalog_home=override
        )
    os.link(source / "step.py", source / "hardlinked.py")
    dest = tmp_path / "copy"
    catalog.copy_workflow(source, dest, catalog_home=override)
    assert not (dest / "step.py").samefile(dest / "hardlinked.py")
    (dest / "hardlinked.py").write_text("independent")
    assert (
        (source / "step.py").read_text()
        == (dest / "step.py").read_text()
        == "original\n"
    )


def test_competing_destination_creation_is_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _flow(tmp_path / "source")
    dest = tmp_path / "dest"
    real_mkdir = Path.mkdir

    def compete(path: Path, *args: object, **kwargs: object) -> None:
        if path == dest:
            real_mkdir(path)
            (path / "other-owner").write_text("preserve")
            raise FileExistsError(str(path))
        real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", compete)
    with pytest.raises(catalog.CatalogError):
        catalog.copy_workflow(source, dest, catalog_home=tmp_path / "catalog")
    assert (dest / "other-owner").read_text() == "preserve"


@pytest.mark.parametrize("bad_location", ["source", "destination"])
def test_unknown_named_user_tilde_is_typed_api_and_cli_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bad_location: str,
) -> None:
    from millrace_plus.router import cli

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    source = _flow(tmp_path / "source")
    missing = Path("~millrace_user_that_does_not_exist_92f715c8/workflow")
    destination = tmp_path / "copy"
    if bad_location == "source":
        source = missing
    else:
        destination = missing
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, destination)
    assert error.value.code == "io_error"
    monkeypatch.setattr(
        __import__("sys"),
        "argv",
        ["millrace-plus", "catalog", "copy", str(source), str(destination)],
    )
    with pytest.raises(SystemExit) as exit_code:
        cli.cli()
    assert exit_code.value.code == 2
    assert json.loads(capsys.readouterr().err)["code"] == "io_error"
    assert not (tmp_path / "copy").exists()
