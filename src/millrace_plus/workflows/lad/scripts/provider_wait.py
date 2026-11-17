"""LAD provider-wait graph guards: one subcommand per declared command node.

``classify|due|prepare-retry|prepare-diagnosis|accept-diagnosis|human ROLE``.
Every subcommand emits exactly one declared outcome of its node; refusals emit
``UNCERTAIN`` (the graph's ``lad_reconciliation_required`` pause), never a native
dispatch. No subcommand launches a native process or calls a Core mutator. A
routing-policy/v1 copy never imports provider code: its classifier returns the
existing ordinary replacement route and every other guard refuses.

Record logic is Plus ``millrace_plus.provider_wait`` (imported lazily for v2
copies only). This helper supplies the semantic proofs the record module does
not: the active LAD occurrence and continuation, the exact context/outbox
handoff, the observed attempt's input fence, the public Core control lineage,
and the existing guidance/source/policy pins.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from lad_state import (
    LadError,
    canonical,
    digest,
    emit,
    guidance,
    load,
    load_json,
    mark,
    provider_policy,
    provider_recovery,
    read,
    roots,
    safe,
    save,
    source,
)

if TYPE_CHECKING:
    from millrace_plus.provider_wait import Operation
    from millrace_plus.router.native_diagnostic import DiagnosticRecord

ACTIONS = (
    "classify",
    "due",
    "prepare-retry",
    "prepare-diagnosis",
    "accept-diagnosis",
    "human",
)
FOREGROUND = (
    "recon",
    "planner",
    "manager",
    "builder",
    "checker",
    "fixer",
    "doublechecker",
    "troubleshooter",
    "consultant",
    "updater",
    "arbiter",
)
CONTEXT = "lad-state/native-context.json"
OUTBOX = "lad-state/native-diagnostics"
LINEAGE_CONTROLS = {
    "pause_plane",
    "resume_plane",
    "pause_run",
    "resume_run",
    "recover_run",
}
OWNER = ("invocation_id", "plane_id", "node_id", "producing_revision")


class Hold(Exception):
    """Refuse into the declared uncertainty pause with a stable code."""


def _now() -> datetime:
    return datetime.now(UTC)


def _binding(role: str) -> dict[str, Any]:
    """The captured graph binding of the role's native node."""
    root = Path(__file__).resolve().parent.parent
    graph = load_json(read(root, "workflow.json"))
    node = graph["planes"]["foreground"]["nodes"][role]
    binding: dict[str, Any] = graph["bindings"][node["invoke"]["binding"]]
    return binding


def packet_lineage(occurrence: dict[str, Any]) -> dict[str, Any]:
    selected = occurrence["packet"]
    return {
        "lineage": occurrence["lineage"],
        "packet_id": None if selected is None else selected["id"],
    }


def pending_route(occurrence: dict[str, Any]) -> dict[str, Any]:
    return {"node": occurrence["role"], "pending": occurrence["pending"]}


def exports(foreground: Path, oid: str) -> list[tuple[dict[str, str], bytes]]:
    """Every published terminal record for one context (no Core locator)."""
    folder = safe(foreground, f"{OUTBOX}/{oid}", missing=True)
    if not os.path.lexists(folder):
        return []
    if folder.is_symlink() or not folder.is_dir():
        raise Hold("provider_outbox_invalid")
    found: list[tuple[dict[str, str], bytes]] = []
    for name in sorted(os.listdir(folder)):
        if name.startswith(".native-diagnostic-"):
            continue  # an unpublished temporary is never a record
        if not name.endswith(".json"):
            raise Hold("provider_outbox_invalid")
        relative = f"{OUTBOX}/{oid}/{name}"
        raw = read(foreground, relative)
        found.append(({"path": relative, "sha256": digest(raw)}, raw))
    return found


