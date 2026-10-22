"""Pinned finite JSONL and owned-process proofs; all native peers are inert."""

from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from millrace.router.execution import CompletionFacts, RouterError
from opencode_support import (
    FINAL,
    PROFILE,
    config,
    event,
    events,
    fake,
    finish,
    flow,
    request,
    wire,
)
from plus_support import create_instance, new_id, start_run

from millrace_plus.router import control
from millrace_plus.router import opencode_configuration as c
from millrace_plus.router.opencode import OpenCodeRunAdapter


def prepared(
    root: Path,
    *,
    prompt: bytes = b"Review; return json-v1.",
    settings: dict[str, str] | None = None,
    **options: Any,
) -> Any:
    req = request(root, prompt=prompt, executable=str(fake(root, **options)))
    req = replace(req, node_config={**req.node_config, **(settings or {})})
    plan = c.plan(req)
    req.invocation_root.mkdir(exist_ok=True)
    for name, raw in plan.generated_controls.items():
        (req.invocation_root / name).write_bytes(raw)
    return plan


def run(
    plan: Any,
    root: Path,
    *,
    poll: Any = lambda: False,
    started: Any = lambda pid: None,
    timeout: float = 3,
) -> Any:
    pids: list[int] = []

    def owned(pid: int) -> None:
        pids.append(pid)
        started(pid)

    result = OpenCodeRunAdapter().invoke(
        plan,
        cwd=root,
        env={"PATH": "/usr/bin:/bin"},
        deadline=time.time() + timeout,
        poll=poll,
        on_started=owned,
    )
    c.validate_completion(plan, facts(plan, result, pids[0] if pids else None))
    for pid in [*pids, result.adapter_evidence.get("probe_pid")]:
        if pid is not None:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    return result


def facts(plan: Any, result: Any, pid: int | None) -> CompletionFacts:
    return CompletionFacts(
        plan.invocation_id,
        pid,
        result.stdout,
        result.stderr,
        result.classification,
        result.writer_state,
        result.diagnostic,
        result.exit_code,
        result.adapter_evidence,
        "adapter",
    )


@pytest.mark.parametrize("layer", ["runtime", "workflow", "plane", "node", "profile"])
@pytest.mark.parametrize(
    "bad",
    [
        {"model": "broken"},
        {"model": "/model"},
        {"model": "provider/"},
        {"model": "-p/m"},
        {"model": "p/m\x01"},
        {"variant": " "},
        {"variant": True},
        {"variant": "-unsafe"},
        {"variant": "x" * 257},
        {"unknown": 1},
    ],
)
def test_profile_and_explicit_settings_refuse_before_plan(
    tmp_path: Path, layer: str, bad: Any
) -> None:
    req = request(tmp_path)
    if layer == "node":
        req = replace(req, node_config={**req.node_config, **bad})
    elif layer == "profile":
        req = replace(
            req,
            files={
                **req.files,
                "opencode.json": json.dumps({**json.loads(PROFILE), **bad}).encode(),
            },
        )
    else:
        req = replace(req, **{layer: bad})
    # Even shadowed explicit settings refuse in validation and planning.
    req = replace(req, node_config={"model": "valid/model", **req.node_config})
    with pytest.raises(RouterError):
        OpenCodeRunAdapter().validate_configuration(config(req))
    with pytest.raises(RouterError):
        c.plan(req)


def test_settings_inheritance_freezes_provenance(tmp_path: Path) -> None:
    req = request(tmp_path)
    plan = c.plan(req)
    assert plan.settings["effective"] == {} and plan.environment == {}
    assert plan.argv == ("/absent/opencode", "run", "--format", "json")
    for raw in (
        b"{}",
        b"\xff",
        PROFILE.replace(b"1.19.3", b"1.19.4"),
        PROFILE[:-1] + b',"opencode_version":"1.19.3"}',
    ):
        with pytest.raises(RouterError):
            c.plan(replace(req, files={**req.files, "opencode.json": raw}))
    req = replace(
        req,
        runtime={"model": "runtime/base", "variant": "low"},
        workflow={"model": "workflow/model"},
        plane={"model": "plane/model"},
        node_config={"prompt": "task.txt", "model": "node/model/with/slashes"},
    )
    plan = c.plan(req)
    assert plan.settings["effective"] == {
        "model": "node/model/with/slashes",
        "variant": "low",
    }
    assert plan.settings["provenance"] == {
        "model": {"source": "node", "policy": None},
        "variant": {"source": "runtime", "policy": None},
    }
    assert plan.details["native_inheritance"] is True


