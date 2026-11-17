"""Finite service protocol tests with fake public peers, never native workers."""

from __future__ import annotations

import copy
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest
from persistent_lad_support import Clock, capabilities, observation, receipt

from millrace_plus import catalog
from millrace_plus import lad_service_runtime as runtime
from millrace_plus.lad_service import Service
from millrace_plus.local_intake import IntakeError, canonical
from millrace_plus.subworkflow_records import SubworkflowError


class Peer:
    """Exact saved-envelope dedup and separate local/OS public identity views."""

    def __init__(self):
        self.sent = []
        self.observed = []
        self.responses = {}
        self.instances = {}
        self.runs = {}
        self.local_override = None
        self.joined = True
        self.allocations = 0

    def capabilities(self, *, deadline):
        return capabilities()

    def submit(self, envelope, *, deadline):
        self.sent.append(copy.deepcopy(envelope))
        key = envelope["request_id"]
        if key in self.responses:
            return copy.deepcopy(self.responses[key])
        operation = envelope["operation"]
        instance = envelope["instance_id"]
        run = envelope["run_id"]
        if operation == "create_instance":
            self.allocations += 1
            instance = uuid.uuid4().hex
            self.instances[instance] = {
                "format": "millrace-router-instance/v2",
                "instance_id": instance,
                "state_version": 1,
                "runs": [],
                "controls": [],
                **copy.deepcopy(envelope["payload"]),
            }
        core = receipt(envelope, instance=instance, run=run)
        core["plane_id"] = envelope["plane_id"]
        self.responses[key] = observation(envelope, joined=self.joined, core=core)
        return copy.deepcopy(self.responses[key])

    def observe(self, envelope, *, deadline):
        self.observed.append(copy.deepcopy(envelope))
        return copy.deepcopy(self.responses[envelope["request_id"]])

    def read(self, instance, run=None, *, deadline):
        return copy.deepcopy(
            self.instances[instance] if run is None else self.runs[(instance, run)]
        )

    def inspect_instance(self, instance, *, state_home):
        if self.local_override is not None:
            return self.local_override(instance, state_home)
        return self.read(instance, deadline=0)

    def inspect_run(self, instance, run, *, state_home):
        return self.read(instance, run, deadline=0)

    @contextmanager
    def exclusive_run_snapshot(
        self, instance, run, *, state_home, expected_state_version
    ):
        view = self.inspect_run(instance, run, state_home=state_home)
        if view["state_version"] != expected_state_version:
            raise IntakeError("stale_state_version")
        yield view


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    target, core = tmp_path / "target", tmp_path / "core"
    target.mkdir()
    core.mkdir()
    (target / "dirty.txt").write_bytes(b"preserve dirty target\n")
    profile = tmp_path / "profile.json"
    profile.write_bytes(
        canonical(
            {
                "format": "millrace-plus-workflow-os-profile/v1",
                "os_origin": "http://127.0.0.1:8731",
                "os_home": "local",
                "os_credential_file": str(tmp_path / "credentials.json"),
            }
        )
        + b"\n"
    )
    definition = tmp_path / "definition.json"
    definition.write_bytes(
        canonical(
            {
                "selector": "bundled:lad",
                "version": catalog._plus_version(),
                "sha256": catalog.workflow_fingerprint(catalog.bundled_root() / "lad"),
            }
        )
        + b"\n"
    )
    peer = Peer()
    monkeypatch.setattr(runtime, "_core", lambda: peer)
    return SimpleNamespace(
        home=tmp_path / "service",
        target=target,
        core=core,
        profile=profile,
        definition=definition,
        peer=peer,
    )


def initialize(f):
    return runtime.initialize(
        f.home, f.target, f.profile, f.definition, f.core, client=f.peer
    )


def test_init_pin_and_handshake_precede_start_and_preserve_dirty_target(setup):
    f = setup
    result = initialize(f)
    assert result["activation_ready"] is True
    assert [row["operation"] for row in f.peer.sent] == ["create_instance"]
    service = Service(f.home)
    intent, record = service.intake.lifecycle()
    assert intent["core_state_home"] == str(f.core)
    assert "core_state_home" not in service.config
    assert record["handshake"]["instance_id"] == result["instance_id"]
    assert result["start_request"] == record["start"]["envelope"]
    assert (f.target / "dirty.txt").read_bytes() == b"preserve dirty target\n"
    assert initialize(f) == result
    assert f.peer.allocations == 1


@pytest.mark.parametrize(
    "fault", ["wrong-home", "wrong-settings", "local-read-failure"]
)
def test_postcreate_handshake_failure_retains_one_parent_and_no_start(setup, fault):
    f = setup

    def bad(instance, state_home):
        if fault == "local-read-failure":
            raise IntakeError("unknown_instance")
        view = f.peer.read(instance, deadline=0)
        if fault == "wrong-home":
            view["instance_id"] = "f" * 32
        else:
            view["host_paths"] = {}
        return view

    f.peer.local_override = bad
    with pytest.raises(IntakeError):
        initialize(f)
    service = Service(f.home)
    intent, record = service.intake.lifecycle()
    assert record["create"]["receipt"]["status"] == "applied"
    assert record["start"]["envelope"] is None and record["handshake"] is None
    assert len(f.peer.sent) == 1 and f.peer.allocations == 1
    frozen = canonical(intent)
    f.peer.local_override = None
    result = initialize(f)
    assert result["activation_ready"] and f.peer.allocations == 1
    assert canonical(service.intake.lifecycle()[0]) == frozen


def test_changed_core_pin_refuses_before_new_send(setup):
    f = setup
    initialize(f)
    other = f.core.parent / "other-core"
    other.mkdir()
    with pytest.raises(IntakeError, match="initialization_conflict"):
        runtime.initialize(
            f.home, f.target, f.profile, f.definition, other, client=f.peer
        )
    assert len(f.peer.sent) == 1


def test_capability_and_local_api_fail_before_ownership(setup, monkeypatch):
    f = setup
    monkeypatch.setattr(
        runtime,
        "_core",
        lambda: (_ for _ in ()).throw(IntakeError("local_core_incompatible")),
    )
    with pytest.raises(IntakeError, match="local_core_incompatible"):
        initialize(f)
    assert not f.home.exists()
    assert not (f.target / ".millrace-plus-target-owner.json").exists()
    assert f.peer.sent == []


def test_known_unjoined_parent_create_cannot_expose_start(setup):
    f = setup
    f.peer.joined = False
    with pytest.raises(IntakeError, match="parent_create_holder_unjoined"):
        initialize(f)
    record = Service(f.home).intake.lifecycle()[1]
    assert record["start"]["envelope"] is None
    assert f.peer.allocations == 1


def test_submission_capacity_runs_before_acceptance_and_keeps_core_free(
    setup, monkeypatch
):
    f = setup
    initialize(f)
    service = Service(f.home)
    request = f.home.parent / "task.md"
    request.write_bytes(b"original task")
    monkeypatch.setattr(runtime, "_core", lambda: pytest.fail("submit imported Core"))
    original = service.submit("first", request)
    monkeypatch.setattr(
        runtime,
        "preflight_capacity",
        lambda record: (_ for _ in ()).throw(SubworkflowError("record_too_large")),
    )
    with pytest.raises(SubworkflowError, match="record_too_large"):
        service.submit("second", request)
    rows = service.intake.tasks()
    assert len(rows) == 1 and rows[0]["task_id"] == original["task_id"]
    service.cancel(str(original["task_id"]), uuid.uuid4().hex)
    assert service.intake.tasks()[0]["status"] == "cancelled"


def test_socket_wait_rechecks_after_binding_and_timeout_does_not_write(setup):
    f = setup
    initialize(f)
    service = Service(f.home)
    before = (f.home / "intake.sqlite3").read_bytes()
    assert runtime.wait_intake(service, seconds=0.001) == "IDLE"
    assert (f.home / "intake.sqlite3").read_bytes() == before
    request = f.home.parent / "wake.md"
    request.write_text("queued")
    service.submit("wake", request)
    assert runtime.wait_intake(service, seconds=0.001) == "WAKE"
    assert not (f.home / "intake.sock").exists()


def activate(f):
    initialized = initialize(f)
    service = Service(f.home)
    intent, record = service.intake.lifecycle()
    sent = record["start"]["envelope"]
    parent, run_id = initialized["instance_id"], uuid.uuid4().hex
    started = receipt(sent, instance=parent, run=run_id)
    started.update(
        state_version=1, instance_state_version=2, retryable=False, code=None
    )
    f.peer.instances[parent].update(
        state_version=2,
        controls=[started],
        runs=[{"id": run_id, "status": "running", "state_version": 1}],
    )
    f.peer.runs[(parent, run_id)] = {
        "id": run_id,
        "instance_id": parent,
        "start_request_id": sent["request_id"],
        "status": "running",
        "state_version": 1,
        "host_settings": {"state_version": 1, **intent["settings"]},
        "quiescent": True,
        "planes": {"service": {"cursor": "claim"}},
        "inflight_invocations": [],
        "invocations": [],
        "completions": [],
        "controls": [],
    }
    f.peer.responses[sent["request_id"]] = observation(sent, core=started)
    active = runtime.Runtime(service, client=f.peer)
    active.parent()
    return active


def claimed(f):
    active = activate(f)
    request = f.home.parent / "request.md"
    request.write_bytes(b"original task\r\n")
    task_id = active.service.submit("first", request)["task_id"]
    active.service.claim_next(writer_guard=active.verify)
    task = active.service.intake.tasks(task_id)[0]
    active._prepare_call(task)
    return active, task


def test_wrapped_child_dispatch_keeps_verify_and_unknown_on_authority_failure(
    setup, monkeypatch
):
    from millrace_plus import subworkflow_v2 as helper

    active, task = claimed(setup)
    storage, saved = active.call(task)
    connection = runtime.WorkflowOSClient(active.service.config["os_profile"], "local")
    events = []

    def request(method, path, **kwargs):
        events.append(method)
        assert method == "OPTIONS"
        return capabilities()

    def refuse():
        events.append("verify")
        assert helper._reservation(storage.load(), "create") == "unknown"
        raise IntakeError("lifecycle_authority_changed")

    monkeypatch.setattr(connection, "_request", request)
    monkeypatch.setattr(active, "verify", refuse)
    with pytest.raises(IntakeError, match="lifecycle_authority_changed"):
        helper._dispatch(
            storage, saved, "create", runtime.LocalClient(active, connection), 12345.0
        )
    assert events == ["OPTIONS", "verify"]
    retained = storage.load()
    assert helper._reservation(retained, "create") == "unknown"
    assert retained["envelopes"] == saved["envelopes"]
    assert retained["os_observations"] == []


def closed_child(f, *, status="failed", joined=True, writer="stopped"):
    from millrace_plus import subworkflow_v2 as helper
    from millrace_plus.subworkflow_persistent_records import reduce_full_views
    from millrace_plus.subworkflow_records import envelope

    active, task = claimed(f)
    journal, record = active.call(task)
    helper._copy(journal, record)
    create = record["envelopes"]["create"]
    created = f.peer.submit(create, deadline=0)
    helper._observe(record, "create", created)
    child = record["instance_id"]
    child_run = uuid.uuid4().hex
    start = envelope(
        "start_run",
        record["request"],
        instance_id=child,
        expected=1,
        payload={"edit_mode": "locked"},
    )
    record["envelopes"]["start"] = start
    started = receipt(start, instance=child, run=child_run)
    started.update(
        state_version=1, instance_state_version=2, retryable=False, code=None
    )
    seen = observation(start, joined=joined, core=started)
    f.peer.responses[start["request_id"]] = seen
    helper._observe(record, "start", seen)
    f.peer.instances[child].update(
        state_version=2,
        controls=[started],
        runs=[{"id": child_run, "status": status, "state_version": 8}],
    )
    invocation = uuid.uuid4().hex
    run = {
        "id": child_run,
        "instance_id": child,
        "start_request_id": start["request_id"],
        "status": status,
        "state_version": 8,
        "quiescent": True,
        "host_settings": {"state_version": 1, **record["settings"]},
        "planes": {},
        "inflight_invocations": [],
        "controls": [],
        "seam_occurrences": [],
        "seam_acknowledgements": [],
        "invocations": [
            {"id": invocation, "status": "completed", "writer_state": writer}
        ],
        "completions": [{"invocation_id": invocation, "effective_writer": writer}],
    }
    f.peer.runs[(child, child_run)] = run
    record["observation"] = reduce_full_views(
        f.peer.instances[child],
        run,
        instance_id=child,
        run_id=child_run,
        start_request_id=start["request_id"],
        paths=record["paths"],
        settings=record["settings"],
    )
    record["phase"] = "terminal"
    journal.save(record)
    return active, task, journal, record


def abandonment(task, *, version=8):
    return (
        canonical(
            {
                "format": "millrace-plus-lad-abandonment-intent/v1",
                "acknowledge_abandonment": True,
                "expected_task_version": task["version"],
                "expected_state_version": version,
            }
        )
        + b"\n"
    )


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_explicit_abandonment_keeps_failure_and_replays_one_terminal_cas(setup, status):
    from millrace_plus.lad_service_disposition import settle

    f = setup
    active, task, _, record = closed_child(f, status=status)
    request_id = uuid.uuid4().hex
    raw = abandonment(task)
    answer = settle(
        active,
        task,
        decision="acknowledge-" + status,
        request_id=request_id,
        operator_raw=raw,
    )
    assert answer["disposition"] == "abandoned-" + status
    reopened = Service(f.home)
    terminal = reopened.intake.tasks(task["task_id"])[0]
    assert terminal["status"] == status and terminal["version"] == task["version"] + 1
    assert terminal["request_bytes"] == b"original task\r\n"
    restarted = runtime.Runtime(reopened, client=f.peer)
    assert (
        settle(
            restarted,
            terminal,
            decision="acknowledge-" + status,
            request_id=request_id,
            operator_raw=raw,
        )
        == answer
    )
    assert reopened.intake.tasks(task["task_id"])[0]["version"] == terminal["version"]
    with pytest.raises(IntakeError, match="decision_conflict"):
        settle(
            restarted,
            terminal,
            decision="acknowledge-" + status,
            request_id=request_id,
            operator_raw=raw + b" ",
        )
    assert not (
        Path(record["paths"]["project"]) / "foreground/lad-state/session.json"
    ).exists()


@pytest.mark.parametrize(
    "joined,writer", [(False, "stopped"), (None, "stopped"), (True, "unknown")]
)
def test_false_null_or_unknown_physical_authority_preserves_active_slot(
    setup, joined, writer
):
    from millrace_plus.lad_service_disposition import settle

    active, task, journal, record = closed_child(setup, joined=joined, writer=writer)
    if joined is None:
        row = record["os_observations"][-1]
        row.update(source="core_receipt", holder_joined=None, phase="receipt_observed")
        journal.save(record)
    with pytest.raises(IntakeError):
        settle(
            active,
            task,
            decision="acknowledge-failed",
            request_id=uuid.uuid4().hex,
            operator_raw=abandonment(task),
        )
    assert Service(setup.home).intake.tasks(task["task_id"])[0]["status"] == "claimed"


def test_success_cannot_abandon_missing_close_ack(setup):
    from millrace_plus.lad_service_disposition import settle

    active, task, _, _ = closed_child(setup, status="succeeded")
    with pytest.raises(IntakeError, match="explicit_abandonment_required"):
        settle(
            active,
            task,
            decision="settle",
            request_id=uuid.uuid4().hex,
            operator_raw=abandonment(task),
        )
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "claimed"


@pytest.mark.parametrize("when", ["publication", "cas", "after-commit"])
def test_proof_publication_and_cas_crash_never_release_twice(setup, monkeypatch, when):
    from millrace_plus import lad_service_disposition as disposition

    active, task, _, _ = closed_child(setup)
    request_id, raw = uuid.uuid4().hex, abandonment(task)
    real_retain, real_decide = disposition.retain, active.service.intake.decide
    if when == "publication":

        def fail(service, raw, label):
            if label == "disposition":
                raise OSError("injected publication failure")
            return real_retain(service, raw, label)

        monkeypatch.setattr(disposition, "retain", fail)
    else:

        def fail(*args, **kwargs):
            if when == "after-commit":
                real_decide(*args, **kwargs)
            raise OSError("injected CAS/reply failure")

        monkeypatch.setattr(active.service.intake, "decide", fail)
    with pytest.raises(OSError):
        disposition.settle(
            active,
            task,
            decision="acknowledge-failed",
            request_id=request_id,
            operator_raw=raw,
        )
    reopened = Service(setup.home)
    row = reopened.intake.tasks(task["task_id"])[0]
    assert row["status"] == ("failed" if when == "after-commit" else "claimed")
    assert list((setup.home / "evidence").iterdir())
    if when == "after-commit":
        receipt = disposition.settle(
            runtime.Runtime(reopened, client=setup.peer),
            row,
            decision="acknowledge-failed",
            request_id=request_id,
            operator_raw=raw,
        )
        assert receipt["disposition"] == "abandoned-failed"
        assert (
            reopened.intake.tasks(task["task_id"])[0]["version"] == task["version"] + 1
        )


def test_proven_no_run_cancel_preserves_no_synthetic_run_or_view_proof(setup):
    active, task = claimed(setup)
    result = active.cancel(task["task_id"], uuid.uuid4().hex)
    assert result["disposition"] == "cancelled-before-start"
    task = Service(setup.home).intake.tasks(task["task_id"])[0]
    assert task["status"] == "cancelled"
    assert setup.peer.allocations == 1  # Parent only; child create was never sent.
    files = list((setup.home / "evidence").glob("*.json"))
    assert files and not any(
        path.name.startswith(("run-", "view-proof-")) for path in files
    )


def test_acknowledge_no_run_settlement_preserves_short_operation_deadline(
    setup, monkeypatch
):
    from millrace_plus import subworkflow_v2 as helper

    active, task = claimed(setup)
    journal, record = active.call(task)
    helper._copy(journal, record)
    created = setup.peer.submit(record["envelopes"]["create"], deadline=0)
    helper._observe(record, "create", created)
    journal.save(record)
    child = record["instance_id"]
    evidence = setup.home.parent / "no-run-budget.txt"
    evidence.write_text("acknowledge failed before start")
    clock, reads = Clock(), []
    clock.now = 10
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    original_read = active.client.read

    def read(instance, run=None, *, deadline):
        reads.append((instance, deadline))
        assert deadline == 10.5
        return original_read(instance, run, deadline=deadline)

    monkeypatch.setattr(active.client, "read", read)
    result = active.decide(
        task["task_id"],
        "acknowledge-failed",
        uuid.uuid4().hex,
        evidence,
        _deadline=10.5,
    )
    assert result["disposition"] == "failed-before-start"
    assert (child, 10.5) in reads
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "failed"


def test_current_snapshot_exclusion_spans_proof_and_cas(setup, monkeypatch):
    from millrace_plus import lad_service_disposition as disposition

    active, task, _, _ = closed_child(setup)
    guarded = {"active": False}
    real_scope = setup.peer.exclusive_run_snapshot

    @contextmanager
    def scope(*args, **kwargs):
        with real_scope(*args, **kwargs) as view:
            guarded["active"] = True
            try:
                yield view
            finally:
                guarded["active"] = False

    monkeypatch.setattr(setup.peer, "exclusive_run_snapshot", scope)
    real_retain = disposition.retain

    def retain(service, raw, label):
        assert guarded["active"]
        return real_retain(service, raw, label)

    monkeypatch.setattr(disposition, "retain", retain)
    real_decide = active.service.intake.decide

    def decide(*args, **kwargs):
        assert guarded["active"]
        return real_decide(*args, **kwargs)

    monkeypatch.setattr(active.service.intake, "decide", decide)
    monkeypatch.setattr(
        setup.peer, "submit", lambda *a, **k: pytest.fail("send inside guard")
    )
    monkeypatch.setattr(
        setup.peer, "observe", lambda *a, **k: pytest.fail("network inside guard")
    )
    result = disposition.settle(
        active,
        task,
        decision="acknowledge-failed",
        request_id=uuid.uuid4().hex,
        operator_raw=abandonment(task),
    )
    assert result["disposition"] == "abandoned-failed" and not guarded["active"]


def test_unknown_raw_child_control_cannot_borrow_other_join(setup):
    from millrace_plus.lad_service_disposition import settle

    active, task, _, record = closed_child(setup)
    child, run_id = record["instance_id"], record["run_id"]
    setup.peer.runs[(child, run_id)]["controls"].append(
        {
            "request_id": uuid.uuid4().hex,
            "request_digest": "a" * 64,
            "instance_id": child,
            "run_id": run_id,
            "plane_id": None,
            "operation": "cancel_run",
            "status": "applied",
            "state_version": 8,
            "retryable": False,
            "code": None,
        }
    )
    with pytest.raises(IntakeError, match="unknown_child_control"):
        settle(
            active,
            task,
            decision="acknowledge-failed",
            request_id=uuid.uuid4().hex,
            operator_raw=abandonment(task),
        )
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "claimed"


def test_retained_true_survives_later_exact_receipt_only_null(setup):
    from millrace_plus.lad_service_disposition import settle

    active, task, journal, record = closed_child(setup)
    later = copy.deepcopy(record["os_observations"][-1])
    later.update(source="core_receipt", holder_joined=None, phase="receipt_observed")
    record["os_observations"].append(later)
    journal.save(record)
    result = settle(
        active,
        task,
        decision="acknowledge-failed",
        request_id=uuid.uuid4().hex,
        operator_raw=abandonment(task),
    )
    assert result["disposition"] == "abandoned-failed"
    assert journal.load()["os_observations"][-1]["holder_joined"] is None


def test_stale_core_version_refuses_without_terminal_task_write(setup):
    from millrace_plus.lad_service_disposition import settle

    active, task, _, _ = closed_child(setup)
    with pytest.raises(IntakeError, match="stale_state_version"):
        settle(
            active,
            task,
            decision="acknowledge-failed",
            request_id=uuid.uuid4().hex,
            operator_raw=abandonment(task, version=7),
        )
    assert Service(setup.home).intake.tasks(task["task_id"])[0]["status"] == "claimed"


def test_tampered_accepted_full_view_ref_refuses_reopen(setup):
    from millrace_plus.lad_service_disposition import settle

    active, task, _, _ = closed_child(setup)
    settle(
        active,
        task,
        decision="acknowledge-failed",
        request_id=uuid.uuid4().hex,
        operator_raw=abandonment(task),
    )
    evidence = next((setup.home / "evidence").glob("run-*.json"))
    original = evidence.read_bytes()
    evidence.write_bytes(original + b" ")
    with pytest.raises(IntakeError, match="evidence_changed"):
        Service(setup.home).intake.tasks()
    assert evidence.read_bytes() == original + b" "


def test_preparation_failure_without_manifest_holds_and_never_infers_child_running(
    setup, monkeypatch
):
    active, task = claimed(setup)
    monkeypatch.setattr(
        runtime,
        "_advance_bytes",
        lambda *a, **k: (_ for _ in ()).throw(
            SubworkflowError("observation_window_expired")
        ),
    )
    result = active.node("advance")
    assert result["outcome"] == "HOLD"
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "claimed"
    claim = task["claim"]
    assert not (setup.home / "project/service" / claim["output_ref"]).exists()
    assert setup.peer.allocations == 1


def test_stop_never_cancels_parent_while_child_disposition_is_missing(setup):
    active, task, _, _ = closed_child(setup)
    parent, parent_run = active.record["instance_id"], active.record["run_id"]
    setup.peer.runs[(parent, parent_run)]["status"] = "paused"
    setup.peer.instances[parent]["runs"][0]["status"] = "paused"
    request_id = uuid.uuid4().hex
    result = active.stop(request_id)
    assert (
        result["status"] == "pending"
        and result["reason"] == "child_disposition_required"
    )
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "held"
    sent_parent_cancels = [
        row
        for row in setup.peer.sent
        if row["operation"] == "cancel_run" and row["instance_id"] == parent
    ]
    assert sent_parent_cancels == []
    intent, record = active.service.intake.lifecycle()
    saved = record["stops"][request_id]
    assert saved["intent"]["task_id"] == task["task_id"]
    assert saved["cancel"]["reservation"] == "unsent"


def parent_request(active, f, *, operation="resume_run", expected=1, **changes):
    sent = runtime._env(
        operation,
        uuid.uuid4().hex,
        instance=active.record["instance_id"],
        run=active.record["run_id"],
        expected=expected,
    )
    sent.update(changes)
    path = f.home.parent / (sent["request_id"] + ".json")
    path.write_bytes(canonical(sent) + b"\n")
    return path, sent


