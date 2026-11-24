"""Inert scripted Claude CLI peer for the LAD provider-wait graph (no provider).

Copied by ``lad_provider_support.fake_cli`` next to a shell launcher and a
sibling ``<payload>.json`` options file:

``{"version": TEXT, "plan": [STEP, ...], "counter": PATH, "peer": PATH,
"scenario": NAME}``

``--version`` prints the supported version banner. Each prompt run consumes the
next plan step (the last step repeats): an integer emits the pinned typed
terminal error envelope (``type: result``, ``is_error: true``, integer
``api_error_status``, exit 0), so the real ``ClaudePrintAdapter`` produces
genuine ``claude_native_error`` evidence and a v2 bridge export; ``"success"``
writes valid LAD role evidence with the existing ``lad_peer.result_for`` and
emits a successful envelope whose ``structured_output`` is the Core json-v1
result. A ``{"status": N, "readonly_outbox": true}`` step first makes this
context's outbox unwritable (publication unavailable). No network, account or
provider is used. A ``{"outcome": NAME}`` step is a success whose valid role
result declares ``NAME``.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

options = json.loads(Path(sys.argv[0] + ".json").read_text())
if "--version" in sys.argv:
    os.write(1, options["version"].encode())
    sys.exit(0)
sys.stdin.buffer.read()
counter = Path(options["counter"])
used = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(used + 1))
plan = options["plan"]
step = plan[min(used, len(plan) - 1)]
base = {
    "type": "result",
    "stop_reason": "end_turn",
    "permission_denials": [],
    "session_id": "00000000-0000-4000-8000-000000000001",
    "modelUsage": {},
    "result": "",
}
outcome = None
if isinstance(step, dict) and "outcome" in step:
    # A valid role result with this declared outcome instead of the default.
    outcome, step = step["outcome"], "success"
if isinstance(step, dict):
    # Unavailable handoff: the outbox for this context cannot accept the export,
    # so the adapter keeps native facts with a null bridge ref.
    context = json.loads(Path("lad-state/native-context.json").read_bytes())
    blocked = Path("lad-state/native-diagnostics") / context["context_id"]
    blocked.mkdir(parents=True, exist_ok=True)
    blocked.chmod(0o500)
    step = step["status"]
if isinstance(step, int) and not isinstance(step, bool):
    envelope = {
        **base,
        "subtype": "error_during_execution",
        "is_error": True,
        "terminal_reason": "error",
        "api_error_status": step,
    }
    os.write(1, json.dumps(envelope).encode())
    sys.exit(0)
if step != "success":
    sys.exit(7)  # an ordinary stopped native error without a terminal envelope
spec = importlib.util.spec_from_file_location("lad_peer", options["peer"])
peer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peer)
role = json.loads(Path("lad-state/active-role.json").read_bytes())["role"]
result = peer.result_for(
    Path.cwd(), role, outcome, scenario=options.get("scenario", "clean")
)
envelope = {
    **base,
    "subtype": "success",
    "is_error": False,
    "terminal_reason": "completed",
    "api_error_status": None,
    "structured_output": {
        "outcome": result["outcome"],
        "summary": "Inert local role evidence",
        "refs": [f"lad-results/{result['occurrence_id']}/result.json"],
    },
}
os.write(1, json.dumps(envelope).encode())
sys.exit(0)
