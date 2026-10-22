"""Shared fake Pi peer and Plus facade helpers for transferred router tests.

The literal pinned-wire helpers (`_assistant`, `_events`, `_fake`, `_wire`) are
transferred byte-for-byte from the accepted Core Pi tests. `native_flow` builds
the same owned fake-Pi workflow in the v3 graph envelope. Every lifecycle helper
uses the public Plus facade, so the Plus resolver reaches each Core control.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from millrace.router.execution import RouterError

from millrace_plus.router import control
from millrace_plus.router.configuration import ADAPTER_CONTRACT, ADAPTER_ID

AGENT_ALIAS = "agent"


class Crash(BaseException):
    """Test-owned crash injection that bypasses ordinary exception handling."""


def _assistant(texts: list[str], *, stop: str = "stop") -> dict[str, object]:
    return {
        "role": "assistant",
        "content": [{"type": "text", "text": text} for text in texts],
        "api": "test-api",
        "provider": "fixture",
        "model": "exact-model",
        "usage": {
            "input": 1,
            "output": 1,
            "cacheRead": 0,
            "cacheWrite": 0,
            "totalTokens": 2,
            "cost": {
                "input": 0,
                "output": 0,
                "cacheRead": 0,
                "cacheWrite": 0,
                "total": 0,
            },
        },
        "stopReason": stop,
        "timestamp": 1,
    }


def _events(cwd: Path, messages: list[dict[str, object]]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {
            "type": "session",
            "version": 3,
            "id": "fake-session",
            "timestamp": "2026-10-19",
            "cwd": str(cwd),
        },
        {"type": "agent_start"},
    ]
    for message in messages:
        events += [
            {"type": "turn_start"},
            {"type": "message_start", "message": {"role": "assistant", "content": []}},
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "progress"},
                "usage": {},
            },
            {"type": "message_end", "message": message},
            {"type": "turn_end", "message": message},
        ]
    return events + [
        {"type": "agent_end", "messages": messages, "willRetry": False},
        {"type": "agent_settled"},
    ]


def _fake(
    tmp: Path,
    wire: bytes,
    *,
    version: str = "0.87.0",
    before: str = "",
    after: str = "",
    version_extra: str = "",
) -> Path:
    target = tmp / "fake-pi"
    target.write_text(
        f"#!{sys.executable}\nimport sys,os,time\nfrom pathlib import Path\n"
        f"if '--version' in sys.argv:\n {version_extra or 'pass'}\n"
        f" print({version!r})\n sys.exit(0)\n{before}\n"
        f"os.write(1,{wire!r})\n{after}\n"
    )
    target.chmod(0o700)
    return target


def _wire(events: list[dict[str, object]]) -> bytes:
    return b"".join(json.dumps(e).encode() + b"\n" for e in events)


def native_flow(flow: tuple[Path, Path, Path]) -> tuple[Path, Path, Path, Path]:
    """Build owned files; callers supply their public API lifecycle assertions."""
    workflow, project, home = flow
    project = project.resolve()
    wire = _wire(
        _events(
            project, [_assistant(['{"outcome":"READY","summary":"done","refs":[]}'])]
        )
    )
    flags = [
        "--offline",
        "--mode",
        "json",
        "--no-session",
        "--no-approve",
        "--provider",
        "fixture",
        "--model",
        "exact-model",
        "--thinking",
        "off",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--",
    ]
    assertion = (
        f"assert sys.argv[1:-1] == {flags!r}\n"
        "assert sys.argv[-1].startswith('@')\n"
        "assert sys.stdin.read() == ''\n"
        "prompt=Path(sys.argv[-1][1:]).read_text()\n"
        "assert prompt.startswith('Millrace invocation instructions.')\n"
        "assert 'outcome must be one of: [\"READY\"]' in prompt\n"
    )
    program = _fake(workflow.parent, wire, before=assertion)
    agent = project / "agent"
    agent.mkdir(exist_ok=True)
    data = json.loads(workflow.read_text())
    data["bindings"]["pi"] = {
        "adapter": ADAPTER_ID,
        "contract": ADAPTER_CONTRACT,
        "executable": str(program),
        "config": {"profile": "pi.json"},
    }
    data["planes"]["main"]["nodes"]["draft"]["invoke"] = {
        "binding": "pi",
        "config": {"prompt": "task.txt"},
        "timeout_seconds": 3,
    }
    data["control_files"] = [*data.get("control_files", []), "pi.json", "task.txt"]
    workflow.write_text(json.dumps(data))
    (workflow.parent / "task.txt").write_text("Review this project.")
    (workflow.parent / "pi.json").write_text(
        json.dumps(
            {
                "format": "millrace-pi-json-profile/v1",
                "pi_version": "0.87.0",
                "agent_dir": AGENT_ALIAS,
                "provider": "fixture",
                "model": "exact-model",
                "thinking": "off",
            }
        )
    )
    return workflow, project, home, agent


def new_id() -> str:
    import uuid

    return uuid.uuid4().hex


def _projection(instance: str, run: str, home: Path) -> dict[str, Any]:
    result = control.inspect_run(instance, run, state_home=home)
    # New receipt rows legitimately accumulate on every explicit recovery.
    # All execution/version/revision/writer history is left intact.
    result["controls"] = []
    return result


def create_instance(
    workflow: Path, project: Path, *, state_home: Path, **kwargs: Any
) -> dict[str, Any]:
    result = control.create_instance(
        workflow,
        project,
        state_home=state_home,
        request_id=new_id(),
        expected_state_version=0,
        **kwargs,
    )
    if result["status"] == "refused":
        raise RouterError(str(result["code"]))
    return result


def start_run(
    instance: str,
    *,
    state_home: Path,
    edit_mode: str = "locked",
    on_started: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, Any]:
    version = control.inspect_instance(instance, state_home=state_home)["state_version"]
    receipt = control.start_run(
        instance,
        state_home=state_home,
        request_id=new_id(),
        expected_state_version=version,
        edit_mode=edit_mode,
        on_started=on_started,
    )
    if receipt["status"] == "refused":
        raise RouterError(str(receipt["code"]))
    return _projection(instance, str(receipt["run_id"]), state_home)


def inspect_run(instance: str, run: str, *, state_home: Path) -> dict[str, Any]:
    return _projection(instance, run, state_home)


def recover_run(instance: str, run: str, *, state_home: Path) -> dict[str, Any]:
    before = control.inspect_run(instance, run, state_home=state_home)
    version = before["state_version"]
    request_id = new_id()
    receipt = control.recover_run(
        instance,
        run,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=version,
    )
    if receipt["status"] == "refused" and receipt["code"] != "stale_state_version":
        raise RouterError(str(receipt["code"]))
    if receipt["status"] == "refused":
        after = control.inspect_run(instance, run, state_home=state_home)
        assert any(
            item["status"] in {"reserved", "active"} for item in before["invocations"]
        )
        assert after["state_version"] > version
        assert after["state_version"] == receipt["state_version"]
        assert after["count"] == before["count"]
        assert after["status"] == "paused" and after["pause_reason"] == "writer_unknown"
        assert (
            control.recover_run(
                instance,
                run,
                state_home=state_home,
                request_id=request_id,
                expected_state_version=version,
            )
            == receipt
        )
    return _projection(instance, run, state_home)


def store_path(instance: str, home: Path) -> Path:
    """The instance SQLite path, for test-owned fault injection only."""
    path = getattr(control.load_instance(instance, home), "store_path")
    assert isinstance(path, Path)
    return path


def pi_instance(
    workflow: Path, project: Path, home: Path, agent: Path, **kwargs: Any
) -> str:
    return str(
        create_instance(
            workflow,
            project,
            state_home=home,
            host_paths={AGENT_ALIAS: str(agent)},
            **kwargs,
        )["instance_id"]
    )
