"""Test-only fake ``forge`` package. It is NOT the Forge SDK and proves nothing of it.

It reproduces only the pinned 0.10.1 runner facts the worker contract relies on
(source citations S09-S14, S17, S19, S21, S25, S41-S43): every call of a batch
runs, the executed batch's TOOL_CALL message is emitted before its first call,
retry messages arrive earlier, a later terminal call overwrites an earlier one,
ToolResolutionError does not mark the batch, an unknown tool or non-dict
arguments reject the whole batch before any tool runs, and budgets are
exhausted at max + 1. Responses come from a JSON scenario named by the
``FORGE_FAKE_SCENARIO`` environment variable. Nothing here opens a network
connection.
"""

import asyncio
import json
import os
import stat
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from . import errors
from .errors import (
    BackendError,
    ContextBudgetExceeded,
    ForgeError,
    MaxIterationsError,
    MissingCredentialError,
    MultipleCredentialsError,
    PrerequisiteError,
    StepEnforcementError,
    ToolCallError,
    ToolExecutionError,
    ToolResolutionError,
    WorkflowCancelledError,
)

__all__ = [
    "BackendError",
    "ContextBudgetExceeded",
    "ContextManager",
    "ForgeError",
    "MaxIterationsError",
    "Message",
    "MessageMeta",
    "MessageRole",
    "MessageType",
    "MissingCredentialError",
    "MultipleCredentialsError",
    "NoCompact",
    "OpenAICompatClient",
    "PrerequisiteError",
    "StepEnforcementError",
    "ToolCallError",
    "ToolCallInfo",
    "ToolDef",
    "ToolExecutionError",
    "ToolResolutionError",
    "ToolSpec",
    "Workflow",
    "WorkflowCancelledError",
    "WorkflowRunner",
    "errors",
]

_PATH = os.environ.get("FORGE_FAKE_SCENARIO")
SCENARIO = json.loads(Path(_PATH).read_text()) if _PATH else {}


def event(kind, **data):
    target = SCENARIO.get("events")
    if target:
        with open(target, "a", encoding="utf-8") as log:
            log.write(json.dumps({"kind": kind, **data}, default=str) + "\n")


