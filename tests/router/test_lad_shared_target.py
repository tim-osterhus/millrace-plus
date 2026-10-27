"""Shared worker authority over fake public reads; no Core/OS runtime imports."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest
from persistent_lad_support import (
    CHILD,
    INVOCATION,
    PARENT,
    RUN,
    Clock,
    FakeClient,
    journal,
    setup,
    write_request,
)

from millrace_plus import catalog, lad_service
from millrace_plus.lad_service import Service, claim_target, validate_shared_target
from millrace_plus.local_intake import IntakeError
from millrace_plus.subworkflow_records import SubworkflowError, canonical, digest

PARENT_RUN = "5" * 32


def load_script(path, monkeypatch, name=None):
    selected = name or path.stem
    spec = importlib.util.spec_from_file_location(selected, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, selected, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def shared(tmp_path, monkeypatch):
    fixture = setup(tmp_path, monkeypatch)
    definition = fixture["definition"]
    shutil.rmtree(definition)
    shutil.copytree(catalog.bundled_root() / "lad", definition)
    sha = catalog.workflow_fingerprint(definition)
    fixture["request"]["definition"] = {
        "selector": "local:child",
        "version": "sha256:" + sha,
        "sha256": sha,
    }
    monkeypatch.setattr(
        catalog,
        "init_template",
        lambda selector, destination: shutil.copytree(definition, destination),
    )
    home, target = fixture["root"] / "service", fixture["root"] / "target"
    home.mkdir()
    target.mkdir()
    (target / "dirty.txt").write_bytes(b"existing user work\n")
    fixture["settings"]["host_paths"] = {"lad_target": str(target)}
    info = target.stat()
    config = {
        "format": "millrace-plus-lad-service/v1",
        "service_id": uuid.uuid4().hex,
        "service_home": str(home),
        "target_root": str(target),
        "target_identity": {
            "path": str(target),
            "device": info.st_dev,
            "inode": info.st_ino,
        },
        "os_home": "local",
        "os_profile": fixture["request"]["os_profile"],
        "worker_definition": fixture["request"]["definition"],
        "settings_sha256": digest(fixture["settings"]),
    }
    (home / "service.json").write_bytes(canonical(config) + b"\n")
    service = Service(home)
    service.intake.initialize()
    claim_target(config, protected_roots=(home,))
    original = fixture["root"] / "task.md"
    original.write_bytes(b"Original request: answer() returns 42.\r\n")
    task_id = service.submit("first", original)["task_id"]
    claim = service.claim_next(writer_guard=lambda: None)
    service.materialize_task(task_id)
    record = {
        "format": "millrace-lad-target/v1",
        "service_id": service.service_id,
        "task_id": task_id,
        "request_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
        "target_root": str(target),
        "target_identity": config["target_identity"],
        "claim_sha256": digest(claim),
        "parent_instance_id": PARENT,
        "parent_run_id": PARENT_RUN,
        "worker_definition_sha256": sha,
    }
    fixture["request"].update(
        call_home=claim["call_home"],
        call_key=claim["call_key"],
        output_ref=claim["output_ref"],
        parent={"instance_id": PARENT, "run_id": PARENT_RUN},
        inputs={
            "request_markdown": original.read_bytes().decode(),
            "lad_target": record,
        },
    )
    write_request(fixture)
    client, timer = FakeClient(fixture), Clock()
    read = client.read

    def public_read(instance, run=None, *, deadline):
        if instance == PARENT and run is not None:
            return {"id": PARENT_RUN, "instance_id": PARENT, "planes": {}}
        return read(instance, run, deadline=deadline)

    client.read = public_read
    assert (
        service.advance_task(
            task_id,
            fixture["file"],
            cwd=fixture["parent"],
            client=client,
            clock=timer,
            sleep=timer.sleep,
        )["outcome"]
        == "CHILD_RUNNING"
    )
    saved = journal(fixture).load()
    project = Path(saved["paths"]["project"])
    workflow = Path(saved["paths"]["workflow"])
    controls = (
        fixture["root"]
        / "core"
        / "instances"
        / CHILD
        / "invocations"
        / INVOCATION
        / "controls"
    )
    controls.mkdir(parents=True)
    graph = json.loads((workflow / "workflow.json").read_bytes())
    captured = {}
    for name in ["workflow.json", *graph["control_files"]]:
        raw = (workflow / name).read_bytes()
        destination = controls / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        captured[name] = base64.b64encode(raw).decode()
    revision = hashlib.sha256(canonical(captured)).hexdigest()
    script = controls / "scripts/init_session.py"
    invocation = {
        "id": INVOCATION,
        "run_id": RUN,
        "revision_id": revision,
        "plane_id": "foreground",
        "node": "init_session",
        "status": "active",
        "writer_state": "running",
        "pid": os.getpid(),
        "cwd": str(project / "foreground"),
        "argv": [sys.executable, str(script), "--project-root", ".."],
        "deadline": time.time() + 30,
    }
    invocation["launch"] = {
        "invocation_id": INVOCATION,
        "plane_id": "foreground",
        "argv": invocation["argv"],
        "adapter_id": "command",
    }
    client.run_override = {
        "revision_id": revision,
        "startup_revision_id": revision,
        "draft_revision": {
            "candidate_id": revision,
            "revision_id": revision,
            "diagnostic": None,
            "files": captured,
        },
        "planes": {"foreground": {"active_invocation": INVOCATION}},
        "inflight_invocations": [INVOCATION],
        "invocations": [invocation],
        "completions": [],
    }
    monkeypatch.chdir(project / "foreground")
    return {
        **fixture,
        "service": service,
        "target": target,
        "record": record,
        "client": client,
        "project": project,
        "script": script,
        "workflow_script": workflow / "scripts/init_session.py",
        "invocation": invocation,
    }


def bootstrap(shared):
    timer = Clock()
    return validate_shared_target(
        shared["record"],
        project=shared["project"],
        bootstrap=True,
        pid=os.getpid(),
        script=shared["script"],
        client=shared["client"],
        clock=timer,
        sleep=timer.sleep,
    )


def test_shared_bootstrap_receipt_save_gap_and_exact_process(shared):
    storage = journal(shared)
    saved = storage.load()
    saved["run_id"] = None
    saved["receipts"].pop("start")
    saved["observation"] = {}
    saved["os_observations"] = [
        row
        for row in saved["os_observations"]
        if row["request_id"] != saved["envelopes"]["start"]["request_id"]
    ]
    storage.save(saved)
    assert bootstrap(shared) == shared["record"]
    assert (
        storage.load()["run_id"] is None
    )  # Bootstrap reads do not adopt/mutate the call.
    assert ("read", CHILD, RUN) in shared["client"].events
    assert len(shared["client"].sent) == 2


@pytest.mark.parametrize(
    "change",
    [
        "pid",
        "cwd",
        "script",
        "launch",
        "duplicate_receipt",
        "parent",
        "target_alias",
        "claim",
    ],
)
def test_shared_bootstrap_rejects_authority_substitution(shared, change):
    if change == "pid":
        shared["invocation"]["pid"] += 1
    elif change == "cwd":
        shared["invocation"]["cwd"] = str(shared["target"])
    elif change == "script":
        shared["invocation"]["argv"][1] = str(
            shared["definition"] / "scripts/init_session.py"
        )
    elif change == "launch":
        shared["invocation"]["launch"]["invocation_id"] = "f" * 32
    elif change == "duplicate_receipt":
        shared["client"].instance_controls.append(
            copy.deepcopy(shared["client"].instance_controls[0])
        )
    elif change == "parent":
        shared["record"]["parent_run_id"] = "f" * 32
    elif change == "target_alias":
        alias = shared["root"] / "alias"
        alias.symlink_to(shared["target"], target_is_directory=True)
        shared["record"]["target_root"] = str(alias)
    else:
        shared["record"]["claim_sha256"] = "f" * 64
    with pytest.raises((IntakeError, SubworkflowError)):
        bootstrap(shared)
    assert len(shared["client"].sent) == 2


def test_bootstrap_retries_reserved_pid_only_until_deadline(shared):
    shared["invocation"].update(status="reserved", pid=None)
    timer = Clock()
    calls = []

    def commit(seconds):
        calls.append(seconds)
        timer.sleep(seconds)
        shared["invocation"].update(status="active", pid=os.getpid())

    assert (
        validate_shared_target(
            shared["record"],
            project=shared["project"],
            bootstrap=True,
            pid=os.getpid(),
            script=shared["script"],
            client=shared["client"],
            clock=timer,
            sleep=commit,
        )
        == shared["record"]
    )
    assert calls
    shared["invocation"].update(status="reserved", pid=None)
    with pytest.raises(SubworkflowError, match="bootstrap_identity_deadline"):
        validate_shared_target(
            shared["record"],
            project=shared["project"],
            bootstrap=True,
            pid=os.getpid(),
            script=shared["script"],
            client=shared["client"],
            clock=timer,
            sleep=timer.sleep,
        )


def test_shared_init_source_and_assignment_use_one_validated_root(
    shared, monkeypatch, capsys
):
    scripts = shared["script"].parent
    monkeypatch.syspath_prepend(str(scripts))
    monkeypatch.chdir(shared["project"] / "foreground")
    from millrace_plus import workflow_os_client

    monkeypatch.setattr(
        workflow_os_client, "WorkflowOSClient", lambda *args, **kwargs: shared["client"]
    )
    state = load_script(scripts / "lad_state.py", monkeypatch)
    initialize = load_script(scripts / "init_session.py", monkeypatch)
    monkeypatch.setattr(sys, "argv", [str(shared["script"]), "--project-root", ".."])
    initialize.main()
    assert json.loads(capsys.readouterr().out)["outcome"] == "NEW_SESSION"
    intake = load_script(scripts / "setup_intake.py", monkeypatch)
    intake.main()
    capsys.readouterr()
    foreground = shared["project"] / "foreground"
    assert state.target_root(foreground) == shared["target"]
    assert list(state.source(foreground)) == ["dirty.txt"]
    (shared["target"] / "dirty.txt").write_bytes(b"next accepted content\n")
    assert (
        state.source(foreground)["dirty.txt"]
        == hashlib.sha256(b"next accepted content\n").hexdigest()
    )
    load_script(scripts / "boundaries.py", monkeypatch)
    acceptor = load_script(scripts / "accept_role.py", monkeypatch)
    prepare = load_script(scripts / "prepare_role.py", monkeypatch)
    with pytest.raises(state.LadError, match="intent_free_redirect_refused"):
        prepare.prepare(foreground, foreground, "planner")
    # Existing-target work starts with Recon. Only its accepted result selects
    # the Planner continuation; a shared root does not alter LAD routing.
    recon = prepare.prepare(foreground, foreground, "recon")
    assert recon["target_root"] == str(shared["target"])
    peer = load_script(
        Path(__file__).parent / "fixtures/lad_peer.py",
        monkeypatch,
        name="shared_lad_peer",
    )
    assert peer.result_for(foreground, "recon")["outcome"] == "PASS"
    assert acceptor.accept(foreground, foreground, "recon", "PASS") == "PLANNER"
    assert state.load(foreground)["continuation"]["node"] == "prepare_planner"
    occurrence = prepare.prepare(foreground, foreground, "planner")
    assert occurrence["target_root"] == str(shared["target"])
    manifest = shared["project"] / "lad-target.json"
    assert occurrence["target_record"] == {
        "path": str(manifest),
        "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }
    assignment = (
        foreground
        / f"lad-state/occurrences/{occurrence['occurrence_id']}/assignment.md"
    )
    assert str(shared["target"]) in assignment.read_text()
    assert (shared["target"] / "dirty.txt").read_bytes() == b"next accepted content\n"
    marker = shared["target"] / lad_service.OWNER
    marker.write_bytes(b"{}")
    with pytest.raises(state.LadError):
        state.source(foreground)


def test_shared_record_never_falls_back_and_standalone_keeps_local_target(
    tmp_path, monkeypatch
):
    scripts = catalog.bundled_root() / "lad/scripts"
    state = load_script(scripts / "lad_state.py", monkeypatch)
    project = tmp_path / "project"
    foreground = project / "foreground"
    (foreground / "target").mkdir(parents=True)
    (foreground / "target/answer.py").write_bytes(b"original\n")
    assert state.target_root(foreground) == foreground / "target"
    assert list(state.source(foreground)) == ["answer.py"]
    manifest = {
        "format": "millrace-plus-subworkflow-inputs/v1",
        "identity": {"fixture": "one"},
        "inputs": {"request_markdown": "task", "lad_target": None},
    }
    (project / "subworkflow-inputs.json").write_bytes(canonical(manifest))
    with pytest.raises(state.LadError):
        state.source(foreground)
    assert (foreground / "target/answer.py").read_bytes() == b"original\n"


@pytest.mark.parametrize(
    "change",
    ["live_copy", "public_copy", "capture_bytes", "revision", "capture_id", "node"],
)
def test_bootstrap_requires_actual_captured_controls(shared, change):
    if change == "live_copy":
        shared["script"] = shared["workflow_script"]
    elif change == "public_copy":
        shared["script"] = shared["workflow_script"]
        shared["invocation"]["argv"][1] = str(shared["workflow_script"])
    elif change == "capture_bytes":
        shared["script"].write_bytes(
            shared["script"].read_bytes() + b"\n# changed capture\n"
        )
    elif change == "revision":
        shared["client"].run_override["draft_revision"]["revision_id"] = "f" * 64
    elif change == "capture_id":
        wrong = (
            shared["script"].parents[3]
            / ("f" * 32)
            / "controls/scripts/init_session.py"
        )
        wrong.parent.mkdir(parents=True)
        wrong.write_bytes(shared["script"].read_bytes())
        shared["script"] = wrong
        shared["invocation"]["argv"][1] = str(wrong)
    else:
        shared["invocation"]["node"] = "setup_intake"
    with pytest.raises((IntakeError, SubworkflowError)):
        bootstrap(shared)
    assert not (shared["project"] / "lad-target.json").exists()
    assert len(shared["client"].sent) == 2
