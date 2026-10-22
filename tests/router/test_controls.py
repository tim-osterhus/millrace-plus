"""Pi control tests transferred to the Plus router facade.

Transferred unchanged in names, parametrization and assertions from the
accepted Core baseline `tests/router/test_control.py`
(sha256 41eb8eff43c147a11fa4d99bc0b0d669941dc0372d132f9adf9a5287c535bb3f):
`test_pi_probe_cap_routes_on_error_not_limit_exhausted`,
`test_pi_controls_reconciliation_and_late_results` (live/locked/uncertain), and
`test_pi_active_fifo_controls_during_flood_and_cleanup`.

Only the seams are translated: lifecycle helpers come from `plus_support`, every
control call goes through `millrace_plus.router.control`, and the Pi invocation
seam is the Plus module global `millrace_plus.router.pi.invoke` rather than the
Core engine. Resolver propagation is exercised because every control goes
through `millrace_plus.router.control`, which injects the Plus resolver into Core.
"""

import json
from pathlib import Path

import pytest
from millrace.router.execution import AdapterResult
from plus_support import (
    _assistant,
    _events,
    _fake,
    _wire,
    create_instance,
    native_flow,
    new_id,
    start_run,
)

from millrace_plus.router import control


def test_pi_probe_cap_routes_on_error_not_limit_exhausted(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    _fake(workflow.parent, b"", version_extra="time.sleep(10)")
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 30
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    graph["limits"]["max_elapsed_seconds"] = 15
    workflow.write_text(json.dumps(graph))
    instance = str(
        create_instance(
            workflow, project, state_home=home, host_paths={"agent": str(agent)}
        )["instance_id"]
    )
    paused = start_run(instance, state_home=home)
    invocation = paused["invocations"][0]
    assert invocation["deadline"] == invocation["run_deadline"]
    assert paused["pause_reason"] == "writer_unknown"
    assert paused["completions"][0]["transport"] == "execution_failure"
    assert invocation["adapter_evidence"]["failure_code"] == "pi_version_timeout"
    control.reconcile_invocation(
        instance,
        str(paused["id"]),
        invocation_id=invocation["id"],
        reason="owned fake root reaped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    current = control.inspect_run(instance, str(paused["id"]), state_home=home)
    control.resume_run(
        instance,
        str(paused["id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=current["state_version"],
    )
    after = control.inspect_run(instance, str(paused["id"]), state_home=home)
    assert after["status"] == "failed" and after["count"] == 1
    assert len(after["transitions"]) == 1


@pytest.mark.parametrize("mode", ["live", "locked", "uncertain"])
def test_pi_controls_reconciliation_and_late_results(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from dataclasses import replace

    import millrace_plus.router.pi as pi

    workflow, project, home, agent = native_flow(single_flow)
    instance = str(
        create_instance(
            workflow, project, state_home=home, host_paths={"agent": str(agent)}
        )["instance_id"]
    )
    real = pi.invoke

    def invoke_native(*args: object, **kwargs: object) -> AdapterResult:
        result = real(*args, **kwargs)
        if mode == "uncertain":
            return replace(
                result,
                writer_state="may_live",
                diagnostic="fixture closure uncertainty",
            )
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {"finish": "failed"}
        workflow.write_text(json.dumps(graph))
        return result

    monkeypatch.setattr(pi, "invoke", invoke_native)
    result = start_run(
        instance, state_home=home, edit_mode="live" if mode == "live" else "locked"
    )
    if mode == "live":
        assert result["status"] == "failed"
        assert result["invocations"][0]["revision_id"] != result["revision_id"]
    elif mode == "locked":
        assert result["pause_reason"] == "locked_edit"
        assert result["completions"][0]["transport"] == "completed"
        assert result["transitions"] == []
    else:
        assert result["pause_reason"] == "writer_unknown"
        before = result["completions"]
        control.reconcile_invocation(
            instance,
            str(result["id"]),
            invocation_id=result["invocations"][0]["id"],
            reason="fixture stopped",
            stopped=True,
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
            plane_id="main",
        )
        current = control.inspect_run(instance, str(result["id"]), state_home=home)
        assert current["transitions"] == []
        control.resume_run(
            instance,
            str(result["id"]),
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
        )
        after = control.inspect_run(instance, str(result["id"]), state_home=home)
        assert after["status"] == "succeeded" and after["count"] == 1
        assert [
            {k: v for k, v in row.items() if k != "effective_writer"}
            for row in after["completions"]
        ] == [
            {k: v for k, v in row.items() if k != "effective_writer"} for row in before
        ]


def test_pi_active_fifo_controls_during_flood_and_cleanup(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import millrace_plus.router.pi as pi

    workflow, project, home, agent = native_flow(single_flow)
    events = _events(project, [_assistant(['{"outcome":"READY"}'])])
    header = _wire(events[:1])
    progress = (
        json.dumps({"type": "entry_appended", "entry": {"note": "x" * 100}}).encode()
        + b"\n"
    )
    before = (
        "import signal\nsignal.signal(signal.SIGTERM, lambda *_: None)\n"
        f"os.write(1,{header!r})\nend=time.monotonic()+0.3\n"
        f"while time.monotonic()<end:\n os.write(1,{progress!r})\n"
        " os.write(2,b'diagnostic\\n')\n time.sleep(0.002)"
    )
    _fake(workflow.parent, _wire(events[1:]), before=before)
    instance = str(
        create_instance(
            workflow, project, state_home=home, host_paths={"agent": str(agent)}
        )["instance_id"]
    )
    ids: dict[str, object] = {}
    request_ids = [new_id(), new_id()]
    queued: list[dict[str, object]] = []
    real = pi.invoke

    def with_controls(*args: object, **kwargs: object) -> AdapterResult:
        values = list(args)
        callback, acknowledge = values[4], values[5]
        launched: list[float] = []

        def started(pid: int) -> None:
            acknowledge(pid)
            launched.append(time.monotonic())
            queued.append(
                control.pause_run(
                    instance,
                    str(ids["run_id"]),
                    state_home=home,
                    request_id=request_ids[0],
                )
            )

        def poll() -> bool:
            if launched and len(queued) == 1 and time.monotonic() - launched[0] > 0.03:
                queued.append(
                    control.cancel_run(
                        instance,
                        str(ids["run_id"]),
                        state_home=home,
                        request_id=request_ids[1],
                    )
                )
            return callback()

        values[4], values[5] = poll, started
        return real(*values, **kwargs)

    monkeypatch.setattr(pi, "invoke", with_controls)
    paused = start_run(instance, state_home=home, on_started=ids.update)
    assert len(queued) == 2 and all(row["status"] == "queued" for row in queued)
    assert paused["pause_reason"] == "writer_unknown"
    assert paused["run_gate"]["reason"] == "writer_unknown"
    assert paused["cancel_requested"] is True
    assert paused["completions"][0]["transport"] == "cancelled"
    assert paused["completions"][0]["stdout"] == ""
    assert paused["completions"][0]["writer"] == "may_live"
    assert paused["completions"][0]["effective_writer"] == "may_live"
    assert paused["invocations"][0]["writer_state"] == "may_live"
    assert (project / ".millrace-router-owner/owner.json").exists()
    assert paused["count"] == 1 and paused["transitions"] == []
    history = control.inspect_run(instance, str(paused["id"]), state_home=home)
    assert [
        r["status"] for r in history["controls"] if r["request_id"] in request_ids
    ] == ["applied", "applied"]
    control.reconcile_invocation(
        instance,
        str(paused["id"]),
        invocation_id=paused["invocations"][0]["id"],
        reason="fake root reaped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    final = control.cancel_run(
        instance, str(paused["id"]), state_home=home, request_id=new_id()
    )
    assert final["status"] == "applied"
    after = control.inspect_run(instance, str(paused["id"]), state_home=home)
    assert after["status"] == "cancelled" and after["count"] == 1
    assert after["cancel_requested"] is True
    assert after["completions"][0]["writer"] == "may_live"
    assert after["completions"][0]["effective_writer"] == "stopped"
    assert after["invocations"][0]["writer_state"] == "stopped"
    assert not (project / ".millrace-router-owner/owner.json").exists()


# OH-owned new proof follows:


def _store_dump(instance: str, home: Path) -> list[str]:
    import sqlite3

    path = home / "instances" / instance / "state.sqlite3"
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
        return list(db.iterdump())


def test_external_draft_publish_preserves_paused_run_candidate_identity(
    single_flow: tuple[Path, Path, Path], tmp_path: Path
) -> None:
    """P7 manual Apply can validate frozen bytes outside the watched folder."""
    import shutil

    workflow, project, home, agent = native_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(json.dumps(graph))
    created = create_instance(
        workflow, project, state_home=home, host_paths={"agent": str(agent)}
    )
    instance = str(created["instance_id"])
    owned_fakes = [
        Path(binding["executable"])
        for binding in graph["bindings"].values()
        if binding["adapter"] != "command"
    ]
    assert owned_fakes and all(
        fake.resolve().is_relative_to(tmp_path.resolve()) and fake.name == "fake-pi"
        for fake in owned_fakes
    )
    paused = start_run(instance, state_home=home)
    assert paused["status"] == "paused"
    assert paused["planes"]["main"]["pause_reason"] == "review"
    run_id = str(paused["id"])
    version = paused["state_version"]
    draft = tmp_path / "outside-watched-workflow"
    shutil.copytree(workflow.parent, draft)
    (draft / "task.txt").write_text("Review the published revision and its assets.\n")
    files = {"workflow.json", *graph["control_files"]}
    frozen = {name: (draft / name).read_bytes() for name in files}
    before = _store_dump(instance, home)
    staged = control.validate_revision(
        instance, state_home=home, workflow_file=draft / "workflow.json", run_id=run_id
    )
    assert staged["valid"] is True, staged
    assert staged["validation_context"]["run_state_version"] == version
    assert _store_dump(instance, home) == before
    for name, raw in frozen.items():
        (workflow.parent / name).write_bytes(raw)
    published = control.validate_revision(instance, state_home=home, run_id=run_id)
    assert published["valid"] is True, published
    assert published["validation_context"] == staged["validation_context"]
    assert published["revision_id"] == staged["revision_id"]
    assert (
        published["draft_revision"]["candidate_id"]
        == staged["draft_revision"]["candidate_id"]
    )
    assert published["draft_revision"]["files"] == staged["draft_revision"]["files"]
    assert _store_dump(instance, home) == before
    assert all(
        (workflow.parent / name).read_bytes() == raw for name, raw in frozen.items()
    )
    cancelled = control.cancel_run(
        instance, run_id, state_home=home, request_id=new_id()
    )
    assert cancelled["status"] == "applied", cancelled


def _durable(instance: str, home: Path, project: Path) -> tuple[object, ...]:
    """Logical durable state: store rows, project hold and instance files."""
    hold = project / ".millrace-router-owner" / "owner.json"
    files = sorted(
        str(p.relative_to(home))
        for p in home.rglob("*")
        if p.is_file() and not p.name.endswith(("-wal", "-shm", ".lock"))
    )
    return (
        _store_dump(instance, home),
        hold.read_bytes() if hold.exists() else None,
        files,
        (home / "orphan-receipts").exists(),
        {
            path.name: path.read_bytes()
            for path in (home / "orphan-receipts").glob("*.json")
        },
    )


def _core_only_refuses(operation, *args, **kwargs) -> None:
    """A default Core-only host has no Pi contract: refusal before any write."""
    from millrace.router.execution import RouterError

    try:
        result = operation(*args, **kwargs)
    except RouterError as exc:
        assert exc.code == "unsupported_adapter_contract", exc.code
        return
    assert result["status"] == "refused", result
    assert result["code"] == "unsupported_adapter_contract", result


def test_pi_facade_replay_recovery_and_writer_uncertainty(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every named facade control carries the Plus resolver into Core.

    A counting wrapper replaces the facade's resolver global, so each operation
    must reach Core's resolver use (validation, Store reload, dispatch, the
    owner-store read inside hold release). The same instance opened by a
    Core-only host refuses before any durable change.
    """
    from dataclasses import replace

    from millrace.router import control as core

    import millrace_plus.router.control as facade
    import millrace_plus.router.pi as pi

    workflow, project, home, agent = native_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on"]["READY"] = {
        "pause": {"reason": "review", "resume": "draft"}
    }
    workflow.write_text(json.dumps(graph))

    seen: dict[str, list[tuple[str, str]]] = {}
    current = ["setup"]
    real_resolver = facade.resolve_adapter

    def counting(adapter_id: str, contract: str):
        seen.setdefault(current[0], []).append((adapter_id, contract))
        return real_resolver(adapter_id, contract)

    monkeypatch.setattr(facade, "resolve_adapter", counting)

    def op(name: str, *args: object, **kwargs: object):
        current[0] = name
        result = getattr(facade, name)(*args, **kwargs)
        assert seen.get(name), f"{name} did not reach the Plus resolver"
        assert set(seen[name]) == {("pi_json", "millrace-pi-json/v1")}
        return result

    created = op(
        "create_instance",
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        host_paths={"agent": str(agent)},
    )
    assert created["status"] == "applied", created
    instance = str(created["instance_id"])
    assert [row["instance_id"] for row in op("list_instances", state_home=home)] == [
        instance
    ]
    version = op("inspect_instance", instance, state_home=home)["state_version"]
    assert getattr(op("load_instance", instance, home), "project_root") == project
    validation = op("validate_revision", instance, state_home=home)
    assert validation["valid"] is True, validation

    # First invocation: genuine completed transport, uncertain writer closure.
    real_invoke = pi.invoke
    uncertain = [True]

    def invoke(*args: object, **kwargs: object) -> AdapterResult:
        result = real_invoke(*args, **kwargs)
        if uncertain and uncertain.pop():
            return replace(result, writer_state="may_live", diagnostic="fixture")
        return result

    monkeypatch.setattr(pi, "invoke", invoke)
    ids: dict[str, object] = {}
    started = op(
        "start_run",
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=version,
        edit_mode="live",
        on_started=ids.update,
    )
    assert started["status"] == "applied", started
    run = str(started["run_id"])
    paused = op("inspect_run", instance, run, state_home=home)
    assert paused["pause_reason"] == "writer_unknown"
    assert paused["completions"][0]["transport"] == "completed"
    invocation = paused["invocations"][0]
    launched = invocation["launch_settings"]
    planned = validation["resolved_nodes"]["main"]["draft"]
    assert planned["common"]["producing_revision"] == validation["revision_id"]
    assert (
        launched["common"]["producing_revision"]
        == invocation["revision_id"]
        == paused["startup_revision_id"]
        == paused["revision_id"]
    )
    assert {k: v for k, v in launched.items() if k != "common"} == {
        k: v for k, v in planned.items() if k != "common"
    }
    assert {
        k: v for k, v in launched["common"].items() if k != "producing_revision"
    } == {k: v for k, v in planned["common"].items() if k != "producing_revision"}
    assert paused["transitions"] == []
    hold = json.loads((project / ".millrace-router-owner" / "owner.json").read_text())

    # A Core-only host cannot inspect, stop, continue or release this instance.
    before = _durable(instance, home, project)
    _core_only_refuses(core.inspect_run, instance, run, state_home=home)
    _core_only_refuses(core.inspect_instance, instance, state_home=home)
    _core_only_refuses(core.validate_revision, instance, state_home=home)
    _core_only_refuses(
        core.pause_run, instance, run, state_home=home, request_id=new_id()
    )
    _core_only_refuses(
        core.cancel_run, instance, run, state_home=home, request_id=new_id()
    )
    for name, extra in (
        ("resume_run", {}),
        ("recover_run", {}),
        ("submit_revision", {}),
        (
            "reconcile_invocation",
            {
                "invocation_id": paused["invocations"][0]["id"],
                "plane_id": "main",
                "reason": "core-only",
                "stopped": True,
            },
        ),
    ):
        _core_only_refuses(
            getattr(core, name),
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=paused["state_version"],
            **extra,
        )
    _core_only_refuses(
        core.release_project_hold,
        project_root=project,
        hold_id=str(hold["hold_id"]),
        owner_instance_id=instance,
        owner_run_id=run,
        reason="core-only host",
        stopped=True,
        state_home=home,
        request_id=new_id(),
    )
    assert _durable(instance, home, project) == before
    assert not (home / "orphan-receipts").exists()

    # The Plus host's owner-store read sees a live nonterminal owner.
    released = op(
        "release_project_hold",
        project_root=project,
        hold_id=str(hold["hold_id"]),
        owner_instance_id=instance,
        owner_run_id=run,
        reason="operator checked",
        stopped=True,
        state_home=home,
        request_id=new_id(),
    )
    assert (released["status"], released["code"]) == (
        "refused",
        "owner_run_nonterminal",
    )
    # Even a retained refusal requires the owner's execution contract before replay.
    receipt_path = home / "orphan-receipts" / (str(released["request_id"]) + ".json")
    replay = json.loads(receipt_path.read_text())["request"]
    before_replay = _durable(instance, home, project)
    _core_only_refuses(core.execute_request, replay, state_home=home)
    assert _durable(instance, home, project) == before_replay
    assert op("execute_request", replay, state_home=home) == released
    assert _durable(instance, home, project) == before_replay
    assert (
        op("pause_run", instance, run, state_home=home, request_id=new_id())["action"]
        == "already_paused"
    )
    state = op("inspect_run", instance, run, state_home=home)["state_version"]
    recovered = op(
        "recover_run",
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=state,
    )
    assert recovered["status"] == "applied", recovered
    state = op("inspect_run", instance, run, state_home=home)
    assert state["pause_reason"] == "writer_unknown"
    refused_plane = op(
        "resume_plane",
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
    )
    assert (refused_plane["status"], refused_plane["code"]) == (
        "refused",
        "plane_not_paused",
    )
    state = op("inspect_run", instance, run, state_home=home)
    assert state["count"] == 1 and state["transitions"] == []
    assert state["planes"]["main"]["status"] == "running"
    assert state["planes"]["main"]["active_invocation"] == invocation["id"]
    assert state["run_gate"]["reason"] == "writer_unknown"
    assert state["completions"][0]["writer"] == "may_live"
    assert state["completions"][0]["effective_writer"] == "may_live"
    assert state["invocations"][0]["writer_state"] == "may_live"
    assert (project / ".millrace-router-owner/owner.json").exists()
    reconciled = op(
        "reconcile_invocation",
        instance,
        run,
        invocation_id=paused["invocations"][0]["id"],
        reason="fixture writer reaped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
        plane_id="main",
    )
    assert reconciled["status"] == "applied", reconciled
    state = op("inspect_run", instance, run, state_home=home)
    assert state["transitions"] == [] and state["count"] == 1
    resumed = op(
        "resume_run",
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    state = op("inspect_run", instance, run, state_home=home)
    assert (state["planes"]["main"]["pause_reason"], state["count"]) == ("review", 1)
    assert state["pause_reason"] == "no_runnable_planes" and state["run_gate"] is None
    assert len(state["transitions"]) == 1
    paused_plane = op(
        "pause_plane",
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
    )
    assert (paused_plane["status"], paused_plane["action"]) == (
        "applied",
        "already_paused",
    )
    state = op("inspect_run", instance, run, state_home=home)
    assert (state["planes"]["main"]["pause_reason"], state["count"]) == ("review", 1)
    assert state["pause_reason"] == "no_runnable_planes" and state["run_gate"] is None

    retried = op(
        "retry_invocation",
        instance,
        run,
        invocation_id=state["invocations"][0]["id"],
        reason="operator retry",
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
        plane_id="main",
    )
    assert retried["status"] == "applied", retried
    state = op("inspect_run", instance, run, state_home=home)
    assert (state["planes"]["main"]["pause_reason"], state["count"]) == ("review", 2)
    assert state["pause_reason"] == "no_runnable_planes" and state["run_gate"] is None
    redirected = op(
        "redirect_plane",
        instance,
        run,
        node_id="draft",
        reason="operator redirect",
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
        plane_id="main",
    )
    assert redirected["status"] == "applied", redirected
    state = op("inspect_run", instance, run, state_home=home)
    assert (state["planes"]["main"]["pause_reason"], state["count"]) == ("review", 3)
    assert state["pause_reason"] == "no_runnable_planes" and state["run_gate"] is None

    # Manual publication uses an explicit run pause, then accept and resume.
    manual_pause = op(
        "pause_run", instance, run, state_home=home, request_id=new_id()
    )
    assert (manual_pause["status"], manual_pause["action"]) == ("applied", "paused")
    state = op("inspect_run", instance, run, state_home=home)
    assert state["run_gate"]["reason"] == "operator_pause"
    assert (state["planes"]["main"]["pause_reason"], state["count"]) == ("review", 3)
    (workflow.parent / "task.txt").write_text("Review this project again.")
    submitted = op(
        "submit_revision",
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
    )
    assert submitted["status"] == "applied", submitted
    state = op("inspect_run", instance, run, state_home=home)
    accepted = op(
        "accept_revision",
        instance,
        run,
        candidate_id=str(submitted["candidate_id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
    )
    assert accepted["status"] == "applied", accepted
    state = op("inspect_run", instance, run, state_home=home)
    assert state["revision_id"] == accepted["revision_id"]
    assert state["run_gate"] is None and state["count"] == 3
    continued = op(
        "resume_plane",
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=state["state_version"],
    )
    assert (continued["status"], continued["action"]) == ("applied", "resumed")
    state = op("inspect_run", instance, run, state_home=home)
    assert (state["planes"]["main"]["pause_reason"], state["count"]) == ("review", 4)
    assert state["pause_reason"] == "no_runnable_planes" and state["run_gate"] is None
    producing = {row["revision_id"] for row in state["invocations"]}
    assert len(producing) == 2 and state["revision_id"] in producing

    # Exact envelope replay through the facade returns the durable receipt.
    request = {
        "format": "millrace-router-request/v2",
        "operation": "cancel_run",
        "instance_id": instance,
        "run_id": run,
        "request_id": new_id(),
        "expected_state_version": None,
        "payload": {},
        "plane_id": None,
    }
    cancelled = op("execute_request", dict(request), state_home=home)
    assert cancelled["status"] == "applied", cancelled
    assert op("execute_request", dict(request), state_home=home) == cancelled
    final = op("inspect_run", instance, run, state_home=home)
    assert final["status"] == "cancelled" and final["count"] == 4
    assert not (project / ".millrace-router-owner" / "owner.json").exists()
    cancelled_plane = op(
        "cancel_plane",
        instance,
        run,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
    )
    assert (cancelled_plane["status"], cancelled_plane["action"]) == (
        "applied",
        "already_terminal",
    )
    after_plane = op("inspect_run", instance, run, state_home=home)
    assert {k: v for k, v in after_plane.items() if k != "controls"} == {
        k: v for k, v in final.items() if k != "controls"
    }
    assert after_plane["controls"] == [*final["controls"], cancelled_plane]
    assert (
        op(
            "cancel_plane",
            instance,
            run,
            plane_id="main",
            state_home=home,
            request_id=cancelled_plane["request_id"],
        )
        == cancelled_plane
    )
    assert op("inspect_run", instance, run, state_home=home) == after_plane
    assert (
        op("cancel_run", instance, run, state_home=home, request_id=new_id())["action"]
        == "already_terminal"
    )
    # Core-only receipts never leaked into the Plus-hosted history.
    final = op("inspect_run", instance, run, state_home=home)
    before = _durable(instance, home, project)
    _core_only_refuses(core.execute_request, dict(request), state_home=home)
    assert _durable(instance, home, project) == before
    assert op("inspect_run", instance, run, state_home=home) == final

    named = {
        "create_instance",
        "list_instances",
        "inspect_instance",
        "load_instance",
        "validate_revision",
        "start_run",
        "inspect_run",
        "release_project_hold",
        "pause_run",
        "recover_run",
        "reconcile_invocation",
        "resume_run",
        "retry_invocation",
        "redirect_plane",
        "pause_plane",
        "resume_plane",
        "cancel_plane",
        "submit_revision",
        "accept_revision",
        "execute_request",
        "cancel_run",
    }
    assert named <= seen.keys()


def test_unused_pi_namespace_requires_plus_resolver_before_write(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """update_instance_config validates an unused explicit namespace too."""
    from millrace.router import control as core

    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    namespace = {"contract": "millrace-pi-json/v1", "config": {"thinking": "off"}}
    version = control.inspect_instance(instance, state_home=home)["state_version"]
    before = _store_dump(instance, home)
    _core_only_refuses(
        core.update_instance_config,
        instance,
        settings={"runtime_defaults": {"adapter_settings": {"pi_json": namespace}}},
        state_home=home,
        request_id=new_id(),
        expected_state_version=version,
    )
    invalid = control.update_instance_config(
        instance,
        settings={
            "runtime_defaults": {
                "adapter_settings": {
                    "pi_json": dict(namespace, config={"thinking": "unbounded"})
                }
            }
        },
        state_home=home,
        request_id=new_id(),
        expected_state_version=version,
    )
    assert (invalid["status"], invalid["code"]) == ("refused", "invalid_settings")
    assert control.inspect_instance(instance, state_home=home)["state_version"] == (
        version
    )
    import millrace_plus.router.control as facade

    seen: list[tuple[str, str]] = []
    real_resolver = facade.resolve_adapter

    def counting(adapter_id: str, contract: str) -> object:
        seen.append((adapter_id, contract))
        return real_resolver(adapter_id, contract)

    monkeypatch.setattr(facade, "resolve_adapter", counting)
    applied = control.update_instance_config(
        instance,
        settings={"runtime_defaults": {"adapter_settings": {"pi_json": namespace}}},
        state_home=home,
        request_id=new_id(),
        expected_state_version=version,
    )
    # The unused explicit namespace was validated by the Plus resolver.
    assert set(seen) == {("pi_json", "millrace-pi-json/v1")}
    assert applied["status"] == "applied", applied
    assert _store_dump(instance, home) != before
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    assert run["invocations"][0]["launch"]["adapter_id"] == "command"


def test_builtin_command_never_reaches_plus_resolver(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace.router.execution import RouterError

    import millrace_plus.router.control as facade
    from millrace_plus.router.pi import resolve_adapter

    calls: list[tuple[str, str]] = []

    def counting(adapter_id: str, contract: str) -> object:
        calls.append((adapter_id, contract))
        return resolve_adapter(adapter_id, contract)

    monkeypatch.setattr(facade, "resolve_adapter", counting)
    workflow, project, home = single_flow
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded" and calls == []
    for pair in (
        ("command", "millrace-command/v1"),
        ("pi_json", "other/v1"),
        ("x", "y"),
    ):
        with pytest.raises(RouterError) as error:
            resolve_adapter(*pair)
        assert error.value.code == "unsupported_adapter_contract"


@pytest.mark.parametrize(
    "operation",
    [
        "pause_plane",
        "resume_plane",
        "cancel_plane",
        "redirect_plane",
        "retry_invocation",
        "reconcile_invocation",
    ],
)
def test_plane_facade_forwards_exact_target_without_projection_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    import millrace_plus.router.control as facade

    expected = {"plane_id": "alpha", "status": "applied", "active_invocations": []}
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def forward(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(facade, "_guard", lambda *args: None)
    monkeypatch.setattr(facade._core, operation, forward, raising=False)
    values: dict[str, object] = {
        "plane_id": "alpha",
        "state_home": tmp_path,
        "request_id": new_id(),
    }
    if operation not in {"pause_plane", "cancel_plane"}:
        values["expected_state_version"] = 7
    if operation in {"redirect_plane", "retry_invocation", "reconcile_invocation"}:
        values["reason"] = "operator evidence"
    if operation == "redirect_plane":
        values["node_id"] = "target"
    if operation in {"retry_invocation", "reconcile_invocation"}:
        values["invocation_id"] = "1" * 32
    if operation == "reconcile_invocation":
        values["stopped"] = True
    result = getattr(facade, operation)("instance", "run", **values)
    assert result is expected
    assert calls == [
        (("instance", "run"), {**values, "adapter_resolver": facade.resolve_adapter})
    ]


def test_facade_signatures_match_core_controls_without_resolver() -> None:
    import inspect

    from millrace.router import control as core

    import millrace_plus.router.control as facade

    assert set(facade.__all__) == {"resolve_adapter", *core.__all__}
    guarded = {
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
    for name in core.__all__:
        theirs = inspect.signature(getattr(core, name)).parameters
        ours = inspect.signature(getattr(facade, name)).parameters
        extra = ["catalog_home"] if name in guarded else []
        assert list(ours) == [p for p in theirs if p != "adapter_resolver"] + extra, (
            name
        )
        assert theirs["adapter_resolver"].kind is inspect.Parameter.KEYWORD_ONLY
        for key, parameter in ours.items():
            if key == "catalog_home":
                assert (
                    name in guarded and parameter.kind is inspect.Parameter.KEYWORD_ONLY
                )
                assert parameter.default is None
                continue
            assert parameter.kind is theirs[key].kind, (name, key)
            if (
                theirs[key].default is not inspect.Parameter.empty
                and type(theirs[key].default) is not object
            ):
                assert parameter.default == theirs[key].default, (name, key)
