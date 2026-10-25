"""Finite public Codex exec wire, bounded stdin/output and owned POSIX lifecycle."""

from __future__ import annotations

import os
import re
import stat
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

from . import codex_configuration as c  # noqa: E402
from .configuration import fields, mapping, text  # noqa: E402
from .stream_records import FramingError, JsonLineRecords  # noqa: E402

ENVIRONMENT = {
    "PATH",
    "CODEX_HOME",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TZ",
    "MILLRACE_SEAM_INPUT",
}
USAGE = {
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
}
L_ERR = b"[codex-jsonl error.message] "
L_FAIL = b"[codex-jsonl turn.failed.error.message] "
H_NATIVE = b"[codex-stderr native]\n"
ESCAPED = frozenset(
    [*range(0x20), *range(0x7F, 0xA0), 0x061C, 0x200E, 0x200F, 0x2028, 0x2029]
    + [*range(0x202A, 0x202F), *range(0x2066, 0x206A)]
)
# Narrow credential shapes, not DLP. Ambiguous email/scheme runs stay bounded.
PATTERNS = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in (
        (rb"(?i)\bbearer\s+[^\s\"'<>,;]+", rb"Bearer [REDACTED]"),
        (rb"\bsk-[A-Za-z0-9_-]+", rb"[REDACTED]"),
        (rb"(?i)\b([a-z][a-z0-9+.-]{0,31}://)[^/\s@\"'<>]{1,256}@", rb"\1[REDACTED]@"),
        (
            rb"(?i)\b((?:access|refresh|id)_token|token|api_key|apikey|client_secret"
            rb"|password|secret|key|sig|signature)=[^&;,\s\"'#<>]+",
            rb"\1=[REDACTED]",
        ),
        (
            rb"(?i)\"((?:access|refresh|id)_token|token|api_key|client_secret|password"
            rb"|secret)\"\s*:\s*\"[^\"]*",
            rb'"\1": "[REDACTED]',
        ),
        (
            rb"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
            rb"\.[A-Za-z]{2,63}",
            rb"[REDACTED]",
        ),
    )
)


