"""Retained call identity, cancellation and interruption aftermath."""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from subworkflow_support import setup

from millrace_plus import subworkflow_records as records
from millrace_plus import subworkflows


@pytest.mark.parametrize(
    "field",
    [
        "format",
        "request_sha256",
        "definition",
        "parent",
        "paths",
        "instance_id",
        "envelopes",
        "deadline_at",
    ],
)
def test_corrupt_journal_authority_refuses_without_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / field)
    monkeypatch.chdir(fixture["project"])
    subworkflows.call(Path(str(fixture["file"])))
    req = fixture["request"]
    path = Path(req["call_home"]) / req["call_key"] / "call.json"
    journal = json.loads(path.read_bytes())
    journal[field] = "corrupt"
    # Bypass the byte-integrity checksum to attack the authority schema itself.
    journal["record_sha256"] = records.digest(
        {k: v for k, v in journal.items() if k != "record_sha256"}
    )
    path.write_text(json.dumps(journal))
    before = path.read_bytes()
    with pytest.raises(records.SubworkflowError):
        subworkflows.call(Path(str(fixture["file"])))
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "field", ["instance_id", "run_id", "envelope_id", "deadline_at"]
)
def test_well_formed_journal_link_mismatches_refuse_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / field)
    monkeypatch.chdir(fixture["project"])
    assert subworkflows.call(fixture["file"])["outcome"] == "CHILD_SUCCEEDED"
    req = fixture["request"]
    journal = records.Journal(Path(req["call_home"]), req["call_key"])
    saved = journal.load()
    before = control.inspect_instance(
        saved["instance_id"], state_home=Path(saved["paths"]["state"])
    )
    changed = json.loads(journal.path.read_bytes())
    if field in {"instance_id", "run_id"}:
        changed[field] = uuid.uuid4().hex
    elif field == "envelope_id":
        changed["envelopes"]["start"]["request_id"] = uuid.uuid4().hex
    else:
        changed[field] = (
            records._date(changed[field]) + timedelta(seconds=1)
        ).isoformat()
        # Keep both digests valid so the deadline's semantic link is tested.
        changed["authority_sha256"] = records.digest(subworkflows._authority(changed))
    changed["record_sha256"] = records.digest(
        {k: v for k, v in changed.items() if k != "record_sha256"}
    )
    journal.path.write_text(json.dumps(changed))
    raw = journal.path.read_bytes()
    with pytest.raises(records.SubworkflowError, match="invalid_journal"):
        subworkflows.call(fixture["file"])
    assert journal.path.read_bytes() == raw
    assert (
        control.inspect_instance(
            saved["instance_id"], state_home=Path(saved["paths"]["state"])
        )
        == before
    )


