"""Factory task data does not create LAD resume or ownership authority."""

from __future__ import annotations

import pytest

from millrace_plus.factory_runtime import ROUTES


def test_submission_resume_observation_and_park_are_separate_declared_operations():
    assert ROUTES["submit_lad"]["SUBMITTED"] == "resume_lad_parent"
    assert ROUTES["resume_lad_parent"]["APPLIED"] == "observe_lad"
    assert ROUTES["observe_lad"]["SUCCEEDED"] == "park_lad"
    assert ROUTES["park_lad"]["CLOSED"] == "export_candidate"
    assert ROUTES["wait_provider"]["WAIT"] == "@factory_lad_provider_wait"


def test_join_observation_keeps_pending_cache_and_requires_actual_true():
    import copy
    import time
    from types import SimpleNamespace

    from persistent_lad_support import observation, receipt

    from millrace_plus.factory_lad import Lane

    envelope = {
        "format": "millrace-router-request/v2",
        "operation": "pause_run",
        "instance_id": "1" * 32,
        "run_id": "2" * 32,
        "plane_id": None,
        "request_id": "3" * 32,
        "expected_state_version": None,
        "payload": {},
    }
    applied = receipt(envelope, instance="1" * 32, run="2" * 32)
    cached = observation(envelope, joined=False, core=applied | {"status": "queued"})
    row = {
        "envelope": envelope,
        "receipt": cached["core_receipt"],
        "observations": [cached],
    }
    lane = object.__new__(Lane)
    lane.service = SimpleNamespace(config={"os_home": "local"})
    lane._expected_receipts = {envelope["request_id"]: applied}
    original = copy.deepcopy(row)
    for joined in (None, False, True):
        fresh = observation(envelope, joined=joined, core=applied)
        lane.runtime = SimpleNamespace(
            connection=SimpleNamespace(observe=lambda *a, **k: fresh)
        )
        captured = []
        assert lane._current_join(row, time.monotonic() + 30, captured) is (
            joined is True
        )
        assert row == original and captured == [fresh]


def test_join_observation_refuses_mismatched_and_conflicting_receipt():
    import time
    from types import SimpleNamespace

    import pytest
    from persistent_lad_support import observation, receipt

    from millrace_plus.factory_lad import Lane

    envelope = {
        "format": "millrace-router-request/v2",
        "operation": "pause_run",
        "instance_id": "1" * 32,
        "run_id": "2" * 32,
        "plane_id": None,
        "request_id": "3" * 32,
        "expected_state_version": None,
        "payload": {},
    }
    applied = receipt(envelope, instance="1" * 32, run="2" * 32)
    row = {"envelope": envelope, "receipt": None, "observations": []}
    lane = object.__new__(Lane)
    lane.service = SimpleNamespace(config={"os_home": "local"})
    lane._expected_receipts = {envelope["request_id"]: applied}
    for changed in (
        applied | {"instance_id": "4" * 32},
        applied | {"status": "refused"},
        applied | {"request_digest": "a" * 64},
    ):
        fresh = observation(envelope, joined=True, core=changed)
        lane.runtime = SimpleNamespace(
            connection=SimpleNamespace(observe=lambda *a, **k: fresh)
        )
        with pytest.raises(Exception):
            lane._current_join(row, time.monotonic() + 30, [])


def test_park_cannot_use_later_stateless_positive_before_owner_retains(
    monkeypatch, tmp_path
):
    import time
    from types import SimpleNamespace

    from millrace_plus import factory_lad

    lane = object.__new__(factory_lad.Lane)
    lane.service = SimpleNamespace(intake=SimpleNamespace(tasks=lambda: []))
    calls = []
    lane._current_join = lambda *args: calls.append(args) or True
    for reason in [
        "factory_lad_parent_draining",
        "factory_lad_parent_unjoined",
        "factory_lad_parent_observation_race",
    ]:
        lane.retain_paused_parent = lambda **kwargs: {
            "retained": False,
            "reason": reason,
        }
        assert lane.parked(None, caller_deadline=time.monotonic() + 30) == {
            "closed": False,
            "reason": reason,
            "task": None,
        }
    assert calls == []


def test_park_retains_before_unsettled_task_return(tmp_path):
    import time
    from types import SimpleNamespace

    from millrace_plus.factory_lad import Lane

    lane = object.__new__(Lane)
    seen = []
    lane.retain_paused_parent = lambda **k: (
        seen.append("owner-retained")
        or {"retained": True, "instance": {}, "parent": {"status": "paused"}}
    )
    lane.home = SimpleNamespace(lad_tasks=lambda: ["task"])
    lane.service = SimpleNamespace(
        intake=SimpleNamespace(tasks=lambda: [{"task_id": "task", "status": "held"}])
    )
    assert (
        lane.parked(None, caller_deadline=time.monotonic() + 30)["reason"]
        == "factory_lad_unsettled"
    )
    assert seen == ["owner-retained"]


