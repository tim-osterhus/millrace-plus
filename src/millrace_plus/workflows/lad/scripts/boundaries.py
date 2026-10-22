"""Finite named domain guards; this file never invokes or schedules a role."""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from lad_state import (
    LadError,
    assert_foreground,
    canonical,
    debit,
    digest,
    emit,
    fields,
    identifier,
    load,
    mark,
    packet,
    read,
    roots,
    run,
    save,
    text,
)


def _criteria(state: dict, result: dict, foreground: Path) -> None:
    criteria = result["data"].get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise LadError("original_criteria_required")
    seen = set()
    request = read(foreground, "request.md").decode("utf-8")
    for row in criteria:
        fields(row, {"id", "text", "request_quote"})
        identifier(row["id"])
        text(row["text"])
        if row["id"] in seen or text(row["request_quote"]) not in request:
            raise LadError("criterion_not_mapped_to_original_request")
        seen.add(row["id"])
    if state["criteria"] and state["criteria"] != criteria:
        raise LadError("original_criteria_changed")
    state["criteria"] = criteria


def _plan(state: dict, occurrence: dict, result: dict, foreground: Path) -> None:
    plan = result["data"].get("plan")
    if not isinstance(plan, list) or not plan or len(plan) > 60:
        raise LadError("complete_ordered_plan_required")
    all_criteria = {row["id"] for row in state["criteria"]}
    if not all_criteria:
        raise LadError("specification_before_execution")
    seen, covered = set(), set()
    old = {row["lineage"]: row for row in state["plan"]}
    accepted = []
    remediation = state["pending"] and state["pending"]["kind"] == "remediation"
    affected = result["data"].get("affected_lineages", [])
    if not isinstance(affected, list) or any(
        lineage not in old for lineage in affected
    ):
        raise LadError("invalid_affected_lineages")
    if affected and (
        not remediation
        or any(
            not set(old[lineage]["criteria"]) & set(state["pending"]["criteria"])
            for lineage in affected
        )
    ):
        raise LadError("remediation_must_target_original_gap")
    for row in plan:
        fields(
            row,
            {
                "id",
                "lineage",
                "criteria",
                "depends_on",
                "paths",
                "input_refs",
                "markdown",
            },
        )
        identifier(row["id"])
        lineage = identifier(row["lineage"])
        if (
            lineage in seen
            or not isinstance(row["criteria"], list)
            or not row["criteria"]
            or not set(row["criteria"]) <= all_criteria
        ):
            raise LadError("packet_criteria_or_lineage")
        if (
            not isinstance(row["depends_on"], list)
            or not set(row["depends_on"]) <= seen
            or len(set(row["depends_on"])) != len(row["depends_on"])
        ):
            raise LadError("packet_dependency_order")
        if (
            not isinstance(row["paths"], list)
            or not row["paths"]
            or not isinstance(row["input_refs"], list)
        ):
            raise LadError("packet_scope_required")
        for name in row["paths"]:
            path = Path(text(name))
            if (
                path.is_absolute()
                or ".." in path.parts
                or str(path) != name
                or name in {".", ""}
            ):
                raise LadError("packet_path_escape")
        # Every packet is an occurrence-scoped immutable draft, not a role-written
        # canonical plan. Acceptance retains the full ordered draft and digests.
        markdown = text(row["markdown"])
        artifact = next(
            (item for item in result["artifacts"] if item["path"] == markdown), None
        )
        if artifact is None:
            raise LadError("packet_markdown_not_evidenced")
        for ref in row["input_refs"]:
            fields(ref, {"occurrence_id", "sha256"})
            if (
                ref["occurrence_id"] not in state["accepted"]
                or state["accepted"][ref["occurrence_id"]]["sha256"] != ref["sha256"]
            ):
                raise LadError("stale_packet_input")
        draft = dict(row)
        draft.update(
            owner_occurrence=occurrence["occurrence_id"],
            markdown_sha256=artifact["sha256"],
            accepted=False,
        )
        previous = old.get(lineage)
        if previous and previous["accepted"]:
            comparable = {key: previous[key] for key in row}
            if lineage not in affected:
                if comparable != row:
                    raise LadError("unaffected_accepted_packet_changed")
                draft = previous
            elif not remediation:
                raise LadError("accepted_packet_reopen_requires_remediation")
        seen.add(lineage)
        covered.update(row["criteria"])
        accepted.append(draft)
    if covered != all_criteria or (old and seen != old.keys()):
        raise LadError("plan_drops_criteria_or_original_lineage")
    state["plan"] = accepted
    state["packet_index"] = next(
        (n for n, row in enumerate(accepted) if not row["accepted"]), len(accepted)
    )
    state["pending"] = None


