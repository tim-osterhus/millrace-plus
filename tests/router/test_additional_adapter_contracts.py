"""One Droid slice: captured authority, public routing and legal recovery."""

from __future__ import annotations

import base64
import copy
import json
import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from millrace.router.execution import (
    AdapterConfiguration,
    CompletionFacts,
    InvocationRequest,
    RouterError,
)
from plus_support import create_instance, new_id, start_run, store_path

from millrace_plus.router import control
from millrace_plus.router import droid_configuration as c
from millrace_plus.router.droid import DroidExecAdapter

FINAL = b'{"outcome":"READY","summary":"done","refs":[]}'
PROFILE = {
    "format": "millrace-droid-profile/v1", "droid_version": "0.247.0",
    "permission_mode": "read_only",
}
PEER = Path(__file__).parent / "fixtures/droid_peer.py"


def envelope(**changes):
    return {
        "type": "result", "subtype": "success", "is_error": False,
        "duration_ms": 1.25, "num_turns": 1, "result": FINAL.decode(),
        "session_id": "session-ordinary-fake",
        "usage": {"input_tokens": 1, "output_tokens": 2,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                  "factory_credits": 0.5},
        **changes,
    }


def fake(root, *, raw=None, version=b"0.247.0\n", **options):
    root.mkdir(parents=True, exist_ok=True)
    target = root / "fake-droid"
    target.write_text(f"#!{sys.executable}\n" + PEER.read_text())
    target.chmod(0o700)
    Path(str(target) + ".json").write_text(json.dumps({
        "wire": base64.b64encode(
            json.dumps(envelope()).encode() if raw is None else raw
        ).decode(),
        "version": base64.b64encode(version).decode(), **options,
        "fresh_session": raw is None,
    }))
    return target


def request(root, *, executable="/absent/droid", prompt=b"Review the project."):
    return InvocationRequest(
        "1" * 32, "main", "draft", "revision", "native", "droid_exec",
        "millrace-droid-exec/v1", executable, {"profile": "droid.json"},
        {"prompt": "task.txt"}, {}, {}, {}, {},
        {"droid.json": json.dumps(PROFILE).encode(), "task.txt": prompt},
        {}, (), ("READY",), "json-v1", root, root / "invocation",
    )


def configuration(req):
    return AdapterConfiguration(
        req.adapter_id, req.adapter_contract, {"native": req.binding_config},
        {("main", "draft"): ("native", req.node_config)}, req.runtime,
        req.workflow, {"main": req.plane}, req.files, req.host_paths,
        req.environment_names,
    )


def prepared(root, **options):
    req = request(root, executable=str(fake(root, **options)))
    plan = c.plan(req)
    req.invocation_root.mkdir(exist_ok=True)
    for name, raw in plan.generated_controls.items():
        (req.invocation_root / name).write_bytes(raw)
    return plan


def facts(plan, result, pid):
    return CompletionFacts(
        plan.invocation_id, pid, result.stdout, result.stderr,
        result.classification, result.writer_state, result.diagnostic,
        result.exit_code, result.adapter_evidence, "adapter",
    )


def invoke(plan, root, *, timeout=3, poll=lambda: False, started=lambda pid: None):
    pids = []

    def on_started(pid):
        pids.append(pid)
        started(pid)

    result = DroidExecAdapter().invoke(
        plan, cwd=root, env={"PATH": "/usr/bin:/bin"},
        deadline=time.time() + timeout, poll=poll, on_started=on_started,
    )
    c.validate_completion(plan, facts(plan, result, pids[0] if pids else None))
    for pid in [*pids, result.adapter_evidence.get("probe_pid")]:
        if pid is not None:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    return result


def droid_flow(single_flow, **options):
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["bindings"] = {"native": {
        "adapter": "droid_exec", "contract": "millrace-droid-exec/v1",
        "executable": str(fake(workflow.parent, **options)),
        "config": {"profile": "droid.json"},
    }}
    graph["control_files"] = ["droid.json", "task.txt"]
    graph["planes"]["main"]["nodes"]["draft"]["invoke"] = {
        "binding": "native", "config": {"prompt": "task.txt", "model": "selected"},
        "timeout_seconds": 3,
    }
    workflow.write_text(json.dumps(graph))
    (workflow.parent / "droid.json").write_text(json.dumps(PROFILE))
    (workflow.parent / "task.txt").write_text("Review the project.")
    return workflow, project, home


def completed(single_flow, **options):
    workflow, project, home = droid_flow(single_flow, **options)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded", result
    return instance, result, home, project


