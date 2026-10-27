"""Current guarded LAD closure proofs and atomic domain queue dispositions.

Saved summaries and proofs are historical evidence. A run-backed release always
publishes under a fresh public Core snapshot and the active-task compare-and-swap.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .lad_service import Service, _path, _read_exact, materialize_exact, target_lock
from .local_intake import IntakeError, canonical, fields, identity, strict_json
from .subworkflow_persistent_records import (
    closed,
    proof_manifest,
    reduce_full_views,
    validate_proof,
)
from .subworkflow_records import (
    SubworkflowError,
    digest,
    mapping,
)
from .subworkflow_v2 import _envelopes, _reservation
from .workflow_os_client import validate_call, validate_receipt

if TYPE_CHECKING:
    from .lad_service_runtime import Runtime


def retain(service: Service, raw: bytes, label: str) -> dict[str, object]:
    """Publish immutable owned bytes; failed transactions retain unaccepted evidence."""
    sha = hashlib.sha256(raw).hexdigest()
    root = service.home / "evidence"
    _path(str(root), directory=True)
    path = root / (label + "-" + sha + ".json")
    materialize_exact(path, raw)
    return {"path": str(path), "sha256": sha}


def read_ref(service: Service, value: object, *, limit: int | None = None) -> bytes:
    ref = fields(value, {"path", "sha256"})
    path = _path(ref["path"])
    if path.parent != service.home / "evidence":
        raise IntakeError("evidence_not_owned")
    # Full public view proofs deliberately have no wire-size cap. Other records
    # remain bounded by their own selected contracts before publication.
    raw = _read_exact(path, limit=limit if limit is not None else path.stat().st_size)
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise IntakeError("evidence_changed")
    return raw


def _load_worker(record: dict[str, object]):
    """Reuse the pinned worker's actual read-only ledger validator."""
    workflow = Path(str(mapping(record["paths"])["workflow"]))
    from . import catalog

    if (
        catalog.workflow_fingerprint(workflow)
        != mapping(record["definition"])["sha256"]
    ):
        raise IntakeError("definition_mismatch")
    scripts = workflow / "scripts"
    prior_path = list(sys.path)
    prior_bytecode = sys.dont_write_bytecode
    names = ("lad_state", "session_compat", "learning_seam", "boundaries")
    prior = {name: sys.modules.get(name) for name in names}
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(scripts))
        for name in names:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(
            "lad_state", scripts / "lad_state.py"
        )
        if spec is None or spec.loader is None:
            raise IntakeError("worker_validator_missing")
        module = importlib.util.module_from_spec(spec)
        sys.modules["lad_state"] = module
        spec.loader.exec_module(module)
        foreground = Path(str(mapping(record["paths"])["project"])) / "foreground"
        return module.load(foreground)
    finally:
        sys.path[:] = prior_path
        sys.dont_write_bytecode = prior_bytecode
        for name in names:
            if prior[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior[name]


def domain_evidence(
    service: Service,
    record: dict[str, object],
    run: dict[str, object],
    *,
    _publish: bool = True,
) -> dict[str, object]:
    """Small CLOSE/ACK adapter over the worker validator and complete Core seams."""
    refs: dict[str, object] = {}
    missing: list[str] = []
    try:
        state = _load_worker(record)
        project = Path(str(mapping(record["paths"])["project"]))
        foreground = project / "foreground"
        if _publish:
            refs["ledger"] = retain(
                service,
                _read_exact(foreground / "lad-state" / "session.json"),
                "ledger",
            )
        accepted = mapping(mapping(state["seam_seen"]).get("accepted_ack"))
        ack = fields(
            accepted["ack"],
            {
                "format",
                "session_id",
                "request_id",
                "request_sha256",
                "request_core",
                "kind",
                "lineage",
                "pending_route",
                "source_sha256",
                "guidance_sha256",
                "terminal",
                "learning",
            },
        )
        core = mapping(accepted["core"])
        if (
            state["terminal"] not in {"succeeded", "failed"}
            or accepted.get("route") != "FINALIZE"
            or ack.get("kind") != "CLOSE"
            or ack.get("terminal") != state["terminal"]
            or state["request"] is not None
            or state["pending"] is not None
            or mapping(state["continuation"]).get("node") != "finalize"
            or mapping(state["continuation"]).get("cleanup_required") is not False
            or ack.get("session_id") != state["session_id"]
            or core.get("run_id") != record["run_id"]
            or core.get("seam_id") != "learning_ack"
            or core.get("producer_plane_id") != "learning"
            or core.get("producer_node_id") != "emit_ack"
            or core.get("consumer_plane_id") != "foreground"
            or core.get("consumer_node_id") != "accept_learning_ack"
        ):
            raise IntakeError("close_ack_incomplete")
        ack_path = (
            project
            / "learning"
            / "lad-state"
            / "seams"
            / ("ack-" + str(ack["request_id"]) + ".json")
        )
        request_path = (
            foreground
            / "lad-state"
            / "seams"
            / ("request-" + str(ack["request_id"]) + ".json")
        )
        ack_raw, request_raw = _read_exact(ack_path), _read_exact(request_path)
        request = fields(
            strict_json(request_raw),
            {
                "format",
                "request_id",
                "session_id",
                "kind",
                "lineage",
                "occurrence_id",
                "pending_route",
                "terminal",
                "source_sha256",
                "guidance_sha256",
            },
        )
        if (
            ack["format"] != "millrace-lad-learning-ack/v1"
            or ack["learning"] is not None
            or request["format"] != "millrace-lad-learning-request/v1"
            or request["request_id"] != ack["request_id"]
            or any(
                request[name] != ack[name]
                for name in (
                    "session_id",
                    "kind",
                    "lineage",
                    "pending_route",
                    "source_sha256",
                    "guidance_sha256",
                    "terminal",
                )
            )
            or strict_json(ack_raw) != ack
            or hashlib.sha256(request_raw).hexdigest() != ack["request_sha256"]
            or mapping(core["result"]).get("summary")
            != "sha256=" + hashlib.sha256(ack_raw).hexdigest()
            or request.get("kind") != "CLOSE"
            or request.get("terminal") != state["terminal"]
            or request.get("session_id") != state["session_id"]
        ):
            raise IntakeError("close_ack_bytes_mismatch")
        if _publish:
            for key, raw in (("close", request_raw), ("ack", ack_raw)):
                refs[key] = retain(service, raw, key)
        occurrences = run.get("seam_occurrences")
        acknowledgements = run.get("seam_acknowledgements")
        selections = run.get("seam_selections")
        completions = run.get("completions")
        if not all(
            isinstance(rows, list)
            for rows in (occurrences, acknowledgements, selections, completions)
        ):
            raise IntakeError("close_ack_core_missing")
        for seam, envelope in (
            (
                "learning_request",
                fields(
                    ack["request_core"],
                    {
                        "run_id",
                        "sequence",
                        "producer_invocation_id",
                        "producing_revision",
                        "completion_id",
                    },
                ),
            ),
            ("learning_ack", core),
        ):
            sequence = envelope["sequence"]
            matching = [
                mapping(row)
                for row in occurrences
                if mapping(row).get("seam_id") == seam
                and mapping(row).get("sequence") == sequence
            ]
            consumed = [
                mapping(row)
                for row in acknowledgements
                if mapping(row).get("seam_id") == seam
                and mapping(row).get("sequence") == sequence
            ]
            produced = [
                mapping(row)
                for row in completions
                if mapping(row).get("invocation_id") == envelope.get("completion_id")
            ]
            selected = [
                mapping(row)
                for row in selections
                if mapping(row).get("seam_id") == seam
                and mapping(row).get("sequence") == sequence
            ]
            producers = [
                mapping(row)
                for row in run["invocations"]
                if mapping(row).get("id") == envelope.get("producer_invocation_id")
            ]
            if any(
                len(rows) != 1
                for rows in (matching, consumed, produced, selected, producers)
            ):
                raise IntakeError("close_ack_core_missing")
            body = fields(
                strict_json(str(matching[0]["body"]).encode()),
                {
                    "format",
                    "run_id",
                    "consumer_plane_id",
                    "consumer_node_id",
                    "seam_id",
                    "sequence",
                    "producer_plane_id",
                    "producer_node_id",
                    "producer_invocation_id",
                    "producing_revision",
                    "completion_id",
                    "producer_workspace",
                    "result",
                },
            )
            producer = producers[0]
            result = fields(body["result"], {"outcome", "summary", "refs"})
            expected_ref = (
                "lad-state/seams/"
                + ("request-" if seam == "learning_request" else "ack-")
                + str(ack["request_id"])
                + ".json"
            )
            if (
                body["format"] != "millrace-seam-input/v1"
                or body["run_id"] != record["run_id"]
                or type(sequence) is not int
                or sequence < 1
                or body["completion_id"] != body["producer_invocation_id"]
                or any(
                    matching[0].get(name) != body["producer_invocation_id"]
                    for name in (
                        "producer_invocation_id",
                        "completion_id",
                        "transition_id",
                    )
                )
                or any(
                    row.get("run_id") != record["run_id"]
                    for row in (
                        matching[0],
                        consumed[0],
                        selected[0],
                        producer,
                        produced[0],
                    )
                )
                or producer.get("node") != body["producer_node_id"]
                or producer.get("plane_id") != body["producer_plane_id"]
                or producer.get("revision_id") != body["producing_revision"]
                or any(
                    produced[0].get(name) != producer.get(name)
                    for name in ("node", "plane_id", "revision_id")
                )
                or body["producer_workspace"]
                != str(
                    project
                    / ("foreground" if seam == "learning_request" else "learning")
                )
                or body["consumer_plane_id"]
                != ("learning" if seam == "learning_request" else "foreground")
                or selected[0].get("consumer_plane_id") != body["consumer_plane_id"]
                or selected[0].get("consumer_node_id") != body["consumer_node_id"]
                or strict_json(str(selected[0]["body"]).encode()) != body
                or result["outcome"]
                != ("REQUEST" if seam == "learning_request" else "ACK")
                or result["refs"] != [expected_ref]
                or strict_json(str(produced[0]["stdout"]).encode()) != result
            ):
                raise IntakeError("close_ack_core_mismatch")
            if (
                body.get("seam_id") != seam
                or (seam == "learning_ack" and body != core)
                or body.get("producer_plane_id")
                != ("foreground" if seam == "learning_request" else "learning")
                or body.get("producer_node_id")
                != (
                    "emit_learning_request"
                    if seam == "learning_request"
                    else "emit_ack"
                )
                or body.get("consumer_node_id")
                != (
                    "receive_request"
                    if seam == "learning_request"
                    else "accept_learning_ack"
                )
                or mapping(body.get("result")).get("summary")
                != "sha256="
                + hashlib.sha256(
                    request_raw if seam == "learning_request" else ack_raw
                ).hexdigest()
            ):
                raise IntakeError("close_ack_core_mismatch")
            consumer = [
                mapping(row)
                for row in completions
                if mapping(row).get("invocation_id") == consumed[0].get("completion_id")
            ]
            consumers = [
                mapping(row)
                for row in run["invocations"]
                if mapping(row).get("id") == consumed[0].get("completion_id")
            ]
            if (
                len(consumer) != 1
                or len(consumers) != 1
                or consumers[0].get("node") != body["consumer_node_id"]
                or consumers[0].get("plane_id") != body["consumer_plane_id"]
                or consumers[0].get("run_id") != record["run_id"]
                or any(
                    consumer[0].get(name) != consumers[0].get(name)
                    for name in ("node", "plane_id", "revision_id", "run_id")
                )
                or consumer[0].get("outcome")
                != ("CLOSE" if seam == "learning_request" else "FINALIZE")
                or produced[0].get("outcome")
                != ("REQUEST" if seam == "learning_request" else "ACK")
            ):
                raise IntakeError("close_ack_core_mismatch")
            if any(
                body.get(key) != envelope.get(key)
                for key in (
                    "run_id",
                    "sequence",
                    "producer_invocation_id",
                    "completion_id",
                    "producing_revision",
                )
            ):
                raise IntakeError("close_ack_core_mismatch")
        finalized = [
            mapping(row)
            for row in run["invocations"]
            if mapping(row).get("node") == "finalize"
            and mapping(row).get("plane_id") == "foreground"
        ]
        final_completions = [
            mapping(row)
            for row in completions
            if any(
                mapping(row).get("invocation_id") == final.get("id")
                for final in finalized
            )
            and mapping(row).get("outcome") == str(state["terminal"]).upper()
        ]
        if len(final_completions) != 1 or len(
            {row.get("id") for row in finalized}
        ) != len(finalized):
            raise IntakeError("finalize_completion_missing")
        final = next(
            row
            for row in finalized
            if row["id"] == final_completions[0]["invocation_id"]
        )
        if final.get("run_id") != record["run_id"] or any(
            final_completions[0].get(name) != final.get(name)
            for name in ("node", "plane_id", "revision_id", "run_id")
        ):
            raise IntakeError("finalize_completion_missing")
        if run["status"] == "succeeded" and state["terminal"] != "succeeded":
            raise IntakeError("close_ack_status_mismatch")
    except (
        IntakeError,
        SubworkflowError,
        OSError,
        KeyError,
        ValueError,
        ImportError,
    ) as exc:
        missing.append(getattr(exc, "code", "close_ack_invalid"))
    except Exception as exc:
        # Worker validators use their own LadError; never substitute unvalidated state.
        if exc.__class__.__name__ != "LadError":
            raise
        missing.append(str(exc))
    return {"available": refs, "missing": missing}


def known_controls(
    runtime: Runtime,
    record: dict[str, object],
    instance: dict[str, object],
    run: dict[str, object] | None = None,
) -> None:
    """A public receipt cannot supply an absent original controller envelope."""
    known = {
        mapping(value)["request_id"]: mapping(value)
        for value in _envelopes(record).values()
        if value is not None
    }
    target = mapping(mapping(record["request"])["inputs"])["lad_target"]
    for value in mapping(runtime.record["maintenance"]).values():
        item = mapping(value)
        if item.get("task_id") != mapping(target)["task_id"]:
            continue
        for label in ("pause", "recover", "resume"):
            row = mapping(item[label])
            if row["envelope"] is not None:
                known[mapping(row["envelope"])["request_id"]] = mapping(row["envelope"])
    if not isinstance(instance.get("controls"), list) or (
        run is not None and not isinstance(run.get("controls"), list)
    ):
        raise IntakeError("invalid_control_observation")
    seen = set()
    for value in [*instance["controls"], *((run or {}).get("controls", []))]:
        receipt = mapping(value)
        envelope = known.get(receipt.get("request_id"))
        if envelope is None:
            raise IntakeError("unknown_child_control")
        if receipt.get("request_id") in seen or receipt.get("status") == "queued":
            raise IntakeError("unresolved_child_control")
        seen.add(receipt.get("request_id"))
        validate_receipt(receipt, envelope)


def recovery_ready(view: dict[str, object]) -> bool:
    """A full public pause gate, never a summary, prevents recovery dispatch."""
    if isinstance(view.get("status"), str) and view["status"] in {
        "succeeded",
        "failed",
        "cancelled",
    }:
        return True
    gate = view.get("run_gate")
    if view.get("status") != "paused" or not isinstance(gate, dict):
        return False
    if (
        set(gate) != {"reason", "request_id"}
        or not isinstance(gate["reason"], str)
        or not gate["reason"]
    ):
        return False
    if gate["request_id"] is not None:
        identity(gate["request_id"])
    return True


def task_sent_calls(
    record: dict[str, object], lifecycle: dict[str, object]
) -> list[dict[str, object]]:
    """The complete task-specific sent set, including prior maintenance calls."""
    calls = []
    for operation, value in _envelopes(record).items():
        if value is None or _reservation(record, operation) == "unsent":
            continue
        sent = mapping(value)
        receipt = (
            mapping(mapping(record["cancellations"])[operation[7:]])["receipt"]
            if operation.startswith("cancel:")
            else mapping(record["receipts"]).get(operation)
        )
        calls.append(
            {
                "envelope": sent,
                "reservation": _reservation(record, operation),
                "receipt": receipt,
                "observations": [
                    row
                    for row in record["os_observations"]
                    if mapping(row).get("request_id") == sent["request_id"]
                ],
            }
        )
    task_id = mapping(mapping(mapping(record["request"])["inputs"])["lad_target"])[
        "task_id"
    ]
    for value in mapping(lifecycle["maintenance"]).values():
        item = mapping(value)
        if item.get("task_id") != task_id:
            continue
        if (
            item.get("instance_id") != record["instance_id"]
            or item.get("run_id") != record["run_id"]
        ):
            raise IntakeError("maintenance_call_conflict")
        for label in ("pause", "recover", "resume"):
            row = mapping(item[label])
            if row["envelope"] is not None and row["reservation"] != "unsent":
                sent = mapping(row["envelope"])
                if (
                    sent.get("instance_id") != record["instance_id"]
                    or sent.get("run_id") != record["run_id"]
                ):
                    raise IntakeError("maintenance_call_conflict")
                calls.append(row)
    identifiers = [mapping(row["envelope"])["request_id"] for row in calls]
    if len(set(identifiers)) != len(identifiers):
        raise IntakeError("child_request_id_conflict")
    return calls


def _physical(
    runtime: Runtime, record: dict[str, object], *, allow_recovery: bool
) -> dict[str, object]:
    from .lad_service_runtime import _joined

    sent_refs = []
    for row in task_sent_calls(record, runtime.record):
        sent = mapping(row["envelope"])
        evidence = {
            "envelope": retain(runtime.service, canonical(sent) + b"\n", "envelope")
        }
        if _joined(runtime.service, row):
            evidence["joined"] = retain(
                runtime.service, canonical(row["observations"]) + b"\n", "joined"
            )
        else:
            if not allow_recovery:
                raise IntakeError("holder_unjoined")
            recovery = _recovery_for(runtime, record, str(sent["request_id"]))
            if recovery is None:
                raise IntakeError("holder_recovery_required")
            evidence["recovery"] = recovery
        sent_refs.append(evidence)
    return {"sent_calls": sent_refs}


def _validate_recovery_ref(
    service: Service,
    ref: object,
    record: dict[str, object],
    lost_id: str,
    *,
    sent_call: dict[str, object] | None = None,
) -> dict[str, object]:
    proof = fields(
        strict_json(read_ref(service, ref)),
        {
            "format",
            "task_id",
            "instance_id",
            "run_id",
            "lost_request_ids",
            "before",
            "after",
            "pause_receipt",
            "recover_envelope",
            "recover_receipt",
            "joined",
            "physical_evidence",
        },
    )
    if (
        proof["format"] != "millrace-plus-lad-holder-recovery/v1"
        or proof["instance_id"] != record["instance_id"]
        or proof["run_id"] != record["run_id"]
        or proof["task_id"]
        != mapping(mapping(mapping(record["request"])["inputs"])["lad_target"])[
            "task_id"
        ]
        or not isinstance(proof["lost_request_ids"], list)
        or lost_id not in proof["lost_request_ids"]
    ):
        raise IntakeError("invalid_holder_recovery")
    for identifier in proof["lost_request_ids"]:
        identity(identifier)
    if len(set(proof["lost_request_ids"])) != len(proof["lost_request_ids"]):
        raise IntakeError("invalid_holder_recovery")
    envelope = mapping(strict_json(read_ref(service, proof["recover_envelope"])))
    receipt = validate_receipt(
        strict_json(read_ref(service, proof["recover_receipt"])), envelope
    )
    if (
        envelope.get("operation") != "recover_run"
        or envelope.get("instance_id") != record["instance_id"]
        or envelope.get("run_id") != record["run_id"]
        or envelope.get("request_id") in proof["lost_request_ids"]
        or receipt.get("status") != "applied"
        or receipt.get("action") not in {"recovered", "already_terminal"}
    ):
        raise IntakeError("invalid_holder_recovery")
    joined = strict_json(read_ref(service, proof["joined"]))
    if not isinstance(joined, list) or not any(
        validate_call(row, str(record["os_home"]), envelope)["holder_joined"] is True
        for row in joined
    ):
        raise IntakeError("invalid_holder_recovery")
    views = {}
    for label in ("before", "after"):
        pair = fields(strict_json(read_ref(service, proof[label])), {"instance", "run"})
        observed = reduce_full_views(
            pair["instance"],
            pair["run"],
            instance_id=str(record["instance_id"]),
            run_id=str(record["run_id"]),
            start_request_id=str(
                mapping(mapping(record["envelopes"])["start"])["request_id"]
            ),
            paths=record["paths"],
            settings=record["settings"],
        )
        if not closed(observed, attention=True) or not recovery_ready(
            mapping(pair["run"])
        ):
            raise IntakeError("invalid_holder_recovery")
        views[label] = pair
    if (
        mapping(views["before"]["run"])["state_version"]
        != envelope["expected_state_version"]
        or mapping(views["after"]["run"])["status"]
        != mapping(views["before"]["run"])["status"]
    ):
        raise IntakeError("invalid_holder_recovery")
    if sent_call is None:
        sent_call = next(
            (
                row
                for row in task_sent_calls(record, {"maintenance": {}})
                if mapping(row["envelope"])["request_id"] == lost_id
            ),
            None,
        )
    if sent_call is None or sent_call["receipt"] is None:
        raise IntakeError("invalid_holder_recovery")
    old = mapping(sent_call["envelope"])
    old_receipt = validate_receipt(sent_call["receipt"], old)
    if old["request_id"] != lost_id or old_receipt["status"] == "queued":
        raise IntakeError("invalid_holder_recovery")
    if old["operation"] == "create_instance":
        if (
            old != mapping(record["envelopes"])["create"]
            or old_receipt != mapping(record["receipts"])["create"]
        ):
            raise IntakeError("recovery_old_call_conflict")
    elif old["operation"] == "start_run":
        if (
            old != mapping(record["envelopes"])["start"]
            or old_receipt != mapping(record["receipts"])["start"]
        ):
            raise IntakeError("recovery_old_call_conflict")
    elif (
        old.get("instance_id") != record["instance_id"]
        or old.get("run_id") != record["run_id"]
    ):
        raise IntakeError("recovery_old_call_conflict")
    for label, pair in views.items():
        controls = [
            *mapping(pair["instance"])["controls"],
            *mapping(pair["run"])["controls"],
        ]
        if label == "before" and any(
            mapping(row).get("request_id") == envelope["request_id"] for row in controls
        ):
            raise IntakeError("recovery_not_fresh")
        if old["operation"] != "create_instance":
            matches = [
                mapping(row)
                for row in controls
                if mapping(row).get("request_id") == lost_id
            ]
            if len(matches) != 1 or validate_receipt(matches[0], old) != old_receipt:
                raise IntakeError("recovery_old_call_conflict")
        if label == "after":
            matches = [
                mapping(row)
                for row in controls
                if mapping(row).get("request_id") == envelope["request_id"]
            ]
            if len(matches) != 1 or validate_receipt(matches[0], envelope) != receipt:
                raise IntakeError("recovery_receipt_conflict")
    refs = fields(
        proof["physical_evidence"],
        {"instance", "run", "observation", "view_proof", "journal"},
    )
    view_proof = strict_json(read_ref(service, refs["view_proof"]))
    validate_proof(
        view_proof,
        local_source=record["local_source"],
        observation=strict_json(read_ref(service, refs["observation"])),
        instance_view_bytes=read_ref(service, refs["instance"]),
        run_view_bytes=read_ref(service, refs["run"]),
    )
    if not recovery_ready(mapping(strict_json(read_ref(service, refs["run"])))):
        raise IntakeError("invalid_holder_recovery")
    if proof["pause_receipt"] is not None:
        read_ref(service, proof["pause_receipt"])
    read_ref(service, refs["journal"])
    return proof


def _recovery_for(
    runtime: Runtime, record: dict[str, object], lost_id: str
) -> dict[str, object] | None:
    from .lad_service_runtime import _joined

    for value in mapping(runtime.record["maintenance"]).values():
        item = mapping(value)
        ref = item.get("recovery_proof")
        if (
            ref is None
            or item.get("instance_id") != record["instance_id"]
            or item.get("run_id") != record["run_id"]
        ):
            continue
        proof = mapping(strict_json(read_ref(runtime.service, ref)))
        identifiers = proof.get("lost_request_ids")
        if not isinstance(identifiers, list):
            raise IntakeError("invalid_holder_recovery")
        for identifier in identifiers:
            identity(identifier)
        if lost_id not in identifiers:
            continue
        sent_call = next(
            (
                row
                for row in task_sent_calls(record, runtime.record)
                if mapping(row["envelope"])["request_id"] == lost_id
            ),
            None,
        )
        _validate_recovery_ref(
            runtime.service, ref, record, lost_id, sent_call=sent_call
        )
        row = mapping(item["recover"])
        receipt = validate_receipt(row["receipt"], mapping(row["envelope"]))
        if (
            receipt.get("status") != "applied"
            or receipt.get("action") not in {"recovered", "already_terminal"}
            or not _joined(runtime.service, row)
        ):
            raise IntakeError("invalid_holder_recovery")
        if (
            read_ref(runtime.service, proof["recover_envelope"])
            != canonical(row["envelope"]) + b"\n"
            or read_ref(runtime.service, proof["recover_receipt"])
            != canonical(row["receipt"]) + b"\n"
        ):
            raise IntakeError("invalid_holder_recovery")
        return cast(dict[str, object], ref)
    return None


def _uncovered_sent_calls(
    runtime: Runtime, record: dict[str, object]
) -> list[dict[str, object]]:
    """Retain raw loss while reusing only the exact already accepted recovery."""
    from .lad_service_runtime import _joined

    uncovered = []
    for row in task_sent_calls(record, runtime.record):
        if _joined(runtime.service, row):
            continue
        history = cast(list[object], row["observations"])
        sent = mapping(row["envelope"])
        if history:
            latest = validate_call(history[-1], str(record["os_home"]), sent)
            if (
                latest["holder_joined"] is None
                and row["receipt"] is not None
                and _recovery_for(runtime, record, str(sent["request_id"])) is not None
            ):
                continue
        uncovered.append(row)
    return uncovered


def _owned_proof(service: Service, intent: dict[str, object]) -> dict[str, object]:
    evidence = fields(intent["evidence"], {"operator_evidence", "proof"})
    if evidence["operator_evidence"] is not None:
        read_ref(service, evidence["operator_evidence"], limit=1048576)
    proof = mapping(strict_json(read_ref(service, evidence["proof"])))
    for ref in mapping(proof.get("domain_evidence", {})).get("available", {}).values():
        read_ref(service, ref)
    physical = mapping(proof["physical_evidence"])
    for ref in physical.get("refs", {}).values():
        read_ref(service, ref)
    for pair in physical.get("sent_calls", []):
        for ref in mapping(pair).values():
            read_ref(service, ref)
    return proof


def receipt(
    service: Service, request_id: str, intent: dict[str, object]
) -> dict[str, object]:
    decision = intent["decision"]
    if decision == "cancel-queued":
        disposition = "cancelled-before-start"
    elif decision in {"cancel-active", "retry-observation", "continue", "close-failed"}:
        disposition = {
            "cancel-active": "cancellation-requested",
            "retry-observation": "observation-requested",
            "continue": "continuation-requested",
            "close-failed": "closure-requested",
        }[str(decision)]
    else:
        proof = _owned_proof(service, intent)
        if proof["task_id"] != intent["task_id"] or proof["request_id"] != request_id:
            raise IntakeError("decision_proof_mismatch")
        disposition = (
            proof["disposition"]
            if proof["format"] != "millrace-plus-lad-abandonment/v1"
            else "abandoned-" + str(proof["terminal_status"])
        )
    return {
        "format": "millrace-plus-lad-decision-receipt/v1",
        "service_id": service.service_id,
        "task_id": intent["task_id"],
        "request_id": request_id,
        "intent_sha256": digest(intent),
        "disposition": disposition,
    }


def aftermath(
    service: Service,
    task: dict[str, object],
    intent: dict[str, object],
    receipt: dict[str, object],
) -> None:
    decision = intent["decision"]
    if decision in {"cancel-active", "retry-observation", "continue", "close-failed"}:
        return  # These receipts never release the active slot.
    if decision == "cancel-queued":
        if (
            task["status"] != "cancelled"
            or task["claim"] is not None
            or task["terminal_receipt"] != receipt
        ):
            raise IntakeError("invalid_intake")
        return
    proof = _owned_proof(service, intent)
    common = {
        "format",
        "request_id",
        "service_id",
        "task_id",
        "claim_sha256",
        "call_identity",
        "task_version",
        "terminal_status",
        "physical_evidence",
    }
    if proof["format"] == "millrace-plus-lad-no-run-disposition/v1":
        fields(proof, common | {"disposition"})
    elif proof["format"] == "millrace-plus-lad-terminal/v1":
        fields(
            proof,
            common
            | {
                "instance_id",
                "run_id",
                "core_state_home",
                "state_version",
                "operator_evidence",
                "domain_evidence",
                "disposition",
            },
        )
    elif proof["format"] != "millrace-plus-lad-abandonment/v1":
        raise IntakeError("invalid_disposition_proof")
    status = proof["terminal_status"]
    identity(proof["request_id"])
    if type(proof["task_version"]) is not int or proof["task_version"] < 1:
        raise IntakeError("decision_proof_mismatch")
    if (
        task["status"] != status
        or task["terminal_receipt"] != receipt
        or task["version"] != proof["task_version"] + 1
        or proof["claim_sha256"] != digest(task["claim"])
        or proof["service_id"] != service.service_id
        or proof["task_id"] != task["task_id"]
    ):
        raise IntakeError("decision_aftermath_mismatch")
    if proof["format"] == "millrace-plus-lad-abandonment/v1":
        fields(
            proof,
            {
                "format",
                "request_id",
                "service_id",
                "task_id",
                "claim_sha256",
                "call_identity",
                "instance_id",
                "run_id",
                "core_state_home",
                "task_version",
                "state_version",
                "terminal_status",
                "operator_evidence",
                "domain_evidence",
                "physical_evidence",
            },
        )
        if status not in {"failed", "cancelled"} or decision != "acknowledge-" + str(
            status
        ):
            raise IntakeError("invalid_abandonment")
        if (
            proof["operator_evidence"]
            != mapping(intent["evidence"])["operator_evidence"]
        ):
            raise IntakeError("invalid_abandonment")
        operator = fields(
            strict_json(read_ref(service, proof["operator_evidence"])),
            {
                "format",
                "acknowledge_abandonment",
                "expected_task_version",
                "expected_state_version",
            },
        )
        if (
            operator["format"] != "millrace-plus-lad-abandonment-intent/v1"
            or operator["acknowledge_abandonment"] is not True
            or type(operator["expected_task_version"]) is not int
            or type(operator["expected_state_version"]) is not int
            or operator["expected_task_version"] != proof["task_version"]
            or operator["expected_state_version"] != proof["state_version"]
        ):
            raise IntakeError("invalid_abandonment")
    physical = mapping(proof["physical_evidence"])
    refs = mapping(physical.get("refs"))
    init_intent = mapping(strict_json(read_ref(service, refs["init_intent"])))
    saved_journal = mapping(strict_json(read_ref(service, refs["journal"])))
    from .subworkflow_v2_records import validate_journal

    source = mapping(saved_journal.get("local_source"))
    if (
        source.get("service_id") != service.service_id
        or source.get("service_home") != str(service.home)
        or source.get("init_intent_sha256") != digest(init_intent)
        or source.get("core_state_home") != init_intent.get("core_state_home")
        or source.get("os_home") != service.config["os_home"]
        or source.get("os_profile_sha256")
        != mapping(service.config["os_profile"])["sha256"]
        or init_intent.get("service_sha256")
        != hashlib.sha256(_read_exact(service.home / "service.json")).hexdigest()
    ):
        raise IntakeError("decision_source_mismatch")
    lifecycle = mapping(strict_json(read_ref(service, refs["lifecycle"])))
    created = validate_receipt(
        mapping(lifecycle["create"])["receipt"], mapping(init_intent["create"])
    )
    started = validate_receipt(
        mapping(lifecycle["start"])["receipt"],
        mapping(mapping(lifecycle["start"])["envelope"]),
    )
    if (
        created.get("status") != "applied"
        or started.get("status") != "applied"
        or created.get("instance_id") != source["parent_instance_id"]
        or started.get("instance_id") != source["parent_instance_id"]
        or started.get("run_id") != source["parent_run_id"]
    ):
        raise IntakeError("decision_source_mismatch")
    claim = mapping(task["claim"])
    validate_journal(
        saved_journal,
        Path(str(claim["call_home"])),
        str(claim["call_key"]),
        _local_source=source,
    )
    if (
        saved_journal["claim_sha256"] != digest(claim)
        or saved_journal["request_sha256"] != proof["call_identity"]
        or mapping(mapping(saved_journal["request"])["inputs"])["request_markdown"]
        != cast(bytes, task["request_bytes"]).decode()
    ):
        raise IntakeError("decision_proof_mismatch")
    supplied = {}
    for value in physical["sent_calls"]:
        pair = mapping(value)
        sent = mapping(strict_json(read_ref(service, pair["envelope"])))
        if sent["request_id"] in supplied:
            raise IntakeError("decision_proof_mismatch")
        supplied[sent["request_id"]] = sent
        if "joined" in pair:
            data = strict_json(read_ref(service, pair["joined"]))
            observations = data if isinstance(data, list) else [data]
            if not any(
                validate_call(row, str(service.config["os_home"]), sent)["source"]
                == "os_owned_call"
                and mapping(row)["holder_joined"] is True
                for row in observations
            ):
                raise IntakeError("holder_unjoined")
        elif proof["format"] == "millrace-plus-lad-abandonment/v1":
            raise IntakeError("holder_unjoined")
        else:
            old_call = next(
                (
                    row
                    for row in task_sent_calls(saved_journal, lifecycle)
                    if mapping(row["envelope"])["request_id"] == sent["request_id"]
                ),
                None,
            )
            _validate_recovery_ref(
                service,
                pair["recovery"],
                saved_journal,
                str(sent["request_id"]),
                sent_call=old_call,
            )
    expected = {
        mapping(value)["request_id"]: value
        for operation, value in _envelopes(saved_journal).items()
        if value is not None and _reservation(saved_journal, operation) != "unsent"
    }
    for value in mapping(lifecycle["maintenance"]).values():
        item = mapping(value)
        if item.get("task_id") != task["task_id"]:
            continue
        for label in ("pause", "recover", "resume"):
            row = mapping(item[label])
            if row["envelope"] is not None and row["reservation"] != "unsent":
                expected[mapping(row["envelope"])["request_id"]] = row["envelope"]
    if supplied != expected:
        raise IntakeError("decision_sent_call_missing")
    if proof["format"] == "millrace-plus-lad-no-run-disposition/v1":
        reservations, receipts = (
            mapping(saved_journal["reservations"]),
            mapping(saved_journal["receipts"]),
        )
        if "instance" in refs:
            instance = mapping(strict_json(read_ref(service, refs["instance"])))
            if (
                instance.get("instance_id") != saved_journal["instance_id"]
                or instance.get("runs") != []
                or instance.get("project_root")
                != mapping(saved_journal["paths"])["project"]
                or instance.get("workflow_file")
                != str(mapping(saved_journal["paths"])["workflow"]) + "/workflow.json"
                or any(
                    instance.get(name) != mapping(saved_journal["settings"])[name]
                    for name in (
                        "path",
                        "env_allowlist",
                        "host_paths",
                        "runtime_defaults",
                        "operator_policy",
                    )
                )
            ):
                raise IntakeError("invalid_no_run_proof")
        if (
            saved_journal["run_id"] is not None
            or (
                reservations["create"] != "unsent"
                and mapping(receipts.get("create", {})).get("status")
                not in {"applied", "refused"}
            )
            or (
                reservations["start"] != "unsent"
                and mapping(receipts.get("start", {})).get("status") != "refused"
            )
        ):
            raise IntakeError("invalid_no_run_proof")
    if "view_proof" in physical:
        refs = mapping(physical["refs"])
        view_proof = mapping(strict_json(read_ref(service, refs["view_proof"])))
        observation = strict_json(read_ref(service, refs["observation"]))
        validate_proof(
            view_proof,
            local_source=source,
            observation=observation,
            instance_view_bytes=read_ref(service, refs["instance"]),
            run_view_bytes=read_ref(service, refs["run"]),
        )
        if (
            not closed(observation)
            or mapping(observation)["status"] != proof["terminal_status"]
            or (
                proof["format"] != "millrace-plus-lad-abandonment/v1"
                and mapping(proof["domain_evidence"])["missing"]
            )
        ):
            raise IntakeError("decision_closure_mismatch")
        if (
            view_proof["instance_id"] != proof["instance_id"]
            or view_proof["run_id"] != proof["run_id"]
            or view_proof["state_version"] != proof["state_version"]
        ):
            raise IntakeError("decision_proof_mismatch")


def replay(
    runtime: Runtime,
    task: dict[str, object],
    decision: str,
    request_id: str,
    operator_raw: bytes | None,
) -> dict[str, object] | None:
    service = runtime.service
    with service.intake._connection() as connection:
        previous = connection.execute(
            "SELECT intent,receipt FROM decisions WHERE request_id=?",
            (identity(request_id),),
        ).fetchone()
        if previous is None:
            return None
        intent = mapping(strict_json(previous["intent"].encode()))
        if intent["task_id"] != task["task_id"] or intent["decision"] != decision:
            raise IntakeError("decision_conflict")
        saved_operator = mapping(intent["evidence"])["operator_evidence"]
        if (
            None if saved_operator is None else read_ref(service, saved_operator)
        ) != operator_raw:
            raise IntakeError("decision_conflict")
        return mapping(strict_json(previous["receipt"].encode()))


def _abandonment_intent(raw: bytes | None) -> dict[str, object] | None:
    if raw is None:
        return None
    try:
        value = strict_json(raw)
    except IntakeError:
        return None
    if (
        isinstance(value, dict)
        and value.get("format") == "millrace-plus-lad-abandonment-intent/v1"
    ):
        return value
    return None


def settle(
    runtime: Runtime,
    task: dict[str, object],
    *,
    decision: str,
    request_id: str,
    operator_raw: bytes | None = None,
) -> dict[str, object]:
    """Publish fresh full evidence and terminal CAS without releasing Core exclusion."""
    service = runtime.service
    previous = replay(runtime, task, decision, request_id, operator_raw)
    if previous is not None:
        return previous
    with target_lock(service.config, protected_roots=(service.home,)):
        journal, record = runtime.call(task)
        with journal.lock(observer=True):
            record = journal.load()
            if record["run_id"] is None:
                return _no_run(
                    runtime, task, journal, record, decision, request_id, operator_raw
                )
            from .lad_service_runtime import _core

            control = _core()
            home = Path(str(runtime.intent["core_state_home"]))
            instance_id, run_id = str(record["instance_id"]), str(record["run_id"])
            # All network observations occur before entering this function/guard.
            current = control.inspect_run(instance_id, run_id, state_home=home)
            operator_intent = _abandonment_intent(operator_raw)
            expected = (
                operator_intent["expected_state_version"]
                if operator_intent is not None
                else current["state_version"]
            )
            with control.exclusive_run_snapshot(
                instance_id, run_id, state_home=home, expected_state_version=expected
            ) as run:
                instance = control.inspect_instance(instance_id, state_home=home)
                local_source = journal.local_source
                observed = reduce_full_views(
                    instance,
                    run,
                    instance_id=instance_id,
                    run_id=run_id,
                    start_request_id=str(
                        mapping(mapping(record["envelopes"])["start"])["request_id"]
                    ),
                    paths=record["paths"],
                    settings=record["settings"],
                )
                if not closed(observed):
                    raise IntakeError("child_not_closed")
                known_controls(runtime, record, instance, run)
                actual = str(run["status"])
                if decision == "settle" and actual != "succeeded":
                    raise IntakeError("explicit_acknowledgement_required")
                if decision != "settle" and decision != "acknowledge-" + actual:
                    raise IntakeError("decision_status_mismatch")
                latest = service.intake.tasks(str(task["task_id"]))[0]
                if (
                    latest["version"] != task["version"]
                    or latest["claim"] != task["claim"]
                    or latest["status"] not in {"claimed", "running", "held"}
                ):
                    raise IntakeError("stale_task")
                domain = domain_evidence(service, record, run)
                abandonment = bool(domain["missing"])
                if abandonment:
                    operator = fields(
                        strict_json(operator_raw or b"{}"),
                        {
                            "format",
                            "acknowledge_abandonment",
                            "expected_task_version",
                            "expected_state_version",
                        },
                    )
                    if (
                        actual not in {"failed", "cancelled"}
                        or decision != "acknowledge-" + actual
                        or operator["format"]
                        != "millrace-plus-lad-abandonment-intent/v1"
                        or operator["acknowledge_abandonment"] is not True
                        or type(operator["expected_task_version"]) is not int
                        or type(operator["expected_state_version"]) is not int
                        or operator["expected_task_version"] != task["version"]
                        or operator["expected_state_version"] != run["state_version"]
                    ):
                        raise IntakeError("explicit_abandonment_required")
                physical = _physical(runtime, record, allow_recovery=not abandonment)
                full_instance, full_run = (
                    canonical(instance) + b"\n",
                    canonical(run) + b"\n",
                )
                instance_ref = retain(service, full_instance, "instance")
                run_ref = retain(service, full_run, "run")
                view_proof = proof_manifest(
                    local_source, observed, instance_ref, run_ref
                )
                validate_proof(
                    view_proof,
                    local_source=local_source,
                    observation=observed,
                    instance_view_bytes=full_instance,
                    run_view_bytes=full_run,
                )
                physical.update(
                    view_proof=True,
                    refs={
                        "instance": instance_ref,
                        "run": run_ref,
                        "observation": retain(
                            service, canonical(observed) + b"\n", "observation"
                        ),
                        "view_proof": retain(
                            service, canonical(view_proof) + b"\n", "view-proof"
                        ),
                        "journal": retain(
                            service, canonical(record) + b"\n", "journal"
                        ),
                        "init_intent": retain(
                            service, canonical(runtime.intent) + b"\n", "init-intent"
                        ),
                        "lifecycle": retain(
                            service, canonical(runtime.record) + b"\n", "lifecycle"
                        ),
                    },
                )
                operator_ref = (
                    None
                    if operator_raw is None
                    else retain(service, operator_raw, "operator")
                )
                proof = {
                    "format": "millrace-plus-lad-abandonment/v1"
                    if abandonment
                    else "millrace-plus-lad-terminal/v1",
                    "request_id": request_id,
                    "service_id": service.service_id,
                    "task_id": task["task_id"],
                    "claim_sha256": digest(task["claim"]),
                    "call_identity": record["request_sha256"],
                    "instance_id": instance_id,
                    "run_id": run_id,
                    "core_state_home": str(home),
                    "task_version": task["version"],
                    "state_version": run["state_version"],
                    "terminal_status": actual,
                    "operator_evidence": operator_ref,
                    "domain_evidence": domain,
                    "physical_evidence": physical,
                }
                if not abandonment:
                    proof["disposition"] = actual
                proof_ref = retain(service, canonical(proof) + b"\n", "disposition")
                intent = {
                    "task_id": task["task_id"],
                    "decision": decision,
                    "evidence": {"operator_evidence": operator_ref, "proof": proof_ref},
                }
                return service.intake.decide(
                    request_id,
                    intent,
                    check=lambda row: None,
                    terminal_status=actual,
                    expected_version=int(task["version"]),
                    expected_claim=mapping(task["claim"]),
                )


def _unused_instance(
    runtime: Runtime, record: dict[str, object], view: dict[str, object]
) -> None:
    if (
        view.get("instance_id") != record["instance_id"]
        or view.get("project_root") != mapping(record["paths"])["project"]
        or view.get("workflow_file")
        != str(mapping(record["paths"])["workflow"]) + "/workflow.json"
        or any(
            view.get(name) != mapping(record["settings"])[name]
            for name in (
                "path",
                "env_allowlist",
                "host_paths",
                "runtime_defaults",
                "operator_policy",
            )
        )
        or type(view.get("state_version")) is not int
        or view["state_version"] < 1
        or view.get("runs") != []
    ):
        raise IntakeError("no_run_conflict")
    known_controls(runtime, record, view)


def _no_run(
    runtime: Runtime,
    task: dict[str, object],
    journal,
    record: dict[str, object],
    decision: str,
    request_id: str,
    operator_raw: bytes | None,
) -> dict[str, object]:
    """Exact no-run authority never enters a Core run guard or invents a run."""
    if _abandonment_intent(operator_raw) is not None:
        raise IntakeError("actual_run_required_for_abandonment")
    reservations = mapping(record["reservations"])
    receipts = mapping(record["receipts"])
    create = mapping(receipts.get("create", {}))
    start = mapping(receipts.get("start", {}))
    if (
        record["run_id"] is not None
        or (
            reservations["create"] != "unsent"
            and create.get("status") not in {"applied", "refused"}
        )
        or (reservations["start"] != "unsent" and start.get("status") != "refused")
        or any(mapping(value).get("status") == "queued" for value in receipts.values())
    ):
        raise IntakeError("no_run_authority_unresolved")
    instance_view = None
    if record["instance_id"] is not None:
        view = runtime.client.read(
            str(record["instance_id"]), deadline=__import__("time").monotonic() + 2
        )
        _unused_instance(runtime, record, view)
        instance_view = view
        sent_start = mapping(record["envelopes"])["start"]
        if sent_start is not None:
            matches = [
                mapping(row)
                for row in view.get("controls", [])
                if mapping(row).get("request_id") == mapping(sent_start)["request_id"]
            ]
            if matches and (
                len(matches) != 1
                or validate_receipt(matches[0], mapping(sent_start))["status"]
                != "refused"
            ):
                raise IntakeError("no_run_conflict")
    physical = _physical(runtime, record, allow_recovery=False)
    status = "cancelled" if record["cancel_intent"] is not None else "failed"
    if decision not in {"acknowledge-" + status, "settle-no-run"}:
        raise IntakeError("decision_status_mismatch")
    service = runtime.service
    physical["refs"] = {
        "journal": retain(service, canonical(record) + b"\n", "no-run-journal"),
        "init_intent": retain(
            service, canonical(runtime.intent) + b"\n", "init-intent"
        ),
        "lifecycle": retain(service, canonical(runtime.record) + b"\n", "lifecycle"),
    }
    if instance_view is not None:
        physical["refs"]["instance"] = retain(
            service, canonical(instance_view) + b"\n", "no-run-instance"
        )
    operator_ref = (
        None if operator_raw is None else retain(service, operator_raw, "operator")
    )
    proof = {
        "format": "millrace-plus-lad-no-run-disposition/v1",
        "request_id": request_id,
        "service_id": service.service_id,
        "task_id": task["task_id"],
        "claim_sha256": digest(task["claim"]),
        "call_identity": record["request_sha256"],
        "task_version": task["version"],
        "terminal_status": status,
        "disposition": status + "-before-start",
        "physical_evidence": physical,
    }
    intent = {
        "task_id": task["task_id"],
        "decision": decision,
        "evidence": {
            "operator_evidence": operator_ref,
            "proof": retain(service, canonical(proof) + b"\n", "no-run"),
        },
    }
    return service.intake.decide(
        request_id,
        intent,
        check=lambda row: None,
        terminal_status=status,
        expected_version=int(task["version"]),
        expected_claim=mapping(task["claim"]),
    )


def verify_terminal_current(runtime: Runtime, task: dict[str, object]) -> None:
    """A saved disposition is historical; stop rechecks actual current closure."""
    if (
        task["status"] not in {"succeeded", "failed", "cancelled"}
        or task["terminal_receipt"] is None
    ):
        raise IntakeError("child_disposition_required")
    journal, record = runtime.call(task)
    with journal.lock(observer=True):
        record = journal.load()
        if record["run_id"] is None:
            reservations = mapping(record["reservations"])
            if (
                reservations["start"] != "unsent"
                and mapping(mapping(record["receipts"]).get("start", {})).get("status")
                != "refused"
            ):
                raise IntakeError("no_run_authority_unresolved")
            _physical(runtime, record, allow_recovery=False)
            if record["instance_id"] is not None:
                view = runtime.client.read(
                    str(record["instance_id"]),
                    deadline=__import__("time").monotonic() + 2,
                )
                _unused_instance(runtime, record, view)
            return
        from .lad_service_runtime import _core

        control = _core()
        home = Path(str(runtime.intent["core_state_home"]))
        run = control.inspect_run(
            str(record["instance_id"]), str(record["run_id"]), state_home=home
        )
        with control.exclusive_run_snapshot(
            str(record["instance_id"]),
            str(record["run_id"]),
            state_home=home,
            expected_state_version=run["state_version"],
        ) as guarded:
            view = control.inspect_instance(str(record["instance_id"]), state_home=home)
            observation = reduce_full_views(
                view,
                guarded,
                instance_id=str(record["instance_id"]),
                run_id=str(record["run_id"]),
                start_request_id=str(
                    mapping(mapping(record["envelopes"])["start"])["request_id"]
                ),
                paths=record["paths"],
                settings=record["settings"],
            )
            if not closed(observation) or guarded["status"] != task["status"]:
                raise IntakeError("child_closure_changed")
            known_controls(runtime, record, view, guarded)
            abandoned = str(
                mapping(task["terminal_receipt"])["disposition"]
            ).startswith("abandoned-")
            _physical(runtime, record, allow_recovery=not abandoned)
