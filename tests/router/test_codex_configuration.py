"""P4b selected configuration authority, purity and host-local environment proof."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from codex_support import ADAPTER_CONTRACT, ADAPTER_ID
from millrace.router.execution import (
    AdapterConfiguration,
    InvocationRequest,
    RouterError,
)


def request(tmp: Path, *, prompt: bytes = b"Review.") -> InvocationRequest:
    profile = {
        "format": "millrace-codex-exec-profile/v1",
        "codex_version": "0.163.0",
        "codex_home": "native",
        "auth_mode": "chatgpt",
        "sandbox": "workspace-write",
        "model": "exact-model",
        "reasoning_effort": "low",
    }
    return InvocationRequest(
        invocation_id="1" * 32,
        node_id="draft",
        producing_revision="revision",
        binding_id="native",
        adapter_id=ADAPTER_ID,
        adapter_contract=ADAPTER_CONTRACT,
        executable="/nonexistent/codex",
        binding_config={"profile": "codex.json"},
        node_config={"prompt": "task.txt"},
        runtime={},
        workflow={},
        common_settings={},
        files={"codex.json": json.dumps(profile).encode(), "task.txt": prompt},
        host_paths={"native": str(tmp / "native")},
        environment_names=(),
        outcomes=("READY",),
        result_contract="json-v1",
        project_root=tmp,
        invocation_root=tmp / "invocation",
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


@pytest.mark.parametrize("effort", ["medium", "max"])
def test_luna_literal_model_and_effort_argv(tmp_path: Path, effort: str) -> None:
    from millrace_plus.router import codex_configuration as c

    req = request(tmp_path)
    profile = json.loads(req.files["codex.json"])
    profile.update(model="gpt-6-luna", reasoning_effort=effort)
    req = replace(req, files={**req.files, "codex.json": json.dumps(profile).encode()})
    c.validate_configuration(config(req))
    plan = c.plan(req)
    assert plan.argv[plan.argv.index("--model") + 1] == "gpt-6-luna"
    assert f'model_reasoning_effort="{effort}"' in plan.argv
    assert plan.settings["effective"] == {
        "model": "gpt-6-luna",
        "reasoning_effort": effort,
    }
    assert plan.details["expected_version"] == "0.163.0"


def test_historical_profile_refusal_is_pure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace_plus.router import codex_configuration as c

    req = request(tmp_path)
    profile = json.loads(req.files["codex.json"])
    profile["codex_version"] = "0.153.2"
    req = replace(req, files={**req.files, "codex.json": json.dumps(profile).encode()})
    cfg = config(req)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("historical profile validation probed live state")

    with monkeypatch.context() as patch:
        for name in ("exists", "stat", "resolve", "read_bytes", "open"):
            patch.setattr(Path, name, forbidden)
        for name in ("getenv", "access", "stat"):
            patch.setattr(os, name, forbidden)
        patch.setattr(os._Environ, "__getitem__", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        for operation in (lambda: c.validate_configuration(cfg), lambda: c.plan(req)):
            with pytest.raises(RouterError, match="unsupported_codex_version"):
                operation()


def test_resolver_exact_pairs_and_pi_compatibility() -> None:
    from millrace_plus.router.pi import PiJsonAdapter
    from millrace_plus.router.pi import resolve_adapter as old
    from millrace_plus.router.resolver import resolve_adapter

    assert isinstance(resolve_adapter("pi_json", "millrace-pi-json/v1"), PiJsonAdapter)
    assert isinstance(old("pi_json", "millrace-pi-json/v1"), PiJsonAdapter)
    assert resolve_adapter(ADAPTER_ID, ADAPTER_CONTRACT).adapter_id == ADAPTER_ID
    for pair in [
        (ADAPTER_ID, "wrong"),
        ("command", "millrace-command/v1"),
        ("unknown", ADAPTER_CONTRACT),
    ]:
        with pytest.raises(RouterError) as caught:
            resolve_adapter(*pair)
        assert caught.value.code == "unsupported_adapter_contract"


@pytest.mark.parametrize(
    "variant",
    [
        "good",
        "unknown",
        "version",
        "format",
        "duplicate",
        "missing",
        "nul",
        "utf8",
        "unused_runtime_namespace_codex_home_collision",
    ],
)
def test_codex_capture_and_selected_validation_parity(
    tmp_path: Path, variant: str
) -> None:
    from millrace_plus.router import codex_configuration as c

    req = request(tmp_path)
    files = dict(req.files)
    if variant in {"unknown", "version", "format"}:
        profile: dict[str, Any] = json.loads(files["codex.json"])
        profile[
            {"unknown": "extra", "version": "codex_version", "format": "format"}[
                variant
            ]
        ] = "bad"
        files["codex.json"] = json.dumps(profile).encode()
    elif variant == "duplicate":
        files["codex.json"] = b'{"format":"a","format":"b"}'
    elif variant == "missing":
        del files["task.txt"]
    elif variant == "nul":
        files["task.txt"] = b"bad\0"
    elif variant == "utf8":
        files["task.txt"] = b"\xff"
    req = replace(req, files=files)
    cfg = config(req)
    if variant == "unused_runtime_namespace_codex_home_collision":
        cfg = replace(
            cfg, bindings={}, nodes={}, files={}, environment_names=("CODEX_HOME",)
        )
    if variant == "good":
        c.validate_configuration(cfg)
        c.plan(req)
    else:
        with pytest.raises(RouterError) as caught:
            c.validate_configuration(cfg)
        if variant in {"format", "version"}:
            assert caught.value.code == (
                "invalid_codex_profile"
                if variant == "format"
                else "unsupported_codex_version"
            )
        if variant != "unused_runtime_namespace_codex_home_collision":
            with pytest.raises(RouterError):
                c.plan(req)


def test_settings_precedence_pins_and_timeout_parity(
    tmp_path: Path, single_flow: tuple[Path, Path, Path]
) -> None:
    from millrace_plus.router import codex_configuration as c

    req = request(tmp_path)
    req = replace(
        req,
        runtime={"model": "runtime"},
        node_config={"prompt": "task.txt", "model": "node"},
    )
    plan = c.plan(req)
    assert plan.settings["effective"] == {"model": "node", "reasoning_effort": "low"}
    assert plan.settings["provenance"]["model"]["source"] == "node"
    with pytest.raises(RouterError):
        c.plan(replace(req, runtime={"reasoning_effort": "invented"}))
    with pytest.raises(RouterError, match="conflicting_codex_setting"):
        c.plan(replace(req, workflow={"model": "conflict"}))

    from codex_support import codex_flow, codex_instance
    from plus_support import new_id, start_run

    from millrace_plus.router import control

    workflow, project, home, native = codex_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["invoke"].pop("timeout_seconds")
    graph.setdefault("defaults", {}).pop("timeout_seconds", None)
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native)
    fallback = control.validate_revision(instance, state_home=home)["resolved_nodes"][
        "main"
    ]["draft"]["common"]
    assert fallback["effective"]["timeout_seconds"] == 1800
    assert fallback["warnings"][0]["code"] == "timeout_defaulted"
    updated = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        settings={
            "runtime_defaults": {"timeout_seconds": 4},
            "operator_policy": {"binding_pin": "codex", "timeout_ceiling_seconds": 2},
        },
    )
    assert updated["status"] == "applied", updated
    validation = control.validate_revision(instance, state_home=home)
    resolved = validation["resolved_nodes"]["main"]["draft"]["common"]
    assert resolved["producing_revision"] == validation["revision_id"]
    assert resolved["effective"] == {"binding": "codex", "timeout_seconds": 2}
    assert resolved["warnings"] == []
    refused = control.update_instance_config(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=2,
        settings={"operator_policy": {"binding_pin": "python"}},
    )
    assert (refused["status"], refused["code"]) == ("refused", "binding_pin_conflict")
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    invocation = run["invocations"][0]
    launched = invocation["launch_settings"]["common"]
    assert (
        launched["producing_revision"]
        == invocation["revision_id"]
        == run["startup_revision_id"]
        == run["revision_id"]
    )
    assert {k: v for k, v in launched.items() if k != "producing_revision"} == {
        k: v for k, v in resolved.items() if k != "producing_revision"
    }
    assert (
        control.inspect_run(instance, run["id"], state_home=home)["status"]
        == "succeeded"
    )


def test_pure_planning_and_historical_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace_plus.router import codex_configuration as c

    req = request(tmp_path, prompt=b"\xef\xbb\xbfReview.")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("pure operation probed live filesystem")

    with monkeypatch.context() as patch:
        for name in ("exists", "stat", "resolve", "read_bytes", "open"):
            patch.setattr(Path, name, forbidden)
        c.validate_configuration(config(req))
        first = c.plan(req)
        other = c.plan(
            replace(
                req,
                invocation_id="0" * 32,
                invocation_root=Path("/__millrace_validation__/invocation"),
            )
        )
    assert first.settings == other.settings
    assert b"\xef\xbb\xbf" not in first.generated_controls["codex-prompt.txt"]


def test_launch_argv_environment_and_generated_bounds(tmp_path: Path) -> None:
    from millrace_plus.router import codex_configuration as c

    req = request(tmp_path)
    plan = c.plan(req)
    assert plan.argv[:4] == (req.executable, "exec", "-c", 'model_provider="openai"')
    assert plan.argv[-1] == "-"
    assert dict(plan.environment) == {"CODEX_HOME": str(tmp_path / "native")}
    assert set(plan.generated_controls) == {
        "codex-prompt.txt",
        "codex-result-schema.json",
    }
    assert str(plan.details["final_path"]) not in plan.generated_controls
    assert (
        len(
            c.plan(request(tmp_path, prompt=b"x" * 1048576)).generated_controls[
                "codex-prompt.txt"
            ]
        )
        < 1114112
    )
    with pytest.raises(RouterError):
        c.plan(request(tmp_path, prompt=b"x" * 1048577))


def test_mixed_pi_codex_environment_is_local(
    tmp_path: Path,
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace_plus.router import codex_configuration as c
    from millrace_plus.router.codex import launch_environment

    req = replace(
        request(tmp_path),
        environment_names=("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"),
    )
    c.validate_configuration(config(req))
    env = {
        "PATH": "/usr/bin",
        "CODEX_HOME": str(tmp_path),
        "DEEPSEEK_API_KEY": "secret",
        "OPENAI_API_KEY": "other",
        "OPENAI_BASE_URL": "http://bad",
    }
    assert launch_environment(env) == {"PATH": "/usr/bin", "CODEX_HOME": str(tmp_path)}
    assert env["DEEPSEEK_API_KEY"] == "secret"

    # A real mixed fake-Pi -> fake-Codex graph proves locality at dispatch/reload.
    import copy

    from codex_support import codex_flow
    from plus_support import native_flow, new_id, start_run

    from millrace_plus.router import control

    workflow, project, home, agent = native_flow(single_flow)
    graph = json.loads(workflow.read_text())
    pi_node = copy.deepcopy(graph["planes"]["main"]["nodes"]["draft"])
    pi_node["on"] = {"READY": {"goto": "codex_step"}}
    program = Path(graph["bindings"]["pi"]["executable"])
    program.write_text(
        program.read_text().replace(
            "os.write(1,",
            "assert os.environ['DEEPSEEK_API_KEY'] == 'inert-pi-key'\nos.write(1,",
        )
    )
    workflow, project, home, native = codex_flow((workflow, project, home))
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["codex_step"] = copy.deepcopy(
        graph["planes"]["main"]["nodes"]["draft"]
    )
    graph["planes"]["main"]["nodes"]["draft"] = pi_node
    graph["control_files"] = sorted(set(graph["control_files"]))
    workflow.write_text(json.dumps(graph))
    names = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL")
    for key, value in zip(
        names, ("inert-pi-key", "inert-openai-key", "http://invalid.local"), strict=True
    ):
        monkeypatch.setenv(key, value)
    created = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        host_paths={"agent": str(agent), "codex_home": str(native)},
        env_allowlist=names,
    )
    assert created["status"] == "applied", created
    instance = str(created["instance_id"])
    run = start_run(instance, state_home=home)
    assert (run["status"], run["count"]) == ("succeeded", 2)
    assert [v["launch"]["adapter_id"] for v in run["invocations"]] == [
        "pi_json",
        "codex_exec",
    ]
    assert control.inspect_instance(instance, state_home=home)["env_allowlist"] == list(
        names
    )
    assert (
        control.inspect_run(instance, run["id"], state_home=home)["status"]
        == "succeeded"
    )
