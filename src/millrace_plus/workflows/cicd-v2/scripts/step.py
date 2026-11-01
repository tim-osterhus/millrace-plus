"""One captured CI v2 deterministic graph node."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from millrace_plus.cicd_v2_runtime import step


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("node")
    args = parser.parse_args()
    try:
        result = step(args.node, Path(__file__).resolve().parents[1])
    except Exception as exc:
        print(getattr(exc, "code", type(exc).__name__), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
