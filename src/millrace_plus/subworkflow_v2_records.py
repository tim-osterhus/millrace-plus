"""Strict bounded v2 child-call files, independent of finite-lifetime v1 calls."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .subworkflow_records import (
    KEY,
    SHA,
    SubworkflowError,
    _fields,
    _id,
    _string,
    atomic_json,
    canonical,
    canonical_path,
    digest,
    envelope,
    mapping,
    request_id,
)
from .subworkflow_records import Journal as V1Journal
from .workflow_os_client import (
    _home,
    _one_of,
    _utf8_json,
    request_body,
    validate_call,
    validate_receipt,
)

__all__ = [
    "Journal",
    "parse_request",
    "parse_journal",
    "validate_journal",
    "input_manifest",
    "write_inputs",
    "preflight",
    "preflight_capacity",
    "result_manifest",
    "parse_result",
    "validate_result",
    "OUTCOMES",
]
REQUEST_FORMAT = "millrace-plus-subworkflow-request/v2"
JOURNAL_FORMAT = "millrace-plus-subworkflow-call/v2"
RESULT_FORMAT = "millrace-plus-subworkflow-result/v2"
REQUEST_LIMIT = INPUT_LIMIT = 1048576
JOURNAL_LIMIT = 2097152
RESULT_LIMIT = 262144
OUTCOMES = {
    "CHILD_RUNNING",
    "CHILD_SUCCEEDED",
    "CHILD_FAILED",
    "CHILD_CANCELLED",
    "CHILD_ATTENTION",
    "CHILD_REFUSED",
    "CHILD_UNCERTAIN",
    "CHILD_OBSERVATION_LOST",
}
STATUSES = {"ready", "running", "waiting", "paused", "succeeded", "failed", "cancelled"}
REFERENCE_FIELDS = {
    "home",
    "request_id",
    "request_digest",
    "instance_id",
    "run_id",
    "source",
    "phase",
    "holder_joined",
    "observed_at",
}
JOURNAL_FIELDS = {
    "format",
    "request",
    "request_raw",
    "request_sha256",
    "claim_sha256",
    "definition",
    "parent",
    "paths",
    "created_at",
    "settings",
    "parent_state_version",
    "os_home",
    "os_origin",
    "os_profile",
    "envelopes",
    "reservations",
    "receipts",
    "instance_id",
    "run_id",
    "os_observations",
    "observation",
    "phase",
    "diagnostic",
    "copy_prepared",
    "output_manifest",
    "saved_result",
    "cancel_intent",
    "cancellations",
    "authority_sha256",
    "record_sha256",
}
AUTHORITY_FIELDS = (
    "request",
    "request_raw",
    "request_sha256",
    "claim_sha256",
    "definition",
    "parent",
    "paths",
    "created_at",
    "settings",
    "parent_state_version",
    "os_home",
    "os_origin",
    "os_profile",
    "output_manifest",
)


def _definition(value: object) -> dict[str, object]:
    definition = _fields(value, {"selector", "version", "sha256"})
    selector = _string(definition["selector"], maximum=256)
    if not re.fullmatch(r"(?:bundled|local):[A-Za-z][A-Za-z0-9_-]{0,63}", selector):
        raise SubworkflowError("invalid_selector")
    sha = _string(definition["sha256"], maximum=64)
    if not SHA.fullmatch(sha):
        raise SubworkflowError("invalid_definition")
    version = _string(definition["version"], maximum=256)
    if selector.startswith("local:") and version != "sha256:" + sha:
        raise SubworkflowError("definition_mismatch")
    return definition


def parse_request(raw: bytes, *, cwd: Path) -> dict[str, object]:
    """Normalize strict v2 inputs without widening any existing v1 grammar."""
    obj = _fields(
        _utf8_json(raw, REQUEST_LIMIT),
        {
            "format",
            "call_key",
            "call_home",
            "parent",
            "definition",
            "output_ref",
            "os_profile",
            "observe_seconds",
            "cleanup_seconds",
        },
        {"inputs"},
    )
    if obj["format"] != REQUEST_FORMAT or not KEY.fullmatch(_string(obj["call_key"])):
        raise SubworkflowError("invalid_request")
    home = canonical_path(obj["call_home"])
    parent = _fields(obj["parent"], {"instance_id"}, {"run_id", "plane_id", "node_id"})
    _id(parent["instance_id"])
    if "run_id" in parent:
        _id(parent["run_id"])
    for name in ("plane_id", "node_id"):
        if name in parent and not KEY.fullmatch(_string(parent[name], maximum=64)):
            raise SubworkflowError("invalid_parent")
    _definition(obj["definition"])
    profile = _fields(obj["os_profile"], {"path", "sha256"})
    canonical_path(profile["path"])
    if not SHA.fullmatch(_string(profile["sha256"], maximum=64)):
        raise SubworkflowError("invalid_os_profile")
    for name, low, high in (("observe_seconds", 1, 300), ("cleanup_seconds", 5, 60)):
        if type(obj[name]) is not int or not low <= obj[name] <= high:
            raise SubworkflowError("invalid_budget")
    output = _string(obj["output_ref"])
    if (
        Path(output).is_absolute()
        or "\\" in output
        or any(part in {"", ".", ".."} for part in output.split("/"))
    ):
        raise SubworkflowError("invalid_output_ref")
    root = canonical_path(str(cwd))
    canonical_path(str(root / output))
    inputs = mapping(obj.get("inputs", {}))
    canonical(inputs)
    return {**obj, "call_home": str(home), "inputs": inputs, "output_root": str(root)}


def bounded(value: object, limit: int) -> bytes:
    raw = canonical(value) + b"\n"
    if len(raw) > limit:
        raise SubworkflowError("record_too_large")
    return raw


def input_manifest(request: dict[str, object]) -> dict[str, object]:
    return {
        "format": "millrace-plus-subworkflow-inputs/v1",
        "identity": {
            name: request[name] for name in ("call_home", "call_key", "output_root")
        },
        "inputs": request["inputs"],
    }


def write_inputs(path: Path, request: dict[str, object]) -> None:
    """Create once; replay must match the exact canonical v1-marker bytes."""
    raw = bounded(input_manifest(request), INPUT_LIMIT)
    canonical_path(str(path))
    if os.path.lexists(path):
        with path.open("rb") as stream:
            if stream.read(INPUT_LIMIT + 1) != raw:
                raise SubworkflowError("input_manifest_conflict")
    else:
        atomic_json(path, input_manifest(request), limit=INPUT_LIMIT)


def authority(record: dict[str, object]) -> dict[str, object]:
    return {name: record[name] for name in AUTHORITY_FIELDS} | (
        {"local_source": record["local_source"]} if "local_source" in record else {}
    )


def preflight(record: dict[str, object]) -> None:
    """Check all exact file and HTTP envelopes before a local mutation or send."""
    req = mapping(record["request"])
    bounded({k: v for k, v in req.items() if k != "output_root"}, REQUEST_LIMIT)
    bounded(input_manifest(req), INPUT_LIMIT)
    bounded(record, JOURNAL_LIMIT)
    for value in mapping(record["envelopes"]).values():
        if value is not None:
            request_body(str(record["os_home"]), mapping(value))


def preflight_capacity(record: dict[str, object]) -> None:
    """Size a worst-case 65,536-byte escaped task using supplied frozen metadata."""
    import copy

    maximum = copy.deepcopy(record)
    req = mapping(maximum["request"])
    inputs = mapping(req["inputs"])
    inputs["request_markdown"] = "\x01" * 65536
    raw_request = {k: v for k, v in req.items() if k != "output_root"}
    maximum["request_raw"] = canonical(raw_request).decode("utf-8")
    maximum["request_sha256"] = digest(req)
    preflight(maximum)


def _nullable_id(value: object) -> None:
    if value is not None:
        _id(value)


def _diagnostic(value: object) -> None:
    if value is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", _string(value)):
        raise SubworkflowError("invalid_diagnostic")


def validate_projection(
    value: object,
    instance: object,
    run: object,
) -> dict[str, object]:
    """Check exact run identity and public writer facts."""
    row = mapping(value)
    if not row:
        return row
    required = {
        "id",
        "instance_id",
        "start_request_id",
        "status",
        "state_version",
        "quiescent",
        "inflight_invocations",
        "invocations",
        "completions",
        "controls",
        "planes",
    }
    if not required <= row.keys():
        raise SubworkflowError("invalid_run_observation")
    if (
        row["id"] != run
        or row["instance_id"] != instance
        or not _one_of(row["status"], STATUSES)
        or type(row["quiescent"]) is not bool
        or type(row["state_version"]) is not int
        or row["state_version"] < 1
    ):
        raise SubworkflowError("invalid_run_observation")
    for name in ("inflight_invocations", "invocations", "completions", "controls"):
        if not isinstance(row[name], list):
            raise SubworkflowError("invalid_run_observation")
    mapping(row["planes"])
    known = set()
    for item in row["invocations"]:
        invocation = mapping(item)
        identifier = _id(invocation.get("id"))
        if identifier in known or not _one_of(
            invocation.get("writer_state"),
            {
                "stopped",
                "may_live",
                "running",
                "unknown",
            },
        ):
            raise SubworkflowError("invalid_run_observation")
        known.add(identifier)
        _string(invocation.get("status"), maximum=64)
    for identifier in row["inflight_invocations"]:
        if _id(identifier) not in known:
            raise SubworkflowError("invalid_run_observation")
    for item in row["completions"]:
        completion = mapping(item)
        if _id(completion.get("invocation_id")) not in known or not _one_of(
            completion.get("effective_writer"),
            {"stopped", "may_live", "running", "unknown"},
        ):
            raise SubworkflowError("invalid_run_observation")
    canonical(row)
    return row


def validate_journal(
    value: object,
    home: Path,
    key: str,
    *,
    _local_source: dict[str, object] | None = None,
) -> dict[str, object]:
    persistent = _local_source is not None
    row = _fields(value, JOURNAL_FIELDS | ({"local_source"} if persistent else set()))
    marker = JOURNAL_FORMAT
    if persistent:
        from .subworkflow_persistent_records import JOURNAL_FORMAT as marker
        from .subworkflow_persistent_records import validate_local_source

        validate_local_source(row["local_source"], expected=_local_source)
        if row["claim_sha256"] is None:
            raise SubworkflowError("persistent_claim_required")
    if (
        row["format"] != marker
        or row["record_sha256"]
        != digest({k: v for k, v in row.items() if k != "record_sha256"})
        or row["authority_sha256"] != digest(authority(row))
    ):
        raise SubworkflowError("invalid_journal")
    req = mapping(row["request"])
    raw = _string(row["request_raw"], maximum=REQUEST_LIMIT).encode("utf-8")
    normalized = parse_request(raw, cwd=canonical_path(req.get("output_root")))
    if (
        normalized != req
        or req["call_home"] != str(home)
        or req["call_key"] != key
        or row["request_sha256"] != digest(req)
        or row["definition"] != req["definition"]
        or row["parent"] != req["parent"]
        or row["os_profile"] != req["os_profile"]
        or (
            row["claim_sha256"] is not None
            and not SHA.fullmatch(_string(row["claim_sha256"], maximum=64))
        )
    ):
        raise SubworkflowError("invalid_journal")
    _home(row["os_home"])
    if persistent:
        source = mapping(row["local_source"])
        parent = mapping(req["parent"])
        if (
            source["parent_instance_id"] != parent["instance_id"]
            or source["parent_run_id"] != parent.get("run_id")
            or source["os_home"] != row["os_home"]
            or source["os_profile_sha256"] != mapping(row["os_profile"])["sha256"]
        ):
            raise SubworkflowError("persistent_source_mismatch")
    from .integrations.paperclip_records import BridgeError, _origin

    try:
        _origin(row["os_origin"], loopback=True)
    except BridgeError as exc:
        raise SubworkflowError("invalid_journal") from exc
    root = home / key
    paths = {name: str(root / name) for name in ("workflow", "project")}
    if row["paths"] != paths or row["output_manifest"] != str(
        Path(str(req["output_root"])) / str(req["output_ref"])
    ):
        raise SubworkflowError("invalid_journal")
    for path in paths.values():
        canonical_path(path)
    from .subworkflow_records import _date

    _date(row["created_at"])
    settings = _fields(
        row["settings"],
        {
            "path",
            "env_allowlist",
            "host_paths",
            "runtime_defaults",
            "operator_policy",
        },
    )
    for name in ("path", "env_allowlist"):
        if not isinstance(settings[name], list) or any(
            not isinstance(item, str) for item in settings[name]
        ):
            raise SubworkflowError("invalid_settings")
    for name in ("host_paths", "runtime_defaults", "operator_policy"):
        mapping(settings[name])
    if type(row["parent_state_version"]) is not int or row["parent_state_version"] < 1:
        raise SubworkflowError("invalid_journal")
    envs = _fields(row["envelopes"], {"create", "start", "cancel"})
    expected_create = envelope(
        "create_instance",
        req,
        instance_id=None,
        expected=0,
        payload={
            "workflow_file": paths["workflow"] + "/workflow.json",
            "project_root": paths["project"],
            **settings,
        },
    )
    if envs["create"] != expected_create:
        raise SubworkflowError("invalid_envelope")
    _nullable_id(row["instance_id"])
    _nullable_id(row["run_id"])
    if envs["start"] is not None and envs["start"] != envelope(
        "start_run",
        req,
        instance_id=_id(row["instance_id"]),
        expected=1,
        payload={"edit_mode": "locked"},
    ):
        raise SubworkflowError("invalid_envelope")
    if envs["cancel"] is not None:
        cancel = mapping(envs["cancel"])
        if cancel != envelope(
            "cancel_run",
            req,
            instance_id=_id(row["instance_id"]),
            run_id=_id(row["run_id"]),
            explicit_id=_id(cancel.get("request_id")),
        ):
            raise SubworkflowError("invalid_envelope")
    reservations = _fields(row["reservations"], {"create", "start", "cancel"})
    for name, state in reservations.items():
        if not _one_of(state, {"unsent", "unknown", "observed"}):
            raise SubworkflowError("invalid_reservation")
        if envs[name] is None and state != "unsent":
            raise SubworkflowError("invalid_reservation")
    receipts = mapping(row["receipts"])
    for name, receipt in receipts.items():
        if name not in envs or envs[name] is None:
            raise SubworkflowError("invalid_core_receipt")
        validate_receipt(receipt, mapping(envs[name]))
        if reservations[name] == "unsent":
            raise SubworkflowError("invalid_reservation")
    for name, field in (("create", "instance_id"), ("start", "run_id")):
        if row[field] is not None:
            receipt = mapping(receipts.get(name))
            if receipt.get("status") != "applied" or receipt.get(field) != row[field]:
                raise SubworkflowError("invalid_child_identity")
            if name == "start" and receipt.get("instance_id") != row["instance_id"]:
                raise SubworkflowError("invalid_child_identity")
    cancellations = mapping(row["cancellations"])
    intent = row["cancel_intent"]
    if (intent is None) != (not cancellations):
        raise SubworkflowError("invalid_cancel_intent")
    distinct = {
        str(mapping(value)["request_id"]): (mapping(value), reservations[name])
        for name, value in envs.items()
        if name != "cancel" and value is not None
    }
    for identifier, value in cancellations.items():
        _id(identifier)
        item = _fields(value, {"created_at", "envelope", "reservation", "receipt"})
        _date(item["created_at"])
        if (
            identifier in distinct
            or identifier == request_id(req, "start_run")
            or not _one_of(item["reservation"], {"unsent", "unknown", "observed"})
        ):
            raise SubworkflowError("invalid_cancellation")
        if item["envelope"] is None:
            if item["reservation"] != "unsent" or item["receipt"] is not None:
                raise SubworkflowError("invalid_cancellation")
        else:
            expected = envelope(
                "cancel_run",
                req,
                instance_id=_id(row["instance_id"]),
                run_id=_id(row["run_id"]),
                explicit_id=identifier,
            )
            if item["envelope"] != expected:
                raise SubworkflowError("invalid_cancellation")
            distinct[identifier] = (expected, item["reservation"])
            if item["receipt"] is not None:
                validate_receipt(item["receipt"], expected)
                if item["reservation"] == "unsent":
                    raise SubworkflowError("invalid_cancellation")
    if intent is None:
        if envs["cancel"] is not None or "cancel" in receipts:
            raise SubworkflowError("invalid_cancel_intent")
    else:
        selected = _fields(intent, {"request_id", "created_at"})
        first = mapping(cancellations.get(_id(selected["request_id"])))
        _date(selected["created_at"])
        if (
            selected["created_at"] != first["created_at"]
            or envs["cancel"] != first["envelope"]
            or reservations["cancel"] != first["reservation"]
            or receipts.get("cancel") != first["receipt"]
        ):
            raise SubworkflowError("invalid_cancel_alias")
    if not isinstance(row["os_observations"], list):
        raise SubworkflowError("invalid_os_history")
    for observation in row["os_observations"]:
        observed = mapping(observation)
        selected = distinct.get(str(observed.get("request_id")))
        if selected is None:
            raise SubworkflowError("invalid_os_history")
        sent, reservation = selected
        validate_call(observed, str(row["os_home"]), sent)
        if reservation == "unsent":
            raise SubworkflowError("invalid_reservation")
    if persistent:
        from .subworkflow_persistent_records import validate_observation

        projection = validate_observation(
            row["observation"],
            row["instance_id"],
            row["run_id"],
            expected_start_request_id=request_id(req, "start_run"),
            expected_paths=paths,
            expected_settings=settings,
        )
    else:
        projection = validate_projection(
            row["observation"],
            row["instance_id"],
            row["run_id"],
        )
    if projection and projection["start_request_id"] != request_id(req, "start_run"):
        raise SubworkflowError("wrong_start_identity")
    if (
        not _one_of(
            row["phase"],
            {
                "prepared",
                "copying",
                "creating",
                "created",
                "starting",
                "observing",
                "held",
                "terminal",
            },
        )
        or type(row["copy_prepared"]) is not bool
    ):
        raise SubworkflowError("invalid_journal")
    _diagnostic(row["diagnostic"])
    if row["saved_result"] is not None:
        result = _fields(row["saved_result"], {"outcome", "summary", "refs"})
        if not _one_of(result["outcome"], OUTCOMES) or result["refs"] != [
            req["output_ref"]
        ]:
            raise SubworkflowError("invalid_result")
        _string(result["summary"], maximum=4096)
    preflight(row)
    return row


def parse_journal(raw: bytes, *, home: Path, key: str) -> dict[str, object]:
    return validate_journal(_utf8_json(raw, JOURNAL_LIMIT), home, key)


class Journal(V1Journal):
    """Reuse v1 short locks and atomic writes, with separate v2 validation/bounds."""

    def __init__(
        self, home: Path, key: str, *, _local_source: dict[str, object] | None = None
    ) -> None:
        super().__init__(home, key)
        self.local_source = _local_source

    def load(self) -> dict[str, object]:
        canonical_path(str(self.path))
        try:
            with self.path.open("rb") as stream:
                return validate_journal(
                    _utf8_json(stream.read(JOURNAL_LIMIT + 1), JOURNAL_LIMIT),
                    self.home,
                    self.key,
                    _local_source=self.local_source,
                )
        except OSError as exc:
            raise SubworkflowError("call_missing") from exc
        except (TypeError, KeyError, ValueError) as exc:
            raise SubworkflowError("invalid_journal") from exc

    def save(self, value: dict[str, object]) -> None:
        value["record_sha256"] = digest(
            {k: v for k, v in value.items() if k != "record_sha256"}
        )
        validate_journal(value, self.home, self.key, _local_source=self.local_source)
        atomic_json(self.path, value, limit=JOURNAL_LIMIT)


def result_manifest(
    record: dict[str, object],
    outcome: str,
    diagnostic: str | None,
) -> dict[str, object]:
    req = mapping(record["request"])
    persistent = "local_source" in record
    observation = (
        record["observation"]
        if persistent
        else {
            key: value
            for key, value in mapping(record["observation"]).items()
            if key != "draft_revision"
        }
    )
    marker = RESULT_FORMAT
    if persistent:
        from .subworkflow_persistent_records import RESULT_FORMAT as marker
    return {
        "format": marker,
        "call_identity": record["request_sha256"],
        "call_key": req["call_key"],
        "definition": req["definition"],
        "instance_id": record["instance_id"],
        "run_id": record["run_id"],
        "dispatch": "started" if record["run_id"] is not None else "not_started",
        "status": mapping(observation).get("status")
        if observation is not None
        else None,
        "observation": observation,
        "receipts": record["receipts"],
        "os_observations": [
            {k: mapping(row)[k] for k in REFERENCE_FIELDS}
            for row in record["os_observations"]
        ],
        "outcome": outcome,
        "diagnostic": diagnostic,
    } | ({"local_source": record["local_source"]} if persistent else {})


def validate_result(
    value: object, *, _local_source: dict[str, object] | None = None
) -> dict[str, object]:
    persistent = _local_source is not None
    marker = RESULT_FORMAT
    if persistent:
        from .subworkflow_persistent_records import RESULT_FORMAT as marker
        from .subworkflow_persistent_records import validate_local_source

        validate_local_source(
            mapping(value).get("local_source"), expected=_local_source
        )
    row = _fields(
        value,
        {
            "format",
            "call_identity",
            "call_key",
            "definition",
            "instance_id",
            "run_id",
            "dispatch",
            "status",
            "observation",
            "receipts",
            "os_observations",
            "outcome",
            "diagnostic",
        }
        | ({"local_source"} if persistent else set()),
    )
    if (
        row["format"] != marker
        or not _one_of(row["outcome"], OUTCOMES)
        or not SHA.fullmatch(_string(row["call_identity"], maximum=64))
        or not KEY.fullmatch(_string(row["call_key"], maximum=64))
        or row["dispatch"]
        != ("started" if row["run_id"] is not None else "not_started")
        or (row["status"] is not None and not _one_of(row["status"], STATUSES))
    ):
        raise SubworkflowError("invalid_result")
    _nullable_id(row["instance_id"])
    _nullable_id(row["run_id"])
    _definition(row["definition"])
    if persistent:
        from .subworkflow_persistent_records import validate_observation

        projection = validate_observation(
            row["observation"],
            row["instance_id"],
            row["run_id"],
        )
        if (projection is None) != (row["status"] is None):
            raise SubworkflowError("invalid_result")
    else:
        projection = validate_projection(
            row["observation"], row["instance_id"], row["run_id"]
        )
    if projection and projection["status"] != row["status"]:
        raise SubworkflowError("invalid_result")
    receipts = mapping(row["receipts"])
    if receipts.keys() - {"create", "start", "cancel"}:
        raise SubworkflowError("invalid_result")
    operations = {
        "create": "create_instance",
        "start": "start_run",
        "cancel": "cancel_run",
    }
    for name, value in receipts.items():
        receipt = mapping(value)
        _id(receipt.get("request_id"))
        if (
            receipt.get("operation") != operations[name]
            or not _one_of(receipt.get("status"), {"queued", "applied", "refused"})
            or not SHA.fullmatch(_string(receipt.get("request_digest"), maximum=64))
        ):
            raise SubworkflowError("invalid_result")
        for field in ("instance_id", "run_id"):
            _nullable_id(receipt.get(field))
    if not isinstance(row["os_observations"], list):
        raise SubworkflowError("invalid_result")
    for reference in row["os_observations"]:
        ref = _fields(reference, REFERENCE_FIELDS)
        _home(ref["home"])
        _id(ref["request_id"])
        for name in ("instance_id", "run_id"):
            _nullable_id(ref[name])
        if (
            not _one_of(ref["source"], {"os_owned_call", "core_receipt"})
            or not _one_of(
                ref["phase"],
                {
                    "admission_pending",
                    "call_active",
                    "call_returned",
                    "call_failed",
                    "receipt_observed",
                },
            )
            or (
                ref["request_digest"] is not None
                and not SHA.fullmatch(_string(ref["request_digest"], maximum=64))
            )
            or (
                ref["holder_joined"] is not None
                and type(ref["holder_joined"]) is not bool
            )
            or not isinstance(ref["observed_at"], str)
            or len(ref["observed_at"]) > 40
            or (ref["source"] == "core_receipt" and ref["holder_joined"] is not None)
        ):
            raise SubworkflowError("invalid_result")
    _diagnostic(row["diagnostic"])
    bounded(row, RESULT_LIMIT)
    return row


def parse_result(raw: bytes) -> dict[str, object]:
    return validate_result(_utf8_json(raw, RESULT_LIMIT))
