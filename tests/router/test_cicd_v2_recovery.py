"""Changed input boundary and genuine interruption/aftermath source proofs.

Fixtures use public Core controls and captured command peers; no provider calls.
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest
from cicd_support import event, new_id
from cicd_v2_support import initialized
from test_cicd_workflow import _foreground, _resume, _run, _start

from millrace_plus.cicd_records import MAX_LOG, CicdError, read_log, validate_event
from millrace_plus.cicd_v2_records import DECISION, CicdV2Error
from millrace_plus.cicd_v2_runtime import V2Home
from millrace_plus.local_intake import canonical
from millrace_plus.router import control


def _guard(surface: str, size: int) -> dict[str, object]:
    return {
        "surface": surface,
        "measure": "bytes",
        "value": size,
        "reason": "capture this exact fixture evidence",
        "origin": "test operator",
        "override": "select another guard before init",
    }


def test_v2_large_input_and_log_use_selected_guard_and_preserve_original_refs(tmp_path):
    log = b"evidence\n" * (MAX_LOG // 9 + 1)
    path = event(tmp_path, "0" * 40, summary="lambda: " + "λ" * 35000, log=log)
    raw = path.read_bytes()
    corrupted = json.loads(raw)
    corrupted["attempt"] = True
    invalid = canonical(corrupted)
    fx = initialized(
        tmp_path,
        guards=[
            _guard("input", max(len(raw), len(invalid))),
            _guard("input.log", len(log)),
        ],
    )
    home = V2Home(fx["home"])
    with pytest.raises(CicdError, match="invalid_event_size"):
        validate_event(raw, "example/calc")
    small = event(tmp_path, "0" * 40, name="small", run_id="small", log=log)
    with pytest.raises(CicdError, match="invalid_event_log"):
        read_log(small, validate_event(small.read_bytes(), "example/calc"))
    accepted = home.submit(path)
    typed = home.input(accepted["input_id"])
    original = home.resolve_ref(typed["original_ref"])
    assert original.read_bytes() == raw
    submitted = json.loads(raw)
    assert (original.parent / submitted["log"]["path"]).read_bytes() == log
    assert home.resolve_ref(typed["evidence_refs"][0]).read_bytes() == log
    assert home.submit(path)["receipt"]["sequence"] == accepted["receipt"]["sequence"]
    corrupted_path = tmp_path / "invalid.json"
    corrupted_path.write_bytes(invalid)
    with pytest.raises(CicdError, match="invalid_event"):
        home.submit(corrupted_path)
    # Reuse the exact-byte size budget rather than accidentally testing size.
    path.write_bytes(raw.replace(b"\\u03bb", b"\\u03bc", 1))
    with pytest.raises(CicdError, match="input_conflict"):
        home.submit(path)
    assert original.read_bytes() == raw
    assert len(home.intake.tasks()) == 1
    # A separate consuming workspace receives transitive exact evidence bytes.
    from millrace_plus.cicd_candidates import _publish_projection

    evidence = home.retain({"nested_ref": typed["original_ref"]})
    role_input = home.retain(
        {
            "subject_ref": home.file_ref(
                home.workspace / (accepted["input_id"] + ".input.json")
            ),
            "evidence_refs": [evidence],
            "output": "result.json",
        }
    )
    projection = home.retain({"input_ref": role_input})
    consuming = tmp_path / "consuming-actor"
    consuming.mkdir()
    root, pointer = _publish_projection(
        home, consuming, {"input_ref": role_input, "projection_ref": projection}
    )
    assert (root / typed["original_ref"]["path"]).read_bytes() == raw
    assert (root / typed["evidence_refs"][0]["path"]).read_bytes() == log
    assert (
        json.loads((root / evidence["path"]).read_bytes())["nested_ref"]
        == typed["original_ref"]
    )
    assert (consuming / pointer["input_ref"]["path"]).read_bytes() == home.resolve_ref(
        role_input
    ).read_bytes()


@pytest.mark.parametrize("surface", ["input", "input.log"])
def test_actual_input_guard_refuses_before_acceptance(tmp_path, surface):
    log = b"a real log\n"
    path = event(tmp_path, "0" * 40, log=log)
    size = len(path.read_bytes()) if surface == "input" else len(log)
    fx = initialized(tmp_path, guards=[_guard(surface, size - 1)])
    home = V2Home(fx["home"])
    code = "input_guard_exceeded" if surface == "input" else "input_log_guard_exceeded"
    with pytest.raises(CicdV2Error, match=code):
        home.submit(path)
    assert home.intake.tasks() == []
    assert not (home.workspace / "originals").exists()


def test_missing_nested_receipt_stays_held_after_cancel_and_physical_cleanup(
    tmp_path, monkeypatch
):
    import cicd_support

    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        (
            "import os, time\nfrom pathlib import Path\n"
            "Path('build').mkdir(exist_ok=True)\n"
            "Path('build/ready').write_text(str(os.getpid()))\n"
            "while not Path('build/release').exists(): time.sleep(0.05)\n"
        ),
    )
    fx = initialized(tmp_path, products=True)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    thread, failures = _foreground(fx, _start)
    child = None
    run_id = None
    cleanup_deadline = None
    try:
        receipts = []
        while thread.is_alive() and not receipts:
            receipts = list(home.workspace.glob("*.pid.json"))
            if not receipts:
                thread.join(0.2)  # Existing controller-fixture observation cadence.
        assert receipts, "configured nested check never published startup evidence"
        receipt = json.loads(receipts[0].read_text())
        child = receipt["pid"]
        view = _run(fx)
        run_id = view["id"]
        (outer,) = [
            row
            for row in view["invocations"]
            if row["id"] == receipt["outer_invocation"]["invocation_id"]
        ]
        cleanup_deadline = outer["deadline"]
        assert outer["status"] == "active" and outer["pid"] != child
        assert os.getpgid(child) == receipt["pgid"] == child
        os.kill(outer["pid"], signal.SIGKILL)
        thread.join(max(0, outer["deadline"] - time.time()))
        assert not thread.is_alive() and not failures
        paused = _run(fx)
        assert paused["status"] == "paused"
        assert all(row["writer_state"] == "stopped" for row in paused["invocations"])
        os.kill(child, 0)  # The nested group is still actually live despite that.
        state = home.load_state(accepted["input_id"])
        home.decide(
            {
                "format": DECISION,
                "request_id": new_id(),
                "input_id": accepted["input_id"],
                "expected_state_version": state["version"],
                "kind": "cancel",
                "basis_sha256": state["basis_sha256"],
                "target_stage": None,
                "changed_cause_ref": None,
                "permission_ref": None,
                "evidence_refs": [],
            }
        )
        _resume(fx)
        task = home.task(accepted["input_id"])
        assert task["status"] == "held" and task["terminal_receipt"] is None
        assert (
            home.read_ref(home.load_state(accepted["input_id"])["hold_ref"])[
                "hold_reason"
            ]
            == "nested_check_evidence_missing"
        )
        assert sum(row["node"] == "reproduce" for row in _run(fx)["invocations"]) == 1
    finally:
        if child is not None:
            try:
                assert os.getpgid(child) == child
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if run_id is not None:
            control.cancel_run(
                fx["instance"], run_id, state_home=fx["state"], request_id=new_id()
            )
        thread.join()
        if child is not None and cleanup_deadline is not None:
            while True:
                try:
                    os.killpg(child, 0)
                except ProcessLookupError:
                    break
                assert time.time() < cleanup_deadline, (
                    "owned nested group did not disappear by its existing deadline"
                )
                time.sleep(0.05)
    assert not thread.is_alive()
    assert home.task(accepted["input_id"])["terminal_receipt"] is None


@pytest.mark.parametrize(
    "boundary", ["claim", "published_freeze", "prepared_investigation"]
)
def test_exact_restart_boundary_preserves_reservation_and_holds_ambiguity(
    tmp_path, monkeypatch, boundary
):
    import shutil

    import millrace_plus.cicd_v2_runtime as runtime

    derive = runtime.derive_graph
    copytree = shutil.copytree

    def graph(template, config):
        result = derive(template, config)
        nodes = result["planes"]["main"]["nodes"]
        if boundary == "claim":
            nodes["claim"]["on"]["CLAIMED"] = {
                "pause": {"reason": "fixture_interruption", "resume": "reconcile"}
            }
        elif boundary == "prepared_investigation":
            for outcome in ("OBSERVED", "NEEDS_INVESTIGATION"):
                nodes["guard_reproduction"]["on"][outcome] = {
                    "pause": {"reason": "fixture_interruption", "resume": "reconcile"}
                }
        return result

    def copy(source, destination, *args, **kwargs):
        result = copytree(source, destination, *args, **kwargs)
        if boundary == "published_freeze" and Path(destination).name == "workflow":
            script = Path(destination) / "scripts/step.py"
            text = script.read_text()
            text = text.replace(
                "from millrace_plus.cicd_v2_runtime import step",
                """from millrace_plus.cicd_v2_runtime import Runtime, step
