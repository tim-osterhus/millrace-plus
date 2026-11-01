"""One captured Factory operation. Core executes the graph and selected role nodes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from millrace_plus.factory_runtime import step

if __name__ == "__main__":
    print(json.dumps(step(sys.argv[1], Path(sys.argv[2]), Path(__file__).resolve())))
