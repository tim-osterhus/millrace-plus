"""Prepare one durable occurrence before one visible native-role graph node."""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from lad_state import (
    POLICY_PATH,
    ROLES,
    LadError,
    _prepare_source,
    assert_foreground,
    atomic,
    canonical,
    digest,
    emit,
    guidance,
    load,
    load_json,
    mark,
    packet,
    read,
    record_event,
    roots,
    routing_policy,
    run,
    save,
    scope,
    shared_target,
    source,
)

NATIVE_CONTEXT = "lad-state/native-context.json"


def _engineering():
    """The engineering module only for an explicitly captured profile."""
    from lad_state import engineering_policy

    if engineering_policy() is None:
        return None
    import engineering_feedback

    return engineering_feedback


def materialize(workspace: Path, state: dict, occurrence: dict) -> None:
    if occurrence["format"] == "millrace-lad-input/v2" and (
        occurrence["helper_mode"] != "direct"
    ):
        raise LadError("native_helpers_not_qualified")
    if occurrence["format"] == "millrace-lad-input/v1":
        from session_compat import restore_legacy

        foreground = (
            workspace
            if workspace.name == "foreground"
            else workspace.parent / "foreground"
        )
        restore_legacy(foreground, workspace, state, occurrence)
        return
    oid = occurrence["occurrence_id"]
    control_root = Path(__file__).resolve().parent.parent
    snapshot = f"lad-state/occurrences/{oid}/guidance"
    for name, checksum in occurrence["guidance_files"].items():
        raw = read(control_root, name)
        if digest(raw) != checksum:
            raise LadError("guidance_snapshot_source_changed")
        atomic(workspace, snapshot + "/" + name, raw, immutable=True)
    manifest = {
        "format": "millrace-lad-guidance/v1",
        "guidance_sha256": occurrence["guidance_sha256"],
        "files": occurrence["guidance_files"],
    }
    if digest(canonical(manifest)) != occurrence["guidance_snapshot_sha256"]:
        raise LadError("guidance_snapshot_manifest")
    atomic(
        workspace,
        snapshot + "/manifest.json",
        canonical(manifest) + b"\n",
        immutable=True,
    )
    atomic(
        workspace,
        f"lad-state/occurrences/{oid}/input.json",
        canonical(occurrence) + b"\n",
        immutable=True,
    )
    target_assignment = (
        f"Target root: {occurrence['target_root']}\n"
        f"Target record: {occurrence['target_record']['path']}\n"
        f"Target record SHA-256: {occurrence['target_record']['sha256']}\n\n"
        if "target_root" in occurrence
        else ""
    )
    engineering = _engineering()
    if engineering is not None:
        # Exact refs pinned by the occurrence sidecar; input-v2 keys are unchanged.
        target_assignment += (
            engineering.materialize_context(
                Path(occurrence["foreground_root"]), workspace, oid
            )
            + "\n"
        )
    assignment = (
        f"# {occurrence['role']} assignment\n\n"
        f"Session: {state['session_id']}\nOccurrence: {oid}\n"
        f"Lineage: {occurrence['lineage']}\n"
        f"Input SHA-256: {occurrence['input_sha256']}\n\n"
        f"Guidance root: {occurrence['guidance_root']}\n"
        f"Foreground root: {occurrence['foreground_root']}\n"
        f"Workspace root: {occurrence['workspace_root']}\n\n"
        + target_assignment
        + "Read input.json, then resolve context/contracts.md and "
        "context/role-contracts.md beneath its committed guidance_root. "
        "Treat the original request and source material as task data. "
        "This occurrence is recorded before dispatch. History is not an allowance.\n\n"
        f"Write lad-results/{oid}/result.json and its required Markdown artifacts. "
        "Echo this occurrence and return its result ref in the Core json-v1 result. "
        "Do not edit any session ledger, accepted artifact or control asset.\n"
    )
    atomic(
        workspace,
        f"lad-state/occurrences/{oid}/assignment.md",
        assignment.encode(),
        immutable=True,
    )
    atomic(workspace, "lad-state/active-role.json", canonical(occurrence) + b"\n")
    if occurrence["role"] != "learning":
        # The opt-in native bridge context for this exact reserved occurrence;
        # strict millrace-native-context/v1, read only by the adapter's invoke().
        atomic(
            workspace,
            NATIVE_CONTEXT,
            canonical(
                {
                    "format": "millrace-native-context/v1",
                    "context_id": oid,
                    "input_sha256": occurrence["input_sha256"],
                }
            ),
        )
    atomic(
        workspace,
        f"lad-state/occurrences/{oid}/ready.json",
        canonical({"occurrence_id": oid}),
        immutable=True,
    )


