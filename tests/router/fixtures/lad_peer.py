"""Inert role peer: useful code and requirement/evidence Markdown, no providers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lad_state import ARTIFACTS, canonical, digest, load, source


def result_for(
    workspace: Path, role: str, outcome: str | None = None, scenario: str = "clean"
) -> dict:
    foreground = (
        workspace if workspace.name == "foreground" else workspace.parent / "foreground"
    )
    item = json.loads((workspace / "lad-state/active-role.json").read_bytes())
    assert item["role"] == role
    assert Path(item["workspace_root"]) == workspace.resolve()
    assert Path(item["foreground_root"]) == foreground.resolve()
    guidance_root = Path(item["guidance_root"])
    assert guidance_root.is_absolute()
    assert (guidance_root / "context/role-contracts.md").is_file()
    assert (guidance_root / "templates/result.md").is_file()
    assert (guidance_root / f"prompts/{role}.md").is_file()
    manifest = json.loads((guidance_root / "manifest.json").read_bytes())
    assert manifest["guidance_sha256"] == item["guidance_sha256"]
    assert manifest["files"] == item["guidance_files"]
    assert digest(canonical(manifest)) == item["guidance_snapshot_sha256"]
    assert (
        guidance_root
        == workspace / f"lad-state/occurrences/{item['occurrence_id']}/guidance"
    )
    for name, checksum in manifest["files"].items():
        assert digest((guidance_root / name).read_bytes()) == checksum
    oid = item["occurrence_id"]
    lineage = item["lineage"]
    output = workspace / "lad-results" / oid
    output.mkdir(parents=True, exist_ok=True)
    if role == "checker":
        (output / "baseline.md").write_text(
            "# Frozen requirement-side expectations\n\n"
            f"Occurrence: {oid}\nSession: {item['session_id']}\n\n"
            "## Evidence\nThe original request says answer() returns 42. "
            "Before reading target code or Builder evidence, require a callable "
            "answer with zero arguments returning integer 42. Check this by "
            "loading the module and calling answer(). No exceptions were supplied. "
            f"Requirement-side packet criteria: {item['packet']['criteria']}. "
            f"Guidance: {item['guidance_sha256']}. "
            "Supplied source digest is metadata only: "
            f"{item['source_sha256']}.\n\n## Outcome\n"
            "Baseline frozen before implementation exposure; do not rewrite.\n"
        )
    state = load(foreground)
    for accepted_id, ref in item["accepted_input_refs"].items():
        assert ref["sha256"] == item["accepted_inputs"][accepted_id]
        selected_root = foreground.parent / ref["workspace"]
        selected_result = json.loads((selected_root / ref["path"]).read_bytes())
        assert selected_result["role"] == ref["role"]
        assert digest(canonical(selected_result)) == ref["sha256"]
    data = {}
    findings = []
    if role == "planner":
        data["criteria"] = [
            {
                "id": "answer",
                "text": "answer() returns 42",
                "request_quote": "answer() returns 42",
            }
        ]
    if role == "manager":
        original = [
            {
                key: row[key]
                for key in (
                    "id",
                    "lineage",
                    "criteria",
                    "depends_on",
                    "paths",
                    "input_refs",
                    "markdown",
                )
            }
            for row in state["plan"]
        ]
        data["plan"] = original or [
            {
                "id": "answer",
                "lineage": "answer",
                "criteria": ["answer"],
                "depends_on": [],
                "paths": ["answer.py"],
                "input_refs": [],
                "markdown": "packet.md",
            }
        ]
        data["affected_lineages"] = (
            ["answer"]
            if state["pending"] and state["pending"]["kind"] == "remediation"
            else []
        )
    if role == "builder":
        (foreground / "target").mkdir(exist_ok=True)
        (foreground / "target/answer.py").write_text(
            "def answer():\n    return 0\n"
            if scenario in {"repair", "gate", "unresolved"}
            else "def answer():\n    return 42\n"
        )
    if role == "fixer":
        data.update(baseline=item["baseline"], finding_ids=item["finding_ids"])
        count = state["usage"].get("fixer:" + lineage, 0)
        fixed = scenario == "repair" or (scenario == "gate" and count >= 2)
        (foreground / "target/answer.py").write_text(
            "def answer():\n    return 42\n"
            if fixed
            else "def answer():\n    return 0\n"
        )
    if role in {"checker", "doublechecker"}:
        data["criteria_checked"] = ["answer"]
        if role == "checker" and item["baseline"]:
            data["baseline"] = item["baseline"]
        if role == "doublechecker":
            data.update(baseline=item["baseline"], fixer=item["fixer"])
        namespace = {}
        code = (foreground / "target/answer.py").read_text()
        exec(compile(code, "answer.py", "exec"), namespace)
        observed = namespace["answer"]()
        data["observed_answer"] = observed
        if observed != 42:
            outcome = outcome or "FINDINGS_ACTIONABLE"
            findings = [
                {
                    "id": "wrong-answer",
                    "criterion": "answer",
                    "path": "answer.py",
                    "reproduction": "answer() == 0",
                    "evidence": "Independent execution found zero rather than 42.",
                }
            ]
    if role == "troubleshooter":
        data.update(resume_role=state["pending"]["target"], repair_verified=True)
        outcome = outcome or (
            "PASS_REVIEW_REPAIRED"
            if state["pending"]["kind"] == "review"
            else "PASS_PREREQUISITE_REPAIRED"
        )
        if state["pending"]["kind"] == "review":
            (foreground / "target/answer.py").write_text(
                "def answer():\n    return 42\n"
            )
    if role == "updater":
        data.update(
            review=item["review"],
            criteria_evidence={
                "answer": "Independent passing review and executable answer() "
                "check at current source."
            },
        )
    if role == "learning":
        data["pending_route"] = state["pending"]
    if role == "arbiter":
        data["verdict"] = {
            "answer": {
                "status": "gap" if outcome == "FINDINGS" else "pass",
                "evidence": "Original request requires answer() == 42; "
                "current target and accepted review agree.",
            }
        }
    outcome = outcome or "PASS"
    if outcome.startswith("ERROR") or outcome == "ERROR":
        data["failure"] = {
            "operation": "local check",
            "attempted": "Inspected exact local prerequisite",
            "evidence": "Pinned local check fixture records failed prerequisite "
            "and its resolution.",
            "prerequisite_resolved": outcome
            in {"ERROR_TRANSIENT_RESOLVED", "ERROR_REPLACEMENT"},
        }
    human = None
    if outcome in {"HUMAN_BLOCKED", "NO_SAFE_ROUTE"}:
        human = {
            "type": "human_choice",
            "obstacle": "The original task leaves a human-owned choice unresolved.",
            "attempted": "Read original request and relevant evidence; "
            "no existing authority selects the choice.",
            "required_resolution": "Operator must select the requested behavior.",
        }
    names = list(ARTIFACTS[role])
    if role == "manager":
        names += [row["markdown"] for row in data["plan"]]
    artifacts = []
    details = {
        "recon": "Existing target file inventory and dirty ownership: "
        + json.dumps(item["source_files"], sort_keys=True)
        + ". The fixture has no prior application behavior or dirty files "
        "outside this inventory. The original request selects a zero-argument "
        "answer function returning 42. Planner must preserve that criterion.",
        "planner": "Specification: create answer.py with answer() returning integer "
        "42. Original criterion quote and stable rubric: "
        + json.dumps(data.get("criteria")),
        "manager": "Whole-plan check: all original criteria are owned, packet paths "
        "are explicit, dependencies precede consumers, and execution is sequential. "
        "Draft plan: " + json.dumps(data.get("plan"), sort_keys=True),
        "builder": "Implemented only the assigned answer.py. The source digest and "
        "subsequent independent review retain the exact observed implementation.",
        "checker": "The requirement-side baseline was written first. Independent "
        "module execution observed answer()="
        + str(data.get("observed_answer"))
        + "; expected 42. Stable findings: "
        + json.dumps(findings, sort_keys=True),
        "fixer": "Repair preserves the frozen baseline and assigned findings: "
        + json.dumps(data.get("finding_ids"))
        + ". The next independent review "
        "must inspect the current implementation; this repair does not accept itself.",
        "doublechecker": "Independent module execution observed answer()="
        + str(data.get("observed_answer"))
        + "; expected 42. Exact inspected Fixer: "
        + json.dumps(item["fixer"], sort_keys=True)
        + ". Findings: "
        + json.dumps(findings, sort_keys=True),
        "troubleshooter": "Diagnosed the saved interrupted role, preserved its "
        "original scope and baseline, and checked its bounded prerequisite/review "
        "repair. Exact saved context: " + json.dumps(state["pending"], sort_keys=True),
        "consultant": "The safe planning repair is a sufficient specification then "
        "an ordered Manager plan for the unchanged answer() == 42 criterion. "
        "An execution repair retains the exact interrupted role; no new scope or "
        "budget is approved here. Saved context: "
        + json.dumps(state["pending"], sort_keys=True),
        "updater": "Completion indexes every packet criterion against the current "
        "passing review. Exact review and evidence: "
        + json.dumps(data, sort_keys=True),
        "learning": "The committed request contains these two actual repair/review "
        "pairs and the unchanged pending route: "
        + json.dumps(state["pending"], sort_keys=True)
        + ". The first repair retained the wrong answer; the second evaluation "
        "records its observed outcome. Existing guidance already requires targeted "
        "verification against 42, so the fixture supports noncompliance rather "
        "than a missing instruction. No instruction candidate is proposed. "
        "Keep guidance inactive and restore the exact saved route.",
        "arbiter": "Original request requires answer() returns 42; packet completion "
        "alone is insufficient. Per-criterion current-source verdict: "
        + json.dumps(data.get("verdict"), sort_keys=True),
    }
    for name in names:
        content = (
            f"# {role} observed evidence\n\nSession: {state['session_id']}\n"
            f"Occurrence: {oid}\n"
            f"Original criterion: answer() returns 42.\n\n## Evidence\n"
            "Read the exact original request and requirement-side packet. "
            "For review, derive expected return value 42 before inspecting "
            "implementation. Execute the local answer function and compare its "
            "return value to 42; retain failed checks. "
            "No provider calls, external services, commits or publication occurred.\n\n"
            + details[role]
            + "\n\n"
            + f"Source: {digest(canonical(source(foreground)))}\n"
            f"Input: {item['input_sha256']}\n\n## Outcome\n{outcome}; "
            "the occurrence result contains exact identities and structured evidence.\n"
        )
        path = output / name
        path.parent.mkdir(exist_ok=True, parents=True)
        if name != "baseline.md" or not path.exists():
            path.write_text(content)
        artifacts.append({"path": name, "sha256": digest(path.read_bytes())})
    result = {
        "format": "millrace-lad-result/v1",
        "session_id": state["session_id"],
        "occurrence_id": oid,
        "role": role,
        "outcome": outcome,
        "lineage": lineage,
        "input_sha256": item["input_sha256"],
        "source_sha256": digest(canonical(source(foreground))),
        "guidance_sha256": item["guidance_sha256"],
        "artifacts": artifacts,
        "findings": findings if outcome != "PASS" else [],
        "human_evidence": human,
        "data": data,
    }
    (output / "result.json").write_bytes(canonical(result) + b"\n")
    return result


if __name__ == "__main__":
    role, scenario = sys.argv[1:3]
    if scenario == "technical_once":
        selected = json.loads(Path("lad-state/active-role.json").read_bytes())
        if selected["replacement_of"] is None:
            raise SystemExit(7)
    result = result_for(
        Path.cwd(),
        role,
        outcome=sys.argv[3] if len(sys.argv) > 3 else None,
        scenario=scenario,
    )
    print(
        json.dumps(
            {
                "outcome": result["outcome"],
                "summary": "Inert local role evidence",
                "refs": [f"lad-results/{result['occurrence_id']}/result.json"],
            }
        )
    )
