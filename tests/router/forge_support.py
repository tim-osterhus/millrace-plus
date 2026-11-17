"""Forge fakes, public DTO fixtures and Core-control helpers. No SDK or network.

The fake executable is ``fixtures/forge_peer.py``. Worker cases run the real
packaged ``forge_worker.py`` over the test-only fake ``forge`` package in
``fixtures/forge_fake_sdk/``. The loopback URL below is fixture data only;
nothing listens on it and no fake connects to it.

Flow helpers call Core's public control API with an explicit resolver. The Plus
resolver now selects the exact Forge pair; ``forge_resolver`` keeps the Forge
branch local to these helpers and otherwise delegates to it. Installed-artifact
proof stays in ``test_forge_distribution.py::test_installed_resolver_and_assets``,
which runs only against a supplied prebuilt artifact manifest.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from millrace.router import control as core
from millrace.router.execution import (
    AdapterConfiguration,
    CompletionFacts,
    InvocationRequest,
)

from millrace_plus.router import forge_configuration as c
from millrace_plus.router.forge import ForgeRunnerAdapter
from millrace_plus.router.resolver import resolve_adapter

FIXTURES = Path(__file__).parent / "fixtures"
PEER = FIXTURES / "forge_peer.py"
FAKE_SDK = FIXTURES / "forge_fake_sdk"
URL_ENV, KEY_ENV = "FORGE_BASE_URL", "FORGE_API_KEY"
ENV_NAMES = (URL_ENV, KEY_ENV)
LOOPBACK = "http://127.0.0.1:9/v1"
KEY = "fixture-key-not-a-secret"
INVOCATION = "1" * 32
# Soft-deadline allowance for worker cases that must not expire. Expiry cases pass
# small explicit values. This is a test allowance, not a runtime budget.
NORMAL_SOFT = 57.0
PROFILE = {
    "format": c.PROFILE_FORMAT,
    "forge_version": "0.10.1",
    "backend": "openai_compat",
    "base_url": {"kind": "env", "name": URL_ENV},
    "auth": {"kind": "env", "name": KEY_ENV},
    "model": "fixture-model",
}
ACCEPTED = {
    "format": c.PROBE_FORMAT,
    "python": "3.12.7",
    "implementation": "cpython",
    "forge_guardrails": "0.10.1",
    "files_match": True,
}
SEMANTIC = '{"outcome":"READY","summary":"done","refs":[]}'
ENV = {"PATH": "/usr/bin:/bin", URL_ENV: LOOPBACK, KEY_ENV: KEY}


def probe_line(**changes: object) -> bytes:
    return c.probe_line({**ACCEPTED, **changes})


def completed(semantic: str = SEMANTIC, invocation_id: str = INVOCATION) -> bytes:
    return c.envelope_line(invocation_id, "completed", semantic, None)


def failed(code: str, invocation_id: str = INVOCATION) -> bytes:
    return c.envelope_line(invocation_id, "failed", None, code)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def fake(root: Path, **options: object) -> Path:
    """Write the peer as an executable with its options file."""
    root.mkdir(parents=True, exist_ok=True)
    target = root / "fake-forge-python"
    target.write_text(f"#!{sys.executable} -B\n" + PEER.read_text())
    target.chmod(0o700)
    selected: dict[str, object] = {
        "fake_sdk": str(FAKE_SDK),
        "probe_stdout": probe_line(),
        "wire": completed(),
        "exit_code": 0,
        **options,
    }
    for key in ("probe_stdout", "probe_stderr", "wire", "stderr"):
        if isinstance(selected.get(key), bytes):
            selected[key] = _b64(selected[key])  # type: ignore[arg-type]
    Path(str(target) + ".json").write_text(json.dumps(selected))
    return target


def request(
    root: Path,
    *,
    executable: str = "/absent/python3.12",
    prompt: bytes = b"Review the project.",
    profile: object = None,
    **changes: object,
) -> InvocationRequest:
    raw = (
        profile
        if isinstance(profile, bytes)
        else json.dumps(PROFILE if profile is None else profile).encode()
    )
    base = InvocationRequest(
        INVOCATION,
        "main",
        "draft",
        "revision",
        "native",
        c.ADAPTER_ID,
        c.ADAPTER_CONTRACT,
        executable,
        {"profile": "forge.json"},
        {"prompt": "task.txt"},
        {},
        {},
        {},
        {"effective": {"timeout_seconds": 120}},
        {"forge.json": raw, "task.txt": prompt},
        {},
        ENV_NAMES,
        ("READY",),
        "json-v1",
        root,
        root / "invocation",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def configuration(req: InvocationRequest, **changes: object) -> AdapterConfiguration:
    base = AdapterConfiguration(
        req.adapter_id,
        req.adapter_contract,
        {"native": req.binding_config},
        {("main", "draft"): ("native", req.node_config)},
        req.runtime,
        req.workflow,
        {"main": req.plane},
        req.files,
        req.host_paths,
        req.environment_names,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def materialize(plan) -> None:
    root = Path(plan.details["worker_path"]).parent
    root.mkdir(parents=True, exist_ok=True)
    for name, raw in plan.generated_controls.items():
        (root / name).write_bytes(raw)


def prepared(root: Path, **options: object):
    plan = c.plan(request(root, executable=str(fake(root, **options))))
    materialize(plan)
    return plan


def facts(plan, result, pid) -> CompletionFacts:
    return CompletionFacts(
        plan.invocation_id,
        pid,
        result.stdout,
        result.stderr,
        result.classification,
        result.writer_state,
        result.diagnostic,
        result.exit_code,
        result.adapter_evidence,
        "adapter",
    )


def invoke(
    plan,
    root: Path,
    *,
    timeout: float = 60,
    poll=lambda: False,
    started=lambda pid: None,
    env=None,
    validate: bool = True,
):
    """Run the adapter, validate its completion purely, and check pids are gone."""
    pids: list[int] = []

    def on_started(pid: int) -> None:
        pids.append(pid)
        started(pid)

    result = ForgeRunnerAdapter().invoke(
        plan,
        cwd=root,
        env=ENV if env is None else env,
        deadline=time.time() + timeout,
        poll=poll,
        on_started=on_started,
    )
    assert len(pids) <= 1
    if validate:
        c.validate_completion(plan, facts(plan, result, pids[0] if pids else None))
    evidence = result.adapter_evidence
    for item in (evidence["probe"], evidence["main"]):
        if item is not None and item["pid"] is not None:
            with pytest.raises(ProcessLookupError):
                os.kill(item["pid"], 0)
    return result


def row(result) -> tuple[str, str, str, int | None]:
    return (
        result.classification,
        result.diagnostic,
        result.writer_state,
        result.exit_code,
    )


# ---------------------------------------------------------------- worker cases


@dataclass
class Worker:
    code: int
    stdout: bytes
    stderr: bytes
    envelope: dict | None
    events: list[dict]
    plan: object
    elapsed: float


def workspace(root: Path) -> Path:
    """The worker case workspace; fakes, scenario and events stay outside it."""
    path = root / "ws"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_worker(
    root: Path,
    scenario: dict | None = None,
    *,
    prompt: bytes | None = None,
    start: bytes | None = None,
    soft_in: float = NORMAL_SOFT,
    python: tuple[int, int, int] | None = None,
    env: dict | None = None,
    profile: object = None,
    cwd: Path | None = None,
    argv: list[str] | None = None,
    timeout: float = 90,
    contract: str = c.ADAPTER_CONTRACT,
    **options: object,
) -> Worker:
    """Run the real worker over the fake SDK, directly (no probe, no adapter)."""
    root.mkdir(parents=True, exist_ok=True)
    events = root / "events.jsonl"
    scenario_path = root / "scenario.json"
    scenario_path.write_text(json.dumps({"events": str(events), **(scenario or {})}))
    if python is not None:
        options = {**options, "python": list(python)}
    executable = fake(
        root / "bin", real_worker=True, scenario=str(scenario_path), **options
    )
    ws = workspace(root)
    req = replace(
        request(ws, executable=str(executable), profile=profile),
        invocation_root=root / "inv",
        adapter_contract=contract,
    )
    if prompt is not None:
        req = replace(req, files={**req.files, "task.txt": prompt})
    plan = resolve_adapter(c.ADAPTER_ID, contract).plan(req)
    materialize(plan)
    now = time.time()
    if start is None:
        start = c.start_record(plan.invocation_id, now + soft_in + 3, now + soft_in)
    began = time.monotonic()
    proc = subprocess.run(
        argv or list(plan.argv),
        input=start,
        cwd=cwd or ws,
        env=ENV if env is None else env,
        capture_output=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - began
    envelope = None
    if proc.stdout.endswith(b"\n") and proc.stdout.count(b"\n") == 1:
        envelope = json.loads(proc.stdout)
    logged = (
        [json.loads(line) for line in events.read_text().splitlines()]
        if events.exists()
        else []
    )
    return Worker(
        proc.returncode, proc.stdout, proc.stderr, envelope, logged, plan, elapsed
    )


def finish_call(
    outcome: str = "READY", summary: str = "done", refs: list[str] | None = None
) -> list:
    return ["finish", {"outcome": outcome, "summary": summary, "refs": refs or []}]


# ---------------------------------------------------------------- flows


def forge_resolver(adapter_id: str, contract: str):
    if (adapter_id, contract) == (c.ADAPTER_ID, c.ADAPTER_CONTRACT):
        return ForgeRunnerAdapter()
    return resolve_adapter(adapter_id, contract)


def new_id() -> str:
    return uuid.uuid4().hex


def forge_flow(single_flow, *, timeout: float = 60, profile=None, **options):
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    graph["bindings"] = {
        "native": {
            "adapter": c.ADAPTER_ID,
            "contract": c.ADAPTER_CONTRACT,
            "executable": str(
                fake(
                    workflow.parent,
                    echo_invocation=True,
                    wire=completed(invocation_id="__INVOCATION__"),
                    **options,
                )
            ),
            "config": {"profile": "forge.json"},
        }
    }
    graph["control_files"] = ["forge.json", "task.txt"]
    graph["limits"] = {"max_invocations": 6, "max_elapsed_seconds": 600}
    node = graph["planes"]["main"]["nodes"]["draft"]
    node["invoke"] = {
        "binding": "native",
        "config": {"prompt": "task.txt"},
        "timeout_seconds": timeout,
    }
    workflow.write_text(json.dumps(graph))
    (workflow.parent / "forge.json").write_text(json.dumps(profile or PROFILE))
    (workflow.parent / "task.txt").write_text("Review the project.")
    return workflow, project, home


def create(workflow: Path, project: Path, home: Path) -> str:
    receipt = core.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        env_allowlist=ENV_NAMES,
        adapter_resolver=forge_resolver,
    )
    assert receipt["status"] == "applied", receipt
    return str(receipt["instance_id"])


def start(instance: str, home: Path, **kwargs) -> dict:
    version = core.inspect_instance(
        instance,
        state_home=home,
        adapter_resolver=forge_resolver,
    )["state_version"]
    receipt = core.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=version,
        adapter_resolver=forge_resolver,
        **kwargs,
    )
    assert receipt["status"] == "applied", receipt
    return inspect(instance, str(receipt["run_id"]), home)


def inspect(instance: str, run: str, home: Path) -> dict:
    result = core.inspect_run(
        instance, run, state_home=home, adapter_resolver=forge_resolver
    )
    result["controls"] = []  # Receipt rows accumulate on explicit recovery.
    return result


def flow_env(monkeypatch) -> None:
    monkeypatch.setenv(URL_ENV, LOOPBACK)
    monkeypatch.setenv(KEY_ENV, KEY)
