from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from millrace_plus.demo import discovery
from millrace_plus.demo.contracts import DemoError, canonical_bytes
from millrace_plus.demo.materialize import materialize
from millrace_plus.router import control
from millrace_plus.router.claude import CLAUDE_VERSION, SUPPORTED_CLAUDE_VERSIONS


@pytest.mark.parametrize("native_selected", [False, True])
def test_exact_materialized_branches(tmp_path, monkeypatch, native_selected):
    monkeypatch.setattr(discovery.shutil, "which", lambda name: sys.executable)
    native = discovery.select_native("claude_print") if native_selected else None
    graph_file = materialize(
        tmp_path,
        native=native,
        no_web=True,
        session_file=tmp_path.parent / "demo-session.json",
    )
    graph = json.loads(graph_file.read_text())
    nodes = graph["planes"]["main"]["nodes"]
    assert graph["format"] == "millrace-router/v4"
    assert graph["limits"] == {"max_parallel_invocations": 1}
    assert set(nodes) == {"detect", "baseline", "fallback", "render", "unavailable"} | (
        {"native", "validate"} if native_selected else {"skip"}
    )
    assert graph["bindings"]["command"]["executable"] == str(
        Path(sys.executable).absolute()
    )
    assert nodes["baseline"]["on"]["READY"] == {
        "pause": {
            "reason": "baseline_saved",
            "resume": "native" if native_selected else "skip",
        }
    }
    for name, node in nodes.items():
        assert set(node["outcomes"]) == set(node["on"])
        assert "on_error" in node and "on_unknown" in node
        assert "retry" not in str(node)
        if name != "native":
            assert "--no-web" in node["invoke"]["config"]["args"]
            assert node["invoke"]["inputs"] == []
    assert not (tmp_path / "demo-session.json").exists()
    if native_selected:
        assert nodes["native"]["invoke"]["inputs"] == ["baseline.json"]
        assert "timeout_seconds" not in nodes["native"]["invoke"]
        assert "timeout_seconds" not in graph["defaults"]
        assert nodes["native"]["on_error"] == {"goto": "fallback"}
    else:
        assert set(graph["bindings"]) == {"command"}
        assert not (tmp_path / "profiles").exists()


def test_captured_command_keeps_the_current_virtual_environment(tmp_path, monkeypatch):
    environment = tmp_path / "environment"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    interpreter = environment / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(sys, "executable", str(interpreter))
    graph_file = materialize(
        workspace,
        native=None,
        no_web=False,
        session_file=tmp_path / "demo-session.json",
    )
    captured = json.loads(graph_file.read_text())["bindings"]["command"]["executable"]
    observed = json.loads(
        subprocess.check_output(
            [
                captured,
                "-c",
                "import json,sys; print(json.dumps([sys.prefix,sys.base_prefix]))",
            ],
            env={"PATH": os.defpath},
            text=True,
        )
    )
    assert Path(observed[0]) == environment
    assert observed[0] != observed[1]


def test_passive_discovery_and_inherited_selection(monkeypatch):
    calls = []
    monkeypatch.setattr(
        discovery.shutil, "which", lambda name: calls.append(name) or sys.executable
    )
    inventory = discovery.passive_inventory()
    assert len(inventory) == 4 and len(calls) == 4
    assert all(item["readiness"] == "version/auth unknown" for item in inventory)
    for adapter in ("claude_print", "opencode_run"):
        native = discovery.select_native(adapter)
        assert native["settings"] == {} and native["provenance"] == "inherited"
        text = discovery.consent_description(native)
        for phrase in (
            "1800-second fallback",
            "runtime defaults",
            "admitted effective timeout",
            "billing",
            "hooks",
            "MCP",
            "Sanitized",
            "not a sandbox",
            "Research",
        ):
            assert phrase in text
    assert "bypasses native permission prompts" in discovery.consent_description(
        discovery.select_native("claude_print")
    )
    assert "does not alter harness permissions" in discovery.consent_description(
        discovery.select_native("opencode_run"), no_web=True
    )
    assert discovery.select_native("pi_json") is None
    assert discovery.select_native("codex_exec") is None


