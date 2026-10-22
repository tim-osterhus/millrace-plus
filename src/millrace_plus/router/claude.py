"""Finite Claude print execution with native settings inheritance and one result."""

from __future__ import annotations

import hashlib
import json
import os
import re
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

from .configuration import (  # noqa: E402
    fields,
    mapping,
    number,
    relative,
    strict_json,
    text,
)

ADAPTER_ID = "claude_print"
ADAPTER_CONTRACT = "millrace-claude-print/v1"
CLAUDE_VERSION = "2.1.296"
PROFILE_FORMAT = "millrace-claude-print-profile/v1"
LAUNCH_FORMAT = "millrace-claude-print-launch/v1"
RESULT_FORMAT = "millrace-claude-print-result/v1"
PROMPT_NAME = "claude-prompt.txt"
SCHEMA_NAME = "claude-result-schema.json"
SETTINGS = {"model", "reasoning_effort"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
FLAGS = ("--print", "--output-format", "json", "--json-schema")
BYPASS = ("--dangerously-skip-permissions", "--permission-prompts", "none")
SHA = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
LAUNCH_FIELDS = {
    "format",
    "profile_path",
    "profile_sha256",
    "prompt_source",
    "prompt_source_sha256",
    "prompt_path",
    "prompt_sha256",
    "schema_sha256",
    "expected_version",
    "native_inheritance",
}
EVIDENCE_FIELDS = {
    "format",
    "invocation_id",
    "phase",
    "observed_version",
    "probe_pid",
    "session_id",
    "reported_models",
    "terminal_state",
    "native_stdout_bytes",
    "native_stdout_sha256",
    "native_stderr_bytes",
    "stderr_truncated",
    "semantic_bytes",
    "semantic_sha256",
    "failure_code",
}


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _model(value: object) -> str:
    result = text(value)
    try:
        result.encode("utf-8")
    except UnicodeError as exc:
        raise RouterError("invalid_claude_settings") from exc
    if (
        len(result.encode("utf-8")) > 256
        or result != result.strip()
        or result.startswith("-")
        or any(c in result for c in "\x00\r\n")
    ):
        raise RouterError("invalid_claude_settings")
    return result


def _settings(value: object) -> dict[str, object]:
    obj = fields(value, set(), SETTINGS)
    for key, item in obj.items():
        _model(item)
        if key == "reasoning_effort" and item not in EFFORTS:
            raise RouterError("invalid_claude_settings")
    return obj


def _control(files: Mapping[str, bytes], value: object) -> tuple[str, bytes]:
    name = relative(value)
    if name not in files or type(files[name]) is not bytes:
        raise RouterError("missing_control")
    raw = files[name]
    if len(raw) > 1048576:
        raise RouterError("invalid_claude_control")
    return name, raw


def _binding(
    value: object, files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    config = fields(value, {"profile"})
    name, raw = _control(files, config["profile"])
    parsed = fields(strict_json(raw), {"format", "claude_version"}, SETTINGS)
    if parsed["format"] != PROFILE_FORMAT:
        raise RouterError("invalid_claude_profile")
    if parsed["claude_version"] != CLAUDE_VERSION:
        raise RouterError("unsupported_claude_version")
    _settings({k: v for k, v in parsed.items() if k in SETTINGS})
    return name, raw, parsed


def _node(
    value: object, files: Mapping[str, bytes]
) -> tuple[str, bytes, dict[str, object]]:
    config = fields(value, {"prompt"}, SETTINGS)
    name, raw = _control(files, config["prompt"])
    try:
        if "\x00" in raw.decode("utf-8-sig"):
            raise RouterError("invalid_claude_prompt")
    except UnicodeError as exc:
        raise RouterError("invalid_claude_prompt") from exc
    return name, raw, _settings({k: v for k, v in config.items() if k in SETTINGS})


def _resolve(*layers: tuple[str, Mapping[str, object]]) -> dict[str, object]:
    effective: dict[str, object] = {}
    provenance: dict[str, object] = {}
    for source, layer in layers:
        for key in sorted(SETTINGS & layer.keys()):
            effective[key] = layer[key]
            provenance[key] = {"source": source, "policy": None}
    return {
        "requested": effective.copy(),
        "effective": effective,
        "provenance": provenance,
        "warnings": [],
    }


def _contract(adapter: str, contract: str) -> None:
    if (adapter, contract) != (ADAPTER_ID, ADAPTER_CONTRACT):
        raise RouterError("unsupported_adapter_contract")


def _argv(
    executable: str, schema: bytes, effective: Mapping[str, object]
) -> tuple[str, ...]:
    result: tuple[str, ...] = (executable,) + FLAGS + (schema.decode(),) + BYPASS
    for key, flag in (("model", "--model"), ("reasoning_effort", "--effort")):
        if key in effective:
            result += (flag, cast(str, effective[key]))
    return result


def _details(plan: LaunchPlan) -> dict[str, object]:
    _contract(plan.adapter_id, plan.adapter_contract)
    details = fields(plan.details, LAUNCH_FIELDS)
    if (
        details["format"] != LAUNCH_FORMAT
        or details["expected_version"] != CLAUDE_VERSION
        or details["native_inheritance"] is not True
        or plan.environment
        or set(plan.generated_controls) != {PROMPT_NAME, SCHEMA_NAME}
    ):
        raise RouterError("invalid_launch_plan")
    for name in ("profile_path", "prompt_source"):
        relative(details[name])
    text(details["prompt_path"])
    for key in (
        "profile_sha256",
        "prompt_source_sha256",
        "prompt_sha256",
        "schema_sha256",
    ):
        if not isinstance(details[key], str) or not SHA.fullmatch(
            cast(str, details[key])
        ):
            raise RouterError("invalid_launch_plan")
    for name, key in ((PROMPT_NAME, "prompt_sha256"), (SCHEMA_NAME, "schema_sha256")):
        if _digest(plan.generated_controls[name]) != details[key]:
            raise RouterError("invalid_launch_plan")
    effective = _settings(mapping(plan.settings)["effective"])
    if plan.argv != _argv(
        plan.argv[0], plan.generated_controls[SCHEMA_NAME], effective
    ):
        raise RouterError("invalid_launch_plan")
    return details


@dataclass
class _Process:
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stdout_sha256: str | None
    stderr_bytes: int
    pid: int | None
    exit_code: int | None
    transport: str
    writer: str
    failure: str | None


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
    data: bytes,
    stdout_limit: int,
    stderr_limit: int,
) -> _Process:
    if os.name != "posix" or deadline <= time.time():
        return _Process(
            b"",
            b"",
            0,
            None,
            0,
            None,
            None,
            "timeout" if deadline <= time.time() else "execution_failure",
            "stopped",
            "claude_timeout" if os.name == "posix" else "claude_unsupported_platform",
        )
    cancelled = poll()
    if cancelled or deadline <= time.time():
        return _Process(
            b"",
            b"",
            0,
            None,
            0,
            None,
            None,
            "cancelled" if cancelled else "timeout",
            "stopped",
            "claude_cancelled" if cancelled else "claude_timeout",
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
        return _Process(
            b"",
            b"",
            0,
            None,
            0,
            None,
            None,
            "execution_failure",
            "stopped",
            "claude_spawn_failed",
        )
    assert child.stdin and child.stdout and child.stderr
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    counts = {"stdout": 0, "stderr": 0}
    digest = hashlib.sha256()
    offset = 0
    reason: str | None = None
    failure: str | None = None
    cleanup: float | None = None
    killed = False
    end = time.monotonic() + max(0, deadline - time.time())
    finished = False

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
                fail("cancelled", "claude_cancelled")
            if now >= end:
                fail("timeout", "claude_timeout")
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
                    if offset == len(data) or reason is not None:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    try:
                        offset += os.write(key.fd, data[offset : offset + 8192])
                    except BlockingIOError:
                        pass
                    except BrokenPipeError:
                        selector.unregister(pipe)
                        pipe.close()
                        fail("execution_failure", "claude_stdin_failure")
                    continue
                try:
                    chunk = os.read(key.fd, 8192)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(pipe)
                    continue
                name = str(key.data)
                counts[name] += len(chunk)
                if name == "stdout":
                    digest.update(chunk)
                limit = stdout_limit if name == "stdout" else stderr_limit
                room = max(0, limit - len(output[name]))
                output[name].extend(chunk[:room])
                if len(chunk) > room:
                    fail("protocol_failure", "claude_output_limit")
            code = child.poll()
            if code is not None and offset < len(data):
                fail("execution_failure", "claude_stdin_failure")
            # Normal root exit never starts cleanup. Native foreground children
            # get the same invocation deadline to close their pipes and group.
            closed = not selector.get_map()
            if (code is not None and closed and not _group(child.pid)) or (
                cleanup is not None and time.monotonic() - cleanup >= 5
            ):
                finished = True
                return _Process(
                    bytes(output["stdout"]),
                    bytes(output["stderr"]),
                    counts["stdout"],
                    digest.hexdigest() if counts["stdout"] else None,
                    counts["stderr"],
                    child.pid,
                    code,
                    reason or ("completed" if code == 0 else "execution_failure"),
                    "may_live" if cleanup is not None else "stopped",
                    failure or (None if code == 0 else "claude_native_exit"),
                )
    finally:
        selector.close()
        for pipe in (child.stdin, child.stdout, child.stderr):
            pipe.close()
        if not finished:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


_SECRET_NAME = re.compile("KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", re.I)
_QUOTED_CREDENTIAL = re.compile(
    rb"""(?i)((?:(?:access|refresh|id)_token|token|api_key|apikey|client_secret|password|secret|key|sig|signature)(?:\\?["'])?\s*[:=]\s*)(\\?)(["'])"""
)
_CREDENTIALS = (
    (re.compile(rb"(?i)\bbearer\s+[^\s\"'<>,;]+"), b"Bearer [REDACTED]"),
    (re.compile(rb"\bsk-[A-Za-z0-9_-]+"), b"[REDACTED]"),
    (
        re.compile(rb"(?i)\b([a-z][a-z0-9+.-]{0,31}://)[^/\s@\"'<>]{1,256}@"),
        rb"\1[REDACTED]@",
    ),
    (
        re.compile(
            rb"(?i)((?:access|refresh|id)_token|token|api_key|apikey|client_secret|password|secret|key|sig|signature)[\"\x27]?\s*[:=]\s*[\"\x27]?[^\s\"\x27<>,;&]+"
        ),
        rb"\1=[REDACTED]",
    ),
)


def _quoted_credentials(raw: bytes) -> bytes:
    """Remove quoted values in plain or once JSON-escaped diagnostics."""
    parts = bytearray()
    cursor = 0
    while match := _QUOTED_CREDENTIAL.search(raw, cursor):
        # Plain delimiters follow an even number of backslashes. One JSON
        # encoding changes that to 1 modulo 4; an inner quote is 3 modulo 4.
        modulus, closing = (4, 1) if match[2] else (2, 0)
        quote = match[3][0]
        slashes = 0
        end = match.end()
        while end < len(raw):
            byte = raw[end]
            if byte == quote and slashes % modulus == closing:
                end += 1
                break
            slashes = slashes + 1 if byte == 92 else 0
            end += 1
        parts.extend(raw[cursor : match.start(2)])
        parts.extend(b"[REDACTED]")
        cursor = end
    parts.extend(raw[cursor:])
    return bytes(parts)


def _redact(raw: bytes, env: Mapping[str, str], cut: bool = False) -> bytes:
    values: set[bytes] = set()
    for name, value in env.items():
        if len(value.encode("utf-8", "surrogatepass")) < (
            4 if _SECRET_NAME.search(name) else 8
        ):
            continue
        values.update(
            (
                value.encode("utf-8", "surrogatepass"),
                os.fsencode(value),
                json.dumps(value, ensure_ascii=True)[1:-1].encode(),
                json.dumps(value, ensure_ascii=False)[1:-1].encode(
                    "utf-8", "surrogatepass"
                ),
            )
        )
    ordered = sorted(values, key=lambda item: -len(item))
    if cut:
        keep = len(raw)
        for secret in ordered:
            for size in range(min(len(secret) - 1, len(raw)), 0, -1):
                if raw.endswith(secret[:size]):
                    keep = min(keep, len(raw) - size)
                    break
        raw = raw[:keep]
        # The collection boundary can cut an unknown credential token too.
        parts = raw.rsplit(None, 1)
        raw = parts[0] if len(parts) == 2 else b""
    for secret in ordered:
        raw = raw.replace(secret, b"[REDACTED]")
    raw = _quoted_credentials(raw)
    for pattern, replacement in _CREDENTIALS:
        raw = pattern.sub(replacement, raw)
    return raw


def _preview(
    raw: bytes, env: Mapping[str, str], limit: int, cut: bool = False
) -> tuple[bytes, bool]:
    redacted = _redact(raw, env, cut).decode("utf-8", "backslashreplace")
    body = bytearray()
    for char in redacted:
        unit = (
            char if char.isprintable() else json.dumps(char, ensure_ascii=True)[1:-1]
        ).encode()
        if len(body) + len(unit) > limit:
            return bytes(body), True
        body.extend(unit)
    return bytes(body), cut


def _diagnostics(
    result: _Process, env: Mapping[str, str], value: object
) -> tuple[bytes, bool]:
    records = bytearray()
    stderr_cut = result.stderr_bytes > len(result.stderr)
    clipped = stderr_cut
    if isinstance(value, dict):
        error = value.get("errors")
        messages = (
            (b"[claude-result] ", value.get("result")),
            (
                b"[claude-error] ",
                error[-1] if isinstance(error, list) and error else None,
            ),
        )
        for label, message in messages:
            if isinstance(message, str) and message:
                body, cut = _preview(
                    message.encode("utf-8", "surrogatepass"), env, 1023 - len(label)
                )
                records.extend(label + body + b"\n")
                clipped |= cut
    if result.stderr:
        label = b"[claude-stderr native]\n"
        body, cut = _preview(
            result.stderr, env, 65536 - len(records) - len(label), stderr_cut
        )
        records.extend(label + body)
        clipped |= cut
    return bytes(records), clipped


def _evidence(invocation: str) -> dict[str, object]:
    return {
        "format": RESULT_FORMAT,
        "invocation_id": invocation,
        "phase": "preflight",
        "observed_version": None,
        "probe_pid": None,
        "session_id": None,
        "reported_models": [],
        "terminal_state": "not_started",
        "native_stdout_bytes": 0,
        "native_stdout_sha256": None,
        "native_stderr_bytes": 0,
        "stderr_truncated": False,
        "semantic_bytes": 0,
        "semantic_sha256": None,
        "failure_code": None,
    }


def _terminal(raw: bytes) -> tuple[dict[str, object] | None, bytes, str | None]:
    try:
        value = strict_json(raw)
    except RouterError:
        return None, b"", "claude_invalid_envelope"
    if not isinstance(value, dict):
        kind = (
            "array"
            if isinstance(value, list)
            else "null"
            if value is None
            else "boolean"
            if isinstance(value, bool)
            else "string"
            if isinstance(value, str)
            else "number"
        )
        return None, b"", "claude_envelope_kind_" + kind
    obj = cast(dict[str, object], value)
    if (
        obj.get("type") != "result"
        or obj.get("subtype") != "success"
        or obj.get("is_error") is not False
        or obj.get("terminal_reason") not in (None, "completed")
        or obj.get("errors")
        or obj.get("api_error_status") is not None
        or obj.get("stop_reason") in ("refusal", "tool_deferred_unavailable")
    ):
        return obj, b"", "claude_native_error"
    if not isinstance(obj.get("structured_output"), dict):
        return obj, b"", "claude_missing_structured_output"
    semantic = json.dumps(
        obj["structured_output"], sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    if len(semantic) > 65536:
        return obj, b"", "claude_output_limit"
    return obj, semantic, None


class ClaudePrintAdapter:
    """Direct Plus adapter. Only invoke reads live host configuration/environment."""

    adapter_id = ADAPTER_ID
    adapter_contract = ADAPTER_CONTRACT

    def validate_configuration(self, config: AdapterConfiguration) -> None:
        _contract(config.adapter_id, config.adapter_contract)
        _settings(config.runtime)
        _settings(config.workflow)
        for value in config.planes.values():
            _settings(value)
        profiles = {
            key: _binding(value, config.files)[2]
            for key, value in config.bindings.items()
        }
        for (plane_id, _), (binding, value) in config.nodes.items():
            if binding not in profiles or plane_id not in config.planes:
                raise RouterError("invalid_adapter_settings")
            _node(value, config.files)

    def plan(self, request: InvocationRequest) -> LaunchPlan:
        _contract(request.adapter_id, request.adapter_contract)
        runtime, workflow = _settings(request.runtime), _settings(request.workflow)
        plane = _settings(request.plane)
        profile_path, profile_raw, parsed = _binding(
            request.binding_config, request.files
        )
        prompt_source, prompt_raw, overrides = _node(request.node_config, request.files)
        settings = _resolve(
            ("runtime", runtime),
            ("profile", parsed),
            ("workflow", workflow),
            ("plane", plane),
            ("node", overrides),
        )
        prompt = (
            prompt_raw.decode("utf-8-sig")
            + "\n\nReturn the final result using the native StructuredOutput tool "
            "matching the supplied schema. outcome must be one of "
            + json.dumps(list(request.outcomes))
            + "; summary is a string and refs is an array of project-relative paths.\n"
        ).encode()
        schema = json.dumps(
            {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": list(request.outcomes)},
                    "summary": {"type": "string"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["outcome", "summary", "refs"],
                "additionalProperties": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(prompt) + len(schema) > 1114112:
            raise RouterError("invalid_launch_plan")
        details = {
            "format": LAUNCH_FORMAT,
            "profile_path": profile_path,
            "profile_sha256": _digest(profile_raw),
            "prompt_source": prompt_source,
            "prompt_source_sha256": _digest(prompt_raw),
            "prompt_path": str(request.invocation_root / PROMPT_NAME),
            "prompt_sha256": _digest(prompt),
            "schema_sha256": _digest(schema),
            "expected_version": CLAUDE_VERSION,
            "native_inheritance": True,
        }
        return LaunchPlan(
            request.invocation_id,
            ADAPTER_ID,
            ADAPTER_CONTRACT,
            _argv(request.executable, schema, mapping(settings["effective"])),
            {},
            {PROMPT_NAME: prompt, SCHEMA_NAME: schema},
            settings,
            details,
        )

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
        _details(plan)
        child_env = dict(os.environ)
        child_env.update(env)
        evidence = _evidence(plan.invocation_id)
        evidence["phase"] = "probe"
        probe = _process(
            (plan.argv[0], "--version"),
            cwd,
            child_env,
            min(deadline, time.time() + 5),
            poll,
            lambda pid: None,
            b"",
            4096,
            4096,
        )
        evidence["probe_pid"] = probe.pid
        if probe.transport != "completed" or probe.stdout not in (
            b"2.1.296 (Claude Code)",
            b"2.1.296 (Claude Code)\n",
            b"2.1.296 (Claude Code)\r\n",
        ):
            failure = (
                probe.failure
                if probe.transport in {"timeout", "cancelled"}
                or probe.failure
                in {"claude_spawn_failed", "claude_unsupported_platform"}
                else "claude_version_mismatch"
            )
            evidence.update(terminal_state="error", failure_code=failure)
            stderr, cut = _diagnostics(probe, child_env, None)
            evidence["stderr_truncated"] = cut
            return AdapterResult(
                b"",
                stderr,
                probe.transport
                if probe.transport in {"timeout", "cancelled"}
                else "execution_failure",
                probe.writer,
                cast(str, failure),
                probe.exit_code,
                evidence,
            )
        evidence.update(
            phase="main", observed_version=CLAUDE_VERSION, terminal_state="incomplete"
        )
        result = _process(
            plan.argv,
            cwd,
            child_env,
            deadline,
            poll,
            on_started,
            plan.generated_controls[PROMPT_NAME],
            1048576,
            65536,
        )
        evidence.update(
            native_stdout_bytes=result.stdout_bytes,
            native_stdout_sha256=result.stdout_sha256,
            native_stderr_bytes=result.stderr_bytes,
        )
        obj: dict[str, object] | None = None
        semantic = b""
        failure = result.failure
        transport = result.transport
        if transport == "completed":
            obj, semantic, failure = _terminal(result.stdout)
            if failure:
                transport = "protocol_failure"
        elif result.stdout and result.stdout_bytes <= 1048576:
            obj, _, _ = _terminal(result.stdout)
        if obj is not None:
            session = obj.get("session_id")
            if isinstance(session, str) and SAFE_ID.fullmatch(session):
                evidence["session_id"] = session
            models = obj.get("modelUsage")
            safe: list[str] = []
            if isinstance(models, dict):
                for model in models:
                    try:
                        name = _model(model)
                        if (
                            _redact(
                                name.encode(),
                                {
                                    k: v
                                    for k, v in child_env.items()
                                    if _SECRET_NAME.search(k)
                                },
                            )
                            == name.encode()
                        ):
                            safe.append(name)
                    except RouterError:
                        pass
            evidence["reported_models"] = sorted(set(safe))[:16]
        if transport == "completed":
            evidence.update(
                terminal_state="success",
                semantic_bytes=len(semantic),
                semantic_sha256=_digest(semantic),
            )
            stderr, cut = b"", False
        else:
            semantic = b""
            evidence["terminal_state"] = (
                "invalid" if transport == "protocol_failure" else "error"
            )
            stderr, cut = _diagnostics(result, child_env, obj)
        evidence.update(stderr_truncated=cut, failure_code=failure)
        return AdapterResult(
            semantic,
            stderr,
            transport,
            result.writer,
            failure or "",
            result.exit_code,
            evidence,
        )

    def validate_completion(
        self, plan: LaunchPlan, completion: CompletionFacts
    ) -> None:
        try:
            _details(plan)
            e = fields(completion.evidence, EVIDENCE_FIELDS)
            if (
                completion.invocation_id != plan.invocation_id
                or e["invocation_id"] != plan.invocation_id
                or e["format"] != RESULT_FORMAT
                or e["phase"] not in {"preflight", "probe", "main"}
                or e["terminal_state"]
                not in {"not_started", "incomplete", "invalid", "error", "success"}
                or e["observed_version"] not in {None, CLAUDE_VERSION}
                or type(e["stderr_truncated"]) is not bool
            ):
                raise RouterError("completion_integrity")
            for key in ("native_stdout_bytes", "native_stderr_bytes", "semantic_bytes"):
                number(e[key], integer=True, zero=True)
            for count, key in (
                ("native_stdout_bytes", "native_stdout_sha256"),
                ("semantic_bytes", "semantic_sha256"),
            ):
                digest = e[key]
                if (e[count] == 0) != (digest is None) or (
                    digest is not None
                    and (not isinstance(digest, str) or not SHA.fullmatch(digest))
                ):
                    raise RouterError("completion_integrity")
            if e["session_id"] is not None and (
                not isinstance(e["session_id"], str)
                or not SAFE_ID.fullmatch(e["session_id"])
            ):
                raise RouterError("completion_integrity")
            models = e["reported_models"]
            if (
                not isinstance(models, list)
                or len(models) > 16
                or models != sorted(set(_model(v) for v in models))
            ):
                raise RouterError("completion_integrity")
            if e["probe_pid"] is not None and (
                type(e["probe_pid"]) is not int or e["probe_pid"] <= 0
            ):
                raise RouterError("completion_integrity")
            if e["failure_code"] is not None and (
                not isinstance(e["failure_code"], str)
                or not re.fullmatch(r"claude_[a-z_]+", e["failure_code"])
            ):
                raise RouterError("completion_integrity")
            if (e["failure_code"] or "") != completion.diagnostic:
                raise RouterError("completion_integrity")
            if completion.pid is not None and (
                type(completion.pid) is not int or completion.pid <= 0
            ):
                raise RouterError("completion_integrity")
            if e["phase"] == "main":
                if e["observed_version"] != CLAUDE_VERSION or e["probe_pid"] is None:
                    raise RouterError("completion_integrity")
                if completion.pid is None and e["failure_code"] not in {
                    "claude_spawn_failed",
                    "claude_timeout",
                    "claude_cancelled",
                    "claude_unsupported_platform",
                }:
                    raise RouterError("completion_integrity")
            elif (
                completion.pid is not None
                or e["observed_version"] is not None
                or e["native_stdout_bytes"]
                or e["native_stderr_bytes"]
                or e["session_id"] is not None
                or models
            ):
                raise RouterError("completion_integrity")
            if e["phase"] == "preflight" and (
                e["probe_pid"] is not None or e["terminal_state"] != "not_started"
            ):
                raise RouterError("completion_integrity")
            if completion.transport == "completed":
                if (
                    e["phase"] != "main"
                    or completion.pid is None
                    or completion.pid <= 0
                    or completion.exit_code != 0
                    or completion.writer_state != "stopped"
                    or e["terminal_state"] != "success"
                    or e["failure_code"] is not None
                    or not 0 < cast(int, e["native_stdout_bytes"]) <= 1048576
                    or cast(int, e["native_stderr_bytes"]) > 65536
                    or not 0 < len(completion.stdout) <= 65536
                ):
                    raise RouterError("completion_integrity")
            elif (
                completion.stdout
                or e["terminal_state"] == "success"
                or e["failure_code"] is None
            ):
                raise RouterError("completion_integrity")
            if e["semantic_bytes"] != len(completion.stdout) or e[
                "semantic_sha256"
            ] != (_digest(completion.stdout) if completion.stdout else None):
                raise RouterError("completion_integrity")
        except (RouterError, KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise RouterError("completion_integrity") from exc
