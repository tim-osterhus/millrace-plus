"""Deterministic command-only helper fixtures using public Plus controls."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

from millrace_plus import catalog
from millrace_plus.router import control


def graph(script: str = "step.py") -> dict[str, object]:
    return {
        "format": "millrace-router/v3",
        "limits": {"max_invocations": 8, "max_elapsed_seconds": 30},
        "bindings": {
            "python": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": sys.executable,
                "config": {},
            }
        },
        "control_files": [script],
        "defaults": {"timeout_seconds": 10},
        "planes": {
            "main": {
                "workspace": ".",
                "start": "step",
                "nodes": {
                    "step": {
                        "invoke": {
                            "binding": "python",
                            "cwd": ".",
                            "config": {"script": script, "args": []},
                        },
                        "result": "json-v1",
                        "outcomes": ["READY", "HOLD"],
                        "on": {
                            "READY": {"finish": "succeeded"},
                            "HOLD": {"pause": {"reason": "human", "resume": "step"}},
                        },
                        "on_error": {"pause": {"reason": "error", "resume": "step"}},
                        "on_unknown": {
                            "pause": {"reason": "unknown", "resume": "step"}
                        },
                    }
                },
            }
        },
    }


def setup(root: Path, *, script: str | None = None) -> dict[str, object]:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    local = root / "catalog"
    definition = local / "child"
    definition.mkdir(parents=True)
    (definition / "workflow.json").write_text(json.dumps(graph()))
    (definition / "step.py").write_text(
        script
        or (
            "import json\nfrom pathlib import Path\n"
            "m=json.loads(Path('subworkflow-inputs.json').read_bytes())\n"
            "Path('answer.md').write_bytes(m['inputs']['request_markdown'].encode())\n"
            "print(json.dumps({'outcome':'READY','summary':'saved','refs':['answer.md']}))\n"
        )
    )
    catalog.set_catalog_home(local)
    workflow, project, state = (
        root / "parent-workflow",
        root / "parent",
        root / "parent-state",
    )
    workflow.mkdir()
    project.mkdir()
    (workflow / "workflow.json").write_text(json.dumps(graph()))
    (workflow / "step.py").write_text('print(\'{"outcome":"READY"}\')\n')
    made = control.create_instance(
        workflow / "workflow.json",
        project,
        state_home=state,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        path=(str(Path(sys.executable).parent), "/usr/bin", "/bin"),
    )
    assert made["status"] == "applied", made
    digest = catalog.workflow_fingerprint(definition)
    request = {
        "format": "millrace-plus-subworkflow-request/v1",
        "call_key": "review_1",
        "call_home": str(root / "calls"),
        "parent": {"state_home": str(state), "instance_id": made["instance_id"]},
        "definition": {
            "selector": "local:child",
            "version": "sha256:" + digest,
            "sha256": digest,
        },
        "output_ref": "results/review.json",
        "wait_seconds": 5,
        "cleanup_seconds": 5,
        "inputs": {"request_markdown": "Exact goal\r\n\n"},
    }
    request_file = root / "request.json"
    request_file.write_text(json.dumps(request))
    return {
        "request": request,
        "file": request_file,
        "project": project,
        "definition": definition,
        "root": root,
        "local": local,
    }


def write_request(fixture: dict[str, object]) -> Path:
    path = Path(str(fixture["file"]))
    path.write_text(json.dumps(fixture["request"]))
    return path


def wait_for(predicate, *, seconds: float = 8):
    """Bounded test synchronization; return the first actionable observation."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("test observation deadline")