@pytest.mark.parametrize("task_status", ["held", "running", "claimed"])
def test_observe_hold_waits_for_parent_and_owner_retention(monkeypatch, task_status):
    import time
    from types import SimpleNamespace

    from millrace_plus import factory_lad

    lane = object.__new__(factory_lad.Lane)
    request = {"task_ref": "task-ref"}
    lane._request = lambda *a: request
    lane.home = SimpleNamespace(
        config={"timeouts": {"observation_windows": {"observe_lad": {"seconds": 0}}}},
        read_ref=lambda ref: {"task_id": "task", "request_sha256": "sha"},
        retain=lambda value: None,
    )
    lane._task = lambda task: {
        "task_id": "task",
        "request_sha256": "sha",
        "status": task_status,
        "request_bytes": b"request",
    }
    lane._result = lambda request, status, proof: (status, proof)
    for retained, reason in [
        (False, "factory_lad_parent_draining"),
        (False, "factory_lad_parent_unjoined"),
        (False, "factory_lad_parent_observation_race"),
        (True, None),
    ]:
        lane.retain_paused_parent = lambda **k: {
            "retained": retained,
            "reason": reason,
        }
        result, proof = lane.observe_task(None, caller_deadline=time.monotonic() + 30)
        assert result == ("held" if retained else "pending")
        assert proof["task"]["status"] == task_status
        if retained and task_status != "held":
            assert proof["reason"] == "factory_lad_parent_attention"
        assert lane._task("task")["status"] == task_status


def test_retention_barrier_reloads_after_cas_and_never_reuses_uncommitted_true(
    monkeypatch,
):
    import time
    from types import SimpleNamespace

    from millrace_plus import factory_lad
    from millrace_plus.local_intake import IntakeError

    lane = object.__new__(factory_lad.Lane)
    lane.config = {"parent_instance_id": "parent"}
    lane.service = object()
    lane._pins = lambda: None
    created = []

    def fresh(service):
        model = {"positive": False}
        created.append(model)

        def retain(**kwargs):
            model["positive"] = True  # In-memory observation is not a durable CAS.
            raise IntakeError("lifecycle_conflict")

        return SimpleNamespace(
            parent=lambda **k: ({}, {"instance_id": "parent"}),
            retain_parent_joins=retain,
        )

    monkeypatch.setattr(factory_lad, "Runtime", fresh)
    monkeypatch.setattr(factory_lad, "closed", lambda *a, **k: True)
    for _ in range(2):
        assert lane.retain_paused_parent(caller_deadline=time.monotonic() + 30) == {
            "retained": False,
            "reason": "factory_lad_parent_observation_race",
        }
    assert len(created) == 2 and all(row["positive"] for row in created)


def test_execute_hold_cannot_pause_before_retained_parent_proof(monkeypatch):
    import time
    from types import SimpleNamespace

    from millrace_plus import factory_lad
    from millrace_plus._factory_steps import Runtime

    step = object.__new__(Runtime)
    step.node = "hold"
    step.deadline = time.monotonic() + 30
    step.home = SimpleNamespace(
        active=lambda: {"task_id": "job"},
        state=lambda job: {"phase": "hold"},
        facts=lambda state: {"lad_task_id": "task"},
        retain=lambda value: "proof",
    )
    step._out = lambda outcome, *a: outcome
    step._save = lambda *a: None
    reports = []
    step._hold_report = lambda *a: reports.append(True) or "report"
    for retained in [False, True]:
        monkeypatch.setattr(
            factory_lad,
            "Lane",
            lambda home: SimpleNamespace(
                wait_paused_parent=lambda **k: {
                    "retained": retained,
                    "reason": "joining",
                }
            ),
        )
        assert step.execute() == ("WAIT_OPERATOR" if retained else "PENDING")
        assert bool(reports) is retained


