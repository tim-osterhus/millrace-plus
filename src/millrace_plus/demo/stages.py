"""Finite command stages; native proposals never own deterministic facts."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any

from millrace_plus.workflow_views import read_output

from .contracts import (
    DemoError,
    canonical_bytes,
    load_document,
    validate_baseline,
    validate_candidate,
    validate_report,
)
from .estimate import build_baseline
from .hardware import collect_hardware, unknown_hardware
from .render import build_report, standalone_html

__all__ = ["run_stage"]


def _write(path: Path, content: bytes, *, immutable: bool = False) -> None:
    temp = path.with_name(".demo-write-" + uuid.uuid4().hex)
    try:
        with temp.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            os.link(temp, path, follow_symlinks=False)
            path.chmod(0o400)
        else:
            os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def _json(path: Path, value: object, *, immutable: bool = False) -> None:
    _write(path, canonical_bytes(value), immutable=immutable)


def _demo_id(workspace: Path, session_file: Path | None) -> str:
    if session_file is None or session_file.name != "demo-session.json":
        raise DemoError("invalid_demo_owner")
    root = session_file.parent
    info = root.lstat()
    marker = root / "owner.json"
    metadata = marker.lstat()
    owner = load_document(marker, limit=16384)
    if (
        owner.keys()
        != {
            "format",
            "demo_id",
            "root",
            "root_device",
            "root_inode",
            "created_at",
            "child_observation",
        }
        or owner["format"] != "millrace-demo-owner/v1"
        or type(owner["demo_id"]) is not str
        or re.fullmatch(r"[0-9a-f]{32}", owner["demo_id"]) is None
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or not stat.S_ISDIR(info.st_mode)
        or root.resolve() != root
        or workspace != root / "workspace"
        or owner["root"] != str(root)
        or type(owner["root_device"]) is not int
        or type(owner["root_inode"]) is not int
        or owner["root_device"] != info.st_dev
        or owner["root_inode"] != info.st_ino
    ):
        raise DemoError("invalid_demo_owner")
    return str(owner["demo_id"])


def _session(path: Path | None) -> dict[str, Any]:
    if path is None:
        raise DemoError("baseline_capture_required")
    obj = load_document(path, limit=8192)
    fields = {
        "format",
        "instance_id",
        "run_id",
        "plane_id",
        "invocation_id",
        "producing_revision_id",
        "state_home",
        "baseline",
    }
    if obj.keys() != fields or obj["format"] != "millrace-demo-session/v1":
        raise DemoError("invalid_demo_session")
    for key in ("instance_id", "run_id", "invocation_id"):
        if type(obj[key]) is not str or not re.fullmatch(r"[0-9a-f]{32}", obj[key]):
            raise DemoError("invalid_demo_session")
    if type(obj["producing_revision_id"]) is not str or not re.fullmatch(
        r"[0-9a-f]{64}", obj["producing_revision_id"]
    ):
        raise DemoError("invalid_demo_session")
    if obj["plane_id"] != "main" or not Path(obj["state_home"]).is_absolute():
        raise DemoError("invalid_demo_session")
    baseline = obj["baseline"]
    if type(baseline) is not dict or baseline.keys() != {
        "reference",
        "json_sha256",
        "report_sha256",
    }:
        raise DemoError("invalid_demo_session")
    reference = baseline["reference"]
    if (
        type(reference) is not dict
        or reference.keys() != {"instance_id", "run_id", "output_id", "record_sha256"}
        or reference["instance_id"] != obj["instance_id"]
        or reference["run_id"] != obj["run_id"]
        or reference["output_id"] != "baseline"
    ):
        raise DemoError("invalid_demo_session")
    for digest in (
        reference["record_sha256"],
        baseline["json_sha256"],
        baseline["report_sha256"],
    ):
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DemoError("invalid_demo_session")
    return obj


def _captured_baseline(session: dict[str, Any]) -> dict[str, Any]:
    descriptor = session["baseline"]
    ref = descriptor["reference"]
    observed = read_output(
        ref["instance_id"],
        ref["run_id"],
        ref["output_id"],
        ref["record_sha256"],
        "json",
        state_home=Path(session["state_home"]),
    )
    representation = observed["representation"]
    expected_invocation = {
        key: session[key]
        for key in ("plane_id", "invocation_id", "producing_revision_id")
    }
    expected_invocation["node_id"] = "baseline"
    if observed["reference"] != ref or observed["invocation"] != expected_invocation:
        raise DemoError("baseline_identity_mismatch")
    baseline = validate_baseline(representation["data"])
    if (
        representation["format"] != baseline["format"]
        or representation["sha256"] != descriptor["json_sha256"]
        or hashlib.sha256(canonical_bytes(baseline)).hexdigest()
        != descriptor["json_sha256"]
        or hashlib.sha256(canonical_bytes(baseline["display"])).hexdigest()
        != descriptor["report_sha256"]
    ):
        raise DemoError("baseline_digest_mismatch")
    return baseline


def _status(
    status: str,
    reason: str | None = None,
    phase: str | None = None,
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"status": status, "phase": phase, "reason": reason, "candidate": candidate}


def _native_state(session: dict[str, Any]) -> dict[str, Any]:
    from millrace_plus.router.control import inspect_run

    view = inspect_run(
        session["instance_id"],
        session["run_id"],
        state_home=Path(session["state_home"]),
    )
    invocation_rows = view["invocations"]
    completion_rows = view["completions"]
    if type(invocation_rows) is not list or type(completion_rows) is not list:
        raise DemoError("invalid_native_state")
    invocations: list[dict[str, Any]] = []
    for item in invocation_rows:
        if type(item) is not dict:
            raise DemoError("invalid_native_state")
        if item["node"] == "native":
            invocations.append(item)
    if not invocations:
        return _status("skipped", "not_selected")
    if len(invocations) != 1:
        raise DemoError("ambiguous_native_invocation")
    invocation = invocations[0]
    if invocation["writer_state"] != "stopped":
        raise DemoError("writer_unconfirmed")
    evidence = invocation.get("adapter_evidence") or {}
    phase = evidence.get("phase")
    if type(phase) is not str or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", phase):
        phase = None
    completions: list[dict[str, Any]] = []
    for item in completion_rows:
        if type(item) is not dict:
            raise DemoError("invalid_native_state")
        if item["invocation_id"] == invocation["id"]:
            completions.append(item)
    if len(completions) != 1:
        return _status("unknown_outcome", "native_completion_missing", phase)
    completion = completions[0]
    classification = completion["classification"]
    transport = completion["transport"]
    if transport in {"timeout", "timed_out"} or classification == "timeout":
        return _status("timed_out", "native_timeout", phase)
    if phase in {"preflight", "probe"}:
        return _status("preparation_failed", "native_preparation_failed", phase)
    if classification == "protocol_failure":
        return _status("protocol_failure", "native_protocol_failure", phase)
    if completion["outcome"] == "DECLINED":
        return _status("unavailable", "native_declined", phase)
    if completion["outcome"] not in {"PROPOSED", "NO_RESEARCH"}:
        return _status("failed", "native_failed", phase)
    return _status("completed", None, phase)


def _restore_baseline(workspace: Path, baseline: dict[str, Any]) -> None:
    _json(workspace / "baseline.json", baseline)
    (workspace / "baseline.json").chmod(0o400)
    _write(workspace / "baseline.html", standalone_html(baseline).encode("utf-8"))
    (workspace / "baseline.html").chmod(0o400)


def _stage(
    stage: str, workspace: Path, session_file: Path | None, no_web: bool
) -> tuple[str, list[str]]:
    if stage == "detect":
        _json(workspace / "hardware.json", collect_hardware())
        return "READY", ["hardware.json"]
    if stage == "baseline":
        baseline = build_baseline(load_document(workspace / "hardware.json"))
        _json(workspace / "baseline.json", baseline, immutable=True)
        _write(
            workspace / "baseline.html",
            standalone_html(baseline).encode("utf-8"),
            immutable=True,
        )
        return "READY", ["baseline.json", "baseline.html"]
    if stage == "unavailable":
        demo_id = _demo_id(workspace, session_file)
        baseline = build_baseline(unknown_hardware())
        _json(workspace / "baseline.json", baseline, immutable=True)
        _write(
            workspace / "baseline.html",
            standalone_html(baseline).encode("utf-8"),
            immutable=True,
        )
        report = build_report(
            demo_id,
            baseline,
            native=_status("unavailable", "hardware_unavailable"),
        )
        _json(workspace / "report.json", validate_report(report))
        _write(workspace / "report.html", standalone_html(report).encode("utf-8"))
        return "READY", ["baseline.json", "baseline.html", "report.json", "report.html"]
    session = _session(session_file)
    baseline = _captured_baseline(session)
    if stage == "skip":
        path = workspace / "native-status.json"
        if path.exists():
            existing = load_document(path)
            if existing.get("status") != "admission_failed":
                raise DemoError("unexpected_native_status")
        else:
            _json(path, _status("skipped", "not_selected"))
        return "SKIPPED", ["native-status.json"]
    if stage == "validate":
        native = _native_state(session)
        try:
            if native["status"] != "completed":
                raise DemoError("native_not_completed")
            working = load_document(workspace / "baseline.json")
            if canonical_bytes(working) != canonical_bytes(baseline):
                raise DemoError("baseline_replaced")
            candidate = validate_candidate(
                load_document(workspace / "candidate.json", limit=32 * 1024),
                no_web=no_web,
            )
        except (DemoError, OSError):
            _restore_baseline(workspace, baseline)
            _json(
                workspace / "native-status.json",
                _status("rejected", "candidate_rejected", native["phase"]),
            )
            return "REJECT", ["native-status.json"]
        _json(workspace / "accepted-candidate.json", candidate)
        native["candidate"] = candidate
        _json(workspace / "native-status.json", native)
        return "ACCEPT", ["accepted-candidate.json", "native-status.json"]
    if stage == "fallback":
        path = workspace / "native-status.json"
        native = _native_state(session)
        if path.exists():
            recorded = load_document(path)
            if (
                native["status"] == "skipped"
                and recorded.get("status") == "admission_failed"
                or native["status"] == "completed"
                and recorded.get("status") == "rejected"
            ):
                native = recorded
        if native["status"] == "completed":
            native = _status("rejected", "candidate_unaccepted", native["phase"])
        native["candidate"] = None
        _restore_baseline(workspace, baseline)
        _json(path, native)
        _json(workspace / "accepted-candidate.json", {"candidate": None})
        return "READY", ["native-status.json"]
    if stage == "render":
        native = load_document(workspace / "native-status.json")
        report = validate_report(
            build_report(_demo_id(workspace, session_file), baseline, native=native)
        )
        _json(workspace / "report.json", report)
        _write(workspace / "report.html", standalone_html(report).encode("utf-8"))
        return "READY", ["report.json", "report.html"]
    raise DemoError("unknown_demo_stage")


def run_stage(
    stage: str,
    *,
    workspace: Path,
    session_file: Path | None = None,
    no_web: bool = False,
) -> int:
    """Emit one existing json-v1 terminal envelope; never expose raw exceptions."""
    try:
        outcome, refs = _stage(
            stage, workspace.resolve(strict=True), session_file, no_web
        )
    except Exception:
        # This executable boundary converts domain/I/O/public-read failures to
        # ordinary Core on_error routing; details never leak native raw output.
        print(canonical_bytes({"error": "demo_stage_failed"}).decode("utf-8"))
        return 1
    print(
        canonical_bytes(
            {"outcome": outcome, "summary": "Demo stage saved", "refs": refs}
        ).decode("utf-8")
    )
    return 0
