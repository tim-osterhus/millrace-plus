"""Exact Droid envelopes and real inert subprocesses; no provider execution."""

from __future__ import annotations

import base64
import copy
import json
import os
import signal
import time
from dataclasses import replace
from pathlib import Path

import pytest
from millrace.router.execution import RouterError
from test_additional_adapter_contracts import (
    FINAL,
    PROFILE,
    configuration,
    envelope,
    facts,
    invoke,
    prepared,
    request,
)

from millrace_plus.router import CoreContractError
from millrace_plus.router import droid as adapter
from millrace_plus.router import droid_configuration as c


@pytest.mark.parametrize("layer", ["profile", "runtime", "workflow", "plane", "node"])
@pytest.mark.parametrize("bad", [
    {"unknown": 1}, {"model": ""}, {"model": "-flag"}, {"model": "x y"},
    {"model": "x\x00"}, {"model": "x\x7f"}, {"model": "é" * 129},
    {"model": "\ud800"},
    {"model": "id\u0080"}, {"model": "id\u009f"},
    {"reasoning_effort": "max"}, {"reasoning_effort": True},
    {"model": "custom:model", "reasoning_effort": "high"},
])
def test_invalid_explicit_settings_including_unused_layers(tmp_path, layer, bad):
    req = request(tmp_path)
    if layer == "profile":
        req = replace(req, files={
            **req.files, "droid.json": json.dumps({**PROFILE, **bad}).encode(),
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
    "argv", "auto", "session", "fork", "mission", "settings", "only_tools",
    "skip_permissions_unsafe", "remote", "worktree", "use_spec",
])
def test_modes_and_permission_policy_are_closed(tmp_path, field):
    req = request(tmp_path)
    with pytest.raises(RouterError):
        c.plan(replace(req, binding_config={**req.binding_config, field: True}))
    profile = {**PROFILE, "permission_mode": "auto"}
    with pytest.raises(RouterError):
        c.plan(replace(req, files={
            **req.files, "droid.json": json.dumps(profile).encode(),
        }))
    assert not any(arg in c.plan(req).argv for arg in (
        "--auto", "--skip-permissions-unsafe", "--only-tools", "--settings",
    ))


@pytest.mark.parametrize("mode", ["read_only", "auto_medium"])
def test_profile_permission_modes_capture_exact_native_argv(tmp_path, mode):
    capture = tmp_path / "capture.json"
    base = prepared(tmp_path, capture=str(capture))
    req = request(tmp_path, executable=base.argv[0])
    req = replace(req, files={
        **req.files,
        "droid.json": json.dumps({**PROFILE, "permission_mode": mode}).encode(),
    })
    c.validate_configuration(configuration(req))
    plan = c.plan(req)
    expected = (
        base.argv[0], "exec", "-f", str(tmp_path / "invocation/droid-prompt.txt"),
        "--cwd", str(tmp_path), "-o", "json",
    )
    if mode == "auto_medium":
        expected += ("--auto", "medium")
    assert plan.argv == expected
    assert plan.details["permission_mode"] == mode
    assert invoke(plan, tmp_path).stdout == FINAL
    assert json.loads(capture.read_text())["argv"] == list(expected[1:])
    # A native flag alone or a captured mode alone cannot change authority.
    opposite = "read_only" if mode == "auto_medium" else "auto_medium"
    for forged in (
        replace(plan, details={**plan.details, "permission_mode": opposite}),
        replace(plan, argv=(*base.argv, "--auto", "low")),
        replace(plan, argv=(*base.argv, "--auto", "high")),
        replace(plan, argv=(*plan.argv, "--skip-permissions-unsafe")),
        replace(plan, argv=(
            base.argv if mode == "auto_medium" else expected + ("--auto", "medium")
        )),
    ):
        with pytest.raises(RouterError):
            c.launch_details(forged)


@pytest.mark.parametrize("mode", [
    None, True, [], "auto_low", "auto_high", "medium", "auto",
])
def test_permission_modes_refuse_unselected_authority(tmp_path, mode):
    req = request(tmp_path)
    req = replace(req, files={
        **req.files,
        "droid.json": json.dumps({**PROFILE, "permission_mode": mode}).encode(),
    })
    with pytest.raises(RouterError):
        c.plan(req)
    with pytest.raises(RouterError):
        c.validate_configuration(configuration(req))


