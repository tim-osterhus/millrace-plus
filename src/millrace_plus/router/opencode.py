"""Finite pinned OpenCode JSONL projection and synchronous POSIX ownership."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from . import CoreContractError, require_core, require_owned_process

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
from .configuration import fields, mapping, number, text  # noqa: E402
from .stream_records import FramingError, JsonLineRecords  # noqa: E402


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
        self.records = JsonLineRecords(
            total_limit=c.EVENT_LIMIT, record_limit=c.LINE_LIMIT
        )
        self.hash = self.records.hash
        self.count = 0
        self.errors = 0
        self.session: str | None = None
        self.final_message: str | None = None
        self.parts: dict[str, str] = {}
        self.texts: dict[str, list[bytes]] = {}

    @property
    def bytes(self) -> int:
        return self.records.bytes

    @property
    def invalid(self) -> bool:
        return self.records.invalid

    def feed(self, raw: bytes) -> None:
        try:
            self.records.feed(raw, self.event)
        except FramingError as exc:
            if exc.code in {"event_limit", "line_limit"}:
                raise RouterError("opencode_" + exc.code) from exc
            if isinstance(exc.__cause__, RouterError):
                raise exc.__cause__ from exc
            raise RouterError("opencode_invalid_events") from exc

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
        try:
            self.records.finish(self.event)
        except FramingError as exc:
            if isinstance(exc.__cause__, RouterError):
                raise exc.__cause__ from exc
            raise RouterError("opencode_invalid_events") from exc
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
    event_bytes: int = 0
    event_sha256: str | None = None



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
    """Keep OpenCode's native failure and conservative cleanup mapping local."""
    from millrace.router.process import (
        OwnedProcessError,
        ProcessRequest,
        run_owned_process,
    )

    probe = stream is None
    native_error: BaseException | None = None
    failure: str | None = None
    transport: str | None = None

    def fail(kind: str, code: str) -> None:
        nonlocal transport, failure
        if transport is None:
            transport, failure = kind, code

    def stopped(cause: str) -> None:
        kind, code = {
            "unsupported_platform": (
                "execution_failure", "opencode_unsupported_platform"
            ),
            "start_failed": ("execution_failure", "opencode_spawn_failed"),
            "cancelled": ("cancelled", "opencode_cancelled"),
            "timeout": ("timeout", "opencode_timeout"),
            "stdin_failed": ("execution_failure", "opencode_stdin_failure"),
            "stdout_overflow": ("execution_failure", "opencode_version_protocol"),
            "stderr_overflow": ("protocol_failure", "opencode_stderr_limit"),
        }.get(cause, ("execution_failure", "opencode_io_failed"))
        if probe and cause == "timeout" and process_deadline < deadline:
            kind, code = "execution_failure", "opencode_version_timeout"
        fail(kind, code)

    def feed(chunk: bytes) -> None:
        nonlocal native_error
        assert stream is not None
        try:
            stream.feed(chunk)
        except (
            RouterError, UnicodeError, ValueError, TypeError, RecursionError
        ) as exc:
            native_error = exc
            fail(
                "protocol_failure",
                exc.code if isinstance(exc, RouterError) else "opencode_invalid_events",
            )
            raise

    process_deadline = min(deadline, time.time() + 5) if probe else deadline
    request = ProcessRequest(
        argv, cwd, env, prompt, process_deadline,
        stdin_limit=max(1, len(prompt)),
        stdout_capture_limit=4096 if probe else 1,
        stderr_capture_limit=1,
        stdout_fail_limit=4096 if probe else None,
        stderr_fail_limit=c.STDERR_LIMIT,
    )
    try:
        result = run_owned_process(
            request, poll=poll, on_started=started,
            on_stdout=None if probe else feed, on_stop=stopped,
        )
    except OwnedProcessError as exc:
        if native_error is None or exc.__cause__ is not native_error:
            raise
        result = exc.result
    if result.stop_cause is not None:
        stopped(result.stop_cause)
    raw = b""
    if transport is None:
        if result.exit_code != 0:
            transport, failure = "execution_failure", "opencode_nonzero_exit"
        elif probe:
            if result.stdout in {b"1.19.3", b"1.19.3\n", b"1.19.3\r\n"}:
                raw = result.stdout
            else:
                transport, failure = "execution_failure", "opencode_version_mismatch"
        else:
            assert stream is not None
            try:
                raw = stream.finish()
            except (
                RouterError, UnicodeError, ValueError, TypeError, RecursionError
            ) as exc:
                transport = "protocol_failure"
                failure = (
                    exc.code
                    if isinstance(exc, RouterError) else "opencode_invalid_events"
                )
    writer = (
        "may_live" if result.pid is not None and result.stop_cause is not None
        else "stopped" if result.cleanup_confirmed else "may_live"
    )
    return _Process(
        raw if transport is None else b"", transport or "completed", writer,
        failure, result.exit_code, result.pid, result.stderr_bytes,
        result.stderr_sha256 if result.stderr_bytes else None,
        result.stdout_bytes, result.stdout_sha256 if result.stdout_bytes else None,
    )


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
        try:
            require_owned_process()
        except CoreContractError as exc:
            failure = "opencode_" + exc.code
            evidence["failure_code"] = failure
            return AdapterResult(
                b"", ("[opencode] " + failure).encode(), "execution_failure",
                "stopped", failure, None, evidence,
            )
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
            event_bytes=result.event_bytes,
            error_count=stream.errors,
            stream_sha256=result.event_sha256,
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
