"""Verified persistent LAD setup and finite graph/operator lifecycle operations.

Core is imported only after the service's immutable local-home authority is checked.
The ordinary intake and catalog modules do not depend on this runtime.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import cast

from . import catalog
from .lad_service import (
    Service,
    _check_roots,
    _definition,
    _filesystem_identity,
    _overlap,
    _path,
    _read_exact,
    claim_target,
    materialize_exact,
    target_lock,
)
from .local_intake import IntakeError, canonical, fields, identity, strict_json
from .subworkflow_records import SubworkflowError, digest, mapping
from .subworkflow_v2 import SETTINGS, Client, _advance_bytes
from .subworkflow_v2_records import Journal, preflight_capacity
from .workflow_os_client import (
    WorkflowOSClient,
    load_profile,
    request_body,
    validate_call,
    validate_capabilities,
    validate_receipt,
)

INIT = "millrace-plus-lad-init-intent/v1"
LIFECYCLE = "millrace-plus-lad-lifecycle/v1"


def _core():
    from .router import require_core

    require_core()
    from .router import control

    if any(
        not callable(getattr(control, name, None))
        for name in (
            "inspect_instance",
            "inspect_run",
            "exclusive_run_snapshot",
        )
    ):
        raise IntakeError("local_core_incompatible")
    return control


def _env(
    operation: str,
    identifier: str,
    *,
    instance: object = None,
    run: object = None,
    expected: object = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "format": "millrace-router-request/v2",
        "operation": operation,
        "instance_id": instance,
        "run_id": run,
        "plane_id": None,
        "request_id": identity(identifier),
        "expected_state_version": expected,
        "payload": payload or {},
    }


def _operation_id(service_id: str, name: str) -> str:
    return uuid.uuid5(uuid.UUID(hex=service_id), name).hex


def _settings(target: Path) -> dict[str, object]:
    return {
        "path": list(
            dict.fromkeys([str(Path(sys.executable).parent), *os.get_exec_path()])
        ),
        "env_allowlist": ["HOME"],
        "host_paths": {"lad_target": str(target)},
        "runtime_defaults": {},
        "operator_policy": {},
    }


def _capacity(
    config: dict[str, object],
    settings: dict[str, object],
    payload: bytes = b"capacity",
    *,
    parent: str = "0" * 32,
    run: str = "0" * 32,
) -> None:
    """Pure sizing before setup/acceptance; no promise about later history growth."""
    task = "0" * 32
    home = Path(str(config["service_home"]))
    target = {
        "format": "millrace-lad-target/v1",
        "service_id": config["service_id"],
        "task_id": task,
        "request_sha256": hashlib.sha256(payload).hexdigest(),
        "target_root": config["target_root"],
        "target_identity": config["target_identity"],
        "claim_sha256": "0" * 64,
        "parent_instance_id": parent,
        "parent_run_id": run,
        "worker_definition_sha256": mapping(config["worker_definition"])["sha256"],
    }
    req = {
        "format": "millrace-plus-subworkflow-request/v2",
        "call_key": "task_" + task,
        "call_home": str(home / "calls"),
        "parent": {"instance_id": parent, "run_id": run},
        "definition": config["worker_definition"],
        "output_ref": "results/" + task + ".json",
        "output_root": str(home / "project" / "service"),
        "os_profile": config["os_profile"],
        "observe_seconds": 60,
        "cleanup_seconds": 30,
        "inputs": {"request_markdown": payload.decode("utf-8"), "lad_target": target},
    }
    create = _env(
        "create_instance",
        task,
        expected=0,
        payload={
            "workflow_file": str(
                home / "calls" / ("task_" + task) / "workflow" / "workflow.json"
            ),
            "project_root": str(home / "calls" / ("task_" + task) / "project"),
            **settings,
        },
    )
    record = {
        "request": req,
        "request_raw": canonical(
            {k: v for k, v in req.items() if k != "output_root"}
        ).decode(),
        "request_sha256": digest(req),
        "os_home": config["os_home"],
        "envelopes": {"create": create},
    }
    preflight_capacity(record)
    request_body(str(config["os_home"]), create)


def _parent_envelope(value: object) -> dict[str, object]:
    sent = fields(
        value,
        {
            "format",
            "operation",
            "instance_id",
            "run_id",
            "plane_id",
            "request_id",
            "expected_state_version",
            "payload",
        },
    )
    identity(sent["request_id"])
    identity(sent["instance_id"])
    identity(sent["run_id"])
    if (
        sent["format"] != "millrace-router-request/v2"
        or not isinstance(sent["operation"], str)
        or sent["operation"]
        not in {"pause_run", "resume_run", "recover_run", "cancel_run", "resume_plane"}
        or sent["plane_id"]
        != ("service" if sent["operation"] == "resume_plane" else None)
        or sent["payload"] != {}
    ):
        raise IntakeError("invalid_parent_control")
    version = sent["expected_state_version"]
    if sent["operation"] in {"pause_run", "cancel_run"}:
        if version is not None:
            raise IntakeError("invalid_parent_control_version")
    elif type(version) is not int or version < 1:
        raise IntakeError("invalid_parent_control_version")
    return sent


def _continuation(evidence: object) -> tuple[dict[str, object], str | None]:
    """Read the explicit selector beside an opaque operator ref, never its bytes."""
    value = mapping(evidence)
    if set(value) == {"path", "sha256"}:
        return value, None
    selected = fields(value, {"operator_evidence", "resume_plane"})
    plane = selected["resume_plane"]
    if not isinstance(plane, str) or plane not in {"foreground", "learning"}:
        raise IntakeError("invalid_resume_plane")
    return fields(selected["operator_evidence"], {"path", "sha256"}), plane


def _purpose_row(
    service: Service, value: object, expected: object
) -> dict[str, object]:
    """Validate one purpose-bound saved call without Core or network access."""
    row = fields(value, {"envelope", "reservation", "receipt", "observations"})
    if (
        row["envelope"] != expected
        or not isinstance(row["reservation"], str)
        or row["reservation"] not in {"unsent", "unknown", "observed"}
        or not isinstance(row["observations"], list)
        or (
            row["reservation"] == "unsent"
            and (row["receipt"] is not None or row["observations"])
        )
    ):
        raise IntakeError("invalid_lifecycle_call")
    if expected is None:
        if row["reservation"] != "unsent":
            raise IntakeError("invalid_lifecycle_call")
        return row
    sent = mapping(expected)
    request_body(str(service.config["os_home"]), sent)
    if row["receipt"] is not None:
        validate_receipt(row["receipt"], sent)
    for value in row["observations"]:
        observed = validate_call(value, str(service.config["os_home"]), sent)
        receipt = observed["core_receipt"]
        if receipt is not None and (
            row["receipt"] is None
            or (mapping(receipt)["status"] != "queued" and receipt != row["receipt"])
        ):
            raise IntakeError("lifecycle_receipt_conflict")
    if (
        row["reservation"] == "observed"
        and not row["observations"]
        and row["receipt"] is None
    ):
        raise IntakeError("invalid_lifecycle_call")
    return row


def _source_descriptor(
    service: Service, intent: dict[str, object], record: dict[str, object]
) -> dict[str, object]:
    return {
        "format": "millrace-plus-lad-local-source/v1",
        "service_id": service.service_id,
        "service_home": str(service.home),
        "init_intent_sha256": digest(intent),
        "core_state_home": intent["core_state_home"],
        "os_home": service.config["os_home"],
        "os_profile_sha256": mapping(service.config["os_profile"])["sha256"],
        "parent_instance_id": record["instance_id"],
        "parent_run_id": record["run_id"],
    }


def _load_lifecycle(service: Service) -> tuple[dict[str, object], dict[str, object]]:
    intent, record, tasks = service.intake._lifecycle_tasks()
    return _validate_lifecycle(service, intent, record, _tasks=tasks)


def _validate_lifecycle(
    service: Service,
    intent: dict[str, object],
    record: dict[str, object],
    *,
    _tasks: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    fields(
        intent, {"format", "core_state_home", "service_sha256", "settings", "create"}
    )
    fields(
        record,
        {
            "format",
            "create",
            "start",
            "instance_id",
            "run_id",
            "handshake",
            "stops",
            "maintenance",
        },
    )
    if (
        intent["format"] != INIT
        or record["format"] != LIFECYCLE
        or intent["service_sha256"]
        != hashlib.sha256(_read_exact(service.home / "service.json")).hexdigest()
        or digest(intent["settings"]) != service.config["settings_sha256"]
    ):
        raise IntakeError("lifecycle_authority_changed")
    core_home = _path(intent["core_state_home"], directory=True)
    roots = (
        service.home,
        Path(str(service.config["target_root"])),
        *catalog.known_roots(),
    )
    if any(_overlap(core_home, root) for root in roots):
        raise IntakeError("core_home_overlap")
    expected_create = _env(
        "create_instance",
        _operation_id(service.service_id, "parent-create"),
        expected=0,
        payload={
            "workflow_file": str(service.home / "workflow" / "workflow.json"),
            "project_root": str(service.home / "project"),
            **mapping(intent["settings"]),
        },
    )
    if (
        mapping(record["create"])["envelope"] != intent["create"]
        or intent["create"] != expected_create
        or mapping(mapping(intent["settings"])["host_paths"]).get("lad_target")
        != service.config["target_root"]
    ):
        raise IntakeError("lifecycle_authority_changed")
    if record["instance_id"] is not None:
        receipt = mapping(mapping(record["create"])["receipt"])
        if (
            receipt.get("status") != "applied"
            or receipt.get("instance_id") != record["instance_id"]
        ):
            raise IntakeError("lifecycle_authority_changed")
    if record["handshake"] is not None:
        handshake = fields(
            record["handshake"], {"state_version", "instance_id", "settings_sha256"}
        )
        if (
            handshake["instance_id"] != record["instance_id"]
            or handshake["settings_sha256"] != service.config["settings_sha256"]
            or type(handshake["state_version"]) is not int
            or handshake["state_version"] < 1
            or mapping(record["start"])["envelope"]
            != _env(
                "start_run",
                _operation_id(service.service_id, "parent-start"),
                instance=record["instance_id"],
                expected=handshake["state_version"],
                payload={"edit_mode": "locked"},
            )
        ):
            raise IntakeError("lifecycle_authority_changed")
    elif mapping(record["start"])["envelope"] is not None:
        raise IntakeError("parent_handshake_required")
    if record["run_id"] is not None:
        start = mapping(mapping(record["start"])["receipt"])
        if start.get("status") != "applied" or start.get("run_id") != record["run_id"]:
            raise IntakeError("lifecycle_authority_changed")
    _purpose_row(service, record["create"], expected_create)
    handshake = record["handshake"]
    expected_start = (
        None
        if handshake is None
        else _env(
            "start_run",
            _operation_id(service.service_id, "parent-start"),
            instance=record["instance_id"],
            expected=mapping(handshake)["state_version"],
            payload={"edit_mode": "locked"},
        )
    )
    _purpose_row(service, record["start"], expected_start)
    rows = [mapping(record[label]) for label in ("create", "start")]
    tasks = {
        str(task["task_id"]): task
        for task in (service.intake.tasks() if _tasks is None else _tasks)
    }
    for request_id, value in mapping(record["stops"]).items():
        identity(request_id)
        stop = fields(value, {"intent", "phase", "pause", "cancel"})
        saved = fields(
            stop["intent"],
            {
                "parent_instance_id",
                "parent_run_id",
                "task_id",
                "claim_sha256",
                "task_version",
                "request_sha256",
            },
        )
        if (
            not isinstance(stop["phase"], str)
            or stop["phase"] not in {"pause-claims", "complete"}
            or saved["parent_instance_id"] != record["instance_id"]
            or saved["parent_run_id"] != record["run_id"]
        ):
            raise IntakeError("invalid_stop")
        identity(saved["parent_instance_id"])
        identity(saved["parent_run_id"])
        if saved["task_id"] is None:
            if any(
                saved[name] is not None
                for name in (
                    "claim_sha256",
                    "task_version",
                    "request_sha256",
                )
            ):
                raise IntakeError("invalid_stop")
        else:
            task = tasks.get(identity(saved["task_id"]))
            if (
                task is None
                or task["claim"] is None
                or digest(task["claim"]) != saved["claim_sha256"]
                or task["request_sha256"] != saved["request_sha256"]
                or type(saved["task_version"]) is not int
                or not 1 <= saved["task_version"] <= task["version"]
            ):
                raise IntakeError("invalid_stop")
        for label, operation in (("pause", "pause_run"), ("cancel", "cancel_run")):
            _purpose_row(
                service,
                stop[label],
                _env(
                    operation,
                    _operation_id(request_id, "parent-" + label),
                    instance=record["instance_id"],
                    run=record["run_id"],
                ),
            )
            rows.append(mapping(stop[label]))
    for key, value in mapping(record["maintenance"]).items():
        item = mapping(value)
        if item.get("scope") == "parent":
            fields(item, {"scope", "task_id", "request_raw", "call"})
            if not isinstance(item["request_raw"], str):
                raise IntakeError("invalid_parent_control")
            sent = _parent_envelope(strict_json(item["request_raw"].encode()))
            if (
                item["task_id"] is not None
                or key != "parent:" + sent["request_id"]
                or sent["instance_id"] != record["instance_id"]
                or sent["run_id"] != record["run_id"]
            ):
                raise IntakeError("invalid_parent_control")
            _purpose_row(service, item["call"], sent)
            rows.append(mapping(item["call"]))
            continue
        identity(key)
        fields(
            item,
            {
                "task_id",
                "operator",
                "instance_id",
                "run_id",
                "pause",
                "recover",
                "resume",
                "recovery_before",
                "recovery_proof",
            }
            | ({"attention"} if "attention" in item else set()),
        )
        task = tasks.get(identity(item["task_id"]))
        if task is None or task["claim"] is None:
            raise IntakeError("maintenance_task_missing")
        claim = mapping(task["claim"])
        source = _source_descriptor(service, intent, record)
        call = Journal(
            Path(str(claim["call_home"])), str(claim["call_key"]), _local_source=source
        ).load()
        if (
            call["claim_sha256"] != digest(claim)
            or call["definition"] != claim["definition"]
            or mapping(mapping(call["request"])["inputs"]).get("request_markdown")
            != cast(bytes, task["request_bytes"]).decode()
            or item["instance_id"] != call["instance_id"]
            or item["run_id"] != call["run_id"]
            or call["run_id"] is None
        ):
            raise IntakeError("maintenance_call_conflict")
        with service.intake._connection() as connection:
            decision_row = connection.execute(
                "SELECT intent FROM decisions WHERE request_id=?",
                (key,),
            ).fetchone()
        if decision_row is None:
            raise IntakeError("maintenance_decision_missing")
        decision = fields(
            strict_json(decision_row["intent"].encode()),
            {"task_id", "decision", "evidence"},
        )
        operator, resume_plane = (
            _continuation(decision["evidence"])
            if decision["decision"] == "continue"
            else (mapping(decision["evidence"]), None)
        )
        if (
            decision["task_id"] != task["task_id"]
            or operator != item["operator"]
            or decision["decision"] not in {"continue", "close-failed"}
        ):
            raise IntakeError("maintenance_decision_conflict")
        from .lad_service_disposition import read_ref

        read_ref(service, item["operator"])
        before_run = None
        if item["recovery_before"] is not None:
            before_views = fields(
                strict_json(read_ref(service, item["recovery_before"])),
                {"instance", "run"},
            )
            before_run = mapping(before_views["run"])
            if (
                before_run.get("id") != call["run_id"]
                or before_run.get("instance_id") != call["instance_id"]
            ):
                raise IntakeError("maintenance_call_conflict")
        for label, operation in (
            ("pause", "pause_run"),
            ("recover", "recover_run"),
            ("resume", "resume_run"),
        ):
            row = mapping(item[label])
            expected = None
            if row.get("envelope") is not None:
                version = mapping(row["envelope"]).get("expected_state_version")
                if label == "pause":
                    version = None
                elif type(version) is not int or version < 1:
                    raise IntakeError("invalid_maintenance_version")
                if label == "recover" and (
                    before_run is None or version != before_run.get("state_version")
                ):
                    raise IntakeError("invalid_maintenance_version")
                expected = _env(
                    operation,
                    _operation_id(key, "child-" + label),
                    instance=call["instance_id"],
                    run=call["run_id"],
                    expected=version,
                )
                if label == "resume" and decision["decision"] == "close-failed":
                    expected.update(
                        operation="redirect_plane",
                        plane_id="foreground",
                        payload={
                            "node_id": "terminal_failure",
                            "reason": "explicit_lad_close_failed",
                        },
                    )
                elif label == "resume" and resume_plane is not None:
                    expected.update(operation="resume_plane", plane_id=resume_plane)
            _purpose_row(service, row, expected)
            rows.append(row)
        if item.get("attention") is not None:
            if (
                item["attention"] != "separate_run_resume_required"
                or decision["decision"] != "continue"
                or resume_plane is None
            ):
                raise IntakeError("invalid_maintenance_attention")
            row = mapping(item["resume"])
            sent = mapping(row["envelope"])
            receipt = validate_receipt(row["receipt"], sent)
            if (
                sent["operation"] != "resume_plane"
                or sent["plane_id"] != resume_plane
                or receipt["status"] != "applied"
                or receipt.get("action") != "resumed"
            ):
                raise IntakeError("invalid_maintenance_attention")
        if item["recovery_proof"] is not None:
            if mapping(item["recover"])["envelope"] is None:
                raise IntakeError("invalid_holder_recovery")
            read_ref(service, item["recovery_proof"])
    seen = set()
    for row in rows:
        if row["envelope"] is None:
            continue
        identifier = mapping(row["envelope"])["request_id"]
        if identifier in seen:
            raise IntakeError("lifecycle_request_id_conflict")
        seen.add(identifier)
    return intent, record


def _row(envelope: dict[str, object] | None) -> dict[str, object]:
    return {
        "envelope": envelope,
        "reservation": "unsent",
        "receipt": None,
        "observations": [],
    }


def _save(
    service: Service,
    intent: dict[str, object],
    before: dict[str, object],
    record: dict[str, object],
) -> None:
    service.intake.save_lifecycle(intent, before, record)


def _observe_row(service: Service, row: dict[str, object], value: object) -> None:
    observed = validate_call(
        value, str(service.config["os_home"]), mapping(row["envelope"])
    )
    history = cast(list[object], row["observations"])
    facts = {k: v for k, v in observed.items() if k != "observed_at"}
    if (
        not history
        or {k: v for k, v in mapping(history[-1]).items() if k != "observed_at"}
        != facts
    ):
        history.append(observed)
    row["reservation"] = "observed"
    if observed["core_receipt"] is not None:
        receipt = validate_receipt(observed["core_receipt"], mapping(row["envelope"]))
        previous = row["receipt"]
        if (
            previous is not None
            and mapping(previous)["status"] != "queued"
            and previous != receipt
        ):
            raise IntakeError("receipt_conflict")
        row["receipt"] = receipt


def _joined(service: Service, row: dict[str, object]) -> bool:
    sent = mapping(row["envelope"])
    for value in row["observations"]:
        observed = validate_call(value, str(service.config["os_home"]), sent)
        if observed["source"] == "os_owned_call" and observed["holder_joined"] is True:
            return True
    return False


def _send(
    service: Service,
    intent: dict[str, object],
    record: dict[str, object],
    row: dict[str, object],
    client: Client,
    deadline: float,
    *,
    replay_create: bool = False,
) -> None:
    _validate_lifecycle(service, intent, record)
    attached = [mapping(record[label]) for label in ("create", "start")]
    attached.extend(
        mapping(mapping(value)[label])
        for value in mapping(record["stops"]).values()
        for label in ("pause", "cancel")
    )
    for value in mapping(record["maintenance"]).values():
        item = mapping(value)
        attached.extend(
            [mapping(item["call"])]
            if item.get("scope") == "parent"
            else [mapping(item[label]) for label in ("pause", "recover", "resume")]
        )
    if not any(value is row for value in attached):
        raise IntakeError("detached_lifecycle_call")
    sent = mapping(row["envelope"])
    if replay_create and row is not mapping(record["create"]):
        raise IntakeError("invalid_create_replay")
    if row["reservation"] == "unsent" and sent["expected_state_version"] not in {
        None,
        0,
    }:
        control = _core()
        home = Path(str(intent["core_state_home"]))
        view = (
            control.inspect_instance(str(sent["instance_id"]), state_home=home)
            if sent["run_id"] is None
            else control.inspect_run(
                str(sent["instance_id"]), str(sent["run_id"]), state_home=home
            )
        )
        if view.get("state_version") != sent["expected_state_version"]:
            raise IntakeError("stale_state_version")
        child_recovery = any(
            mapping(value).get("scope") != "parent"
            and mapping(mapping(value)["recover"]) is row
            for value in mapping(record["maintenance"]).values()
        )
        if child_recovery:
            from .lad_service_disposition import recovery_ready
            from .subworkflow_v2 import closed

            if not recovery_ready(view) or not closed(view, attention=True):
                raise IntakeError("run_wide_pause_required")
    validate_capabilities(
        client.capabilities(deadline=deadline), str(service.config["os_home"])
    )
    request_body(str(service.config["os_home"]), sent)
    if (
        not replay_create
        and row is mapping(record["create"])
        and row["reservation"] != "unsent"
        and record["instance_id"] is not None
        and row["receipt"] is not None
        and validate_receipt(row["receipt"], sent)["status"] == "applied"
        and mapping(row["receipt"])["instance_id"] == record["instance_id"]
        and _joined(service, row)
    ):
        return
    before = copy.deepcopy(record)
    if row["reservation"] == "unsent" or replay_create:
        row["reservation"] = "unknown"
        _save(service, intent, before, record)
        before = copy.deepcopy(record)
        _validate_lifecycle(service, intent, record)
        response = client.submit(sent, deadline=deadline)
    else:
        response = client.observe(sent, deadline=deadline)
    _observe_row(service, row, response)
    if record != before:
        _save(service, intent, before, record)


def _handshake(
    service: Service,
    intent: dict[str, object],
    record: dict[str, object],
    client: Client,
    deadline: float,
) -> None:
    receipt = mapping(mapping(record["create"])["receipt"])
    if receipt.get("status") != "applied":
        raise IntakeError("parent_create_unresolved")
    instance = identity(receipt.get("instance_id"))
    os_view = client.read(instance, deadline=deadline)
    local = _core().inspect_instance(
        instance, state_home=Path(str(intent["core_state_home"]))
    )
    expected = mapping(mapping(intent["create"])["payload"])
    for view in (os_view, local):
        if (
            view.get("instance_id") != instance
            or any(
                view.get(name) != expected[name]
                for name in ("workflow_file", "project_root", *SETTINGS)
            )
            or type(view.get("state_version")) is not int
            or view["state_version"] < 1
        ):
            raise IntakeError("parent_home_handshake_mismatch")
    if local["state_version"] != os_view["state_version"]:
        raise IntakeError("parent_home_handshake_mismatch")
    before = copy.deepcopy(record)
    record["instance_id"] = instance
    if record["handshake"] is None:
        record["handshake"] = {
            "state_version": local["state_version"],
            "instance_id": instance,
            "settings_sha256": digest(intent["settings"]),
        }
    if mapping(record["start"])["envelope"] is None:
        record["start"] = _row(
            _env(
                "start_run",
                _operation_id(service.service_id, "parent-start"),
                instance=instance,
                expected=local["state_version"],
                payload={"edit_mode": "locked"},
            )
        )
    _save(service, intent, before, record)


def initialize(
    home: Path,
    target: Path,
    profile_file: Path,
    definition_file: Path,
    core_state_home: Path,
    *,
    client: Client | None = None,
) -> dict[str, object]:
    """Register one parent and verify the local/OS identity before exposing start."""
    home, target = _path(str(home)), _path(str(target), directory=True)
    core_home = _path(str(core_state_home), directory=True)
    profile_path = _path(str(profile_file))
    profile_ref = {
        "path": str(profile_path),
        "sha256": hashlib.sha256(_read_exact(profile_path, limit=65536)).hexdigest(),
    }
    profile = load_profile(profile_ref)
    definition = _definition(strict_json(_read_exact(_path(str(definition_file)))))
    settings = _settings(target)
    for root in (
        target,
        home,
        home / "workflow",
        home / "project",
        home / "calls",
        *catalog.known_roots(),
    ):
        if _overlap(core_home, root):
            raise IntakeError("core_home_overlap")
    if any(_overlap(target, root) for root in (home, *catalog.known_roots())):
        raise IntakeError("target_overlap")
    _core()  # Required public seam exists before target ownership or local writes.
    connection = client or WorkflowOSClient(profile_ref, str(profile["os_home"]))
    validate_capabilities(
        connection.capabilities(deadline=time.monotonic() + 2), str(profile["os_home"])
    )
    if home.exists():
        service = Service(home)
        intent, record = _load_lifecycle(service)
        if (
            intent["core_state_home"] != str(core_home)
            or service.config["target_root"] != str(target)
            or service.config["os_profile"] != profile_ref
            or service.config["worker_definition"] != definition
        ):
            raise IntakeError("initialization_conflict")
    else:
        config = {
            "format": "millrace-plus-lad-service/v1",
            "service_id": uuid.uuid4().hex,
            "service_home": str(home),
            "target_root": str(target),
            "target_identity": _filesystem_identity(target),
            "os_home": profile["os_home"],
            "os_profile": profile_ref,
            "worker_definition": definition,
            "settings_sha256": digest(settings),
        }
        _capacity(config, settings)
        # Pin source definition before taking ownership or preparing the parent.
        from .subworkflow_v2 import _definition as resolve_definition

        resolve_definition({"definition": definition})
        home.mkdir(mode=0o700)
        materialize_exact(home / "service.json", canonical(config) + b"\n")
        service = Service(home)
        claim_target(config, protected_roots=(home, core_home, *catalog.known_roots()))
        service.intake.initialize()
        create = _env(
            "create_instance",
            _operation_id(service.service_id, "parent-create"),
            expected=0,
            payload={
                "workflow_file": str(home / "workflow" / "workflow.json"),
                "project_root": str(home / "project"),
                **settings,
            },
        )
        request_body(str(profile["os_home"]), create)
        intent = {
            "format": INIT,
            "core_state_home": str(core_home),
            "service_sha256": hashlib.sha256(
                _read_exact(home / "service.json")
            ).hexdigest(),
            "settings": settings,
            "create": create,
        }
        record = {
            "format": LIFECYCLE,
            "create": _row(create),
            "start": _row(None),
            "instance_id": None,
            "run_id": None,
            "handshake": None,
            "stops": {},
            "maintenance": {},
        }
        service.intake.initialize_lifecycle(intent, record)
        catalog.init_template("bundled:lad-service", home / "workflow")
        (home / "project" / "service").mkdir(parents=True)
        for name in ("tasks", "calls", "evidence"):
            (home / name).mkdir()
    with target_lock(service.config, protected_roots=(home, core_home)):
        if not (home / "workflow" / "workflow.json").exists():
            raise IntakeError("partial_initialization_hold")
        create_row = mapping(record["create"])
        create_deadline = time.monotonic() + 2
        if create_row["receipt"] is None or not _joined(service, create_row):
            try:
                _send(
                    service,
                    intent,
                    record,
                    create_row,
                    connection,
                    create_deadline,
                )
            except SubworkflowError as exc:
                if (
                    exc.code not in {"os_http_404", "os_unavailable"}
                    or create_row["reservation"] == "unsent"
                    or (
                        create_row["receipt"] is not None
                        and mapping(create_row["receipt"])["status"] != "queued"
                    )
                ):
                    raise
                # OS deduplicates exact (resolved home, request ID) and digest.
                _send(
                    service,
                    intent,
                    record,
                    create_row,
                    connection,
                    create_deadline,
                    replay_create=True,
                )
        while not _joined(service, create_row):
            history = cast(list[object], create_row["observations"])
            if not history:
                break
            latest = validate_call(
                history[-1],
                str(service.config["os_home"]),
                mapping(create_row["envelope"]),
            )
            remaining = create_deadline - time.monotonic()
            if (
                latest["source"] != "os_owned_call"
                or latest["holder_joined"] is not False
                or latest["phase"] == "call_failed"
                or remaining <= 0
            ):
                break
            time.sleep(min(0.05, remaining))
            if time.monotonic() >= create_deadline:
                break
            _send(service, intent, record, create_row, connection, create_deadline)
        if not _joined(service, create_row):
            raise IntakeError("parent_create_holder_unjoined")
        _handshake(service, intent, record, connection, time.monotonic() + 2)
        return {
            "service_id": service.service_id,
            "instance_id": record["instance_id"],
            "start_request": mapping(record["start"])["envelope"],
            "activation_ready": True,
        }


class LocalClient:
    """Existing OS mutations/join observations plus verified complete local reads."""

    def __init__(self, runtime: Runtime, connection: Client) -> None:
        self.runtime, self.connection = runtime, connection

    def capabilities(self, *, deadline: float) -> dict[str, object]:
        return self.connection.capabilities(deadline=deadline)

    def submit(
        self, envelope: dict[str, object], *, deadline: float
    ) -> dict[str, object]:
        self.runtime.verify()
        return self.connection.submit(envelope, deadline=deadline)

    def observe(
        self, envelope: dict[str, object], *, deadline: float
    ) -> dict[str, object]:
        self.runtime.verify()
        return self.connection.observe(envelope, deadline=deadline)

    def read(
        self, instance: str, run: str | None = None, *, deadline: float
    ) -> dict[str, object]:
        self.runtime.verify()
        control = _core()
        home = Path(str(self.runtime.intent["core_state_home"]))
        return (
            control.inspect_instance(instance, state_home=home)
            if run is None
            else control.inspect_run(instance, run, state_home=home)
        )


class Runtime:
    """One finite operation; the ordinary Core graph owns repetition/routing."""

    def __init__(self, service: Service, *, client: Client | None = None) -> None:
        self.service = service
        self.intent, self.record = _load_lifecycle(service)
        self.connection = client or WorkflowOSClient(
            service.config["os_profile"], str(service.config["os_home"])
        )
        self.client = LocalClient(self, self.connection)
        self.verify()

    def verify(self) -> None:
        intent, record = _load_lifecycle(self.service)
        if intent != self.intent:
            raise IntakeError("lifecycle_authority_changed")
        load_profile(
            self.service.config["os_profile"], home=str(self.service.config["os_home"])
        )
        _check_roots(
            self.service.config,
            (
                self.service.home,
                Path(str(intent["core_state_home"])),
                *catalog.known_roots(),
            ),
        )
        if record["handshake"] is None or record["instance_id"] is None:
            raise IntakeError("parent_handshake_required")
        # Do not replace in-progress row objects: the next lifecycle CAS detects
        # a competing writer while immutable authority remains checked here.

    def parent(self) -> tuple[dict[str, object], dict[str, object]]:
        instance = identity(self.record["instance_id"])
        view = self.client.read(instance, deadline=time.monotonic() + 2)
        sent = mapping(mapping(self.record["start"])["envelope"])
        controls = view.get("controls")
        if not isinstance(controls, list):
            raise IntakeError("parent_start_unresolved")
        matches = [
            mapping(row)
            for row in controls
            if mapping(row).get("request_id") == sent["request_id"]
        ]
        if len(matches) != 1:
            raise IntakeError("parent_start_unresolved")
        receipt = validate_receipt(matches[0], sent)
        if receipt["status"] != "applied" or receipt.get("run_id") is None:
            raise IntakeError("parent_start_unresolved")
        run = self.client.read(
            instance, identity(receipt["run_id"]), deadline=time.monotonic() + 2
        )
        expected = mapping(mapping(self.intent["create"])["payload"])
        if (
            any(
                view.get(name) != expected[name]
                for name in ("workflow_file", "project_root", *SETTINGS)
            )
            or run.get("start_request_id") != sent["request_id"]
            or any(
                mapping(run.get("host_settings")).get(name) != expected[name]
                for name in SETTINGS
            )
            or run.get("instance_id") != instance
            or run.get("id") != receipt["run_id"]
            or (
                self.record["run_id"] is not None
                and self.record["run_id"] != receipt["run_id"]
            )
        ):
            raise IntakeError("parent_authority_changed")
        if self.record["run_id"] is None:
            before = copy.deepcopy(self.record)
            self.record["run_id"] = receipt["run_id"]
            mapping(self.record["start"])["receipt"] = receipt
            mapping(self.record["start"])["reservation"] = "observed"
            try:
                _save(self.service, self.intent, before, self.record)
            except IntakeError as exc:
                if exc.code != "lifecycle_conflict":
                    raise
                latest_intent, latest = _load_lifecycle(self.service)
                self.record = latest
                if (
                    latest_intent != self.intent
                    or latest["instance_id"] != instance
                    or latest["run_id"] != receipt["run_id"]
                    or latest["handshake"] != before["handshake"]
                    or mapping(latest["create"])["envelope"]
                    != mapping(before["create"])["envelope"]
                    or mapping(latest["create"])["receipt"]
                    != mapping(before["create"])["receipt"]
                    or mapping(latest["start"])["envelope"] != sent
                    or mapping(latest["start"])["receipt"] != receipt
                ):
                    raise IntakeError("parent_authority_changed") from exc
                # The competing binder established exactly this authority. Keep
                # its newer stop/maintenance facts; never overwrite their state.
        return view, run

    def source(self) -> dict[str, object]:
        self.parent()
        return _source_descriptor(self.service, self.intent, self.record)

    def active(self) -> dict[str, object] | None:
        rows = [
            row
            for row in self.service.intake.tasks()
            if row["status"] in {"claimed", "running", "held"}
        ]
        if len(rows) > 1:
            raise IntakeError("multiple_active_tasks")
        return rows[0] if rows else None

    def call(self, task: dict[str, object]) -> tuple[Journal, dict[str, object]]:
        claim = mapping(task["claim"])
        journal = Journal(
            Path(str(claim["call_home"])),
            str(claim["call_key"]),
            _local_source=self.source(),
        )
        record = journal.load()
        inputs = mapping(mapping(record["request"])["inputs"])
        target = mapping(inputs.get("lad_target"))
        expected_target = {
            "format": "millrace-lad-target/v1",
            "service_id": self.service.service_id,
            "task_id": task["task_id"],
            "request_sha256": task["request_sha256"],
            "target_root": self.service.config["target_root"],
            "target_identity": self.service.config["target_identity"],
            "claim_sha256": digest(claim),
            "parent_instance_id": self.record["instance_id"],
            "parent_run_id": self.record["run_id"],
            "worker_definition_sha256": mapping(claim["definition"])["sha256"],
        }
        task_root = self.service.home / "tasks" / str(task["task_id"])
        if (
            record["claim_sha256"] != digest(claim)
            or record["definition"] != claim["definition"]
            or target != expected_target
            or inputs["request_markdown"] != cast(bytes, task["request_bytes"]).decode()
            or _read_exact(task_root / "request.md", limit=65536)
            != task["request_bytes"]
            or _read_exact(task_root / "claim.json") != canonical(claim) + b"\n"
            or _read_exact(task_root / "request.json").decode() != record["request_raw"]
        ):
            raise IntakeError("shared_call_conflict")
        return journal, record

    def request(self, task: dict[str, object]) -> Path:
        self.service.materialize_task(str(task["task_id"]))
        claim = mapping(task["claim"])
        source = self.source()
        target = {
            "format": "millrace-lad-target/v1",
            "service_id": self.service.service_id,
            "task_id": task["task_id"],
            "request_sha256": task["request_sha256"],
            "target_root": self.service.config["target_root"],
            "target_identity": self.service.config["target_identity"],
            "claim_sha256": digest(claim),
            "parent_instance_id": source["parent_instance_id"],
            "parent_run_id": source["parent_run_id"],
            "worker_definition_sha256": mapping(claim["definition"])["sha256"],
        }
        request = {
            "format": "millrace-plus-subworkflow-request/v2",
            "call_key": claim["call_key"],
            "call_home": claim["call_home"],
            "parent": {
                "instance_id": source["parent_instance_id"],
                "run_id": source["parent_run_id"],
                "plane_id": "service",
            },
            "definition": claim["definition"],
            "output_ref": claim["output_ref"],
            "os_profile": self.service.config["os_profile"],
            "observe_seconds": 60,
            "cleanup_seconds": 30,
            "inputs": {
                "request_markdown": cast(bytes, task["request_bytes"]).decode(),
                "lad_target": target,
            },
        }
        path = self.service.home / "tasks" / str(task["task_id"]) / "request.json"
        materialize_exact(path, canonical(request) + b"\n")
        return path

    def _prepare_call(self, task: dict[str, object]) -> None:
        from .subworkflow_v2 import _prepare
        from .subworkflow_v2_records import parse_request

        path = self.request(task)
        raw = _read_exact(path)
        request = parse_request(raw, cwd=self.service.home / "project" / "service")
        claim = mapping(task["claim"])
        journal = Journal(
            Path(str(claim["call_home"])),
            str(claim["call_key"]),
            _local_source=self.source(),
        )
        with journal.lock(observer=True):
            _prepare(
                journal,
                request,
                raw,
                os_home=str(self.service.config["os_home"]),
                claim_sha256=digest(claim),
                client=self.client,
                deadline=time.monotonic() + 2,
            )

    def cancel(self, task_id: str, request_id: str) -> dict[str, object]:
        from . import subworkflow_v2

        task = self.service.intake.tasks(identity(task_id))[0]
        intent = {"task_id": task_id, "decision": "cancel-active", "evidence": None}
        with target_lock(self.service.config, protected_roots=(self.service.home,)):
            receipt = self.service.intake.decide(
                identity(request_id), intent, check=self._require_active
            )
        if task["status"] not in {"claimed", "running", "held"}:
            return receipt
        claim = mapping(task["claim"])
        path = Path(str(claim["call_home"])) / str(claim["call_key"]) / "call.json"
        if not path.exists():
            self._prepare_call(task)  # Preparation saves intent, never dispatches.
        record = subworkflow_v2.cancel(
            Path(str(claim["call_home"])),
            str(claim["call_key"]),
            request_id,
            client=self.client,
            _local_source=self.source(),
        )
        if record["run_id"] is None:
            from .lad_service_disposition import settle

            try:
                return settle(
                    self,
                    task,
                    decision="settle-no-run",
                    request_id=_operation_id(request_id, "cancel-disposition"),
                )
            except (IntakeError, SubworkflowError):
                pass
        latest = self.service.intake.tasks(task_id)[0]
        self.service.intake.transition(
            task_id,
            version=int(latest["version"]),
            claim=mapping(latest["claim"]),
            status="held",
        )
        return receipt | {"outcome": mapping(record["saved_result"])["outcome"]}

    def _recovery_observation(
        self, record: dict[str, object]
    ) -> tuple[str | None, bool]:
        """Service observation eligibility; never terminal closure authority."""
        from .lad_service_disposition import _recovery_for, task_sent_calls

        observation = record["observation"]
        if not isinstance(observation, dict) or observation.get("status") not in {
            "running",
            "succeeded",
        }:
            return None, False
        unjoined = [
            row
            for row in task_sent_calls(record, self.record)
            if not _joined(self.service, row)
        ]
        accepted_recovery = False
        pending_resume = False
        for row in unjoined:
            sent = mapping(row["envelope"])
            if _recovery_for(self, record, str(sent["request_id"])) is not None:
                accepted_recovery = True
                continue
            # A fresh same-run resume owns the currently observed live interval.
            # Its known false is retained; it never supplies closure or a join.
            owner = next(
                (
                    mapping(value)
                    for value in mapping(self.record["maintenance"]).values()
                    if mapping(value).get("task_id")
                    == mapping(
                        mapping(mapping(record["request"])["inputs"])["lad_target"]
                    )["task_id"]
                    and mapping(mapping(value)["resume"]) is row
                ),
                None,
            )
            history = cast(list[object], row["observations"])
            latest = mapping(history[-1]) if history else {}
            if (
                owner is None
                or owner["recovery_proof"] is None
                or not _joined(self.service, mapping(owner["recover"]))
                or row["receipt"] is None
                or mapping(row["receipt"])["status"] != "applied"
                or latest.get("source") != "os_owned_call"
                or latest.get("holder_joined") is not False
                or latest.get("phase") == "call_failed"
            ):
                return None, False
            pending_resume = True
        if not accepted_recovery:
            return None, False
        if observation["status"] == "succeeded":
            return (None, True) if pending_resume else ("CHILD_SUCCEEDED", False)
        return "CHILD_RUNNING", True

    def _observe_child_maintenance(self, task_id: str, deadline: float) -> None:
        """Refresh pending saved child calls before proof.

        Never dispatch unsent rows.
        """
        for value in mapping(self.record["maintenance"]).values():
            item = mapping(value)
            if item.get("task_id") != task_id:
                continue
            for label in ("pause", "recover", "resume"):
                row = mapping(item[label])
                if (
                    row["envelope"] is not None
                    and row["reservation"] != "unsent"
                    and not _joined(self.service, row)
                ):
                    if time.monotonic() >= deadline:
                        raise SubworkflowError("observation_window_expired")
                    _send(
                        self.service,
                        self.intent,
                        self.record,
                        row,
                        self.connection,
                        deadline,
                    )

    def _settlement_join_tail(self, task: dict[str, object], deadline: float) -> None:
        """Wait only for actual known-false maintenance retirement before settle."""
        from .lad_service_disposition import (
            _recovery_for,
            domain_evidence,
            known_controls,
            task_sent_calls,
        )
        from .subworkflow_persistent_records import closed, reduce_full_views

        with target_lock(self.service.config, protected_roots=(self.service.home,)):
            _, call = self.call(task)
            if call["run_id"] is None:
                raise IntakeError("child_run_required")
            instance = self.client.read(str(call["instance_id"]), deadline=deadline)
            run = self.client.read(
                str(call["instance_id"]), str(call["run_id"]), deadline=deadline
            )
            observed = reduce_full_views(
                instance,
                run,
                instance_id=str(call["instance_id"]),
                run_id=str(call["run_id"]),
                start_request_id=str(
                    mapping(mapping(call["envelopes"])["start"])["request_id"]
                ),
                paths=call["paths"],
                settings=call["settings"],
            )
            if run["status"] != "succeeded" or not closed(observed):
                raise IntakeError("child_not_closed")
            known_controls(self, call, instance, run)
            current = self.service.intake.tasks(str(task["task_id"]))[0]
            if (
                current["version"] != task["version"]
                or current["claim"] != task["claim"]
                or current["status"] not in {"claimed", "running", "held"}
            ):
                raise IntakeError("stale_task")
            if domain_evidence(self.service, call, run, _publish=False)["missing"]:
                raise IntakeError("lad_close_ack_required")
            maintenance = [
                mapping(mapping(item)[label])
                for item in mapping(self.record["maintenance"]).values()
                if mapping(item).get("task_id") == task["task_id"]
                for label in ("pause", "recover", "resume")
            ]
            pending = []

            def eligible(row):
                sent = mapping(row["envelope"])
                receipt = row["receipt"]
                history = cast(list[object], row["observations"])
                latest = mapping(history[-1]) if history else {}
                if receipt is not None:
                    validate_receipt(receipt, sent)
                if (
                    not any(row is saved for saved in maintenance)
                    or row["reservation"] == "unsent"
                    or receipt is None
                    or mapping(receipt)["status"] not in {"applied", "refused"}
                    or latest.get("source") != "os_owned_call"
                    or latest.get("holder_joined") is not False
                    or latest.get("phase") == "call_failed"
                ):
                    raise IntakeError("maintenance_holder_unjoined")
                validate_call(latest, str(self.service.config["os_home"]), sent)

            for row in task_sent_calls(call, self.record):
                if _joined(self.service, row):
                    continue
                if (
                    _recovery_for(
                        self, call, str(mapping(row["envelope"])["request_id"])
                    )
                    is not None
                ):
                    continue
                eligible(row)
                pending.append(row)
            while pending:
                if time.monotonic() >= deadline:
                    raise IntakeError("maintenance_join_window_expired")
                remaining = []
                for row in pending:
                    if time.monotonic() >= deadline:
                        raise IntakeError("maintenance_join_window_expired")
                    _send(
                        self.service,
                        self.intent,
                        self.record,
                        row,
                        self.connection,
                        deadline,
                    )
                    if not _joined(self.service, row):
                        eligible(row)
                        remaining.append(row)
                pending = remaining
                if pending:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise IntakeError("maintenance_join_window_expired")
                    time.sleep(min(5, left))

    def _maintenance(
        self,
        task: dict[str, object],
        request_id: str,
        operator: dict[str, object],
        *,
        decision: str = "continue",
        resume_plane: str | None = None,
    ) -> dict[str, object]:
        """One saved same-run maintenance sequence, with every OS call retained."""
        from . import subworkflow_v2

        journal, call = self.call(task)
        if call["run_id"] is None:
            raise IntakeError("child_run_required")
        instance, run_id = str(call["instance_id"]), str(call["run_id"])
        entries = mapping(self.record["maintenance"])
        previous = mapping(entries[request_id]) if request_id in entries else None
        if previous is not None and previous.get("attention") is not None:
            # Historical partial attention survives later decisions/run progress.
            # Its exact saved calls may be observed, never planned or POSTed again.
            for label in ("pause", "recover", "resume"):
                row = mapping(previous[label])
                if row["envelope"] is not None and row["reservation"] != "unsent":
                    _send(
                        self.service,
                        self.intent,
                        self.record,
                        row,
                        self.connection,
                        time.monotonic() + 2,
                    )
            raise IntakeError("separate_run_resume_required")
        _, parent = self.parent()
        if parent["status"] != "paused" or parent.get("quiescent") is not True:
            raise IntakeError("pause_parent_before_maintenance")
        run = self.client.read(instance, run_id, deadline=time.monotonic() + 2)
        if resume_plane is not None and resume_plane not in mapping(run["planes"]):
            raise IntakeError("invalid_resume_plane")
        if request_id not in entries:
            before = copy.deepcopy(self.record)
            entries[request_id] = {
                "task_id": task["task_id"],
                "operator": operator,
                "instance_id": instance,
                "run_id": run_id,
                "pause": _row(None),
                "recover": _row(None),
                "resume": _row(None),
                "recovery_before": None,
                "recovery_proof": None,
            }
            _save(self.service, self.intent, before, self.record)
        item = mapping(entries[request_id])
        if (
            item["task_id"] != task["task_id"]
            or item["operator"] != operator
            or item["instance_id"] != instance
            or item["run_id"] != run_id
        ):
            raise IntakeError("decision_conflict")
        from .lad_service_disposition import (
            _uncovered_sent_calls,
            recovery_ready,
            task_sent_calls,
        )
        from .subworkflow_persistent_records import (
            closed as summary_closed,
        )
        from .subworkflow_persistent_records import (
            reduce_full_views,
        )

        def stopped(
            *, require_gate: bool = False, guarded: dict[str, object] | None = None
        ) -> tuple[dict[str, object], dict[str, object]]:
            instance_view = self.client.read(instance, deadline=time.monotonic() + 2)
            run_view = (
                guarded
                if guarded is not None
                else self.client.read(instance, run_id, deadline=time.monotonic() + 2)
            )
            observed = reduce_full_views(
                instance_view,
                run_view,
                instance_id=instance,
                run_id=run_id,
                start_request_id=str(
                    mapping(mapping(call["envelopes"])["start"])["request_id"]
                ),
                paths=call["paths"],
                settings=call["settings"],
            )
            if not summary_closed(observed, attention=True):
                raise IntakeError("child_writers_unresolved")
            if require_gate and not recovery_ready(run_view):
                raise IntakeError("run_wide_pause_required")
            from .lad_service_disposition import known_controls

            known_controls(self, call, instance_view, run_view)
            return instance_view, run_view

        def finish_resume() -> None:
            if resume_plane is not None:
                row = mapping(item["resume"])
                if (
                    row["receipt"] is None
                    or mapping(row["receipt"])["status"] == "queued"
                ):
                    raise IntakeError("child_resume_pending")
                applied = validate_receipt(row["receipt"], mapping(row["envelope"]))
                if applied["status"] != "applied" or applied.get("action") != "resumed":
                    raise IntakeError("child_resume_refused")
                current = self.client.read(
                    instance, run_id, deadline=time.monotonic() + 2
                )
                if current.get("run_gate") is not None:
                    _, current = stopped()
                    if current["state_version"] != applied.get("state_version"):
                        raise IntakeError("stale_state_version")
                    selected = mapping(mapping(current["planes"])[resume_plane])
                    if selected.get("status") != "ready":
                        raise IntakeError("child_resume_pending")
                    current_task = self.service.intake.tasks(str(task["task_id"]))[0]
                    if current_task["status"] != "held":
                        self.service.intake.transition(
                            str(task["task_id"]),
                            version=int(current_task["version"]),
                            claim=mapping(current_task["claim"]),
                            status="held",
                        )
                    before = copy.deepcopy(self.record)
                    item["attention"] = "separate_run_resume_required"
                    _save(self.service, self.intent, before, self.record)
                    raise IntakeError("separate_run_resume_required")

        final = mapping(item["resume"])
        if final["envelope"] is not None and final["reservation"] != "unsent":
            # A pending final action is already selected. Replay observes its
            # saved calls and cannot plan a new pause/recovery at a later stage.
            for label in ("pause", "recover", "resume"):
                row = mapping(item[label])
                if row["envelope"] is not None and row["reservation"] != "unsent":
                    _send(
                        self.service,
                        self.intent,
                        self.record,
                        row,
                        self.connection,
                        time.monotonic() + 2,
                    )
            finish_resume()
            return {
                "task_id": task["task_id"],
                "request_id": request_id,
                "status": "maintenance-retained",
            }

        deadline = time.monotonic() + 30
        subworkflow_v2._refresh(journal, call, self.client, deadline, time.monotonic)
        call = journal.load()
        self._observe_child_maintenance(str(task["task_id"]), deadline)
        missing = bool(_uncovered_sent_calls(self, call))
        if run["status"] not in {"succeeded", "failed", "cancelled"} and (
            run["status"] != "paused" or (missing and not recovery_ready(run))
        ):
            before = copy.deepcopy(self.record)
            if mapping(item["pause"])["envelope"] is None:
                item["pause"] = _row(
                    _env(
                        "pause_run",
                        _operation_id(request_id, "child-pause"),
                        instance=instance,
                        run=run_id,
                    )
                )
                _save(self.service, self.intent, before, self.record)
            _send(
                self.service,
                self.intent,
                self.record,
                mapping(item["pause"]),
                self.connection,
                time.monotonic() + 2,
            )
        # Refresh only already-sent envelopes. These bounded network reads finish
        # before the current Core snapshot scope; no maintenance call is invented.
        subworkflow_v2._refresh(journal, call, self.client, deadline, time.monotonic)
        call = journal.load()
        self._observe_child_maintenance(str(task["task_id"]), deadline)
        lost_rows = _uncovered_sent_calls(self, call)
        for saved in lost_rows:
            if (
                saved["receipt"] is None
                or mapping(saved["receipt"])["status"] == "queued"
            ):
                raise IntakeError("lost_call_receipt_unresolved")
            history = cast(list[object], saved["observations"])
            if not history or mapping(history[-1])["holder_joined"] is not None:
                raise IntakeError("holder_known_unjoined")
        lost = [mapping(row["envelope"])["request_id"] for row in lost_rows]
        before_instance, before_run = stopped(require_gate=bool(lost))
        if not lost and any(
            not _joined(self.service, row) for row in task_sent_calls(call, self.record)
        ):
            # Earlier accepted recovery is historical call coverage, not a lease.
            # Revalidate the complete current child and known plane aftermath.
            with _core().exclusive_run_snapshot(
                instance,
                run_id,
                state_home=Path(str(self.intent["core_state_home"])),
                expected_state_version=int(before_run["state_version"]),
            ) as guarded:
                before_instance, before_run = stopped(guarded=guarded)
                if _uncovered_sent_calls(self, call):
                    raise IntakeError("holder_recovery_required")
        if lost and item["recovery_proof"] is not None:
            from .lad_service_disposition import _recovery_for

            if any(
                _recovery_for(self, call, str(identifier)) is None
                for identifier in lost
            ):
                raise IntakeError("invalid_holder_recovery")
        if lost and item["recovery_proof"] is None:
            from .lad_service_disposition import retain

            before = copy.deepcopy(self.record)
            if item["recovery_before"] is None:
                item["recovery_before"] = retain(
                    self.service,
                    canonical({"instance": before_instance, "run": before_run}) + b"\n",
                    "recovery-before",
                )
                _save(self.service, self.intent, before, self.record)
                before = copy.deepcopy(self.record)
            if mapping(item["recover"])["envelope"] is None:
                item["recover"] = _row(
                    _env(
                        "recover_run",
                        _operation_id(request_id, "child-recover"),
                        instance=instance,
                        run=run_id,
                        expected=before_run["state_version"],
                    )
                )
                _save(self.service, self.intent, before, self.record)
            row = mapping(item["recover"])
            _send(
                self.service,
                self.intent,
                self.record,
                row,
                self.connection,
                time.monotonic() + 2,
            )
            if row["receipt"] is None:
                raise IntakeError("fresh_recovery_unresolved")
            receipt = mapping(row["receipt"])
            if (
                receipt.get("status") != "applied"
                or receipt.get("action") not in {"recovered", "already_terminal"}
                or not _joined(self.service, row)
            ):
                raise IntakeError("fresh_recovery_unresolved")
            after_instance, after_run = stopped(require_gate=True)
            from .lad_service_disposition import retain

            proof = {
                "format": "millrace-plus-lad-holder-recovery/v1",
                "task_id": task["task_id"],
                "instance_id": instance,
                "run_id": run_id,
                "lost_request_ids": lost,
                "before": item["recovery_before"],
                "after": retain(
                    self.service,
                    canonical({"instance": after_instance, "run": after_run}) + b"\n",
                    "recovery-after",
                ),
                "pause_receipt": None
                if mapping(item["pause"])["receipt"] is None
                else retain(
                    self.service,
                    canonical(mapping(item["pause"])["receipt"]) + b"\n",
                    "pause-receipt",
                ),
                "recover_envelope": retain(
                    self.service, canonical(row["envelope"]) + b"\n", "recover-envelope"
                ),
                "recover_receipt": retain(
                    self.service, canonical(receipt) + b"\n", "recover-receipt"
                ),
                "joined": retain(
                    self.service,
                    canonical(row["observations"]) + b"\n",
                    "recover-joined",
                ),
            }
            # Fresh guard validates current facts through immutable proof publication.
            with _core().exclusive_run_snapshot(
                instance,
                run_id,
                state_home=Path(str(self.intent["core_state_home"])),
                expected_state_version=int(after_run["state_version"]),
            ) as guarded:
                local = _core().inspect_instance(
                    instance, state_home=Path(str(self.intent["core_state_home"]))
                )
                observed = reduce_full_views(
                    local,
                    guarded,
                    instance_id=instance,
                    run_id=run_id,
                    start_request_id=str(
                        mapping(mapping(call["envelopes"])["start"])["request_id"]
                    ),
                    paths=call["paths"],
                    settings=call["settings"],
                )
                if not summary_closed(observed, attention=True):
                    raise IntakeError("child_writers_unresolved")
                if not recovery_ready(guarded):
                    raise IntakeError("run_wide_pause_required")
                from .subworkflow_persistent_records import (
                    proof_manifest,
                    validate_proof,
                )

                instance_bytes, run_bytes = (
                    canonical(local) + b"\n",
                    canonical(guarded) + b"\n",
                )
                instance_ref = retain(self.service, instance_bytes, "recovery-instance")
                run_ref = retain(self.service, run_bytes, "recovery-run")
                view_proof = proof_manifest(
                    journal.local_source, observed, instance_ref, run_ref
                )
                validate_proof(
                    view_proof,
                    local_source=journal.local_source,
                    observation=observed,
                    instance_view_bytes=instance_bytes,
                    run_view_bytes=run_bytes,
                )
                proof["physical_evidence"] = {
                    "instance": instance_ref,
                    "run": run_ref,
                    "observation": retain(
                        self.service,
                        canonical(observed) + b"\n",
                        "recovery-observation",
                    ),
                    "view_proof": retain(
                        self.service,
                        canonical(view_proof) + b"\n",
                        "recovery-view-proof",
                    ),
                    "journal": retain(
                        self.service, canonical(call) + b"\n", "recovery-journal"
                    ),
                }
                before = copy.deepcopy(self.record)
                item["recovery_proof"] = retain(
                    self.service, canonical(proof) + b"\n", "holder-recovery"
                )
                _save(self.service, self.intent, before, self.record)
        run = self.client.read(instance, run_id, deadline=time.monotonic() + 2)
        resume_row = mapping(item["resume"])
        if run["status"] == "paused" or resume_row["envelope"] is not None:
            # The worker's declared pending route remains authoritative; no redirect.
            from .lad_service_disposition import _load_worker

            state = _load_worker(call)
            if decision == "close-failed":
                from .lad_service_disposition import read_ref

                value = fields(
                    strict_json(read_ref(self.service, operator)),
                    {"format", "session_id", "decision", "reason"},
                )
                if (
                    value["format"] != "millrace-lad-operator-intent/v1"
                    or value["session_id"] != state["session_id"]
                    or value["decision"] != "terminal_failure"
                    or not isinstance(value["reason"], str)
                    or not value["reason"].strip()
                    or mapping(state["continuation"]).get("node") != "human_pause"
                    or mapping(mapping(run["planes"])["foreground"]).get("cursor")
                    != "human_pause"
                ):
                    raise IntakeError("close_failed_requires_human_pause_boundary")
                with _core().exclusive_run_snapshot(
                    instance,
                    run_id,
                    state_home=Path(str(self.intent["core_state_home"])),
                    expected_state_version=int(run["state_version"]),
                ) as guarded:
                    local = _core().inspect_instance(
                        instance, state_home=Path(str(self.intent["core_state_home"]))
                    )
                    observed = reduce_full_views(
                        local,
                        guarded,
                        instance_id=instance,
                        run_id=run_id,
                        start_request_id=str(
                            mapping(mapping(call["envelopes"])["start"])["request_id"]
                        ),
                        paths=call["paths"],
                        settings=call["settings"],
                    )
                    if not summary_closed(observed, attention=True):
                        raise IntakeError("child_writers_unresolved")
                    materialize_exact(
                        Path(str(mapping(call["paths"])["project"]))
                        / "foreground"
                        / "lad-state"
                        / "operator-intent.json",
                        read_ref(self.service, operator),
                    )
            elif mapping(state["continuation"]).get("cleanup_required") is not False:
                raise IntakeError("lad_cleanup_required")
            before = copy.deepcopy(self.record)
            if mapping(item["resume"])["envelope"] is None:
                if resume_plane is not None:
                    plane = mapping(mapping(run["planes"])[resume_plane])
                    if plane.get("status") != "paused":
                        raise IntakeError("plane_not_paused")
                sent = _env(
                    "redirect_plane"
                    if decision == "close-failed"
                    else "resume_plane"
                    if resume_plane is not None
                    else "resume_run",
                    _operation_id(request_id, "child-resume"),
                    instance=instance,
                    run=run_id,
                    expected=run["state_version"],
                    payload={
                        "node_id": "terminal_failure",
                        "reason": "explicit_lad_close_failed",
                    }
                    if decision == "close-failed"
                    else {},
                )
                if decision == "close-failed":
                    sent["plane_id"] = "foreground"
                elif resume_plane is not None:
                    sent["plane_id"] = resume_plane
                item["resume"] = _row(sent)
                _save(self.service, self.intent, before, self.record)
            _send(
                self.service,
                self.intent,
                self.record,
                mapping(item["resume"]),
                self.connection,
                time.monotonic() + 2,
            )
            finish_resume()
        return {
            "task_id": task["task_id"],
            "request_id": request_id,
            "status": "maintenance-retained",
        }

    def decide(
        self,
        task_id: str,
        decision: str,
        request_id: str,
        evidence_file: Path,
        *,
        resume_plane: str | None = None,
    ) -> dict[str, object]:
        from .lad_service_disposition import retain, settle

        if decision not in {
            "retry-observation",
            "continue",
            "close-failed",
            "acknowledge-failed",
            "acknowledge-cancelled",
        }:
            raise IntakeError("invalid_decision")
        if resume_plane is not None and (
            decision != "continue"
            or not isinstance(resume_plane, str)
            or resume_plane not in {"foreground", "learning"}
        ):
            raise IntakeError("invalid_resume_plane")
        task = self.service.intake.tasks(identity(task_id))[0]
        if resume_plane is not None:
            _, selected_call = self.call(task)
            if selected_call["run_id"] is None:
                raise IntakeError("child_run_required")
            selected_run = self.client.read(
                str(selected_call["instance_id"]),
                str(selected_call["run_id"]),
                deadline=time.monotonic() + 2,
            )
            if resume_plane not in mapping(selected_run["planes"]):
                raise IntakeError("invalid_resume_plane")
        raw = _read_exact(evidence_file)
        if decision.startswith("acknowledge-"):
            # Refresh only original saved calls before entering a fresh local guard.
            if task["status"] in {"claimed", "running", "held"}:
                claim = mapping(task["claim"])
                from .subworkflow_v2 import reconcile

                reconcile(
                    Path(str(claim["call_home"])),
                    str(claim["call_key"]),
                    client=self.client,
                    _local_source=self.source(),
                )
                self._observe_child_maintenance(task_id, time.monotonic() + 30)
            return settle(
                self,
                task,
                decision=decision,
                request_id=identity(request_id),
                operator_raw=raw,
            )
        operator = retain(self.service, raw, "operator")
        intent = {"task_id": task_id, "decision": decision, "evidence": operator}
        if resume_plane is not None:
            intent["evidence"] = {
                "operator_evidence": operator,
                "resume_plane": resume_plane,
            }
        with target_lock(self.service.config, protected_roots=(self.service.home,)):
            receipt = self.service.intake.decide(
                identity(request_id),
                intent,
                check=lambda row: self._require_active(row),
            )
        if decision in {"continue", "close-failed"}:
            journal, _ = self.call(task)
            with target_lock(self.service.config, protected_roots=(self.service.home,)):
                with journal.lock(observer=True):
                    self._maintenance(
                        task,
                        request_id,
                        operator,
                        decision=decision,
                        resume_plane=resume_plane,
                    )
            # A retained attention result predates the explicit same-run control.
            # Refresh through the shared observer before graph observation is
            # reactivated; never reinterpret the old paused summary as current.
            from .subworkflow_v2 import reconcile

            claim = mapping(task["claim"])
            reconcile(
                Path(str(claim["call_home"])),
                str(claim["call_key"]),
                client=self.client,
                _local_source=self.source(),
            )
            current = self.service.intake.tasks(task_id)[0]
            call = journal.load()
            if call["run_id"] is not None:
                run = self.client.read(
                    str(call["instance_id"]),
                    str(call["run_id"]),
                    deadline=time.monotonic() + 2,
                )
                if run["status"] in {"ready", "running", "waiting", "succeeded"}:
                    self.service.intake.transition(
                        task_id,
                        version=int(current["version"]),
                        claim=mapping(current["claim"]),
                        status="running",
                    )
        elif decision == "retry-observation":
            from .subworkflow_v2 import reconcile

            claim = mapping(task["claim"])
            reconcile(
                Path(str(claim["call_home"])),
                str(claim["call_key"]),
                client=self.client,
                _local_source=self.source(),
            )
        return receipt

    @staticmethod
    def _require_active(task: dict[str, object]) -> None:
        if task["status"] not in {"claimed", "running", "held"}:
            raise IntakeError("task_not_active")

    def parent_control(self, request_file: Path) -> dict[str, object]:
        """Record/forward one existing exact parent control, never create a run."""
        raw = _read_exact(request_file)
        sent = _parent_envelope(strict_json(raw))
        identifier = identity(sent["request_id"])
        expected = sent["expected_state_version"]
        request_body(str(self.service.config["os_home"]), sent)
        with target_lock(self.service.config, protected_roots=(self.service.home,)):
            instance, run = self.parent()
            if (
                sent["instance_id"] != self.record["instance_id"]
                or sent["run_id"] != self.record["run_id"]
            ):
                raise IntakeError("wrong_parent_control_target")
            entries = mapping(self.record["maintenance"])
            key = "parent:" + identifier
            if key in entries:
                item = fields(entries[key], {"scope", "task_id", "request_raw", "call"})
                if (
                    item["scope"] != "parent"
                    or item["task_id"] is not None
                    or item["request_raw"] != raw.decode()
                    or mapping(item["call"])["envelope"] != sent
                ):
                    raise IntakeError("parent_control_conflict")
            else:
                if any(
                    mapping(value).get("request_id") == identifier
                    for value in [*instance["controls"], *run["controls"]]
                ):
                    raise IntakeError("parent_control_id_already_used")
                reserved = [
                    mapping(self.record[label])["envelope"]
                    for label in ("create", "start")
                ]
                reserved.extend(
                    mapping(mapping(value)[label])["envelope"]
                    for value in mapping(self.record["stops"]).values()
                    for label in ("pause", "cancel")
                )
                for value in entries.values():
                    previous = mapping(value)
                    if previous.get("scope") == "parent":
                        reserved.append(mapping(previous["call"])["envelope"])
                    else:
                        reserved.extend(
                            mapping(previous[label])["envelope"]
                            for label in ("pause", "recover", "resume")
                        )
                if (
                    any(
                        value is not None and mapping(value)["request_id"] == identifier
                        for value in reserved
                    )
                    or identifier in entries
                ):
                    raise IntakeError("parent_control_id_already_used")
                if expected is not None and expected != run["state_version"]:
                    raise IntakeError("stale_state_version")
                before = copy.deepcopy(self.record)
                item = {
                    "scope": "parent",
                    "task_id": None,
                    "request_raw": raw.decode(),
                    "call": _row(sent),
                }
                entries[key] = item
                _save(self.service, self.intent, before, self.record)
        # A resumed parent may immediately claim or bind its intake waiter.
        # Its nodes need this same target lock; durable CAS below owns the send.
        row = mapping(item["call"])
        # _send uses POST only for unsent. Unknown/observed is GET-only,
        # preserving this original call's target/version and join obligation.
        _send(
            self.service,
            self.intent,
            self.record,
            row,
            self.connection,
            time.monotonic() + 2,
        )
        if sent["operation"] == "pause_run" and row["receipt"] is not None:
            if mapping(row["receipt"])["status"] in {"queued", "applied"}:
                wake(self.service)
        return {
            "request_id": identifier,
            "receipt": row["receipt"],
            "holder_joined": _joined(self.service, row),
            "status": "observed" if _joined(self.service, row) else "pending",
        }

    def stop(self, request_id: str) -> dict[str, object]:
        identity(request_id)
        deadline = time.monotonic() + 30
        self.parent()
        with target_lock(self.service.config, protected_roots=(self.service.home,)):
            stops = mapping(self.record["stops"])
            if request_id not in stops:
                active = self.active()
                before = copy.deepcopy(self.record)
                stops[request_id] = {
                    "intent": {
                        "parent_instance_id": self.record["instance_id"],
                        "parent_run_id": self.record["run_id"],
                        "task_id": None if active is None else active["task_id"],
                        "claim_sha256": None
                        if active is None
                        else digest(active["claim"]),
                        "task_version": None if active is None else active["version"],
                        "request_sha256": None
                        if active is None
                        else active["request_sha256"],
                    },
                    "phase": "pause-claims",
                    "pause": _row(
                        _env(
                            "pause_run",
                            _operation_id(request_id, "parent-pause"),
                            instance=self.record["instance_id"],
                            run=self.record["run_id"],
                        )
                    ),
                    "cancel": _row(
                        _env(
                            "cancel_run",
                            _operation_id(request_id, "parent-cancel"),
                            instance=self.record["instance_id"],
                            run=self.record["run_id"],
                        )
                    ),
                }
                _save(self.service, self.intent, before, self.record)
        stop = mapping(mapping(self.record["stops"])[request_id])
        saved = fields(
            stop["intent"],
            {
                "parent_instance_id",
                "parent_run_id",
                "task_id",
                "claim_sha256",
                "task_version",
                "request_sha256",
            },
        )
        if (
            saved["parent_instance_id"] != self.record["instance_id"]
            or saved["parent_run_id"] != self.record["run_id"]
        ):
            raise IntakeError("stop_authority_changed")
        _send(
            self.service,
            self.intent,
            self.record,
            mapping(stop["pause"]),
            self.connection,
            deadline,
        )
        if mapping(stop["pause"])["receipt"] is not None:
            if mapping(mapping(stop["pause"])["receipt"])["status"] in {
                "queued",
                "applied",
            }:
                wake(self.service)
        _, parent = self.parent()
        if (
            parent["status"] not in {"paused", "failed", "cancelled", "succeeded"}
            or parent.get("quiescent") is not True
        ):
            return {
                "request_id": request_id,
                "status": "pending",
                "reason": "parent_pause_pending",
            }
        task_id = saved["task_id"]
        active = self.active()
        if active is not None:
            if (
                active["task_id"] != task_id
                or digest(active["claim"]) != saved["claim_sha256"]
                or active["request_sha256"] != saved["request_sha256"]
            ):
                raise IntakeError("stop_active_claim_changed")
            self.cancel(str(task_id), _operation_id(request_id, "stop-child-cancel"))
            if self.active() is not None:
                return {
                    "request_id": request_id,
                    "status": "pending",
                    "reason": "child_disposition_required",
                }
        if task_id is not None:
            from .lad_service_disposition import verify_terminal_current

            verify_terminal_current(self, self.service.intake.tasks(str(task_id))[0])
        instance, parent = self.parent()
        rows = [mapping(self.record[label]) for label in ("create", "start")]
        for value in mapping(self.record["stops"]).values():
            rows.extend(mapping(mapping(value)[label]) for label in ("pause", "cancel"))
        for value in mapping(self.record["maintenance"]).values():
            item = mapping(value)
            if item.get("scope") == "parent":
                rows.append(mapping(item["call"]))
        known = {
            mapping(row["envelope"])["request_id"]: mapping(row["envelope"])
            for row in rows
            if row["envelope"] is not None
        }
        for value in [*instance.get("controls", []), *parent.get("controls", [])]:
            receipt = mapping(value)
            sent = known.get(receipt.get("request_id"))
            if sent is None:
                raise IntakeError("unknown_parent_control")
            validate_receipt(receipt, sent)
        if time.monotonic() >= deadline:
            return {
                "request_id": request_id,
                "status": "pending",
                "reason": "cleanup_window_expired",
            }
        _send(
            self.service,
            self.intent,
            self.record,
            mapping(stop["cancel"]),
            self.connection,
            deadline,
        )
        for row in rows:
            if time.monotonic() >= deadline:
                return {
                    "request_id": request_id,
                    "status": "pending",
                    "reason": "cleanup_window_expired",
                }
            if row["envelope"] is not None and row["reservation"] != "unsent":
                _send(
                    self.service,
                    self.intent,
                    self.record,
                    row,
                    self.connection,
                    deadline,
                )
        _, parent = self.parent()
        from .subworkflow_v2 import closed

        if (
            not closed(parent)
            or not _joined(self.service, mapping(stop["pause"]))
            or not _joined(self.service, mapping(stop["cancel"]))
            or any(
                not _joined(self.service, row)
                for row in rows
                if row["reservation"] != "unsent"
            )
        ):
            return {
                "request_id": request_id,
                "status": "pending",
                "reason": "parent_closure_unresolved",
            }
        before = copy.deepcopy(self.record)
        stop["phase"] = "complete"
        _save(self.service, self.intent, before, self.record)
        return {"request_id": request_id, "status": "stopped"}

    def node(self, name: str) -> dict[str, object]:
        try:
            self.parent()
            active = self.active()
            if name == "reconcile":
                if active:
                    if active["status"] == "held":
                        return self.result("HOLD", "active_task_held")
                    claim = mapping(active["claim"])
                    if (
                        Path(str(claim["call_home"]))
                        / str(claim["call_key"])
                        / "call.json"
                    ).exists():
                        self.call(active)
                    return self.result("ACTIVE")
                return self.result("READY")
            if name == "claim":
                _, parent_run = self.parent()
                if parent_run["status"] != "running" or any(
                    mapping(row).get("phase") != "complete"
                    for row in mapping(self.record["stops"]).values()
                ):
                    raise IntakeError("claims_paused")

                # Use this exact active claim invocation's original Core budget,
                # never a fresh timeout per attempt or a guessed owner deadline.
                claim_deadline = None
                active_id = mapping(mapping(parent_run["planes"])["service"]).get(
                    "active_invocation"
                )
                matching = [
                    mapping(row)
                    for row in parent_run.get("invocations", [])
                    if mapping(row).get("id") == active_id
                ]
                if active_id is not None and len(matching) == 1:
                    invocation = matching[0]
                    deadline = invocation.get("deadline")
                    if (
                        invocation.get("run_id") == parent_run["id"]
                        and invocation.get("plane_id") == "service"
                        and invocation.get("node") == "claim"
                        and type(deadline) in {int, float}
                        and math.isfinite(deadline)
                    ):
                        claim_deadline = time.monotonic() + max(
                            0, deadline - time.time()
                        )

                def claim_guard():
                    # Service.claim_next holds the existing target exclusion
                    # through these fresh reads and the FIFO claim transaction.
                    # Never merge cached rows over a newer durable stop intent.
                    self.verify()
                    _, current_parent = self.parent()
                    _, current = _load_lifecycle(self.service)
                    if current_parent["status"] != "running" or any(
                        mapping(row)["phase"] != "complete"
                        for row in mapping(current["stops"]).values()
                    ):
                        raise IntakeError("claims_paused")

                if claim_deadline is None:
                    claim = self.service.claim_next(writer_guard=claim_guard)
                else:
                    claim = self.service.claim_next(
                        writer_guard=claim_guard,
                        _deadline=claim_deadline,
                        _clock=time.monotonic,
                        _sleep=time.sleep,
                    )
                return self.result("EMPTY" if claim is None else "CLAIMED")
            if name == "wait_intake":
                return self.result(wait_intake(self.service))
            if active is None:
                raise IntakeError("active_task_missing")
            if name == "advance":
                path = self.request(active)
                raw = _read_exact(path)
                advance_deadline = time.monotonic() + int(
                    mapping(strict_json(raw))["observe_seconds"]
                )
                result = _advance_bytes(
                    raw,
                    expected_home=str(self.service.config["os_home"]),
                    claim_sha256=digest(active["claim"]),
                    cwd=self.service.home / "project" / "service",
                    client=self.client,
                    _local_source=self.source(),
                    _deadline=advance_deadline,
                    clock=time.monotonic,
                    sleep=time.sleep,
                )
                if time.monotonic() < advance_deadline:
                    self._observe_child_maintenance(
                        str(active["task_id"]), advance_deadline
                    )
                outcome = str(result["outcome"])
                if outcome == "CHILD_OBSERVATION_LOST":
                    from .subworkflow_v2 import reconcile

                    claim = mapping(active["claim"])
                    while time.monotonic() < advance_deadline:
                        saved = reconcile(
                            Path(str(claim["call_home"])),
                            str(claim["call_key"]),
                            client=self.client,
                            _local_source=self.source(),
                            _deadline=advance_deadline,
                            clock=time.monotonic,
                        )
                        self._observe_child_maintenance(
                            str(active["task_id"]), advance_deadline
                        )
                        result = mapping(saved["saved_result"])
                        outcome = str(result["outcome"])
                        if outcome != "CHILD_OBSERVATION_LOST":
                            break
                        candidate, waitable = self._recovery_observation(saved)
                        if candidate == "CHILD_SUCCEEDED":
                            # Only a settlement candidate; generic journal/result
                            # and all raw nulls remain unchanged. Settle still
                            # owns every current physical/domain check and CAS.
                            outcome = candidate
                            result = result | {
                                "outcome": candidate,
                                "summary": "accepted recovery settlement candidate",
                            }
                            break
                        remaining = advance_deadline - time.monotonic()
                        if not waitable or remaining <= 0:
                            break
                        time.sleep(min(5, remaining))
                        if time.monotonic() >= advance_deadline:
                            if candidate == "CHILD_RUNNING":
                                outcome = candidate
                                result = result | {
                                    "outcome": candidate,
                                    "summary": "same-child recovery observation window",
                                }
                            break
                status = (
                    "running"
                    if outcome == "CHILD_RUNNING"
                    else "held"
                    if outcome != "CHILD_SUCCEEDED"
                    else active["status"]
                )
                self.service.intake.transition(
                    str(active["task_id"]),
                    version=int(active["version"]),
                    claim=mapping(active["claim"]),
                    status=str(status),
                )
                return result
            if name == "settle":
                from .lad_service_disposition import settle

                self._settlement_join_tail(active, time.monotonic() + 30)
                settle(
                    self,
                    active,
                    decision="settle",
                    request_id=_operation_id(str(active["task_id"]), "settle"),
                )
                return self.result("SETTLED")
            if name == "hold":
                self.service.intake.transition(
                    str(active["task_id"]),
                    version=int(active["version"]),
                    claim=mapping(active["claim"]),
                    status="held",
                )
                return self.result("WAIT_OPERATOR", "operator_decision_required")
            raise IntakeError("unknown_service_node")
        except (IntakeError, SubworkflowError, catalog.CatalogError, OSError) as exc:
            return self.result("HOLD", getattr(exc, "code", "io_error"))

    @staticmethod
    def result(outcome: str, summary: str | None = None) -> dict[str, object]:
        return {"outcome": outcome, "summary": summary or outcome, "refs": []}


def preflight_submit(service: Service, payload: bytes) -> None:
    """Core-free acceptance sizing from saved immutable settings, when initialized."""
    try:
        intent, record = _load_lifecycle(service)
    except IntakeError as exc:
        # Legacy foundation fixtures remain intake-only; they cannot activate runtime.
        if exc.code == "lifecycle_missing":
            return
        raise
    _capacity(
        service.config,
        mapping(intent["settings"]),
        payload,
        parent=str(record["instance_id"] or "0" * 32),
        run=str(record["run_id"] or "0" * 32),
    )


def wake(service: Service) -> None:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.sendto(b"wake", str(service.home / "intake.sock"))
    except OSError:
        pass  # Durable commit is authoritative; socket data is only a hint.


def _wait_paused(service: Service) -> bool:
    """A bind-time wake check, never closure or authority to send a control."""
    _, record = _load_lifecycle(service)
    if any(
        mapping(row)["phase"] != "complete" for row in mapping(record["stops"]).values()
    ):
        return True
    if record["run_id"] is None:
        return False
    _, run = Runtime(service).parent()
    if run["status"] != "running" or run.get("run_gate") is not None:
        return True
    controls = {mapping(row)["request_id"]: row for row in run["controls"]}
    for value in mapping(record["maintenance"]).values():
        item = mapping(value)
        if item.get("scope") != "parent":
            continue
        row = mapping(item["call"])
        sent = mapping(row["envelope"])
        current = controls.get(sent["request_id"])
        if sent["operation"] == "pause_run" and current is not None:
            if validate_receipt(current, sent)["status"] == "queued":
                return True
    return False


def wait_intake(service: Service, *, seconds: float = 900) -> str:
    path = service.home / "intake.sock"
    with target_lock(service.config, protected_roots=(service.home,)):
        if os.path.lexists(path):
            # Never unlink another live waiter or an unowned filesystem object.
            raise IntakeError("intake_socket_exists")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            connection.bind(str(path))
            info = path.lstat()
        except BaseException:
            connection.close()
            raise
    try:
        if any(row["status"] == "queued" for row in service.intake.tasks()):
            return "WAKE"
        if _wait_paused(service):
            return "WAKE"
        connection.settimeout(seconds)
        try:
            connection.recv(64)
            return "WAKE"
        except TimeoutError:
            return "IDLE"
    finally:
        connection.close()
        if (
            path.exists()
            and path.lstat().st_ino == info.st_ino
            and path.lstat().st_dev == info.st_dev
        ):
            path.unlink()
