"""Execute one deterministic CI/CD node; Core owns routing, repetition and controls."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from millrace_plus.cicd_runtime import step

NODES = (
    "reconcile",
    "claim",
    "wait_event",
    "freeze",
    "guard_diagnosis",
    "guard_patch",
    "checks",
    "guard_publication",
    "settle",
    "hold",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("node", choices=NODES)
    args = parser.parse_args()

    def terminate(signum: int, _frame: object) -> None:
        # Core classifies the stopped invocation. Unwind owned check processes
        # through their cleanup; never print a fabricated node result.
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    # This file runs from Core's pinned invocation controls; prompts sit beside it.
    controls = Path(__file__).resolve().parents[1]
    try:
        result = step(args.node, controls)
    except Exception as exc:
        # A refusal is an execution error: the graph pauses and resumes reconcile.
        code = getattr(exc, "code", None)
        print(code if isinstance(code, str) else type(exc).__name__, file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
