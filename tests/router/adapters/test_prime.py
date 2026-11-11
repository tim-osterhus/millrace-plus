"""Official Prime text boundary, inert process peers and public aftermath."""

from __future__ import annotations

import base64
import copy
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from millrace.router.execution import RouterError
from plus_support import create_instance, new_id, start_run, store_path
from prime_support import (
    FINAL,
    PROFILE,
    configuration,
    facts,
    fake,
    invoke,
    prepared,
    prime_flow,
    request,
)

from millrace_plus.router import CoreContractError, control
from millrace_plus.router import prime as adapter
from millrace_plus.router import prime_configuration as c
from millrace_plus.router.resolver import resolve_adapter


@pytest.mark.parametrize("layer", ["profile", "runtime", "workflow", "plane", "node"])
@pytest.mark.parametrize("bad", [
    {"unknown": 1}, {"model": ""}, {"model": "-flag"}, {"model": "x y"},
    {"model": "x\x00"}, {"model": "x\x7f"}, {"model": "é" * 129},
    {"model": "\ud800"}, {"model": "id\u0080"}, {"model": "id\u009f"},
    {"thinking": "none"}, {"thinking": True}, {"provider": "OpenAI"},
    {"provider": "openai/model"}, {"provider": "-openai"},
])
def test_every_explicit_settings_layer_is_validated(tmp_path, layer, bad):
    req = request(tmp_path)
    if layer == "profile":
        req = replace(req, files={
            **req.files, "prime.json": json.dumps({**PROFILE, **bad}).encode(),
        })
    elif layer == "node":
        req = replace(req, node_config={**req.node_config, **bad})
    else:
        req = replace(req, **{layer: bad})
    with pytest.raises(RouterError):
        c.plan(req)
    with pytest.raises(RouterError):
        c.validate_configuration(configuration(req))


@pytest.mark.parametrize("field", [
    "argv", "session", "resume", "continue", "fork", "goal", "autonomous",
    "mode", "api_key", "environment", "remote", "daemon", "json", "thinking_level",
])
def test_unselected_flags_and_modes_refuse(tmp_path, field):
    req = request(tmp_path)
    for changed in (
        replace(req, binding_config={**req.binding_config, field: True}),
        replace(req, files={**req.files, "prime.json": json.dumps({
            **PROFILE, field: True,
        }).encode()}),
    ):
        with pytest.raises(RouterError):
            c.plan(changed)


@pytest.mark.parametrize("raw", [
    b"{}", b"\xff", b'{"format":"x","format":"y"}',
    json.dumps({**PROFILE, "prime_version": "0.10.1"}).encode(),
])
def test_strict_profile_and_exact_generation(tmp_path, raw):
    req = request(tmp_path)
    with pytest.raises(RouterError):
        c.plan(replace(req, files={**req.files, "prime.json": raw}))


def test_settings_fallback_precedence_omission_and_exact_argv(tmp_path):
    req = request(tmp_path)
    plan = c.plan(req)
    assert plan.settings["effective"] == {} and plan.environment == {}
    assert plan.argv == (
        "/absent/prime-agent", "--print", "--no-session", "--cwd", str(tmp_path),
        "--", plan.generated_controls[c.PROMPT_NAME].decode(),
    )
    req = replace(req, files={**req.files, "prime.json": json.dumps({
        **PROFILE, "provider": "prime-inference",
        "model": "fallback", "thinking": "low",
    }).encode()}, runtime={
        "provider": "openai", "model": "runtime", "thinking": "high",
    })
    assert c.plan(req).settings["effective"] == {
        "provider": "openai", "model": "runtime", "thinking": "high",
    }
    req = replace(req, workflow={"model": "workflow"}, plane={"model": "plane"},
                  node_config={
                      "prompt": "task.txt", "model": "node", "thinking": "max",
                  })
    plan = c.plan(req)
    assert plan.settings["effective"] == {
        "provider": "openai", "model": "node", "thinking": "max",
    }
    assert plan.settings["provenance"]["model"]["source"] == "node"
    assert plan.argv[5:-2] == (
        "--provider", "openai", "--model", "node", "--thinking", "max",
    )
    for value in c.THINKING:
        c.plan(replace(req, node_config={"prompt": "task.txt", "thinking": value}))