def _recover(state: dict, occurrence: dict, *, kind: str = "execution") -> str:
    if occurrence["role"] not in {"troubleshooter", "consultant"}:
        state["pending"] = {
            "kind": kind,
            "target": occurrence["role"],
            "lineage": occurrence["lineage"],
            "occurrence_id": occurrence["occurrence_id"],
        }
    elif not state["pending"]:
        raise LadError("saved_interrupted_role_required")
    return "TROUBLESHOOTER"


def _consult(state: dict, occurrence: dict) -> str:
    if not state["pending"]:
        state["pending"] = {
            "kind": "contract",
            "target": occurrence["role"],
            "lineage": occurrence["lineage"],
            "occurrence_id": occurrence["occurrence_id"],
        }
    return "CONSULTANT"


def _review(state: dict, occurrence: dict, result: dict, foreground: Path) -> None:
    lineage = occurrence["lineage"]
    baseline = state["baselines"].get(lineage)
    if occurrence["role"] == "checker":
        artifact = next(
            item for item in result["artifacts"] if item["path"] == "baseline.md"
        )
        allowed_new = state["pending"] and state["pending"]["kind"] == "new_baseline"
        if baseline and not allowed_new:
            if result["data"].get("baseline") != baseline:
                raise LadError("checker_cannot_replace_frozen_baseline")
        else:
            baseline = {
                "occurrence_id": occurrence["occurrence_id"],
                "sha256": artifact["sha256"],
                "path": f"lad-results/{occurrence['occurrence_id']}/baseline.md",
                "criteria": occurrence["packet"]["criteria"],
                "finding_ids": [row["id"] for row in result["findings"]],
            }
            state["baselines"][lineage] = baseline
        state["pending"] = None
    elif (
        result["data"].get("baseline") != baseline
        or result["data"].get("fixer") != occurrence["fixer"]
    ):
        raise LadError("review_pair_or_baseline_mismatch")
    if result["outcome"] == "PASS":
        checked = result["data"].get("criteria_checked")
        if not isinstance(checked, list) or set(checked) != set(
            occurrence["packet"]["criteria"]
        ):
            raise LadError("review_missing_packet_criteria")
        state["reviews"][lineage] = {
            "occurrence_id": occurrence["occurrence_id"],
            "source_sha256": result["source_sha256"],
            "baseline": baseline,
        }
    else:
        state["reviews"].pop(lineage, None)
    state["findings"][lineage] = [row["id"] for row in result["findings"]]


def _gate(state: dict, occurrence: dict, result: dict) -> str:
    lineage = occurrence["lineage"]
    repair = occurrence["fixer"]
    if repair is None:
        return "UPDATER" if result["outcome"] == "PASS" else "FIXER"
    if repair["occurrence_id"] not in state["accepted"]:
        raise LadError("inspectable_fixer_required")
    pairs = state["pairs"].setdefault(lineage, [])
    if not any(row["fixer"] == repair for row in pairs):
        pairs.append(
            {
                "fixer": repair,
                "doublechecker": occurrence["occurrence_id"],
                "outcome": result["outcome"],
                "source_sha256": result["source_sha256"],
            }
        )
    passed = result["outcome"] == "PASS"
    target = "updater" if passed else ("fixer" if len(pairs) < 2 else "troubleshooter")
    if target == "troubleshooter":
        _recover(state, occurrence, kind="review")
    if len(pairs) >= 2 and lineage not in state["learning"]:
        state["pending"] = {
            "kind": "learning_gate",
            "target": target,
            "lineage": lineage,
            "occurrence_id": occurrence["occurrence_id"],
            "pairs": pairs[:2],
            "source_sha256": result["source_sha256"],
            "guidance_sha256": occurrence["guidance_sha256"],
        }
        return "ASSESS"
    return target.upper()


