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
            "120-second",
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
