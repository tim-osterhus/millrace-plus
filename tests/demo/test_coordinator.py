"""Exact public intent, immutable capture, and lifecycle composition."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from millrace_plus.demo import coordinator, publication, retention
from millrace_plus.demo.contracts import DemoError, canonical_bytes
from millrace_plus.demo.estimate import build_baseline
from millrace_plus.demo.hardware import unknown_hardware
from millrace_plus.demo.render import build_report
from millrace_plus.subworkflow_records import public_request_digest


@pytest.fixture
def owner(tmp_path):
    result = coordinator.Coordinator(
        coordinator.DemoOptions(output=tmp_path / "results"), {}
    )
    result.root, result.owner = retention.create_root()
    result.instance, result.run_id = "a" * 32, "b" * 32
    yield result
    if result.root.exists():
        retention.cleanup_root(
            result.root,
            result.owner,
            writers_stopped=True,
            child_exited=True,
            workers_drained=True,
            service_stopped=True,
            exports_verified=True,
        )


def observation(request, *, joined=True):
    return {
        "request_id": request["request_id"],
        "instance_id": request["instance_id"],
        "run_id": request["run_id"],
        "source": "os_owned_call",
        "phase": "call_returned",
        "holder_joined": joined,
        "core_receipt": {"status": "applied"},
    }


def saved_request(owner, operation, request_id, *, plane_id=None):
    return {
        "format": "millrace-router-request/v2",
        "operation": operation,
        "instance_id": owner.instance,
        "run_id": owner.run_id,
        "plane_id": plane_id,
        "request_id": request_id,
        "expected_state_version": None,
        "payload": {},
    }


def complete_observation(request, *, joined=True):
    result = observation(request, joined=joined)
    digest = public_request_digest(request)
    result.update(
        contract="millrace-os-router-call/v2",
        home="demo",
        request_digest=digest,
        observed_at="2026-11-11T13:00:00+00:00",
    )
    result["core_receipt"] = {
        "request_id": request["request_id"],
        "request_digest": digest,
        "operation": request["operation"],
        "instance_id": request["instance_id"],
        "run_id": request["run_id"],
        "plane_id": request["plane_id"],
        "status": "applied",
        "code": None,
    }
    return result


def closing_handle(owner, events):
    def finish(**kwargs):
        snapshot = json.loads((owner.root / "join-observations.json").read_bytes())
        events.append(("finish", snapshot))

    return SimpleNamespace(
        finish=finish,
        wait=lambda: events.append(("wait", None)) or True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: events.append(("close", None)),
    )


def test_request_saved_before_post_and_uncertainty_observes_same_id(owner):
    seen = []

    def submit(request, **kwargs):
        assert json.loads((owner.root / "requests.json").read_bytes()) == [request]
        seen.append(("POST", request))
        raise TimeoutError

    def observe(request, **kwargs):
        seen.append(("GET", request))
        return observation(request)

    owner.client = SimpleNamespace(submit=submit, observe=observe)
    owner._submit("cancel_run", {}, version=None)
    assert [method for method, _ in seen] == ["POST", "GET"]
    assert seen[0][1] is seen[1][1]
    assert seen[0][1]["expected_state_version"] is None


def test_native_timeout_disclosure_uses_public_effective_value(
    owner, monkeypatch, capsys
):
    from millrace_plus.router import control

    calls = []

    def validate(instance_id, *, state_home):
        calls.append((instance_id, state_home))
        return {
            "data": {
                "valid": True,
                "resolved_nodes": {
                    "main": {
                        "native": {
                            "common": {
                                "requested": {"timeout_seconds": 90},
                                "effective": {"timeout_seconds": 45},
                                "provenance": {
                                    "timeout_seconds": {
                                        "source": "runtime",
                                        "policy": {"kind": "ceiling", "value": 45},
                                    }
                                },
                            }
                        }
                    }
                },
            }
        }

    monkeypatch.setattr(control, "validate_revision_summary", validate)
    owner._disclose_native_timeout()
    assert calls == [(owner.instance, owner.state_home)]
    text = capsys.readouterr().out
    assert "45 seconds" in text and "source: runtime" in text
    assert "'kind': 'ceiling', 'value': 45" in text
    assert "90 seconds" not in text and "1800 seconds" not in text


def test_invalid_native_admission_does_not_disclose_an_effective_timeout(
    owner, monkeypatch, capsys
):
    from millrace_plus.router import control

    monkeypatch.setattr(
        control,
        "validate_revision_summary",
        lambda *a, **kw: {"data": {"valid": False}},
    )
    with pytest.raises(DemoError, match="native_validation_failed"):
        owner._disclose_native_timeout()
    assert capsys.readouterr().out == ""


def test_unknown_started_run_retains_state_after_child_drain(owner, monkeypatch):
    owner.run_id = None
    seen = []
    clock = [0.0]
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: clock[0])

    def submit(request, **kwargs):
        # The worker may have started a run before its response was lost.
        seen.append(("POST", request))
        raise TimeoutError

    def observe(request, **kwargs):
        seen.append(("GET", request))
        clock[0] += 16
        raise TimeoutError

    owner.client = SimpleNamespace(submit=submit, observe=observe)
    with pytest.raises(TimeoutError):
        owner._submit("start_run", {"edit_mode": "locked"}, version=1)
    owner.error = "TimeoutError"
    owner.handle = SimpleNamespace(
        finish=lambda **kw: None,
        wait=lambda: True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: None,
    )

    assert owner._finish() == 1
    assert owner.root.exists()
    assert [method for method, _ in seen] == ["POST", "GET", "GET"]
    assert all(request is seen[0][1] for _, request in seen)
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert record["writers_stopped"] is False
    assert record["child_exited"] and record["shutdown"]["workers_drained"]


def artifacts(node="baseline", action="pause"):
    identity = {
        "invocation_id": "c" * 32,
        "plane_id": "main",
        "node_id": node,
        "producing_revision_id": "d" * 64,
    }
    event = dict(
        identity,
        event_kind="transition",
        completion_id=identity["invocation_id"],
        action=canonical_bytes(
            {"pause": {"reason": "baseline_saved", "resume": "skip"}}
            if action == "pause"
            else {"finish": "succeeded"}
        ).decode(),
        event_id=identity["invocation_id"],
        target="skip" if action == "pause" else None,
        outcome="READY",
    )
    event.pop(
        "invocation_id"
    )  # Public events link through completion_id, not invocation_id.
    summary = {
        "status": "paused",
        "quiescent": True,
        "state_version": 17,
        "run_gate": None,
        "pause_reason": "no_runnable_planes",
        "planes": {
            "main": {
                "status": "paused",
                "pause_reason": "baseline_saved",
                "resume_target": "skip",
                "last_event": event,
            }
        },
    }
    invocation = dict(
        identity,
        writer={"effective": "stopped"},
        completion={"classification": "semantic", "outcome": "READY"},
    )
    return summary, [invocation], identity


def accepted(owner):
    return {
        "reference": {
            "instance_id": owner.instance,
            "run_id": owner.run_id,
            "output_id": "baseline",
            "record_sha256": "e" * 64,
        },
        "json_sha256": "f" * 64,
        "report_sha256": "1" * 64,
    }


def test_capture_publishes_and_saves_exact_session_before_resume(owner, monkeypatch):
    baseline = build_baseline(unknown_hardware())
    (owner.root / "workspace/baseline.json").write_bytes(canonical_bytes(baseline))
    summary, invocations, identity = artifacts()
    events = []
    monkeypatch.setattr(owner, "_summary", lambda: summary)
    monkeypatch.setattr(owner, "_invocations", lambda: invocations)
    monkeypatch.setattr(owner, "_check_stop", lambda: None)
    monkeypatch.setattr(
        owner,
        "_publish",
        lambda value, name, producer: (
            events.append((name, producer)) or accepted(owner)
        ),
    )

    def submit(operation, payload, *, version, plane_id=None):
        session = json.loads((owner.root / "demo-session.json").read_bytes())
        assert session["baseline"] == accepted(owner)
        assert session["invocation_id"] == identity["invocation_id"]
        assert "os_origin" not in session and "token" not in session
        events.append((operation, version, plane_id))

    monkeypatch.setattr(owner, "_submit", submit)
    owner._capture_gate()
    assert events == [("baseline", identity), ("resume_plane", 17, "main")]
    assert owner.baseline_bytes == canonical_bytes(baseline)


def test_ambiguous_or_live_producer_refuses():
    summary, invocations, _ = artifacts()
    with pytest.raises(DemoError, match="ambiguous"):
        publication.producer_at(
            summary, invocations * 2, node="baseline", action="pause"
        )
    invocations[0]["writer"]["effective"] = "unknown"
    with pytest.raises(DemoError, match="invalid_artifact"):
        publication.producer_at(summary, invocations, node="baseline", action="pause")


def test_changed_session_or_baseline_refuses_final(owner, monkeypatch):
    baseline = build_baseline(unknown_hardware())
    owner.baseline, owner.baseline_bytes = baseline, canonical_bytes(baseline)
    owner.session_bytes = b"{}"
    (owner.root / "demo-session.json").write_bytes(b'{"changed":true}')
    (owner.root / "workspace/baseline.json").write_bytes(owner.baseline_bytes)
    summary, invocations, _ = artifacts("render", "finish")
    monkeypatch.setattr(owner, "_summary", lambda: summary)
    monkeypatch.setattr(owner, "_invocations", lambda: invocations)
    with pytest.raises(DemoError, match="captured_input_changed"):
        owner._capture_final()


@pytest.mark.parametrize("stopped,expected", [(False, 1), (True, 130)])
@pytest.mark.parametrize("native_selected", [False, True])
def test_cancel_retains_baseline_and_requires_public_closure(
    owner, monkeypatch, stopped, expected, native_selected
):
    baseline = build_baseline(unknown_hardware())
    owner.baseline, owner.baseline_bytes = baseline, canonical_bytes(baseline)
    working = owner.root / "workspace/baseline.json"
    working.write_bytes(b"replaced")
    owner.cancelled, owner.stop_source = True, "terminal_enter"
    if native_selected:
        owner.native = {"binding": {"adapter": "claude_print"}}
        owner.options = coordinator.DemoOptions(
            output=owner.options.output, native=owner.native, native_consent=True
        )
    handle = SimpleNamespace(
        finish=lambda **kw: None,
        wait=lambda: True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: None,
    )
    owner.handle = handle
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {
            "status": "cancelled",
            "quiescent": stopped,
            "inflight_invocations": [],
        },
    )
    if not stopped:
        clock = iter((0.0, 16.0))
        monkeypatch.setattr(coordinator.time, "monotonic", lambda: next(clock))
    assert owner._finish() == expected
    report_path = next(owner.options.output.glob("demo-*/report.json"))
    report = json.loads(report_path.read_bytes())
    assert report["completion"] == "cancelled" and report["baseline"] == baseline
    assert report["native"]["status"] == (
        "skipped"
        if not native_selected
        else "cancelled"
        if stopped
        else "writer_unconfirmed"
    )
    record = json.loads(report_path.with_name("execution.json").read_bytes())
    assert record["completion"] == "cancelled"
    assert record["stop_source"] == "terminal_enter"
    assert record["writers_stopped"] is stopped
    assert record["native"]["status"] == report["native"]["status"]
    if not stopped:
        assert working.read_bytes() == b"replaced" and owner.root.exists()
    else:
        assert not owner.root.exists()
    html = report_path.with_suffix(".html").read_text()
    assert "Hardware" in html and "<script" not in html


def test_completed_finish_exports_and_cleans_only_after_shutdown(owner, monkeypatch):
    owner.baseline = build_baseline(unknown_hardware())
    owner.baseline_bytes = canonical_bytes(owner.baseline)
    owner.report = build_report("demo", owner.baseline)
    owner.stop_source = "finish_request"
    owner.handle = SimpleNamespace(
        finish=lambda **kw: None,
        wait=lambda: False,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: None,
    )
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "succeeded", "quiescent": True, "inflight_invocations": []},
    )
    assert owner._finish() == 1 and owner.root.exists()
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert record["stop_source"] == "finish_request" and not record["child_exited"]


def test_real_command_only_lifecycle(tmp_path):
    """Real owned service and public graph; no model, provider or native fake."""
    result = coordinator.run_demo(
        coordinator.DemoOptions(output=tmp_path, no_open=True), {}
    )
    assert result == 0
    execution = json.loads(next(tmp_path.glob("demo-*/execution.json")).read_bytes())
    assert execution["completion"] == "complete" and execution["writers_stopped"]
    assert execution["child_exited"] and execution["shutdown"]["workers_drained"]
    assert execution["shutdown"]["service_stopped"]
    assert [row["reference"]["output_id"] for row in execution["accepted_outputs"]] == [
        "report",
        "baseline",
    ]
    assert (
        next(tmp_path.glob("demo-*/report.html"))
        .read_text()
        .startswith("<!doctype html>")
    )


def test_execution_record_allowlists_public_facts_and_hashes(owner):
    import hashlib
    from importlib.resources import files

    from millrace_plus.demo.materialize import materialize

    workflow = materialize(
        owner.root / "workspace",
        native=None,
        no_web=False,
        session_file=owner.root / "demo-session.json",
    )
    owner._capture_inputs(workflow)
    request_id = "8" * 32
    owner.requests = [
        {
            "request_id": request_id,
            "operation": "create_instance",
            "expected_state_version": 0,
            "payload": {
                "os_credential_file": "/private/credential-locator",
                "token": "private-bearer",
            },
        }
    ]
    owner.calls = [
        {
            "request_id": request_id,
            "request_digest": "9" * 64,
            "holder_joined": True,
            "core_receipt": {"status": "applied", "stdout": "raw-native-log"},
            "token": "private-bearer",
        }
    ]
    _, rows, _ = artifacts()
    owner.node_observations = rows
    record = owner._execution_record(
        {"baseline.json": b"captured-bytes"},
        stopped=True,
        child_exited=True,
        shutdown={"workers_drained": True},
        cleanup="pending",
        exported_at=None,
    )
    encoded = canonical_bytes(record)
    for forbidden in (
        b"credential-locator",
        b"private-bearer",
        b"raw-native-log",
        b'"payload"',
        b'"calls"',
    ):
        assert forbidden not in encoded

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    assert record["digests"] == {
        "graph_sha256": sha(workflow.read_bytes()),
        "reference_sha256": sha(
            (workflow.parent / "references/shortlist.json").read_bytes()
        ),
        "estimator_sha256": sha(
            files("millrace_plus.demo").joinpath("estimate.py").read_bytes()
        ),
    }
    assert record["artifacts"]["baseline.json"] == sha(b"captured-bytes")
    assert record["nodes"][0]["writer"] == "stopped"
    assert record["nodes"][0]["phase"] is None
    assert record["native"] == {
        "adapter": None,
        "choice": "not_selected",
        "consent": False,
        "web_requested": False,
        "no_web": False,
        "admission_failed": False,
        "status": None,
        "phase": None,
    }
    assert record["timestamps"]["started_at"]
    assert record["timestamps"]["service_ready_at"] is None
    with pytest.raises(DemoError, match="invalid_execution_record"):
        coordinator._validate_execution(record | {"unknown": True})


def test_failed_start_exports_truthful_compact_record(owner, monkeypatch):
    def failed():
        owner.service_start_attempted = True
        raise OSError("/private/credential-locator private-bearer")

    monkeypatch.setattr(owner, "_start", failed)
    owner.run_id = None
    assert owner.execute() == 1
    path = next(owner.options.output.glob("demo-*/execution.json"))
    record = json.loads(path.read_bytes())
    assert record["completion"] == "incomplete" and record["error"] == "OSError"
    assert not record["service_start_observed"] and not record["child_exited"]
    assert record["shutdown"]["service_stopped"] is None
    assert record["retention"]["cleanup"] == "retained"
    assert record["digests"] == {
        "graph_sha256": None,
        "reference_sha256": None,
        "estimator_sha256": None,
    }
    assert b"credential-locator" not in path.read_bytes()
    assert owner.root.exists()


def test_postcleanup_annotation_failure_keeps_prior_durable_record(owner, monkeypatch):
    owner.baseline = build_baseline(unknown_hardware())
    owner.baseline_bytes = canonical_bytes(owner.baseline)
    owner.report = build_report("demo", owner.baseline)
    owner.handle = SimpleNamespace(
        finish=lambda **kw: None,
        wait=lambda: True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: None,
    )
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "succeeded", "quiescent": True, "inflight_invocations": []},
    )
    original = coordinator.write_private

    def fail_annotation(path, raw):
        if path.name == "execution.json" and path.parent.parent == owner.options.output:
            raise OSError("disk full after cleanup")
        return original(path, raw)

    monkeypatch.setattr(coordinator, "write_private", fail_annotation)
    assert owner._finish() == 1
    path = next(owner.options.output.glob("demo-*/execution.json"))
    record = json.loads(path.read_bytes())
    assert record["retention"]["cleanup"] == "pending"
    assert not owner.root.exists()
    assert owner.error == "execution_annotation_failed"
    assert path.with_name("report.html").is_file()


def test_actual_public_baseline_gate_projection_pins_exact_producer():
    from pathlib import Path

    snapshot = json.loads(
        (Path(__file__).parent / "fixtures/baseline-gate-inspection.json").read_bytes()
    )
    event = snapshot["summary"]["planes"]["main"]["last_event"]
    assert type(event["action"]) is str and "invocation_id" not in event
    assert publication.producer_at(
        snapshot["summary"],
        snapshot["invocations"]["items"],
        node="baseline",
        action="pause",
    ) == {
        "invocation_id": event["completion_id"],
        "plane_id": "main",
        "node_id": "baseline",
        "producing_revision_id": event["producing_revision_id"],
    }


@pytest.mark.parametrize(
    "change",
    [
        "bare_kind",
        "wrong_target",
        "duplicate_action",
        "wrong_plane",
        "wrong_occurrence",
        "wrong_revision",
    ],
)
def test_actual_gate_corrupt_identity_or_action_refuses(change):
    from pathlib import Path

    snapshot = json.loads(
        (Path(__file__).parent / "fixtures/baseline-gate-inspection.json").read_bytes()
    )
    event = snapshot["summary"]["planes"]["main"]["last_event"]
    if change == "bare_kind":
        event["action"] = "pause"
    elif change == "wrong_target":
        event["action"] = '{"pause":{"reason":"baseline_saved","resume":"native"}}'
    elif change == "duplicate_action":
        event["action"] = (
            '{"pause":{},"pause":{"reason":"baseline_saved","resume":"skip"}}'
        )
    elif change == "wrong_plane":
        event["plane_id"] = "other"
    elif change == "wrong_occurrence":
        event["event_id"] = "f" * 32
    else:
        snapshot["invocations"]["items"][0]["producing_revision_id"] = "f" * 64
    with pytest.raises(DemoError):
        publication.producer_at(
            snapshot["summary"],
            snapshot["invocations"]["items"],
            node="baseline",
            action="pause",
        )


def test_baseline_capture_refuses_a_competing_run_gate(owner, monkeypatch):
    summary, rows, _ = artifacts()
    summary["run_gate"] = {"reason": "operator_pause", "request_id": "f" * 32}
    monkeypatch.setattr(owner, "_summary", lambda: summary)
    monkeypatch.setattr(owner, "_invocations", lambda: rows)
    monkeypatch.setattr(owner, "_submit", lambda *a, **k: pytest.fail("no gate resume"))
    with pytest.raises(DemoError, match="unexpected_baseline_gate"):
        owner._capture_gate()


def test_plane_resume_envelope_keeps_exact_target_and_observed_version(owner):
    seen = []
    owner.client = SimpleNamespace(
        submit=lambda request, **kwargs: seen.append(request) or observation(request)
    )
    owner._submit("resume_plane", {}, version=12, plane_id="main")
    request = seen[0]
    assert request["operation"] == "resume_plane" and request["plane_id"] == "main"
    assert (
        request["instance_id"] == owner.instance and request["run_id"] == owner.run_id
    )
    assert request["expected_state_version"] == 12 and request["payload"] == {}
    assert json.loads((owner.root / "requests.json").read_bytes()) == [request]


def test_actual_run_resume_refusal_is_recorded_without_replacement(owner):
    from pathlib import Path

    observations = json.loads(
        (Path(__file__).parent / "fixtures/normal-run-resume-refusal.json").read_bytes()
    )
    refused = next(row for row in observations if row["status"] == "refused")
    assert refused["operation"] == "resume_run" and refused["code"] == "run_not_paused"
    seen = []

    def submit(request, **kwargs):
        seen.append(request)
        result = observation(request)
        result["core_receipt"] = {"status": refused["status"], "code": refused["code"]}
        result["holder_joined"] = refused["holder_joined"]
        return result

    owner.client = SimpleNamespace(submit=submit)
    result = owner._submit(
        refused["operation"], {}, version=refused["expected_state_version"]
    )
    with pytest.raises(DemoError, match="control_refused"):
        owner._await_join(result)
    assert len(seen) == len(owner.requests) == 1
    record = owner._execution_record(
        {},
        stopped=True,
        child_exited=True,
        shutdown={},
        cleanup="pending",
        exported_at=None,
    )
    assert record["requests"][0]["code"] == "run_not_paused"
    assert record["requests"][0]["plane_id"] is None


def test_active_continuation_join_rereads_the_old_gate_summary(owner, monkeypatch):
    owner.baseline = build_baseline(unknown_hardware())
    owner.baseline_bytes = canonical_bytes(owner.baseline)
    request = {
        "request_id": "f" * 32,
        "instance_id": owner.instance,
        "run_id": owner.run_id,
    }
    joined = observation(request)
    active = dict(joined, phase="call_active", holder_joined=False)
    reads = iter((active, joined, joined))
    summaries = iter(({"status": "paused"}, {"status": "succeeded"}))
    monkeypatch.setattr(owner, "_start", lambda: None)
    monkeypatch.setattr(owner, "_observe", lambda **kwargs: next(reads))
    monkeypatch.setattr(owner, "_summary", lambda: next(summaries))
    monkeypatch.setattr(owner, "_check_stop", lambda: None)
    monkeypatch.setattr(coordinator.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        owner, "_capture_gate", lambda: pytest.fail("do not recapture old gate")
    )

    def capture_final():
        owner.report = build_report(owner.owner["demo_id"], owner.baseline)

    monkeypatch.setattr(owner, "_capture_final", capture_final)
    monkeypatch.setattr(owner, "_finish", lambda: 0 if owner.report is not None else 1)
    assert owner.execute() == 0
    assert owner.error is None and owner.report["completion"] == "complete"


def test_cancel_and_writer_poll_share_the_original_cleanup_deadline(owner, monkeypatch):
    owner.cancelled = True
    owner.stop_source = "terminal_enter"
    summaries = iter(
        (
            {"status": "running", "quiescent": False, "inflight_invocations": ["c"]},
            {"status": "running", "quiescent": False, "inflight_invocations": ["c"]},
            {"status": "cancelled", "quiescent": True, "inflight_invocations": []},
        )
    )
    deadlines = []
    monkeypatch.setattr(owner, "_summary", lambda: next(summaries))
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(coordinator.time, "sleep", lambda _: None)

    def submit(operation, payload, *, version, deadline):
        assert operation == "cancel_run"
        deadlines.append(deadline)
        return {
            "source": "os_owned_call",
            "phase": "call_returned",
            "holder_joined": True,
            "core_receipt": {"status": "applied"},
        }

    monkeypatch.setattr(owner, "_submit", submit)
    owner.handle = SimpleNamespace(
        finish=lambda **kw: None,
        wait=lambda: True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: None,
    )
    assert owner._finish() == 130
    assert deadlines == [16.0]
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert record["writers_stopped"] and record["completion"] == "cancelled"


def test_finish_refreshes_all_saved_joins_before_original_service_shutdown(
    owner, monkeypatch
):
    events, gets, posts = [], [], []
    owner.options = replace(owner.options, keep_demo=True)
    owner.cancelled, owner.stop_source = True, "terminal_enter"
    earlier = saved_request(owner, "pause_plane", "1" * 32, plane_id="main")
    resume = saved_request(owner, "resume_plane", "2" * 32, plane_id="main")
    done = saved_request(owner, "pause_run", "3" * 32)
    owner.requests = [earlier, done, resume]
    owner.calls = [
        complete_observation(earlier, joined=False),
        complete_observation(done),
        complete_observation(resume, joined=False),
    ]
    owner.current = resume
    summaries = iter(
        [
            {"status": "running", "quiescent": False, "inflight_invocations": ["n"]},
            {"status": "cancelled", "quiescent": True, "inflight_invocations": []},
        ]
    )
    monkeypatch.setattr(owner, "_summary", lambda: next(summaries))
    monkeypatch.setattr(owner, "_invocations", lambda: [])

    def submit(request, *, deadline):
        posts.append(request)
        assert request["operation"] == "cancel_run"
        return complete_observation(request, joined=False)

    def observe(request, *, deadline):
        gets.append(request)
        assert request is next(
            row for row in owner.requests if row["request_id"] == request["request_id"]
        )
        events.append(("GET", request["request_id"]))
        return complete_observation(request)

    owner.client = SimpleNamespace(submit=submit, observe=observe)
    owner.handle = closing_handle(owner, events)
    assert owner._finish() == 130
    assert len(posts) == 1 and owner.current is posts[0]
    assert gets == [earlier, resume, posts[0]]
    assert len(owner.requests) == 4
    assert [name for name, _ in events] == [
        "GET",
        "GET",
        "GET",
        "finish",
        "wait",
        "close",
    ]
    snapshot = events[3][1]
    assert [row["request_id"] for row in snapshot["requests"]] == [
        request["request_id"] for request in owner.requests
    ]
    assert all(
        row["observation"]["source"] == "os_owned_call"
        and row["observation"]["phase"] == "call_returned"
        and row["observation"]["holder_joined"] is True
        for row in snapshot["requests"]
    )
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert all(row["holder_joined"] is True for row in record["requests"])
    assert owner.root.exists() and owner.error is None


@pytest.mark.parametrize(
    "negative",
    [
        "wrong_target",
        "wrong_digest",
        "wrong_request",
        "wrong_receipt",
        "missing_receipt",
        "queued_receipt",
        "core_fallback",
        "active",
        "failed",
        "false_join",
        "null_join",
        "unavailable",
        "cached_mismatch",
    ],
)
def test_finish_unconfirmed_join_retains_state_and_truth(owner, monkeypatch, negative):
    events, gets = [], []
    clock = [0.0]
    owner.cancelled, owner.stop_source = True, "terminal_enter"
    request = saved_request(owner, "resume_plane", "4" * 32, plane_id="main")
    owner.requests = [request]
    row = complete_observation(request)
    if negative in {"wrong_target", "cached_mismatch"}:
        row["instance_id"] = "d" * 32
    elif negative == "wrong_digest":
        row["request_digest"] = "0" * 64
    elif negative == "wrong_request":
        row["request_id"] = "5" * 32
    elif negative == "wrong_receipt":
        row["core_receipt"]["plane_id"] = "other"
    elif negative == "missing_receipt":
        row["core_receipt"] = None
    elif negative == "queued_receipt":
        row["core_receipt"]["status"] = "queued"
    elif negative == "core_fallback":
        row.update(source="core_receipt", phase="receipt_observed", holder_joined=None)
    elif negative in {"active", "failed"}:
        row.update(phase="call_" + negative, holder_joined=False)
    elif negative == "false_join":
        row["holder_joined"] = False
    elif negative == "null_join":
        row["holder_joined"] = None
    if negative == "cached_mismatch":
        owner.calls = [row]

    def observe(envelope, *, deadline):
        gets.append(envelope)
        if negative == "unavailable":
            raise TimeoutError("private transport detail")
        return row

    owner.client = SimpleNamespace(observe=observe)
    owner.handle = closing_handle(owner, events)
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(coordinator.time, "sleep", lambda _: clock.__setitem__(0, 16.0))
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "cancelled", "quiescent": True, "inflight_invocations": []},
    )
    monkeypatch.setattr(owner, "_invocations", lambda: [])
    assert owner._finish() == 1
    assert owner.root.exists() and owner.instance == request["instance_id"]
    assert all(envelope is request for envelope in gets)
    assert [name for name, _ in events] == ["finish", "wait", "close"]
    snapshot = events[0][1]
    observed = snapshot["requests"][0]["observation"]
    assert observed is None or not (
        observed["source"] == "os_owned_call"
        and observed["phase"] == "call_returned"
        and observed["holder_joined"] is True
        and (observed["receipt"] or {}).get("status") == "applied"
    )
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert record["writers_stopped"] is True
    assert record["retention"]["reason"] == "closure_unconfirmed"
    assert record["error"]
    assert b"private transport detail" not in canonical_bytes(record)


def test_finish_saved_joins_use_one_deadline_and_refuse_late_positive(
    owner, monkeypatch
):
    clock, deadlines, events = [0.0], [], []
    owner.requests = [
        saved_request(owner, "pause_run", "6" * 32),
        saved_request(owner, "resume_plane", "7" * 32, plane_id="main"),
    ]

    def observe(request, *, deadline):
        deadlines.append(deadline)
        clock[0] += 8
        return complete_observation(request)

    owner.client = SimpleNamespace(observe=observe)
    owner.handle = closing_handle(owner, events)
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "cancelled", "quiescent": True, "inflight_invocations": []},
    )
    monkeypatch.setattr(owner, "_invocations", lambda: [])
    assert owner._finish() == 1
    assert deadlines == [15.0, 15.0]
    rows = events[0][1]["requests"]
    assert rows[0]["observation"]["holder_joined"] is True
    assert rows[1]["observation"] is None
    assert owner.root.exists() and owner.error == "control_join_not_observed"


def test_finish_invalid_then_valid_join_keeps_failure_and_root(owner, monkeypatch):
    clock, events, gets = [0.0], [], []
    owner.cancelled = True
    request = saved_request(owner, "resume_plane", "a" * 32, plane_id="main")
    owner.requests = [request]

    def observe(envelope, *, deadline):
        gets.append(envelope)
        row = complete_observation(envelope)
        if len(gets) == 1:
            row["instance_id"] = "d" * 32
        return row

    owner.client = SimpleNamespace(observe=observe)
    owner.handle = closing_handle(owner, events)
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(coordinator.time, "sleep", lambda _: clock.__setitem__(0, 0.2))
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "cancelled", "quiescent": True, "inflight_invocations": []},
    )
    monkeypatch.setattr(owner, "_invocations", lambda: [])
    assert owner._finish() == 1
    assert gets == [request, request] and owner.root.exists()
    assert owner.error == "wrong_os_target"
    assert events[0][1]["requests"][0]["observation"]["holder_joined"] is True
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert record["writers_stopped"] and record["requests"][0]["holder_joined"]
    assert record["error"] == "wrong_os_target"
    assert record["retention"] == {
        "keep_demo": False,
        "cleanup": "retained",
        "reason": "closure_unconfirmed",
    }


def test_finish_late_writer_summary_keeps_fact_without_closeout_credit(
    owner, monkeypatch
):
    clock, events, reads = [0.0], [], []
    owner.cancelled = True
    request = saved_request(owner, "resume_plane", "b" * 32, plane_id="main")
    owner.requests, owner.calls = [request], [complete_observation(request)]

    def summary():
        reads.append(clock[0])
        if len(reads) == 2:
            clock[0] = 16.0
        return {"status": "cancelled", "quiescent": True, "inflight_invocations": []}

    owner.handle = closing_handle(owner, events)
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(owner, "_summary", summary)
    monkeypatch.setattr(owner, "_invocations", lambda: [])
    assert owner._finish() == 1
    assert len(reads) == 2 and owner.root.exists()
    assert owner.error == "cleanup_observation_expired"
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert record["writers_stopped"] and record["requests"][0]["holder_joined"]
    assert record["error"] == "cleanup_observation_expired"
    assert record["retention"]["reason"] == "closure_unconfirmed"


def test_finish_join_snapshot_is_whitelisted_and_precedes_shutdown(owner, monkeypatch):
    owner.options = replace(owner.options, keep_demo=True)
    request = saved_request(owner, "resume_plane", "8" * 32, plane_id="main")
    request["payload"] = {"args": ["private-argument"], "config": "private-config"}
    call = complete_observation(request)
    call["core_receipt"]["headers"] = {"Authorization": "private-credential"}
    owner.requests, owner.calls = [request], [call]
    events = []
    owner.handle = closing_handle(owner, events)
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "succeeded", "quiescent": True, "inflight_invocations": []},
    )
    monkeypatch.setattr(owner, "_invocations", lambda: [])
    owner.report = build_report(
        owner.owner["demo_id"], build_baseline(unknown_hardware())
    )
    assert owner._finish() == 0
    raw = (owner.root / "join-observations.json").read_bytes()
    snapshot = json.loads(raw)
    assert snapshot == events[0][1]
    row = snapshot["requests"][0]
    assert row.keys() == {
        "request_id",
        "request_digest",
        "operation",
        "target",
        "observation",
    }
    assert row["request_digest"] == public_request_digest(request)
    assert row["target"] == {
        "instance_id": owner.instance,
        "run_id": owner.run_id,
        "plane_id": "main",
    }
    assert row["observation"].keys() == {
        "instance_id",
        "run_id",
        "source",
        "phase",
        "holder_joined",
        "receipt",
    }
    assert row["observation"]["receipt"].keys() == {
        "request_id",
        "request_digest",
        "operation",
        "instance_id",
        "run_id",
        "plane_id",
        "status",
        "code",
    }
    assert not any(
        secret in raw
        for secret in (b"private-argument", b"private-config", b"private-credential")
    )
    assert (owner.root / "join-observations.json").stat().st_mode & 0o777 == 0o600


def test_finish_join_snapshot_write_failure_retains_state(owner, monkeypatch):
    events = []
    owner.cancelled = True
    request = saved_request(owner, "resume_plane", "9" * 32, plane_id="main")
    owner.requests, owner.calls = [request], [complete_observation(request)]
    write = coordinator.write_private

    def failing_write(path, raw):
        if path.name == "join-observations.json":
            raise OSError("private disk detail")
        return write(path, raw)

    owner.handle = SimpleNamespace(
        finish=lambda **kw: events.append("finish"),
        wait=lambda: events.append("wait") or True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: events.append("close"),
    )
    monkeypatch.setattr(coordinator, "write_private", failing_write)
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "cancelled", "quiescent": True, "inflight_invocations": []},
    )
    monkeypatch.setattr(owner, "_invocations", lambda: [])
    assert owner._finish() == 1
    assert events == ["finish", "wait", "close"] and owner.root.exists()
    assert not (owner.root / "join-observations.json").exists()
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert record["writers_stopped"] and record["requests"][0]["holder_joined"]
    assert record["error"] == "OSError"
    assert record["retention"]["reason"] == "closure_unconfirmed"


def test_finish_joined_cancel_refusal_is_not_success(owner, monkeypatch):
    events = []

    def submit(request, *, deadline):
        row = complete_observation(request)
        row["core_receipt"].update(status="refused", code="cancel_guard_refused")
        return row

    owner.client = SimpleNamespace(submit=submit)
    owner.handle = closing_handle(owner, events)
    monkeypatch.setattr(owner, "_summary", lambda: {"status": "running"})
    monkeypatch.setattr(owner, "_invocations", lambda: [])
    assert owner._finish() == 1
    assert owner.error == "cancel_refused" and owner.root.exists()
    row = events[0][1]["requests"][0]["observation"]
    assert row["holder_joined"] is True
    assert row["receipt"]["status"] == "refused"


@pytest.mark.parametrize("failure", ["summary", "cancel"])
def test_public_cleanup_failure_still_observes_owned_child_shutdown(
    owner, monkeypatch, failure
):
    events = []
    owner.handle = SimpleNamespace(
        finish=lambda **kw: events.append("finish"),
        wait=lambda: events.append("wait") or True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: events.append("close"),
    )
    if failure == "summary":
        monkeypatch.setattr(
            owner, "_summary", lambda: (_ for _ in ()).throw(DemoError("bad_read"))
        )
    else:
        monkeypatch.setattr(owner, "_summary", lambda: {"status": "running"})
        monkeypatch.setattr(
            owner, "_submit", lambda *a, **kw: (_ for _ in ()).throw(OSError())
        )
    assert owner._finish() == 1
    assert events == ["finish", "wait", "close"]
    assert owner.root.exists()
    record = json.loads(
        next(owner.options.output.glob("demo-*/execution.json")).read_bytes()
    )
    assert not record["writers_stopped"] and record["child_exited"]


@pytest.mark.parametrize(
    "stage,status", [("baseline", "paused"), ("report", "succeeded")]
)
def test_capture_failure_publishes_only_safe_error_and_retains_pins(
    owner, monkeypatch, stage, status
):
    pin = accepted(owner)
    owner.accepted = [pin]
    updates = []
    owner.handle = SimpleNamespace(
        update_outputs=lambda refs, **kw: updates.append((refs, kw))
    )
    monkeypatch.setattr(owner, "_start", lambda: None)
    monkeypatch.setattr(owner, "_check_stop", lambda: None)
    monkeypatch.setattr(
        owner,
        "_observe",
        lambda: {
            "source": "os_owned_call",
            "phase": "call_returned",
            "holder_joined": True,
            "core_receipt": {"status": "applied"},
        },
    )
    monkeypatch.setattr(owner, "_summary", lambda: {"status": status})
    monkeypatch.setattr(owner, "_finish", lambda: 1)
    target = "_capture_gate" if stage == "baseline" else "_capture_final"
    monkeypatch.setattr(
        owner, target, lambda: (_ for _ in ()).throw(DemoError("bad_capture"))
    )
    assert owner.execute() == 1
    assert updates == [([pin], {"output_error": stage + "_capture_failed"})]


@pytest.mark.parametrize(
    "failure", [DemoError("invalid_execution_record"), OSError("private disk detail")]
)
def test_execution_construction_failure_is_operational_and_retains_state(
    owner, monkeypatch, capsys, failure
):
    owner.baseline = build_baseline(unknown_hardware())
    owner.baseline_bytes = canonical_bytes(owner.baseline)
    owner.report = build_report(owner.owner["demo_id"], owner.baseline)
    events = []
    owner.handle = SimpleNamespace(
        finish=lambda **kw: events.append("finish"),
        wait=lambda: True,
        shutdown_result={"workers_drained": True, "service_stopped": True},
        close=lambda: None,
    )
    monkeypatch.setattr(owner, "_start", lambda: None)
    monkeypatch.setattr(owner, "_check_stop", lambda: None)
    monkeypatch.setattr(
        owner,
        "_observe",
        lambda: {
            "source": "os_owned_call",
            "phase": "call_returned",
            "holder_joined": True,
            "core_receipt": {"status": "applied"},
        },
    )
    monkeypatch.setattr(
        owner,
        "_summary",
        lambda: {"status": "succeeded", "quiescent": True, "inflight_invocations": []},
    )
    monkeypatch.setattr(owner, "_capture_final", lambda: None)
    monkeypatch.setattr(
        owner, "_execution_record", lambda *a, **kw: (_ for _ in ()).throw(failure)
    )
    assert owner.execute() == 1
    assert events == ["finish"] and owner.root.exists()
    assert "Demo state retained: " + str(owner.root) in capsys.readouterr().out
