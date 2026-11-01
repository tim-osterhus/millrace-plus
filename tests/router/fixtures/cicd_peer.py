"""Deterministic CI/CD role peer. It stands in for a native role offline.

It reads the same role input a native agent receives, writes artifacts in the
same schemas and makes a real target edit for repair. The scenario comes from
the event summary only because this is a test double; product code never takes
authority from event text. Each call is appended to `<project>/peer-calls.log`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from millrace_plus.cicd_records import digest
from millrace_plus.cicd_source import snapshot


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _ref(path: Path, workspace: Path) -> dict:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _verified(workspace: Path, ref: dict) -> Path:
    path = workspace / ref["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]
    return path


def _move_head(target: Path) -> None:
    """Same-tree revision movement: an empty commit leaves every file unchanged."""
    subprocess.run(
        ["git", "-C", str(target), "-c", "user.name=Peer",
         "-c", "user.email=peer@example.invalid",
         "commit", "--allow-empty", "--quiet", "-m", "moved"],
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        check=True,
        capture_output=True,
        timeout=30,
    )


def _emit(outcome: str, output: str, summary: str) -> None:
    print(json.dumps({"outcome": outcome, "summary": summary, "refs": [output]}))


def diagnose(data: dict, workspace: Path, scenario: str, output: Path) -> str:
    evidence = [data["log_ref"]] if data["log_ref"] else [data["event_ref"]]
    outcome, paths, summary = "SMALL_REPAIR", ["src/calc.py"], "Use addition."
    if scenario == "large":
        outcome, paths, summary = "LARGE_ISSUE", [], None
    elif scenario == "noaction":
        outcome, paths, summary = "NO_ACTION", [], None
    elif scenario == "inject":
        # Follows the hostile log literally; the guard must refuse the scope.
        paths = ["checks/check_calc.py"]
    elif scenario in {"mutate-noaction", "mutate-large"}:
        # A misbehaving diagnosis edits the target, then claims no repair.
        notes = Path(data["target_root"]) / "src" / "notes.txt"
        notes.write_text(notes.read_text() + "edited during diagnosis\n")
        outcome = "NO_ACTION" if scenario == "mutate-noaction" else "LARGE_ISSUE"
        paths, summary = [], None
    elif scenario == "headmove-diagnose":
        _move_head(Path(data["target_root"]))
    artifact = {
        "format": "millrace-plus-cicd-diagnosis/v1",
        "event_id": data["event_id"],
        "head_sha": data["head_sha"],
        "classification": outcome,
        "cause": "add() subtracts its operands; the CI log shows add(2, 3) == -1.",
        "proposed_paths": paths,
        "repair_summary": summary,
        "evidence_refs": evidence,
    }
    output.write_text(json.dumps(artifact))
    return outcome


def repair(data: dict, workspace: Path, scenario: str, output: Path) -> str:
    if scenario == "blocked":
        return "BLOCKED"
    target = Path(data["target_root"])
    calc = target / "src" / "calc.py"
    replacement = "a * b" if scenario == "wrongfix" else "a + b"
    calc.write_text(calc.read_text().replace("a - b", replacement))
    changed = ["src/calc.py"]
    if scenario == "outside":
        (target / "README.md").write_text("unrelated edit\n")
    notes = output.parent / "notes.md"
    notes.write_text("Replaced subtraction with addition in add().\n")
    artifact = {
        "format": "millrace-plus-cicd-repair/v1",
        "event_id": data["event_id"],
        "config_sha256": data["config_sha256"],
        "source_before_sha256": data["source_before_sha256"],
        "source_after_sha256": digest(snapshot(target)),
        "changed_paths": changed,
        "summary": "Correct the add() operator.",
        "evidence_refs": [_ref(notes, workspace)],
    }
    output.write_text(json.dumps(artifact))
    return "PATCH_READY"


def review(data: dict, workspace: Path, scenario: str, output: Path) -> str:
    if scenario == "headmove-review":
        _move_head(Path(data["target_root"]))
    patch = _verified(workspace, data["patch_ref"])
    batch = _load(_verified(workspace, data["checks_ref"]))
    findings = []
    if batch["status"] != "PASS" or b"+    return a + b" not in patch.read_bytes():
        findings.append(
            {"id": "F1", "text": "Patch does not fix add().", "evidence_refs": []}
        )
    if scenario == "reject":
        findings.append({"id": "F2", "text": "Reviewer rejects.", "evidence_refs": []})
    verdict = "REJECT" if findings else "ACCEPT"
    artifact = {
        "format": "millrace-plus-cicd-review/v1",
        "event_id": data["event_id"],
        "config_sha256": data["config_sha256"],
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "checks_ref": data["checks_ref"],
        "verdict": verdict,
        "findings": findings,
    }
    output.write_text(json.dumps(artifact))
    return verdict


def main() -> None:
    role = sys.argv[1]
    workspace = Path.cwd()
    pointer = _load(workspace / "active.json")
    data = _load(workspace / pointer["input"])
    assert pointer["role"] == role == data["role"]
    _verified(workspace, data["prompt_ref"])
    _verified(workspace, data["skill_ref"])
    summary = _load(_verified(workspace, data["event_ref"]))["summary"]
    scenario = ""
    if "SCENARIO:" in summary:
        scenario = summary.split("SCENARIO:", 1)[1].split()[0]
    with (workspace.parent / "peer-calls.log").open("a") as stream:
        stream.write(f"{role} {data['event_id']}\n")
    if scenario == "crash-" + role:
        sys.exit(3)  # Simulates a role process failing after reservation.
    output = workspace / data["output"]
    outcome = {"diagnose": diagnose, "repair": repair, "review": review}[role](
        data, workspace, scenario, output
    )
    _emit(outcome, data["output"] if output.exists() else pointer["input"], role)


if __name__ == "__main__":
    main()