def launch_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Keep the finite Codex environment local to this adapter, leaving peers alone."""
    return {key: value for key, value in env.items() if key in ENVIRONMENT}


class Stream:
    """Bounded JSONL framing; items remain opaque and never supply result bytes."""

    def __init__(self) -> None:
        self.records = JsonLineRecords(
            total_limit=c.EVENT_LIMIT, record_limit=c.LINE_LIMIT
        )
        self.hash = self.records.hash
        self.count = 0
        self.errors = 0
        self.thread: str | None = None
        self.state = "header"
        # Last valid diagnostic messages; emitted only for non-completed transport.
        self.error_message: str | None = None
        self.failed_message: str | None = None

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
            code = (
                "codex_event_limit"
                if exc.code == "event_limit" else "codex_invalid_events"
            )
            raise RouterError(code) from exc
        except (RouterError, UnicodeError, RecursionError) as exc:
            raise RouterError("codex_invalid_events") from exc

    def event(self, event: dict[str, object]) -> None:
        self.count += 1
        kind = event.get("type")
        if not isinstance(kind, str):
            raise RouterError("codex_invalid_event_type")
        if self.state in {"settled", "failed"}:
            raise RouterError("codex_event_order")
        if kind == "thread.started":
            obj = fields(event, {"type", "thread_id"})
            value = text(obj["thread_id"])
            if self.state != "header" or len(value.encode()) > 4096:
                raise RouterError("codex_event_order")
            self.thread, self.state = value, "ready"
            return
        if self.state == "header":
            raise RouterError("codex_event_order")
        if kind == "turn.started":
            fields(event, {"type"})
            if self.state != "ready":
                raise RouterError("codex_event_order")
            self.state = "active"
        elif kind == "turn.completed":
            obj = fields(event, {"type", "usage"})
            usage = fields(obj["usage"], USAGE)
            if self.state != "active" or any(
                type(n) is not int or not -(2**63) <= n < 2**63 for n in usage.values()
            ):
                raise RouterError("codex_invalid_usage")
            self.state = "settled"
        elif kind == "turn.failed":
            obj = fields(event, {"type", "error"})
            error = fields(obj["error"], {"message"})
            if self.state != "active" or not isinstance(error["message"], str):
                raise RouterError("codex_invalid_error")
            self.state = "failed"
            self.failed_message = error["message"]
        elif kind == "error":
            obj = fields(event, {"type", "message"})
            if not isinstance(obj["message"], str):
                raise RouterError("codex_invalid_error")
            self.errors += 1
            self.error_message = obj["message"]
        elif kind in {"item.started", "item.updated", "item.completed"}:
            obj = fields(event, {"type", "item"})
            item = mapping(obj["item"])
            for key in ("id", "type"):
                if len(text(item.get(key)).encode()) > 4096:
                    raise RouterError("codex_invalid_item")
        else:
            raise RouterError("codex_unknown_event")

    def finish(self) -> tuple[str, str | None]:
        if self.invalid:
            return "protocol_failure", "codex_invalid_events"
        try:
            self.records.finish(self.event)
        except (RouterError, FramingError, UnicodeError, RecursionError):
            return "protocol_failure", "codex_invalid_events"
        if self.state == "settled":
            return "completed", None
        if self.state == "failed" or self.errors:
            return "execution_failure", "codex_turn_failed"
        return "protocol_failure", "codex_incomplete_events"


def _read_file(
    path: str, limit: int, poll: Callable[[], bool], deadline: float
) -> bytes:
    """Read one regular-file snapshot, never following a link or waiting on a FIFO."""
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise RouterError("codex_invalid_file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        start = os.fstat(descriptor)
        if (start.st_dev, start.st_ino, start.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise RouterError("codex_changed_file")
        raw = bytearray()
        while len(raw) <= limit:
            if poll():
                raise RouterError("codex_cancelled")
            if time.time() >= deadline:
                raise RouterError("codex_timeout")
            chunk = os.read(descriptor, min(8192, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        end = os.fstat(descriptor)
        after = os.lstat(path)
        expected = (start.st_dev, start.st_ino, start.st_size, start.st_mtime_ns)
        if (
            len(raw) > limit
            or expected != (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns)
            or expected
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or not stat.S_ISREG(after.st_mode)
            or len(raw) != end.st_size
        ):
            raise RouterError("codex_changed_file")
        return bytes(raw)
    finally:
        os.close(descriptor)


def _preflight(
    details: Mapping[str, object], poll: Callable[[], bool], deadline: float
) -> bytes:
    if poll():
        raise RouterError("codex_cancelled")
    if time.time() >= deadline:
        raise RouterError("codex_timeout")
    native = Path(str(details["codex_home"]))
    if (
        str(native.resolve()) != str(native)
        or not native.is_dir()
        or not os.access(native, os.R_OK)
    ):
        raise RouterError("invalid_host_path")
    auth = native / "auth.json"
    try:
        auth_mode = auth.lstat().st_mode
    except FileNotFoundError as exc:
        raise RouterError("invalid_codex_credentials") from exc
    if not stat.S_ISREG(auth_mode) or not os.access(auth, os.R_OK):
        raise RouterError("invalid_codex_credentials")
    if os.path.lexists(str(details["final_path"])):
        raise RouterError("codex_final_exists")
    prompt = b""
    for name in ("prompt", "schema"):
        raw = _read_file(str(details[name + "_path"]), 1114112, poll, deadline)
        if c.digest(raw) != details[name + "_sha256"]:
            raise RouterError("codex_control_changed")
        if name == "prompt":
            prompt = raw
    return prompt


@dataclass
class ProcessResult:
    raw: bytes
    stderr: bytes
    transport: str
    writer: str
    failure: str | None
    code: int | None
    pid: int | None
    truncated: bool
    event_bytes: int = 0
    event_sha256: str | None = None


def _values(env: Mapping[str, str]) -> list[bytes]:
    """Passed values as native fsencode and JSON-text surrogatepass bytes."""
    values: set[bytes] = set()
    for value in env.values():
        values.add(value.encode("utf-8", "surrogatepass"))
        try:
            values.add(os.fsencode(value))
        except UnicodeError:
            pass
    return sorted(values - {b""}, key=lambda value: (-len(value), value))


def _redact(raw: bytes, env: Mapping[str, str], cut: bool = False) -> bytes:
    """Redact complete values and credential shapes; callers clip afterwards."""
    values = _values(env)
    if cut:
        # A collection cut can split a value or token: drop that partial tail.
        keep = len(raw)
        for value in values:
            for size in range(min(len(value) - 1, len(raw)), 0, -1):
                if raw.endswith(value[:size]):
                    keep = min(keep, len(raw) - size)
                    break
        end = keep
        while end > max(0, keep - 4096) and raw[end - 1] not in b" \t\n\r\x0b\x0c":
            end -= 1
        raw = raw[:end]
    for value in values:
        raw = raw.replace(value, b"[REDACTED]")
    for pattern, replacement in PATTERNS:
        raw = pattern.sub(replacement, raw)
    return raw


def _record(label: bytes, message: str, env: Mapping[str, str]) -> tuple[bytes, bool]:
    """One labelled line: redact the full message, escape controls, clip whole units."""
    text = _redact(message.encode("utf-8", "surrogatepass"), env).decode(
        "utf-8", "backslashreplace"
    )
    room = 1024 - len(label) - 1
    body = bytearray()
    for char in text:
        point = ord(char)
        unit = char.encode()
        if point in ESCAPED:
            unit = (f"\\x{point:02x}" if point < 0x100 else f"\\u{point:04x}").encode()
        if len(body) + len(unit) > room:
            return label + bytes(body) + b"\n", True
        body += unit
    return label + bytes(body) + b"\n", False


def _stderr(
    native: bytes,
    cut: bool,
    env: Mapping[str, str],
    transport: str,
    stream: Stream | None = None,
) -> tuple[bytes, bool]:
    """Bounded stderr: failure records first, then one native header and text."""
    text = _redact(native, env, cut)
    if transport == "completed":
        return text[:65536], cut or len(text) > 65536
    prefix = b""
    clipped = False
    if stream is not None:
        for label, message in (
            (L_ERR, stream.error_message),
            (L_FAIL, stream.failed_message),
        ):
            if message is not None:
                record, partial = _record(label, message, env)
                prefix += record
                clipped = clipped or partial
    if text:
        prefix += H_NATIVE
    room = 65536 - len(prefix)
    return prefix + text[:room], cut or clipped or len(text) > room


def _process(
    argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    deadline: float,
    poll: Callable[[], bool],
    started: Callable[[int], None],
    *,
    prompt: bytes | None = None,
    stream: Stream | None = None,
    final_path: str | None = None,
) -> ProcessResult:
    """Compose byte ownership with Codex's final snapshot and two-axis result."""
    from millrace.router.process import (
        OwnedProcessError,
        ProcessRequest,
        run_owned_process,
    )

    probe = stream is None
    transport: str | None = None
    failure: str | None = None
    raw = b""
    native_error: BaseException | None = None

    def fail(kind: str, code: str) -> None:
        nonlocal transport, failure
        if transport is None:
            transport, failure = kind, code

    def stopped(cause: str) -> None:
        kind, code = {
            "unsupported_platform": ("execution_failure", "unsupported_platform"),
            "start_failed": ("execution_failure", "codex_launch_failed"),
            "cancelled": ("cancelled", "codex_cancelled"),
            "timeout": ("timeout", "codex_timeout"),
            "stdin_failed": ("execution_failure", "codex_stdin_closed"),
            "stdout_overflow": ("execution_failure", "codex_version_protocol"),
        }.get(cause, ("execution_failure", "codex_io_failed"))
        if probe and cause == "timeout" and process_deadline < deadline:
            kind, code = "execution_failure", "codex_version_timeout"
        fail(kind, code)

    def feed(chunk: bytes) -> None:
        nonlocal native_error
        assert stream is not None
        try:
            stream.feed(chunk)
        except (
            RouterError, UnicodeError, RecursionError, TypeError, ValueError
        ) as exc:
            native_error = exc
            fail("protocol_failure", "codex_invalid_events")
            raise
        if stream.state == "failed":
            fail("execution_failure", "codex_turn_failed")

    def drained(code: int) -> None:
        nonlocal raw, transport
        if transport is not None:
            return
        if probe:
            if code == 0 and bytes(version) in {
                b"codex-cli 0.163.0", b"codex-cli 0.163.0\n", b"codex-cli 0.163.0\r\n",
            }:
                transport, raw = "completed", bytes(version)
            else:
                fail("execution_failure", "codex_version_mismatch")
        elif code != 0:
            fail("execution_failure", "codex_nonzero_exit")
        else:
            assert stream is not None and final_path is not None
            kind, error = stream.finish()
            if kind != "completed":
                fail(kind, error or "codex_incomplete_events")
                return
            try:
                candidate = _read_file(final_path, c.FINAL_LIMIT, poll, deadline)
                if not candidate:
                    raise RouterError("codex_empty_final")
                candidate.decode("utf-8")
                # A finite snapshot callback cannot commit completion over budget.
                if poll():
                    raise RouterError("codex_cancelled")
                if time.time() >= deadline:
                    raise RouterError("codex_timeout")
                raw, transport = candidate, "completed"
            except (OSError, RouterError, UnicodeError) as exc:
                error = (
                    exc.code if isinstance(exc, RouterError) else "codex_invalid_final"
                )
                fail({"codex_cancelled": "cancelled", "codex_timeout": "timeout"}.get(
                    error, "protocol_failure"
                ), error)

    version = bytearray()

    def version_bytes(chunk: bytes) -> None:
        version.extend(chunk[:max(0, 4096 - len(version))])

    process_deadline = min(deadline, time.time() + 5) if probe else deadline
    request = ProcessRequest(
        argv, cwd, env, None if probe else prompt, process_deadline,
        stdin_limit=1114112,
        stdout_capture_limit=4096 if probe else 1,
        stderr_capture_limit=4096 if probe else 65536,
        stdout_fail_limit=4096 if probe else None,
        stderr_fail_limit=None,
        cleanup_on_root_exit=True,
    )
    try:
        result = run_owned_process(
            request, poll=poll, on_started=started,
            on_stdout=version_bytes if probe else feed, on_drained=drained,
            on_stop=stopped,
        )
    except OwnedProcessError as exc:
        if native_error is None or exc.__cause__ is not native_error:
            raise
        result = exc.result
    if result.stop_cause is not None:
        stopped(result.stop_cause)
    if transport is None:
        fail("execution_failure", "codex_close_timeout")
    writer = (
        "may_live" if result.cleanup_forced else
        "stopped" if result.cleanup_confirmed else "unknown"
    )
    return ProcessResult(
        raw if transport == "completed" else b"", result.stderr,
        transport or "execution_failure", writer, failure, result.exit_code,
        result.pid, result.stderr_truncated, result.stdout_bytes,
        result.stdout_sha256 if result.stdout_bytes else None,
    )