@pytest.mark.parametrize("layer", ["runtime", "workflow", "plane", "node"])
def test_permission_mode_is_profile_only(tmp_path, layer):
    req = request(tmp_path)
    value = {"permission_mode": "auto_medium"}
    if layer == "node":
        req = replace(req, node_config={**req.node_config, **value})
    else:
        req = replace(req, **{layer: value})
    with pytest.raises(RouterError):
        c.plan(req)
    with pytest.raises(RouterError):
        c.validate_configuration(configuration(req))


@pytest.mark.parametrize("raw", [
    b"{}", b"\xff", b'{"format":"x","format":"y"}',
    json.dumps({**PROFILE, "droid_version": "latest"}).encode(),
])
def test_profiles_refuse_malformed_duplicate_and_unsupported(tmp_path, raw):
    req = request(tmp_path)
    with pytest.raises(RouterError):
        c.plan(replace(req, files={**req.files, "droid.json": raw}))


def test_prompt_file_authority_and_bounds(tmp_path):
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
    slash = c.plan(request(tmp_path, prompt=b"/login\nquoted json-v1"))
    materialized = slash.generated_controls[c.PROMPT_NAME]
    assert not materialized.startswith(b"/")
    assert b"/login\nquoted json-v1" in materialized
    assert slash.details["prompt_source_sha256"] != slash.details["prompt_sha256"]
    for raw in (b"", b" \n", b"\x00", b"\xff", b"x" * (1048576 + 1)):
        with pytest.raises(RouterError):
            c.plan(request(tmp_path, prompt=raw))
    full = c.plan(request(tmp_path, prompt=b"x" * 1048576))
    assert len(full.generated_controls[c.PROMPT_NAME]) <= 1114112
    for mutation in (replace(plan, argv=(*plan.argv, "--auto", "high")),
                     replace(plan, generated_controls={c.PROMPT_NAME: b"forged"})):
        with pytest.raises(RouterError):
            c.launch_details(mutation)
    with pytest.raises(RouterError):
        adapter.DroidExecAdapter().invoke(
            plan, cwd=tmp_path / "foreign", env={}, deadline=time.time() + 1,
            poll=lambda: False, on_started=lambda pid: pytest.fail("wrong cwd spawned"),
        )


@pytest.mark.parametrize("version,good", [
    (b"0.247.0", True), (b"0.247.0\n", True),
    (b"0.247.0\r\n", False), (b"v0.247.0\n", False), (b"1.0.0\n", False),
    (b"0.247.0\nextra", False), (b"x" * 4097, False),
])
def test_version_probe_bounds_and_mismatch(tmp_path, version, good):
    starts = tmp_path / "starts"
    result = invoke(prepared(tmp_path, version=version, starts=str(starts)), tmp_path)
    assert (result.classification == "completed") is good
    assert len(starts.read_text().splitlines()) == (2 if good else 1)


@pytest.mark.parametrize("options", [
    {"probe_exit": 1}, {"probe": "time.sleep(30)"},
    {"probe_stderr": base64.b64encode(b"x" * 4097).decode()},
])
def test_probe_failure_never_dispatches_main(tmp_path, options):
    starts = tmp_path / "starts"
    result = invoke(
        prepared(tmp_path, starts=str(starts), **options), tmp_path, timeout=0.2,
    )
    assert result.classification != "completed"
    assert len(starts.read_text().splitlines()) == 1


def test_probe_five_second_ceiling_within_longer_invocation(tmp_path):
    plan = prepared(tmp_path, probe="time.sleep(30)")
    result = invoke(plan, tmp_path, timeout=8)
    assert result.diagnostic == "droid_version_timeout"
    assert result.adapter_evidence["phase"] == "preflight"


@pytest.mark.parametrize("changed", [
    {"type": "tool_result"}, {"subtype": "failure"}, {"is_error": True},
    {"is_error": 0}, {"extra": 1}, {"result": None}, {"session_id": ""},
    {"session_id": "x\n"}, {"duration_ms": True}, {"duration_ms": -1},
    {"num_turns": 0.5}, {"num_turns": True},
    {"usage": {"input_tokens": 1}},
])
def test_final_envelope_and_error_precedence(tmp_path, changed):
    raw = json.dumps(envelope(**changed)).encode()
    result = invoke(prepared(tmp_path, raw=raw), tmp_path)
    assert result.classification == "protocol_failure" and result.stdout == b""