_ROOT = Path(__file__).resolve().parent.parent
event(
    "imported",
    file=str(Path(__file__).resolve()),
    errors_file=str(Path(errors.__file__).resolve()),
    root_on_search_path=any(
        Path(entry or ".").resolve() == _ROOT for entry in sys.path
    ),
)
_SWAP = dict(SCENARIO.get("swap") or {})
_MODES = {
    name: getattr(stat, kind)
    for name, kind in (SCENARIO.get("fake_modes") or {}).items()
}
if _SWAP or _MODES or SCENARIO.get("record_opens"):
    # Test-only hooks on the worker's dir_fd-relative stat/open calls:
    # - swap: replace an entry right after the type check (W-TL-04 identity);
    # - fake_modes: report a regular file as a device or FIFO, so pre-open
    #   refusal is proved without creating or opening a real device;
    # - record_opens: log every dir_fd-relative open as an "opened" event.
    _stat, _open = os.stat, os.open

    def _hooked_stat(path, *args, **kwargs):
        result = _stat(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            if _SWAP and path == _SWAP["name"]:
                os.replace(_SWAP.pop("replacement"), _SWAP.pop("target"))
                _SWAP.clear()
            if path in _MODES:
                result = os.stat_result((_MODES[path] | 0o644, *tuple(result)[1:]))
        return result

    def _hooked_open(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            event("opened", name=path)
        return _open(path, flags, *args, **kwargs)

    os.stat, os.open = _hooked_stat, _hooked_open
if SCENARIO.get("import_print"):
    print(SCENARIO["import_print"])  # A library print; must land on stderr.
if SCENARIO.get("import_sleep"):
    time.sleep(SCENARIO["import_sleep"])


# Upstream 0.10.1 declares both enums with the ``(str, Enum)`` base. The fake
# keeps that base so str()/format() match the pinned SDK. UP042 is
# suppressed on these two test-fake classes only.
class MessageType(str, Enum):  # noqa: UP042
    SYSTEM_PROMPT = "system_prompt"
    USER_INPUT = "user_input"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TEXT_RESPONSE = "text_response"
    RETRY_NUDGE = "retry_nudge"


class MessageRole(str, Enum):  # noqa: UP042
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class MessageMeta:
    type: MessageType
    step_index: int = 0


@dataclass
class ToolCallInfo:
    name: str
    args: object
    call_id: str


@dataclass
class Message:
    role: MessageRole
    content: str
    metadata: MessageMeta
    tool_name: object = None
    tool_call_id: object = None
    tool_calls: object = None


class ToolSpec:
    def __init__(self, name, description, schema):
        self.name, self.description, self.schema = name, description, schema

    @classmethod
    def from_json_schema(cls, name, description, schema):
        return cls(name, description, schema)


@dataclass
class ToolDef:
    spec: ToolSpec
    callable: object
    prerequisites: list = field(default_factory=list)

    @property
    def name(self):
        return self.spec.name


@dataclass
class Workflow:
    name: str
    description: str
    tools: dict
    required_steps: list
    terminal_tool: object
    system_prompt_template: str

    def __post_init__(self):
        self.terminal_tools = frozenset(
            [self.terminal_tool]
            if isinstance(self.terminal_tool, str)
            else self.terminal_tool
        )
        for key, item in self.tools.items():
            if key != item.name:
                raise ValueError("tool key mismatch")

    def build_system_prompt(self, **kwargs):
        return self.system_prompt_template.format(**kwargs)


class NoCompact:
    pass


class ContextManager:
    def __init__(self, strategy, budget_tokens, **kwargs):
        event(
            "context",
            strategy=type(strategy).__name__,
            budget_tokens=budget_tokens,
            extra=sorted(kwargs),
        )


@dataclass
class _Text:
    content: str


def _raise(kind):
    """Scripted backend or library failures, matching pinned message shapes."""
    if kind == "read_timeout_408":
        try:
            raise errors.ReadTimeout("timed out")
        except errors.ReadTimeout as exc:
            raise BackendError(408, "Read timeout") from exc
    if kind.startswith("status_"):
        raise BackendError(int(kind[7:]), raw_body="SECRET-BODY-TEXT")
    if kind == "no_choices":
        raise BackendError(500, "response has no choices: {'secret': 'SECRET-BODY'}")
    if kind == "transport":
        raise errors.ConnectError("SECRET-TRANSPORT-TEXT")
    if kind == "json_decode":
        json.loads("SECRET not json")
    if kind == "non_object":
        [].get("choices")  # The pinned AttributeError path for a 200 JSON array.
    if kind == "int_limit":
        int("9" * 5000)
    if kind == "wrong_choices":
        {"choices": 5}["choices"][0]  # Wrong-shape choices: TypeError.
    if kind == "wrong_usage":
        {"usage": None}["usage"].get("prompt_tokens")  # Wrong-shape usage.
    if kind == "argument_int_limit":
        # Tool-argument JSON holding an over-limit integer: a plain ValueError
        # from json.loads, which is not a JSONDecodeError (pinned S21).
        json.loads('{"offset": ' + "9" * 5000 + "}")
    if kind == "timeout_error":
        raise TimeoutError("SECRET-TIMEOUT-TEXT")
    if kind == "cancelled":
        raise asyncio.CancelledError()
    if kind == "forge_error":
        raise ForgeError("SECRET-FORGE-TEXT")
    if kind == "missing_credential":
        raise MissingCredentialError("SECRET")
    if kind == "max_iterations":
        raise MaxIterationsError("SECRET")
    if kind == "context_budget":
        raise ContextBudgetExceeded("SECRET")
    if kind == "step_enforcement":
        raise StepEnforcementError("SECRET")
    if kind == "sdk_cancelled":
        raise WorkflowCancelledError("SECRET")
    raise RuntimeError("SECRET-UNKNOWN-" + kind)


class OpenAICompatClient:
    def __init__(
        self,
        model,
        base_url,
        *,
        api_key="",
        timeout=120.0,
        recommended_sampling=False,
        **extra,
    ):
        event(
            "client",
            model=model,
            base_url=base_url,
            api_key_sent=bool(api_key),
            api_key=api_key,
            timeout=timeout,
            recommended_sampling=recommended_sampling,
            extra=sorted(extra),
        )
        self.steps = list(SCENARIO.get("responses", []))

    async def send(self, messages, tools):
        event(
            "send",
            messages=[
                {
                    "role": m.role.value,
                    "type": m.metadata.type.value,
                    "content": m.content,
                }
                for m in messages
            ],
        )
        if not self.steps:
            raise RuntimeError("SECRET scenario exhausted")
        step = self.steps.pop(0)
        if step.get("block"):
            time.sleep(step["block"])
        if step.get("sleep"):
            if step.get("convert_cancel"):
                try:
                    await asyncio.sleep(step["sleep"])
                except asyncio.CancelledError:
                    _raise(step["convert_cancel"])
            else:
                await asyncio.sleep(step["sleep"])
        if step.get("raise"):
            _raise(step["raise"])
        if "text" in step:
            return _Text(step["text"])
        return [(name, args) for name, args in step["calls"]]

    async def aclose(self):
        event("aclose")
        behavior = SCENARIO.get("aclose", {})
        if behavior.get("sleep"):
            await asyncio.sleep(behavior["sleep"])
        if behavior.get("raise"):
            raise RuntimeError("SECRET aclose")


class WorkflowRunner:
    def __init__(
        self,
        client,
        context_manager,
        max_iterations=10,
        max_retries_per_step=3,
        max_tool_errors=2,
        stream=False,
        on_chunk=None,
        on_message=None,
        rescue_enabled=True,
        retry_nudge=None,
        reasoning_replay="full",
    ):
        event(
            "runner",
            max_iterations=max_iterations,
            max_retries_per_step=max_retries_per_step,
            max_tool_errors=max_tool_errors,
            stream=stream,
            on_chunk=on_chunk is not None,
            on_message=on_message is not None,
            rescue_enabled=rescue_enabled,
            retry_nudge=retry_nudge is not None,
            reasoning_replay=reasoning_replay,
        )
        self.client = client
        self.max_iterations = max_iterations
        self.max_retries = max_retries_per_step
        self.max_tool_errors = max_tool_errors
        self.on_message = on_message

    async def run(
        self,
        workflow,
        user_message,
        prompt_vars=None,
        initial_messages=None,
        cancel_event=None,
    ):
        event(
            "run",
            user_message=user_message,
            prompt_vars=prompt_vars,
            initial_messages=initial_messages is not None,
            cancel_event=cancel_event is not None,
            required_steps=workflow.required_steps,
            terminal_tools=sorted(workflow.terminal_tools),
            system_prompt=workflow.system_prompt_template,
            tools={name: item.spec.schema for name, item in workflow.tools.items()},
        )
        messages = []

        def emit(message):
            messages.append(message)
            if self.on_message is not None:
                self.on_message(message)
            event(
                "message",
                role=message.role.value,
                type=message.metadata.type.value,
                content=message.content,
                calls=[c.name for c in message.tool_calls or []],
            )

        if SCENARIO.get("malformed_tool_call") and self.on_message is not None:
            # An unreadable observed TOOL_CALL: tool_calls is not iterable.
            self.on_message(
                Message(
                    MessageRole.ASSISTANT,
                    "",
                    MessageMeta(MessageType.TOOL_CALL),
                    tool_calls=None,
                )
            )
            event("observer_returned")
        if SCENARIO.get("return_without_finish") is not None:
            return SCENARIO["return_without_finish"]
        system = workflow.build_system_prompt(**(prompt_vars or {}))
        emit(
            Message(MessageRole.SYSTEM, system, MessageMeta(MessageType.SYSTEM_PROMPT))
        )
        emit(
            Message(MessageRole.USER, user_message, MessageMeta(MessageType.USER_INPUT))
        )
        retries = tool_errors = iteration = counter = 0
        while iteration < self.max_iterations:
            if cancel_event is not None and cancel_event.is_set():
                raise WorkflowCancelledError("cancelled")
            pending = []
            batch = None
            while iteration < self.max_iterations:
                iteration += 1
                response = await self.client.send(messages, workflow.tools)
                if isinstance(response, _Text):
                    retries += 1
                    if retries > self.max_retries:
                        raise ToolCallError("exhausted")
                    pending += [
                        Message(
                            MessageRole.ASSISTANT,
                            response.content,
                            MessageMeta(MessageType.TEXT_RESPONSE),
                        ),
                        Message(
                            MessageRole.USER,
                            "nudge",
                            MessageMeta(MessageType.RETRY_NUDGE),
                        ),
                    ]
                    continue
                unknown = any(name not in workflow.tools for name, _ in response)
                shapeless = any(not isinstance(args, dict) for _, args in response)
                if unknown or shapeless:
                    if unknown:
                        retries += 1
                        exhausted = retries > self.max_retries
                    else:
                        tool_errors += 1
                        exhausted = tool_errors > self.max_tool_errors
                    if exhausted:
                        raise ToolCallError("exhausted")
                    infos = [
                        ToolCallInfo(name, args, f"retry_{i}")
                        for i, (name, args) in enumerate(response)
                    ]
                    pending.append(
                        Message(
                            MessageRole.ASSISTANT,
                            "",
                            MessageMeta(MessageType.TOOL_CALL),
                            tool_calls=infos,
                        )
                    )
                    continue
                retries = 0
                batch = response
                break
            for message in pending:  # Retry messages arrive before the executed batch.
                messages.append(message)
                if self.on_message is not None:
                    self.on_message(message)
            if batch is None:
                break
            infos = []
            for name, args in batch:
                counter += 1
                infos.append(ToolCallInfo(name, args, f"call_{counter}"))
            emit(
                Message(
                    MessageRole.ASSISTANT,
                    "",
                    MessageMeta(MessageType.TOOL_CALL),
                    tool_calls=infos,
                )
            )
            had_error = False
            terminal = None
            for info in infos:
                fn = workflow.tools[info.name].callable
                try:
                    value = fn(**info.args)
                except ToolResolutionError as exc:
                    emit(
                        Message(
                            MessageRole.TOOL,
                            f"[ToolResolutionError] {exc}",
                            MessageMeta(MessageType.TOOL_RESULT),
                            info.name,
                        )
                    )
                    if info.name in workflow.terminal_tools:
                        terminal = exc
                    continue
                except Exception as exc:
                    had_error = True
                    emit(
                        Message(
                            MessageRole.TOOL,
                            f"[ToolError] {type(exc).__name__}: {exc}",
                            MessageMeta(MessageType.TOOL_RESULT),
                            info.name,
                        )
                    )
                    if info.name in workflow.terminal_tools:
                        terminal = exc
                    continue
                text = value if isinstance(value, str) else json.dumps(value)
                emit(
                    Message(
                        MessageRole.TOOL,
                        text,
                        MessageMeta(MessageType.TOOL_RESULT),
                        info.name,
                    )
                )
                if info.name in workflow.terminal_tools:
                    terminal = value
            if had_error:
                tool_errors += 1
                if tool_errors > self.max_tool_errors:
                    raise ToolExecutionError("exhausted")
            else:
                tool_errors = 0
            if terminal is not None and not isinstance(terminal, Exception):
                return await self._after_terminal(workflow, terminal)
        raise MaxIterationsError("max iterations")

    async def _after_terminal(self, workflow, terminal):
        """Scripted post-terminal deviations for worker post-run checks."""
        after = SCENARIO.get("after_terminal", {})
        if after.get("extra_call"):
            try:
                workflow.tools["list_dir"].callable(path="")
            except Exception:
                pass
        if after.get("block"):
            time.sleep(after["block"])
        if after.get("swallow_cancel"):
            try:
                await asyncio.sleep(after["swallow_cancel"])
            except asyncio.CancelledError:
                pass  # A library swallowing cancellation: normal exit after expiry.
        if after.get("other_object"):
            return (terminal + " ")[:-1]
        return terminal
