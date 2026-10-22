"""Pi store tests transferred to the Plus router facade (graph v2, store v6).

Transferred from the accepted Core baseline `tests/router/test_store.py`
(sha256 487397815260dec194af4f494b3dcf2f9bc6f899717e3941ae4a33b2623dd547)
with identical test names and parametrization ids/values, except as listed:

- test_pi_launch_authority_and_history
- test_pi_result_persistence_and_corruption (9 params)
- test_pi_format_isolation_and_historical_reload
- test_pi_raw_completion_crash_routes_once
- test_pi_unsafe_materialization_is_counted_preparation_failure
- test_pi_core_semantic_channel_after_native_completion (3 params)
- test_failed_pi_evidence_remains_required_and_typed (3 params)
- test_pi_cross_adapter_and_selected_authority (5 params)
- test_pi_failure_with_evidence_keeps_diagnostic_stderr

Split from baseline `test_pi_null_evidence_requires_empty_transport_bytes`
(transfer map entry 55, root-admitted):

- its `preparation` arm moved to Core
  `test_execution_boundary.py::test_core_preparation_null_evidence_requires_empty_bytes`;
- its interrupted arms are `test_pi_interruption_null_evidence_requires_empty_bytes`
  (kind: interrupted_reserved, interrupted_active; field: stdout, stderr);
- new Plus-owned `test_pi_unsafe_prompt_path_has_preflight_evidence`.

Root-admitted deviations (transfer map entries 49, 51, 55):

- Entry 49 (format isolation): the historical v4 downgrade becomes a v5
  downgrade. v6 refuses the old format with `unsupported_store_version` and
  leaves the store byte-identical. Only the fresh tmp store is touched.
- Entry 51 (unsafe materialization): the unsafe native prompt path is Plus
  invoke-time preflight, not Core preparation. The origin is `adapter`, `pid`
  is None, preflight evidence is required, and one reservation is counted.
  A version-probe marker proves that no probe process ran.
- Entry 55 (null evidence): the split above. Interrupted arms keep the Core
  interruption origin with empty bytes and no evidence.
Shape translations, not semantic changes: the baseline `native_launch` record
is the generic `launch` envelope with Plus `details`, and mutations target
`launch["details"]`. `launch_settings` is the v3 common/adapter shape. The v1
prepare `script`/`args` become v2 `config`, and v2 invoke nodes carry
`timeout_seconds`. Added assertions: the entry 49 refusal checks the
`unsupported_store_version` code and a byte-identical store dump, and the
launch-authority test pins the v6 launch envelope.

Seams: lifecycle helpers come from `plus_support`. Every control call goes
through `millrace_plus.router.control`. The Store is opened with the Plus
resolver. Test-owned fault injection uses `Crash` and monkeypatched Store
methods on fresh tmp stores only.
"""

import copy
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from millrace.router.execution import AdapterResult, RouterError
from millrace.router.store import Store
from plus_support import (
    Crash,
    _assistant,
    _events,
    _fake,
    _wire,
    inspect_run,
    native_flow,
    new_id,
    pi_instance,
    recover_run,
    start_run,
    store_path,
)

from millrace_plus.router import control
from millrace_plus.router.pi import resolve_adapter

READY_WIRE_OUTPUT = '{"outcome":"READY","summary":"done","refs":[]}'


def _store(instance: str, home: Path) -> Store:
    """Core store opened with the Plus resolver, for test-owned fault injection."""
    return Store(
        control.load_instance(instance, home), adapter_resolver=resolve_adapter
    )


def test_pi_launch_authority_and_history(single_flow: tuple[Path, Path, Path]) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    instance = pi_instance(workflow, project, home, agent)
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    invocation = run["invocations"][0]
    assert invocation["adapter_evidence"]["invocation_id"] == invocation["id"]
    launch = invocation["launch"]
    assert launch["format"] == "millrace-router-launch/v1"
    assert launch["adapter_id"] == "pi_json"
    assert launch["environment"] == {"PI_CODING_AGENT_DIR": str(agent)}
    assert set(launch["generated_controls"]) == {"pi-prompt.txt"}
    assert launch["details"]["format"] == "millrace-pi-json-launch/v1"
    assert launch["details"]["expected_version"] == "0.87.0"
    (workflow.parent / "task.txt").unlink()
    (workflow.parent / "pi.json").unlink()
    (workflow.parent / "fake-pi").unlink()
    agent.rmdir()
    after = inspect_run(instance, str(run["id"]), state_home=home)
    assert after["status"] == "succeeded" and after["count"] == 1
    assert after["invocations"][0]["launch"] == launch
    assert after["invocations"][0]["adapter_evidence"] == invocation["adapter_evidence"]


