"""Fresh test-owned v3 flows; Plus router tests never touch retained stores."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def flow(tmp_path: Path) -> tuple[Path, Path, Path]:
    workflow = tmp_path / "workflow"
    shutil.copytree(FIXTURES / "brief-flow", workflow)
    project = tmp_path / "project"
    project.mkdir()
    (project / "request.txt").write_text("A small authored workflow\n")
    file = workflow / "workflow.json"
    data = json.loads(file.read_text())
    data["bindings"]["python"]["executable"] = sys.executable
    file.write_text(json.dumps(data))
    return file, project, tmp_path / "state"


@pytest.fixture
def single_flow(flow: tuple[Path, Path, Path]) -> tuple[Path, Path, Path]:
    workflow, project, home = flow
    shutil.copyfile(FIXTURES / "command_cases.py", workflow.parent / "scripts/draft.py")
    data = json.loads(workflow.read_text())
    data["planes"]["main"]["nodes"] = {
        "draft": data["planes"]["main"]["nodes"]["draft"]
    }
    data["control_files"] = ["scripts/draft.py"]
    node = data["planes"]["main"]["nodes"]["draft"]
    node["invoke"]["config"]["args"] = ["good"]
    node["invoke"].update(inputs=[], timeout_seconds=1)
    node["outcomes"] = ["READY"]
    node["on"] = {"READY": {"finish": "succeeded"}}
    node["on_error"] = {"pause": {"reason": "execution_error", "resume": "draft"}}
    node["on_unknown"] = {"pause": {"reason": "unknown_outcome", "resume": "draft"}}
    data["limits"] = {"max_invocations": 6, "max_elapsed_seconds": 10}
    workflow.write_text(json.dumps(data))
    return workflow, project, home