def test_unused_bindings_planes_and_deterministic_pure_reload(tmp_path, monkeypatch):
    req = request(tmp_path)
    config = configuration(req)
    for bad in (
        replace(config, planes={**config.planes, "unused": {"unsafe": True}}),
        replace(config, bindings={**config.bindings, "unused": {"profile": "bad.json"}},
                files={**config.files, "bad.json": b'{"format":"unsupported"}'}),
    ):
        with pytest.raises(RouterError):
            c.validate_configuration(bad)
    plan = c.plan(req)
    assert plan == c.plan(req)
    live = prepared(tmp_path)
    result = invoke(live, tmp_path)
    Path(live.argv[0]).unlink()

    def forbidden(*args, **kwargs):
        pytest.fail("pure operation performed live access")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    c.validate_configuration(config)
    assert c.plan(req) == plan
    c.validate_completion(live, facts(live, result, result.adapter_evidence["pid"]))


def test_positional_prompt_bound_capture_and_authority(tmp_path):
    capture = tmp_path / "capture.json"
    plan = prepared(tmp_path, capture=str(capture))
    assert invoke(plan, tmp_path).stdout == FINAL
    observed = json.loads(capture.read_text())
    prompt = plan.generated_controls[c.PROMPT_NAME]
    assert base64.b64decode(observed["prompt"]) == prompt
    assert observed["argv"] == list(plan.argv[1:])
    assert set(observed["environment"]) <= {
        "PATH", "LC_CTYPE", "__CF_USER_TEXT_ENCODING",
    }
    slash = c.plan(request(tmp_path, prompt=b"/login\n--mode rpc"))
    assert not slash.argv[-1].startswith("/")
    assert "/login\n--mode rpc" in slash.argv[-1]
    prefix_size = len(c._prefix(("READY",)))
    exact = c.plan(request(tmp_path, prompt=b"x" * (32768 - prefix_size)))
    assert len(exact.argv[-1].encode()) == 32768
    for raw in (b"", b" \n", b"\x00", b"\xff", b"x" * (32769 - prefix_size),
                b"x" * (1048576 + 1)):
        with pytest.raises(RouterError):
            c.plan(request(tmp_path, prompt=raw))
    for changed in (
        replace(plan, argv=(*plan.argv, "--mode", "json")),
        replace(plan, argv=(*plan.argv[:-1], "forged")),
        replace(plan, generated_controls={c.PROMPT_NAME: b"forged"}),
    ):
        with pytest.raises(RouterError):
            c.launch_details(changed)
    with pytest.raises(RouterError):
        adapter.PrimePrintAdapter().invoke(
            plan, cwd=tmp_path / "foreign", env={}, deadline=time.time() + 1,
            poll=lambda: False, on_started=lambda pid: pytest.fail("wrong cwd spawned"),
        )


def test_lone_surrogate_outcome_refuses_with_typed_contract_error(tmp_path):
    with pytest.raises(RouterError) as error:
        c.plan(replace(request(tmp_path), outcomes=("\ud800",)))
    assert error.value.code == "invalid_prime_result_contract"


@pytest.mark.parametrize("malformed,code", [
    ("prompt", "invalid_launch_plan"),
    ("outcomes", "invalid_prime_result_contract"),
])
def test_invalid_launch_unicode_refuses_before_probe_or_main(tmp_path, malformed, code):
    starts = tmp_path / "starts"
    plan = prepared(tmp_path, starts=str(starts))
    if malformed == "prompt":
        plan = replace(plan, generated_controls={
            c.PROMPT_NAME: c._prefix(("READY",)) + b"\xff",
        })
    else:
        plan = replace(plan, details={**plan.details, "outcomes": ["\ud800"]})
    with pytest.raises(RouterError) as error:
        invoke(plan, tmp_path)
    assert error.value.code == code
    assert not starts.exists()


