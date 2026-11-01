"""Typed local feedback and advisory correlation for CI v2.

Submitted prose and evidence are data. The existing configured reproduction,
review and explicit operator continuation routes own every actionable decision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from . import cicd_v2_records as vr
from .local_intake import canonical, strict_json

if TYPE_CHECKING:
    from .cicd_v2_runtime import V2Home

__all__ = [
    "FEEDBACK",
    "CORRELATION",
    "validate_feedback",
    "validate_correlation",
    "feedback_identity",
    "feedback_refs",
    "correlations",
    "submit_report",
    "record_correlation",
]

FEEDBACK = "millrace-plus-cicd-feedback/v1"
CORRELATION = "millrace-plus-cicd-correlation/v1"


def validate_feedback(
    value: object, repository: str | None = None
) -> dict[str, object]:
    """Validate the exact feedback contract without synthesizing CI fields."""
    code = "invalid_feedback"
    obj = vr._record(
        value,
        (
            "format",
            "kind",
            "origin",
            "report_id",
            "repository",
            "affected_version",
            "environment",
            "summary",
            "reproduction_steps",
            "evidence_refs",
            "impact",
            "occurred_at",
            "redaction",
        ),
        FEEDBACK,
        code,
    )
    vr._enum(obj["kind"], ("bug_report", "production_incident"), code)
    origin = vr._fields(obj["origin"], ("producer", "origin_refs"), code)
    vr._text(origin["producer"], code)
    vr._refs(origin["origin_refs"], code)
    vr._text(obj["report_id"], code)
    vr._repository(obj["repository"], code)
    if repository is not None and obj["repository"] != repository:
        raise vr.CicdV2Error("repository_mismatch")
    version = vr._fields(
        obj["affected_version"], ("release", "head_sha", "artifact_sha256"), code
    )
    vr._optional_text(version["release"], code)
    vr._optional_revision(version["head_sha"], code)
    vr._optional_sha(version["artifact_sha256"], code)
    if all(item is None for item in version.values()):
        raise vr.CicdV2Error("affected_version_unknown")
    env = vr._fields(
        obj["environment"], ("name", "platform", "configuration_ref"), code
    )
    vr._text(env["name"], code)
    vr._text(env["platform"], code)
    vr._optional_ref(env["configuration_ref"], code)
    vr._text(obj["summary"], code)
    # Descriptive steps need not be distinct; no prose/count quality gate.
    for step in vr._list(obj["reproduction_steps"], code):
        vr._text(step, code)
    vr._refs(obj["evidence_refs"], code)
    impact = vr._fields(
        obj["impact"], ("observed", "affected_scope", "severity_hint"), code
    )
    vr._text(impact["observed"], code)
    vr._text(impact["affected_scope"], code)
    if impact["severity_hint"] is not None:
        vr._enum(impact["severity_hint"], ("low", "medium", "high", "critical"), code)
    vr._time(obj["occurred_at"], code)
    redaction = vr._fields(obj["redaction"], ("performed", "notes"), code)
    vr._bool(redaction["performed"], code)
    vr._text(redaction["notes"], code)
    return obj


def feedback_identity(report: dict[str, object]) -> dict[str, object]:
    """The immutable original identity used by v2 input admission."""
    report = validate_feedback(report)
    return {
        "kind": report["kind"],
        "producer": report["origin"]["producer"],
        "report_id": report["report_id"],
        "repository": report["repository"],
    }


def feedback_refs(report: dict[str, object]) -> list[dict[str, object]]:
    """All directly referenced external files, relative to the original report."""
    refs = [*report["origin"]["origin_refs"], *report["evidence_refs"]]
    configured = report["environment"]["configuration_ref"]
    if configured is not None:
        refs.append(configured)
    return list({canonical(ref): ref for ref in refs}.values())


def _capture(refs, source: Path, read: Callable, limit: int | None):
    files = []
    for ref in refs:
        raw = read(source / str(ref["path"]), limit=limit)
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise vr.CicdV2Error("feedback_evidence_digest_mismatch")
        files.append((ref, raw))
    return files


def _original_name(refs: list[dict[str, object]]) -> str:
    # A correction can legitimately refer to an older file named report.json.
    # Pick a disjoint retained filename without rewriting either original.
    name = "report.json"
    paths = [str(ref["path"]) for ref in refs]
    while any(path == name or path.startswith(name + "/") for path in paths):
        name = "_" + name
    return name


def submit_report(home: V2Home, report_file: Path) -> dict[str, object]:
    """Retain exact report/evidence bytes and admit through the existing FIFO."""
    from .cicd_records import intake_key
    from .cicd_source import write_exact
    from .cicd_v2_runtime import _read

    source = report_file.absolute()
    raw = _read(source, limit=home.guards.get("input"))
    report = validate_feedback(strict_json(raw), str(home.config["repository"]))
    identity = feedback_identity(report)
    input_id = vr.input_identity(str(report["kind"]), identity)
    files = _capture(
        feedback_refs(report), source.parent, _read, home.guards.get("input.evidence")
    )
    with home.lock(), home.state_lock():
        directory = home.workspace / "originals" / input_id
        original = directory / _original_name(feedback_refs(report))
        write_exact(original, raw, "input_conflict")
        for ref, content in files:
            write_exact(directory / str(ref["path"]), content, "input_conflict")
        original_ref = home.file_ref(original)
        evidence_refs = [
            home.file_ref(directory / str(ref["path"])) for ref, _ in files
        ]
        payload = {"input_id": input_id, "original_ref": original_ref, "log_ref": None}
        receipt = home.intake.submit(intake_key(input_id), canonical(payload))
        # Intake annotates a replay; feedback returns the original receipt.
        receipt = receipt | {"disposition": "accepted"}
        record = vr.validate_input(
            {
                "format": vr.INPUT,
                "input_id": input_id,
                "kind": report["kind"],
                "original_ref": original_ref,
                "evidence_refs": evidence_refs,
                "accepted_sequence": receipt["sequence"],
                "repository": report["repository"],
                "source_identity": identity,
            }
        )
        write_exact(
            home.workspace / (input_id + ".input.json"),
            canonical(record),
            "input_conflict",
        )
    return {"input_id": input_id, "receipt": receipt}


def validate_correlation(value: object) -> dict[str, object]:
    """Validate advisory records; no disposition grants queue/effect authority."""
    code = "invalid_correlation"
    obj = vr._record(
        value,
        (
            "format",
            "correlation_id",
            "input_ids",
            "hypothesis",
            "evidence_refs",
            "confidence",
            "disposition",
            "actor",
        ),
        CORRELATION,
        code,
    )
    vr._text(obj["correlation_id"], code)
    for input_id in vr._ids(obj["input_ids"], code, nonempty=True):
        vr._sha(input_id, code)
    for key in ("hypothesis", "confidence", "actor"):
        vr._text(obj[key], code)
    vr._refs(obj["evidence_refs"], code)
    vr._enum(obj["disposition"], ("suggested", "human_confirmed", "rejected"), code)
    return obj


def record_correlation(home: V2Home, record_file: Path) -> dict[str, object]:
    """Append one exact advisory identity without altering any admitted input."""
    from .cicd_source import write_exact
    from .cicd_v2_runtime import _read

    source = record_file.absolute()
    raw = _read(source, limit=home.guards.get("input"))
    record = validate_correlation(strict_json(raw))
    files = _capture(
        record["evidence_refs"], source.parent, _read, home.guards.get("input.evidence")
    )
    with home.lock(), home.state_lock():
        for input_id in record["input_ids"]:
            home.input(input_id)
        directory = (
            home.workspace / "correlations" / vr.digest(record["correlation_id"])
        )
        path = directory / (hashlib.sha256(raw).hexdigest() + ".correlation.json")
        pointer = directory.with_suffix(".ref.json")
        record_ref = {
            "path": path.relative_to(home.workspace).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if pointer.exists() and _read(pointer) != canonical(record_ref):
            raise vr.CicdV2Error("correlation_conflict")
        write_exact(path, raw, "correlation_conflict")
        for ref, content in files:
            write_exact(directory / str(ref["path"]), content, "correlation_conflict")
        write_exact(pointer, canonical(record_ref), "correlation_conflict")
        return {"correlation_ref": record_ref}


def correlations(home: V2Home, input_id: str) -> list[dict[str, object]]:
    """Read existing advisory records and verify their files; never heal state."""
    from .cicd_v2_runtime import _read

    result = []
    root = home.workspace / "correlations"
    for directory in sorted(root.glob("*/")):
        # A small immutable pointer distinguishes the record from arbitrary
        # evidence filenames, and pins exact accepted bytes on later reads.
        ref = strict_json(_read(directory.with_suffix(".ref.json")))
        path = home.resolve_ref(ref)
        if (
            path.parent != directory
            or path.name != str(ref["sha256"]) + ".correlation.json"
        ):
            raise vr.CicdV2Error("correlation_identity_mismatch")
        raw = _read(path)
        record = validate_correlation(strict_json(raw))
        if path.parent.name != vr.digest(record["correlation_id"]):
            raise vr.CicdV2Error("correlation_identity_mismatch")
        for member in record["input_ids"]:
            home.input(member)
        _capture(
            record["evidence_refs"],
            path.parent,
            _read,
            home.guards.get("input.evidence"),
        )
        if input_id in record["input_ids"]:
            result.append(home.file_ref(path))
    return result