@pytest.mark.parametrize("suffix", [b'\n{"type":"error"}', b" trailing", b"\xff"])
def test_trailing_output_cannot_become_success(tmp_path, suffix):
    plan = prepared(tmp_path, raw=json.dumps(envelope()).encode() + suffix)
    result = invoke(plan, tmp_path)
    assert result.classification == "protocol_failure"


def test_nonzero_and_duplicate_json_defeat_plausible_result(tmp_path):
    result = invoke(prepared(tmp_path, exit_code=1), tmp_path)
    assert result.classification == "execution_failure"
    raw = json.dumps(envelope()).encode()[:-1] + b',"is_error":false}'
    result = invoke(prepared(tmp_path, raw=raw), tmp_path)
    assert result.classification == "protocol_failure"


@pytest.mark.parametrize("raw", [b"", b"{}", b"[]", b"{", b"\xff"])
def test_missing_or_malformed_final_never_succeeds(tmp_path, raw):
    result = invoke(prepared(tmp_path, raw=raw), tmp_path)
    assert result.classification == "protocol_failure"


def test_native_metadata_optional_fields_and_semantic_failure(tmp_path):
    value = envelope(result='{"outcome":"BLOCKED"}')
    value["usage"].update(thinking_tokens=0, ttft_ms=0.25)
    result = invoke(prepared(tmp_path, raw=json.dumps(value).encode()), tmp_path)
    assert result.classification == "completed"
    assert result.stdout == b'{"outcome":"BLOCKED"}'
    # Core owns semantic routing: a workflow failure is still native completion.
    assert result.adapter_evidence["final"]["metadata"]["usage"]["ttft_ms"] == 0.25


def test_native_warning_is_not_a_fabricated_protocol_error(tmp_path):
    warning = b"ordinary native warning"
    result = invoke(prepared(
        tmp_path, stderr=base64.b64encode(warning).decode(),
    ), tmp_path)
    assert result.classification == "completed" and result.stdout == FINAL
    assert result.stderr == b""
    assert result.adapter_evidence["stderr_bytes"] == len(warning)


@pytest.mark.parametrize("target", [
    "stdout", "stderr", "semantic", "nonfinite", "usage", "secret",
])
def test_capture_limits_and_redaction(tmp_path, target):
    secret = b"sk-secret-do-not-persist"
    options = {"stderr": base64.b64encode(secret).decode(), "exit_code": 1}
    if target == "stdout":
        options = {"raw": b"x" * (c.STDOUT_LIMIT + 1)}
    elif target == "stderr":
        options = {"stderr": base64.b64encode(b"x" * (c.STDERR_LIMIT + 1)).decode()}
    elif target == "semantic":
        options = {"raw": json.dumps(envelope(result="x" * 65537)).encode()}
    elif target == "nonfinite":
        options = {"raw": json.dumps(envelope(duration_ms=float("inf"))).encode()}
    elif target == "usage":
        value = envelope()
        value["usage"]["input_tokens"] = True
        options = {"raw": json.dumps(value).encode()}
    result = invoke(prepared(tmp_path, **options), tmp_path)
    assert result.classification != "completed" and result.stdout == b""
    assert secret not in result.stderr
    assert secret.decode() not in json.dumps(result.adapter_evidence)