def _record(
    ref: dict[str, str], raw: bytes, oid: str, occurrence: dict[str, Any]
) -> tuple[DiagnosticRecord, dict[str, Any]]:
    """The exact v1/v2 record for this occurrence's context and input."""
    from millrace_plus.router import native_diagnostic as nd

    try:
        record = nd.load_record(raw)
    except Exception as exc:  # strict reader refusal
        raise Hold("provider_record_invalid") from exc
    facts: dict[str, Any] = record.to_json()  # strictly validated record bytes
    if (
        ref["path"] != f"{OUTBOX}/{oid}/{facts['invocation_id']}.json"
        or facts["context_id"] != oid
        or facts["input_sha256"] != occurrence["input_sha256"]
        or facts["plane_id"] != "foreground"
        or facts["node_id"] != occurrence["role"]
    ):
        raise Hold("provider_record_mismatch")
    return record, facts


def _authority_facts(facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "adapter_id": facts["adapter_id"],
        "contract": facts["adapter_contract"],
        "config_sha256": facts["settings_sha256"],
        "requested_model": facts["requested_model"],
        "requested_effort": facts["requested_effort"],
        "resolved_model": facts["resolved_model"],
        "resolved_effort": facts["resolved_effort"],
        "host_route_sha256": facts["host_route_sha256"],
    }


def _service(project: Path) -> dict[str, object] | None:
    from millrace_plus import engineering_feedback as ef

    subject = ef.service_subject(project)
    if subject is None:
        return None
    return {
        "service_id": subject["service_id"],
        "task_id": subject["task_id"],
        "claim_id": subject["claim_sha256"],
    }


def _failure(operation: Operation) -> dict[str, object]:
    for event in reversed(operation.events):
        if (
            event["kind"] in {"failure_classified", "human_required"}
            and event["diagnostic_ref"] is not None
        ):
            return event
    raise Hold("provider_failure_missing")


def _preparations(
    foreground: Path, operation: Operation
) -> dict[str, tuple[dict[str, Any], dict[str, object]]]:
    found: dict[str, tuple[dict[str, Any], dict[str, object]]] = {}
    for event in operation.events:
        if event["kind"] in {"retry_reserved", "diagnosis_reserved"}:
            # A strict C2 reservation event always pins its preparation ref.
            ref = cast(dict[str, str], event["preparation_ref"])
            raw = read(foreground, ref["path"])
            if digest(raw) != ref["sha256"]:
                raise Hold("provider_preparation_invalid")
            preparation: dict[str, Any] = load_json(raw)
            found[preparation["new_occurrence"]] = (preparation, event)
    return found


def attempt_lineage(
    foreground: Path, state: dict[str, Any], operation: Operation, oid: str
) -> tuple[str, dict[str, object] | list[str]] | None:
    """``("retry", ref)``, ``("replacement", chain)`` or None for this occurrence.

    A retry must be this operation's exact retry preparation with the occurrence's
    actual input digest; a replacement chain follows the existing
    ``replacement_of`` / ``replacement_intent.old_occurrence_id`` links back to a
    generation of this operation (the original or a retry occurrence). The
    immutable first input is never substituted for a later attempt.
    """
    preparations = _preparations(foreground, operation)
    occurrence = state["occurrences"][oid]
    found = preparations.get(oid)
    if found is not None:
        preparation, event = found
        if (
            preparation["kind"] != "retry"
            or preparation["input_sha256"] != occurrence["input_sha256"]
        ):
            return None
        return "retry", cast(dict[str, object], event["preparation_ref"])
    generations = {operation.authority["original_occurrence"]} | {
        key for key, (prep, _) in preparations.items() if prep["kind"] == "retry"
    }
    chain: list[str] = [oid]
    current = occurrence
    while current["replacement_of"] is not None:
        previous = current["replacement_of"]
        intent = current["replacement_intent"]
        if (
            not isinstance(intent, dict)
            or intent.get("old_occurrence_id") != previous
            or previous in chain
            or previous not in state["occurrences"]
            or state["occurrences"][previous]["role"] != occurrence["role"]
        ):
            return None
        chain.insert(0, previous)
        if previous in generations:
            return "replacement", chain
        current = state["occurrences"][previous]
    return None


def _continuation(state: dict[str, Any], node: str) -> str:
    continuation = state["continuation"]
    if not continuation or continuation["node"] != node:
        raise Hold("provider_continuation_not_selected")
    selected: str = continuation["occurrence_id"]  # validated session ledger
    return selected


# ------------------------------------------------------------------ classify