def test_recorded_parent_control_replay_is_get_only_and_keeps_original_version(setup):
    active = activate(setup)
    path, sent = parent_request(active, setup)
    first = active.parent_control(path)
    posted = len(setup.peer.sent)
    assert first["holder_joined"] is True
    parent, run = active.record["instance_id"], active.record["run_id"]
    setup.peer.runs[(parent, run)]["state_version"] = 9
    setup.peer.instances[parent]["runs"][0]["state_version"] = 9
    assert active.parent_control(path) == first
    assert len(setup.peer.sent) == posted
    assert setup.peer.observed[-1] == sent
    stored = Service(setup.home).intake.lifecycle()[1]["maintenance"][
        "parent:" + sent["request_id"]
    ]
    assert stored["call"]["envelope"]["expected_state_version"] == 1
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(IntakeError, match="parent_control_conflict"):
        active.parent_control(path)
    assert len(setup.peer.sent) == posted


@pytest.mark.parametrize(
    "operation,expected",
    [
        ("pause_run", 1),
        ("cancel_run", False),
        ("resume_run", None),
        ("resume_run", True),
        ("recover_run", 0),
        ("recover_run", 9),
    ],
)
def test_parent_control_invalid_or_fresh_stale_version_never_posts(
    setup, operation, expected
):
    active = activate(setup)
    path, _ = parent_request(active, setup, operation=operation, expected=expected)
    before = len(setup.peer.sent)
    with pytest.raises(IntakeError):
        active.parent_control(path)
    assert len(setup.peer.sent) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"instance_id": "f" * 32},
        {"run_id": "e" * 32},
        {"plane_id": "service"},
        {"operation": "start_run"},
        {"payload": {"edit_mode": "locked"}},
    ],
)
def test_parent_control_target_or_effect_mismatch_never_posts(setup, changes):
    active = activate(setup)
    path, _ = parent_request(active, setup, **changes)
    before = len(setup.peer.sent)
    with pytest.raises(IntakeError):
        active.parent_control(path)
    assert len(setup.peer.sent) == before


def test_ambiguous_parent_control_send_is_never_reposted(setup, monkeypatch):
    active = activate(setup)
    path, sent = parent_request(active, setup)
    real_submit = setup.peer.submit

    def failed_reply(envelope, *, deadline):
        real_submit(envelope, deadline=deadline)
        raise SubworkflowError("os_unavailable")

    monkeypatch.setattr(setup.peer, "submit", failed_reply)
    with pytest.raises(SubworkflowError, match="os_unavailable"):
        active.parent_control(path)
    posted = len(setup.peer.sent)
    row = Service(setup.home).intake.lifecycle()[1]["maintenance"][
        "parent:" + sent["request_id"]
    ]["call"]
    assert row["reservation"] == "unknown"
    assert active.parent_control(path)["holder_joined"] is True
    assert len(setup.peer.sent) == posted


def saved_stop(active, request_id):
    """Owned unsent stop reservation, before any external operation."""
    before = copy.deepcopy(active.record)
    active.record["stops"][request_id] = {
        "intent": {
            "parent_instance_id": active.record["instance_id"],
            "parent_run_id": active.record["run_id"],
            "task_id": None,
            "claim_sha256": None,
            "task_version": None,
            "request_sha256": None,
        },
        "phase": "pause-claims",
        "pause": runtime._row(
            runtime._env(
                "pause_run",
                runtime._operation_id(request_id, "parent-pause"),
                instance=active.record["instance_id"],
                run=active.record["run_id"],
            )
        ),
        "cancel": runtime._row(
            runtime._env(
                "cancel_run",
                runtime._operation_id(request_id, "parent-cancel"),
                instance=active.record["instance_id"],
                run=active.record["run_id"],
            )
        ),
    }
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    return active.record["stops"][request_id]["pause"]


def saved_child_maintenance(active, task, call, request_id):
    from millrace_plus.lad_service_disposition import retain

    operator = retain(active.service, b"Explicit same-child continuation\n", "operator")
    active.service.intake.decide(
        request_id,
        {"task_id": task["task_id"], "decision": "continue", "evidence": operator},
        check=active._require_active,
    )
    before = copy.deepcopy(active.record)
    active.record["maintenance"][request_id] = {
        "task_id": task["task_id"],
        "operator": operator,
        "instance_id": call["instance_id"],
        "run_id": call["run_id"],
        "pause": runtime._row(None),
        "recover": runtime._row(None),
        "resume": runtime._row(
            runtime._env(
                "resume_run",
                runtime._operation_id(request_id, "child-resume"),
                instance=call["instance_id"],
                run=call["run_id"],
                expected=8,
            )
        ),
        "recovery_before": None,
        "recovery_proof": None,
    }
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    return active.record["maintenance"][request_id]["resume"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_prevalidated_send_has_one_probe_and_possible_post_replays_get_only(
    setup, monkeypatch, ambiguous
):
    active = activate(setup)
    stop_id = uuid.uuid4().hex
    row = saved_stop(active, stop_id)
    sent = copy.deepcopy(row["envelope"])
    connection = runtime.WorkflowOSClient(active.service.config["os_profile"], "local")
    methods = []

    def request(method, path, **kwargs):
        methods.append(method)
        if method == "OPTIONS":
            return capabilities()
        if method == "POST":
            saved = Service(setup.home).intake.lifecycle()[1]["stops"][stop_id]["pause"]
            assert saved["reservation"] == "unknown" and saved["envelope"] == sent
            result = setup.peer.submit(sent, deadline=kwargs["deadline"])
            if ambiguous:
                raise SubworkflowError("os_unavailable")
            return result
        assert method == "GET"
        return setup.peer.observe(sent, deadline=kwargs["deadline"])

    monkeypatch.setattr(connection, "_request", request)
    if ambiguous:
        with pytest.raises(SubworkflowError, match="os_unavailable"):
            runtime._send(
                active.service, active.intent, active.record, row, connection, 10**10
            )
        assert row["reservation"] == "unknown"
    else:
        runtime._send(
            active.service, active.intent, active.record, row, connection, 10**10
        )
    assert methods == ["OPTIONS", "POST"]
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    saved = reopened.record["stops"][stop_id]["pause"]
    runtime._send(
        reopened.service, reopened.intent, reopened.record, saved, connection, 10**10
    )
    assert methods == ["OPTIONS", "POST", "OPTIONS", "GET"]
    assert saved["envelope"] == sent and runtime._joined(reopened.service, saved)
    assert (
        sum(value["request_id"] == sent["request_id"] for value in setup.peer.sent) == 1
    )


def test_send_capability_failure_preserves_unsent_lifecycle(setup, monkeypatch):
    active = activate(setup)
    row = saved_stop(active, uuid.uuid4().hex)
    before = copy.deepcopy(active.record)
    posted = copy.deepcopy(setup.peer.sent)
    monkeypatch.setattr(
        setup.peer, "capabilities", lambda **kwargs: {**capabilities(), "home": "other"}
    )
    with pytest.raises(SubworkflowError):
        runtime._send(
            active.service, active.intent, active.record, row, setup.peer, 10**10
        )
    assert active.record == before == Service(setup.home).intake.lifecycle()[1]
    assert row["reservation"] == "unsent" and setup.peer.sent == posted


@pytest.fixture
def budgeted_http(setup, monkeypatch):
    from millrace_plus import workflow_os_client

    active, clock, requests = activate(setup), Clock(), []
    credential = setup.profile.parent / "credentials.json"
    credential.write_bytes(canonical({"token": "a" * 32}))
    credential.chmod(0o600)
    connection = runtime.WorkflowOSClient(
        active.service.config["os_profile"], "local", clock=clock
    )
    active.connection = connection
    active.client = runtime.LocalClient(active, connection)
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    inspect_run, save = setup.peer.inspect_run, runtime._save

    def inspect_slow(instance, run, *, state_home):
        if any(
            item.get("scope") == "parent" and item["call"]["reservation"] == "unsent"
            for item in active.record["maintenance"].values()
        ):
            clock.sleep(1.7)
        return inspect_run(instance, run, state_home=state_home)

    def save_slow(service, intent, before, record):
        save(service, intent, before, record)
        if any(
            item.get("scope") == "parent"
            and item["call"]["reservation"] == "unknown"
            and before["maintenance"][key]["call"]["reservation"] == "unsent"
            for key, item in record["maintenance"].items()
        ):
            clock.sleep(1.62)

    @contextmanager
    def open_request(request, *, timeout):
        method = request.get_method()
        requests.append((method, clock.now, timeout))
        receipt_get = method == "GET" and urlsplit(request.full_url).path.startswith(
            "/api/v1/router/requests/"
        )
        assert 0 < timeout <= (30 if receipt_get else 2)
        if method == "OPTIONS":
            clock.sleep(0.15)
            value = capabilities()
        elif method == "POST":
            sent = runtime.strict_json(request.data)["request"]
            value = setup.peer.submit(sent, deadline=clock.now + timeout)
        else:
            raise HTTPError(request.full_url, 404, "", {}, None)
        yield SimpleNamespace(status=200, read=lambda _size: canonical(value))

    monkeypatch.setattr(setup.peer, "inspect_run", inspect_slow)
    monkeypatch.setattr(runtime, "_save", save_slow)
    monkeypatch.setattr(
        workflow_os_client,
        "build_opener",
        lambda *_handlers: SimpleNamespace(open=open_request),
    )
    return SimpleNamespace(active=active, clock=clock, requests=requests)


@pytest.mark.parametrize("caller_deadline", [None, 4.0, 60.0])
def test_parent_control_local_work_uses_operation_budget_with_two_second_http_cap(
    setup, budgeted_http, caller_deadline
):
    active, clock = budgeted_http.active, budgeted_http.clock
    path, sent = parent_request(active, setup)
    kwargs = {} if caller_deadline is None else {"_deadline": caller_deadline}
    result = active.parent_control(path, **kwargs)
    assert result["holder_joined"] is True and clock.now == pytest.approx(3.47)
    assert [row[0] for row in budgeted_http.requests] == ["OPTIONS", "POST"]
    bound = 30 if caller_deadline is None else min(30, caller_deadline)
    assert budgeted_http.requests[-1][2] == pytest.approx(min(2, bound - clock.now))
    saved = Service(setup.home).intake.lifecycle()[1]["maintenance"][
        "parent:" + sent["request_id"]
    ]["call"]
    assert saved["reservation"] == "observed" and saved["envelope"] == sent


def test_expired_send_refuses_before_unknown_reservation(setup, monkeypatch):
    active = activate(setup)
    row = saved_stop(active, uuid.uuid4().hex)
    before, posted, clock = copy.deepcopy(active.record), len(setup.peer.sent), Clock()
    monkeypatch.setattr(runtime.time, "monotonic", clock)

    def capabilities_at_expiry(*, deadline):
        clock.now = deadline
        return capabilities()

    monkeypatch.setattr(setup.peer, "capabilities", capabilities_at_expiry)
    with pytest.raises(SubworkflowError, match="observation_window_expired"):
        runtime._send(
            active.service, active.intent, active.record, row, setup.peer, 2.0
        )
    assert row["reservation"] == "unsent" and len(setup.peer.sent) == posted
    assert Service(setup.home).intake.lifecycle()[1] == before


def test_operation_expiry_during_cas_stays_unknown_and_replay_is_get_only(
    setup, budgeted_http
):
    active, clock = budgeted_http.active, budgeted_http.clock
    path, sent = parent_request(active, setup)
    with pytest.raises(SubworkflowError, match="observation_window_expired"):
        active.parent_control(path, _deadline=2.5)
    saved = Service(setup.home).intake.lifecycle()[1]["maintenance"][
        "parent:" + sent["request_id"]
    ]["call"]
    assert saved["reservation"] == "unknown" and saved["envelope"] == sent
    assert [row[0] for row in budgeted_http.requests] == ["OPTIONS"]
    with pytest.raises(SubworkflowError, match="os_http_404"):
        active.parent_control(path, _deadline=clock.now + 5)
    assert [row[0] for row in budgeted_http.requests] == ["OPTIONS", "OPTIONS", "GET"]
    assert budgeted_http.requests[-1][2] == pytest.approx(4.85)
    assert (
        Service(setup.home).intake.lifecycle()[1]["maintenance"][
            "parent:" + sent["request_id"]
        ]["call"]
        == saved
    )


def test_send_preserves_http_subclass_submit_override(setup, monkeypatch):
    active = activate(setup)
    stop_id = uuid.uuid4().hex
    row = saved_stop(active, stop_id)
    submitted = []

    class CustomHTTP(runtime.WorkflowOSClient):
        def submit(self, envelope, *, deadline):
            stored = Service(setup.home).intake.lifecycle()[1]["stops"][stop_id][
                "pause"
            ]
            assert stored["reservation"] == "unknown"
            submitted.append((copy.deepcopy(envelope), deadline))
            return setup.peer.submit(envelope, deadline=deadline)

    connection = CustomHTTP(active.service.config["os_profile"], "local")
    monkeypatch.setattr(connection, "capabilities", lambda **kwargs: capabilities())
    monkeypatch.setattr(
        connection,
        "_submit_prevalidated",
        lambda *args, **kwargs: pytest.fail("subclass submit bypassed"),
    )
    deadline = runtime.time.monotonic() + 30
    runtime._send(
        active.service, active.intent, active.record, row, connection, deadline
    )
    assert submitted == [(row["envelope"], deadline)]
    assert runtime._joined(active.service, row)


def corrupt_call(row, fault):
    if fault in {
        "operation",
        "request_id",
        "instance_id",
        "run_id",
        "plane_id",
        "payload",
        "version",
    }:
        row["envelope"].update(
            {
                "operation": {"operation": "cancel_run"},
                "request_id": {"request_id": "f" * 32},
                "instance_id": {"instance_id": "e" * 32},
                "run_id": {"run_id": "d" * 32},
                "plane_id": {"plane_id": "foreground"},
                "payload": {"payload": {"unselected": True}},
                "version": {"expected_state_version": True},
            }[fault]
        )
    elif fault == "reservation":
        row["reservation"] = "sent"
    elif fault == "reservation-type":
        row["reservation"] = []
    elif fault == "history-type":
        row["observations"] = {}
    elif fault == "receipt":
        row["receipt"] = receipt(
            row["envelope"],
            instance=row["envelope"]["instance_id"],
            run=row["envelope"]["run_id"],
        )
    else:
        row["observations"] = [observation(row["envelope"], joined=True)]


@pytest.mark.parametrize("purpose", ["stop", "child"])
@pytest.mark.parametrize(
    "fault",
    [
        "operation",
        "request_id",
        "instance_id",
        "run_id",
        "plane_id",
        "payload",
        "version",
        "reservation",
        "reservation-type",
        "receipt",
        "history",
        "history-type",
    ],
)
def test_corrupt_unsent_lifecycle_reopen_refuses_before_post(setup, purpose, fault):
    if purpose == "stop":
        active = activate(setup)
        request_id = uuid.uuid4().hex
        row = saved_stop(active, request_id)
    else:
        active, task, _, call = closed_child(setup, status="paused")
        request_id = uuid.uuid4().hex
        row = saved_child_maintenance(active, task, call, request_id)
    before = copy.deepcopy(active.record)
    corrupt_call(row, fault)
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    database = (setup.home / "intake.sqlite3").read_bytes()
    posted = len(setup.peer.sent)
    with pytest.raises((IntakeError, SubworkflowError)):
        runtime.Runtime(Service(setup.home), client=setup.peer)
    assert len(setup.peer.sent) == posted
    assert (setup.home / "intake.sqlite3").read_bytes() == database
    assert active.service.intake.lifecycle()[0] == active.intent


def test_send_revalidates_attached_unsent_row_and_submit_keeps_core_free(
    setup, monkeypatch
):
    active = activate(setup)
    request_id = uuid.uuid4().hex
    row = saved_stop(active, request_id)
    accepted = active.service.intake.lifecycle()
    posted = len(setup.peer.sent)
    row["envelope"]["instance_id"] = "f" * 32
    with pytest.raises((IntakeError, SubworkflowError)):
        runtime._send(
            active.service, active.intent, active.record, row, setup.peer, 10**10
        )
    assert len(setup.peer.sent) == posted
    assert active.service.intake.lifecycle() == accepted
    active.service.intake.save_lifecycle(active.intent, accepted[1], active.record)
    monkeypatch.setattr(
        runtime, "_core", lambda: pytest.fail("preflight imported Core")
    )
    request = setup.home.parent / "structural-preflight.md"
    request.write_text("accepted only after structural validation")
    with pytest.raises((IntakeError, SubworkflowError)):
        active.service.submit("blocked", request)
    assert active.service.intake.tasks() == []


def recovery_peer(
    f, monkeypatch, *, pause_sets_gate=True, recover_joined=True, plane_joined=True
):
    """Synthetic public full views; command protocol tests, not runtime proof."""
    real_submit = f.peer.submit

    def submit(sent, *, deadline):
        if (
            sent["request_id"] in f.peer.responses
            or sent["operation"] == "create_instance"
        ):
            return real_submit(sent, deadline=deadline)
        response = real_submit(sent, deadline=deadline)
        key = (sent["instance_id"], sent["run_id"])
        run = f.peer.runs[key]
        run["state_version"] += 1
        if sent["operation"] == "pause_run":
            run["status"] = "paused"
            run["run_gate"] = (
                {"reason": "operator_pause", "request_id": sent["request_id"]}
                if pause_sets_gate
                else None
            )
        elif sent["operation"] == "resume_run":
            run["status"] = "running"
            run["run_gate"] = None
        elif sent["operation"] == "resume_plane":
            run["planes"][sent["plane_id"]].update(
                status="ready",
                pause_reason=None,
                resume_target=None,
            )
            if run.get("run_gate") is None:
                run["status"] = "running"
        core = response["core_receipt"]
        core.update(
            state_version=run["state_version"],
            retryable=False,
            code=None,
            action={
                "recover_run": "recovered",
                "resume_run": "resumed",
                "resume_plane": "resumed",
                "pause_run": "paused",
            }.get(sent["operation"], "applied"),
        )
        run["controls"].append(copy.deepcopy(core))
        inventory = f.peer.instances[sent["instance_id"]]["runs"][0]
        inventory.update(status=run["status"], state_version=run["state_version"])
        if sent["operation"] == "recover_run":
            response["holder_joined"] = recover_joined
        elif sent["operation"] == "resume_plane":
            response["holder_joined"] = plane_joined
        f.peer.responses[sent["request_id"]] = copy.deepcopy(response)
        return copy.deepcopy(response)

    monkeypatch.setattr(f.peer, "submit", submit)
    from millrace_plus import lad_service_disposition as disposition

    monkeypatch.setattr(
        disposition,
        "_load_worker",
        lambda _: {"continuation": {"cleanup_required": False}},
    )


def lost_start(active, journal, record, f):
    sent = record["envelopes"]["start"]
    core = record["receipts"]["start"]
    lost = observation(sent, joined=None, core=core, source="core_receipt")
    lost["phase"] = "receipt_observed"
    record["os_observations"] = [
        row
        for row in record["os_observations"]
        if row["request_id"] != sent["request_id"]
    ] + [lost]
    journal.save(record)
    f.peer.responses[sent["request_id"]] = copy.deepcopy(lost)
    return sent["request_id"]


def paused_parent(active, f):
    parent = f.peer.runs[(active.record["instance_id"], active.record["run_id"])]
    parent["status"] = "paused"
    f.peer.instances[active.record["instance_id"]]["runs"][0]["status"] = "paused"


def test_paused_without_full_run_gate_cannot_send_recover(setup, monkeypatch):
    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch, pause_sets_gate=False)
    evidence = setup.home.parent / "continue.txt"
    evidence.write_text("same child only")
    request_id = uuid.uuid4().hex
    with pytest.raises(IntakeError, match="run_wide_pause_required"):
        active.decide(task["task_id"], "continue", request_id, evidence)
    operations = [sent["operation"] for sent in setup.peer.sent]
    assert "pause_run" in operations and "recover_run" not in operations
    item = Service(setup.home).intake.lifecycle()[1]["maintenance"][request_id]
    assert item["recovery_proof"] is None and item["recover"]["envelope"] is None
    assert active.service.intake.tasks(task["task_id"])[0]["claim"] == task["claim"]


def test_real_full_gate_precedes_fresh_recovery_and_reopen(setup, monkeypatch):
    from millrace_plus.lad_service_disposition import _recovery_for, read_ref

    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    old_id = lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    evidence = setup.home.parent / "continue.txt"
    evidence.write_text("same child only")
    request_id = uuid.uuid4().hex
    active.decide(task["task_id"], "continue", request_id, evidence)
    assert [sent["operation"] for sent in setup.peer.sent][-3:] == [
        "pause_run",
        "recover_run",
        "resume_run",
    ]
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    _, saved = reopened.call(reopened.service.intake.tasks(task["task_id"])[0])
    ref = _recovery_for(reopened, saved, old_id)
    assert ref is not None
    proof = runtime.strict_json(read_ref(reopened.service, ref))
    before = runtime.strict_json(read_ref(reopened.service, proof["before"]))
    after = runtime.strict_json(read_ref(reopened.service, proof["after"]))
    assert (
        before["run"]["run_gate"] is not None and after["run"]["run_gate"] is not None
    )
    assert proof["run_id"] == record["run_id"]
    assert saved["os_observations"][-1]["holder_joined"] is None


def test_call_source_forwards_short_caller_deadline_and_preserves_default_windows(
    setup, monkeypatch
):
    active, task, _, _ = closed_child(setup, status="paused")
    clock, deadlines = Clock(), []
    clock.now = 10
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    original_read = active.client.read

    def read(instance, run=None, *, deadline):
        deadlines.append(deadline)
        return original_read(instance, run, deadline=deadline)

    monkeypatch.setattr(active.client, "read", read)
    active.call(task, _deadline=10.5)
    assert deadlines == [10.5, 10.5]
    deadlines.clear()
    active.call(task)
    assert deadlines == [12, 12]


@pytest.mark.parametrize("caller_deadline", [None, 20.0, 60.0])
def test_decide_maintenance_and_reconcile_share_original_operation_deadline(
    setup, monkeypatch, caller_deadline
):
    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    evidence = setup.home.parent / "operation-budget.txt"
    evidence.write_text("same child recovery")
    clock, deadlines = Clock(), []
    clock.now = 10
    bound = 40 if caller_deadline is None else min(40, caller_deadline)
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    original_read, original_caps = active.client.read, setup.peer.capabilities
    original_file = runtime._read_exact

    def delayed_evidence(path, *args, **kwargs):
        value = original_file(path, *args, **kwargs)
        if path == evidence:
            clock.sleep(3)
        return value

    def read(instance, run=None, *, deadline):
        deadlines.append(deadline)
        assert deadline == bound
        return original_read(instance, run, deadline=deadline)

    def caps(*, deadline):
        deadlines.append(deadline)
        assert deadline == bound
        clock.sleep(0.1)
        return original_caps(deadline=deadline)

    monkeypatch.setattr(runtime, "_read_exact", delayed_evidence)
    monkeypatch.setattr(active.client, "read", read)
    monkeypatch.setattr(setup.peer, "capabilities", caps)
    kwargs = {} if caller_deadline is None else {"_deadline": caller_deadline}
    active.decide(task["task_id"], "continue", uuid.uuid4().hex, evidence, **kwargs)
    assert deadlines and all(deadline == bound for deadline in deadlines)
    assert 13 < clock.now < bound
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "running"


