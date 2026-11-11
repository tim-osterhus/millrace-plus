"""Prime's ordinary executable fake and captured/public DTO fixtures."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import pytest
from millrace.router.execution import (
    AdapterConfiguration,
    CompletionFacts,
    InvocationRequest,
)

from millrace_plus.router import prime_configuration as c
from millrace_plus.router.prime import PrimePrintAdapter

FINAL = b'{"outcome":"READY","summary":"done","refs":[]}\n'
PROFILE = {"format": "millrace-prime-profile/v1", "prime_version": "0.10.2"}
PEER = Path(__file__).parent / "fixtures/prime_peer.py"


def fake(root, *, raw=FINAL, version=b"0.10.2\n", **options):
    root.mkdir(parents=True, exist_ok=True)
    target = root / "fake-prime"
    target.write_text(f"#!{sys.executable}\n" + PEER.read_text())
    target.chmod(0o700)
    Path(str(target) + ".json").write_text(json.dumps({
        "wire": base64.b64encode(raw).decode(),
        "version": base64.b64encode(version).decode(), **options,
    }))
    return target


def request(root, *, executable="/absent/prime-agent", prompt=b"Review the project."):
    return InvocationRequest(
        "1" * 32, "main", "draft", "revision", "native", "prime_print",
        "millrace-prime-print/v1", executable, {"profile": "prime.json"},
        {"prompt": "task.txt"}, {}, {}, {}, {},
        {"prime.json": json.dumps(PROFILE).encode(), "task.txt": prompt},
        {}, (), ("READY",), "json-v1", root, root / "invocation",
    )


def configuration(req):
    return AdapterConfiguration(
        req.adapter_id, req.adapter_contract, {"native": req.binding_config},
        {("main", "draft"): ("native", req.node_config)}, req.runtime,
        req.workflow, {"main": req.plane}, req.files, req.host_paths,
        req.environment_names,
    )


def prepared(root, **options):
    req = request(root, executable=str(fake(root, **options)))
    plan = c.plan(req)
    req.invocation_root.mkdir(exist_ok=True)
    for name, raw in plan.generated_controls.items():
        (req.invocation_root / name).write_bytes(raw)
    return plan


def facts(plan, result, pid):
    return CompletionFacts(
        plan.invocation_id, pid, result.stdout, result.stderr,
        result.classification, result.writer_state, result.diagnostic,
        result.exit_code, result.adapter_evidence, "adapter",
    )


def invoke(plan, root, *, timeout=3, poll=lambda: False, started=lambda pid: None):
    pids = []

    def on_started(pid):
        pids.append(pid)
        started(pid)

    result = PrimePrintAdapter().invoke(
        plan, cwd=root, env={"PATH": "/usr/bin:/bin"},
        deadline=time.time() + timeout, poll=poll, on_started=on_started,
    )
    c.validate_completion(plan, facts(plan, result, pids[0] if pids else None))
    for pid in [*pids, result.adapter_evidence.get("probe_pid")]:
        if pid is not None:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    return result


def prime_flow(single_flow, **options):
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["bindings"] = {"native": {
        "adapter": "prime_print", "contract": "millrace-prime-print/v1",
        "executable": str(fake(workflow.parent, **options)),
        "config": {"profile": "prime.json"},
    }}
    graph["control_files"] = ["prime.json", "task.txt"]
    graph["planes"]["main"]["nodes"]["draft"]["invoke"] = {
        "binding": "native", "config": {"prompt": "task.txt", "model": "selected"},
        "timeout_seconds": 3,
    }
    workflow.write_text(json.dumps(graph))
    (workflow.parent / "prime.json").write_text(json.dumps(PROFILE))
    (workflow.parent / "task.txt").write_text("Review the project.")
    return workflow, project, home