def classify(foreground: Path, project: Path, role: str) -> str:
    if not provider_recovery():
        return "ORDINARY_ERROR"  # routing-policy/v1: existing guarded recovery.
    from millrace_plus import provider_wait as pw
    from millrace_plus.router import native_diagnostic as nd

    policy = provider_policy()[0]
    state = load(foreground)
    oid = state["active"].get("foreground")
    occurrence = state["occurrences"].get(oid)
    if (
        occurrence is None
        or occurrence["role"] != role
        or occurrence["format"] != "millrace-lad-input/v2"
        or oid in state["accepted"]
    ):
        raise Hold("provider_attempt_owner")
    continuation = state["continuation"]
    if continuation and continuation["node"] in {
        f"provider_due_{role}",
        f"provider_human_{role}",
    }:
        return classify_replay(foreground, state, occurrence, role, policy)
    if _continuation(state, role) != oid:
        raise Hold("provider_attempt_owner")
    path = safe(foreground, CONTEXT, missing=True)
    if not os.path.lexists(path):
        # Refused bridge preflight has both members null and therefore no
        # export. An export without its context is invalid retained state.
        if exports(foreground, oid):
            raise Hold("provider_context_missing_with_export")
        return "ORDINARY_ERROR"
    try:
        context = nd.NativeContext.load(read(foreground, CONTEXT))
    except Exception as exc:
        raise Hold("provider_context_invalid") from exc
    if (context.context_id, context.input_sha256) != (oid, occurrence["input_sha256"]):
        raise Hold("provider_context_mismatch")
    operation = pw.pending_operation(foreground, role=role, recover=True)
    current_source = digest(canonical(source(foreground)))
    published = exports(foreground, oid)

    def uncertain() -> str:
        if operation is not None:
            pw.classify_failure(
                foreground,
                authority=operation.authority,
                classification=pw.Classification(None, "UNCERTAIN"),
                occurrence_id=oid,
                diagnostic_ref=None,
                failed_at=_now(),
                current_source_sha256=current_source,
                policy=policy,
            )
        return "UNCERTAIN"

    if not published:
        # Native facts may exist with a null bridge ref: ordinary recovery, no count.
        return "ORDINARY_ERROR"
    if len(published) != 1:
        return uncertain()
    ref, raw = published[0]
    try:
        record, facts = _record(ref, raw, oid, occurrence)
    except Hold:
        return uncertain()
    if facts["transport"] == "completed":
        return "ORDINARY_ERROR"  # application/result error after native success
    if not isinstance(record, nd.NativeDiagnosticV2):
        # A valid v1 record lacks authority facts: ordinary guarded recovery,
        # preserving any existing operation without count or retry authority.
        return "ORDINARY_ERROR"
    binding = _binding(role)
    found = pw.classify(
        adapter_id=binding["adapter"],
        adapter_contract=binding["contract"],
        observed_version=facts["observed_version"],
        transport=facts["transport"],
        writer_state=facts["writer_state"],
        failure_code=facts["failure_code"],
        native_diagnostic=facts["native_diagnostic"],
        bridge_ref=ref,
        bridge_record=facts,
        effects_resolved=occurrence["helper_mode"] == "direct",
    )
    if operation is None:
        authority = pw.authority_record(
            session_id=state["session_id"],
            service=_service(project),
            packet_lineage=packet_lineage(occurrence),
            role=role,
            pending_route=pending_route(occurrence),
            original_occurrence=oid,
            input_sha256=occurrence["input_sha256"],
            request_sha256=occurrence["request_sha256"],
            source_sha256=occurrence["source_sha256"],
            guidance_sha256=occurrence["guidance_sha256"],
            policy_sha256=policy["sha256"],
            binding=_authority_facts(facts),
            native_owner={key: facts[key] for key in OWNER},
        )
    else:
        authority = operation.authority
        # The strict first C2 failure event always pins its diagnostic ref.
        original = cast(dict[str, str], operation.events[0]["diagnostic_ref"])
        prior_raw = read(foreground, original["path"])
        if digest(prior_raw) != original["sha256"]:
            return uncertain()
        prior = nd.load_record(prior_raw)
        lineage = attempt_lineage(foreground, state, operation, oid)
        # The original occurrence is the operation's own first generation: a
        # crash before save() replays its exact recorded failure idempotently.
        original_replay = oid == authority["original_occurrence"]
        if (
            lineage is None
            and not original_replay
            and found.outcome == "ORDINARY_ERROR"
        ):
            # An unrelated same-role failure keeps its ordinary route; the
            # unfinished operation is not recorded, changed or cleared.
            return found.outcome
        if (
            _authority_facts(facts) != authority["binding"]
            or not isinstance(prior, nd.NativeDiagnosticV2)
            or prior.observed_version != facts["observed_version"]
            or (lineage is None and not original_replay)
            or occurrence["guidance_sha256"] != authority["guidance_sha256"]
            or packet_lineage(occurrence) != authority["packet_lineage"]
        ):
            return uncertain()  # no count, retry, rebind or reset
    outcome = pw.classify_failure(
        foreground,
        authority=authority,
        classification=found,
        occurrence_id=oid,
        diagnostic_ref=ref,
        failed_at=_now(),
        current_source_sha256=current_source,
        policy=policy,
    )[0]
    if outcome == "PROVIDER_WAIT":
        mark(state, f"provider_due_{role}", oid)
        save(foreground, state)
    elif outcome == "PROVIDER_HUMAN_REQUIRED":
        mark(state, f"provider_human_{role}", oid)
        save(foreground, state)
    return outcome


