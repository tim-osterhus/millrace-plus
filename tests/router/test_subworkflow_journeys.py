"""Composition with independent child copies and exact input delivery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from subworkflow_support import setup, write_request

from millrace_plus import catalog, subworkflows


def test_two_parents_reuse_definition_with_distinct_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    first, second = setup(tmp_path / "a"), setup(tmp_path / "b")
    # Both select exactly one shared definition; source and sibling stay independent.
    catalog.set_catalog_home(Path(str(first["local"])))
    definition = Path(str(first["definition"]))
    source_digest = catalog.workflow_fingerprint(definition)
    projects = []
    for fixture, text in ((first, "Parent A\n"), (second, "Parent B\r\n")):
        fixture["request"]["inputs"]["request_markdown"] = text
        write_request(fixture)
        monkeypatch.chdir(fixture["project"])
        result = subworkflows.call(Path(str(fixture["file"])))
        assert result["outcome"] == "CHILD_SUCCEEDED", result
        req = fixture["request"]
        saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
        project = Path(saved["paths"]["project"])
        assert (project / "answer.md").read_bytes() == text.encode()
        projects.append(saved)
    assert projects[0]["instance_id"] != projects[1]["instance_id"]
    assert projects[0]["run_id"] != projects[1]["run_id"]
    first_copy = Path(projects[0]["paths"]["workflow"])
    (first_copy / "step.py").write_text("local edited copy")
    assert catalog.workflow_fingerprint(definition) == source_digest
    assert (
        catalog.workflow_fingerprint(Path(projects[1]["paths"]["workflow"]))
        == source_digest
    )


def test_coordinator_two_children_and_independent_parent_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import copy
    import sys
    import uuid

    from subworkflow_support import graph

    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "coordinator")
    root = Path(str(fixture["root"]))
    workflow, project, state = (
        root / "coordinator-flow",
        root / "coordinator-project",
        root / "coordinator-state",
    )
    workflow.mkdir()
    project.mkdir()
    for key in ("left", "right"):
        (project / key).mkdir()
    value = graph()
    value["bindings"] = {
        "helper": {
            "adapter": "command",
            "contract": "millrace-command/v1",
            "executable": str(Path(sys.executable).parent / "millrace-plus"),
            "config": {},
        }
    }
    value["control_files"] = []
    value["limits"] = {"max_invocations": 4, "max_elapsed_seconds": 400}
    node = {
        "invoke": {
            "binding": "helper",
            "cwd": ".",
            "timeout_seconds": 325,
            "config": {
                "args": ["subworkflow", "call", "--request", "call-request.json"]
            },
        },
        "result": "json-v1",
        "outcomes": ["CHILD_SUCCEEDED", "CHILD_UNCERTAIN"],
        "on": {
            "CHILD_SUCCEEDED": {"finish": "succeeded"},
            "CHILD_UNCERTAIN": {"pause": {"reason": "cleanup", "resume": "step"}},
        },
        "on_error": {"pause": {"reason": "error", "resume": "step"}},
        "on_unknown": {"pause": {"reason": "unknown", "resume": "step"}},
    }
    value["planes"] = {
        key: {"workspace": key, "start": "step", "nodes": {"step": copy.deepcopy(node)}}
        for key in ("left", "right")
    }
    (workflow / "workflow.json").write_text(json.dumps(value))
    made = control.create_instance(
        workflow / "workflow.json",
        project,
        state_home=state,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
        env_allowlist=("HOME",),
        path=(str(Path(sys.executable).parent), "/usr/bin", "/bin"),
    )
    assert made["status"] == "applied", made
    for key in ("left", "right"):
        plane = project / key
        request = copy.deepcopy(fixture["request"])
        request.update(
            call_key=key,
            output_ref="results/result.json",
            wait_seconds=300,
            cleanup_seconds=15,
            parent={
                "state_home": str(state),
                "instance_id": made["instance_id"],
                "plane_id": key,
            },
        )
        request["inputs"]["request_markdown"] = key + "\r\n"
        (plane / "call-request.json").write_text(json.dumps(request))
        assert node["invoke"]["cwd"] == "."
        assert (
            node["invoke"]["timeout_seconds"]
            >= request["wait_seconds"] + request["cleanup_seconds"] + 10
        )
    started = control.start_run(
        made["instance_id"],
        state_home=state,
        request_id=uuid.uuid4().hex,
        expected_state_version=1,
    )
    run = control.inspect_run(made["instance_id"], started["run_id"], state_home=state)
    assert run["status"] == "succeeded", run
    ids = []
    for key in ("left", "right"):
        manifest = json.loads((project / key / "results/result.json").read_bytes())
        ids.append(manifest["instance_id"])
        child = Path(fixture["request"]["call_home"]) / key / "project"
        assert (child / "answer.md").read_bytes() == (key + "\r\n").encode()
    assert len(set(ids)) == 2
