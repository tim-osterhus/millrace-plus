"""Public loader and aftermath proofs over the migrated native contracts."""

from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
from dataclasses import replace

import pytest
from codex_support import codex_flow, codex_instance
from millrace.router.execution import RouterError
from opencode_support import fake as opencode_fake
from opencode_support import flow as opencode_flow
from opencode_support import request as opencode_request
from plus_support import create_instance, new_id, start_run, store_path
from test_codex_configuration import request as codex_request

from millrace_plus.router import codex, control, opencode
from millrace_plus.router.setting_layers import merge_setting_layers


def completed(single_flow, native):
    if native == "codex":
        workflow, project, home, native_home = codex_flow(single_flow)
    else:
        workflow, project, home = opencode_flow(single_flow)
    graph = json.loads(workflow.read_text())
    model = "fixture/selected" if native == "opencode" else "selected"
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["config"]["model"] = model
    workflow.write_text(json.dumps(graph))
    instance = (
        codex_instance(workflow, project, home, native_home)
        if native == "codex" else
        str(create_instance(workflow, project, state_home=home)["instance_id"])
    )
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded", result
    return instance, result, home, project


def dump(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


@pytest.mark.parametrize("native", ["codex", "opencode"])
def test_settings_preservation_and_capture_authority(tmp_path, native) -> None:
    req = codex_request(tmp_path) if native == "codex" else opencode_request(tmp_path)
    adapter = (
        codex.CodexExecAdapter() if native == "codex" else opencode.OpenCodeRunAdapter()
    )
    prefix = "fixture/" if native == "opencode" else ""
    request = replace(
        req, runtime={"model": prefix + "runtime"}, plane={"model": prefix + "plane"},
        node_config={**req.node_config, "model": prefix + "node"},
    )
    original = adapter.plan(request)
    assert original.settings["effective"]["model"] == prefix + "node"
    assert original.settings["provenance"]["model"] == {
        "source": "node", "policy": None,
    }
    changed = adapter.plan(replace(
        request, node_config={**req.node_config, "model": prefix + "next"},
    ))
    assert changed.settings["effective"]["model"] == prefix + "next"
    assert original.settings["effective"]["model"] == prefix + "node"
    # The generic merge is caller-ordered; conflict/requiredness stays native.
    assert merge_setting_layers({"model"}, ("fallback", {}))["effective"] == {}
    bad = replace(request, plane={"model": 0})
    with pytest.raises(RouterError):
        adapter.plan(bad)


@pytest.mark.parametrize("native", ["codex", "opencode"])
@pytest.mark.parametrize(
    "mutation", ["native_identity", "final_digest", "exit", "origin"]
)
def test_native_finality_and_completion_corruption(
    single_flow, monkeypatch, native, mutation,
):
    instance, result, home, _ = completed(single_flow, native)
    path = store_path(instance, home)
    with sqlite3.connect(path) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        evidence = body["adapter_evidence"]
        if mutation == "native_identity":
            evidence["thread_id" if native == "codex" else "session_id"] = ""
        elif mutation == "final_digest":
            evidence["final"]["text_sha256"] = "0" * 64
        elif mutation == "exit":
            db.execute("UPDATE completions SET exit_code=1")
        else:
            body["origin"] = "core_interruption"
        db.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    frozen = dump(path)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("reload native probe")
    )
    for _ in range(2):
        with pytest.raises(RouterError) as caught:
            control.inspect_run(instance, str(result["id"]), state_home=home)
        assert caught.value.code == "completion_integrity"
        assert dump(path) == frozen