_original_save = Runtime._save
def interrupted_save(self, state, **changes):
    if changes.get('source_binding_ref') is not None:
        raise RuntimeError('fixture: freeze was published before state CAS')
    return _original_save(self, state, **changes)
Runtime._save = interrupted_save""",
            )
            script.write_text(text)
        return result

    monkeypatch.setattr(runtime, "derive_graph", graph)
    monkeypatch.setattr(shutil, "copytree", copy)
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    before = home.load_state(accepted["input_id"])
    assert _run(fx)["status"] == "paused"
    reservation = before["current_attempt_id"]
    if boundary == "prepared_investigation":
        assert reservation is not None
        assert all(row["node"] != "investigate" for row in _run(fx)["invocations"])
    else:
        assert before["attempt_refs"] == []
    publication = home.workspace / (accepted["input_id"] + ".freeze.json")
    published = publication.read_bytes() if boundary == "published_freeze" else None
    _resume(fx)
    view = _run(fx)
    state = home.load_state(accepted["input_id"])
    if boundary == "published_freeze":
        assert publication.read_bytes() == published
        assert state["source_binding_ref"] is None
        assert (
            home.read_ref(state["hold_ref"])["hold_reason"]
            == "freeze_dispatch_possible"
        )
        assert home.task(accepted["input_id"])["terminal_receipt"] is None
        assert (home.workspace / (accepted["input_id"] + ".freeze.json")).exists()
        assert [row["node"] for row in view["invocations"]].count("freeze") == 1
        assert state["attempt_refs"] == []
        last = view["completions"][-1]
        assert last["classification"] == "semantic"
        assert last["outcome"] == "WAIT_OPERATOR"
    else:
        assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
        attempts = runtime.Runtime(home, home.home / "workflow")._attempts(state)
        if reservation is not None:
            assert attempts[reservation]["status"] == "completed"
            assert [row["node"] for row in view["invocations"]].count(
                "investigate"
            ) == 1
        else:
            assert [row["node"] for row in view["invocations"]].count("freeze") == 1
    control.cancel_run(
        fx["instance"], view["id"], state_home=fx["state"], request_id=new_id()
    )


def test_changed_cause_continuation_requires_facts_and_links_fresh_attempt(
    tmp_path, monkeypatch
):
    import cicd_support

    from millrace_plus.cicd_v2_runtime import Runtime

    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        (
            "from pathlib import Path\n"
            "print('dependency version', Path('dependency-state/version').read_text() "
            "if Path('dependency-state/version').exists() else 'v1')\n"
            + cicd_support.CHECK
        ),
    )
    fx = initialized(tmp_path, environment_paths=["dependency-state"])
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
    from millrace_plus.cicd_v2_runtime import initialize

    execution = (home.home / "execution.json").read_bytes()
    initialize(
        fx["home"],
        fx["target"],
        fx["config"],
        authority_file=home.resolve_ref(home.config["local_authority_ref"]),
    )
    assert (home.home / "execution.json").read_bytes() == execution
    decision = {
        "format": DECISION,
        "request_id": new_id(),
        "input_id": accepted["input_id"],
        "expected_state_version": state["version"],
        "kind": "continue",
        "basis_sha256": state["hold_ref"]["sha256"],
        "target_stage": "checks",
        "changed_cause_ref": home.retain(
            {"summary": "please check again with different words"}
        ),
        "permission_ref": None,
        "evidence_refs": [],
    }
    saved = home.state_path(accepted["input_id"]).read_bytes()
    with pytest.raises(CicdV2Error, match="unchanged_cause"):
        home.decide(decision)
    assert home.state_path(accepted["input_id"]).read_bytes() == saved
    assert home.decisions(accepted["input_id"]) == []
    # The captured check actually consumes this explicitly opaque prerequisite.
    (home.target / "dependency-state").mkdir()
    (home.target / "dependency-state/version").write_text("v2")
    environment = Runtime(home, home.home / "workflow")._environment(home.observe())
    cause = home.retain(environment)
    decision.update(request_id=new_id(), changed_cause_ref=cause)
    home.decide(decision)
    _resume(fx)
    final = home.load_state(accepted["input_id"])
    assert home.read_ref(final["hold_ref"])["status"] == "checked_patch"
    attempts = Runtime(home, home.home / "workflow")._attempts(final)
    fresh = [row for row in attempts.values() if row["changed_cause_ref"] == cause]
    assert (
        len(fresh) == 1
        and fresh[0]["kind"] == "checks"
        and fresh[0]["status"] == "completed"
    )
    batch = home.read_ref(fresh[0]["artifact_ref"])
    receipt = home.read_ref(batch["results"][0]["receipt_ref"])
    assert (
        b"dependency version v2" in home.resolve_ref(receipt["stdout_ref"]).read_bytes()
    )
    assert sum(row["node"] == "checks" for row in _run(fx)["invocations"]) == 2
    control.cancel_run(
        fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
    )


def test_bootstrap_replay_rejects_changed_authority_owner_and_captured_definition(
    tmp_path,
):
    from millrace_plus.cicd_v2_runtime import initialize

    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    before = control.inspect_instance(fx["instance"], state_home=fx["state"])
    authority = home.read_ref(home.config["local_authority_ref"])
    for field in ("owner_ref", "actors"):
        wrong = json.loads(json.dumps(authority))
        if field == "owner_ref":
            wrong[field]["sha256"] = "0" * 64
        else:
            wrong[field][0]["definition_ref"]["sha256"] = "0" * 64
        selected = tmp_path / (field + ".json")
        selected.write_bytes(canonical(wrong))
        with pytest.raises(CicdV2Error, match="authority_preparation_mismatch"):
            initialize(fx["home"], fx["target"], fx["config"], authority_file=selected)
    graph = fx["home"] / "workflow/workflow.json"
    original = graph.read_bytes()
    graph.write_bytes(original + b"\n")
    try:
        with pytest.raises(CicdV2Error):
            home.instance()
        assert graph.read_bytes() == original + b"\n"  # No read-time repair.
    finally:
        graph.write_bytes(original)
    assert control.inspect_instance(fx["instance"], state_home=fx["state"]) == before


def test_factory_source_shape_does_not_grant_authority_or_dispatch_preparer(
    tmp_path, monkeypatch
):
    import cicd_v2_support

    original = cicd_v2_support.config

    def config():
        value = original()
        value["source_policy"] = {
            "mode": "factory",
            "known_binding_refs": [],
            "trusted_preparer_command_id": "calc",
        }
        return value

    monkeypatch.setattr(cicd_v2_support, "config", config)
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    before = (home.target / "src/calc.py").read_bytes()
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    assert (
        home.read_ref(state["hold_ref"])["hold_reason"]
        == "factory_source_validator_unavailable"
    )
    assert state["attempt_refs"] == [] and state["source_binding_ref"] is None
    assert (home.target / "src/calc.py").read_bytes() == before
    assert not any(
        row["node"] in {"reproduce", "investigate", "repair"}
        for row in _run(fx)["invocations"]
    )
    control.cancel_run(
        fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
    )


def test_check_source_write_stays_held_after_stopped_wrapper_and_cancel(
    tmp_path, monkeypatch
):
    import cicd_support

    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        (
            "from pathlib import Path\n"
            "Path('src/calc.py').write_text('unexpected check write\\n')\n"
            "print('expected 5')\nraise SystemExit(1)\n"
        ),
    )
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    assert home.read_ref(state["hold_ref"])["hold_reason"] == "check_source_write"
    assert all(row["writer_state"] == "stopped" for row in _run(fx)["invocations"])
    home.decide(
        {
            "format": DECISION,
            "request_id": new_id(),
            "input_id": accepted["input_id"],
            "expected_state_version": state["version"],
            "kind": "cancel",
            "basis_sha256": state["hold_ref"]["sha256"],
            "target_stage": None,
            "changed_cause_ref": None,
            "permission_ref": None,
            "evidence_refs": [],
        }
    )
    _resume(fx)
    assert home.task(accepted["input_id"])["status"] == "held"
    assert home.task(accepted["input_id"])["terminal_receipt"] is None
    assert (home.target / "src/calc.py").read_text() == "unexpected check write\n"
    control.cancel_run(
        fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
    )


@pytest.mark.parametrize("role", ["investigate", "repair", "review"])
@pytest.mark.parametrize("failure", ["error", "invalid", "unknown"])
@pytest.mark.parametrize("decision_kind", ["close", "cancel"])
def test_stopped_role_failure_holds_and_settles_without_redispatch(
    tmp_path, role, failure, decision_kind
):
    from test_cicd_workflow import drive

    from millrace_plus.cicd_v2_runtime import Runtime

    fx = initialized(tmp_path)
    peer = tmp_path / "peer"
    source = Path(__file__).parent / "fixtures/cicd_v2_peer.py"
    import sys

    body = [
        f"#!{sys.executable}",
        "import json, runpy, sys",
        "from pathlib import Path",
        "pointer = json.loads(Path('active-role.json').read_text())",
        f"if pointer['role'] == {role!r}:",
    ]
    if failure == "error":
        body.append("    raise SystemExit(1)")
    elif failure == "unknown":
        body += [
            "    print(json.dumps({'outcome': 'UNDECLARED', 'refs': []}))",
            "    raise SystemExit(0)",
        ]
    else:
        outcome = {
            "investigate": "ASSESSED",
            "repair": "PATCH_READY",
            "review": "ACCEPT",
        }[role]
        body += [
            "    path = Path(pointer['input_ref']['path'])",
            "    document = json.loads(path.read_text())",
            "    Path(document['output']).write_bytes(b'{invalid')",
            f"    print(json.dumps({{'outcome': {outcome!r}, 'refs': []}}))",
            "    raise SystemExit(0)",
        ]
    body.append(f"runpy.run_path({str(source)!r}, run_name='__main__')")
    peer.write_text("\n".join(body) + "\n")
    peer.chmod(0o700)
    home = V2Home(fx["home"])
    first = home.submit(event(tmp_path, fx["head"]))
    second = home.submit(
        event(tmp_path, fx["head"], name="next", run_id="next", conclusion="success")
    )
    _start(fx)
    state = home.load_state(first["input_id"])
    if state["hold_ref"] is None:
        _resume(fx)
        state = home.load_state(first["input_id"])
    assert home.task(first["input_id"])["status"] == "held"
    assert home.task(second["input_id"])["status"] == "queued"
    assert state["hold_ref"] is not None
    attempt = Runtime(home, home.home / "workflow")._attempts(state)[
        state["current_attempt_id"]
    ]
    assert attempt["kind"] == role and attempt["status"] == "completed"
    assert attempt["completion_ref"] is not None
    assert (
        home.read_ref(attempt["completion_ref"])["completion"]["effective_writer"]
        == "stopped"
    )
    home.decide(
        {
            "format": DECISION,
            "request_id": new_id(),
            "input_id": first["input_id"],
            "expected_state_version": state["version"],
            "kind": decision_kind,
            "basis_sha256": state["hold_ref"]["sha256"],
            "target_stage": None,
            "changed_cause_ref": None,
            "permission_ref": None,
            "evidence_refs": [],
        }
    )
    drive(fx, _resume)
    assert home.task(first["input_id"])["status"] == (
        "cancelled" if decision_kind == "cancel" else "failed"
    )
    assert home.task(first["input_id"])["terminal_receipt"] is not None
    assert home.task(second["input_id"])["status"] == "succeeded"
    name = "review_1" if role == "review" else role
    view = _run(fx)
    assert sum(row["node"] == name for row in view["invocations"]) == 1
    control.cancel_run(
        fx["instance"], view["id"], state_home=fx["state"], request_id=new_id()
    )


@pytest.mark.parametrize("state_lock", [False, True])
def test_completed_command_evidence_survives_compliant_lock_holder(
    tmp_path, monkeypatch, state_lock
):
    import select
    import shutil
    from contextlib import ExitStack

    from test_cicd_workflow import drive

    paths = {
        name: tmp_path / (name + ".fifo") for name in ("observed", "release", "batch")
    }
    for path in paths.values():
        os.mkfifo(path)
    fds = {
        name: os.open(path, os.O_RDWR | os.O_NONBLOCK) for name, path in paths.items()
    }
    copytree = shutil.copytree

    def copy(src, dst, *args, **kwargs):
        result = copytree(src, dst, *args, **kwargs)
        if Path(dst).name == "workflow":
            script = Path(dst) / "scripts/step.py"
            text = script.read_text().replace(
                "from millrace_plus.cicd_v2_runtime import step",
                f"""from millrace_plus.cicd_v2_runtime import step