def test_explicit_parent_resume_validates_current_scope_and_sent_replay(
    monkeypatch, tmp_path
):
    import copy
    import time
    from types import SimpleNamespace

    import pytest

    from millrace_plus import factory_lad
    from millrace_plus.local_intake import IntakeError

    job, task = "a" * 32, "b" * 32
    task_ref = {"path": "task.json", "sha256": "a" * 64}
    request_ref = {"path": "parent.json", "sha256": "b" * 64}
    envelope = {
        "format": "millrace-router-request/v2",
        "operation": "resume_plane",
        "instance_id": "1" * 32,
        "run_id": "2" * 32,
        "request_id": "3" * 32,
        "plane_id": "service",
        "expected_state_version": 9,
        "payload": {},
    }
    parent = {
        "instance_id": "1" * 32,
        "id": "2" * 32,
        "status": "paused",
        "state_version": 9,
        "run_gate": None,
        "planes": {"service": {"status": "paused"}},
        "controls": [],
    }
    item = {
        "task_id": task,
        "request_sha256": "d" * 64,
        "status": "held",
        "claim": {"owner": "original"},
    }
    objects = {
        "task.json": {"task_id": task, "request_sha256": "d" * 64},
        "parent.json": envelope,
    }
    lane = object.__new__(factory_lad.Lane)
    lane._pins = lambda: None
    lane.config = {"service_id": "4" * 32}
    lane.home = SimpleNamespace(
        factory_id="5" * 32,
        active=lambda: {"task_id": job},
        read_ref=lambda ref: objects[ref["path"]],
    )
    lane._task = lambda identifier: item
    lane.runtime = SimpleNamespace(
        call=lambda *a, **k: None,
        parent=lambda **k: ({}, parent),
        active=lambda: item,
        record={"maintenance": {}},
    )
    monkeypatch.setattr(factory_lad, "closed", lambda *a, **k: True)
    result = lane.parent_resume_intent(
        job, task_ref, request_ref, caller_deadline=time.monotonic() + 30, fresh=True
    )
    assert (
        result["request_ref"] == request_ref
        and result["state"] == "unsent"
        and result["receipt_ref"] is None
    )
    for change in [
        {"run_id": "f" * 32},
        {"expected_state_version": 8},
        {"plane_id": "foreground"},
        {"operation": "resume_run", "plane_id": None},
    ]:
        objects["parent.json"] = envelope | change
        with pytest.raises((factory_lad.fr.FactoryError, IntakeError)):
            lane.parent_resume_intent(
                job,
                task_ref,
                request_ref,
                caller_deadline=time.monotonic() + 30,
                fresh=True,
            )
    objects["parent.json"] = envelope
    lane.runtime.record["maintenance"]["parent:" + envelope["request_id"]] = {
        "scope": "parent",
        "call": {"envelope": copy.deepcopy(envelope), "reservation": "observed"},
    }
    with pytest.raises(factory_lad.fr.FactoryError, match="id_spent"):
        lane.parent_resume_intent(
            job,
            task_ref,
            request_ref,
            caller_deadline=time.monotonic() + 30,
            fresh=True,
        )
    parent.update(status="running", state_version=12)
    item["status"] = "succeeded"
    assert (
        lane.parent_resume_intent(
            job,
            task_ref,
            request_ref,
            caller_deadline=time.monotonic() + 30,
            fresh=False,
        )["request_ref"]
        == request_ref
    )

    # A sent parent GET is independent of the newly claimed child's journal.
    from persistent_lad_support import receipt

    from millrace_plus.local_intake import canonical
    from millrace_plus.subworkflow_records import SubworkflowError

    calls = []

    def missing_child(*args, **kwargs):
        raise SubworkflowError("call_missing")

    lane.runtime.call = missing_child
    lane.home.workspace = tmp_path
    lane.home.raw_ref = lambda ref: canonical(objects[ref["path"]])
    request = {"job_id": job, "task_ref": task_ref, "control_ref": request_ref}
    lane._request = lambda *args: request
    lane._result = lambda request, status, proof: (status, proof)
    applied = receipt(
        envelope, instance=envelope["instance_id"], run=envelope["run_id"]
    )
    deadline = time.monotonic() + 30

    def observe_only(path, **kwargs):
        calls.append((path.read_bytes(), kwargs["_deadline"]))
        return {"receipt": applied, "holder_joined": False}

    lane.runtime.parent_control = observe_only
    saved = lane.runtime.record["maintenance"]["parent:" + envelope["request_id"]]
    for reservation in ["unknown", "observed"]:
        saved["call"]["reservation"] = reservation
        item["status"] = "claimed"
        result, proof = lane.resume_parent(None, caller_deadline=deadline)
        assert result == "completed" and proof["result"]["holder_joined"] is False
    assert calls == [(canonical(envelope), deadline)] * 2
    assert saved["call"]["envelope"] == envelope
    # All cross-owner/identity guards remain before any owner API reentry.
    for mapping, key, wrong in [
        (item, "request_sha256", "9" * 64),
        (parent, "instance_id", "8" * 32),
        (saved, "scope", "foreign"),
        (saved["call"], "envelope", envelope | {"request_id": "7" * 32}),
    ]:
        original = copy.deepcopy(mapping[key])
        mapping[key] = wrong
        with pytest.raises(factory_lad.fr.FactoryError):
            lane.resume_parent(None, caller_deadline=deadline)
        mapping[key] = original
    assert len(calls) == 2
    # Fresh and UNSENT authority still requires the actual child journal.
    parent.update(status="paused", state_version=9)
    for unsent in [True, False]:
        saved["call"]["reservation"] = "unsent"
        if not unsent:
            del lane.runtime.record["maintenance"]["parent:" + envelope["request_id"]]
        with pytest.raises(SubworkflowError, match="call_missing"):
            lane.parent_resume_intent(
                job, task_ref, request_ref, caller_deadline=deadline, fresh=False
            )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "run_gate,plane_status,expected",
    [
        ({"reason": "operator"}, "ready", "resume_run"),
        (None, "paused", "resume_plane"),
        ({"reason": "operator"}, "paused", "factory_lad_parent_resume_ambiguous"),
        (None, "ready", "factory_lad_parent_resume_inadmissible"),
    ],
)
def test_automatic_resume_selects_once_from_actual_parent_and_replays_saved_intent(
    monkeypatch, run_gate, plane_status, expected
):
    import copy
    import time
    from types import SimpleNamespace

    from millrace_plus import factory_lad, subworkflow_v2
    from millrace_plus._factory_steps import Runtime
    from millrace_plus.factory_records import FactoryError

    objects = {}

    def retain(value):
        key = str(len(objects)) + ".json"
        objects[key] = copy.deepcopy(value)
        return {"path": key, "sha256": "a" * 64}

    parent = {
        "instance_id": "1" * 32,
        "id": "2" * 32,
        "status": "paused",
        "state_version": 12,
        "run_gate": run_gate,
        "planes": {"service": {"status": plane_status, "resume_target": "reconcile"}},
    }
    calls = []

    def observe(**kwargs):
        calls.append(True)
        return {}, parent

    monkeypatch.setattr(
        factory_lad,
        "Lane",
        lambda home: SimpleNamespace(runtime=SimpleNamespace(parent=observe)),
    )
    monkeypatch.setattr(subworkflow_v2, "closed", lambda *a, **k: True)
    step = object.__new__(Runtime)
    step.deadline = time.monotonic() + 30
    step.node = "resume_lad_parent"
    step.home = SimpleNamespace(
        factory_id="3" * 32,
        config={"development_lane": {"service_id": "4" * 32}},
        retain=retain,
        read_ref=lambda ref: objects[ref["path"]],
    )
    state = {"job_id": "5" * 32}
    facts = {"lad_task_ref": {"path": "task.json", "sha256": "b" * 64}}
    saved = []
    step._save = lambda state, facts, node: saved.append(facts) or state
    if expected.startswith("factory_"):
        with pytest.raises(FactoryError, match=expected):
            step._control_intent(state, facts, "resume_run")
        assert not objects and not saved
        return
    _, intent = step._control_intent(state, facts, "resume_run")
    envelope = objects[intent["request_ref"]["path"]]
    assert envelope["operation"] == expected
    assert envelope["plane_id"] == ("service" if expected == "resume_plane" else None)
    assert envelope["expected_state_version"] == 12
    # An already selected envelope remains exact after any possible outcome;
    # fresh parent state is never an authority to rewrite it.
    parent.update(status="running", state_version=99, run_gate={"changed": True})
    _, replay = step._control_intent(state, saved[0], "resume_run")
    assert replay == intent
    assert objects[replay["request_ref"]["path"]] == envelope
    assert calls == [True] and len(saved) == 1


