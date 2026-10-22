"""Codex completion durability, selected authority and revision boundaries.

All peers and SQLite mutations belong to fresh pytest fixtures. Native result
bytes reach Core's parser after raw persistence; corruption probes never repair
or reopen an actual runtime workspace.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from codex_support import (
    ADAPTER_CONTRACT,
    ADAPTER_ID,
    FINAL,
    codex_flow,
    codex_instance,
)
from millrace.router.execution import AdapterResult, RouterError
from millrace.router.store import Store
from plus_support import Crash, new_id, start_run, store_path

from millrace_plus.router import control


def _inspect(instance: str, run: str, home: Path) -> dict[str, Any]:
    return control.inspect_run(instance, run, state_home=home)


def _dump(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def _open_store(instance: str, home: Path) -> Store:
    return Store(
        control.load_instance(instance, home),
        adapter_resolver=control.resolve_adapter,
    )


def test_historical_codex_evidence_refuses_without_store_mutation(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home, native_home = codex_flow(single_flow)
    instance = codex_instance(workflow, project, home, native_home)
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    path = store_path(instance, home)
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT id,body FROM invocations").fetchone()
        body = json.loads(row[1])
        body["adapter_evidence"]["observed_version"] = "0.153.2"
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(body), row[0])
        )
    historical = _dump(path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("historical inspection spawned a native process")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", forbidden)
        for _ in range(2):
            with pytest.raises(RouterError) as error:
                _inspect(instance, str(run["id"]), home)
            assert error.value.code in {"completion_integrity", "invocation_integrity"}
            assert _dump(path) == historical


@pytest.mark.parametrize(
    "raw,classification,status",
    [
        pytest.param(FINAL, "semantic", "succeeded", id="valid"),
        pytest.param(b'{"outcome":"OTHER"}', "unknown", "failed", id="unknown"),
        pytest.param(
            b'{"summary":"missing"}', "protocol_failure", "failed", id="missing"
        ),
        pytest.param(
            b'{"outcome":"READY","outcome":"OTHER"}',
            "protocol_failure",
            "failed",
            id="duplicate",
        ),
        pytest.param(
            b'{"outcome":"READY","extra":1}', "protocol_failure", "failed", id="extra"
        ),
        pytest.param(
            b'{"outcome":"READY","refs":["../escape"]}',
            "protocol_failure",
            "failed",
            id="unsafe_ref",
        ),
    ],
)
def test_raw_first_semantics_and_unknown_fallback(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    classification: str,
    status: str,
) -> None:
    workflow, project, home, native_home = codex_flow(single_flow, final=raw)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    graph["planes"]["main"]["nodes"]["draft"]["on_unknown"] = {"finish": "failed"}
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native_home)
    ids: dict[str, object] = {}
    original = Store.complete

    def crash_after_commit(store: Store, *args: Any, **kwargs: Any) -> None:
        original(store, *args, **kwargs)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", crash_after_commit)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    path = store_path(instance, home)
    with sqlite3.connect(path) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        completion = db.execute(
            "SELECT stdout,transport,classification FROM completions"
        ).fetchone()
        assert completion == (raw, "completed", None)
        assert body["ref_admission"] is None
        assert (
            body["adapter_evidence"]["final"]["text_sha256"]
            == hashlib.sha256(raw).hexdigest()
        )
        assert db.execute("SELECT count(*) FROM transitions").fetchone()[0] == 0
    run_id = str(ids["run_id"])
    before = _inspect(instance, run_id, home)
    request_id = new_id()
    receipt = control.recover_run(
        instance,
        run_id,
        state_home=home,
        request_id=request_id,
        expected_state_version=before["state_version"],
    )
    assert receipt["status"] == "applied", receipt
    recovered = _inspect(instance, run_id, home)
    assert (recovered["status"], recovered["count"]) == (status, 1)
    assert recovered["completions"][0]["classification"] == classification
    assert len(recovered["transitions"]) == 1
    stable = _dump(path)
    assert (
        control.recover_run(
            instance,
            run_id,
            state_home=home,
            request_id=request_id,
            expected_state_version=before["state_version"],
        )
        == receipt
    )
    assert _dump(path) == stable
    # Reload cannot depend on current native credentials, controls or executable.
    (native_home / "auth.json").unlink()
    native_home.rmdir()
    for name in ("task.txt", "codex.json", "fake-codex", "fake-codex.json"):
        (workflow.parent / name).unlink()
    cold = _inspect(instance, run_id, home)
    assert cold["completions"] == recovered["completions"]
    assert cold["invocations"] == recovered["invocations"]
    assert cold["transitions"] == recovered["transitions"]
    assert _dump(path) == stable


@pytest.mark.parametrize(
    "mutation",
    [
        "identical",
        "conflicting_stdout",
        "conflicting_stderr",
        "conflicting_writer",
        "conflicting_transport",
        "conflicting_diagnostic",
        "conflicting_exit",
        "copied_invocation",
        "missing_evidence",
        "wrong_phase",
        "wrong_version",
        "wrong_state",
        "event_count_bool",
        "negative_event_bytes",
        "event_bytes_over_profile_limit",
        "event_count_exceeds_bytes",
        "final_hash",
        "stream_hash",
        "completed_pid_none",
        "invocation_probe_pid_none",
        "returned_invalid_evidence",
    ],
)
def test_completion_evidence_integrity_and_idempotence(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    workflow, project, home, native_home = codex_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native_home)
    if mutation == "returned_invalid_evidence":
        adapter = control.resolve_adapter(ADAPTER_ID, ADAPTER_CONTRACT)
        original = type(adapter).invoke

        def invalid(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
            result = original(self, *args, **kwargs)
            evidence = copy.deepcopy(result.adapter_evidence)
            assert evidence is not None
            evidence["event_count"] = True
            return replace(result, adapter_evidence=evidence)

        with monkeypatch.context() as patch:
            patch.setattr(type(adapter), "invoke", invalid)
            paused = start_run(instance, state_home=home)
        invocation = paused["invocations"][0]
        assert paused["pause_reason"] == "writer_unknown"
        assert paused["count"] == 1 and paused["completions"] == []
        assert invocation["uncertainty_diagnostic"] == "invalid_adapter_result"
        assert invocation["adapter_evidence"] is None
        reconciled = control.reconcile_invocation(
            instance,
            str(paused["id"]),
            invocation_id=str(invocation["id"]),
            stopped=True,
            reason="owned fake process already reaped",
            state_home=home,
            request_id=new_id(),
            expected_state_version=paused["state_version"],
            plane_id="main",
        )
        assert reconciled["status"] == "applied", reconciled
        current = _inspect(instance, str(paused["id"]), home)
        assert current["transitions"] == []
        assert current["completions"][0]["transport"] == "interrupted"
        assert current["completions"][0]["stdout"] == ""
        resumed = control.resume_run(
            instance,
            str(paused["id"]),
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
        )
        assert resumed["status"] == "applied", resumed
        terminal = _inspect(instance, str(paused["id"]), home)
        assert terminal["status"] == "failed" and terminal["count"] == 1
        assert len(terminal["transitions"]) == 1
        fresh = start_run(instance, state_home=home)
        assert fresh["status"] == "succeeded" and fresh["count"] == 1
        return

    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    invocation = run["invocations"][0]
    path = store_path(instance, home)
    store = _open_store(instance, home)
    try:
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
        stable = list(store.db.iterdump())
        store.complete(str(invocation["id"]), exact)
        store.complete(str(invocation["id"]), exact)
        assert list(store.db.iterdump()) == stable
        if mutation.startswith("conflicting_"):
            key, value = {
                "conflicting_stdout": ("stdout", b'{"outcome":"OTHER"}'),
                "conflicting_stderr": ("stderr", b"changed"),
                "conflicting_writer": ("writer_state", "may_live"),
                "conflicting_transport": ("classification", "execution_failure"),
                "conflicting_diagnostic": ("diagnostic", "changed"),
                "conflicting_exit": ("exit_code", 1),
            }[mutation]
            with pytest.raises(RouterError, match="conflicting_completion"):
                store.complete(str(invocation["id"]), replace(exact, **{key: value}))
            assert list(store.db.iterdump()) == stable
            return
        if mutation == "identical":
            store.validate()
            assert len(_inspect(instance, str(run["id"]), home)["transitions"]) == 1
            return
    finally:
        store.close()

    with sqlite3.connect(path) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        evidence = body["adapter_evidence"]
        if mutation == "copied_invocation":
            evidence["invocation_id"] = "f" * 32
        elif mutation == "missing_evidence":
            body["adapter_evidence"] = None
        elif mutation == "wrong_phase":
            evidence["phase"] = "preflight"
        elif mutation == "wrong_version":
            evidence["observed_version"] = "0.153.1"
        elif mutation == "wrong_state":
            evidence["protocol_state"] = "incomplete"
        elif mutation == "event_count_bool":
            evidence["event_count"] = True
        elif mutation == "negative_event_bytes":
            evidence["event_bytes"] = -1
        elif mutation == "event_bytes_over_profile_limit":
            evidence["event_bytes"] = 16_777_217
        elif mutation == "event_count_exceeds_bytes":
            evidence["event_count"] = evidence["event_bytes"] + 1
        elif mutation == "final_hash":
            evidence["final"]["text_sha256"] = "0" * 64
        elif mutation == "stream_hash":
            evidence["stream_sha256"] = "invalid"
        elif mutation == "completed_pid_none":
            body["pid"] = None
        elif mutation == "invocation_probe_pid_none":
            evidence["probe_pid"] = None
        else:
            raise AssertionError(mutation)
        db.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    corrupt = _dump(path)
    for _ in range(2):
        with pytest.raises(RouterError) as error:
            _inspect(instance, str(run["id"]), home)
        assert error.value.code in {"completion_integrity", "invocation_integrity"}
        assert _dump(path) == corrupt


@pytest.mark.parametrize(
    "mutation",
    [
        "preflight_pin_without_probe_pid",
        "bytes_without_stream_hash",
        "stream_hash_without_bytes",
        "settled_without_thread",
    ],
)
def test_failed_completion_evidence_consistency(
    single_flow: tuple[Path, Path, Path], mutation: str
) -> None:
    """Failure facts stay coherent without claiming provider attestation."""
    workflow, project, home, native_home = codex_flow(
        single_flow,
        version="0.0.0" if mutation == "preflight_pin_without_probe_pid" else "0.163.0",
        exit_code=1,
        final=None,
    )
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native_home)
    run = start_run(instance, state_home=home)
    assert run["status"] == "failed" and run["count"] == 1
    path = store_path(instance, home)
    with sqlite3.connect(path) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        evidence = body["adapter_evidence"]
        if mutation == "preflight_pin_without_probe_pid":
            evidence["observed_version"] = "0.163.0"
            evidence["probe_pid"] = None
        elif mutation == "bytes_without_stream_hash":
            assert evidence["event_bytes"] > 0
            evidence["stream_sha256"] = None
        elif mutation == "stream_hash_without_bytes":
            assert evidence["stream_sha256"] is not None
            evidence.update(
                event_bytes=0,
                event_count=0,
                error_count=0,
                thread_id=None,
                protocol_state="incomplete",
            )
        else:
            assert evidence["protocol_state"] == "settled"
            evidence["thread_id"] = None
        db.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    corrupted = _dump(path)
    for _ in range(2):
        with pytest.raises(RouterError) as error:
            _inspect(instance, str(run["id"]), home)
        assert error.value.code == "completion_integrity"
        assert _dump(path) == corrupted


@pytest.mark.parametrize(
    "mutation",
    [
        "producing_revision",
        "binding",
        "adapter",
        "contract",
        "argv",
        "generated_hash",
        "host_locator",
        "shadowed_setting",
    ],
)
def test_selected_launch_authority_corruption_refuses(
    single_flow: tuple[Path, Path, Path], mutation: str
) -> None:
    workflow, project, home, native_home = codex_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["bindings"]["other"] = copy.deepcopy(graph["bindings"]["codex"])
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native_home)
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    # A supported current-host update does not reinterpret the completed run.
    next_home = project / "next-codex-home"
    next_home.mkdir()
    (next_home / "auth.json").write_text("{}")
    current = control.inspect_instance(instance, state_home=home)
    receipt = control.update_instance_config(
        instance,
        settings={"host_paths": {"codex_home": str(next_home)}},
        state_home=home,
        request_id=new_id(),
        expected_state_version=current["state_version"],
    )
    assert receipt["status"] == "applied", receipt
    assert _inspect(instance, str(run["id"]), home)["invocations"] == run["invocations"]
    path = store_path(instance, home)
    with sqlite3.connect(path) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        launch = body["launch"]
        if mutation == "producing_revision":
            body["revision_id"] = "f" * 64
        elif mutation == "binding":
            body["launch_settings"]["common"]["requested"]["binding"] = "other"
            body["launch_settings"]["common"]["effective"]["binding"] = "other"
        elif mutation == "adapter":
            launch["adapter_id"] = "pi_json"
        elif mutation == "contract":
            launch["adapter_contract"] = "millrace-codex-exec/v2"
        elif mutation == "argv":
            body["argv"][-1] = "invented prompt"
            launch["argv"][-1] = "invented prompt"
        elif mutation == "generated_hash":
            launch["generated_controls"]["codex-result-schema.json"]["sha256"] = (
                "0" * 64
            )
        elif mutation == "host_locator":
            launch["details"]["codex_home"] = str(next_home)
            launch["environment"]["CODEX_HOME"] = str(next_home)
        elif mutation == "shadowed_setting":
            row = db.execute("SELECT id,body FROM runs").fetchone()
            run_body = json.loads(row[1])
            run_body["host_settings"]["runtime_defaults"] = {
                "adapter_settings": {
                    ADAPTER_ID: {
                        "contract": ADAPTER_CONTRACT,
                        "config": {"model": "shadowed-model"},
                    }
                }
            }
            db.execute(
                "UPDATE runs SET body=? WHERE id=?", (json.dumps(run_body), row[0])
            )
        db.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    corrupt = _dump(path)
    for _ in range(2):
        with pytest.raises(RouterError) as error:
            _inspect(instance, str(run["id"]), home)
        assert error.value.code == (
            "run_integrity"
            if mutation == "shadowed_setting"
            else "invocation_integrity"
        )
        assert _dump(path) == corrupt


@pytest.mark.parametrize("mode", ["live", "locked"])
def test_live_and_locked_edits_keep_producing_contract(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    workflow, project, home, native_home = codex_flow(single_flow)
    instance = codex_instance(workflow, project, home, native_home)
    adapter = control.resolve_adapter(ADAPTER_ID, ADAPTER_CONTRACT)
    original = type(adapter).invoke

    def edit_after_native(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
        result = original(self, *args, **kwargs)
        graph = json.loads(workflow.read_text())
        node = graph["planes"]["main"]["nodes"]["draft"]
        node["invoke"] = {
            "binding": "python",
            "config": {"script": "scripts/draft.py", "args": ["good"]},
            "timeout_seconds": 3,
        }
        graph["planes"]["main"]["nodes"]["consumer"] = copy.deepcopy(node)
        node["on"] = {"READY": {"goto": "consumer"}}
        graph["planes"]["main"]["nodes"]["consumer"]["on"] = {
            "READY": {"finish": "succeeded"}
        }
        workflow.write_text(json.dumps(graph))
        profile = json.loads((workflow.parent / "codex.json").read_text())
        profile["model"] = "later-model"
        (workflow.parent / "codex.json").write_text(json.dumps(profile))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(type(adapter), "invoke", edit_after_native)
        result = start_run(instance, state_home=home, edit_mode=mode)
    producing = result["invocations"][0]
    assert producing["launch"]["adapter_id"] == ADAPTER_ID
    assert (
        producing["launch_settings"]["adapter"]["effective"]["model"] == "exact-model"
    )
    assert result["completions"][0]["transport"] == "completed"
    if mode == "locked":
        assert result["pause_reason"] == "locked_edit"
        assert result["count"] == 1 and result["transitions"] == []
        pending = result["pending_revision"]
        assert pending is not None
        accepted = control.accept_revision(
            instance,
            str(result["id"]),
            candidate_id=pending["candidate_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
        )
        assert accepted["status"] == "applied", accepted
    final = _inspect(instance, str(result["id"]), home)
    assert final["status"] == "succeeded" and final["count"] == 2
    assert final["invocations"][0]["revision_id"] != final["revision_id"]
    assert final["invocations"][1]["launch"]["adapter_id"] == "command"
    assert final["transitions"][0]["revision_id"] == final["revision_id"]
    assert final["completions"][0]["stdout"] == FINAL.decode()
    assert len(final["transitions"]) == 2
    assert _inspect(instance, str(result["id"]), home) == final