def test_missing_unknown_and_bad_selected_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery.shutil, "which", lambda name: None)
    assert all(i["presence"] == "missing" for i in discovery.passive_inventory())
    with pytest.raises(DemoError, match="native_executable_missing"):
        discovery.select_native("claude_print")
    with pytest.raises(DemoError, match="unsupported_native_adapter"):
        discovery.select_native("invented")
    monkeypatch.setattr(discovery.shutil, "which", lambda name: sys.executable)
    profile = tmp_path / "profile.json"
    profile.write_bytes(
        canonical_bytes(
            {"format": "millrace-claude-print-profile/v1", "claude_version": "wrong"}
        )
    )
    with pytest.raises(DemoError, match="invalid_native_profile"):
        discovery.select_native("claude_print", profile)


def test_selected_native_shim_keeps_its_dispatch_name(tmp_path, monkeypatch):
    dispatcher = tmp_path / "mise"
    dispatcher.write_text(
        f"#!{sys.executable}\n"
        "import pathlib,sys\n"
        "if pathlib.Path(sys.argv[0]).name != 'claude':\n"
        "    raise SystemExit('wrong shim dispatch')\n"
        "print('2.1.305 (Claude Code)')\n"
    )
    dispatcher.chmod(0o700)
    shim = tmp_path / "claude"
    shim.symlink_to(dispatcher)
    monkeypatch.setattr(discovery.shutil, "which", lambda name: str(shim))
    selected = discovery.select_native("claude_print")
    assert (
        subprocess.check_output(
            [selected["binding"]["executable"], "--version"], text=True
        ).strip()
        == "2.1.305 (Claude Code)"
    )


@pytest.mark.parametrize("version", sorted(SUPPORTED_CLAUDE_VERSIONS))
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_claude_profile_keeps_exact_version_and_selected_settings(
    tmp_path, monkeypatch, version, effort
):
    monkeypatch.setattr(
        discovery.shutil,
        "which",
        lambda cmd, mode=os.F_OK | os.X_OK, path=None: sys.executable,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("admission launched a process"),
    )
    inherited = discovery.select_native("claude_print")
    assert inherited["profile"] == {
        "format": "millrace-claude-print-profile/v1",
        "claude_version": CLAUDE_VERSION,
    }
    profile = tmp_path / "profile.json"
    captured = {
        "format": "millrace-claude-print-profile/v1",
        "claude_version": version,
        "model": "sonnet",
        "reasoning_effort": effort,
    }
    profile.write_bytes(canonical_bytes(captured))
    selected = discovery.select_native("claude_print", profile)
    assert selected["profile"] == captured
    settings = {"model": "sonnet", "reasoning_effort": effort}
    assert selected["settings"] == settings
    assert selected["provenance"] == "explicit"
    consent = discovery.consent_description(selected)
    assert "reasoning_effort" in consent and effort in consent and "sonnet" in consent
    assert "'effort':" not in consent
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow = materialize(
        workspace,
        native=selected,
        no_web=True,
        session_file=tmp_path / "session.json",
    )
    profile.write_bytes(b"{}")  # The admitted copy retains the selected settings.
    assert json.loads((workspace / "profiles/native.json").read_bytes()) == captured
    state_home = tmp_path / "state"
    receipt = control.create_instance(
        workflow,
        workspace,
        state_home=state_home,
        request_id="a" * 32,
        expected_state_version=0,
    )
    assert receipt["status"] == "applied"
    validation = control.validate_revision_summary(
        receipt["instance_id"], state_home=state_home
    )["data"]
    assert validation["valid"] is True
    # The bounded summary exposes common settings only. The public full
    # validation read also exposes the admitted adapter settings.
    full = control.validate_revision(receipt["instance_id"], state_home=state_home)
    assert full["valid"] is True
    admitted = full["resolved_nodes"]["main"]["native"]["adapter"]
    assert admitted["requested"] == admitted["effective"] == settings
    assert admitted["provenance"] == {
        key: {"source": "profile", "policy": None} for key in settings
    }