from millrace_plus import cicd_v2_checks as _checks, cicd_v2_runtime as _runtime
import os as _os
_original_command = _checks.run_command
_original_write = _runtime.write_exact
def _observed(*args, **kwargs):
    result = _original_command(*args, **kwargs)
    once = Path({str(tmp_path / "barrier-used")!r})
    if once.exists():
        return result
    once.write_text('one exact reproduction interleaving')
    signal = _os.open({str(paths["observed"])!r}, _os.O_WRONLY)
    _os.write(signal, b'x'); _os.close(signal)
    release = _os.open({str(paths["release"])!r}, _os.O_RDONLY)
    _os.read(release, 1); _os.close(release)
    return result
def _written(path, *args, **kwargs):
    result = _original_write(path, *args, **kwargs)
    if str(path).endswith('.batch.json'):
        signal = _os.open({str(paths["batch"])!r}, _os.O_WRONLY)
        _os.write(signal, b'x'); _os.close(signal)
    return result
_checks.run_command = _observed
_runtime.write_exact = _written""",
            )
            script.write_text(text)
        return result

    monkeypatch.setattr(shutil, "copytree", copy)
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    thread, failures = _foreground(fx, _start)
    try:
        # Wait on a completion barrier using the published invocation bound.
        while True:
            ready, _, _ = select.select([fds["observed"]], [], [], 0)
            if ready:
                os.read(fds["observed"], 1)
                break
            assert thread.is_alive(), failures
            thread.join(0.2)  # Existing public-controller observation cadence.
        view = _run(fx)
        invocation = next(
            row for row in view["invocations"] if row["node"] == "reproduce"
        )
        with ExitStack() as stack:
            stack.enter_context(home.lock())
            if state_lock:
                stack.enter_context(home.state_lock())
            os.write(fds["release"], b"x")
            ready, _, _ = select.select(
                [fds["batch"]], [], [], max(0, invocation["deadline"] - time.time())
            )
            assert ready, (
                "closed command evidence was blocked by a compliant lock holder"
            )
            os.read(fds["batch"], 1)
            (batch_path,) = home.workspace.glob("*.batch.json")
            batch = json.loads(batch_path.read_bytes())
            receipt = home.read_ref(batch["results"][0]["receipt_ref"])
            assert receipt["process_facts"] == {
                "root_reaped": True,
                "stdout_eof": True,
                "stderr_eof": True,
                "group_state": "closed",
            }
        thread.join()
        assert not failures
        state = home.load_state(accepted["input_id"])
        basis = (
            state["hold_ref"]["sha256"] if state["hold_ref"] else state["basis_sha256"]
        )
        before = [row["node"] for row in _run(fx)["invocations"]]
        home.decide(
            {
                "format": DECISION,
                "request_id": new_id(),
                "input_id": accepted["input_id"],
                "expected_state_version": state["version"],
                "kind": "cancel",
                "basis_sha256": basis,
                "target_stage": None,
                "changed_cause_ref": None,
                "permission_ref": None,
                "evidence_refs": [],
            }
        )
        drive(fx, _resume)
        if home.task(accepted["input_id"])["terminal_receipt"] is None:
            drive(fx, _resume)
        assert home.task(accepted["input_id"])["status"] == "cancelled"
        after = [row["node"] for row in _run(fx)["invocations"]]
        assert after.count("reproduce") == 1
        assert all(
            after.count(name) == before.count(name)
            for name in ("investigate", "repair", "review_1")
        )
    finally:
        for fd in fds.values():
            os.close(fd)
        if thread.is_alive():
            control.cancel_run(
                fx["instance"],
                _run(fx)["id"],
                state_home=fx["state"],
                request_id=new_id(),
            )
            thread.join()
    control.cancel_run(
        fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
    )


def _settlement_fixture(tmp_path, monkeypatch, fault):
    """Interrupt an actual captured writer at one logical restoration boundary."""
    import shutil

    import cicd_support

    marker = tmp_path / "settlement-interrupted"
    copytree = shutil.copytree
    injected = f"""