@pytest.mark.parametrize(
    "interruption", ["before-save", "after-save", "legacy-partial"]
)
def test_recovery_preparation_saves_snapshot_and_original_envelope_together(
    setup, monkeypatch, interruption
):
    from millrace_plus.lad_service_disposition import read_ref

    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    evidence = setup.home.parent / "recovery-preparation.txt"
    evidence.write_text("same child recovery")
    request_id = uuid.uuid4().hex
    real_save = runtime._save
    prepared = {}

    def interrupted(service, intent, before, current):
        item = current["maintenance"].get(request_id)
        previous = before["maintenance"].get(request_id)
        if (
            item is not None
            and item["recovery_before"] is not None
            and previous["recovery_before"] is None
        ):
            snapshot = runtime.strict_json(read_ref(service, item["recovery_before"]))
            sent = item["recover"]["envelope"]
            assert sent is not None
            assert sent["expected_state_version"] == snapshot["run"]["state_version"]
            assert item["recover"]["reservation"] == "unsent"
            prepared.update(copy.deepcopy(item))
            if interruption != "before-save":
                real_save(service, intent, before, current)
            raise RuntimeError("preparation interrupted")
        return real_save(service, intent, before, current)

    monkeypatch.setattr(runtime, "_save", interrupted)
    with pytest.raises(RuntimeError, match="preparation interrupted"):
        active.decide(task["task_id"], "continue", request_id, evidence)
    monkeypatch.setattr(runtime, "_save", real_save)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    saved = reopened.record["maintenance"][request_id]
    if interruption == "before-save":
        assert saved["recovery_before"] is None
        assert saved["recover"]["envelope"] is None
    else:
        assert saved["recovery_before"] == prepared["recovery_before"]
        assert saved["recover"] == prepared["recover"]
    recover_id = prepared["recover"]["envelope"]["request_id"]
    assert not any(sent["request_id"] == recover_id for sent in setup.peer.sent)
    if interruption == "legacy-partial":
        before = copy.deepcopy(reopened.record)
        saved["recover"] = runtime._row(None)
        reopened.service.intake.save_lifecycle(reopened.intent, before, reopened.record)
        key = (record["instance_id"], record["run_id"])
        setup.peer.runs[key]["state_version"] += 1
        setup.peer.instances[key[0]]["runs"][0]["state_version"] += 1
        reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
        with pytest.raises(IntakeError, match="stale_state_version"):
            reopened.decide(task["task_id"], "continue", request_id, evidence)
        saved = Service(setup.home).intake.lifecycle()[1]["maintenance"][request_id]
        assert saved["recovery_before"] == prepared["recovery_before"]
        assert saved["recover"] == prepared["recover"]
        assert not any(sent["request_id"] == recover_id for sent in setup.peer.sent)
    else:
        reopened.decide(task["task_id"], "continue", request_id, evidence)
        saved = Service(setup.home).intake.lifecycle()[1]["maintenance"][request_id]
        assert saved["recover"]["envelope"] == prepared["recover"]["envelope"]
        assert sum(sent["request_id"] == recover_id for sent in setup.peer.sent) == 1


@pytest.mark.parametrize("later", ["null", "false", "true", "true-then-false"])
def test_historical_recovery_cannot_override_latest_exact_false_observation(
    setup, monkeypatch, later
):
    from millrace_plus import lad_service_disposition as disposition

    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_id = lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    evidence = setup.home.parent / "recovery-later-observation.txt"
    evidence.write_text("same child recovery")
    active.decide(task["task_id"], "continue", uuid.uuid4().hex, evidence)
    _, saved = active.call(active.service.intake.tasks(task["task_id"])[0])
    sent = saved["envelopes"]["start"]
    core = saved["receipts"]["start"]
    values = (
        [True, False]
        if later == "true-then-false"
        else [{"null": None, "false": False, "true": True}[later]]
    )
    for joined in values:
        seen = observation(
            sent,
            joined=joined,
            core=core,
            source="core_receipt" if joined is None else "os_owned_call",
        )
        if joined is None:
            seen["phase"] = "receipt_observed"
        saved["os_observations"].append(seen)
    journal.save(saved)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    _, saved = reopened.call(reopened.service.intake.tasks(task["task_id"])[0])
    old = next(
        row
        for row in disposition.task_sent_calls(saved, reopened.record)
        if row["envelope"]["request_id"] == lost_id
    )
    if later == "false":
        assert disposition._recovery_for(reopened, saved, lost_id) is None
        assert reopened._recovery_observation(saved) == (None, False)
        with pytest.raises(IntakeError, match="holder_recovery_required"):
            disposition._physical(reopened, saved, allow_recovery=True)
        assert old in disposition._uncovered_sent_calls(reopened, saved)
    else:
        physical = disposition._physical(reopened, saved, allow_recovery=True)
        assert disposition._recovery_for(reopened, saved, lost_id) is not None
        assert old not in disposition._uncovered_sent_calls(reopened, saved)
        if later == "null":
            assert any("recovery" in item for item in physical["sent_calls"])
        else:
            assert runtime._joined(reopened.service, old)
    assert reopened.service.intake.tasks(task["task_id"])[0]["status"] == "running"


def test_lost_prior_maintenance_join_uses_ordinary_recovery_but_never_abandonment(
    setup, monkeypatch
):
    from millrace_plus import lad_service_disposition as disposition

    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    recovery_peer(setup, monkeypatch)
    old_decision = uuid.uuid4().hex
    row = saved_child_maintenance(active, task, record, old_decision)
    runtime._send(active.service, active.intent, active.record, row, setup.peer, 10**10)
    before = copy.deepcopy(active.record)
    seen = observation(
        row["envelope"], joined=None, core=row["receipt"], source="core_receipt"
    )
    seen["phase"] = "receipt_observed"
    row["observations"] = [seen]
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    setup.peer.responses[row["envelope"]["request_id"]] = copy.deepcopy(seen)
    run = setup.peer.runs[(record["instance_id"], record["run_id"])]
    run["status"] = "paused"
    run["run_gate"] = None
    setup.peer.instances[record["instance_id"]]["runs"][0]["status"] = "paused"
    evidence = setup.home.parent / "recover-old-maintenance.txt"
    evidence.write_text("fresh recovery")
    active.decide(task["task_id"], "continue", uuid.uuid4().hex, evidence)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    _, saved = reopened.call(reopened.service.intake.tasks(task["task_id"])[0])
    with setup.peer.exclusive_run_snapshot(
        record["instance_id"],
        record["run_id"],
        state_home=setup.core,
        expected_state_version=run["state_version"],
    ):
        physical = disposition._physical(reopened, saved, allow_recovery=True)
        assert any("recovery" in item for item in physical["sent_calls"])
        with pytest.raises(IntakeError, match="holder_unjoined"):
            disposition._physical(reopened, saved, allow_recovery=False)
    assert row["observations"][-1]["holder_joined"] is None
    assert reopened.service.intake.tasks(task["task_id"])[0]["status"] == "running"


def test_fresh_recover_requires_its_actual_join_before_proof_publication(
    setup, monkeypatch
):
    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch, recover_joined=False)
    evidence = setup.home.parent / "unjoined-recovery.txt"
    evidence.write_text("no borrowed join")
    request_id = uuid.uuid4().hex
    with pytest.raises(IntakeError, match="fresh_recovery_unresolved"):
        active.decide(task["task_id"], "continue", request_id, evidence)
    item = Service(setup.home).intake.lifecycle()[1]["maintenance"][request_id]
    assert item["recovery_proof"] is None
    assert "resume_run" not in [sent["operation"] for sent in setup.peer.sent]


def test_pending_recover_receipt_reobserves_exact_call_before_proof_and_resume(
    setup, monkeypatch
):
    active, task, journal, record = closed_child(setup, status="paused")
    active.service.intake.transition(
        task["task_id"], version=task["version"], claim=task["claim"], status="held"
    )
    paused_parent(active, setup)
    lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    original, final = setup.peer.submit, {}

    def submit(sent, *, deadline):
        value = original(sent, deadline=deadline)
        if sent["operation"] == "recover_run":
            final.update(envelope=copy.deepcopy(sent), observation=copy.deepcopy(value))
            value.update(core_receipt=None, phase="call_active", holder_joined=False)
            setup.peer.responses[sent["request_id"]] = copy.deepcopy(value)
        return value

    monkeypatch.setattr(setup.peer, "submit", submit)
    evidence = setup.home.parent / "pending-recovery.txt"
    evidence.write_text("observe the original recovery before continuing")
    request_id = uuid.uuid4().hex
    with pytest.raises(IntakeError, match="fresh_recovery_unresolved"):
        active.decide(task["task_id"], "continue", request_id, evidence)
    item = Service(setup.home).intake.lifecycle()[1]["maintenance"][request_id]
    assert item["recover"]["envelope"] == final["envelope"]
    assert item["recover"]["reservation"] == "observed"
    assert item["recover"]["receipt"] is None and item["recovery_proof"] is None
    assert item["resume"]["envelope"] is None
    held = Service(setup.home).intake.tasks(task["task_id"])[0]
    assert held["status"] == "held" and held["terminal_receipt"] is None

    posted = copy.deepcopy(setup.peer.sent)
    with pytest.raises(IntakeError, match="lost_call_receipt_unresolved"):
        runtime.Runtime(Service(setup.home), client=setup.peer).decide(
            task["task_id"], "continue", request_id, evidence
        )
    pending = Service(setup.home).intake.lifecycle()[1]["maintenance"][request_id]
    assert pending == item and setup.peer.sent == posted
    assert final["envelope"] in setup.peer.observed
    assert Service(setup.home).intake.tasks(task["task_id"])[0] == held

    # A later GET reveals the real applied receipt and join for this saved call.
    setup.peer.responses[final["envelope"]["request_id"]] = final["observation"]
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    reopened.decide(task["task_id"], "continue", request_id, evidence)
    item = reopened.service.intake.lifecycle()[1]["maintenance"][request_id]
    assert item["recover"]["envelope"] == final["envelope"]
    assert item["recover"]["receipt"] == final["observation"]["core_receipt"]
    assert runtime._joined(reopened.service, item["recover"])
    assert item["recovery_proof"] is not None
    assert item["resume"]["envelope"]["operation"] == "resume_run"
    assert [row["operation"] for row in setup.peer.sent].count("recover_run") == 1
    assert final["envelope"] in setup.peer.observed
    current = reopened.service.intake.tasks(task["task_id"])[0]
    assert current["status"] == "running" and current["terminal_receipt"] is None
    _, saved = reopened.call(current)
    assert saved["os_observations"][-1]["holder_joined"] is None

    posted = copy.deepcopy(setup.peer.sent)
    runtime.Runtime(Service(setup.home), client=setup.peer).decide(
        task["task_id"], "continue", request_id, evidence
    )
    assert setup.peer.sent == posted
    assert Service(setup.home).intake.tasks(task["task_id"])[0] == current


def test_recovery_proof_binds_lost_id_digest_version_gate_and_new_join(
    setup, monkeypatch
):
    from millrace_plus import lad_service_disposition as disposition

    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_id = lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    evidence = setup.home.parent / "proof-links.txt"
    evidence.write_text("exact saved evidence")
    decision_id = uuid.uuid4().hex
    active.decide(task["task_id"], "continue", decision_id, evidence)
    proof_ref = active.record["maintenance"][decision_id]["recovery_proof"]
    original = runtime.strict_json(disposition.read_ref(active.service, proof_ref))
    _, saved = active.call(active.service.intake.tasks(task["task_id"])[0])
    old_call = next(
        row
        for row in disposition.task_sent_calls(saved, active.record)
        if row["envelope"]["request_id"] == lost_id
    )
    for fault in ("lost-id", "digest", "stale-before", "null-gate", "new-join"):
        proof = copy.deepcopy(original)
        if fault == "lost-id":
            proof["lost_request_ids"] = ["f" * 32]
        elif fault == "digest":
            value = runtime.strict_json(
                disposition.read_ref(active.service, proof["recover_receipt"])
            )
            value["request_digest"] = "f" * 64
            proof["recover_receipt"] = disposition.retain(
                active.service, canonical(value) + b"\n", "bad-receipt"
            )
        elif fault in {"stale-before", "null-gate"}:
            value = runtime.strict_json(
                disposition.read_ref(active.service, proof["before"])
            )
            if fault == "null-gate":
                value["run"]["run_gate"] = None
            else:
                value["run"]["state_version"] -= 1
                value["instance"]["runs"][0]["state_version"] -= 1
            proof["before"] = disposition.retain(
                active.service, canonical(value) + b"\n", "bad-before"
            )
        else:
            value = runtime.strict_json(
                disposition.read_ref(active.service, proof["joined"])
            )
            for row in value:
                row["holder_joined"] = False
            proof["joined"] = disposition.retain(
                active.service, canonical(value) + b"\n", "bad-joined"
            )
        ref = disposition.retain(
            active.service, canonical(proof) + b"\n", "bad-recovery"
        )
        with pytest.raises((IntakeError, SubworkflowError)):
            disposition._validate_recovery_ref(
                active.service, ref, saved, lost_id, sent_call=old_call
            )
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "running"


@pytest.mark.parametrize("drift", [False, True])
def test_parent_binding_cas_race_accepts_only_exact_authority_and_preserves_newer_stop(
    setup, monkeypatch, drift
):
    active = activate(setup)
    before = copy.deepcopy(active.record)
    active.record["run_id"] = None
    active.record["start"].update(receipt=None, reservation="unsent", observations=[])
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    outer = runtime.Runtime(Service(setup.home), client=setup.peer)
    contender = runtime.Runtime(Service(setup.home), client=setup.peer)
    real_save = outer.service.intake.save_lifecycle
    stop_id = uuid.uuid4().hex

    def raced(intent, original, record):
        contender.parent()
        saved_stop(contender, stop_id)
        if drift:
            prior = copy.deepcopy(contender.record)
            contender.record["run_id"] = "f" * 32
            contender.record["start"]["receipt"]["run_id"] = "f" * 32
            contender.service.intake.save_lifecycle(
                contender.intent, prior, contender.record
            )
        return real_save(intent, original, record)

    monkeypatch.setattr(outer.service.intake, "save_lifecycle", raced)
    posted = len(setup.peer.sent)
    if drift:
        with pytest.raises(IntakeError, match="parent_authority_changed|invalid_stop"):
            outer.parent()
    else:
        _, run = outer.parent()
        assert outer.record["run_id"] == run["id"]
        assert stop_id in outer.record["stops"]
        assert outer.record == contender.service.intake.lifecycle()[1]
    assert len(setup.peer.sent) == posted
    assert stop_id in Service(setup.home).intake.lifecycle()[1]["stops"]


def test_saved_child_resume_stale_version_and_changed_observations_never_post(setup):
    active, task, _, call = closed_child(setup, status="paused")
    row = saved_child_maintenance(active, task, call, uuid.uuid4().hex)
    before = copy.deepcopy(active.record)
    row["envelope"]["expected_state_version"] = 9
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    saved = next(
        item["resume"]
        for item in reopened.record["maintenance"].values()
        if item.get("task_id") == task["task_id"]
    )
    posted = len(setup.peer.sent)
    with pytest.raises(IntakeError, match="stale_state_version"):
        runtime._send(
            reopened.service,
            reopened.intent,
            reopened.record,
            saved,
            setup.peer,
            10**10,
        )
    assert len(setup.peer.sent) == posted
    before = copy.deepcopy(reopened.record)
    saved["reservation"] = "observed"
    saved["receipt"] = receipt(
        saved["envelope"], instance=call["instance_id"], run=call["run_id"]
    )
    corrupt = observation(saved["envelope"], core=saved["receipt"])
    corrupt["request_digest"] = "f" * 64
    saved["observations"] = [corrupt]
    reopened.service.intake.save_lifecycle(reopened.intent, before, reopened.record)
    database = (setup.home / "intake.sqlite3").read_bytes()
    with pytest.raises((IntakeError, SubworkflowError)):
        runtime.Runtime(Service(setup.home), client=setup.peer)
    assert len(setup.peer.sent) == posted
    assert (setup.home / "intake.sqlite3").read_bytes() == database


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_continue_observes_succeeded_child_without_resuming_or_acknowledging_terminal(
    setup, status
):
    active, task, _, _ = closed_child(setup, status=status)
    active.service.intake.transition(
        task["task_id"], version=task["version"], claim=task["claim"], status="held"
    )
    paused_parent(active, setup)
    evidence = setup.home.parent / "terminal-observation.txt"
    evidence.write_text("Observe this same terminal child; no new run")
    posted = len(setup.peer.sent)
    active.decide(task["task_id"], "continue", uuid.uuid4().hex, evidence)
    row = Service(setup.home).intake.tasks(task["task_id"])[0]
    assert row["status"] == ("running" if status == "succeeded" else "held")
    assert row["terminal_receipt"] is None and len(setup.peer.sent) == posted
    if status == "succeeded":
        # Observation reactivation is not domain acceptance: absent CLOSE/ACK
        # still prevents the actual guarded service settlement.
        assert active.node("advance")["outcome"] == "CHILD_SUCCEEDED"
        assert active.node("settle")["outcome"] == "HOLD"
        assert (
            active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None
        )


def test_recovered_live_observation_uses_one_window_without_changing_generic_loss(
    setup, monkeypatch
):
    from millrace_plus import subworkflow_v2 as helper

    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    evidence = setup.home.parent / "live-observation.txt"
    evidence.write_text("same recovered child")
    active.decide(task["task_id"], "continue", uuid.uuid4().hex, evidence)
    clock = Clock()
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    refreshes = []
    real_reconcile = helper.reconcile

    def reconcile(*args, **kwargs):
        refreshes.append(clock.now)
        assert kwargs["_deadline"] == 60
        return real_reconcile(*args, **kwargs)

    monkeypatch.setattr(helper, "reconcile", reconcile)
    posted = len(setup.peer.sent)
    result = active.node("advance")
    assert result["outcome"] == "CHILD_RUNNING" and clock.now == 60
    assert 1 <= len(refreshes) <= 13
    assert all(later - earlier >= 5 for earlier, later in zip(refreshes, refreshes[1:]))
    assert len(setup.peer.sent) == posted
    saved = journal.load()
    assert saved["saved_result"]["outcome"] == "CHILD_OBSERVATION_LOST"
    assert saved["os_observations"][-1]["holder_joined"] is None
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


def test_recovered_live_resume_false_can_observe_but_never_close(setup, monkeypatch):
    active, task, journal, record = closed_child(setup, status="paused")
    paused_parent(active, setup)
    lost_start(active, journal, record, setup)
    recovery_peer(setup, monkeypatch)
    evidence = setup.home.parent / "known-live-resume.txt"
    evidence.write_text("Observe known live resume without inventing a join")
    decision_id = uuid.uuid4().hex
    active.decide(task["task_id"], "continue", decision_id, evidence)
    before = copy.deepcopy(active.record)
    resume = active.record["maintenance"][decision_id]["resume"]
    latest = copy.deepcopy(resume["observations"][-1])
    latest.update(holder_joined=False, phase="call_active")
    resume["observations"] = [latest]
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    setup.peer.responses[resume["envelope"]["request_id"]] = copy.deepcopy(latest)
    clock = Clock()
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    assert active.node("advance")["outcome"] == "CHILD_RUNNING"
    assert clock.now == 60 and resume["observations"][-1]["holder_joined"] is False
    run = setup.peer.runs[(record["instance_id"], record["run_id"])]
    run["status"] = "succeeded"
    setup.peer.instances[record["instance_id"]]["runs"][0]["status"] = "succeeded"
    # A known false for this newer call cannot borrow the old recovery proof to
    # become closure. Its actual true must arrive before guarded settlement.
    assert active.node("advance")["outcome"] == "CHILD_OBSERVATION_LOST"
    assert clock.now == 120
    saved = journal.load()
    assert saved["saved_result"]["outcome"] == "CHILD_OBSERVATION_LOST"
    assert saved["os_observations"][-1]["holder_joined"] is None
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


@pytest.mark.parametrize("arrival", ["before-node", "before-claim-lock"])
def test_stale_runtime_cannot_claim_after_durable_stop_intent(
    setup, monkeypatch, arrival
):
    active = activate(setup)
    request = setup.home.parent / "queued-during-stop.md"
    request.write_text("Must remain queued after stop reservation")
    submitted = active.service.submit("queued", request)
    stale = runtime.Runtime(Service(setup.home), client=setup.peer)
    contender = runtime.Runtime(Service(setup.home), client=setup.peer)
    stop_id = uuid.uuid4().hex
    original = stale.service.claim_next
    if arrival == "before-node":
        saved_stop(contender, stop_id)
    else:

        def claim_next(*, writer_guard):
            saved_stop(contender, stop_id)
            return original(writer_guard=writer_guard)

        monkeypatch.setattr(stale.service, "claim_next", claim_next)
    posted = len(setup.peer.sent)
    result = stale.node("claim")
    assert result["outcome"] == "HOLD"
    row = Service(setup.home).intake.tasks(submitted["task_id"])[0]
    assert row["status"] == "queued" and row["claim"] is None and row["version"] == 1
    assert stop_id in Service(setup.home).intake.lifecycle()[1]["stops"]
    assert len(setup.peer.sent) == posted
    assert not (setup.home / "tasks" / submitted["task_id"]).exists()


def synthetic_tail(setup, monkeypatch, *, mode="true"):
    """Isolate retirement timing only; this fixture can never accept a domain proof."""
    from millrace_plus import lad_service_disposition as disposition

    active, task, _, call = closed_child(setup, status="succeeded")
    row = saved_child_maintenance(active, task, call, uuid.uuid4().hex)
    before = copy.deepcopy(active.record)
    core = receipt(row["envelope"], instance=call["instance_id"], run=call["run_id"])
    core.update(state_version=8, retryable=False, code=None, action="resumed")
    row.update(
        reservation="observed",
        receipt=core,
        observations=[observation(row["envelope"], joined=False, core=core)],
    )
    setup.peer.runs[(call["instance_id"], call["run_id"])]["controls"].append(
        copy.deepcopy(core)
    )
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    real_domain = disposition.domain_evidence

    def eligibility(service, record, run, *, _publish=True):
        if not _publish:
            return {"available": {}, "missing": []}
        return real_domain(service, record, run, _publish=_publish)

    monkeypatch.setattr(disposition, "domain_evidence", eligibility)
    clock = Clock()
    reads = []
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        setup.peer, "submit", lambda *a, **k: pytest.fail("join tail POSTed")
    )

    def observe(sent, *, deadline):
        assert sent == row["envelope"] and deadline == 30
        reads.append(clock.now)
        value = observation(sent, joined=False, core=core)
        if len(reads) >= 2:
            if mode == "true":
                value["holder_joined"] = True
            elif mode == "null":
                value.update(
                    source="core_receipt", phase="receipt_observed", holder_joined=None
                )
            elif mode == "failed":
                value["phase"] = "call_failed"
            elif mode == "digest":
                value["request_digest"] = "f" * 64
        return value

    monkeypatch.setattr(setup.peer, "observe", observe)
    return active, task, call, row, clock, reads


def test_known_false_tail_is_get_only_false_then_true_before_guard(setup, monkeypatch):
    active, task, call, row, clock, reads = synthetic_tail(setup, monkeypatch)
    monkeypatch.setattr(
        setup.peer,
        "exclusive_run_snapshot",
        lambda *a, **k: pytest.fail("tail entered Core guard"),
    )
    active._settlement_join_tail(task, 30)
    assert reads == [0, 5] and clock.now == 5
    assert [item["holder_joined"] for item in row["observations"]] == [False, True]
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


@pytest.mark.parametrize("mode", ["false", "null", "failed", "digest"])
def test_known_false_tail_has_one_deadline_and_unknown_facts_hold(
    setup, monkeypatch, mode
):
    active, task, _, row, clock, reads = synthetic_tail(setup, monkeypatch, mode=mode)
    with pytest.raises((IntakeError, SubworkflowError)):
        active._settlement_join_tail(task, 30)
    if mode == "false":
        assert reads == [0, 5, 10, 15, 20, 25] and clock.now == 30
    else:
        assert reads == [0, 5] and clock.now == 5
    assert all(at < 30 for at in reads)
    assert not any(item["holder_joined"] is True for item in row["observations"])
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


@pytest.mark.parametrize(
    "blocker", ["null", "missing", "queued", "writer", "stale-task"]
)
def test_join_tail_noneligible_authority_does_not_poll(setup, monkeypatch, blocker):
    active, task, call, row, _, reads = synthetic_tail(setup, monkeypatch)
    before = copy.deepcopy(active.record)
    run = setup.peer.runs[(call["instance_id"], call["run_id"])]
    if blocker == "null":
        row["observations"][-1].update(
            source="core_receipt", phase="receipt_observed", holder_joined=None
        )
    elif blocker == "missing":
        row["observations"] = []
    elif blocker == "queued":
        run["controls"][0] = {
            key: value
            for key, value in run["controls"][0].items()
            if key
            in {
                "request_id",
                "request_digest",
                "instance_id",
                "run_id",
                "plane_id",
                "operation",
                "status",
            }
        }
        run["controls"][0]["status"] = "queued"
    elif blocker == "writer":
        run["invocations"][-1]["writer_state"] = "unknown"
        run["completions"][-1]["effective_writer"] = "unknown"
    else:
        active.service.intake.transition(
            task["task_id"], version=task["version"], claim=task["claim"], status="held"
        )
    if blocker in {"null", "missing"}:
        active.service.intake.save_lifecycle(active.intent, before, active.record)
    with pytest.raises((IntakeError, SubworkflowError)):
        active._settlement_join_tail(task, 30)
    assert reads == []
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


