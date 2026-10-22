"""Execution/v2 plane contracts and inert transports; no native tools/providers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from millrace.router.execution import (
    AdapterConfiguration,
    InvocationRequest,
    RouterError,
    SeamInput,
)


def test_execution_v2_contract_guard() -> None:
    from millrace_plus.router import REQUIRED_EXECUTION_CONTRACT, require_core

    assert REQUIRED_EXECUTION_CONTRACT == "millrace-router-execution/v2"
    assert require_core() == "millrace-router-execution/v2"


def _request(root: Path, adapter_id: str, plane_id: str) -> InvocationRequest:
    profiles: dict[str, dict[str, object]] = {
        "pi_json": {
            "format": "millrace-pi-json-profile/v1",
            "pi_version": "0.87.0",
            "agent_dir": "native",
            "provider": "fixture",
            "model": "profile",
            "thinking": "off",
        },
        "codex_exec": {
            "format": "millrace-codex-exec-profile/v1",
            "codex_version": "0.163.0",
            "codex_home": "native",
            "auth_mode": "chatgpt",
            "sandbox": "workspace-write",
            "model": "profile",
            "reasoning_effort": "low",
        },
        "claude_print": {
            "format": "millrace-claude-print-profile/v1",
            "claude_version": "2.1.296",
            "model": "profile",
            "reasoning_effort": "low",
        },
    }
    contracts = {
        "pi_json": "millrace-pi-json/v1",
        "codex_exec": "millrace-codex-exec/v1",
        "claude_print": "millrace-claude-print/v1",
    }
    return InvocationRequest(
        invocation_id="1" * 32,
        plane_id=plane_id,
        node_id="step",
        producing_revision="a" * 64,
        binding_id="native",
        adapter_id=adapter_id,
        adapter_contract=contracts[adapter_id],
        executable="/not/a/native/executable",
        binding_config={"profile": "profile.json"},
        node_config={"prompt": "task.txt"},
        runtime={"model": "runtime"},
        workflow={"model": "profile"},
        plane=_plane_settings(adapter_id, plane_id),
        common_settings={"plane_id": plane_id, "producing_revision": "a" * 64},
        files={
            "profile.json": json.dumps(profiles[adapter_id]).encode(),
            "task.txt": b"Review this plane.",
        },
        host_paths={"native": str(root / "absent-native-home")},
        environment_names=(),
        outcomes=("READY",),
        result_contract="json-v1",
        project_root=root / plane_id,
        invocation_root=root / "invocations" / plane_id,
        seam_input=None,
    )


def _plane_settings(adapter_id: str, plane_id: str) -> dict[str, object]:
    result: dict[str, object] = {"model": plane_id}
    if adapter_id == "pi_json":
        result.update(
            provider="provider-" + plane_id,
            thinking="medium" if plane_id == "alpha" else "high",
        )
    else:
        result["reasoning_effort"] = "medium" if plane_id == "alpha" else "high"
    return result


def _config(request: InvocationRequest) -> AdapterConfiguration:
    return AdapterConfiguration(
        adapter_id=request.adapter_id,
        adapter_contract=request.adapter_contract,
        bindings={"native": request.binding_config},
        nodes={
            ("alpha", "step"): ("native", request.node_config),
            ("beta", "step"): ("native", request.node_config),
        },
        runtime=request.runtime,
        workflow=request.workflow,
        planes={
            name: _plane_settings(request.adapter_id, name)
            for name in ("alpha", "beta")
        },
        files=request.files,
        host_paths=request.host_paths,
        environment_names=request.environment_names,
    )


@pytest.mark.parametrize("adapter_id", ["pi_json", "codex_exec", "claude_print"])
def test_all_native_plane_layers_and_profile_conflicts(
    tmp_path: Path, adapter_id: str
) -> None:
    from millrace_plus.router.resolver import resolve_adapter

    alpha = _request(tmp_path, adapter_id, "alpha")
    beta = _request(tmp_path, adapter_id, "beta")
    adapter = resolve_adapter(adapter_id, alpha.adapter_contract)
    adapter.validate_configuration(_config(alpha))
    for request in (alpha, beta, alpha):
        plan = adapter.plan(request)
        assert plan.settings["effective"] == request.plane
        assert plan.settings["requested"] == request.plane
        assert plan.settings["provenance"] == {
            key: {"source": "plane", "policy": None} for key in request.plane
        }
        assert plan.argv[plan.argv.index("--model") + 1] == request.plane_id
        node = replace(request, node_config={"prompt": "task.txt", "model": "node"})
        node_plan = adapter.plan(node)
        assert node_plan.settings["effective"]["model"] == "node"
        assert node_plan.settings["provenance"]["model"]["source"] == "node"
    conflicting = replace(
        alpha,
        workflow={"model": "conflict"},
        node_config={"prompt": "task.txt", "model": "node"},
    )
    if adapter_id in {"pi_json", "codex_exec"}:
        code = (
            "conflicting_pi_setting"
            if adapter_id == "pi_json"
            else "conflicting_codex_setting"
        )
        for operation in (
            lambda: adapter.validate_configuration(_config(conflicting)),
            lambda: adapter.plan(conflicting),
        ):
            with pytest.raises(RouterError, match=code):
                operation()
    else:
        assert adapter.plan(conflicting).settings["effective"]["model"] == "node"
        profile = json.loads(alpha.files["profile.json"])
        profile.pop("model")
        profile.pop("reasoning_effort")
        omitted = replace(
            alpha,
            runtime={},
            workflow={},
            plane={},
            files={**alpha.files, "profile.json": json.dumps(profile).encode()},
        )
        plan = adapter.plan(omitted)
        assert plan.settings["effective"] == {}
        assert plan.settings["provenance"] == {}
        assert "--model" not in plan.argv and "--effort" not in plan.argv


@pytest.mark.parametrize("adapter_id", ["pi_json", "codex_exec", "claude_print"])
def test_invalid_selected_plane_cannot_be_hidden_by_node_override(
    tmp_path: Path, adapter_id: str
) -> None:
    from millrace_plus.router.resolver import resolve_adapter

    request = replace(
        _request(tmp_path, adapter_id, "alpha"),
        node_config={"prompt": "task.txt", "model": "valid-node-model"},
    )
    adapter = resolve_adapter(adapter_id, request.adapter_contract)
    config = _config(request)
    adapter.validate_configuration(config)
    assert adapter.plan(request).settings["effective"]["model"] == "valid-node-model"

    invalid = replace(request, plane={"model": ""})
    with pytest.raises(RouterError):
        adapter.validate_configuration(
            replace(config, planes={**config.planes, "alpha": invalid.plane})
        )
    with pytest.raises(RouterError):
        adapter.plan(invalid)


@pytest.mark.parametrize("adapter_id", ["pi_json", "codex_exec", "claude_print"])
def test_public_graph_selects_native_plane_namespace_and_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_id: str
) -> None:
    from millrace.router.graph import capture, resolve_nodes

    from millrace_plus.router.resolver import resolve_adapter

    request = _request(tmp_path, adapter_id, "alpha")
    adapter = resolve_adapter(adapter_id, request.adapter_contract)
    project = tmp_path / "project"
    project.mkdir()
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    for key, raw in request.files.items():
        (workflow / key).write_bytes(raw)

    def defaults(value: object) -> dict[str, object]:
        return {
            "adapter_settings": {
                adapter_id: {"contract": request.adapter_contract, "config": value}
            }
        }

    graph: dict[str, Any] = {
        "format": "millrace-router/v3",
        "limits": {
            "max_invocations": 4,
            "max_elapsed_seconds": 60,
            "max_parallel_invocations": 2,
        },
        "bindings": {
            "native": {
                "adapter": adapter_id,
                "contract": request.adapter_contract,
                "executable": sys.executable,
                "config": request.binding_config,
            }
        },
        "control_files": list(request.files),
        "defaults": defaults(request.workflow),
        "planes": {},
    }
    for name in ("alpha", "beta"):
        (project / name).mkdir()
        graph["planes"][name] = {
            "start": "step",
            "workspace": name,
            "defaults": defaults(_plane_settings(adapter_id, name)),
            "nodes": {
                "step": {
                    "invoke": {"binding": "native", "config": request.node_config},
                    "result": "json-v1",
                    "outcomes": ["READY"],
                    "on": {"READY": {"finish": "succeeded"}},
                    "on_error": {"finish": "failed"},
                    "on_unknown": {"finish": "failed"},
                }
            },
        }
    path = workflow / "workflow.json"
    path.write_text(json.dumps(graph))
    revision = capture(path, project, host_paths=dict(request.host_paths))
    selected: list[InvocationRequest] = []
    original = adapter.plan

    def plan(value: InvocationRequest) -> Any:
        selected.append(value)
        return original(value)

    monkeypatch.setattr(adapter, "plan", plan)
    resolved = resolve_nodes(
        revision,
        settings_context={
            "state_version": 1,
            "runtime_defaults": defaults(request.runtime),
            "operator_policy": {},
            "host_paths": dict(request.host_paths),
        },
        adapter_resolver=lambda name, contract: adapter,
        project_root=project,
    )
    assert set(resolved) == {"alpha", "beta"}
    for value in selected:
        assert value.node_id == "step"
        assert value.project_root == project / value.plane_id
        assert value.plane == _plane_settings(adapter_id, value.plane_id)
        assert value.seam_input is None
        settings = resolved[value.plane_id][value.node_id]
        assert settings["adapter"]["effective"] == value.plane
        assert settings["common"]["plane_id"] == value.plane_id
        assert settings["common"]["producing_revision"] == revision.revision_id
    assert [value.plane_id for value in selected] == ["alpha", "beta"]


@pytest.mark.parametrize("adapter_id", ["pi_json", "codex_exec", "claude_print"])
def test_unused_plane_namespaces_are_validated_without_live_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_id: str
) -> None:
    from millrace_plus.router.resolver import resolve_adapter

    request = _request(tmp_path, adapter_id, "alpha")
    adapter = resolve_adapter(adapter_id, request.adapter_contract)
    config = _config(request)
    invalid = replace(config, planes={**config.planes, "unused": {"model": ""}})

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("plane validation/planning probed live state")

    with monkeypatch.context() as patch:
        for owner, name in (
            (Path, "exists"),
            (Path, "stat"),
            (Path, "resolve"),
            (Path, "read_bytes"),
            (Path, "open"),
            (os, "getenv"),
            (os, "access"),
            (os, "stat"),
            (os._Environ, "__getitem__"),
            (subprocess, "Popen"),
            (subprocess, "run"),
        ):
            patch.setattr(owner, name, forbidden)
        adapter.validate_configuration(config)
        adapter.plan(request)
        with pytest.raises(RouterError):
            adapter.validate_configuration(invalid)
        with pytest.raises(RouterError):
            adapter.plan(replace(request, plane={"model": ""}))


def test_validation_context_run_version_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace_plus.router import control

    expected = {
        "valid": False,
        "diagnostics": [{"code": "incompatible_revision"}],
        "validation_context": {"run_state_version": 7},
        "resolved_nodes": {"alpha": {"step": {}}, "beta": {"step": {}}},
    }
    calls: list[tuple[str, dict[str, object]]] = []

    def validate(instance_id: str, **kwargs: object) -> dict[str, object]:
        calls.append((instance_id, kwargs))
        return expected

    monkeypatch.setattr(control._core, "validate_revision", validate)
    draft = tmp_path / "draft.json"
    result = control.validate_revision(
        "instance", state_home=tmp_path, workflow_file=draft, run_id="run"
    )
    assert result is expected
    assert result["validation_context"]["run_state_version"] == 7
    assert calls == [
        (
            "instance",
            {
                "state_home": tmp_path,
                "workflow_file": draft,
                "run_id": "run",
                "adapter_resolver": control.resolve_adapter,
            },
        )
    ]


@pytest.mark.parametrize("adapter_id", ["pi_json", "codex_exec", "claude_print"])
def test_same_native_adapter_concurrent_transports_keep_plane_roots_and_seam_input(
    tmp_path: Path, adapter_id: str
) -> None:
    """Adapter transport proof; Core reservation/injection is proved separately."""
    from millrace_plus.router.resolver import resolve_adapter

    rendezvous = tmp_path / "rendezvous"
    rendezvous.mkdir()
    adapter = resolve_adapter(
        adapter_id, _request(tmp_path, adapter_id, "alpha").adapter_contract
    )
    prepared: list[tuple[InvocationRequest, Any]] = []
    for plane in ("alpha", "beta"):
        root = tmp_path / plane
        root.mkdir()
        project = root / "project"
        project.mkdir()
        native = root / "native"
        native.mkdir()
        (native / "auth.json").write_text("{}")
        invocation = root / "invocation"
        invocation.mkdir()
        content = json.dumps({"plane": plane, "producer": "retained-result"}).encode()
        seam = invocation / "seam-input.json"
        seam.write_bytes(content)
        request = replace(
            _request(root, adapter_id, plane),
            invocation_id=("a" if plane == "alpha" else "b") * 32,
            project_root=project,
            invocation_root=invocation,
            host_paths={"native": str(native)},
            seam_input=SeamInput(
                path=seam,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                seam_id="input-" + plane,
                sequence=1,
                producer_invocation_id="c" * 32,
            ),
        )
        before = (
            "import os,time,json\nfrom pathlib import Path\n"
            f"assert Path.cwd() == Path({str(project)!r})\n"
            "observed = Path(os.environ['MILLRACE_SEAM_INPUT']).read_bytes()\n"
            f"assert observed == {content!r}\n"
            f"Path({str(rendezvous / plane)!r}).touch()\n"
            "until=time.monotonic()+5\n"
            f"shared = Path({str(rendezvous)!r})\n"
            "while not all((shared/name).exists() for name in ('alpha','beta')):\n"
            " if time.monotonic()>until: raise RuntimeError('peer missing')\n"
            " time.sleep(.01)\n"
        )
        if adapter_id == "pi_json":
            from plus_support import _assistant, _events, _fake, _wire

            message = _assistant(['{"outcome":"READY","summary":"done","refs":[]}'])
            message.update(model=plane, provider="provider-" + plane)
            executable = _fake(root, _wire(_events(project, [message])), before=before)
        elif adapter_id == "codex_exec":
            from codex_support import fake as codex_fake

            executable = codex_fake(root, before=before)
        else:
            from claude_support import fake as claude_fake

            executable = claude_fake(root, before=before)
        request = replace(request, executable=str(executable))
        plan = adapter.plan(request)
        for name, raw in plan.generated_controls.items():
            (invocation / name).write_bytes(raw)
        prepared.append((request, plan))

    def invoke(item: tuple[InvocationRequest, Any]) -> Any:
        request, plan = item
        assert request.seam_input is not None
        return adapter.invoke(
            plan,
            cwd=request.project_root,
            env={
                "PATH": "/usr/bin:/bin",
                **plan.environment,
                "MILLRACE_SEAM_INPUT": str(request.seam_input.path),
            },
            deadline=time.time() + 10,
            poll=lambda: False,
            on_started=lambda pid: None,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, prepared))
    assert {p.name for p in rendezvous.iterdir()} == {"alpha", "beta"}
    for result in results:
        assert result.classification == "completed", result
        assert result.writer_state == "stopped"
        assert json.loads(result.stdout)["outcome"] == "READY"


@pytest.mark.parametrize("adapter_id", ["pi_json", "codex_exec", "claude_print"])
def test_native_consumer_reads_core_reserved_seam_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_id: str
) -> None:
    """Public runtime integration, to run after the paired Core handoff."""
    from millrace_plus.router import control
    from millrace_plus.router.resolver import resolve_adapter

    workflow = tmp_path / "workflow"
    workflow.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    for name in ("producer", "consumer", "native"):
        (project / name).mkdir()
    (project / "producer/payload.txt").write_text("producer-owned payload\n")
    (project / "native/auth.json").write_text("{}")
    request = _request(tmp_path, adapter_id, "checking")
    for name, raw in request.files.items():
        (workflow / name).write_bytes(raw)
    (workflow / "produce.py").write_text(
        'print(\'{"outcome":"READY","summary":"retained producer",'
        '"refs":["payload.txt"]}\')\n'
    )
    before = (
        "import os,json\nfrom pathlib import Path\n"
        "path=Path(os.environ['MILLRACE_SEAM_INPUT'])\n"
        "data=json.loads(path.read_bytes())\n"
        "assert data['format']=='millrace-seam-input/v1'\n"
        "assert data['consumer_plane_id']=='checking'\n"
        "assert data['consumer_node_id']=='step'\n"
        "assert data['producer_plane_id']=='producing'\n"
        "assert data['producer_node_id']=='step'\n"
        "assert data['seam_id']=='handoff' and data['sequence']==1\n"
        "assert data['result']['outcome']=='READY'\n"
        "assert data['result']['summary']=='retained producer'\n"
        "assert data['result']['refs']==['payload.txt']\n"
        "assert (Path(data['producer_workspace'])/'payload.txt').read_text()"
        "=='producer-owned payload\\n'\n"
        f"assert Path.cwd()==Path({str(project / 'consumer')!r})\n"
        "Path('consumed.json').write_text(json.dumps(data))\n"
    )
    if adapter_id == "pi_json":
        from plus_support import _assistant, _events, _fake, _wire

        message = _assistant(['{"outcome":"READY","summary":"consumed","refs":[]}'])
        message.update(model="checking", provider="provider-checking")
        executable = _fake(
            tmp_path, _wire(_events(project / "consumer", [message])), before=before
        )
    elif adapter_id == "codex_exec":
        from codex_support import fake as codex_fake

        executable = codex_fake(tmp_path, before=before)
    else:
        from claude_support import fake as claude_fake

        executable = claude_fake(tmp_path, before=before)

    def node(binding: str, config: object) -> dict[str, object]:
        return {
            "invoke": {"binding": binding, "config": config},
            "result": "json-v1",
            "outcomes": ["READY"],
            "on": {"READY": {"finish": "succeeded"}},
            "on_error": {"finish": "failed"},
            "on_unknown": {"finish": "failed"},
        }

    graph = {
        "format": "millrace-router/v3",
        "limits": {
            "max_invocations": 2,
            "max_elapsed_seconds": 30,
            "max_parallel_invocations": 2,
        },
        "defaults": {"timeout_seconds": 10},
        "bindings": {
            "command": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": sys.executable,
                "config": {},
            },
            "native": {
                "adapter": adapter_id,
                "contract": request.adapter_contract,
                "executable": str(executable),
                "config": request.binding_config,
            },
        },
        "control_files": [*request.files, "produce.py"],
        "planes": {
            "producing": {
                "start": "step",
                "workspace": "producer",
                "nodes": {
                    "step": node("command", {"script": "produce.py", "args": []})
                },
            },
            "checking": {
                "start": "step",
                "workspace": "consumer",
                "defaults": {
                    "adapter_settings": {
                        adapter_id: {
                            "contract": request.adapter_contract,
                            "config": _plane_settings(adapter_id, "checking"),
                        }
                    }
                },
                "nodes": {"step": node("native", request.node_config)},
            },
        },
        "seams": {
            "handoff": {
                "producer": {"plane": "producing", "node": "step", "outcome": "READY"},
                "consumer": {"plane": "checking", "node": "step"},
            }
        },
    }
    path = workflow / "workflow.json"
    path.write_text(json.dumps(graph))
    adapter = resolve_adapter(adapter_id, request.adapter_contract)

    def resolve(name: str, contract: str) -> Any:
        assert (name, contract) == (adapter_id, request.adapter_contract)
        return adapter

    monkeypatch.setattr(control, "resolve_adapter", resolve)
    home = tmp_path / "state"
    created = control.create_instance(
        path,
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        host_paths={"native": str(project / "native")},
    )
    assert created["status"] == "applied", created
    instance = str(created["instance_id"])
    assert executable.resolve().is_relative_to(tmp_path.resolve())
    assert executable.name in {"fake-pi", "fake-codex", "fake-claude"}
    authored = json.loads(path.read_text())
    assert [
        binding["executable"]
        for binding in authored["bindings"].values()
        if binding["adapter"] != "command"
    ] == [str(executable)]
    started = control.start_run(
        instance,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=1,
    )
    view = control.inspect_run(instance, str(started["run_id"]), state_home=home)
    assert view["status"] == "succeeded", view
    assert view["count"] == 2
    assert [row["plane_id"] for row in view["invocations"]] == ["producing", "checking"]
    consumed = json.loads((project / "consumer/consumed.json").read_text())
    assert consumed["producer_invocation_id"] == view["invocations"][0]["id"]
    assert not (project / ".millrace-router-owner/owner.json").exists()
