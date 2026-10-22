"""Pi fake-process tests transferred from the accepted Core P4a file
(sha256 da60351a78ea220a71a8fbb15936f6b26a9c4d273e1f30ae98c0f4157d5346f8)
into the Plus-owned adapter, unchanged in assertions.

Literal pinned-wire fakes exercise the real finite subprocess boundary."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from millrace.router.execution import AdapterResult
from plus_support import _assistant, _events, _fake, _wire

from millrace_plus.router.pi import invoke


def _launch(
    tmp: Path,
    program: Path,
    *,
    limit: int = 16777216,
    deadline: float | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    on_started: Callable[[int], None] = lambda pid: None,
) -> AdapterResult:
    prompt = tmp / "pi-prompt.txt"
    if not prompt.exists():
        prompt.write_text("Millrace invocation instructions.\n")
    native = {
        "loader_path": None,
        "prompt_path": str(prompt),
        "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        "max_event_bytes": limit,
    }
    return invoke(
        [
            str(program),
            "--offline",
            "--mode",
            "json",
            "--no-session",
            "--",
            "@" + str(prompt),
        ],
        tmp,
        {"PATH": os.environ.get("PATH", ""), "PI_CODING_AGENT_DIR": str(tmp)},
        time.time() + 2 if deadline is None else deadline,
        cancelled,
        on_started,
        invocation_id="a" * 32,
        native_launch=native,
        effective={"provider": "fixture", "model": "exact-model", "thinking": "off"},
    )


@pytest.mark.parametrize("version", ["0.73.1", "1.3.0", "banner\\n0.87.0"])
def test_version_preflight_bounds_and_no_fallback(tmp_path: Path, version: str) -> None:
    marker = tmp_path / "launched"
    program = _fake(
        tmp_path, b"", version=version, before=f"Path({str(marker)!r}).touch()"
    )
    result = _launch(tmp_path, program)
    assert result.classification == "execution_failure" and not marker.exists()
    assert result.stdout == b"" and result.adapter_evidence["phase"] == "preflight"
    assert result.adapter_evidence["observed_version"] is None


@pytest.mark.parametrize(
    "texts,classification",
    [
        (['{"out', 'come":"READY"}'], "completed"),
        (["é" * 32768], "completed"),
        (["é" * 32769], "protocol_failure"),
        ([" \n" + '{"outcome":"READY"}' + " " * 70000], "completed"),
    ],
)
def test_final_text_block_join_and_bound(
    tmp_path: Path, texts: list[str], classification: str
) -> None:
    program = _fake(tmp_path, _wire(_events(tmp_path, [_assistant(texts)])))
    result = _launch(tmp_path, program)
    assert result.classification == classification
    expected = "".join(texts).strip().encode()
    assert result.stdout == (expected if classification == "completed" else b"")
    assert result.writer_state == "stopped"


@pytest.mark.parametrize(
    "variant",
    [
        "valid",
        "empty_latest",
        "error",
        "after_settled",
        "partial",
        "unknown",
        "mismatch",
        "crlf",
        "large_superseded",
    ],
)
def test_pinned_events_final_channel_and_limits(tmp_path: Path, variant: str) -> None:
    messages = [_assistant(['{"outcome":"READY"}'])]
    if variant == "empty_latest":
        messages.append(_assistant([" \n"]))
    if variant == "error":
        messages.append(_assistant(["failed"], stop="error"))
    if variant == "large_superseded":
        messages.insert(0, _assistant(["x" * 1100000]))
    events = _events(tmp_path, messages)
    if variant == "after_settled":
        events.append({"type": "entry_appended", "entry": {}})
    if variant == "unknown":
        events.insert(1, {"type": "bash_execution_update"})
    if variant == "mismatch":
        events[-2]["messages"] = []
    wire = _wire(events)
    if variant == "partial":
        wire = wire[:-1]
    if variant == "crlf":
        wire = wire.replace(b"\n", b"\r\n")
    result = _launch(tmp_path, _fake(tmp_path, wire))
    assert result.classification == (
        "completed"
        if variant in {"valid", "empty_latest", "crlf", "large_superseded"}
        else "execution_failure"
        if variant == "error"
        else "protocol_failure"
    )
    if variant == "empty_latest":
        assert result.stdout == b""  # Never revive the older READY marker.
    if result.classification == "completed":
        assert (
            result.adapter_evidence["final"]["text_sha256"]
            == hashlib.sha256(result.stdout).hexdigest()
        )


@pytest.mark.parametrize("cancel", [False, True])
def test_cancel_timeout_cleanup_and_uncertain_writers(
    tmp_path: Path, cancel: bool
) -> None:
    started: list[int] = []
    polls: list[int] = []

    def poll() -> bool:
        polls.append(1)
        return bool(started) and cancel

    main_ready = tmp_path / "main-ready"
    program = _fake(
        tmp_path, b"", before=f"Path({str(main_ready)!r}).touch()\ntime.sleep(10)"
    )

    def acknowledge(pid: int) -> None:
        started.append(pid)
        end = time.monotonic() + 2
        while not main_ready.exists():
            assert time.monotonic() < end, "owned fake main did not start"
            time.sleep(0.01)

    before = time.monotonic()
    result = _launch(
        tmp_path,
        program,
        cancelled=poll,
        on_started=acknowledge,
        deadline=time.time() + 5,
    )
    assert result.classification == ("cancelled" if cancel else "timeout")
    assert result.stdout == b"" and result.writer_state == "may_live"
    assert len(polls) > 3
    assert started and main_ready.exists()
    if not cancel:
        assert 4.5 <= time.monotonic() - before < 7
    with pytest.raises(ProcessLookupError):
        os.kill(started[0], 0)


def test_frame_boundary_includes_cr_and_not_lf(tmp_path: Path) -> None:
    wire = _wire(_events(tmp_path, [_assistant(['{"outcome":"READY"}'])]))
    maximum = max(map(len, wire.splitlines()))
    result = _launch(tmp_path, _fake(tmp_path, wire), limit=maximum)
    assert result.classification == "completed"
    result = _launch(
        tmp_path, _fake(tmp_path, wire.replace(b"\n", b"\r\n")), limit=maximum
    )
    assert result.classification == "protocol_failure"
    assert result.adapter_evidence["failure_code"] == "pi_frame_too_large"


def test_prompt_tamper_after_version_never_launches(tmp_path: Path) -> None:
    prompt = tmp_path / "pi-prompt.txt"
    prompt.write_text("owned prompt")
    marker = tmp_path / "main"
    program = _fake(
        tmp_path,
        b"",
        version_extra=f"Path({str(prompt)!r}).write_text('changed')",
        before=f"Path({str(marker)!r}).touch()",
    )
    result = _launch(tmp_path, program)
    assert (
        result.classification == "execution_failure"
        and result.diagnostic == "pi_prompt_changed"
    )
    assert not marker.exists() and result.writer_state == "stopped"


def test_public_retry_compaction_recovers_final_success(tmp_path: Path) -> None:
    first = _assistant(["retry error"], stop="error")
    events = _events(tmp_path, [first])[:-1]
    events[-1]["willRetry"] = True
    events += [
        {"type": "auto_retry_start"},
        {"type": "auto_retry_end"},
        {"type": "compaction_start"},
        {"type": "compaction_end"},
    ]
    next_events = _events(tmp_path, [_assistant(['{"outcome":"READY"}'])])
    events += next_events[1:]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed" and b"READY" in result.stdout


def test_probe_cap_and_deadline_are_distinct(tmp_path: Path) -> None:
    program = _fake(tmp_path, b"", version_extra="time.sleep(10)")
    before = time.monotonic()
    result = _launch(tmp_path, program, deadline=time.time() + 8)
    assert result.classification == "execution_failure"
    assert (
        result.diagnostic == "pi_version_timeout" and result.writer_state == "may_live"
    )
    assert 5 <= time.monotonic() - before < 7


def test_forced_stop_does_not_prove_detached_writer_stopped(tmp_path: Path) -> None:
    """The test owns the detached fake writer; the adapter cannot discover it."""
    import signal
    import subprocess

    writer = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True
    )
    try:
        started: list[int] = []
        result = _launch(
            tmp_path,
            _fake(tmp_path, b"", before="time.sleep(10)"),
            cancelled=lambda: bool(started),
            on_started=started.append,
        )
        assert (
            result.writer_state == "may_live" and result.classification == "cancelled"
        )
        assert writer.poll() is None
    finally:
        os.killpg(writer.pid, signal.SIGTERM)
        writer.wait(timeout=3)
    assert writer.returncode is not None


def test_valid_version_with_unknown_cleanup_never_launches_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import millrace_plus.router.pi as adapter

    marker = tmp_path / "main-started"
    program = _fake(tmp_path, b"", before=f"Path({str(marker)!r}).touch()")
    monkeypatch.setattr(adapter, "_group", lambda _: True)
    result = _launch(tmp_path, program, deadline=time.time() + 10)
    assert (
        result.classification == "execution_failure"
        and result.writer_state == "may_live"
    )
    assert result.adapter_evidence["observed_version"] == "0.87.0"
    assert result.adapter_evidence["failure_code"] == "pi_version_cleanup_unconfirmed"
    assert not marker.exists()


@pytest.mark.parametrize(
    "malformation",
    [
        "stop_list",
        "content_bool",
        "header_duplicate",
        "blank",
        "utf8",
        "no_settlement",
        "nonzero",
    ],
)
def test_illegal_native_shapes_fail_as_typed_results(
    tmp_path: Path, malformation: str
) -> None:
    message = _assistant(['{"outcome":"READY"}'])
    if malformation == "stop_list":
        message["stopReason"] = []
    if malformation == "content_bool":
        message["content"] = True
    events = _events(tmp_path, [message])
    if malformation == "header_duplicate":
        events.insert(1, events[0])
    if malformation == "no_settlement":
        events.pop()
    wire = _wire(events)
    if malformation == "blank":
        wire += b"\n"
    if malformation == "utf8":
        wire = b"\xff\n"
    result = _launch(
        tmp_path,
        _fake(tmp_path, wire, after="sys.exit(1)" if malformation == "nonzero" else ""),
    )
    assert result.classification in {"protocol_failure", "execution_failure"}
    assert result.stdout == b"" and result.adapter_evidence["failure_code"]


def test_pinned_retry_end_inside_retried_turn(tmp_path: Path) -> None:
    """0.87 closes successful recovery on message_end, before turn_end."""
    failed = _assistant(["temporary failure"], stop="error")
    events = _events(tmp_path, [failed])[:-1]
    events[-1]["willRetry"] = True
    events.append({"type": "auto_retry_start", "attempt": 1})
    final = _events(tmp_path, [_assistant(['{"outcome":"READY"}'])])[1:]
    index = next(i for i, event in enumerate(final) if event["type"] == "turn_end")
    final.insert(index, {"type": "auto_retry_end", "success": True, "attempt": 1})
    events += final
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed"
    assert result.writer_state == "stopped" and b"READY" in result.stdout


def test_threshold_compaction_inside_agent_run(tmp_path: Path) -> None:
    first = _assistant(["intermediate"])
    last = _assistant(['{"outcome":"READY"}'])
    events = _events(tmp_path, [first, last])
    index = next(i for i, event in enumerate(events) if event["type"] == "turn_end") + 1
    events[index:index] = [
        {"type": "compaction_start", "reason": "threshold"},
        {"type": "summarization_retry_scheduled"},
        {"type": "summarization_retry_attempt_start"},
        {"type": "summarization_retry_finished"},
        {"type": "compaction_end", "aborted": False},
    ]
    assert (
        _launch(tmp_path, _fake(tmp_path, _wire(events))).classification == "completed"
    )


def test_repeated_retry_starts_close_one_recovery_chain(tmp_path: Path) -> None:
    failed = _assistant(["temporary failure"], stop="error")
    events = _events(tmp_path, [failed])[:-1]
    events[-1]["willRetry"] = True
    for attempt in (1, 2):
        events.append({"type": "auto_retry_start", "attempt": attempt})
        message = failed if attempt == 1 else _assistant(['{"outcome":"READY"}'])
        turn = _events(tmp_path, [message])[1:-1]
        turn[-1]["willRetry"] = attempt == 1
        if attempt == 2:
            index = next(
                i for i, event in enumerate(turn) if event["type"] == "turn_end"
            )
            turn.insert(
                index, {"type": "auto_retry_end", "success": True, "attempt": 2}
            )
        events += turn
    events.append({"type": "agent_settled"})
    assert (
        _launch(tmp_path, _fake(tmp_path, _wire(events))).classification == "completed"
    )


def test_tools_close_before_final_settlement(tmp_path: Path) -> None:
    tool = _assistant([], stop="toolUse")
    tool["content"] = [
        {
            "type": "toolCall",
            "id": "call1",
            "name": "read",
            "arguments": {"path": "input.txt"},
        }
    ]
    final = _assistant(['{"outcome":"READY"}'])
    events = _events(tmp_path, [tool, final])
    end = next(i for i, e in enumerate(events) if e["type"] == "turn_end")
    result = {
        "role": "toolResult",
        "toolCallId": "call1",
        "toolName": "read",
        "content": [],
        "isError": False,
        "timestamp": 2,
    }
    events[end:end] = [
        {"type": "tool_execution_start", "toolCallId": "call1", "toolName": "read"},
        {"type": "tool_execution_update", "toolCallId": "call1", "toolName": "read"},
        {
            "type": "tool_execution_end",
            "toolCallId": "call1",
            "toolName": "read",
            "result": {},
            "isError": False,
        },
        {"type": "message_start", "message": {"role": "toolResult"}},
        {"type": "message_end", "message": result},
    ]
    events[-2]["messages"] = [tool, result, final]
    actual = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert actual.classification == "completed" and b"READY" in actual.stdout


def test_continuous_progress_is_not_cumulative_output_cap(tmp_path: Path) -> None:
    events = _events(tmp_path, [_assistant(['{"outcome":"READY"}'])])
    # More than the old command cumulative64KiB; each record remains bounded.
    events[1:1] = [
        {"type": "entry_appended", "entry": {"note": "x" * 2048}} for _ in range(100)
    ]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed"
    assert result.adapter_evidence["event_bytes"] > 200000
    assert len(result.stdout) < 100 and result.stderr == b""


def test_current_executable_and_explicit_loader_profile(tmp_path: Path) -> None:
    script = _fake(
        tmp_path, _wire(_events(tmp_path, [_assistant(['{"outcome":"READY"}'])]))
    )
    prompt = tmp_path / "pi-prompt.txt"
    prompt.write_text("Millrace invocation instructions.\n")
    native = {
        "loader_path": str(script),
        "prompt_path": str(prompt),
        "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        "max_event_bytes": 16777216,
    }
    result = invoke(
        [
            sys.executable,
            str(script),
            "--offline",
            "--mode",
            "json",
            "--",
            "@" + str(prompt),
        ],
        tmp_path,
        {"PATH": os.environ.get("PATH", ""), "PI_CODING_AGENT_DIR": str(tmp_path)},
        time.time() + 3,
        lambda: False,
        lambda _: None,
        invocation_id="a" * 32,
        native_launch=native,
        effective={"provider": "fixture", "model": "exact-model", "thinking": "off"},
    )
    assert result.classification == "completed" and result.writer_state == "stopped"


@pytest.mark.parametrize("stderr_size", [10000, 70000])
def test_redaction_expansion_is_bounded_and_reports_truncation(
    tmp_path: Path, stderr_size: int
) -> None:
    script = _fake(
        tmp_path,
        _wire(_events(tmp_path, [_assistant(['{"outcome":"READY"}'])])),
        after=f"os.write(2,b'x'*{stderr_size})",
    )
    prompt = tmp_path / "pi-prompt.txt"
    prompt.write_text("Millrace invocation instructions.\n")
    native = {
        "loader_path": None,
        "prompt_path": str(prompt),
        "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        "max_event_bytes": 16777216,
    }
    result = invoke(
        [str(script), "--offline", "--mode", "json", "--", "@" + str(prompt)],
        tmp_path,
        {
            "PATH": os.environ.get("PATH", ""),
            "PI_CODING_AGENT_DIR": str(tmp_path),
            "P4A_TEST_VALUE": "x",
            "P4A_OVERLAP_VALUE": "[redacted][redacted]",
        },
        time.time() + 3,
        lambda: False,
        lambda _: None,
        invocation_id="a" * 32,
        native_launch=native,
        effective={"provider": "fixture", "model": "exact-model", "thinking": "off"},
    )
    assert result.classification == "completed"
    assert len(result.stderr) <= 65536 and b"stderr_truncated" in result.stderr
    assert "stderr_truncated" in result.diagnostic
    assert result.stderr.count(b"[stderr_truncated]") == 1


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_summary_retry_pairs_close_one_compaction_chain(
    tmp_path: Path, attempts: int
) -> None:
    events = _events(
        tmp_path, [_assistant(["intermediate"]), _assistant(['{"outcome":"READY"}'])]
    )
    boundary = (
        next(i for i, event in enumerate(events) if event["type"] == "turn_end") + 1
    )
    recovery: list[dict[str, object]] = [
        {"type": "compaction_start", "reason": "threshold"}
    ]
    for attempt in range(1, attempts + 1):
        recovery.extend(
            [
                {
                    "type": "summarization_retry_scheduled",
                    "attempt": attempt,
                    "maxAttempts": 3,
                    "delayMs": 1,
                    "errorMessage": "retryable",
                },
                {
                    "type": "summarization_retry_attempt_start",
                    "source": "compaction",
                    "reason": "threshold",
                    "attempt": attempt,
                },
            ]
        )
    recovery.extend(
        [
            {"type": "summarization_retry_finished"},
            {"type": "compaction_end", "reason": "threshold", "willRetry": False},
        ]
    )
    events[boundary:boundary] = recovery
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed"
    assert result.stdout == b'{"outcome":"READY"}' and result.writer_state == "stopped"


@pytest.mark.parametrize("usage", [{"input": "not-a-number"}, {"cost": []}])
def test_malformed_pinned_usage_is_not_a_completed_result(
    tmp_path: Path, usage: dict[str, object]
) -> None:
    message = _assistant(['{"outcome":"READY"}'])
    message["usage"] = usage
    result = _launch(tmp_path, _fake(tmp_path, _wire(_events(tmp_path, [message]))))
    assert result.classification == "protocol_failure"
    assert result.stdout == b"" and result.adapter_evidence["failure_code"]


def test_normal_exit_before_slow_started_ack_keeps_confirmed_closure(
    tmp_path: Path,
) -> None:
    pids: list[int] = []

    def started(pid: int) -> None:
        pids.append(pid)
        time.sleep(0.1)

    result = _launch(
        tmp_path,
        _fake(
            tmp_path, _wire(_events(tmp_path, [_assistant(['{"outcome":"READY"}'])]))
        ),
        on_started=started,
    )
    assert (
        result.classification == "completed" and result.stdout == b'{"outcome":"READY"}'
    )
    assert result.writer_state == "stopped"
    assert len(pids) == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(pids[0], os.WNOHANG)


@pytest.mark.parametrize(
    "variant",
    [
        "empty",
        "missing_cost",
        "input_bool",
        "input_text",
        "cost_bool",
        "cost_text",
        "missing_total",
        "nonfinite",
    ],
)
def test_required_usage_shape_cannot_select_final_text(
    tmp_path: Path, variant: str
) -> None:
    message = _assistant(['{"outcome":"READY"}'])
    usage = cast(dict[str, object], message["usage"]).copy()
    cost = cast(dict[str, object], usage["cost"]).copy()
    usage["cost"] = cost
    if variant == "empty":
        usage.clear()
    elif variant == "missing_cost":
        del usage["cost"]
    elif variant == "input_bool":
        usage["input"] = True
    elif variant == "input_text":
        usage["input"] = "1"
    elif variant == "cost_bool":
        cost["input"] = False
    elif variant == "cost_text":
        cost["input"] = "0"
    elif variant == "missing_total":
        del cost["total"]
    else:
        usage["input"] = float("inf")
    message["usage"] = usage
    result = _launch(tmp_path, _fake(tmp_path, _wire(_events(tmp_path, [message]))))
    assert result.classification == "protocol_failure" and result.stdout == b""


def test_usage_numbers_do_not_add_accounting_or_range_policy(tmp_path: Path) -> None:
    message = _assistant(['{"outcome":"READY"}'])
    message["usage"] = {
        "input": -0.5,
        "output": 1.25,
        "cacheRead": 0,
        "cacheWrite": 1,
        "totalTokens": -17,
        "cost": {
            "input": -0.25,
            "output": 2.5,
            "cacheRead": 0,
            "cacheWrite": 3,
            "total": -99.0,
        },
    }
    result = _launch(tmp_path, _fake(tmp_path, _wire(_events(tmp_path, [message]))))
    assert result.classification == "completed" and result.writer_state == "stopped"
    assert result.stdout == b'{"outcome":"READY"}'


@pytest.mark.parametrize("continuation", ["same_run", "next_run"])
def test_turn_end_requires_its_own_assistant(tmp_path: Path, continuation: str) -> None:
    message = _assistant(['{"outcome":"READY"}'])
    events = _events(tmp_path, [message])
    empty_turn = [{"type": "turn_start"}, {"type": "turn_end", "message": message}]
    if continuation == "same_run":
        events[-2:-2] = empty_turn
    else:
        events = (
            events[:-1]
            + [{"type": "agent_start"}]
            + empty_turn
            + [
                {"type": "agent_end", "messages": [], "willRetry": False},
                {"type": "agent_settled"},
            ]
        )
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure"
    assert result.stdout == b""
    assert result.adapter_evidence["failure_code"] == "pi_event_order"


def test_identical_assistants_and_empty_latest_remain_legal(tmp_path: Path) -> None:
    message = _assistant(['{"outcome":"READY"}'])
    events = _events(tmp_path, [message, message, _assistant(["  "])])
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed" and result.stdout == b""
    assert result.adapter_evidence["protocol_state"] == "settled"


def test_failed_backoff_end_settles_execution_failure(tmp_path: Path) -> None:
    events = _events(tmp_path, [_assistant(["retry stopped"], stop="error")])[:-1]
    events[-1]["willRetry"] = True
    events += [
        {"type": "auto_retry_start", "attempt": 1},
        {"type": "auto_retry_end", "success": False, "attempt": 1},
        {"type": "agent_settled"},
    ]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "execution_failure"
    assert result.stdout == b"" and result.writer_state == "stopped"
    assert result.adapter_evidence["failure_code"] == "pi_final_failure"
    assert result.adapter_evidence["protocol_state"] == "settled"


def test_successful_retry_end_does_not_settle_without_retried_run(
    tmp_path: Path,
) -> None:
    events = _events(tmp_path, [_assistant(["retry error"], stop="error")])[:-1]
    events[-1]["willRetry"] = True
    events += [
        {"type": "auto_retry_start"},
        {"type": "auto_retry_end", "success": True},
        {"type": "agent_settled"},
    ]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure" and result.stdout == b""


@pytest.mark.parametrize(
    "variant,state",
    [
        ("utf8", "invalid"),
        ("queue", "invalid"),
        ("after_settlement", "invalid"),
        ("surrogate", "invalid"),
        ("incomplete", "incomplete"),
        ("error", "settled"),
    ],
)
def test_protocol_state_records_parser_facts(
    tmp_path: Path, variant: str, state: str
) -> None:
    message = _assistant(["failure"], stop="error")
    if variant == "surrogate":
        message = _assistant(["\ud800"])
    events = _events(tmp_path, [message])
    if variant == "queue":
        events.insert(1, {"type": "queue_update", "steering": ["work"], "followUp": []})
    elif variant == "after_settlement":
        events.append({"type": "queue_update", "steering": [], "followUp": []})
    elif variant == "incomplete":
        events.pop()
    wire = b"\xff\n" if variant == "utf8" else _wire(events)
    result = _launch(tmp_path, _fake(tmp_path, wire))
    assert result.classification in {"protocol_failure", "execution_failure"}
    assert result.stdout == b""
    assert result.adapter_evidence["protocol_state"] == state


def _tool_events(
    cwd: Path, *, parallel: bool = False, is_error: bool = False
) -> list[dict[str, object]]:
    calls = ["call1", "call2"] if parallel else ["call1"]
    tool = _assistant([], stop="toolUse")
    tool["content"] = [
        {"type": "toolCall", "id": call, "name": "read", "arguments": {}}
        for call in calls
    ]
    final = _assistant(['{"outcome":"READY"}'])
    results: list[dict[str, object]] = [
        {
            "role": "toolResult",
            "toolCallId": call,
            "toolName": "read",
            "content": [],
            "isError": is_error,
            "timestamp": 2,
        }
        for call in calls
    ]
    events = _events(cwd, [tool, final])
    boundary = next(i for i, event in enumerate(events) if event["type"] == "turn_end")
    execution: list[dict[str, object]] = [
        {"type": "tool_execution_start", "toolCallId": call, "toolName": "read"}
        for call in calls
    ]
    execution += [
        {
            "type": "tool_execution_end",
            "toolCallId": call,
            "toolName": "read",
            "result": {},
            "isError": is_error,
        }
        for call in reversed(calls)
    ]
    for result in results:
        execution += [
            {"type": "message_start", "message": {"role": "toolResult"}},
            {"type": "message_end", "message": result},
        ]
    events[boundary:boundary] = execution
    events[-2]["messages"] = [tool, *results, final]
    return events


@pytest.mark.parametrize(
    "mutation",
    [
        "no_execution",
        "no_start",
        "no_end",
        "wrong_start_name",
        "wrong_end_name",
        "wrong_result_name",
        "duplicate_end",
    ],
)
def test_tool_result_requires_public_execution_lifecycle(
    tmp_path: Path, mutation: str
) -> None:
    events = _tool_events(tmp_path)
    if mutation in {"no_execution", "no_start", "no_end"}:
        removed = {
            "no_execution": {"tool_execution_start", "tool_execution_end"},
            "no_start": {"tool_execution_start"},
            "no_end": {"tool_execution_end"},
        }[mutation]
        events = [event for event in events if event["type"] not in removed]
    elif mutation == "duplicate_end":
        index = next(
            i for i, e in enumerate(events) if e["type"] == "tool_execution_end"
        )
        events.insert(index, dict(events[index]))
    elif mutation == "wrong_result_name":
        event = next(
            event
            for event in events
            if event["type"] == "message_end"
            and cast(dict[str, object], event["message"])["role"] == "toolResult"
        )
        cast(dict[str, object], event["message"])["toolName"] = "write"
    else:
        kind = (
            "tool_execution_start"
            if mutation == "wrong_start_name"
            else "tool_execution_end"
        )
        next(event for event in events if event["type"] == kind)["toolName"] = "write"
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure" and result.stdout == b""
    assert result.adapter_evidence["failure_code"] == "pi_event_order"


@pytest.mark.parametrize(
    "parallel,is_error", [(False, False), (False, True), (True, True)]
)
def test_parallel_tool_results_preserve_public_lifecycle(
    tmp_path: Path, parallel: bool, is_error: bool
) -> None:
    events = _tool_events(tmp_path, parallel=parallel, is_error=is_error)
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed" and b"READY" in result.stdout
    assert result.writer_state == "stopped"


@pytest.mark.parametrize("stop", ["error", "aborted"])
def test_failed_assistant_with_unexecuted_calls_is_execution_failure(
    tmp_path: Path, stop: str
) -> None:
    message = _assistant(["partial"], stop=stop)
    message["content"] = [
        {"type": "toolCall", "id": "partial", "name": "read", "arguments": {}}
    ]
    result = _launch(tmp_path, _fake(tmp_path, _wire(_events(tmp_path, [message]))))
    assert result.classification == "execution_failure"
    assert result.stdout == b"" and result.writer_state == "stopped"
    assert result.adapter_evidence["failure_code"] == "pi_final_failure"
    assert result.adapter_evidence["protocol_state"] == "settled"


def test_retry_after_unexecuted_failed_tool_call(tmp_path: Path) -> None:
    message = _assistant([], stop="error")
    message["content"] = [
        {"type": "toolCall", "id": "partial", "name": "read", "arguments": {}}
    ]
    events = _events(tmp_path, [message])[:-1]
    events[-1]["willRetry"] = True
    events.append({"type": "auto_retry_start", "attempt": 1})
    retried = _events(tmp_path, [_assistant(['{"outcome":"READY"}'])])[1:]
    boundary = next(i for i, event in enumerate(retried) if event["type"] == "turn_end")
    retried.insert(boundary, {"type": "auto_retry_end", "success": True})
    events += retried
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed" and b"READY" in result.stdout
    assert result.writer_state == "stopped"


@pytest.mark.parametrize("obligation", ["active", "finished", "unknown_result"])
def test_failed_assistant_does_not_discard_tool_execution_obligations(
    tmp_path: Path, obligation: str
) -> None:
    message = _assistant([], stop="aborted")
    message["content"] = [
        {"type": "toolCall", "id": "partial", "name": "read", "arguments": {}}
    ]
    events = _events(tmp_path, [message])
    boundary = next(i for i, event in enumerate(events) if event["type"] == "turn_end")
    extra: list[dict[str, object]] = [
        {"type": "tool_execution_start", "toolCallId": "partial", "toolName": "read"}
    ]
    if obligation == "finished":
        extra.append(
            {
                "type": "tool_execution_end",
                "toolCallId": "partial",
                "toolName": "read",
                "isError": True,
                "result": {},
            }
        )
    elif obligation == "unknown_result":
        extra = [
            {"type": "message_start", "message": {"role": "toolResult"}},
            {
                "type": "message_end",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "unknown",
                    "toolName": "read",
                    "content": [],
                    "isError": True,
                    "timestamp": 2,
                },
            },
        ]
    events[boundary:boundary] = extra
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure" and result.stdout == b""
    assert result.adapter_evidence["failure_code"] == "pi_event_order"


@pytest.mark.parametrize("stop", ["stop", "aborted", "length"])
def test_retry_declaration_cannot_revive_non_error_assistant(
    tmp_path: Path, stop: str
) -> None:
    events = _events(tmp_path, [_assistant(['{"outcome":"READY"}'], stop=stop)])[:-1]
    events[-1]["willRetry"] = True
    events += [
        {"type": "auto_retry_start"},
        {"type": "auto_retry_end", "success": False},
        {"type": "agent_settled"},
    ]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure" and result.stdout == b""


def test_second_successful_assistant_in_one_turn_is_invalid(tmp_path: Path) -> None:
    first = _assistant(["earlier"])
    second = _assistant(['{"outcome":"READY"}'])
    events = _events(tmp_path, [first])
    boundary = next(i for i, event in enumerate(events) if event["type"] == "turn_end")
    events[boundary:boundary] = [
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
        {"type": "message_end", "message": second},
    ]
    events[boundary + 2]["message"] = second
    events[-2]["messages"] = [first, second]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure" and result.stdout == b""


def _fallback_assistant(stop: str = "error") -> dict[str, object]:
    message = _assistant([""], stop=stop)
    usage = cast(dict[str, object], message["usage"])
    for key in ("input", "output", "cacheRead", "cacheWrite", "totalTokens"):
        usage[key] = 0
    message["errorMessage"] = ""
    return message


def _fallback_events(
    cwd: Path, position: str, stop: str = "error", *, full_snapshot: bool = False
) -> list[dict[str, object]]:
    user: dict[str, object] = {
        "role": "user",
        "content": [{"type": "text", "text": "request"}],
        "timestamp": 1,
    }
    events = _events(cwd, [_assistant(["earlier"])])
    events = events[:3] + [
        {"type": "message_start", "message": user},
        {"type": "message_end", "message": user},
    ]
    if position == "open":
        events.append({"type": "message_start", "message": {"role": "assistant"}})
    elif position == "after":
        prior = _assistant(["earlier"])
        events += [
            {"type": "message_start", "message": {"role": "assistant"}},
            {"type": "message_end", "message": prior},
        ]
    elif position == "between":
        events = _tool_events(cwd)
        boundary = next(
            i for i, event in enumerate(events) if event["type"] == "turn_end"
        )
        events = events[: boundary + 1]
    failure = _fallback_assistant(stop)
    events += [
        {"type": "message_start", "message": failure},
        {"type": "message_end", "message": failure},
        {"type": "turn_end", "message": failure, "toolResults": []},
        {
            "type": "agent_end",
            "messages": [user, failure] if full_snapshot else [failure],
            "willRetry": False,
        },
        {"type": "agent_settled"},
    ]
    return events


@pytest.mark.parametrize("position", ["before", "open", "after", "between"])
@pytest.mark.parametrize("stop", ["error", "aborted"])
def test_public_outer_failure_fallback_stays_execution_failure(
    tmp_path: Path, position: str, stop: str
) -> None:
    events = _fallback_events(tmp_path, position, stop)
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "execution_failure"
    assert result.stdout == b"" and result.writer_state == "stopped"
    assert result.adapter_evidence["protocol_state"] == "settled"
    assert result.adapter_evidence["failure_code"] == "pi_final_failure"


def test_full_in_band_failure_snapshot_is_still_legal(tmp_path: Path) -> None:
    events = _fallback_events(tmp_path, "before", full_snapshot=True)
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert (
        result.classification == "execution_failure"
        and result.writer_state == "stopped"
    )


def test_outer_error_fallback_can_retry_into_new_run(tmp_path: Path) -> None:
    events = _fallback_events(tmp_path, "after")[:-1]
    messages = cast(list[dict[str, object]], events[-1]["messages"])
    messages[0]["errorMessage"] = "503 Service Unavailable"
    events[-1]["willRetry"] = True
    events.append({"type": "auto_retry_start"})
    retried = _events(tmp_path, [_assistant(['{"outcome":"READY"}'])])[1:]
    boundary = next(i for i, event in enumerate(retried) if event["type"] == "turn_end")
    retried.insert(boundary, {"type": "auto_retry_end", "success": True})
    events += retried
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed" and b"READY" in result.stdout


@pytest.mark.parametrize(
    "mutation",
    ["start_shape", "end_mismatch", "snapshot", "snapshot_type", "prior_invalid"],
)
def test_malformed_fallback_cannot_repair_protocol_or_select_text(
    tmp_path: Path, mutation: str
) -> None:
    events = _fallback_events(tmp_path, "after")
    if mutation == "start_shape":
        start = next(
            event
            for event in events
            if event["type"] == "message_start"
            and cast(dict[str, object], event["message"]).get("stopReason") == "error"
        )
        start["message"] = {"role": "assistant"}
    elif mutation == "end_mismatch":
        end = next(
            event
            for event in events
            if event["type"] == "message_end"
            and cast(dict[str, object], event["message"]).get("stopReason") == "error"
        )
        end["message"] = dict(
            cast(dict[str, object], end["message"]), errorMessage="changed"
        )
    elif mutation == "snapshot":
        events[-2]["messages"] = []
    elif mutation == "snapshot_type":
        failure = dict(_fallback_assistant())
        failure["usage"] = dict(cast(dict[str, object], failure["usage"]), input=False)
        events[-2]["messages"] = [failure]
    else:
        events.insert(1, {"type": "unsupported_event"})
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure" and result.stdout == b""
    assert result.adapter_evidence["protocol_state"] == "invalid"


@pytest.mark.parametrize("stop", ["stop", "length", "error"])
def test_compaction_continuation_does_not_require_agent_end_retry(
    tmp_path: Path, stop: str
) -> None:
    events = _events(tmp_path, [_assistant(["previous"], stop=stop)])[:-1]
    events += [
        {"type": "compaction_start", "reason": "overflow"},
        {"type": "compaction_end", "willRetry": True},
    ]
    events += _events(tmp_path, [_assistant(['{"outcome":"READY"}'])])[1:]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "completed" and b"READY" in result.stdout


def test_new_agent_run_cannot_reuse_previous_assistant(tmp_path: Path) -> None:
    events = _events(tmp_path, [_assistant(['{"outcome":"READY"}'])])[:-1]
    events += [
        {"type": "agent_start"},
        {"type": "agent_end", "messages": [], "willRetry": False},
        {"type": "agent_settled"},
    ]
    result = _launch(tmp_path, _fake(tmp_path, _wire(events)))
    assert result.classification == "protocol_failure" and result.stdout == b""


def test_literal_child_stderr_marker_is_not_a_truncation_fact(tmp_path: Path) -> None:
    diagnostic = b"child says [stderr_truncated] but wrote a short diagnostic\n"
    script = _fake(
        tmp_path,
        _wire(_events(tmp_path, [_assistant(['{"outcome":"READY"}'])])),
        after=f"os.write(2,{diagnostic!r})",
    )
    result = _launch(tmp_path, script)
    assert result.classification == "completed" and result.stderr == diagnostic
    assert "stderr_truncated" not in result.diagnostic


def test_stderr_capture_truncation_labels_actual_fact(tmp_path: Path) -> None:
    script = _fake(
        tmp_path,
        _wire(_events(tmp_path, [_assistant(['{"outcome":"READY"}'])])),
        after="os.write(2,b'z'*70000)",
    )
    result = _launch(tmp_path, script)
    assert result.classification == "completed"
    assert len(result.stderr) == 65536
    assert result.stderr.count(b"[stderr_truncated]") == 1
    assert "stderr_truncated" in result.diagnostic
