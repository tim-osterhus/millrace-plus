"""Pinned Pi 0.87 finite JSON transport, with owned synchronous process cleanup.

Transferred from the accepted Core P4a adapter without protocol changes. The
pure profile/settings/plan/evidence rules live in `configuration`; this module
owns local invocation prerequisites, the version probe and the event lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from . import require_core

require_core()

from millrace.router.execution import (  # noqa: E402 - after the contract handshake
    AdapterConfiguration,
    AdapterResult,
    CompletionFacts,
    ExecutionAdapter,
    InvocationRequest,
    LaunchPlan,
    RouterError,
)

from . import configuration  # noqa: E402
from .configuration import mapping, number, strict_json, text  # noqa: E402


def _digest(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


class Stream:
    """Bounded current-message state and rolling per-run snapshot authority."""

    def __init__(self, cwd: Path, limit: int) -> None:
        self.cwd = str(cwd)
        self.limit = limit
        self.pending = bytearray()
        self.hash = hashlib.sha256()
        self.count = 0
        self.size = 0
        self.session: str | None = None
        self.run = False
        self.turn = False
        self.message: str | None = None
        self.recovery: set[str] = set()
        self.calls: dict[str, str] = {}
        self.executing: set[str] = set()
        self.executed: set[str] = set()
        self.run_messages = hashlib.sha256()
        self.latest: dict[str, object] | None = None
        self.turn_assistant: dict[str, object] | None = None
        self.fallback: dict[str, object] | None = None
        self.settled = False
        self.invalid = False
        self.last_retry: bool | None = None

    def feed(self, raw: bytes) -> None:
        self.hash.update(raw)
        self.size += len(raw)
        start = 0
        while start < len(raw):
            end = raw.find(b"\n", start)
            part = raw[start:] if end < 0 else raw[start:end]
            if len(self.pending) + len(part) > self.limit:
                raise RouterError("pi_frame_too_large")
            self.pending.extend(part)
            if end < 0:
                return
            line = bytes(self.pending)
            self.pending.clear()
            if line.endswith(b"\r"):
                line = line[:-1]
            self.event(mapping(strict_json(line)))
            self.count += 1
            start = end + 1

    def event(self, event: dict[str, object]) -> None:
        kind = text(event.get("type"))
        if self.settled:
            raise RouterError("pi_after_settlement")
        if self.session is None:
            if (
                kind != "session"
                or type(event.get("version")) is not int
                or event["version"] != 3
                or event.get("cwd") != self.cwd
                or event.get("parentSession") is not None
            ):
                raise RouterError("pi_session_header")
            self.session = text(event.get("id"))
            text(event.get("timestamp"))
            return
        if kind == "agent_start":
            if (
                self.run
                or self.turn
                or self.message
                or self.executing
                or "compaction" in self.recovery
            ):
                raise RouterError("pi_event_order")
            self.run = True
            self.run_messages = hashlib.sha256()
            self.turn_assistant = None
            self.fallback = None
        elif kind == "turn_start":
            if (
                not self.run
                or self.turn
                or "compaction" in self.recovery
                or "summarization_retry" in self.recovery
                or self.fallback is not None
            ):
                raise RouterError("pi_event_order")
            self.turn = True
            self.turn_assistant = None
        elif kind == "message_start":
            message = mapping(event.get("message"))
            role = text(message.get("role"))
            if self._failure_fallback(message):
                if not self.run or self.executing or self.executed:
                    raise RouterError("pi_event_order")
                self.fallback = message
                self.turn = True
            elif (
                not self.turn
                or self.message is not None
                or self.fallback is not None
                or (role == "assistant" and self.turn_assistant is not None)
            ):
                raise RouterError("pi_event_order")
            self.message = role
        elif kind == "message_update":
            if self.message != "assistant":
                raise RouterError("pi_event_order")
            mapping(event.get("assistantMessageEvent"))
            if "usage" in event:
                mapping(event["usage"])
        elif kind == "message_end":
            message = mapping(event.get("message"))
            if self.message is None or message.get("role") != self.message:
                raise RouterError("pi_event_order")
            if self.message == "assistant":
                if self.fallback is not None and _digest(message) != _digest(
                    self.fallback
                ):
                    raise RouterError("pi_snapshot_mismatch")
                self._assistant(message)
                self.latest = message
                self.turn_assistant = message
            elif self.message == "toolResult":
                call = text(message.get("toolCallId"))
                name = text(message.get("toolName"))
                if call not in self.executed or name != self.calls.get(call):
                    raise RouterError("pi_event_order")
                if (
                    not isinstance(message.get("content"), list)
                    or type(message.get("isError")) is not bool
                ):
                    raise RouterError("pi_message_shape")
                number(message.get("timestamp"), zero=True)
                del self.calls[call]
                self.executed.remove(call)
            elif self.message not in {"user", "system"}:
                raise RouterError("pi_message_shape")
            self.run_messages.update(_digest(message) + b"\n")
            self.message = None
        elif kind in {
            "tool_execution_start",
            "tool_execution_update",
            "tool_execution_end",
        }:
            call = text(event.get("toolCallId"))
            if (
                not self.turn
                or self.fallback is not None
                or call not in self.calls
                or event.get("toolName") != self.calls[call]
            ):
                raise RouterError("pi_event_order")
            if kind == "tool_execution_start":
                if call in self.executing or call in self.executed:
                    raise RouterError("pi_event_order")
                self.executing.add(call)
            elif call not in self.executing:
                raise RouterError("pi_event_order")
            elif kind == "tool_execution_end":
                if type(event.get("isError")) is not bool or "result" not in event:
                    raise RouterError("pi_message_shape")
                self.executing.remove(call)
                self.executed.add(call)
        elif kind == "turn_end":
            if (
                not self.turn
                or self.message
                or self.executing
                or self.executed
                or self.turn_assistant is None
                or (
                    self.calls
                    and self.turn_assistant["stopReason"] not in {"error", "aborted"}
                )
            ):
                raise RouterError("pi_event_order")
            if _digest(event.get("message")) != _digest(self.turn_assistant):
                raise RouterError("pi_snapshot_mismatch")
            self.calls.clear()
            self.turn = False
        elif kind == "agent_end":
            if (
                not self.run
                or self.turn
                or self.message
                or self.calls
                or self.turn_assistant is None
                or type(event.get("willRetry")) is not bool
                or (
                    event.get("willRetry") is True
                    and self.turn_assistant["stopReason"] != "error"
                )
            ):
                raise RouterError("pi_event_order")
            messages = event.get("messages")
            if not isinstance(messages, list):
                raise RouterError("pi_message_shape")
            digest = hashlib.sha256()
            for message in messages:
                digest.update(_digest(mapping(message)) + b"\n")
            if digest.digest() != self.run_messages.digest() and (
                self.fallback is None or _digest(messages) != _digest([self.fallback])
            ):
                raise RouterError("pi_snapshot_mismatch")
            self.last_retry = cast(bool, event["willRetry"])
            self.run = False
        elif kind == "agent_settled":
            if (
                self.run
                or self.turn
                or self.message
                or self.calls
                or self.executing
                or self.recovery
                or self.last_retry is not False
            ):
                raise RouterError("pi_event_order")
            self.settled = True
        elif kind in {
            "auto_retry_start",
            "compaction_start",
            "summarization_retry_scheduled",
        }:
            phase = kind.removesuffix("_start").removesuffix("_scheduled")
            if self.turn or self.message or self.executing:
                raise RouterError("pi_event_order")
            if kind == "auto_retry_start":
                if self.run or self.last_retry is not True:
                    raise RouterError("pi_event_order")
            elif kind == "compaction_start" and phase in self.recovery:
                raise RouterError("pi_event_order")
            if (
                kind == "summarization_retry_scheduled"
                and "compaction" not in self.recovery
            ):
                raise RouterError("pi_event_order")
            self.recovery.add(phase)
        elif kind in {
            "auto_retry_end",
            "compaction_end",
            "summarization_retry_finished",
        }:
            phase = kind.removesuffix("_end").removesuffix("_finished")
            if phase not in self.recovery or (
                phase == "compaction" and "summarization_retry" in self.recovery
            ):
                raise RouterError("pi_event_order")
            self.recovery.remove(phase)
            if kind == "auto_retry_end" and event.get("success") is False:
                self.last_retry = False
        elif kind == "summarization_retry_attempt_start":
            if "summarization_retry" not in self.recovery:
                raise RouterError("pi_event_order")
        elif kind == "queue_update":
            if event.get("steering") != [] or event.get("followUp") != []:
                raise RouterError("pi_external_queue")
        elif kind == "entry_appended":
            mapping(event.get("entry"))
        elif kind == "session_info_changed":
            if "name" in event and not isinstance(event["name"], str):
                raise RouterError("pi_message_shape")
        elif kind == "thinking_level_changed":
            if event.get("level") not in {
                "off",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            }:
                raise RouterError("pi_message_shape")
        else:
            raise RouterError("unsupported_pi_event")

    def _failure_fallback(self, message: dict[str, object]) -> bool:
        """The public Agent owner can replace partial output with this failure."""
        if (
            message.get("role") != "assistant"
            or message.get("stopReason") not in {"error", "aborted"}
            or message.get("content") != [{"type": "text", "text": ""}]
            or not isinstance(message.get("errorMessage"), str)
        ):
            return False
        usage = mapping(message.get("usage"))
        cost = mapping(usage.get("cost"))
        return all(
            usage.get(key) == 0
            for key in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")
        ) and all(
            cost.get(key) == 0
            for key in ("input", "output", "cacheRead", "cacheWrite", "total")
        )

    def _assistant(self, message: dict[str, object]) -> None:
        for key in ("api", "provider", "model"):
            text(message.get(key))
        usage = mapping(message.get("usage"))
        cost = mapping(usage.get("cost"))
        for values, keys in (
            (usage, ("input", "output", "cacheRead", "cacheWrite", "totalTokens")),
            (cost, ("input", "output", "cacheRead", "cacheWrite", "total")),
        ):
            for key in keys:
                value = values.get(key)
                if type(value) not in {int, float} or (
                    isinstance(value, float) and not math.isfinite(value)
                ):
                    raise RouterError("pi_message_shape")
        number(message.get("timestamp"), zero=True)
        if message.get("stopReason") not in {
            "pending",
            "stop",
            "length",
            "toolUse",
            "error",
            "aborted",
            "deferred",
        }:
            raise RouterError("pi_message_shape")
        content = message.get("content")
        if not isinstance(content, list):
            raise RouterError("pi_message_shape")
        for raw in content:
            block = mapping(raw)
            if block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    raise RouterError("pi_message_shape")
            elif block.get("type") == "thinking":
                if not isinstance(block.get("thinking"), str):
                    raise RouterError("pi_message_shape")
            elif block.get("type") == "toolCall":
                call = text(block.get("id"))
                name = text(block.get("name"))
                mapping(block.get("arguments"))
                if call in self.calls:
                    raise RouterError("pi_event_order")
                self.calls[call] = name
            else:
                raise RouterError("pi_message_shape")

    def final(self, effective: dict[str, object]) -> tuple[str, str | None, bytes]:
        if self.pending or not self.settled or self.latest is None:
            return "protocol_failure", "pi_incomplete_stream", b""
        if (
            self.latest["provider"] != effective["provider"]
            or self.latest["model"] != effective["model"]
        ):
            return "protocol_failure", "pi_model_mismatch", b""
        if self.latest["stopReason"] in {"error", "aborted"}:
            return "execution_failure", "pi_final_failure", b""
        if any(
            mapping(b)["type"] == "toolCall"
            for b in cast(list[object], self.latest["content"])
        ):
            return "protocol_failure", "pi_final_tool_call", b""
        if self.latest["stopReason"] == "toolUse":
            return "protocol_failure", "pi_final_tool_call", b""
        if self.latest["stopReason"] != "stop":
            return "execution_failure", "pi_final_failure", b""
        raw = (
            "".join(
                str(mapping(b)["text"])
                for b in cast(list[object], self.latest["content"])
                if mapping(b)["type"] == "text"
            )
            .strip()
            .encode()
        )
        if len(raw) > 65536:
            return "protocol_failure", "pi_final_text_too_large", b""
        return "completed", None, raw

    def evidence(
        self, invocation_id: str, failure: str | None, raw: bytes
    ) -> dict[str, object]:
        final = None
        if self.latest is not None:
            final = {
                "provider": self.latest["provider"],
                "model": self.latest["model"],
                "stop_reason": self.latest["stopReason"],
                "tool_calls": sum(
                    mapping(b)["type"] == "toolCall"
                    for b in cast(list[object], self.latest["content"])
                ),
                "text_sha256": hashlib.sha256(raw).hexdigest()
                if failure is None
                else None,
            }
        return {
            "format": "millrace-pi-json-result/v1",
            "invocation_id": invocation_id,
            "phase": "invocation",
            "observed_version": "0.87.0",
            "session_id": self.session,
            "protocol_state": "invalid"
            if self.invalid
            else "settled"
            if self.settled
            else "incomplete",
            "event_count": self.count,
            "event_bytes": self.size,
            "stream_sha256": self.hash.hexdigest(),
            "final": final,
            "failure_code": failure,
        }


def _group(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _redact(
    raw: bytes, env: dict[str, str], limit: int, *, truncated: bool = False
) -> tuple[bytes, bool]:
    truncated = truncated or len(raw) > limit
    for name, value in env.items():
        if name != "PATH" and value:
            raw = raw.replace(value.encode(), b"[redacted]")
            truncated = truncated or len(raw) > limit
            raw = raw[:limit]
    if truncated:
        marker = b"\n[stderr_truncated]"
        return raw[: max(0, limit - len(marker))] + marker[:limit], True
    return raw[:limit], False


def _process(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    deadline: float,
    cancelled: Callable[[], bool],
    on_started: Callable[[int], None],
    stream: Stream | None,
    effective: dict[str, object],
) -> tuple[bytes, bytes, str, str | None, str, int | None, bool]:
    """One owned root/group; poll even after failure and throughout cleanup."""
    probe = stream is None
    if os.name != "posix":
        return (
            b"",
            b"",
            "execution_failure",
            "unsupported_platform",
            "stopped",
            None,
            False,
        )
    absolute = deadline
    probe_deadline = min(deadline, time.time() + 5) if probe else deadline
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = False
    failure: str | None = None
    classification: str | None = None
    raw_result = b""
    cleanup: float | None = None
    forced = False
    killed = False
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    try:
        stop = cancelled()
        now = time.time()
        if stop or now >= absolute:
            return (
                b"",
                b"",
                "cancelled" if stop else "timeout",
                "pi_cancelled" if stop else "pi_timeout",
                "stopped",
                None,
                False,
            )
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except (OSError, ValueError):
            return (
                b"",
                b"",
                "execution_failure",
                "pi_launch_failed",
                "stopped",
                None,
                False,
            )
        assert process.stdout is not None and process.stderr is not None
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        on_started(process.pid)
        while True:
            stop = cancelled()
            now = time.time()
            if classification is None:
                if stop:
                    classification, failure = "cancelled", "pi_cancelled"
                elif now >= absolute:
                    classification, failure = "timeout", "pi_timeout"
                elif probe and now >= probe_deadline:
                    classification, failure = "execution_failure", "pi_version_timeout"
            if (
                classification is not None
                and classification != "completed"
                and cleanup is None
            ):
                cleanup = time.monotonic()
            if cleanup is not None and not forced and _group(process.pid):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    forced = True
                else:
                    forced = True
            if (
                cleanup is not None
                and time.monotonic() - cleanup >= 2
                and not killed
                and _group(process.pid)
            ):
                killed = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    forced = True
                else:
                    forced = True
            for key, _ in selector.select(0.02):
                try:
                    chunk = os.read(key.fd, 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout" and stream is not None:
                    if classification is None:
                        try:
                            stream.feed(chunk)
                        except (
                            RouterError,
                            UnicodeError,
                            RecursionError,
                            TypeError,
                            ValueError,
                            KeyError,
                        ) as exc:
                            stream.invalid = True
                            classification = "protocol_failure"
                            failure = (
                                exc.code
                                if isinstance(exc, RouterError)
                                else "pi_invalid_text"
                            )
                else:
                    name = str(key.data)
                    limit = 4096 if probe else 65536
                    room = limit - len(buffers[name])
                    buffers[name].extend(chunk[:room])
                    if len(chunk) > room:
                        if name == "stdout" and classification is None:
                            classification, failure = (
                                "execution_failure",
                                "pi_version_protocol",
                            )
                        else:
                            truncated = True
            code = process.poll()
            closed = not selector.get_map()
            group_gone = not _group(process.pid)
            if code is not None and closed and classification is None:
                if probe:
                    if code == 0 and bytes(buffers["stdout"]) in {
                        b"0.87.0",
                        b"0.87.0\n",
                        b"0.87.0\r\n",
                    }:
                        classification = "completed"
                        raw_result = bytes(buffers["stdout"])
                    else:
                        classification, failure = (
                            "execution_failure",
                            "pi_version_mismatch",
                        )
                        buffers["stderr"].extend(buffers["stdout"][:4096])
                else:
                    assert stream is not None
                    try:
                        classification, failure, raw_result = stream.final(effective)
                    except (RouterError, UnicodeError, RecursionError):
                        stream.invalid = True
                        classification, failure, raw_result = (
                            "protocol_failure",
                            "pi_invalid_text",
                            b"",
                        )
                    if code != 0:
                        classification, failure, raw_result = (
                            "execution_failure",
                            "pi_nonzero_exit",
                            b"",
                        )
            if code is not None and closed and group_gone:
                diagnostic, stderr_truncated = _redact(
                    bytes(buffers["stderr"]),
                    env,
                    4096 if probe else 65536,
                    truncated=truncated,
                )
                return (
                    raw_result if classification == "completed" else b"",
                    diagnostic,
                    classification or "execution_failure",
                    failure,
                    "may_live"
                    if forced
                    or (
                        not probe
                        and (
                            code != 0
                            or stream is None
                            or not stream.settled
                            or failure
                            not in {
                                None,
                                "pi_final_failure",
                                "pi_final_text_too_large",
                                "pi_model_mismatch",
                            }
                        )
                    )
                    else "stopped",
                    code,
                    stderr_truncated,
                )
            if code is not None and cleanup is None:
                cleanup = time.monotonic()
            if cleanup is not None and time.monotonic() - cleanup >= 5:
                diagnostic, stderr_truncated = _redact(
                    bytes(buffers["stderr"]), env, 65536, truncated=truncated
                )
                return (
                    raw_result if classification == "completed" else b"",
                    diagnostic,
                    classification or "execution_failure",
                    failure
                    or (
                        None
                        if classification == "completed"
                        else "pi_cleanup_unconfirmed"
                    ),
                    "may_live",
                    code,
                    stderr_truncated,
                )
    finally:
        selector.close()
        if process is not None:
            for owned_pipe in (process.stdout, process.stderr):
                if owned_pipe is not None:
                    owned_pipe.close()
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    process.wait(timeout=0)
                except subprocess.TimeoutExpired:
                    pass


def invoke(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    deadline: float,
    cancelled: Callable[[], bool],
    on_started: Callable[[int], None],
    *,
    invocation_id: str,
    native_launch: dict[str, object],
    effective: dict[str, object],
) -> AdapterResult:
    prefix = argv[:2] if native_launch["loader_path"] is not None else argv[:1]
    raw, err, classification, failure, writer, code, _ = _process(
        prefix + ["--offline", "--version"],
        cwd,
        env,
        deadline,
        cancelled,
        lambda _: None,
        None,
        effective,
    )
    if classification != "completed" or writer != "stopped":
        if classification == "completed":
            classification, failure = (
                "execution_failure",
                "pi_version_cleanup_unconfirmed",
            )
        evidence: dict[str, object] = {
            "format": "millrace-pi-json-result/v1",
            "invocation_id": invocation_id,
            "phase": "preflight",
            "observed_version": "0.87.0" if raw else None,
            "session_id": None,
            "protocol_state": "not_started",
            "event_count": 0,
            "event_bytes": 0,
            "stream_sha256": None,
            "final": None,
            "failure_code": failure,
        }
        return AdapterResult(
            b"", err, classification, writer, failure or "", code, evidence
        )
    try:
        fd = os.open(str(native_launch["prompt_path"]), os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as prompt_file:
            if not stat.S_ISREG(os.fstat(prompt_file.fileno()).st_mode):
                raise RouterError("pi_prompt_changed")
            remaining = os.fstat(prompt_file.fileno()).st_size
            prompt_hash = hashlib.sha256()
            while remaining:
                if cancelled():
                    raise RouterError("pi_cancelled")
                if time.time() >= deadline:
                    raise RouterError("pi_timeout")
                chunk = prompt_file.read(min(8192, remaining))
                if not chunk:
                    raise RouterError("pi_prompt_changed")
                prompt_hash.update(chunk)
                remaining -= len(chunk)
            if prompt_file.read(1):
                raise RouterError("pi_prompt_changed")
        if prompt_hash.hexdigest() != native_launch["prompt_sha256"]:
            raise RouterError("pi_prompt_changed")
    except (OSError, RouterError) as exc:
        prompt_failure = (
            exc.code if isinstance(exc, RouterError) else "pi_prompt_changed"
        )
        prompt_classification = {
            "pi_cancelled": "cancelled",
            "pi_timeout": "timeout",
        }.get(prompt_failure, "execution_failure")
        evidence = {
            "format": "millrace-pi-json-result/v1",
            "invocation_id": invocation_id,
            "phase": "preflight",
            "observed_version": "0.87.0",
            "session_id": None,
            "protocol_state": "not_started",
            "event_count": 0,
            "event_bytes": 0,
            "stream_sha256": None,
            "final": None,
            "failure_code": prompt_failure,
        }
        return AdapterResult(
            b"", b"", prompt_classification, "stopped", prompt_failure, 0, evidence
        )
    stream = Stream(cwd, cast(int, native_launch["max_event_bytes"]))
    raw, err, classification, failure, writer, code, truncated = _process(
        argv, cwd, env, deadline, cancelled, on_started, stream, effective
    )
    evidence = stream.evidence(invocation_id, failure, raw)
    diagnostic = failure or ("cleanup_unconfirmed" if writer != "stopped" else "")
    if truncated:
        diagnostic += ",stderr_truncated"
    return AdapterResult(raw, err, classification, writer, diagnostic, code, evidence)


def _host_path(value: object, *, directory: bool) -> str | None:
    """Live locator presence/type, checked only while preparing an invocation."""
    path = Path(str(value))
    try:
        if not path.exists():
            return "missing_host_path"
        if str(path.resolve()) != str(value) or not (
            path.is_dir() if directory else path.is_file()
        ):
            return "invalid_host_path"
    except (OSError, RuntimeError):
        return "invalid_host_path"
    return None


def preflight(native_launch: Mapping[str, object]) -> str | None:
    """Local prerequisites that must refuse before any probe process exists."""
    prompt_path = str(native_launch["prompt_path"])
    if any(c in prompt_path for c in configuration.UNSAFE_PROMPT_PATH):
        return "invalid_pi_prompt_path"
    failure = _host_path(native_launch["agent_dir"], directory=True)
    if failure is None and native_launch["loader_path"] is not None:
        failure = _host_path(native_launch["loader_path"], directory=False)
    return failure


class PiJsonAdapter:
    """The Plus-owned `pi_json` / `millrace-pi-json/v1` execution adapter."""

    adapter_id = configuration.ADAPTER_ID
    adapter_contract = configuration.ADAPTER_CONTRACT

    def validate_configuration(self, config: AdapterConfiguration) -> None:
        configuration.validate_configuration(config)

    def plan(self, request: InvocationRequest) -> LaunchPlan:
        return configuration.plan(request)

    def invoke(
        self,
        plan: LaunchPlan,
        *,
        cwd: Path,
        env: Mapping[str, str],
        deadline: float,
        poll: Callable[[], bool],
        on_started: Callable[[int], None],
    ) -> AdapterResult:
        native = configuration.launch_details(plan)
        failure = preflight(native)
        if failure is not None:
            return AdapterResult(
                b"",
                b"",
                "execution_failure",
                "stopped",
                failure,
                None,
                configuration.preflight_evidence(
                    plan.invocation_id, failure, observed=None
                ),
            )
        return invoke(
            list(plan.argv),
            cwd,
            dict(env),
            deadline,
            poll,
            on_started,
            invocation_id=plan.invocation_id,
            native_launch=native,
            effective=configuration.effective(plan),
        )

    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts
    ) -> None:
        configuration.validate_completion(plan, completion)


def resolve_adapter(adapter_id: str, contract: str) -> ExecutionAdapter:
    """Explicit Plus resolver: only the exact Pi pair; never the builtin command."""
    if (adapter_id, contract) == (
        configuration.ADAPTER_ID,
        configuration.ADAPTER_CONTRACT,
    ):
        return PiJsonAdapter()
    raise RouterError("unsupported_adapter_contract", f"{adapter_id} {contract}")
