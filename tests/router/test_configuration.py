"""Plus Pi configuration parity: selected-context validation, settings and refusals.

Transferred from the accepted Core tests. Immutable baselines:

- test_graph.py
  (sha256 d556080162836d007e308dbd001bfaddebb80844cc259128260dd8bb9cb01bae):
  test_pi_profile_prompt_capture_and_validation_parity and
  test_pi_static_and_live_validation_parity (12 variants).
- test_settings.py
  (sha256 0628a9232ea8a0c5cf75c5985c9110b3cbb35267660456bcde073d3f2a115536):
  test_pi_settings_applicability_and_provenance and
  test_pi_settings_refusals_are_not_silently_dropped (4 variants).

Root-admitted phase dispositions: Core capture is context-free and records only
declared control bytes, so exact Pi shape refusals run in Plus selected-context
validation (validate_workflow and resolve_settings with resolve_adapter). The
agent_file variant is an invoke-time Plus preflight: capture and static
locator-shape validation accept the absolute locator, and the run records a
counted execution_failure with no process started.

test_pi_capture_settings_and_refusal_parity is the new packet section 8
"Captured configuration" proof.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from millrace.router.execution import RouterError
from millrace.router.graph import (
    capture,
    decode_revision,
    resolve_settings,
    validate_workflow,
)
from plus_support import (
    create_instance,
    inspect_run,
    native_flow,
    pi_instance,
    start_run,
)

from millrace_plus.router import control
from millrace_plus.router.configuration import ADAPTER_CONTRACT, ADAPTER_ID
from millrace_plus.router.pi import resolve_adapter


def _pi_settings(config: dict[str, object]) -> dict[str, object]:
    """The namespaced runtime/defaults layer for the exact Pi adapter pair."""
    pi_json = {"contract": ADAPTER_CONTRACT, "config": config}
    return {"adapter_settings": {"pi_json": pi_json}}


def _edit_json(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    data: dict[str, Any] = json.loads(path.read_text())
    edit(data)
    path.write_text(json.dumps(data))


def test_pi_profile_prompt_capture_and_validation_parity(
    single_flow: tuple[Path, Path, Path],
) -> None:
    """A literal native declaration must capture its profile and prompt controls."""
    workflow, project, _ = single_flow
    data = json.loads(workflow.read_text())
    data["bindings"] = {
        "native": {
            "adapter": ADAPTER_ID,
            "contract": ADAPTER_CONTRACT,
            "executable": sys.executable,
            "config": {"profile": "pi.json"},
        }
    }
    data["planes"]["main"]["nodes"]["draft"]["invoke"] = {
        "binding": "native",
        "config": {"prompt": "task.txt"},
        "timeout_seconds": 3,
    }
    data["control_files"] = [*data.get("control_files", []), "pi.json", "task.txt"]
    workflow.write_text(json.dumps(data))
    (workflow.parent / "task.txt").write_text("Review the supplied project.")
    (workflow.parent / "pi.json").write_text(
        json.dumps(
            {
                "format": "millrace-pi-json-profile/v1",
                "pi_version": "0.87.0",
                "agent_dir": "agent",
                "provider": "test",
                "model": "exact",
                "thinking": "off",
            }
        )
    )
    agent = project / "agent"
    agent.mkdir()
    result = capture(workflow, project, host_paths={"agent": str(agent)})
    assert {"pi.json", "task.txt"} <= result.files.keys()
    settings = resolve_settings(
        result,
        "main",
        "draft",
        settings_context={
            "state_version": 1,
            "runtime_defaults": {},
            "operator_policy": {},
            "host_paths": {"agent": str(agent)},
        },
        adapter_resolver=resolve_adapter,
    )
    assert settings["adapter"]["effective"]["model"] == "exact"
    assert decode_revision(result.encoded(), result.revision_id) == result


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("version", "unsupported_pi_version"),
        ("unknown", "invalid_pi_profile"),
        ("script", "invalid_pi_invocation"),
        ("command_prompt", "invalid_adapter_settings"),
        ("thinking", "invalid_settings"),
        ("frame", "invalid_pi_profile"),
        ("agent_file", "invalid_host_path"),
        ("agent_relative", "invalid_host_path"),
        ("profile_duplicate", "duplicate_key"),
        ("prompt_utf8", "invalid_pi_invocation"),
        ("profile_large", "invalid_pi_profile"),
        ("prompt_large", "invalid_pi_invocation"),
    ],
)
def test_pi_static_and_live_validation_parity(
    single_flow: tuple[Path, Path, Path], variant: str, expected: str
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    data = json.loads(workflow.read_text())
    profile_path = workflow.parent / "pi.json"
    profile = json.loads(profile_path.read_text())
    paths = {"agent": str(agent)}
    if variant == "version":
        profile["pi_version"] = "0.73.1"
    elif variant == "unknown":
        profile["arbitrary"] = {}
    elif variant == "thinking":
        profile["thinking"] = "unbounded"
    elif variant == "frame":
        profile["max_event_bytes"] = 268435457
    elif variant == "script":
        data["planes"]["main"]["nodes"]["draft"]["invoke"]["config"]["script"] = (
            "scripts/draft.py"
        )
    elif variant == "command_prompt":
        data["planes"]["main"]["nodes"]["draft"]["invoke"]["binding"] = "python"
    elif variant == "agent_file":
        agent.rmdir()
        agent.write_text("not a directory")
    elif variant == "agent_relative":
        paths["agent"] = "relative"
    elif variant == "prompt_utf8":
        (workflow.parent / "task.txt").write_bytes(b"\xff")
    profile_path.write_text(json.dumps(profile))
    if variant == "profile_large":
        profile_path.write_text(" " * 1048577)
    if variant == "prompt_large":
        (workflow.parent / "task.txt").write_bytes(b"x" * 1048577)
    if variant == "profile_duplicate":
        profile_path.write_text('{"format":"x","format":"x"}')
    workflow.write_text(json.dumps(data))
    context = {
        "state_version": 1,
        "runtime_defaults": {},
        "operator_policy": {},
        "path": [],
        "host_paths": paths,
    }
    # Context-free capture records declared bytes and never interprets the Pi shape.
    capture(workflow, project, host_paths=paths)
    if variant == "agent_file":
        # Static locator-shape validation accepts the absolute path; the live
        # type is an invoke-time Plus preflight, not a validation refusal.
        assert validate_workflow(
            workflow,
            project,
            settings_context=context,
            adapter_resolver=resolve_adapter,
        )
        # A marker write at the top of the program makes any process spawn,
        # including the version probe, observable.
        program = Path(json.loads(workflow.read_text())["bindings"]["pi"]["executable"])
        marker = project / "spawned.marker"
        shebang, body = program.read_text().split("\n", 1)
        program.write_text(
            f"{shebang}\nfrom pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('spawned')\n{body}"
        )
        run = start_run(pi_instance(workflow, project, home, agent), state_home=home)
        draft = run["invocations"][0]
        assert run["count"] == 1
        assert run["status"] == "paused"
        assert run["planes"]["main"]["pause_reason"] == "execution_error"
        assert run["pause_reason"] == "no_runnable_planes"
        assert run["run_gate"] is None
        assert draft["pid"] is None
        assert draft["adapter_evidence"]["phase"] == "preflight"
        assert draft["adapter_evidence"]["observed_version"] is None
        assert draft["adapter_evidence"]["failure_code"] == "invalid_host_path"
        completion = run["completions"][0]
        assert completion["classification"] == "execution_failure"
        assert completion["diagnostic"] == "invalid_host_path"
        assert completion["exit_code"] is None
        assert not marker.exists()
        return
    with pytest.raises(RouterError) as error:
        validate_workflow(
            workflow,
            project,
            settings_context=context,
            adapter_resolver=resolve_adapter,
        )
    assert error.value.code == expected


def test_pi_settings_applicability_and_provenance(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["prepare"] = {
        "invoke": {
            "binding": "python",
            "timeout_seconds": 1,
            "cwd": ".",
            "inputs": [],
            "config": {"script": "scripts/draft.py", "args": ["good"]},
        },
        "result": "json-v1",
        "outcomes": ["READY"],
        "on": {"READY": {"goto": "draft"}},
        "on_error": {"finish": "failed"},
        "on_unknown": {"finish": "failed"},
    }
    graph["planes"]["main"]["start"] = "prepare"
    workflow.write_text(json.dumps(graph))
    profile = json.loads((workflow.parent / "pi.json").read_text())
    for key in ("provider", "model", "thinking"):
        del profile[key]
    (workflow.parent / "pi.json").write_text(json.dumps(profile))
    instance = create_instance(
        workflow,
        project,
        state_home=home,
        host_paths={"agent": str(agent)},
        runtime_defaults=_pi_settings(
            {"provider": "fixture", "model": "exact-model", "thinking": "off"}
        ),
    )
    result = start_run(str(instance["instance_id"]), state_home=home)
    assert result["status"] == "succeeded" and result["count"] == 2
    command, native = result["invocations"]
    assert set(command["launch_settings"]["common"]["effective"]) == {
        "binding",
        "timeout_seconds",
    }
    assert command["launch_settings"]["adapter"]["effective"] == {}
    # v3 equivalent of the baseline "no native launch" check: the command launch
    # projection carries no Pi details, environment or generated controls.
    assert command["launch"]["adapter_id"] == "command"
    assert command["launch"]["details"] == {}
    assert command["launch"]["environment"] == {}
    assert command["launch"]["generated_controls"] == {}
    assert command["adapter_evidence"] is None
    assert native["launch_settings"]["adapter"]["effective"]["model"] == "exact-model"
    assert native["launch_settings"]["adapter"]["provenance"]["model"] == {
        "source": "runtime",
        "policy": None,
    }
    assert native["launch"]["details"]["agent_dir"] == str(agent)


@pytest.mark.parametrize(
    "variant,code",
    [
        ("invalid_inactive", "invalid_settings"),
        ("command_node", "invalid_adapter_settings"),
        ("profile_conflict", "conflicting_pi_setting"),
        ("missing", "missing_effective_pi_setting"),
    ],
)
def test_pi_settings_refusals_are_not_silently_dropped(
    single_flow: tuple[Path, Path, Path], variant: str, code: str
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    graph = json.loads(workflow.read_text())
    runtime: dict[str, object] = {}
    if variant == "invalid_inactive":
        runtime = _pi_settings({"thinking": "unbounded"})
    if variant == "command_node":
        graph["planes"]["main"]["nodes"]["draft"]["invoke"] = {
            "binding": "python",
            "timeout_seconds": 1,
            "cwd": ".",
            "inputs": [],
            "config": {
                "script": "scripts/draft.py",
                "args": ["good"],
                "provider": "fixture",
            },
        }
    if variant == "profile_conflict":
        graph["defaults"] = _pi_settings({"model": "other"})
    if variant == "missing":
        profile = json.loads((workflow.parent / "pi.json").read_text())
        del profile["thinking"]
        (workflow.parent / "pi.json").write_text(json.dumps(profile))
    workflow.write_text(json.dumps(graph))
    with pytest.raises(RouterError) as error:
        create_instance(
            workflow,
            project,
            state_home=home,
            host_paths={"agent": str(agent)},
            runtime_defaults=runtime,
        )
    assert error.value.code == code
    assert control.list_instances(state_home=home) == []


def test_pi_capture_settings_and_refusal_parity(
    single_flow: tuple[Path, Path, Path],
) -> None:
    """Explicit Pi config resolves, history survives asset loss, refusals hold."""
    workflow, project, home, agent = native_flow(single_flow)
    cases = home.parent / "cases"
    refusals = {
        "undeclared_prompt": "missing_control",
        "missing_prompt": "missing_control",
        "undeclared_profile": "missing_control",
        "missing_profile": "missing_control",
        "profile_asset": "invalid_pi_profile",
        "prompt_asset": "invalid_pi_invocation",
        "unused_setting": "invalid_settings",
        "shadowed_setting": "invalid_settings",
        "host_locator": "invalid_host_path",
    }
    # Each refusal runs on a fresh copy so no case can mask another.
    for name, expected in refusals.items():
        case_dir = cases / name
        shutil.copytree(workflow.parent, case_dir / "workflow")
        flow_file = case_dir / "workflow" / "workflow.json"
        runtime: dict[str, object] = {}
        paths: dict[str, str] = {"agent": str(agent)}
        if name == "undeclared_prompt":
            _edit_json(
                flow_file,
                lambda g: g.update(
                    control_files=[c for c in g["control_files"] if c != "task.txt"]
                ),
            )
        elif name == "undeclared_profile":
            _edit_json(
                flow_file,
                lambda g: g.update(
                    control_files=[c for c in g["control_files"] if c != "pi.json"]
                ),
            )
        elif name == "missing_prompt":
            (case_dir / "workflow" / "task.txt").unlink()
        elif name == "missing_profile":
            (case_dir / "workflow" / "pi.json").unlink()
        elif name == "profile_asset":
            (case_dir / "workflow" / "pi.json").write_text(
                json.dumps({"format": "not-a-profile"})
            )
        elif name == "prompt_asset":
            (case_dir / "workflow" / "task.txt").write_bytes(b"\xff")
        elif name == "unused_setting":
            runtime = _pi_settings({"thinking": "unbounded"})
        elif name == "shadowed_setting":
            runtime = _pi_settings({"thinking": "unbounded"})
            _edit_json(
                flow_file,
                lambda g: g["planes"]["main"]["nodes"]["draft"]["invoke"][
                    "config"
                ].update(thinking="off"),
            )
        elif name == "host_locator":
            paths = {"agent": "substituted/agent"}
        state_home = case_dir / "state"
        context = {
            "state_version": 1,
            "runtime_defaults": runtime,
            "operator_policy": {},
            "path": [],
            "host_paths": paths,
        }
        with pytest.raises(RouterError) as error:
            validate_workflow(
                flow_file,
                project,
                settings_context=context,
                adapter_resolver=resolve_adapter,
            )
        assert error.value.code == expected, name
        with pytest.raises(RouterError) as error:
            create_instance(
                flow_file,
                project,
                state_home=state_home,
                host_paths=paths,
                runtime_defaults=runtime,
            )
        assert error.value.code == expected, name
        assert control.list_instances(state_home=state_home) == [], name
    # Captured closure: the terminal history must reconstruct after live assets vanish.
    instance = pi_instance(workflow, project, home, agent)
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    effective = run["invocations"][0]["launch_settings"]["adapter"]["effective"]
    assert effective["model"] == "exact-model"
    run_id = run["id"]
    before = inspect_run(instance, run_id, state_home=home)
    shutil.rmtree(workflow.parent)
    shutil.rmtree(agent)
    after = inspect_run(instance, run_id, state_home=home)
    repeated = inspect_run(instance, run_id, state_home=home)
    # draft_revision is re-captured from live workflow files by Core store.draft, so it
    # is excluded from the terminal-history comparison and the receipts list is excluded
    # as well. The terminal fields are asserted separately below.
    excluded = {"draft_revision", "controls"}
    assert {k: v for k, v in after.items() if k not in excluded} == {
        k: v for k, v in before.items() if k not in excluded
    }
    assert after["status"] == before["status"]
    assert after["count"] == before["count"]
    assert after["invocations"] == before["invocations"]
    assert after["completions"] == before["completions"]
    assert after["transitions"] == before["transitions"]
    assert repeated == after
