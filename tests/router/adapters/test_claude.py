"""P4c native result/configuration/process proofs; no provider calls."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from claude_support import FINAL, cleanup_proof, config, envelope, fake, request
from millrace.router.execution import CompletionFacts, RouterError

from millrace_plus.router import claude as c
from millrace_plus.router import native_diagnostic as nd


def prepared(tmp: Path, *, claude_version: str = "2.1.296", **options: Any) -> Any:
    req = request(
        tmp, executable=str(fake(tmp, **options)), claude_version=claude_version
    )
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


@pytest.mark.parametrize("layer", ["runtime", "profile", "workflow", "plane", "node"])
@pytest.mark.parametrize(
    "selected",
    [
        {},
        {"model": "operator-selected"},
        {"reasoning_effort": "high"},
        {"model": "operator-selected", "reasoning_effort": "high"},
    ],
)
def test_independent_model_effort_overrides_preserve_omitted_native_defaults(
    tmp_path: Path, layer: str, selected: dict
) -> None:
    req = request(tmp_path)
    if layer == "profile":
        profile = json.loads(req.files["claude.json"])
        profile.update(selected)
        req = replace(
            req, files={**req.files, "claude.json": json.dumps(profile).encode()}
        )
    elif layer == "node":
        req = replace(req, node_config={"prompt": "task.txt", **selected})
    else:
        req = replace(req, **{layer: selected})
    plan = c.ClaudePrintAdapter().plan(req)
    assert plan.settings["effective"] == selected
    for setting, flag in (("model", "--model"), ("reasoning_effort", "--effort")):
        if setting in selected:
            assert plan.argv[plan.argv.index(flag) + 1] == selected[setting]
            assert plan.settings["provenance"][setting]["source"] == layer
        else:
            assert flag not in plan.argv


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


@pytest.mark.parametrize("version", ["2.1.296", "2.1.305"])
@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_supported_profile_pins_exact_native_version(
    tmp_path: Path, version: str, ending: bytes
) -> None:
    capture = tmp_path / "capture.json"
    plan = prepared(
        tmp_path,
        claude_version=version,
        version=f"{version} (Claude Code)".encode() + ending,
        capture=str(capture),
    )
    assert plan.details["expected_version"] == version
    result = run(plan, tmp_path)
    assert capture.exists() and result.classification == "completed"
    assert result.adapter_evidence["observed_version"] == version
    assert plan.settings["effective"] == {}
    assert "--model" not in plan.argv and "--effort" not in plan.argv


@pytest.mark.parametrize("version", ["2.1.297", "2.1.306", "2.1", "latest", [], None])
def test_unsupported_profile_version_refuses_without_host_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: Any
) -> None:
    req = request(tmp_path)
    profile = json.loads(req.files["claude.json"])
    profile["claude_version"] = version
    req = replace(req, files={**req.files, "claude.json": json.dumps(profile).encode()})

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("unsupported profile accessed host")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for operation, value in (
        (c.ClaudePrintAdapter().validate_configuration, config(req)),
        (c.ClaudePrintAdapter().plan, req),
    ):
        with pytest.raises(RouterError, match="unsupported_claude_version"):
            operation(value)


@pytest.mark.parametrize(
    "selected,actual",
    [
        ("2.1.296", "2.1.305"),
        ("2.1.305", "2.1.296"),
        ("2.1.305", "2.1.306"),
    ],
)
def test_preflight_refuses_other_supported_or_unknown_version(
    tmp_path: Path, selected: str, actual: str
) -> None:
    capture = tmp_path / "capture.json"
    plan = prepared(
        tmp_path,
        claude_version=selected,
        version=f"{actual} (Claude Code)\n".encode(),
        capture=str(capture),
    )
    started = []
    result = run(plan, tmp_path, started=started.append)
    assert not started and not capture.exists()
    assert result.classification == "execution_failure"
    assert result.diagnostic == "claude_version_mismatch" and result.stdout == b""
    assert result.adapter_evidence["observed_version"] is None


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
        if case in {"large_prompt", "delayed_child", "duplex", "cancel"}:
            expected = "cancelled" if case == "cancel" else "completed"
            assert (
                result.classification == expected and result.writer_state == "stopped"
            )
            if case == "cancel":
                assert result.stdout == b""
                assert result.adapter_evidence["cleanup"] == cleanup_proof(pids[0])
        else:
            assert (
                result.stdout == b""
                and result.classification != "completed"
                and result.writer_state == "may_live"
            )
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.mark.parametrize(
    "missing",
    [
        None,
        "root_reaped",
        "stdin_closed",
        "stdout_eof",
        "stderr_eof",
        "group_present",
        "group_unknown",
        "deadline",
        "earlier_timeout",
    ],
)
def test_cancellation_cleanup_requires_each_live_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str | None
) -> None:
    class Pipe:
        def __init__(self, descriptor: int) -> None:
            self.descriptor, self.closed = descriptor, False

        def fileno(self) -> int:
            return self.descriptor

        def close(self) -> None:
            self.closed = True

    pipes = {name: Pipe(fd) for fd, name in enumerate(("stdin", "stdout", "stderr"))}
    child = SimpleNamespace(
        pid=123,
        **pipes,
        poll=lambda: None if missing == "root_reaped" else -15,
    )

    class Selector:
        def __init__(self) -> None:
            self.keys: dict[Pipe, Any] = {}

        def register(self, pipe: Pipe, event: int, name: str) -> None:
            self.keys[pipe] = SimpleNamespace(fileobj=pipe, fd=pipe.fileno(), data=name)

        def unregister(self, pipe: Pipe) -> None:
            del self.keys[pipe]

        def select(self, timeout: float) -> list[tuple[Any, int]]:
            return [
                (key, 0)
                for key in self.keys.values()
                if not (key.data == "stdin" and missing == "stdin_closed")
            ]

        def close(self) -> None:
            self.keys.clear()

    elapsed = 0.0

    def clock() -> float:
        nonlocal elapsed
        elapsed += 6 if missing == "deadline" else 0.25
        return elapsed

    def read(fd: int, count: int) -> bytes:
        if missing == ("stdout_eof" if fd == 1 else "stderr_eof"):
            raise BlockingIOError
        return b""

    groups = {"group_present": "present", "group_unknown": "unknown"}
    started = []
    polls = 0
    group_reads = 0

    def poll() -> bool:
        nonlocal polls
        if not started:
            return False
        polls += 1
        return not (missing == "earlier_timeout" and polls == 1)

    def group(pid: int) -> str:
        nonlocal group_reads
        group_reads += 1
        if missing == "earlier_timeout" and group_reads == 1:
            return "present"
        return groups.get(missing, "absent")

    monkeypatch.setattr(c.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(c.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(
        c,
        "os",
        SimpleNamespace(
            name="posix",
            set_blocking=lambda *a: None,
            killpg=lambda *a: None,
            read=read,
        ),
    )
    monkeypatch.setattr(c, "time", SimpleNamespace(monotonic=clock, time=lambda: 0))
    monkeypatch.setattr(c, "_group", group)
    result = c._process(
        ("inert",),
        tmp_path,
        {},
        0.1 if missing == "earlier_timeout" else 100,
        poll,
        started.append,
        b"prompt",
        1024,
        1024,
    )
    proof = result.cleanup
    expected = "timeout" if missing == "earlier_timeout" else "cancelled"
    assert result.transport == expected and started == [123]
    assert result.writer == ("stopped" if missing is None else "may_live")
    assert proof == cleanup_proof(
        return_reason="confirmed"
        if missing in {None, "earlier_timeout"}
        else "deadline",
        root_reaped=missing != "root_reaped",
        stdin_closed=missing != "stdin_closed",
        stdout_eof=missing != "stdout_eof",
        stderr_eof=missing != "stderr_eof",
        group_state=groups.get(missing, "absent"),
    )
    # finally closes descriptors, but cannot upgrade an unobserved EOF/close fact.
    assert all(pipe.closed for pipe in pipes.values())


@pytest.mark.parametrize(
    "error,expected",
    [(None, "present"), (ProcessLookupError, "absent"), (PermissionError, "unknown")],
)
def test_group_absence_requires_a_positive_lookup(
    monkeypatch: pytest.MonkeyPatch, error: type[OSError] | None, expected: str
) -> None:
    def lookup(pid: int, sig: int) -> None:
        assert (pid, sig) == (123, 0)
        if error is not None:
            raise error

    monkeypatch.setattr(c, "os", SimpleNamespace(killpg=lookup))
    assert c._group(123) == expected


@pytest.mark.parametrize("ignore_term", [False, True])
def test_cancelled_cleanup_keeps_no_semantics_after_term_or_kill(
    tmp_path: Path, ignore_term: bool
) -> None:
    ready = tmp_path / "ready"
    wire = json.dumps(envelope()).encode()
    handler = (
        "signal.SIG_IGN"
        if ignore_term
        else f"lambda *_: (os.write(1, {wire!r}), sys.exit(0))"
    )
    before = (
        f"signal.signal(signal.SIGTERM, {handler});"
        f"Path({str(ready)!r}).write_text('ready');time.sleep(10)"
    )
    pids = []
    result = run(
        prepared(tmp_path, before=before),
        tmp_path,
        poll=ready.exists,
        started=pids.append,
    )
    assert result.classification == "cancelled" and result.writer_state == "stopped"
    assert result.stdout == b"" and result.diagnostic == "claude_cancelled"
    assert result.adapter_evidence["native_stdout_bytes"] == (
        0 if ignore_term else len(wire)
    )
    assert result.adapter_evidence["semantic_sha256"] is None
    assert result.adapter_evidence["cleanup"] == cleanup_proof(pids[0])


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


@pytest.mark.parametrize("version", ["2.1.296", "2.1.305"])
def test_completion_evidence_integrity(tmp_path: Path, version: str) -> None:
    plan = prepared(
        tmp_path,
        claude_version=version,
        version=f"{version} (Claude Code)\n".encode(),
    )
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
        ("observed_version", "2.1.305" if version == "2.1.296" else "2.1.296"),
        ("observed_version", "2.1.306"),
    ):
        with pytest.raises(RouterError):
            c.ClaudePrintAdapter().validate_completion(
                plan, replace(facts, evidence={**e, key: value})
            )
    with pytest.raises(RouterError, match="completion_integrity"):
        c.ClaudePrintAdapter().validate_completion(
            replace(plan, details={**plan.details, "expected_version": "2.1.306"}),
            facts,
        )


@pytest.mark.parametrize("version", ["2.1.296", "2.1.305"])
@pytest.mark.parametrize("phase", ["main", "probe"])
def test_cancelled_stopped_requires_matching_cleanup_evidence(
    tmp_path: Path, version: str, phase: str
) -> None:
    plan = c.ClaudePrintAdapter().plan(request(tmp_path, claude_version=version))
    evidence = {
        **c._evidence(plan.invocation_id),
        "phase": phase,
        "observed_version": version if phase == "main" else None,
        "probe_pid": 321 if phase == "main" else 123,
        "terminal_state": "error",
        "failure_code": "claude_cancelled",
        "cleanup": cleanup_proof(),
    }
    facts = CompletionFacts(
        plan.invocation_id,
        123 if phase == "main" else None,
        b"",
        b"",
        "cancelled",
        "stopped",
        "claude_cancelled",
        -15,
        evidence,
        "adapter",
    )
    adapter = c.ClaudePrintAdapter()
    adapter.validate_completion(plan, facts)
    legacy = {key: value for key, value in evidence.items() if key != "cleanup"}
    adapter.validate_completion(
        plan, replace(facts, writer_state="may_live", evidence=legacy)
    )
    with pytest.raises(RouterError, match="completion_integrity"):
        adapter.validate_completion(plan, replace(facts, evidence=legacy))
    uncertain = {**evidence, "cleanup": cleanup_proof(return_reason="deadline")}
    adapter.validate_completion(
        plan, replace(facts, writer_state="may_live", evidence=uncertain)
    )
    with pytest.raises(RouterError, match="completion_integrity"):
        adapter.validate_completion(plan, replace(facts, evidence=uncertain))
    timeout = {**evidence, "failure_code": "claude_timeout"}
    adapter.validate_completion(
        plan,
        replace(
            facts,
            transport="timeout",
            writer_state="may_live",
            diagnostic="claude_timeout",
            evidence=timeout,
        ),
    )
    with pytest.raises(RouterError, match="completion_integrity"):
        adapter.validate_completion(
            plan,
            replace(
                facts,
                transport="timeout",
                diagnostic="claude_timeout",
                evidence=timeout,
            ),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "format",
        "wrong_pid",
        "bool_pid",
        "zero_pid",
        "return_reason",
        "root_reaped",
        "stdin_closed",
        "stdout_eof",
        "stderr_eof",
        "group_state",
        "unknown_group",
        "bool_fact",
        "missing_field",
        "extra_field",
        "exit_code",
        "null_exit",
        "failure_code",
        "unstarted",
        "preflight",
    ],
)
def test_invalid_cleanup_proof_cannot_assert_stopped(
    tmp_path: Path, mutation: str
) -> None:
    plan = c.ClaudePrintAdapter().plan(request(tmp_path))
    proof = cleanup_proof()
    evidence = {
        **c._evidence(plan.invocation_id),
        "phase": "main",
        "observed_version": "2.1.296",
        "probe_pid": 321,
        "terminal_state": "error",
        "failure_code": "claude_cancelled",
        "cleanup": proof,
    }
    facts = CompletionFacts(
        plan.invocation_id,
        123,
        b"",
        b"",
        "cancelled",
        "stopped",
        "claude_cancelled",
        -15,
        evidence,
        "adapter",
    )
    replacements = {
        "format": ("format", "unknown"),
        "wrong_pid": ("root_pid", 124),
        "bool_pid": ("root_pid", True),
        "zero_pid": ("root_pid", 0),
        "return_reason": ("return_reason", "unknown"),
        "group_state": ("group_state", "present"),
        "unknown_group": ("group_state", "unknown"),
        "bool_fact": ("stdout_eof", 1),
    }
    if mutation in replacements:
        key, value = replacements[mutation]
        proof[key] = value
    elif mutation in {"root_reaped", "stdin_closed", "stdout_eof", "stderr_eof"}:
        proof[mutation] = False
    elif mutation == "missing_field":
        del proof["stdout_eof"]
    elif mutation == "extra_field":
        proof["unexpected"] = True
    elif mutation in {"exit_code", "null_exit"}:
        facts = replace(facts, exit_code=True if mutation == "exit_code" else None)
    elif mutation == "failure_code":
        evidence["failure_code"] = "claude_timeout"
        facts = replace(facts, diagnostic="claude_timeout")
    elif mutation == "unstarted":
        facts = replace(facts, pid=None)
    else:
        evidence.update(phase="preflight", observed_version=None, probe_pid=None)
        facts = replace(facts, pid=None)
    with pytest.raises(RouterError, match="completion_integrity"):
        c.ClaudePrintAdapter().validate_completion(plan, facts)


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


# Opt-in native diagnostic bridge (launch/evidence v2). No provider calls.

BRIDGE = {
    "context_path": "state/native-context.json",
    "outbox_path": "state/native-diagnostics",
}
CONTEXT_ID = "00000000-0000-4000-8000-0000000000aa"
INPUT_SHA = "a" * 64


def write_context(root: Path, **changes: Any) -> None:
    path = root / BRIDGE["context_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "format": "millrace-native-context/v1",
        "context_id": CONTEXT_ID,
        "input_sha256": INPUT_SHA,
        **changes,
    }
    path.write_text(json.dumps(value))


def bridged(
    tmp: Path,
    *,
    version: str = "2.1.296",
    context: bool = True,
    changes: dict[str, Any] | None = None,
    **options: Any,
) -> Any:
    if "executable" in options:
        executable = options.pop("executable")
    else:
        native = f"{version} (Claude Code)\n".encode()
        executable = str(fake(tmp / "peer", version=native, **options))
    req = replace(
        request(tmp, executable=executable, claude_version=version),
        **(changes or {}),
    )
    req = replace(req, node_config={**req.node_config, "diagnostic_bridge": BRIDGE})
    if context:
        write_context(tmp)
    return c.ClaudePrintAdapter().plan(req)


def bridged_run(plan: Any, tmp: Path, **kwargs: Any) -> tuple[Any, CompletionFacts]:
    pids: list[int] = []
    result = run(plan, tmp, started=pids.append, **kwargs)
    facts = CompletionFacts(
        plan.invocation_id,
        pids[-1] if pids else None,
        result.stdout,
        result.stderr,
        result.classification,
        result.writer_state,
        result.diagnostic,
        result.exit_code,
        result.adapter_evidence,
        "adapter",
    )
    return result, facts


def canonical_sha(value: object) -> str:
    """Independent canonical digest (sorted, compact, UTF-8, finite)."""
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def binding(plan: Any, evidence: Any) -> dict[str, object]:
    """The seven v2 facts recomputed from the actual plan and terminal evidence."""
    requested, effective = plan.settings["requested"], plan.settings["effective"]
    return {
        "settings_sha256": canonical_sha(plan.settings),
        "observed_version": evidence["observed_version"],
        "requested_model": requested.get("model"),
        "requested_effort": requested.get("reasoning_effort"),
        "resolved_model": effective.get("model"),
        "resolved_effort": effective.get("reasoning_effort"),
        "host_route_sha256": canonical_sha(
            {
                "adapter_id": "claude_print",
                "adapter_contract": "millrace-claude-print/v1",
                "executable": plan.argv[0],
                "native_home": None,
            }
        ),
    }


def published(plan: Any, tmp: Path, result: Any) -> nd.NativeDiagnosticV2:
    """Verify the actual sidecar against the retained launch and evidence."""
    e = result.adapter_evidence
    ref = nd.DiagnosticBridgeRef(**e["diagnostic_bridge"])
    assert ref.path == f"{BRIDGE['outbox_path']}/{CONTEXT_ID}/{plan.invocation_id}.json"
    raw = (tmp / ref.path).read_bytes()
    assert set(json.loads(raw)) == nd.RECORD_V2_FIELDS
    assert json.loads(raw)["format"] == "millrace-native-diagnostic/v2"
    record = nd.verify_record(
        raw,
        ref,
        nd.BridgeConfig.load(BRIDGE),
        {
            "context_id": CONTEXT_ID,
            "input_sha256": INPUT_SHA,
            "invocation_id": plan.invocation_id,
            "plane_id": "main",
            "node_id": "draft",
            "producing_revision": "revision",
            "adapter_id": "claude_print",
            "adapter_contract": "millrace-claude-print/v1",
            "evidence_format": c.RESULT_FORMAT_V2,
            "launch_sha256": hashlib.sha256(nd.canonical(plan.details)).hexdigest(),
            "transport": result.classification,
            "writer_state": result.writer_state,
            "failure_code": e["failure_code"],
            "native_diagnostic": e["native_diagnostic"],
            **binding(plan, e),
        },
    )
    assert isinstance(record, nd.NativeDiagnosticV2)
    return record


def test_bridge_disabled_launch_and_evidence_stay_legacy_v1(tmp_path: Path) -> None:
    plan = prepared(tmp_path)
    req = request(tmp_path)
    assert plan.details == {
        "format": "millrace-claude-print-launch/v1",
        "profile_path": "claude.json",
        "profile_sha256": hashlib.sha256(req.files["claude.json"]).hexdigest(),
        "prompt_source": "task.txt",
        "prompt_source_sha256": hashlib.sha256(b"Review.").hexdigest(),
        "prompt_path": str(tmp_path / "invocation" / c.PROMPT_NAME),
        "prompt_sha256": hashlib.sha256(
            plan.generated_controls[c.PROMPT_NAME]
        ).hexdigest(),
        "schema_sha256": hashlib.sha256(
            plan.generated_controls[c.SCHEMA_NAME]
        ).hexdigest(),
        "expected_version": "2.1.296",
        "native_inheritance": True,
    }
    result, facts = bridged_run(plan, tmp_path)
    assert result.classification == "completed"
    e = result.adapter_evidence
    assert e["format"] == "millrace-claude-print-result/v1"
    assert set(e) == c.EVIDENCE_FIELDS
    assert not (tmp_path / "state").exists()
    c.ClaudePrintAdapter().validate_completion(plan, facts)
    # A legacy capture never accepts, and is never reinterpreted with, v2 members.
    for evidence in (
        {**e, "native_diagnostic": None, "diagnostic_bridge": None},
        {**e, "format": c.RESULT_FORMAT_V2},
        {
            **e,
            "format": c.RESULT_FORMAT_V2,
            "native_diagnostic": None,
            "diagnostic_bridge": None,
        },
    ):
        with pytest.raises(RouterError, match="completion_integrity"):
            c.ClaudePrintAdapter().validate_completion(
                plan, replace(facts, evidence=evidence)
            )


@pytest.mark.parametrize(
    "bridge",
    [
        None,
        {"context_path": "state/native-context.json"},
        {**BRIDGE, "outbox_path": "/abs"},
        {**BRIDGE, "context_path": "../context.json"},
        {**BRIDGE, "outbox_path": BRIDGE["context_path"]},
        {**BRIDGE, "extra": "x"},
    ],
)
def test_bridge_configuration_is_strict(tmp_path: Path, bridge: object) -> None:
    req = request(tmp_path)
    req = replace(req, node_config={**req.node_config, "diagnostic_bridge": bridge})
    with pytest.raises(RouterError):
        c.ClaudePrintAdapter().validate_configuration(config(req))
    with pytest.raises(RouterError):
        c.ClaudePrintAdapter().plan(req)


def test_bridge_launch_v2_is_pure_and_extends_v1_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = c.ClaudePrintAdapter().plan(request(tmp_path))
    req = request(tmp_path)
    req = replace(req, node_config={**req.node_config, "diagnostic_bridge": BRIDGE})

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("planning touched the bridge filesystem")

    with monkeypatch.context() as patch:
        for name in ("preflight", "publish", "_read", "_walk", "_file"):
            patch.setattr(nd, name, forbidden)
        patch.setattr(os, "open", forbidden)
        c.ClaudePrintAdapter().validate_configuration(config(req))
        plan = c.ClaudePrintAdapter().plan(req)
    assert plan.details == {
        **legacy.details,
        "format": "millrace-claude-print-launch/v2",
        "diagnostic_bridge": BRIDGE,
        "plane_id": "main",
        "node_id": "draft",
        "producing_revision": "revision",
    }
    assert (plan.argv, plan.environment, plan.settings) == (
        legacy.argv,
        legacy.environment,
        legacy.settings,
    )
    assert plan.generated_controls == legacy.generated_controls
    assert not (tmp_path / "state").exists()
    for details in (
        {**plan.details, "format": c.LAUNCH_FORMAT},
        {**plan.details, "diagnostic_bridge": {**BRIDGE, "outbox_path": "/abs"}},
        {**plan.details, "plane_id": ""},
        {**plan.details, "extra": 1},
        {k: v for k, v in plan.details.items() if k != "producing_revision"},
        {**legacy.details, "format": "millrace-claude-print-launch/v3"},
    ):
        with pytest.raises(RouterError):
            c._details(replace(plan, details=details))


@pytest.mark.parametrize("version", ["2.1.296", "2.1.305"])
@pytest.mark.parametrize("shape", ["claude_native_error", "claude_native_exit"])
@pytest.mark.parametrize("status", [429, 500, 529, 401, 403])
def test_bridge_exports_typed_status_for_both_terminal_error_shapes(
    tmp_path: Path, version: str, shape: str, status: int
) -> None:
    secret = "sk-provider-secret-value"
    value = envelope(
        is_error=True,
        api_error_status=status,
        result=f"API Error: {status} rate limited {secret}",
        structured_output=None,
    )
    plan = bridged(
        tmp_path,
        version=version,
        wire=json.dumps(value).encode(),
        stderr=f"HTTP {status} {secret}".encode(),
        exit_code=1 if shape == "claude_native_exit" else 0,
    )
    result, facts = bridged_run(plan, tmp_path)
    transport = c.STATUS_FAILURES[shape]
    assert (result.classification, result.diagnostic) == (transport, shape)
    assert result.writer_state == "stopped" and result.stdout == b""
    e = result.adapter_evidence
    assert e["format"] == c.RESULT_FORMAT_V2
    assert set(e) == c.EVIDENCE_FIELDS | {"native_diagnostic", "diagnostic_bridge"}
    assert e["native_diagnostic"] == {
        "protocol": "claude-print-json/v1",
        "api_error_status": status,
    }
    record = published(plan, tmp_path, result)
    assert record.diagnostic.api_error_status == status
    # The actual exact banner probe established the observed version.
    assert record.observed_version == version == e["observed_version"]
    raw = (tmp_path / e["diagnostic_bridge"]["path"]).read_bytes()
    # Only finite structured facts are retained; no prose, stderr or secret.
    for text in (raw, json.dumps(e).encode()):
        assert b"rate limited" not in text and secret.encode() not in text
    c.ClaudePrintAdapter().validate_completion(plan, facts)


@pytest.mark.parametrize("shape", ["claude_native_error", "claude_native_exit"])
@pytest.mark.parametrize("status", [99, 600, 777])
def test_bridge_retains_unknown_integer_status_as_data(
    tmp_path: Path, shape: str, status: int
) -> None:
    """Unknown integers are retained, never capped or given positive meaning."""
    value = envelope(is_error=True, api_error_status=status, structured_output=None)
    plan = bridged(
        tmp_path,
        wire=json.dumps(value).encode(),
        exit_code=1 if shape == "claude_native_exit" else 0,
    )
    result, facts = bridged_run(plan, tmp_path)
    assert result.diagnostic == shape
    assert result.adapter_evidence["native_diagnostic"] == {
        "protocol": "claude-print-json/v1",
        "api_error_status": status,
    }
    assert published(plan, tmp_path, result).diagnostic.api_error_status == status
    c.ClaudePrintAdapter().validate_completion(plan, facts)


@pytest.mark.parametrize(
    "case",
    [
        "success",
        "absent",
        "null",
        "string",
        "bool",
        "float",
        "not_is_error",
        "is_error_string",
        "wrong_type",
        "prose_only",
        "stderr_only",
        "unparseable_exit",
        "array_envelope",
    ],
)
def test_bridge_status_is_typed_null_without_selected_envelope(
    tmp_path: Path, case: str
) -> None:
    value = envelope(is_error=True, structured_output=None, api_error_status=429)
    options: dict[str, Any] = {}
    if case == "success":
        value = envelope()
    elif case == "absent":
        value.pop("api_error_status")
    elif case == "null":
        value["api_error_status"] = None
    elif case == "string":
        value["api_error_status"] = "429"
    elif case == "bool":
        value["api_error_status"] = True
    elif case == "float":
        value["api_error_status"] = 429.0
    elif case == "not_is_error":
        value = envelope(api_error_status=429)
    elif case == "is_error_string":
        value["is_error"] = "true"
    elif case == "wrong_type":
        value["type"] = "assistant"
    elif case == "prose_only":
        value.update(api_error_status=None, result="API Error: 429 rate limit")
        value["errors"] = ["429 Too Many Requests"]
    elif case == "stderr_only":
        value["api_error_status"] = None
        options.update(stderr=b"HTTP 429 quota rate_limit_error", exit_code=1)
    wire = json.dumps([value] if case == "array_envelope" else value).encode()
    if case == "unparseable_exit":
        wire = b"429 Too Many Requests"
        options["exit_code"] = 1
    plan = bridged(tmp_path, wire=wire, **options)
    result, facts = bridged_run(plan, tmp_path)
    assert (result.classification == "completed") == (case == "success")
    e = result.adapter_evidence
    assert e["native_diagnostic"] == {
        "protocol": "claude-print-json/v1",
        "api_error_status": None,
    }
    assert published(plan, tmp_path, result).diagnostic.api_error_status is None
    c.ClaudePrintAdapter().validate_completion(plan, facts)


@pytest.mark.parametrize("case", ["timeout", "cancel", "spawn", "probe_mismatch"])
def test_bridge_stop_and_spawn_failures_keep_lifecycle_and_null_status(
    tmp_path: Path, case: str
) -> None:
    value = envelope(is_error=True, structured_output=None, api_error_status=429)
    options: dict[str, Any] = {"wire": json.dumps(value).encode()}
    kwargs: dict[str, Any] = {}
    if case == "spawn":
        options = {"executable": str(tmp_path / "missing-claude")}
    elif case == "probe_mismatch":
        options["exit_code"] = 1
    else:
        options["before"] = "time.sleep(10)"
    plan = bridged(tmp_path, **options)
    if case == "probe_mismatch":
        plan = replace(plan, details={**plan.details, "expected_version": "2.1.305"})
    if case == "timeout":
        kwargs["timeout"] = 1
    if case == "cancel":
        started: list[int] = []
        kwargs["poll"] = lambda: bool(started)
        result = run(plan, tmp_path, started=started.append, **kwargs)
        facts = CompletionFacts(
            plan.invocation_id,
            started[-1],
            result.stdout,
            result.stderr,
            result.classification,
            result.writer_state,
            result.diagnostic,
            result.exit_code,
            result.adapter_evidence,
            "adapter",
        )
    else:
        result, facts = bridged_run(plan, tmp_path, **kwargs)
    expected = {
        "timeout": ("timeout", "claude_timeout"),
        "cancel": ("cancelled", "claude_cancelled"),
        "spawn": ("execution_failure", "claude_spawn_failed"),
        "probe_mismatch": ("execution_failure", "claude_version_mismatch"),
    }[case]
    assert (result.classification, result.diagnostic) == expected
    e = result.adapter_evidence
    assert e["native_diagnostic"]["api_error_status"] is None
    record = published(plan, tmp_path, result)
    assert (record.diagnostic.transport, record.diagnostic.writer_state) == (
        result.classification,
        result.writer_state,
    )
    if case in {"spawn", "probe_mismatch"}:
        # No established probe: null, never the launch's expected version pin.
        assert record.observed_version is None
        assert isinstance(plan.details["expected_version"], str)
    if case != "probe_mismatch":
        c.ClaudePrintAdapter().validate_completion(plan, facts)
    if case == "cancel":
        # Positive cancelled/stopped still needs the existing confirmed cleanup.
        assert result.writer_state == "stopped"
        assert e["cleanup"]["return_reason"] == "confirmed"
        legacy = {k: v for k, v in e.items() if k != "cleanup"}
        with pytest.raises(RouterError, match="completion_integrity"):
            c.ClaudePrintAdapter().validate_completion(
                plan, replace(facts, evidence=legacy)
            )
        renamed = replace(facts, diagnostic="claude_timeout")
        with pytest.raises(RouterError, match="completion_integrity"):
            c.ClaudePrintAdapter().validate_completion(
                plan,
                replace(renamed, evidence={**e, "failure_code": "claude_timeout"}),
            )


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", "claude_bridge_context_missing"),
        ("invalid_json", "claude_bridge_context_invalid"),
        ("extra_field", "claude_bridge_context_invalid"),
        ("wrong_format", "claude_bridge_context_invalid"),
        ("unsafe_id", "claude_bridge_context_invalid"),
        ("symlink", "claude_bridge_context_invalid"),
        ("directory", "claude_bridge_context_invalid"),
        ("outbox_symlink", "claude_bridge_outbox_invalid"),
    ],
)
def test_bridge_preflight_refusal_is_stopped_failure_before_native_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, code: str
) -> None:
    plan = bridged(tmp_path, context=case not in {"missing", "symlink", "directory"})
    context = tmp_path / BRIDGE["context_path"]
    context.parent.mkdir(parents=True, exist_ok=True)
    if case == "invalid_json":
        context.write_text("{")
    elif case == "extra_field":
        write_context(tmp_path, extra=1)
    elif case == "wrong_format":
        write_context(tmp_path, format="millrace-native-context/v0")
    elif case == "unsafe_id":
        write_context(tmp_path, context_id="../escape")
    elif case == "symlink":
        (tmp_path / "elsewhere.json").write_text("{}")
        context.symlink_to(tmp_path / "elsewhere.json")
    elif case == "directory":
        context.mkdir()
    elif case == "outbox_symlink":
        (tmp_path / "outside").mkdir()
        (tmp_path / BRIDGE["outbox_path"]).symlink_to(tmp_path / "outside")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("native process started")
    )
    result, facts = bridged_run(plan, tmp_path)
    assert (result.stdout, result.stderr, result.exit_code) == (b"", b"", None)
    assert (result.classification, result.writer_state, result.diagnostic) == (
        "execution_failure",
        "stopped",
        code,
    )
    e = result.adapter_evidence
    assert e == {
        **c._evidence(plan.invocation_id),
        "format": c.RESULT_FORMAT_V2,
        "failure_code": code,
        "native_diagnostic": None,
        "diagnostic_bridge": None,
    }
    assert facts.pid is None
    c.ClaudePrintAdapter().validate_completion(plan, facts)
    if case == "outbox_symlink":
        assert not list((tmp_path / "outside").iterdir())
    else:
        assert not (tmp_path / BRIDGE["outbox_path"]).exists()


def test_bridge_publication_replays_and_conflict_never_overwrites(
    tmp_path: Path,
) -> None:
    plan = bridged(tmp_path)
    first, facts = bridged_run(plan, tmp_path)
    ref = first.adapter_evidence["diagnostic_bridge"]
    path = tmp_path / ref["path"]
    raw = path.read_bytes()
    # The same terminal facts for the same invocation replay the same record.
    second, _ = bridged_run(plan, tmp_path)
    assert second.adapter_evidence["diagnostic_bridge"] == ref
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    # Conflicting retained bytes are never overwritten and never referenced.
    path.unlink()
    path.write_bytes(raw.replace(b'"stopped"', b'"unknown"'))
    conflicted = path.read_bytes()
    third, third_facts = bridged_run(plan, tmp_path)
    assert third.classification == "completed" and json.loads(third.stdout) == FINAL
    assert third.adapter_evidence["diagnostic_bridge"] is None
    assert third.adapter_evidence["native_diagnostic"] == {
        "protocol": "claude-print-json/v1",
        "api_error_status": None,
    }
    assert path.read_bytes() == conflicted
    assert [p.name for p in path.parent.iterdir()] == [path.name]
    c.ClaudePrintAdapter().validate_completion(plan, third_facts)
    c.ClaudePrintAdapter().validate_completion(plan, facts)


@pytest.mark.parametrize("case", ["inherited_runtime", "node_override", "omitted"])
def test_bridge_exports_actual_binding_facts_not_graph_defaults(
    tmp_path: Path, case: str
) -> None:
    runtime = {"model": "runtime-model-sentinel", "reasoning_effort": "high"}
    changes: dict[str, Any] = {
        "inherited_runtime": {"runtime": runtime},
        "node_override": {
            "runtime": runtime,
            "node_config": {"prompt": "task.txt", "reasoning_effort": "low"},
        },
        "omitted": {},
    }[case]
    plan = bridged(tmp_path, version="2.1.305", changes=changes)
    # The captured graph (workflow/node bytes) names no model; only the actual
    # inherited runtime layer can supply these values.
    graph = request(tmp_path)
    assert graph.workflow == {} and set(graph.node_config) == {"prompt"}
    result, facts = bridged_run(plan, tmp_path)
    assert result.classification == "completed"
    record = published(plan, tmp_path, result)
    expected = {
        "inherited_runtime": ("runtime-model-sentinel", "high"),
        "node_override": ("runtime-model-sentinel", "low"),
        "omitted": (None, None),
    }[case]
    assert (record.requested_model, record.requested_effort) == expected
    assert (record.resolved_model, record.resolved_effort) == expected
    assert record.settings_sha256 == canonical_sha(plan.settings)
    assert record.settings_sha256 != canonical_sha({})
    assert record.observed_version == "2.1.305"
    raw = (tmp_path / result.adapter_evidence["diagnostic_bridge"]["path"]).read_bytes()
    # Only digests and the four named values leave the plan: no raw settings,
    # provenance, warnings, argv, executable path or host path.
    for private in (plan.argv[0], str(tmp_path), "provenance", "warnings", "--model"):
        assert private.encode() not in raw
    c.ClaudePrintAdapter().validate_completion(plan, facts)


def test_bridge_host_route_is_stable_across_prompt_but_tracks_executable(
    tmp_path: Path,
) -> None:
    first = bridged(tmp_path / "a")
    other_prompt = bridged(
        tmp_path / "b",
        executable=first.argv[0],
        changes={"files": {**request(tmp_path).files, "task.txt": b"Other."}},
    )
    assert first.generated_controls != other_prompt.generated_controls
    other_route = bridged(tmp_path / "c")
    assert other_route.argv[0] != first.argv[0]
    routes = []
    for root, plan in (
        (tmp_path / "a", first),
        (tmp_path / "b", other_prompt),
        (tmp_path / "c", other_route),
    ):
        result, _ = bridged_run(plan, root)
        routes.append(published(plan, root, result).host_route_sha256)
    assert routes[0] == routes[1] != routes[2]


def test_bridge_completion_validation_reads_no_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = envelope(is_error=True, structured_output=None, api_error_status=429)
    plan = bridged(tmp_path, wire=json.dumps(value).encode())
    result, facts = bridged_run(plan, tmp_path)
    assert result.adapter_evidence["diagnostic_bridge"] is not None
    shutil.rmtree(tmp_path / "state")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("completion validation touched the bridge filesystem")

    with monkeypatch.context() as patch:
        for name in ("preflight", "publish", "_read", "_walk", "_file"):
            patch.setattr(nd, name, forbidden)
        patch.setattr(os, "open", forbidden)
        c.ClaudePrintAdapter().validate_completion(plan, facts)


@pytest.mark.parametrize(
    "mutation",
    [
        "v1_format",
        "missing_native",
        "missing_ref",
        "extra_member",
        "protocol",
        "bool_status",
        "string_status",
        "float_status",
        "other_status_rule",
        "status_without_stdout",
        "native_extra",
        "ref_extra",
        "ref_other_invocation",
        "ref_escape",
        "ref_other_outbox",
        "ref_sha",
        "ref_without_native",
        "bridge_code_with_native",
        "launch_v1",
        "launch_other_bridge",
        "launch_extra",
    ],
)
def test_bridge_evidence_v2_corruption_refuses(tmp_path: Path, mutation: str) -> None:
    value = envelope(is_error=True, structured_output=None, api_error_status=429)
    plan = bridged(tmp_path, wire=json.dumps(value).encode())
    result, facts = bridged_run(plan, tmp_path)
    adapter = c.ClaudePrintAdapter()
    adapter.validate_completion(plan, facts)
    e = dict(result.adapter_evidence)
    native = dict(e["native_diagnostic"])
    ref = dict(e["diagnostic_bridge"])
    details = dict(plan.details)
    if mutation == "v1_format":
        e["format"] = c.RESULT_FORMAT
    elif mutation == "missing_native":
        e.pop("native_diagnostic")
    elif mutation == "missing_ref":
        e.pop("diagnostic_bridge")
    elif mutation == "extra_member":
        e["provider_class"] = "provider_usage"
    elif mutation == "protocol":
        e["native_diagnostic"] = {**native, "protocol": "codex-exec-jsonl/v1"}
    elif mutation == "bool_status":
        e["native_diagnostic"] = {**native, "api_error_status": True}
    elif mutation == "string_status":
        e["native_diagnostic"] = {**native, "api_error_status": "429"}
    elif mutation == "float_status":
        e["native_diagnostic"] = {**native, "api_error_status": 429.0}
    elif mutation == "other_status_rule":
        facts = replace(facts, diagnostic="claude_native_exit")
        e["failure_code"] = "claude_native_exit"
    elif mutation == "status_without_stdout":
        e.update(native_stdout_bytes=0, native_stdout_sha256=None)
    elif mutation == "native_extra":
        e["native_diagnostic"] = {**native, "message": "rate limited"}
    elif mutation == "ref_extra":
        e["diagnostic_bridge"] = {**ref, "size": 1}
    elif mutation == "ref_other_invocation":
        path = ref["path"].replace(plan.invocation_id, "2" * 32)
        e["diagnostic_bridge"] = {**ref, "path": path}
    elif mutation == "ref_escape":
        path = f"{BRIDGE['outbox_path']}/../{plan.invocation_id}.json"
        e["diagnostic_bridge"] = {**ref, "path": path}
    elif mutation == "ref_other_outbox":
        e["diagnostic_bridge"] = {**ref, "path": "other/" + ref["path"]}
    elif mutation == "ref_sha":
        e["diagnostic_bridge"] = {**ref, "sha256": "Z" * 64}
    elif mutation == "ref_without_native":
        e["native_diagnostic"] = None
    elif mutation == "bridge_code_with_native":
        facts = replace(facts, diagnostic="claude_bridge_context_missing")
        e["failure_code"] = "claude_bridge_context_missing"
    elif mutation == "launch_v1":
        details = {k: v for k, v in details.items() if k not in nd.LAUNCH_FIELDS}
        details["format"] = c.LAUNCH_FORMAT
    elif mutation == "launch_other_bridge":
        details["diagnostic_bridge"] = {**BRIDGE, "outbox_path": "state/elsewhere"}
    else:
        details["unexpected"] = True
    with pytest.raises(RouterError, match="completion_integrity"):
        adapter.validate_completion(
            replace(plan, details=details), replace(facts, evidence=e)
        )
