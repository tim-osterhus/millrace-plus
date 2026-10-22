"""Inert Claude protocol peer for explicit-main stock graphs."""

import json
import re
import sys
from pathlib import Path

if "--version" in sys.argv:
    print("2.1.296 (Claude Code)")
    raise SystemExit(0)
prompt = sys.stdin.read()
node = re.search(r"TEST_NODE:([a-z_]+)", prompt).group(1)
project = Path.cwd()
options = json.loads((project / "case.json").read_text())
scenario = options.get("scenario", "clean")
report = project / "work"
report.mkdir(exist_ok=True)
with (project / "invocations.jsonl").open("a") as stream:
    stream.write(json.dumps({"node": node, "executable": sys.argv[0]}) + "\n")
product = project / options.get("product", "document.md")
accepted = (
    "def answer():\n    return 42\n"
    if product.suffix == ".py"
    else "accepted content\n"
)
if node == "work":
    product.write_text(
        "broken\n" if scenario in {"repair", "recheck_fail"} else accepted
    )
    outcome = "PLANNING_DEFECT" if scenario == "planning_defect" else "READY"
    if options.get("edit"):
        workflow = Path(options["edit"])
        graph = json.loads(workflow.read_text())
        graph["planes"]["main"]["nodes"]["extra"] = dict(
            graph["planes"]["main"]["nodes"]["check_inputs"]
        )
        graph["planes"]["main"]["nodes"]["extra"]["on"] = {
            "READY": {"goto": "check"},
            "NEEDS_INPUT": {"pause": {"reason": "extra_input", "resume": "extra"}},
        }
        graph["planes"]["main"]["nodes"]["work"]["on"]["READY"] = {"goto": "extra"}
        workflow.write_text(json.dumps(graph))
        (workflow.parent / "prompts/check.md").write_text(
            (workflow.parent / "prompts/check.md").read_text() + "\nEdited copy.\n"
        )
        script = workflow.parent / "scripts/check_inputs.py"
        script.write_text(script.read_text() + "\n# Edited copy script.\n")
elif node in {"check", "recheck"}:
    outcome = "REVISE" if product.read_text() == "broken\n" else "ACCEPT"
    (report / f"{node}.md").write_text(f"Independent {node}: {outcome}\n")
elif node == "repair":
    if scenario != "recheck_fail":
        product.write_text(accepted)
    outcome = "READY"
elif node == "goal_review":
    outcome = "REVISE" if scenario == "goal_revise" else "ACCEPT"
    (report / "goal-review.md").write_text(
        (project / "request.md").read_text() + f"\nGoal: {outcome}\n"
    )
elif node == "diagnose":
    (report / "diagnosis.md").write_text("Original criteria require clarification.\n")
    outcome = "DIAGNOSED"
else:
    raise AssertionError(node)
result = {"outcome": outcome, "summary": f"inert {node}", "refs": []}
print(
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "stop_reason": "end_turn",
            "terminal_reason": "completed",
            "api_error_status": None,
            "permission_denials": [],
            "session_id": "00000000-0000-4000-8000-000000000001",
            "modelUsage": {},
            "result": "ignored",
            "structured_output": result,
        }
    )
)
