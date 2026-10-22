"""Pure filesystem proofs; these tests must also run without Core installed."""

from __future__ import annotations

import hashlib
import importlib.metadata
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


def test_workflow_fingerprint_matches_sorted_definition_records(
    tmp_path: Path,
) -> None:
    source = _flow(tmp_path / "source")
    nested = source / "café"
    nested.mkdir()
    executable = nested / "run.sh"
    executable.write_bytes(b"#!/bin/sh\necho ok\n")
    executable.chmod(0o751)
    (source / "__pycache__").mkdir()
    (source / "__pycache__/ignored.pyc").write_bytes(b"ignored")

    records = [
        {
            "path": path.relative_to(source).as_posix(),
            "executable_bits": path.stat().st_mode & 0o111,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(source.rglob("*"))
        if path.is_file() and "__pycache__" not in path.relative_to(source).parts
    ]
    encoded = json.dumps(
        records, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")

    assert catalog.workflow_fingerprint(source) == hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_workflow_fingerprint_refuses_nonregular_definition_files(
    tmp_path: Path, kind: str
) -> None:
    source = _flow(tmp_path / "source")
    special = source / "special"
    if kind == "symlink":
        special.symlink_to(source / "step.py")
    else:
        os.mkfifo(special)

    with pytest.raises(catalog.CatalogError):
        catalog.workflow_fingerprint(source)


@pytest.mark.parametrize("selector", ["bundled:same", "local:same"])
def test_init_template_reports_descriptive_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str
) -> None:
    bundled = _flow(tmp_path / "bundled/same")
    local = _flow(tmp_path / "catalog/same")
    monkeypatch.setattr(catalog, "bundled_root", lambda: bundled.parent)
    destination = tmp_path / "copy"

    result = catalog.init_template(selector, destination, catalog_home=local.parent)

    source = bundled if selector.startswith("bundled:") else local
    digest = catalog.workflow_fingerprint(source)
    assert result["source"] == str(source.resolve())
    assert result["destination"] == str(destination.resolve())
    try:
        expected_plus_version = importlib.metadata.version("millrace-plus")
    except importlib.metadata.PackageNotFoundError:
        from millrace_plus import __version__ as expected_plus_version

    assert result["provenance"] == {
        "template_id": selector,
        "plus_version": expected_plus_version,
        "sha256": digest,
    }
    assert catalog.workflow_fingerprint(destination) == digest
    assert not (destination / "provenance.json").exists()


def test_init_template_refuses_source_change_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _flow(tmp_path / "bundled/same")
    monkeypatch.setattr(catalog, "bundled_root", lambda: source.parent)
    destination = tmp_path / "copy"
    real_copy2 = catalog.shutil.copy2

    def mutate_after_graph_copy(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> str:
        copied = real_copy2(src, dst)
        if Path(src).name == "workflow.json":
            (source / "step.py").write_text("changed during copy\n")
        return copied

    monkeypatch.setattr(catalog.shutil, "copy2", mutate_after_graph_copy)
    with pytest.raises(catalog.CatalogError):
        catalog.init_template(
            "bundled:same", destination, catalog_home=tmp_path / "catalog"
        )

    assert (source / "step.py").read_text() == "changed during copy\n"
    assert not destination.exists()


def test_copy_path_refusals_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _flow(tmp_path / "source")
    source_files = {
        name: (source / name).read_bytes() for name in ("workflow.json", "step.py")
    }
    local = tmp_path / "catalog"
    local.mkdir()
    local_sentinel = local / "operator-owned.txt"
    local_sentinel.write_bytes(b"keep this catalog entry")
    dest = tmp_path / "dest"
    dangling_target = tmp_path / "missing"
    dest.symlink_to(dangling_target)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, dest, catalog_home=local)
    assert error.value.code == "destination_exists"
    assert dest.is_symlink() and not dangling_target.exists()
    dest.unlink()

    forbidden_paths = (
        (source / "child", "forbidden_destination"),
        (local, "destination_exists"),
        (local / "nested" / "child", "forbidden_destination"),
    )
    for target, expected_code in forbidden_paths:
        with pytest.raises(catalog.CatalogError) as error:
            catalog.copy_workflow(source, target, catalog_home=local)
        assert error.value.code == expected_code
        assert local_sentinel.read_bytes() == b"keep this catalog entry"
        assert {
            name: (source / name).read_bytes() for name in source_files
        } == source_files
    assert not (source / "child").exists()
    assert not (local / "nested").exists()

    missing_parent = tmp_path / "absent"
    missing_destination = missing_parent / "dest"
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, missing_destination, catalog_home=local)
    assert error.value.code == "destination_parent_missing"
    assert not missing_parent.exists() and not missing_destination.exists()
    assert local_sentinel.read_bytes() == b"keep this catalog entry"

    linked = source / "linked"
    linked.symlink_to(source / "step.py")
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, dest, catalog_home=local)
    assert error.value.code == "unsupported_file_type"
    assert not dest.exists() and (source / "linked").is_symlink()
    assert (source / "step.py").read_bytes() == source_files["step.py"]
    assert local_sentinel.read_bytes() == b"keep this catalog entry"
    linked.unlink()

    os.mkfifo(source / "pipe")
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, dest, catalog_home=local)
    assert error.value.code == "unsupported_file_type"
    assert not dest.exists() and (source / "pipe").exists()
    assert (source / "step.py").read_bytes() == source_files["step.py"]
    assert local_sentinel.read_bytes() == b"keep this catalog entry"
    (source / "pipe").unlink()

    (source / "__pycache__").symlink_to(tmp_path / "absent")
    catalog.copy_workflow(source, dest, catalog_home=local)
    assert not (dest / "__pycache__").exists()
    import shutil

    shutil.rmtree(dest)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("owned test I/O failure")

    monkeypatch.setattr(catalog.shutil, "copy2", fail)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, dest, catalog_home=local)
    assert error.value.code == "io_error"
    assert not dest.exists() and source.is_dir()
    assert {
        name: (source / name).read_bytes() for name in source_files
    } == source_files
    assert local_sentinel.read_bytes() == b"keep this catalog entry"


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
    sentinel = bundled / "existing-template.txt"
    sentinel.write_bytes(b"bundled asset remains unchanged")
    monkeypatch.setattr(catalog, "bundled_root", lambda: bundled)
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, bundled / "new", catalog_home=bundled)
    assert error.value.code == "forbidden_destination"
    assert not (bundled / "new").exists()
    assert sentinel.read_bytes() == b"bundled asset remains unchanged"


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
    source_graph = (source / "workflow.json").read_bytes()
    source_step = (source / "step.py").read_bytes()
    with pytest.raises(catalog.CatalogError) as error:
        catalog.check_ownership(
            alias / "source/workflow.json", tmp_path / "project", catalog_home=override
        )
    assert error.value.code == "catalog_ownership"
    assert (source / "workflow.json").read_bytes() == source_graph
    assert (source / "step.py").read_bytes() == source_step
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
    monkeypatch.chdir(tmp_path)
    real_source = _flow(tmp_path / "source")
    source_sentinel = (real_source / "step.py").read_bytes()
    saved_catalog = tmp_path / "saved-catalog"
    saved_catalog.mkdir()
    settings = home / ".config/millrace-plus/catalog.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(
        (json.dumps({"catalog_home": str(saved_catalog)}) + "\n").encode()
    )
    settings_before = settings.read_bytes()
    source = real_source
    missing = Path("~millrace_user_that_does_not_exist_92f715c8/workflow")
    destination = tmp_path / "copy"
    attempted_destination = destination
    if bad_location == "source":
        source = missing
    else:
        destination = missing
        attempted_destination = tmp_path / missing
    with pytest.raises(catalog.CatalogError) as error:
        catalog.copy_workflow(source, destination)
    assert error.value.code == "io_error"
    assert not attempted_destination.exists()
    assert not (tmp_path / missing).exists()
    assert settings.read_bytes() == settings_before
    monkeypatch.setattr(
        __import__("sys"),
        "argv",
        ["millrace-plus", "catalog", "copy", str(source), str(destination)],
    )
    with pytest.raises(SystemExit) as exit_code:
        cli.cli()
    assert exit_code.value.code == 2
    assert json.loads(capsys.readouterr().err)["code"] == "io_error"
    assert not attempted_destination.exists()
    assert not (tmp_path / missing).exists()
    assert settings.read_bytes() == settings_before
    assert (real_source / "step.py").read_bytes() == source_sentinel
