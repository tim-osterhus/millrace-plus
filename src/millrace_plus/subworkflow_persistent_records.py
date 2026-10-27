"""Pure tagged LAD observations and proof codecs; no current authority or Core import.

The service verifies local authority before selecting these codecs. Serialized counts
and proofs describe a past read; only a fresh guarded read can authorize disposition.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import catalog
from .subworkflow_records import (
    KEY,
    SHA,
    SubworkflowError,
    _fields,
    _id,
    _string,
    canonical,
    canonical_path,
    digest,
    mapping,
)
from .workflow_os_client import _home, _one_of

__all__ = [
    "JOURNAL_FORMAT",
    "RESULT_FORMAT",
    "LOCAL_SOURCE_FORMAT",
    "OBSERVATION_FORMAT",
    "PROOF_FORMAT",
    "validate_local_source",
    "validate_observation",
    "reduce_full_views",
    "closed",
    "validate_proof",
    "proof_manifest",
]

JOURNAL_FORMAT = "millrace-plus-lad-subworkflow-call/v1"
RESULT_FORMAT = "millrace-plus-lad-subworkflow-result/v1"
LOCAL_SOURCE_FORMAT = "millrace-plus-lad-local-source/v1"
OBSERVATION_FORMAT = "millrace-plus-lad-run-observation/v1"
PROOF_FORMAT = "millrace-plus-lad-core-view-proof/v1"
SETTINGS = {
    "path",
    "env_allowlist",
    "host_paths",
    "runtime_defaults",
    "operator_policy",
}
STATUSES = {"ready", "running", "waiting", "paused", "succeeded", "failed", "cancelled"}
TERMINAL = {"succeeded", "failed", "cancelled"}
WRITERS = {"stopped", "may_live", "running", "unknown"}
CONTROL_STATUSES = {"queued", "applied", "refused"}
INSTANCE_OPERATIONS = {"start_run", "update_instance_config"}
PLANE_OPERATIONS = {
    "pause_plane",
    "resume_plane",
    "cancel_plane",
    "redirect_plane",
    "retry_invocation",
    "reconcile_invocation",
}
RUN_OPERATIONS = PLANE_OPERATIONS | {
    "recover_run",
    "pause_run",
    "cancel_run",
    "resume_run",
    "submit_revision",
    "accept_revision",
}
OBSERVATION_FIELDS = {
    "format",
    "source",
    "instance_id",
    "run_id",
    "start_request_id",
    "instance_state_version",
    "state_version",
    "workflow_file",
    "project_root",
    "settings_sha256",
    "status",
    "quiescent",
    "counts",
}


def _positive(value: object) -> int:
    if type(value) is not int or value < 1:
        raise SubworkflowError("invalid_state_version")
    return value


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise SubworkflowError("invalid_observation_counts")
    return value


def _sha(value: object) -> str:
    text = _string(value, maximum=64)
    if not SHA.fullmatch(text):
        raise SubworkflowError("invalid_sha256")
    return text


def _settings(value: object) -> dict[str, object]:
    row = _fields(value, SETTINGS)
    for name in ("path", "env_allowlist"):
        if not isinstance(row[name], list) or any(
            not isinstance(item, str) or "\0" in item for item in row[name]
        ):
            raise SubworkflowError("invalid_settings")
    for name in ("host_paths", "runtime_defaults", "operator_policy"):
        mapping(row[name])
    canonical(row)
    return row


def validate_local_source(
    value: object, *, expected: object | None = None
) -> dict[str, object]:
    """Validate the descriptor shape, never establish its lifecycle authority."""
    row = _fields(
        value,
        {
            "format",
            "service_id",
            "service_home",
            "init_intent_sha256",
            "core_state_home",
            "os_home",
            "os_profile_sha256",
            "parent_instance_id",
            "parent_run_id",
        },
    )
    if row["format"] != LOCAL_SOURCE_FORMAT:
        raise SubworkflowError("invalid_local_source")
    for name in ("service_id", "parent_instance_id", "parent_run_id"):
        _id(row[name])
    for name in ("service_home", "core_state_home"):
        canonical_path(row[name])
    for name in ("init_intent_sha256", "os_profile_sha256"):
        _sha(row[name])
    _home(row["os_home"])
    if expected is not None and row != validate_local_source(expected):
        raise SubworkflowError("local_source_mismatch")
    return row


def _aggregate(value: object, categories: set[str]) -> dict[str, object]:
    row = _fields(value, {"total", *categories})
    if _count(row["total"]) != sum(_count(row[name]) for name in categories):
        raise SubworkflowError("invalid_observation_counts")
    return row


def validate_observation(
    value: object,
    instance_id: object,
    run_id: object,
    *,
    expected_start_request_id: object | None = None,
    expected_paths: object | None = None,
    expected_settings: object | None = None,
) -> dict[str, object] | None:
    """Validate a tagged past observation; null never proves no run or closure."""
    if value is None:
        return None
    row = _fields(value, OBSERVATION_FIELDS)
    if (
        row["format"] != OBSERVATION_FORMAT
        or row["source"] != "verified_local_core"
        or row["instance_id"] != _id(instance_id)
        or row["run_id"] != _id(run_id)
        or not _one_of(row["status"], STATUSES)
        or type(row["quiescent"]) is not bool
    ):
        raise SubworkflowError("invalid_run_observation")
    _id(row["start_request_id"])
    _positive(row["instance_state_version"])
    _positive(row["state_version"])
    canonical_path(row["workflow_file"])
    canonical_path(row["project_root"])
    _sha(row["settings_sha256"])
    if expected_start_request_id is not None and row["start_request_id"] != _id(
        expected_start_request_id
    ):
        raise SubworkflowError("wrong_start_identity")
    if expected_paths is not None:
        paths = _fields(expected_paths, {"workflow", "project"})
        if (row["workflow_file"], row["project_root"]) != (
            str(canonical_path(paths["workflow"]) / "workflow.json"),
            str(canonical_path(paths["project"])),
        ):
            raise SubworkflowError("wrong_child_identity")
    if expected_settings is not None and row["settings_sha256"] != digest(
        _settings(expected_settings)
    ):
        raise SubworkflowError("invalid_settings")
    counts = _fields(
        row["counts"],
        {
            "inflight_invocations",
            "other_nonterminal_runs",
            "invocations",
            "completions",
            "run_controls",
            "instance_controls",
        },
    )
    _count(counts["inflight_invocations"])
    _count(counts["other_nonterminal_runs"])
    for name in ("invocations", "completions"):
        _aggregate(counts[name], WRITERS)
    if counts["inflight_invocations"] > mapping(counts["invocations"])["total"]:
        raise SubworkflowError("invalid_observation_counts")
    for name in ("run_controls", "instance_controls"):
        _aggregate(counts[name], CONTROL_STATUSES)
    canonical(row)
    return row


def _rows(value: object) -> list[object]:
    if not isinstance(value, list):
        raise SubworkflowError("invalid_run_observation")
    return value


def _writers(rows: list[object], field: str) -> dict[str, object]:
    counts: dict[str, object] = {name: 0 for name in WRITERS}
    for value in rows:
        writer = mapping(value).get(field)
        if not _one_of(writer, WRITERS):
            raise SubworkflowError("invalid_run_observation")
        counts[str(writer)] = int(counts[str(writer)]) + 1
    return {"total": len(rows), **counts}


def _controls(
    value: object,
    *,
    instance_id: str,
    run_id: str,
    inventory: set[str],
    seen: set[str],
    instance_scope: bool,
) -> dict[str, object]:
    rows = _rows(value)
    counts = {name: 0 for name in CONTROL_STATUSES}
    for value in rows:
        row = mapping(value)
        if (
            not {
                "request_id",
                "request_digest",
                "instance_id",
                "run_id",
                "plane_id",
                "operation",
                "status",
            }
            <= row.keys()
        ):
            raise SubworkflowError("invalid_core_receipt")
        identifier = _id(row["request_id"])
        _sha(row["request_digest"])
        if (
            identifier in seen
            or row["instance_id"] != instance_id
            or not _one_of(row["status"], CONTROL_STATUSES)
        ):
            raise SubworkflowError("invalid_core_receipt")
        seen.add(identifier)
        operation = row["operation"]
        if instance_scope:
            if (
                not _one_of(operation, INSTANCE_OPERATIONS)
                or row["plane_id"] is not None
            ):
                raise SubworkflowError("invalid_core_receipt")
            if operation == "start_run" and row["status"] == "applied":
                if _id(row["run_id"]) not in inventory:
                    raise SubworkflowError("invalid_core_receipt")
            elif row["run_id"] is not None:
                raise SubworkflowError("invalid_core_receipt")
        else:
            if not _one_of(operation, RUN_OPERATIONS) or row["run_id"] != run_id:
                raise SubworkflowError("invalid_core_receipt")
            if operation in PLANE_OPERATIONS:
                if not KEY.fullmatch(_string(row["plane_id"], maximum=64)):
                    raise SubworkflowError("invalid_core_receipt")
            elif row["plane_id"] is not None:
                raise SubworkflowError("invalid_core_receipt")
        if row["status"] == "queued":
            _fields(
                row,
                {
                    "request_id",
                    "request_digest",
                    "instance_id",
                    "run_id",
                    "plane_id",
                    "operation",
                    "status",
                },
            )
        else:
            if not {"state_version", "retryable", "code"} <= row.keys():
                raise SubworkflowError("invalid_core_receipt")
            if (
                type(row["retryable"]) is not bool
                or (row["status"] == "applied" and row["code"] is not None)
                or (row["status"] == "refused" and not isinstance(row["code"], str))
            ):
                raise SubworkflowError("invalid_core_receipt")
            if row["state_version"] is not None:
                _positive(row["state_version"])
        counts[str(row["status"])] += 1
    return {"total": len(rows), **counts}


class _ReadProgress(SubworkflowError):
    """Individually valid public views span forward progress, not one snapshot."""


def reduce_full_views(
    instance_view: object,
    run_view: object,
    *,
    instance_id: str,
    run_id: str,
    start_request_id: str,
    paths: object,
    settings: object,
) -> dict[str, object]:
    """Check complete public views and count every relevant row before reduction.

    The caller supplies views from the matching verified public local facade.
    This function performs no read, authority selection, publication or mutation.
    """
    # Lazy reuse avoids a cycle with v2's fixed private persistent dispatch seam.
    from .subworkflow_v2_records import validate_projection

    _id(instance_id)
    _id(run_id)
    _id(start_request_id)
    instance = mapping(instance_view)
    run = validate_projection(run_view, instance_id, run_id)
    if not run:
        raise SubworkflowError("invalid_run_observation")
    paths = _fields(paths, {"workflow", "project"})
    saved = _settings(settings)
    if (
        instance.get("format") != "millrace-router-instance/v2"
        or instance.get("instance_id") != instance_id
        or instance.get("project_root") != str(canonical_path(paths["project"]))
        or instance.get("workflow_file")
        != str(canonical_path(paths["workflow"]) / "workflow.json")
        or run.get("start_request_id") != start_request_id
        or any(instance.get(name) != saved[name] for name in SETTINGS)
    ):
        raise SubworkflowError("wrong_child_identity")
    instance_version = _positive(instance.get("state_version"))
    captured = _fields(run.get("host_settings"), SETTINGS | {"state_version"})
    _positive(captured["state_version"])
    if {name: captured[name] for name in SETTINGS} != saved:
        raise SubworkflowError("invalid_settings")
    inventory: set[str] = set()
    current = None
    other_nonterminal = 0
    for value in _rows(instance.get("runs")):
        item = mapping(value)
        identifier = _id(item.get("id"))
        if identifier in inventory or not _one_of(item.get("status"), STATUSES):
            raise SubworkflowError("invalid_instance_observation")
        inventory.add(identifier)
        _positive(item.get("state_version"))
        if identifier == run_id:
            current = item
        elif item["status"] not in TERMINAL:
            other_nonterminal += 1
    if current is None:
        raise SubworkflowError("invalid_instance_observation")
    invocations = _rows(run["invocations"])
    known = {str(mapping(value)["id"]): mapping(value) for value in invocations}
    inflight = _rows(run["inflight_invocations"])
    if len(set(inflight)) != len(inflight):
        raise SubworkflowError("invalid_run_observation")
    completed: set[str] = set()
    for value in _rows(run["completions"]):
        item = mapping(value)
        identifier = _id(item.get("invocation_id"))
        if (
            identifier in completed
            or item["effective_writer"] != known[identifier]["writer_state"]
        ):
            raise SubworkflowError("invalid_run_observation")
        completed.add(identifier)
    for value in [*invocations, *_rows(run["completions"])]:
        item = mapping(value)
        if "run_id" in item and item["run_id"] != run_id:
            raise SubworkflowError("invalid_run_observation")
    seen_controls: set[str] = set()
    counts = {
        "inflight_invocations": len(inflight),
        "other_nonterminal_runs": other_nonterminal,
        "invocations": _writers(invocations, "writer_state"),
        "completions": _writers(_rows(run["completions"]), "effective_writer"),
        "run_controls": _controls(
            run["controls"],
            instance_id=instance_id,
            run_id=run_id,
            inventory=inventory,
            seen=seen_controls,
            instance_scope=False,
        ),
        "instance_controls": _controls(
            instance.get("controls"),
            instance_id=instance_id,
            run_id=run_id,
            inventory=inventory,
            seen=seen_controls,
            instance_scope=True,
        ),
    }
    starts = [
        mapping(value)
        for value in _rows(instance["controls"])
        if mapping(value)["request_id"] == start_request_id
    ]
    if len(starts) != 1 or (
        starts[0]["operation"] != "start_run"
        or starts[0]["status"] != "applied"
        or starts[0]["run_id"] != run_id
        or starts[0]["state_version"] != 1
        or type(starts[0]["state_version"]) is not int
        or _positive(starts[0].get("instance_state_version"))
        != captured["state_version"] + 1
        or starts[0]["instance_state_version"] > instance_version
    ):
        raise SubworkflowError("wrong_start_identity")
    observation = {
        "format": OBSERVATION_FORMAT,
        "source": "verified_local_core",
        "instance_id": instance_id,
        "run_id": run_id,
        "start_request_id": start_request_id,
        "instance_state_version": instance_version,
        "state_version": run["state_version"],
        "workflow_file": instance["workflow_file"],
        "project_root": instance["project_root"],
        "settings_sha256": digest(saved),
        "status": run["status"],
        "quiescent": run["quiescent"],
        "counts": counts,
    }
    validate_observation(observation, instance_id, run_id)
    if any(current[name] != run[name] for name in ("id", "status", "state_version")):
        # Classify forward skew only after every other complete-view check.
        # The caller must acquire a new pair; these views are never published.
        if current["state_version"] < run["state_version"] and (
            run["status"] in {"ready", "running", "waiting"}
            or (run["status"] == "succeeded" and closed(observation, attention=True))
        ):
            raise _ReadProgress("invalid_instance_observation")
        raise SubworkflowError("invalid_instance_observation")
    return observation


def closed(value: object, *, attention: bool = False) -> bool:
    """Classify validated facts; a serialized summary is never current authority."""
    if value is None:
        return False
    row = mapping(value)
    checked = validate_observation(value, row.get("instance_id"), row.get("run_id"))
    assert checked is not None
    counts = mapping(checked["counts"])
    return (
        checked["status"] in (TERMINAL | ({"paused"} if attention else set()))
        and checked["quiescent"] is True
        and counts["inflight_invocations"] == 0
        and counts["other_nonterminal_runs"] == 0
        and all(
            mapping(counts[name])["total"] == mapping(counts[name])["stopped"]
            for name in ("invocations", "completions")
        )
        and all(
            mapping(counts[name])["queued"] == 0
            for name in ("run_controls", "instance_controls")
        )
    )


def _view_ref(value: object, raw: bytes | None) -> dict[str, object]:
    row = _fields(value, {"path", "sha256"})
    canonical_path(row["path"])
    _sha(row["sha256"])
    if raw is not None and hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise SubworkflowError("invalid_view_proof")
    return row


def _full_bytes(raw: bytes) -> dict[str, object]:
    try:
        value = mapping(catalog.strict_json(raw))
    except catalog.CatalogError as exc:
        raise SubworkflowError("invalid_view_proof") from exc
    if canonical(value) + b"\n" != raw:
        raise SubworkflowError("invalid_view_proof")
    return value


def validate_proof(
    value: object,
    *,
    local_source: object,
    observation: object,
    instance_view_bytes: bytes | None = None,
    run_view_bytes: bytes | None = None,
) -> dict[str, object]:
    """Validate exact proof refs, optionally recomputing from complete saved bytes.

    Ref ownership, immutable publication, current snapshot exclusion and terminal
    CAS remain the service's responsibility. No proof exists for an absent run.
    """
    row = _fields(
        value,
        {
            "format",
            "local_source",
            "instance_id",
            "run_id",
            "start_request_id",
            "instance_state_version",
            "state_version",
            "instance_view",
            "run_view",
            "observation_sha256",
        },
    )
    validate_local_source(row["local_source"], expected=local_source)
    checked = validate_observation(observation, row["instance_id"], row["run_id"])
    if checked is None or row["format"] != PROOF_FORMAT:
        raise SubworkflowError("invalid_view_proof")
    for name in (
        "instance_id",
        "run_id",
        "start_request_id",
        "instance_state_version",
        "state_version",
    ):
        if row[name] != checked[name] or type(row[name]) is not type(checked[name]):
            raise SubworkflowError("invalid_view_proof")
    if _sha(row["observation_sha256"]) != digest(checked):
        raise SubworkflowError("invalid_view_proof")
    if (instance_view_bytes is None) != (run_view_bytes is None):
        raise SubworkflowError("invalid_view_proof")
    instance_ref = _view_ref(row["instance_view"], instance_view_bytes)
    run_ref = _view_ref(row["run_view"], run_view_bytes)
    if instance_ref["path"] == run_ref["path"]:
        raise SubworkflowError("invalid_view_proof")
    if instance_view_bytes is not None and run_view_bytes is not None:
        instance = _full_bytes(instance_view_bytes)
        run = _full_bytes(run_view_bytes)
        reduced = reduce_full_views(
            instance,
            run,
            instance_id=str(row["instance_id"]),
            run_id=str(row["run_id"]),
            start_request_id=str(row["start_request_id"]),
            paths={
                "workflow": str(Path(str(checked["workflow_file"])).parent),
                "project": checked["project_root"],
            },
            settings={name: instance.get(name) for name in SETTINGS},
        )
        if reduced != checked:
            raise SubworkflowError("invalid_view_proof")
    canonical(row)
    return row


def proof_manifest(
    local_source: object,
    observation: object,
    instance_view_ref: object,
    run_view_ref: object,
) -> dict[str, object]:
    """Build a past-read proof; the caller owns its immutable full-view files."""
    observed = mapping(observation)
    row = {
        "format": PROOF_FORMAT,
        "local_source": local_source,
        **{
            name: observed.get(name)
            for name in (
                "instance_id",
                "run_id",
                "start_request_id",
                "instance_state_version",
                "state_version",
            )
        },
        "instance_view": instance_view_ref,
        "run_view": run_view_ref,
        "observation_sha256": digest(observation),
    }
    return validate_proof(row, local_source=local_source, observation=observation)
