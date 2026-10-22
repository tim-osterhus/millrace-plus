"""Actual stock graphs use inert settlement; artifacts are checked independently."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from catalog_support import assert_fake, create, run, stock

from millrace_plus import catalog
from millrace_plus.router import control


@pytest.mark.parametrize("name", ["software-change", "document-review"])
@pytest.mark.parametrize(
    "scenario",
    [
        "clean",
        "repair",
        "recheck_fail",
        "planning_defect",
        "goal_revise",
        "missing_input",
    ],
)
def test_stock_workflow_decisions_and_repair_bounds(
    tmp_path: Path, name: str, scenario: str
) -> None:
    workflow, project, home, fake = stock(tmp_path, name, scenario)
    if scenario == "missing_input":
        (project / "request.md").unlink()
    instance = create(workflow, project, home, fake)
    result = run(instance, home, fake)
    assert result["status"] == (
        "succeeded" if scenario in {"clean", "repair"} else "paused"
    ), result
    nodes = [entry["node"] for entry in result["invocations"]]
    assert nodes[0] == "check_inputs"
    assert len(nodes) <= 7
    if scenario in {"clean", "repair"}:
        product = project / (
            "result.py" if name == "software-change" else "document.md"
        )
        if name == "software-change":
            assert product.read_text() == "def answer():\n    return 42\n"
            compile(product.read_text(), str(product), "exec")
        else:
            assert product.read_text() == "accepted content\n"
        assert "Original goal:" in (project / "work/goal-review.md").read_text()
    if scenario in {"repair", "recheck_fail"}:
        assert nodes.count("repair") == nodes.count("recheck") == 1
    if scenario == "planning_defect":
        assert nodes[-1] == "diagnose" and (project / "work/diagnosis.md").is_file()
    if scenario == "missing_input":
        assert (
            nodes == ["check_inputs"] and not (project / "invocations.jsonl").exists()
        )
    assert result["startup_cap"] == 16 and result["startup_elapsed"] == 86400
    for invocation in result["invocations"]:
        assert invocation["deadline"] - invocation["created_at"] <= 1801


@pytest.mark.parametrize("mode", ["locked", "live"])
def test_authored_copy_expand_and_revise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    workflow, project, home, fake = stock(tmp_path, "document-review")
    sibling = tmp_path / "sibling"
    catalog.init_template("bundled:document-review", sibling)
    sibling_graph = (sibling / "workflow.json").read_bytes()
    bundled_graph = (
        catalog.bundled_root() / "document-review/workflow.json"
    ).read_bytes()
    options = json.loads((project / "case.json").read_text())
    options["edit"] = str(workflow)
    (project / "case.json").write_text(json.dumps(options))
    instance = create(workflow, project, home, fake)
    result = run(instance, home, fake, mode=mode)
    nodes = [entry["node"] for entry in result["invocations"]]
    if mode == "live":
        assert result["status"] == "succeeded" and "extra" in nodes
    else:
        assert result["status"] == "paused" and nodes == ["check_inputs", "work"]
        assert_fake(instance, home, fake)
        current = control.inspect_run(instance, str(result["id"]), state_home=home)
        receipt = control.accept_revision(
            instance,
            str(result["id"]),
            state_home=home,
            candidate_id=current["pending_revision"]["candidate_id"],
            request_id=uuid.uuid4().hex,
            expected_state_version=current["state_version"],
        )
        assert receipt["status"] == "applied", receipt
    assert (sibling / "workflow.json").read_bytes() == sibling_graph
    assert (
        catalog.bundled_root() / "document-review/workflow.json"
    ).read_bytes() == bundled_graph
    # A second ordinary copied folder demonstrates command rebinding without a DSL.
    graph = json.loads((sibling / "workflow.json").read_text())
    graph["bindings"] = {
        "python": {
            "adapter": "command",
            "contract": "millrace-command/v1",
            "executable": __import__("sys").executable,
            "config": {},
        }
    }
    graph["planes"]["main"]["nodes"] = {
        "check_inputs": graph["planes"]["main"]["nodes"]["check_inputs"]
    }
    graph["planes"]["main"]["nodes"]["check_inputs"]["invoke"]["binding"] = "python"
    graph["planes"]["main"]["nodes"]["check_inputs"]["on"]["READY"] = {
        "finish": "succeeded"
    }
    graph["control_files"] = ["scripts/check_inputs.py"]
    (sibling / "workflow.json").write_text(json.dumps(graph))
    made = control.create_instance(
        sibling / "workflow.json",
        project,
        state_home=tmp_path / "command-state",
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    started = control.start_run(
        str(made["instance_id"]),
        state_home=tmp_path / "command-state",
        request_id=uuid.uuid4().hex,
        expected_state_version=1,
    )
    assert (
        control.inspect_run(
            str(made["instance_id"]),
            str(started["run_id"]),
            state_home=tmp_path / "command-state",
        )["status"]
        == "succeeded"
    )