def classify_replay(
    foreground: Path,
    state: dict[str, Any],
    occurrence: dict[str, Any],
    role: str,
    policy: dict[str, object],
) -> str:
    """Exact re-run of ``classify`` after its own session save.

    The continuation must be the mark this node saved for the operation's tip:
    the recorded failure of this occurrence, pinned to its single retained
    export, with unchanged context, guidance and policy. Anything else holds.
    Nothing is recorded, marked or saved.
    """
    from millrace_plus import provider_wait as pw
    from millrace_plus.router import native_diagnostic as nd

    oid = occurrence["occurrence_id"]
    operation = pw.pending_operation(foreground, role=role, recover=True)
    published = exports(foreground, oid)
    try:
        context = nd.NativeContext.load(read(foreground, CONTEXT))
    except Exception as exc:
        raise Hold("provider_context_invalid") from exc
    if operation is None or len(published) != 1:
        raise Hold("provider_replay_conflict")
    tip = operation.tip
    held = tip["phase"] == "human_required"
    expected = f"provider_human_{role}" if held else f"provider_due_{role}"
    continuation = state["continuation"]
    selected = (continuation["node"], continuation["occurrence_id"])
    pinned = (context.context_id, context.input_sha256)
    if (
        tip["kind"] not in {"failure_classified", "human_required"}
        or tip["occurrence_id"] != oid
        or tip["diagnostic_ref"] != published[0][0]
        or pinned != (oid, occurrence["input_sha256"])
        or selected != (expected, oid)
        or occurrence["guidance_sha256"] != guidance()
        or operation.authority["guidance_sha256"] != occurrence["guidance_sha256"]
        or operation.authority["policy_sha256"] != policy["sha256"]
    ):
        raise Hold("provider_replay_conflict")
    return "PROVIDER_HUMAN_REQUIRED" if held else "PROVIDER_WAIT"


# ----------------------------------------------------------------------- due


def _pending(foreground: Path, role: str) -> Operation:
    from millrace_plus import provider_wait as pw

    operation = pw.pending_operation(foreground, role=role, recover=True)
    if operation is None:
        raise Hold("provider_operation_missing")
    return operation


def _observed(
    foreground: Path, operation: Operation
) -> tuple[dict[str, object], dict[str, Any], str]:
    observed = [e for e in operation.events if e["kind"] == "observed"]
    if not observed:
        raise Hold("provider_observation_required")
    event = observed[-1]
    # A strict C2 ``observed`` event always pins its observation ref.
    pinned = cast(dict[str, str], event["observation_ref"])
    raw = read(foreground, pinned["path"])
    if digest(raw) != pinned["sha256"]:
        raise Hold("provider_observation_invalid")
    observation: dict[str, Any] = load_json(raw)
    observation_id: str = observation["observation_id"]
    return event, observation, observation_id


