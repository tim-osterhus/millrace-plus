"""Finite pinned OpenCode JSONL projection and synchronous POSIX ownership."""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from . import require_core

require_core()
from millrace.router.execution import (  # noqa: E402
    AdapterConfiguration,
    AdapterResult,
    CompletionFacts,
    InvocationRequest,
    LaunchPlan,
    RouterError,
)

from . import opencode_configuration as c  # noqa: E402
from .configuration import fields, mapping, number, strict_json, text  # noqa: E402


def _identity(value: object) -> str:
    result = text(value)
    if len(result.encode()) > 4096 or any(ord(ch) < 32 for ch in result):
        raise RouterError("opencode_invalid_identity")
    return result


def _time(value: object, *, tool: bool = False) -> None:
    obj = fields(value, {"start", "end"}, {"compacted"} if tool else set())
    number(obj["start"], zero=True)
    number(obj["end"], zero=tool)
    if "compacted" in obj:
        number(obj["compacted"], zero=True)
    if cast(float, obj["end"]) < cast(float, obj["start"]):
        raise RouterError("opencode_invalid_part")


class Stream:
    """Bounded framing and identities; only one stopped message supplies bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.hash = hashlib.sha256()
        self.bytes = 0
        self.count = 0
        self.errors = 0
        self.session: str | None = None
        self.final_message: str | None = None
        self.invalid = False
        self.parts: dict[str, str] = {}
        self.texts: dict[str, list[bytes]] = {}

    def feed(self, raw: bytes) -> None:
        self.bytes += len(raw)
        self.hash.update(raw)
        if self.invalid:
            return
        try:
            if self.bytes > c.EVENT_LIMIT:
                raise RouterError("opencode_event_limit")
            self.buffer.extend(raw)
            while b"\n" in self.buffer:
                line, _, tail = self.buffer.partition(b"\n")
                self.buffer = bytearray(tail)
                if len(line) > c.LINE_LIMIT:
                    raise RouterError("opencode_line_limit")
                self.event(mapping(strict_json(bytes(line))))
            if len(self.buffer) > c.LINE_LIMIT:
                raise RouterError("opencode_line_limit")
        except (RouterError, UnicodeError, ValueError, TypeError, RecursionError):
            self.invalid = True
            self.buffer.clear()
            raise

    def event(self, event: dict[str, object]) -> None:
        self.count += 1
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in {
            "step_start",
            "step_finish",
            "text",
            "tool_use",
            "reasoning",
            "error",
        }:
            raise RouterError("opencode_unknown_event")
        obj = fields(
            event,
            {"type", "timestamp", "sessionID", "error" if kind == "error" else "part"},
        )
        number(obj["timestamp"], zero=True)
        session = _identity(obj["sessionID"])
        if self.session is not None and self.session != session:
            raise RouterError("opencode_session_conflict")
        self.session = session
        if kind == "error":
            error = mapping(obj["error"])
            text(error.get("name"))
            self.errors += 1
            raise RouterError("opencode_native_error")
        part = mapping(obj["part"])
        identity, message = _identity(part.get("id")), _identity(part.get("messageID"))
        if part.get("sessionID") != session:
            raise RouterError("opencode_session_conflict")
        expected = {
            "step_start": "step-start",
            "step_finish": "step-finish",
            "tool_use": "tool",
        }.get(kind, kind)
        if part.get("type") != expected:
            raise RouterError("opencode_invalid_part")
        base = {"id", "messageID", "sessionID", "type"}
        if kind == "step_start":
            fields(part, base, {"snapshot"})
        elif kind == "step_finish":
            fields(part, base | {"reason", "cost", "tokens"}, {"snapshot"})
            number(part["cost"], zero=True)
            tokens = fields(
                part["tokens"], {"input", "output", "reasoning", "cache"}, {"total"}
            )
            for key in tokens.keys() - {"cache"}:
                number(tokens[key], zero=True)
            for value in fields(tokens["cache"], {"read", "write"}).values():
                number(value, zero=True)
            if part["reason"] not in {"stop", "tool-calls"}:
                raise RouterError("opencode_stop_reason")
        elif kind in {"text", "reasoning"}:
            fields(
                part,
                base | {"text", "time"},
                {"metadata", "synthetic", "ignored"}
                if kind == "text"
                else {"metadata"},
            )
            if not isinstance(part["text"], str):
                raise RouterError("opencode_invalid_part")
            _time(part["time"])
            for key in ("synthetic", "ignored"):
                if key in part and type(part[key]) is not bool:
                    raise RouterError("opencode_invalid_part")
            if "metadata" in part:
                mapping(part["metadata"])
        else:
            fields(part, base | {"callID", "tool", "state"}, {"metadata"})
            _identity(part["callID"])
            text(part["tool"])
            state = mapping(part["state"])
            if state.get("status") not in {"completed", "error"}:
                raise RouterError("opencode_invalid_part")
            mapping(state.get("input"))
            _time(state.get("time"), tool=True)
            if not isinstance(
                state.get("output" if state["status"] == "completed" else "error"), str
            ):
                raise RouterError("opencode_invalid_part")
        content = c.digest(
            json.dumps(
                part,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        )
        if identity in self.parts:
            if self.parts[identity] != content:
                raise RouterError("opencode_part_conflict")
            return
        self.parts[identity] = content
        if self.final_message is not None and (
            message != self.final_message or kind == "step_finish"
        ):
            raise RouterError("opencode_event_after_stop")
        if kind == "text":
            self.texts.setdefault(message, []).append(cast(str, part["text"]).encode())
        elif kind == "step_finish" and part["reason"] == "stop":
            self.final_message = message

    def finish(self) -> bytes:
        if self.invalid:
            raise RouterError("opencode_invalid_events")
        if self.buffer:
            # A complete final JSON record without a newline is legal; a partial
            # one is rejected by strict_json. EOF is established by the owner.
            try:
                self.event(mapping(strict_json(bytes(self.buffer))))
                self.buffer.clear()
            except (RouterError, UnicodeError, ValueError, TypeError, RecursionError):
                self.invalid = True
                raise
        if not self.final_message or self.errors:
            raise RouterError("opencode_missing_stop")
        raw = b"".join(self.texts.get(self.final_message, []))
        if not raw:
            raise RouterError("opencode_missing_text")
        return raw


@dataclass
class _Process:
    raw: bytes = b""
    transport: str = "execution_failure"
    writer: str = "stopped"
    failure: str | None = None
    code: int | None = None
    pid: int | None = None
    stderr_bytes: int = 0
    stderr_sha256: str | None = None


def _group(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _process(
    argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    deadline: float,
    poll: Callable[[], bool],
    started: Callable[[int], None],
    *,
    prompt: bytes = b"",
    stream: Stream | None = None,
) -> _Process:
    """Own pipes/group through delivery, EOF and closure, with bounded cleanup."""
    probe = stream is None
    if os.name != "posix":
        return _Process(failure="opencode_unsupported_platform")
    stop = poll()
    if stop or time.time() >= deadline:
        return _Process(
            transport="cancelled" if stop else "timeout",
            failure="opencode_cancelled" if stop else "opencode_timeout",
        )
    try:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return _Process(failure="opencode_spawn_failed")
    assert child.stdin and child.stdout and child.stderr
    selector = selectors.DefaultSelector()
    version = bytearray()
    stderr_hash = hashlib.sha256()
    stderr_bytes = 0
    offset = 0
    reason: str | None = None
    failure: str | None = None
    cleanup: float | None = None
    killed = False
    finished = False
    end = time.monotonic() + max(0, deadline - time.time())
    probe_end = min(end, time.monotonic() + 5)

    def fail(kind: str, code: str) -> None:
        nonlocal reason, failure
        if reason is None:
            reason, failure = kind, code

    try:
        for name, pipe, event in (
            ("stdin", child.stdin, selectors.EVENT_WRITE),
            ("stdout", child.stdout, selectors.EVENT_READ),
            ("stderr", child.stderr, selectors.EVENT_READ),
        ):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, event, name)
        started(child.pid)
        while True:
            now = time.monotonic()
            if poll():
                fail("cancelled", "opencode_cancelled")
            if now >= end:
                fail("timeout", "opencode_timeout")
            if probe and now >= probe_end:
                fail("execution_failure", "opencode_version_timeout")
            if reason is not None and cleanup is None:
                cleanup = now
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            if cleanup is not None and now - cleanup >= 2 and not killed:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                killed = True
            for key, _ in selector.select(0.02):
                pipe = cast(BinaryIO, key.fileobj)
                if key.data == "stdin":
                    if offset == len(prompt) or reason is not None:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    try:
                        offset += os.write(key.fd, prompt[offset : offset + 8192])
                    except BlockingIOError:
                        pass
                    except BrokenPipeError:
                        selector.unregister(pipe)
                        pipe.close()
                        fail("execution_failure", "opencode_stdin_failure")
                    continue
                try:
                    chunk = os.read(key.fd, 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(pipe)
                    continue
                if key.data == "stderr":
                    stderr_bytes += len(chunk)
                    stderr_hash.update(chunk)
                    if stderr_bytes > c.STDERR_LIMIT:
                        fail("protocol_failure", "opencode_stderr_limit")
                elif probe:
                    room = max(0, 4096 - len(version))
                    version.extend(chunk[:room])
                    if len(chunk) > room:
                        fail("execution_failure", "opencode_version_protocol")
                else:
                    assert stream is not None
                    try:
                        stream.feed(chunk)
                    except (
                        RouterError,
                        UnicodeError,
                        ValueError,
                        TypeError,
                        RecursionError,
                    ) as exc:
                        fail(
                            "protocol_failure",
                            exc.code
                            if isinstance(exc, RouterError)
                            else "opencode_invalid_events",
                        )
            code = child.poll()
            if code is not None and offset < len(prompt):
                fail("execution_failure", "opencode_stdin_failure")
            closed = not selector.get_map()
            natural = code is not None and closed and not _group(child.pid)
            if natural or (cleanup is not None and time.monotonic() - cleanup >= 5):
                raw = b""
                if reason is None:
                    if code != 0:
                        fail("execution_failure", "opencode_nonzero_exit")
                    elif probe:
                        if bytes(version) in {b"1.19.3", b"1.19.3\n", b"1.19.3\r\n"}:
                            raw = bytes(version)
                        else:
                            fail("execution_failure", "opencode_version_mismatch")
                    else:
                        assert stream is not None
                        try:
                            raw = stream.finish()
                        except (
                            RouterError,
                            UnicodeError,
                            ValueError,
                            TypeError,
                            RecursionError,
                        ) as exc:
                            fail(
                                "protocol_failure",
                                exc.code
                                if isinstance(exc, RouterError)
                                else "opencode_invalid_events",
                            )
                finished = True
                return _Process(
                    raw if reason is None else b"",
                    reason or "completed",
                    "may_live" if cleanup is not None else "stopped",
                    failure,
                    code,
                    child.pid,
                    stderr_bytes,
                    stderr_hash.hexdigest() if stderr_bytes else None,
                )
    finally:
        selector.close()
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()
        if not finished:
            # Callback exceptions cannot orphan a process this invocation owns.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            until = time.monotonic() + 2
            while _group(child.pid) and time.monotonic() < until:
                child.poll()
                time.sleep(0.02)
            if _group(child.pid):
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        if child.poll() is None:
            try:
                child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass


class OpenCodeRunAdapter:
    """One-shot native projection; Core parses json-v1 and owns durable routing."""

    adapter_id = c.ADAPTER_ID
    adapter_contract = c.ADAPTER_CONTRACT

    def validate_configuration(self, config: AdapterConfiguration) -> None:
        c.validate_configuration(config)

    def plan(self, request: InvocationRequest) -> LaunchPlan:
        return c.plan(request)

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
        c.launch_details(plan)
        evidence = c.evidence(plan.invocation_id)
        # Core supplies the declared environment. Do not widen it with ambient
        # variables or inspect/copy native config and credential stores.
        native_env = dict(env)
        probe = _process(
            (plan.argv[0], "--version"), cwd, native_env, deadline, poll, lambda _: None
        )
        evidence.update(
            probe_pid=probe.pid,
            stderr_bytes=probe.stderr_bytes,
            stderr_sha256=probe.stderr_sha256,
        )
        if probe.transport != "completed" or probe.writer != "stopped":
            failure = probe.failure or "opencode_version_cleanup_unconfirmed"
            evidence["failure_code"] = failure
            return AdapterResult(
                b"",
                ("[opencode] " + failure).encode(),
                probe.transport
                if probe.transport != "completed"
                else "execution_failure",
                probe.writer,
                failure,
                probe.code,
                evidence,
            )
        evidence["observed_version"] = c.OPENCODE_VERSION
        stream = Stream()
        result = _process(
            plan.argv,
            cwd,
            native_env,
            deadline,
            poll,
            on_started,
            prompt=plan.generated_controls[c.PROMPT_NAME],
            stream=stream,
        )
        evidence.update(
            phase="invocation",
            session_id=stream.session,
            protocol_state="invalid"
            if stream.invalid
            else "settled"
            if result.transport == "completed"
            else "incomplete",
            event_count=stream.count,
            event_bytes=stream.bytes,
            error_count=stream.errors,
            stream_sha256=stream.hash.hexdigest() if stream.bytes else None,
            stderr_bytes=result.stderr_bytes,
            stderr_sha256=result.stderr_sha256,
            final={
                "message_id": stream.final_message,
                "reason": "stop",
                "size": len(result.raw),
                "text_sha256": c.digest(result.raw),
            }
            if result.transport == "completed"
            else None,
            failure_code=result.failure,
        )
        diagnostic = result.failure or ""
        # Native stderr and error payloads may contain credentials. Persist only
        # fixed failure codes and bounded counts/digests, never native previews.
        return AdapterResult(
            result.raw,
            ("[opencode] " + diagnostic).encode() if diagnostic else b"",
            result.transport,
            result.writer,
            diagnostic,
            result.code,
            evidence,
        )

    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts
    ) -> None:
        c.validate_completion(plan, completion)
