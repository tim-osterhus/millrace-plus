"""Thin Factory controls. Initialization starts no role or OS service."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from typing import NoReturn

from . import factory_records as fr
from .check_contracts import CheckContractError
from .cicd_records import CicdError
from .factory_runtime import Home, initialize, wake, write_exact
from .local_intake import IntakeError, canonical, identity, strict_json

__all__ = ["main", "status", "decide", "cancel"]


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise fr.FactoryError("usage_error")


def status(home: Home, job: str | None = None) -> dict:
    rows = home.intake.tasks(job)
    jobs = []
    for task in rows:
        row = {key: value for key, value in task.items() if key != "request_bytes"}
        path = home.workspace / "jobs" / task["task_id"] / "state.json"
        if task["claim"] is not None:
            if not path.exists():
                raise fr.FactoryError("factory_claim_projection_missing")
            state = home.state(task["task_id"])
            row["state_ref"] = {
                "path": "factory/"
                + home.factory_id
                + "/records/"
                + hashlib.sha256(canonical(state)).hexdigest()
                + ".json",
                "sha256": hashlib.sha256(canonical(state)).hexdigest(),
            }
            row["phase"] = state["phase"]
            row["facts"] = home.facts(state)
        jobs.append(row)
    return {
        "factory_id": home.factory_id,
        "accepted_base_ref": home.base_ref(),
        "jobs": jobs,
    }


def _evidence(home: Home, state: dict, file: Path) -> tuple[dict, dict]:
    evidence = strict_json(file.read_bytes())
    fr.require_fields(
        evidence,
        "state_ref changed_cause_ref lad_decision lad_evidence_ref resume_plane",
        "invalid_factory_decision_evidence",
    )
    if home.read_ref(evidence["state_ref"]) != state:
        raise fr.FactoryError("factory_decision_stale")
    for field in ("changed_cause_ref", "lad_evidence_ref"):
        if evidence[field] is not None:
            home.raw_ref(evidence[field])
    return evidence, home.retain(evidence)


def decide(
    home: Home, job: str, decision: str, request_id: str, evidence_file: Path
) -> dict:
    identity(request_id)
    task = home.intake.tasks(identity(job))[0]
    state = home.state(job)
    raw_evidence = strict_json(evidence_file.read_bytes())
    evidence_ref = home.retain(raw_evidence)
    intent = {"task_id": job, "decision": decision, "evidence": evidence_ref}
    prior = home.intake.decision(request_id, intent)
    if prior is not None:
        with home.lock():
            home.apply_decision_plan(request_id, prior["receipt"])
        return prior["receipt"]
    evidence, evidence_ref = _evidence(home, state, evidence_file)
    facts = home.facts(state)
    held = state["phase"] == "hold"
    parent_control = (
        decision == "continue-lad" and evidence["lad_decision"] == "parent-control"
    )
    if not held or parent_control:
        held = False
        from .router import control
        from .subworkflow_v2 import closed

        execution = strict_json((home.home / "execution.json").read_bytes())
        instance = control.inspect_instance(
            execution["instance_id"], state_home=Path(execution["state_home"])
        )
        active = [
            row
            for row in instance["runs"]
            if row["status"] not in {"succeeded", "failed", "cancelled"}
        ]
        if len(active) == 1:
            view = control.inspect_run(
                execution["instance_id"],
                active[0]["id"],
                state_home=Path(execution["state_home"]),
            )
            held = view["status"] == "paused" and closed(view, attention=True)
    if not held or task["status"] not in {"claimed", "running", "held"}:
        raise fr.FactoryError("factory_decision_not_held")
    if decision == "approve-delivery":
        raise fr.FactoryError("unsupported_until_factory_f2")
    if decision == "retry-evidence":
        if (
            evidence["changed_cause_ref"] is None
            or facts.get("repair_projection_ref")
            or facts.get("lad_task_id")
        ):
            raise fr.FactoryError("factory_new_evidence_required")
        # A new read-only assessment retains the original implementation reservation.
        facts["prior_investigation_refs"] = [
            *facts.get("prior_investigation_refs", []),
            facts.get("investigate_projection_ref"),
        ]
        facts.pop("investigate_projection_ref", None)
        facts["evidence_occurrence"] = facts.get("evidence_occurrence", 0) + 1
        facts["changed_cause_ref"] = evidence["changed_cause_ref"]
        phase = "prepare_investigation"
    elif decision == "retry-verification":
        if not facts.get("candidate_ref"):
            raise fr.FactoryError("factory_candidate_required")
        phase = "select_check"
        facts["prior_check_refs"] = [
            *facts.get("prior_check_refs", []),
            *facts["checks"],
        ]
        facts["checks"] = []
        facts["verification_occurrence"] += 1
        facts["verification_retry"] = True
    elif decision == "continue-lad":
        if (
            not facts.get("lad_task_id")
            or evidence["lad_decision"] is None
            or evidence["lad_evidence_ref"] is None
        ):
            raise fr.FactoryError("factory_exact_lad_decision_required")
        facts.pop("held_parent_join_ref", None)
        path = home.workspace / "decisions" / (request_id + ".lad-evidence.json")
        write_exact(path, home.raw_ref(evidence["lad_evidence_ref"]))
        if not parent_control:
            facts["lad_continuation_intent_ref"] = home.retain(
                {
                    "task_id": facts["lad_task_id"],
                    "decision": evidence["lad_decision"],
                    "request_id": request_id,
                    "evidence_ref": evidence["lad_evidence_ref"],
                    "resume_plane": evidence["resume_plane"],
                }
            )
        phase = "observe_lad"
        if parent_control:
            if evidence["resume_plane"] != "service":
                raise fr.FactoryError("factory_exact_service_resume_required")
            from .factory_lad import Lane

            resumed = Lane(home).parent_resume_intent(
                job,
                facts["lad_task_ref"],
                evidence["lad_evidence_ref"],
                caller_deadline=time.monotonic() + 30,
                fresh=True,
            )
            for key in ("parent_resume", "parent_resume_result"):
                prior_ref = facts.get(key + "_ref")
                if prior_ref is not None:
                    facts[key + "_history_refs"] = [
                        *facts.get(key + "_history_refs", []),
                        prior_ref,
                    ]
            facts["parent_resume_ref"] = home.retain(resumed)
            facts.pop("parent_resume_result_ref", None)
            phase = "resume_lad_parent"
    elif decision in {"close", "acknowledge-cancelled"}:
        if decision == "acknowledge-cancelled" and not facts.get("cancel_requested"):
            raise fr.FactoryError("factory_cancel_not_requested")
        facts.update(close_requested=True, accepted=False)
        phase = (
            "select_reproduction"
            if (facts.get("pending") or {}).get("node") == "reproduce"
            else "select_check"
            if facts.get("pending")
            else "restore_validation"
        )
    else:
        raise fr.FactoryError("invalid_factory_decision")
    plan = {
        "intent": intent,
        "state_ref": home.retain(state),
        "facts_ref": home.retain(facts),
        "phase": phase,
    }
    write_exact(
        home.workspace / "decisions" / (request_id + ".plan.json"), canonical(plan)
    )
    if decision == "continue-lad" and not parent_control:
        from .factory_lad import Lane

        # Freeze Factory intent before forwarding. Interrupted replay uses the same
        # owning LAD request; its attention/run/plane and unknown guards still apply.
        result_path = home.workspace / "decisions" / (request_id + ".lad-result.json")
        result = Lane(home).runtime.decide(
            facts["lad_task_id"],
            evidence["lad_decision"],
            request_id,
            path,
            resume_plane=evidence["resume_plane"],
            _deadline=time.monotonic() + 30,
        )
        write_exact(result_path, canonical(result))
    receipt = home.intake.decide(request_id, intent, check=lambda row: None)
    with home.lock():
        home.apply_decision_plan(request_id, receipt)
    wake(home)
    return receipt


def cancel(home: Home, job: str, request_id: str) -> dict:
    identity(request_id)
    task = home.intake.tasks(identity(job))[0]
    intent = {
        "task_id": job,
        "decision": "cancel",
        "evidence": home.retain(
            {"job_id": job, "request_sha256": task["request_sha256"]}
        ),
    }
    prior = home.intake.decision(request_id, intent)
    plan_path = home.workspace / "decisions" / (request_id + ".plan.json")
    if prior is None and task["status"] not in {
        "queued",
        "succeeded",
        "failed",
        "cancelled",
    }:
        state = home.state(job)
        facts = home.facts(state) | {"cancel_requested": True, "accepted": False}
        plan = {
            "intent": intent,
            "state_ref": home.retain(state),
            "facts_ref": home.retain(facts),
            "phase": "hold",
        }
        write_exact(plan_path, canonical(plan))
    receipt = home.intake.decide(
        request_id,
        intent,
        check=lambda row: None,
        cancel_queued=task["status"] == "queued",
    )
    if not plan_path.exists():
        wake(home)
        return receipt
    with home.lock():
        saved = home.apply_decision_plan(request_id, receipt)
    result_path = home.workspace / "decisions" / (request_id + ".cancel-result.json")
    if result_path.exists():
        return strict_json(result_path.read_bytes())
    facts = home.facts(saved)
    if facts.get("lad_task_id"):
        from .factory_lad import Lane

        result = Lane(home).runtime.cancel(facts["lad_task_id"], request_id)
    else:
        from .router import control

        claim = home.read_ref(saved["claim_ref"])
        result = control.cancel_run(
            claim["parent_instance_id"],
            claim["parent_run_id"],
            state_home=home.home / "state",
            request_id=uuid.uuid5(uuid.UUID(hex=request_id), "factory-run-cancel").hex,
        )
    answer = receipt | {
        "state_ref": home.retain(saved),
        "control_ref": home.retain(result),
    }
    write_exact(result_path, canonical(answer))
    wake(home)
    return answer


def main(argv=None) -> int:
    parser = Parser(prog="millrace-plus factory")
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("init", "submit", "status", "decide", "cancel"):
        command = sub.add_parser(name)
        command.add_argument("--factory-home", required=True, type=Path)
        if name == "init":
            command.add_argument("--config", required=True, type=Path)
        if name == "submit":
            command.add_argument("--input", required=True, type=Path)
        if name in {"status", "decide", "cancel"}:
            command.add_argument("--job", required=name != "status")
        if name in {"decide", "cancel"}:
            command.add_argument("--request-id", required=True)
        if name == "decide":
            command.add_argument("--decision", required=True)
            command.add_argument("--evidence", required=True, type=Path)
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        if args.operation == "init":
            result = initialize(args.factory_home, args.config)
        else:
            home = Home(args.factory_home)
            if args.operation == "submit":
                result = home.submit(args.input)
            elif args.operation == "status":
                result = status(home, args.job)
            elif args.operation == "decide":
                result = decide(
                    home, args.job, args.decision, args.request_id, args.evidence
                )
            else:
                result = cancel(home, args.job, args.request_id)
        print(canonical(result).decode())
        return 0
    except (CicdError, IntakeError, CheckContractError) as exc:
        print(json.dumps({"code": exc.code}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"code": "factory_io_or_record_error"}), file=sys.stderr)
        return 2