@pytest.mark.parametrize("persistent", [False, True])
def test_post_spawn_save_failure_retains_controller_and_real_writer_aftermath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    from subworkflow_support import wait_for

    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(
        tmp_path / "save-failure",
        script=(
            "import json,os,time\nfrom pathlib import Path\n"
            "marker=Path('started.tmp')\n"
            "marker.write_text(json.dumps({'pid':os.getpid()}))\n"
            "marker.replace('started.json')\n"
            "time.sleep(20)\n"
        ),
    )
    monkeypatch.chdir(fixture["project"])
    original_save = records.Journal.save
    original_init = subworkflows.ControlProcess.__init__
    spawned = []
    observed = {}
    failed = False

    def capture(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        spawned.append(self)

    def fail_save(self, record):
        nonlocal failed
        if "setup_error" in observed:
            return original_save(self, record)
        if (persistent or not failed) and any(
            row["operation"] == "start" and row["pid"] is not None
            for row in record["controllers"]
        ):
            if not failed:
                # This save holds the helper journal lock. Observe Core directly;
                # Popen alone does not establish public admission or a real writer.
                try:
                    sent = record["envelopes"]["start"]
                    home = Path(record["paths"]["state"])
                    instance = record["instance_id"]
                    receipt = wait_for(
                        lambda: next(
                            (
                                r
                                for r in control.inspect_instance(
                                    instance, state_home=home
                                )["controls"]
                                if r["request_id"] == sent["request_id"]
                                and r["request_digest"]
                                == records.public_request_digest(sent)
                                and r["operation"] == "start_run"
                                and r["status"] == "applied"
                            ),
                            None,
                        )
                    )
                    observed.update(receipt=receipt, instance=instance, home=str(home))
                    marker = Path(record["paths"]["project"]) / "started.json"

                    def real_writer():
                        view = control.inspect_run(
                            instance, receipt["run_id"], state_home=home
                        )
                        if not marker.exists():
                            return None
                        pid = json.loads(marker.read_bytes())["pid"]
                        return next(
                            (
                                row
                                for row in view["invocations"]
                                if row["id"] in view["inflight_invocations"]
                                and row["pid"] == pid
                            ),
                            None,
                        )

                    observed["writer"] = wait_for(real_writer)
                except BaseException as exc:
                    observed["setup_error"] = repr(exc)
                    raise
            failed = True
            raise OSError("injected post-spawn journal failure")
        return original_save(self, record)

    monkeypatch.setattr(subworkflows.ControlProcess, "__init__", capture)
    monkeypatch.setattr(records.Journal, "save", fail_save)
    req = fixture["request"]
    try:
        with pytest.raises(OSError, match="injected post-spawn"):
            subworkflows.call(fixture["file"])
        saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
        controller = next(p for p in spawned if p.operation == "start")
        assert controller.joined or any(
            row["pid"] in {None, controller.pid} and not row["joined"]
            for row in saved["controllers"]
        )
        assert saved["phase"] == ("starting" if persistent else "cleanup_required")
        assert saved["saved_result"] is None
        assert not Path(saved["output_manifest"]).exists()
        if persistent:
            assert saved["controllers"][-1]["pid"] is None
            assert saved["diagnostic"] == "controller_launch_unresolved"
            monkeypatch.setattr(records.Journal, "save", original_save)
            result = subworkflows.call(fixture["file"])
            assert result["outcome"] == "CHILD_UNCERTAIN"
            assert result["summary"] == "controller_identity_unresolved"
            assert saved["run_id"] is None
    finally:
        monkeypatch.setattr(records.Journal, "save", original_save)
        saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
        home = Path(saved["paths"]["state"])
        instance = saved["instance_id"]
        # Setup failure may precede admission. Inspect once for the exact target;
        # do not replace the original setup error with a never-admitted wait.
        receipt = next(
            (
                r
                for r in control.inspect_instance(instance, state_home=home)[
                    "controls"
                ]
                if r["request_id"] == saved["envelopes"]["start"]["request_id"]
                and r["request_digest"]
                == records.public_request_digest(saved["envelopes"]["start"])
                and r["operation"] == "start_run"
                and r["status"] == "applied"
            ),
            None,
        )
        try:
            if receipt is not None:
                run = receipt["run_id"]
                if failed:
                    live = wait_for(
                        lambda: control.inspect_run(instance, run, state_home=home)[
                            "inflight_invocations"
                        ]
                    )
                    assert live
                control.cancel_run(
                    instance, run, state_home=home, request_id=uuid.uuid4().hex
                )
            for process in spawned:
                # This fault owner retains actual handles even when the helper
                # cannot durably identify its detached controller.
                assert process.process is not None
                process.process.wait(timeout=5)
                assert process.process.poll() is not None
        finally:
            (tmp_path / "owned-aftermath.json").write_text(
                json.dumps(
                    {
                        "observed": observed,
                        "cleanup_receipt": receipt,
                        "processes": [
                            {
                                "operation": process.operation,
                                "pid": process.pid,
                                "returncode": process.process.poll(),
                                "stdout": bytes(process.output["stdout"]).decode(
                                    errors="replace"
                                ),
                                "stderr": bytes(process.output["stderr"]).decode(
                                    errors="replace"
                                ),
                            }
                            for process in spawned
                            if process.process is not None
                        ],
                    },
                    indent=2,
                )
            )
        if failed:
            assert receipt == observed["receipt"]
            settled = control.inspect_run(instance, run, state_home=home)
            assert settled["status"] == "cancelled" and subworkflows.closed(settled)
            assert (
                len(
                    [
                        r
                        for r in control.inspect_instance(instance, state_home=home)[
                            "controls"
                        ]
                        if r["operation"] == "start_run"
                    ]
                )
                == 1
            )
            if persistent:
                # Even actual writer closure cannot identify an unrecorded PID.
                assert (
                    subworkflows.reconcile(Path(req["call_home"]), req["call_key"])[
                        "phase"
                    ]
                    == "cleanup_required"
                )
                assert (
                    subworkflows.call(fixture["file"])["outcome"] == "CHILD_UNCERTAIN"
                )
            else:
                assert (
                    subworkflows.call(fixture["file"])["outcome"] == "CHILD_CANCELLED"
                )


@pytest.mark.parametrize("failure", ["open", "chmod", "spawn"])
def test_controller_preparation_failure_never_spawns_unrecorded_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from millrace_plus import subworkflow_process

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / failure)
    monkeypatch.chdir(fixture["project"])
    attempted = []
    opened = []
    original_open = Path.open
    original_chmod = subworkflow_process.os.chmod

    def track_open(self, *args, **kwargs):
        if self.parent.name == "logs":
            if failure == "open":
                raise OSError("injected log open failure")
            stream = original_open(self, *args, **kwargs)
            opened.append(stream)
            return stream
        return original_open(self, *args, **kwargs)

    def fail_chmod(path, mode, **kwargs):
        if Path(path).parent.name == "logs":
            raise OSError("injected log chmod failure")
        return original_chmod(path, mode, **kwargs)

    def no_spawn(*args, **kwargs):
        attempted.append(True)
        raise OSError("injected spawn failure")

    monkeypatch.setattr(Path, "open", track_open)
    monkeypatch.setattr(subworkflow_process.subprocess, "Popen", no_spawn)
    if failure == "chmod":
        monkeypatch.setattr(subworkflow_process.os, "chmod", fail_chmod)
    with pytest.raises(OSError, match="injected"):
        subworkflows.call(fixture["file"])
    req = fixture["request"]
    saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    assert attempted == ([True] if failure == "spawn" else [])
    assert all(stream.closed for stream in opened)
    assert saved["run_id"] is None and saved["instance_id"] is None
    assert not Path(saved["paths"]["state"]).exists()
    assert all(row["pid"] is None and row["joined"] for row in saved["controllers"])


