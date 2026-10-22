"""Raw-first, revision and public-control aftermath for the Plus Claude adapter."""

from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from claude_support import ADAPTER_CONTRACT, ADAPTER_ID, envelope, fake, flow
from millrace.router.execution import AdapterResult, RouterError
from millrace.router.store import Store
from plus_support import Crash, create_instance, new_id, start_run, store_path

from millrace_plus.router import control
from millrace_plus.router.claude import ClaudePrintAdapter


def test_resolver_three_exact_pairs() -> None:
    assert isinstance(
        control.resolve_adapter(ADAPTER_ID, ADAPTER_CONTRACT), ClaudePrintAdapter
    )
    for pair in ((ADAPTER_ID, "wrong"), ("unknown", ADAPTER_CONTRACT)):
        with pytest.raises(RouterError):
            control.resolve_adapter(*pair)
    assert (
        control.resolve_adapter("pi_json", "millrace-pi-json/v1").adapter_id
        == "pi_json"
    )
    assert (
        control.resolve_adapter("codex_exec", "millrace-codex-exec/v1").adapter_id
        == "codex_exec"
    )


@pytest.mark.parametrize(
    "semantic,status,classification",
    [
        ({"outcome": "READY", "summary": "done", "refs": []}, "succeeded", "semantic"),
        ({"outcome": "OTHER"}, "failed", "unknown"),
        ({"summary": "missing"}, "failed", "protocol_failure"),
        ({"outcome": "READY", "refs": ["../escape"]}, "failed", "protocol_failure"),
        ({"outcome": "READY", "extra": 1}, "failed", "protocol_failure"),
    ],
)
def test_raw_first_completion_and_corruption(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    semantic: dict[str, Any],
    status: str,
    classification: str,
) -> None:
    workflow, project, home = flow(
        single_flow, wire=json.dumps(envelope(structured_output=semantic)).encode()
    )
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    ids = {}
    original = Store.complete

    def crash(store: Store, *args: Any, **kwargs: Any) -> None:
        original(store, *args, **kwargs)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    path = store_path(instance, home)
    raw = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT stdout,transport,classification FROM completions"
        ).fetchone() == (raw, "completed", None)
        assert db.execute("SELECT count(*) FROM transitions").fetchone()[0] == 0
    run = control.inspect_run(instance, str(ids["run_id"]), state_home=home)
    receipt = control.recover_run(
        instance,
        str(run["id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=run["state_version"],
    )
    assert receipt["status"] == "applied"
    recovered = control.inspect_run(instance, str(run["id"]), state_home=home)
    assert (
        recovered["status"] == status
        and recovered["completions"][0]["classification"] == classification
    )
    assert len(recovered["transitions"]) == 1
    # Historical reload uses captured inputs, not the executable or environment.
    for name in ("fake-claude", "fake-claude.json", "claude.json", "task.txt"):
        (workflow.parent / name).unlink()
    with monkeypatch.context() as patch:
        patch.setattr(
            subprocess, "Popen", lambda *a, **k: pytest.fail("reload spawned process")
        )
        cold = control.inspect_run(instance, str(run["id"]), state_home=home)
        for key in ("completions", "invocations", "transitions"):
            assert cold[key] == recovered[key]
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT id,body FROM invocations").fetchone()
        body = json.loads(row[1])
        body["adapter_evidence"]["semantic_sha256"] = "0" * 64
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(body), row[0])
        )
        before = list(db.iterdump())
    with pytest.raises(RouterError):
        control.inspect_run(instance, str(run["id"]), state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


@pytest.mark.parametrize("mutation", ["argv", "settings", "evidence"])
def test_producing_launch_authority_refuses(
    single_flow: tuple[Path, Path, Path], mutation: str
) -> None:
    workflow, project, home = flow(single_flow)
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    with sqlite3.connect(store_path(instance, home)) as db:
        row = db.execute("SELECT id,body FROM invocations").fetchone()
        body = json.loads(row[1])
        if mutation == "argv":
            body["launch"]["argv"].append("--model")
        if mutation == "settings":
            body["launch"]["settings"]["effective"]["model"] = "forged"
        if mutation == "evidence":
            body["adapter_evidence"]["invocation_id"] = "copied"
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(body), row[0])
        )
    with pytest.raises(RouterError):
        control.inspect_run(instance, str(run["id"]), state_home=home)


@pytest.mark.parametrize("mode", ["cancel", "locked", "live"])
def test_controls_recovery_and_revision_authority(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    if mode != "cancel":
        _revision_case(single_flow, monkeypatch, mode)
        return
    workflow, project, home = flow(single_flow, before="time.sleep(10)")
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    ids = {}
    cancel_id = new_id()
    original = ClaudePrintAdapter.invoke

    def instrument(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
        callback = kwargs["on_started"]

        def started(pid: int) -> None:
            callback(pid)
            assert (
                control.cancel_run(
                    instance, str(ids["run_id"]), state_home=home, request_id=cancel_id
                )["status"]
                == "queued"
            )

        return original(self, *args, **{**kwargs, "on_started": started})

    with monkeypatch.context() as patch:
        patch.setattr(ClaudePrintAdapter, "invoke", instrument)
        cancelled = start_run(instance, state_home=home, on_started=ids.update)
    assert cancelled["completions"][0]["transport"] == "cancelled"
    current = control.inspect_run(instance, str(ids["run_id"]), state_home=home)
    target = current["invocations"][0]
    if target["writer_state"] != "stopped":
        receipt = control.reconcile_invocation(
            instance,
            str(current["id"]),
            invocation_id=target["id"],
            stopped=True,
            reason="owned fake root/group stopped",
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
            plane_id="main",
        )
        assert receipt["status"] == "applied"
    current = control.inspect_run(instance, str(current["id"]), state_home=home)
    assert current["status"] == "cancelled"
    assert (
        control.cancel_run(
            instance, str(current["id"]), state_home=home, request_id=cancel_id
        )["status"]
        == "applied"
    )
    fake(workflow.parent)
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded" and fresh["id"] != current["id"]
    assert (
        control.inspect_run(instance, str(current["id"]), state_home=home)[
            "completions"
        ]
        == current["completions"]
    )


def _revision_case(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    workflow, project, home = flow(single_flow)
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    original = ClaudePrintAdapter.invoke

    def edit_after(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
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
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ClaudePrintAdapter, "invoke", edit_after)
        result = start_run(instance, state_home=home, edit_mode=mode)
    assert result["invocations"][0]["launch_settings"]["adapter"]["effective"] == {}
    assert result["completions"][0]["transport"] == "completed"
    if mode == "locked":
        assert result["pause_reason"] == "locked_edit" and result["transitions"] == []
        accepted = control.accept_revision(
            instance,
            str(result["id"]),
            candidate_id=result["pending_revision"]["candidate_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
        )
        assert accepted["status"] == "applied"
    final = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 2
    assert final["invocations"][0]["revision_id"] != final["revision_id"]
    assert final["invocations"][1]["launch"]["adapter_id"] == "command"
    assert final["transitions"][0]["revision_id"] == final["revision_id"]