@pytest.mark.parametrize("native", ["codex", "opencode"])
@pytest.mark.parametrize(
    "mutation",
    ["invocation", "plane", "node", "binding", "revision", "workspace",
     "adapter", "contract", "control_digest", "provenance", "coherent_wrong_plan"],
)
def test_public_store_authority_and_legal_aftermath(single_flow, native, mutation):
    instance, result, home, project = completed(single_flow, native)
    path = store_path(instance, home)
    with sqlite3.connect(path) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        launch = body["launch"]
        if mutation in {"invocation", "plane", "node", "revision", "workspace"}:
            key, value = {
                "invocation": ("id", "f" * 32),
                "plane": ("plane_id", "foreign"),
                "node": ("node", "foreign"),
                "revision": ("revision_id", "f" * 64),
                "workspace": ("cwd", str(project / "foreign")),
            }[mutation]
            body[key] = value
        elif mutation == "binding":
            launch["binding_id"] = "foreign"
        elif mutation == "adapter":
            launch["adapter_id"] = "pi_json"
        elif mutation == "contract":
            launch["adapter_contract"] = "unsupported/v1"
        elif mutation == "control_digest":
            next(iter(launch["generated_controls"].values()))["sha256"] = "0" * 64
        elif mutation == "provenance":
            launch["settings"]["provenance"]["model"]["source"] = "runtime"
        else:
            # Agreement among forged projections is not selected graph authority.
            foreign = "fixture/foreign" if native == "opencode" else "foreign"
            launch["settings"]["effective"]["model"] = foreign
            launch["settings"]["requested"]["model"] = foreign
            body["launch_settings"]["adapter"] = copy.deepcopy(launch["settings"])
            argv = list(launch["argv"])
            argv[argv.index("--model") + 1] = foreign
            launch["argv"] = argv
            body["argv"] = argv.copy()
        db.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    frozen = dump(path)
    with pytest.raises(RouterError) as caught:
        control.inspect_run(instance, str(result["id"]), state_home=home)
    assert caught.value.code == "invocation_integrity"
    assert dump(path) == frozen