def test_observer_waits_for_concurrent_cancel_reservation_to_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from subworkflow_support import write_request

    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(
        tmp_path / "concurrent-cancel", script="import time;time.sleep(20)\n"
    )
    fixture["request"]["wait_seconds"] = 30
    write_request(fixture)
    monkeypatch.chdir(fixture["project"])
    ready, reserved, observed, contending, release = (
        threading.Event() for _ in range(5)
    )
    original_refresh = subworkflows._refresh
    original_start = subworkflows.ControlProcess.start
    original_lock = records.Journal.lock
    owned, errors, results = [], [], {}
    req = fixture["request"]
    journal = records.Journal(Path(req["call_home"]), req["call_key"])

    def refresh(record):
        saved = original_refresh(record)
        if (
            threading.current_thread().name == "call-observer"
            and saved["run_id"] is not None
            and not ready.is_set()
        ):
            ready.set()
            assert reserved.wait(5), "cancel never reserved its controller"
            saved = record.load()  # Deliberately return the transient lock-free view.
            assert saved["controllers"][-1]["pid"] is None
            observed.set()
        return saved

    def start(process):
        owned.append(process)
        if process.operation == "cancel":
            reserved.set()
            assert release.wait(5), "cancel launch barrier was never released"
        return original_start(process)

    @contextmanager
    def lock(record, *, observer=False):
        if (
            threading.current_thread().name == "call-observer"
            and observed.is_set()
            and not observer
        ):
            contending.set()
        with original_lock(record, observer=observer):
            yield

    def observe():
        try:
            results["call"] = subworkflows.call(fixture["file"])
        except BaseException as exc:
            errors.append(exc)

    def cancel():
        try:
            results["cancel"] = subworkflows.cancel(
                Path(req["call_home"]), req["call_key"], uuid.uuid4().hex
            )
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(subworkflows, "_refresh", refresh)
    monkeypatch.setattr(subworkflows.ControlProcess, "start", start)
    monkeypatch.setattr(records.Journal, "lock", lock)
    observer = threading.Thread(target=observe, name="call-observer")
    sender = threading.Thread(target=cancel, name="explicit-cancel")
    observer.start()
    try:
        assert ready.wait(5), "observer never learned the actual child run"
        sender.start()
        assert contending.wait(5), "observer never reached the held journal lock"
        # The sender still holds the journal lock with its durable null-PID row.
        # The observer must wait, not publish uncertainty from that transient view.
        assert not Path(journal.load()["output_manifest"]).exists()
    finally:
        release.set()
        sender.join(10)
        observer.join(10)
        assert not sender.is_alive() and not observer.is_alive()
        for process in owned:
            if process.process is not None:
                process.process.wait(timeout=5)
        saved = journal.load()
        view = control.inspect_run(
            saved["instance_id"],
            saved["run_id"],
            state_home=Path(saved["paths"]["state"]),
        )
        assert view["status"] == "cancelled" and subworkflows.closed(view)
    assert errors == []
    assert results["call"]["outcome"] == "CHILD_CANCELLED"
    assert all(
        row["pid"] is not None and row["joined"]
        for row in journal.load()["controllers"]
    )