def test_join_tail_truth_is_not_a_lease_for_later_settlement(setup, monkeypatch):
    from millrace_plus.lad_service_disposition import settle

    active, task, call, row, _, reads = synthetic_tail(setup, monkeypatch)
    active._settlement_join_tail(task, 30)
    assert reads == [0, 5] and runtime._joined(active.service, row)
    setup.peer.runs[(call["instance_id"], call["run_id"])]["quiescent"] = False
    monkeypatch.setattr(
        setup.peer, "observe", lambda *a, **k: pytest.fail("GET inside settle guard")
    )
    with pytest.raises(IntakeError, match="child_not_closed"):
        settle(active, task, decision="settle", request_id=uuid.uuid4().hex)
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


def test_tail_retained_actual_true_survives_later_receipt_only_null(setup, monkeypatch):
    active, task, _, row, clock, reads = synthetic_tail(setup, monkeypatch)
    before = copy.deepcopy(active.record)
    true = observation(row["envelope"], joined=True, core=row["receipt"])
    null = copy.deepcopy(true)
    null.update(source="core_receipt", phase="receipt_observed", holder_joined=None)
    row["observations"].extend([true, null])
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    active._settlement_join_tail(task, 30)
    assert reads == [] and clock.now == 0
    assert row["observations"][-1]["holder_joined"] is None
    assert runtime._joined(active.service, row)


@pytest.mark.parametrize("mode", ["true", "expiry", "null", "error", "digest"])
def test_init_create_retirement_uses_one_deadline_and_never_reposts(
    setup, monkeypatch, mode
):
    f, clock, reads = setup, Clock(), []
    f.peer.joined = False
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    original = f.peer.observe

    def observe(sent, *, deadline):
        assert deadline == 2
        reads.append(clock.now)
        value = original(sent, deadline=deadline)
        if mode == "true" and len(reads) == 2:
            value["holder_joined"] = True
        elif mode == "null":
            value.update(
                source="core_receipt", phase="receipt_observed", holder_joined=None
            )
        elif mode == "error":
            raise SubworkflowError("os_unavailable")
        elif mode == "digest":
            value["request_digest"] = "f" * 64
        return value

    monkeypatch.setattr(f.peer, "observe", observe)
    if mode == "true":
        result = initialize(f)
        assert result["activation_ready"] is True
        assert reads == pytest.approx([0.05, 0.1])
    else:
        with pytest.raises((IntakeError, SubworkflowError)):
            initialize(f)
        _, row = Service(f.home).intake.lifecycle()
        assert row["start"]["envelope"] is None and row["handshake"] is None
        assert row["create"]["receipt"]["instance_id"] in f.peer.instances
        if mode == "expiry":
            assert clock.now == 2 and len(reads) <= 40
            assert reads[-1] < 2
        # Reopen observes only the exact already-created parent.
        monkeypatch.setattr(
            f.peer,
            "observe",
            lambda sent, **kw: observation(
                sent, joined=True, core=original(sent, **kw)["core_receipt"]
            ),
        )
        assert initialize(f)["activation_ready"] is True
    assert f.peer.allocations == 1
    assert [sent["operation"] for sent in f.peer.sent] == ["create_instance"]


def test_parent_explicit_plane_control_replay_retains_exact_plane_and_version(setup):
    active = activate(setup)
    path, sent = parent_request(
        active,
        setup,
        operation="resume_plane",
        plane_id="service",
    )
    first = active.parent_control(path)
    assert first["receipt"]["plane_id"] == "service"
    posted = len(setup.peer.sent)
    parent, run = active.record["instance_id"], active.record["run_id"]
    setup.peer.runs[(parent, run)]["state_version"] = 9
    setup.peer.instances[parent]["runs"][0]["state_version"] = 9
    assert active.parent_control(path) == first
    assert len(setup.peer.sent) == posted and setup.peer.observed[-1] == sent


@pytest.mark.parametrize("plane", [None, "foreground", "foreign"])
def test_parent_plane_control_wrong_plane_never_posts(setup, plane):
    active = activate(setup)
    path, _ = parent_request(
        active,
        setup,
        operation="resume_plane",
        plane_id=plane,
    )
    before = len(setup.peer.sent)
    with pytest.raises(IntakeError):
        active.parent_control(path)
    assert len(setup.peer.sent) == before


@pytest.mark.parametrize("purpose", ["pause", "stop"])
def test_accepted_pause_is_durable_before_waiter_wake_hint(setup, monkeypatch, purpose):
    active = activate(setup)
    identifier, seen = uuid.uuid4().hex, []

    def wake(service):
        _, saved = service.intake.lifecycle()
        row = (
            saved["stops"][identifier]["pause"]
            if purpose == "stop"
            else saved["maintenance"]["parent:" + identifier]["call"]
        )
        assert row["reservation"] == "observed"
        assert row["receipt"]["status"] in {"queued", "applied"}
        assert row["envelope"]["operation"] == "pause_run"
        seen.append(service.home)

    monkeypatch.setattr(runtime, "wake", wake)
    if purpose == "stop":
        answer = active.stop(identifier)
        assert answer["status"] == "pending"
    else:
        path, sent = parent_request(active, setup, operation="pause_run", expected=None)
        sent["request_id"] = identifier
        path.write_bytes(canonical(sent) + b"\n")
        active.parent_control(path)
    assert seen == [setup.home]


@pytest.mark.parametrize("timing", ["before-bind", "after-bind"])
@pytest.mark.parametrize("kind", ["stop", "pause"])
def test_wait_bind_rechecks_durable_stop_or_current_pause(
    setup, monkeypatch, timing, kind
):
    active = activate(setup)
    actual_socket = runtime.socket.socket

    def persist():
        if kind == "stop":
            saved_stop(active, uuid.uuid4().hex)
        else:
            parent = setup.peer.runs[
                (active.record["instance_id"], active.record["run_id"])
            ]
            parent["status"] = "paused"
            parent["run_gate"] = {
                "reason": "operator_pause",
                "request_id": uuid.uuid4().hex,
            }

    class BoundSocket:
        def __init__(self, *args):
            self.inner = actual_socket(*args)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def bind(self, path):
            self.inner.bind(path)
            if timing == "after-bind":
                persist()

        def recv(self, size):
            pytest.fail("durable pause/stop was missed before blocking")

    if timing == "before-bind":
        persist()
    monkeypatch.setattr(runtime.socket, "socket", BoundSocket)
    assert runtime.wait_intake(active.service, seconds=0.001) == "WAKE"
    assert not (setup.home / "intake.sock").exists()


def test_wait_distinguishes_current_queued_pause_from_historical_cleared_pause(setup):
    active = activate(setup)
    path, sent = parent_request(active, setup, operation="pause_run", expected=None)
    active.parent_control(path)
    parent = setup.peer.runs[(active.record["instance_id"], active.record["run_id"])]
    applied = setup.peer.responses[sent["request_id"]]["core_receipt"]
    parent["controls"] = [copy.deepcopy(applied)]
    parent["run_gate"] = None
    before = (setup.home / "intake.sqlite3").read_bytes()
    assert runtime.wait_intake(active.service, seconds=0.001) == "IDLE"
    assert (setup.home / "intake.sqlite3").read_bytes() == before
    parent["controls"][0]["status"] = "queued"
    assert runtime.wait_intake(active.service, seconds=0.001) == "WAKE"


def plane_child(f, monkeypatch, *, lost=False, plane_joined=True):
    active, task, journal, record = closed_child(f, status="paused")
    paused_parent(active, f)
    run = f.peer.runs[(record["instance_id"], record["run_id"])]
    run["run_gate"] = None
    run["planes"] = {
        "foreground": {
            "status": "paused",
            "cursor": "human_pause",
            "resume_target": "next",
            "active_invocation": None,
            "pause_reason": "human_pause",
        }
    }
    if lost:
        lost_start(active, journal, record, f)
    recovery_peer(f, monkeypatch, plane_joined=plane_joined)
    evidence = f.home.parent / "plane-continue.txt"
    evidence.write_bytes(b'{"operation":"resume_plane","plane_id":"notes-only"}\n')
    return active, task, record, evidence


def test_json_looking_continue_notes_preserve_default_run_only_authority(
    setup, monkeypatch
):
    active, task, call, evidence = plane_child(setup, monkeypatch)
    run = setup.peer.runs[(call["instance_id"], call["run_id"])]
    run["run_gate"] = {"reason": "operator_pause", "request_id": uuid.uuid4().hex}
    decision = uuid.uuid4().hex
    active.decide(task["task_id"], "continue", decision, evidence)
    item = active.service.intake.lifecycle()[1]["maintenance"][decision]
    assert item["resume"]["envelope"]["operation"] == "resume_run"
    assert item["resume"]["envelope"]["plane_id"] is None and "attention" not in item
    with active.service.intake._connection() as db:
        saved = runtime.strict_json(
            db.execute("SELECT intent FROM decisions WHERE request_id=?", (decision,))
            .fetchone()[0]
            .encode()
        )
    assert saved["evidence"] == item["operator"]
    from millrace_plus.lad_service_disposition import read_ref

    assert read_ref(active.service, item["operator"]) == evidence.read_bytes()


def test_explicit_plane_without_run_gate_does_not_emit_run_resume(setup, monkeypatch):
    active, task, call, evidence = plane_child(setup, monkeypatch)
    decision = uuid.uuid4().hex
    before = len(setup.peer.sent)
    active.decide(
        task["task_id"], "continue", decision, evidence, resume_plane="foreground"
    )
    assert [row["operation"] for row in setup.peer.sent[before:]] == ["resume_plane"]
    assert setup.peer.runs[(call["instance_id"], call["run_id"])]["status"] == "running"
    item = active.service.intake.lifecycle()[1]["maintenance"][decision]
    assert "attention" not in item
    with pytest.raises(IntakeError, match="decision_conflict"):
        active.decide(task["task_id"], "continue", decision, evidence)
    assert len(setup.peer.sent) == before + 1


def unfinished_worker(next_node="next"):
    occurrence = uuid.uuid4().hex
    return {
        "terminal": None,
        "request": None,
        "occurrences": {occurrence: {"role": "planner"}},
        "continuation": {
            "node": next_node,
            "occurrence_id": occurrence,
            "lineage": "unpacketized",
            "cleanup_required": True,
        },
    }


@pytest.mark.parametrize("node", ["next", "provider_due_recon"])
@pytest.mark.parametrize("run_gate", [False, True])
def test_unfinished_accepted_boundary_continues_exact_plane_without_clearing_cleanup(
    setup, monkeypatch, run_gate, node
):
    from millrace_plus import lad_service_disposition as disposition

    active, task, call, evidence = plane_child(setup, monkeypatch)
    run = setup.peer.runs[(call["instance_id"], call["run_id"])]
    run["planes"]["foreground"]["resume_target"] = node
    if run_gate:
        run["run_gate"] = {"reason": "operator_pause", "request_id": uuid.uuid4().hex}
    state = unfinished_worker(node)
    monkeypatch.setattr(disposition, "_load_worker", lambda _: copy.deepcopy(state))
    decision = uuid.uuid4().hex
    before = len(setup.peer.sent)
    if run_gate:
        with pytest.raises(IntakeError, match="separate_run_resume_required"):
            active.decide(
                task["task_id"],
                "continue",
                decision,
                evidence,
                resume_plane="foreground",
            )
        assert [r["operation"] for r in setup.peer.sent[before:]] == ["resume_plane"]
        # Core's resume-plane moves the cursor to its saved resume target.
        run["planes"]["foreground"]["cursor"] = node
        active.decide(task["task_id"], "continue", uuid.uuid4().hex, evidence)
        assert [r["operation"] for r in setup.peer.sent[before:]] == [
            "resume_plane",
            "resume_run",
        ]
    else:
        active.decide(
            task["task_id"],
            "continue",
            decision,
            evidence,
            resume_plane="foreground",
        )
        assert [r["operation"] for r in setup.peer.sent[before:]] == ["resume_plane"]
    assert state["continuation"]["cleanup_required"] is True
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-node",
        "missing-continuation",
        "missing-node",
        "unknown-occurrence",
        "missing-occurrence",
        "terminal",
        "learning-request",
        "wrong-plane",
        "default-with-paused-plane",
        "live-writer",
        "not-quiescent",
        "provider-due-cursor-mismatch",
    ],
)
def test_unfinished_continuation_refuses_wrong_or_unclosed_authority(
    setup, monkeypatch, mutation
):
    from millrace_plus import lad_service_disposition as disposition

    active, task, call, evidence = plane_child(setup, monkeypatch)
    run = setup.peer.runs[(call["instance_id"], call["run_id"])]
    state = unfinished_worker()
    selector = "foreground"
    if mutation == "wrong-node":
        state["continuation"]["node"] = "prepare_other_role"
    elif mutation == "missing-continuation":
        state.pop("continuation")
    elif mutation == "missing-node":
        state["continuation"].pop("node")
    elif mutation == "unknown-occurrence":
        state["continuation"]["occurrence_id"] = uuid.uuid4().hex
    elif mutation == "missing-occurrence":
        state["continuation"]["occurrence_id"] = None
    elif mutation == "terminal":
        state["terminal"] = "succeeded"
    elif mutation == "learning-request":
        state["request"] = {"kind": "CLOSE"}
    elif mutation == "wrong-plane":
        run["planes"]["learning"] = copy.deepcopy(run["planes"]["foreground"])
        selector = "learning"
    elif mutation == "default-with-paused-plane":
        run["run_gate"] = {"reason": "operator_pause", "request_id": uuid.uuid4().hex}
        selector = None
    elif mutation == "live-writer":
        run["planes"]["foreground"]["active_invocation"] = uuid.uuid4().hex
    elif mutation == "not-quiescent":
        run["quiescent"] = False
    elif mutation == "provider-due-cursor-mismatch":
        state["continuation"]["node"] = "provider_due_recon"
    monkeypatch.setattr(disposition, "_load_worker", lambda _: copy.deepcopy(state))
    before = len(setup.peer.sent)
    with pytest.raises((IntakeError, SubworkflowError)):
        active.decide(
            task["task_id"],
            "continue",
            uuid.uuid4().hex,
            evidence,
            resume_plane=selector,
        )
    assert "resume_plane" not in [r["operation"] for r in setup.peer.sent[before:]]
    assert "resume_run" not in [r["operation"] for r in setup.peer.sent[before:]]
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None


def prepared_plane(f, monkeypatch, *, plane_joined=True):
    active, task, call, evidence = plane_child(
        f, monkeypatch, lost=True, plane_joined=plane_joined
    )
    decision = uuid.uuid4().hex
    with pytest.raises(IntakeError, match="separate_run_resume_required"):
        active.decide(
            task["task_id"], "continue", decision, evidence, resume_plane="foreground"
        )
    return active, task, call, evidence, decision


@pytest.mark.parametrize("aftermath", ["child-terminal", "settled-task"])
def test_gated_plane_attention_replay_then_new_default_reuses_exact_recovery(
    setup, monkeypatch, aftermath
):
    active, task, call, evidence, decision = prepared_plane(setup, monkeypatch)
    run = setup.peer.runs[(call["instance_id"], call["run_id"])]
    item = active.service.intake.lifecycle()[1]["maintenance"][decision]
    assert (
        run["run_gate"] is not None and run["planes"]["foreground"]["status"] == "ready"
    )
    assert (
        item["attention"] == "separate_run_resume_required" and item["recovery_proof"]
    )
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "held"
    assert [row["operation"] for row in setup.peer.sent][-3:] == [
        "pause_run",
        "recover_run",
        "resume_plane",
    ]
    posted = len(setup.peer.sent)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    with pytest.raises(IntakeError, match="separate_run_resume_required"):
        reopened.decide(
            task["task_id"], "continue", decision, evidence, resume_plane="foreground"
        )
    assert len(setup.peer.sent) == posted
    next_id = uuid.uuid4().hex
    reopened.decide(task["task_id"], "continue", next_id, evidence)
    assert [row["operation"] for row in setup.peer.sent[posted:]] == ["resume_run"]
    next_item = reopened.service.intake.lifecycle()[1]["maintenance"][next_id]
    assert (
        next_item["pause"]["envelope"] is None
        and next_item["recover"]["envelope"] is None
    )
    assert run["status"] == "running" and run["run_gate"] is None
    setup.peer.runs[(active.record["instance_id"], active.record["run_id"])][
        "status"
    ] = "running"
    posted = len(setup.peer.sent)
    after = runtime.Runtime(Service(setup.home), client=setup.peer)
    with pytest.raises(IntakeError, match="separate_run_resume_required"):
        after.decide(
            task["task_id"], "continue", decision, evidence, resume_plane="foreground"
        )
    assert len(setup.peer.sent) == posted
    assert (
        after.record["maintenance"][decision]["attention"]
        == "separate_run_resume_required"
    )
    _, saved = after.call(after.service.intake.tasks(task["task_id"])[0])
    assert any(row["holder_joined"] is None for row in saved["os_observations"])
    # A newer decision may finish the child before this historical refusal is
    # replayed. Its exact published attention needs no current Core/OS facts.
    run["status"] = "succeeded"
    setup.peer.instances[call["instance_id"]]["runs"][0]["status"] = "succeeded"
    if aftermath == "settled-task":
        # The fake worker has no CLOSE/ACK ledger. Use the existing guarded
        # explicit-abandonment path for a real terminal task/proof aftermath.
        run["status"] = "failed"
        setup.peer.instances[call["instance_id"]]["runs"][0]["status"] = "failed"
        for value in setup.peer.responses.values():
            value.update(
                source="os_owned_call",
                phase="call_returned",
                holder_joined=True,
            )
        current = after.service.intake.tasks(task["task_id"])[0]
        acknowledgement = setup.home.parent / "acknowledge-failed.json"
        acknowledgement.write_bytes(abandonment(current, version=run["state_version"]))
        after.decide(
            task["task_id"],
            "acknowledge-failed",
            uuid.uuid4().hex,
            acknowledgement,
        )
        terminal = after.service.intake.tasks(task["task_id"])[0]
        assert terminal["status"] == "failed" and terminal["terminal_receipt"]
    intake_before = (setup.home / "intake.sqlite3").read_bytes()
    journal, _ = after.call(after.service.intake.tasks(task["task_id"])[0])
    journal_before = journal.path.read_bytes()
    lifecycle_before = copy.deepcopy(after.service.intake.lifecycle())

    def no_observation(*args, **kwargs):
        pytest.fail("fully published historical attention observed Core/OS")

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_core", no_observation)
        for name in ("read", "capabilities", "observe", "submit"):
            patch.setattr(setup.peer, name, no_observation)
        with pytest.raises(IntakeError, match="^separate_run_resume_required$"):
            after.decide(
                task["task_id"],
                "continue",
                decision,
                evidence,
                resume_plane="foreground",
            )
    assert len(setup.peer.sent) == posted
    assert (setup.home / "intake.sqlite3").read_bytes() == intake_before
    assert journal.path.read_bytes() == journal_before
    assert after.service.intake.lifecycle() == lifecycle_before


@pytest.mark.parametrize("fault", ["enum", "receipt", "selector", "operation"])
def test_tampered_historical_attention_refuses_reopen_without_post(
    setup, monkeypatch, fault
):
    active, _, _, _, decision = prepared_plane(setup, monkeypatch)
    before = copy.deepcopy(active.record)
    item = active.record["maintenance"][decision]
    if fault == "enum":
        item["attention"] = "completed"
    elif fault == "receipt":
        item["resume"]["receipt"]["status"] = "refused"
    elif fault == "operation":
        item["resume"]["envelope"]["operation"] = "resume_run"
    else:
        with active.service.intake._connection(write=True) as db:
            row = db.execute(
                "SELECT intent FROM decisions WHERE request_id=?", (decision,)
            ).fetchone()
            intent = runtime.strict_json(row[0].encode())
            intent["evidence"]["resume_plane"] = "learning"
            db.execute(
                "UPDATE decisions SET intent=? WHERE request_id=?",
                (canonical(intent).decode(), decision),
            )
    if fault != "selector":
        active.service.intake.save_lifecycle(active.intent, before, active.record)
    posted = len(setup.peer.sent)
    database = (setup.home / "intake.sqlite3").read_bytes()
    with pytest.raises((IntakeError, SubworkflowError)):
        runtime.Runtime(Service(setup.home), client=setup.peer)
    assert len(setup.peer.sent) == posted
    assert (setup.home / "intake.sqlite3").read_bytes() == database


def test_attention_publication_failure_stays_held_and_replay_never_posts(
    setup, monkeypatch
):
    active, task, _, evidence = plane_child(setup, monkeypatch, lost=True)
    original = runtime._save
    decision = uuid.uuid4().hex

    def save(service, intent, before, after):
        if after["maintenance"].get(decision, {}).get("attention"):
            raise OSError("attention CAS publication failed")
        return original(service, intent, before, after)

    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_save", save)
        with pytest.raises(OSError, match="publication failed"):
            active.decide(
                task["task_id"],
                "continue",
                decision,
                evidence,
                resume_plane="foreground",
            )
    saved = active.service.intake.lifecycle()[1]
    assert "attention" not in saved["maintenance"][decision]
    plane_receipt = saved["maintenance"][decision]["resume"]["receipt"]
    assert (
        setup.peer.runs[(plane_receipt["instance_id"], plane_receipt["run_id"])][
            "state_version"
        ]
        == plane_receipt["state_version"]
    )
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "held"
    posted = len(setup.peer.sent)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    observed = len(setup.peer.observed)
    with pytest.raises(IntakeError, match="separate_run_resume_required"):
        reopened.decide(
            task["task_id"], "continue", decision, evidence, resume_plane="foreground"
        )
    assert len(setup.peer.sent) == posted
    assert len(setup.peer.observed) > observed
    assert reopened.service.intake.lifecycle()[1]["maintenance"][decision]["attention"]


@pytest.mark.parametrize(
    "decision,plane",
    [("continue", "foreign"), ("continue", "learning"), ("close-failed", "foreground")],
)
def test_invalid_continue_selector_refuses_before_authority_or_post(
    setup, monkeypatch, decision, plane
):
    active, task, _, evidence = plane_child(setup, monkeypatch)
    before = active.service.intake.lifecycle()
    posted = len(setup.peer.sent)
    request_id = uuid.uuid4().hex
    with pytest.raises(IntakeError, match="invalid_resume_plane"):
        active.decide(
            task["task_id"],
            decision,
            request_id,
            evidence,
            resume_plane=plane,
        )
    assert active.service.intake.lifecycle() == before
    assert len(setup.peer.sent) == posted
    with active.service.intake._connection() as db:
        assert (
            db.execute(
                "SELECT 1 FROM decisions WHERE request_id=?", (request_id,)
            ).fetchone()
            is None
        )


def test_old_recovery_cannot_cover_fresh_plane_call_false_join(setup, monkeypatch):
    active, task, _, evidence, decision = prepared_plane(
        setup,
        monkeypatch,
        plane_joined=False,
    )
    item = active.service.intake.lifecycle()[1]["maintenance"][decision]
    assert item["recovery_proof"] and not runtime._joined(
        active.service, item["resume"]
    )
    posted = len(setup.peer.sent)
    with pytest.raises(IntakeError, match="holder_known_unjoined"):
        active.decide(task["task_id"], "continue", uuid.uuid4().hex, evidence)
    assert len(setup.peer.sent) == posted
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "held"


def test_pending_plane_receipt_replay_cannot_plan_recovery_at_next_graph_pause(
    setup, monkeypatch
):
    active, task, call, evidence = plane_child(setup, monkeypatch)
    original, final = setup.peer.submit, {}

    def submit(sent, *, deadline):
        value = original(sent, deadline=deadline)
        if sent["operation"] == "resume_plane":
            final.update(envelope=copy.deepcopy(sent), observation=copy.deepcopy(value))
            value.update(core_receipt=None, phase="call_active", holder_joined=False)
            setup.peer.responses[sent["request_id"]] = copy.deepcopy(value)
        return value

    monkeypatch.setattr(setup.peer, "submit", submit)
    request_id = uuid.uuid4().hex
    with pytest.raises(IntakeError, match="child_resume_pending"):
        active.decide(
            task["task_id"],
            "continue",
            request_id,
            evidence,
            resume_plane="foreground",
        )
    # Core applied the original action and reached another graph pause before
    # the original HTTP caller observed its applied receipt.
    run = setup.peer.runs[(call["instance_id"], call["run_id"])]
    run["status"] = "paused"
    run["planes"]["foreground"].update(status="paused", cursor="next_pause")
    setup.peer.instances[call["instance_id"]]["runs"][0]["status"] = "paused"
    setup.peer.responses[final["envelope"]["request_id"]] = final["observation"]
    posted = len(setup.peer.sent)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    observed = len(setup.peer.observed)
    reopened.decide(
        task["task_id"],
        "continue",
        request_id,
        evidence,
        resume_plane="foreground",
    )
    assert len(setup.peer.sent) == posted
    assert len(setup.peer.observed) > observed
    item = reopened.service.intake.lifecycle()[1]["maintenance"][request_id]
    assert item["pause"]["envelope"] is None and item["recover"]["envelope"] is None
    assert item["resume"]["envelope"] == final["envelope"]