def dump(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def test_droid_settings_precedence_and_omission(tmp_path):
    req = request(tmp_path)
    plan = c.plan(req)
    assert plan.settings["effective"] == {} and plan.environment == {}
    assert "--model" not in plan.argv and "--reasoning-effort" not in plan.argv
    req = replace(req, files={**req.files, "droid.json": json.dumps({
        **PROFILE, "model": "fallback", "reasoning_effort": "low",
    }).encode()}, runtime={"model": "runtime", "reasoning_effort": "high"})
    assert c.plan(req).settings["effective"] == {
        "model": "runtime", "reasoning_effort": "high",
    }
    req = replace(req, workflow={"model": "workflow"}, plane={"model": "plane"},
                  node_config={"prompt": "task.txt", "model": "node"})
    selected = c.plan(req).settings
    assert selected["effective"]["model"] == "node"
    assert selected["provenance"]["model"]["source"] == "node"
    assert selected["provenance"]["reasoning_effort"]["source"] == "runtime"
    # Custom-model/effort conflicts across different layers also refuse admission.
    bad = replace(req, node_config={"prompt": "task.txt", "model": "custom:model"})
    for action in (lambda: c.plan(bad),
                   lambda: c.validate_configuration(configuration(bad))):
        with pytest.raises(RouterError):
            action()


def test_droid_captured_authority_and_pure_reload(tmp_path, monkeypatch):
    req = request(tmp_path)
    plan = c.plan(req)
    assert plan == c.plan(req)
    run_plan = prepared(tmp_path)
    result = invoke(run_plan, tmp_path)
    native = Path(run_plan.argv[0])
    native.unlink()

    def forbidden(*args, **kwargs):
        pytest.fail("pure operation performed a live probe")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    c.validate_configuration(configuration(req))
    assert c.plan(req) == plan
    original = facts(run_plan, result, result.adapter_evidence["pid"])
    c.validate_completion(run_plan, original)


def test_droid_unused_binding_and_plane_settings_refuse(tmp_path):
    config = configuration(request(tmp_path))
    bad = replace(config, planes={**config.planes, "unused": {"unsafe": True}})
    with pytest.raises(RouterError):
        c.validate_configuration(bad)
    bad = replace(
        config, bindings={**config.bindings, "unused": {"profile": "bad.json"}},
        files={**config.files, "bad.json": b'{"format":"unsupported"}'},
    )
    with pytest.raises(RouterError):
        c.validate_configuration(bad)


def test_droid_two_planes_use_selected_workspaces(single_flow):
    workflow, project, home = droid_flow(single_flow)
    (project / "other").mkdir()
    graph = json.loads(workflow.read_text())
    graph["planes"]["other"] = copy.deepcopy(graph["planes"]["main"])
    graph["planes"]["other"]["workspace"] = "other"
    workflow.write_text(json.dumps(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded", result
    assert {i["plane_id"] for i in result["invocations"]} == {"main", "other"}
    assert {i["cwd"] for i in result["invocations"]} == {
        str(project), str(project / "other"),
    }
    later = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert later["status"] == "succeeded"


@pytest.mark.parametrize("outcome", ["READY", "LATER"])
def test_droid_live_edit_uses_producing_contract_and_new_routing_revision(
    single_flow, outcome,
):
    workflow, project, home = droid_flow(single_flow)
    task = workflow.parent / "task.txt"
    original_task = task.read_bytes()
    capture = workflow.parent / "droid-live-capture.json"
    graph = json.loads(workflow.read_text())
    updated = copy.deepcopy(graph)
    draft = updated["planes"]["main"]["nodes"]["draft"]
    # LATER is absent from the producing contract but present in the new one.
    # Its old completion must stay unknown and use the new on_unknown route.
    draft["outcomes"] = ["READY", "LATER"]
    draft["invoke"]["config"]["model"] = "routing-model"
    draft["on"] = {
        "READY": {"pause": {"reason": "new_known_route", "resume": "finish"}},
        "LATER": {"finish": "failed"},
    }
    draft["on_unknown"] = {
        "pause": {"reason": "new_unknown_route", "resume": "finish"},
    }
    finish = copy.deepcopy(draft)
    finish["outcomes"] = ["READY"]
    finish["on"] = {"READY": {"finish": "succeeded"}}
    finish["on_unknown"] = finish["on_error"] = {"finish": "failed"}
    updated["planes"]["main"]["nodes"]["finish"] = finish
    next_task = b"A changed task captured only by the routing revision."
    semantic = json.dumps({"outcome": outcome}, separators=(",", ":")).encode()
    wire = json.dumps(envelope(result=semantic.decode())).encode()
    # The ordinary fake edits the authored workflow only after emitting its
    # result. No adapter/Core result, dispatcher or persisted row is replaced.
    fake(
        workflow.parent, raw=wire, capture=str(capture),
        after=(
            f"Path({str(task)!r}).write_bytes({next_task!r})\n"
            f"Path({str(workflow)!r}).write_text({json.dumps(updated)!r})\n"
        ),
    )
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    paused = start_run(instance, state_home=home, edit_mode="live")
    run_id = str(paused["id"])
    producing = paused["startup_revision_id"]
    routing = paused["revision_id"]
    assert producing != routing
    assert paused["status"] == "paused" and paused["count"] == 1
    expected_reason = "new_known_route" if outcome == "READY" else "new_unknown_route"
    assert paused["planes"]["main"]["pause_reason"] == expected_reason
    assert paused["pending_revision"] is None and paused["run_gate"] is None
    assert paused["activations"][-1]["revision_id"] == routing
    old_invocation = copy.deepcopy(paused["invocations"][0])
    old_completion = copy.deepcopy(paused["completions"][0])
    old_launch = old_invocation["launch"]
    assert old_invocation["revision_id"] == producing
    assert old_launch["producing_revision"] == producing
    old_common = old_invocation["launch_settings"]["common"]
    assert old_common["producing_revision"] == producing
    assert old_launch["details"]["common_settings"]["producing_revision"] == producing
    assert old_launch["settings"]["effective"]["model"] == "selected"
    assert old_launch["details"]["outcomes"] == ["READY"]
    assert old_launch["details"]["prompt_source_sha256"] == c.digest(original_task)
    old_prompt = base64.b64decode(json.loads(capture.read_text())["prompt"])
    assert old_launch["details"]["prompt_sha256"] == c.digest(old_prompt)
    assert original_task in old_prompt and next_task not in old_prompt
    assert old_completion["revision_id"] == producing
    assert old_completion["classification"] == (
        "semantic" if outcome == "READY" else "unknown"
    )
    assert old_completion["outcome"] == outcome
    assert old_completion["stdout"].encode() == semantic
    old_final = old_invocation["adapter_evidence"]["final"]
    assert old_final["text_sha256"] == c.digest(semantic)
    transition = paused["transitions"][0]
    assert transition["completion_id"] == old_invocation["id"]
    assert transition["revision_id"] == routing
    assert json.loads(transition["action"]) == {
        "pause": {"reason": expected_reason, "resume": "finish"},
    }
    assert transition["target"] == "finish"

    # Recovery must reload the old launch through its producing revision,
    # without reclassifying or redispatching the saved completion.
    recovered = control.recover_run(
        instance, run_id, state_home=home, request_id=new_id(),
        expected_state_version=paused["state_version"],
    )
    assert recovered["status"] == "applied"
    current = control.inspect_run(instance, run_id, state_home=home)
    assert current["revision_id"] == routing and current["count"] == 1
    assert current["invocations"] == [old_invocation]
    assert current["completions"] == [old_completion]
    assert current["transitions"] == paused["transitions"]
    fake(workflow.parent, capture=str(capture))
    resumed = control.resume_plane(
        instance, run_id, plane_id="main", state_home=home, request_id=new_id(),
        expected_state_version=current["state_version"],
    )
    assert resumed["status"] == "applied"
    final = control.inspect_run(instance, run_id, state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 2
    assert [item["revision_id"] for item in final["invocations"]] == [
        producing, routing,
    ]
    assert final["invocations"][0] == old_invocation
    assert final["completions"][0] == old_completion
    fresh = final["invocations"][1]
    assert fresh["node"] == "finish"
    assert fresh["launch"]["producing_revision"] == routing
    assert fresh["launch"]["settings"]["effective"]["model"] == "routing-model"
    assert fresh["launch"]["details"]["prompt_source_sha256"] == c.digest(next_task)
    new_prompt_hash = fresh["launch"]["details"]["prompt_sha256"]
    assert new_prompt_hash != old_launch["details"]["prompt_sha256"]
    assert final["transitions"][1]["revision_id"] == routing
    before = dump(store_path(instance, home))
    Path(fresh["argv"][0]).unlink()
    historical = control.inspect_run(instance, run_id, state_home=home)
    assert historical["invocations"] == final["invocations"]
    assert historical["completions"] == final["completions"]
    assert historical["transitions"] == final["transitions"]
    assert dump(store_path(instance, home)) == before


@pytest.mark.parametrize("mutation", [
    "invocation", "plane", "node", "binding", "revision", "workspace",
    "adapter", "contract", "control_digest", "provenance", "coherent_wrong_plan",
    "native_identity", "final_digest", "exit", "origin",
])
def test_droid_store_authority_and_legal_aftermath(single_flow, mutation):
    instance, result, home, project = completed(single_flow)
    path = store_path(instance, home)
    later = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert later["status"] == "succeeded"
    with sqlite3.connect(path) as db:
        body = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        launch = body["launch"]
        keys = {"invocation": ("id", "f" * 32), "plane": ("plane_id", "foreign"),
                "node": ("node", "foreign"), "revision": ("revision_id", "f" * 64),
                "workspace": ("cwd", str(project / "foreign"))}
        if mutation in keys:
            key, value = keys[mutation]
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
        elif mutation == "coherent_wrong_plan":
            for key in ("effective", "requested"):
                launch["settings"][key]["model"] = "foreign"
            body["launch_settings"]["adapter"] = copy.deepcopy(launch["settings"])
            argv = list(launch["argv"])
            argv[argv.index("--model") + 1] = "foreign"
            launch["argv"], body["argv"] = argv, argv.copy()
        elif mutation == "native_identity":
            body["adapter_evidence"]["session_id"] = ""
        elif mutation == "final_digest":
            body["adapter_evidence"]["final"]["text_sha256"] = "0" * 64
        elif mutation == "exit":
            db.execute("UPDATE completions SET exit_code=1")
        else:
            body["origin"] = "core_interruption"
        db.execute("UPDATE invocations SET body=?", (json.dumps(body),))
    frozen = dump(path)
    with pytest.raises(RouterError):
        control.inspect_run(instance, str(result["id"]), state_home=home)
    assert dump(path) == frozen


def test_droid_hold_reconcile_fresh_recovery(single_flow):
    workflow, project, home = droid_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 0.5
    workflow.write_text(json.dumps(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    useful = start_run(instance, state_home=home)
    assert useful["status"] == "succeeded"
    peer = Path(str(workflow.parent / "fake-droid") + ".json")
    options = json.loads(peer.read_text())
    options["before"] = "time.sleep(30)"
    peer.write_text(json.dumps(options))
    failed = start_run(instance, state_home=home)
    invocation = failed["invocations"][0]
    assert failed["pause_reason"] == "writer_unknown" and failed["count"] == 1
    assert failed["completions"][0]["transport"] == "timeout"
    raw = {k: v for k, v in failed["completions"][0].items() if k != "effective_writer"}
    receipt = control.reconcile_invocation(
        instance, str(failed["id"]), invocation_id=str(invocation["id"]),
        plane_id="main", stopped=True, reason="exact fake group verified closed",
        state_home=home, request_id=new_id(),
        expected_state_version=failed["state_version"],
    )
    assert receipt["status"] == "applied"
    current = control.inspect_run(instance, str(failed["id"]), state_home=home)
    assert current["count"] == 1 and current["transitions"] == []
    control.cancel_run(
        instance, str(failed["id"]), state_home=home, request_id=new_id(),
    )
    fake(workflow.parent)
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded"
    assert fresh["invocations"][0]["id"] != invocation["id"]
    previous_session = useful["invocations"][0]["adapter_evidence"]["session_id"]
    fresh_session = fresh["invocations"][0]["adapter_evidence"]["session_id"]
    assert previous_session and fresh_session and previous_session != fresh_session
    later = control.inspect_run(instance, str(failed["id"]), state_home=home)
    original = {
        k: v for k, v in later["completions"][0].items() if k != "effective_writer"
    }
    assert original == raw


@pytest.mark.parametrize("corrupt", [False, True])
def test_droid_probe_failure_legal_and_corrupt_aftermath(single_flow, corrupt):
    workflow, project, home = droid_flow(single_flow, probe="time.sleep(30)")
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 0.5
    workflow.write_text(json.dumps(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    failed = start_run(instance, state_home=home)
    invocation = failed["invocations"][0]
    assert failed["pause_reason"] == "writer_unknown", failed
    assert invocation["adapter_evidence"]["phase"] == "preflight"
    assert invocation["adapter_evidence"]["probe_pid"] > 0
    assert invocation["adapter_evidence"]["pid"] is None
    if corrupt:
        path = store_path(instance, home)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE completions SET writer='stopped'")
        frozen = dump(path)
        with pytest.raises(RouterError):
            control.inspect_run(instance, str(failed["id"]), state_home=home)
        assert dump(path) == frozen
        return
    receipt = control.reconcile_invocation(
        instance, str(failed["id"]), invocation_id=str(invocation["id"]),
        plane_id="main", stopped=True, reason="exact fake probe group closed",
        state_home=home, request_id=new_id(),
        expected_state_version=failed["state_version"],
    )
    assert receipt["status"] == "applied"
    control.cancel_run(
        instance, str(failed["id"]), state_home=home, request_id=new_id(),
    )
    fake(workflow.parent)
    assert start_run(instance, state_home=home)["status"] == "succeeded"
    later = control.inspect_run(instance, str(failed["id"]), state_home=home)
    assert later["completions"][0]["transport"] == "timeout"
    assert later["transitions"] == []