@pytest.mark.parametrize(
    "prompt", [b"  leading", b"trail\n", b"no newline", "\ufeff  héllo 世界".encode()]
)
@pytest.mark.parametrize("explicit", [False, True])
def test_exact_prompt_stdin_and_finite_argv(
    tmp_path: Path, prompt: bytes, explicit: bool
) -> None:
    capture = tmp_path / "capture.json"
    selected = {"model": "provider/model/part", "variant": "high"} if explicit else {}
    expected = ["run", "--format", "json"]
    if explicit:
        expected += ["--model", "provider/model/part", "--variant", "high"]
    plan = prepared(
        tmp_path,
        prompt=prompt,
        capture=str(capture),
        expected_prompt=base64.b64encode(prompt).decode(),
        expected_argv=expected,
        settings=selected,
    )
    assert plan.generated_controls == {c.PROMPT_NAME: prompt}
    result = run(plan, tmp_path)
    assert result.classification == "completed" and result.stdout == FINAL, result
    observed = json.loads(capture.read_text())
    assert base64.b64decode(observed["stdin"]) == prompt
    assert observed["cwd"] == str(tmp_path)
    # macOS/Python can insert these encoding markers in an otherwise exact env.
    assert set(observed["environment"]) <= {
        "PATH",
        "LC_CTYPE",
        "__CF_USER_TEXT_ENCODING",
    }