# Append to the existing admitted runtime test module; never execute standalone.
def test_old_plane_success_cannot_acquire_attention_from_later_gated_preparation(
    setup, monkeypatch
):
    active, task, call, evidence = plane_child(
        setup,
        monkeypatch,
        plane_joined=False,
    )
    old_id = uuid.uuid4().hex
    active.decide(
        task["task_id"],
        "continue",
        old_id,
        evidence,
        resume_plane="foreground",
    )
    old_item = active.service.intake.lifecycle()[1]["maintenance"][old_id]
    old_version = old_item["resume"]["receipt"]["state_version"]
    assert "attention" not in old_item
    run = setup.peer.runs[(call["instance_id"], call["run_id"])]
    assert run["run_gate"] is None and run["status"] == "running"

    # The original selected action was applied, but no true join was retained.
    # A subsequent graph pause and receipt-only loss require a NEW decision.
    before = copy.deepcopy(active.record)
    row = active.record["maintenance"][old_id]["resume"]
    lost = observation(
        row["envelope"],
        joined=None,
        core=row["receipt"],
        source="core_receipt",
    )
    lost["phase"] = "receipt_observed"
    row["observations"].append(lost)
    setup.peer.responses[row["envelope"]["request_id"]] = copy.deepcopy(lost)
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    run.update(status="paused", run_gate=None)
    run["planes"]["foreground"].update(
        status="paused",
        cursor="next_pause",
        resume_target="next",
    )
    setup.peer.instances[call["instance_id"]]["runs"][0]["status"] = "paused"
    new_id = uuid.uuid4().hex
    with pytest.raises(IntakeError, match="separate_run_resume_required"):
        active.decide(
            task["task_id"],
            "continue",
            new_id,
            evidence,
            resume_plane="foreground",
        )
    assert run["state_version"] > old_version
    assert run["run_gate"] is not None
    assert run["planes"]["foreground"]["status"] == "ready"
    before_task = active.service.intake.tasks(task["task_id"])[0]
    posted = len(setup.peer.sent)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    with pytest.raises(IntakeError, match="stale_state_version"):
        reopened.decide(
            task["task_id"],
            "continue",
            old_id,
            evidence,
            resume_plane="foreground",
        )
    assert len(setup.peer.sent) == posted
    current = reopened.service.intake.lifecycle()[1]
    assert "attention" not in current["maintenance"][old_id]
    assert (
        current["maintenance"][old_id]["resume"]["envelope"]["expected_state_version"]
        == old_item["resume"]["envelope"]["expected_state_version"]
    )
    assert current["maintenance"][new_id]["attention"] == "separate_run_resume_required"
    assert reopened.service.intake.tasks(task["task_id"])[0] == before_task


@pytest.mark.parametrize("error", ["os_http_404", "os_unavailable"])
@pytest.mark.parametrize("outcome", ["applied", "refused"])
def test_reopened_confirmed_owned_false_create_never_posts_after_get_error(
    setup, monkeypatch, error, outcome
):
    clock = Clock()
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    setup.peer.joined = False
    original = setup.peer.submit

    def submit(sent, *, deadline):
        if outcome == "applied":
            return original(sent, deadline=deadline)
        setup.peer.sent.append(copy.deepcopy(sent))
        value = observation(
            sent,
            joined=False,
            core=receipt(sent, status="refused", instance=None, run=None),
        )
        setup.peer.responses[sent["request_id"]] = copy.deepcopy(value)
        return value

    monkeypatch.setattr(setup.peer, "submit", submit)

    def unavailable(sent, *, deadline):
        raise SubworkflowError(error)

    # The initial POST supplies the exact final receipt/owned-false observation;
    # only its subsequent retirement GET fails. No handshake/start is exposed.
    monkeypatch.setattr(setup.peer, "observe", unavailable)
    with pytest.raises(SubworkflowError, match=error):
        initialize(setup)
    service = Service(setup.home)
    intent, before = service.intake.lifecycle()
    assert before["create"]["receipt"]["status"] == outcome
    assert before["create"]["observations"][-1]["holder_joined"] is False
    assert before["start"]["reservation"] == "unsent"
    posted = len(setup.peer.sent)
    with pytest.raises(SubworkflowError, match=error):
        initialize(setup)
    assert len(setup.peer.sent) == posted == 1
    assert setup.peer.allocations == (1 if outcome == "applied" else 0)
    assert service.intake.lifecycle() == (intent, before)


def test_reopened_ambiguous_create_preserves_exact_replay_and_original_deadline(
    setup, monkeypatch
):
    clock, deadlines = Clock(), []
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", clock.sleep)
    original = setup.peer.submit

    class LostReply(BaseException):
        pass

    def interrupted(sent, *, deadline):
        original(sent, deadline=deadline)
        raise LostReply()

    monkeypatch.setattr(setup.peer, "submit", interrupted)
    with pytest.raises(LostReply):
        initialize(setup)
    service = Service(setup.home)
    intent, before = service.intake.lifecycle()
    assert before["create"]["receipt"] is None
    assert before["create"]["reservation"] == "unknown"
    assert before["create"]["observations"] == []

    def unavailable(sent, *, deadline):
        deadlines.append(deadline)
        clock.sleep(0.1)
        raise SubworkflowError("os_http_404")

    def replay(sent, *, deadline):
        deadlines.append(deadline)
        return original(sent, deadline=deadline)

    monkeypatch.setattr(setup.peer, "observe", unavailable)
    monkeypatch.setattr(setup.peer, "submit", replay)
    assert initialize(setup)["activation_ready"] is True
    assert deadlines == [2, 2]
    assert len(setup.peer.sent) == 2
    assert setup.peer.sent[0] == setup.peer.sent[1] == intent["create"]
    assert setup.peer.allocations == 1
    assert service.intake.lifecycle()[0] == intent


@pytest.mark.parametrize("boundary", ["submit", "observe"])
def test_parent_resume_transport_allows_immediate_service_claim(
    setup, monkeypatch, boundary
):
    active = activate(setup)
    request = setup.home.parent / "queued-after-resume.md"
    request.write_bytes(b"Queued next task\n")
    task = active.service.submit("next", request)
    path, sent = parent_request(
        active, setup, operation="resume_plane", plane_id="service"
    )
    if boundary == "observe":
        active.parent_control(path)
    original = getattr(setup.peer, boundary)
    claims = []

    def dispatch(envelope, *, deadline):
        assert envelope == sent
        current = runtime.Runtime(Service(setup.home), client=setup.peer)
        row = current.record["maintenance"]["parent:" + sent["request_id"]]["call"]
        assert row["reservation"] == ("unknown" if boundary == "submit" else "observed")
        assert row["envelope"] == sent
        claims.append(current.node("claim"))
        return original(envelope, deadline=deadline)

    with monkeypatch.context() as patch:
        patch.setattr(setup.peer, boundary, dispatch)
        answer = active.parent_control(path)
    assert [row["outcome"] for row in claims] == ["CLAIMED"]
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "claimed"
    assert answer["receipt"]["status"] == "applied" and answer["holder_joined"] is True
    posted = len(setup.peer.sent)
    assert active.parent_control(path) == answer
    assert len(setup.peer.sent) == posted
    assert setup.peer.observed[-1] == sent


def test_stop_after_parent_send_reservation_blocks_claim_and_survives_observation_cas(
    setup, monkeypatch
):
    active = activate(setup)
    request = setup.home.parent / "queued-before-stop.md"
    request.write_bytes(b"Must stay queued\n")
    task = active.service.submit("next", request)
    path, sent = parent_request(
        active, setup, operation="resume_plane", plane_id="service"
    )
    stop_id = uuid.uuid4().hex
    original = setup.peer.submit
    durable = []

    def stop_then_dispatch(envelope, *, deadline):
        assert envelope == sent
        current = runtime.Runtime(Service(setup.home), client=setup.peer)
        row = current.record["maintenance"]["parent:" + sent["request_id"]]["call"]
        assert row["reservation"] == "unknown" and row["observations"] == []
        saved_stop(current, stop_id)
        durable.append(copy.deepcopy(current.record))
        assert current.node("claim") == current.result("HOLD", "claims_paused")
        return original(envelope, deadline=deadline)

    with monkeypatch.context() as patch:
        patch.setattr(setup.peer, "submit", stop_then_dispatch)
        with pytest.raises(IntakeError, match="lifecycle_conflict"):
            active.parent_control(path)
    assert active.service.intake.lifecycle()[1] == durable[0]
    assert active.service.intake.tasks(task["task_id"])[0]["status"] == "queued"
    posted = len(setup.peer.sent)
    reopened = runtime.Runtime(Service(setup.home), client=setup.peer)
    answer = reopened.parent_control(path)
    assert answer["receipt"]["status"] == "applied" and answer["holder_joined"] is True
    assert len(setup.peer.sent) == posted and setup.peer.observed[-1] == sent
    assert reopened.record["stops"] == durable[0]["stops"]
    assert reopened.node("claim") == reopened.result("HOLD", "claims_paused")
    assert reopened.service.intake.tasks(task["task_id"])[0]["status"] == "queued"


@pytest.mark.parametrize(
    "status,writer",
    [
        ("ready", "running"),
        ("running", "running"),
        ("waiting", "may_live"),
        ("running", "unknown"),
        ("succeeded", "stopped"),
    ],
)
def test_persistent_refresh_reacquires_full_pair_after_between_read_progress(
    setup, monkeypatch, status, writer
):
    from millrace_plus import subworkflow_v2 as helper

    _, _, journal, record = closed_child(setup, status="running")
    instance, run_id = record["instance_id"], record["run_id"]
    original = setup.peer.read
    clock, reads, sleeps = Clock(), [], []

    def progress(child, run=None, *, deadline):
        view = original(child, run, deadline=deadline)
        reads.append((child, run, deadline))
        if child == instance and run is None and len(reads) == 1:
            current = setup.peer.runs[(instance, run_id)]
            current.update(
                state_version=9, status=status, quiescent=writer == "stopped"
            )
            current["invocations"][0]["writer_state"] = writer
            current["completions"][0]["effective_writer"] = writer
            setup.peer.instances[instance]["runs"][0].update(
                state_version=9, status=status
            )
        return view

    def sleep(seconds):
        sleeps.append(seconds)
        clock.sleep(seconds)

    monkeypatch.setattr(setup.peer, "read", progress)
    posted = len(setup.peer.sent)
    helper._refresh(journal, record, setup.peer, 12, clock, sleep)
    assert sleeps == [5] and clock.now == 5
    assert [row[1] for row in reads] == [None, run_id, None, run_id]
    assert all(row[2] == 12 for row in reads)
    assert journal.load()["observation"]["state_version"] == 9
    observed = journal.load()["observation"]
    assert observed["status"] == status
    assert observed["counts"]["invocations"][writer] == 1
    assert len(setup.peer.sent) == posted


@pytest.mark.parametrize(
    "fault", ["duplicate", "status", "settings", "writer", "control", "same-version"]
)
def test_persistent_read_skew_never_retries_malformed_or_conflicting_views(
    setup, monkeypatch, fault
):
    from millrace_plus import subworkflow_v2 as helper
    from millrace_plus.subworkflow_persistent_records import _ReadProgress

    _, _, journal, record = closed_child(setup, status="running")
    instance, run_id = record["instance_id"], record["run_id"]
    view = setup.peer.instances[instance]
    run = setup.peer.runs[(instance, run_id)]
    run["state_version"] = 9
    if fault == "duplicate":
        view["runs"].append(copy.deepcopy(view["runs"][0]))
    elif fault == "status":
        view["runs"][0]["status"] = "foreign"
    elif fault == "settings":
        run["host_settings"]["env_allowlist"] = ["UNSELECTED"]
    elif fault == "writer":
        run["invocations"][0]["writer_state"] = "foreign"
    elif fault == "control":
        run["controls"] = [{"request_id": uuid.uuid4().hex}]
    else:
        run["state_version"] = 8
        run["status"] = "succeeded"
    before, posted = journal.path.read_bytes(), len(setup.peer.sent)
    clock = Clock()

    def no_sleep(seconds):
        pytest.fail("malformed/conflicting views were classified as normal progress")

    with pytest.raises(SubworkflowError) as caught:
        helper._refresh(journal, record, setup.peer, 12, clock, no_sleep)
    assert not isinstance(caught.value, _ReadProgress)
    assert journal.path.read_bytes() == before
    assert len(setup.peer.sent) == posted and clock.now == 0


@pytest.mark.parametrize(
    "obligation,read_expires",
    [("none", False), ("none", True), ("lost", False), ("failed", False)],
)
def test_persistent_continuous_read_progress_is_finite_pending_then_observable(
    setup, monkeypatch, obligation, read_expires
):
    from millrace_plus import subworkflow_v2 as helper

    active, task, journal, record = closed_child(
        setup, status="running", joined=obligation == "none"
    )
    if obligation != "none":
        sent = record["envelopes"]["start"]
        changed = observation(
            sent,
            joined=None if obligation == "lost" else False,
            core=record["receipts"]["start"] if obligation == "lost" else None,
            source="core_receipt" if obligation == "lost" else "os_owned_call",
        )
        changed["phase"] = "receipt_observed" if obligation == "lost" else "call_failed"
        setup.peer.responses[sent["request_id"]] = changed
    request = setup.home.parent / "still-queued.md"
    request.write_bytes(b"No release without current closure\n")
    second = active.service.submit("next", request)
    prior = copy.deepcopy(record["observation"])
    original = setup.peer.read
    instance, run_id = record["instance_id"], record["run_id"]
    clock, sleeps, deadlines = Clock(), [], []

    def progress(child, run=None, *, deadline):
        view = original(child, run, deadline=deadline)
        deadlines.append(deadline)
        if child == instance and run is None:
            setup.peer.runs[(instance, run_id)]["state_version"] += 1
            setup.peer.instances[instance]["runs"][0]["state_version"] += 1
            if read_expires and clock.now == 5:
                clock.now = 12
        return view

    def sleep(seconds):
        sleeps.append(seconds)
        clock.sleep(seconds)

    posted = len(setup.peer.sent)
    with monkeypatch.context() as patch:
        patch.setattr(setup.peer, "read", progress)
        answer = helper._advance_bytes(
            record["request_raw"].encode(),
            claim_sha256=record["claim_sha256"],
            cwd=active.service.home / "project" / "service",
            client=setup.peer,
            clock=clock,
            sleep=sleep,
            _local_source=journal.local_source,
            _deadline=12,
        )
    expected = {
        "none": ("CHILD_RUNNING", "observation_window_expired"),
        "lost": ("CHILD_OBSERVATION_LOST", "holder_observation_lost"),
        "failed": ("CHILD_UNCERTAIN", "os_call_failed"),
    }[obligation]
    assert (answer["outcome"], answer["summary"]) == expected
    assert sleeps == ([5] if read_expires else [5, 5, 2]) and clock.now == 12
    assert deadlines and all(deadline == 12 for deadline in deadlines)
    pending = journal.load()
    assert pending["observation"] == prior
    assert pending["phase"] == ("observing" if obligation == "none" else "held")
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None
    assert active.service.intake.tasks(second["task_id"])[0]["status"] == "queued"
    assert len(setup.peer.sent) == posted
    if obligation != "none":
        assert pending["os_observations"][-1] == changed
        return

    # A new ordinary observation window can see real coherent completion. The
    # helper still does not publish any terminal task/disposition receipt.
    setup.peer.runs[(instance, run_id)].update(status="succeeded", state_version=50)
    setup.peer.instances[instance]["runs"][0].update(
        status="succeeded", state_version=50
    )
    answer = helper._advance_bytes(
        record["request_raw"].encode(),
        claim_sha256=record["claim_sha256"],
        cwd=active.service.home / "project" / "service",
        client=setup.peer,
        clock=clock,
        sleep=sleep,
        _local_source=journal.local_source,
        _deadline=24,
    )
    assert answer["outcome"] == "CHILD_SUCCEEDED"
    assert journal.load()["observation"]["state_version"] == 50
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None
    assert active.service.intake.tasks(second["task_id"])[0]["status"] == "queued"
    assert len(setup.peer.sent) == posted


@pytest.mark.parametrize(
    "status,writer,queued",
    [
        ("paused", "stopped", False),
        ("failed", "stopped", False),
        ("cancelled", "stopped", False),
        ("succeeded", "unknown", False),
        ("succeeded", "may_live", False),
        ("succeeded", "stopped", True),
        ("paused", "unknown", True),
        ("failed", "may_live", True),
        ("cancelled", "unknown", True),
    ],
)
def test_fresh_attention_failure_and_unsafe_terminal_skew_never_becomes_pending(
    setup, monkeypatch, status, writer, queued
):
    from millrace_plus import subworkflow_v2 as helper

    active, task, journal, record = closed_child(setup, status="running")
    prior = copy.deepcopy(record["observation"])
    instance, run_id = record["instance_id"], record["run_id"]
    original = setup.peer.read
    clock, reads = Clock(), []
    clock.now = 11

    def fresh_unsafe(child, run=None, *, deadline):
        view = original(child, run, deadline=deadline)
        reads.append((child, run, deadline))
        if child == instance and run is None:
            current = setup.peer.runs[(instance, run_id)]
            current.update(state_version=9, status=status, quiescent=True)
            current["invocations"][0]["writer_state"] = writer
            current["completions"][0]["effective_writer"] = writer
            setup.peer.instances[instance]["runs"][0].update(
                state_version=9, status=status
            )
            if queued:
                sent = runtime._env(
                    "pause_run", uuid.uuid4().hex, instance=instance, run=run_id
                )
                current["controls"] = [
                    receipt(sent, status="queued", instance=instance, run=run_id)
                ]
        elif child == instance:
            # The full run read consumes the original remaining budget. Its
            # valid fresh failure/attention facts still must hold, not retry.
            clock.now = deadline
        return view

    def no_sleep(seconds):
        pytest.fail("fresh attention/failure/unsafe closure was laundered as progress")

    monkeypatch.setattr(setup.peer, "read", fresh_unsafe)
    posted = len(setup.peer.sent)
    answer = helper._advance_bytes(
        record["request_raw"].encode(),
        claim_sha256=record["claim_sha256"],
        cwd=active.service.home / "project" / "service",
        client=setup.peer,
        clock=clock,
        sleep=no_sleep,
        _local_source=journal.local_source,
        _deadline=12,
    )
    assert (answer["outcome"], answer["summary"]) == (
        "CHILD_UNCERTAIN",
        "invalid_instance_observation",
    )
    assert [row[1] for row in reads] == [None, run_id]
    assert all(row[2] == 12 for row in reads) and clock.now == 12
    held = journal.load()
    assert held["phase"] == "held" and held["observation"] == prior
    assert active.service.intake.tasks(task["task_id"])[0]["terminal_receipt"] is None
    assert len(setup.peer.sent) == posted


def _claim_invocation_budget(active, setup, seconds):
    """Exact public active-node metadata; the target inode stays the real one."""
    parent = setup.peer.runs[(active.record["instance_id"], active.record["run_id"])]
    identifier = uuid.uuid4().hex
    parent["planes"]["service"]["active_invocation"] = identifier
    parent["inflight_invocations"] = [identifier]
    parent["quiescent"] = False
    parent["invocations"] = [
        {
            "id": identifier,
            "run_id": parent["id"],
            "plane_id": "service",
            "node": "claim",
            "status": "running",
            "writer_state": "running",
            "deadline": 1000 + seconds,
        }
    ]
    return parent


@pytest.mark.parametrize("after_wait", ["claim", "stop", "pause"])
def test_claim_waits_for_real_target_inode_then_rechecks_parent_and_stop(
    setup, monkeypatch, after_wait
):
    active = activate(setup)
    path = setup.home.parent / "mutex-task.md"
    path.write_bytes(b"First queued request\n")
    task = active.service.submit("first", path)
    parent = _claim_invocation_budget(active, setup, 12)
    holder = runtime.target_lock(active.service.config, protected_roots=(setup.home,))
    holder.__enter__()
    released = False
    clock, sleeps = Clock(), []

    def release(seconds):
        nonlocal released
        sleeps.append(seconds)
        clock.sleep(seconds)
        holder.__exit__(None, None, None)
        released = True
        if after_wait == "stop":
            current = runtime.Runtime(Service(setup.home), client=setup.peer)
            with runtime.target_lock(
                current.service.config, protected_roots=(setup.home,)
            ):
                saved_stop(current, uuid.uuid4().hex)
        elif after_wait == "pause":
            parent["status"] = "paused"
            parent["run_gate"] = {
                "reason": "operator_pause",
                "request_id": uuid.uuid4().hex,
            }

    try:
        with monkeypatch.context() as patch:
            patch.setattr(runtime.time, "monotonic", clock)
            patch.setattr(runtime.time, "time", lambda: 1000 + clock.now)
            patch.setattr(runtime.time, "sleep", release)
            answer = active.node("claim")
    finally:
        if not released:
            holder.__exit__(None, None, None)
    assert sleeps == [0.05] and clock.now == 0.05
    if after_wait == "claim":
        assert answer["outcome"] == "CLAIMED"
        assert active.service.intake.tasks(task["task_id"])[0]["status"] == "claimed"
    else:
        assert answer == active.result("HOLD", "claims_paused")
        assert active.service.intake.tasks(task["task_id"])[0]["status"] == "queued"


def test_claim_mutex_wait_exhausts_original_invocation_budget_without_writes(
    setup, monkeypatch
):
    from millrace_plus import lad_service

    active = activate(setup)
    path = setup.home.parent / "mutex-expiry.md"
    path.write_bytes(b"Must remain queued\n")
    active.service.submit("first", path)
    _claim_invocation_budget(active, setup, 0.12)
    before = (setup.home / "intake.sqlite3").read_bytes()
    clock, sleeps, attempts, path_checks = Clock(), [], [], []
    actual_lock, actual_roots = lad_service.target_lock, lad_service._check_roots

    @contextmanager
    def counted_lock(*args, **kwargs):
        attempts.append(clock.now)
        with actual_lock(*args, **kwargs):
            yield

    def counted_paths(*args, **kwargs):
        path_checks.append(clock.now)
        return actual_roots(*args, **kwargs)

    def wait(seconds):
        sleeps.append(seconds)
        clock.sleep(seconds)

    with runtime.target_lock(active.service.config, protected_roots=(setup.home,)):
        with monkeypatch.context() as patch:
            patch.setattr(lad_service, "target_lock", counted_lock)
            patch.setattr(lad_service, "_check_roots", counted_paths)
            patch.setattr(runtime.time, "monotonic", clock)
            patch.setattr(runtime.time, "time", lambda: 1000 + clock.now)
            patch.setattr(runtime.time, "sleep", wait)
            answer = active.node("claim")
    assert answer == active.result("HOLD", "target_busy")
    assert sleeps == pytest.approx([0.05, 0.05, 0.02])
    assert clock.now == pytest.approx(0.12)
    assert attempts == pytest.approx([0, 0.05, 0.1])
    assert path_checks == pytest.approx([0, 0.05, 0.1])
    assert (setup.home / "intake.sqlite3").read_bytes() == before


@pytest.mark.parametrize("code", ["target_busy", "unknown_writers"])
def test_claim_never_retries_guard_refusals_after_mutex_acquisition(
    setup, monkeypatch, code
):
    active = activate(setup)
    path = setup.home.parent / "guard-refusal.md"
    path.write_bytes(b"No authority from holding a mutex\n")
    active.service.submit("first", path)
    _claim_invocation_budget(active, setup, 12)
    before = (setup.home / "intake.sqlite3").read_bytes()
    clock, checks = Clock(), []

    def refuse():
        checks.append(code)
        raise IntakeError(code)

    def no_sleep(seconds):
        pytest.fail("an acquired-lock authority refusal was retried")

    with monkeypatch.context() as patch:
        # Verification occurs during initial parent reads too; arrange refusal
        # only after the target mutex has actually been acquired.
        original = active.service.claim_next

        def guarded(**kwargs):
            kwargs["writer_guard"] = refuse
            return original(**kwargs)

        patch.setattr(active.service, "claim_next", guarded)
        patch.setattr(runtime.time, "monotonic", clock)
        patch.setattr(runtime.time, "time", lambda: 1000 + clock.now)
        patch.setattr(runtime.time, "sleep", no_sleep)
        answer = active.node("claim")
    assert answer == active.result("HOLD", code)
    assert clock.now == 0 and checks == [code]
    assert (setup.home / "intake.sqlite3").read_bytes() == before