def test_cancel_before_dispatch_and_during_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "cancel")
    monkeypatch.chdir(fixture["project"])
    # Preparation is separately observable for deterministic boundary injection.
    saved = subworkflows.prepare(Path(str(fixture["file"])))
    req = fixture["request"]
    cancel_id = uuid.uuid4().hex
    subworkflows.cancel(Path(req["call_home"]), req["call_key"], cancel_id)
    result = subworkflows.call(Path(str(fixture["file"])))
    assert result["outcome"] == "CHILD_CANCELLED"
    after = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    assert after["run_id"] is None and after["instance_id"] is None
    assert after["cancel_intent"]["request_id"] == cancel_id
    assert not Path(saved["paths"]["state"]).exists()


@pytest.mark.parametrize("create_sent", [False, True])
def test_recovered_creating_call_replays_exact_create_then_prevents_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, create_sent: bool
) -> None:
    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "creating")
    monkeypatch.chdir(fixture["project"])
    saved = subworkflows.prepare(fixture["file"])
    req = fixture["request"]
    journal = records.Journal(Path(req["call_home"]), req["call_key"])
    subworkflows._copy(journal)
    saved = subworkflows._update(journal, phase="creating")
    original_create = saved["envelopes"]["create"]
    first = None
    if create_sent:
        first = control.execute_request(
            original_create, state_home=Path(saved["paths"]["state"])
        )
        assert first["status"] == "applied"
    cancel_id = uuid.uuid4().hex
    subworkflows.cancel(Path(req["call_home"]), req["call_key"], cancel_id)
    result = subworkflows.call(fixture["file"])
    assert result["outcome"] == "CHILD_CANCELLED"
    after = journal.load()
    assert after["envelopes"]["create"] == original_create
    assert after["envelopes"]["start"] is None and after["run_id"] is None
    assert after["instance_id"] == after["receipts"]["create"]["instance_id"]
    if first:
        assert after["receipts"]["create"] == first
    assert after["cancel_intent"]["request_id"] == cancel_id
    assert all(
        row["operation"] == "create" and row["joined"] for row in after["controllers"]
    )
    assert len(after["controllers"]) == 1
    view = control.inspect_instance(
        after["instance_id"], state_home=Path(after["paths"]["state"])
    )
    assert view["runs"] == []
    assert not any(row["operation"] == "start_run" for row in view["controls"])
    manifest = json.loads(Path(after["output_manifest"]).read_bytes())
    assert manifest["run_id"] is None and manifest["dispatch"] == "not_started"
    assert subworkflows.call(fixture["file"]) == result


def test_known_refused_create_under_cancel_settles_without_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "refused")
    monkeypatch.chdir(fixture["project"])
    saved = subworkflows.prepare(fixture["file"])
    req = fixture["request"]
    journal = records.Journal(Path(req["call_home"]), req["call_key"])
    subworkflows._copy(journal)
    saved = subworkflows._update(journal, phase="creating")
    (Path(saved["paths"]["workflow"]) / "workflow.json").write_text("{}")
    receipt = control.execute_request(
        saved["envelopes"]["create"], state_home=Path(saved["paths"]["state"])
    )
    assert receipt["status"] == "refused"
    assert subworkflows._receipt(journal, "create", receipt)
    subworkflows.cancel(Path(req["call_home"]), req["call_key"], uuid.uuid4().hex)
    result = subworkflows.call(fixture["file"])
    assert result["outcome"] == "CHILD_CANCELLED"
    assert result["summary"] == "dispatch_prevented"
    after = journal.load()
    assert after["instance_id"] is None and after["run_id"] is None
    assert after["receipts"]["create"] == receipt
    assert after["envelopes"]["start"] is None and after["controllers"] == []
    assert after["phase"] == "terminal"
    assert control.list_instances(state_home=Path(after["paths"]["state"])) == []
    assert json.loads(Path(after["output_manifest"]).read_bytes()) == {
        "format": "millrace-plus-subworkflow-result/v1",
        "call_identity": after["request_sha256"],
        "call_key": req["call_key"],
        "definition": after["definition"],
        "instance_id": None,
        "run_id": None,
        "dispatch": "not_started",
        "status": None,
        "observation": {},
        "receipts": {"create": receipt},
        "outcome": "CHILD_CANCELLED",
        "diagnostic": "dispatch_prevented",
        "controllers": [],
    }