from millrace_plus import cicd_v2_checks as _sc, local_intake as _li
from millrace_plus.cicd_v2_runtime import Runtime as _R, V2Home as _H
import os as _os
_fault = {fault!r}
_marker = Path({str(marker)!r})
_active = False
def _interrupt(where):
    if _active and _fault == where and not _marker.exists():
        _marker.write_text(where)
        raise RuntimeError('fixture logical interruption: ' + where)
_settle = _R._settle
def _settling(self, *args, **kwargs):
    global _active
    _active = True
    try: return _settle(self, *args, **kwargs)
    finally: _active = False
_R._settle = _settling
_reverse = _sc.reverse_products
def _reversed(*args, **kwargs):
    result = _reverse(*args, **kwargs)
    if _active and _fault == 'source_during_cleanup' and not _marker.exists():
        (args[0] / 'src/calc.py').write_bytes(b'corrupt immutable deliverable')
        _marker.write_text('source changed inside cleanup window')
    _interrupt('after_reverse')
    return result
_sc.reverse_products = _reversed
_replace = _os.replace
def _replaced(source, destination, *args, **kwargs):
    result = _replace(source, destination, *args, **kwargs)
    if '/target/build/' in str(destination): _interrupt('partial_path')
    return result
_os.replace = _replaced
_retain = _H.retain
def _retained(self, value):
    result = _retain(self, value)
    if isinstance(value, dict) and value.get('status') == 'restored':
        _interrupt('after_record')
    return result