def test_static_plan_and_captured_reload_make_no_live_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = prepared(tmp_path)
    pids: list[int] = []
    result = run(plan, tmp_path, started=pids.append)

    def deny(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("static/captured operation touched live state")

    monkeypatch.setattr(subprocess, "Popen", deny)
    monkeypatch.setattr(Path, "stat", deny)
    monkeypatch.setattr(Path, "read_bytes", deny)
    monkeypatch.setattr(os, "getenv", deny)
    OpenCodeRunAdapter().validate_configuration(config(request(tmp_path)))
    assert c.plan(request(tmp_path)) == c.plan(request(tmp_path))
    c.validate_completion(plan, facts(plan, result, pids[0]))


@pytest.mark.parametrize(
    "version,accepted",
    [
        (b"1.19.3", True),
        (b"1.19.3\n", True),
        (b"1.19.3\r\n", True),
        (b"v1.19.3\n", False),
        (b"1.19.4\n", False),
        (b"1.19.3\nextra\n", False),
        (b"local\n", False),
        (b"x" * 4097, False),
    ],
)
def test_version_probe_is_bounded_and_precedes_main(
    tmp_path: Path, version: bytes, accepted: bool
) -> None:
    plan = prepared(tmp_path, version=version)
    mains: list[int] = []
    result = run(plan, tmp_path, started=mains.append)
    assert (result.classification == "completed") == accepted, result
    assert bool(mains) == accepted
    assert result.stdout == (FINAL if accepted else b"")
    probe = result.adapter_evidence["probe_pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(probe, 0)


@pytest.mark.parametrize("order", ["text_before_stop", "stop_before_text"])
def test_pinned_event_final_message_and_deduplication(
    tmp_path: Path, order: str
) -> None:
    values = [
        event(
            "text",
            "old",
            "msg-tool",
            text='{"outcome":"WRONG"}',
            time={"start": 1, "end": 2},
        ),
        finish("msg-tool", "tool-calls"),
        event(
            "tool_use",
            "tool",
            "msg-tool",
            tool="question",
            callID="call",
            state={
                "status": "error",
                "input": {},
                "error": "permission denied",
                "time": {"start": 1, "end": 2},
            },
        ),
        event(
            "reasoning",
            "reason",
            text='{"outcome":"WRONG"}',
            time={"start": 1, "end": 2},
        ),
    ]
    first, second = FINAL[:20].decode(), FINAL[20:].decode()
    part = event("text", "a", text=first, time={"start": 1, "end": 2})
    final_parts = [
        part,
        copy.deepcopy(part),
        event("text", "b", text=second, time={"start": 1, "end": 2}),
        finish(),
    ]
    if order == "stop_before_text":
        final_parts = [final_parts[-1], *final_parts[:-1]]
    values += [*final_parts, copy.deepcopy(part), finish()]
    result = run(prepared(tmp_path, values=values), tmp_path)
    assert result.classification == "completed" and result.stdout == FINAL, result


@pytest.mark.parametrize(
    "case",
    [
        "missing_stop",
        "missing_text",
        "incomplete_text",
        "unknown_reason",
        "session",
        "part_session",
        "identity_conflict",
        "two_final",
        "wrong_message",
        "later_error",
        "unknown",
        "malformed",
        "duplicate_key",
        "truncated",
        "trailing",
        "nonzero",
        "bad_tokens",
        "bad_timestamp",
        "bad_part_type",
    ],
)
def test_invalid_and_conflicting_events_refuse(tmp_path: Path, case: str) -> None:
    values = events()
    options: dict[str, Any] = {}
    if case == "missing_stop":
        values.pop()
    elif case == "missing_text":
        values.pop(1)
    elif case == "incomplete_text":
        values[1]["part"]["time"].pop("end")
    elif case == "unknown_reason":
        values[-1]["part"]["reason"] = "length"
    elif case == "session":
        values[1]["sessionID"] = "ses-other"
    elif case == "part_session":
        values[1]["part"]["sessionID"] = "ses-other"
    elif case == "identity_conflict":
        values.insert(2, copy.deepcopy(values[1]))
        values[2]["part"]["text"] = "changed"
    elif case == "two_final":
        values += [
            event(
                "text",
                "other",
                "other",
                text=FINAL.decode(),
                time={"start": 1, "end": 2},
            ),
            finish("other"),
        ]
    elif case == "wrong_message":
        values[1]["part"]["messageID"] = "wrong"
    elif case == "later_error":
        values += [
            {
                "type": "error",
                "timestamp": 3,
                "sessionID": "ses-test",
                "error": {"name": "APIError", "data": {"message": "Bearer secret"}},
            }
        ]
    elif case == "unknown":
        values[1]["type"] = "session.completed"
    elif case == "malformed":
        options["raw"] = b"not json\n"
    elif case == "duplicate_key":
        options["raw"] = b'{"type":"text","type":"error"}\n'
    elif case == "truncated":
        options["raw"] = wire(values) + b'{"type":'
    elif case == "trailing":
        options["raw"] = wire(values) + b"extra\n"
    elif case == "nonzero":
        options["exit_code"] = 2
    elif case == "bad_tokens":
        values[-1]["part"]["tokens"]["input"] = True
    elif case == "bad_timestamp":
        values[0]["timestamp"] = True
    elif case == "bad_part_type":
        values[1]["part"]["type"] = "tool"
    result = run(prepared(tmp_path, values=values, **options), tmp_path)
    assert result.classification != "completed" and result.stdout == b"", result
    assert len(result.stderr) < 4096 and b"Bearer secret" not in result.stderr


@pytest.mark.parametrize("case", ["record", "stdout", "stderr"])
def test_stream_and_record_limits_stop_with_bounded_diagnostics(
    tmp_path: Path, case: str
) -> None:
    options = (
        {"raw": b"x" * (c.LINE_LIMIT + 1)}
        if case == "record"
        else (
            {
                "raw": wire(
                    [
                        event(
                            "reasoning",
                            "r",
                            text="x" * 600000,
                            time={"start": 1, "end": 2},
                        )
                    ]
                )
                * 15
            }
            if case == "stdout"
            else {"stderr": b"x" * (c.STDERR_LIMIT + 1)}
        )
    )
    result = run(prepared(tmp_path, **options), tmp_path)
    assert result.classification != "completed" and result.stdout == b"", result
    assert len(result.stderr) < 4096


@pytest.mark.parametrize("phase", ["before_probe", "probe"])
def test_startup_cancel_never_launches_main(tmp_path: Path, phase: str) -> None:
    marker = tmp_path / "probe"
    plan = prepared(tmp_path, probe=f"Path({str(marker)!r}).touch();time.sleep(30)")
    mains: list[int] = []
    result = run(
        plan,
        tmp_path,
        poll=lambda: phase == "before_probe" or marker.exists(),
        started=mains.append,
    )
    assert (
        result.classification == "cancelled" and result.stdout == b"" and not mains
    ), result


@pytest.mark.parametrize(
    "mode", ["late_output", "blocked_stdin", "flood", "term_ignored"]
)
def test_cooperative_cancel_and_late_output_never_complete(
    tmp_path: Path, mode: str
) -> None:
    marker = tmp_path / "started"
    late = wire(events())
    before = (
        f"Path({str(marker)!r}).touch()\n"
        "signal.signal(signal.SIGTERM, lambda *_: "
        f"(os.write(1, {late!r}), sys.exit(0)))\n"
        "while True: time.sleep(0.01)"
    )
    if mode == "blocked_stdin":
        before = f"Path({str(marker)!r}).touch()\ntime.sleep(30)"
    elif mode == "flood":
        packet = wire(
            [event("reasoning", "r", text="thinking", time={"start": 1, "end": 2})]
        )
        before = f"Path({str(marker)!r}).touch()\nwhile True: os.write(1, {packet!r})"
    elif mode == "term_ignored":
        before = (
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"Path({str(marker)!r}).touch()\ntime.sleep(30)"
        )
    plan = prepared(tmp_path, before=before, prompt=b"x" * c.CONTROL_LIMIT)
    result = run(plan, tmp_path, poll=marker.exists)
    assert result.classification == "cancelled" and result.stdout == b"", result
    assert result.writer_state == "may_live"


@pytest.mark.parametrize(
    "phase",
    [
        "probe",
        "probe_ceiling",
        "stdin",
        "drain",
        "short_descendant",
        "forced_descendant",
    ],
)
def test_deadline_covers_probe_stdin_and_drain(tmp_path: Path, phase: str) -> None:
    options: dict[str, Any] = {}
    if phase in {"probe", "probe_ceiling"}:
        options["probe"] = "time.sleep(30)"
    elif phase == "stdin":
        options["before"] = "time.sleep(30)"
    elif phase == "drain":
        options["after"] = "time.sleep(30)"
    else:
        child = (
            "import time;time.sleep("
            + ("0.15" if phase == "short_descendant" else "30")
            + ")"
        )
        options["after"] = (
            f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL)"
        )
    plan = prepared(tmp_path, prompt=b"x" * c.CONTROL_LIMIT, **options)
    start = time.monotonic()
    result = run(plan, tmp_path, timeout=8 if phase == "probe_ceiling" else 0.6)
    assert (result.classification == "completed") == (phase == "short_descendant"), (
        result
    )
    assert result.stdout == (FINAL if phase == "short_descendant" else b"")
    if phase != "short_descendant":
        assert result.writer_state == "may_live"
    if phase == "probe_ceiling":
        assert result.diagnostic == "opencode_version_timeout"
        assert 5 <= time.monotonic() - start < 7


@pytest.mark.parametrize(
    "change",
    [
        "invocation",
        "session",
        "message",
        "hash",
        "size",
        "reason",
        "version",
        "writer",
        "exit",
        "settings",
    ],
)
def test_captured_evidence_corruption_refuses_without_live_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    plan = prepared(tmp_path)
    pids: list[int] = []
    result = run(plan, tmp_path, started=pids.append)
    captured = facts(plan, result, pids[0])
    e = copy.deepcopy(dict(captured.evidence))
    if change == "invocation":
        e["invocation_id"] = "2" * 32
    elif change == "session":
        e["session_id"] = ""
    elif change == "message":
        e["final"]["message_id"] = ""
    elif change == "hash":
        e["final"]["text_sha256"] = "0" * 64
    elif change == "size":
        e["final"]["size"] += 1
    elif change == "reason":
        e["final"]["reason"] = "tool-calls"
    elif change == "version":
        e["observed_version"] = "local"
    elif change == "writer":
        captured = replace(captured, writer_state="unknown")
    elif change == "exit":
        captured = replace(captured, exit_code=True)
    elif change == "settings":
        plan = replace(plan, settings={"effective": {"model": "invalid"}})
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("reload probe")
    )
    with pytest.raises(RouterError, match="completion_integrity"):
        c.validate_completion(plan, replace(captured, evidence=e))