def _retained(foreground: Path, pinned: dict[str, str]) -> object:
    raw = read(foreground, pinned["path"])
    if digest(raw) != pinned["sha256"]:
        raise Hold("provider_fact_invalid")
    return load_json(raw)


def lineage_controls(
    foreground: Path,
    view: dict[str, Any],
    event: dict[str, object],
    observation: dict[str, Any],
    role: str,
) -> list[dict[str, Any]]:
    """Public controls applied since the observation; only legitimate lineage.

    The current guard invocation must be the running ``provider_due_R`` node of
    the observed revision; the failed completion and its classifier transition
    are unchanged; no operator move happened after the observed version; and
    every control receipt not pinned at observation is an applied or refused
    pause/resume/recover of this run (standalone public control, or the journaled
    service maintenance pause and ``lad decide`` continuation).
    """
    if view.get("active_revision") != observation["active_revision"]:
        raise Hold("provider_revision_changed")
    planes = view.get("planes")
    plane = planes.get("foreground") if isinstance(planes, dict) else None
    active = plane.get("active_invocation") if isinstance(plane, dict) else None
    rows = [r for r in view.get("invocations", []) if r.get("id") == active]
    if (
        active is None
        or len(rows) != 1
        or rows[0].get("node") != f"provider_due_{role}"
    ):
        raise Hold("provider_guard_invocation")
    completions = [
        r
        for r in view.get("completions", [])
        if r.get("invocation_id") == observation["invocation_id"]
    ]
    if len(completions) != 1 or completions[0].get("effective_writer") != "stopped":
        raise Hold("provider_writer_unresolved")
    retained = _retained(foreground, observation["completion_ref"])
    if not isinstance(retained, dict) or any(
        completions[0].get(key) != value for key, value in retained.items()
    ):
        raise Hold("provider_completion_changed")
    transitions = [
        r
        for r in view.get("transitions", [])
        if r.get("completion_id") == observation["invocation_id"]
    ]
    if len(transitions) != 1 or transitions[0] != _retained(
        foreground, observation["transition_ref"]
    ):
        raise Hold("provider_transition_changed")
    version = observation["observed_state_version"]
    if any(
        type(move.get("version")) is not int or move["version"] > version
        for move in view.get("operator_moves", [])
    ):
        raise Hold("provider_operator_move")
    # Strict C2 events always carry a list of {path, sha256} control refs.
    refs = cast(list[dict[str, str]], event["control_refs"])
    pinned = {item["sha256"] for item in refs}
    new: list[dict[str, Any]] = []
    for receipt in view.get("controls", []):
        if digest(canonical(receipt)) in pinned:
            continue
        if (
            not isinstance(receipt, dict)
            or receipt.get("operation") not in LINEAGE_CONTROLS
            or receipt.get("status") not in {"applied", "refused"}
            or receipt.get("instance_id") != observation["instance_id"]
            or receipt.get("run_id") != observation["run_id"]
            or receipt.get("plane_id") not in {None, "foreground"}
        ):
            raise Hold("provider_unknown_control")
        new.append(receipt)
    return new