def test_explicit_parent_resume_refuses_unclaimed_before_runtime_call():
    import time
    from types import SimpleNamespace

    from millrace_plus.factory_lad import Lane
    from millrace_plus.factory_records import FactoryError

    lane = object.__new__(Lane)
    lane._pins = lambda: None
    envelope = {
        "format": "millrace-router-request/v2",
        "request_id": "1" * 32,
        "operation": "resume_plane",
        "instance_id": "2" * 32,
        "run_id": "3" * 32,
        "plane_id": "service",
        "expected_state_version": 1,
        "payload": {},
    }
    lane.home = SimpleNamespace(
        active=lambda: {"task_id": "job"},
        read_ref=lambda ref: (
            envelope
            if ref == "request"
            else {"task_id": "task", "request_sha256": "sha"}
        ),
    )
    lane._task = lambda task: {
        "task_id": "task",
        "request_sha256": "sha",
        "claim": None,
        "status": "queued",
    }

    def forbidden(*args, **kwargs):
        pytest.fail("unclaimed explicit planning must not construct a child call")

    lane.runtime = SimpleNamespace(
        call=forbidden,
        record={"maintenance": {}},
        parent=lambda **k: (
            {},
            {"instance_id": envelope["instance_id"], "id": envelope["run_id"]},
        ),
    )
    with pytest.raises(FactoryError, match="factory_lad_task_unclaimed"):
        lane.parent_resume_intent(
            "job", "task", "request", caller_deadline=time.monotonic() + 30, fresh=True
        )