def choose(state: dict, occurrence: dict, result: dict, foreground: Path) -> str:
    """Validate one semantic result and return a declared finite guard outcome."""
    role, outcome, data = occurrence["role"], result["outcome"], result["data"]
    lineage = occurrence["lineage"]
    if role == "learning":
        if data.get("pending_route") != state["pending"] or not state["request"]:
            raise LadError("learning_changed_pending_route")
        if (
            outcome == "ERROR_TRANSIENT_RESOLVED"
            and state["usage"].get("learning:" + lineage, 0) < 2
        ):
            return "REPLACE_LEARNING"
        state["learning"][lineage] = {
            "disposition": "passed"
            if outcome == "PASS"
            else "findings"
            if outcome == "FINDINGS"
            else "failed",
            "occurrence_id": occurrence["occurrence_id"],
        }
        return "ACK"
    if outcome == "HUMAN_BLOCKED" or outcome == "NO_SAFE_ROUTE":
        state["pending"] = state["pending"] or {
            "kind": "human",
            "target": role,
            "lineage": lineage,
            "occurrence_id": occurrence["occurrence_id"],
        }
        return "PAUSE"
    if outcome in {"ERROR_REPLACEMENT"}:
        return "REPLACE_" + role.upper()
    if outcome in {"ERROR", "ERROR_REPAIRABLE", "FINDINGS_UNINSPECTABLE"}:
        if role == "troubleshooter":
            return "CONSULTANT"
        return _recover(state, occurrence)
    if role == "recon":
        return "PLANNER" if outcome == "PASS" else _consult(state, occurrence)
    if role == "planner":
        if outcome != "PASS":
            return _consult(state, occurrence)
        _criteria(state, result, foreground)
        state["specification"] = {
            "occurrence_id": occurrence["occurrence_id"],
            "sha256": next(
                a["sha256"]
                for a in result["artifacts"]
                if a["path"] == "specification.md"
            ),
        }
        return "MANAGER"
    if role == "manager":
        if outcome != "PASS":
            return _consult(state, occurrence)
        _plan(state, occurrence, result, foreground)
        return "BUILDER" if packet(state) else "ARBITER"
    if role == "builder":
        return "CHECKER"
    if role == "checker":
        if outcome == "FINDINGS_CONTRACT":
            return _consult(state, occurrence)
        _review(state, occurrence, result, foreground)
        return "UPDATER" if outcome == "PASS" else "FIXER"
    if role == "fixer":
        if (
            data.get("baseline") != state["baselines"].get(lineage)
            or not isinstance(data.get("finding_ids"), list)
            or set(data["finding_ids"]) != set(occurrence["finding_ids"])
        ):
            raise LadError("repair_baseline_or_findings_changed")
        state["last_fixer"][lineage] = {
            "occurrence_id": occurrence["occurrence_id"],
            "sha256": digest(canonical(result)),
        }
        return "DOUBLECHECKER"
    if role == "doublechecker":
        if outcome == "FINDINGS_CONTRACT":
            return _consult(state, occurrence)
        _review(state, occurrence, result, foreground)
        return _gate(state, occurrence, result)
    if role == "troubleshooter":
        if outcome == "FINDINGS":
            return "CONSULTANT"
        pending = state["pending"]
        if not pending or pending["target"] not in {
            "recon",
            "planner",
            "manager",
            "builder",
            "checker",
            "fixer",
            "doublechecker",
            "updater",
            "arbiter",
            "consultant",
        }:
            raise LadError("exact_saved_role_required")
        if (
            data.get("resume_role") != pending["target"]
            or data.get("repair_verified") is not True
            or not occurrence["local_repair_allowed"]
        ):
            raise LadError("repair_resume_evidence_required")
        if outcome == "PASS_REVIEW_REPAIRED":
            if lineage not in state["last_fixer"]:
                raise LadError("review_repair_requires_inspectable_fixer")
            return "DOUBLECHECKER"
        return "RESUME_" + pending["target"].upper()
    if role == "consultant":
        if outcome in {"REPAIR_SPEC", "REPAIR_PACKETS"}:
            debit(state, ["planning_repair"], str(uuid.uuid4()), "planning_repair")
            return "PLANNER" if outcome == "REPAIR_SPEC" else "MANAGER"
        if outcome == "REPAIR_EXECUTION":
            return _recover(state, occurrence)
        raise LadError("unsupported_consultant_route")
    if role == "updater":
        if outcome == "FINDINGS_CONTRACT":
            return _consult(state, occurrence)
        if outcome.startswith("REVIEW_INVALIDATED"):
            state["reviews"].pop(lineage, None)
            if outcome.endswith("NEW_BASELINE"):
                state["pending"] = {
                    "kind": "new_baseline",
                    "target": "checker",
                    "lineage": lineage,
                    "occurrence_id": occurrence["occurrence_id"],
                }
                return "CHECKER"
            return "DOUBLECHECKER"
        review = state["reviews"].get(lineage)
        if (
            not review
            or review["source_sha256"] != result["source_sha256"]
            or data.get("review") != review
            or set(data.get("criteria_evidence", {}))
            != set(occurrence["packet"]["criteria"])
        ):
            raise LadError("packet_acceptance_requires_current_review_and_criteria")
        for evidence in data["criteria_evidence"].values():
            text(evidence)
        state["plan"][state["packet_index"]]["accepted"] = True
        state["packet_index"] = next(
            (n for n, row in enumerate(state["plan"]) if not row["accepted"]),
            len(state["plan"]),
        )
        return "BUILDER" if packet(state) else "ARBITER"
    if role == "arbiter":
        verdict = data.get("verdict")
        if not isinstance(verdict, dict) or set(verdict) != {
            row["id"] for row in state["criteria"]
        }:
            raise LadError("arbiter_original_criteria_required")
        for value in verdict.values():
            fields(value, {"status", "evidence"})
            if value["status"] not in {"pass", "gap", "unavailable"}:
                raise LadError("invalid_verdict")
            text(value["evidence"])
        if outcome == "PASS":
            if (
                packet(state)
                or any(not row["accepted"] for row in state["plan"])
                or any(value["status"] != "pass" for value in verdict.values())
            ):
                raise LadError("closure_guard_failed")
            state["terminal"] = "succeeded"
            state["pending"] = None
            return "CLOSE"
        if not any(value["status"] == "gap" for value in verdict.values()):
            raise LadError("remediation_gap_required")
        debit(state, ["remediation"], str(uuid.uuid4()), "remediation")
        state["pending"] = {
            "kind": "remediation",
            "target": "manager",
            "lineage": "unpacketized",
            "occurrence_id": occurrence["occurrence_id"],
            "criteria": [
                key for key, value in verdict.items() if value["status"] == "gap"
            ],
        }
        return "MANAGER"
    raise LadError("undeclared_guard")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=["terminal_failure", "finalize", "human_pause", "record_replacement"],
    )
    parser.add_argument("--role")
    parser.add_argument("--occurrence")
    parser.add_argument("--evidence")
    args = parser.parse_args()
    foreground, _ = roots()
    state = load(foreground)
    assert_foreground(state)
    if args.operation == "record_replacement":
        from lad_state import guidance, source

        old = state["occurrences"].get(args.occurrence)
        if not old or old["role"] != args.role:
            raise LadError("exact_old_occurrence_required")
        evidence = read(foreground, args.evidence).decode("utf-8")
        if args.occurrence not in text(evidence) or state["session_id"] not in evidence:
            raise LadError("correlated_public_writer_evidence_required")
        intent = {
            "old_occurrence_id": args.occurrence,
            "role": args.role,
            "reason": "operator_after_public_writer_inspection",
            "evidence_path": args.evidence,
            "evidence_sha256": digest(evidence.encode()),
            "source_sha256": digest(canonical(source(foreground))),
            "guidance_sha256": guidance(),
        }
        existing = state["replacement_intents"].get(args.occurrence)
        if existing and existing != intent:
            raise LadError("replacement_intent_conflict")
        state["replacement_intents"][args.occurrence] = intent
        state["pending"] = {
            "kind": "operator_replacement",
            "target": args.role,
            "lineage": old["lineage"],
            "occurrence_id": args.occurrence,
        }
        mark(state, "prepare_" + args.role + "_replacement", args.occurrence)
        save(foreground, state)
        emit(
            "INTENT_RECORDED",
            "Redirect only through the named replacement preparation; "
            "Core writer guards still apply.",
        )
    elif args.operation == "terminal_failure":
        from lad_state import load_json

        intent = fields(
            load_json(read(foreground, "lad-state/operator-intent.json")),
            {"format", "session_id", "decision", "reason"},
        )
        if (
            intent["format"] != "millrace-lad-operator-intent/v1"
            or intent["session_id"] != state["session_id"]
            or intent["decision"] != "terminal_failure"
        ):
            raise LadError("explicit_terminal_intent_required")
        text(intent["reason"])
        state["terminal"] = "failed"
        mark(state, "emit_learning_request")
        save(foreground, state)
        emit("CLOSE", "Explicit operator terminal choice; closing both planes.")
    elif args.operation == "finalize":
        if (
            state["terminal"] not in {"succeeded", "failed"}
            or state["continuation"]["node"] != "finalize"
        ):
            raise LadError("close_ack_required")
        state["continuation"]["cleanup_required"] = False
        save(foreground, state)
        emit(state["terminal"].upper(), "Both planes completed the CLOSE/ACK protocol.")
    else:
        emit(
            "HOLD",
            "session="
            + state["session_id"]
            + "; saved="
            + str(state["continuation"])
            + "; pending="
            + str(state["pending"]),
        )


if __name__ == "__main__":
    run(main)
