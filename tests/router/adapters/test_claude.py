"""P4c native result/configuration/process proofs; no provider calls."""

from __future__ import annotations

import json
import os
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
