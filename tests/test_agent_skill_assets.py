"""Validate packaged advisory guidance without loading the optional Core runtime."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "src/millrace_plus/skills"
NAMES = {
    "millrace-entrypoint-authoring",
    "millrace-loop-configuration",
    "millrace-instruction-manual",
}


def test_advisory_inventory_results_and_agent_metadata(tmp_path: Path) -> None:
    assert {p.name for p in SKILLS.iterdir()} == NAMES
    for folder in SKILLS.iterdir():
        assert {
            p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()
        } == {"SKILL.md", "agents/openai.yaml"}
        text = (folder / "SKILL.md").read_text()
        agent = (folder / "agents/openai.yaml").read_text()
        assert f"name: {folder.name}" in text
        assert f"${folder.name}" in agent
        assert "graph-v3" in agent and "json-v1" in agent and "advisory" in agent
        assert "advisory package data" in text
        for block in re.findall(r"```json\n(.*?)\n```", text, re.S):
            result = json.loads(block)
            assert result == {
                "outcome": "DONE",
                "summary": "Verified the requested change",
                "refs": [],
            }
        for forbidden in (
            "kernel_ping",
            "millrace run daemon",
            "terminal marker",
            "compiled-manifest",
            "selected_authority",
        ):
            assert forbidden not in text
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if not re.match(r"[a-z]+:", target):
                assert (folder / target.split("#")[0]).exists(), target
        # Each catalog command is accepted by the actual standalone parser.
        for line in text.splitlines():
            if line.startswith("millrace-plus catalog "):
                env = os.environ.copy()
                env["HOME"] = str(tmp_path)
                env["PYTHONPATH"] = str(ROOT / "src")
                command = line.split()[1:]
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from millrace_plus.router.cli import cli; cli()",
                        *command,
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                assert result.returncode == 0, result.stderr


def test_stock_graphs_have_no_implicit_skill_authority() -> None:
    for path in (ROOT / "src/millrace_plus/workflows").glob("*/workflow.json"):
        graph = json.loads(path.read_text())
        assert graph["format"] == "millrace-router/v3"
        assert all("skills/" not in asset for asset in graph["control_files"])
        assert "selected_authority" not in graph
