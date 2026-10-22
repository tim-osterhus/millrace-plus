"""Finite native stdin/events/final-file/lifecycle proofs, without a provider."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from codex_support import FINAL, events, fake, wire
from millrace.router.execution import CompletionFacts
from test_codex_configuration import request

from millrace_plus.router import codex_configuration as c
from millrace_plus.router.codex import CodexExecAdapter


def prepared(tmp: Path, **kwargs: Any) -> tuple[Any, Path]:
    req = replace(request(tmp), executable=str(fake(tmp, **kwargs)))
    native = tmp / "native"
    native.mkdir(exist_ok=True)
    (native / "auth.json").write_text("{}")
    plan = c.plan(req)
    req.invocation_root.mkdir(exist_ok=True)
    for key, raw in plan.generated_controls.items():
        (req.invocation_root / key).write_bytes(raw)
    return plan, native


def run(
    plan: Any,
    tmp: Path,
    *,
    poll: Any = lambda: False,
    started: Any = lambda pid: None,
    timeout: float = 8,
) -> Any:
    return CodexExecAdapter().invoke(
        plan,
        cwd=tmp,
        env={"PATH": "/usr/bin:/bin", **plan.environment},
        deadline=time.time() + timeout,
        poll=poll,
        on_started=started,
    )


def test_core_seam_environment_reaches_native_child(tmp_path: Path) -> None:
    seam = tmp_path / "seam-input.json"
    seam.write_text('{"format":"millrace-seam-input/v1"}')
    observed = tmp_path / "observed-seam-path.txt"
    plan, _ = prepared(
        tmp_path,
        before=(
            f"Path({str(observed)!r}).write_text("
            "os.environ.get('MILLRACE_SEAM_INPUT', 'missing'))\n"
            "assert not any(name in os.environ for name in "
            "('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'DEEPSEEK_API_KEY'))"
        ),
    )
    result = CodexExecAdapter().invoke(
        plan,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            **plan.environment,
            "MILLRACE_SEAM_INPUT": str(seam),
            "OPENAI_API_KEY": "inert-excluded-openai-key",
            "OPENAI_BASE_URL": "http://invalid.local",
            "DEEPSEEK_API_KEY": "inert-excluded-pi-key",
        },
        deadline=time.time() + 8,
        poll=lambda: False,
        on_started=lambda pid: None,
    )
    assert result.classification == "completed", result
    assert observed.read_text() == str(seam)


@pytest.mark.parametrize(
    "output,exit_code,accepted",
    [
        pytest.param(b"codex-cli 0.163.0", 0, True, id="no-newline"),
        pytest.param(b"codex-cli 0.163.0\n", 0, True, id="lf"),
        pytest.param(b"codex-cli 0.163.0\r\n", 0, True, id="crlf"),
        pytest.param(b"codex-cli 0.153.2\n", 0, False, id="historical"),
        pytest.param(b"codex-cli 0.163.1\n", 0, False, id="wrong-patch"),
        pytest.param(b"codex-cli 0.163\n", 0, False, id="short-version"),
        pytest.param(b"codex-cli 0.163.0 build\n", 0, False, id="build-text"),
        pytest.param(b" codex-cli 0.163.0\n", 0, False, id="leading-space"),
        pytest.param(b"codex-cli 0.163.0\n", 3, False, id="nonzero"),
    ],
)
def test_exact_codex_cli_version_forms(
    tmp_path: Path, output: bytes, exit_code: int, accepted: bool
) -> None:
    plan, _ = prepared(
        tmp_path,
        version_extra=f"os.write(1, {output!r});sys.exit({exit_code})",
    )
    mains: list[int] = []
    result = run(plan, tmp_path, started=mains.append)
    assert result.writer_state == "stopped"
    probe = result.adapter_evidence["probe_pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(probe, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(probe, 0)
    if accepted:
        assert result.classification == "completed" and result.stdout == FINAL
        assert len(mains) == 1
        assert result.adapter_evidence["observed_version"] == "0.163.0"
    else:
        assert result.classification == "execution_failure"
        assert result.diagnostic == "codex_version_mismatch"
        assert result.adapter_evidence["phase"] == "preflight"
        assert mains == [] and result.stdout == b""


@pytest.mark.parametrize(
    "variant",
    [
        "misleading",
        "missing",
        "empty",
        "oversize",
        "utf8",
        "symlink",
        "fifo",
        "directory",
        "final_replaced_after_snapshot_during_group_wait",
        "final_replaced_during_read",
    ],
)
def test_exec_final_channel_and_writer_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    from millrace_plus.router import codex

    misleading = {
        "type": "item.completed",
        "item": {"id": "old", "type": "agent_message", "text": '{"outcome":"WRONG"}'},
    }
    kwargs: dict[str, Any] = {"events": events(items=[misleading])}
    if variant in {"missing", "empty", "oversize", "utf8"}:
        kwargs["final"] = {
            "missing": None,
            "empty": b"",
            "oversize": b"x" * 65537,
            "utf8": b"\xff",
        }[variant]
    if variant in {"symlink", "fifo", "directory"}:
        kwargs["final"] = None
        kwargs["after"] = {
            "symlink": "output.symlink_to(sys.argv[0])",
            "fifo": "os.mkfifo(output)",
            "directory": "output.mkdir()",
        }[variant]
    if variant == "final_replaced_after_snapshot_during_group_wait":
        child = (
            "import signal,time;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)"
        )
        kwargs["after"] = (
            f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(0.04)"
        )
    plan, _ = prepared(tmp_path, **kwargs)
    final_path = Path(plan.details["final_path"])
    if variant == "final_replaced_after_snapshot_during_group_wait":
        original = codex._read_file

        def snapshot(path: str, *args: Any) -> bytes:
            value = original(path, *args)
            if path == str(final_path):
                final_path.write_bytes(b'{"outcome":"WRONG"}')
            return value

        monkeypatch.setattr(codex, "_read_file", snapshot)
    elif variant == "final_replaced_during_read":
        original_read = os.read
        replaced = False

        def replace_during_read(fd: int, size: int) -> bytes:
            nonlocal replaced
            value = original_read(fd, size)
            if (
                not replaced
                and final_path.exists()
                and os.fstat(fd).st_ino == final_path.stat().st_ino
            ):
                changed = final_path.with_suffix(".changed")
                changed.write_bytes(FINAL)
                changed.replace(final_path)
                replaced = True
            return value

        monkeypatch.setattr(os, "read", replace_during_read)
    pids: list[int] = []
    result = run(plan, tmp_path, started=pids.append)
    good = variant in {"misleading", "final_replaced_after_snapshot_during_group_wait"}
    assert (result.classification == "completed") == good, result
    assert result.stdout == (FINAL if good else b"")
    if good:
        assert result.writer_state == (
            "may_live" if variant.startswith("final_replaced") else "stopped"
        )
        c.validate_completion(
            plan,
            CompletionFacts(
                plan.invocation_id,
                pids[0],
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


@pytest.mark.parametrize(
    "variant",
    [
        "success",
        "retry_error_large_message",
        "retry_error_nul_message",
        "retry_error_empty_message",
        "turn_failed",
        "turn_failed_natural_exit",
        "exit_nonzero",
        "extra_thread_field",
        "extra_turn_started_field",
        "extra_usage_field",
        "bad_usage",
        "trailing",
        "duplicate",
        "unknown",
        "nonstring_type",
        "invalid_json",
        "oversized_line",
    ],
)
def test_jsonl_lifecycle_and_failure_precedence(tmp_path: Path, variant: str) -> None:
    values = events()
    kwargs: dict[str, Any] = {}
    good = variant == "success" or variant.startswith("retry_error")
    if variant.startswith("retry_error"):
        message = {
            "retry_error_large_message": "x" * 8192,
            "retry_error_nul_message": "\0",
            "retry_error_empty_message": "",
        }[variant]
        values.insert(2, {"type": "error", "message": message})
    elif variant in {"turn_failed", "turn_failed_natural_exit"}:
        values[-1] = {"type": "turn.failed", "error": {"message": "failed"}}
        if variant == "turn_failed_natural_exit":
            kwargs.update(
                exit_code=1,
                after=(
                    "sys.stderr.write('native failure detail\\n');"
                    "sys.stderr.flush();time.sleep(0.1)"
                ),
            )
    elif variant == "exit_nonzero":
        kwargs["exit_code"] = 1
    elif variant == "extra_thread_field":
        values[0]["extra"] = 1
    elif variant == "extra_turn_started_field":
        values[1]["extra"] = 1
    elif variant in {"extra_usage_field", "bad_usage"}:
        values[-1]["usage"][
            "extra" if variant == "extra_usage_field" else "input_tokens"
        ] = True
    elif variant == "trailing":
        values.append({"type": "error", "message": "late"})
    elif variant == "duplicate":
        values.insert(1, values[0])
    elif variant == "unknown":
        values.insert(2, {"type": "unknown"})
    elif variant == "nonstring_type":
        values.insert(2, {"type": []})
    elif variant == "invalid_json":
        kwargs["raw_wire"] = b'{"type":"turn.started","type":"turn.completed"}\n'
    elif variant == "oversized_line":
        kwargs["raw_wire"] = b"x" * (c.LINE_LIMIT + 1)
    plan, _ = prepared(tmp_path, events=values, **kwargs)
    result = run(plan, tmp_path)
    assert (result.classification == "completed") == good, result
    assert result.stdout == (FINAL if good else b"")
    if variant == "turn_failed_natural_exit":
        assert result.writer_state == "stopped", result
        assert result.exit_code == 1, result
        assert result.diagnostic == "codex_turn_failed", result
        assert b"native failure detail" in result.stderr
    if variant == "nonstring_type":
        assert result.adapter_evidence["protocol_state"] == "invalid"


@pytest.mark.parametrize(
    "variant",
    [
        "missing",
        "auth_directory",
        "auth_device",
        "auth_fifo",
        "auth_unreadable",
        "auth_symlink",
        "version",
        "version_nonzero",
        "version_oversize",
        "version_timeout",
        "cancel_probe",
        "version_cleanup",
        "probe_created_final",
        "host_missing",
        "stale_final",
        "changed_prompt",
    ],
)
def test_preflight_and_version_prevent_main_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    extras = {
        "probe_created_final": (
            f"Path({str(tmp_path / 'invocation/codex-final.json')!r})"
            f".write_bytes({FINAL!r})"
        ),
        "version_nonzero": "sys.exit(3)",
        "version_oversize": "os.write(1, b'x' * 4097)",
        "version_timeout": "time.sleep(30)",
        "cancel_probe": (
            f"Path({str(tmp_path / 'probe-started')!r})"
            ".write_text('yes');time.sleep(30)"
        ),
        "version_cleanup": (
            "subprocess.Popen([sys.executable,'-c',"
            "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            "time.sleep(20)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL);time.sleep(0.04)"
        ),
    }
    plan, native = prepared(
        tmp_path,
        version="wrong" if variant == "version" else "0.163.0",
        version_extra=extras.get(variant, ""),
    )
    auth = native / "auth.json"
    if variant == "missing":
        auth.unlink()
    elif variant == "host_missing":
        auth.unlink()
        native.rmdir()
    elif variant == "auth_device":
        original_stat = Path.lstat

        def device_stat(path: Path, *args: Any, **kwargs: Any) -> Any:
            return (
                os.stat("/dev/null")
                if path == auth
                else original_stat(path, *args, **kwargs)
            )

        monkeypatch.setattr(Path, "lstat", device_stat)
    elif variant == "auth_directory":
        auth.unlink()
        auth.mkdir()
    elif variant == "auth_fifo":
        auth.unlink()
        os.mkfifo(auth)
    elif variant == "auth_symlink":
        auth.unlink()
        auth.symlink_to(tmp_path / "fake-codex")
    elif variant == "auth_unreadable":
        auth.chmod(0)
    elif variant == "stale_final":
        Path(plan.details["final_path"]).write_bytes(FINAL)
    elif variant == "changed_prompt":
        Path(plan.details["prompt_path"]).write_text("changed")
    pids: list[int] = []
    result = run(
        plan,
        tmp_path,
        started=pids.append,
        poll=lambda: (
            variant == "cancel_probe" and (tmp_path / "probe-started").exists()
        ),
    )
    assert result.classification == (
        "cancelled" if variant == "cancel_probe" else "execution_failure"
    ), result
    assert pids == [] and result.adapter_evidence["phase"] == "preflight"
    if variant == "missing":
        assert result.diagnostic == "invalid_codex_credentials"
    if variant == "probe_created_final":
        assert result.adapter_evidence["observed_version"] == "0.163.0"
        assert result.adapter_evidence["probe_pid"] > 0
        assert Path(plan.details["final_path"]).read_bytes() == FINAL
        c.validate_completion(
            plan,
            CompletionFacts(
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
            ),
        )


def test_stdin_and_streams_do_not_deadlock(tmp_path: Path) -> None:
    before = 'os.write(2, b"x" * 131072)\ntime.sleep(0.05)'
    plan, _ = prepared(tmp_path, before=before)
    generated = dict(plan.generated_controls)
    generated[c.PROMPT_NAME] += b"x" * 1048576
    details = dict(plan.details)
    details["prompt_sha256"] = c.digest(generated[c.PROMPT_NAME])
    plan = replace(plan, generated_controls=generated, details=details)
    Path(details["prompt_path"]).write_bytes(generated[c.PROMPT_NAME])
    result = run(plan, tmp_path)
    assert result.classification == "completed", result
    assert (
        len(result.stderr) <= 65536
        and result.adapter_evidence["stderr_truncated"] is True
    )


@pytest.mark.parametrize(
    "variant", ["early_exit", "blocked_input_cancel", "flood_cancel"]
)
def test_stdin_pressure_and_prompt_cleanup(tmp_path: Path, variant: str) -> None:
    """Unread stdin and sustained output cannot strand the owned process."""
    marker = tmp_path / "ready"
    term = tmp_path / "term"
    if variant == "early_exit":
        before = "sys.exit(0)"
    else:
        before = (
            "signal.signal(signal.SIGTERM, lambda *_: "
            f"Path({str(term)!r}).write_text(str(time.monotonic())))\n"
            f"Path({str(marker)!r}).write_text('ready')\n"
            + (
                "while True:\n    os.write(2, b'x' * 8192)"
                if variant == "flood_cancel"
                else "time.sleep(30)"
            )
        )
    plan, _ = prepared(tmp_path, before=before)
    generated = dict(plan.generated_controls)
    generated[c.PROMPT_NAME] += b"x" * 1048576
    details = dict(plan.details)
    details["prompt_sha256"] = c.digest(generated[c.PROMPT_NAME])
    plan = replace(plan, generated_controls=generated, details=details)
    Path(details["prompt_path"]).write_bytes(generated[c.PROMPT_NAME])
    cancelled_at: list[float] = []
    ready_at: list[float] = []

    def poll() -> bool:
        if marker.exists():
            if not ready_at:
                ready_at.append(time.monotonic())
            if time.monotonic() - ready_at[0] < 0.1:
                return False
            if not cancelled_at:
                cancelled_at.append(time.monotonic())
            return True
        return False

    pids: list[int] = []
    started_at = time.monotonic()
    result = run(plan, tmp_path, poll=poll, started=pids.append)
    assert result.stdout == b"" and pids, result
    if variant == "early_exit":
        assert result.classification == "execution_failure", result
        assert result.diagnostic == "codex_stdin_closed", result
    else:
        assert result.classification == "cancelled", result
        assert result.diagnostic == "codex_cancelled", result
        assert result.writer_state == "may_live", result
        assert term.exists(), result
        assert float(term.read_text()) - cancelled_at[0] < 1.5
        assert time.monotonic() - started_at < 5
        if variant == "flood_cancel":
            assert result.adapter_evidence["stderr_truncated"] is True
    from millrace_plus.router.codex import _group

    assert not _group(pids[0])


def test_surrogate_environment_redaction() -> None:
    from millrace_plus.router.codex import _redact

    value = os.fsdecode(b"path-\xff")
    assert _redact(b"secret path-\xff", {"HOME": value}) == b"secret [REDACTED]"
    assert _redact(b"C", {"LANG": "C"}) == b"[REDACTED]"


L_ERR = b"[codex-jsonl error.message] "
L_FAIL = b"[codex-jsonl turn.failed.error.message] "
H_NATIVE = b"[codex-stderr native]\n"
REASONING = {"type": "item.completed", "item": {"id": "i1", "type": "reasoning"}}


def _failed(*errors: str, failed: str | None = "turn failed") -> list[dict[str, Any]]:
    values = events()[:2] + [REASONING]
    values += [{"type": "error", "message": message} for message in errors]
    if failed is not None:
        values.append({"type": "turn.failed", "error": {"message": failed}})
    return values


def _invoke(
    plan: Any, tmp: Path, env: dict[str, str] | None = None, **kwargs: Any
) -> Any:
    return CodexExecAdapter().invoke(
        plan,
        cwd=tmp,
        env={"PATH": "/usr/bin:/bin", **plan.environment, **(env or {})},
        deadline=time.time() + kwargs.pop("timeout", 8),
        poll=kwargs.pop("poll", lambda: False),
        on_started=kwargs.pop("started", lambda pid: None),
    )


def _windows(secret: bytes) -> list[bytes]:
    return [secret[i : i + 4] for i in range(len(secret) - 3)]


def _no_controls(raw: bytes) -> bool:
    return all(b >= 0x20 and b != 0x7F for b in raw.replace(b"\n", b""))


@pytest.mark.parametrize(
    "variant",
    [
        "observed_failed_shape",
        "last_error_wins",
        "startup_error_then_nonzero_exit",
        "error_then_eof",
        "protocol_failure_after_error",
        "empty_messages",
        "control_and_label_forgery",
        "lone_surrogate",
        "multibyte_clip",
        "emoji_clip",
        "escape_unit_not_split",
        "at_line_limit",
        "cancel_after_error",
    ],
)
def test_failure_messages_become_labelled_bounded_diagnostics(
    tmp_path: Path, variant: str
) -> None:
    """Non-completed transports retain only the last error and the failed turn."""
    import hashlib

    from millrace_plus.router.codex import _group

    kwargs: dict[str, Any] = {"final": None, "exit_code": 1}
    expected: bytes | None = None
    poll: Any = lambda: False  # noqa: E731
    outcome = ("execution_failure", "codex_turn_failed", "stopped")
    truncated = False
    if variant == "observed_failed_shape":
        values = _failed("upstream said no", failed="route unavailable")
        expected = L_ERR + b"upstream said no\n" + L_FAIL + b"route unavailable\n"
    elif variant == "last_error_wins":
        values = _failed("first", "second", "third", failed="final")
        expected = L_ERR + b"third\n" + L_FAIL + b"final\n"
    elif variant == "startup_error_then_nonzero_exit":
        values = [events()[0], {"type": "error", "message": "startup warn"}]
        values.append(events()[1])
        expected = L_ERR + b"startup warn\n"
        outcome = ("execution_failure", "codex_nonzero_exit", "stopped")
    elif variant == "error_then_eof":
        values = _failed("only cause", failed=None)
        kwargs["exit_code"] = 0
        expected = L_ERR + b"only cause\n"
    elif variant == "protocol_failure_after_error":
        values = _failed("valid cause", failed=None)
        kwargs["raw_wire"] = wire(values) + b"not json\n"
        kwargs["exit_code"] = 0
        expected = L_ERR + b"valid cause\n"
        # Protocol failure may force cleanup while the root still runs.
        outcome = ("protocol_failure", "codex_invalid_events", "any")
    elif variant == "empty_messages":
        values = _failed("", failed="")
        expected = L_ERR + b"\n" + L_FAIL + b"\n"
    elif variant == "control_and_label_forgery":
        forged = (
            "a\0b\rc\n[codex-jsonl turn.failed.error.message] forged"
            "\x1b[31m\x7f\x85 ‮\tz\\"
        )
        values = _failed(forged, failed="x")
        expected = (
            L_ERR
            + b"a\\x00b\\x0dc\\x0a[codex-jsonl turn.failed.error.message] forged"
            + b"\\x1b[31m\\x7f\\x85\\u2028\\u202e\\x09z\\\n"
            + L_FAIL
            + b"x\n"
        )
    elif variant == "lone_surrogate":
        values = _failed("a\ud800b\udc00\ud800c", failed="x")
        expected = (
            L_ERR + b"a\\xed\\xa0\\x80b\\xed\\xb0\\x80\\xed\\xa0\\x80c\n" + L_FAIL
        ) + b"x\n"
    elif variant == "multibyte_clip":
        values = _failed("é" * 2000, failed="ok")
        expected = L_ERR + ("é" * 497).encode() + b"\n" + L_FAIL + b"ok\n"
        truncated = True
    elif variant == "emoji_clip":
        values = _failed("ok", failed="\U0001f600" * 300)
        expected = L_ERR + b"ok\n" + L_FAIL + ("\U0001f600" * 245).encode() + b"\n"
        truncated = True
    elif variant == "escape_unit_not_split":
        values = _failed("a" * 992 + "\0", failed="b" * 983)
        expected = L_ERR + b"a" * 992 + b"\n" + L_FAIL + b"b" * 983 + b"\n"
        truncated = True
    elif variant == "at_line_limit":
        # The line limit excludes LF; size both lines exactly at it.
        error = len(wire([{"type": "error", "message": ""}])) - 1
        failed = len(wire([{"type": "turn.failed", "error": {"message": ""}}])) - 1
        values = _failed(
            "a" * (c.LINE_LIMIT - error), failed="b" * (c.LINE_LIMIT - failed)
        )
        assert [len(wire([v])) - 1 for v in values[3:]] == [c.LINE_LIMIT] * 2
        expected = L_ERR + b"a" * 995 + b"\n" + L_FAIL + b"b" * 983 + b"\n"
        truncated = True
    else:
        marker = tmp_path / "emitted"
        values = _failed("cause before cancel", failed=None)
        kwargs.update(after=f"Path({str(marker)!r}).write_text('1');time.sleep(30)")
        seen: list[float] = []

        def poll() -> bool:
            if marker.exists() and not seen:
                seen.append(time.monotonic())
            return bool(seen) and time.monotonic() - seen[0] >= 0.2

        expected = L_ERR + b"cause before cancel\n"
        outcome = ("cancelled", "codex_cancelled", "may_live")
    plan, _ = prepared(tmp_path, events=values, **kwargs)
    pids: list[int] = []
    result = _invoke(plan, tmp_path, poll=poll, started=pids.append)
    observed = (result.classification, result.diagnostic, result.writer_state)
    assert observed[:2] == outcome[:2], result
    assert outcome[2] == "any" or observed[2] == outcome[2], result
    assert result.stdout == b""
    assert result.stderr == expected, result.stderr[:2048]
    assert result.stderr.count(b"\n") == expected.count(b"\n")
    assert _no_controls(result.stderr)
    result.stderr.decode("utf-8")
    assert all(len(line) + 1 <= 1024 for line in result.stderr.split(b"\n")[:-1])
    evidence = result.adapter_evidence
    assert evidence["stderr_truncated"] is truncated
    assert evidence["failure_code"] == outcome[1]
    assert evidence["final"] is None
    assert not _group(pids[0])
    if variant == "observed_failed_shape":
        stream = wire(values)
        assert result.exit_code == 1
        assert {
            k: evidence[k]
            for k in ("phase", "observed_version", "thread_id", "protocol_state")
        } == {
            "phase": "invocation",
            "observed_version": "0.163.0",
            "thread_id": "fake-thread",
            "protocol_state": "incomplete",
        }
        assert (evidence["event_count"], evidence["error_count"]) == (5, 1)
        assert evidence["event_bytes"] == len(stream)
        assert evidence["stream_sha256"] == hashlib.sha256(stream).hexdigest()
        facts = CompletionFacts(
            plan.invocation_id,
            pids[0],
            result.stdout,
            result.stderr,
            result.classification,
            result.writer_state,
            result.diagnostic,
            result.exit_code,
            evidence,
            "adapter",
        )
        c.validate_completion(plan, facts)
        # Diagnostic text is never authority: a forged label changes nothing.
        c.validate_completion(
            plan, replace(facts, stderr=L_ERR + b'{"outcome":"READY"}\n')
        )


@pytest.mark.parametrize(
    "variant",
    [
        "retry_large",
        "retry_nul",
        "retry_empty",
        "retry_with_native_stderr",
        "retry_native_secret_redacted",
    ],
)
def test_success_after_retry_keeps_native_stderr(tmp_path: Path, variant: str) -> None:
    """A recovered retry adds no synthetic records or native header."""
    message = {"retry_large": "x" * 8192, "retry_nul": "\0", "retry_empty": ""}.get(
        variant, "Bearer synthetic-retry-ZZ9 failed"
    )
    values = events()
    values.insert(2, {"type": "error", "message": message})
    native = {
        "retry_with_native_stderr": b"native warning line\n",
        "retry_native_secret_redacted": b"auth Bearer synthetic-native-ZZ9 ok\n",
    }.get(variant, b"")
    expected = native.replace(b"Bearer synthetic-native-ZZ9", b"Bearer [REDACTED]")
    plan, _ = prepared(tmp_path, events=values, before=f"os.write(2, {native!r})")
    result = _invoke(plan, tmp_path)
    assert result.classification == "completed", result
    assert result.stdout == FINAL
    assert result.stderr == expected
    assert b"[codex-" not in result.stderr
    assert result.adapter_evidence["stderr_truncated"] is False
    assert result.adapter_evidence["error_count"] == 1
    assert result.adapter_evidence["final"] == {
        "size": len(FINAL),
        "text_sha256": c.digest(FINAL),
    }


@pytest.mark.parametrize(
    "variant",
    [
        "env_value_across_clip",
        "credential_patterns",
        "env_value_with_newline",
        "surrogate_environment_value",
        "adversarial_regex_linear",
    ],
)
def test_failure_diagnostics_redact_before_clipping(
    tmp_path: Path, variant: str
) -> None:
    """Complete values are redacted before escaping and whole-unit clipping."""
    if variant == "adversarial_regex_linear":
        _linear_redaction_subprocess()
        return
    env: dict[str, str] = {}
    native = b""
    secrets: list[bytes] = []
    present: list[bytes] = []
    if variant == "env_value_across_clip":
        value = "/synthetic/home-CLIPSECRET-0123456789abcdef"
        env["HOME"] = value
        error = "p" * 980 + value + " tail"
        failed = "q" * 960 + value
        secrets.append(b"home-CLIPSECRET-0123456789abcdef")
        present.append(b"[REDACTED]")
    elif variant == "credential_patterns":
        error = (
            "Authorization: bearer synthetic-bt-4K8X; sk-synthetic1234567890; "
            "https://user:synthetic-pass-4K8X@example.invalid/x?token=synthetic-tk"
            "-4K8X&ok=1; https://example.invalid/e?key=synthetic-query-4K8X; "
            'contact synthetic-email@example.invalid; {"access_token": "synthetic-jv'
            '-4K8X"}; status code=429'
        )
        failed = "refresh_token=synthetic-rf-4K8X, client_secret=synthetic-cs-4K8X"
        secrets += [
            b"synthetic-bt-4K8X",
            b"synthetic1234567890",
            b"synthetic-pass-4K8X",
            b"synthetic-tk-4K8X",
            b"synthetic-query-4K8X",
            b"synthetic-email",
            b"synthetic-jv-4K8X",
            b"synthetic-rf-4K8X",
            b"synthetic-cs-4K8X",
        ]
        present += [
            b"Bearer [REDACTED]",
            b"https://[REDACTED]@example.invalid",
            b"token=[REDACTED]&ok=1",
            b"key=[REDACTED];",
            b'"access_token": "[REDACTED]"',
            b"status code=429",
        ]
    elif variant == "env_value_with_newline":
        value = "/synthetic/tmp\nLINE-SECRET-77"
        env["TMPDIR"] = str(tmp_path)  # keep the fake runnable
        env["LANG"] = value
        error = "lang is " + value + " end"
        failed = "x"
        secrets.append(b"LINE-SECRET-77")
        present.append(b"lang is [REDACTED] end")
    else:
        value = os.fsdecode(b"synthetic-env-path-\xff")
        env["HOME"] = value
        error = "json " + value + " end"
        failed = value
        native = b"native synthetic-env-path-\xff end\n"
        secrets.append(b"synthetic-env-path")
        present += [b"json [REDACTED] end", H_NATIVE + b"native [REDACTED] end\n"]
    plan, _ = prepared(
        tmp_path,
        events=_failed(error, failed=failed),
        final=None,
        exit_code=1,
        before=f"os.write(2, {native!r})",
    )
    result = _invoke(plan, tmp_path, env=env)
    assert result.diagnostic == "codex_turn_failed", result
    assert result.stderr.startswith(L_ERR)
    assert result.stderr.count(L_FAIL) == 1
    for secret in secrets:
        assert not any(w in result.stderr for w in _windows(secret)), secret
    for text in present:
        assert text in result.stderr, (text, result.stderr)
    assert _no_controls(result.stderr.split(H_NATIVE)[0])


def _linear_redaction_subprocess() -> None:
    """Run adversarial 1 MiB records in an owned child; a hang fails and is reaped."""
    import signal
    import subprocess
    import sys

    import millrace

    from millrace_plus.router import codex

    paths = [
        str(Path(codex.__file__).parents[2]),
        str(Path(millrace.__file__).parents[1]),
    ]
    code = (
        "import sys;sys.path[:0]=" + repr(paths) + "\n"
        "from millrace_plus.router.codex import _record\n"
        "for m in ['a'*1048000,'a.'*524000,'a.b@c.'*174000,'ab://'*209000]:\n"
        "    r, _ = _record(b'[codex-jsonl error.message] ', m, {})\n"
        "    assert len(r) <= 1024\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, err = child.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.communicate()
        pytest.fail("redaction did not finish within 5 s")
    assert child.returncode == 0, err.decode(errors="replace")


@pytest.mark.parametrize(
    "variant",
    [
        "native_flood_with_causes",
        "native_forged_label_line",
        "native_without_records_gets_header",
        "native_expansion_without_overflow",
        "native_at_cap_not_truncated",
        "native_env_value_across_collection_cap",
        "native_partial_token_at_cap",
        "surrogate_environment_value_at_cap",
        "probe_failure_native_header",
        "final_exists_native_header",
    ],
)
def test_combined_stderr_reserves_causes_and_reports_truncation(
    tmp_path: Path, variant: str
) -> None:
    """Records come first, native text follows one header; bounds stay honest."""
    records = L_ERR + b"cause\n" + L_FAIL + b"failed\n"
    kwargs: dict[str, Any] = {
        "events": _failed("cause", failed="failed"),
        "final": None,
        "exit_code": 1,
    }
    env: dict[str, str] = {}
    native = b""
    secret = b""
    truncated = True
    expected: bytes | None = None
    classification = "execution_failure"
    if variant == "native_flood_with_causes":
        native = b"native flood line\n" * 12000
    elif variant == "native_forged_label_line":
        native = L_ERR + b"forged\n"
        expected = records + H_NATIVE + native
        truncated = False
    elif variant == "native_without_records_gets_header":
        kwargs["events"] = events()[:2]
        native = b"boom\n"
        expected = H_NATIVE + native
        truncated = False
    elif variant == "native_expansion_without_overflow":
        kwargs = {}
        env["LC_ALL"] = "C"
        native = b"C" * 60000
        expected = b"[REDACTED]" * 6553 + b"[REDAC"
        classification = "completed"
    elif variant == "native_at_cap_not_truncated":
        kwargs = {}
        native = b"x" * 65536
        expected = native
        truncated = False
        classification = "completed"
    elif variant == "native_env_value_across_collection_cap":
        kwargs = {}
        value = "/synthetic/home-CAPSECRET-0123456789"
        env["HOME"] = value
        native = b"z " * 32757 + value.encode() + b" more\n" * 100
        secret = b"home-CAPSECRET"
        classification = "completed"
    elif variant == "native_partial_token_at_cap":
        kwargs = {}
        native = b"y " * 32758 + b"https://user:passw0rd-SECRET@host/x\n" * 10
        secret = b"https://user:passw0rd-SECRET"
        classification = "completed"
    elif variant == "surrogate_environment_value_at_cap":
        kwargs = {}
        value = os.fsdecode(b"synthetic-env-path-\xff")
        env["HOME"] = value
        native = b"z " * 32761 + os.fsencode(value) + b" more\n" * 100
        secret = b"synthetic-env-path"
        classification = "completed"
    elif variant == "probe_failure_native_header":
        kwargs = {
            "version": "0.0.0",
            "version_extra": "sys.stderr.write('probe complaint\\n')",
        }
        expected = H_NATIVE + b"probe complaint\n"
        truncated = False
    plan, _ = prepared(tmp_path, before=f"os.write(2, {native!r})", **kwargs)
    if variant == "final_exists_native_header":
        final = plan.details["final_path"]
        fake(
            tmp_path,
            version_extra=(
                f"Path({final!r}).write_text('x');sys.stderr.write('probe note\\n')"
            ),
        )
        expected = H_NATIVE + b"probe note\n"
        truncated = False
    result = _invoke(plan, tmp_path, env=env)
    assert result.classification == classification, result
    assert len(result.stderr) <= 65536
    assert result.adapter_evidence["stderr_truncated"] is truncated
    if expected is not None:
        assert result.stderr == expected, result.stderr[:256]
    if secret:
        assert not any(w in result.stderr for w in _windows(secret)), secret
    if variant == "native_flood_with_causes":
        assert result.stderr.startswith(records + H_NATIVE + b"native flood line\n")
        assert native.startswith(result.stderr[len(records) + len(H_NATIVE) :])
    if variant in {"probe_failure_native_header", "final_exists_native_header"}:
        assert result.diagnostic == (
            "codex_version_mismatch"
            if variant == "probe_failure_native_header"
            else "codex_final_exists"
        )
        assert result.adapter_evidence["phase"] == "preflight"
