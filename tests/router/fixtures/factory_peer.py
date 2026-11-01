"""Inert role peer. Product code owns actual guards, checks, transfer and joins."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path

from millrace_plus import check_contracts as cc
from millrace_plus.cicd_source import snapshot_policy, source_identity
from millrace_plus.factory_records import subject_id


def main():
    pointer = json.loads(Path("active-role.json").read_bytes())
    input_path = Path(pointer["input_ref"]["path"])
    root = input_path.parent

    def read(ref):
        raw = (root / ref["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
        return json.loads(raw)

    document = json.loads(input_path.read_bytes())
    role = pointer["role"]
    if role == "review":
        frozen = read(document["candidate_ref"])
        config = read(document["config_ref"])
        target = Path(config["target_root"])
        context = read(document["factory_context_ref"])
        subject = read(context["input_ref"])
        obligations = read(context["obligations_ref"])
        assert subject["criteria"]
        # The second job must see the first job's exact accepted obligation.
        if "answer()" in subject["request"]:
            assert any("sum" in row["id"] for row in obligations["requirements"])
            assert (target / "src/calc.py").read_text().endswith("return a + b\n")
            assert (target / "answer.py").exists()
        artifact = {
            "format": "millrace-plus-cicd-review/v2",
            "input_id": None,
            "candidate_id": frozen["candidate_id"],
            "materialization_id": document["materialization_id"],
            "attempt_id": document["attempt_id"],
            "candidate_sha256": source_identity(
                snapshot_policy(
                    target, cc.validate_path_policy(read(config["path_policy_ref"]))
                )
            ),
            "patch_ref": frozen["delta_ref"],
            "checks_refs": document["checks_refs"],
            "verdict": "REJECT" if "review reject" in subject["request"] else "ACCEPT",
            "findings": [],
            "evidence_refs": [document["factory_context_ref"]],
        }
        outcome = artifact["verdict"]
    else:
        subject = read(document["subject_ref"])
        assert document["input_id"] == subject_id(subject)
        common = {
            "input_id": document["input_id"],
            "attempt_id": document["attempt_id"],
        }
        if role == "investigate":
            regression = "regress retained" in subject["request"]
            lad = "answer()" in subject["request"] and not regression
            artifact = {
                "format": "millrace-plus-cicd-assessment/v2",
                **common,
                "subject_ref": document["subject_ref"],
                "reproduction_refs": document["reproduction_refs"],
                "hypotheses": [
                    {
                        "id": "original-request",
                        "claim": subject["request"],
                        "status": "supported",
                        "evidence_refs": [document["criteria_ref"]],
                    }
                ],
                "proposed_paths": []
                if any(
                    text in subject["request"] for text in ("no action", "human hold")
                )
                else ["answer.py", "src/calc.py"]
                if regression
                else ["answer.py"]
                if lad
                else ["src/calc.py"],
                "change_classes": [],
                "risk": {
                    "impact": "local function",
                    "likelihood": "deterministic",
                    "scenario": "original criterion",
                    "evidence_refs": [document["criteria_ref"]],
                },
                "route": "NO_ACTION"
                if "no action" in subject["request"]
                else "HUMAN_HOLD"
                if "human hold" in subject["request"]
                else "LAD_CANDIDATE"
                if lad
                else "REPAIR_ALLOWED",
                "evidence_refs": [document["criteria_ref"]],
            }
            outcome = "ASSESSED"
        else:
            target = Path(document["target_root"])
            path = target / "src/calc.py"
            before = path.read_text()
            replacement = (
                "return a + b + 1"
                if "bad addition" in subject["request"]
                else "return a + b"
            )
            regression = "regress retained" in subject["request"]
            after = (
                before.replace("return a + b", "return a + b + 1")
                if regression
                else before.replace("return a - b", replacement)
            )
            path.write_text(after)
            patch = "".join(
                difflib.unified_diff(
                    before.splitlines(True),
                    after.splitlines(True),
                    fromfile="a/src/calc.py",
                    tofile="b/src/calc.py",
                )
            )
            changed_paths = ["src/calc.py"]
            if regression:
                answer = "def answer():\n    return 42\n"
                (target / "answer.py").write_text(answer)
                patch += "".join(
                    difflib.unified_diff(
                        [],
                        answer.splitlines(True),
                        fromfile="a/answer.py",
                        tofile="b/answer.py",
                    )
                )
                changed_paths = ["answer.py", "src/calc.py"]
            if "forbidden product" in subject["request"]:
                (target / "build").mkdir(exist_ok=True)
                (target / "build/noncheck.txt").write_text("undeclared native product")
            patch_file = root / (document["attempt_id"] + ".patch")
            patch_file.write_text(patch)
            config = read(document["config_ref"])
            artifact = {
                "format": "millrace-plus-cicd-repair/v2",
                **common,
                "assessment_ref": document["assessment_ref"],
                "source_before_sha256": document["source_before_sha256"],
                "source_after_sha256": source_identity(
                    snapshot_policy(
                        target, cc.validate_path_policy(read(config["path_policy_ref"]))
                    )
                ),
                "patch_ref": {
                    "path": patch_file.name,
                    "sha256": hashlib.sha256(patch.encode()).hexdigest(),
                },
                "changed_paths": changed_paths,
                "change_classes": [],
                "summary": "implement original sum criterion",
                "evidence_refs": [],
            }
            outcome = "PATCH_READY"
    (root / document["output"]).write_text(json.dumps(artifact))
    print(json.dumps({"outcome": outcome, "summary": "inert role peer", "refs": []}))


if __name__ == "__main__":
    main()