@pytest.mark.parametrize(
    "settings",
    [
        {"effort": "high"},
        {"effort": "high", "reasoning_effort": "high"},
        {"unknown": "high"},
        {"reasoning_effort": "unsupported"},
        {"reasoning_effort": True},
        {"reasoning_effort": "--high"},
    ],
)
def test_claude_profile_refuses_legacy_and_invalid_settings(
    tmp_path, monkeypatch, settings
):
    monkeypatch.setattr(discovery.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("discovery launched a process"),
    )
    profile = tmp_path / "profile.json"
    profile.write_bytes(
        canonical_bytes(
            {
                "format": "millrace-claude-print-profile/v1",
                "claude_version": CLAUDE_VERSION,
                **settings,
            }
        )
    )
    with pytest.raises(DemoError, match="invalid_native_profile"):
        discovery.select_native("claude_print", profile)


def test_claude_omitted_settings_remain_inherited_through_admission(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        discovery.shutil,
        "which",
        lambda cmd, mode=os.F_OK | os.X_OK, path=None: sys.executable,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **kw: pytest.fail("admission launched a process"),
    )
    native = discovery.select_native("claude_print")
    assert native["settings"] == {} and native["provenance"] == "inherited"
    assert (
        "native inheritance; effective values unknown"
        in discovery.consent_description(native)
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow = materialize(
        workspace,
        native=native,
        no_web=True,
        session_file=tmp_path / "session.json",
    )
    state_home = tmp_path / "state"
    receipt = control.create_instance(
        workflow,
        workspace,
        state_home=state_home,
        request_id="a" * 32,
        expected_state_version=0,
    )
    assert receipt["status"] == "applied"
    validation = control.validate_revision_summary(
        receipt["instance_id"], state_home=state_home
    )["data"]
    assert validation["valid"] is True
    full = control.validate_revision(receipt["instance_id"], state_home=state_home)
    assert full["valid"] is True
    admitted = full["resolved_nodes"]["main"]["native"]["adapter"]
    assert (
        admitted["requested"] == admitted["effective"] == admitted["provenance"] == {}
    )


@pytest.mark.parametrize(
    "runtime,policy,timeout,source,constraint",
    [
        ({}, {}, 1800, "fallback", None),
        ({"timeout_seconds": 90}, {}, 90, "runtime", None),
        (
            {"timeout_seconds": 90},
            {"timeout_ceiling_seconds": 45},
            45,
            "runtime",
            {"kind": "ceiling", "value": 45},
        ),
    ],
)
def test_materialized_native_timeout_uses_public_core_resolution(
    tmp_path, monkeypatch, runtime, policy, timeout, source, constraint
):
    monkeypatch.setattr(
        discovery.shutil,
        "which",
        lambda cmd, mode=os.F_OK | os.X_OK, path=None: sys.executable,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workflow = materialize(
        workspace,
        native=discovery.select_native("claude_print"),
        no_web=True,
        session_file=tmp_path / "session.json",
    )
    graph = json.loads(workflow.read_bytes())
    # A command stand-in exercises real admission without executing a native
    # runtime, probe, model or any graph node.
    graph["bindings"]["native"] = dict(graph["bindings"]["command"])
    graph["planes"]["main"]["nodes"]["native"]["invoke"]["config"] = {
        "script": "scripts/stage.py",
        "args": ["skip"],
    }
    workflow.write_bytes(canonical_bytes(graph))
    state_home = tmp_path / "state"
    receipt = control.create_instance(
        workflow,
        workspace,
        state_home=state_home,
        request_id="a" * 32,
        expected_state_version=0,
        runtime_defaults=runtime,
        operator_policy=policy,
    )
    assert receipt["status"] == "applied"
    validation = control.validate_revision_summary(
        receipt["instance_id"], state_home=state_home
    )["data"]
    assert validation["valid"] is True
    nodes = validation["resolved_nodes"]["main"]
    common = nodes["native"]["common"]
    assert common["effective"]["timeout_seconds"] == timeout
    assert common["provenance"]["timeout_seconds"] == {
        "source": source,
        "policy": constraint,
    }
    assert nodes["detect"]["common"]["effective"]["timeout_seconds"] == 15