def test_claim_guard_consuming_original_budget_cannot_write_fifo(setup, monkeypatch):
    active = activate(setup)
    path = setup.home.parent / "guard-expiry.md"
    path.write_bytes(b"Budget ends before claim transaction\n")
    task = active.service.submit("first", path)
    _claim_invocation_budget(active, setup, 0.12)
    before = (setup.home / "intake.sqlite3").read_bytes()
    original = active.service.claim_next
    clock, checked = Clock(), []

    def guarded(**kwargs):
        original_guard = kwargs["writer_guard"]

        def consume_budget():
            original_guard()
            checked.append(clock.now)
            clock.now = kwargs["_deadline"]

        kwargs["writer_guard"] = consume_budget
        return original(**kwargs)

    def no_sleep(seconds):
        pytest.fail("an acquired guard exhausting its budget must not retry")

    with monkeypatch.context() as patch:
        patch.setattr(active.service, "claim_next", guarded)
        patch.setattr(runtime.time, "monotonic", clock)
        patch.setattr(runtime.time, "time", lambda: 1000 + clock.now)
        patch.setattr(runtime.time, "sleep", no_sleep)
        answer = active.node("claim")
    assert answer == active.result("HOLD", "target_busy")
    assert checked == [0] and clock.now == pytest.approx(0.12)
    row = active.service.intake.tasks(task["task_id"])[0]
    assert row["status"] == "queued" and row["claim"] is None
    assert (setup.home / "intake.sqlite3").read_bytes() == before


@pytest.mark.parametrize(
    "proof",
    [
        "owned-true",
        "false",
        "null",
        "missing",
        "unowned-true",
        "wrong-digest",
        "wrong-home",
        "wrong-request",
        "wrong-target",
        "changed-bytes",
        "owned-file-missing",
        "current-live",
        "service-start",
    ],
)
def test_public_fixture_ttl_reuses_only_owned_exact_true_and_fresh_closure(
    tmp_path, monkeypatch, proof
):
    """Mock transport TTL only; no OS/Core service or process is started here."""
    import json

    from public_runtime_support import PublicRuntime, write_json
    from test_lad_service_journeys import _release_unrelated_fixture_parent

    from millrace_plus.router import control

    owned = PublicRuntime(tmp_path / "ttl-fixture")
    owned.parent, owned.parent_run = uuid.uuid4().hex, uuid.uuid4().hex
    create = owned.request("create_instance", expected=0)
    start = owned.request("start_run", instance=owned.parent, expected=1)
    core_create = receipt(create, instance=owned.parent)
    core_start = receipt(start, instance=owned.parent, run=owned.parent_run)
    responses = {
        create["request_id"]: observation(create, home="proof", core=core_create),
        start["request_id"]: observation(start, home="proof", core=core_start),
    }
    expired, observed, reads, shutdown = [False], [], [], []
    view = {
        "status": "succeeded",
        "quiescent": True,
        "inflight_invocations": [],
        "invocations": [],
        "completions": [],
        "controls": [],
    }

    def observe(envelope, *, deadline):
        observed.append(copy.deepcopy(envelope))
        if expired[0]:
            raise SubworkflowError("os_http_404")
        return copy.deepcopy(responses[envelope["request_id"]])

    def read(instance, run, *, deadline):
        assert (instance, run) == (owned.parent, owned.parent_run)
        reads.append((instance, run))
        return copy.deepcopy(view)

    def submit(envelope, *, deadline):
        value = copy.deepcopy(responses[envelope["request_id"]])
        if envelope == start:
            value.update(holder_joined=False, phase="call_active")
        return value

    def once(predicate, *, seconds=10):
        value = predicate()
        assert value, "mock bounded fixture observation remained unresolved"
        return value

    owned.client = SimpleNamespace(
        home="proof", submit=submit, observe=observe, read=read
    )
    owned.service = SimpleNamespace(
        stop=lambda: shutdown.append("stop"),
        request_shutdown=lambda: shutdown.append("request_shutdown"),
        wait_for_stop=lambda seconds: shutdown.append(("wait_for_stop", seconds)),
    )
    owned.until = once
    owned.send_parent(create, joined=True)
    owned.send_parent(start)
    assert create["request_id"] in owned.parent_join_proofs
    assert start["request_id"] not in owned.parent_join_proofs
    cached = owned.root / "parent-observations" / (start["request_id"] + ".json")
    if proof not in {"false", "null", "missing", "unowned-true"}:
        monkeypatch.setattr(
            control, "inspect_run", lambda *args, **kwargs: copy.deepcopy(view)
        )
        _release_unrelated_fixture_parent(owned)
        assert start["request_id"] in owned.parent_join_proofs
    elif proof == "null":
        value = copy.deepcopy(responses[start["request_id"]])
        value.update(
            source="core_receipt", holder_joined=None, phase="receipt_observed"
        )
        write_json(cached, value)
    elif proof == "unowned-true":
        write_json(cached, responses[start["request_id"]])

    if proof in {"missing", "owned-file-missing"}:
        cached.unlink()
    elif proof.startswith("wrong-"):
        value = json.loads(cached.read_bytes())
        field, invalid = {
            "wrong-digest": ("request_digest", "a" * 64),
            "wrong-home": ("home", "foreign"),
            "wrong-request": ("request_id", uuid.uuid4().hex),
            "wrong-target": ("instance_id", uuid.uuid4().hex),
        }[proof]
        value[field] = invalid
        write_json(cached, value)
    elif proof == "changed-bytes":
        cached.write_bytes(cached.read_bytes() + b" ")
    elif proof == "current-live":
        view["status"] = "running"

    if proof == "service-start":
        inner = owned.request("start_run", instance=uuid.uuid4().hex, expected=1)
        responses[inner["request_id"]] = observation(
            inner,
            home="proof",
            core=receipt(inner, instance=inner["instance_id"], run=uuid.uuid4().hex),
        )
        owned.parent_calls.append(inner)
        owned.retain_parent_joins()
        assert inner["request_id"] in owned.parent_join_proofs
    expired[0] = True
    before = len(observed)
    if proof in {"owned-true", "service-start"}:
        owned.close()
        assert shutdown == ["stop", ("wait_for_stop", 0)]
        assert len(observed) == before
    else:
        with pytest.raises(AssertionError, match="Unknown fixture cleanup"):
            owned.close()
        assert shutdown == ["request_shutdown", ("wait_for_stop", 10)]
    cleanup = json.loads((owned.root / "cleanup.json").read_bytes())
    assert cleanup["closure"] == (
        "publicly_proven" if proof in {"owned-true", "service-start"} else "unknown"
    )
    assert reads == [(owned.parent, owned.parent_run)]
    assert owned.parent_calls[:2] == [create, start] and start["run_id"] is None
    assert cleanup["closure_scope"] == "fixture_outer_parent_and_parent_call_joins"


def test_combined_lifecycle_connection_closes_before_runtime_and_public_reads(
    setup, monkeypatch
):
    active = activate(setup)
    connection = active.service.intake._connection
    connections = []

    @contextmanager
    def tracked(*args, **kwargs):
        with connection(*args, **kwargs) as opened:
            connections.append(opened)
            yield opened

    def assert_closed():
        assert connections
        for opened in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                opened.execute("SELECT 1")

    validate = runtime._validate_lifecycle

    def checked_validation(*args, **kwargs):
        assert_closed()
        return validate(*args, **kwargs)

    inspect_instance, inspect_run = setup.peer.inspect_instance, setup.peer.inspect_run

    def checked_instance(*args, **kwargs):
        assert_closed()
        return inspect_instance(*args, **kwargs)

    def checked_run(*args, **kwargs):
        assert_closed()
        return inspect_run(*args, **kwargs)

    monkeypatch.setattr(active.service.intake, "_connection", tracked)
    monkeypatch.setattr(runtime, "_validate_lifecycle", checked_validation)
    monkeypatch.setattr(setup.peer, "inspect_instance", checked_instance)
    monkeypatch.setattr(setup.peer, "inspect_run", checked_run)
    reopened = runtime.Runtime(active.service, client=setup.peer)
    reopened.parent()
    assert_closed()


@pytest.mark.parametrize("change", ["stop", "claim", "maintenance"])
def test_combined_lifecycle_reloads_mutable_authority_on_each_operation(
    setup, monkeypatch, change
):
    active, task = claimed(setup)
    validate = runtime._validate_lifecycle
    observed_tasks = []

    def checked(*args, **kwargs):
        observed_tasks.append(copy.deepcopy(kwargs["_tasks"]))
        return validate(*args, **kwargs)

    monkeypatch.setattr(runtime, "_validate_lifecycle", checked)
    initial = runtime._load_lifecycle(active.service)
    request_id = uuid.uuid4().hex
    if change == "stop":
        saved_stop(active, request_id)
    elif change == "claim":
        active.service.intake.transition(
            task["task_id"], version=task["version"], claim=task["claim"], status="held"
        )
    else:
        before = copy.deepcopy(active.record)
        sent = runtime._env(
            "pause_run",
            request_id,
            instance=active.record["instance_id"],
            run=active.record["run_id"],
        )
        active.record["maintenance"]["parent:" + request_id] = {
            "scope": "parent",
            "task_id": None,
            "request_raw": canonical(sent).decode(),
            "call": runtime._row(sent),
        }
        active.service.intake.save_lifecycle(active.intent, before, active.record)
    latest = runtime._load_lifecycle(active.service)
    assert latest[0] == initial[0]
    if change == "claim":
        observed = next(
            row for row in observed_tasks[-1] if row["task_id"] == task["task_id"]
        )
        assert observed["status"] == "held"
        assert observed["version"] == task["version"] + 1
        assert observed_tasks[-1] != observed_tasks[0]
    else:
        assert latest[1] == active.record and latest[1] != initial[1]
        if change == "stop":
            assert request_id in latest[1]["stops"]
        else:
            assert "parent:" + request_id in latest[1]["maintenance"]


@pytest.mark.parametrize("reader", ["combined", "direct"])
def test_combined_and_direct_lifecycle_recheck_tampered_settled_proof(setup, reader):
    from millrace_plus.lad_service_disposition import settle

    active, task, _, _ = closed_child(setup)
    settle(
        active,
        task,
        decision="acknowledge-failed",
        request_id=uuid.uuid4().hex,
        operator_raw=abandonment(task),
    )
    retained = runtime._load_lifecycle(active.service)
    proof = next((setup.home / "evidence").glob("run-*.json"))
    changed = proof.read_bytes() + b" "
    proof.write_bytes(changed)
    database = active.service.intake.path.read_bytes()
    posted = len(setup.peer.sent)
    with pytest.raises(IntakeError, match="evidence_changed"):
        if reader == "combined":
            runtime._load_lifecycle(active.service)
        else:
            runtime._validate_lifecycle(active.service, *retained)
    assert active.service.intake.path.read_bytes() == database
    assert proof.read_bytes() == changed and len(setup.peer.sent) == posted


@pytest.mark.parametrize("purpose", ["start", "stop", "maintenance"])
@pytest.mark.parametrize("later_null", [False, True])
def test_proven_parent_create_expiry_keeps_other_calls_fresh(
    setup, monkeypatch, purpose, later_null
):
    active = activate(setup)
    if purpose == "stop":
        row = saved_stop(active, uuid.uuid4().hex)
        runtime._send(
            active.service, active.intent, active.record, row, setup.peer, 10**10
        )
    elif purpose == "maintenance":
        path, sent = parent_request(active, setup, operation="pause_run", expected=None)
        active.parent_control(path)
        row = active.record["maintenance"]["parent:" + sent["request_id"]]["call"]
    else:
        row = active.record["start"]
    create = active.record["create"]
    if later_null:
        before = copy.deepcopy(active.record)
        null = copy.deepcopy(create["observations"][-1])
        null.update(source="core_receipt", phase="receipt_observed", holder_joined=None)
        create["observations"].append(null)
        active.service.intake.save_lifecycle(active.intent, before, active.record)
    before = copy.deepcopy(active.record)
    database = active.service.intake.path.read_bytes()
    sends = copy.deepcopy(setup.peer.sent)
    observed, deadlines = [], []
    observe, caps = setup.peer.observe, setup.peer.capabilities

    def expired_create(sent, *, deadline):
        observed.append(copy.deepcopy(sent))
        if sent == create["envelope"]:
            raise SubworkflowError("os_http_404")
        return observe(sent, deadline=deadline)

    def capabilities_checked(*, deadline):
        deadlines.append(deadline)
        return caps(deadline=deadline)

    monkeypatch.setattr(setup.peer, "observe", expired_create)
    monkeypatch.setattr(setup.peer, "capabilities", capabilities_checked)
    monkeypatch.setattr(runtime, "_core", lambda: pytest.fail("_send imported Core"))
    for _ in range(2):
        runtime._send(
            active.service, active.intent, active.record, create, setup.peer, 10**10
        )
    assert active.record == before
    assert active.service.intake.path.read_bytes() == database
    assert observed == [] and deadlines == [10**10, 10**10]
    sent = row["envelope"]
    setup.peer.responses[sent["request_id"]].update(
        holder_joined=False, phase="call_active"
    )
    runtime._send(active.service, active.intent, active.record, row, setup.peer, 10**10)
    assert row["observations"][-1]["holder_joined"] is False
    if purpose == "start":
        assert not runtime._joined(active.service, row)
    setup.peer.responses[sent["request_id"]].update(
        holder_joined=True, phase="call_returned"
    )
    runtime._send(active.service, active.intent, active.record, row, setup.peer, 10**10)
    assert observed == [sent, sent] and setup.peer.sent == sends
    assert runtime._joined(active.service, row)
    assert create == before["create"]
    assert active.service.intake.lifecycle()[1] == active.record


@pytest.mark.parametrize("proof", ["missing", "false", "null", "unbound"])
def test_parent_create_without_bound_true_proof_still_observes(
    setup, monkeypatch, proof
):
    active = activate(setup)
    before = copy.deepcopy(active.record)
    create = active.record["create"]
    if proof == "missing":
        create["observations"] = []
    elif proof == "unbound":
        active.record.update(
            instance_id=None, run_id=None, handshake=None, start=runtime._row(None)
        )
    else:
        for value in create["observations"]:
            value.update(holder_joined=False, phase="call_active")
            if proof == "null":
                value.update(
                    source="core_receipt", holder_joined=None, phase="receipt_observed"
                )
    active.service.intake.save_lifecycle(active.intent, before, active.record)
    accepted = copy.deepcopy(active.record)
    database = active.service.intake.path.read_bytes()
    sends = copy.deepcopy(setup.peer.sent)
    observed = []

    def unavailable(sent, *, deadline):
        observed.append(copy.deepcopy(sent))
        raise SubworkflowError("os_http_404")

    monkeypatch.setattr(setup.peer, "observe", unavailable)
    monkeypatch.setattr(runtime, "_core", lambda: pytest.fail("_send imported Core"))
    with pytest.raises(SubworkflowError, match="os_http_404"):
        runtime._send(
            active.service, active.intent, active.record, create, setup.peer, 10**10
        )
    assert observed == [create["envelope"]] and setup.peer.sent == sends
    assert active.record == accepted
    assert active.service.intake.path.read_bytes() == database


@pytest.mark.parametrize("status", ["queued", "refused"])
def test_joined_parent_create_without_applied_receipt_still_observes(
    setup, monkeypatch, status
):
    def submit(sent, *, deadline):
        setup.peer.sent.append(copy.deepcopy(sent))
        value = observation(
            sent, joined=True, core=receipt(sent, status=status, instance=None)
        )
        setup.peer.responses[sent["request_id"]] = copy.deepcopy(value)
        return value

    monkeypatch.setattr(setup.peer, "submit", submit)
    with pytest.raises(IntakeError, match="parent_create_unresolved"):
        initialize(setup)
    service = Service(setup.home)
    intent, record = service.intake.lifecycle()
    create = record["create"]
    assert record["instance_id"] is None and runtime._joined(service, create)
    assert create["receipt"]["status"] == status
    database, sends = service.intake.path.read_bytes(), copy.deepcopy(setup.peer.sent)
    observed = []

    def unavailable(sent, *, deadline):
        observed.append(copy.deepcopy(sent))
        raise SubworkflowError("os_http_404")

    monkeypatch.setattr(setup.peer, "observe", unavailable)
    monkeypatch.setattr(runtime, "_core", lambda: pytest.fail("_send imported Core"))
    with pytest.raises(SubworkflowError, match="os_http_404"):
        runtime._send(service, intent, record, create, setup.peer, 10**10)
    assert observed == [create["envelope"]] and setup.peer.sent == sends
    assert service.intake.path.read_bytes() == database


@pytest.mark.parametrize(
    "fault",
    [
        "bound-instance",
        "receipt-digest",
        "join-digest",
        "join-home",
        "join-target",
        "later-digest",
        "envelope",
        "detached",
    ],
)
def test_proven_parent_create_revalidates_authority_before_reuse(
    setup, monkeypatch, fault
):
    active = activate(setup)
    create = active.record["create"]
    accepted = active.service.intake.lifecycle()
    database = active.service.intake.path.read_bytes()
    sends = copy.deepcopy(setup.peer.sent)
    if fault == "bound-instance":
        active.record["instance_id"] = "f" * 32
    elif fault == "receipt-digest":
        create["receipt"]["request_digest"] = "f" * 64
    elif fault == "join-digest":
        create["observations"][0]["request_digest"] = "f" * 64
    elif fault == "join-home":
        create["observations"][0]["home"] = "other"
    elif fault == "join-target":
        create["observations"][0]["instance_id"] = "f" * 32
    elif fault == "later-digest":
        later = copy.deepcopy(create["observations"][0])
        later["request_digest"] = "f" * 64
        create["observations"].append(later)
    elif fault == "envelope":
        create["envelope"] = copy.deepcopy(create["envelope"])
        create["envelope"]["payload"]["project_root"] = str(setup.target)
    else:
        create = copy.deepcopy(create)
    monkeypatch.setattr(runtime, "_core", lambda: pytest.fail("_send imported Core"))
    monkeypatch.setattr(
        setup.peer, "observe", lambda *a, **k: pytest.fail("GET before refusal")
    )
    with pytest.raises((IntakeError, SubworkflowError)):
        runtime._send(
            active.service, active.intent, active.record, create, setup.peer, 10**10
        )
    assert setup.peer.sent == sends
    assert active.service.intake.path.read_bytes() == database
    assert active.service.intake.lifecycle() == accepted


def test_proven_parent_create_still_requires_capabilities(setup, monkeypatch):
    active = activate(setup)
    database = active.service.intake.path.read_bytes()
    sends = copy.deepcopy(setup.peer.sent)
    monkeypatch.setattr(runtime, "_core", lambda: pytest.fail("_send imported Core"))
    monkeypatch.setattr(
        setup.peer, "capabilities", lambda **kw: {**capabilities(), "home": "other"}
    )
    with pytest.raises(SubworkflowError):
        runtime._send(
            active.service,
            active.intent,
            active.record,
            active.record["create"],
            setup.peer,
            10**10,
        )
    assert setup.peer.sent == sends
    assert active.service.intake.path.read_bytes() == database


def test_explicit_proven_parent_create_replay_is_not_skipped(setup, monkeypatch):
    active = activate(setup)
    create = active.record["create"]
    before = copy.deepcopy(active.record)
    sends, allocations = copy.deepcopy(setup.peer.sent), setup.peer.allocations
    monkeypatch.setattr(runtime, "_core", lambda: pytest.fail("_send imported Core"))
    monkeypatch.setattr(
        setup.peer, "observe", lambda *a, **k: pytest.fail("GET during explicit replay")
    )
    runtime._send(
        active.service,
        active.intent,
        active.record,
        create,
        setup.peer,
        10**10,
        replay_create=True,
    )
    assert setup.peer.sent == sends + [create["envelope"]]
    assert setup.peer.allocations == allocations and active.record == before
    assert active.service.intake.lifecycle()[1] == before


@pytest.mark.parametrize(
    "phase", ["closed_parent", "partial_parent", "retain_parent_joins"]
)
def test_public_fixture_cleanup_records_exact_parent_failure_phase(
    tmp_path, monkeypatch, phase
):
    from public_runtime_support import PublicRuntime

    owned = PublicRuntime(tmp_path / "cleanup-facts")
    owned.parent, owned.parent_run = uuid.uuid4().hex, uuid.uuid4().hex
    sent = owned.request("start_run", instance=uuid.uuid4().hex, expected=1)
    owned.parent_calls = [sent]
    core = receipt(sent, instance=sent["instance_id"], run=owned.parent_run)
    joined = observation(sent, joined=False, core=core)
    view = {
        "status": "running" if phase == "closed_parent" else "succeeded",
        "quiescent": phase != "closed_parent",
        "inflight_invocations": [],
        "invocations": [],
        "completions": [],
        "controls": [],
    }
    if phase == "partial_parent":
        view.pop("status")
        view.pop("quiescent")
    failure_phase = "closed_parent" if phase == "partial_parent" else phase
    requests, shutdown = [], []

    def read(instance, run, *, deadline):
        requests.append((instance, run))
        return copy.deepcopy(view)

    owned.client = SimpleNamespace(
        home="local",
        read=read,
        observe=lambda envelope, **kwargs: copy.deepcopy(joined),
    )
    owned.service = SimpleNamespace(
        request_shutdown=lambda: shutdown.append("request_shutdown"),
        wait_for_stop=lambda seconds: shutdown.append(("wait_for_stop", seconds)),
        stop=lambda: pytest.fail("unknown cleanup reported success"),
    )

    def until(callback):
        value = callback()
        assert value is not None, "private exception detail must not be copied"
        return value

    monkeypatch.setattr(owned, "until", until)
    with pytest.raises(AssertionError, match="Unknown fixture cleanup"):
        owned.close()
    raw = (owned.root / "cleanup.json").read_bytes()
    saved = runtime.strict_json(raw)
    assert saved["closure"] == "unknown"
    assert saved["parent_instance_id"] == owned.parent
    assert saved["parent_run_id"] == owned.parent_run
    expected_error = {
        "parent": owned.parent,
        "parent_run": owned.parent_run,
        "phase": failure_phase,
        "error": "AssertionError",
    }
    if phase == "retain_parent_joins":
        expected_error = {
            "operation": sent["operation"],
            "request_id": sent["request_id"],
            "instance_id": sent["instance_id"],
            "run_id": sent["run_id"],
            "phase": failure_phase,
            "error": "AssertionError",
        }
    assert saved["errors"] == [expected_error]
    facts = saved["parent_cleanup"]
    assert facts["phase"] == failure_phase
    assert facts["last_closure"] == {
        "status": view.get("status"),
        "quiescent": view.get("quiescent"),
        "closed": phase == "retain_parent_joins",
    }
    if phase == "retain_parent_joins":
        assert facts["join_request_id"] == sent["request_id"]
        assert facts["join_instance_id"] == sent["instance_id"]
        assert facts["join_run_id"] == sent["run_id"]
        assert facts["last_join"] == {
            "source": "os_owned_call",
            "phase": "call_active",
            "holder_joined": False,
        }
    else:
        assert "last_join" not in facts
    assert requests == [(owned.parent, owned.parent_run)]
    assert shutdown == ["request_shutdown", ("wait_for_stop", 10)]
    assert b"private exception detail" not in raw


@pytest.mark.parametrize("change", ["task", "kind", "selector", "evidence"])
def test_published_attention_changed_intent_refuses_without_observation(
    setup, monkeypatch, change
):
    active, task, _, evidence, decision = prepared_plane(setup, monkeypatch)
    task_id, kind, selector = task["task_id"], "continue", "foreground"
    if change == "task":
        task_id = uuid.uuid4().hex
    elif change == "kind":
        kind, selector = "close-failed", None
    elif change == "selector":
        selector = "learning"
    else:
        evidence.write_bytes(evidence.read_bytes() + b" ")
    before = active.service.intake.path.read_bytes()

    def no_observation(*args, **kwargs):
        pytest.fail("changed historical intent observed Core/OS")

    monkeypatch.setattr(runtime, "_core", no_observation)
    for name in ("read", "capabilities", "observe", "submit"):
        monkeypatch.setattr(setup.peer, name, no_observation)
    with pytest.raises(IntakeError, match="^decision_conflict$"):
        active.decide(task_id, kind, decision, evidence, resume_plane=selector)
    assert active.service.intake.path.read_bytes() == before


