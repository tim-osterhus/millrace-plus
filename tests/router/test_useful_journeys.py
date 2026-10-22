"""Installed software-change routes through an inert peer and checked artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from test_distribution import Env  # noqa: F401
from test_distribution import artifacts as artifacts


@pytest.mark.parametrize("scenario", ["repair", "clean"])
def test_installed_software_change_useful_route(
    artifacts: dict[str, Path],  # noqa: F811 - imported pytest fixture
    scenario: str,
) -> None:
    env = Env(
        artifacts["root"] / f"software-change-{scenario}",
        [artifacts["core_wheel"], artifacts["plus_wheel"]],
    )
    console = env.root / "bin/millrace-plus"
    workflow = env.cwd / "workflow"
    project = env.cwd / "project"
    state = env.cwd / "state"
    request = "Original goal: implement answer() returning 42.\n"
    env.run(
        str(console),
        "catalog",
        "init",
        "bundled:software-change",
        str(workflow),
        check=True,
    )
    graph = json.loads((workflow / "workflow.json").read_text())
    for node, spec in graph["planes"]["main"]["nodes"].items():
        prompt = spec["invoke"]["config"].get("prompt")
        if prompt:
            path = workflow / prompt
            path.write_text(f"TEST_NODE:{node}\n" + path.read_text())

    project.mkdir()
    (project / "request.md").write_text(request)
    (project / "case.json").write_text(
        json.dumps({"scenario": scenario, "product": "result.py"})
    )
    fake = env.cwd / "fake-claude"
    peer = Path(__file__).with_name("fixtures") / "workflow_peer.py"
    fake.write_text(f"#!{env.python}\n" + peer.read_text())
    fake.chmod(0o700)

    versions = env.py(
        "import importlib.metadata as m, json; "
        "print(json.dumps({n: json.loads(m.distribution(n).read_text("
        "'direct_url.json'))['url'] "
        "for n in ('millrace-ai', 'millrace-plus')}))"
    )
    assert json.loads(versions.stdout) == {
        "millrace-ai": artifacts["core_wheel"].as_uri(),
        "millrace-plus": artifacts["plus_wheel"].as_uri(),
    }

    created = env.run(
        str(console),
        "router",
        "instance",
        "create",
        "--workflow",
        str(workflow / "workflow.json"),
        "--project",
        str(project),
        "--state-home",
        str(state),
        "--host-paths",
        json.dumps({"claude": str(fake), "python3": str(env.python)}),
        check=True,
    )
    instance = json.loads(created.stdout.splitlines()[-1])["instance_id"]
    selected = env.py(
        "import base64, json, sqlite3; from pathlib import Path\n"
        f"instance = {instance!r}\n"
        "db = Path.cwd() / 'state/instances' / instance / 'state.sqlite3'\n"
        "with sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True) as c:\n"
        "    rows = c.execute('SELECT body FROM revisions').fetchall()\n"
        "assert rows\nselected = []\n"
        "for (body,) in rows:\n"
        "    revision = json.loads(body)\n"
        "    graph = json.loads(base64.b64decode(revision['files']['workflow.json']))\n"
        "    for name, binding in graph.get('bindings', {}).items():\n"
        "        if binding.get('adapter') == 'claude_print':\n"
        "            selected.append(revision['executables'][name])\n"
        f"assert selected and all(Path(value).resolve() == Path({str(fake)!r}).resolve() for value in selected), selected\n"  # noqa: E501 - embedded isolated proof
        "print(json.dumps(selected))\n"
    )
    captured = json.loads(selected.stdout)
    assert captured and all(
        Path(value).resolve() == fake.resolve() for value in captured
    )

    started = env.run(
        str(console),
        "router",
        "start",
        "--instance",
        instance,
        "--state-home",
        str(state),
        check=True,
    )
    run_id = json.loads(started.stdout.splitlines()[-1])["run_id"]
    status = env.run(
        str(console),
        "router",
        "status",
        "--instance",
        instance,
        "--state-home",
        str(state),
        "--run",
        str(run_id),
        check=True,
    )
    view = json.loads(status.stdout.splitlines()[-1])
    expected_nodes = (
        ["check_inputs", "work", "check", "repair", "recheck", "goal_review"]
        if scenario == "repair"
        else ["check_inputs", "work", "check", "goal_review"]
    )
    assert view["status"] == "succeeded", view
    assert [item["node"] for item in view["invocations"]] == expected_nodes

    invocations = [
        json.loads(line)
        for line in (project / "invocations.jsonl").read_text().splitlines()
    ]
    assert [item["node"] for item in invocations] == expected_nodes[1:]
    assert all(
        Path(item["executable"]).resolve() == fake.resolve() for item in invocations
    )
    product = project / "result.py"
    assert product.read_text() == "def answer():\n    return 42\n"
    compile(product.read_text(), str(product), "exec")
    assert (project / "work/check.md").is_file()
    if scenario == "repair":
        assert (project / "work/recheck.md").is_file()
        assert "REVISE" in (project / "work/check.md").read_text()
        assert "ACCEPT" in (project / "work/recheck.md").read_text()
    else:
        assert not (project / "work/recheck.md").exists()
        assert "ACCEPT" in (project / "work/check.md").read_text()
    goal_review = (project / "work/goal-review.md").read_text()
    assert request in goal_review and "Goal: ACCEPT" in goal_review
    (env.cwd / "journey-proof.json").write_text(
        json.dumps(
            {
                "scenario": scenario,
                "artifact_wheels": {
                    "core": artifacts["core_wheel"].as_uri(),
                    "plus": artifacts["plus_wheel"].as_uri(),
                },
                "instance_id": instance,
                "run_id": run_id,
                "status": view["status"],
                "ordered_nodes": expected_nodes,
                "fake_executable": str(fake),
                "product_sha256": hashlib.sha256(product.read_bytes()).hexdigest(),
                "goal_review_sha256": hashlib.sha256(goal_review.encode()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )


def test_inert_peer_refuses_unmarked_prompt_outside_fixture(tmp_path: Path) -> None:
    peer = tmp_path / "workflow_peer.py"
    peer.write_bytes(
        Path(__file__).with_name("fixtures").joinpath("workflow_peer.py").read_bytes()
    )
    project = tmp_path / "ordinary-project"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-I", str(peer)],
        input="Review this ordinary request without fixture metadata.\n",
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert result.stdout == "", "An unmarked prompt must not yield structured success"
    assert list(project.iterdir()) == [], "Refusal must precede any fixture side effect"


@pytest.mark.parametrize("slots", [1, 3, 5])
def test_qualification_copy_cap_bounds_fake_native_starts(
    tmp_path: Path, slots: int
) -> None:
    """The qualification recipe spends one command plus at most allocated mains."""
    from catalog_support import create, run, stock

    from millrace_plus.router import control

    workflow, project, home, fake = stock(tmp_path, "software-change", "repair")
    assert fake.resolve().is_relative_to(tmp_path.resolve())
    assert fake.name == "fake-claude"
    graph = json.loads(workflow.read_text())
    graph["limits"] = {
        "max_invocations": 1 + slots,
        "max_elapsed_seconds": 60,
        "max_parallel_invocations": 1,
    }
    preflight = graph["planes"]["main"]["nodes"]["check_inputs"]
    preflight["on_error"] = {"pause": {"reason": "qualification_preflight_error"}}
    preflight["on_unknown"] = {"pause": {"reason": "qualification_preflight_unknown"}}
    preflight["on"]["NEEDS_INPUT"] = {
        "pause": {"reason": "qualification_request_missing"}
    }
    workflow.write_text(json.dumps(graph))
    instance = create(workflow, project, home, fake)
    # run() also verifies every captured native executable equals this owned fake.
    view = run(instance, home, fake)
    starts = (project / "invocations.jsonl").read_text().splitlines()
    assert len(starts) == slots
    assert view["count"] == 1 + slots
    assert [json.loads(line)["node"] for line in starts] == [
        "work",
        "check",
        "repair",
        "recheck",
        "goal_review",
    ][:slots]
    assert view["status"] == ("succeeded" if slots == 5 else "paused"), view
    if slots < 5:
        assert view["pause_reason"] == "limit_exhausted"
        for operation in (control.recover_run, control.resume_run):
            current = control.inspect_run(instance, str(view["id"]), state_home=home)
            operation(
                instance,
                str(view["id"]),
                state_home=home,
                request_id=uuid.uuid4().hex,
                expected_state_version=int(current["state_version"]),
            )
        assert (project / "invocations.jsonl").read_text().splitlines() == starts
        control.cancel_run(
            instance,
            str(view["id"]),
            state_home=home,
            request_id=uuid.uuid4().hex,
        )
    assert (project / "invocations.jsonl").read_text().splitlines() == starts
    assert not (project / ".millrace-router-owner" / "owner.json").exists()