_H.retain = _retained
_save = _R._save
def _saved(self, state, **changes):
    result = _save(self, state, **changes)
    if 'product_layer_refs' in changes: _interrupt('after_state')
    return result
_R._save = _saved
_decide = _li.Intake.decide
def _decided(self, *args, **kwargs):
    if kwargs.get('terminal_status') is not None: _interrupt('before_cas')
    result = _decide(self, *args, **kwargs)
    if kwargs.get('terminal_status') is not None: _interrupt('after_cas')
    return result
_li.Intake.decide = _decided
"""

    def copy(source, destination, *args, **kwargs):
        result = copytree(source, destination, *args, **kwargs)
        if Path(destination).name == "workflow":
            script = Path(destination) / "scripts/step.py"
            script.write_text(
                script.read_text().replace(
                    "from millrace_plus.cicd_v2_runtime import step",
                    "from millrace_plus.cicd_v2_runtime import step\n" + injected,
                )
            )
        return result

    monkeypatch.setattr(shutil, "copytree", copy)
    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        (
            "from pathlib import Path\n"
            "Path('build/sub').mkdir(parents=True, exist_ok=True)\n"
            "value = Path('src/calc.py').read_bytes()\n"
            "Path('build/a.txt').write_bytes(value)\n"
            "Path('build/sub/b.txt').write_bytes(value)\n" + cicd_support.CHECK
        ),
    )
    fx = initialized(tmp_path, products=True)
    return fx, V2Home(fx["home"]), marker


def _close_held(home, input_id):
    state = home.load_state(input_id)
    decision = {
        "format": DECISION,
        "request_id": new_id(),
        "input_id": input_id,
        "expected_state_version": state["version"],
        "kind": "close",
        "basis_sha256": state["hold_ref"]["sha256"],
        "target_stage": None,
        "changed_cause_ref": None,
        "permission_ref": None,
        "evidence_refs": [],
    }
    home.decide(decision)
    return decision


@pytest.mark.parametrize(
    "fault",
    [
        "partial_path",
        "after_reverse",
        "after_record",
        "after_state",
        "before_cas",
        "after_cas",
    ],
)
def test_overlapping_products_resume_exact_settlement(tmp_path, monkeypatch, fault):
    from test_cicd_workflow import drive

    fx, home, marker = _settlement_fixture(tmp_path, monkeypatch, fault)
    first = home.submit(event(tmp_path, fx["head"]))["input_id"]
    second = home.submit(
        event(tmp_path, fx["head"], name="next", run_id="next", conclusion="success")
    )["input_id"]
    _start(fx)
    state = home.load_state(first)
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
    original = list(state["product_layer_refs"])
    layers = [home.read_ref(ref) for ref in original]
    assert len([row for row in layers if "build/a.txt" in row["owned_paths"]]) >= 2
    source = (home.target / "src/calc.py").read_bytes()
    decision = _close_held(home, first)
    try:
        _resume(fx)
        assert marker.read_text() == fault
        plan_path = home.workspace / (first + ".settlement.json")
        plan_bytes = plan_path.read_bytes()
        plan = json.loads(plan_bytes)
        interrupted = home.load_state(first)
        saved = home.state_path(first).read_bytes()
        home.status()
        assert home.state_path(first).read_bytes() == saved  # Reads never heal.
        assert home.task(second)["status"] == "queued"
        assert (home.task(first)["terminal_receipt"] is not None) == (
            fault == "after_cas"
        )
        if fault == "after_cas":
            assert decision["request_id"] not in interrupted["applied_decision_ids"]
        drive(fx, _resume)
        final = home.load_state(first)
        assert home.task(first)["status"] == "succeeded"
        assert home.task(second)["status"] == "succeeded"
        assert plan_path.read_bytes() == plan_bytes
        receipt = home.intake.decision(plan["request_id"], plan["intent"])
        assert receipt["receipt"] == home.task(first)["terminal_receipt"]
        assert final["applied_decision_ids"].count(decision["request_id"]) == 1
        for old, ref in zip(original, final["product_layer_refs"], strict=True):
            assert home.read_ref(ref) == home.read_ref(old) | {
                "status": "restored",
                "previous_ref": old,
            }
        assert not (home.target / "build").exists()
        assert (home.target / "src/calc.py").read_bytes() == source
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


@pytest.mark.parametrize("foreign", ["bytes", "mode", "link", "child"])
def test_completed_reversal_boundary_refuses_foreign_writes(
    tmp_path, monkeypatch, foreign
):
    fx, home, marker = _settlement_fixture(tmp_path, monkeypatch, "after_state")
    first = home.submit(event(tmp_path, fx["head"]))["input_id"]
    second = home.submit(
        event(tmp_path, fx["head"], name="next", run_id="next", conclusion="success")
    )["input_id"]
    _start(fx)
    _close_held(home, first)
    try:
        _resume(fx)
        assert marker.exists()
        path = home.target / "build/a.txt"
        assert path.is_file()
        if foreign == "bytes":
            path.write_bytes(b"foreign")
        elif foreign == "mode":
            path.chmod(0o700)
        elif foreign == "link":
            path.unlink()
            path.symlink_to("../src/calc.py")
        else:
            path = home.target / "build/unowned"
            path.write_bytes(b"foreign")
        before = home.load_state(first)
        _resume(fx)
        assert home.task(first)["terminal_receipt"] is None
        assert home.task(second)["status"] == "queued"
        after = home.load_state(first)
        for key in (
            "product_layer_refs",
            "applied_decision_ids",
            "attempt_refs",
            "hold_ref",
            "settlement_ref",
        ):
            assert after[key] == before[key]
        assert path.is_symlink() if foreign == "link" else path.exists()
        if foreign in {"bytes", "child"}:
            assert path.read_bytes() == b"foreign"
        if foreign == "mode":
            assert path.stat().st_mode & 0o777 == 0o700
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


def test_later_claim_pristine_freeze_uses_current_claim_history(tmp_path, monkeypatch):
    from test_cicd_workflow import drive

    import millrace_plus.cicd_v2_runtime as runtime

    derive = runtime.derive_graph

    def graph(template, config):
        result = derive(template, config)
        result["planes"]["main"]["nodes"]["claim"]["on"]["CLAIMED"] = {
            "pause": {"reason": "fixture_claim_interruption", "resume": "reconcile"}
        }
        return result

    monkeypatch.setattr(runtime, "derive_graph", graph)
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    first = home.submit(event(tmp_path, fx["head"], conclusion="success"))["input_id"]
    try:
        _start(fx)
        drive(fx, _resume)
        assert home.task(first)["status"] == "succeeded"
        second = home.submit(
            event(tmp_path, fx["head"], name="second", run_id="second")
        )["input_id"]
        _resume(fx)
        assert home.load_state(second)["attempt_refs"] == []
        _resume(fx)
        state = home.load_state(second)
        assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
        assert [row["node"] for row in _run(fx)["invocations"]].count("freeze") == 2
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


def test_never_dispatched_role_can_hold_without_manufactured_completion(
    tmp_path, monkeypatch
):
    import millrace_plus.cicd_v2_runtime as runtime

    derive = runtime.derive_graph

    def graph(template, config):
        result = derive(template, config)
        for outcome in ("OBSERVED", "NEEDS_INVESTIGATION"):
            result["planes"]["main"]["nodes"]["guard_reproduction"]["on"][outcome] = {
                "pause": {"reason": "fixture_prepared", "resume": "hold"}
            }
        return result

    monkeypatch.setattr(runtime, "derive_graph", graph)
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    input_id = home.submit(event(tmp_path, fx["head"]))["input_id"]
    try:
        _start(fx)
        before = home.load_state(input_id)
        attempt_ref = before["attempt_refs"][-1]
        original = home.read_ref(attempt_ref)
        assert original["status"] == "reserved"
        _resume(fx)
        held = home.load_state(input_id)
        assert home.read_ref(held["hold_ref"])["status"] == "uncertain"
        assert held["attempt_refs"][-1] == attempt_ref
        assert home.read_ref(attempt_ref) == original
        assert original["completion_ref"] is None
        assert not any(row["node"] == "investigate" for row in _run(fx)["invocations"])
        assert home.task(input_id)["terminal_receipt"] is None
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


@pytest.mark.parametrize("corruption", ["deliverable", "source"])
def test_corrupt_deliverable_ref_refuses_before_product_reversal(
    tmp_path, monkeypatch, corruption
):
    fx, home, _ = _settlement_fixture(
        tmp_path,
        monkeypatch,
        "unused" if corruption == "deliverable" else "source_during_cleanup",
    )
    input_id = home.submit(event(tmp_path, fx["head"]))["input_id"]
    _start(fx)
    state = home.load_state(input_id)
    report = home.read_ref(state["hold_ref"])
    _close_held(home, input_id)
    patch = home.resolve_ref(report["patch_ref"])
    changed = patch if corruption == "deliverable" else home.target / "src/calc.py"
    if corruption == "deliverable":
        changed.write_bytes(b"corrupt immutable deliverable")
    products = home.observe()["products"]
    saved = home.load_state(input_id)
    try:
        _resume(fx)
        if corruption == "deliverable":
            assert home.observe()["products"] == products
            assert (
                home.load_state(input_id)["product_layer_refs"]
                == saved["product_layer_refs"]
            )
            assert not (home.workspace / (input_id + ".settlement.json")).exists()
        else:
            assert any(
                "settlement_source_changed" in row.get("stderr", "")
                for row in _run(fx)["completions"]
            )
        after = home.load_state(input_id)
        for key in ("applied_decision_ids", "attempt_refs", "hold_ref"):
            assert after[key] == saved[key]
        assert after["settlement_ref"] == saved["hold_ref"]
        assert home.task(input_id)["terminal_receipt"] is None
        assert changed.read_bytes() == b"corrupt immutable deliverable"
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


@pytest.mark.parametrize("products", [False, True])
@pytest.mark.parametrize("move", ["empty", "commit_patch"])
@pytest.mark.parametrize("decision_kind", ["close", "cancel"])
def test_terminal_historical_report_survives_operator_head_move(
    tmp_path, monkeypatch, products, move, decision_kind
):
    from cicd_support import git
    from test_cicd_workflow import drive

    if products:
        fx, home, _ = _settlement_fixture(tmp_path, monkeypatch, "unused")
    else:
        fx = initialized(tmp_path)
        home = V2Home(fx["home"])
    input_id = home.submit(event(tmp_path, fx["head"]))["input_id"]
    _start(fx)
    original_report = home.load_state(input_id)["hold_ref"]
    if move == "commit_patch":
        git(home.target, "add", "--", "src/calc.py")
    if move == "edit_source":
        path = home.target / "src/calc.py"
        path.write_bytes(path.read_bytes() + b"# later operator source edit\n")
    git(
        home.target,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "operator revision",
    )
    current_head = git(home.target, "rev-parse", "HEAD").strip()
    current_source = (home.target / "src/calc.py").read_bytes()
    state = home.load_state(input_id)
    selected = {
        "format": DECISION,
        "request_id": new_id(),
        "input_id": input_id,
        "expected_state_version": state["version"],
        "kind": decision_kind,
        "basis_sha256": original_report["sha256"],
        "target_stage": None,
        "changed_cause_ref": None,
        "permission_ref": None,
        "evidence_refs": [],
    }
    home.decide(selected)
    try:
        drive(fx, _resume)
        assert home.task(input_id)["status"] == (
            "succeeded" if decision_kind == "close" else "cancelled"
        )
        assert home.load_state(input_id)["hold_ref"] == original_report
        assert git(home.target, "rev-parse", "HEAD").strip() == current_head
        assert (home.target / "src/calc.py").read_bytes() == current_source
        assert not (home.target / "build").exists()
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


@pytest.mark.parametrize("tracked", ["index", "head"])
def test_settlement_preserves_newly_tracked_former_products(
    tmp_path, monkeypatch, tracked
):
    from cicd_support import git

    fx, home, _ = _settlement_fixture(tmp_path, monkeypatch, "unused")
    input_id = home.submit(event(tmp_path, fx["head"]))["input_id"]
    _start(fx)
    git(home.target, "add", "--", "build")
    if tracked == "head":
        git(
            home.target,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "operator adopts generated source",
        )
    before = {
        p: (home.target / p).read_bytes() for p in ("build/a.txt", "build/sub/b.txt")
    }
    _close_held(home, input_id)
    try:
        _resume(fx)
        assert home.task(input_id)["terminal_receipt"] is None
        assert all((home.target / p).read_bytes() == raw for p, raw in before.items())
        assert all(
            home.read_ref(ref)["status"] == "accepted"
            for ref in home.load_state(input_id)["product_layer_refs"]
        )
        assert any(
            "owned_product_now_tracked" in row.get("stderr", "")
            for row in _run(fx)["completions"]
        )
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


@pytest.mark.parametrize("origin", ["no_action", "close"])
@pytest.mark.parametrize("projection_fault", [False, True])
def test_pinned_settlement_supersedes_later_cancel_with_same_receipt(
    tmp_path, monkeypatch, origin, projection_fault
):
    import shutil

    from test_cicd_workflow import drive

    if projection_fault:
        copytree = shutil.copytree

        def copy(source, destination, *args, **kwargs):
            result = copytree(source, destination, *args, **kwargs)
            if Path(destination).name == "workflow":
                script = Path(destination) / "scripts/step.py"
                script.write_text(
                    script.read_text().replace(
                        "from millrace_plus.cicd_v2_runtime import step",
                        "from millrace_plus.cicd_v2_runtime import step\n"
                        "from millrace_plus import cicd_v2_runtime as _dp\n"
                        "_exact = _dp.write_exact\n"
                        "def _disposition_gap(path, *args, **kwargs):\n"
                        f"    marker = Path({str(tmp_path / 'disposition-gap')!r})\n"
                        "    if str(path).endswith('.decision-disposition.json') "
                        "and not marker.exists():\n"
                        "        marker.write_text('postCAS disposition gap')\n"
                        "        raise RuntimeError('disposition gap')\n"
                        "    return _exact(path, *args, **kwargs)\n"
                        "_dp.write_exact = _disposition_gap\n",
                    )
                )
            return result

        monkeypatch.setattr(shutil, "copytree", copy)
    fx, home, marker = _settlement_fixture(tmp_path, monkeypatch, "before_cas")
    first = home.submit(
        event(
            tmp_path,
            fx["head"],
            conclusion="success" if origin == "no_action" else "failure",
        )
    )["input_id"]
    second = home.submit(
        event(tmp_path, fx["head"], name="next", run_id="next", conclusion="success")
    )["input_id"]
    _start(fx)
    if origin == "close":
        _close_held(home, first)
        _resume(fx)
    assert marker.exists()
    plan_path = home.workspace / (first + ".settlement.json")
    original_plan = plan_path.read_bytes()
    plan = json.loads(original_plan)
    state = home.load_state(first)
    cancel = {
        "format": DECISION,
        "request_id": new_id(),
        "input_id": first,
        "expected_state_version": state["version"],
        "kind": "cancel",
        "basis_sha256": state["hold_ref"]["sha256"]
        if state["hold_ref"]
        else state["basis_sha256"],
        "target_stage": None,
        "changed_cause_ref": None,
        "permission_ref": None,
        "evidence_refs": [],
    }
    home.decide(cancel)
    try:
        if projection_fault:
            _resume(fx)
            assert home.task(first)["status"] == "succeeded"
            assert home.task(second)["status"] == "queued"
            assert home.status(first)["inputs"][0]["decision_dispositions"] == []
            assert (tmp_path / "disposition-gap").exists()
        drive(fx, _resume)
        assert home.task(first)["status"] == home.task(second)["status"] == "succeeded"
        assert plan_path.read_bytes() == original_plan
        receipt = home.intake.decision(plan["request_id"], plan["intent"])["receipt"]
        assert receipt == home.task(first)["terminal_receipt"]
        (disposition,) = home.status(first)["inputs"][0]["decision_dispositions"]
        assert disposition["status"] == "superseded_by_settlement"
        assert disposition["terminal_receipt"] == receipt
        assert home.read_ref(disposition["decision_ref"]) == cancel
        assert disposition["settlement_plan_ref"] == home.file_ref(plan_path)
        assert (
            cancel["request_id"] not in home.load_state(first)["applied_decision_ids"]
        )
        assert not (home.target / "build").exists()
    finally:
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


@pytest.mark.parametrize("gap", ["reserved", "between", "unknown_prefix"])
def test_precommand_contention_retains_exact_not_started_remainder(
    tmp_path, monkeypatch, gap
):
    import copy
    import select
    import shutil

    import cicd_support
    import cicd_v2_support
    from test_cicd_workflow import drive

    paths = {name: tmp_path / (name + ".fifo") for name in ("gap", "release", "batch")}
    for path in paths.values():
        os.mkfifo(path)
    fds = {
        name: os.open(path, os.O_RDWR | os.O_NONBLOCK) for name, path in paths.items()
    }
    prepare = cicd_v2_support.prepare

    def two_commands(home, target, config, *args, **kwargs):
        value = json.loads(config.read_bytes())
        second = copy.deepcopy(value["commands"][0])
        second["id"] = "calc-two"
        value["commands"].append(second)
        value["reproduction"]["command_ids"] = ["calc", "calc-two"]
        value["check_policy"]["mandatory_ids"] = ["calc", "calc-two"]
        value["check_policy"]["full_ids"] = ["calc", "calc-two"]
        config.write_bytes(canonical(value))
        return prepare(home, target, config, *args, **kwargs)

    monkeypatch.setattr(cicd_v2_support, "prepare", two_commands)
    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        "from pathlib import Path\nPath('build').mkdir(exist_ok=True)\n"
        "Path('build/a').write_text('owned prefix')\n" + cicd_support.CHECK,
    )
    copytree = shutil.copytree
    injected = f"""
