"""Finite deterministic Markdown presentation views for LAD evidence (stdlib only).

Raw bytes and their digests stay authoritative. This module recognizes only a
fixed set of line-anchored labels outside fenced code and blockquotes. It never
infers a section from prose, a synonym or a model, and it never writes files.
It imports no sibling script: ``lad_state`` loads it and re-raises its refusals.
"""

from __future__ import annotations

import hashlib
import json
import re

NORMALIZER = "millrace-lad-presentation-normalizer/v1"


class LadError(Exception):
    """A finite presentation refusal code; ``lad_state`` re-raises the same code."""


def canonical(value: object) -> bytes:
    # The same canonical UTF-8 JSON encoding the LAD ledger uses.
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()

BEGIN = "<!-- millrace-lad-generated-header/v1 -->"
END = "<!-- /millrace-lad-generated-header/v1 -->"
SECTIONS = ("evidence", "outcome")
CHECK = "development check"
IDENTITY_FIELDS = ("session_id", "occurrence_id", "role", "lineage")

_PREFIX = re.compile(r"[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)*")
_ATX = re.compile(r"(#{1,6})(?:[ \t]+(.*?))?[ \t]*\Z")
_CLOSING = re.compile(r"(?:\A|[ \t]+)#+[ \t]*\Z")
_BOLD = re.compile(r"(\*\*|__)(.+?)\1(.*)\Z", re.S)
_FENCE = re.compile(r"(`{3,}|~{3,})")
_LABEL = re.compile(
    r"(session/occurrence/role/lineage|input/source/guidance/baseline identities"
    r"|session|occurrence|role|lineage)[ \t]*:(.*)\Z",
    re.I,
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SCALAR = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_PLACEHOLDER = re.compile(r"<[^<>\n]*>\Z")
_COMBINED = {
    "session/occurrence/role/lineage": ("session", "occurrence", "role", "lineage"),
    "input/source/guidance/baseline identities": (
        "input", "source", "guidance", "baseline"
    ),
}


def _strip_bold(value: str) -> str:
    match = _BOLD.match(value)
    return match.group(2) + match.group(3) if match else value


def _fence(value: str) -> tuple[str, int] | None:
    opened = _FENCE.match(value)
    if not opened or (opened.group(1)[0] == "`" and "`" in value[
        len(opened.group(1)):
    ]):
        return None
    return opened.group(1)[0], len(opened.group(1))


def _lines(text: str) -> list[dict]:
    """Classify each logical line once; fences/quotes are never labels.

    Fence and blockquote openers are recognized after the same indentation and
    list-item prefix that labels may use, so a list item's code stays code.
    """
    rows, fence = [], None
    for raw in text.split("\n"):
        stripped = raw.strip()
        body = raw[_PREFIX.match(raw).end():]
        row = {"raw": raw, "zone": "text", "heading": None, "label": None}
        if fence is not None:
            row["zone"] = "fence"
            if stripped and set(stripped) == {fence[0]} and len(stripped) >= fence[1]:
                fence = None
            rows.append(row)
            continue
        opened = _fence(stripped) or _fence(body.strip())
        if opened:
            fence = opened
            row["zone"] = "fence"
            rows.append(row)
            continue
        if stripped.startswith(">") or body.lstrip().startswith(">"):
            row["zone"] = "quote"
            rows.append(row)
            continue
        heading = _ATX.match(body)
        if heading:
            title = _CLOSING.sub("", heading.group(2) or "").strip()
            row["heading"] = len(heading.group(1))
            row["label"] = _section_label(_strip_bold(title), heading=True)
        else:
            row["label"] = _section_label(_strip_bold(body.strip()), heading=False)
        rows.append(row)
    return rows


def _section_label(value: str, *, heading: bool) -> dict | None:
    lowered = value.lower()
    for name in (*SECTIONS, CHECK):
        if not lowered.startswith(name):
            continue
        rest = value[len(name):]
        if rest.lstrip(" \t").startswith(":"):
            return {"name": name, "inline": rest.lstrip(" \t")[1:].strip(),
                    "suffix": ""}
        if heading:
            # A legacy literal heading-title suffix remains title text.
            return {"name": name, "inline": "", "suffix": rest}
    return None


def _body(rows: list[dict], start: int) -> tuple[str, int]:
    """Return one recognized label's body and its exclusive end line."""
    label, level = rows[start]["label"], rows[start]["heading"]
    end = start + 1
    if level is None and label["name"] == CHECK:
        return label["inline"], end  # Inline check content ends at its line.
    while end < len(rows):
        row = rows[end]
        if row["zone"] == "text":
            if level is None and row["heading"] is not None:
                break
            if level is not None and row["heading"] is not None and (
                row["heading"] <= level
            ):
                break
            if label["name"] in SECTIONS and row["label"] and (
                row["label"]["name"] in SECTIONS
            ):
                break
        end += 1
    lines = [label["inline"]] + [row["raw"] for row in rows[start + 1:end]]
    return "\n".join(lines).strip(), end


def _collect(
    rows: list[dict], name: str, prefix: str
) -> tuple[str | None, list[str]]:
    """Exactly one distinct nonblank body; identical repeats are recorded."""
    bodies = [
        (row["label"]["suffix"], _body(rows, index)[0])
        for index, row in enumerate(rows)
        if row["zone"] == "text" and row["label"] and row["label"]["name"] == name
    ]
    if not bodies:
        return None, []
    if len(set(bodies)) != 1:
        raise LadError(prefix + "conflicting")
    if not bodies[0][1]:
        raise LadError(prefix + "empty")
    return bodies[0][1], (["identical_duplicate_" + name.replace(" ", "_")]
                          if len(bodies) > 1 else [])


def _missing(rows: list[dict], name: str, prefix: str) -> LadError:
    """Distinguish why no line-anchored label exists; every case refuses."""
    needles = (name + ":",) if name == CHECK else ("# " + name, name + ":")
    for zone, reason in (("fence", "fenced_only"), ("quote", "quoted_only")):
        if any(row["zone"] == zone and any(n in row["raw"].lower() for n in needles)
               for row in rows):
            return LadError(prefix + reason)
    if any(any(n in row["raw"].lower() for n in needles) for row in rows):
        return LadError(prefix + "ambiguous_midline")
    return LadError(prefix + "missing")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "`":
        value = value[1:-1].strip()
    return value


def _parts(label: str, value: str) -> dict[str, str]:
    keys = _COMBINED[label]
    value = _unquote(value)
    if _PLACEHOLDER.fullmatch(value):
        return {}
    if "=" in value:
        tokens = [token for token in re.split(r"[\s,;]+", value) if token]
        pairs = [token.split("=", 1) for token in tokens]
        if (any(len(pair) != 2 for pair in pairs)
                or sorted(key.lower() for key, _ in pairs) != sorted(keys)):
            raise LadError("presentation_malformed_identity")
        found = {key.lower(): _unquote(item) for key, item in pairs}
    else:
        items = value.split("/")
        if len(items) != len(keys):
            raise LadError("presentation_malformed_identity")
        found = dict(zip(keys, (_unquote(item) for item in items), strict=True))
    return {key: item for key, item in found.items()
            if not _PLACEHOLDER.fullmatch(item)}


def _identities(rows: list[dict], authority: dict) -> set[str]:
    """Check every explicit raw self-identity; return the fields it supplies."""
    allowed = {
        "session": {authority["session_id"]},
        "occurrence": {authority["occurrence_id"]},
        "role": {authority["role"]},
        "lineage": {authority["lineage"]},
        "input": {authority["input_sha256"]},
        "source": set(authority["sources"]),
        "guidance": {authority["guidance_sha256"]},
        "baseline": set(authority["baselines"]),
    }
    seen: set[str] = set()
    for row in rows:
        if row["zone"] != "text" or row["heading"] is not None:
            continue
        line = _strip_bold(row["raw"][_PREFIX.match(row["raw"]).end():].strip())
        match = _LABEL.match(line)
        if not match:
            continue
        label, value = match.group(1).lower(), match.group(2)
        if label in _COMBINED:
            found = _parts(label, value)
        else:
            value = _unquote(value)
            found = {} if _PLACEHOLDER.fullmatch(value) else {label: value}
        for key, item in found.items():
            if key in {"session", "occurrence"}:
                normal, valid = item.lower(), _UUID.fullmatch(item.lower())
            elif key in {"role", "lineage"}:
                normal = item.lower() if key == "role" else item
                valid = _SCALAR.fullmatch(item)
            elif key == "baseline" and item.lower() in {"null", "none"}:
                normal, valid = None, True
            else:
                normal, valid = item.lower(), _SHA.fullmatch(item.lower())
            if not valid:
                raise LadError("presentation_malformed_identity")
            if normal not in allowed[key]:
                raise LadError("presentation_identity_" + key)
            seen.add(key)
    return {field for field, key in zip(
        IDENTITY_FIELDS, ("session", "occurrence", "role", "lineage"), strict=True
    ) if key in seen}


def header(authority: dict) -> str:
    return "\n".join([
        BEGIN,
        "Session: " + authority["session_id"],
        "Occurrence: " + authority["occurrence_id"],
        "Role: " + authority["role"],
        "Lineage: " + authority["lineage"],
        "Declared outcome: " + authority["outcome"],
        END,
    ]) + "\n\n"


def _text(raw: bytes) -> tuple[str, list[str]]:
    rules = []
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise LadError("presentation_utf8_required") from None
    if text.startswith("﻿"):
        text, rules = text[1:], rules + ["utf8_bom_removed"]
    if "\r\n" in text:
        text, rules = text.replace("\r\n", "\n"), rules + ["line_endings_lf"]
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    trimmed = "\n".join(lines) + "\n"
    if trimmed != text:
        rules.append("outer_blank_lines_trimmed")
    return trimmed, rules


def _render(row: dict) -> str:
    label = row["label"]
    word = {"evidence": "Evidence", "outcome": "Outcome",
            CHECK: "Development check"}[label["name"]]
    tail = ": " + label["inline"] if label["inline"] else label["suffix"]
    if row["heading"] is None:
        return word + ":" + (" " + label["inline"] if label["inline"] else "")
    return "#" * row["heading"] + " " + word + tail


def view(raw: bytes, authority: dict) -> tuple[bytes, dict]:
    """Return the deterministic UTF-8/LF view and its applied rule record."""
    text, rules = _text(raw)
    expected = header(authority)
    if text.startswith(expected):
        text = text[len(expected):].lstrip("\n") or "\n"
        rules.append("existing_generated_header")
    if BEGIN in text or END in text:
        raise LadError("presentation_generated_header_conflict")
    rows = _lines(text)
    for name in SECTIONS:
        prefix = "presentation_" + name + "_"
        body, repeated = _collect(rows, name, prefix)
        if body is None:
            raise _missing(rows, name, prefix)
        rules.extend(repeated)
    supplied = _identities(rows, authority)
    rendered = []
    for row in rows:
        line = row["raw"]
        if row["zone"] == "text" and row["label"]:
            line = _render(row)
            if line != row["raw"]:
                rules.append("label_canonicalized")
        rendered.append(line)
    output = "\n".join(rendered)
    generated = [field for field in IDENTITY_FIELDS if field not in supplied]
    if generated:
        output = expected + output
        rules.append("generated_identity_header")
        generated.append("outcome")
    return output.encode("utf-8"), {
        "rules": sorted(set(rules)), "generated": generated,
    }


def development_check(raw: bytes) -> tuple[str, list[str]]:
    """Return the one exact packet development check from a raw packet draft.

    The same draft's common artifact view, including header checks, is already
    validated before plan acceptance reads it.
    """
    text, _ = _text(raw)
    prefix = "packet_development_check_"
    rows = _lines(text)
    body, repeated = _collect(rows, CHECK, prefix)
    if body is None:
        error = _missing(rows, CHECK, prefix)
        raise LadError(prefix + "required") if str(error).endswith(
            "_missing"
        ) else error
    return body, repeated


def expected_record(
    *, occurrence: dict, result: dict, raw_result: bytes,
    artifacts: list[tuple[str, bytes]], input_raw: bytes, policy: dict,
) -> bytes:
    """The complete immutable provenance record recomputed from retained facts.

    Inputs are the raw result bytes, the digest-checked raw artifact bytes, the
    exact prepared input projection and the occurrence's captured policy ref.
    Acceptance publishes these bytes; replay and every ordinary load compare the
    retained record with them exactly. Views are recomputed, never stored.
    """
    oid = occurrence["occurrence_id"]
    baselines = {None if occurrence["baseline"] is None
                 else occurrence["baseline"]["sha256"]}
    if occurrence["role"] == "checker":
        baselines |= {digest(raw) for name, raw in artifacts if name == "baseline.md"}
    authority = {
        "session_id": occurrence["session_id"], "occurrence_id": oid,
        "role": occurrence["role"], "lineage": occurrence["lineage"],
        "outcome": result["outcome"], "input_sha256": occurrence["input_sha256"],
        "guidance_sha256": occurrence["guidance_sha256"],
        "sources": {occurrence["source_sha256"], result["source_sha256"]},
        "baselines": baselines,
    }
    return canonical({
        "format": "millrace-lad-presentation/v1",
        "session_id": occurrence["session_id"],
        "occurrence_id": oid,
        "policy": policy,
        "normalizer": NORMALIZER,
        "result": {"path": f"lad-results/{oid}/result.json",
                   "sha256": digest(raw_result)},
        "authority": {"path": f"lad-state/occurrences/{oid}/input.json",
                      "sha256": digest(input_raw)},
        "artifacts": [_artifact_row(name, raw, authority) for name, raw in artifacts],
    }) + b"\n"


def _artifact_row(name: str, raw: bytes, authority: dict) -> dict:
    if not name.endswith(".md"):
        return {"path": name, "sha256": digest(raw), "view_sha256": None,
                "rules": [], "generated": []}
    output, applied = view(raw, authority)
    return {"path": name, "sha256": digest(raw), "view_sha256": digest(output),
            **applied}
