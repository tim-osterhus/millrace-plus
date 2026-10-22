"""Fresh captured controls and an inert native peer for P4c proofs."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

from millrace.router.execution import AdapterConfiguration, InvocationRequest

ADAPTER_ID = "claude_print"
ADAPTER_CONTRACT = "millrace-claude-print/v1"
FINAL = {"outcome": "READY", "summary": "done", "refs": []}


def envelope(**changes: Any) -> dict[str, Any]:
    # Ordinary envelope fields were observed in OX; structured_output is fake.
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "api_error_status": None,
        "permission_denials": [],
        "session_id": "00000000-0000-4000-8000-000000000001",
        "modelUsage": {"claude-opus-5-5": {}},
        "result": '{"outcome":"WRONG"}',
        "structured_output": FINAL,
        **changes,
    }


def fake(
    root: Path,
    *,
    wire: bytes | None = None,
    version: bytes = b"2.1.296 (Claude Code)\n",
    stderr: bytes = b"",
    **options: Any,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    executable = root / "fake-claude"
    source = Path(__file__).parent / "fixtures/claude_peer.py"
    executable.write_text(f"#!{sys.executable}\n" + source.read_text())
    executable.chmod(0o700)
    Path(str(executable) + ".json").write_text(
        json.dumps(
            {
                "wire": base64.b64encode(
                    wire if wire is not None else json.dumps(envelope()).encode()
                ).decode(),
                "version": base64.b64encode(version).decode(),
                "stderr": base64.b64encode(stderr).decode(),
                **options,
            }
        )
    )
    return executable


def request(
    root: Path, *, prompt: bytes = b"Review.", executable: str = "/no/claude"
) -> InvocationRequest:
    return InvocationRequest(
        invocation_id="1" * 32,
        node_id="draft",
        producing_revision="revision",
        binding_id="native",
        adapter_id=ADAPTER_ID,
        adapter_contract=ADAPTER_CONTRACT,
        executable=executable,
        binding_config={"profile": "claude.json"},
        node_config={"prompt": "task.txt"},
        runtime={},
        workflow={},
        common_settings={},
        files={
            "claude.json": (
                b'{"format":"millrace-claude-print-profile/v1",'
                b'"claude_version":"2.1.296"}'
            ),
            "task.txt": prompt,
        },
        host_paths={},
        environment_names=(),
        outcomes=("READY",),
        result_contract="json-v1",
        project_root=root,
        invocation_root=root / "invocation",
        plane_id="main",
        plane={},
    )


def config(req: InvocationRequest) -> AdapterConfiguration:
    return AdapterConfiguration(
        adapter_id=req.adapter_id,
        adapter_contract=req.adapter_contract,
        bindings={"native": req.binding_config},
        nodes={("main", "draft"): ("native", req.node_config)},
        runtime=req.runtime,
        workflow=req.workflow,
        files=req.files,
        host_paths=req.host_paths,
        environment_names=req.environment_names,
        planes={"main": {}},
    )


def flow(single: tuple[Path, Path, Path], **options: Any) -> tuple[Path, Path, Path]:
    workflow, project, home = single
    native = fake(workflow.parent, **options)
    data = json.loads(workflow.read_text())
    data["bindings"]["claude"] = {
        "adapter": ADAPTER_ID,
        "contract": ADAPTER_CONTRACT,
        "executable": str(native),
        "config": {"profile": "claude.json"},
    }
    data["control_files"] = [*data.get("control_files", []), "claude.json", "task.txt"]
    data["planes"]["main"]["nodes"]["draft"]["invoke"] = {
        "binding": "claude",
        "config": {"prompt": "task.txt"},
        "timeout_seconds": 3,
    }
    data["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    data["planes"]["main"]["nodes"]["draft"]["on_unknown"] = {"finish": "failed"}
    workflow.write_text(json.dumps(data))
    (workflow.parent / "task.txt").write_text("Review.")
    (workflow.parent / "claude.json").write_bytes(request(project).files["claude.json"])
    return workflow, project, home