class CodexExecAdapter:
    """One-shot pinned Codex exec; Core owns semantic results and durable routing."""

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
        details = c.launch_details(plan)
        evidence = c.evidence(plan.invocation_id)
        try:
            require_owned_process()
        except CoreContractError as exc:
            failure = "codex_" + exc.code
            evidence["failure_code"] = failure
            return AdapterResult(
                b"", ("[codex] " + failure).encode(), "execution_failure",
                "stopped", failure, None, evidence,
            )
        native_env = launch_environment(env)
        try:
            prompt = _preflight(details, poll, deadline)
        except (OSError, RouterError, RuntimeError) as exc:
            error = exc.code if isinstance(exc, RouterError) else "invalid_host_path"
            evidence["failure_code"] = error
            return AdapterResult(
                b"",
                b"",
                {"codex_cancelled": "cancelled", "codex_timeout": "timeout"}.get(
                    error, "execution_failure"
                ),
                "stopped",
                error,
                None,
                evidence,
            )
        probe = _process(
            (plan.argv[0], "--version"), cwd, native_env, deadline, poll, lambda _: None
        )
        evidence["probe_pid"] = probe.pid
        evidence["stderr_truncated"] = probe.truncated
        if probe.transport != "completed" or probe.writer != "stopped":
            failure = probe.failure or "codex_version_cleanup_unconfirmed"
            transport = (
                probe.transport
                if probe.transport != "completed"
                else "execution_failure"
            )
            stderr, evidence["stderr_truncated"] = _stderr(
                probe.stderr, probe.truncated, native_env, transport
            )
            evidence["failure_code"] = failure
            return AdapterResult(
                b"",
                stderr,
                transport,
                probe.writer,
                failure,
                probe.code,
                evidence,
            )
        evidence["observed_version"] = c.CODEX_VERSION
        if os.path.lexists(str(details["final_path"])):
            evidence["failure_code"] = "codex_final_exists"
            stderr, evidence["stderr_truncated"] = _stderr(
                probe.stderr, probe.truncated, native_env, "execution_failure"
            )
            return AdapterResult(
                b"",
                stderr,
                "execution_failure",
                "stopped",
                "codex_final_exists",
                probe.code,
                evidence,
            )
        stream = Stream()
        result = _process(
            plan.argv,
            cwd,
            native_env,
            deadline,
            poll,
            on_started,
            prompt=prompt,
            stream=stream,
            final_path=cast(str, details["final_path"]),
        )
        stderr, truncated = _stderr(
            result.stderr, result.truncated, native_env, result.transport, stream
        )
        evidence.update(
            phase="invocation",
            protocol_state="invalid"
            if stream.invalid
            else "settled"
            if stream.state == "settled"
            else "incomplete",
            thread_id=stream.thread,
            event_count=stream.count,
            event_bytes=result.event_bytes,
            error_count=stream.errors,
            stream_sha256=result.event_sha256,
            final={"size": len(result.raw), "text_sha256": c.digest(result.raw)}
            if result.transport == "completed"
            else None,
            stderr_truncated=truncated,
            failure_code=result.failure,
        )
        return AdapterResult(
            result.raw,
            stderr,
            result.transport,
            result.writer,
            result.failure
            or ("cleanup_unconfirmed" if result.writer != "stopped" else ""),
            result.code,
            evidence,
        )

    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts
    ) -> None:
        c.validate_completion(plan, completion)