import os as _os
from millrace_plus import cicd_v2_checks as _checks, cicd_v2_runtime as _runtime
_gap_kind = {gap!r}
_pattern = _runtime.vr.validate_failure_patterns
_command = _checks.run_command
_exact = _runtime.write_exact
def _barrier():
    marker = Path({str(tmp_path / "gap-used")!r})
    if marker.exists(): return
    marker.write_text('exact precommand gap')
    signal = _os.open({str(paths["gap"])!r}, _os.O_WRONLY)
    _os.write(signal, b'x'); _os.close(signal)
    release = _os.open({str(paths["release"])!r}, _os.O_RDONLY)
    _os.read(release, 1); _os.close(release)
def _patterns(*args, **kwargs):
    if _gap_kind == 'reserved': _barrier()
    return _pattern(*args, **kwargs)
def _commands(*args, **kwargs):
    result = _command(*args, **kwargs)
    if _gap_kind != 'reserved': _barrier()
    if _gap_kind == 'unknown_prefix': result['process_facts']['group_state'] = 'unknown'
    return result
def _written(path, *args, **kwargs):
    result = _exact(path, *args, **kwargs)
    if str(path).endswith('.batch.json'):
        signal = _os.open({str(paths["batch"])!r}, _os.O_WRONLY)
        _os.write(signal, b'x'); _os.close(signal)
    return result