def test_deadline_and_cleanup_survive_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "deadline")
    monkeypatch.chdir(fixture["project"])
    first = subworkflows.prepare(Path(str(fixture["file"])))
    second = subworkflows.prepare(Path(str(fixture["file"])))
    assert first["deadline_at"] == second["deadline_at"]
    req = fixture["request"]
    subworkflows.cancel(Path(req["call_home"]), req["call_key"], uuid.uuid4().hex)
    third = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    subworkflows.cancel(Path(req["call_home"]), req["call_key"], uuid.uuid4().hex)
    fourth = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    assert third["cleanup_deadline_at"] == fourth["cleanup_deadline_at"]


@pytest.mark.parametrize("boundary", ["create", "start", "learned_run"])
def test_crash_gaps_recover_exact_target_without_duplicate_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    from millrace_plus.router import control

    class Crash(BaseException):
        pass

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / boundary)
    monkeypatch.chdir(fixture["project"])
    original_launch, original_refresh = subworkflows._launch, subworkflows._refresh
    captured = []
    injected = False

    def crash_launch(journal, operation):
        nonlocal injected
        process = original_launch(journal, operation)
        if process is not None:
            captured.append(process)
        if operation == boundary and not injected:
            injected = True
            raise Crash()
        return process

    def crash_refresh(journal):
        nonlocal injected
        saved = original_refresh(journal)
        if boundary == "learned_run" and saved["run_id"] and not injected:
            injected = True
            raise Crash()
        return saved

    monkeypatch.setattr(subworkflows, "_launch", crash_launch)
    monkeypatch.setattr(subworkflows, "_refresh", crash_refresh)
    with pytest.raises(Crash):
        subworkflows.call(Path(str(fixture["file"])))
    for process in captured:
        if boundary == "learned_run":
            # call() detached its local pipes in this boundary's finally block.
            # Reap the actual owned handle before recovery, independently of
            # whether the short command happened to finish before the crash.
            assert process.process is not None
            process.process.wait(timeout=5)
        else:
            assert process.finish(5)
        assert process.process.poll() is not None
    monkeypatch.setattr(subworkflows, "_launch", original_launch)
    monkeypatch.setattr(subworkflows, "_refresh", original_refresh)
    result = subworkflows.call(Path(str(fixture["file"])))
    assert result["outcome"] == "CHILD_SUCCEEDED"
    req = fixture["request"]
    saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    assert saved["instance_id"] == saved["receipts"]["create"]["instance_id"]
    view = control.inspect_instance(
        saved["instance_id"], state_home=Path(saved["paths"]["state"])
    )
    assert len(view["runs"]) == 1
    assert len([r for r in view["controls"] if r["operation"] == "start_run"]) == 1
    assert saved["envelopes"]["create"]["request_id"] == records.request_id(
        saved["request"], "create_instance"
    )


def test_unresolved_start_is_never_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace_plus.router import control

    class Crash(BaseException):
        pass

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "unknown")
    monkeypatch.chdir(fixture["project"])
    original = subworkflows._launch

    def crash_before_send(journal, operation):
        if operation == "start":
            raise Crash()
        return original(journal, operation)

    monkeypatch.setattr(subworkflows, "_launch", crash_before_send)
    with pytest.raises(Crash):
        subworkflows.call(Path(str(fixture["file"])))
    monkeypatch.setattr(subworkflows, "_launch", original)
    assert subworkflows.call(Path(str(fixture["file"])))["outcome"] == "CHILD_UNCERTAIN"
    req = fixture["request"]
    saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    view = control.inspect_instance(
        saved["instance_id"], state_home=Path(saved["paths"]["state"])
    )
    assert view["runs"] == []


def test_cancelled_replay_then_fresh_key_after_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from subworkflow_support import write_request

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "fresh")
    monkeypatch.chdir(fixture["project"])
    req = fixture["request"]
    subworkflows.prepare(Path(str(fixture["file"])))
    subworkflows.cancel(Path(req["call_home"]), req["call_key"], uuid.uuid4().hex)
    first = subworkflows.call(Path(str(fixture["file"])))
    assert first["outcome"] == "CHILD_CANCELLED"
    assert subworkflows.call(Path(str(fixture["file"]))) == first
    req.update(call_key="fresh_key", output_ref="results/fresh.json")
    write_request(fixture)
    assert subworkflows.call(Path(str(fixture["file"])))["outcome"] == "CHILD_SUCCEEDED"


