"""Fresh immutable DTOs and real pinned event shapes for offline OpenCode tests."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from millrace.router.execution import AdapterConfiguration, InvocationRequest

ADAPTER_ID = "opencode_run"
ADAPTER_CONTRACT = "millrace-opencode-run/v1"
FINAL = b'{"outcome":"READY","summary":"done","refs":[]}'
PROFILE = b'{"format":"millrace-opencode-run-profile/v1","opencode_version":"1.19.3"}'


def event(
    kind: str, part_id: str, message: str = "msg-final", **values: Any
) -> dict[str, Any]:
    return {
        "type": kind,
        "timestamp": 1,
        "sessionID": "ses-test",
        "part": {
            "id": part_id,
            "messageID": message,
            "sessionID": "ses-test",
            "type": {
                "step_start": "step-start",
                "step_finish": "step-finish",
                "tool_use": "tool",
            }.get(kind, kind),
            **values,
        },
    }


def finish(message: str = "msg-final", reason: str = "stop") -> dict[str, Any]:
    return event(
        "step_finish",
        "finish-" + message,
        message,
        reason=reason,
        cost=0,
        tokens={
            "input": 1,
            "output": 1,
            "reasoning": 0,
            "cache": {"read": 0, "write": 0},
        },
    )


def events(text: bytes = FINAL) -> list[dict[str, Any]]:
    return [
        event("step_start", "start"),
        event("text", "text-final", text=text.decode(), time={"start": 1, "end": 2}),
        finish(),
    ]


def wire(values: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(value, ensure_ascii=False).encode() + b"\n" for value in values
    )


def fake(
    root: Path,
    *,
    values: list[dict[str, Any]] | None = None,
    raw: bytes | None = None,
    version: bytes = b"1.19.3\n",
    stderr: bytes = b"",
    **options: Any,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / "fake-opencode"
    target.write_text(
        f"#!{sys.executable}\n"
        + (Path(__file__).parent / "fixtures/opencode_peer.py").read_text()
    )
    target.chmod(0o700)
    Path(str(target) + ".json").write_text(
        json.dumps(
            {
                "wire": base64.b64encode(
                    raw
                    if raw is not None
                    else wire(values if values is not None else events())
                ).decode(),
                "version": base64.b64encode(version).decode(),
                "stderr": base64.b64encode(stderr).decode(),
                **options,
            }
        )
    )
    return target


def request(
    root: Path,
    *,
    prompt: bytes = b"Review; return json-v1.",
    executable: str = "/absent/opencode",
) -> InvocationRequest:
    return InvocationRequest(
        invocation_id="1" * 32,
        plane_id="main",
        node_id="draft",
        producing_revision="revision",
        binding_id="native",
        adapter_id=ADAPTER_ID,
        adapter_contract=ADAPTER_CONTRACT,
        executable=executable,
        binding_config={"profile": "opencode.json"},
        node_config={"prompt": "task.txt"},
        runtime={},
        workflow={},
        plane={},
        common_settings={},
        files={"opencode.json": PROFILE, "task.txt": prompt},
        host_paths={},
        environment_names=(),
        outcomes=("READY",),
        result_contract="json-v1",
        project_root=root,
        invocation_root=root / "invocation",
    )


def config(req: InvocationRequest) -> AdapterConfiguration:
    return AdapterConfiguration(
        adapter_id=req.adapter_id,
        adapter_contract=req.adapter_contract,
        bindings={"native": req.binding_config},
        nodes={("main", "draft"): ("native", req.node_config)},
        runtime=req.runtime,
        workflow=req.workflow,
        planes={"main": req.plane},
        files=req.files,
        host_paths=req.host_paths,
        environment_names=req.environment_names,
    )


def flow(single: tuple[Path, Path, Path], **options: Any) -> tuple[Path, Path, Path]:
    workflow, project, home = single
    native = fake(workflow.parent, **options)
    data = json.loads(workflow.read_text())
    data["bindings"]["opencode"] = {
        "adapter": ADAPTER_ID,
        "contract": ADAPTER_CONTRACT,
        "executable": str(native),
        "config": {"profile": "opencode.json"},
    }
    data["control_files"] = [
        *data.get("control_files", []),
        "opencode.json",
        "task.txt",
    ]
    data["planes"]["main"]["nodes"]["draft"]["invoke"] = {
        "binding": "opencode",
        "config": {"prompt": "task.txt"},
        "timeout_seconds": 2,
    }
    workflow.write_text(json.dumps(data))
    (workflow.parent / "task.txt").write_bytes(request(project).files["task.txt"])
    (workflow.parent / "opencode.json").write_bytes(PROFILE)
    return workflow, project, home