_runtime.vr.validate_failure_patterns = _patterns
_checks.run_command = _commands
_runtime.write_exact = _written
"""

    def copytree_with_barrier(source, destination, *args, **kwargs):
        result = copytree(source, destination, *args, **kwargs)
        if Path(destination).name == "workflow":
            script = Path(destination) / "scripts/step.py"
            script.write_text(
                script.read_text().replace(
                    "from millrace_plus.cicd_v2_runtime import step",
                    "from millrace_plus.cicd_v2_runtime import step\n" + injected,
                )
            )
        return result

    monkeypatch.setattr(shutil, "copytree", copytree_with_barrier)
    fx = initialized(tmp_path, products=True)
    home = V2Home(fx["home"])
    input_id = home.submit(event(tmp_path, fx["head"]))["input_id"]
    thread, failures = _foreground(fx, _start)
    try:
        while not select.select([fds["gap"]], [], [], 0)[0]:
            assert thread.is_alive(), failures
            thread.join(0.2)
        os.read(fds["gap"], 1)
        invocation = next(
            row for row in _run(fx)["invocations"] if row["node"] == "reproduce"
        )
        with home.lock():
            os.write(fds["release"], b"x")
            ready, _, _ = select.select(
                [fds["batch"]], [], [], max(0, invocation["deadline"] - time.time())
            )
            assert ready, "precommand refusal lost attempt-owned evidence"
            os.read(fds["batch"], 1)
        thread.join()
        assert not failures
        (batch_path,) = home.workspace.glob("*.batch.json")
        batch = json.loads(batch_path.read_bytes())
        prefix = 0 if gap == "reserved" else 1
        assert len(batch["results"]) == prefix
        assert batch["not_started"]["ordered_command_ids"] == ["calc", "calc-two"]
        assert (
            batch["not_started"]["remaining_command_ids"]
            == ["calc", "calc-two"][prefix:]
        )
        assert batch["not_started"]["reason"] == "target_busy"
        state = home.load_state(input_id)
        cancel = {
            "format": DECISION,
            "request_id": new_id(),
            "input_id": input_id,
            "expected_state_version": state["version"],
            "kind": "cancel",
            "basis_sha256": state["hold_ref"]["sha256"]
            if state["hold_ref"]
            else state["basis_sha256"],
            "target_stage": None,
            "changed_cause_ref": None,
            "permission_ref": None,
            "evidence_refs": [],
        }
        home.decide(cancel)
        drive(fx, _resume)
        if gap == "unknown_prefix":
            assert home.task(input_id)["terminal_receipt"] is None
            held = home.load_state(input_id)
            assert (
                home.read_ref(held["hold_ref"])["hold_reason"] == "check_writer_unknown"
            )
            assert any(
                home.read_ref(ref)["status"] == "running"
                for ref in held["attempt_refs"]
            )
        else:
            if home.task(input_id)["terminal_receipt"] is None:
                drive(fx, _resume)
            assert home.task(input_id)["status"] == "cancelled"
            assert not (home.target / "build").exists()
            assert (
                home.read_ref(home.load_state(input_id)["attempt_refs"][-1])["status"]
                == "completed"
            )
        nodes = [row["node"] for row in _run(fx)["invocations"]]
        assert nodes.count("reproduce") == 1
        assert "investigate" not in nodes and "repair" not in nodes
    finally:
        if thread.is_alive():
            control.cancel_run(
                fx["instance"],
                _run(fx)["id"],
                state_home=fx["state"],
                request_id=new_id(),
            )
            thread.join()
        for fd in fds.values():
            os.close(fd)
        control.cancel_run(
            fx["instance"], _run(fx)["id"], state_home=fx["state"], request_id=new_id()
        )


def test_terminal_historical_report_preserves_later_operator_source_edit(
    tmp_path, monkeypatch
):
    test_terminal_historical_report_survives_operator_head_move(
        tmp_path, monkeypatch, True, "edit_source", "close"
    )
