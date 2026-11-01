"""Command-only CI/CD proof through the public `millrace-plus` entry point."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cicd_support import (
    config,
    event,
    evidence,
    git,
    make_target,
    new_id,
    peer_executable,
    write_json,
)

ENTRY = (
    "import sys\n"
    "from millrace_plus.router.cli import cli\n"
    "sys.argv[0] = 'millrace-plus'\n"
    "cli()\n"
)
# Blocks Core import for commands that must work from local records alone.
NO_CORE = "import sys\nsys.modules['millrace'] = None\n" + ENTRY


def plus(*args: object, core: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", ENTRY if core else NO_CORE, *map(str, args)],
        capture_output=True,
        text=True,
        timeout=300,
    )


def ok(child: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Router mutators print the request envelope first; the result is last."""
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout.strip().splitlines()[-1])


def refused(child: subprocess.CompletedProcess[str]) -> str:
    assert child.returncode == 2, (child.stdout, child.stderr)
    return str(json.loads(child.stderr.strip().splitlines()[-1])["code"])


def test_usage_lists_cicd() -> None:
    child = plus()
    assert child.returncode == 2
    assert "millrace-plus cicd ..." in child.stderr


def test_init_is_inert_and_freezes_config(tmp_path: Path) -> None:
    target, _ = make_target(tmp_path)
    peer = peer_executable(tmp_path)
    authorized = config(
        target,
        peer,
        external_policy={"mode": "authorized", "effects": ["push"]},
    )
    bad = write_json(tmp_path / "authorized.json", authorized)
    home, state = tmp_path / "home", tmp_path / "state"
    args = ["--target", target, "--state-home", state]
    args += ["--catalog-home", tmp_path / "cat"]
    assert (
        refused(plus("cicd", "init", "--workflow-home", home, "--config", bad, *args))
        == "external_mode_unsupported"
    )
    assert not home.exists()
    assert not (target / ".millrace-plus-target-owner.json").exists()
    good = write_json(tmp_path / "config.json", config(target, peer))
    no_core = plus(
        "cicd", "init", "--workflow-home", home, "--config", good, *args, core=False
    )
    assert refused(no_core) == "core_absent"
    assert not home.exists()
    result = ok(plus("cicd", "init", "--workflow-home", home, "--config", good, *args))
    assert result["start"][:3] == ["millrace-plus", "router", "--state-home"]
    assert (home / "config.json").read_bytes() == good.read_bytes()
    # Init claims ownership only; it starts no run, role or check.
    assert not (home / "project" / "peer-calls.log").exists()
    assert git(target, "status", "--porcelain").splitlines() == [
        "?? .millrace-plus-target-owner.json"
    ]
    replay = ok(plus("cicd", "init", "--workflow-home", home, "--config", good, *args))
    assert replay["instance_id"] == result["instance_id"]
    changed = tmp_path / "changed.json"
    changed.write_text(good.read_text().replace('"src"', '"src", "checks"'))
    conflict = plus("cicd", "init", "--workflow-home", home, "--config", changed, *args)
    assert refused(conflict) == "initialization_conflict"


def test_command_only_useful_repair_and_large_hold(tmp_path: Path) -> None:
    target, head = make_target(tmp_path)
    peer = peer_executable(tmp_path)
    good = write_json(tmp_path / "config.json", config(target, peer))
    home, state = tmp_path / "home", tmp_path / "state"
    init = ok(
        plus(
            "cicd", "init", "--workflow-home", home, "--config", good,
            "--target", target, "--state-home", state,
            "--catalog-home", tmp_path / "cat",
        )
    )
    first = event(tmp_path, head, name="first")
    submitted = ok(plus("cicd", "submit", "--workflow-home", home, "--event", first,
                        core=False))
    assert submitted["receipt"]["disposition"] == "accepted"
    again = ok(plus("cicd", "submit", "--workflow-home", home, "--event", first,
                    core=False))
    assert again["receipt"]["disposition"] == "replayed"
    event_id = submitted["event_id"]

    ok(plus(*init["start"][1:]))  # Foreground controller until the human hold.
    status = ok(plus("cicd", "status", "--workflow-home", home, "--event", event_id,
                     core=False))
    row = status["events"][0]
    assert row["status"] == "held"
    assert row["hold"] == {
        "reason": "publication_requires_human",
        "status": "checked_patch",
    }
    assert git(target, "rev-list", "--count", "HEAD").strip() == "1"

    assert (
        refused(
            plus(
                "cicd", "decide", "--workflow-home", home, "--event", event_id,
                "--decision", "approve-publish", "--request-id", new_id(),
                "--evidence", evidence(tmp_path), core=False,
            )
        )
        == "publication_not_supported"
    )
    second = event(tmp_path, head, name="second", summary="SCENARIO:large", run_id="2")
    queued = ok(plus("cicd", "submit", "--workflow-home", home, "--event", second,
                     core=False))
    ok(
        plus(
            "cicd", "decide", "--workflow-home", home, "--event", event_id,
            "--decision", "close", "--request-id", new_id(),
            "--evidence", evidence(tmp_path), core=False,
        )
    )
    view = ok(
        plus(
            "router", "--state-home", state, "instance", "inspect",
            "--instance", init["instance_id"],
        )
    )
    run_id = view["runs"][-1]["id"]
    ok(
        plus(
            "router", "--state-home", state, "resume-plane",
            "--instance", init["instance_id"], "--run", run_id, "--plane", "main",
        )
    )
    rows = {
        row["event_id"]: row
        for row in ok(
            plus("cicd", "status", "--workflow-home", home, core=False)
        )["events"]
    }
    assert rows[event_id]["status"] == "succeeded"
    assert rows[queued["event_id"]]["status"] == "held"
    assert rows[queued["event_id"]]["hold"]["status"] == "held_large"
    calls = (home / "project" / "peer-calls.log").read_text().split()
    assert calls[0::2] == ["diagnose", "repair", "review", "diagnose"]


def test_default_init_owns_core_store_and_is_inert(tmp_path: Path) -> None:
    """The spec spelling needs no --state-home; Core state lives in the home."""
    target, _ = make_target(tmp_path)
    peer = peer_executable(tmp_path)
    good = write_json(tmp_path / "config.json", config(target, peer))
    home = tmp_path / "home"
    result = ok(
        plus(
            "cicd", "init", "--workflow-home", home, "--target", target,
            "--config", good, "--catalog-home", tmp_path / "cat",
        )
    )
    state = home / "state"
    assert result["start"] == [
        "millrace-plus", "router", "--state-home", str(state),
        "start", "--instance", result["instance_id"],
    ]
    record = json.loads((home / "cicd.json").read_text())
    assert record["state_home"] == str(state)
    view = ok(
        plus(
            "router", "--state-home", state, "instance", "inspect",
            "--instance", result["instance_id"],
        )
    )
    assert view["runs"] == []  # Registered only; nothing started.
    assert not (home / "project" / "peer-calls.log").exists()
    assert git(target, "status", "--porcelain").splitlines() == [
        "?? .millrace-plus-target-owner.json"
    ]
    # The same spelling replays the frozen default store.
    again = ok(
        plus(
            "cicd", "init", "--workflow-home", home, "--target", target,
            "--config", good, "--catalog-home", tmp_path / "cat",
        )
    )
    assert again["instance_id"] == result["instance_id"]
