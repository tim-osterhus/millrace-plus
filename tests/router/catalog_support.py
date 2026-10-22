"""Task-owned inert stock execution with a hard captured-executable check."""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path

from millrace_plus import catalog
from millrace_plus.router import control


def stock(
    root: Path, name: str, scenario: str = "clean"
) -> tuple[Path, Path, Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    workflow = root / "workflow"
    catalog.init_template(f"bundled:{name}", workflow, catalog_home=root / "catalog")
    graph = json.loads((workflow / "workflow.json").read_text())
    for node, value in graph["planes"]["main"]["nodes"].items():
        prompt = value["invoke"]["config"].get("prompt")
        if prompt:
            path = workflow / prompt
            path.write_text(f"TEST_NODE:{node}\n" + path.read_text())
    project = root / "project"
    project.mkdir()
    (project / "request.md").write_text(
        "Original goal: accepted content, independently checked.\n"
    )
    (project / "case.json").write_text(
        json.dumps(
            {
                "scenario": scenario,
                "product": "result.py" if name == "software-change" else "document.md",
            }
        )
    )
    fakebin = root / "fakebin"
    fakebin.mkdir()
    fake = fakebin / "fake-claude"
    fake.write_text(
        f"#!{sys.executable}\n"
        + (Path(__file__).parent / "fixtures/workflow_peer.py").read_text()
    )
    fake.chmod(0o700)
    return workflow / "workflow.json", project, root / "state", fake


def create(workflow: Path, project: Path, home: Path, fake: Path) -> str:
    receipt = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        path=(str(fake.parent),),
        host_paths={"claude": str(fake), "python3": sys.executable},
    )
    assert receipt["status"] == "applied", receipt
    instance = str(receipt["instance_id"])
    assert_fake(instance, home, fake)
    return instance


def assert_fake(instance: str, home: Path, fake: Path) -> None:
    """Read test-owned capture before dispatch; production accesses state via Core."""
    view = control.inspect_instance(instance, state_home=home)
    assert view["host_paths"]["claude"] == str(fake)
    assert view["path"] == [str(fake.parent)] or str(fake.parent) in view["path"]
    database = home / "instances" / instance / "state.sqlite3"
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        rows = connection.execute("SELECT body FROM revisions").fetchall()
    assert rows
    found = False
    for row in rows:
        revision = json.loads(row[0])
        for name, binding in (
            json.loads(
                __import__("base64").b64decode(revision["files"]["workflow.json"])
            )
            .get("bindings", {})
            .items()
        ):
            if binding["adapter"] == "claude_print":
                assert Path(revision["executables"][name]).resolve() == fake.resolve()
                found = True
    assert found


def run(
    instance: str, home: Path, fake: Path, *, mode: str = "locked"
) -> dict[str, object]:
    assert_fake(instance, home, fake)
    receipt = control.start_run(
        instance,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
        edit_mode=mode,
    )
    assert receipt["status"] == "applied", receipt
    return control.inspect_run(instance, str(receipt["run_id"]), state_home=home)
