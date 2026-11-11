"""Materialize the finite hardware-demo graph with one captured interpreter."""

from __future__ import annotations

import copy
import json
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from .contracts import canonical_bytes

__all__ = ["materialize"]


def _node(
    stage: str,
    timeout: int,
    routes: dict[str, Any],
    error: dict[str, Any],
    session_file: Path,
    no_web: bool = False,
) -> dict[str, Any]:
    return {
        "invoke": {
            "binding": "command",
            "timeout_seconds": timeout,
            "config": {
                "script": "scripts/stage.py",
                "args": [stage, "--session-file", str(session_file)]
                + (["--no-web"] if no_web else []),
            },
            "cwd": ".",
            "inputs": [],
        },
        "result": "json-v1",
        "outcomes": list(routes),
        "on": routes,
        "on_error": error,
        "on_unknown": copy.deepcopy(error),
    }


def materialize(
    workspace: Path, *, native: dict[str, Any] | None, no_web: bool, session_file: Path
) -> Path:
    """Copy assets and author a v4 graph without preparing any native runtime.

    The caller creates a fresh owned workspace and starts its instance with
    locked edit mode. Public create_instance admission validates the graph and
    selected adapter before start_run. No overall invocation or elapsed-time
    cap is authored.
    """
    workspace = workspace.resolve(strict=True)
    session_file = session_file.absolute()
    source = files("millrace_plus").joinpath("workflows/hardware-demo")
    for name in ("scripts/stage.py", "prompts/native.md", "references/shortlist.json"):
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(source.joinpath(name).read_bytes())
    choice = {
        "format": "millrace-demo-choice/v1",
        "no_web": no_web,
        "native_selected": native is not None,
        "reason": "not_selected",
    }
    (workspace / "demo-choice.json").write_bytes(canonical_bytes(choice))

    def goto(target: str) -> dict[str, str]:
        return {"goto": target}

    selected = "native" if native is not None else "skip"
    graph = json.loads(source.joinpath("workflow.json").read_text(encoding="utf-8"))
    nodes = graph["planes"]["main"]["nodes"]
    for stage, node in nodes.items():
        node["invoke"]["config"]["args"] = [stage, "--session-file", str(session_file)]
        if no_web:
            node["invoke"]["config"]["args"].append("--no-web")
    nodes["baseline"]["on"]["READY"]["pause"]["resume"] = selected
    bindings = graph["bindings"]
    # Python uses the invocation path to find pyvenv.cfg and its installed
    # packages. Dereferencing a venv symlink silently selects the base runtime.
    bindings["command"]["executable"] = str(Path(sys.executable).absolute())
    controls = graph["control_files"]
    controls.append("demo-choice.json")
    if native is not None:
        del nodes["skip"]
        profile = workspace / "profiles/native.json"
        profile.parent.mkdir()
        profile.write_bytes(canonical_bytes(native["profile"]))
        bindings["native"] = copy.deepcopy(native["binding"])
        bindings["native"]["config"] = {"profile": "profiles/native.json"}
        controls.extend(["profiles/native.json", "prompts/native.md"])
        prompt = (workspace / "prompts/native.md").read_text(encoding="utf-8")
        prompt += (
            "\nWeb research is disabled. Set research_status to skipped.\n"
            if no_web
            else "\nAttempt research using existing permitted tools; "
            "if unavailable, say unavailable.\n"
        )
        (workspace / "prompts/native.md").write_text(prompt, encoding="utf-8")
        nodes["native"] = {
            "invoke": {
                "binding": "native",
                "timeout_seconds": 120,
                "config": {"prompt": "prompts/native.md"},
                "cwd": ".",
                "inputs": ["baseline.json"],
            },
            "result": "json-v1",
            "outcomes": ["PROPOSED", "NO_RESEARCH", "DECLINED"],
            "on": {
                "PROPOSED": goto("validate"),
                "NO_RESEARCH": goto("validate"),
                "DECLINED": goto("fallback"),
            },
            "on_error": goto("fallback"),
            "on_unknown": goto("fallback"),
        }
        nodes["validate"] = _node(
            "validate",
            10,
            {"ACCEPT": goto("render"), "REJECT": goto("fallback")},
            goto("fallback"),
            session_file,
            no_web,
        )
    encoded = canonical_bytes(graph)
    path = workspace / "workflow.json"
    path.write_bytes(encoded)
    return path
