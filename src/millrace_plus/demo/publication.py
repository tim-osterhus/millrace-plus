"""Pin validated demo documents to exact public producer identities."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from millrace_plus import workflow_views

from .contracts import DemoError, canonical_bytes

__all__ = ["producer_at", "publish_document"]


def producer_at(
    summary: dict[str, Any],
    invocations: list[dict[str, Any]],
    *,
    node: str,
    action: str,
) -> dict[str, Any]:
    plane = summary.get("planes", {}).get("main", {})
    event = plane.get("last_event") or {}
    target: str | None
    expected_action: dict[str, object]
    if (
        action == "pause"
        and node == "baseline"
        and plane.get("resume_target") in {"native", "skip"}
    ):
        target = plane["resume_target"]
        expected_action = {"pause": {"reason": "baseline_saved", "resume": target}}
    elif action == "finish" and node in {"render", "unavailable"}:
        target = None
        expected_action = {"finish": "succeeded"}
    else:
        raise DemoError("ambiguous_artifact_producer")
    serialized_action = event.get("action")
    if type(serialized_action) is not str:
        raise DemoError("ambiguous_artifact_producer")
    try:
        # Public inspection exposes the canonical serialized action, not its kind.
        observed_action = json.loads(serialized_action)
    except (TypeError, ValueError):
        raise DemoError("ambiguous_artifact_producer") from None
    matches = [
        row
        for row in invocations
        if row.get("invocation_id") == event.get("completion_id")
    ]
    if (
        not summary.get("quiescent")
        or len(matches) != 1
        or event.get("event_kind") != "transition"
        or event.get("node_id") != node
        or observed_action != expected_action
        or event.get("action") != canonical_bytes(expected_action).decode("utf-8")
        or event.get("target") != target
        or event.get("plane_id") != "main"
        or event.get("event_id") != event.get("completion_id")
        or event.get("outcome") != "READY"
    ):
        raise DemoError("ambiguous_artifact_producer")
    row = matches[0]
    completion = row.get("completion") or {}
    if (
        row.get("plane_id") != "main"
        or row.get("node_id") != node
        or row.get("producing_revision_id") != event.get("producing_revision_id")
        or row.get("writer", {}).get("effective") != "stopped"
        or completion.get("classification") != "semantic"
        or completion.get("outcome") != "READY"
    ):
        raise DemoError("invalid_artifact_producer")
    return {
        key: row[key]
        for key in ("plane_id", "node_id", "invocation_id", "producing_revision_id")
    }


def publish_document(
    value: dict[str, Any],
    *,
    output_id: str,
    identity: dict[str, Any],
    instance_id: str,
    run_id: str,
    state_home: Path,
) -> dict[str, Any]:
    display = value["display"]
    record = {
        "format": workflow_views.OUTPUT_FORMAT,
        "output_id": output_id,
        "instance_id": instance_id,
        "run_id": run_id,
        **identity,
        "producer": {"id": "millrace-plus-hardware-demo", "version": "1"},
        "label": "Hardware baseline" if output_id == "baseline" else "Hardware report",
        "published_at": datetime.now(UTC).isoformat(),
        "representations": {
            "report": {
                "format": workflow_views.REPORT_FORMAT,
                "media_type": "application/json",
                "data": display,
                "sha256": hashlib.sha256(canonical_bytes(display)).hexdigest(),
            },
            "json": {
                "format": value["format"],
                "media_type": "application/json",
                "data": value,
                "sha256": hashlib.sha256(canonical_bytes(value)).hexdigest(),
            },
        },
    }
    accepted = workflow_views.publish_output(record, state_home=state_home)
    ref = accepted["reference"]
    observed = workflow_views.read_output(
        **ref, representation="json", state_home=state_home
    )
    if (
        observed["reference"] != ref
        or observed["invocation"] != identity
        or observed["representation"]["sha256"] != accepted["json_sha256"]
        or canonical_bytes(observed["representation"]["data"]) != canonical_bytes(value)
    ):
        raise DemoError("output_readback_mismatch")
    return {key: accepted[key] for key in ("reference", "json_sha256", "report_sha256")}