@pytest.mark.parametrize("version,good", [
    (b"0.10.2", True), (b"0.10.2\n", True), (b"0.10.2\r\n", False),
    (b"v0.10.2\n", False), (b"0.10.1\n", False), (b"0.10.2\nextra", False),
    (b"x" * 4097, False),
])
def test_exact_version_probe_and_no_main_on_mismatch(tmp_path, version, good):
    starts = tmp_path / "starts"
    result = invoke(prepared(tmp_path, version=version, starts=str(starts)), tmp_path)
    assert (result.classification == "completed") is good
    assert len(starts.read_text().splitlines()) == (2 if good else 1)


@pytest.mark.parametrize("options", [
    {"probe_exit": 1}, {"probe": "time.sleep(30)"},
    {"probe_stderr": base64.b64encode(b"x" * 4097).decode()},
])
def test_probe_failures_and_ceiling_never_dispatch_main(tmp_path, options):
    starts = tmp_path / "starts"
    result = invoke(
        prepared(tmp_path, starts=str(starts), **options), tmp_path, timeout=.3,
    )
    assert result.classification != "completed"
    assert len(starts.read_text().splitlines()) == 1


def test_probe_five_second_ceiling_and_before_probe_cancel(tmp_path):
    starts = tmp_path / "starts"
    plan = prepared(tmp_path, starts=str(starts), probe="time.sleep(30)")
    cancelled = invoke(plan, tmp_path, poll=lambda: True)
    assert cancelled.classification == "cancelled" and not starts.exists()
    result = invoke(plan, tmp_path, timeout=8)
    assert result.diagnostic == "prime_version_timeout"
    assert result.writer_state == "may_live"
    with pytest.raises(RouterError):
        c.validate_completion(
            plan, replace(facts(plan, result, None), writer_state="stopped"),
        )


@pytest.mark.parametrize("raw", [b"", b" \n", b"\xff", b"x\x00", b"x" * 65537])
def test_invalid_or_oversized_semantic_text_fails(tmp_path, raw):
    result = invoke(prepared(tmp_path, raw=raw), tmp_path)
    assert result.classification == "protocol_failure" and result.stdout == b""
    assert result.adapter_evidence["final"] is None


@pytest.mark.parametrize("options", [
    {"exit_code": 1},
    {"after": "os.write(2,b'late native error')", "exit_code": 1},
    {"raw": b"x" * (8388608 + 1)},
    {"stderr": base64.b64encode(b"x" * (1048576 + 1)).decode()},
])
def test_nonzero_later_error_and_stream_overflow_defeat_final(tmp_path, options):
    result = invoke(prepared(tmp_path, **options), tmp_path)
    assert result.classification != "completed" and result.stdout == b""
    assert result.adapter_evidence["final"] is None


def test_whole_text_is_preserved_without_fabricated_native_identity(tmp_path):
    raw = b'progress {"outcome":"BAD"}\n{"outcome":"READY"}\n'
    result = invoke(prepared(tmp_path, raw=raw), tmp_path)
    assert result.stdout == raw  # Core owns semantic parsing, no substring extraction.
    evidence = result.adapter_evidence
    assert evidence["stdout_sha256"] == c.digest(raw)
    assert evidence["final"] == {"size": len(raw), "text_sha256": c.digest(raw)}
    assert not {
        "session_id", "stop_reason", "effective_model", "effective_thinking",
    } & evidence.keys()
    secret = b"sk-secret-do-not-persist"
    failed = invoke(prepared(tmp_path, stderr=base64.b64encode(secret).decode(),
                             exit_code=1), tmp_path)
    assert secret not in failed.stderr
    assert secret.decode() not in json.dumps(failed.adapter_evidence)


