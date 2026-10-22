"""Catalog guards surround every Plus host path without changing Core controls."""

from __future__ import annotations

import inspect
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from millrace.router import control as core

from millrace_plus.catalog import CatalogError
from millrace_plus.router import control

GUARDED = {
    "create_instance",
    "execute_request",
    "start_run",
    "recover_run",
    "resume_run",
    "retry_invocation",
    "redirect_plane",
    "pause_plane",
    "resume_plane",
    "cancel_plane",
    "accept_revision",
    "submit_revision",
    "update_instance_config",
    "pause_run",
    "cancel_run",
    "reconcile_invocation",
}


def test_all_plus_registration_and_launch_paths_guard_catalog_roots(
    flow: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    workflow, project, home = flow
    catalog = tmp_path / "catalog"
    shutil.copytree(workflow.parent, catalog / "unsafe")
    unsafe = catalog / "unsafe/workflow.json"
    options = dict(
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        catalog_home=catalog,
    )
    with pytest.raises(CatalogError):
        control.create_instance(unsafe, project, **options)
    with pytest.raises(CatalogError):
        control.create_instance(workflow, catalog, **options)
    assert not home.exists()
    envelope = dict(
        format="millrace-router-request/v2",
        operation="create_instance",
        instance_id=None,
        run_id=None,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        payload={"workflow_file": str(unsafe), "project_root": str(project)},
        plane_id=None,
    )
    with pytest.raises(CatalogError):
        control.execute_request(envelope, state_home=home, catalog_home=catalog)
    made = core.create_instance(
        unsafe,
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        path=(str(Path(sys.executable).parent),),
    )
    instance = str(made["instance_id"])
    database = home / "instances" / instance / "state.sqlite3"
    before = database.read_bytes()
    values = dict(
        instance_id=instance,
        run_id="0" * 32,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=1,
        catalog_home=catalog,
        invocation_id="1" * 32,
        plane_id="main",
        node_id="draft",
        candidate_id="2" * 64,
        settings={},
        reason="test",
        stopped=True,
    )
    for name in GUARDED - {"create_instance", "execute_request"}:
        function = getattr(control, name)
        kwargs = {
            key: values[key]
            for key in inspect.signature(function).parameters
            if key in values
        }
        with pytest.raises(CatalogError) as error:
            function(**kwargs)
        assert error.value.code == "catalog_ownership", name
        assert database.read_bytes() == before, name
    for op in (
        "start_run",
        "recover_run",
        "resume_run",
        "retry_invocation",
        "redirect_plane",
        "pause_plane",
        "resume_plane",
        "cancel_plane",
        "accept_revision",
        "submit_revision",
        "update_instance_config",
        "pause_run",
        "cancel_run",
        "reconcile_invocation",
    ):
        request = dict(
            envelope,
            operation=op,
            instance_id=instance,
            run_id="0" * 32,
            payload={},
            plane_id="main"
            if op
            in {
                "pause_plane",
                "resume_plane",
                "cancel_plane",
                "redirect_plane",
                "retry_invocation",
                "reconcile_invocation",
            }
            else None,
        )
        with pytest.raises(CatalogError):
            control.execute_request(request, state_home=home, catalog_home=catalog)
        assert database.read_bytes() == before
    assert control.inspect_instance(instance, state_home=home)["workflow_file"] == str(
        unsafe
    )


@pytest.mark.parametrize("observed_status", ["running", "ready"])
def test_unsafe_matching_stop_and_paused_reconcile(
    single_flow: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observed_status: str,
) -> None:
    workflow, project, home = single_flow
    catalog = tmp_path / "catalog"
    shutil.copytree(workflow.parent, catalog / "unsafe")
    unsafe = catalog / "unsafe/workflow.json"
    data = json.loads(unsafe.read_text())
    data["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "inspect", "resume": "draft"}
    }
    unsafe.write_text(json.dumps(data))
    made = core.create_instance(
        unsafe,
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    instance = str(made["instance_id"])
    started = core.start_run(
        instance, state_home=home, request_id=uuid.uuid4().hex, expected_state_version=1
    )
    run = str(started["run_id"])
    paused = core.inspect_run(instance, run, state_home=home)
    common = dict(state_home=home, request_id=uuid.uuid4().hex, catalog_home=catalog)
    assert control.pause_run(instance, run, **common)["status"] == "applied"
    receipt = control.reconcile_invocation(
        instance,
        run,
        invocation_id=paused["invocations"][0]["id"],
        reason="owned fixture",
        stopped=True,
        expected_state_version=paused["state_version"],
        **dict(common, request_id=uuid.uuid4().hex),
        plane_id="main",
    )
    assert receipt["status"] in {
        "applied",
        "refused",
    }  # Core remains the semantic authority.
    real_inspect = core.inspect_instance

    def running(*args: object, **kwargs: object) -> dict[str, object]:
        view = real_inspect(*args, **kwargs)
        for row in view["runs"]:
            if row["id"] == run:
                row["status"] = observed_status
        return view

    monkeypatch.setattr(core, "inspect_instance", running)
    database = home / "instances" / instance / "state.sqlite3"
    before = database.read_bytes()
    with pytest.raises(CatalogError):
        control.reconcile_invocation(
            instance,
            run,
            invocation_id="1" * 32,
            plane_id="main",
            reason="no dispatch",
            stopped=True,
            expected_state_version=paused["state_version"],
            **common,
        )
    assert database.read_bytes() == before
    monkeypatch.setattr(core, "inspect_instance", real_inspect)
    cancelled = control.cancel_run(
        instance, run, **dict(common, request_id=uuid.uuid4().hex)
    )
    assert cancelled["status"] == "applied"
    after_cancel = database.read_bytes()
    released = control.release_project_hold(
        project_root=project,
        hold_id="1" * 32,
        owner_instance_id=instance,
        owner_run_id=run,
        reason="owned test cleanup",
        stopped=True,
        state_home=home,
        request_id=uuid.uuid4().hex,
    )
    assert released["operation"] == "release_project_hold"
    assert released.get("code") != "catalog_ownership"
    assert database.read_bytes() == after_cancel


def test_cli_create_and_replay_guard_before_state_write(
    flow: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from millrace_plus.router import cli

    workflow, project, home = flow
    catalog = tmp_path / "catalog"
    shutil.copytree(workflow.parent, catalog / "unsafe")
    unsafe = catalog / "unsafe/workflow.json"
    prefix = ["--catalog-home", str(catalog)]
    assert (
        cli.main(
            [
                *prefix,
                "instance",
                "create",
                "--workflow",
                str(unsafe),
                "--project",
                str(project),
                "--state-home",
                str(home),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["code"] == "catalog_ownership"
    assert not home.exists()
    request = dict(
        format="millrace-router-request/v2",
        operation="create_instance",
        instance_id=None,
        run_id=None,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        payload={"workflow_file": str(unsafe), "project_root": str(project)},
        plane_id=None,
    )
    assert (
        cli.main(
            [
                *prefix,
                "replay",
                "--state-home",
                str(home),
                "--envelope",
                json.dumps(request),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["code"] == "catalog_ownership"
    assert not home.exists()


def test_unsafe_stored_cli_and_replay_refuse_dispatch(
    flow: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from millrace_plus.router import cli

    workflow, project, home = flow
    catalog = tmp_path / "catalog"
    shutil.copytree(workflow.parent, catalog / "unsafe")
    made = core.create_instance(
        catalog / "unsafe/workflow.json",
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    instance = str(made["instance_id"])
    database = home / "instances" / instance / "state.sqlite3"
    before = database.read_bytes()
    prefix = ["--catalog-home", str(catalog)]
    assert (
        cli.main([*prefix, "start", "--state-home", str(home), "--instance", instance])
        == 2
    )
    assert json.loads(capsys.readouterr().err)["code"] == "catalog_ownership"
    request = dict(
        format="millrace-router-request/v2",
        operation="start_run",
        instance_id=instance,
        run_id=None,
        request_id=uuid.uuid4().hex,
        expected_state_version=1,
        payload={"edit_mode": "locked"},
        plane_id=None,
    )
    assert (
        cli.main(
            [
                *prefix,
                "replay",
                "--state-home",
                str(home),
                "--envelope",
                json.dumps(request),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["code"] == "catalog_ownership"
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "entrypoint", ["api", "envelope", "console", "module", "replay"]
)
def test_final_workflow_file_alias_refuses_before_mutation(
    flow: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entrypoint: str,
) -> None:
    import os
    import subprocess

    from millrace_plus.router import cli

    workflow, project, home = flow
    catalog = tmp_path / "catalog"
    shutil.copytree(workflow.parent, catalog / "template")
    external = tmp_path / "external"
    external.mkdir()
    link = external / "workflow.json"
    link.symlink_to(catalog / "template/workflow.json")
    request = dict(
        format="millrace-router-request/v2",
        operation="create_instance",
        instance_id=None,
        run_id=None,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        payload={
            "workflow_file": str(link),
            "project_root": str(project),
            "path": [str(Path(sys.executable).parent)],
            "env_allowlist": [],
            "host_paths": {},
        },
        plane_id=None,
    )
    if entrypoint == "api":
        with pytest.raises(CatalogError) as error:
            control.create_instance(
                link,
                project,
                state_home=home,
                request_id=uuid.uuid4().hex,
                expected_state_version=0,
                catalog_home=catalog,
            )
        assert error.value.code == "catalog_ownership"
    elif entrypoint == "envelope":
        with pytest.raises(CatalogError) as error:
            control.execute_request(request, state_home=home, catalog_home=catalog)
        assert error.value.code == "catalog_ownership"
    else:
        prefix = ["--catalog-home", str(catalog)]
        create_args = [
            "instance",
            "create",
            "--workflow",
            str(link),
            "--project",
            str(project),
            "--state-home",
            str(home),
        ]
        if entrypoint == "module":
            result = subprocess.run(
                [sys.executable, "-m", "millrace_plus.router", *prefix, *create_args],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 2, result.stdout
            diagnostic = result.stderr
        elif entrypoint == "console":
            monkeypatch.setattr(
                sys, "argv", ["millrace-plus", "router", *prefix, *create_args]
            )
            with pytest.raises(SystemExit) as stopped:
                cli.cli()
            assert stopped.value.code == 2
            diagnostic = capsys.readouterr().err
        else:
            assert (
                cli.main(
                    [
                        *prefix,
                        "replay",
                        "--state-home",
                        str(home),
                        "--envelope",
                        json.dumps(request),
                    ]
                )
                == 2
            )
            diagnostic = capsys.readouterr().err
        assert json.loads(diagnostic)["code"] == "catalog_ownership"
    assert not home.exists(), "Plus refusal must precede every Core durable write"


@pytest.mark.parametrize("target", ["workflow", "project"])
@pytest.mark.parametrize("entrypoint", ["api", "cli"])
def test_literal_tilde_core_paths_guard_before_mutation(
    flow: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    target: str,
    entrypoint: str,
) -> None:
    """Core treats a quoted tilde literally; preflight must inspect that path."""
    from millrace_plus.router import cli

    workflow, project, home = flow
    catalog = tmp_path / "catalog"
    shutil.copytree(workflow.parent, catalog / "unsafe")
    (catalog / "unsafe-project").mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "~").symlink_to(catalog, target_is_directory=True)
    user_home = tmp_path / "separate-user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.chdir(cwd)
    selected_workflow = (
        Path("~/unsafe/workflow.json") if target == "workflow" else workflow
    )
    selected_project = Path("~/unsafe-project") if target == "project" else project
    selected = selected_workflow if target == "workflow" else selected_project
    assert selected.resolve().is_relative_to(catalog)
    assert not selected.expanduser().resolve().is_relative_to(catalog)
    if entrypoint == "api":
        with pytest.raises(CatalogError) as error:
            control.create_instance(
                selected_workflow,
                selected_project,
                state_home=home,
                request_id=uuid.uuid4().hex,
                expected_state_version=0,
                catalog_home=catalog,
            )
        assert error.value.code == "catalog_ownership"
    else:
        assert (
            cli.main(
                [
                    "--catalog-home",
                    str(catalog),
                    "instance",
                    "create",
                    "--workflow",
                    str(selected_workflow),
                    "--project",
                    str(selected_project),
                    "--state-home",
                    str(home),
                ]
            )
            == 2
        )
        assert json.loads(capsys.readouterr().err)["code"] == "catalog_ownership"
    assert not home.exists()