def test_deadline_cancels_active_child_and_joins_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from subworkflow_support import write_request

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "deadline-active", script="import time;time.sleep(20)\n")
    fixture["request"]["wait_seconds"] = 1
    write_request(fixture)
    monkeypatch.chdir(fixture["project"])
    result = subworkflows.call(Path(str(fixture["file"])))
    assert result["outcome"] == "CHILD_CANCELLED", result
    req = fixture["request"]
    saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    assert saved["cancel_intent"]["request_id"] == records.request_id(
        saved["request"], "deadline_cancel"
    )
    assert saved["observation"]["quiescent"]
    assert all(row["joined"] for row in saved["controllers"])
    assert subworkflows.call(Path(str(fixture["file"]))) == result


def test_cleanup_expiry_preserves_known_target_and_does_not_assert_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from subworkflow_support import write_request

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "uncertain", script="import time;time.sleep(20)\n")
    fixture["request"]["wait_seconds"] = 1
    write_request(fixture)
    monkeypatch.chdir(fixture["project"])
    original_launch, original_seconds = subworkflows._launch, subworkflows.seconds_left
    owned = []

    def no_cancel(journal, operation, **kwargs):
        if operation == "cancel":
            return None  # deterministic unavailable cancel transport
        process = original_launch(journal, operation, **kwargs)
        if process:
            owned.append(process)
        return process

    def expired(value):
        req = fixture["request"]
        try:
            saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
            if saved["cleanup_deadline_at"] == value and value is not None:
                return -1
        except records.SubworkflowError:
            pass
        return original_seconds(value)

    monkeypatch.setattr(subworkflows, "_launch", no_cancel)
    monkeypatch.setattr(subworkflows, "seconds_left", expired)
    result = subworkflows.call(Path(str(fixture["file"])))
    assert result["outcome"] == "CHILD_UNCERTAIN"
    req = fixture["request"]
    saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    assert saved["phase"] == "cleanup_required"
    assert saved["run_id"] and saved["instance_id"]
    assert saved["observation"]["inflight_invocations"]
    monkeypatch.setattr(subworkflows, "_launch", original_launch)
    monkeypatch.setattr(subworkflows, "seconds_left", original_seconds)
    subworkflows.cancel(Path(req["call_home"]), req["call_key"], uuid.uuid4().hex)
    # This in-process fault fixture retains the original Popen handles. Reap
    # only those known owned children after public cancellation, never a saved
    # journal PID (which could have been reused).
    for process in owned:
        process.process.wait(timeout=5)
    from subworkflow_support import wait_for

    wait_for(
        lambda: subworkflows.reconcile(Path(req["call_home"]), req["call_key"])["phase"]
        == "terminal"
    )


def test_waiting_plane_stays_observed_until_public_deadline_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy

    from subworkflow_support import graph, write_request

    from millrace_plus import catalog
    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "waiting", script="import time;time.sleep(20)\n")
    source = Path(str(fixture["definition"]))
    value = graph()
    producer = value["planes"]["main"]
    producer["workspace"] = "producer"
    consumer = copy.deepcopy(producer)
    consumer["workspace"] = "consumer"
    consumer["nodes"]["step"]["requires_terminal"] = [
        {"plane": "producer", "status": "succeeded"}
    ]
    value["planes"] = {"producer": producer, "consumer": consumer}
    (source / "workflow.json").write_text(json.dumps(value))
    fingerprint = catalog.workflow_fingerprint(source)
    fixture["request"]["definition"].update(
        sha256=fingerprint, version="sha256:" + fingerprint
    )
    fixture["request"]["wait_seconds"] = 1
    write_request(fixture)
    monkeypatch.chdir(fixture["project"])
    observed_wait = []
    original = subworkflows._refresh

    def observe(journal):
        saved = original(journal)
        if saved["run_id"]:
            run = control.inspect_run(
                saved["instance_id"],
                saved["run_id"],
                state_home=Path(saved["paths"]["state"]),
            )
            observed_wait.append(run["planes"]["consumer"]["status"] == "waiting")
        return saved

    monkeypatch.setattr(subworkflows, "_refresh", observe)
    result = subworkflows.call(Path(str(fixture["file"])))
    assert any(observed_wait)
    assert result["outcome"] == "CHILD_CANCELLED"
    req = fixture["request"]
    saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    for name in ("producer", "consumer"):
        assert (Path(saved["paths"]["project"]) / name).is_dir()
    assert saved["observation"]["quiescent"]
