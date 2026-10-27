"""Finite advance and crash-gap proofs with fake OS and bounded socket evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from persistent_lad_support import (
    CHILD,
    RUN,
    Clock,
    FakeClient,
    advance,
    capabilities,
    journal,
    observation,
    setup,
    write_request,
)

from millrace_plus import subworkflow_v2 as helper
from millrace_plus.subworkflow_records import SubworkflowError, canonical, mapping


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return setup(tmp_path, monkeypatch)


def test_real_socket_window_expiry_publishes_saved_running_evidence(fixture):
    release, entered = threading.Event(), threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            requests.append("OPTIONS")
            raw = canonical(capabilities())
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            requests.append("GET")
            entered.set()
            release.wait(15)

        def do_POST(self):
            requests.append("POST")
            self.send_error(500)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        profile = fixture["profile"]
        data = json.loads(profile.read_bytes())
        data["os_origin"] = f"http://127.0.0.1:{server.server_port}"
        profile.write_bytes(canonical(data))
        fixture["request"]["os_profile"]["sha256"] = hashlib.sha256(
            profile.read_bytes()
        ).hexdigest()
        write_request(fixture)
        seed = FakeClient(fixture)
        assert advance(fixture, seed)["outcome"] == "CHILD_RUNNING"
        before = journal(fixture).load()
        result = helper.advance(
            fixture["file"],
            expected_home="local",
            claim_sha256="a" * 64,
            cwd=fixture["parent"],
            clock=time.monotonic,
        )
        assert entered.is_set()
        assert requests == ["OPTIONS", "GET"]
        assert result["outcome"] == "CHILD_RUNNING"
        saved = journal(fixture).load()
        assert saved["phase"] == "observing" and saved["diagnostic"] is None
        assert saved["observation"] == before["observation"]
        assert saved["os_observations"] == before["os_observations"]
        assert saved["envelopes"] == before["envelopes"]
        manifest = json.loads((fixture["parent"] / "results/task_1.json").read_bytes())
        assert manifest["outcome"] == "CHILD_RUNNING"
    finally:
        release.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_multiwindow_running_never_cancels(fixture):
    client = FakeClient(fixture)
    clock = Clock()
    assert advance(fixture, client, clock)["outcome"] == "CHILD_RUNNING"
    original = journal(fixture).load()
    assert advance(fixture, client, clock)["outcome"] == "CHILD_RUNNING"
    saved = journal(fixture).load()
    assert (saved["instance_id"], saved["run_id"]) == (CHILD, RUN)
    assert saved["envelopes"] == original["envelopes"]
    assert [e["operation"] for e in client.sent] == ["create_instance", "start_run"]
    assert mapping(saved["envelopes"])["cancel"] is None
    assert clock() == 2
    result = json.loads((fixture["parent"] / "results/task_1.json").read_bytes())
    assert result["dispatch"] == "started"
    assert result["outcome"] == "CHILD_RUNNING"
    assert "controllers" not in result


@pytest.mark.parametrize(
    "status,outcome",
    [
        ("succeeded", "CHILD_SUCCEEDED"),
        ("failed", "CHILD_FAILED"),
        ("cancelled", "CHILD_CANCELLED"),
        ("paused", "CHILD_ATTENTION"),
    ],
)
def test_exact_closed_core_facts_and_join(status, outcome, fixture):
    client = FakeClient(fixture)
    client.status = status
    result = advance(fixture, client)
    assert result == {
        "outcome": outcome,
        "summary": outcome,
        "refs": ["results/task_1.json"],
    }
    saved = journal(fixture).load()
    assert helper.has_join_proof(saved, "create")
    assert helper.has_join_proof(saved, "start")
    assert helper.closed(mapping(saved["observation"]), attention=True)
    assert not any("lad" in key or "queue" in key for key in result)


@pytest.mark.parametrize("writer", ["running", "may_live", "unknown"])
def test_join_and_writer_uncertainty_never_settle(fixture, writer):
    client = FakeClient(fixture)
    client.status, client.writer = "succeeded", writer
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_UNCERTAIN"
    assert result["summary"] == "writers_unresolved"
    assert journal(fixture).load()["run_id"] == RUN


def test_terminal_without_physical_join_is_uncertain(fixture):
    client = FakeClient(fixture)
    client.status, client.joined = "succeeded", False
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_UNCERTAIN"
    assert result["summary"] == "holder_unjoined"


def test_join_history_survives_cache_expiry(fixture):
    client = FakeClient(fixture)
    advance(fixture, client)
    before = journal(fixture).load()
    assert helper.has_join_proof(before, "start")
    client.status, client.receipt_only = "succeeded", True
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_SUCCEEDED"
    saved = journal(fixture).load()
    for operation in ("create", "start"):
        request_id = mapping(mapping(saved["envelopes"])[operation])["request_id"]
        rows = [
            row for row in saved["os_observations"] if row["request_id"] == request_id
        ]
        assert [row["holder_joined"] for row in rows] == (
            [True] if operation == "create" else [True, None]
        )
        assert rows[0] in before["os_observations"]
        assert helper.has_join_proof(saved, operation)


def test_proven_create_expiry_does_not_block_unresolved_start(fixture):
    client = FakeClient(fixture)
    submit = client.submit

    def live_start(envelope, *, deadline):
        row = submit(envelope, deadline=deadline)
        if envelope["operation"] == "create_instance":
            client.joined = False
        return row

    client.submit = live_start
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    before = journal(fixture).load()
    assert helper.has_join_proof(before, "create")
    assert not helper.has_join_proof(before, "start")
    create_id = before["envelopes"]["create"]["request_id"]
    start = before["envelopes"]["start"]
    sends = copy.deepcopy(client.sent)
    observed = []
    observe = client.observe

    def expired_create(envelope, *, deadline):
        observed.append(envelope["request_id"])
        if envelope["request_id"] == create_id:
            raise SubworkflowError("os_http_404")
        return observe(envelope, deadline=deadline)

    client.observe = expired_create
    client.status = "succeeded"
    assert advance(fixture, client)["summary"] == "holder_unjoined"
    held = journal(fixture).load()
    assert held["observation"]["status"] == "succeeded"
    assert not helper.has_join_proof(held, "start")
    client.responses[start["request_id"]].update(
        holder_joined=True, phase="call_returned"
    )
    assert advance(fixture, client)["outcome"] == "CHILD_SUCCEEDED"
    saved = journal(fixture).load()
    assert create_id not in observed and start["request_id"] in observed
    assert client.sent == sends
    assert [r for r in saved["os_observations"] if r["request_id"] == create_id] == [
        r for r in before["os_observations"] if r["request_id"] == create_id
    ]
    assert [
        r["holder_joined"]
        for r in saved["os_observations"]
        if r["request_id"] == start["request_id"]
    ] == [False, True]
    assert ("read", CHILD, RUN) in client.events


@pytest.mark.parametrize("proof", ["missing", "false", "null"])
def test_create_refresh_without_retained_true_still_observes(fixture, proof):
    client = FakeClient(fixture)
    advance(fixture, client)
    storage = journal(fixture)
    saved = storage.load()
    create_id = saved["envelopes"]["create"]["request_id"]
    for row in list(saved["os_observations"]):
        if row["request_id"] != create_id:
            continue
        if proof == "missing":
            saved["os_observations"].remove(row)
        elif proof == "false":
            row.update(holder_joined=False, phase="call_active")
        else:
            row.update(
                source="core_receipt", holder_joined=None, phase="receipt_observed"
            )
    storage.save(saved)
    before, sends = storage.path.read_bytes(), copy.deepcopy(client.sent)
    client.fail_observe = "os_http_404"
    timer = Clock()
    with pytest.raises(SubworkflowError, match="os_http_404"):
        helper._refresh(storage, storage.load(), client, 1, timer, timer.sleep)
    assert client.events[-1][1]["request_id"] == create_id
    assert storage.path.read_bytes() == before and client.sent == sends


@pytest.mark.parametrize("status", ["queued", "refused"])
def test_joined_create_without_applied_receipt_still_observes(fixture, status):
    client = FakeClient(fixture)
    submit = client.submit

    def not_applied(envelope, *, deadline):
        row = submit(envelope, deadline=deadline)
        assert envelope["operation"] == "create_instance"
        row["core_receipt"].update(status=status, instance_id=None)
        row["instance_id"] = None
        client.responses[envelope["request_id"]] = copy.deepcopy(row)
        return row

    client.submit = not_applied
    advance(fixture, client)
    storage = journal(fixture)
    saved = storage.load()
    assert saved["instance_id"] is None
    assert saved["receipts"]["create"]["status"] == status
    assert helper.has_join_proof(saved, "create")
    before, sends = storage.path.read_bytes(), copy.deepcopy(client.sent)
    client.fail_observe = "os_http_404"
    timer = Clock()
    with pytest.raises(SubworkflowError, match="os_http_404"):
        helper._refresh(storage, saved, client, 1, timer, timer.sleep)
    assert client.events[-1][1]["operation"] == "create_instance"
    assert storage.path.read_bytes() == before and client.sent == sends


def test_null_join_without_prior_true_holds(fixture):
    client = FakeClient(fixture)
    original = client.submit

    def submit(envelope, *, deadline):
        row = original(envelope, deadline=deadline)
        row.update(source="core_receipt", phase="receipt_observed", holder_joined=None)
        client.responses[envelope["request_id"]] = row
        return row

    client.submit = submit
    client.status = "succeeded"
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_OBSERVATION_LOST"
    assert not helper.has_join_proof(journal(fixture).load(), "create")
    assert [row["operation"] for row in client.sent] == ["create_instance"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_digest", "f" * 64),
        ("instance_id", "f" * 32),
        ("request_id", "f" * 32),
        ("home", "other"),
    ],
)
def test_join_proof_cannot_be_borrowed(fixture, field, value):
    client = FakeClient(fixture)
    advance(fixture, client)
    saved = journal(fixture).load()
    mapping(saved["envelopes"])["create"] = None
    start_id = mapping(mapping(saved["envelopes"])["start"])["request_id"]
    saved["os_observations"] = [
        copy.deepcopy(row)
        for row in saved["os_observations"]
        if row["request_id"] == start_id
    ]
    saved["os_observations"][0][field] = value
    assert not helper.has_join_proof(saved, "start")


def test_unknown_start_never_dispatches_again_and_receipt_gap_recovers(fixture):
    client = FakeClient(fixture)
    client.fail_submit = "start_run"
    assert advance(fixture, client)["outcome"] == "CHILD_UNCERTAIN"
    saved = journal(fixture).load()
    assert mapping(saved["reservations"])["start"] == "unknown"
    assert saved["run_id"] is None
    start = copy.deepcopy(mapping(saved["envelopes"])["start"])
    client.fail_submit = None
    client.receipt_only = True
    assert advance(fixture, client)["outcome"] == "CHILD_OBSERVATION_LOST"
    saved = journal(fixture).load()
    assert saved["run_id"] == RUN
    assert mapping(saved["envelopes"])["start"] == start
    assert [e["operation"] for e in client.sent].count("start_run") == 1
    assert ("read", CHILD, RUN) in client.events
    assert all(
        e[1]["run_id"] is None
        for e in client.events
        if e[0] == "observe" and e[1]["operation"] == "start_run"
    )


def test_start_receipt_gap_uses_exact_instance_control(fixture):
    client = FakeClient(fixture)
    client.fail_submit = "start_run"
    advance(fixture, client)
    sent = mapping(mapping(journal(fixture).load()["envelopes"])["start"])
    client.fail_submit = None
    original = client.observe

    def no_receipt(envelope, *, deadline):
        row = original(envelope, deadline=deadline)
        if envelope["operation"] == "start_run":
            row.update(core_receipt=None, instance_id=CHILD, run_id=None)
        return row

    client.observe = no_receipt
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    saved = journal(fixture).load()
    assert saved["run_id"] == RUN
    assert (
        mapping(mapping(saved["receipts"])["start"])["request_id"] == sent["request_id"]
    )
    assert sum(e["operation"] == "start_run" for e in client.sent) == 1
    client.run_override = {"start_request_id": "f" * 32}
    assert advance(fixture, client)["summary"] == "wrong_start_identity"


def test_unknown_create_replays_only_exact_idempotent_envelope(fixture):
    client = FakeClient(fixture)
    client.fail_submit = "create_instance"
    advance(fixture, client)
    original = copy.deepcopy(client.sent[0])
    client.fail_submit = None
    observe = client.observe
    called = False

    def missing(envelope, *, deadline):
        nonlocal called
        if not called:
            called = True
            raise SubworkflowError("os_http_404")
        return observe(envelope, deadline=deadline)

    client.observe = missing
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    creates = [row for row in client.sent if row["operation"] == "create_instance"]
    assert creates == [original, original]


@pytest.mark.parametrize("operation", ["create", "start"])
def test_crash_after_reservation_before_send_keeps_exact_envelope(
    fixture, monkeypatch, operation
):
    client = FakeClient(fixture)
    save = helper._save
    crashed = False

    def crash(journal, record):
        nonlocal crashed
        save(journal, record)
        if not crashed and mapping(record["reservations"])[operation] == "unknown":
            crashed = True
            raise RuntimeError("simulated power loss")

    monkeypatch.setattr(helper, "_save", crash)
    with pytest.raises(RuntimeError, match="simulated power loss"):
        advance(fixture, client)
    saved = journal(fixture).load()
    sent = copy.deepcopy(mapping(saved["envelopes"])[operation])
    assert mapping(saved["reservations"])[operation] == "unknown"
    monkeypatch.setattr(helper, "_save", save)
    if operation == "start":
        # A pre-send crash is indistinguishable from an in-flight start. Observe only.
        def missing(envelope, *, deadline):
            if envelope["operation"] == "start_run":
                raise SubworkflowError("os_http_404")
            return copy.deepcopy(client.responses[envelope["request_id"]])

        client.observe = missing
        assert advance(fixture, client)["outcome"] == "CHILD_UNCERTAIN"
        assert not any(row["operation"] == "start_run" for row in client.sent)
    else:

        def missing(envelope, *, deadline):
            raise SubworkflowError("os_http_404")

        client.observe = missing
        advance(fixture, client)
        assert client.sent[0] == sent
    assert mapping(journal(fixture).load()["envelopes"])[operation] == sent


def test_unchanged_observations_do_not_rewrite(fixture, monkeypatch):
    client = FakeClient(fixture)
    advance(fixture, client)
    storage = journal(fixture)
    before = storage.path.read_bytes()
    writes = []
    original = helper._save

    def save(*args):
        writes.append(True)
        return original(*args)

    monkeypatch.setattr(helper, "_save", save)
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    assert storage.path.read_bytes() == before
    assert writes == []


def test_nonblocking_advancing_lock(fixture):
    client = FakeClient(fixture)
    advance(fixture, client)
    count = len(client.sent)
    with journal(fixture).lock(observer=True):
        with pytest.raises(SubworkflowError, match="call_busy"):
            advance(fixture, client)
    assert len(client.sent) == count


def test_changed_raw_request_or_profile_refuses_replay(fixture):
    client = FakeClient(fixture)
    advance(fixture, client)
    fixture["file"].write_bytes(canonical(fixture["request"]))
    with pytest.raises(SubworkflowError, match="call_conflict"):
        advance(fixture, client)
    write_request(fixture)
    fixture["profile"].write_bytes(fixture["profile"].read_bytes() + b"\n")
    with pytest.raises(SubworkflowError, match="os_profile_changed"):
        advance(fixture, client)
    assert len(client.sent) == 2


def test_window_counts_client_time_and_never_sends_after_expiry(fixture):
    client = FakeClient(fixture)
    clock = Clock()
    original = client.submit

    def delayed(envelope, *, deadline):
        row = original(envelope, deadline=deadline)
        clock.now = deadline
        return row

    client.submit = delayed
    result = advance(fixture, client, clock)
    assert result["outcome"] == "CHILD_RUNNING"
    assert [e["operation"] for e in client.sent] == ["create_instance"]
    assert clock() == 1


def test_registration_refusal_does_not_bypass_or_start(fixture):
    client = FakeClient(fixture)

    def refused(envelope, *, deadline):
        from persistent_lad_support import receipt

        client.sent.append(copy.deepcopy(envelope))
        return observation(
            envelope, core=receipt(envelope, status="refused", instance=None)
        )

    client.submit = refused
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_REFUSED"
    assert journal(fixture).load()["run_id"] is None
    assert [e["operation"] for e in client.sent] == ["create_instance"]


def test_wrong_child_paths_hold_and_preserve_join_history(fixture):
    client = FakeClient(fixture)
    original = client.read

    def wrong(instance, run=None, *, deadline):
        row = original(instance, run, deadline=deadline)
        if instance == CHILD and run is None:
            row["project_root"] = str(fixture["parent"])
        return row

    client.read = wrong
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_UNCERTAIN"
    assert result["summary"] == "wrong_child_identity"
    saved = journal(fixture).load()
    assert helper.has_join_proof(saved, "create")
    assert helper.has_join_proof(saved, "start")


def test_large_result_is_compact_uncertain_without_losing_journal_evidence(fixture):
    client = FakeClient(fixture)
    client.status = "succeeded"
    client.run_override = {"bounded_public_extra": "x" * 270000}
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_UNCERTAIN"
    assert result["summary"] == "result_limit"
    saved = journal(fixture).load()
    assert len(mapping(saved["observation"])["bounded_public_extra"]) == 270000
    manifest = json.loads((fixture["parent"] / "results/task_1.json").read_bytes())
    assert manifest["observation"] == {}
    assert manifest["diagnostic"] == "result_limit"


@pytest.mark.parametrize(
    "initial,later",
    [
        (None, "a" * 64),
        ("a" * 64, None),
        ("a" * 64, "b" * 64),
    ],
)
def test_claim_binding_drift_refuses_before_file_or_send_mutation(
    fixture, initial, later
):
    client, clock = FakeClient(fixture), Clock()
    helper.advance(
        fixture["file"],
        claim_sha256=initial,
        cwd=fixture["parent"],
        client=client,
        clock=clock,
        sleep=clock.sleep,
    )
    storage = journal(fixture)
    before = storage.path.read_bytes()
    output = (fixture["parent"] / "results/task_1.json").read_bytes()
    sends = copy.deepcopy(client.sent)
    with pytest.raises(SubworkflowError, match="call_conflict"):
        helper.advance(
            fixture["file"],
            claim_sha256=later,
            cwd=fixture["parent"],
            client=client,
            clock=clock,
            sleep=clock.sleep,
        )
    assert storage.path.read_bytes() == before
    assert (fixture["parent"] / "results/task_1.json").read_bytes() == output
    assert client.sent == sends


def test_generic_home_is_derived_and_expected_home_cannot_override(fixture):
    client, clock = FakeClient(fixture), Clock()
    with pytest.raises(SubworkflowError, match="os_home_mismatch"):
        helper.advance(
            fixture["file"],
            expected_home="other",
            cwd=fixture["parent"],
            client=client,
            clock=clock,
            sleep=clock.sleep,
        )
    assert not journal(fixture).root.exists()
    assert client.events == []
    result = helper.advance(
        fixture["file"],
        cwd=fixture["parent"],
        client=client,
        clock=clock,
        sleep=clock.sleep,
    )
    assert result["outcome"] == "CHILD_RUNNING"
    saved = journal(fixture).load()
    assert saved["os_home"] == "local"
    assert saved["claim_sha256"] is None


def test_copy_save_gap_replays_only_the_owned_pinned_copy(fixture, monkeypatch):
    from millrace_plus import catalog

    client = FakeClient(fixture)
    initialize = catalog.init_template
    crashed = False

    def interrupted(selector, destination):
        nonlocal crashed
        result = initialize(selector, destination)
        if not crashed:
            crashed = True
            raise RuntimeError("copy-save gap")
        return result

    monkeypatch.setattr(catalog, "init_template", interrupted)
    with pytest.raises(RuntimeError, match="copy-save gap"):
        advance(fixture, client)
    saved = journal(fixture).load()
    assert saved["phase"] == "copying"
    assert saved["copy_prepared"] is False
    assert client.sent == []
    monkeypatch.setattr(catalog, "init_template", initialize)
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    assert len(client.sent) == 2
    assert journal(fixture).load()["copy_prepared"] is True


def test_result_save_gap_repairs_without_dispatching_again(fixture, monkeypatch):
    client = FakeClient(fixture)
    client.status = "succeeded"
    atomic = helper.atomic_json

    def interrupted(path, value, *, limit):
        atomic(path, value, limit=limit)
        raise RuntimeError("result-save gap")

    monkeypatch.setattr(helper, "atomic_json", interrupted)
    with pytest.raises(RuntimeError, match="result-save gap"):
        advance(fixture, client)
    storage = journal(fixture)
    assert storage.load()["saved_result"] is None
    saved_manifest = (fixture["parent"] / "results/task_1.json").read_bytes()
    monkeypatch.setattr(helper, "atomic_json", atomic)
    assert advance(fixture, client)["outcome"] == "CHILD_SUCCEEDED"
    assert (fixture["parent"] / "results/task_1.json").read_bytes() == saved_manifest
    assert len(client.sent) == 2


def test_two_fake_instances_share_home_and_preserve_sibling(fixture):
    first = FakeClient(fixture)
    advance(fixture, first)
    original = journal(fixture).path.read_bytes()
    second_fixture = {**fixture, "request": copy.deepcopy(fixture["request"])}
    second_fixture["request"].update(
        call_key="task_2", output_ref="results/task_2.json"
    )
    second_fixture["file"] = fixture["root"] / "second-request.json"
    write_request(second_fixture)
    second = FakeClient(second_fixture)
    second.instance, second.run = "e" * 32, "f" * 32
    assert advance(second_fixture, second)["outcome"] == "CHILD_RUNNING"
    second_saved = journal(second_fixture).load()
    assert second_saved["os_home"] == journal(fixture).load()["os_home"] == "local"
    assert (second_saved["instance_id"], second_saved["run_id"]) == ("e" * 32, "f" * 32)
    assert journal(fixture).path.read_bytes() == original
    assert ("read", "e" * 32, "f" * 32) in second.events
    assert ("read", CHILD, RUN) not in second.events


@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_start_controls_never_adopt_unknown_run(fixture, conflicting):
    client = FakeClient(fixture)
    client.status, client.fail_submit = "succeeded", "start_run"
    assert advance(fixture, client)["outcome"] == "CHILD_UNCERTAIN"
    assert journal(fixture).load()["run_id"] is None
    client.fail_submit = None
    duplicate = copy.deepcopy(client.instance_controls[0])
    if conflicting:
        duplicate["run_id"] = "f" * 32
    client.instance_controls.append(duplicate)
    original = client.observe

    def without_receipt(envelope, *, deadline):
        row = original(envelope, deadline=deadline)
        if envelope["operation"] == "start_run":
            row.update(core_receipt=None, instance_id=CHILD, run_id=None)
        return row

    client.observe = without_receipt
    sends = copy.deepcopy(client.sent)
    result = advance(fixture, client)
    assert result["outcome"] == "CHILD_UNCERTAIN"
    assert result["summary"] == "ambiguous_start_receipt"
    saved = journal(fixture).load()
    assert saved["run_id"] is None
    assert saved["phase"] == "held"
    assert "start" not in mapping(saved["receipts"])
    assert ("read", CHILD, RUN) not in client.events
    assert client.sent == sends
    manifest = json.loads((fixture["parent"] / "results/task_1.json").read_bytes())
    assert manifest["run_id"] is None
    assert manifest["dispatch"] == "not_started"
    assert manifest["outcome"] == "CHILD_UNCERTAIN"


@pytest.mark.parametrize("foreign", ["other_call", "arbitrary_bytes"])
def test_result_replacement_requires_exact_existing_call_owner(fixture, foreign):
    client = FakeClient(fixture)
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    output = fixture["parent"] / "results/task_1.json"
    if foreign == "other_call":
        owner = json.loads(output.read_bytes())
        owner["call_identity"] = "f" * 64
        preserved = canonical(owner) + b"\n"
    else:
        preserved = b"operator-owned arbitrary output\n"
    output.write_bytes(preserved)
    sends = copy.deepcopy(client.sent)
    client.status = "succeeded"
    with pytest.raises(SubworkflowError, match="output_not_owned"):
        advance(fixture, client)
    assert output.read_bytes() == preserved
    assert client.sent == sends
    assert journal(fixture).load()["saved_result"]["outcome"] == "CHILD_RUNNING"


class CancelClient(FakeClient):
    def submit(self, envelope, *, deadline):
        if envelope["operation"] != "cancel_run":
            return super().submit(envelope, deadline=deadline)
        from persistent_lad_support import observation, receipt

        self.sent.append(copy.deepcopy(envelope))
        core = receipt(envelope, instance=self.instance, run=self.run)
        row = observation(envelope, joined=self.joined, core=core)
        self.responses[envelope["request_id"]] = row
        if self.fail_submit == "cancel_run":
            raise SubworkflowError("os_unavailable")
        self.status = "cancelled"
        return copy.deepcopy(row)


def retained_cancel(fixture, client, identifier="a" * 32):
    timer = Clock()
    return helper.cancel(
        journal(fixture).home,
        journal(fixture).key,
        identifier,
        client=client,
        clock=timer,
        sleep=timer.sleep,
    )


def prepared_dispatch(fixture, monkeypatch, operation):
    peer = CancelClient(fixture)
    if operation.startswith("cancel:"):
        advance(fixture, peer)
    original = helper._dispatch

    def before_send(storage, saved, selected, client, deadline):
        if selected == operation:
            raise RuntimeError("dispatch prepared")
        return original(storage, saved, selected, client, deadline)

    with monkeypatch.context() as patch:
        patch.setattr(helper, "_dispatch", before_send)
        with pytest.raises(RuntimeError, match="dispatch prepared"):
            if operation.startswith("cancel:"):
                retained_cancel(fixture, peer)
            else:
                advance(fixture, peer)
    storage = journal(fixture)
    saved = storage.load()
    assert helper._reservation(saved, operation) == "unsent"
    return peer, storage, saved


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize(
    "operation,ambiguous",
    [("create", True), ("start", False), ("start", True), ("cancel:" + "a" * 32, True)],
)
def test_prevalidated_child_dispatch_probes_before_unknown_and_never_repeats_effect(
    fixture, monkeypatch, wrapped, operation, ambiguous
):
    from millrace_plus.lad_service_runtime import LocalClient

    peer, storage, saved = prepared_dispatch(fixture, monkeypatch, operation)
    sent = copy.deepcopy(helper._envelopes(saved)[operation])
    connection = helper.WorkflowOSClient(fixture["request"]["os_profile"], "local")
    events = []
    deadline = 12345.0

    def request(method, path, **kwargs):
        events.append((method, kwargs["deadline"]))
        assert kwargs["deadline"] == deadline
        if method == "OPTIONS":
            assert helper._reservation(storage.load(), operation) == "unsent"
            return capabilities()
        assert method == "POST"
        stored = storage.load()
        assert helper._reservation(stored, operation) == "unknown"
        assert helper._envelopes(stored)[operation] == sent
        assert json.loads(kwargs["body"])["request"] == sent
        return peer.submit(sent, deadline=kwargs["deadline"])

    monkeypatch.setattr(connection, "_request", request)
    client = (
        LocalClient(
            SimpleNamespace(verify=lambda: events.append(("verify", deadline))),
            connection,
        )
        if wrapped
        else connection
    )
    if ambiguous:
        peer.fail_submit = sent["operation"]
        with pytest.raises(SubworkflowError, match="os_unavailable"):
            helper._dispatch(storage, saved, operation, client, deadline)
    else:
        helper._dispatch(storage, saved, operation, client, deadline)
    assert events == (
        [("OPTIONS", deadline), ("verify", deadline), ("POST", deadline)]
        if wrapped
        else [("OPTIONS", deadline), ("POST", deadline)]
    )
    assert helper._reservation(storage.load(), operation) == (
        "unknown" if ambiguous else "observed"
    )
    peer.fail_submit = None
    posts = copy.deepcopy(peer.sent)
    if operation.startswith("cancel:"):
        retained_cancel(fixture, peer)
    else:
        advance(fixture, peer)
    assert sum(row["request_id"] == sent["request_id"] for row in peer.sent) == 1
    assert peer.sent[: len(posts)] == posts
    assert any(
        event[0] == "observe" and event[1]["request_id"] == sent["request_id"]
        for event in peer.events
    )
    assert helper._envelopes(storage.load())[operation] == sent


@pytest.mark.parametrize("wrapped", [False, True])
def test_child_dispatch_capability_refusal_preserves_unsent(
    fixture, monkeypatch, wrapped
):
    from millrace_plus.lad_service_runtime import LocalClient

    _, storage, saved = prepared_dispatch(fixture, monkeypatch, "start")
    before = storage.path.read_bytes()
    connection = helper.WorkflowOSClient(fixture["request"]["os_profile"], "local")
    events = []

    def request(method, path, **kwargs):
        events.append(method)
        assert method == "OPTIONS"
        return {**capabilities(), "home": "other"}

    monkeypatch.setattr(connection, "_request", request)
    client = (
        LocalClient(
            SimpleNamespace(verify=lambda: pytest.fail("verify after refusal")),
            connection,
        )
        if wrapped
        else connection
    )
    with pytest.raises(SubworkflowError):
        helper._dispatch(storage, saved, "start", client, 12345.0)
    assert events == ["OPTIONS"]
    assert storage.path.read_bytes() == before
    assert helper._reservation(saved, "start") == "unsent"


@pytest.mark.parametrize("kind", ["http", "wrapped-http", "local"])
def test_child_dispatch_preserves_submit_overrides(fixture, monkeypatch, kind):
    from millrace_plus.lad_service_runtime import LocalClient

    peer, storage, saved = prepared_dispatch(fixture, monkeypatch, "start")
    events = []
    deadline = 12345.0

    class CustomHTTP(helper.WorkflowOSClient):
        def submit(self, envelope, *, deadline):
            events.append(("submit", deadline))
            assert helper._reservation(storage.load(), "start") == "unknown"
            return peer.submit(envelope, deadline=deadline)

    class CustomLocal(LocalClient):
        def submit(self, envelope, *, deadline):
            events.append(("submit", deadline))
            assert helper._reservation(storage.load(), "start") == "unknown"
            return peer.submit(envelope, deadline=deadline)

    connection_type = helper.WorkflowOSClient if kind == "local" else CustomHTTP
    connection = connection_type(fixture["request"]["os_profile"], "local")

    def request(method, path, **kwargs):
        events.append((method, kwargs["deadline"]))
        assert method == "OPTIONS"
        return capabilities()

    monkeypatch.setattr(connection, "_request", request)
    owner = SimpleNamespace(verify=lambda: events.append(("verify", deadline)))
    client = (
        connection
        if kind == "http"
        else CustomLocal(owner, connection)
        if kind == "local"
        else LocalClient(owner, connection)
    )
    helper._dispatch(storage, saved, "start", client, deadline)
    assert events == (
        [("OPTIONS", deadline), ("verify", deadline), ("submit", deadline)]
        if kind == "wrapped-http"
        else [("OPTIONS", deadline), ("submit", deadline)]
    )
    assert helper._reservation(storage.load(), "start") == "observed"


def test_proven_create_refresh_keeps_unresolved_cancel_fresh(fixture):
    client = CancelClient(fixture)
    advance(fixture, client)
    client.joined = False
    retained_cancel(fixture, client)
    storage = journal(fixture)
    saved = storage.load()
    assert helper.has_join_proof(saved, "create")
    assert not helper.has_join_proof(saved, "cancel")
    sends = copy.deepcopy(client.sent)
    observed = []
    observe = client.observe

    def expired_create(envelope, *, deadline):
        observed.append(envelope["operation"])
        if envelope["operation"] == "create_instance":
            raise SubworkflowError("os_http_404")
        return observe(envelope, deadline=deadline)

    client.observe = expired_create
    timer = Clock()
    helper._refresh(storage, saved, client, 1, timer, timer.sleep)
    assert "create_instance" not in observed and "cancel_run" in observed
    assert not helper.has_join_proof(storage.load(), "cancel")
    assert helper._outcome(saved) == ("CHILD_UNCERTAIN", "holder_unjoined")
    assert client.sent == sends


def test_cancel_ids_are_durable_and_replay_never_posts_again(fixture):
    client = CancelClient(fixture)
    advance(fixture, client)
    first = retained_cancel(fixture, client)
    sends = copy.deepcopy(client.sent)
    replay = retained_cancel(fixture, client)
    # The finite first window can end before a post-cancel public run read.
    # Replay may learn terminal state, but cannot change saved control authority.
    assert first["saved_result"]["outcome"] == "CHILD_RUNNING"
    assert first["observation"]["status"] == "running"
    assert replay["saved_result"]["outcome"] == "CHILD_CANCELLED"
    assert replay["observation"]["status"] == "cancelled"
    for name in (
        "authority_sha256",
        "claim_sha256",
        "request_sha256",
        "cancel_intent",
        "cancellations",
        "envelopes",
        "reservations",
        "receipts",
    ):
        assert replay[name] == first[name]
    assert client.sent == sends
    second = retained_cancel(fixture, client, "b" * 32)
    assert set(second["cancellations"]) == {"a" * 32, "b" * 32}
    assert second["cancel_intent"] == first["cancel_intent"]
    assert second["envelopes"]["cancel"] == first["envelopes"]["cancel"]
    assert second["saved_result"]["outcome"] == "CHILD_CANCELLED"
    assert [
        row["request_id"] for row in client.sent if row["operation"] == "cancel_run"
    ] == ["a" * 32, "b" * 32]


@pytest.mark.parametrize("after_create", [False, True])
def test_cancel_prevents_unsent_create_or_saved_start(fixture, after_create):
    client = CancelClient(fixture)
    original = helper._dispatch

    def interrupted(storage, saved, operation, peer, deadline):
        if not after_create or operation == "start":
            raise RuntimeError("before-send")
        return original(storage, saved, operation, peer, deadline)

    from unittest.mock import patch

    with patch.object(helper, "_dispatch", interrupted):
        with pytest.raises(RuntimeError, match="before-send"):
            advance(fixture, client)
    saved = retained_cancel(fixture, client)
    assert saved["saved_result"]["outcome"] == "CHILD_CANCELLED"
    assert saved["diagnostic"] == "dispatch_prevented"
    assert saved["run_id"] is None and saved["observation"] == {}
    sends = copy.deepcopy(client.sent)
    assert advance(fixture, client)["outcome"] == "CHILD_CANCELLED"
    assert client.sent == sends
    assert all(row["operation"] != "start_run" for row in client.sent)
    output = json.loads((fixture["parent"] / "results/task_1.json").read_bytes())
    assert output["status"] is None and output["dispatch"] == "not_started"


def test_cancel_save_precedes_post_and_unknown_cancel_is_not_resent(
    fixture, monkeypatch
):
    client = CancelClient(fixture)
    advance(fixture, client)
    submit = client.submit

    def checked(envelope, *, deadline):
        if envelope["operation"] == "cancel_run":
            stored = journal(fixture).load()
            assert stored["cancel_intent"]["request_id"] == envelope["request_id"]
            assert (
                stored["cancellations"][envelope["request_id"]]["reservation"]
                == "unknown"
            )
        return submit(envelope, deadline=deadline)

    monkeypatch.setattr(client, "submit", checked)
    client.fail_submit = "cancel_run"
    saved = retained_cancel(fixture, client)
    assert saved["cancellations"]["a" * 32]["reservation"] == "unknown"
    sends = copy.deepcopy(client.sent)
    client.fail_submit = None
    retained_cancel(fixture, client)
    assert client.sent == sends
    before = journal(fixture).path.read_bytes()
    with journal(fixture).lock(observer=True):
        with pytest.raises(SubworkflowError, match="call_busy"):
            retained_cancel(fixture, client, "b" * 32)
    assert journal(fixture).path.read_bytes() == before


def test_reconcile_only_reads_and_keeps_bound_call(fixture):
    client = CancelClient(fixture)
    advance(fixture, client)
    before = journal(fixture).path.read_bytes()
    sends = copy.deepcopy(client.sent)
    timer = Clock()
    saved = helper.reconcile(
        journal(fixture).home, journal(fixture).key, client=client, clock=timer
    )
    assert journal(fixture).path.read_bytes() == before and client.sent == sends
    assert saved["claim_sha256"] == "a" * 64
    assert helper.inspect(journal(fixture).home, journal(fixture).key) == saved
    with pytest.raises(SubworkflowError, match="call_conflict"):
        helper.advance(
            fixture["file"],
            cwd=fixture["parent"],
            client=client,
            clock=timer,
            sleep=timer.sleep,
        )


def test_second_cancel_holder_and_null_cannot_be_hidden_by_first_alias(fixture):
    client = CancelClient(fixture)
    advance(fixture, client)
    first = retained_cancel(fixture, client)
    client.joined = False
    saved = retained_cancel(fixture, client, "b" * 32)
    assert saved["saved_result"]["outcome"] == "CHILD_UNCERTAIN"
    assert saved["receipts"]["cancel"] == first["receipts"]["cancel"]
    client.receipt_only = True
    timer = Clock()
    saved = helper.reconcile(
        journal(fixture).home, journal(fixture).key, client=client, clock=timer
    )
    assert saved["saved_result"]["outcome"] == "CHILD_OBSERVATION_LOST"
    assert helper.has_join_proof(saved, "cancel:" + "a" * 32)
    assert not helper.has_join_proof(saved, "cancel:" + "b" * 32)


@pytest.mark.parametrize("next_window", ["reconcile", "advance"])
def test_pending_cancel_discovers_only_exact_ambiguous_start(fixture, next_window):
    client = CancelClient(fixture)
    client.fail_submit = "start_run"
    assert advance(fixture, client)["outcome"] == "CHILD_UNCERTAIN"
    assert journal(fixture).load()["run_id"] is None
    client.fail_submit = None
    saved = retained_cancel(fixture, client)
    assert saved["saved_result"]["outcome"] == "CHILD_RUNNING"
    assert saved["run_id"] == RUN
    assert saved["observation"]["status"] == "running"
    sends = copy.deepcopy(client.sent)
    if next_window == "reconcile":
        saved = helper.reconcile(
            journal(fixture).home, journal(fixture).key, client=client, clock=Clock()
        )
    else:
        assert advance(fixture, client)["outcome"] == "CHILD_CANCELLED"
        saved = journal(fixture).load()
    assert saved["saved_result"]["outcome"] == "CHILD_CANCELLED"
    assert saved["observation"]["status"] == "cancelled"
    assert client.sent == sends
    assert [row["operation"] for row in client.sent] == [
        "create_instance",
        "start_run",
        "cancel_run",
    ]
    starts = [
        entry[1]
        for entry in client.events
        if entry[0] == "observe" and entry[1]["operation"] == "start_run"
    ]
    assert starts and all(row["run_id"] is None for row in starts)


@pytest.mark.parametrize("foreign", [b"foreign bytes", b"{}"])
def test_cancel_never_replaces_foreign_result(fixture, foreign):
    client = CancelClient(fixture)
    advance(fixture, client)
    output = fixture["parent"] / "results/task_1.json"
    output.write_bytes(foreign)
    with pytest.raises(SubworkflowError, match="output_not_owned"):
        retained_cancel(fixture, client)
    assert output.read_bytes() == foreign


@pytest.mark.parametrize("operation", ["cancel", "reconcile"])
@pytest.mark.parametrize("code", ["observation_window_expired", "wrong_child_identity"])
def test_retained_control_budget_boundary_uses_durable_evidence(
    fixture, monkeypatch, operation, code
):
    client = CancelClient(fixture)
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    storage = journal(fixture)
    before = storage.load()
    sends = copy.deepcopy(client.sent)
    timer = Clock()

    def boundary(instance, run=None, *, deadline):
        timer.now = deadline
        raise SubworkflowError(code)

    monkeypatch.setattr(client, "read", boundary)
    if operation == "cancel":
        saved = helper.cancel(
            storage.home,
            storage.key,
            "b" * 32,
            client=client,
            clock=timer,
            sleep=timer.sleep,
        )
        assert saved["cancel_intent"]["request_id"] == "b" * 32
        assert saved["cancellations"]["b" * 32]["envelope"] is None
    else:
        saved = helper.reconcile(storage.home, storage.key, client=client, clock=timer)
    expected = (
        "CHILD_RUNNING" if code == "observation_window_expired" else "CHILD_UNCERTAIN"
    )
    assert saved["saved_result"]["outcome"] == expected
    assert saved["claim_sha256"] == before["claim_sha256"]
    assert saved["os_observations"] == before["os_observations"]
    assert client.sent == sends
    assert timer.now == 5


@pytest.mark.parametrize("operation", ["cancel", "reconcile"])
def test_retained_control_expiry_preserves_genuine_lost_observation(
    fixture, monkeypatch, operation
):
    client = CancelClient(fixture)
    client.joined = False
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    client.receipt_only = True
    assert advance(fixture, client)["outcome"] == "CHILD_OBSERVATION_LOST"
    storage = journal(fixture)
    before = storage.load()
    timer = Clock()

    def boundary(instance, run=None, *, deadline):
        timer.now = deadline
        raise SubworkflowError("observation_window_expired")

    monkeypatch.setattr(client, "read", boundary)
    if operation == "cancel":
        saved = helper.cancel(
            storage.home,
            storage.key,
            "b" * 32,
            client=client,
            clock=timer,
            sleep=timer.sleep,
        )
    else:
        saved = helper.reconcile(storage.home, storage.key, client=client, clock=timer)
    assert saved["saved_result"]["outcome"] == "CHILD_OBSERVATION_LOST"
    assert saved["os_observations"] == before["os_observations"]
    assert all(row["holder_joined"] is not True for row in saved["os_observations"])


@pytest.mark.parametrize(
    "status,outcome",
    [
        ("succeeded", "CHILD_SUCCEEDED"),
        ("failed", "CHILD_FAILED"),
        ("cancelled", "CHILD_CANCELLED"),
        ("paused", "CHILD_ATTENTION"),
    ],
)
def test_known_false_join_tail_observes_true_without_dispatch(fixture, status, outcome):
    fixture["request"]["observe_seconds"] = 12
    write_request(fixture)
    client, timer = FakeClient(fixture), Clock()
    client.status, client.joined = status, False
    observe = client.observe

    def retired(envelope, *, deadline):
        row = observe(envelope, deadline=deadline)
        if timer() >= 5:
            row.update(holder_joined=True, phase="call_returned")
        return row

    client.observe = retired
    assert advance(fixture, client, timer)["outcome"] == outcome
    assert timer() == 5
    assert [row["operation"] for row in client.sent] == ["create_instance", "start_run"]
    saved = journal(fixture).load()
    assert saved["observation"]["status"] == status
    for operation in ("create", "start"):
        identifier = saved["envelopes"][operation]["request_id"]
        rows = [
            row for row in saved["os_observations"] if row["request_id"] == identifier
        ]
        assert [row["holder_joined"] for row in rows] == [False, True]
        assert helper.has_join_proof(saved, operation)
    assert all(event[-1] == 12 for event in client.events if event[0] == "capabilities")


def test_known_false_join_tail_expires_uncertain_at_original_deadline(fixture):
    fixture["request"]["observe_seconds"] = 12
    write_request(fixture)
    client, timer = FakeClient(fixture), Clock()
    client.status, client.joined = "succeeded", False
    result = advance(fixture, client, timer)
    assert result["outcome"] == "CHILD_UNCERTAIN"
    assert result["summary"] == "holder_unjoined"
    assert timer() == 12
    assert len(client.sent) == 2
    assert journal(fixture).load()["observation"]["status"] == "succeeded"


@pytest.mark.parametrize("seconds", [1, 2, 5, 12])
def test_repeated_join_windows_poll_before_cooldown_without_resending(fixture, seconds):
    fixture["request"]["observe_seconds"] = seconds
    write_request(fixture)
    client, timer = FakeClient(fixture), Clock()
    client.status, client.joined = "succeeded", False
    assert advance(fixture, client, timer)["summary"] == "holder_unjoined"
    before = journal(fixture).load()
    assert helper._join_tail_eligible(before)
    sends = copy.deepcopy(client.sent)
    observed, retired = [], False
    observe = client.observe

    def peer(envelope, *, deadline):
        observed.append((envelope["operation"], timer(), deadline))
        row = observe(envelope, deadline=deadline)
        if retired:
            row.update(holder_joined=True, phase="call_returned")
        return row

    client.observe = peer
    # These enter with already terminal Core facts and retained known-false
    # holders, so each new window must observe before consuming its cooldown.
    for _ in range(2):
        window_start = timer()
        observed.clear()
        assert advance(fixture, client, timer)["summary"] == "holder_unjoined"
        assert timer() == window_start + seconds
        for operation in ("create_instance", "start_run"):
            assert [
                at - window_start for name, at, _ in observed if name == operation
            ] == list(range(0, seconds, 5))
        assert all(deadline == window_start + seconds for _, _, deadline in observed)
        held = journal(fixture).load()
        assert held["os_observations"] == before["os_observations"]
        assert not helper.has_join_proof(held, "start")
        assert client.sent == sends

    retired = True
    window_start = timer()
    observed.clear()
    assert advance(fixture, client, timer)["outcome"] == "CHILD_SUCCEEDED"
    assert timer() == window_start
    assert observed == [
        ("create_instance", window_start, window_start + seconds),
        ("start_run", window_start, window_start + seconds),
    ]
    saved = journal(fixture).load()
    for operation in ("create", "start"):
        identifier = saved["envelopes"][operation]["request_id"]
        assert [
            row["holder_joined"]
            for row in saved["os_observations"]
            if row["request_id"] == identifier
        ] == [False, True]
    assert client.sent == sends and set(saved) == set(before)


@pytest.mark.parametrize(
    "defect", ["null", "writer", "queued", "call_failed", "wrong_home"]
)
def test_join_tail_never_waits_on_other_closure_defects(fixture, defect):
    fixture["request"]["observe_seconds"] = 12
    write_request(fixture)
    client, timer = FakeClient(fixture), Clock()
    client.status, client.joined = "succeeded", False
    if defect == "writer":
        client.writer = "unknown"
    if defect == "queued":
        client.run_override = {"controls": [{"status": "queued"}]}
    observe = client.observe

    def changed(envelope, *, deadline):
        row = observe(envelope, deadline=deadline)
        if defect == "null":
            row.update(
                source="core_receipt", phase="receipt_observed", holder_joined=None
            )
        elif defect == "call_failed":
            row["phase"] = "call_failed"
        elif defect == "wrong_home":
            row["home"] = "other"
        return row

    client.observe = changed
    result = advance(fixture, client, timer)
    assert result["outcome"] == (
        "CHILD_OBSERVATION_LOST" if defect == "null" else "CHILD_UNCERTAIN"
    )
    assert timer() == 0
    assert len(client.sent) == 2


@pytest.mark.parametrize("operation", ["cancel", "advance_pending"])
def test_pending_cancel_join_tail_never_dispatches_after_false(fixture, operation):
    fixture["request"]["observe_seconds"] = 12
    fixture["request"]["cleanup_seconds"] = 12 if operation == "cancel" else 5
    write_request(fixture)
    client = CancelClient(fixture)
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    client.joined = False
    if operation == "advance_pending":
        assert (
            retained_cancel(fixture, client)["saved_result"]["outcome"]
            == "CHILD_RUNNING"
        )
    timer = Clock()
    observe = client.observe
    retirement = 10 if operation == "cancel" else 5

    def retired(envelope, *, deadline):
        row = observe(envelope, deadline=deadline)
        if envelope["operation"] == "cancel_run" and timer() >= retirement:
            row.update(holder_joined=True, phase="call_returned")
        return row

    client.observe = retired
    if operation == "cancel":
        saved = helper.cancel(
            journal(fixture).home,
            journal(fixture).key,
            "a" * 32,
            client=client,
            clock=timer,
            sleep=timer.sleep,
        )
        outcome = saved["saved_result"]["outcome"]
    else:
        outcome = advance(fixture, client, timer)["outcome"]
    assert outcome == "CHILD_CANCELLED"
    assert timer() == retirement
    assert [row["operation"] for row in client.sent] == [
        "create_instance",
        "start_run",
        "cancel_run",
    ]


def test_reconcile_does_not_grow_a_join_wait_loop(fixture):
    client = FakeClient(fixture)
    assert advance(fixture, client)["outcome"] == "CHILD_RUNNING"
    client.status = "succeeded"
    # Remove retained true only in an owned corrupt-state fixture to model a
    # call whose complete recorded history contains known false, not proof.
    saved = journal(fixture).load()
    for row in saved["os_observations"]:
        row.update(holder_joined=False, phase="call_active")
    journal(fixture).save(saved)
    for row in client.responses.values():
        row.update(holder_joined=False, phase="call_active")
    timer = Clock()
    sends = copy.deepcopy(client.sent)
    result = helper.reconcile(
        journal(fixture).home, journal(fixture).key, client=client, clock=timer
    )
    assert result["saved_result"]["outcome"] == "CHILD_UNCERTAIN"
    assert result["diagnostic"] == "holder_unjoined"
    assert timer() == 0 and client.sent == sends


def test_large_captured_draft_success_preserves_every_other_fact_and_raw_view(fixture):
    client = FakeClient(fixture)
    client.status = "succeeded"
    client.run_override = {
        "draft_revision": {
            "revision_id": "a" * 64,
            "files": {"scripts/init_session.py": "x" * 325000},
        },
        "extra_fact": {
            "draft_revision": "nested field stays",
            "false": False,
            "null": None,
        },
    }
    views = []
    read = client.read

    def captured(instance, run=None, *, deadline):
        view = read(instance, run, deadline=deadline)
        if run is not None:
            views.append(copy.deepcopy(view))
        return view

    client.read = captured
    assert advance(fixture, client)["outcome"] == "CHILD_SUCCEEDED"
    saved = journal(fixture).load()
    raw = mapping(saved["observation"])
    assert canonical(raw) == canonical(views[-1])
    output = fixture["parent"] / "results/task_1.json"
    published = json.loads(output.read_bytes())
    assert published["observation"] == {
        key: value for key, value in raw.items() if key != "draft_revision"
    }
    assert published["observation"]["extra_fact"] == client.run_override["extra_fact"]
    assert published["receipts"] == saved["receipts"]
    assert published["call_identity"] == saved["request_sha256"]
    assert published["definition"] == saved["definition"]
    assert published["status"] == raw["status"] == "succeeded"
    assert len(output.read_bytes()) <= 262144
    assert len(canonical(raw)) > 262144
    before = canonical(raw)
    helper._result(journal(fixture), saved, "CHILD_SUCCEEDED", None)
    assert canonical(journal(fixture).load()["observation"]) == before


@pytest.mark.parametrize("foreign", ["identity", "corrupt"])
def test_projected_large_draft_still_refuses_foreign_output(fixture, foreign):
    client = FakeClient(fixture)
    advance(fixture, client)
    output = fixture["parent"] / "results/task_1.json"
    current = json.loads(output.read_bytes())
    current["call_identity"] = "f" * 64
    preserved = (
        canonical(current) if foreign == "identity" else b"foreign corrupt output"
    )
    output.write_bytes(preserved)
    client.status = "succeeded"
    client.run_override = {"draft_revision": {"files": {"captured": "x" * 325000}}}
    with pytest.raises(SubworkflowError, match="output_not_owned"):
        advance(fixture, client)
    assert output.read_bytes() == preserved
    assert "draft_revision" in journal(fixture).load()["observation"]


def test_saved_queued_cancel_is_checked_before_join_tail(fixture):
    from persistent_lad_support import receipt

    from millrace_plus.subworkflow_records import envelope

    client = FakeClient(fixture)
    client.status, client.joined = "succeeded", False
    advance(fixture, client)
    storage = journal(fixture)
    saved = storage.load()
    identifier = "e" * 32
    sent = envelope(
        "cancel_run",
        saved["request"],
        instance_id=CHILD,
        run_id=RUN,
        explicit_id=identifier,
    )
    queued = receipt(sent, status="queued", instance=CHILD, run=RUN)
    saved["cancel_intent"] = {
        "request_id": identifier,
        "created_at": saved["created_at"],
    }
    saved["cancellations"] = {
        identifier: {
            "created_at": saved["created_at"],
            "envelope": sent,
            "reservation": "observed",
            "receipt": queued,
        }
    }
    saved["os_observations"].append(observation(sent, joined=False, core=queued))
    client.responses[identifier] = copy.deepcopy(saved["os_observations"][-1])
    helper._cancel_aliases(saved)
    storage.save(saved)
    assert helper._outcome(saved) == ("CHILD_UNCERTAIN", "controls_unresolved")
    assert helper._join_tail_eligible(saved) is False
    timer = Clock()
    sends = copy.deepcopy(client.sent)
    result = advance(fixture, client, timer)
    assert result["summary"] == "controls_unresolved"
    assert timer() == 0 and client.sent == sends


def test_missing_latest_observation_cannot_enter_join_tail(fixture):
    client = FakeClient(fixture)
    client.status, client.joined = "succeeded", False
    advance(fixture, client)
    storage = journal(fixture)
    saved = storage.load()
    start_id = saved["envelopes"]["start"]["request_id"]
    saved["os_observations"] = [
        row for row in saved["os_observations"] if row["request_id"] != start_id
    ]
    storage.save(saved)
    timer = Clock()
    sends = copy.deepcopy(client.sent)
    assert advance(fixture, client, timer)["summary"] == "holder_unjoined"
    assert timer() == 0 and client.sent == sends


def test_cancel_false_join_tail_uses_original_cleanup_deadline(fixture):
    client = CancelClient(fixture)
    advance(fixture, client)
    client.joined, client.status = False, "cancelled"
    timer = Clock()
    saved = helper.cancel(
        journal(fixture).home,
        journal(fixture).key,
        "a" * 32,
        client=client,
        clock=timer,
        sleep=timer.sleep,
    )
    assert saved["saved_result"]["outcome"] == "CHILD_UNCERTAIN"
    assert saved["diagnostic"] == "holder_unjoined"
    assert saved["observation"]["status"] == "cancelled"
    assert timer() == fixture["request"]["cleanup_seconds"] == 5
    assert [row["operation"] for row in client.sent] == [
        "create_instance",
        "start_run",
        "cancel_run",
    ]