def due(foreground: Path, project: Path, role: str) -> str:
    if not provider_recovery():
        raise Hold("provider_recovery_disabled")
    from millrace_plus import provider_wait as pw
    from millrace_plus.router import control

    policy = provider_policy()[0]
    state = load(foreground)
    oid = _continuation(state, f"provider_due_{role}")
    operation = _pending(foreground, role)
    authority = operation.authority
    failure = _failure(operation)
    if failure["occurrence_id"] != oid or state["terminal"] is not None:
        raise Hold("provider_attempt_owner")
    if (
        authority["guidance_sha256"] != guidance()
        or authority["policy_sha256"] != policy["sha256"]
        or authority["service"] != _service(project)
    ):
        raise Hold("provider_authority_stale")
    observed: tuple[dict[str, object], dict[str, Any], str] | None
    try:
        observed = _observed(foreground, operation)
    except Hold as exc:
        if str(exc) != "provider_observation_required":
            raise  # an invalid retained observation holds for reconciliation
        observed = None
    if observed is None or observed[0]["diagnostic_ref"] != failure["diagnostic_ref"]:
        # Not yet observed for the latest failure: remain paused for explicit
        # operator observation, exactly as before the first observation.
        mark(state, f"provider_due_{role}", oid)
        save(foreground, state)
        return "PROVIDER_WAIT"
    event, observation, observation_id = observed
    # Bound once by the strict C2 ``observed`` event (instance/run/state home).
    locator = cast(dict[str, str], operation.tip["core_locator"])
    view = control.inspect_run(
        locator["instance_id"],
        locator["run_id"],
        state_home=Path(locator["state_home"]),
    )
    controls = lineage_controls(foreground, view, event, observation, role)
    result = pw.check_provider_due(
        foreground,
        operation_id=operation.id,
        observation_id=observation_id,
        now=_now(),
        policy=policy,
        controls=controls,
    )
    phase = result["phase"]
    if phase in {"retry_ready", "retry_reserved"} and (
        result["eligible"] or phase == "retry_reserved"
    ):
        mark(state, f"prepare_provider_retry_{role}", oid)
        outcome = "RETRY_READY"
    elif phase in {"diagnosis_required", "diagnosis_reserved"} and (
        result["eligible"] or phase == "diagnosis_reserved"
    ):
        mark(state, f"prepare_provider_diagnosis_{role}", oid)
        outcome = "DIAGNOSIS_READY"
    elif phase == "human_required":
        mark(state, f"provider_human_{role}", oid)
        outcome = "PROVIDER_HUMAN_REQUIRED"
    elif phase == "waiting":
        mark(state, f"provider_due_{role}", oid)
        outcome = "PROVIDER_WAIT"
    else:
        raise Hold("provider_phase_" + str(phase))
    save(foreground, state)
    return outcome


# --------------------------------------------------------------- preparation


def prepare_attempt(foreground: Path, role: str, kind: str) -> str:
    if not provider_recovery():
        raise Hold("provider_recovery_disabled")
    from prepare_role import prepare

    operation = _pending(foreground, role)
    if kind == "diagnosis":
        # The one diagnosis node must use the captured policy's binding key.
        policy = provider_policy()[0]
        root = Path(__file__).resolve().parent.parent
        graph = load_json(read(root, "workflow.json"))
        node = graph["planes"]["foreground"]["nodes"][f"provider_diagnosis_{role}"]
        if node["invoke"]["binding"] != policy["troubleshooter_binding"] or (
            policy["troubleshooter_binding"] not in graph["bindings"]
        ):
            raise Hold("provider_troubleshooter_binding")
    prepare(
        foreground,
        foreground,
        role if kind == "retry" else "troubleshooter",
        provider_intent={"operation_id": operation.id, "kind": kind},
    )
    return "READY"


def accept_diagnosis(foreground: Path, role: str) -> str:
    if not provider_recovery():
        raise Hold("provider_recovery_disabled")
    from accept_role import validate

    from millrace_plus import provider_wait as pw

    state = load(foreground)
    oid = _continuation(state, f"provider_diagnosis_{role}")
    operation = _pending(foreground, role)
    failure = _failure(operation)
    failed = cast(str, failure["occurrence_id"])  # strict C2 failure field
    tip = operation.tip
    if tip["kind"] not in {"diagnosis_reserved", "diagnosis_recorded"} or (
        tip["occurrence_id"] != oid
    ):
        raise Hold("provider_diagnosis_unreserved")
    occurrence = state["occurrences"][oid]
    restoration: dict[str, object] | None = None
    try:
        raw = read(foreground, f"lad-results/{oid}/result.json")
        outcome = load_json(raw)["outcome"]
        result = validate(foreground, foreground, state, occurrence, outcome)[0]
        resolved = diagnosis_repaired(outcome, result["data"], occurrence["pending"])
    except (LadError, KeyError, TypeError, ValueError):
        resolved = False  # invalid diagnosis evidence is an unresolved diagnosis
    if resolved:
        # Only an actual restoration recorded for this operation after its latest
        # failure can make a repaired diagnosis a defensible same-binding retry.
        for event in reversed(operation.events):
            # Strict C2 sequences are integers; restoration refs are refs.
            if cast(int, event["sequence"]) <= cast(int, failure["sequence"]):
                break
            if event["kind"] == "restoration_recorded":
                restoration = cast(dict[str, object], event["restoration_ref"])
                break
        resolved = restoration is not None
    pw.record_diagnosis(
        foreground,
        operation_id=operation.id,
        occurrence_id=oid,
        resolved=resolved,
        restoration_ref=restoration,
    )
    if resolved:
        mark(state, f"provider_due_{role}", failed)
        save(foreground, state)
        return "RETRY_READY"
    mark(state, f"provider_human_{role}", failed)
    save(foreground, state)
    return "PROVIDER_HUMAN_REQUIRED"