@pytest.mark.parametrize("mode", ["before", "after", "child", "ignore_term"])
def test_cancelled_foreground_and_descendant_closure(tmp_path, mode):
    ready, stopped = tmp_path / "ready.json", tmp_path / "stopped.json"
    mark = f"Path({str(ready)!r}).write_text(json.dumps({{'root':os.getpid()}}))\n"
    options = {"before": mark + "time.sleep(30)"}
    if mode == "after":
        options = {"after": mark + "time.sleep(30)"}
    elif mode == "ignore_term":
        options = {"before": "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                   + mark + "time.sleep(30)"}
    elif mode == "child":
        child = (
            "import os,signal,sys,time,json\nfrom pathlib import Path\n"
            "def stop(signum,frame):\n"
            " Path(sys.argv[3]).write_text(str(signum))\n raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM,stop)\n"
            "while True:\n"
            " try:os.kill(int(sys.argv[1]),0)\n"
            " except ProcessLookupError:break\n"
            " time.sleep(.01)\n"
            "Path(sys.argv[2]).write_text(json.dumps("
            "{'root':int(sys.argv[1]),'pid':os.getpid()}))\n"
            "time.sleep(30)\n"
        )
        options = {"after": (
            f"subprocess.Popen([sys.executable,'-c',{child!r},str(os.getpid()),"
            f"{str(ready)!r},{str(stopped)!r}])\n"
        )}
    plan = prepared(tmp_path, **options)
    began = time.monotonic()
    result = invoke(plan, tmp_path, timeout=10, poll=ready.exists)
    assert json.loads(ready.read_text())["root"] == result.adapter_evidence["pid"]
    assert result.classification == "cancelled" and result.writer_state == "may_live"
    assert result.stdout == b"" and result.adapter_evidence["final"] is None
    assert result.adapter_evidence["root_reaped"]
    if mode in {"after", "child"}:
        assert result.adapter_evidence["stdout_sha256"] == c.digest(FINAL)
    if mode == "ignore_term":
        assert result.exit_code == -signal.SIGKILL and time.monotonic() - began >= 2
    elif mode == "child":
        assert result.exit_code == 0
        assert stopped.read_text() == str(signal.SIGTERM)
    else:
        assert result.exit_code == -signal.SIGTERM


def test_delayed_eof_natural_child_closure_and_timeout_after_final(tmp_path):
    child = "import time;time.sleep(.3)"
    plan = prepared(
        tmp_path, after=f"subprocess.Popen([sys.executable,'-c',{child!r}])",
    )
    began = time.monotonic()
    assert invoke(plan, tmp_path).stdout == FINAL
    assert time.monotonic() - began >= .3
    timed = invoke(prepared(tmp_path, after="time.sleep(30)"), tmp_path, timeout=.4)
    assert timed.classification == "timeout" and timed.writer_state == "may_live"
    assert timed.stdout == b"" and timed.adapter_evidence["final"] is None


def test_callback_failure_partial_startup_and_capability_refusal(tmp_path, monkeypatch):
    from millrace.router.process import OwnedProcessError

    def broken(pid):
        raise RuntimeError("test callback")

    with pytest.raises(OwnedProcessError) as caught:
        invoke(prepared(tmp_path, before="time.sleep(30)"), tmp_path, started=broken)
    assert caught.value.result.root_reaped
    plan = prepared(tmp_path)
    Path(plan.argv[0]).unlink()
    assert invoke(plan, tmp_path).diagnostic == "prime_spawn_failed"

    def unavailable():
        raise CoreContractError("core_process_missing", "test")

    monkeypatch.setattr(adapter, "require_owned_process", unavailable)
    result = invoke(prepared(tmp_path), tmp_path)
    assert result.diagnostic == "prime_core_process_missing"
    assert result.writer_state == "stopped"
    assert result.adapter_evidence["probe_pid"] is None


def test_post_popen_setup_failure_preserves_observed_process(tmp_path, monkeypatch):
    from millrace.router import process

    original, calls = process.selectors.DefaultSelector, 0

    def selector():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("deterministic setup failure after main Popen")
        return original()

    monkeypatch.setattr(process.selectors, "DefaultSelector", selector)
    result = invoke(prepared(tmp_path), tmp_path)
    assert result.diagnostic == "prime_io_failed" and result.writer_state == "may_live"
    assert result.adapter_evidence["pid"] is None
    assert result.adapter_evidence["process_pid"] > 0


@pytest.mark.parametrize("key,value", [
    ("invocation_id", "foreign"), ("pid", 0), ("observed_version", "0.10.1"),
    ("stdout_bytes", 0), ("stdout_sha256", "0" * 64), ("group_state", "running"),
    ("cleanup_forced", True), ("stdout_eof", False), ("external_effects", "closed"),
])
def test_completion_evidence_corruption_refuses(tmp_path, key, value):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    evidence = copy.deepcopy(result.adapter_evidence)
    evidence[key] = value
    with pytest.raises(RouterError):
        c.validate_completion(plan, replace(
            facts(plan, result, result.adapter_evidence["pid"]), evidence=evidence,
        ))


def test_public_resolver_cli_and_two_workspace_planes(single_flow):
    assert isinstance(resolve_adapter("prime_print", "millrace-prime-print/v1"),
                      adapter.PrimePrintAdapter)
    with pytest.raises(RouterError):
        resolve_adapter("prime_print", "millrace-prime-print/v2")
    workflow, project, home = prime_flow(single_flow)
    (project / "other").mkdir()
    graph = json.loads(workflow.read_text())
    graph["planes"]["other"] = copy.deepcopy(graph["planes"]["main"])
    graph["planes"]["other"]["workspace"] = "other"
    workflow.write_text(json.dumps(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    common = ["--state-home", str(home), "--instance", instance]
    result = subprocess.run(
        [sys.executable, "-m", "millrace_plus.router", "start", *common],
        capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    assert result.returncode == 0, result.stderr
    runs = control.inspect_instance(instance, state_home=home)["runs"]
    run = control.inspect_run(instance, str(runs[-1]["id"]), state_home=home)
    assert run["status"] == "succeeded"
    assert {item["cwd"] for item in run["invocations"]} == {
        str(project), str(project / "other"),
    }
    Path(graph["bindings"]["native"]["executable"]).unlink()
    later = control.inspect_run(instance, str(run["id"]), state_home=home)
    assert later["status"] == "succeeded"


@pytest.mark.parametrize("raw", [
    b'progress {"outcome":"BAD"}\n{"outcome":"READY"}\n',
    b'{"outcome":"READY"', b'{"outcome":"READY","outcome":"BAD"}',
])
def test_public_core_rejects_polluted_truncated_or_duplicate_semantic_text(
    single_flow, raw,
):
    workflow, project, home = prime_flow(single_flow, raw=raw)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] != "succeeded"
    assert result["completions"][0]["classification"] != "semantic"


def test_hold_reconcile_preserves_failure_and_allows_fresh_run(single_flow):
    workflow, project, home = prime_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = .5
    workflow.write_text(json.dumps(graph))
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    assert start_run(instance, state_home=home)["status"] == "succeeded"
    fake(workflow.parent, before="time.sleep(30)")
    failed = start_run(instance, state_home=home)
    assert failed["pause_reason"] == "writer_unknown" and failed["count"] == 1
    invocation = failed["invocations"][0]
    assert invocation["adapter_evidence"]["group_state"] == "closed"
    raw = {key: value for key, value in failed["completions"][0].items()
           if key != "effective_writer"}
    receipt = control.reconcile_invocation(
        instance, str(failed["id"]), invocation_id=str(invocation["id"]),
        plane_id="main",
        stopped=True, reason="exact fake group verified closed", state_home=home,
        request_id=new_id(), expected_state_version=failed["state_version"],
    )
    assert receipt["status"] == "applied"
    control.cancel_run(
        instance, str(failed["id"]), state_home=home, request_id=new_id(),
    )
    fake(workflow.parent)
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded"
    assert fresh["invocations"][0]["id"] != invocation["id"]
    later = control.inspect_run(instance, str(failed["id"]), state_home=home)
    assert later["transitions"] == []
    assert {key: value for key, value in later["completions"][0].items()
            if key != "effective_writer"} == raw


def dump(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


@pytest.mark.parametrize("outcome", ["READY", "LATER"])
def test_prime_live_edit_uses_producing_contract_and_new_routing_revision(
    single_flow, outcome,
):
    workflow, project, home = prime_flow(single_flow)
    task = workflow.parent / "task.txt"
    original_task = task.read_bytes()
    capture = workflow.parent / "prime-live-capture.json"
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
    wire = semantic
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
    "final_digest", "exit", "origin",
])
def test_saved_producing_authority_and_corrupt_aftermath(single_flow, mutation):
    workflow, project, home = prime_flow(single_flow)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded"
    path = store_path(instance, home)
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