@pytest.mark.parametrize(
    "fault",
    [
        "request.md",
        "claim.json",
        "request.json",
        "operator",
        "receipt",
        "service",
        "init",
        "stored-claim",
        "journal",
    ],
)
def test_published_attention_revalidates_local_authority_on_existing_runtime(
    setup, monkeypatch, fault
):
    active, task, _, evidence, decision = prepared_plane(setup, monkeypatch)
    if fault in {"request.md", "claim.json", "request.json"}:
        path = setup.home / "tasks" / task["task_id"] / fault
        path.write_bytes(path.read_bytes() + b" ")
    elif fault == "operator":
        item = active.service.intake.lifecycle()[1]["maintenance"][decision]
        path = Path(item["operator"]["path"])
        path.write_bytes(path.read_bytes() + b" ")
    elif fault == "service":
        path = setup.home / "service.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif fault in {"init", "stored-claim"}:
        with active.service.intake._connection(write=True) as db:
            if fault == "init":
                intent = copy.deepcopy(active.intent)
                intent["settings"]["runtime_defaults"] = {"changed": True}
                db.execute(
                    "UPDATE lifecycle SET intent=?", (canonical(intent).decode(),)
                )
            else:
                claim = copy.deepcopy(task["claim"])
                claim["request_sha256"] = "0" * 64
                db.execute(
                    "UPDATE tasks SET claim=? WHERE task_id=?",
                    (canonical(claim).decode(), task["task_id"]),
                )
    elif fault == "journal":
        journal, saved = active.call(active.service.intake.tasks(task["task_id"])[0])
        saved["instance_id"] = uuid.uuid4().hex
        saved["record_sha256"] = runtime.digest(
            {name: value for name, value in saved.items() if name != "record_sha256"}
        )
        journal.path.write_bytes(canonical(saved) + b"\n")
    else:
        before = copy.deepcopy(active.record)
        saved = active.record["maintenance"][decision]["resume"]["receipt"]
        saved["status"] = "refused"
        active.service.intake.save_lifecycle(active.intent, before, active.record)
    database = active.service.intake.path.read_bytes()

    def no_observation(*args, **kwargs):
        pytest.fail("corrupt historical authority observed Core/OS")

    monkeypatch.setattr(runtime, "_core", no_observation)
    for name in ("read", "capabilities", "observe", "submit"):
        monkeypatch.setattr(setup.peer, name, no_observation)
    with pytest.raises((IntakeError, SubworkflowError)) as caught:
        active.decide(
            task["task_id"],
            "continue",
            decision,
            evidence,
            resume_plane="foreground",
        )
    assert caught.value.code != "separate_run_resume_required"
    assert active.service.intake.path.read_bytes() == database


@pytest.mark.parametrize("expiry", ["before", "during-local-validation"])
def test_published_attention_preserves_original_caller_expiry(
    setup, monkeypatch, expiry
):
    active, task, _, evidence, decision = prepared_plane(setup, monkeypatch)
    clock = Clock()
    monkeypatch.setattr(runtime.time, "monotonic", clock)
    if expiry == "during-local-validation":
        load = runtime._load_lifecycle

        def expires(service):
            result = load(service)
            clock.sleep(2)
            return result

        monkeypatch.setattr(runtime, "_load_lifecycle", expires)
    before = active.service.intake.path.read_bytes()

    def no_observation(*args, **kwargs):
        pytest.fail("expired historical replay observed Core/OS")

    monkeypatch.setattr(runtime, "_core", no_observation)
    for name in ("read", "capabilities", "observe", "submit"):
        monkeypatch.setattr(setup.peer, name, no_observation)
    with pytest.raises(SubworkflowError, match="^observation_window_expired$"):
        active.decide(
            task["task_id"],
            "continue",
            decision,
            evidence,
            resume_plane="foreground",
            _deadline=0 if expiry == "before" else 1,
        )
    assert active.service.intake.path.read_bytes() == before


@pytest.mark.parametrize("lock", ["target", "journal"])
def test_published_attention_preserves_nonblocking_ownership_locks(
    setup, monkeypatch, lock
):
    active, task, _, evidence, decision = prepared_plane(setup, monkeypatch)
    journal, _ = active.call(active.service.intake.tasks(task["task_id"])[0])
    guard = (
        runtime.target_lock(active.service.config, protected_roots=(setup.home,))
        if lock == "target"
        else journal.lock(observer=True)
    )
    before = active.service.intake.path.read_bytes()

    def no_observation(*args, **kwargs):
        pytest.fail("busy historical replay observed Core/OS")

    monkeypatch.setattr(runtime, "_core", no_observation)
    for name in ("read", "capabilities", "observe", "submit"):
        monkeypatch.setattr(setup.peer, name, no_observation)
    error, code = (
        (IntakeError, "target_busy")
        if lock == "target"
        else (SubworkflowError, "call_busy")
    )
    with guard, pytest.raises(error, match="^" + code + "$"):
        active.decide(
            task["task_id"],
            "continue",
            decision,
            evidence,
            resume_plane="foreground",
        )
    assert active.service.intake.path.read_bytes() == before


@pytest.mark.parametrize("event", [True, False, "raises"])
@pytest.mark.parametrize("threads", ["ended", "alive", "missing"])
def test_public_fixture_shutdown_records_event_and_retained_thread_facts(
    tmp_path, event, threads
):
    from public_runtime_support import PublicRuntime

    owned = PublicRuntime(tmp_path / "shutdown-facts")
    owned.parent_calls = [owned.request("create_instance", expected=0)]
    server = (
        None
        if threads == "missing"
        else SimpleNamespace(is_alive=lambda: threads == "alive")
    )
    drain = (
        None
        if threads == "missing"
        else SimpleNamespace(is_alive=lambda: threads == "alive")
    )
    calls = []

    def request_shutdown():
        calls.append("request_shutdown")
        owned.service._server_thread = None
        owned.service._shutdown_thread = drain

    def wait(seconds):
        calls.append(("wait_for_stop", seconds))
        if event == "raises":
            raise RuntimeError("private shutdown details")
        return event

    owned.service = SimpleNamespace(
        _server_thread=server,
        _shutdown_thread=None,
        request_shutdown=request_shutdown,
        wait_for_stop=wait,
    )
    with pytest.raises(AssertionError, match="Unknown fixture cleanup"):
        owned.close()
    raw = (owned.root / "cleanup.json").read_bytes()
    saved = runtime.strict_json(raw)
    assert "os_shutdown" in saved, "fixture omitted actual shutdown completion facts"
    facts = saved["os_shutdown"]
    assert facts["stop_event_observed"] == (None if event == "raises" else event)
    assert facts["error_type"] == ("RuntimeError" if event == "raises" else None)
    assert facts["elapsed_seconds"] >= 0
    assert facts["server_thread_alive"] == (
        None if threads == "missing" else threads == "alive"
    )
    assert facts["drain_thread_alive"] == (
        None if threads == "missing" else threads == "alive"
    )
    assert calls == ["request_shutdown", ("wait_for_stop", 10)]
    assert b"private shutdown details" not in raw


@pytest.mark.parametrize("status", ["cancelled", "failed", "running"])
def test_b4_failure_cleanup_acknowledges_actual_terminal_then_replays_saved_stop(
    tmp_path, status
):
    from test_lad_service_journeys import _b4_cleanup_stop

    stop_id, task_id = uuid.uuid4().hex, uuid.uuid4().hex
    task = {"task_id": task_id, "version": 7}
    record = {"instance_id": uuid.uuid4().hex, "run_id": uuid.uuid4().hex}
    view = {
        "status": status,
        "state_version": 31,
        "quiescent": status != "running",
        "inflight_invocations": [],
        "invocations": [],
        "completions": [],
        "controls": [],
    }
    acknowledged, calls = [], []

    def stop(identifier):
        calls.append(("stop", identifier))
        return (
            {"status": "stopped"}
            if acknowledged
            else {"status": "pending", "reason": "child_disposition_required"}
        )

    def decide(task_id_arg, decision, identifier, evidence, *, _deadline):
        calls.append(("decide", identifier))
        assert task_id_arg == task_id and decision == "acknowledge-" + status
        assert runtime.strict_json(evidence.read_bytes()) == {
            "format": "millrace-plus-lad-abandonment-intent/v1",
            "acknowledge_abandonment": True,
            "expected_task_version": 7,
            "expected_state_version": 31,
        }
        acknowledged.append(identifier)
        return {"disposition": status}

    active = SimpleNamespace(
        stop=stop,
        decide=decide,
        active=lambda: task,
        call=lambda row: (None, record),
        client=SimpleNamespace(read=lambda *args, **kwargs: view),
    )
    facts = {}
    deadline = runtime.time.monotonic() + 60
    assert (
        _b4_cleanup_stop(lambda: active, tmp_path, stop_id, facts, deadline=deadline)
        is None
    )
    assert facts["last_stop"]["reason"] == "child_disposition_required"
    assert facts["last_child"]["status"] == status
    if status == "running":
        assert "acknowledgement" not in facts and not acknowledged
    else:
        saved_id = facts["acknowledgement"]["request_id"]
        assert _b4_cleanup_stop(
            lambda: active, tmp_path, stop_id, facts, deadline=deadline
        ) == {"status": "stopped"}
        assert calls == [("stop", stop_id), ("decide", saved_id), ("stop", stop_id)]


def test_b4_failure_cleanup_missing_proof_retains_same_acknowledgement_and_hold(
    tmp_path,
):
    from test_lad_service_journeys import _b4_cleanup_stop

    identifier = uuid.uuid4().hex
    evidence = tmp_path / "exact-evidence.json"
    evidence.write_bytes(b"original exact evidence")
    facts = {
        "acknowledgement": {
            "task_id": uuid.uuid4().hex,
            "decision": "acknowledge-cancelled",
            "request_id": identifier,
            "evidence_file": str(evidence),
            "receipt": None,
        }
    }
    calls = []

    def decide(task, decision, request, path, *, _deadline):
        calls.append((request, path.read_bytes()))
        raise IntakeError("holder_unjoined")

    active = SimpleNamespace(
        decide=decide, stop=lambda identifier: pytest.fail("stop before disposition")
    )
    for _ in range(2):
        with pytest.raises(IntakeError, match="holder_unjoined"):
            _b4_cleanup_stop(
                lambda: active,
                tmp_path,
                uuid.uuid4().hex,
                facts,
                deadline=runtime.time.monotonic() + 60,
            )
    assert calls == [(identifier, b"original exact evidence")] * 2
    assert facts["acknowledgement"]["receipt"] is None


def test_b4_cleanup_restores_truth_only_after_actual_held_thread_returns(
    tmp_path, monkeypatch
):
    import threading
    import time

    from test_lad_service_journeys import _b4_server_lost_join

    class Observation(SimpleNamespace):
        def model_dump(self):
            return vars(self).copy()

    lost, held = uuid.uuid4().hex, uuid.uuid4().hex
    workers = SimpleNamespace(
        _run=lambda call, operation: None,
        submit=lambda *args, **kwargs: None,
        _observation=lambda call, **kwargs: Observation(
            source="os_owned_call", phase="call_returned", holder_joined=True
        ),
    )
    fixture = SimpleNamespace(service=SimpleNamespace(router_workers=workers))
    _, withheld, returned, release, _, restore = _b4_server_lost_join(
        fixture, monkeypatch, lost, held
    )
    held_call, lost_call = (
        SimpleNamespace(request={"request_id": identifier})
        for identifier in (held, lost)
    )
    worker = threading.Thread(target=workers._run, args=(held_call, None))
    worker.start()
    try:
        assert returned.wait(2) and worker.is_alive() and not release.is_set()
        assert workers._observation(lost_call, version=2).holder_joined is None
        restore(deadline=time.monotonic() + 2)
        assert not worker.is_alive()
        assert workers._observation(lost_call, version=2).holder_joined is True
        assert withheld and all(row["holder_joined"] is None for row in withheld)
    finally:
        release.set()
        worker.join(timeout=2)


@pytest.mark.parametrize("holder", ["missing", "still-active"])
def test_b4_cleanup_missing_or_active_holder_keeps_lost_join_mask(monkeypatch, holder):
    import threading
    import time

    from test_lad_service_journeys import _b4_server_lost_join

    class Observation(SimpleNamespace):
        def model_dump(self):
            return vars(self).copy()

    entered, finish = threading.Event(), threading.Event()

    def original_run(call, operation):
        entered.set()
        finish.wait()

    lost, held = uuid.uuid4().hex, uuid.uuid4().hex
    workers = SimpleNamespace(
        _run=original_run,
        submit=lambda *args, **kwargs: None,
        _observation=lambda call, **kwargs: Observation(
            source="os_owned_call", phase="call_returned", holder_joined=True
        ),
    )
    fixture = SimpleNamespace(service=SimpleNamespace(router_workers=workers))
    _, withheld, _, release, _, restore = _b4_server_lost_join(
        fixture, monkeypatch, lost, held
    )
    lost_call = SimpleNamespace(request={"request_id": lost})
    worker = None
    try:
        if holder == "still-active":
            worker = threading.Thread(
                target=workers._run,
                args=(SimpleNamespace(request={"request_id": held}), None),
            )
            worker.start()
            assert entered.wait(2)
        assert workers._observation(lost_call, version=2).holder_joined is None
        message = "return is unknown" if holder == "missing" else "still active"
        with pytest.raises(AssertionError, match=message):
            restore(deadline=time.monotonic())
        assert workers._observation(lost_call, version=2).holder_joined is None
        assert withheld and all(row["holder_joined"] is None for row in withheld)
    finally:
        release.set()
        finish.set()
        if worker is not None:
            worker.join(timeout=2)


@pytest.mark.parametrize("expires_in", ["construction", "decide", "stop", "read"])
def test_b4_failure_cleanup_keeps_original_deadline_across_callbacks(
    tmp_path, monkeypatch, expires_in
):
    from test_lad_service_journeys import _b4_cleanup_stop

    clock, calls = Clock(), []
    task = {"task_id": uuid.uuid4().hex, "version": 7}
    record = {"instance_id": uuid.uuid4().hex, "run_id": uuid.uuid4().hex}
    facts = {}
    if expires_in == "decide":
        facts["acknowledgement"] = {
            "task_id": task["task_id"],
            "decision": "acknowledge-cancelled",
            "request_id": uuid.uuid4().hex,
            "evidence_file": str(tmp_path / "existing.json"),
            "receipt": None,
        }

    def expire(name):
        calls.append(name)
        if expires_in == name:
            clock.now = 2

    def decide(*args, _deadline):
        assert _deadline == 1
        expire("decide")
        return {"disposition": "cancelled"}

    def stop(identifier):
        expire("stop")
        return {"status": "pending", "reason": "child_disposition_required"}

    def read(*args, deadline):
        assert deadline == 1
        expire("read")
        return {
            "status": "cancelled",
            "quiescent": True,
            "state_version": 31,
            "inflight_invocations": [],
            "invocations": [],
            "completions": [],
            "controls": [],
        }

    active = SimpleNamespace(
        decide=decide,
        stop=stop,
        active=lambda: task,
        call=lambda row: (None, record),
        client=SimpleNamespace(read=read),
    )

    def engine():
        expire("construction")
        return active

    monkeypatch.setattr(runtime.time, "monotonic", clock)
    with pytest.raises(SubworkflowError, match="observation_window_expired"):
        _b4_cleanup_stop(engine, tmp_path, uuid.uuid4().hex, facts, deadline=1)
    assert calls[-1] == expires_in
    assert not (tmp_path / "b4-cleanup-acknowledgement.json").exists()


@pytest.mark.parametrize("interval", [None, 10])
def test_b4_until_default_and_passive_cadence_return_actual_ready_value(
    monkeypatch, interval
):
    from test_lad_service_journeys import _b4_until

    clock, sleeps, starts = Clock(), [], []
    ready = {"rows": ["actual"]}
    answers = [SubworkflowError("observation_window_expired"), None, [], ready]

    def predicate():
        starts.append(clock.now)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    options = {} if interval is None else {"interval": interval}
    assert _b4_until(predicate, seconds=600, **options) is ready
    step = 1 if interval is None else 10
    assert starts == [0, step, 2 * step, 3 * step] and sleeps == [step] * 3
    assert not answers


def test_b4_until_clips_passive_sleep_and_starts_no_predicate_after_expiry(
    monkeypatch,
):
    from test_lad_service_journeys import _b4_until

    clock, sleeps, starts = Clock(), [], []

    def predicate():
        starts.append(clock.now)
        clock.now += 3  # Non-preemptive read cost consumes the same window.
        return None

    def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr(runtime.time, "monotonic", clock)
    monkeypatch.setattr(runtime.time, "sleep", sleep)
    with pytest.raises(AssertionError, match="bounded B4 public observation expired"):
        _b4_until(predicate, seconds=25, interval=10)
    assert starts == [0, 13] and sleeps == [10, 9] and clock.now == 25


def test_owner_retains_original_start_join_before_later_receipt_only_fallback(setup):
    import time

    f = setup
    active = activate(f)
    original = copy.deepcopy(active.record["start"])
    sent = original["envelope"]
    posted = len(f.peer.sent)
    result = active.retain_parent_joins(_deadline=time.monotonic() + 30)
    assert (
        result["holder_joined"] is True and result["request_id"] == sent["request_id"]
    )
    stored = Service(f.home).intake.lifecycle()[1]["start"]
    assert stored["observations"] == [f.peer.responses[sent["request_id"]]]
    assert original["observations"] == [] and stored["envelope"] == original["envelope"]
    fallback = copy.deepcopy(f.peer.responses[sent["request_id"]])
    fallback.update(source="core_receipt", phase="receipt_observed", holder_joined=None)
    f.peer.responses[sent["request_id"]] = fallback
    reopened = runtime.Runtime(Service(f.home), client=f.peer)
    runtime._send(
        reopened.service,
        reopened.intent,
        reopened.record,
        reopened.record["start"],
        f.peer,
        time.monotonic() + 30,
    )
    assert reopened.record["start"]["observations"][-1] == fallback
    assert (
        reopened.retain_parent_joins(_deadline=time.monotonic() + 30)["holder_joined"]
        is True
    )
    assert len(f.peer.sent) == posted  # Original start is never POSTed again.
    assert f.peer.observed == [sent, sent]


@pytest.mark.parametrize(
    "kind", ["unknown", "false", "wrong-id", "wrong-digest", "wrong-target"]
)
def test_owner_start_join_missing_or_mismatched_proof_never_becomes_true(setup, kind):
    import time

    f = setup
    active = activate(f)
    sent = active.record["start"]["envelope"]
    response = copy.deepcopy(f.peer.responses[sent["request_id"]])
    if kind == "unknown":
        response.update(
            source="core_receipt", phase="receipt_observed", holder_joined=None
        )
    elif kind == "false":
        response.update(holder_joined=False, phase="call_active")
    elif kind == "wrong-id":
        response["request_id"] = "f" * 32
    elif kind == "wrong-digest":
        response["request_digest"] = "f" * 64
    else:
        response["instance_id"] = "f" * 32
    f.peer.responses[sent["request_id"]] = response
    posted = len(f.peer.sent)
    if kind in {"unknown", "false"}:
        assert (
            active.retain_parent_joins(_deadline=time.monotonic() + 30)["holder_joined"]
            is None
        )
        assert not runtime._joined(active.service, active.record["start"])
    else:
        before = canonical(Service(f.home).intake.lifecycle()[1])
        with pytest.raises((SubworkflowError, IntakeError)):
            active.retain_parent_joins(_deadline=time.monotonic() + 30)
        assert canonical(Service(f.home).intake.lifecycle()[1]) == before
    assert len(f.peer.sent) == posted


def test_parent_start_join_retention_never_overwrites_concurrent_owner_cas(setup):
    import time

    f = setup
    active = activate(f)
    sent = active.record["start"]["envelope"]
    positive = copy.deepcopy(f.peer.responses[sent["request_id"]])
    fallback = positive | {
        "source": "core_receipt",
        "phase": "receipt_observed",
        "holder_joined": None,
    }
    saved = []

    def concurrent(envelope, *, deadline):
        assert envelope == sent
        intent, before = Service(f.home).intake.lifecycle()
        newer = copy.deepcopy(before)
        runtime._observe_row(active.service, newer["start"], fallback)
        active.service.intake.save_lifecycle(intent, before, newer)
        saved.append(newer)
        return positive

    f.peer.observe = concurrent
    with pytest.raises(IntakeError, match="lifecycle_conflict"):
        active.retain_parent_joins(_deadline=time.monotonic() + 30)
    assert Service(f.home).intake.lifecycle()[1] == saved[0]
    assert not runtime._joined(active.service, saved[0]["start"])


@pytest.mark.parametrize("operation", ["resume_run", "pause_run"])
def test_owner_retains_saved_parent_control_join_across_os_fallback(setup, operation):
    import time

    f = setup
    active = activate(f)
    f.peer.joined = False
    path, sent = parent_request(
        active, f, operation=operation, expected=None if operation == "pause_run" else 1
    )
    assert active.parent_control(path)["holder_joined"] is False
    old = copy.deepcopy(
        active.record["maintenance"]["parent:" + sent["request_id"]]["call"]
    )
    f.peer.responses[sent["request_id"]].update(
        holder_joined=True, phase="call_returned"
    )
    posted = len(f.peer.sent)
    answer = active.retain_parent_joins(
        parent_controls=(sent,), _deadline=time.monotonic() + 30
    )
    assert answer["parent_controls"][0]["holder_joined"] is True
    stored = Service(f.home).intake.lifecycle()[1]["maintenance"][
        "parent:" + sent["request_id"]
    ]["call"]
    assert stored["observations"][: len(old["observations"])] == old["observations"]
    assert stored["observations"][-1]["holder_joined"] is True
    f.peer.responses[sent["request_id"]].update(
        source="core_receipt", holder_joined=None, phase="receipt_observed"
    )
    reopened = runtime.Runtime(Service(f.home), client=f.peer)
    row = reopened.record["maintenance"]["parent:" + sent["request_id"]]["call"]
    runtime._send(
        reopened.service,
        reopened.intent,
        reopened.record,
        row,
        f.peer,
        time.monotonic() + 30,
    )
    assert row["observations"][-1]["holder_joined"] is None
    assert (
        reopened.retain_parent_joins(
            parent_controls=(sent,), _deadline=time.monotonic() + 30
        )["parent_controls"][0]["holder_joined"]
        is True
    )
    assert (
        len(f.peer.sent) == posted and old["observations"][-1]["holder_joined"] is False
    )


@pytest.mark.parametrize(
    "kind",
    [
        "unknown",
        "false",
        "wrong-id",
        "wrong-digest",
        "wrong-target",
        "unsent",
        "envelope",
    ],
)
def test_owner_parent_control_retention_refuses_unknown_and_mismatched_proof(
    setup, kind
):
    import time

    f = setup
    active = activate(f)
    active.retain_parent_joins(_deadline=time.monotonic() + 30)
    f.peer.joined = False
    path, sent = parent_request(active, f)
    active.parent_control(path)
    response = f.peer.responses[sent["request_id"]]
    if kind == "unknown":
        response.update(
            source="core_receipt", holder_joined=None, phase="receipt_observed"
        )
    elif kind == "wrong-id":
        response["request_id"] = "f" * 32
    elif kind == "wrong-digest":
        response["request_digest"] = "f" * 64
    elif kind == "wrong-target":
        response["run_id"] = "f" * 32
    elif kind == "unsent":
        before = copy.deepcopy(active.record)
        active.record["maintenance"]["parent:" + sent["request_id"]]["call"] = (
            runtime._row(sent)
        )
        runtime._save(active.service, active.intent, before, active.record)
    elif kind == "envelope":
        sent = sent | {"payload": {"changed": True}}
    posted = len(f.peer.sent)
    if kind in {"unknown", "false"}:
        result = active.retain_parent_joins(
            parent_controls=(sent,), _deadline=time.monotonic() + 30
        )
        assert result["parent_controls"][0]["holder_joined"] is None
    else:
        before = canonical(Service(f.home).intake.lifecycle()[1])
        with pytest.raises((SubworkflowError, IntakeError)):
            active.retain_parent_joins(
                parent_controls=(sent,), _deadline=time.monotonic() + 30
            )
        assert canonical(Service(f.home).intake.lifecycle()[1]) == before
    assert len(f.peer.sent) == posted


def test_parent_control_join_retention_cas_cannot_overwrite_concurrent_owner(setup):
    import time

    f = setup
    active = activate(f)
    active.retain_parent_joins(_deadline=time.monotonic() + 30)
    f.peer.joined = False
    path, sent = parent_request(active, f)
    active.parent_control(path)
    positive = copy.deepcopy(f.peer.responses[sent["request_id"]])
    positive.update(holder_joined=True, phase="call_returned")
    saved = []

    def concurrent(envelope, *, deadline):
        assert envelope == sent
        intent, before = Service(f.home).intake.lifecycle()
        newer = copy.deepcopy(before)
        runtime._observe_row(
            active.service,
            newer["maintenance"]["parent:" + sent["request_id"]]["call"],
            positive
            | {
                "source": "core_receipt",
                "holder_joined": None,
                "phase": "receipt_observed",
            },
        )
        active.service.intake.save_lifecycle(intent, before, newer)
        saved.append(newer)
        return positive

    f.peer.observe = concurrent
    with pytest.raises(IntakeError, match="lifecycle_conflict"):
        active.retain_parent_joins(
            parent_controls=(sent,), _deadline=time.monotonic() + 30
        )
    stored = Service(f.home).intake.lifecycle()[1]
    assert stored == saved[0]
    assert not runtime._joined(
        active.service, stored["maintenance"]["parent:" + sent["request_id"]]["call"]
    )