def diagnosis_repaired(outcome: object, data: object, pending: dict[str, Any]) -> bool:
    """The existing Troubleshooter repaired-prerequisite evidence, exactly.

    The diagnosis input's pending is ``{kind: execution, target, lineage,
    occurrence_id: failed owner, cause_id: operation id}``; a verified repair
    echoes all four role-contract fields. Anything else is unresolved.
    """
    return (
        outcome == "PASS_PREREQUISITE_REPAIRED"
        and isinstance(data, dict)
        and data.get("trigger_occurrence_id") == pending["occurrence_id"]
        and data.get("cause_id") == pending["cause_id"]
        and data.get("resume_role") == pending["target"]
        and data.get("repair_verified") is True
    )


def human(foreground: Path, role: str) -> str:
    if not provider_recovery():
        raise Hold("provider_recovery_disabled")
    from millrace_plus import provider_wait as pw

    state = load(foreground)
    operation = _pending(foreground, role)
    failure = _failure(operation)
    tip = operation.tip
    continuation = state["continuation"]
    if tip["kind"] == "diagnosis_reserved":
        # The diagnosis node's native error/unknown edge: record the actual failed
        # dispatch/completion; it never claims the Troubleshooter diagnosed.
        if (continuation["node"], continuation["occurrence_id"]) != (
            f"provider_diagnosis_{role}",
            tip["occurrence_id"],
        ) or tip["occurrence_id"] in state["accepted"]:
            raise Hold("provider_human_cause_missing")
        pw.record_diagnosis(
            foreground,
            operation_id=operation.id,
            occurrence_id=cast(str, tip["occurrence_id"]),  # strict C2 event
            resolved=False,
            restoration_ref=None,
        )
    elif (
        tip["kind"] == "diagnosis_recorded"
        and tip["phase"] == "human_required"
        and tip["restoration_ref"] is None
        and continuation["node"] == f"provider_diagnosis_{role}"
        and continuation["occurrence_id"] == tip["occurrence_id"]
        and tip["occurrence_id"] not in state["accepted"]
    ):
        pass  # exact re-run after this node's own failed-diagnosis record
    elif tip["phase"] != "human_required":
        raise Hold("provider_human_cause_missing")
    elif continuation["node"] not in {
        f"provider_human_{role}",
        f"provider_due_{role}",
    }:
        raise Hold("provider_continuation_not_selected")
    mark(state, f"provider_due_{role}", cast(str, failure["occurrence_id"]))
    save(foreground, state)
    return "HUMAN_REQUIRED"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("role", choices=FOREGROUND)
    args = parser.parse_args()
    foreground, project = roots()
    try:
        if args.action == "classify":
            outcome = classify(foreground, project, args.role)
        elif args.action == "due":
            outcome = due(foreground, project, args.role)
        elif args.action == "prepare-retry":
            outcome = prepare_attempt(foreground, args.role, "retry")
        elif args.action == "prepare-diagnosis":
            outcome = prepare_attempt(foreground, args.role, "diagnosis")
        elif args.action == "accept-diagnosis":
            outcome = accept_diagnosis(foreground, args.role)
        else:
            outcome = human(foreground, args.role)
    except Hold as exc:
        emit("UNCERTAIN", "Provider reconciliation required: " + str(exc))
        return
    except Exception as exc:  # every other refusal holds; nothing is dispatched
        code = getattr(exc, "code", None)
        emit(
            "UNCERTAIN",
            "Provider reconciliation required: "
            + (code if isinstance(code, str) else str(exc) or type(exc).__name__),
        )
        return
    emit(outcome, "Provider guard " + args.action + " " + args.role + ".")


if __name__ == "__main__":
    main()
