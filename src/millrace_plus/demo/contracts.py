"""Strict, bounded Plus-owned hardware demo documents.

Domain validation never confers execution success or writer ownership. Baseline
and display consistency are checked by regenerating the deterministic document.
"""

from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from millrace_plus.workflow_views import canonical_json_bytes

__all__ = [
    "DemoError",
    "canonical_bytes",
    "load_document",
    "validate_hardware",
    "validate_baseline",
    "validate_candidate",
    "validate_report",
    "safe_url",
]

DOCUMENT_LIMIT = 128 * 1024
CANDIDATE_LIMIT = 32 * 1024
UNKNOWN_REASONS = {
    "missing",
    "unsupported",
    "permission_denied",
    "timeout",
    "invalid_output",
    "probe_failed",
}
NATIVE_STATUSES = {
    "skipped",
    "unavailable",
    "admission_failed",
    "preparation_failed",
    "failed",
    "protocol_failure",
    "unknown_outcome",
    "timed_out",
    "rejected",
    "completed",
    "cancelled",
    "writer_unconfirmed",
}
_PLAIN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff<>]")
_CLAIM = re.compile(
    r"\b(?:(?<!not )guarantee\w*|definitely\s+fit|will\s+(?:fit|load|run))\b|"
    r"\b\d+(?:\.\d+)?\s*(?:tokens?\s*/\s*s(?:ec)?|tps|tok/s|[kK]?\s*context)\b",
    re.IGNORECASE,
)


class DemoError(ValueError):
    """A safe diagnostic code, suitable for a demo execution record."""


def canonical_bytes(value: object) -> bytes:
    return canonical_json_bytes(value)


def _object(value: object, fields: set[str]) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != fields:
        raise DemoError("invalid_document_fields")
    return value


def _text(value: object, limit: int, *, empty: bool = False) -> str:
    if (
        type(value) is not str
        or len(value) > limit
        or (not value and not empty)
        or _PLAIN.search(value)
    ):
        raise DemoError("invalid_plain_text")
    return value


