"""Execute one deterministic service node; Core owns repetition and controls."""

from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path

from millrace_plus.lad_service import Service
from millrace_plus.lad_service_runtime import Runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "node",
        choices=("reconcile", "claim", "wait_intake", "advance", "settle", "hold"),
    )
    args = parser.parse_args()

    def terminate(signum, _frame):
        # Core still classifies the cancelled invocation. Unwind only our owned
        # waiter/finally cleanup; emit no fabricated successful node result.
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    try:
        result = Runtime(Service(Path.cwd().parent.parent)).node(args.node)
    except Exception as exc:
        result = {
            "outcome": "HOLD",
            "summary": getattr(exc, "code", "service_runtime_refused"),
            "refs": [],
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