@pytest.mark.parametrize(
    "joined_after,window,deadline,sleeps",
    [
        (1, 12, 100, [5]),
        (None, 12, 100, [5, 5, 2]),
        (None, 12, 3, [3]),
    ],
)
def test_hold_reuses_observation_window_and_cadence(
    monkeypatch, joined_after, window, deadline, sleeps
):
    from types import SimpleNamespace

    from millrace_plus import factory_lad

    lane = object.__new__(factory_lad.Lane)
    clock = [0]
    waited = []
    observed = []
    monkeypatch.setattr(factory_lad.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        waited.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(factory_lad.time, "sleep", sleep)
    lane.home = SimpleNamespace(
        config={
            "timeouts": {"observation_windows": {"observe_lad": {"seconds": window}}}
        }
    )

    def retain(**kwargs):
        observed.append(clock[0])
        return {
            "retained": joined_after is not None and len(observed) > joined_after,
            "reason": "joining",
        }

    lane.retain_paused_parent = retain
    proof = lane.wait_paused_parent(caller_deadline=deadline)
    assert proof["retained"] is (joined_after is not None)
    assert waited == sleeps
    assert all(t < min(window, deadline) for t in observed)


def test_queued_automatic_resume_binds_actual_invocation_and_original_intent(
    monkeypatch, tmp_path
):
    import copy
    import hashlib
    import os
    import time
    from types import SimpleNamespace

    from millrace_plus import cicd_candidates, factory_lad
    from millrace_plus.factory_records import FactoryError
    from millrace_plus.local_intake import canonical

    lane = object.__new__(factory_lad.Lane)
    job, task = "a" * 32, "b" * 32
    task_ref = {"path": "task.json", "sha256": "c" * 64}
    control_ref = {"path": "request.json", "sha256": "d" * 64}
    envelope = {
        "format": "millrace-router-request/v2",
        "request_id": "1" * 32,
        "operation": "resume_plane",
        "instance_id": "2" * 32,
        "run_id": "3" * 32,
        "plane_id": "service",
        "expected_state_version": 12,
        "payload": {},
    }
    intent = {
        "format": "millrace-plus-factory-lad-parent-resume/v1",
        "factory_id": "4" * 32,
        "job_id": job,
        "service_id": "5" * 32,
        "task_ref": task_ref,
        "parent_instance_id": envelope["instance_id"],
        "parent_run_id": envelope["run_id"],
        "request_ref": control_ref,
        "request_sha256": hashlib.sha256(canonical(envelope)).hexdigest(),
        "state": "unsent",
        "receipt_ref": None,
        "join_refs": [],
    }
    objects = {
        "intent": intent,
        "task.json": {"task_id": task, "request_sha256": "e" * 64},
        "request.json": envelope,
    }
    raw = {"input": b"original request", "rendered": b"selected LAD request"}
    facts = {
        "parent_resume_ref": {"path": "intent", "sha256": "f" * 64},
        "lad_task_ref": task_ref,
        "lad_task_id": task,
        "lad_rendered_ref": "rendered",
    }
    state = {"phase": "resume_lad_parent", "input_ref": "input"}
    item = {
        "task_id": task,
        "request_sha256": "e" * 64,
        "claim": None,
        "status": "queued",
        "request_bytes": raw["rendered"],
    }
    original = {"request_bytes": raw["input"]}
    active = {"task_id": job}
    lane.home = SimpleNamespace(
        home=tmp_path,
        factory_id=intent["factory_id"],
        active=lambda: active,
        state=lambda selected: state,
        facts=lambda selected: facts,
        read_ref=lambda ref: objects[ref["path"]],
        raw_ref=lambda ref: (
            canonical(objects[ref["path"]]) if isinstance(ref, dict) else raw[ref]
        ),
        intake=SimpleNamespace(tasks=lambda selected: [original]),
    )
    lane.config = {"service_id": intent["service_id"]}
    lane._pins = lambda: None
    lane._task = lambda selected: item
    parent = {
        "instance_id": envelope["instance_id"],
        "id": envelope["run_id"],
        "state_version": 12,
        "status": "paused",
        "run_gate": None,
        "planes": {"service": {"status": "paused", "resume_target": "reconcile"}},
    }
    lane.runtime = SimpleNamespace(
        record={"maintenance": {}}, parent=lambda **k: ({}, parent)
    )
    bound = []
    monkeypatch.setattr(cicd_candidates, "bind_invocation", lambda *a: bound.append(a))
    monkeypatch.setattr(factory_lad, "closed", lambda *a, **k: True)
    request = {"job_id": job, "task_ref": task_ref, "control_ref": control_ref}
    deadline = time.monotonic() + 30
    script = tmp_path / "script.py"
    lane._automatic_queued_resume(request, script, caller_deadline=deadline)
    assert bound == [
        (
            tmp_path,
            "factory",
            "resume_lad_parent",
            script,
            os.getpid(),
            factory_lad.Path.cwd(),
        )
    ]
    for mapping, key, wrong in [
        (active, "task_id", "wrong"),
        (state, "phase", "hold"),
        (original, "request_bytes", b"other"),
        (item, "request_bytes", b"other"),
        (intent, "service_id", "6" * 32),
        (intent, "request_ref", task_ref),
    ]:
        before = copy.deepcopy(mapping[key])
        mapping[key] = wrong
        with pytest.raises(FactoryError, match="automatic_resume_mismatch"):
            lane._automatic_queued_resume(request, script, caller_deadline=deadline)
        mapping[key] = before

    def no_binding(*args):
        raise FactoryError("actual_invocation_mismatch")

    monkeypatch.setattr(cicd_candidates, "bind_invocation", no_binding)
    with pytest.raises(FactoryError, match="actual_invocation_mismatch"):
        lane._automatic_queued_resume(request, script, caller_deadline=deadline)
    monkeypatch.setattr(cicd_candidates, "bind_invocation", lambda *a: None)
    # A possibly sent row replays its exact envelope despite changed parent state.
    lane.runtime.record["maintenance"]["parent:" + envelope["request_id"]] = {
        "scope": "parent",
        "request_raw": canonical(envelope).decode(),
        "call": {"envelope": copy.deepcopy(envelope), "reservation": "unknown"},
    }
    parent.update(status="running", state_version=99)
    lane._automatic_queued_resume(request, script, caller_deadline=deadline)
    assert objects["request.json"] == envelope
    saved = lane.runtime.record["maintenance"]["parent:" + envelope["request_id"]]
    saved["call"]["envelope"] = envelope | {"run_id": "7" * 32}
    for reservation in ["unsent", "unknown", "observed"]:
        saved["call"]["reservation"] = reservation
        with pytest.raises(FactoryError, match="id_spent"):
            lane._automatic_queued_resume(request, script, caller_deadline=deadline)

    # SENT graph replays validate actor and immutable binding before mutable
    # task admission. The owner may claim/finish during binder public reads.
    from persistent_lad_support import receipt

    saved["call"]["envelope"] = copy.deepcopy(envelope)
    saved["request_raw"] = canonical(envelope).decode()
    lane.home.workspace = tmp_path
    lane._request = lambda *args: request
    lane._result = lambda request, status, proof: (status, proof)
    owner_gets = []
    applied = receipt(
        envelope, instance=envelope["instance_id"], run=envelope["run_id"]
    )

    def get_original(path, **kwargs):
        assert saved["call"]["reservation"] in {"unknown", "observed"}
        owner_gets.append((path.read_bytes(), kwargs["_deadline"]))
        return {"receipt": applied, "holder_joined": False}

    lane.runtime.parent_control = get_original

    def no_child_or_slot(*args, **kwargs):
        pytest.fail(
            "sent parent observation must not require a child journal/active slot"
        )

    lane.runtime.call = lane.runtime.active = no_child_or_slot
    for reservation in ["unknown", "observed"]:
        saved["call"]["reservation"] = reservation
        for status in ["claimed", "running", "succeeded", "failed"]:
            item.update(status="queued", claim=None)

            def bind_then_progress(*args):
                item.update(status=status, claim={"original": True})

            monkeypatch.setattr(cicd_candidates, "bind_invocation", bind_then_progress)
            result, proof = lane.resume_parent(
                None, caller_deadline=deadline, script=script
            )
            assert result == "completed" and proof["result"]["holder_joined"] is False
    assert owner_gets == [(canonical(envelope), deadline)] * 8
    for mapping, key, wrong in [
        (saved, "scope", "foreign"),
        (saved, "request_raw", "different raw"),
        (saved["call"], "envelope", envelope | {"run_id": "8" * 32}),
        (original, "request_bytes", b"foreign request"),
        (item, "request_bytes", b"foreign rendered request"),
        (intent, "service_id", "7" * 32),
    ]:
        before = copy.deepcopy(mapping[key])
        mapping[key] = wrong
        with pytest.raises(FactoryError):
            lane.resume_parent(None, caller_deadline=deadline, script=script)
        mapping[key] = before
    monkeypatch.setattr(cicd_candidates, "bind_invocation", no_binding)
    with pytest.raises(FactoryError, match="actual_invocation_mismatch"):
        lane.resume_parent(None, caller_deadline=deadline, script=script)
    monkeypatch.setattr(cicd_candidates, "bind_invocation", lambda *args: None)
    saved["call"]["reservation"] = "invalid"
    with pytest.raises(FactoryError, match="parent_control_state_invalid"):
        lane.resume_parent(None, caller_deadline=deadline, script=script)
    saved["call"]["reservation"] = "unsent"
    item.update(status="claimed", claim={"original": True})
    with pytest.raises(FactoryError, match="automatic_resume_mismatch"):
        lane._automatic_queued_resume(request, script, caller_deadline=deadline)
    assert len(owner_gets) == 8


def test_resume_pending_busy_replay_and_applied_keep_exact_frozen_envelope(tmp_path):
    import time
    from types import SimpleNamespace

    from persistent_lad_support import receipt

    from millrace_plus.factory_lad import Lane
    from millrace_plus.local_intake import IntakeError, canonical

    envelope = {
        "format": "millrace-router-request/v2",
        "request_id": "1" * 32,
        "operation": "resume_plane",
        "instance_id": "2" * 32,
        "run_id": "3" * 32,
        "plane_id": "service",
        "expected_state_version": 45,
        "payload": {},
    }
    raw = canonical(envelope)
    control_ref = {"path": "control.json", "sha256": "a" * 64}
    task_ref = {"path": "task.json", "sha256": "b" * 64}
    request = {"control_ref": control_ref, "task_ref": task_ref, "job_id": "4" * 32}
    lane = object.__new__(Lane)
    lane.home = SimpleNamespace(
        workspace=tmp_path,
        raw_ref=lambda ref: raw,
        read_ref=lambda ref: envelope if ref == control_ref else {"task_id": "task"},
    )
    lane._request = lambda *args: request
    lane._task = lambda *args: {"claim": {"original": True}}
    lane.parent_resume_intent = lambda *a, **k: None
    lane._result = lambda request, status, proof: (status, proof)
    deadline = time.monotonic() + 30
    calls = []
    applied = receipt(
        envelope, instance=envelope["instance_id"], run=envelope["run_id"]
    )
    replies = [None, IntakeError("target_busy"), applied]

    def owner(path, **kwargs):
        calls.append((path.read_bytes(), kwargs["_deadline"]))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply  # Existing nonblocking owner lock refused before send.
        return {"receipt": reply, "holder_joined": False, "status": "pending"}

    lane.runtime = SimpleNamespace(parent_control=owner)
    assert lane.resume_parent(None, caller_deadline=deadline)[0] == "pending"
    busy = lane.resume_parent(None, caller_deadline=deadline)
    assert busy == (
        "pending",
        {"control_ref": control_ref, "reason": "factory_lad_parent_busy"},
    )
    assert "receipt" not in busy[1] and "holder_joined" not in busy[1]
    status, proof = lane.resume_parent(None, caller_deadline=deadline)
    assert status == "completed" and proof["result"]["receipt"] == applied
    assert proof["result"]["holder_joined"] is False  # Applied is not joined.
    assert calls == [(raw, deadline)] * 3
    assert (
        tmp_path / "controls" / (envelope["request_id"] + ".json")
    ).read_bytes() == raw

    # Terminal refusal is held; permanent owner errors are never made pending.
    lane.runtime.parent_control = lambda *a, **k: {
        "receipt": applied | {"status": "refused"}
    }
    assert lane.resume_parent(None, caller_deadline=deadline)[0] == "held"
    for code in [
        "wrong_parent_control_target",
        "parent_control_conflict",
        "stale_state_version",
        "lifecycle_authority_changed",
        "parent_control_id_already_used",
        "invalid_os_capabilities",
    ]:

        def refused(*args, **kwargs):
            raise IntakeError(code)

        lane.runtime.parent_control = refused
        with pytest.raises(IntakeError, match=code):
            lane.resume_parent(None, caller_deadline=deadline)


def test_graph_parent_cas_pending_reopens_fresh_lane_and_preserves_original_call(
    monkeypatch, tmp_path
):
    import copy
    import hashlib
    from types import SimpleNamespace

    from persistent_lad_support import receipt

    from millrace_plus import factory_lad
    from millrace_plus._factory_steps import Runtime
    from millrace_plus.local_intake import IntakeError, canonical

    envelope = {
        "format": "millrace-router-request/v2",
        "request_id": "1" * 32,
        "operation": "resume_plane",
        "instance_id": "2" * 32,
        "run_id": "3" * 32,
        "plane_id": "service",
        "expected_state_version": 45,
        "payload": {},
    }
    raw = canonical(envelope)
    objects = {}

    def retain(value):
        digest = hashlib.sha256(canonical(value)).hexdigest()
        ref = {"path": digest + ".json", "sha256": digest}
        objects[ref["path"]] = copy.deepcopy(value)
        return ref

    control_ref = retain(envelope)
    task_ref = retain({"task_id": "4" * 32})
    request = {
        "request_id": "5" * 32,
        "operation": "resume_parent",
        "job_id": "6" * 32,
        "task_ref": task_ref,
        "control_ref": control_ref,
    }
    home = SimpleNamespace(
        workspace=tmp_path,
        retain=retain,
        config={"development_lane": {"service_home": str(tmp_path / "service")}},
        raw_ref=lambda ref: canonical(objects[ref["path"]]),
        read_ref=lambda ref: objects[ref["path"]],
        facts=lambda state: {},
    )
    durable = {
        "envelope": copy.deepcopy(envelope),
        "observations": [None, False],
        "maintenance": {
            "parent:" + envelope["request_id"]: {
                "scope": "parent",
                "request_raw": raw.decode(),
                "call": {
                    "envelope": copy.deepcopy(envelope),
                    "reservation": "observed",
                },
            }
        },
    }
    snapshots, calls = [], []
    applied = receipt(
        envelope, instance=envelope["instance_id"], run=envelope["run_id"]
    )

    class Owner:
        def __init__(self, service):
            self.record = copy.deepcopy(durable)
            snapshots.append(self.record)

        def parent_control(self, path, **kwargs):
            calls.append((path.read_bytes(), kwargs["_deadline"]))
            if len(calls) == 1:
                return {"receipt": None, "holder_joined": False}
            if len(calls) == 2:
                durable["concurrent"] = True
                self.record["losing_positive"] = True
                raise IntakeError("lifecycle_conflict")
            assert self.record.get("concurrent") is True
            assert "losing_positive" not in self.record
            assert self.record["observations"] == [None, False]
            return {"receipt": applied, "holder_joined": True}

    monkeypatch.setattr(factory_lad, "Service", lambda path: object())
    monkeypatch.setattr(factory_lad, "Runtime", Owner)
    monkeypatch.setattr(factory_lad.Lane, "_pins", lambda self: None)
    monkeypatch.setattr(
        factory_lad.Lane, "_automatic_queued_resume", lambda *a, **k: None
    )
    monkeypatch.setattr(factory_lad.Lane, "_request", lambda *a: request)
    monkeypatch.setattr(
        factory_lad.Lane, "_task", lambda *a: {"claim": {"original": True}}
    )
    monkeypatch.setattr(factory_lad.Lane, "parent_resume_intent", lambda *a, **k: None)
    clock = [0]
    monkeypatch.setattr(factory_lad.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        factory_lad.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    step = object.__new__(Runtime)
    step.home, step.node, step.script, step.deadline = (
        home,
        "resume_lad_parent",
        tmp_path / "script",
        30,
    )
    step._control_intent = lambda *a: ({}, {"request_ref": control_ref})
    step._lad_request = lambda *a, **k: tmp_path / "request.json"
    step._transition = lambda state, facts, outcome: (outcome, facts)
    outcome, facts = step._resume_lad_parent({}, {})
    assert outcome == "APPLIED" and len(snapshots) == 3
    assert calls == [(raw, 30)] * 3
    assert durable["envelope"] == envelope and durable["observations"] == [None, False]
    assert durable["concurrent"] is True and "losing_positive" not in durable
    pending = [
        v
        for v in objects.values()
        if v.get("reason") == "factory_lad_parent_observation_race"
    ]
    assert pending == [
        {"control_ref": control_ref, "reason": "factory_lad_parent_observation_race"}
    ]
    assert objects[facts["parent_resume_result_ref"]["path"]]["status"] == "completed"