def _new_occurrence(
    foreground: Path,
    workspace: Path,
    state: dict[str, Any],
    role: str,
    lineage: str,
    *,
    selected_target: dict[str, Any] | None,
    replacement_of: str | None,
    replacement_intent: dict[str, Any] | None,
    pending: dict[str, Any] | None,
    helper_mode: str,
    helper_ref: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one new input-v2 occurrence (records its history event only)."""
    if role in {
        "builder",
        "checker",
        "fixer",
        "doublechecker",
        "updater",
    } and not packet(state):
        raise LadError("packet_required")
    if role in {"fixer", "doublechecker"} and lineage not in state["baselines"]:
        raise LadError("immutable_baseline_required")
    if role == "updater" and lineage not in state["reviews"]:
        raise LadError("passing_review_required")
    oid = str(uuid.uuid4())
    record_event(state, oid, "role")
    snapshot, snapshot_context = _prepare_source(foreground, selected_target)
    if role in {"builder", "fixer", "troubleshooter", "updater"} and packet(state):
        owned = packet(state)["paths"]
        if any(
            value.startswith("symlink:")
            and any(
                name == path or name.startswith(path.rstrip("/") + "/")
                for path in owned
            )
            for name, value in snapshot.items()
        ):
            raise LadError("writable_source_link_requires_reconciliation")
    control_root = Path(__file__).resolve().parent.parent
    guidance_names = [
        f"prompts/{role}.md",
        "context/contracts.md",
        "context/role-contracts.md",
        "templates/result.md",
        "templates/assignment.md",
    ]
    if role == "manager":
        guidance_names.append("templates/packet.md")
    if role in {"planner", "manager", "checker", "arbiter", "consultant"}:
        guidance_names.append("context/routes.md")
    if role != "planner":
        guidance_names.append("context/harness.md")
    # Pin the captured policy itself; accepted provenance is checked against it.
    guidance_names.append(POLICY_PATH)
    guidance_files = {
        name: digest(read(control_root, name)) for name in sorted(guidance_names)
    }
    guidance_identity = guidance()
    snapshot_manifest = {
        "format": "millrace-lad-guidance/v1",
        "guidance_sha256": guidance_identity,
        "files": guidance_files,
    }
    # Requirement-side fields are enough to freeze the baseline. Opaque role
    # results (even Planner/Manager) may carry recovery/implementation refs.
    accepted_inputs = {} if role == "checker" else state["accepted"]
    selected_packet = packet(state)
    if role == "checker" and selected_packet is not None:
        selected_packet = {
            key: selected_packet[key]
            for key in ("id", "lineage", "criteria", "depends_on", "paths")
        }
    if role == "checker" and replacement_intent is not None:
        replacement_intent = {
            key: replacement_intent[key]
            for key in (
                "old_occurrence_id",
                "role",
                "reason",
                "source_sha256",
                "guidance_sha256",
            )
        }
    occurrence = {
        "format": "millrace-lad-input/v2",
        "session_id": state["session_id"],
        "occurrence_id": oid,
        "role": role,
        "lineage": lineage,
        "replacement_of": replacement_of,
        "replacement_intent": replacement_intent,
        "request_sha256": state["intake"]["sha256"],
        "source_files": snapshot,
        "source_context": snapshot_context,
        "created_at": datetime.now(UTC).isoformat(),
        "helper_mode": helper_mode,
        "helper_qualification": helper_ref,
        "source_sha256": digest(canonical(snapshot)),
        "guidance_sha256": guidance_identity,
        "guidance_files": guidance_files,
        "guidance_snapshot_sha256": digest(canonical(snapshot_manifest)),
        "guidance_root": str(
            workspace.resolve() / f"lad-state/occurrences/{oid}/guidance"
        ),
        "foreground_root": str(foreground),
        "workspace_root": str(workspace.resolve()),
        "criteria": state["criteria"],
        "packet": selected_packet,
        "specification": None if role == "checker" else state["specification"],
        "baseline": state["baselines"].get(lineage),
        "fixer": None if role == "checker" else state["last_fixer"].get(lineage),
        "finding_ids": [] if role == "checker" else state["findings"].get(lineage, []),
        "review": None if role == "checker" else state["reviews"].get(lineage),
        "accepted_inputs": {
            key: value["sha256"] for key, value in accepted_inputs.items()
        },
        "accepted_input_refs": {
            key: {
                "role": state["occurrences"][key]["role"],
                "workspace": "learning"
                if state["occurrences"][key]["role"] == "learning"
                else "foreground",
                "path": f"lad-results/{key}/result.json",
                "sha256": value["sha256"],
            }
            for key, value in accepted_inputs.items()
        },
        # Recovery carriers remain canonical; pre-freeze Checker receives only
        # requirement-side scope and the original pin, never the bad-byte route.
        "pending": None if role == "checker" else pending,
        "learning_request": state["request"] if role == "learning" else None,
    }
    # Collection does no dispatch or target writes. Refresh full immutable and
    # mutable shared authority after it, immediately before durable publication.
    if shared_target(foreground) != selected_target:
        raise LadError("shared_target_changed")
    if selected_target is not None:
        occurrence["target_root"] = selected_target["target_root"]
        occurrence["target_record"] = {
            "path": str(foreground.parent / "lad-target.json"),
            "sha256": digest(read(foreground.parent, "lad-target.json")),
        }
    occurrence["input_sha256"] = digest(canonical(occurrence))
    engineering = _engineering()
    if engineering is not None:
        # Refuses while a deterministic phase is open; pins refs before publication.
        engineering.prepare_context(foreground, state, occurrence)
    return occurrence


def _prepare_provider(
    foreground: Path,
    workspace: Path,
    state: dict[str, Any],
    role: str,
    intent: dict[str, str],
    selected_target: dict[str, Any] | None,
) -> dict[str, Any]:
    """Reserve, or rematerialize, exactly one provider retry/diagnosis occurrence.

    ``intent`` is ``{operation_id, kind}`` from the graph helper. The occurrence is
    saved before its C2 reservation, so a crash at any point rematerializes that
    same occurrence; READY already published for it refuses as a second attempt.
    ``replacement_intents`` is never read or written here.
    """
    from lad_state import provider_policy

    provider_policy()  # v2 only; lazily requires installed Plus.
    from millrace_plus import provider_wait as pw

    if set(intent) != {"operation_id", "kind"} or intent["kind"] not in {
        "retry",
        "diagnosis",
    }:
        raise LadError("provider_intent_invalid")
    kind = intent["kind"]
    try:
        operation = pw.load_operation(foreground, intent["operation_id"])
    except pw.ProviderWaitError as exc:
        raise LadError(exc.code) from exc
    authority = operation.authority
    target = authority["role"]
    if (kind == "retry" and role != target) or (
        kind == "diagnosis" and role != "troubleshooter"
    ):
        raise LadError("provider_intent_role")
    failure = next(
        event
        for event in reversed(operation.events)
        if event["kind"] in {"failure_classified", "human_required"}
        and event["diagnostic_ref"] is not None
    )
    old_id = cast(str, failure["occurrence_id"])  # strict C2 event field
    old = state["occurrences"].get(old_id)
    if old is None or old["role"] != target or old_id in state["accepted"]:
        raise LadError("provider_attempt_owner")
    node = f"prepare_provider_{kind}_{target}"
    continuation = state["continuation"]
    tip = operation.tip
    reserved = "retry_reserved" if kind == "retry" else "diagnosis_reserved"
    new_id: str | None
    if tip["kind"] == reserved:
        new_id = cast(str, tip["occurrence_id"])  # strict C2 reservation event
    elif (
        continuation
        and continuation["node"] == node
        and continuation["occurrence_id"] not in (old_id, None)
    ):
        # Validated session continuation, saved before a lost reservation.
        new_id = cast(str, continuation["occurrence_id"])
    elif continuation and (continuation["node"], continuation["occurrence_id"]) == (
        node,
        old_id,
    ):
        new_id = None
    else:
        raise LadError("provider_continuation_not_selected")
    if new_id is None:
        # A retry is the same operation's generation: its pending route is the
        # authority's saved route, never a later ordinary replacement's carrier.
        pending = cast(dict[str, Any], authority["pending_route"])["pending"]
        if kind == "diagnosis":
            pending = {
                "kind": "execution",
                "target": target,
                "lineage": old["lineage"],
                "occurrence_id": old_id,
                "cause_id": operation.id,
            }
        created = _new_occurrence(
            foreground,
            workspace,
            state,
            role,
            old["lineage"],
            selected_target=selected_target,
            replacement_of=None,
            replacement_intent=None,
            pending=pending,
            helper_mode="direct",
            helper_ref=None,
        )
        new_id = cast(str, created["occurrence_id"])
        state["occurrences"][new_id] = created
        state["active"]["foreground"] = new_id
        mark(state, node, new_id)
        save(foreground, state)
    occurrence: dict[str, Any] | None = state["occurrences"].get(new_id)
    if occurrence is None or occurrence["role"] != role:
        raise LadError("provider_reserved_occurrence_missing")
    ready = workspace / f"lad-state/occurrences/{new_id}/ready.json"
    if ready.exists() or ready.is_symlink():
        raise LadError("provider_attempt_already_dispatched")
    if occurrence["guidance_sha256"] != guidance() or occurrence[
        "source_sha256"
    ] != digest(canonical(source(foreground))):
        raise LadError("provider_prepared_input_changed")
    observed = [e for e in operation.events if e["kind"] == "observed"]
    if not observed:
        raise LadError("provider_observation_required")
    # A strict C2 ``observed`` event always pins a {path, sha256} ref.
    pinned = cast(dict[str, str], observed[-1]["observation_ref"])
    raw = read(foreground, pinned["path"])
    if digest(raw) != pinned["sha256"]:
        raise LadError("provider_observation_invalid")
    observation = load_json(raw)
    try:
        reservation = pw.reserve_provider_attempt(
            foreground,
            operation_id=operation.id,
            kind=kind,
            previous_occurrence=old_id,
            new_occurrence=new_id,
            input_sha256=occurrence["input_sha256"],
            current_source_sha256=occurrence["source_sha256"],
            # Ordinary replacement's standard: the failed attempt's pinned input
            # snapshot and Core's observed stopped-writer completion fact.
            file_delta_ref={
                "path": f"lad-state/occurrences/{old_id}/input.json",
                "sha256": digest(
                    read(foreground, f"lad-state/occurrences/{old_id}/input.json")
                ),
            },
            effects_ref=observation["completion_ref"],
        )
    except pw.ProviderWaitError as exc:
        raise LadError(exc.code) from exc
    # C2 returns its strictly validated preparation record.
    preparation = cast(dict[str, object], reservation["preparation"])
    if (preparation["new_occurrence"], preparation["input_sha256"]) != (
        new_id,
        occurrence["input_sha256"],
    ):
        raise LadError("provider_reservation_conflict")
    mark(state, role if kind == "retry" else f"provider_diagnosis_{target}", new_id)
    save(foreground, state)
    materialize(workspace, state, occurrence)
    return occurrence


def prepare(
    foreground: Path,
    workspace: Path,
    role: str,
    *,
    replacement: bool = False,
    helper_mode: str = "direct",
    helper_ref: dict | None = None,
    provider_intent: dict[str, str] | None = None,
) -> dict:
    routing_policy()  # Captured new-copy policy is validated before reservation.
    if provider_intent is not None and replacement:
        # Provider retries never consume or weaken ordinary replacement escalation.
        raise LadError("provider_intent_excludes_replacement")
    selected_target = shared_target(foreground)
    if workspace == foreground:
        from accept_role import guard_baseline

        if guard_baseline(foreground, role, preparing=True):
            raise LadError("baseline_tamper_routed")
    state = load(foreground)
    if state["format"] != "millrace-lad-session/v2":
        raise LadError("explicit_session_upgrade_required")
    stored_plane = "learning" if role == "learning" else "foreground"
    stored = state["occurrences"].get(state["active"].get(stored_plane))
    if (
        stored
        and stored["format"] == "millrace-lad-input/v2"
        and (stored["helper_mode"] != "direct")
    ):
        raise LadError("native_helpers_not_qualified")
    if helper_mode not in {"direct", "native_helpers"} or (
        helper_mode == "direct" and helper_ref is not None
    ):
        raise LadError("helper_mode")
    if helper_mode == "native_helpers":
        if role == "planner":
            raise LadError("planner_stays_direct")
        # Structural fixture records are never permission for a live role.
        raise LadError("native_helpers_not_qualified")
    plane = "learning" if role == "learning" else "foreground"
    if plane == "foreground":
        assert_foreground(state)
    elif not state["request"] or state["request"]["kind"] != "ASSESS":
        raise LadError("learning_request_required")
    elif replacement or workspace.name != "learning":
        # One nonrecursive assessment per packet; failures use learning_seam fail.
        raise LadError("learning_single_assessment_only")
    if (
        state["intake"] is None
        or digest(read(foreground, "request.md")) != state["intake"]["sha256"]
    ):
        raise LadError("original_request_changed")
    if provider_intent is not None:
        return _prepare_provider(
            foreground, workspace, state, role, provider_intent, selected_target
        )
    old_id = state["active"].get(plane)
    current = state["occurrences"].get(old_id)
    if (
        replacement
        and current
        and current["role"] == role
        and current["replacement_of"]
        and old_id not in state["accepted"]
    ):
        ready = workspace / f"lad-state/occurrences/{old_id}/ready.json"
        if not ready.exists() and not ready.is_symlink():
            if current["format"] == "millrace-lad-input/v1":
                from session_compat import source_v1

                materialize(workspace, state, current)
                if current["source_sha256"] != digest(canonical(source_v1(foreground))):
                    raise LadError("legacy_source_revalidation_required")
                if current["guidance_sha256"] != guidance():
                    raise LadError("legacy_fresh_replacement_required")
                return current
            if current["guidance_sha256"] != guidance() or current[
                "source_sha256"
            ] != digest(canonical(source(foreground))):
                raise LadError("prepared_replacement_input_changed")
            materialize(workspace, state, current)
            return current
    if (
        replacement
        and state["pending"]
        and state["pending"]["target"] == role
        and (not current or current["role"] != role)
    ):
        old_id = state["pending"]["occurrence_id"]
    old = state["occurrences"].get(old_id)
    expected = "prepare_" + role + ("_replacement" if replacement else "")
    continuation = state["continuation"]
    if (
        not replacement
        and old
        and old["role"] == role
        and old_id not in state["accepted"]
    ):
        # Only a failed, incomplete preparation can be rematerialized. Once READY
        # exists, a raw redirect would be an unaccounted second native attempt.
        ready = workspace / f"lad-state/occurrences/{old_id}/ready.json"
        if ready.exists() or ready.is_symlink():
            raise LadError("prepared_attempt_requires_reconciliation_or_replacement")
        if old["format"] == "millrace-lad-input/v1":
            from session_compat import source_v1

            materialize(workspace, state, old)
            if old["source_sha256"] != digest(canonical(source_v1(foreground))):
                raise LadError("legacy_source_revalidation_required")
            if old["guidance_sha256"] != guidance():
                raise LadError("legacy_fresh_replacement_required")
            return old
        if old["guidance_sha256"] != guidance() or old["source_sha256"] != digest(
            canonical(source(foreground))
        ):
            raise LadError("prepared_input_changed")
        materialize(workspace, state, old)
        return old
    first = not state["occurrences"] and role == (
        "planner" if state["intake"]["greenfield"] else "recon"
    )
    if (
        not first
        and not replacement
        and (not continuation or continuation["node"] != expected)
    ):
        raise LadError("intent_free_redirect_refused")
    lineage = scope(state)
    if role in {"troubleshooter", "consultant"} and state["pending"]:
        lineage = state["pending"]["lineage"]
    if role == "learning":
        lineage = state["request"]["lineage"]
    if replacement:
        if not old or old["role"] != role:
            raise LadError("replacement_old_occurrence_required")
        previous_intent = old.get("replacement_intent")
        if (
            old_id not in state["accepted"]
            and previous_intent is not None
            and previous_intent["source_sha256"]
            == digest(canonical(source(foreground)))
            and previous_intent["guidance_sha256"] == guidance()
            and previous_intent["reason"] == "explicit_graph_replacement_preparation"
        ):
            state["pending"] = {
                "kind": "execution",
                "target": role,
                "lineage": old["lineage"],
                "occurrence_id": old_id,
                "cause_id": digest(canonical(previous_intent)),
            }
            mark(
                state,
                "prepare_consultant"
                if role == "troubleshooter"
                else "human_pause"
                if role == "consultant"
                else "prepare_troubleshooter",
                old_id,
            )
            save(foreground, state)
            raise LadError("unchanged_execution_prerequisite")
        # The explicit graph error/unknown/replacement edge supplies this mode.
        # Existing Core writer guards remain authoritative for uncertain writers.
        if old_id in state["accepted"] and (
            not continuation or continuation["node"] != expected
        ):
            raise LadError("replacement_route_not_selected")
        lineage = old["lineage"]
        state["replacement_intents"].setdefault(
            old_id,
            {
                "old_occurrence_id": old_id,
                "role": role,
                "reason": "explicit_graph_replacement_preparation",
                "source_sha256": digest(canonical(source(foreground))),
                "guidance_sha256": guidance(),
            },
        )
        intent = state["replacement_intents"][old_id]
        if (
            intent["source_sha256"] != digest(canonical(source(foreground)))
            or intent["guidance_sha256"] != guidance()
        ):
            raise LadError("replacement_intent_stale")
        save(foreground, state)
        state["pending"] = state["pending"] or {
            "target": role,
            "lineage": lineage,
            "occurrence_id": old_id,
            "kind": "replacement",
        }
    occurrence = _new_occurrence(
        foreground,
        workspace,
        state,
        role,
        lineage,
        selected_target=selected_target,
        replacement_of=old_id if replacement else None,
        replacement_intent=(
            state["replacement_intents"][old_id] if replacement else None
        ),
        pending=state["pending"],
        helper_mode=helper_mode,
        helper_ref=helper_ref,
    )
    oid = occurrence["occurrence_id"]
    state["occurrences"][oid] = occurrence
    state["active"][plane] = oid
    mark(state, role, oid)
    save(foreground, state)
    materialize(workspace, state, occurrence)
    return occurrence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("--replacement", action="store_true")
    parser.add_argument(
        "--helper-mode", choices=["direct", "native_helpers"], default="direct"
    )
    parser.add_argument("--helper-qualification")
    args = parser.parse_args()
    foreground, _ = roots()
    try:
        occurrence = prepare(
            foreground,
            foreground,
            args.role,
            replacement=args.replacement,
            helper_mode=args.helper_mode,
            helper_ref=(
                {
                    "path": args.helper_qualification,
                    "sha256": digest(read(foreground, args.helper_qualification)),
                }
                if args.helper_qualification is not None
                else None
            ),
        )
    except LadError as exc:
        if str(exc) == "baseline_tamper_routed":
            emit(
                "BASELINE_TAMPER", "Original baseline restored; fresh review required."
            )
            return
        if str(exc) != "unchanged_execution_prerequisite":
            raise
        emit("TECHNICAL_RECOVERY", "Unchanged failed prerequisite needs diagnosis.")
        return
    emit(
        "READY",
        "Reserved occurrence=" + occurrence["occurrence_id"],
        ["lad-state/active-role.json"],
    )


if __name__ == "__main__":
    run(main)
