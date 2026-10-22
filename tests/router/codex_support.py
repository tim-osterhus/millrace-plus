"""Shared finite fake Codex wire and public-control helpers for P4b tests."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from plus_support import new_id

from millrace_plus.router import control

ADAPTER_ID = "codex_exec"
ADAPTER_CONTRACT = "millrace-codex-exec/v1"
FINAL = b'{"outcome":"READY","summary":"done","refs":[]}'


def events(
    *, thread_id: str = "fake-thread", items: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    return [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        *(items or []),
        {
            "type": "turn.completed",
            "usage": {
                key: 0
                for key in (
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                )
            },
        },
    ]


def wire(values: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(v).encode() + b"\n" for v in values)


def fake(
    tmp: Path,
    *,
    events: list[dict[str, Any]] | None = None,
    final: bytes | None = FINAL,
    version: str = "0.163.0",
    before: str = "",
    after: str = "",
    version_extra: str = "",
    exit_code: int = 0,
    raw_wire: bytes | None = None,
) -> Path:
    target = tmp / "fake-codex"
    source = Path(__file__).parent / "fixtures/codex_peer.py"
    target.write_text(
        f"#!{sys.executable}\n"
        + source.read_text().replace("from __future__ import annotations\n", "")
    )
    target.chmod(0o700)
    Path(str(target) + ".json").write_text(
        json.dumps(
            {
                "wire": base64.b64encode(
                    raw_wire
                    if raw_wire is not None
                    else wire(events if events is not None else globals()["events"]())
                ).decode(),
                "final": base64.b64encode(final).decode()
                if final is not None
                else None,
                "version": version,
                "before": before,
                "after": after,
                "version_extra": version_extra,
                "exit_code": exit_code,
            }
        )
    )
    return target


def codex_flow(
    flow: tuple[Path, Path, Path], **kwargs: Any
) -> tuple[Path, Path, Path, Path]:
    workflow, project, home = flow
    program = fake(workflow.parent, **kwargs)
    native_home = project / "codex-home"
    native_home.mkdir(exist_ok=True)
    (native_home / "auth.json").write_text("{}")
    data = json.loads(workflow.read_text())
    data["bindings"]["codex"] = {
        "adapter": ADAPTER_ID,
        "contract": ADAPTER_CONTRACT,
        "executable": str(program),
        "config": {"profile": "codex.json"},
    }
    data["control_files"] = [*data.get("control_files", []), "codex.json", "task.txt"]
    data["planes"]["main"]["nodes"]["draft"]["invoke"] = {
        "binding": "codex",
        "config": {"prompt": "task.txt"},
        "timeout_seconds": 3,
    }
    workflow.write_text(json.dumps(data))
    (workflow.parent / "task.txt").write_text("Review this project.")
    (workflow.parent / "codex.json").write_text(
        json.dumps(
            {
                "format": "millrace-codex-exec-profile/v1",
                "codex_version": "0.163.0",
                "codex_home": "codex_home",
                "auth_mode": "chatgpt",
                "sandbox": "workspace-write",
                "model": "exact-model",
                "reasoning_effort": "low",
            }
        )
    )
    return workflow, project, home, native_home


def codex_instance(
    workflow: Path, project: Path, home: Path, codex_home: Path, **kwargs: Any
) -> str:
    result = control.create_instance(
        workflow,
        project,
        state_home=home,
        host_paths={"codex_home": str(codex_home)},
        request_id=new_id(),
        expected_state_version=0,
        **kwargs,
    )
    assert result["status"] == "applied", result
    return str(result["instance_id"])
