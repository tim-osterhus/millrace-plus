"""Accept exactly one immutable role result before exposing its finite route."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from boundaries import _recover, choose
from lad_state import (
    ARTIFACTS,
    ROLES,
    LadError,
    _prepare_source,
    _validate_state,
    assert_foreground,
    atomic,
    canonical,
    captured_policy,
    digest,
    emit,
    fields,
    guidance,
    history_name,
    load,
    load_json,
    mark,
    presentation_record,
    read,
    roots,
    routing_policy,
    run,
    safe,
    save,
    shared_target,
    text,
)


def validate(
    workspace: Path, foreground: Path, state: dict, occurrence: dict, expected: str
) -> tuple[dict, str, tuple[str, bytes] | None]:
    oid = occurrence["occurrence_id"]
    current_contract = occurrence["format"] == "millrace-lad-input/v2"
    raw_result = read(workspace, f"lad-results/{oid}/result.json")
    result = fields(
        load_json(raw_result),
        {
            "format",
            "session_id",
            "occurrence_id",
            "role",
            "outcome",
            "lineage",
            "input_sha256",
            "source_sha256",
            "guidance_sha256",
            "artifacts",
            "findings",
            "human_evidence",
            "data",
        }
        | ({"helper_mode", "helper_evidence"} if current_contract else set()),
    )
    for key in (
        "session_id",
        "occurrence_id",
        "role",
        "lineage",
        "input_sha256",
        "guidance_sha256",
    ):
        if result[key] != occurrence[key]:
            raise LadError("result_identity_" + key)
    if (
        result["format"]
        != ("millrace-lad-result/v2" if current_contract else "millrace-lad-result/v1")
        or result["outcome"] != expected
        or (current_contract and guidance() != occurrence["guidance_sha256"])
    ):
        raise LadError("result_contract_or_stale_guidance")
    if digest(read(foreground, "request.md")) != occurrence["request_sha256"]:
        raise LadError("original_request_changed")
    current = current_source(foreground, occurrence, current_contract=current_contract)
    if result["source_sha256"] != digest(canonical(current)):
        raise LadError("stale_source")
    artifacts = result["artifacts"]
    if (
        not isinstance(artifacts, list)
        or not artifacts
        or not isinstance(result["data"], dict)
        or not isinstance(result["findings"], list)
    ):
        raise LadError("missing_role_evidence")
    if current_contract:
        validate_execution_evidence(workspace, occurrence, result)
    names = set()
    raws = []
    engineering = _engineering() if current_contract else None
    records = (
        engineering.ROLE_RECORDS.get(occurrence["role"], set())
        if engineering is not None
        else set()
    )
    for artifact in artifacts:
        fields(artifact, {"path", "sha256"})
        name = text(artifact["path"])
        if name in names or (
            not name.endswith(".md")
            and name != "baseline-freeze.json"
            and name not in records
        ):
            raise LadError("duplicate_or_nonmarkdown_artifact")
        names.add(name)
        raw = read(workspace, f"lad-results/{oid}/" + name)
        # Raw bytes are authority: a digest mismatch is integrity, not format.
        if digest(raw) != artifact["sha256"]:
            raise LadError("incomplete_or_wrong_occurrence_artifact")
        if not current_contract:
            # Retained v1 occurrences keep their original literal interpretation.
            content = raw.decode("utf-8")
            if name != "baseline-freeze.json" and (
                oid not in content
                or len(content.strip()) < 100
                or "## Evidence" not in content
                or "## Outcome" not in content
            ):
                raise LadError("incomplete_or_wrong_occurrence_artifact")
        raws.append((name, raw))
    presentation = None
    if current_contract:
        # The strict current policy must be the occurrence's captured policy.
        if routing_policy()["sha256"] != captured_policy(occurrence)["sha256"]:
            raise LadError("routing_policy_capture_changed")
        presentation = presentation_record(
            workspace, occurrence, result, raw_result, raws
        )
    required = set(ARTIFACTS[occurrence["role"]])
    if expected in {"ERROR", "HUMAN_BLOCKED", "ERROR_REPAIRABLE", "ERROR_REPLACEMENT"}:
        required.discard("baseline.md")
    if not required <= names:
        raise LadError("missing_required_artifact")
    ids = set()
    for finding in result["findings"]:
        fields(finding, {"id", "criterion", "path", "reproduction", "evidence"})
        if finding["id"] in ids or finding["criterion"] not in {
            c["id"] for c in state["criteria"]
        }:
            raise LadError("finding_identity")
        ids.add(finding["id"])
        for value in finding.values():
            text(value)
        if (
            occurrence["role"] in {"checker", "doublechecker"}
            and finding["criterion"] not in occurrence["packet"]["criteria"]
        ):
            raise LadError("finding_outside_packet_criteria")
    if expected == "FINDINGS_ACTIONABLE" and not ids:
        raise LadError("actionable_findings_required")
    if expected == "PASS" and result["findings"]:
        raise LadError("pass_with_unresolved_findings")
    if expected == "HUMAN_BLOCKED" or expected == "NO_SAFE_ROUTE":
        human = fields(
            result["human_evidence"],
            {"type", "obstacle", "attempted", "required_resolution"},
        )
        if human["type"] not in {
            "unavailable_input",
            "human_choice",
            "protected_work",
            "budget_exhausted",
            "diagnosis_exhausted",
        }:
            raise LadError("human_dependence_not_established")
        for value in human.values():
            text(value)
    elif result["human_evidence"] is not None:
        raise LadError("unexpected_human_evidence")
    if expected.startswith("ERROR") or expected == "ERROR":
        failure = fields(
            result["data"].get("failure"),
            {"operation", "attempted", "evidence", "prerequisite_resolved"},
        )
        for key in ("operation", "attempted", "evidence"):
            text(failure[key])
        if type(failure["prerequisite_resolved"]) is not bool:
            raise LadError("invalid_failure_evidence")
        if (
            expected in {"ERROR_REPLACEMENT", "ERROR_TRANSIENT_RESOLVED"}
            and not failure["prerequisite_resolved"]
        ):
            raise LadError("unresolved_replacement_prerequisite")
    if engineering is not None:
        # Captured deterministic obligations; check evidence never comes from prose.
        engineering.role_gate(foreground, state, occurrence, result, dict(raws))
    return result, digest(canonical(result)), presentation


def current_source(
    foreground: Path, occurrence: dict, *, current_contract: bool = True
) -> dict:
    if current_contract:
        try:
            selected_target = shared_target(foreground)
        except LadError as exc:
            raise LadError("shared_target_" + str(exc)) from exc
        current, context = _prepare_source(foreground, selected_target)
        if occurrence["source_context"]["outside"] != context["outside"]:
            raise LadError("out_of_scope_source_change")
        previous = occurrence["source_context"]["git"]
        observed = context["git"]
        if (previous is None) != (observed is None) or (
            previous is not None
            and any(previous[key] != observed[key] for key in ("base", "index"))
        ):
            raise LadError("source_git_base_or_index_changed")
    else:
        from session_compat import source_v1

        current = source_v1(foreground)
    writable = occurrence["role"] in {"builder", "fixer", "troubleshooter", "updater"}
    paths = occurrence["packet"]["paths"] if occurrence["packet"] and writable else []
    changed = {
        name
        for name in current.keys() | occurrence["source_files"].keys()
        if current.get(name) != occurrence["source_files"].get(name)
    }
    if any(
        current.get(name, "").startswith("symlink:")
        or occurrence["source_files"].get(name, "").startswith("symlink:")
        for name in changed
    ):
        raise LadError("source_link_change_requires_reconciliation")
    if any(
        not any(
            name == allowed or name.startswith(allowed.rstrip("/") + "/")
            for allowed in paths
        )
        for name in changed
    ):
        raise LadError("out_of_scope_source_change")
    if current_contract:
        try:
            observed_target = shared_target(foreground)
        except LadError as exc:
            raise LadError("shared_target_" + str(exc)) from exc
        if observed_target != selected_target:
            raise LadError("shared_target_changed")
        engineering = _engineering()
        if engineering is not None:
            # Declared product paths are excluded from source identity, so every
            # non-check write to them is checked separately and holds.
            engineering.products_unchanged(foreground, occurrence)
    return current


def _engineering():
    """The engineering module only for an explicitly captured profile."""
    from lad_state import engineering_policy

    if engineering_policy() is None:
        return None
    import engineering_feedback

    return engineering_feedback


def guard_baseline(foreground: Path, role: str, *, preparing: bool = False) -> bool:
    """Restore only a pinned baseline projection under the graph's role fence.

    The caller owns controller/helper joins and excludes other domain writers.
    Core's command/writer barriers supply the normal graph entry fence; this
    function cannot attest closure of arbitrary external writers.
    """
    state = load_json(read(foreground, "lad-state/session.json"))
    if state.get("format") != "millrace-lad-session/v2":
        return False
    current = state["occurrences"].get(state["active"].get("foreground"))
    if not current or current["lineage"] not in state["baselines"]:
        return False
    baseline = state["baselines"][current["lineage"]]
    if "parent_pin" not in baseline:
        return False  # Historical evidence has no invented backup authority.
    owner = current["occurrence_id"]
    record_path = f"lad-state/baseline-pins/{owner}-restoration.json"
    record_exists = safe(foreground, record_path, missing=True).exists()
    try:
        observed = read(foreground, baseline["path"])
    except LadError as exc:
        if str(exc) != "missing_file":
            raise
        observed = None
    changed = observed is None or digest(observed) != baseline["sha256"]
    if not changed and not record_exists:
        return False
    assert_foreground(state)
    if not preparing and role != current["role"]:
        raise LadError("baseline_owner_mismatch")
    if (
        preparing
        and not record_exists
        and state["continuation"]["node"]
        not in {"prepare_" + role, "prepare_" + role + "_replacement"}
    ):
        raise LadError("baseline_owner_mismatch")
    if current["format"] != "millrace-lad-input/v2" or (
        current["helper_mode"] != "direct"
    ):
        raise LadError("baseline_writer_reconciliation_required")
    current_source(foreground, current)
    pin = baseline["parent_pin"]
    pin_raw = read(foreground, pin["path"])
    expected = {key: value for key, value in baseline.items() if key != "parent_pin"}
    if digest(pin_raw) != pin["sha256"] or load_json(pin_raw) != expected:
        raise LadError("baseline_parent_pin_changed")
    checker = state["occurrences"].get(baseline["occurrence_id"])
    accepted = state["accepted"].get(baseline["occurrence_id"])
    if (
        not checker
        or checker["role"] != "checker"
        or not accepted
        or checker["lineage"] != current["lineage"]
    ):
        raise LadError("baseline_owner_mismatch")
    freeze_ref = fields(
        accepted["result"]["data"].get("baseline_freeze"), {"path", "sha256"}
    )
    freeze_raw = read(
        foreground, f"lad-results/{checker['occurrence_id']}/" + freeze_ref["path"]
    )
    if digest(freeze_raw) != freeze_ref["sha256"] or load_json(freeze_raw) != {
        "format": "millrace-lad-baseline-freeze/v1",
        "occurrence_id": checker["occurrence_id"],
        "criteria": baseline["criteria"],
        "path": baseline["path"],
        "sha256": baseline["sha256"],
    }:
        raise LadError("baseline_freeze_mismatch")
    backup_path = "lad-state/baseline-pins/" + baseline["occurrence_id"] + ".md"
    backup = read(foreground, backup_path)
    if digest(backup) != baseline["sha256"]:
        raise LadError("baseline_backup_changed")
    # Validate every other accepted byte and the original freeze/result identity
    # BEFORE preserving an observation or changing the damaged projection.
    _validate_state(foreground, state, baseline_projection=(baseline["path"], backup))
    if record_exists:
        record = load_json(read(foreground, record_path))
        fields(
            record,
            {"format", "session_id", "owner", "input_sha256", "baseline", "observed"},
        )
        if any(
            record[key] != value
            for key, value in {
                "format": "millrace-lad-baseline-restoration/v1",
                "session_id": state["session_id"],
                "owner": owner,
                "input_sha256": current["input_sha256"],
                "baseline": baseline,
            }.items()
        ):
            raise LadError("baseline_restoration_conflict")
        bad_ref = record["observed"]
        if bad_ref is not None:
            if digest(read(foreground, bad_ref["path"])) != bad_ref["sha256"]:
                raise LadError("baseline_observation_changed")
        if changed and (
            (observed is None) != (bad_ref is None)
            or (observed is not None and digest(observed) != bad_ref["sha256"])
        ):
            raise LadError("baseline_restoration_conflict")
    else:
        bad_ref = None
        if observed is not None:
            bad_path = f"lad-state/baseline-pins/{owner}-rejected.md"
            atomic(foreground, bad_path, observed, immutable=True)
            bad_ref = {"path": bad_path, "sha256": digest(observed)}
        record = {
            "format": "millrace-lad-baseline-restoration/v1",
            "session_id": state["session_id"],
            "owner": owner,
            "input_sha256": current["input_sha256"],
            "baseline": baseline,
            "observed": bad_ref,
        }
        atomic(foreground, record_path, canonical(record) + b"\n", immutable=True)
    if changed:
        atomic(foreground, baseline["path"], backup)
    load(foreground)  # Ordinary strict validation remains the publication check.
    cause = digest(canonical(record))
    pending = {
        "kind": "baseline_tamper",
        "target": "checker",
        "lineage": current["lineage"],
        "occurrence_id": owner,
        "cause_id": cause,
        "restoration": {
            "path": record_path,
            "sha256": digest(canonical(record) + b"\n"),
        },
    }
    if state["pending"] == pending and preparing and role == "consultant":
        return False
    state["pending"] = pending
    state["reviews"].pop(current["lineage"], None)
    mark(state, "prepare_consultant", owner)
    save(foreground, state)
    return True


def accept(foreground: Path, workspace: Path, role: str, expected: str) -> str:
    state = load(foreground)
    plane = "learning" if role == "learning" else "foreground"
    if plane == "foreground":
        assert_foreground(state)
    oid = state["active"].get(plane)
    occurrence = state["occurrences"].get(oid)
    if not occurrence or occurrence["role"] != role:
        raise LadError("unknown_or_wrong_role_occurrence")
    result, checksum, presentation = validate(
        workspace, foreground, state, occurrence, expected
    )
    existing = state["accepted"].get(oid)
    if existing:
        if existing["sha256"] != checksum or existing["result"] != result:
            raise LadError("conflicting_accepted_result")
        if presentation is not None:
            try:
                retained = read(workspace, presentation[0])
            except LadError:
                raise LadError("accepted_presentation_missing") from None
            if retained != presentation[1]:
                raise LadError("accepted_presentation_conflict")
        # Exact accepted replay repairs a crash after save, without native rerun.
        reconcile_provider_success(foreground, state, occurrence)
        return existing["route"]
    if plane == "learning" and (
        state["continuation"]["node"] != "learning"
        or state["continuation"]["occurrence_id"] != oid
    ):
        raise LadError("learning_assessment_not_selected")
    if (
        state["pending"]
        and state["pending"]["kind"] == "invalid_result"
        and (state["pending"]["occurrence_id"] == oid)
    ):
        state["pending"] = None
    route = choose(state, occurrence, result, foreground)
    if presentation is not None:
        # Immutable and idempotent; a conflicting existing record refuses.
        atomic(workspace, *presentation, immutable=True)
    state["accepted"][oid] = {"sha256": checksum, "result": result, "route": route}
    mark(
        state,
        "prepare_" + route.lower()
        if not route.startswith("RESUME_")
        else "prepare_" + route[7:].lower() + "_replacement",
        oid,
    )
    if route in {"ASSESS", "CLOSE", "ACK", "PAUSE"}:
        mark(
            state,
            {
                "ASSESS": "emit_learning_request",
                "CLOSE": "emit_learning_request",
                "ACK": "emit_ack",
                "PAUSE": "human_pause",
            }[route],
            oid,
        )
    if route.startswith("RESUME_ENGINEERING_"):
        # The repaired prerequisite returns to the exact saved phase entry; the
        # phase then opens a fresh attempt. Its pending record is consumed.
        pending = state["pending"]
        if not pending or pending.get("kind") != "engineering_prerequisite":
            raise LadError("exact_saved_role_required")
        state["continuation"] = dict(pending["resume_continuation"])
        state["pending"] = pending["resume_pending"]
    if route == "REPLACE_CONSULTANT":
        mark(state, "prepare_consultant_replacement", oid)
    if route == "REPLACE_ARBITER":
        mark(state, "prepare_arbiter_replacement", oid)
    save(foreground, state)
    reconcile_provider_success(foreground, state, occurrence)
    return route


def reconcile_provider_success(
    foreground: Path, state: dict[str, Any], occurrence: dict[str, Any]
) -> None:
    """End a pending same-operation provider streak after durable acceptance.

    Runs only for routing-policy/v2 foreground input-v2 occurrences. It matches
    the operation's role, packet lineage, saved pending route and binding, the
    original captured source, the exact provider retry preparation or existing
    ordinary replacement chain, and this occurrence's own successful native
    export. Diagnosis, Learning and unrelated success, or a success without its
    export, leave the operation unresolved. Conflicts refuse with a distinct
    ``provider_success_*`` code (the existing REFUSED reconciliation route), never
    an invalid-result replacement. The returned semantic route is unchanged.
    """
    oid = occurrence["occurrence_id"]
    if (
        occurrence["role"] == "learning"
        or occurrence["format"] != "millrace-lad-input/v2"
        or oid not in state["accepted"]
    ):
        return
    from lad_state import provider_recovery

    if not provider_recovery():
        return
    import provider_wait as helper

    try:
        from millrace_plus import provider_wait as pw
        from millrace_plus.router import native_diagnostic as nd
    except ImportError as exc:
        raise LadError("provider_success_requires_plus") from exc
    try:
        operation = pw.pending_operation(
            foreground, role=occurrence["role"], recover=True
        )
        if operation is None:
            return
        lineage = helper.attempt_lineage(foreground, state, operation, oid)
        if lineage is None:
            return  # unrelated or diagnosis success: the streak stays pending
        published = helper.exports(foreground, oid)
        if not published:
            return  # missing verified success evidence: unresolved, route proceeds
        if len(published) != 1:
            raise LadError("provider_success_conflict")
        ref, raw = published[0]
        record, facts = helper._record(ref, raw, oid, occurrence)
        if not isinstance(record, nd.NativeDiagnosticV2):
            raise LadError("provider_success_conflict")
        _, value = lineage
        if isinstance(value, list):
            head = state["occurrences"][value[0]]
            evidence: dict[str, object] = {"kind": "replacement", "chain": value}
        else:
            head = occurrence
            evidence = {"kind": "retry", "preparation_ref": value}
        original = state["occurrences"].get(operation.authority["original_occurrence"])
        if original is None:
            raise LadError("provider_success_conflict")
        pw.reconcile_provider_success(
            foreground,
            operation_id=operation.id,
            success={
                "role": occurrence["role"],
                "packet_lineage": helper.packet_lineage(occurrence),
                "pending_route": helper.pending_route(head),
                "binding": helper._authority_facts(facts),
                "source_sha256": original["source_sha256"],
                "current_source_sha256": occurrence["source_sha256"],
                "occurrence_id": oid,
                "input_sha256": occurrence["input_sha256"],
                "lineage": evidence,
                "native_owner": {key: facts[key] for key in helper.OWNER},
                "transport": facts["transport"],
                "writer_state": facts["writer_state"],
                "accepted": oid in state["accepted"],
            },
        )
    except (helper.Hold, LadError, pw.ProviderWaitError, KeyError, TypeError) as exc:
        # Never a recoverable invalid-result code: accepted bytes are preserved.
        code = getattr(exc, "code", None) or str(exc) or type(exc).__name__
        raise LadError(
            code if code.startswith("provider_success_") else "provider_success_" + code
        ) from None


def restore_history(workspace: Path, occurrence: dict) -> bool:
    if occurrence["format"] != "millrace-lad-input/v2":
        return False
    oid = occurrence["occurrence_id"]
    relative = f"lad-results/{oid}/result.json"
    try:
        raw = read(workspace, relative)
    except LadError as exc:
        if str(exc) == "missing_file":
            return False
        raise
    result = load_json(raw)
    if result.get("helper_mode") != occurrence["helper_mode"]:
        return False
    if result["helper_mode"] == "native_helpers":
        validate_helpers(workspace, occurrence, result)
    elif result.get("helper_evidence") is not None:
        return False
    name = "historylog/" + history_name(occurrence)
    file = workspace / f"lad-results/{oid}/" / name
    if file.exists() or file.is_symlink():
        return False
    # This is factual bookkeeping from retained bytes, never regenerated checks.
    record = f"lad-state/rejections/{oid}/missing-history-{digest(raw)}.json"
    atomic(workspace, record, raw, immutable=True)
    summary = (
        f"# {occurrence['role']} retained summary\n"
        f"Session: {occurrence['session_id']}\n"
        f"Occurrence: {oid}\n## Evidence\nRetained result SHA-256: {digest(raw)}. "
        "Restored missing bookkeeping only; no native task or check was rerun.\n"
        f"## Outcome\n{result['outcome']}; "
        "original role artifacts still require validation.\n"
    ).encode()
    atomic(workspace, f"lad-results/{oid}/" + name, summary, immutable=True)
    artifacts = [row for row in result["artifacts"] if row.get("path") != name]
    artifacts.append({"path": name, "sha256": digest(summary)})
    result["artifacts"] = artifacts
    atomic(workspace, relative, canonical(result) + b"\n")
    return True


def record_invalid(foreground: Path, workspace: Path, role: str, code: str) -> str:
    recoverable = (
        code.startswith(
            (
                "result_identity_",
                "result_contract_",
                "historylog_",
                "missing_role_",
                "missing_required_",
                "incomplete_or_wrong_",
                "duplicate_or_nonmarkdown_",
                "finding_",
                "pass_with_",
                "actionable_",
                "invalid_failure_",
                "presentation_",
                "packet_development_check_",
            )
        )
        or code
        in {
            "missing_file",
            "JSONDecodeError",
            "ValueError",
            "KeyError",
            "TypeError",
            "UnicodeDecodeError",
            "invalid_fields",
            "object_required",
            "nonfinite_json",
            "duplicate_json_key",
            "stale_source",
        }
        or (
            role == "learning"
            and code
            in {
                # Invalid assessment evidence is a failed assessment, never a hold.
                "learning_changed_pending_route",
                "unresolved_replacement_prerequisite",
            }
        )
    )
    if not recoverable:
        raise LadError(code)
    state = load(foreground)
    plane = "learning" if role == "learning" else "foreground"
    if plane == "foreground":
        assert_foreground(state)
    oid = state["active"].get(plane)
    occurrence = state["occurrences"].get(oid)
    if occurrence is None or occurrence["role"] != role or oid in state["accepted"]:
        raise LadError("invalid_result_owner")
    if occurrence["format"] != "millrace-lad-input/v2":
        raise LadError("legacy_result_needs_original_contract")
    if guidance() != occurrence["guidance_sha256"] or (
        digest(read(foreground, "request.md")) != occurrence["request_sha256"]
    ):
        raise LadError("invalid_result_authority_changed")
    current = current_source(foreground, occurrence)
    last = next(
        (
            row["id"]
            for row in reversed(state["events"])
            if row["kind"] == "role" and row["id"] in state["accepted"]
        ),
        None,
    )
    fingerprint = digest(
        canonical(
            {
                "role": role,
                "diagnostic": code,
                "source": digest(canonical(current)),
                "guidance": occurrence["guidance_sha256"],
            }
        )
    )
    cause = digest(
        canonical({"role": role, "diagnostic": code, "lineage": occurrence["lineage"]})
    )
    repaired_cause = any(
        state["occurrences"][old_id]["role"] == "troubleshooter"
        and accepted["result"]["data"].get("cause_id") == cause
        and accepted["result"]["data"].get("repair_verified") is True
        for old_id, accepted in state["accepted"].items()
    )
    repeated = any(
        old_id != oid and intent.get("invalid_fingerprint") == fingerprint
        for old_id, intent in state["replacement_intents"].items()
    )
    decision = "INVALID_RESULT"
    if role == "learning":
        # One assessment only: a rejected result selects the failed-disposition
        # helper for this exact occurrence and creates no replacement authority.
        if state["continuation"]["occurrence_id"] != oid or state["continuation"][
            "node"
        ] not in {"learning", "record_learning_failure"}:
            raise LadError("learning_rejection_not_selected")
    elif repeated or repaired_cause:
        route = _recover(state, occurrence, kind="invalid_result", cause=cause)
        decision = (
            "INVALID_CONSULT"
            if route == "CONSULTANT" or role == "troubleshooter"
            else "INVALID_REPEAT"
        )
        if role == "consultant":
            decision = "INVALID_HOLD"
    relative = f"lad-results/{oid}/result.json"
    try:
        rejected = digest(read(workspace, relative))
    except LadError as exc:
        if str(exc) != "missing_file":
            raise
        rejected = None
    intent = {
        "old_occurrence_id": oid,
        "role": role,
        "reason": "invalid_semantic_result",
        "diagnostic": code,
        "result_path": relative,
        "result_sha256": rejected,
        "invalid_fingerprint": fingerprint,
        "decision": decision,
        "source_sha256": digest(canonical(current)),
        "guidance_sha256": guidance(),
        "last_accepted_occurrence": last,
    }
    if state["replacement_intents"].get(oid) == intent:
        return decision
    if role == "learning" and oid in state["replacement_intents"]:
        raise LadError("learning_rejection_conflict")
    record = f"lad-state/rejections/{oid}/{digest(canonical(intent))}.json"
    atomic(foreground, record, canonical(intent) + b"\n", immutable=True)
    state["replacement_intents"][oid] = intent
    if role != "learning":
        state["pending"] = {
            "kind": "invalid_result",
            "target": role,
            "lineage": occurrence["lineage"],
            "occurrence_id": oid,
            "cause_id": cause,
        }
    target = (
        "prepare_consultant"
        if decision == "INVALID_CONSULT"
        else "prepare_troubleshooter"
        if decision == "INVALID_REPEAT"
        else "human_pause"
        if decision == "INVALID_HOLD"
        else "record_learning_failure"
        if role == "learning"
        else "prepare_" + role + "_replacement"
    )
    mark(state, target, oid)
    save(foreground, state)
    # Core's selected graph and writer barriers alone permit the next invocation.
    # This record supplies no writer/permission override and never dispatches.
    return decision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("outcome")
    args = parser.parse_args()
    if args.outcome not in ROLES[args.role]:
        raise LadError("undeclared_role_outcome")
    foreground, _ = roots()
    emit(
        accept_checked(foreground, foreground, args.role, args.outcome),
        "Deterministic role evidence/invalid-result observation.",
    )


def accept_checked(foreground: Path, workspace: Path, role: str, expected: str) -> str:
    if workspace == foreground and guard_baseline(foreground, role):
        return "BASELINE_TAMPER"
    try:
        return accept(foreground, workspace, role, expected)
    except (LadError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        code = str(exc) if isinstance(exc, LadError) else type(exc).__name__
        if code.startswith("shared_target_"):
            raise
        state = load(foreground)
        plane = "learning" if role == "learning" else "foreground"
        occurrence = state["occurrences"].get(state["active"].get(plane))
        if (code.startswith("historylog_") or code == "missing_file") and occurrence:
            if restore_history(workspace, occurrence):
                return accept(foreground, workspace, role, expected)
        return record_invalid(foreground, workspace, role, code)


def validate_execution_evidence(
    workspace: Path, occurrence: dict, result: dict
) -> None:
    if result["helper_mode"] != occurrence["helper_mode"]:
        raise LadError("helper_mode_changed")
    if occurrence["helper_mode"] == "direct":
        if result["helper_evidence"] is not None:
            raise LadError("direct_helper_evidence")
    else:
        try:
            qualification, _ = helper_qualification(
                workspace, occurrence["helper_qualification"]
            )
        except Exception as exc:
            raise LadError("helper_closure_unresolved") from exc
        if qualification["scope"] == "offline_validator_fixture":
            raise LadError("native_helpers_not_qualified")
        try:
            validate_helpers(workspace, occurrence, result)
        except Exception as exc:
            raise LadError("helper_closure_unresolved") from exc
    expected = "historylog/" + history_name(occurrence)
    history = next(
        (row for row in result["artifacts"] if row.get("path") == expected), None
    )
    if history is None:
        raise LadError("historylog_identity")
    fields(history, {"path", "sha256"})
    raw = read(workspace, f"lad-results/{occurrence['occurrence_id']}/" + expected)
    content = raw.decode("utf-8", "strict")
    if (
        len(raw) >= 5120
        or digest(raw) != history["sha256"]
        or any(
            identity not in content
            for identity in (
                occurrence["occurrence_id"],
                occurrence["session_id"],
                occurrence["role"],
            )
        )
    ):
        raise LadError("historylog_identity_or_size")


def referenced(root: Path, value: object) -> tuple[dict, bytes]:
    ref = fields(value, {"path", "sha256"})
    raw = read(root, text(ref["path"]))
    if digest(raw) != ref["sha256"]:
        raise LadError("helper_reference_digest")
    return ref, raw


def helper_qualification(workspace: Path, ref: dict) -> tuple[dict, dict]:
    _, raw = referenced(workspace, ref)
    qualification = fields(
        load_json(raw),
        {
            "format",
            "scope",
            "binding",
            "facility_version",
            "parent_task_id",
            "inventory_ref",
        },
    )
    if qualification["format"] != "millrace-lad-helper-qualification/v1" or (
        qualification["scope"] != "offline_validator_fixture"
    ):
        raise LadError("native_helpers_not_qualified")
    for key in ("binding", "facility_version", "parent_task_id"):
        text(qualification[key])
    _, inventory_raw = referenced(workspace, qualification["inventory_ref"])
    inventory = fields(
        load_json(inventory_raw),
        {
            "format",
            "binding",
            "facility_version",
            "parent_task_id",
            "complete",
            "tasks",
        },
    )
    if (
        inventory["format"] != "millrace-lad-facility-inventory/v1"
        or (inventory["complete"] is not True)
        or any(
            inventory[key] != qualification[key]
            for key in ("binding", "facility_version", "parent_task_id")
        )
        or not isinstance(inventory["tasks"], list)
    ):
        raise LadError("helper_inventory_authority")
    return qualification, inventory


def validate_helpers(workspace: Path, occurrence: dict, result: dict) -> None:
    if occurrence["role"] == "planner":
        raise LadError("planner_stays_direct")
    qualification, inventory = helper_qualification(
        workspace, occurrence["helper_qualification"]
    )
    _, raw = referenced(workspace, result["helper_evidence"])
    evidence = fields(
        load_json(raw),
        {
            "format",
            "occurrence_id",
            "parent_task_id",
            "binding",
            "facility_version",
            "inventory_ref",
            "tasks",
            "integration_ref",
        },
    )
    if (
        evidence["format"] != "millrace-lad-helper-evidence/v1"
        or (evidence["occurrence_id"] != occurrence["occurrence_id"])
        or any(
            evidence[key] != qualification[key]
            for key in (
                "binding",
                "facility_version",
                "parent_task_id",
                "inventory_ref",
            )
        )
        or evidence["tasks"] != inventory["tasks"]
    ):
        raise LadError("helper_inventory_mismatch")
    tasks = evidence["tasks"]
    for row in tasks:
        if not isinstance(row, dict):
            raise LadError("helper_task_identity")
        text(row.get("task_id"))
    ids = [row["task_id"] for row in tasks]
    parent = qualification["parent_task_id"]
    if len(set(ids)) != len(ids) or parent in ids:
        raise LadError("helper_task_identity")
    indexed = {row["task_id"]: row for row in tasks}
    areas = []
    ancestry = {}
    for task in tasks:
        fields(
            task,
            {
                "task_id",
                "parent_task_id",
                "role",
                "work_area",
                "model",
                "effort",
                "result_ref",
                "terminal_state",
                "terminal_observation_ref",
            },
        )
        for key in ("task_id", "parent_task_id", "role", "work_area"):
            text(task[key])
        for key in ("model", "effort"):
            if task[key] is not None:
                text(task[key])
        ancestors = set()
        ancestor = task["parent_task_id"]
        while ancestor != parent:
            if (
                ancestor not in indexed
                or ancestor in ancestors
                or ancestor == task["task_id"]
            ):
                raise LadError("helper_parent_chain")
            ancestors.add(ancestor)
            ancestor = indexed[ancestor]["parent_task_id"]
        ancestry[task["task_id"]] = ancestors
        area = safe(workspace, task["work_area"], missing=True)
        for previous_id, previous in areas:
            if (
                area == previous
                or (previous in area.parents and previous_id not in ancestors)
                or (
                    area in previous.parents
                    and task["task_id"] not in ancestry[previous_id]
                )
            ):
                raise LadError("helper_work_area_overlap")
        areas.append((task["task_id"], area))
        if task["terminal_state"] not in {"completed", "failed", "cancelled"}:
            raise LadError("helper_terminal_unknown")
        result_ref, _ = referenced(workspace, task["result_ref"])
        if not result_ref["path"].startswith(task["work_area"].rstrip("/") + "/"):
            raise LadError("helper_result_ownership")
        _, terminal_raw = referenced(workspace, task["terminal_observation_ref"])
        terminal = fields(
            load_json(terminal_raw),
            {
                "format",
                "binding",
                "facility_version",
                "task_id",
                "parent_task_id",
                "terminal_state",
                "result_ref",
                "collected",
                "started_at",
                "historylog_ref",
            },
        )
        if (
            terminal["format"] != "millrace-lad-facility-terminal/v1"
            or (terminal["collected"] is not True)
            or any(
                terminal[key] != task[key]
                for key in ("task_id", "parent_task_id", "terminal_state", "result_ref")
            )
            or any(
                terminal[key] != qualification[key]
                for key in ("binding", "facility_version")
            )
        ):
            raise LadError("helper_terminal_conflict")
        history_ref, history_raw = referenced(workspace, terminal["historylog_ref"])
        expected_name = history_name(
            {"role": occurrence["role"], "created_at": terminal["started_at"]}
        )
        expected_path = task["work_area"].rstrip("/") + "/historylog/" + expected_name
        if history_ref["path"] != expected_path:
            raise LadError("helper_historylog_identity")
        content = history_raw.decode("utf-8", "strict")
        if len(history_raw) >= 5120 or any(
            value not in content
            for value in (task["task_id"], task["parent_task_id"], task["role"])
        ):
            raise LadError("helper_historylog_identity_or_size")
    _, integrated = referenced(workspace, evidence["integration_ref"])
    summary = integrated.decode("utf-8", "strict")
    if any(
        value not in summary for value in [occurrence["occurrence_id"], parent, *ids]
    ):
        raise LadError("helper_integration_incomplete")


if __name__ == "__main__":
    run(main)