@pytest.mark.parametrize(
    "mutation",
    [
        "native_null",
        "wrong_prompt",
        "wrong_host",
        "wrong_provider",
        "evidence_id",
        "evidence_null",
        "text_hash",
        "wrong_phase",
        "nonzero",
    ],
)
def test_pi_result_persistence_and_corruption(
    single_flow: tuple[Path, Path, Path], mutation: str
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    instance = pi_instance(workflow, project, home, agent)
    run = start_run(instance, state_home=home)
    con = sqlite3.connect(store_path(instance, home))
    body = json.loads(con.execute("SELECT body FROM invocations").fetchone()[0])
    if mutation == "native_null":
        body["launch"]["details"] = None
    elif mutation == "wrong_prompt":
        body["launch"]["details"]["prompt_sha256"] = "0" * 64
    elif mutation == "wrong_host":
        body["launch"]["details"]["agent_dir"] = str(home)
    elif mutation == "wrong_provider":
        body["adapter_evidence"]["final"]["provider"] = "other"
    elif mutation == "evidence_id":
        body["adapter_evidence"]["invocation_id"] = "f" * 32
    elif mutation == "evidence_null":
        body["adapter_evidence"] = None
    elif mutation == "text_hash":
        body["adapter_evidence"]["final"]["text_sha256"] = "0" * 64
    elif mutation == "wrong_phase":
        body["adapter_evidence"]["phase"] = "preflight"
    elif mutation == "nonzero":
        con.execute("UPDATE completions SET exit_code=1")
    con.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    con.commit()
    before = list(con.iterdump())
    for _ in range(2):
        with pytest.raises(RouterError) as error:
            inspect_run(instance, str(run["id"]), state_home=home)
        assert error.value.code in {"invocation_integrity", "completion_integrity"}
        assert list(con.iterdump()) == before
    con.close()


def test_pi_format_isolation_and_historical_reload(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    instance = pi_instance(workflow, project, home, agent)
    run = start_run(instance, state_home=home)
    path = store_path(instance, home)
    store = _store(instance, home)
    try:
        invocation = run["invocations"][0]
        row = store.db.execute("SELECT * FROM completions").fetchone()
        exact = AdapterResult(
            row["stdout"],
            row["stderr"],
            row["transport"],
            row["writer"],
            row["diagnostic"],
            row["exit_code"],
            invocation["adapter_evidence"],
        )
        store.complete(str(invocation["id"]), exact)
        changed = dict(invocation["adapter_evidence"], session_id="another-observation")
        with pytest.raises(RouterError, match="conflicting_completion"):
            store.complete(
                str(invocation["id"]), replace(exact, adapter_evidence=changed)
            )
        changed_type = dict(
            invocation["adapter_evidence"],
            event_count=float(invocation["adapter_evidence"]["event_count"]),
        )
        with pytest.raises(RouterError, match="conflicting_completion"):
            store.complete(
                str(invocation["id"]), replace(exact, adapter_evidence=changed_type)
            )
        store.validate()
        store.db.execute(
            "UPDATE meta SET value='millrace-router-store/v5' WHERE key='format'"
        )
        store.db.commit()
    finally:
        store.close()
    con = sqlite3.connect(path)
    before = list(con.iterdump())
    with pytest.raises(RouterError) as error:
        inspect_run(instance, str(run["id"]), state_home=home)
    assert error.value.code == "unsupported_store_version"
    assert list(con.iterdump()) == before
    con.close()


def test_pi_raw_completion_crash_routes_once(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    instance = pi_instance(workflow, project, home, agent)
    ids: dict[str, object] = {}
    original = Store.complete

    def committed(store: Store, *args: object, **kwargs: object) -> None:
        original(store, *args, **kwargs)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", committed)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    con = sqlite3.connect(store_path(instance, home))
    body = json.loads(con.execute("SELECT body FROM invocations").fetchone()[0])
    assert body["adapter_evidence"]["final"]["stop_reason"] == "stop"
    assert body["ref_admission"] is None
    assert con.execute("SELECT classification FROM completions").fetchone()[0] is None
    con.close()
    after = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert after["status"] == "succeeded" and after["count"] == 1
    again = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert len(again["transitions"]) == 1


def test_pi_unsafe_materialization_is_counted_preparation_failure(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    marker = project / "version-probe.marker"
    _fake(
        workflow.parent,
        _wire(_events(project, [_assistant([READY_WIRE_OUTPUT])])),
        version_extra=f"Path({str(marker)!r}).write_text('probed')",
    )
    home = home.parent / "unsafe&home"
    instance = pi_instance(workflow, project, home, agent)
    result = start_run(instance, state_home=home)
    assert result["count"] == 1
    assert result["completions"][0]["transport"] == "execution_failure"
    assert result["completions"][0]["diagnostic"] == "invalid_pi_prompt_path"
    invocation = result["invocations"][0]
    assert invocation["origin"] == "adapter"
    assert invocation["pid"] is None
    assert invocation["adapter_evidence"] is not None
    assert result["status"] == "paused"
    assert result["planes"]["main"]["pause_reason"] == "execution_error"
    assert result["pause_reason"] == "no_runnable_planes"
    assert result["run_gate"] is None
    assert not marker.exists()


@pytest.mark.parametrize(
    "output,classification",
    [
        ('{"outcome":"OTHER"}', "unknown"),
        ('{"summary":"no outcome"}', "protocol_failure"),
        ("", "protocol_failure"),
    ],
)
def test_pi_core_semantic_channel_after_native_completion(
    single_flow: tuple[Path, Path, Path], output: str, classification: str
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    _fake(workflow.parent, _wire(_events(project, [_assistant([output])])))
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    data["planes"]["main"]["nodes"]["draft"]["on_unknown"] = {"finish": "failed"}
    workflow.write_text(json.dumps(data))
    instance = pi_instance(workflow, project, home, agent)
    run = start_run(instance, state_home=home)
    assert run["status"] == "failed" and run["count"] == 1
    assert run["completions"][0]["transport"] == "completed"
    assert run["completions"][0]["classification"] == classification
    assert run["invocations"][0]["adapter_evidence"]["final"]["text_sha256"]


@pytest.mark.parametrize("mutation", ["missing", "version", "exit_type"])
def test_failed_pi_evidence_remains_required_and_typed(
    single_flow: tuple[Path, Path, Path], mutation: str
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    _fake(
        workflow.parent, _wire(_events(project, [_assistant(["failed"], stop="error")]))
    )
    instance = pi_instance(workflow, project, home, agent)
    result = start_run(instance, state_home=home)
    with sqlite3.connect(store_path(instance, home)) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        if mutation == "missing":
            body["adapter_evidence"] = None
        elif mutation == "version":
            body["adapter_evidence"]["observed_version"] = None
        else:
            db.execute("UPDATE completions SET exit_code='bad'")
        db.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    with pytest.raises(RouterError) as error:
        inspect_run(instance, str(result["id"]), state_home=home)
    assert error.value.code == "completion_integrity"


@pytest.mark.parametrize(
    "mutation",
    [
        "command_launch",
        "command_evidence",
        "copied_evidence",
        "coherent_binding",
        "current_host",
    ],
)
def test_pi_cross_adapter_and_selected_authority(
    single_flow: tuple[Path, Path, Path], mutation: str
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["bindings"]["other"] = dict(graph["bindings"]["pi"])
    graph["planes"]["main"]["nodes"]["prepare"] = copy.deepcopy(
        graph["planes"]["main"]["nodes"]["draft"]
    )
    if mutation != "copied_evidence":
        graph["planes"]["main"]["nodes"]["prepare"]["invoke"] = {
            "binding": "python",
            "config": {"script": "scripts/draft.py", "args": ["good"]},
            "timeout_seconds": 3,
        }
    graph["planes"]["main"]["nodes"]["prepare"]["on"] = {"READY": {"goto": "draft"}}
    graph["planes"]["main"]["start"] = "prepare"
    workflow.write_text(json.dumps(graph))
    instance = pi_instance(workflow, project, home, agent)
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded" and result["count"] == 2
    if mutation == "current_host":
        another = project / "new-agent"
        another.mkdir()
        current = control.inspect_instance(instance, state_home=home)
        receipt = control.update_instance_config(
            instance,
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
            settings={"host_paths": {"agent": str(another)}},
        )
        assert receipt["status"] == "applied"
    with sqlite3.connect(store_path(instance, home)) as db:
        rows = db.execute("SELECT id,body FROM invocations ORDER BY attempt").fetchall()
        first, second = [json.loads(row[1]) for row in rows]
        target = second
        target_id = rows[1][0]
        if mutation == "command_launch":
            first["launch"] = second["launch"]
            target, target_id = first, rows[0][0]
        elif mutation == "command_evidence":
            first["adapter_evidence"] = dict(
                second["adapter_evidence"], invocation_id=first["id"]
            )
            target, target_id = first, rows[0][0]
        elif mutation == "copied_evidence":
            second["adapter_evidence"] = first["adapter_evidence"]
        elif mutation == "coherent_binding":
            second["launch_settings"]["common"]["requested"]["binding"] = "other"
            second["launch_settings"]["common"]["effective"]["binding"] = "other"
        else:
            second["launch"]["details"]["agent_dir"] = str(another)
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(target), target_id)
        )
    for _ in range(2):
        with pytest.raises(RouterError) as error:
            inspect_run(instance, str(result["id"]), state_home=home)
        assert error.value.code in {"invocation_integrity", "completion_integrity"}


@pytest.mark.parametrize("kind", ["interrupted_reserved", "interrupted_active"])
@pytest.mark.parametrize("field", ["stdout", "stderr"])
def test_pi_interruption_null_evidence_requires_empty_bytes(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    field: str,
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 1, "exhausted": {"finish": "failed"}}
    }
    workflow.write_text(json.dumps(graph))
    instance = pi_instance(workflow, project, home, agent)
    ids: dict[str, object] = {}
    method = "active" if kind == "interrupted_active" else "reserve"
    original = getattr(Store, method)

    def crash(store: Store, *args: object, **kwargs: object) -> None:
        original(store, *args, **kwargs)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, method, crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    paused = recover_run(instance, str(ids["run_id"]), state_home=home)
    assert paused["pause_reason"] == "writer_unknown"
    if kind == "interrupted_active":
        assert type(paused["invocations"][0]["pid"]) is int
    assert (project / ".millrace-router-owner/owner.json").exists()
    control.reconcile_invocation(
        instance,
        str(paused["id"]),
        invocation_id=paused["invocations"][0]["id"],
        reason="owned test crashed before dispatch",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    reconciled = inspect_run(instance, str(paused["id"]), state_home=home)
    assert reconciled["completions"][0]["transport"] == "interrupted"
    assert reconciled["invocations"][0]["adapter_evidence"] is None
    assert reconciled["transitions"] == []
    control.resume_run(
        instance,
        str(paused["id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=reconciled["state_version"],
    )
    result = inspect_run(instance, str(paused["id"]), state_home=home)
    assert result["status"] == "succeeded" and result["count"] == 2
    assert not (project / ".millrace-router-owner/owner.json").exists()
    assert inspect_run(instance, str(result["id"]), state_home=home) == result
    invocation = str(result["invocations"][0]["id"])
    with sqlite3.connect(store_path(instance, home)) as db:
        row = db.execute(
            "SELECT stdout,stderr FROM completions WHERE invocation_id=?", (invocation,)
        ).fetchone()
        assert row == (b"", b"")
        db.execute(
            f"UPDATE completions SET {field}=? WHERE invocation_id=?",
            (b"corrupt null-evidence bytes", invocation),
        )
        db.commit()
        before = list(db.iterdump())
        for _ in range(2):
            with pytest.raises(RouterError, match="completion_integrity"):
                inspect_run(instance, str(result["id"]), state_home=home)
            assert list(db.iterdump()) == before


def test_pi_unsafe_prompt_path_has_preflight_evidence(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    home = home.parent / "unsafe&home"
    instance = pi_instance(workflow, project, home, agent)
    result = start_run(instance, state_home=home)
    invocation = result["invocations"][0]
    assert invocation["origin"] == "adapter"
    assert result["completions"][0]["transport"] == "execution_failure"
    evidence = invocation["adapter_evidence"]
    assert evidence is not None
    assert evidence["invocation_id"] == invocation["id"]
    assert evidence["phase"] == "preflight"
    assert evidence["observed_version"] is None
    assert evidence["session_id"] is None
    assert evidence["protocol_state"] == "not_started"
    assert evidence["event_count"] == 0 and evidence["event_bytes"] == 0
    assert evidence["stream_sha256"] is None and evidence["final"] is None
    assert evidence["failure_code"] == "invalid_pi_prompt_path"
    db = sqlite3.connect(store_path(instance, home))
    try:
        stdout, stderr = db.execute("SELECT stdout,stderr FROM completions").fetchone()
    finally:
        db.close()
    assert stdout == b""
    assert isinstance(stderr, bytes)


def test_pi_failure_with_evidence_keeps_diagnostic_stderr(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    _fake(
        workflow.parent,
        _wire(_events(project, [_assistant(["native failure"], stop="error")])),
        after="os.write(2,b'native diagnostic')",
    )
    instance = pi_instance(workflow, project, home, agent)
    result = start_run(instance, state_home=home)
    assert result["completions"][0]["transport"] == "execution_failure"
    assert result["invocations"][0]["adapter_evidence"]["protocol_state"] == "settled"
    with sqlite3.connect(store_path(instance, home)) as db:
        assert db.execute("SELECT stdout,stderr FROM completions").fetchone() == (
            b"",
            b"native diagnostic",
        )
    assert inspect_run(instance, str(result["id"]), state_home=home) == result
