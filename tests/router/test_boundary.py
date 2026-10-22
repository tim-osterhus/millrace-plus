"""Plus-side boundary proofs: public imports, purity, preflight order and bounds.

- Production Plus router code imports only Core's public execution, control and
  CLI modules (packet section 2, R8), and `millrace_plus.router` alone imports
  no Core code.
- Configuration validation, planning and completion validation are pure over
  the public DTOs: no filesystem, process or environment access, and adapter
  settings do not depend on invocation identity or roots (B1, O2).
- Native local prerequisites refuse before any version-probe process (O3).
- The Pi prompt fits Core's generated-control allowance: a full 1 MiB prompt
  with ordinary outcomes runs, and the 1,114,112-byte aggregate is exact (O6).
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from millrace.router.execution import (
    AdapterConfiguration,
    CompletionFacts,
    InvocationRequest,
    RouterError,
)
from plus_support import create_instance, native_flow, pi_instance, start_run

from millrace_plus.router import REQUIRED_EXECUTION_CONTRACT, configuration, control
from millrace_plus.router.pi import PiJsonAdapter

SOURCE = Path(__file__).resolve().parents[2] / "src" / "millrace_plus" / "router"
PUBLIC_CORE = {
    "millrace.router.execution",
    "millrace.router.control",
    "millrace.router.cli",
}
SENTINEL_ID = "0" * 32
SENTINEL_ROOT = Path("/__millrace_validation__/invocation")
PROFILE = json.dumps(
    {
        "format": "millrace-pi-json-profile/v1",
        "pi_version": "0.87.0",
        "agent_dir": "agent",
        "provider": "fixture",
        "model": "exact-model",
        "thinking": "off",
    }
).encode()


def _core_imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.split(".")[0] == "millrace"}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.split(".")[0] == "millrace":
                if node.module == "millrace.router":
                    found |= {f"millrace.router.{a.name}" for a in node.names}
                else:
                    found.add(node.module)
    return found


def test_production_imports_only_public_core_modules() -> None:
    modules = sorted(SOURCE.glob("*.py"))
    assert [p.name for p in modules] == [
        "__init__.py",
        "__main__.py",
        "claude.py",
        "cli.py",
        "codex.py",
        "codex_configuration.py",
        "configuration.py",
        "control.py",
        "pi.py",
        "preflight.py",
        "resolver.py",
    ]
    used: set[str] = set()
    for path in modules:
        imports = _core_imports(path)
        assert imports <= PUBLIC_CORE, (path.name, imports - PUBLIC_CORE)
        used |= imports
        text = path.read_text()
        assert "sqlite3" not in text and "Store" not in text, path.name
        assert 'importlib.import_module("millrace.router.' not in text.replace(
            "millrace.router.execution", ""
        )
    assert used == PUBLIC_CORE
    assert _core_imports(SOURCE / "__init__.py") == set()
    for name in ("configuration.py", "codex_configuration.py"):
        _assert_pure_module(SOURCE / name)


def _assert_pure_module(path: Path) -> None:
    pure = ast.parse(path.read_text())
    stdlib = {
        alias.name.split(".")[0]
        for node in ast.walk(pure)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(pure)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
    }
    assert not stdlib & {"os", "subprocess", "selectors", "signal", "sqlite3", "shutil"}


def test_router_package_import_is_inert_and_handshake_exact() -> None:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, millrace_plus.router as r\n"
            "print('millrace' in sys.modules, r.require_core())\n",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.split() == ["False", "millrace-router-execution/v2"]


def test_execution_v2_and_stock_reauthor() -> None:
    assert REQUIRED_EXECUTION_CONTRACT == "millrace-router-execution/v2"
    workflows = SOURCE.parent / "workflows"
    expected_nodes = {
        "check_inputs",
        "work",
        "check",
        "repair",
        "recheck",
        "goal_review",
        "diagnose",
    }
    for name in ("software-change", "document-review"):
        graph = json.loads((workflows / name / "workflow.json").read_text())
        assert graph["format"] == "millrace-router/v3"
        assert not {"start", "nodes"} & graph.keys()
        assert set(graph["planes"]) == {"main"}
        plane = graph["planes"]["main"]
        assert plane["workspace"] == "." and plane["start"] == "check_inputs"
        nodes = plane["nodes"]
        assert set(nodes) == expected_nodes
        assert nodes["check_inputs"]["on"]["READY"] == {"goto": "work"}
        assert nodes["work"]["on"]["READY"] == {"goto": "check"}
        assert nodes["check"]["on"]["REVISE"] == {"goto": "repair"}
        assert nodes["repair"]["on"]["READY"] == {"goto": "recheck"}
        for node in ("check", "recheck"):
            assert nodes[node]["on"]["ACCEPT"] == {"goto": "goal_review"}
        assert nodes["goal_review"]["on"]["ACCEPT"] == {"finish": "succeeded"}
        assert nodes["recheck"]["on"]["REVISE"] == {
            "pause": {"reason": "recheck_found_unresolved_gap", "resume": "recheck"}
        }
        assert nodes["diagnose"]["on"]["DIAGNOSED"] == {
            "pause": {
                "reason": "planning_defect_requires_user_decision",
                "resume": "diagnose",
            }
        }
        for node in nodes.values():
            assert node["on_error"]["retry"]["limit"] == 1
            assert "pause" in node["on_error"]["retry"]["exhausted"]
            assert "pause" in node["on_unknown"]
        assert graph["defaults"] == {"timeout_seconds": 1800}
        assert graph["limits"] == {"max_invocations": 16, "max_elapsed_seconds": 86400}
        profile = json.loads((workflows / name / "profiles/claude.json").read_text())
        assert profile == {
            "format": "millrace-claude-print-profile/v1",
            "claude_version": "2.1.296",
        }


@contextmanager
def _no_live_access(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"live access during pure operation: {args!r}")

    class Environment(dict[str, str]):
        def __getitem__(self, key: str) -> str:
            raise AssertionError(f"environment read: {key}")

        def get(self, key: str, default: object = None) -> object:  # type: ignore[override]
            raise AssertionError(f"environment read: {key}")

    with monkeypatch.context() as patch:
        for owner, name in (
            (builtins, "open"),
            (os, "open"),
            (os, "stat"),
            (os, "lstat"),
            (os, "listdir"),
            (os, "scandir"),
            (os, "getenv"),
            (subprocess, "Popen"),
            (Path, "exists"),
            (Path, "resolve"),
            (Path, "is_dir"),
            (Path, "is_file"),
        ):
            patch.setattr(owner, name, forbidden)
        patch.setattr(os, "environ", Environment())
        yield


def _request(
    invocation_id: str,
    root: Path,
    *,
    prompt: bytes = b"Review.",
    outcomes: tuple[str, ...] = ("READY",),
) -> InvocationRequest:
    return InvocationRequest(
        invocation_id=invocation_id,
        node_id="step",
        producing_revision="f" * 64,
        binding_id="native",
        adapter_id="pi_json",
        adapter_contract="millrace-pi-json/v1",
        executable="/nonexistent/bin/pi",
        binding_config={"profile": "pi.json"},
        node_config={"prompt": "task.txt", "model": "exact-model"},
        runtime={"thinking": "off"},
        workflow={},
        common_settings={
            "runtime_settings_version": 1,
            "requested": {},
            "effective": {},
        },
        files={"pi.json": PROFILE, "task.txt": prompt},
        host_paths={"agent": "/nonexistent/agent"},
        environment_names=("HOME",),
        outcomes=outcomes,
        result_contract="json-v1",
        project_root=Path("/nonexistent/project"),
        invocation_root=root,
        plane_id="main",
        plane={},
    )


def test_pure_operations_and_identity_independent_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PiJsonAdapter()
    config = AdapterConfiguration(
        adapter_id="pi_json",
        adapter_contract="millrace-pi-json/v1",
        bindings={"native": {"profile": "pi.json"}, "unused": {"profile": "pi.json"}},
        nodes={
            ("main", "step"): ("native", {"prompt": "task.txt", "model": "exact-model"})
        },
        runtime={"thinking": "off"},
        workflow={},
        files={"pi.json": PROFILE, "task.txt": b"Review."},
        host_paths={"agent": "/nonexistent/agent"},
        environment_names=("HOME",),
        planes={"main": {}},
    )
    actual_root = Path("/state/instances/i/invocations") / ("a" * 32)
    with _no_live_access(monkeypatch):
        adapter.validate_configuration(config)
        sentinel = adapter.plan(_request(SENTINEL_ID, SENTINEL_ROOT))
        actual = adapter.plan(_request("a" * 32, actual_root))
        again = adapter.plan(_request("a" * 32, actual_root))
    assert actual == again
    assert sentinel.settings == actual.settings
    assert actual.settings["provenance"] == {
        "provider": {"source": "profile", "policy": None},
        "model": {"source": "node", "policy": None},
        # Retained precedence: the profile shadows the runtime default.
        "thinking": {"source": "profile", "policy": None},
    }
    assert sentinel.argv[-1] == f"@{SENTINEL_ROOT}/pi-prompt.txt"
    assert actual.argv[-1] == f"@{actual_root}/pi-prompt.txt"
    assert sentinel.generated_controls == actual.generated_controls
    assert actual.environment == {"PI_CODING_AGENT_DIR": "/nonexistent/agent"}
    assert actual.details["prompt_path"] == f"{actual_root}/pi-prompt.txt"
    raw = actual.generated_controls["pi-prompt.txt"]
    assert actual.details["prompt_sha256"] == hashlib.sha256(raw).hexdigest()

    evidence = configuration.preflight_evidence(
        "a" * 32, "invalid_host_path", observed=None
    )
    facts = CompletionFacts(
        "a" * 32,
        None,
        b"",
        b"",
        "execution_failure",
        "stopped",
        "invalid_host_path",
        None,
        evidence,
        "adapter",
    )
    with _no_live_access(monkeypatch):
        adapter.validate_completion(actual, facts)
        for changed in (
            {"pid": 4242},  # a preflight never has a main process
            {"evidence": None},  # adapter-origin results always carry evidence
            {"invocation_id": "b" * 32},
            {"stdout": b"late text"},
            {"evidence": dict(evidence, session_id="s" * 4097)},
            {"evidence": dict(evidence, invocation_id="b" * 32)},
        ):
            values = {**facts.__dict__, **changed}
            with pytest.raises(RouterError) as error:
                adapter.validate_completion(actual, CompletionFacts(**values))
            assert error.value.code == "completion_integrity", changed


@pytest.mark.parametrize(
    "case,code",
    [
        ("unsafe_prompt_path", "invalid_pi_prompt_path"),
        ("missing_agent", "missing_host_path"),
        ("agent_file", "invalid_host_path"),
        ("agent_alias", "invalid_host_path"),
        ("missing_loader", "missing_host_path"),
        ("loader_dir", "invalid_host_path"),
    ],
)
def test_local_prerequisites_refuse_before_any_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str, code: str
) -> None:
    root = tmp_path / ("unsafe&root" if case == "unsafe_prompt_path" else "root")
    agent = tmp_path / "agent"
    agent.mkdir()
    loader = tmp_path / "loader.js"
    loader.write_text("")
    paths = {"agent": str(agent)}
    profile = json.loads(PROFILE)
    if case == "missing_agent":
        paths["agent"] = str(tmp_path / "absent")
    elif case == "agent_file":
        paths["agent"] = str(loader)
    elif case == "agent_alias":
        (tmp_path / "alias").symlink_to(agent)
        paths["agent"] = str(tmp_path / "alias")
    elif case in {"missing_loader", "loader_dir"}:
        profile["loader"] = "loader"
        paths["loader"] = (
            str(tmp_path / "absent.js") if case == "missing_loader" else str(agent)
        )
    request = _request("a" * 32, root)
    request = InvocationRequest(
        **{
            **request.__dict__,
            "files": {"pi.json": json.dumps(profile).encode(), "task.txt": b"Review."},
            "host_paths": paths,
        }
    )
    adapter = PiJsonAdapter()
    plan = adapter.plan(request)

    def no_process(*args: object, **kwargs: object) -> object:
        raise AssertionError("a local refusal must not spawn a probe process")

    started: list[int] = []
    monkeypatch.setattr(subprocess, "Popen", no_process)
    result = adapter.invoke(
        plan,
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        deadline=1e12,
        poll=lambda: False,
        on_started=started.append,
    )
    assert started == []
    assert (result.classification, result.writer_state) == (
        "execution_failure",
        "stopped",
    )
    assert (result.stdout, result.diagnostic, result.exit_code) == (b"", code, None)
    assert result.adapter_evidence == configuration.preflight_evidence(
        "a" * 32, code, observed=None
    )
    adapter.validate_completion(
        plan,
        CompletionFacts(
            "a" * 32,
            None,
            result.stdout,
            result.stderr,
            result.classification,
            result.writer_state,
            result.diagnostic,
            result.exit_code,
            result.adapter_evidence,
            "adapter",
        ),
    )


def test_full_prompt_and_generated_aggregate_bounds(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    task = workflow.parent / "task.txt"
    task.write_bytes(b"a" * configuration.CONTROL_LIMIT)
    instance = pi_instance(workflow, project, home, agent)
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded" and run["count"] == 1
    generated = run["invocations"][0]["launch"]["generated_controls"]["pi-prompt.txt"]
    assert configuration.CONTROL_LIMIT < generated["size"] < 1114112

    task.write_bytes(b"a" * (configuration.CONTROL_LIMIT + 1))
    with pytest.raises(RouterError) as error:
        create_instance(
            workflow,
            project,
            state_home=home.parent / "second",
            host_paths={"agent": str(agent)},
        )
    assert error.value.code == "invalid_pi_invocation"


@pytest.mark.parametrize("extra,valid", [(0, True), (1, False)])
def test_generated_prompt_aggregate_is_exact(
    single_flow: tuple[Path, Path, Path], extra: int, valid: bool
) -> None:
    """New Core limit: 1 MiB prompt + 64 KiB wrapper; large outcome lists refuse."""
    workflow, project, home, agent = native_flow(single_flow)
    wrapper = 1114112 - configuration.CONTROL_LIMIT + extra
    base = configuration.prompt(b"", ())
    names = []
    while len(configuration.prompt(b"", tuple(names))) + 67 <= wrapper:
        names.append(f"O{len(names):05d}" + "x" * 58)
    remaining = wrapper - len(configuration.prompt(b"", tuple(names)))
    assert len(base) < wrapper and 0 <= remaining < 67
    if remaining:
        # One more identifier: `,"` + name + `"` adds len(name) + 3 bytes.
        assert remaining >= 4
        names.append("Q" + "q" * (remaining - 4))
    outcomes = tuple(names)
    assert len(configuration.prompt(b"", outcomes)) == wrapper
    graph = json.loads(workflow.read_text())
    node = graph["planes"]["main"]["nodes"]["draft"]
    node["outcomes"] = list(outcomes)
    node["on"] = {name: {"finish": "succeeded"} for name in outcomes}
    workflow.write_text(json.dumps(graph))
    (workflow.parent / "task.txt").write_bytes(b"a" * configuration.CONTROL_LIMIT)
    total = len(configuration.prompt(b"a" * configuration.CONTROL_LIMIT, outcomes))
    assert total == 1114112 + extra
    if valid:
        instance = pi_instance(workflow, project, home, agent)
        assert control.validate_revision(instance, state_home=home)["valid"] is True
    else:
        with pytest.raises(RouterError) as error:
            pi_instance(workflow, project, home, agent)
        assert error.value.code == "invalid_launch_plan"
        assert not (home / "instances").exists() or not any(
            (home / "instances").iterdir()
        )


def test_agent_directory_environment_collision_refuses(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    with pytest.raises(RouterError) as error:
        create_instance(
            workflow,
            project,
            state_home=home,
            host_paths={"agent": str(agent)},
            env_allowlist=("PI_CODING_AGENT_DIR",),
        )
    assert error.value.code == "invalid_adapter_settings"


@pytest.mark.parametrize("tamper", ["model", "text_hash", "oversized_session"])
def test_rejected_pi_evidence_takes_uncertainty_path(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    """Evidence refused by the Pi validator is never committed as a completion."""
    from dataclasses import replace

    from plus_support import new_id

    import millrace_plus.router.pi as pi

    workflow, project, home, agent = native_flow(single_flow)
    instance = pi_instance(workflow, project, home, agent)
    real = pi.invoke
    first = [True]

    def invoke(*args: object, **kwargs: object):
        result = real(*args, **kwargs)
        if not (first and first.pop()):
            return result
        evidence = dict(result.adapter_evidence or {})
        if tamper == "model":
            evidence["final"] = dict(evidence["final"], model="other-model")
        elif tamper == "text_hash":
            evidence["final"] = dict(evidence["final"], text_sha256="0" * 64)
        else:
            evidence["session_id"] = "s" * 5000
        return replace(result, adapter_evidence=evidence)

    monkeypatch.setattr(pi, "invoke", invoke)
    paused = start_run(instance, state_home=home)
    assert (paused["status"], paused["pause_reason"]) == ("paused", "writer_unknown")
    assert paused["count"] == 1 and paused["completions"] == []
    invocation = paused["invocations"][0]
    assert invocation["status"] == "uncertain"
    assert invocation["adapter_evidence"] is None and invocation["origin"] is None
    assert (project / ".millrace-router-owner/owner.json").exists()
    run = str(paused["id"])
    assert control.inspect_run(instance, run, state_home=home)["count"] == 1
    receipt = control.reconcile_invocation(
        instance,
        run,
        invocation_id=str(invocation["id"]),
        reason="fixture writer reaped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        plane_id="main",
    )
    assert receipt["status"] == "applied", receipt
    reconciled = control.inspect_run(instance, run, state_home=home)
    assert reconciled["completions"][0]["transport"] == "interrupted"
    assert reconciled["invocations"][0]["origin"] == "core_interruption"
    assert reconciled["transitions"] == []
    resumed = control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=reconciled["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    final = control.inspect_run(instance, run, state_home=home)
    # single_flow routes interrupted work through on_error (pause), never success.
    assert final["count"] == 1 and final["status"] == "paused"
    assert final["planes"]["main"]["pause_reason"] == "execution_error"
    assert final["pause_reason"] == "no_runnable_planes"
    assert final["run_gate"] is None
    assert all(row["transport"] != "completed" for row in final["completions"])
