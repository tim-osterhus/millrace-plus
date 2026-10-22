"""Lost replies, stale authority, credential reload and finite cleanup."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from millrace_plus.integrations import paperclip_bridge as bridge
from millrace_plus.integrations import paperclip_records as records

pytest_plugins = ["paperclip_support"]


def _observation_fixture(tmp_path):
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    (workflow / "workflow.json").write_text('{"planes":{}}')
    saved = {
        "instance_id": "a" * 32,
        "run_id": "b" * 32,
        "assignment": {
            "paths": {"workflow": str(workflow), "root": str(tmp_path)},
            "configuration": {"os_home": "local", "cleanup_seconds": 5},
            "identity": {},
            "binding_digest": "f" * 64,
        },
        "envelopes": {"start": None, "cancel": None},
        "observation": {},
        "phase": "observing",
        "cancel_intent": False,
    }
    full = {
        "instance_id": saved["instance_id"],
        "id": saved["run_id"],
        "status": "succeeded",
        "state_version": 2,
        "revision_id": "c" * 64,
        "quiescent": True,
        "inflight_invocations": [],
        "invocations": [],
        "completions": [],
        "controls": [],
        "transitions": [],
    }

    class Store:
        def save(self, value):
            pass

    class Client:
        def __init__(self, rows):
            self.rows, self.calls = rows, []

        def read(self, instance, run=None):
            assert instance == saved["instance_id"]
            assert run in {None, saved["run_id"]}
            self.calls.append((instance, run))
            if run is None:
                return {"instance_id": instance, "controls": [], "runs": self.rows}
            return copy.deepcopy(full)

    return Store(), saved, full, Client


@pytest.mark.parametrize("status", ["ready", "running", "waiting"])
def test_active_summary_observation_defers_full_history(tmp_path, status):
    store, saved, _, client_type = _observation_fixture(tmp_path)
    client = client_type(
        [
            {"id": "d" * 32, "status": "succeeded", "pause_reason": None},
            {"id": saved["run_id"], "status": status, "pause_reason": None},
        ]
    )
    assert bridge._refresh(store, saved, client, active_summary=True) is None
    assert client.calls == [(saved["instance_id"], None)]
    assert saved["observation"] == {}


@pytest.mark.parametrize("status", ["paused", "succeeded", "failed", "cancelled"])
def test_summary_boundary_requires_exact_full_view(tmp_path, status):
    store, saved, full, client_type = _observation_fixture(tmp_path)
    full["status"] = status
    client = client_type(
        [{"id": saved["run_id"], "status": status, "pause_reason": None}]
    )
    assert bridge._refresh(store, saved, client, active_summary=True) == full
    assert client.calls == [
        (saved["instance_id"], None),
        (saved["instance_id"], saved["run_id"]),
    ]


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"id": "d" * 32, "status": "succeeded", "pause_reason": None}],
        [{"id": "b" * 32, "status": "unsupported", "pause_reason": None}],
        [{"id": "b" * 32, "status": "running"}],
        [{"id": "b" * 32, "status": "running", "pause_reason": None}] * 2,
    ],
)
def test_invalid_summary_falls_back_to_exact_full_view(tmp_path, rows):
    store, saved, full, client_type = _observation_fixture(tmp_path)
    client = client_type(rows)
    assert bridge._refresh(store, saved, client, active_summary=True) == full
    assert client.calls[-1] == (saved["instance_id"], saved["run_id"])


@pytest.mark.parametrize(
    "reason", ["cancel", "cleanup", "pause_reason", "unknown_writer"]
)
def test_active_summary_cannot_hide_retained_closure_obligation(tmp_path, reason):
    store, saved, full, client_type = _observation_fixture(tmp_path)
    row = {"id": saved["run_id"], "status": "running", "pause_reason": None}
    full.update(
        status="paused",
        quiescent=False,
        invocations=[
            {"id": "e" * 32, "status": "completed", "writer_state": "unknown"}
        ],
        completions=[{"invocation_id": "e" * 32, "effective_writer": "unknown"}],
    )
    if reason == "cancel":
        saved["cancel_intent"] = True
    elif reason == "cleanup":
        saved["phase"] = "cleanup_required"
    elif reason == "pause_reason":
        row["pause_reason"] = "writer_unknown"
    else:
        saved["observation"] = {
            "invocations": full["invocations"],
            "completions": full["completions"],
        }
    client = client_type([row])
    view = bridge._refresh(store, saved, client, active_summary=True)
    assert view == full and not bridge.closed(view, attention=True)
    assert client.calls[-1] == (saved["instance_id"], saved["run_id"])


@pytest.mark.parametrize("trigger", ["regular", "abort", "deadline", "cleanup"])
def test_observer_wait_preserves_abort_and_first_deadlines(monkeypatch, trigger):
    from types import SimpleNamespace

    clock = [0.0]
    sleeps = []
    base = records.date(records.now())
    saved = {
        "cancel_intent": trigger == "cleanup",
        "deadline_at": (
            base + timedelta(seconds=0.125 if trigger == "deadline" else 30)
        ).isoformat(),
        "cleanup_deadline_at": (base + timedelta(seconds=0.125)).isoformat()
        if trigger == "cleanup"
        else None,
    }
    original = copy.deepcopy(saved)

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(
        bridge, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    monkeypatch.setattr(
        bridge, "now", lambda: (base + timedelta(seconds=clock[0])).isoformat()
    )
    for _ in range(3 if trigger == "regular" else 1):
        bridge._wait_for_observation(
            saved, lambda: trigger == "abort" and clock[0] >= 0.125
        )
    expected = 3.0 if trigger == "regular" else 0.15 if trigger == "abort" else 0.125
    assert clock[0] == pytest.approx(expected, abs=1e-6)
    assert all(0 < step <= 0.05 for step in sleeps)
    assert saved == original


def test_default_refresh_retains_full_closure_inspection(tmp_path):
    store, saved, full, client_type = _observation_fixture(tmp_path)
    client = client_type(
        [{"id": saved["run_id"], "status": "running", "pause_reason": None}]
    )
    assert bridge._refresh(store, saved, client) == full
    assert client.calls[-1] == (saved["instance_id"], saved["run_id"])


def test_observer_paces_http_and_stops_requests_after_closure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    store, saved, _, client_type = _observation_fixture(tmp_path)
    base = records.date(records.now())
    saved.update(
        deadline_at=(base + timedelta(seconds=10)).isoformat(),
        cleanup_deadline_at=None,
        receipts={},
        cleanup_required=True,
    )
    saved["envelopes"]["start"] = {}
    clock, reads = [0.0], []

    class Client(client_type):
        def read(self, instance, run=None):
            self.rows = [
                {
                    "id": saved["run_id"],
                    "status": "running" if clock[0] < 2 else "succeeded",
                    "pause_reason": None,
                }
            ]
            reads.append((round(clock[0], 6), run))
            return super().read(instance, run)

        def submit(self, *_):
            raise AssertionError("observation cannot dispatch a new operation")

    client = Client([])

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(
        bridge, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    monkeypatch.setattr(
        bridge, "now", lambda: (base + timedelta(seconds=clock[0])).isoformat()
    )
    result = bridge._observe(
        store,
        saved,
        client,
        {"attempt_id": "attempt", "agent_id": "agent"},
        lambda: False,
    )
    assert result["status"] == "succeeded" and saved["cleanup_required"] is False
    assert reads == [(0.0, None), (1.0, None), (2.0, None), (2.0, saved["run_id"])]
    assert clock[0] == pytest.approx(2)


@pytest.mark.parametrize(
    "old_status",
    [
        "paused",
        "succeeded",
        "failed",
        "cancelled",
        "unsupported",
        "wrong_run",
        "incompatible_revision",
    ],
)
def test_active_summary_cannot_reuse_stale_full_outcome(tmp_path, old_status):
    store, saved, full, client_type = _observation_fixture(tmp_path)
    saved["observation"] = {"id": saved["run_id"], "status": old_status}
    if old_status in {"wrong_run", "incompatible_revision"}:
        saved["observation"].update(status="running", graph_revision="d" * 64)
        if old_status == "wrong_run":
            saved["observation"]["id"] = "e" * 32
    full.update(status="running", quiescent=False)
    client = client_type(
        [
            {
                "id": saved["run_id"],
                "status": "running",
                "pause_reason": None,
                "revision_id": "c" * 64,
            }
        ]
    )
    assert bridge._refresh(store, saved, client, active_summary=True) == full
    assert client.calls[-1] == (saved["instance_id"], saved["run_id"])


def test_compatible_active_summary_preserves_last_full_and_cleanup(tmp_path):
    store, saved, _, client_type = _observation_fixture(tmp_path)
    saved["cleanup_required"] = True
    saved["observation"] = {
        "id": saved["run_id"],
        "status": "running",
        "graph_revision": "c" * 64,
        "invocations": [{"writer_state": "running"}],
        "completions": [],
    }
    previous = copy.deepcopy(saved["observation"])
    client = client_type(
        [
            {
                "id": saved["run_id"],
                "status": "running",
                "pause_reason": None,
                "revision_id": "c" * 64,
            }
        ]
    )
    assert bridge._refresh(store, saved, client, active_summary=True) is None
    assert saved["observation"] == previous and saved["cleanup_required"] is True


def test_running_orphan_keeps_uncertainty_through_bounded_cancel(tmp_path, monkeypatch):
    from types import SimpleNamespace

    store, saved, full, client_type = _observation_fixture(tmp_path)
    base = records.date(records.now())
    saved.update(
        deadline_at=(base + timedelta(seconds=1)).isoformat(),
        cleanup_deadline_at=None,
        receipts={},
        cleanup_required=True,
    )
    saved["envelopes"]["start"] = {}
    full.update(
        status="paused",
        quiescent=False,
        invocations=[
            {"id": "e" * 32, "status": "completed", "writer_state": "unknown"}
        ],
        completions=[{"invocation_id": "e" * 32, "effective_writer": "unknown"}],
    )
    clock, cancels, full_times = [0.0], [], []

    class Client(client_type):
        def read(self, instance, run=None):
            if run is not None:
                full_times.append(clock[0])
            return super().read(instance, run)

        def submit(self, envelope):
            assert envelope["operation"] == "cancel_run"
            cancels.append(copy.deepcopy(envelope))
            return {
                "contract": "millrace-os-router-call/v1",
                "home": "local",
                "request_id": envelope["request_id"],
                "request_digest": records.request_digest(envelope),
                "source": "os_owned_call",
                "phase": "call_active",
                "core_receipt": None,
            }

    client = Client(
        [{"id": saved["run_id"], "status": "running", "pause_reason": None}]
    )

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(
        bridge, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep)
    )
    monkeypatch.setattr(
        bridge, "now", lambda: (base + timedelta(seconds=clock[0])).isoformat()
    )
    result = bridge._observe(
        store,
        saved,
        client,
        {"attempt_id": "attempt", "agent_id": "agent"},
        lambda: False,
    )
    assert result["status"] == "uncertain" and saved["cleanup_required"] is True
    assert clock[0] == pytest.approx(6) and full_times[0] == pytest.approx(1)
    assert cancels and all(row == cancels[0] for row in cancels)
    assert cancels[0]["run_id"] == saved["run_id"]


def test_public_paused_binding_resume_requires_fresh_full_view(paperclip, monkeypatch):
    from uuid import uuid4

    from subworkflow_support import wait_for

    paperclip.script(
        "from pathlib import Path\nimport time\n"
        "if not Path('resume-ready').exists(): print('{\"outcome\":\"HOLD\"}')\n"
        "else:\n"
        " while not Path('finish').exists(): time.sleep(0.02)\n"
        " Path('answer.md').write_text('useful resumed result')\n"
        ' print(\'{"outcome":"READY","refs":["answer.md"]}\')\n'
    )
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert first["status"] == "attention_required"
    project = Path(first["result_root"]) / "project"
    (project / "resume-ready").touch()
    view = paperclip.client.inspect_run("local", first["instance_id"], first["run_id"])
    resume = {
        "format": "millrace-router-request/v2",
        "operation": "resume_plane",
        "instance_id": first["instance_id"],
        "run_id": first["run_id"],
        "plane_id": "main",
        "request_id": uuid4().hex,
        "expected_state_version": view["state_version"],
        "payload": {},
    }
    paperclip.workers.submit("local", resume)
    wait_for(
        lambda: paperclip.client.inspect_run(
            "local", first["instance_id"], first["run_id"]
        )["status"]
        == "running"
    )
    active_full = threading.Event()
    read = bridge.OSClient.read

    def capture(client, instance, run=None):
        current = read(client, instance, run)
        if run == first["run_id"] and current["status"] == "running":
            active_full.set()
        return current

    monkeypatch.setattr(bridge.OSClient, "read", capture)
    reports = []
    worker = threading.Thread(
        target=lambda: reports.append(
            bridge.run(paperclip.config_file, paperclip.assignment_file)
        )
    )
    worker.start()
    try:
        assert active_full.wait(2), (
            "resumed active run must replace stale full pause facts"
        )
    finally:
        (project / "finish").touch()
        worker.join(8)
    assert not worker.is_alive() and reports[0]["status"] == "succeeded", reports
    assert reports[0]["run_id"] == first["run_id"]
    assert paperclip.operations.count("create_instance") == 1
    assert paperclip.operations.count("start_run") == 1
    final = paperclip.client.inspect_run("local", first["instance_id"], first["run_id"])
    assert bridge.closed(final)


@pytest.mark.parametrize(
    "mutation,rehash",
    [
        ("issue", False),
        ("issue", True),
        ("company", True),
        ("origin", True),
        ("containing_key", True),
    ],
)
def test_corrupt_previous_identity_blocks_new_revision(
    paperclip, monkeypatch, mutation, rehash
) -> None:
    from subworkflow_support import wait_for

    paperclip.script(
        "import json,time\nfrom pathlib import Path\n"
        "m=json.loads(Path('subworkflow-inputs.json').read_bytes())\n"
        "if m['identity']['assignment_revision']==1: time.sleep(20)\n"
        "Path('answer.md').write_bytes(m['inputs']['request_markdown'].encode())\n"
        "print(json.dumps({'outcome':'READY','refs':['answer.md']}))\n"
    )
    paperclip.config["wait_seconds"] = 15
    paperclip.write()
    cancelled, park, parked, resume = (threading.Event() for _ in range(4))
    reports = []
    refresh = bridge._refresh
    worker = None

    def gated_refresh(*args, **kwargs):
        if threading.current_thread() is worker and park.is_set():
            parked.set()
            assert resume.wait(10), "test owner was not released"
        return refresh(*args, **kwargs)

    monkeypatch.setattr(bridge, "_refresh", gated_refresh)
    worker = threading.Thread(
        target=lambda: reports.append(
            bridge.run(
                paperclip.config_file,
                paperclip.assignment_file,
                cancelled=cancelled.is_set,
            )
        )
    )
    worker.start()
    original_path, moved_path, original = None, None, None
    try:

        def running():
            try:
                saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
                return saved if saved["run_id"] else None
            except (OSError, records.BridgeError):
                return None

        active = wait_for(running)
        park.set()
        assert parked.wait(3)
        original_path = Path(
            paperclip.config["binding_state_root"],
            active["assignment"]["binding_digest"],
            "call.json",
        )
        original = original_path.read_bytes()
        corrupted = json.loads(original)
        identity = corrupted["assignment"]["identity"]
        if mutation == "issue":
            identity["issue_id"] = "issue-other"
        elif mutation == "company":
            identity["company_id"] = "company-other"
        elif mutation == "origin":
            identity["paperclip_origin"] = "https://other.example"
        else:
            moved_path = original_path.parent.with_name("f" * 64)
            original_path.parent.rename(moved_path)
        if rehash:
            corrupted["assignment_sha256"] = records.digest(corrupted["assignment"])
            corrupted["authority_sha256"] = records.digest(
                {k: corrupted[k] for k in ("assignment", "created_at", "deadline_at")}
            )
            corrupted["record_sha256"] = records.digest(
                {k: v for k, v in corrupted.items() if k != "record_sha256"}
            )
        target = moved_path / "call.json" if moved_path else original_path
        target.write_bytes(records.canonical(corrupted))
        before_requests = copy.deepcopy(paperclip.requests)
        before_instance = copy.deepcopy(
            paperclip.client.inspect_instance("local", active["instance_id"])[
                "controls"
            ]
        )
        before_run = copy.deepcopy(
            paperclip.client.inspect_run(
                "local", active["instance_id"], active["run_id"]
            )["controls"]
        )
        paperclip.config["assignment_revisions"] = {"issue-1": 2}
        paperclip.write()
        result = bridge.run(paperclip.config_file, paperclip.assignment_file)
        assert result["status"] == "refused", result
        assert result["diagnostic"] == "invalid_binding", result
        assert paperclip.requests == before_requests
        assert (
            paperclip.client.inspect_instance("local", active["instance_id"])[
                "controls"
            ]
            == before_instance
        )
        assert (
            paperclip.client.inspect_run(
                "local", active["instance_id"], active["run_id"]
            )["controls"]
            == before_run
        )
        assert len(paperclip.client.list_instances("local")) == 1
    finally:
        if moved_path is not None:
            moved_path.rename(original_path.parent)
        if original is not None:
            original_path.write_bytes(original)
        paperclip.config["assignment_revisions"] = {}
        paperclip.write()
        cancelled.set()
        resume.set()
        worker.join(10)
    assert not worker.is_alive() and reports[0]["status"] == "cancelled"


def test_definitive_create_refusal_then_corrected_revision(paperclip) -> None:
    paperclip.config["instance_settings"]["runtime_defaults"] = {"unknown": True}
    paperclip.write()
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert first["status"] == "refused"
    saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    receipt = saved["receipts"]["create"]
    envelope = saved["envelopes"]["create"]
    assert receipt["status"] == "refused" and receipt["state_version"] == 0
    assert receipt["instance_id"] is None and receipt["run_id"] is None
    assert receipt["request_digest"] == records.request_digest(envelope)
    assert saved["phase"] == "terminal" and saved["cleanup_required"] is False
    assert saved["envelopes"]["start"] is None
    assert paperclip.client.list_instances("local") == []
    assert paperclip.operations == ["create_instance"]
    before = copy.deepcopy(paperclip.requests)
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "refused"
    )
    replayed = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    assert (
        replayed["envelopes"]["create"] == envelope
        and replayed["receipts"]["create"] == receipt
    )
    assert paperclip.requests == before
    del paperclip.config["instance_settings"]["runtime_defaults"]
    paperclip.write()
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["diagnostic"]
        == "assignment_conflict"
    )
    assert paperclip.requests == before
    paperclip.config["assignment_revisions"] = {"issue-1": 2}
    paperclip.write()
    corrected = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert corrected["status"] == "succeeded", corrected
    assert corrected["assignment"]["assignment_revision"] == 2
    assert len(paperclip.client.list_instances("local")) == 1
    assert paperclip.operations == ["create_instance", "create_instance", "start_run"]
    assert (
        Path(corrected["result_root"], corrected["refs"][0]).read_bytes()
        == paperclip.assignment["description"].encode()
    )


def test_lost_create_reply_after_os_restart_uses_exact_post_replay(paperclip) -> None:
    paperclip.lose.add("create_instance")
    paperclip.restart_after_loss = True
    report = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert report["status"] == "succeeded", report
    creates = [
        row for row in paperclip.requests if row["operation"] == "create_instance"
    ]
    assert len(creates) == 2 and creates[0] == creates[1]
    assert len(paperclip.client.list_instances("local")) == 1
    assert paperclip.operations.count("start_run") == 1


def test_unknown_start_inspects_original_receipt_without_new_launch(paperclip) -> None:
    paperclip.lose.add("start_run")
    report = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert report["status"] == "succeeded", report
    assert paperclip.operations.count("start_run") == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "key",
        "description",
        "pin",
        "settings",
        "paths",
        "envelope",
        "receipt",
        "run",
        "observation",
    ],
)
def test_binding_corruption_refuses_without_mutation(paperclip, mutation) -> None:
    report = bridge.run(paperclip.config_file, paperclip.assignment_file)
    path = Path(
        paperclip.config["binding_state_root"], report["binding_digest"], "call.json"
    )
    saved = json.loads(path.read_bytes())
    if mutation == "key":
        saved["assignment"]["identity"]["assignment_revision"] = 2
    elif mutation == "description":
        saved["assignment"]["description"] += "changed"
    elif mutation == "pin":
        saved["assignment"]["configuration"]["definition"]["sha256"] = "a" * 64
    elif mutation == "settings":
        saved["assignment"]["configuration"]["instance_settings"]["path"] = []
    elif mutation == "paths":
        saved["assignment"]["paths"]["project"] += "-other"
    elif mutation == "envelope":
        saved["envelopes"]["start"]["request_id"] = "a" * 32
    elif mutation == "receipt":
        saved["receipts"]["start"]["request_digest"] = "a" * 64
    elif mutation == "run":
        saved["run_id"] = "a" * 32
    else:
        saved["observation"]["id"] = "a" * 32
    # Recomputed checksums cannot replace semantic authority validation.
    saved["assignment_sha256"] = records.digest(saved["assignment"])
    saved["record_sha256"] = records.digest(
        {k: v for k, v in saved.items() if k != "record_sha256"}
    )
    path.write_bytes(records.canonical(saved))
    before = copy.deepcopy(paperclip.requests)
    result = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert result["status"] == "refused", result
    assert paperclip.requests == before


def test_racing_401_retains_exact_create_identity(paperclip) -> None:
    paperclip.failures["/api/v1/router/requests"] = 401
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert first["status"] == "uncertain" and first["diagnostic"] == "os_http_401"
    saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    original = saved["envelopes"]["create"]
    paperclip.failures.clear()
    paperclip.token = "z" * 48
    paperclip.credential.write_text(json.dumps({"token": paperclip.token}))
    result = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert result["status"] == "succeeded", result
    assert paperclip.requests[0] == original


def test_pin_replacement_refuses_without_create(paperclip) -> None:
    (paperclip.definition / "step.py").write_text("print('changed')\n")
    result = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert (
        result["status"] == "refused" and result["diagnostic"] == "definition_mismatch"
    )
    assert paperclip.operations == []


def test_persisted_deadline_cancels_and_replay_does_not_extend(paperclip) -> None:
    paperclip.script('import time\ntime.sleep(20)\nprint(\'{"outcome":"READY"}\')\n')
    paperclip.config["wait_seconds"] = 1
    paperclip.write()
    report = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert report["status"] == "cancelled", report
    saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    again = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert again["run_id"] == report["run_id"]
    assert (
        bridge.inspect(paperclip.config_file, paperclip.assignment_file)["deadline_at"]
        == saved["deadline_at"]
    )


def test_paused_unknown_writer_then_closure_aftermath(paperclip) -> None:
    paperclip.script('print(\'{"outcome":"HOLD"}\')\n')

    def unknown(view):
        view["quiescent"] = False
        view["invocations"][0]["writer_state"] = "unknown"
        view["completions"][0]["effective_writer"] = "unknown"
        return view

    paperclip.read_patch = unknown
    result = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert result["status"] == "uncertain"
    before = list(paperclip.operations)
    paperclip.config["assignment_revisions"] = {"issue-1": 2}
    paperclip.write()
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["diagnostic"]
        == "prior_binding_unsettled"
    )
    assert paperclip.operations == before
    paperclip.read_patch = None
    paperclip.config["assignment_revisions"] = {}
    paperclip.write()
    assert (
        bridge.run(
            paperclip.config_file, paperclip.assignment_file, cancelled=lambda: True
        )["status"]
        == "cancelled"
    )
    saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    assert saved["cleanup_required"] is False
    paperclip.config["assignment_revisions"] = {"issue-1": 2}
    paperclip.write()
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "attention_required"
    )


def test_cleanup_expiry_is_persisted_and_new_revision_refuses(
    paperclip, monkeypatch
) -> None:
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)

    def unresolved(view):
        view["status"] = "running"
        view["quiescent"] = False
        return view

    paperclip.read_patch = unresolved
    base = records.date(records.now())
    tick = [0]

    def clock(*, after=0):
        tick[0] += 1
        return (base + timedelta(seconds=20 + tick[0] + after)).isoformat()

    monkeypatch.setattr(bridge, "now", clock)
    report = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert (
        report["status"] == "uncertain" and report["diagnostic"] == "cleanup_deadline"
    )
    saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    deadline = saved["cleanup_deadline_at"]
    assert saved["phase"] == "cleanup_required" and saved["cleanup_required"]
    assert saved["run_id"] == first["run_id"]
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "uncertain"
    )
    assert (
        bridge.inspect(paperclip.config_file, paperclip.assignment_file)[
            "cleanup_deadline_at"
        ]
        == deadline
    )
    before = list(paperclip.operations)
    paperclip.config["assignment_revisions"] = {"issue-1": 2}
    paperclip.write()
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "refused"
    )
    assert paperclip.operations == before


def test_running_revision_and_concurrent_attempt_are_nonmutating(paperclip) -> None:
    from subworkflow_support import wait_for

    paperclip.script('import time\ntime.sleep(20)\nprint(\'{"outcome":"READY"}\')\n')
    cancelled = threading.Event()
    reports = []
    worker = threading.Thread(
        target=lambda: reports.append(
            bridge.run(
                paperclip.config_file,
                paperclip.assignment_file,
                cancelled=cancelled.is_set,
            )
        )
    )
    worker.start()
    try:
        wait_for(lambda: "start_run" in paperclip.operations)
        before = list(paperclip.operations)
        assert (
            bridge.run(paperclip.config_file, paperclip.assignment_file)["diagnostic"]
            == "attention_busy"
        )
        paperclip.config["assignment_revisions"] = {"issue-1": 2}
        paperclip.write()
        assert (
            bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
            == "refused"
        )
        assert paperclip.operations == before
    finally:
        cancelled.set()
        worker.join(10)
    assert not worker.is_alive() and reports[0]["status"] == "cancelled"


def test_proxy_disabled_and_credential_owner_mode_checked(
    paperclip, monkeypatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "succeeded"
    )
    from millrace_plus.integrations.paperclip_http import OSClient

    paperclip.credential.chmod(0o644)
    with pytest.raises(records.BridgeError, match="invalid_os_credential"):
        OSClient(paperclip.config).read("instance")
    paperclip.credential.chmod(0o600)


def test_redirect_refuses_without_following(paperclip) -> None:
    from millrace_plus.integrations.paperclip_http import OSClient

    paperclip.redirect = paperclip.origin + "/do-not-follow"
    with pytest.raises(records.BridgeError, match="os_redirect_refused"):
        OSClient(paperclip.config).read("instance")
    assert paperclip.operations == []


def test_forced_helper_death_retains_cleanup_and_exact_target(paperclip) -> None:
    from subworkflow_support import wait_for

    paperclip.script('import time\ntime.sleep(20)\nprint(\'{"outcome":"READY"}\')\n')
    source = Path(__file__).resolve().parents[2] / "src"
    code = "from millrace_plus.router.cli import cli;cli()"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            "paperclip",
            "run",
            "--config",
            str(paperclip.config_file),
            "--assignment",
            str(paperclip.assignment_file),
        ],
        env={
            "HOME": str(paperclip.root / "home"),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(source),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def running():
        try:
            saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
            return saved if saved["run_id"] else None
        except (OSError, records.BridgeError):
            return None

    try:
        active = wait_for(running)
        process.kill()
        process.communicate(timeout=3)
        assert process.returncode != 0
        retained = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
        assert retained["cleanup_required"] is True
        report = bridge.run(
            paperclip.config_file, paperclip.assignment_file, cancelled=lambda: True
        )
        assert report["status"] == "cancelled"
        assert report["run_id"] == active["run_id"]
        assert paperclip.operations.count("start_run") == 1
        assert (
            bridge.inspect(paperclip.config_file, paperclip.assignment_file)[
                "cleanup_required"
            ]
            is False
        )
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=3)


@pytest.mark.parametrize(
    "field,value",
    [
        ("instance_id", "wrong-instance"),
        ("invocations", None),
        ("completions", [{"wrong": True}]),
    ],
)
def test_malformed_public_read_is_uncertain_without_dispatch(
    paperclip, field, value
) -> None:
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    paperclip.read_patch = lambda view: {**view, field: value}
    before = list(paperclip.operations)
    result = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert result["status"] == "uncertain"
    assert result["run_id"] == first["run_id"]
    assert paperclip.operations == before
