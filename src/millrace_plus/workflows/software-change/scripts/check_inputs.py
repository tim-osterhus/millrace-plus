"""Check only whether the required original request is present and nonempty."""

from __future__ import annotations

import json
from pathlib import Path

request = Path("request.md")
try:
    ready = request.is_file() and bool(request.read_text(encoding="utf-8").strip())
except (OSError, UnicodeError):
    ready = False

if ready:
    result = {
        "outcome": "READY",
        "summary": "request.md is present and nonempty",
        "refs": ["request.md"],
    }
else:
    result = {
        "outcome": "NEEDS_INPUT",
        "summary": "Create request.md with the original goal and acceptance criteria",
        "refs": [],
    }

print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