@pytest.mark.parametrize(
    "text,succeeds",
    [
        (FINAL, True),
        (b'{"outcome":"WRONG","summary":"done","refs":[]}', False),
        (b"not json", False),
    ],
)
def test_public_controls_parse_existing_json_v1(
    single_flow: tuple[Path, Path, Path], text: bytes, succeeds: bool
) -> None:
    workflow, project, home = flow(single_flow, values=events(text))
    made = create_instance(workflow, project, state_home=home)
    instance = str(made["instance_id"])
    result = start_run(instance, state_home=home)
    assert (result["status"] == "succeeded") == succeeds, result
    assert result["count"] == 1
    again = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert again["invocations"][0]["launch"]["adapter_id"] == "opencode_run"


@pytest.mark.parametrize("mode", ["captured_unknown", "forced_timeout"])
def test_terminal_contract_and_unknown_aftermath(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from millrace_plus.router import opencode

    workflow, project, home = flow(
        single_flow, before="time.sleep(30)" if mode == "forced_timeout" else ""
    )
    made = create_instance(workflow, project, state_home=home)
    instance = str(made["instance_id"])
    real = opencode.OpenCodeRunAdapter.invoke

    def uncertain(self: Any, *args: Any, **kwargs: Any) -> Any:
        value = real(self, *args, **kwargs)
        return replace(
            value,
            stdout=b"",
            classification="execution_failure",
            writer_state="unknown",
            diagnostic="fixture_unknown",
            adapter_evidence={
                **value.adapter_evidence,
                "final": None,
                "failure_code": "fixture_unknown",
            },
        )

    if mode == "captured_unknown":
        monkeypatch.setattr(opencode.OpenCodeRunAdapter, "invoke", uncertain)
    result = start_run(instance, state_home=home)
    run_id = str(result["id"])
    assert result["pause_reason"] == "writer_unknown" and result["count"] == 1
    launch = result["invocations"][0]["launch"]
    if mode == "forced_timeout":
        assert result["invocations"][0]["writer_state"] == "may_live"
        with pytest.raises(ProcessLookupError):
            os.kill(result["invocations"][0]["pid"], 0)
    # No automatic dispatch while the producing writer is unresolved.
    snapshot = control.inspect_run(instance, run_id, state_home=home)
    assert snapshot["count"] == 1 and snapshot["invocations"][0]["launch"] == launch
    recovered = control.recover_run(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
        expected_state_version=snapshot["state_version"],
    )
    assert recovered["status"] == "applied"
    snapshot = control.inspect_run(instance, run_id, state_home=home)
    assert snapshot["pause_reason"] == "writer_unknown" and snapshot["count"] == 1
    receipt = control.reconcile_invocation(
        instance,
        run_id,
        invocation_id=str(snapshot["invocations"][0]["id"]),
        reason="inert peer reaped",
        stopped=True,
        plane_id="main",
        state_home=home,
        request_id=new_id(),
        expected_state_version=snapshot["state_version"],
    )
    assert receipt["status"] == "applied", receipt
    monkeypatch.setattr(opencode.OpenCodeRunAdapter, "invoke", real)
    fake(workflow.parent)
    inspected = control.inspect_run(instance, run_id, state_home=home)
    assert inspected["count"] == 1 and inspected["invocations"][0]["launch"] == launch
    # Explicitly close the reconciled old run before starting fresh recovery.
    cancelled = control.cancel_run(
        instance,
        run_id,
        state_home=home,
        request_id=new_id(),
    )
    assert cancelled["status"] == "applied", cancelled
    # Fresh recovery is a new public run, with a new invocation; no native resume.
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded" and fresh["count"] == 1, fresh
    assert fresh["invocations"][0]["id"] != result["invocations"][0]["id"]