@pytest.mark.parametrize("native", ["codex", "opencode"])
def test_historical_reload_does_not_query_process_capability(
    single_flow, monkeypatch, native,
):
    instance, result, home, _ = completed(single_flow, native)
    path = store_path(instance, home)
    before = dump(path)

    def forbidden(*args, **kwargs):
        pytest.fail("historical read queried live process capability or spawned")

    monkeypatch.setattr(codex, "require_owned_process", forbidden)
    monkeypatch.setattr(opencode, "require_owned_process", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    reloaded = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert reloaded["completions"] == result["completions"]
    assert dump(path) == before


def test_hold_reconcile_then_fresh_attempt(single_flow) -> None:
    workflow, project, home = opencode_flow(single_flow, before="time.sleep(30)")
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 0.5
    workflow.write_text(json.dumps(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    failed = start_run(instance, state_home=home)
    run_id = str(failed["id"])
    invocation = failed["invocations"][0]
    assert failed["count"] == 1 and failed["pause_reason"] == "writer_unknown"
    assert failed["completions"][0]["transport"] == "timeout"
    raw = {k: v for k, v in failed["completions"][0].items() if k != "effective_writer"}
    receipt = control.reconcile_invocation(
        instance, run_id, invocation_id=str(invocation["id"]), plane_id="main",
        reason="test-owned fake root and group closed", stopped=True, state_home=home,
        request_id=new_id(), expected_state_version=failed["state_version"],
    )
    assert receipt["status"] == "applied"
    current = control.inspect_run(instance, run_id, state_home=home)
    assert current["transitions"] == [] and current["count"] == 1
    current_raw = {
        k: v for k, v in current["completions"][0].items() if k != "effective_writer"
    }
    assert current_raw == raw
    cancelled = control.cancel_run(
        instance, run_id, state_home=home, request_id=new_id(),
    )
    assert cancelled["status"] == "applied"
    opencode_fake(workflow.parent)
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded" and fresh["count"] == 1
    assert fresh["invocations"][0]["id"] != invocation["id"]
    later = control.inspect_run(instance, run_id, state_home=home)
    later_raw = {
        k: v for k, v in later["completions"][0].items() if k != "effective_writer"
    }
    assert later_raw == raw


@pytest.mark.parametrize("native", ["codex", "opencode"])
def test_run_deadline_before_native_cleanup_error_keeps_limit_gate(single_flow, native):
    if native == "codex":
        from codex_support import events, wire

        late = wire(events()[:2] + [{
            "type": "turn.failed", "error": {"message": "cleanup failed"},
        }])
    else:
        late = (json.dumps({
            "type": "error", "timestamp": 3, "sessionID": "ses-test",
            "error": {"name": "APIError"},
        }) + "\n").encode()
    before = (
        "def term(*_):\n"
        f" os.write(1,{late!r});sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,term)\n"
        "time.sleep(30)\n"
    )
    if native == "codex":
        workflow, project, home, native_home = codex_flow(single_flow, before=before)
    else:
        workflow, project, home = opencode_flow(single_flow, before=before)
    graph = json.loads(workflow.read_text())
    graph["limits"]["max_invocations"] = 3
    graph["limits"]["max_elapsed_seconds"] = 1
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 5
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {
        "retry": {"limit": 2, "exhausted": {"finish": "failed"}},
    }
    workflow.write_text(json.dumps(graph))
    instance = (
        codex_instance(workflow, project, home, native_home)
        if native == "codex" else
        str(create_instance(workflow, project, state_home=home)["instance_id"])
    )
    result = start_run(instance, state_home=home)
    assert result["count"] == 1 and result["pause_reason"] == "writer_unknown"
    assert result["transitions"] == []
    completion = result["completions"][0]
    assert completion["transport"] == "timeout" and completion["stdout"] == ""
    assert completion["diagnostic"] == native + "_timeout"
    assert completion["writer"] == "may_live"
    assert result["invocations"][0]["adapter_evidence"]["event_bytes"] == len(late)
    with sqlite3.connect(store_path(instance, home)) as db:
        gates = [
            json.loads(body)["reason"] for (body,) in db.execute(
                "SELECT body FROM run_gate_events WHERE run_id=? AND kind='set' "
                "ORDER BY version,ordinal", (result["id"],),
            )
        ]
    # The limit gate is durable; a truthful writer hold has public precedence.
    assert gates == ["limit_exhausted", "writer_unknown"]
    reloaded = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert reloaded["pause_reason"] == "writer_unknown"
    assert reloaded["transitions"] == [] and reloaded["completions"] == [completion]
    run_id = str(result["id"])
    invocation = result["invocations"][0]
    raw = {key: value for key, value in completion.items() if key != "effective_writer"}
    evidence = copy.deepcopy(invocation["adapter_evidence"])
    with sqlite3.connect(store_path(instance, home)) as db:
        history = db.execute(
            "SELECT * FROM completions WHERE run_id=?", (run_id,),
        ).fetchall()
    reconciled = control.reconcile_invocation(
        instance, run_id, invocation_id=str(invocation["id"]), plane_id="main",
        reason="test-owned root and group reaped", stopped=True, state_home=home,
        request_id=new_id(), expected_state_version=reloaded["state_version"],
    )
    assert reconciled["status"] == "applied"
    current = control.inspect_run(instance, run_id, state_home=home)
    assert current["completions"][0]["effective_writer"] == "stopped"
    assert current["transitions"] == [] and current["count"] == 1
    resumed = control.resume_run(
        instance, run_id, state_home=home, request_id=new_id(),
        expected_state_version=current["state_version"],
    )
    assert resumed["status"] == "applied"
    after = control.inspect_run(instance, run_id, state_home=home)
    assert after["pause_reason"] == "limit_exhausted"
    assert after["transitions"] == [] and after["retry_counts"]["main"] == {}
    assert after["count"] == 1 and len(after["invocations"]) == 1
    assert after["count"] < after["cap"]  # Elapsed expiry, not count exhaustion.
    assert after["invocations"][0]["id"] == invocation["id"]
    assert after["invocations"][0]["adapter_evidence"] == evidence
    assert {
        key: value for key, value in after["completions"][0].items()
        if key != "effective_writer"
    } == raw
    with sqlite3.connect(store_path(instance, home)) as db:
        assert db.execute(
            "SELECT * FROM completions WHERE run_id=?", (run_id,),
        ).fetchall() == history
    assert control.inspect_run(instance, run_id, state_home=home) == after