@pytest.mark.parametrize("operation", ["resume_plane", "recover_run", "cancel_run"])
def test_owner_default_retention_includes_sent_operator_parent_rows(setup, operation):
    import time

    f = setup
    active = activate(f)
    active.retain_parent_joins(_deadline=time.monotonic() + 30)
    f.peer.joined = False
    path, sent = parent_request(
        active,
        f,
        operation=operation,
        expected=None if operation == "cancel_run" else 1,
        plane_id="service" if operation == "resume_plane" else None,
    )
    assert active.parent_control(path)["holder_joined"] is False
    old = copy.deepcopy(
        active.record["maintenance"]["parent:" + sent["request_id"]]["call"]
    )
    posted = len(f.peer.sent)
    f.peer.responses[sent["request_id"]].update(
        holder_joined=True, phase="call_returned"
    )
    proof = active.retain_parent_joins(_deadline=time.monotonic() + 30)
    assert proof["parent_controls"][0]["request_id"] == sent["request_id"]
    assert proof["parent_controls"][0]["holder_joined"] is True
    stored = Service(f.home).intake.lifecycle()[1]["maintenance"][
        "parent:" + sent["request_id"]
    ]["call"]
    assert stored["observations"][: len(old["observations"])] == old["observations"]
    assert len(f.peer.sent) == posted
    f.peer.responses[sent["request_id"]].update(
        source="core_receipt", holder_joined=None, phase="receipt_observed"
    )
    reopened = runtime.Runtime(Service(f.home), client=f.peer)
    row = reopened.record["maintenance"]["parent:" + sent["request_id"]]["call"]
    runtime._send(
        reopened.service,
        reopened.intent,
        reopened.record,
        row,
        f.peer,
        time.monotonic() + 30,
    )
    assert (
        reopened.retain_parent_joins(_deadline=time.monotonic() + 30)[
            "parent_controls"
        ][0]["holder_joined"]
        is True
    )
    assert len(f.peer.sent) == posted


def test_owner_default_retention_never_sends_unsent_parent_row(setup, monkeypatch):
    import time

    f = setup
    active = activate(f)
    active.retain_parent_joins(_deadline=time.monotonic() + 30)
    path, sent = parent_request(active, f, operation="resume_plane", plane_id="service")
    original_send = runtime._send
    monkeypatch.setattr(runtime, "_send", lambda *a, **k: None)
    active.parent_control(path)
    monkeypatch.setattr(runtime, "_send", original_send)
    posted, observed = len(f.peer.sent), len(f.peer.observed)
    assert (
        active.retain_parent_joins(_deadline=time.monotonic() + 30)["parent_controls"]
        == []
    )
    assert len(f.peer.sent) == posted and len(f.peer.observed) == observed
    assert (
        active.record["maintenance"]["parent:" + sent["request_id"]]["call"][
            "reservation"
        ]
        == "unsent"
    )


def test_parent_retention_skips_valid_scope_less_child_maintenance(setup, monkeypatch):
    import time

    from millrace_plus.factory_lad import Lane

    active, task, call, evidence, decision = prepared_plane(setup, monkeypatch)
    child = copy.deepcopy(active.service.intake.lifecycle()[1]["maintenance"][decision])
    assert "scope" not in child and child["task_id"] == task["task_id"]
    path, sent = parent_request(active, setup, operation="pause_run", expected=None)
    active.parent_control(path)
    posted = copy.deepcopy(setup.peer.sent)
    owner = runtime.Runtime(Service(setup.home), client=setup.peer)
    proof = owner.retain_parent_joins(_deadline=time.monotonic() + 30)
    assert [row["request_id"] for row in proof["parent_controls"]] == [
        sent["request_id"]
    ]
    assert proof["parent_controls"][0]["holder_joined"] is True
    assert owner.service.intake.lifecycle()[1]["maintenance"][decision] == child
    assert setup.peer.sent == posted
    lane = object.__new__(Lane)
    lane.runtime = owner
    lane._known_parent_controls({"controls": []}, {"controls": []})
    assert (
        lane._expected_receipts == {}
    )  # No child row was treated as parent authority.


# ------------------------------------------- provider wait: actual service child

from public_runtime_support import runtime as public_runtime  # noqa: E402,F401


def test_provider_wait_service_child_resumes_only_through_decide(request, monkeypatch):
    """Offline B5B seam evidence: actual OS/Core lifecycle, real claude_print
    adapter with an inert fake CLI. Not native/provider-route qualification."""
    import hashlib
    import json
    import shutil
    import sys
    import threading
    import time
    from datetime import UTC, datetime

    import lad_provider_support as support
    from public_runtime_support import write_json
    from test_lad_service_journeys import (
        _release_unrelated_fixture_parent,
        _terminal_proof,
    )

    from millrace_plus import lad_service_disposition as disposition
    from millrace_plus import lad_service_runtime as service_runtime
    from millrace_plus import provider_wait as pw
    from millrace_plus import workflow_os_client as os_client
    from millrace_plus.lad_service_runtime import initialize
    from millrace_plus.router import control
    from millrace_plus.subworkflow_v2 import closed, has_join_proof

    try:
        public = request.getfixturevalue("public_runtime")
    except pytest.FixtureLookupError:
        public = request.getfixturevalue("runtime")
    # Actual serialized OS response sizes at the existing client read seam:
    # WorkflowOSClient._request reads the exact response bytes and decodes them
    # with _utf8_json(raw, 2097152). Only this process's client calls are seen;
    # only byte counts, digests and a nonsecret method/path category are kept.
    # The wrapped calls keep their exact arguments, results and exceptions.
    measured: list[dict[str, object]] = []
    local = threading.local()  # per-thread call category stack
    real_request = os_client.WorkflowOSClient._request
    real_decode = os_client._utf8_json

    def request_seam(self, method, path, *, deadline, body=None, call=False):
        parts = path.split("?", 1)[0].split("/")
        named = [
            part if part in {"requests", "instances", "runs"} else "<id>"
            for part in parts
        ]
        stack = local.__dict__.setdefault("stack", [])
        stack.append(method + " " + "/".join(named))
        try:
            value = real_request(
                self, method, path, deadline=deadline, body=body, call=call
            )
        finally:
            label = stack.pop()
            if body is not None:
                measured.append(
                    {
                        "category": label,
                        "direction": "request",
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                    }
                )
        return value

    def decode_seam(raw, limit):
        stack = local.__dict__.get("stack")
        if limit == 2097152 and stack:  # the response path only
            measured.append(
                {
                    "category": stack[-1],
                    "direction": "response",
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        return real_decode(raw, limit)

    monkeypatch.setattr(os_client.WorkflowOSClient, "_request", request_seam)
    monkeypatch.setattr(os_client, "_utf8_json", decode_seam)

    def await_state(observe, problem):
        """Poll actual public state until it holds; fail on a concrete problem.

        No elapsed-time acceptance window: product transport/invocation
        deadlines and the owned cleanup safeguards below remain in force.
        """
        while True:
            value = observe()
            if value:
                return value
            found = problem()
            if found:
                pytest.fail(found)
            time.sleep(0.05)

    _release_unrelated_fixture_parent(public)
    definition = public.catalog / "public-lad-provider"
    shutil.copytree(catalog.bundled_root() / "lad", definition)
    executable = support.fake_cli(
        public.root / "native", [429, "success"], scripts=definition / "scripts"
    )
    graph = json.loads((definition / "workflow.json").read_bytes())
    graph["bindings"]["claude"]["executable"] = str(executable)
    graph["bindings"]["command"]["executable"] = sys.executable
    (definition / "workflow.json").write_text(json.dumps(graph, indent=2) + "\n")
    # Small positive captured *test* policy before any operation exists.
    (definition / "context/provider-policy.json").write_text(
        json.dumps(support.TEST_POLICY, indent=2) + "\n"
    )
    sha = catalog.workflow_fingerprint(definition)
    pin = public.root / "public-lad-provider-pin.json"
    write_json(
        pin,
        {
            "selector": "local:public-lad-provider",
            "version": "sha256:" + sha,
            "sha256": sha,
        },
    )
    original_settings = service_runtime._settings
    monkeypatch.setattr(
        service_runtime,
        "_settings",
        lambda target: (
            original_settings(target) | {"env_allowlist": ["HOME", "PYTHONPATH"]}
        ),
    )
    home = public.root / "provider-service"
    initialized = initialize(
        home, public.target, Path(public.profile["path"]), pin, public.home
    )
    service = Service(home)
    path = public.root / "provider-request.md"
    path.write_bytes(b"Original request: answer() returns 42.\n")
    service.submit("provider-0", path)
    started = public.send_parent(initialized["start_request"])
    assert started["status"] == "applied", started
    parent, parent_run = initialized["instance_id"], started["run_id"]

    def parent_problem():
        view = control.inspect_run(parent, parent_run, state_home=public.home)
        if view["status"] in {"succeeded", "failed", "cancelled"}:
            return "service parent ended: " + str(view["status"])
        return None

    # Let the graph bind its exact start receipt before operator-side reads.
    await_state(
        lambda: Service(home).intake.lifecycle()[1]["run_id"] == parent_run,
        parent_problem,
    )

    def finite():
        """A fresh Runtime per public operation, as the CLI and service do.

        Runtime is one finite operation: it loads the lifecycle record once and
        its lifecycle CAS rejects that snapshot after any competing writer.
        """
        return runtime.Runtime(Service(home))

    finite().parent()
    stop_id = uuid.uuid4().hex
    stopped = False
    try:

        def task_problem():
            rows = Service(home).intake.tasks()
            if rows and rows[0]["status"] in {"failed", "cancelled", "abandoned"}:
                return "task ended: " + str(rows[0]["status"])
            return parent_problem()

        def provider_paused():
            rows = Service(home).intake.tasks()
            if len(rows) != 1 or rows[0]["status"] not in {"held", "running"}:
                return None
            reader = finite()
            try:
                _, record = reader.call(rows[0])
            except Exception:
                return None  # the child call is not bound yet
            child = control.inspect_run(
                record["instance_id"], record["run_id"], state_home=public.home
            )
            plane = child["planes"]["foreground"]
            if child["status"] in {"succeeded", "failed", "cancelled"}:
                pytest.fail("child ended before provider pause: " + child["status"])
            if child["status"] == "paused" and (
                plane.get("resume_target") != "provider_due_recon"
            ):
                pytest.fail(
                    "child paused elsewhere: "
                    + str(plane.get("resume_target"))
                    + " "
                    + str(child.get("pause_reason"))
                )
            return (
                (rows[0], record, child)
                if child["status"] == "paused"
                and plane.get("resume_target") == "provider_due_recon"
                else None
            )

        task, record, child = await_state(provider_paused, task_problem)
        instance, run = record["instance_id"], record["run_id"]
        project = Path(record["paths"]["project"])
        operation = support.pending(project, "recon")
        assert operation.tip["kind"] == "failure_classified"
        assert operation.authority["service"]["task_id"] == task["task_id"]
        state = disposition._load_worker(record)
        assert state["continuation"]["node"] == "provider_due_recon"
        assert state["continuation"]["cleanup_required"] is True
        assert state["request"] is None and state["terminal"] is None
        # Explicit journaled parent pause at the observed child pause (the idle
        # pause pattern): the same saved request is re-sent until applied, the
        # parent is closed with attention and its control holder has joined.
        pause = public.root / "provider-parent-pause.json"
        write_json(
            pause,
            service_runtime._env(
                "pause_run", uuid.uuid4().hex, instance=parent, run=parent_run
            ),
        )

        def parent_paused():
            answer = finite().parent_control(pause)
            receipt = answer["receipt"]
            if receipt is not None and receipt["status"] == "refused":
                pytest.fail(str(receipt))
            view = control.inspect_run(parent, parent_run, state_home=public.home)
            return (
                answer
                if receipt is not None
                and receipt["status"] == "applied"
                and view["status"] == "paused"
                and closed(view, attention=True)
                else None
            )

        applied_pause = await_state(parent_paused, task_problem)
        await_state(
            lambda: finite().parent_control(pause)["holder_joined"],
            task_problem,
        )
        # Exact replay of the journaled envelope is a read of the same receipt.
        replayed = finite().parent_control(pause)
        assert replayed["receipt"] == applied_pause["receipt"]
        assert json.loads(pause.read_bytes())["operation"] == "pause_run"
        # Busy maintenance: while the owned target lock is held, observation
        # refuses without publication or any journal or child-control change.
        from millrace_plus.lad_service import target_lock

        unobserved = support.events(project, "recon")
        maintenance = Service(home).intake.lifecycle()[1]["maintenance"]
        owner = Service(home)
        with target_lock(owner.config, protected_roots=(owner.home,)):
            with pytest.raises(IntakeError) as busy:
                pw.observe_snapshot(
                    project,
                    state_home=public.home,
                    instance_id=instance,
                    run_id=run,
                    plane_id="foreground",
                    operation_id=operation.id,
                    invocation_id=support.failed_invocation(operation),
                    expected_state_version=int(child["state_version"]),
                    observed_at=datetime.now(UTC),
                    service_home=home,
                    task=task["task_id"],
                )
        assert busy.value.code == "target_busy"
        assert support.events(project, "recon") == unobserved
        assert Service(home).intake.lifecycle()[1]["maintenance"] == maintenance
        current = control.inspect_run(instance, run, state_home=public.home)
        assert current["state_version"] == child["state_version"]
        assert current["controls"] == child["controls"]
        # Exact public observation under the service target lock.
        observed = pw.observe_snapshot(
            project,
            state_home=public.home,
            instance_id=instance,
            run_id=run,
            plane_id="foreground",
            operation_id=operation.id,
            invocation_id=support.failed_invocation(operation),
            expected_state_version=int(child["state_version"]),
            observed_at=datetime.now(UTC),
            service_home=home,
            task=task["task_id"],
        )
        assert observed["phase"] == "waiting"
        from millrace.router.execution import RouterError

        unpublished = support.events(project, "recon")
        with pytest.raises(RouterError) as stale:
            # Core's public snapshot refuses a stale version without publication.
            pw.observe_snapshot(
                project,
                state_home=public.home,
                instance_id=instance,
                run_id=run,
                plane_id="foreground",
                operation_id=operation.id,
                invocation_id=support.failed_invocation(operation),
                expected_state_version=int(child["state_version"]) - 1,
                observed_at=datetime.now(UTC),
                service_home=home,
                task=task["task_id"],
            )
        assert stale.value.code == "stale_state_version"
        assert support.events(project, "recon") == unpublished
        due = support.restore(project, operation.id, str(observed["observation_id"]))
        assert due["eligible"] is True
        # Journaled service continuation only; no raw child control.
        evidence = public.root / "provider-continue.txt"
        evidence.write_text("same child only: provider restoration confirmed")

        def continue_decision(request_id, **plane):
            """One finite continue decision with its own immutable request.

            ``child_resume_pending`` is the supported in-flight receipt state:
            re-running the same request ID, evidence and plane only observes
            the saved calls (Runtime._send), never sends a new action.
            """

            def attempt():
                try:
                    return finite().decide(
                        task["task_id"], "continue", request_id, evidence, **plane
                    )
                except IntakeError as exc:
                    if exc.code != "child_resume_pending":
                        raise
                    return None

            return await_state(attempt, task_problem)

        plane_request = uuid.uuid4().hex
        try:
            answer = continue_decision(plane_request, resume_plane="foreground")
        except IntakeError as exc:
            # Only an actual child run gate needs the separate run continuation,
            # a distinct run-level decision with its own request ID.
            assert exc.code == "separate_run_resume_required", exc
            gated = control.inspect_run(instance, run, state_home=public.home)
            assert gated["run_gate"] is not None, gated
            answer = continue_decision(uuid.uuid4().hex)
        assert answer is not None
        # The plane continuation's exact control and applied receipt persist.
        resumed = Service(home).intake.lifecycle()[1]["maintenance"][plane_request][
            "resume"
        ]
        assert resumed["envelope"]["operation"] == "resume_plane"
        assert resumed["envelope"]["plane_id"] == "foreground"
        assert resumed["receipt"]["request_id"] == resumed["envelope"]["request_id"]
        assert resumed["receipt"]["status"] == "applied"
        # Resume the explicitly paused parent from its retained current version.
        version = control.inspect_run(parent, parent_run, state_home=public.home)[
            "state_version"
        ]
        resume = public.root / "provider-parent-resume.json"
        write_json(
            resume,
            service_runtime._env(
                "resume_run",
                uuid.uuid4().hex,
                instance=parent,
                run=parent_run,
                expected=version,
            ),
        )

        def parent_resumed():
            answer = finite().parent_control(resume)
            receipt = answer["receipt"]
            if receipt is not None and receipt["status"] == "refused":
                pytest.fail(str(receipt))
            return answer if receipt and receipt["status"] == "applied" else None

        applied_resume = await_state(parent_resumed, task_problem)
        # resume_run restarts the long-lived service cursor: its holder joins
        # only at lawful stop/closure. Here only the exact replay is asserted;
        # the join proof for this same control identity follows the stop.
        resume_id = json.loads(resume.read_bytes())["request_id"]
        assert applied_resume["request_id"] == resume_id
        assert finite().parent_control(resume)["receipt"] == applied_resume["receipt"]

        def settled():
            rows = Service(home).intake.tasks()
            return rows[0] if rows[0]["status"] == "succeeded" else None

        def service_hold(view):
            """The stock lad-service operator hold (WAIT_OPERATOR): the service
            plane paused at its ``reconcile`` resume target, no run gate."""
            plane = view["planes"].get("service", {})
            return (
                view["status"] == "paused"
                and view.get("run_gate") is None
                and plane.get("status") == "paused"
                and plane.get("resume_target") == "reconcile"
                and plane.get("active_invocation") is None
            )

        def settlement_problem(*, hold_allowed):
            current = control.inspect_run(instance, run, state_home=public.home)
            if current["status"] in {"failed", "cancelled"}:
                return "child ended: " + str(current["status"])
            if current["status"] == "paused":
                return "child paused: " + str(
                    current["planes"]["foreground"].get("resume_target")
                )
            outer = control.inspect_run(parent, parent_run, state_home=public.home)
            if outer["status"] == "paused" and not (
                hold_allowed and service_hold(outer)
            ):
                return (
                    "service parent paused: "
                    + str(outer.get("pause_reason"))
                    + " at "
                    + str(outer["planes"].get("service", {}).get("resume_target"))
                )
            return task_problem()

        def settled_or_held():
            row = settled()
            if row:
                return "settled", row
            outer = control.inspect_run(parent, parent_run, state_home=public.home)
            return ("held", outer) if service_hold(outer) else None

        phase, value = await_state(
            settled_or_held, lambda: settlement_problem(hold_allowed=True)
        )
        plane_resume, plane_receipt = None, None
        if phase == "held":
            # The child attention reached the service graph's operator hold,
            # which paused the service plane itself; resume_run cleared only the
            # run gate. After the operator decision, release that hold through
            # the journaled version-fenced public resume_plane(service), with
            # its own immutable request identity.
            plane_resume = public.root / "provider-parent-plane-resume.json"
            selected = service_runtime._env(
                "resume_run",
                uuid.uuid4().hex,
                instance=parent,
                run=parent_run,
                expected=value["state_version"],
            )
            selected.update(operation="resume_plane", plane_id="service")
            write_json(plane_resume, selected)

            # Journal and send it once, at the quiescent hold. parent_control
            # releases the target lock before sending; a resumed parent's
            # advance then needs that lock (materialize_task), so no further
            # parent_control runs while the parent graph is active.
            sent_plane = finite().parent_control(plane_resume)
            assert sent_plane["request_id"] == selected["request_id"] != resume_id
            plane_receipt = sent_plane["receipt"]
            if plane_receipt is None or plane_receipt["status"] != "applied":
                # Observe the same saved public call, GET-only, validated against
                # its exact immutable envelope (no lock, lifecycle write or new
                # request), using the service's own OS profile and home.
                saved_plane = json.loads(plane_resume.read_bytes())
                observer = os_client.WorkflowOSClient(
                    service.config["os_profile"], str(service.config["os_home"])
                )

                def plane_applied():
                    found = observer.observe(
                        saved_plane, deadline=time.monotonic() + 30
                    )["core_receipt"]
                    if found is not None and found["status"] == "refused":
                        pytest.fail(str(found))
                    return found if found and found["status"] == "applied" else None

                plane_receipt = await_state(plane_applied, task_problem)
            assert plane_receipt["request_id"] == selected["request_id"]
            value = await_state(settled, lambda: settlement_problem(hold_allowed=False))
        final = value
        _, again = finite().call(final)
        assert (again["instance_id"], again["run_id"]) == (instance, run)
        view = control.inspect_run(instance, run, state_home=public.home)
        assert view["status"] == "succeeded" and closed(view), view
        kinds = [event["kind"] for event in support.events(project, "recon")]
        assert kinds.count("retry_reserved") == 1 and kinds[-1] == "succeeded"
        assert [row["node"] for row in view["invocations"]].count("recon") == 2
        assert all(has_join_proof(again, name) for name in ("create", "start"))
        assert disposition.domain_evidence(service, again, view)["missing"] == []
        proof = _terminal_proof(service, final)
        assert proof["domain_evidence"]["missing"] == []
        physical = proof["physical_evidence"]
        assert physical["view_proof"] is True and physical["sent_calls"]
        for pair in physical["sent_calls"]:
            # Every sent child call, including maintenance pause/resume, joined.
            sent = json.loads(disposition.read_ref(service, pair["envelope"]))
            joined = json.loads(disposition.read_ref(service, pair["joined"]))
            assert any(
                row["request_id"] == sent["request_id"] and row["holder_joined"] is True
                for row in joined
            )
        worker = disposition._load_worker(again)
        assert worker["terminal"] == "succeeded"
        assert worker["continuation"]["cleanup_required"] is False

        def stopping():
            answer = finite().stop(stop_id)
            return answer if answer["status"] == "stopped" else None

        assert await_state(stopping, lambda: None)["status"] == "stopped"
        stopped = True
        assert closed(control.inspect_run(parent, parent_run, state_home=public.home))
        # Deferred join proof: the same resume control identity, GET-only replay.
        joined_resume = finite().parent_control(resume)
        assert joined_resume["request_id"] == resume_id
        assert joined_resume["receipt"] == applied_resume["receipt"]
        assert joined_resume["holder_joined"] is True
        if plane_resume is not None:
            # After stop refreshed and joined every parent row: the same
            # plane-resume control identity, exact replay, identical receipt.
            joined_plane = finite().parent_control(plane_resume)
            assert joined_plane["request_id"] == selected["request_id"]
            assert joined_plane["receipt"] == plane_receipt
            assert joined_plane["holder_joined"] is True
        controls = json.loads((definition / "workflow.json").read_bytes())[
            "control_files"
        ]
        responses = [row for row in measured if row["direction"] == "response"]
        sizes = {
            "workflow_json_bytes": len((definition / "workflow.json").read_bytes()),
            "control_files_bytes": sum(
                len((definition / name).read_bytes()) for name in controls
            ),
            "init_session_bytes": len(
                (definition / "scripts/init_session.py").read_bytes()
            ),
            "os_client_response_limit": 2097152,
            "observed": measured,
            "max_response_bytes": max(
                (int(row["bytes"]) for row in responses), default=None
            ),
            "categories": sorted({str(row["category"]) for row in responses}),
            "unobserved": "OS client calls made by other processes (service parent "
            "graph workers and child processes) are outside this in-process seam",
        }
        write_json(public.root / "provider-sizes.json", sizes)
        assert responses, "no in-process OS client response was observed"
        assert int(sizes["max_response_bytes"]) <= 2097152
    finally:
        primary_error = sys.exc_info()[1]
        try:
            try:
                if not stopped:

                    def cleanup_stop():
                        answer = finite().stop(stop_id)
                        return answer if answer["status"] == "stopped" else None

                    public.until(cleanup_stop, seconds=90)
            finally:
                write_json(
                    public.root / "provider-aftermath.json", Service(home).status()
                )
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise primary_error from cleanup_error
            raise
