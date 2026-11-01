"""Factory bootstrap and persistence over existing source and lifecycle controls."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import os
import socket
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from . import cicd_v2_records as ci
from . import factory_candidate as candidate
from . import factory_records as fr
from .cicd_source import (
    canonical_dir,
    filesystem_identity,
    overlap,
)
from .cicd_source import (
    write_exact as _write_exact,
)
from .cicd_source import (
    write_replace as _write_replace,
)
from .cicd_v2_runtime import V2Home
from .cicd_v2_runtime import _read as _read_record
from .local_intake import Intake, canonical, identity, strict_json

__all__ = ["Home", "initialize", "step", "derive_graph", "wake", "ROUTES"]


def _record_path(path: Path) -> None:
    canonical_dir(path, "factory_record_path_invalid", directory=False)
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise fr.FactoryError("factory_record_not_regular")


def write_exact(path: Path, raw: bytes, code: str = "factory_record_conflict") -> None:
    """Use existing immutable writes after validating the owned record path."""
    _record_path(path)
    _write_exact(path, raw, code)


def write_replace(path: Path, raw: bytes) -> None:
    """Keep mutable views and their temporary files inside canonical directories."""
    _record_path(path)
    _record_path(path.with_name("." + path.name + ".tmp"))
    _write_replace(path, raw)


# Fixed graph routes, including every local reconciliation destination.
ROUTES = {
    "claim": {"CLAIMED": "normalize", "EMPTY": "wait_intake", "HOLD": "hold"},
    "wait_intake": {"WAKE": "claim", "IDLE": "claim"},
    "normalize": {"READY": "prepare_baseline", "NO_ACTION": "retain", "HOLD": "hold"},
    "prepare_baseline": {"PREPARED": "guard_prepared_base", "HOLD": "hold"},
    "guard_prepared_base": {
        "TASK_READY": "prepare_investigation",
        "FEEDBACK_READY": "select_reproduction",
        "HOLD": "hold",
    },
    "prepare_investigation": {"READY": "investigate", "HOLD": "hold"},
    "investigate": {"ASSESSED": "guard_investigation", "BLOCKED": "hold"},
    "guard_investigation": {
        "TASK_READY": "scope_gate",
        "REPRODUCED": "scope_gate",
        "PROBE_REQUIRED": "probe",
        "NO_ACTION": "restore_validation",
        "EVIDENCE_HOLD": "hold",
        "HUMAN_HOLD": "hold",
    },
    "probe": {"EVIDENCE": "hold", "INFRASTRUCTURE": "hold", "UNCERTAIN": "hold"},
    "select_reproduction": {
        "CLOSE_READY": "restore_validation",
        "NEXT": "reproduce",
        "REPRODUCED": "prepare_investigation",
        "UNREPRODUCED": "prepare_investigation",
        "EXTERNAL_BLOCKER": "hold",
        "UNCERTAIN": "hold",
        "HOLD": "hold",
    },
    "reproduce": {"RECORDED": "select_reproduction"},
    "scope_gate": {"CI_REPAIR": "prepare_ci", "LAD_TASK": "submit_lad", "HOLD": "hold"},
    "prepare_ci": {"READY": "repair", "HOLD": "hold"},
    "repair": {"PATCH_READY": "guard_patch", "BLOCKED": "hold"},
    "guard_patch": {"SCOPED": "export_candidate", "HOLD": "hold"},
    "submit_lad": {
        "SUBMITTED": "resume_lad_parent",
        "REPLAYED": "resume_lad_parent",
        "HOLD": "hold",
    },
    "resume_lad_parent": {
        "APPLIED": "observe_lad",
        "PENDING": "resume_lad_parent",
        "HOLD": "hold",
    },
    "observe_lad": {
        "RUNNING": "observe_lad",
        "SUCCEEDED": "park_lad",
        "PROVIDER_WAIT": "wait_provider",
        "HOLD": "hold",
    },
    "park_lad": {"CLOSED": "export_candidate", "PENDING": "park_lad", "HOLD": "hold"},
    "export_candidate": {"EXPORTED": "materialize_candidate", "HOLD": "hold"},
    "materialize_candidate": {"READY": "select_check", "HOLD": "hold"},
    "select_check": {
        "CLOSE_READY": "restore_validation",
        "NEXT": "check_candidate",
        "COMPLETE": "prepare_review",
        "FAIL": "hold",
        "UNCERTAIN": "hold",
        "HOLD": "hold",
    },
    "check_candidate": {"RECORDED": "select_check"},
    "prepare_review": {"READY": "review_candidate", "HOLD": "hold"},
    "review_candidate": {
        "ACCEPT": "verify_candidate",
        "REJECT": "hold",
        "BLOCKED": "hold",
    },
    "verify_candidate": {"PASS": "guard_delivery", "FAIL": "hold", "UNCERTAIN": "hold"},
    "guard_delivery": {"LOCAL": "restore_validation", "HOLD": "hold"},
    "restore_validation": {"RESTORED": "synchronize_base", "HOLD": "hold"},
    "synchronize_base": {
        "ACCEPTED": "retain",
        "UNCHANGED": "retain",
        "RESTORED_UNACCEPTED": "retain",
        "HOLD": "hold",
    },
    "wait_provider": {
        "RESUMED": "reconcile",
        "WAIT": "@factory_lad_provider_wait",
        "HOLD": "hold",
    },
    "retain": {"RETAINED": "settle", "HOLD": "hold"},
    "settle": {"SETTLED": "claim", "HOLD": "hold"},
    "hold": {"WAIT_OPERATOR": "@factory_hold", "PENDING": "hold"},
}
NATIVE = {
    "investigate": "investigate",
    "repair": "repair",
    "review_candidate": "review",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def derive_graph(config: dict, ci_config: dict, factory_home: Path) -> dict:
    """Capture CI role assets in a serial Factory graph without dispatch."""
    routes = copy.deepcopy(ROUTES)
    destinations = sorted(set(routes) - set(NATIVE) | {"normalize"})
    routes["reconcile"] = {
        "READY": "claim",
        **{name.upper(): name for name in destinations},
    }
    graph = {
        "format": "millrace-router/v4",
        "limits": {"max_parallel_invocations": 1},
        "bindings": {
            "command": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": "python3",
                "config": {},
            }
        },
        "control_files": [
            "scripts/step.py",
            "schemas/factory-input.json",
            "context/contract.md",
        ],
        "planes": {
            "factory": {"workspace": "factory", "start": "reconcile", "nodes": {}}
        },
    }
    nodes = graph["planes"]["factory"]["nodes"]
    for name, edges in routes.items():
        invoke = {
            "binding": "command",
            "cwd": ".",
            "config": {"script": "scripts/step.py", "args": [name, str(factory_home)]},
        }
        if name in NATIVE:
            role = NATIVE[name]
            selections = ci_config["role_bindings"]
            selection = selections[role][0] if role == "review" else selections[role]
            binding_name = role
            graph["bindings"][binding_name] = copy.deepcopy(selection["binding"])
            invoke = {
                "binding": binding_name,
                "cwd": ".",
                "inputs": ["active-role.json"],
                "config": {"args": [role]},
            }
            if selection["binding"]["adapter"] != "command":
                graph["bindings"][binding_name]["config"]["profile"] = selection[
                    "profile_ref"
                ]["path"]
                invoke["config"] = {"prompt": selection["prompt_ref"]["path"]}
            for key in ("profile_ref", "prompt_ref", "skill_ref", "result_schema_ref"):
                if selection[key] is not None:
                    graph["control_files"].append(selection[key]["path"])
        policy = config["timeouts"]["node_deadlines"].get(name)
        if policy is not None:
            invoke["timeout_seconds"] = policy["seconds"]
        if name in {"reproduce", "check_candidate"}:
            ids = (
                ci_config["reproduction"]["command_ids"]
                if name == "reproduce"
                else [row["id"] for row in ci_config["commands"]]
            )
            maximum = max(
                (
                    row["timeout_seconds"]
                    for row in ci_config["commands"]
                    if row["id"] in ids
                ),
                default=0,
            )
            bookkeeping = ci_config["node_deadlines"]["checks"]["bookkeeping_seconds"]
            required = maximum + bookkeeping
            if policy is not None and policy["seconds"] < required:
                raise fr.FactoryError("factory_check_node_budget_too_short")
            invoke["timeout_seconds"] = policy["seconds"] if policy else required
        pause = {"pause": {"reason": "factory_hold", "resume": "reconcile"}}
        nodes[name] = {
            "invoke": invoke,
            "result": "json-v1",
            "outcomes": list(edges),
            "on": {
                outcome: (
                    {"pause": {"reason": target[1:], "resume": "reconcile"}}
                    if target.startswith("@")
                    else {"goto": target}
                )
                for outcome, target in edges.items()
            },
            "on_error": pause,
            "on_unknown": pause,
        }
        if name in {"reproduce", "check_candidate"}:
            guard = "select_reproduction" if name == "reproduce" else "select_check"
            nodes[name]["on_error"] = nodes[name]["on_unknown"] = {"goto": guard}
    graph["control_files"] = sorted(set(graph["control_files"]))
    return graph


class Home:
    """Factory-owned intake and projections. CI keeps its separate target and lease."""

    def __init__(self, home: Path) -> None:
        self.home = canonical_dir(str(home), "invalid_factory_home")
        self.workspace = self.home / "project/factory"
        self.record = strict_json(_read_record(self.home / "factory.json"))
        self.factory_id = identity(self.record["factory_id"])
        if self.record["factory_home"] != str(self.home):
            raise fr.FactoryError("factory_home_changed")
        self.ci = V2Home(Path(self.record["ci_home"]))
        self.config = fr.validate_config(self.read_ref(self.record["config_ref"]))
        if self.config["validation_lane"]["workflow_home"] != str(self.ci.home):
            raise fr.FactoryError("factory_ci_home_changed")
        self.config_sha256 = self.record["config_ref"]["sha256"]
        self.intake = Intake(
            self.home / "intake.sqlite3",
            self.factory_id,
            validate_payload=lambda raw: fr.validate_input(strict_json(raw)),
            validate_claim=self._claim,
            validate_decision=self._decision,
            validate_decision_aftermath=self._aftermath,
            task_id_for_key=lambda key: (
                uuid.uuid5(uuid.UUID(hex=self.factory_id), key).hex
            ),
            decision_receipt=self._receipt,
            authority_sha256=hashlib.sha256(
                (self.home / "factory.json").read_bytes()
            ).hexdigest(),
        )

    def _claim(self, value: dict) -> None:
        fr.require_fields(
            value, "task_id request_sha256 factory_claim_ref", "invalid_factory_claim"
        )
        claim = fr.validate_record(
            self.read_ref(value["factory_claim_ref"]), "millrace-plus-factory-claim/v1"
        )
        if (
            claim["factory_id"] != self.factory_id
            or claim["job_id"] != value["task_id"]
            or claim["input_sha256"] != value["request_sha256"]
            or claim["config_sha256"] != self.config_sha256
        ):
            raise fr.FactoryError("factory_claim_changed")

    def _decision(self, value: dict) -> None:
        fr.require_fields(
            value, "task_id decision evidence", "invalid_factory_decision"
        )
        identity(value["task_id"])
        if value["decision"] not in {
            "retry-evidence",
            "retry-verification",
            "continue-lad",
            "approve-delivery",
            "close",
            "acknowledge-cancelled",
            "cancel",
            "settle",
        }:
            raise fr.FactoryError("invalid_factory_decision")
        fr.ref(value["evidence"])
        self.raw_ref(value["evidence"])

    def _receipt(self, request_id: str, intent: dict) -> dict:
        return {
            "format": "millrace-plus-factory-decision-receipt/v1",
            "factory_id": self.factory_id,
            "job_id": intent["task_id"],
            "request_id": request_id,
            "intent_sha256": ci.digest(intent),
            "decision": intent["decision"],
            "evidence_ref": intent["evidence"],
        }

    def _aftermath(self, task: dict, intent: dict, receipt: dict) -> None:
        if receipt != self._receipt(receipt["request_id"], intent):
            raise fr.FactoryError("factory_decision_receipt_changed")
        if task["terminal_receipt"] == receipt:
            if intent["decision"] not in {"settle", "cancel"}:
                raise fr.FactoryError("factory_terminal_decision_changed")

    @contextmanager
    def lock(self):
        fd = os.open(
            self.home / "state.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise fr.FactoryError("factory_busy") from exc
            yield
        finally:
            os.close(fd)

    def raw_ref(self, value: object) -> bytes:
        ref = fr.ref(value)
        path = self.workspace / ref["path"]
        if not path.exists():
            return self.ci.resolve_ref(ref).read_bytes()
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise fr.FactoryError("factory_ref_link")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise fr.FactoryError("factory_ref_not_regular")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise fr.FactoryError("factory_ref_changed")
        return raw

    def read_ref(self, ref: object) -> dict:
        value = strict_json(self.raw_ref(ref))
        if not isinstance(value, dict):
            raise fr.FactoryError("factory_record_not_object")
        return value

    def retain_bytes(self, raw: bytes, suffix: str = ".json") -> dict:
        digest = hashlib.sha256(raw).hexdigest()
        relative = "factory/" + self.factory_id + "/records/" + digest + suffix
        write_exact(self.workspace / relative, raw)
        write_exact(self.ci.workspace / relative, raw)
        return {"path": relative, "sha256": digest}

    def retain(self, value: object) -> dict:
        return self.retain_bytes(canonical(value))

    def active(self) -> dict | None:
        rows = [
            row
            for row in self.intake.tasks()
            if row["status"] in {"claimed", "running", "held"}
        ]
        if len(rows) > 1:
            raise fr.FactoryError("multiple_active_factory_jobs")
        return rows[0] if rows else None

    def state(self, job: str) -> dict:
        path = self.workspace / "jobs" / identity(job) / "state.json"
        value = fr.validate_state(strict_json(_read_record(path)))
        if value["factory_id"] != self.factory_id or value["job_id"] != job:
            raise fr.FactoryError("factory_state_identity_changed")
        for ref in value["accepted_refs"].values():
            self.raw_ref(ref)
        self.raw_ref(value["input_ref"])
        claim = fr.validate_record(
            self.read_ref(value["claim_ref"]), "millrace-plus-factory-claim/v1"
        )
        reservations = fr.validate_record(
            self.read_ref(value["reservations_ref"]),
            "millrace-plus-factory-reservations/v1",
        )
        if (
            claim["job_id"] != job
            or claim["factory_id"] != self.factory_id
            or claim["input_sha256"] != value["input_ref"]["sha256"]
            or reservations["job_id"] != job
        ):
            raise fr.FactoryError("factory_state_claim_changed")
        facts = self.read_ref(value["accepted_refs"]["facts"])
        if (
            facts["input_sha256"] != claim["input_sha256"]
            or facts["scope_ref"] != claim["scope_ref"]
        ):
            raise fr.FactoryError("factory_state_basis_changed")
        base = fr.validate_base(self.read_ref(claim["base_candidate_ref"]))
        candidate_id = self.ci.read_ref(base["candidate_ref"])["candidate_id"]
        if (
            facts.get("base_candidate_ref", base["candidate_ref"])
            != base["candidate_ref"]
        ):
            raise fr.FactoryError("factory_state_base_changed")
        for row in reservations["reservations"]:
            if (
                row["input_sha256"] != claim["input_sha256"]
                or row["scope_sha256"] != claim["scope_ref"]["sha256"]
                or row["base_candidate_id"] != candidate_id
                or row["profile_ref"] != self.config["role_profile"]["profile_ref"]
            ):
                raise fr.FactoryError("factory_reservation_basis_changed")
            for key in (
                "definition_ref",
                "profile_ref",
                "request_ref",
                "result_ref",
                "completion_ref",
                "acceptance_ref",
            ):
                if row[key] is not None:
                    self.raw_ref(row[key])
        if value["baseline_ref"] is not None:
            baseline = fr.validate_record(
                self.read_ref(value["baseline_ref"]),
                "millrace-plus-factory-baseline/v1",
            )
            if baseline["job_id"] != job or baseline["scope_ref"] != claim["scope_ref"]:
                raise fr.FactoryError("factory_state_baseline_changed")
        return value

    def facts(self, state: dict) -> dict:
        return self.read_ref(state["accepted_refs"]["facts"])

    def save(self, state: dict, facts: dict, phase: str) -> dict:
        path = self.workspace / "jobs" / state["job_id"] / "state.json"
        current = fr.validate_state(strict_json(_read_record(path)))
        if current != state:
            raise fr.FactoryError("factory_state_conflict")
        refs = state["accepted_refs"] | {"facts": self.retain(facts)}
        baseline_ref = facts.get("baseline_ref", state["baseline_ref"])
        if state["baseline_ref"] not in (None, baseline_ref):
            raise fr.FactoryError("factory_baseline_changed")
        saved = fr.integrity(
            state
            | {
                "phase": phase,
                "version": state["version"] + 1,
                "accepted_refs": refs,
                "baseline_ref": baseline_ref,
            }
        )
        fr.validate_state(saved)
        self.retain(saved)
        self.project_job_records(saved, facts)
        write_replace(path, canonical(saved))
        return saved

    def project_job_records(self, state: dict, facts: dict) -> None:
        """Named job views point at exact retained evidence; reads never repair them."""
        root = self.workspace / "jobs" / state["job_id"]
        immutable = {
            "input.json": state["input_ref"],
            "scope.json": facts["scope_ref"],
            "baseline.json": state["baseline_ref"],
        }
        for name, ref in immutable.items():
            if ref is not None:
                write_exact(root / name, self.raw_ref(ref))
        subject = fr.validate_input(self.read_ref(state["input_ref"]))
        if subject["feedback_ref"] is not None:
            write_exact(
                root / "feedback/original.json", self.raw_ref(subject["feedback_ref"])
            )
        for ref in subject["evidence_refs"]:
            write_exact(root / "feedback" / ref["sha256"], self.raw_ref(ref))
        views = {
            "reservations.json": state["reservations_ref"],
            "candidate/candidate.json": facts.get("candidate_ref"),
            "candidate/binding.json": facts.get("candidate_binding_ref"),
            "lad-binding.json": facts.get("lad_binding_ref"),
            "review/review.json": facts.get("review_ref"),
        }
        for role in ("investigate", "repair", "review"):
            ref = facts.get(role + "_projection_ref")
            if ref is not None:
                attempt = self.ci.read_ref(ref)["attempt_id"]
                views["roles/" + role + "/" + attempt + ".json"] = ref
        for ref in [
            *facts.get("reproductions", []),
            *facts.get("checks", []),
            *facts.get("engineering_refs", []),
        ]:
            views["checks/" + ref["sha256"] + ".json"] = ref
        for name, ref in views.items():
            if ref is None:
                continue
            raw = self.raw_ref(ref)
            path = root / name
            if not path.exists() or path.read_bytes() != raw:
                write_replace(path, raw)
        if facts.get("candidate_ref") is not None:
            frozen = self.ci.read_ref(facts["candidate_ref"])
            tree = self.ci.read_ref(frozen["tree_manifest_ref"])
            for entry in tree["entries"]:
                write_exact(
                    root / "candidate/objects" / entry["sha256"],
                    self.raw_ref(entry["payload_ref"]),
                )
        from .factory_candidate import materialization

        lease = (
            materialization(self)
            if facts.get("source_binding_ref") is not None
            else None
        )
        if lease is not None:
            raw = canonical(lease)
            path = root / "materialization.json"
            if not path.exists() or path.read_bytes() != raw:
                write_replace(path, raw)

    def apply_decision_plan(self, request_id: str, receipt: dict) -> dict:
        """Project an accepted exact decision once, including interruption replay."""
        plan = strict_json(
            (self.workspace / "decisions" / (request_id + ".plan.json")).read_bytes()
        )
        fr.require_fields(
            plan, "intent state_ref facts_ref phase", "invalid_factory_decision_plan"
        )
        if receipt != self._receipt(request_id, plan["intent"]):
            raise fr.FactoryError("factory_decision_plan_changed")
        original = fr.validate_state(self.read_ref(plan["state_ref"]))
        current = self.state(original["job_id"])
        receipt_ref = self.retain(receipt)
        if receipt_ref in current["decision_refs"]:
            return current
        if current != original:
            raise fr.FactoryError("factory_decision_projection_conflict")
        facts_ref = plan["facts_ref"]
        if (
            plan["intent"]["decision"] == "continue-lad"
            and plan["phase"] != "resume_lad_parent"
        ):
            result = strict_json(
                _read_record(
                    self.workspace / "decisions" / (request_id + ".lad-result.json")
                )
            )
            facts_ref = self.retain(
                self.read_ref(facts_ref) | {"lad_continuation_ref": self.retain(result)}
            )
        saved = fr.integrity(
            current
            | {
                "phase": plan["phase"],
                "version": current["version"] + 1,
                "decision_refs": [*current["decision_refs"], receipt_ref],
                "accepted_refs": current["accepted_refs"] | {"facts": facts_ref},
            }
        )
        fr.validate_state(saved)
        self.retain(saved)
        write_exact(
            self.workspace
            / "jobs"
            / current["job_id"]
            / "decisions"
            / (request_id + ".json"),
            canonical(receipt),
        )
        write_replace(
            self.workspace / "jobs" / current["job_id"] / "state.json", canonical(saved)
        )
        return saved

    def reconcile_claim(self, job: str) -> dict:
        """Publish an exact pre-transaction claim projection after its writer stops."""
        path = self.workspace / "jobs" / identity(job) / "state.json"
        if path.exists():
            return self.state(job)
        proposed = fr.validate_state(
            strict_json(_read_record(path.with_name("claim-state.json")))
        )
        task = self.intake.tasks(job)[0]
        if (
            task["claim"] is None
            or proposed["factory_id"] != self.factory_id
            or proposed["job_id"] != job
            or proposed["claim_ref"] != task["claim"]["factory_claim_ref"]
            or proposed["input_ref"]["sha256"] != task["request_sha256"]
            or proposed["phase"] != "normalize"
            or proposed["version"] != 1
            or proposed["baseline_ref"] is not None
            or proposed["decision_refs"]
        ):
            raise fr.FactoryError("factory_claim_projection_changed")
        facts = self.read_ref(proposed["accepted_refs"]["facts"])
        context = facts["claim_context"]
        claim = self.read_ref(proposed["claim_ref"])
        expected = {
            "input_sha256": task["request_sha256"],
            "pending": None,
            "checks": [],
            "reproductions": [],
            "completion_refs": [],
            "verification_occurrence": 0,
            "claim_context": context,
            "scope_ref": claim["scope_ref"],
            "accepted": False,
        }
        reservations = fr.validate_record(
            self.read_ref(proposed["reservations_ref"]),
            "millrace-plus-factory-reservations/v1",
        )
        if (
            facts != expected
            or reservations["job_id"] != job
            or reservations["reservations"]
        ):
            raise fr.FactoryError("factory_claim_projection_changed")
        if (
            context["instance_id"] != claim["parent_instance_id"]
            or context["run_id"] != claim["parent_run_id"]
            or context["node"] != "claim"
        ):
            raise fr.FactoryError("factory_claim_producer_changed")
        from .router import control

        view = control.inspect_run(
            context["instance_id"],
            context["run_id"],
            state_home=Path(context["state_home"]),
        )
        invocations = [
            row for row in view["invocations"] if row["id"] == context["invocation_id"]
        ]
        completions = [
            row
            for row in view["completions"]
            if row["invocation_id"] == context["invocation_id"]
        ]
        if (
            len(invocations) != 1
            or len(completions) != 1
            or invocations[0]["writer_state"] != "stopped"
            or completions[0]["effective_writer"] != "stopped"
            or any(
                invocations[0].get(field) != context[field]
                for field in (
                    "plane_id",
                    "node",
                    "attempt",
                    "pid",
                    "cwd",
                    "argv",
                    "deadline",
                    "revision_id",
                )
            )
        ):
            raise fr.FactoryError("factory_claim_writer_unknown")
        if task["status"] not in {"claimed", "running", "held"}:
            raise fr.FactoryError("factory_claim_projection_advanced")
        instance = control.inspect_instance(
            context["instance_id"], state_home=Path(context["state_home"])
        )
        seen = False
        for item in instance["runs"]:
            if item["id"] == context["run_id"]:
                seen = True
                observed = view
                later = [
                    row
                    for row in observed["invocations"]
                    if row["plane_id"] == context["plane_id"]
                    and row["attempt"] > context["attempt"]
                ]
            elif seen:
                observed = control.inspect_run(
                    context["instance_id"],
                    item["id"],
                    state_home=Path(context["state_home"]),
                )
                later = observed["invocations"]
            else:
                continue
            if any(row["node"] != "reconcile" for row in later):
                raise fr.FactoryError("factory_claim_projection_advanced")
        if not seen:
            raise fr.FactoryError("factory_claim_producer_changed")
        with self.lock():
            write_exact(path, canonical(proposed))
        return self.state(job)

    def reconcile_decisions(self, job: str) -> None:
        """Named reconciliation publishes only already accepted, exact saved plans."""
        for row in self.intake.decisions(job):
            if row["intent"]["decision"] == "cancel":
                from .factory_cli import cancel

                cancel(self, job, row["request_id"])
            elif row["intent"]["decision"] != "settle":
                with self.lock():
                    self.apply_decision_plan(row["request_id"], row["receipt"])

    def base_ref(self) -> dict:
        pointer = strict_json((self.home / "accepted-base.json").read_bytes())
        self.base_chain(pointer)
        return pointer

    def base_chain(self, ref: dict) -> list[dict]:
        rows, seen = [], set()
        while ref is not None:
            key = canonical(ref)
            if key in seen:
                raise fr.FactoryError("factory_base_cycle")
            seen.add(key)
            row = fr.validate_base(self.read_ref(ref))
            if row["factory_id"] != self.factory_id:
                raise fr.FactoryError("factory_base_owner_changed")
            ci.validate_candidate(self.ci.read_ref(row["candidate_ref"]))
            if row["outcome_ref"] is not None:
                outcome = fr.validate_record(
                    self.read_ref(row["outcome_ref"]),
                    "millrace-plus-factory-outcome/v1",
                )
                task = self.intake.tasks(outcome["job_id"])[0]
                if (
                    outcome["status"] != "local_candidate"
                    or outcome["candidate_ref"] != row["candidate_ref"]
                    or outcome["accepted_base_ref"] != row["previous_ref"]
                    or self.read_ref(outcome["terminal_receipt_ref"])
                    != task["terminal_receipt"]
                    or task["status"] != "succeeded"
                ):
                    raise fr.FactoryError("factory_base_acceptance_changed")
            if rows and rows[-1]["sequence"] != row["sequence"] + 1:
                raise fr.FactoryError("factory_base_sequence_changed")
            rows.append(row)
            ref = row["previous_ref"]
        return rows

    def lad_tasks(self) -> set[str]:
        result = set()
        for row in self.intake.tasks():
            path = self.workspace / "jobs" / row["task_id"] / "state.json"
            if path.exists():
                facts = self.facts(self.state(row["task_id"]))
                if facts.get("lad_task_id"):
                    result.add(facts["lad_task_id"])
        return result

    def submit(self, file: Path) -> dict:
        raw = file.read_bytes()
        subject = fr.validate_input(strict_json(raw))
        if subject["repository"] != self.config["repository"]:
            raise fr.FactoryError("factory_repository_mismatch")
        self._capture_input_refs(subject, file.parent)
        if subject["source_kind"] != "task":
            original = self.read_ref(subject["feedback_ref"])
            if subject["source_kind"] == "ci_event":
                from .cicd_records import _validate_event, event_identity

                original = _validate_event(
                    canonical(original), self.config["repository"], legacy_limits=False
                )
                original_id = event_identity(original)
            else:
                from .cicd_feedback import feedback_identity, validate_feedback

                original = validate_feedback(original, self.config["repository"])
                expected_kind = (
                    "bug_report"
                    if subject["source_kind"] == "user_report"
                    else "production_incident"
                )
                if original["kind"] != expected_kind:
                    raise fr.FactoryError("factory_feedback_kind_mismatch")
                original_id = ci.digest(feedback_identity(original))
            prefix = Path(subject["feedback_ref"]["path"]).parent
            self._capture_input_refs(original, file.parent / prefix, prefix=prefix)
            if original_id != subject["source_id"]:
                raise fr.FactoryError("factory_feedback_identity_mismatch")
        with self.lock():
            result = self.intake.submit(fr.source_key(subject), raw)
        wake(self)
        return result

    def _capture_input_refs(
        self, value: object, source: Path, *, prefix: Path = Path()
    ) -> None:
        if isinstance(value, dict):
            if set(value) == {"path", "sha256"}:
                ref = fr.ref(value)
                path = source / ref["path"]
                if any(parent.is_symlink() for parent in (path, *path.parents)):
                    raise fr.FactoryError("factory_input_ref_link")
                raw = path.read_bytes()
                if (
                    path.stat().st_nlink != 1
                    or hashlib.sha256(raw).hexdigest() != ref["sha256"]
                ):
                    raise fr.FactoryError("factory_input_ref_changed")
                write_exact(
                    self.workspace / prefix / ref["path"],
                    raw,
                    "factory_input_ref_conflict",
                )
                write_exact(
                    self.ci.workspace / prefix / ref["path"],
                    raw,
                    "factory_input_ref_conflict",
                )
            else:
                for item in value.values():
                    self._capture_input_refs(item, source, prefix=prefix)
        elif isinstance(value, list):
            for item in value:
                self._capture_input_refs(item, source, prefix=prefix)


def _capture_configuration(value: object, source: Path, destination: Path) -> None:
    """Retain selected operator files without changing nested relative references."""
    if isinstance(value, dict):
        if set(value) == {"path", "sha256"}:
            if Path(str(value["path"])).is_absolute():
                # Service independently pins the existing canonical OS profile.
                return
            ref = fr.ref(value)
            path = source / ref["path"]
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise fr.FactoryError("factory_configuration_link")
            raw = path.read_bytes()
            if (
                path.stat().st_nlink != 1
                or hashlib.sha256(raw).hexdigest() != ref["sha256"]
            ):
                raise fr.FactoryError("factory_configuration_changed")
            write_exact(
                destination / ref["path"], raw, "factory_configuration_conflict"
            )
        else:
            for item in value.values():
                _capture_configuration(item, source, destination)
    elif isinstance(value, list):
        for item in value:
            _capture_configuration(item, source, destination)


def initialize(home: Path, config_file: Path) -> dict:
    """Derive actor artifacts, then bind selected shared bootstrap authority once.

    An incomplete bootstrap returns existing CI preparation metadata and its
    UNAPPROVED suggestion. This creates no Core instance or source candidate.
    There is no additional Factory prepare command or public record format.
    """
    from .router import control

    home = canonical_dir(str(home.absolute()), "invalid_factory_home", directory=False)
    authored = strict_json(config_file.read_bytes())
    authored = fr.validate_config(authored)
    validation = authored["validation_lane"]
    ci_home = Path(validation["workflow_home"])
    prepared = strict_json((ci_home / "preparation.json").read_bytes())
    main = ci_home / "project/main"
    prepared_ref = prepared["prepared_config_ref"]
    raw = (main / prepared_ref["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != prepared_ref["sha256"]:
        raise fr.FactoryError("factory_ci_preparation_changed")
    ci_config = strict_json(raw)
    if ci_config["source_policy"]["mode"] != "factory":
        raise fr.FactoryError("factory_source_policy_required")
    if any(
        overlap(home, Path(root))
        for root in (
            authored["development_lane"]["target_root"],
            validation["target_root"],
            authored["development_lane"]["service_home"],
            str(ci_home),
        )
    ):
        raise fr.FactoryError("factory_roots_overlap")
    dev_root = Path(authored["development_lane"]["target_root"])
    val_root = Path(validation["target_root"])
    if overlap(dev_root, val_root):
        raise fr.FactoryError("factory_lanes_overlap")
    if filesystem_identity(val_root) != validation["target_identity"]:
        raise fr.FactoryError("factory_validation_identity_changed")
    # Definition derivation excludes the authority-dependent final CI config Ref.
    # Exact final config and authority are captured before instance registration.
    immutable_input = copy.deepcopy(authored)
    immutable_input["local_authority"]["authority_ref"] = None
    immutable_input["validation_lane"]["config_ref"] = None
    inputs = ci.digest(immutable_input)
    manifest_path = home / "preparation.json"
    if manifest_path.exists():
        manifest = strict_json(manifest_path.read_bytes())
        if manifest["inputs_sha256"] != inputs:
            raise fr.FactoryError("factory_preparation_conflict")
        factory_id = manifest["service_id"]
    else:
        if home.exists() and any(home.iterdir()):
            raise fr.FactoryError("factory_home_not_empty")
        factory_id = uuid.uuid4().hex
        home.mkdir(mode=0o700, exist_ok=True)
    workspace = home / "project/factory"
    workspace.mkdir(parents=True, exist_ok=True)
    compatibility = home / "project/main"
    compatibility.mkdir(exist_ok=True)
    graph = derive_graph(authored, ci_config, home)
    source = Path(__file__).parent / "workflows/software-factory"
    workflow = home / "workflow"
    for name in graph["control_files"]:
        origin = (
            source / "schemas/input.json"
            if name == "schemas/factory-input.json"
            else source / name
            if (source / name).is_file()
            else ci_home / "workflow" / name
        )
        write_exact(workflow / name, origin.read_bytes(), "factory_control_conflict")
    graph_raw = canonical(graph)
    definition_ref = {
        "path": "factory/" + factory_id + "/definition.json",
        "sha256": hashlib.sha256(graph_raw).hexdigest(),
    }
    for root in (workspace, compatibility, main):
        write_exact(
            root / definition_ref["path"], graph_raw, "factory_definition_conflict"
        )
    write_exact(workflow / "workflow.json", graph_raw, "factory_definition_conflict")
    hashes = {
        name: hashlib.sha256((workflow / name).read_bytes()).hexdigest()
        for name in graph["control_files"]
    }
    settings = {
        "path": list(
            dict.fromkeys([str(Path(sys.executable).parent), *os.get_exec_path()])
        ),
        "env_allowlist": ["HOME"],
        "host_paths": {"cicd_target": str(val_root)},
    }
    if not manifest_path.exists():
        suggestion = strict_json((main / "UNAPPROVED-authority.json").read_bytes())
        suggestion["operations"] = list(
            dict.fromkeys(
                [
                    *suggestion["operations"],
                    *authored["local_authority"]["allowed_operations"],
                ]
            )
        )
        suggestion["actors"].append(
            {
                "execution_home": str(home),
                "instance_id": None,
                "definition_ref": definition_ref,
            }
        )
        suggested_path = compatibility / "UNAPPROVED-authority.json"
        write_exact(suggested_path, canonical(ci.validate_local_authority(suggestion)))
        manifest = {
            "format": "millrace-plus-cicd-preparation/v1",
            "service_id": factory_id,
            "service_home": str(home),
            "inputs_sha256": inputs,
            "target_root": str(val_root),
            "target_identity": filesystem_identity(val_root),
            "head": prepared["head"],
            "owner_ref": prepared["owner_ref"],
            "definition_ref": definition_ref,
            "state_home": str(home / "state"),
            "create_request_id": uuid.uuid5(
                uuid.UUID(hex=factory_id), "create-factory"
            ).hex,
            "settings": settings,
            "idle_wait_policy": prepared["idle_wait_policy"],
            "prepared_config_ref": prepared_ref,
            "control_hashes": hashes,
            "suggested_authority_file": str(suggested_path),
            "authority_status": "UNAPPROVED",
        }
        write_exact(manifest_path, canonical(manifest))
    if (
        manifest["definition_ref"] != definition_ref
        or manifest["control_hashes"] != hashes
    ):
        raise fr.FactoryError("factory_prepared_artifacts_changed")
    if not (ci_home / "cicd-v2.json").exists():
        return manifest
    ci_owner = V2Home(ci_home)
    if (
        validation["config_ref"] != ci_owner.config_ref
        or validation["service_id"] != ci_owner.service_id
        or validation["target_root"] != str(ci_owner.target)
        or authored["path_policy_ref"] != ci_owner.config["path_policy_ref"]
    ):
        raise fr.FactoryError("factory_ci_binding_changed")
    authority_ref = authored["local_authority"]["authority_ref"]
    if authority_ref != ci_owner.config["local_authority_ref"]:
        raise fr.FactoryError("factory_shared_authority_mismatch")
    authority = ci.validate_local_authority(ci_owner.read_ref(authority_ref))
    expected_actor = {
        "execution_home": str(home),
        "instance_id": None,
        "definition_ref": definition_ref,
    }
    if expected_actor not in authority["actors"] or not set(
        authored["local_authority"]["allowed_operations"]
    ).issubset(authority["operations"]):
        raise fr.FactoryError("factory_actor_not_authorized")
    # Lane refs resolve in their owner home, not beside the authored config.
    from .lad_service import Service

    copied = copy.deepcopy(authored)
    lane_files = [
        (
            validation["config_ref"],
            ci_owner.resolve_ref(validation["config_ref"]).read_bytes(),
        ),
        (
            authored["development_lane"]["service_config_ref"],
            (
                Service(Path(authored["development_lane"]["service_home"])).home
                / "service.json"
            ).read_bytes(),
        ),
        (authority_ref, ci_owner.resolve_ref(authority_ref).read_bytes()),
        (
            authored["path_policy_ref"],
            ci_owner.resolve_ref(authored["path_policy_ref"]).read_bytes(),
        ),
    ]
    for ref, raw in lane_files:
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise fr.FactoryError("factory_lane_record_changed")
        write_exact(workspace / ref["path"], raw, "factory_configuration_conflict")
    copied["validation_lane"]["config_ref"] = None
    copied["development_lane"]["service_config_ref"] = None
    copied["local_authority"]["authority_ref"] = None
    copied["path_policy_ref"] = None
    _capture_configuration(copied, config_file.parent, workspace)
    _capture_configuration(copied, config_file.parent, ci_owner.workspace)
    write_exact(
        compatibility / authority_ref["path"],
        ci_owner.resolve_ref(authority_ref).read_bytes(),
    )
    config_raw = canonical(authored)
    config_ref = {
        "path": "factory/" + factory_id + "/config.json",
        "sha256": hashlib.sha256(config_raw).hexdigest(),
    }
    write_exact(workspace / config_ref["path"], config_raw)
    write_exact(ci_owner.workspace / config_ref["path"], config_raw)
    record_file = home / "factory.json"
    if record_file.exists():
        record = strict_json(record_file.read_bytes())
        if record["config_ref"] != config_ref or record["factory_id"] != factory_id:
            raise fr.FactoryError("factory_initialization_conflict")
    else:
        record = {
            "format": "millrace-plus-factory-home/v1",
            "factory_id": factory_id,
            "factory_home": str(home),
            "ci_home": str(ci_home),
            "config_ref": config_ref,
            "created_at": _now(),
            "development_baseline_ref": None,
        }
        # A bootstrap object supplies only the existing retain/read operations.
        bootstrap = object.__new__(Home)
        bootstrap.home, bootstrap.workspace, bootstrap.ci = home, workspace, ci_owner
        bootstrap.factory_id, bootstrap.config, bootstrap.record = (
            factory_id,
            authored,
            record,
        )
        mapping = candidate.engineering_mapping(bootstrap)
        if mapping["snapshot"] is not None:
            # Capture only the selected typed snapshot's explicit provenance.
            # Historical records stay data; they grant no source/writer authority.
            bootstrap._capture_input_refs(mapping["snapshot"], config_file.parent)
        if (
            bootstrap.read_ref(authored["role_profile"]["profile_ref"])
            != ci_owner.config["role_bindings"]
        ):
            raise fr.FactoryError("factory_role_profile_mismatch")
        if (
            bootstrap.read_ref(authored["check_policy"]["ci_policy_ref"])
            != ci_owner.config["check_policy"]
        ):
            raise fr.FactoryError("factory_check_policy_mismatch")
        if authored["role_profile"]["require_independent_review_family"]:
            # CI role selections currently carry no declared family mapping.
            # Neither model spelling nor an unrelated L-Q1 binding attests it.
            raise fr.FactoryError("factory_independent_review_family_unavailable")
        from .cicd_source import _git_read

        anchor = authored["anchor"]
        for root in (dev_root, val_root):
            actual = (
                _git_read(
                    root,
                    ["rev-parse", "--show-object-format", "HEAD", "HEAD^{tree}"],
                    30,
                )
                .decode()
                .splitlines()
            )
            if actual != [
                anchor["object_format"],
                anchor["head_sha"],
                anchor["tree_sha"],
            ]:
                raise fr.FactoryError("factory_anchor_changed")
        from .factory_lad import Lane

        lane = Lane(bootstrap)
        # Before any work exists this dedicated lane must be parked and joined.
        bootstrap.lad_tasks = lambda: set()
        proof = lane.parked(None, caller_deadline=time.monotonic() + 30)
        if not proof["closed"]:
            raise fr.FactoryError(proof["reason"])
        baseline = candidate.development_baseline(bootstrap, deadline=time.time() + 30)
        record["development_baseline_ref"] = bootstrap.retain(baseline)
        write_exact(record_file, canonical(record))
    current = Home(home)
    if not (home / "intake.sqlite3").exists():
        current.intake.initialize()
    else:
        current.intake.tasks()
    with current.lock():
        receipt = control.create_instance(
            workflow / "workflow.json",
            home / "project",
            state_home=home / "state",
            request_id=manifest["create_request_id"],
            expected_state_version=0,
            path=tuple(manifest["settings"]["path"]),
            env_allowlist=tuple(manifest["settings"]["env_allowlist"]),
            host_paths=manifest["settings"]["host_paths"],
        )
        if receipt.get("status") != "applied":
            raise fr.FactoryError(str(receipt.get("code") or "factory_create_refused"))
        receipt_ref = current.retain(receipt)
        write_exact(compatibility / receipt_ref["path"], canonical(receipt))
        binding = ci.validate_actor_binding(
            {
                "format": ci.ACTOR_BINDING,
                "authority_ref": authority_ref,
                "execution_home": str(home),
                "instance_id": receipt["instance_id"],
                "definition_ref": definition_ref,
                "create_request_id": manifest["create_request_id"],
                "create_receipt_ref": receipt_ref,
            }
        )
        ci.verify_actor_binding(authority, binding)
        write_exact(compatibility / "actor-binding.json", canonical(binding))
        validation_result = control.validate_revision(
            receipt["instance_id"], state_home=home / "state"
        )
        if validation_result.get("valid") is not True:
            raise fr.FactoryError("factory_captured_revision_invalid")
        execution_path = home / "execution.json"
        if execution_path.exists():
            old = strict_json(execution_path.read_bytes())
            retained = current.read_ref(old["validation_ref"])
            if (
                retained["draft_revision"]["files"]
                != validation_result["draft_revision"]["files"]
            ):
                raise fr.FactoryError("factory_captured_revision_changed")
            validation_ref = old["validation_ref"]
        else:
            validation_ref = current.retain(validation_result)
            write_exact(
                compatibility / validation_ref["path"], canonical(validation_result)
            )
        descriptor = {
            "format": "millrace-plus-local-execution/v1",
            "execution_home": str(home),
            "instance_id": receipt["instance_id"],
            "state_home": str(home / "state"),
            "project_root": str(home / "project"),
            "host_paths": manifest["settings"]["host_paths"],
            "control_hashes": hashes,
            "poll_seconds": manifest["idle_wait_policy"]["poll_seconds"],
            "validated_draft_id": current.read_ref(validation_ref)["revision_id"],
            "validation_ref": validation_ref,
        }
        write_exact(execution_path, canonical(descriptor))
        if not (home / "accepted-base.json").exists():
            anchor_ref = candidate.bootstrap_anchor(
                current, current.read_ref(record["development_baseline_ref"])
            )
            base = fr.integrity(
                {
                    "format": fr.BASE,
                    "factory_id": factory_id,
                    "sequence": 1,
                    "previous_ref": None,
                    "candidate_ref": anchor_ref,
                    "source_binding_ref": None,
                    "outcome_ref": None,
                    "requirements_snapshot_ref": authored["check_policy"][
                        "requirements_snapshot_ref"
                    ],
                }
            )
            write_exact(
                home / "accepted-base.json",
                canonical(current.retain(fr.validate_base(base))),
            )
        current.base_ref()
    return {
        "factory_id": factory_id,
        "instance_id": receipt["instance_id"],
        "factory_home": str(home),
        "status": "initialized",
    }


def wake(home: Home) -> None:
    path = home.home / "intake.sock"
    if not path.exists():
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
        try:
            connection.sendto(b"wake", str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            pass


def step(node: str, home: Path, script: Path) -> dict:
    from ._factory_steps import step as execute

    return execute(node, home, script)