@pytest.mark.parametrize("mode", ["before", "after", "child", "ignore_term"])
def test_owned_process_closure_and_cleanup(tmp_path, mode):
    ready = tmp_path / "ready.json"
    stopped = tmp_path / "child-stopped.json"
    starts = tmp_path / "starts.jsonl"
    wire = json.dumps(envelope(session_id="session-closure")).encode()
    mark_ready = (
        f"Path({str(ready)!r}).write_text(json.dumps("
        f"{{'root_pid':os.getpid(),'kind':{mode!r}}}))\n"
    )
    options = {"before": mark_ready + "time.sleep(30)"}
    if mode == "after":
        options = {"after": mark_ready + "time.sleep(30)"}
    elif mode == "child":
        child = (
            "import json,os,signal,sys,time\nfrom pathlib import Path\n"
            "root=int(sys.argv[1])\n"
            "def terminated(signum,frame):\n"
            " Path(sys.argv[3]).write_text(json.dumps("
            "{'pid':os.getpid(),'signal':signum}))\n"
            " raise SystemExit(0)\n"
            "signal.signal(signal.SIGTERM,terminated)\n"
            "while True:\n"
            " try:os.kill(root,0)\n"
            " except ProcessLookupError:break\n"
            " time.sleep(0.01)\n"
            "Path(sys.argv[2]).write_text(json.dumps("
            "{'root_pid':root,'pid':os.getpid(),'kind':'child'}))\n"
            "time.sleep(30)\n"
        )
        options = {"after": (
            f"subprocess.Popen([sys.executable,'-c',{child!r},"
            f"str(os.getpid()),{str(ready)!r},{str(stopped)!r}])\n"
        )}
    elif mode == "ignore_term":
        options = {"before": (
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            + mark_ready + "time.sleep(30)"
        )}
    plan = prepared(tmp_path, raw=wire, starts=str(starts), **options)
    began = time.monotonic()
    # Cancellation begins only after the selected main branch proves readiness.
    # The generous safety deadline fails the case if that branch is never reached.
    result = invoke(plan, tmp_path, timeout=10, poll=ready.exists)
    elapsed = time.monotonic() - began
    observed = json.loads(ready.read_text())
    evidence = result.adapter_evidence
    assert observed["kind"] == mode
    assert evidence["phase"] == "invocation"
    assert evidence["pid"] == evidence["process_pid"] == observed["root_pid"]
    assert evidence["probe_pid"] > 0
    assert [json.loads(line) for line in starts.read_text().splitlines()] == [
        ["--version"], list(plan.argv[1:]),
    ]
    assert result.classification == "cancelled" and result.writer_state == "may_live"
    assert evidence["stop_cause"] == "cancelled" and evidence["cleanup_forced"]
    assert evidence["root_reaped"]
    assert result.stdout == b"" and evidence["final"] is None
    if mode in {"after", "child"}:
        # The ready marker follows the actual write of the complete native final.
        assert evidence["stdout_bytes"] == len(wire)
        assert evidence["stdout_sha256"] == c.digest(wire)
        assert evidence["session_id"] == "session-closure"
        assert evidence["protocol_state"] == "observed"
    else:
        assert evidence["stdout_bytes"] == 0 and evidence["session_id"] is None
    if mode == "ignore_term":
        # The marker was written after SIG_IGN installation; -KILL proves the
        # TERM phase could not stop this main and the actual escalation ran.
        assert result.exit_code == -signal.SIGKILL and elapsed >= 2
    elif mode == "child":
        assert result.exit_code == 0  # Root had exited before child readiness.
        child_stop = json.loads(stopped.read_text())
        assert observed["pid"] != observed["root_pid"]
        assert child_stop == {"pid": observed["pid"], "signal": signal.SIGTERM}
    else:
        assert result.exit_code == -signal.SIGTERM


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_reported_session_survives_later_transport_failure(tmp_path, failure):
    ready = tmp_path / "final-emitted.json"
    session = "reported-before-" + failure
    wire = json.dumps(envelope(session_id=session)).encode()
    options = {"exit_code": 1}
    if failure == "timeout":
        options = {"after": (
            f"Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid()}}))\n"
            "time.sleep(30)\n"
        )}
    plan = prepared(tmp_path, raw=wire, **options)
    result = invoke(plan, tmp_path, timeout=3)
    evidence = result.adapter_evidence
    assert evidence["phase"] == "invocation"
    assert evidence["pid"] == evidence["process_pid"] and evidence["pid"] > 0
    assert evidence["stdout_bytes"] == len(wire)
    assert evidence["stdout_sha256"] == c.digest(wire)
    assert evidence["session_id"] == session
    assert evidence["protocol_state"] == "observed"
    assert result.stdout == b"" and evidence["final"] is None
    assert result.classification == (
        "execution_failure" if failure == "nonzero" else "timeout"
    )
    if failure == "timeout":
        assert json.loads(ready.read_text())["pid"] == evidence["pid"]
        assert evidence["stop_cause"] == "timeout"
    else:
        assert result.exit_code == 1 and evidence["stop_cause"] is None
    original = facts(plan, result, evidence["pid"])
    for key, value in (("session_id", None), ("protocol_state", "incomplete")):
        corrupt = copy.deepcopy(evidence)
        corrupt[key] = value
        with pytest.raises(RouterError):
            c.validate_completion(plan, replace(original, evidence=corrupt))


