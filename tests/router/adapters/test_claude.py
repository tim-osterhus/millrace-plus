"""P4c native result/configuration/process proofs; no provider calls."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from claude_support import FINAL, config, envelope, fake, request
from millrace.router.execution import CompletionFacts, RouterError

from millrace_plus.router import claude as c


def prepared(tmp: Path, **options: Any) -> Any:
    req = request(tmp, executable=str(fake(tmp, **options)))
    return c.ClaudePrintAdapter().plan(req)


def run(
    plan: Any,
    tmp: Path,
    *,
    poll: Any = lambda: False,
    started: Any = lambda pid: None,
    timeout: float = 4,
) -> Any:
    return c.ClaudePrintAdapter().invoke(
        plan,
        cwd=tmp,
        env={"PATH": "/usr/bin:/bin"},
        deadline=time.time() + timeout,
        poll=poll,
        on_started=started,
    )


def test_configuration_and_inherited_settings(tmp_path: Path) -> None:
    adapter = c.ClaudePrintAdapter()
    req = request(tmp_path)
    adapter.validate_configuration(config(req))
    plan = adapter.plan(req)
    assert plan.settings["effective"] == {} and plan.environment == {}
    assert "--model" not in plan.argv and "--effort" not in plan.argv
    req = replace(
        req,
        runtime={"model": "base", "reasoning_effort": "low"},
        workflow={"model": "workflow"},
        node_config={"prompt": "task.txt", "model": "node"},
    )
    plan = adapter.plan(req)
    assert plan.settings["effective"] == {"model": "node", "reasoning_effort": "low"}
    for bad in ("", "-bad", " bad", "a" * 257, "a\n", True):
        with pytest.raises(RouterError):
            adapter.plan(replace(req, runtime={"model": bad}))
    for raw in (b'{"format":1,"format":2}', b"{}", b"\xff"):
        with pytest.raises(RouterError):
            adapter.plan(replace(req, files={**req.files, "claude.json": raw}))
    with pytest.raises(RouterError):
        adapter.validate_configuration(replace(config(req), runtime={"unknown": 1}))


def test_launch_and_environment_inheritance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inert-api-key-only")
    monkeypatch.setenv("MILLRACE_NATIVE_SENTINEL", "native-settings-pass")
    plan = prepared(tmp_path, capture=str(capture))
    result = run(plan, tmp_path)
    assert result.classification == "completed"
    observed = json.loads(capture.read_text())
    assert (
        observed["api"] == "inert-api-key-only"
        and observed["native"] == "native-settings-pass"
    )
    assert observed["path"] == "/usr/bin:/bin"
    assert "--dangerously-skip-permissions" in observed["argv"]
    assert (
        observed["argv"][observed["argv"].index("--permission-prompts") + 1] == "none"
    )
    assert "StructuredOutput" in plan.generated_controls["claude-prompt.txt"].decode()
    assert "inert-api-key-only" not in repr(plan) + repr(result)


def test_purity_and_version_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = c.ClaudePrintAdapter()
    req = request(tmp_path)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("live access in pure method")

    with monkeypatch.context() as m:
        m.setattr(Path, "read_bytes", forbidden)
        m.setattr(Path, "exists", forbidden)
        m.setattr(os._Environ, "__getitem__", forbidden)
        m.setattr(subprocess, "Popen", forbidden)
        adapter.validate_configuration(config(req))
        adapter.plan(req)
    for version in (b"2.1.297 (Claude Code)\n", b"2.1.296 (Claude Code)\nnoise\n"):
        started = []
        result = run(
            prepared(tmp_path, version=version), tmp_path, started=started.append
        )
        assert (
            not started
            and result.stdout == b""
            and result.diagnostic == "claude_version_mismatch"
        )


@pytest.mark.parametrize(
    "case",
    [
        "good",
        "missing",
        "null",
        "error",
        "boolstring",
        "refusal",
        "deferred",
        "aborted",
        "errors",
        "apierror",
        "denials",
        "array",
        "noise",
        "trailing",
        "duplicate",
        "nonfinite",
        "utf8",
        "semantic_limit",
        "nonzero",
    ],
)
def test_print_result_authority(tmp_path: Path, case: str) -> None:
    value = envelope()
    options = {}
    if case == "missing":
        value.pop("structured_output")
    if case == "null":
        value["structured_output"] = None
    if case == "error":
        value.update(is_error=True, subtype="error_during_execution")
    if case == "boolstring":
        value["is_error"] = "false"
    if case == "refusal":
        value["stop_reason"] = "refusal"
    if case == "deferred":
        value["stop_reason"] = "tool_deferred_unavailable"
    if case == "aborted":
        value["terminal_reason"] = "aborted_tools"
    if case == "errors":
        value["errors"] = ["bad"]
    if case == "apierror":
        value["api_error_status"] = 403
    if case == "denials":
        value["permission_denials"] = [{"tool": "Read"}]
    if case == "semantic_limit":
        value["structured_output"] = {"outcome": "READY", "summary": "x" * 65536}
    wire = json.dumps([value] if case == "array" else value).encode()
    if case == "noise":
        wire = b"noise" + wire
    if case == "trailing":
        wire += b"\n{}"
    if case == "duplicate":
        wire = b'{"type":"result","type":"result"}'
    if case == "nonfinite":
        wire = b'{"value":NaN}'
    if case == "utf8":
        wire = b"\xff"
    if case == "nonzero":
        options["exit_code"] = 1
    result = run(prepared(tmp_path, wire=wire, **options), tmp_path)
    assert (result.classification == "completed") == (case in {"good", "denials"})
    if case in {"good", "denials"}:
        assert json.loads(result.stdout) == FINAL
    else:
        assert result.stdout == b""
    if case == "array":
        assert result.diagnostic == "claude_envelope_kind_array"


@pytest.mark.parametrize(
    "case",
    [
        "large_prompt",
        "duplex",
        "term_ignored",
        "delayed_child",
        "overdue_child",
        "stdout_flood",
        "stderr_flood",
        "blocked_stdin",
        "cancel",
        "callback",
    ],
)
def test_process_controls_and_writer_uncertainty(tmp_path: Path, case: str) -> None:
    options = {}
    timeout = 3

    def poll():
        return False

    def started(pid):
        return None

    if case in {"delayed_child", "overdue_child"}:
        seconds = 0.5 if case == "delayed_child" else 10
        options["after"] = (
            "subprocess.Popen([sys.executable,'-c',"
            f"'import time;time.sleep({seconds})']);time.sleep(0.02)"
        )
        if case == "overdue_child":
            timeout = 0.4
    if case == "duplex":
        options["before"] = "os.write(2, b'e' * 60000);os.write(1, b' ' * 100000)"
    if case == "term_ignored":
        options["before"] = (
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)"
        )
        timeout = 0.4
    if case == "stdout_flood":
        options["wire"] = b" " * 1048577
    if case == "stderr_flood":
        options["stderr"] = b"x" * 65537
    if case == "blocked_stdin":
        options["before"] = "time.sleep(10)"
        timeout = 0.4
    if case == "cancel":
        options["before"] = "time.sleep(10)"

        def poll():
            return bool(pids)

    pids = []
    if case == "callback":

        def started(pid: int) -> None:
            pids.append(pid)
            raise RuntimeError("callback")
    else:
        started = pids.append
    plan = prepared(tmp_path, **options)
    if case in {"large_prompt", "blocked_stdin", "duplex"}:
        plan = c.ClaudePrintAdapter().plan(
            request(tmp_path, prompt=b"x" * 1000000, executable=plan.argv[0])
        )
    if case == "callback":
        with pytest.raises(RuntimeError):
            run(plan, tmp_path, started=started)
    else:
        result = run(plan, tmp_path, timeout=timeout, poll=poll, started=started)
        if case in {"large_prompt", "delayed_child", "duplex"}:
            assert (
                result.classification == "completed"
                and result.writer_state == "stopped"
            )
        else:
            assert (
                result.stdout == b""
                and result.classification != "completed"
                and result.writer_state == "may_live"
            )
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def test_failure_diagnostics_preserve_cause_and_scrub_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = 'inert-API-secret-"escaped"'
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("X_AUTH_DISABLED", "1")
    message = (
        "HTTP 401 model unavailable; HTTP 429 quota; "
        + secret
        + " "
        + json.dumps(secret)
        + " password=config-secret-inert"
    )
    wire = json.dumps(envelope(is_error=True, result=message)).encode()
    result = run(prepared(tmp_path, wire=wire, stderr=message.encode()), tmp_path)
    assert b"HTTP 401" in result.stderr and b"HTTP 429" in result.stderr
    assert (
        b"inert-API" not in result.stderr
        and b"config-secret-inert" not in result.stderr
    )
    assert b"[REDACTED]" in result.stderr
    huge = b"HTTP 429 quota " + b"x " * 32754 + secret.encode()
    result = run(prepared(tmp_path, wire=wire, stderr=huge), tmp_path)
    assert b"inert-API" not in result.stderr


def test_completion_evidence_integrity(tmp_path: Path) -> None:
    plan = prepared(tmp_path)
    result = run(plan, tmp_path)
    e = result.adapter_evidence
    facts = CompletionFacts(
        plan.invocation_id,
        123,
        result.stdout,
        result.stderr,
        result.classification,
        result.writer_state,
        result.diagnostic,
        result.exit_code,
        e,
        "adapter",
    )
    c.ClaudePrintAdapter().validate_completion(plan, facts)
    for key, value in (
        ("invocation_id", "wrong"),
        ("phase", "preflight"),
        ("semantic_sha256", "0" * 64),
        ("probe_pid", None),
        ("terminal_state", "error"),
    ):
        with pytest.raises(RouterError):
            c.ClaudePrintAdapter().validate_completion(
                plan, replace(facts, evidence={**e, key: value})
            )


@pytest.mark.parametrize(
    "message",
    [
        b'HTTP 401 config failed: {"password": "inert config secret 4K8X"}',
        b"HTTP 429 config failed: password='inert config secret 4K8X'",
        b'HTTP 401 config failed: api_key="inert config \\"secret\\" 4K8X"',
        rb"HTTP 401 config failed: {\"api_key\": \"inert config secret 4K8X\"}",
    ],
)
def test_quoted_credential_value_redacts_entire_secret(
    tmp_path: Path, message: bytes
) -> None:
    # Unknown config values must be removed by their label, without config reads.
    raw = json.dumps(envelope(is_error=True, result=message.decode())).encode()
    result = run(prepared(tmp_path, wire=raw, stderr=message), tmp_path)
    assert b"config secret 4K8X" not in result.stderr
    assert b"4K8X" not in result.stderr and b"inert config" not in result.stderr
    assert b"HTTP 401" in result.stderr or b"HTTP 429" in result.stderr
    assert b"[REDACTED]" in result.stderr


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_pending_stop_between_probe_and_main_never_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    plan = prepared(tmp_path)
    original = c._process
    wall_time = c.time.time
    pending = False
    mains = []

    def observed(*args: Any, **kwargs: Any) -> Any:
        nonlocal pending
        result = original(*args, **kwargs)
        if args[0][1:] == ("--version",):
            assert result.transport == "completed"
            pending = True
            if reason == "timeout":
                monkeypatch.setattr(c.time, "time", lambda: wall_time() + 10)
        return result

    monkeypatch.setattr(c, "_process", observed)
    result = run(
        plan,
        tmp_path,
        poll=lambda: pending and reason == "cancel",
        started=mains.append,
    )
    assert mains == [] and result.stdout == b""
    assert result.classification == ("cancelled" if reason == "cancel" else "timeout")
    assert result.writer_state == "stopped" and result.exit_code is None
    facts = CompletionFacts(
        plan.invocation_id,
        None,
        result.stdout,
        result.stderr,
        result.classification,
        result.writer_state,
        result.diagnostic,
        result.exit_code,
        result.adapter_evidence,
        "adapter",
    )
    c.ClaudePrintAdapter().validate_completion(plan, facts)


@pytest.mark.parametrize("stderr", [b"\n", b"HTTP 429 quota rate_limit_error"])
def test_clipped_result_keeps_complete_stderr_cause(
    tmp_path: Path, stderr: bytes
) -> None:
    wire = json.dumps(envelope(is_error=True, result="long failure " * 200)).encode()
    result = run(prepared(tmp_path, wire=wire, stderr=stderr), tmp_path)
    assert (
        result.classification == "protocol_failure" and result.writer_state == "stopped"
    )
    assert result.adapter_evidence["stderr_truncated"] is True
    if b"rate_limit_error" in stderr:
        assert b"rate_limit_error" in result.stderr
    c.ClaudePrintAdapter().validate_completion(
        prepared(tmp_path),
        CompletionFacts(
            "1" * 32,
            123,
            result.stdout,
            result.stderr,
            result.classification,
            result.writer_state,
            result.diagnostic,
            result.exit_code,
            result.adapter_evidence,
            "adapter",
        ),
    )


@pytest.mark.parametrize("raw", [b"\n", b"  partial-secret"])
def test_cut_whitespace_or_single_partial_token_is_empty(raw: bytes) -> None:
    assert c._redact(raw, {}, cut=True) == b""


def test_probe_spawn_failure_keeps_concrete_cause(tmp_path: Path) -> None:
    plan = c.ClaudePrintAdapter().plan(request(tmp_path))
    result = run(plan, tmp_path)
    assert result.diagnostic == "claude_spawn_failed"
    assert (
        result.classification == "execution_failure"
        and result.writer_state == "stopped"
    )


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize(
    "secret", ['inert "quoted" config secret 4K8X', "inert config secret 4K8X\\"]
)
def test_quoted_credential_escape_boundaries(
    tmp_path: Path, encoded: bool, secret: str
) -> None:
    payload = json.dumps({"api_key": secret})
    if encoded:
        payload = json.dumps(payload)[1:-1]
    message = ("HTTP 401 " + payload + " HTTP 429 quota").encode()
    wire = json.dumps(envelope(is_error=True, result=message.decode())).encode()
    result = run(prepared(tmp_path, wire=wire, stderr=message), tmp_path)
    assert b"4K8X" not in result.stderr and b"inert" not in result.stderr
    assert b"HTTP 401" in result.stderr and b"HTTP 429 quota" in result.stderr
    assert b"[REDACTED]" in result.stderr
