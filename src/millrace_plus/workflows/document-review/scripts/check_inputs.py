"""Check only whether the required original request is present and nonempty."""

from __future__ import annotations

import json
import stat
from pathlib import Path

request = Path("request.md")


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON object key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_number(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _request_matches(content: bytes) -> bool:
    try:
        return stat.S_ISREG(request.lstat().st_mode) and request.read_bytes() == content
    except OSError:
        return False


def _map_manifest() -> bool:
    manifest = Path("subworkflow-inputs.json")
    try:
        info = manifest.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    try:
        value = json.loads(
            manifest.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_number,
        )
        if (
            not isinstance(value, dict)
            or set(value) != {"format", "identity", "inputs"}
            or value["format"] != "millrace-plus-subworkflow-inputs/v1"
            or not isinstance(value["identity"], dict)
            or not value["identity"]
            or not isinstance(value["inputs"], dict)
        ):
            return False
        markdown = value["inputs"].get("request_markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return False
        content = markdown.encode("utf-8")
    except (OSError, UnicodeError, ValueError):
        return False

    if _request_matches(content):
        return True
    try:
        with request.open("xb") as stream:
            stream.write(content)
        return True
    except FileExistsError:
        return _request_matches(content)
    except OSError:
        return False


try:
    ready = (
        _map_manifest()
        and stat.S_ISREG(request.lstat().st_mode)
        and bool(request.read_text(encoding="utf-8").strip())
    )
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