@pytest.mark.parametrize("kind", ["truncated", "trailing", "foreign", "overflow"])
def test_invalid_capture_never_supplies_a_session_identity(tmp_path, kind):
    wire = json.dumps(envelope(session_id="must-not-be-inferred")).encode()
    if kind == "truncated":
        wire = wire[:-1]
    elif kind == "trailing":
        wire += b'\n{"type":"error","session_id":"foreign"}'
    elif kind == "foreign":
        wire = json.dumps({"type": "tool_result", "session_id": "foreign"}).encode()
    else:
        wire += b" " * (c.STDOUT_LIMIT + 1)
    result = invoke(prepared(tmp_path, raw=wire, exit_code=1), tmp_path)
    assert result.classification != "completed" and result.stdout == b""
    assert result.adapter_evidence["phase"] == "invocation"
    assert result.adapter_evidence["session_id"] is None
    assert result.adapter_evidence["final"] is None


def test_cancellation_boundaries_and_no_retry(tmp_path):
    starts = tmp_path / "starts"
    plan = prepared(tmp_path, starts=str(starts))
    result = invoke(plan, tmp_path, poll=lambda: True)
    assert result.classification == "cancelled" and not starts.exists()
    flag = tmp_path / "main"
    plan = prepared(
        tmp_path, starts=str(starts),
        after=f"Path({str(flag)!r}).touch();time.sleep(30)",
    )
    result = invoke(plan, tmp_path, poll=flag.exists)
    assert result.classification == "cancelled" and result.stdout == b""
    assert len(starts.read_text().splitlines()) == 2


def test_cancellation_during_native_work_and_partial_startup(tmp_path):
    flag = tmp_path / "working"
    plan = prepared(
        tmp_path, before=f"Path({str(flag)!r}).touch();time.sleep(30)",
    )
    assert invoke(plan, tmp_path, poll=flag.exists).classification == "cancelled"
    plan = prepared(tmp_path)
    Path(plan.argv[0]).unlink()
    result = invoke(plan, tmp_path)
    assert result.classification == "execution_failure"
    assert result.adapter_evidence["probe_pid"] is None


def test_callback_failure_and_capability_refusal(tmp_path, monkeypatch):
    from millrace.router.process import OwnedProcessError

    def broken(pid):
        raise RuntimeError("test callback")

    with pytest.raises(OwnedProcessError) as caught:
        invoke(prepared(tmp_path, before="time.sleep(30)"), tmp_path, started=broken)
    assert caught.value.result.root_reaped

    def unavailable():
        raise CoreContractError("core_process_missing", "test")

    monkeypatch.setattr(adapter, "require_owned_process", unavailable)
    result = invoke(prepared(tmp_path), tmp_path)
    assert result.classification == "execution_failure"
    assert result.writer_state == "stopped"
    assert result.adapter_evidence["probe_pid"] is None


def test_post_popen_setup_failure_preserves_observed_pid(tmp_path, monkeypatch):
    from millrace.router import process

    original = process.selectors.DefaultSelector
    calls = 0

    def selector():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("deterministic main setup failure after Popen")
        return original()

    monkeypatch.setattr(process.selectors, "DefaultSelector", selector)
    result = invoke(prepared(tmp_path), tmp_path)
    assert result.diagnostic == "droid_io_failed"
    assert result.adapter_evidence["pid"] is None  # Core saw no start callback.
    observed = result.adapter_evidence["process_pid"]
    assert type(observed) is int and observed > 0
    assert result.writer_state == "may_live"
    with pytest.raises(ProcessLookupError):
        os.kill(observed, 0)


def test_probe_failure_cannot_be_relabelled_stopped(tmp_path):
    plan = prepared(tmp_path, probe="time.sleep(30)")
    result = invoke(plan, tmp_path, timeout=0.1)
    assert result.adapter_evidence["phase"] == "preflight"
    assert result.adapter_evidence["probe_pid"] > 0
    assert result.writer_state == "may_live"
    with pytest.raises(RouterError):
        original = facts(plan, result, None)
        c.validate_completion(plan, replace(original, writer_state="stopped"))


@pytest.mark.parametrize("key,value", [
    ("invocation_id", "foreign"), ("pid", 0), ("session_id", ""),
    ("session_id", "another-valid-session"),
    ("observed_version", "1.0.0"), ("stdout_bytes", 0),
    ("group_state", "running"), ("cleanup_forced", True), ("stdout_eof", False),
])
def test_completion_evidence_corruption(tmp_path, key, value):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    evidence = copy.deepcopy(result.adapter_evidence)
    evidence[key] = value
    with pytest.raises(RouterError):
        original = facts(plan, result, result.adapter_evidence["pid"])
        c.validate_completion(plan, replace(original, evidence=evidence))