def _integer(value: object, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise DemoError("invalid_integer")
    return value


def _list(value: object, limit: int) -> list[Any]:
    if type(value) is not list or len(value) > limit:
        raise DemoError("invalid_list")
    return value


def _nullable(obj: dict[str, Any], validators: dict[str, Any]) -> None:
    reasons = obj["unknown_reasons"]
    if type(reasons) is not dict:
        raise DemoError("invalid_unknown_reasons")
    expected = {name for name in validators if obj[name] is None}
    if reasons.keys() != expected or any(
        v not in UNKNOWN_REASONS for v in reasons.values()
    ):
        raise DemoError("invalid_unknown_reasons")
    for name, check in validators.items():
        if obj[name] is not None:
            check(obj[name])


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise DemoError("invalid_boolean")
    return value


def load_document(path: Path, *, limit: int = DOCUMENT_LIMIT) -> dict[str, Any]:
    """Read a regular non-symlink bounded strict UTF-8 JSON document."""
    import os
    import stat

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise DemoError("invalid_document_file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise DemoError("document_too_large")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise DemoError("duplicate_json_key")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise DemoError("nonfinite_number")

    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
        canonical_bytes(value)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DemoError("invalid_json") from exc
    if type(value) is not dict:
        raise DemoError("invalid_document")
    return value


def validate_hardware(value: object) -> dict[str, Any]:
    obj = _object(
        value,
        {
            "format",
            "platform",
            "architecture",
            "logical_cpus",
            "unknown_reasons",
            "memory",
            "gpus",
            "probe_status",
        },
    )
    if obj["format"] != "millrace-demo-hardware/v1":
        raise DemoError("invalid_hardware_format")
    _nullable(
        obj,
        {
            "platform": lambda v: _text(v, 80),
            "architecture": lambda v: _text(v, 80),
            "logical_cpus": lambda v: _integer(v, positive=True),
        },
    )
    memory = _object(
        obj["memory"],
        {"physical_bytes", "available_bytes", "unified", "unknown_reasons"},
    )
    _nullable(
        memory,
        {"physical_bytes": _integer, "available_bytes": _integer, "unified": _boolean},
    )
    if (
        memory["physical_bytes"] is not None
        and memory["available_bytes"] is not None
        and memory["available_bytes"] > memory["physical_bytes"]
    ):
        raise DemoError("invalid_memory_available")
    for gpu in _list(obj["gpus"], 8):
        gpu = _object(
            gpu,
            {
                "model",
                "device_id",
                "total_bytes",
                "free_bytes",
                "source",
                "unknown_reasons",
            },
        )
        _nullable(
            gpu,
            {
                "model": lambda v: _text(v, 160),
                "device_id": lambda v: _text(v, 80),
                "total_bytes": _integer,
                "free_bytes": _integer,
            },
        )
        if gpu["source"] not in {"system_profiler", "nvidia-smi", "sysfs"}:
            raise DemoError("invalid_gpu_source")
        if (
            gpu["total_bytes"] is not None
            and gpu["free_bytes"] is not None
            and gpu["free_bytes"] > gpu["total_bytes"]
        ):
            raise DemoError("invalid_gpu_free")
    if obj["probe_status"] not in {"complete", "partial", "unavailable"}:
        raise DemoError("invalid_probe_status")
    return obj


def safe_url(value: object) -> str:
    text = _text(value, 2048)
    try:
        parsed = urlsplit(text)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or host.lower() == "localhost"
            or host.lower().endswith(".localhost")
            or any(c.isspace() for c in text)
            or "\\" in text
        ):
            raise DemoError("unsafe_source_url")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith("."):
                raise DemoError("unsafe_source_url") from None
        else:
            if not address.is_global:
                raise DemoError("unsafe_source_url")
    except ValueError as exc:
        raise DemoError("unsafe_source_url") from exc
    return text


def validate_candidate(value: object, *, no_web: bool = False) -> dict[str, Any]:
    obj = _object(
        value,
        {
            "format",
            "research_status",
            "summary",
            "suggestions",
            "published_measurements",
        },
    )
    if len(canonical_bytes(obj)) > CANDIDATE_LIMIT:
        raise DemoError("candidate_too_large")
    if obj["format"] != "millrace-demo-candidate/v1":
        raise DemoError("invalid_candidate_format")
    if obj["research_status"] not in {"completed", "unavailable", "partial", "skipped"}:
        raise DemoError("invalid_research_status")
    if no_web and obj["research_status"] != "skipped":
        raise DemoError("unexpected_research")
    summary = _text(obj["summary"], 1200)
    sentences = [part for part in re.split(r"[.!?]+(?:\s+|$)", summary) if part.strip()]
    if not 2 <= len(sentences) <= 3 or _CLAIM.search(summary):
        raise DemoError("unsupported_candidate_claim")
    for suggestion in _list(obj["suggestions"], 3):
        suggestion = _object(
            suggestion, {"model_id", "artifact_url", "source_urls", "rationale"}
        )
        _text(suggestion["model_id"], 160)
        safe_url(suggestion["artifact_url"])
        urls = _list(suggestion["source_urls"], 4)
        if not urls:
            raise DemoError("missing_candidate_source")
        for url in urls:
            safe_url(url)
        if _CLAIM.search(_text(suggestion["rationale"], 400)):
            raise DemoError("unsupported_candidate_claim")
    for measurement in _list(obj["published_measurements"], 3):
        measurement = _object(
            measurement,
            {
                "source_url",
                "hardware",
                "model",
                "artifact",
                "quantization",
                "backend",
                "backend_version",
                "context",
                "batch",
                "cache",
                "metric",
                "unit",
                "value",
                "conditions",
            },
        )
        safe_url(measurement["source_url"])
        for name in measurement.keys() - {"source_url", "value", "context", "batch"}:
            _text(measurement[name], 400)
        _integer(measurement["context"], positive=True)
        _integer(measurement["batch"], positive=True)
        number = measurement["value"]
        if type(number) not in (int, float) or number < 0:
            raise DemoError("invalid_measurement_value")
    if obj["research_status"] in {"unavailable", "skipped"} and (
        obj["suggestions"] or obj["published_measurements"]
    ):
        raise DemoError("unexpected_research_sources")
    return obj


def _display(value: object) -> None:
    obj = _object(value, {"title", "paragraphs", "facts", "tables", "sources"})
    _text(obj["title"], 160)
    for paragraph in _list(obj["paragraphs"], 4):
        _text(paragraph, 1200)
    for fact in _list(obj["facts"], 24):
        fact = _object(fact, {"label", "value"})
        _text(fact["label"], 80)
        _text(fact["value"], 240)
    for table in _list(obj["tables"], 3):
        table = _object(table, {"caption", "columns", "rows"})
        _text(table["caption"], 160)
        columns = _list(table["columns"], 8)
        if not columns:
            raise DemoError("invalid_display_columns")
        for column in columns:
            _text(column, 80)
        for row in _list(table["rows"], 24):
            if type(row) is not list or len(row) != len(columns):
                raise DemoError("invalid_display_row")
            for cell in row:
                _text(cell, 240, empty=True)
    for source in _list(obj["sources"], 12):
        source = _object(source, {"label", "url"})
        _text(source["label"], 160)
        safe_url(source["url"])


def validate_baseline(value: object) -> dict[str, Any]:
    from .estimate import build_baseline

    obj = _object(
        value,
        {
            "format",
            "reference_version",
            "estimator_version",
            "hardware",
            "entries",
            "explanation",
            "limitations",
            "throughput_status",
            "display",
        },
    )
    if len(canonical_bytes(obj)) > DOCUMENT_LIMIT:
        raise DemoError("baseline_too_large")
    _display(obj["display"])
    if obj != build_baseline(validate_hardware(obj["hardware"])):
        raise DemoError("baseline_mismatch")
    return obj


def validate_report(value: object) -> dict[str, Any]:
    from .render import build_report

    obj = _object(
        value,
        {
            "format",
            "demo_id",
            "baseline",
            "native",
            "completion",
            "limitations",
            "display",
        },
    )
    _text(obj["demo_id"], 160)
    _display(obj["display"])
    validate_baseline(obj["baseline"])
    native = _object(obj["native"], {"status", "phase", "reason", "candidate"})
    if native["status"] not in NATIVE_STATUSES:
        raise DemoError("invalid_native_status")
    for key in ("phase", "reason"):
        if native[key] is not None and (
            type(native[key]) is not str
            or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", native[key])
        ):
            raise DemoError("invalid_native_diagnostic")
    if native["candidate"] is not None:
        validate_candidate(native["candidate"])
        if native["status"] != "completed":
            raise DemoError("invalid_candidate_status")
    elif native["status"] == "completed":
        raise DemoError("missing_accepted_candidate")
    if obj["completion"] not in {"complete", "incomplete", "cancelled"}:
        raise DemoError("invalid_completion")
    if len(canonical_bytes(obj)) > DOCUMENT_LIMIT or obj != build_report(
        obj["demo_id"], obj["baseline"], native=native, completion=obj["completion"]
    ):
        raise DemoError("report_mismatch")
    return obj
