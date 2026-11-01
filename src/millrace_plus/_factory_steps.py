"""Named local Factory graph operations. Routing stays in the captured graph."""

from __future__ import annotations

import os
import socket
import time
import uuid
from pathlib import Path

from . import cicd_candidates as facade
from . import cicd_v2_records as ci
from . import factory_candidate as candidate
from . import factory_records as fr
from .cicd_source import snapshot_policy, source_identity
from .factory_runtime import NATIVE, ROUTES, Home, _now, write_exact, write_replace
from .local_intake import canonical, strict_json

__all__ = ["Runtime", "step"]


class Runtime:
    """Execute one named graph node using its actual public invocation context."""

    def __init__(self, home: Home, script: Path, node: str) -> None:
        self.home, self.script, self.node = home, script, node
        self.controls = script.parent.parent
        self.context = facade.bind_invocation(
            home.home, "factory", node, script, os.getpid(), Path.cwd()
        )
        self.deadline = time.monotonic() + max(
            0, self.context["deadline"] - time.time()
        )

    def _save(self, state: dict, facts: dict, phase: str) -> dict:
        with self.home.lock():
            return self.home.save(state, facts, phase)

    def _transition(
        self, state: dict, facts: dict, outcome: str, summary: str | None = None
    ) -> dict:
        target = ROUTES[self.node][outcome]
        phase = "hold" if target.startswith("@") else target
        saved = self._save(state, facts, phase)
        return self._out(outcome, summary, [self.home.retain(saved)])

    @staticmethod
    def _out(
        outcome: str, summary: str | None = None, refs: list | None = None
    ) -> dict:
        return {
            "outcome": outcome,
            "summary": summary or outcome,
            "refs": [
                ref["path"] if isinstance(ref, dict) else ref for ref in refs or []
            ],
        }

    def _run(self, context: dict) -> dict:
        from .router import control

        return control.inspect_run(
            context["instance_id"],
            context["run_id"],
            state_home=Path(context["state_home"]),
        )

    def _completion(
        self, context: dict, *, native: str | None = None
    ) -> tuple[dict, dict]:
        run = self._run(context)
        rows = [
            row
            for row in run["invocations"]
            if (
                row["plane_id"] == context["plane_id"]
                and (
                    row["node"] == native and row["attempt"] == context["attempt"] + 1
                    if native
                    else row["id"] == context["invocation_id"]
                )
            )
        ]
        if len(rows) != 1 or rows[0]["writer_state"] != "stopped":
            raise fr.FactoryError("factory_producer_not_stopped")
        done = [
            row for row in run["completions"] if row["invocation_id"] == rows[0]["id"]
        ]
        if len(done) != 1 or done[0]["effective_writer"] != "stopped":
            raise fr.FactoryError("factory_completion_unproven")
        if rows[0]["revision_id"] != context["revision_id"]:
            raise fr.FactoryError("factory_producing_revision_changed")
        row = rows[0]
        actual = context | {
            "node": row["node"],
            "attempt": row["attempt"],
            "invocation_id": row["id"],
            "pid": row["pid"],
            "cwd": row["cwd"],
            "argv": row["argv"],
            "deadline": row["deadline"],
        }
        return actual, self.home.ci.retain(done[0])

    def _git_basis(self, state: dict) -> None:
        from .cicd_source import git_head

        if git_head(self.home.ci.target) != self.home.config["anchor"]["head_sha"]:
            raise fr.FactoryError("factory_validation_anchor_changed")
        if state["baseline_ref"] is None:
            raise fr.FactoryError("factory_baseline_missing")
        baseline = self.home.read_ref(state["baseline_ref"])
        indices = self.home.read_ref(baseline["index_ref"])
        for key, root in (
            ("development", Path(self.home.config["development_lane"]["target_root"])),
            ("validation", self.home.ci.target),
        ):
            if candidate.index_identity(root) != indices[key]:
                raise fr.FactoryError("factory_git_index_changed")

    def _request(self, state: dict, operation: str, **values) -> dict:
        self._git_basis(state)
        facts = self.home.facts(state)
        lease = candidate.materialization(self.home)
        authority = self.home.ci.config["local_authority_ref"]
        result = {
            "format": ci.CANDIDATE_REQUEST,
            "request_id": self.home.factory_id
            + "/"
            + state["job_id"]
            + "/"
            + self.node
            + "/"
            + str(facts.get("verification_occurrence", 0))
            + "/"
            + str(len(facts.get("checks", [])))
            + "/"
            + str(len(facts.get("reproductions", [])))
            + "/"
            + str(facts.get("evidence_occurrence", 0)),
            "operation": operation,
            "candidate_ref": facts.get("candidate_ref"),
            "subject_ref": None,
            "export_basis_ref": None,
            "role": None,
            "materialization_id": lease["materialization_id"]
            if lease is not None
            else None,
            "expected_materialization_version": lease["version"]
            if lease is not None
            else None,
            "authority_ref": authority,
            "approval_ref": authority,
            "command_id": None,
            "role_binding_id": None,
            "expected_target_sha256": source_identity(self.home.ci.observe()),
            "evidence_refs": [],
        }
        if operation == "export":
            result.update(
                candidate_ref=None,
                expected_target_sha256=None,
                authority_ref=None,
                approval_ref=None,
            )
        elif operation in {"review", "verify"}:
            result.update(authority_ref=None, approval_ref=None)
        result.update(values)
        return ci.validate_candidate_request(result)

    def _operate(
        self, state: dict, facts: dict, request: dict, following: str
    ) -> tuple[dict, dict, dict]:
        """Reserve before mutation; preserve the original intent after interruption."""
        if facts.get("pending"):
            raise fr.FactoryError("factory_operation_pending")
        pending = {
            "node": self.node,
            "context": self.context,
            "request_ref": self.home.retain(request),
            "result_ref": None,
        }
        facts = facts | {"pending": pending}
        state = self._save(state, facts, following)
        result = facade.operate_candidate(
            self.home.ci, request, self.controls, self.context
        )
        pending["result_ref"] = self.home.retain(result)
        facts = facts | {"pending": pending}
        state = self._save(state, facts, following)
        return state, facts, result

    def _accept_pending(self, state: dict, facts: dict) -> tuple[dict, dict]:
        self._git_basis(state)
        pending = facts.get("pending")
        if pending is None:
            return state, facts
        request = self.home.read_ref(pending["request_ref"])
        if pending["result_ref"] is None:
            result_path = self.home.ci.workspace / (
                ci.digest(request["request_id"]) + ".candidate-result.json"
            )
            if not result_path.exists():
                raise fr.FactoryError("factory_operation_result_unknown")
            result = ci.validate_candidate_result(strict_json(result_path.read_bytes()))
        else:
            result = ci.validate_candidate_result(
                self.home.read_ref(pending["result_ref"])
            )
        if result["request_sha256"] != ci.digest(request):
            raise fr.FactoryError("factory_operation_request_changed")
        lease = candidate.materialization(self.home)
        operations = [
            self.home.ci.read_ref(ref)
            for ref in result["artifact_refs"]
            if self.home.ci.read_ref(ref).get("format") == ci.MATERIALIZATION_OPERATION
        ]
        if len(operations) != 1 or lease is None:
            raise fr.FactoryError("factory_operation_evidence_missing")
        _, completion_ref = self._completion(pending["context"])
        lease = facade.accept_materialization_operation(
            self.home.ci,
            lease["materialization_id"],
            operations[0]["operation_id"],
            completion_ref,
            lease["version"],
        )
        facts = facts | {
            "pending": None,
            "completion_refs": list(
                dict.fromkeys(
                    canonical(ref)
                    for ref in [*facts.get("completion_refs", []), completion_ref]
                )
            ),
        }
        facts["completion_refs"] = [
            strict_json(raw) for raw in facts["completion_refs"]
        ]
        if request["operation"] == "check":
            accepted = strict_json(
                (
                    self.home.ci.workspace
                    / (operations[0]["operation_id"] + ".check-accepted.json")
                ).read_bytes()
            )
            key = (
                "reproductions"
                if pending["node"] == "reproduce"
                else "probes"
                if pending["node"] == "probe"
                else "checks"
            )
            facts[key] = [*facts.get(key, []), accepted["acceptance_ref"]]
            if key != "probes":
                evidence, provenance = candidate.engineering_evidence(
                    self.home,
                    state,
                    facts,
                    accepted["acceptance_ref"],
                    accepted["product_ref"],
                    baseline=key == "reproductions",
                )
                facts["engineering_refs"] = [
                    *facts.get("engineering_refs", []),
                    *evidence,
                ]
                facts["engineering_adapter_refs"] = [
                    *facts.get("engineering_adapter_refs", []),
                    *provenance,
                ]
        elif request["operation"] == "restore":
            facts["restoration_ref"] = lease["restoration_ref"]
        state = self._save(state, facts, self.node)
        return state, facts

    def _allowed(self, operation: str) -> None:
        if operation not in self.home.config["local_authority"]["allowed_operations"]:
            raise fr.FactoryError("factory_operation_not_authorized")

    def _invalidate_pointer(self, facts: dict | None = None) -> None:
        pointer_path = self.home.workspace / "active-role.json"
        if not pointer_path.exists():
            return
        pointer = ci.validate_active_role(strict_json(pointer_path.read_bytes()))
        from .router import control

        publications = []
        for suffix in ("*.facade-publication.json", "*.review-publication.json"):
            for file in self.home.ci.workspace.glob(suffix):
                value = strict_json(file.read_bytes())
                if (
                    value["consuming_workspace"] == str(self.home.workspace)
                    and value["pointer"] == pointer
                ):
                    publications.append((file, value))
        if len(publications) != 1:
            raise fr.FactoryError("factory_stale_pointer_unowned")
        file, publication = publications[0]
        attempt = pointer["attempt_id"]
        suffix = (
            ".candidate-review.json"
            if pointer["role"] == "review"
            else ".facade-role.json"
        )
        preparer = strict_json(
            (self.home.ci.workspace / (attempt + suffix)).read_bytes()
        )["preparer"]
        instance = control.inspect_instance(
            preparer["instance_id"], state_home=Path(preparer["state_home"])
        )
        original_seen = False
        later_runs = set()
        seen_origin = False
        for item in instance["runs"]:
            if item["id"] == preparer["run_id"]:
                seen_origin = True
            elif seen_origin:
                later_runs.add(item["id"])
        for run in instance["runs"]:
            view = control.inspect_run(
                preparer["instance_id"],
                run["id"],
                state_home=Path(preparer["state_home"]),
            )
            if run["id"] == preparer["run_id"]:
                original_seen = True
                continue
            # A later native consumer cannot be excluded by a stopped preparer run.
            if run["id"] in later_runs and any(
                row["node"] in NATIVE for row in view["invocations"]
            ):
                raise fr.FactoryError("factory_stale_projection_later_consumer")
        if not original_seen:
            raise fr.FactoryError("factory_pointer_origin_missing")
        if (
            facts is not None
            and facts.get("active_projection_ref") != pointer["projection_ref"]
        ):
            raise fr.FactoryError("factory_pointer_changed")
        # Remove only the exact owned pointer. Immutable artifacts remain.
        pointer_path.unlink()

    def _claim(self) -> dict:
        if self.home.active() is not None:
            raise fr.FactoryError("factory_active_job")
        self._validate_terminal_projections()
        self._invalidate_pointer()
        base_ref = self.home.base_ref()
        candidate.check_development(self.home, base_ref)
        from .factory_lad import Lane

        if not Lane(self.home).parked(None, caller_deadline=self.deadline)["closed"]:
            raise fr.FactoryError("factory_lane_not_parked")

        def build(task):
            subject = fr.validate_input(strict_json(task["request_bytes"]))
            input_ref = self.home.retain_bytes(task["request_bytes"])
            scope = {
                "format": "millrace-plus-factory-scope/v1",
                "job_id": task["task_id"],
                "input_ref": input_ref,
                "config_ref": self.home.record["config_ref"],
                "write_paths": self.home.config["scope_policy"]["write_paths"],
                "protected_paths": self.home.config["scope_policy"]["protected_paths"],
                "change_classes": self.home.ci.config["repair_policy"][
                    "allowed_change_classes"
                ],
                "authority_ref": self.home.config["local_authority"]["authority_ref"],
                "requirements_ref": self.home.config["check_policy"][
                    "requirements_snapshot_ref"
                ],
                "rationale_refs": [],
            }
            if subject["scope_ref"] is not None:
                narrow = self.home.read_ref(subject["scope_ref"])
                fr.require_fields(
                    narrow,
                    "write_paths protected_paths",
                    "invalid_factory_narrow_scope",
                )
                if any(
                    not any(
                        path == prefix or path.startswith(prefix + "/")
                        for prefix in scope["write_paths"]
                    )
                    for path in narrow["write_paths"]
                ):
                    raise fr.FactoryError("factory_scope_widened")
                scope["write_paths"] = narrow["write_paths"]
                scope["protected_paths"] = sorted(
                    set(scope["protected_paths"] + narrow["protected_paths"])
                )
            scope_ref = self.home.retain(fr.validate_record(scope, scope["format"]))
            claim = {
                "format": "millrace-plus-factory-claim/v1",
                "factory_id": self.home.factory_id,
                "job_id": task["task_id"],
                "input_sha256": task["request_sha256"],
                "config_sha256": self.home.config_sha256,
                "base_candidate_ref": base_ref,
                "scope_ref": scope_ref,
                "parent_instance_id": self.context["instance_id"],
                "parent_run_id": self.context["run_id"],
            }
            claim_ref = self.home.retain(fr.validate_record(claim, claim["format"]))
            state = self._initial_claim_state(task, claim_ref, scope_ref)
            write_exact(
                self.home.workspace / "jobs" / task["task_id"] / "claim-state.json",
                canonical(state),
            )
            return {
                "task_id": task["task_id"],
                "request_sha256": task["request_sha256"],
                "factory_claim_ref": claim_ref,
            }

        with self.home.lock():
            receipt = self.home.intake.claim(build)
        if receipt is None:
            return self._out("EMPTY")
        proposal = (
            self.home.workspace / "jobs" / receipt["task_id"] / "claim-state.json"
        )
        state = fr.validate_state(strict_json(proposal.read_bytes()))
        if state["claim_ref"] != receipt["factory_claim_ref"]:
            raise fr.FactoryError("factory_claim_projection_changed")
        write_exact(proposal.with_name("state.json"), canonical(state))
        return self._out("CLAIMED", refs=[self.home.retain(state)])

    def _initial_claim_state(
        self, task: dict, claim_ref: dict, scope_ref: dict
    ) -> dict:
        reservations = fr.integrity(
            {
                "format": "millrace-plus-factory-reservations/v1",
                "job_id": task["task_id"],
                "reservations": [],
            }
        )
        facts = {
            "input_sha256": task["request_sha256"],
            "pending": None,
            "checks": [],
            "reproductions": [],
            "completion_refs": [],
            "verification_occurrence": 0,
            "claim_context": self.context,
            "scope_ref": scope_ref,
            "accepted": False,
        }
        state = fr.integrity(
            {
                "format": fr.STATE,
                "factory_id": self.home.factory_id,
                "job_id": task["task_id"],
                "input_ref": self.home.retain_bytes(task["request_bytes"]),
                "claim_ref": claim_ref,
                "baseline_ref": None,
                "reservations_ref": self.home.retain(reservations),
                "accepted_refs": {"facts": self.home.retain(facts)},
                "decision_refs": [],
                "phase": "normalize",
                "version": 1,
            }
        )
        self.home.retain(fr.validate_state(state))
        self.home.project_job_records(state, facts)
        return state

    def _obligations(self, state: dict) -> dict:
        claim = self.home.read_ref(state["claim_ref"])
        base_ref = claim["base_candidate_ref"]
        selected = candidate.engineering_mapping(self.home)
        requirements = []
        if selected["snapshot"] is not None:
            requirements = [dict(row) for row in selected["snapshot"]["requirements"]]
        inputs = []
        for base in reversed(self.home.base_chain(base_ref)):
            if base["outcome_ref"] is None:
                continue
            outcome = self.home.read_ref(base["outcome_ref"])
            original_ref = candidate.outcome_input(self.home, outcome)
            original = fr.validate_input(self.home.read_ref(original_ref))
            inputs.append(original_ref)
            job = outcome["job_id"]
            for row in original["criteria"]:
                requirements.append(
                    {
                        "id": job + ":" + row["id"],
                        "text": row["text"],
                        "request_quote": row["text"],
                        "origin_task_id": job,
                        "status": "active",
                        "supersedes": [],
                        "evidence_refs": [original_ref, base["outcome_ref"]],
                    }
                )
        return fr.integrity(
            {
                "format": "millrace-plus-factory-obligations/v1",
                "job_id": state["job_id"],
                "base_ref": base_ref,
                "lq_snapshot_ref": self.home.config["check_policy"][
                    "requirements_snapshot_ref"
                ],
                "accepted_input_refs": inputs,
                "requirements": requirements,
            }
        )

    def _normalize(self, state: dict, facts: dict) -> dict:
        claim = self.home.read_ref(state["claim_ref"])
        subject = fr.validate_input(self.home.read_ref(state["input_ref"]))
        base = fr.validate_base(self.home.read_ref(claim["base_candidate_ref"]))
        predecessor = ci.validate_candidate(
            self.home.ci.read_ref(base["candidate_ref"])
        )
        if subject["base_candidate_sha256"] not in {
            None,
            predecessor["candidate_tree_sha256"],
        }:
            raise fr.FactoryError("factory_base_stale")
        development = candidate.check_development(
            self.home, claim["base_candidate_ref"]
        )
        root = Path(self.home.config["development_lane"]["target_root"])
        view = snapshot_policy(root, self.home.ci.policy, self.home.ci.guards)
        before = facade.before_manifest(
            view,
            target=predecessor["source_target"],
            anchor_head=predecessor["anchor_head"],
            logical_base_id=predecessor["candidate_tree_sha256"],
            policy_ref=self.home.ci.config["path_policy_ref"],
            environment_ref=self.home.ci.retain({"environment": view["environment"]}),
            foreign_dirty=development["foreign_dirty_paths"],
            payload_refs=facade.retain_payloads(root, view, self.home.ci.workspace),
        )
        obligations = self._obligations(state)
        baseline = {
            "format": "millrace-plus-factory-baseline/v1",
            "job_id": state["job_id"],
            "anchor": self.home.config["anchor"],
            "accepted_candidate_ref": base["candidate_ref"],
            "development_ref": self.home.ci.retain(before),
            "validation_ref": self.home.ci.retain(self.home.ci.observe()),
            "index_ref": self.home.retain(
                {
                    "development": candidate.index_identity(root),
                    "validation": candidate.index_identity(self.home.ci.target),
                }
            ),
            "protected_dirty_ref": self.home.retain(development),
            "scope_ref": claim["scope_ref"],
            "requirements_snapshot_ref": self.home.config["check_policy"][
                "requirements_snapshot_ref"
            ],
        }
        facts = facts | {
            "baseline_ref": self.home.retain(
                fr.validate_record(baseline, baseline["format"])
            ),
            "base_candidate_ref": base["candidate_ref"],
            "development_before_ref": self.home.ci.retain(before),
            "obligations_ref": self.home.retain(obligations),
            "normalize_context": self.context,
        }
        for task in self.home.intake.tasks():
            if task["status"] != "failed" or task["task_id"] == state["job_id"]:
                continue
            previous_state = self.home.state(task["task_id"])
            prior = self.home.read_ref(previous_state["input_ref"])
            previous_claim = self.home.read_ref(previous_state["claim_ref"])
            same_base = (
                previous_claim["base_candidate_ref"] == claim["base_candidate_ref"]
            )
            new_evidence = {canonical(ref) for ref in subject["evidence_refs"]} - {
                canonical(ref) for ref in prior["evidence_refs"]
            }
            if (
                same_base
                and fr.assignment_sha256(prior) == fr.assignment_sha256(subject)
                and not new_evidence
            ):
                return self._transition(
                    state, facts | {"hold_reason": "repeated_failure_no_change"}, "HOLD"
                )
        return self._transition(state, facts, "READY")

    def _prepare_baseline(self, state: dict, facts: dict) -> dict:
        self._allowed("materialize_validation")
        request = self._request(
            state,
            "prepare-base",
            candidate_ref=facts["base_candidate_ref"],
            materialization_id=None,
            expected_materialization_version=None,
        )
        state, facts, _ = self._operate(state, facts, request, "guard_prepared_base")
        return self._out("PREPARED", refs=[self.home.retain(state)])

    def _guard_prepared_base(self, state: dict, facts: dict) -> dict:
        state, facts = self._accept_pending(state, facts)
        subject = fr.validate_input(self.home.read_ref(state["input_ref"]))
        binding = candidate.local_source_binding(self.home, state, state["input_ref"])
        facts = facts | {"source_binding_ref": self.home.ci.retain(binding)}
        return self._transition(
            state,
            facts,
            "TASK_READY" if subject["source_kind"] == "task" else "FEEDBACK_READY",
        )

    def _project(self, state: dict, facts: dict, role: str) -> dict:
        if role == "repair":
            self._allowed("ci_repair")
            if facts.get("repair_projection_ref") is not None:
                raise fr.FactoryError("factory_repair_reservation_spent")
        subject = fr.validate_input(self.home.read_ref(state["input_ref"]))
        is_task = subject["source_kind"] == "task"
        binding = candidate.local_source_binding(self.home, state, state["input_ref"])
        schema = self.home.ci.workspace / "schemas/factory-input.json"
        basis = ci.validate_role_basis(
            {
                "format": ci.ROLE_BASIS,
                "subject_schema_ref": self.home.ci.file_ref(schema),
                "source_binding_ref": self.home.ci.retain(binding),
                "source_before_sha256": source_identity(self.home.ci.observe()),
                "config_sha256": self.home.ci.config_sha256,
                "assessment_ref": facts.get("assessment_ref")
                if role == "repair"
                else None,
                "reproduction_refs": facts["reproductions"],
                "criteria_ref": state["input_ref"] if is_task else None,
                "scope_ref": facts.get("repair_scope_ref")
                if role == "repair"
                else None,
                "public_writer_refs": facts.get("assessment_acceptance_refs", [])
                + facts["reproductions"],
            },
            role=role,
        )
        request = self._request(
            state,
            "project-role",
            candidate_ref=None,
            subject_ref=state["input_ref"],
            role=role,
            role_binding_id=self.home.ci.config["role_bindings"][role]["id"],
            evidence_refs=[self.home.ci.retain(basis)],
        )
        following = "guard_patch" if role == "repair" else "guard_investigation"
        # A durable reservation precedes publication and the graph's native edge.
        if facts.get("pending"):
            raise fr.FactoryError("factory_operation_pending")
        facts = facts | {
            role + "_context": self.context,
            role + "_request_ref": self.home.retain(request),
        }
        state = self._save(state, facts, following)
        result = facade.operate_candidate(
            self.home.ci, request, self.controls, self.context
        )
        projection_ref = result["artifact_refs"][0]
        projection = self.home.ci.read_ref(projection_ref)
        facts = facts | {
            role + "_projection_ref": projection_ref,
            "active_projection_ref": strict_json(
                (self.home.workspace / "active-role.json").read_bytes()
            )["projection_ref"],
        }
        state = self._save(state, facts, following)
        state = self._reserve_role(state, facts, role, request, projection_ref)
        return self._out("READY", refs=[self.home.retain(projection)])

    def _reservations(self, state: dict, rows: list) -> dict:
        record = fr.integrity(
            {
                "format": "millrace-plus-factory-reservations/v1",
                "job_id": state["job_id"],
                "reservations": rows,
            }
        )
        fr.validate_record(record, record["format"])
        with self.home.lock():
            if self.home.state(state["job_id"]) != state:
                raise fr.FactoryError("factory_state_conflict")
            saved = fr.integrity(
                state
                | {
                    "reservations_ref": self.home.retain(record),
                    "version": state["version"] + 1,
                }
            )
            self.home.retain(saved)
            self.home.project_job_records(saved, self.home.facts(saved))
            write_replace(
                self.home.workspace / "jobs" / state["job_id"] / "state.json",
                canonical(saved),
            )
        return saved

    def _reserve_role(
        self, state: dict, facts: dict, role: str, request: dict, projection_ref: dict
    ) -> dict:
        projection = self.home.ci.read_ref(projection_ref)
        reservations = self.home.read_ref(state["reservations_ref"])
        if any(
            row["id"] == projection["attempt_id"]
            for row in reservations["reservations"]
        ):
            raise fr.FactoryError("factory_duplicate_role_reservation")
        manifest = strict_json((self.home.home / "preparation.json").read_bytes())
        row = {
            "id": projection["attempt_id"],
            "operation": role,
            "input_sha256": facts["input_sha256"],
            "scope_sha256": facts["scope_ref"]["sha256"],
            "base_candidate_id": self.home.ci.read_ref(facts["base_candidate_ref"])[
                "candidate_id"
            ],
            "expected_attempt": self.context["attempt"] + 1,
            "producer_context": self.context,
            "definition_ref": manifest["definition_ref"],
            "profile_ref": self.home.config["role_profile"]["profile_ref"],
            "request_ref": self.home.retain(request),
            "result_ref": projection_ref,
            "completion_ref": None,
            "acceptance_ref": None,
            "state": "reserved",
        }
        return self._reservations(state, [*reservations["reservations"], row])

    def _accept_role_reservation(
        self,
        state: dict,
        projection_ref: dict,
        completion_ref: dict,
        acceptance_ref: dict,
    ) -> dict:
        reservations = self.home.read_ref(state["reservations_ref"])
        matching = [
            row
            for row in reservations["reservations"]
            if row["result_ref"] == projection_ref
        ]
        if len(matching) != 1:
            raise fr.FactoryError("factory_role_reservation_missing")
        original = matching[0]
        updated = original | {
            "state": "accepted",
            "completion_ref": completion_ref,
            "acceptance_ref": acceptance_ref,
        }
        if original["state"] == "accepted" and original != updated:
            raise fr.FactoryError("factory_role_acceptance_changed")
        return self._reservations(
            state,
            [
                updated if row == original else row
                for row in reservations["reservations"]
            ],
        )

    def _guard_investigation(self, state: dict, facts: dict) -> dict:
        projection_ref = facts.get("investigate_projection_ref")
        if projection_ref is None:
            raise fr.FactoryError("factory_investigation_reservation_unknown")
        context, completion = self._completion(
            facts["investigate_context"], native="investigate"
        )
        accepted = facade.accept_candidate_role(
            self.home.ci, projection_ref, completion
        )
        state = self._accept_role_reservation(
            state, projection_ref, completion, self.home.ci.retain(accepted)
        )
        assessment = self.home.ci.read_ref(accepted["artifact_ref"])
        facts = facts | {
            "assessment_ref": accepted["artifact_ref"],
            "assessment_acceptance_refs": [self.home.ci.retain(accepted)],
            "investigate_completion_ref": completion,
            "investigate_producer": context,
        }
        route = assessment["route"]
        if route == "NO_ACTION":
            return self._transition(state, facts | {"no_action": True}, "NO_ACTION")
        if route == "HUMAN_HOLD":
            return self._transition(state, facts, "HUMAN_HOLD")
        if route == "INVESTIGATE":
            return self._transition(state, facts, "PROBE_REQUIRED")
        subject = self.home.read_ref(state["input_ref"])
        return self._transition(
            state,
            facts,
            "TASK_READY" if subject["source_kind"] == "task" else "REPRODUCED",
        )

    def _scope_gate(self, state: dict, facts: dict) -> dict:
        assessment = self.home.ci.read_ref(facts["assessment_ref"])
        scope = self.home.read_ref(facts["scope_ref"])
        proposed = assessment["proposed_paths"]
        if any(
            not any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in scope["write_paths"]
            )
            or any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in scope["protected_paths"]
            )
            or ci.structural_forbidden(path)
            for path in proposed
        ):
            raise fr.FactoryError("factory_scope_forbidden")
        baseline = self.home.read_ref(self.home.record["development_baseline_ref"])
        if set(proposed) & set(baseline["foreign_dirty_paths"]):
            raise fr.FactoryError("candidate_overlap_with_prior_work")
        # Only captured risk policy can narrow the small-repair route.
        risk = self.home.read_ref(self.home.config["scope_policy"]["risk_policy_ref"])
        fr.require_fields(
            risk,
            "protected_paths protected_change_classes",
            "invalid_factory_risk_policy",
        )
        risky = set(assessment["change_classes"]) & set(
            risk["protected_change_classes"]
        ) or any(
            path == prefix or path.startswith(prefix + "/")
            for path in proposed
            for prefix in risk["protected_paths"]
        )
        subject = fr.validate_input(self.home.read_ref(state["input_ref"]))
        if subject["source_kind"] != "task":
            matcher = self.home.ci.config["reproduction"]["failure_matcher"]
            checks = [
                ci.validate_check(
                    self.home.ci.read_ref(
                        cc_acceptance(self.home.ci.read_ref(ref))["process_receipt_ref"]
                    )
                )
                for ref in facts["reproductions"]
            ]
            if set(row["command_id"] for row in checks) != set(
                self.home.ci.config["reproduction"]["command_ids"]
            ) or any(
                row["status"] != "failed"
                or row["exit_code"] not in matcher["failing_exit_codes"]
                or set(row["matched_failure_ids"])
                != set(matcher["evidence_pattern_ids"])
                for row in checks
            ):
                raise fr.FactoryError("factory_reproduction_required")
        if assessment["route"] == "LAD_CANDIDATE" or risky:
            self._allowed("lad_task")
            return self._transition(state, facts | {"route": "lad"}, "LAD_TASK")
        if assessment["route"] != "REPAIR_ALLOWED":
            raise fr.FactoryError("factory_repair_not_supported")
        self._allowed("ci_repair")
        facts = facts | {
            "route": "ci",
            "repair_scope_ref": self.home.ci.retain({"paths": proposed}),
        }
        return self._transition(state, facts, "CI_REPAIR")

    def _guard_patch(self, state: dict, facts: dict) -> dict:
        self._git_basis(state)
        projection = self.home.ci.read_ref(facts["repair_projection_ref"])
        context, completion = self._completion(facts["repair_context"], native="repair")
        lease = candidate.materialization(self.home)
        matching = [
            self.home.ci.read_ref(ref)
            for ref in lease["operation_refs"]
            if self.home.ci.read_ref(ref)["operation"] == "repair"
        ]
        operation = matching[-1]
        lease = facade.accept_materialization_operation(
            self.home.ci,
            lease["materialization_id"],
            operation["operation_id"],
            completion,
            lease["version"],
        )
        applied = [
            ref
            for ref in lease["operation_refs"]
            if self.home.ci.read_ref(ref)["operation_id"] == operation["operation_id"]
            and self.home.ci.read_ref(ref)["status"] == "applied"
            and self.home.ci.read_ref(ref)["completion_ref"] == completion
        ]
        if len(applied) != 1:
            raise fr.FactoryError("factory_repair_operation_missing")
        state = self._accept_role_reservation(
            state, facts["repair_projection_ref"], completion, applied[0]
        )
        facts = facts | {
            "repair_completion_ref": completion,
            "repair_producer": context,
            "completion_refs": [*facts["completion_refs"], completion],
        }
        source_view = self.home.ci.observe()
        intent = self.home.ci.read_ref(operation["write_intent_ref"])
        before_tree = self.home.ci.read_ref(operation["beforeimages_ref"])
        before = {
            "format": ci.BEFORE,
            "target": self.home.ci.read_ref(facts["base_candidate_ref"])[
                "verification_target"
            ],
            "anchor_head": before_tree["anchor_head"],
            "logical_base_id": ci.tree_digest(before_tree),
            "path_policy_ref": before_tree["path_policy_ref"],
            "environment_ref": self.home.ci.retain(
                {"environment": source_view["environment"]}
            ),
            "source_sha256": self.home.ci.read_ref(intent["basis_ref"])[
                "source_before_sha256"
            ],
            "foreign_dirty_paths": [],
            "entries": before_tree["entries"],
            "baseline_links": before_tree["baseline_links"],
        }
        facts["repair_before_ref"] = self.home.ci.retain(ci.validate_before(before))
        facts["repair_subject_ref"] = projection["subject_ref"]
        return self._transition(state, facts, "SCOPED")

    def _export_candidate(self, state: dict, facts: dict) -> dict:
        from .factory_lad import Lane

        previous = ci.validate_candidate(
            self.home.ci.read_ref(facts["base_candidate_ref"])
        )
        if facts["route"] == "ci":
            root = self.home.ci.target
            before_ref = facts["repair_before_ref"]
            producer, completion = (
                facts["repair_producer"],
                facts["repair_completion_ref"],
            )
        else:
            proof = Lane(self.home).parked(
                facts["lad_task_id"], caller_deadline=self.deadline
            )
            if not proof["closed"] or proof["task"]["status"] != "succeeded":
                raise fr.FactoryError("factory_lad_not_settled")
            root = Path(self.home.config["development_lane"]["target_root"])
            before_ref = facts["development_before_ref"]
            task = Lane(self.home)._task(facts["lad_task_id"])
            _, call = Lane(self.home).runtime.call(task, _deadline=self.deadline)
            from .router import control

            core_home = Path(Lane(self.home).runtime.intent["core_state_home"])
            run = control.inspect_run(
                call["instance_id"], call["run_id"], state_home=core_home
            )
            # The fixed LAD contract's successful finalizer follows real CLOSE/ACK.
            # Do not select a last/stopped invocation from an unrelated plane.
            done = [
                value
                for value in run["completions"]
                if value["plane_id"] == "foreground"
                and value["node"] == "finalize"
                and value["classification"] == "semantic"
                and value["outcome"] == "SUCCEEDED"
                and value["effective_writer"] == "stopped"
            ]
            if len(done) != 1:
                raise fr.FactoryError("factory_lad_completion_missing")
            rows = [
                row
                for row in run["invocations"]
                if row["id"] == done[0]["invocation_id"]
                and row["writer_state"] == "stopped"
            ]
            if len(rows) != 1:
                raise fr.FactoryError("factory_lad_producer_missing")
            row = rows[0]
            scripts = [
                Path(arg)
                for arg in row["argv"]
                if "/controls/" in arg and arg.endswith(".py")
            ]
            if len(scripts) != 1:
                raise fr.FactoryError("factory_lad_producing_script_missing")
            producer = {
                "instance_id": call["instance_id"],
                "state_home": str(core_home),
                "run_id": call["run_id"],
                "plane_id": row["plane_id"],
                "node": row["node"],
                "attempt": row["attempt"],
                "invocation_id": row["id"],
                "revision_id": row["revision_id"],
                "controls_root": str(scripts[0].parent.parent),
                "pid": row["pid"],
                "cwd": row["cwd"],
                "argv": row["argv"],
                "deadline": row["deadline"],
                "public_run_ref": ci.digest(run),
            }
            completion = self.home.ci.retain(done[0])
        after = candidate.capture_tree(self.home, root)
        basis = ci.validate_export_basis(
            {
                "format": ci.EXPORT_BASIS,
                "subject_ref": state["input_ref"],
                "source_target": previous["source_target"],
                "verification_target": previous["verification_target"],
                "anchor_head": previous["anchor_head"],
                "logical_base_id": previous["candidate_tree_sha256"],
                "prior_candidate_ref": facts["base_candidate_ref"],
                "before_manifest_ref": before_ref,
                "source_after_manifest_ref": self.home.ci.retain(after),
                "config_ref": self.home.ci.config_ref,
                "assessment_ref": facts["assessment_ref"],
                "authority_ref": self.home.ci.config["local_authority_ref"],
                "writer_facts": {
                    "core_invocation_id": producer["invocation_id"],
                    "writer_state": "stopped",
                },
            }
        )
        request = self._request(
            state,
            "export",
            subject_ref=state["input_ref"],
            export_basis_ref=self.home.ci.retain(basis),
            evidence_refs=[
                self.home.ci.retain(
                    {"invocation_context": producer, "completion_ref": completion}
                )
            ],
        )
        result = facade.operate_candidate(
            self.home.ci, request, self.controls, self.context
        )
        facts = facts | {
            "candidate_ref": result["artifact_refs"][0],
            "export_context": self.context,
            "export_basis_ref": self.home.ci.retain(basis),
        }
        binding = {
            "format": "millrace-plus-factory-candidate-binding/v1",
            "factory_id": self.home.factory_id,
            "job_id": state["job_id"],
            "candidate_ref": facts["candidate_ref"],
            "request_sha256": facts["input_sha256"],
            "scope_ref": facts["scope_ref"],
            "producer": {
                "kind": "ci_repair" if facts["route"] == "ci" else "lad",
                "projection_ref": facts.get("repair_projection_ref"),
                "task_ref": facts.get("lad_task_ref"),
                "completion_ref": completion,
            },
            "development_before_ref": facts["development_before_ref"],
            "protected_dirty_ref": self.home.record["development_baseline_ref"],
            "closure_refs": [completion],
            "source_binding_ref": facts["source_binding_ref"],
            "created_at": _now(),
        }
        facts["candidate_binding_ref"] = self.home.retain(
            fr.validate_record(binding, binding["format"])
        )
        return self._transition(state, facts, "EXPORTED")

    def _materialize_candidate(self, state: dict, facts: dict) -> dict:
        if facts["route"] == "lad":
            self._allowed("materialize_validation")
            request = self._request(state, "import")
            state, facts, _ = self._operate(state, facts, request, "select_check")
        else:
            lease = candidate.materialization(self.home)
            lease = facade.accept_materialization(
                self.home.ci,
                lease["materialization_id"],
                facts["candidate_ref"],
                facts["completion_refs"],
                lease["version"],
            )
            state = self._save(state, facts, "select_check")
        return self._out("READY", refs=[self.home.retain(state)])

    def _select_check(self, state: dict, facts: dict, *, reproduce: bool) -> dict:
        state, facts = self._accept_pending(state, facts)
        lease = candidate.materialization(self.home)
        if (
            not reproduce
            and facts.get("route") == "lad"
            and lease["state"] == "base_ready"
        ):
            facade.accept_materialization(
                self.home.ci,
                lease["materialization_id"],
                facts["candidate_ref"],
                facts["completion_refs"],
                lease["version"],
            )
        key = "reproductions" if reproduce else "checks"
        if facts.get("close_requested"):
            return self._transition(state, facts, "CLOSE_READY")
        if reproduce:
            ids = self.home.ci.config["reproduction"]["command_ids"]
        else:
            frozen = ci.validate_candidate(
                self.home.ci.read_ref(facts["candidate_ref"])
            )
            delta = ci.validate_delta(self.home.ci.read_ref(frozen["delta_ref"]))
            ids = candidate.selected_commands(
                self.home,
                [row["path"] for row in delta["changes"]],
                self.home.read_ref(facts["obligations_ref"]),
            )
        by_id = {}
        for ref in facts[key]:
            acceptance = cc_acceptance(self.home.ci.read_ref(ref))
            check = ci.validate_check(
                self.home.ci.read_ref(acceptance["process_receipt_ref"])
            )
            if check["command_id"] in by_id:
                raise fr.FactoryError("factory_duplicate_check_evidence")
            by_id[check["command_id"]] = check
        remaining = [name for name in ids if name not in by_id]
        if remaining:
            facts = facts | {
                "next_command_id": remaining[0],
                "selected_command_ids": ids,
            }
            return self._transition(state, facts, "NEXT")
        if reproduce:
            matcher = self.home.ci.config["reproduction"]["failure_matcher"]
            reproduced = all(
                row["status"] == "failed"
                and row["exit_code"] in matcher["failing_exit_codes"]
                and set(row["matched_failure_ids"])
                == set(matcher["evidence_pattern_ids"])
                for row in by_id.values()
            )
            if any(
                row["status"] in {"infrastructure", "uncertain"}
                for row in by_id.values()
            ):
                return self._transition(state, facts, "UNCERTAIN")
            return self._transition(
                state, facts, "REPRODUCED" if reproduced else "UNREPRODUCED"
            )
        if any(row["status"] != "passed" for row in by_id.values()):
            return self._transition(state, facts, "FAIL")
        if not by_id:
            raise fr.FactoryError("factory_no_check_disposition_required")
        facts["selected_command_ids"] = ids
        return self._transition(state, facts, "COMPLETE")

    def _check(self, state: dict, facts: dict, *, reproduce: bool) -> dict:
        self._allowed("reproduce" if reproduce else "materialize_validation")
        request = self._request(
            state,
            "check",
            candidate_ref=facts["base_candidate_ref"]
            if reproduce
            else facts["candidate_ref"],
            command_id=facts["next_command_id"],
        )
        following = "select_reproduction" if reproduce else "select_check"
        state, _, _ = self._operate(state, facts, request, following)
        return self._out("RECORDED", refs=[self.home.retain(state)])

    def _probe(self, state: dict, facts: dict) -> dict:
        self._allowed("reproduce")
        ids = self.home.ci.config["investigation"]["probe_command_ids"]
        if not ids:
            return self._transition(
                state, facts, "INFRASTRUCTURE", "no configured probe"
            )
        command = next(
            row for row in self.home.ci.config["commands"] if row["id"] == ids[0]
        )
        if command["expected_products"]:
            raise fr.FactoryError("factory_probe_not_readonly")
        request = self._request(
            state, "check", candidate_ref=facts["base_candidate_ref"], command_id=ids[0]
        )
        state, _, _ = self._operate(state, facts, request, "hold")
        return self._out(
            "EVIDENCE",
            "read-only probe recorded; explicit new-evidence continuation required",
            [self.home.retain(state)],
        )

    def _prepare_review(self, state: dict, facts: dict) -> dict:
        if facts.get("review_projection_ref") and not facts.get("verification_retry"):
            raise fr.FactoryError("factory_review_reservation_spent")
        # Review the exact candidate and current obligations independently.
        selection = self.home.ci.config["role_bindings"]["review"][0]
        request = self._request(
            state,
            "review",
            role_binding_id=selection["id"],
            evidence_refs=facts["checks"],
        )
        facts = facts | {
            "review_context": self.context,
            "review_request_ref": self.home.retain(request),
            "verification_retry": False,
        }
        state = self._save(state, facts, "verify_candidate")
        projection = facade.prepare_candidate_review(
            self.home.ci, request, self.controls, self.context
        )
        facts = facts | {
            "review_projection_ref": self.home.ci.retain(projection),
            "active_projection_ref": strict_json(
                (self.home.workspace / "active-role.json").read_bytes()
            )["projection_ref"],
        }
        state = self._save(state, facts, "verify_candidate")
        state = self._reserve_role(
            state, facts, "review", request, facts["review_projection_ref"]
        )
        return self._out("READY", refs=[self.home.retain(state)])

    def _verify_candidate(self, state: dict, facts: dict) -> dict:
        _, completion = self._completion(
            facts["review_context"], native="review_candidate"
        )
        accepted = facade.accept_candidate_review(
            self.home.ci, facts["review_projection_ref"], completion
        )
        state = self._accept_role_reservation(
            state,
            facts["review_projection_ref"],
            completion,
            self.home.ci.retain(accepted),
        )
        review = self.home.ci.read_ref(accepted["review_ref"])
        if review["verdict"] != "ACCEPT":
            return self._transition(
                state,
                facts | {"review_acceptance_ref": self.home.ci.retain(accepted)},
                "FAIL",
            )
        evidence = [
            *facts["checks"],
            accepted["review_ref"],
            self.home.ci.retain(accepted),
        ]
        request = self._request(state, "verify", evidence_refs=evidence)
        result = facade.operate_candidate(
            self.home.ci, request, self.controls, self.context
        )
        facts = facts | {
            "verification_ref": result["artifact_refs"][0],
            "review_ref": accepted["review_ref"],
            "review_acceptance_ref": self.home.ci.retain(accepted),
            "accepted": True,
        }
        return self._transition(state, facts, "PASS")

    def _restore_validation(self, state: dict, facts: dict) -> dict:
        self._allowed("restore_validation")
        # A stopped failure still passes its pending product acceptance guard first.
        if facts.get("pending"):
            raise fr.FactoryError("factory_pending_acceptance_guard_required")
        lease = candidate.materialization(self.home)
        if lease is None or lease["state"] == "restored":
            self._git_basis(state)
            baseline = self.home.read_ref(state["baseline_ref"])
            before = self.home.read_ref(baseline["validation_ref"])
            actual = self.home.ci.observe()
            if (
                source_identity(before) != source_identity(actual)
                or before["products"] != actual["products"]
            ):
                raise fr.FactoryError("factory_unleased_target_changed")
            facts["restoration_ref"] = self.home.retain(
                {
                    "status": "unchanged",
                    "baseline_ref": state["baseline_ref"],
                    "observation_ref": self.home.ci.retain(actual),
                    "producer_context": self.context,
                }
            )
            return self._transition(state, facts, "RESTORED")
        request = self._request(
            state,
            "restore",
            candidate_ref=facts.get("candidate_ref") or facts["base_candidate_ref"],
        )
        state, _, _ = self._operate(state, facts, request, "synchronize_base")
        return self._out("RESTORED", refs=[self.home.retain(state)])

    def _synchronize_base(self, state: dict, facts: dict) -> dict:
        state, facts = self._accept_pending(state, facts)
        self._allowed(
            "synchronize_development" if facts["accepted"] else "restore_development"
        )
        # Save the exact synchronization proposal before any development write.
        facts = facts | {"synchronize_context": self.context}
        state = self._save(state, facts, "retain")
        proof = candidate.synchronize(
            self.home, state, accepted=facts["accepted"], deadline=self.deadline
        )
        facts = facts | {"synchronization_ref": self.home.retain(proof)}
        outcome = {
            "accepted": "ACCEPTED",
            "unchanged": "UNCHANGED",
            "restored_unaccepted": "RESTORED_UNACCEPTED",
        }[proof["status"]]
        return self._transition(state, facts, outcome)

    def _lad_request(
        self, state: dict, facts: dict, operation: str, *, control_ref=None
    ) -> Path:
        request = {
            "format": "millrace-plus-factory-lad-request/v1",
            "request_id": uuid.uuid5(
                uuid.UUID(hex=self.home.factory_id), state["job_id"] + "/" + operation
            ).hex,
            "factory_id": self.home.factory_id,
            "job_id": state["job_id"],
            "operation": operation,
            "lane_ref": self.home.retain(self.home.config["development_lane"]),
            "input_ref": facts.get("lad_rendered_ref")
            if operation == "submit_task"
            else None,
            "task_ref": facts.get("lad_task_ref"),
            "candidate_ref": facts.get("candidate_ref")
            if operation in {"accept_candidate", "restore_candidate"}
            else None,
            "authority_ref": self.home.config["local_authority"]["authority_ref"],
            "control_ref": control_ref,
            "evidence_refs": [],
        }
        fr.validate_record(request, request["format"])
        suffix = ""
        if operation in {"resume_parent", "park_lane"} and control_ref is not None:
            suffix = "/" + self.home.read_ref(control_ref)["request_id"]
            request["request_id"] = uuid.uuid5(
                uuid.UUID(hex=self.home.factory_id),
                state["job_id"] + "/" + operation + suffix,
            ).hex
        path = (
            self.home.workspace
            / "jobs"
            / state["job_id"]
            / (operation + suffix.replace("/", "-") + "-request.json")
        )
        write_exact(path, canonical(request))
        return path

    def _submit_lad(self, state: dict, facts: dict) -> dict:
        self._allowed("lad_task")
        from .factory_lad import Lane

        lane = Lane(self.home)
        if not lane.parked(None, caller_deadline=self.deadline)["closed"]:
            raise fr.FactoryError("factory_lane_not_parked")
        raw = candidate.render_request(
            self.home.read_ref(state["input_ref"]),
            self.home.read_ref(facts["obligations_ref"]),
        )
        facts = facts | {"lad_rendered_ref": self.home.retain_bytes(raw, ".request.md")}
        state = self._save(state, facts, "submit_lad")
        if len(raw) > 65536:
            return self._transition(
                state,
                facts | {"hold_reason": "lad_request_too_large"},
                "HOLD",
                "lad_request_too_large",
            )
        path = self._lad_request(state, facts, "submit_task")
        result = lane.submit_task(path, caller_deadline=self.deadline)
        submission = self.home.read_ref(result["artifact_refs"][0])["submission"]
        task_id = submission["task_id"]
        task = lane._task(task_id)
        facts = facts | {
            "lad_task_id": task_id,
            "lad_submission_ref": self.home.retain(result),
            "lad_task_ref": self.home.retain(
                {"task_id": task_id, "request_sha256": task["request_sha256"]}
            ),
        }
        return self._transition(state, facts, "SUBMITTED")

    def _control_intent(
        self, state: dict, facts: dict, operation: str
    ) -> tuple[dict, dict]:
        from .factory_lad import Lane

        key = "parent_resume" if operation == "resume_run" else "parent_pause"
        if facts.get(key + "_ref") is not None:
            intent = fr.validate_record(
                self.home.read_ref(facts[key + "_ref"]),
                "millrace-plus-factory-lad-parent-resume/v1",
            )
            if (
                intent["factory_id"] != self.home.factory_id
                or intent["job_id"] != state["job_id"]
                or intent["service_id"]
                != self.home.config["development_lane"]["service_id"]
                or intent["task_ref"] != facts["lad_task_ref"]
                or intent["request_sha256"]
                != ci.digest(self.home.read_ref(intent["request_ref"]))
                or intent["parent_instance_id"]
                != self.home.read_ref(intent["request_ref"])["instance_id"]
                or intent["parent_run_id"]
                != self.home.read_ref(intent["request_ref"])["run_id"]
            ):
                raise fr.FactoryError("factory_lad_resume_intent_changed")
            return state, intent
        _, parent = Lane(self.home).runtime.parent(_deadline=self.deadline)
        if operation == "resume_run":
            from .subworkflow_v2 import closed

            plane = parent["planes"].get("service")
            if (
                parent["status"] != "paused"
                or not closed(parent, attention=True)
                or plane is None
            ):
                raise fr.FactoryError("factory_lad_parent_resume_inadmissible")
            if parent.get("run_gate") is None:
                if plane["status"] != "paused" or not (
                    plane.get("resume_target") or plane.get("cursor")
                ):
                    raise fr.FactoryError("factory_lad_parent_resume_inadmissible")
                operation = "resume_plane"
            elif plane["status"] == "paused":
                raise fr.FactoryError("factory_lad_parent_resume_ambiguous")
        envelope = {
            "format": "millrace-router-request/v2",
            "request_id": uuid.uuid5(
                uuid.UUID(hex=self.home.factory_id), state["job_id"] + "/" + operation
            ).hex,
            "operation": operation,
            "instance_id": parent["instance_id"],
            "run_id": parent["id"],
            "plane_id": "service" if operation == "resume_plane" else None,
            "expected_state_version": parent["state_version"]
            if operation in {"resume_run", "resume_plane"}
            else None,
            "payload": {},
        }
        intent = {
            "format": "millrace-plus-factory-lad-parent-resume/v1",
            "factory_id": self.home.factory_id,
            "job_id": state["job_id"],
            "service_id": self.home.config["development_lane"]["service_id"],
            "task_ref": facts["lad_task_ref"],
            "parent_instance_id": parent["instance_id"],
            "parent_run_id": parent["id"],
            "request_ref": self.home.retain(envelope),
            "request_sha256": ci.digest(envelope),
            "state": "unsent",
            "receipt_ref": None,
            "join_refs": [],
        }
        facts = facts | {
            key + "_ref": self.home.retain(fr.validate_record(intent, intent["format"]))
        }
        return self._save(state, facts, self.node), intent

    def _resume_lad_parent(self, state: dict, facts: dict) -> dict:
        from .factory_lad import Lane

        state, intent = self._control_intent(state, facts, "resume_run")
        facts = self.home.facts(state)
        result = Lane(self.home).resume_parent(
            self._lad_request(
                state, facts, "resume_parent", control_ref=intent["request_ref"]
            ),
            caller_deadline=self.deadline,
            script=self.script,
        )
        facts["parent_resume_result_ref"] = self.home.retain(result)
        if result["status"] == "completed":
            return self._transition(state, facts, "APPLIED")
        if result["status"] == "held":
            return self._transition(state, facts, "HOLD")
        # Observe the same saved request cooperatively before a pending self-edge.
        end = min(self.deadline, time.monotonic() + 60)
        while time.monotonic() < end:
            time.sleep(min(5, end - time.monotonic()))
            result = Lane(self.home).resume_parent(
                self._lad_request(
                    state, facts, "resume_parent", control_ref=intent["request_ref"]
                ),
                caller_deadline=self.deadline,
                script=self.script,
            )
            if result["status"] == "completed":
                return self._transition(
                    state,
                    facts | {"parent_resume_result_ref": self.home.retain(result)},
                    "APPLIED",
                )
        return self._transition(state, facts, "PENDING")

    def _observe_lad(self, state: dict, facts: dict) -> dict:
        from .factory_lad import Lane

        lane = Lane(self.home)
        result = lane.observe_task(
            self._lad_request(state, facts, "observe_task"),
            caller_deadline=self.deadline,
        )
        facts["lad_binding_ref"] = lane._binding(
            facts["lad_task_id"], caller_deadline=self.deadline
        )
        facts["lad_observation_ref"] = self.home.retain(result)
        return self._transition(
            state,
            facts,
            {"completed": "SUCCEEDED", "pending": "RUNNING"}.get(
                result["status"], "HOLD"
            ),
        )

    def _park_lad(self, state: dict, facts: dict) -> dict:
        from .factory_lad import Lane

        state, intent = self._control_intent(state, facts, "pause_run")
        facts = self.home.facts(state)
        lane = Lane(self.home)
        result = lane.park_lane(
            self._lad_request(
                state, facts, "park_lane", control_ref=intent["request_ref"]
            ),
            caller_deadline=self.deadline,
        )
        facts["lad_binding_ref"] = lane._binding(
            facts["lad_task_id"],
            caller_deadline=self.deadline,
            closure=self.home.read_ref(result["artifact_refs"][0])
            if result["status"] == "completed"
            else None,
        )
        facts["lad_park_ref"] = self.home.retain(result)
        return self._transition(
            state, facts, "CLOSED" if result["status"] == "completed" else "PENDING"
        )

    def _retain(self, state: dict, facts: dict) -> dict:
        if facts.get("synchronize_context"):
            _, completion = self._completion(facts["synchronize_context"])
            if facts.get("synchronization_ref") is None:
                raise fr.FactoryError("factory_synchronization_unknown")
            facts["synchronization_completion_ref"] = completion
        if facts.get("pending"):
            raise fr.FactoryError("factory_pending_acceptance_guard_required")
        claim = self.home.read_ref(state["claim_ref"])
        status = (
            "succeeded"
            if facts.get("accepted")
            else "cancelled"
            if facts.get("cancel_requested")
            else "failed"
        )
        if facts.get("no_action"):
            status = "succeeded"
        disposition = {
            "format": "millrace-plus-factory-disposition/v1",
            "job_id": state["job_id"],
            "input_ref": state["input_ref"],
            "candidate_ref": facts.get("candidate_ref"),
            "prior_base_ref": claim["base_candidate_ref"],
            "verification_refs": [facts["verification_ref"]]
            if facts.get("verification_ref")
            else [],
            "review_ref": facts.get("review_ref"),
            "engineering_refs": [
                facts["obligations_ref"],
                *facts.get("engineering_adapter_refs", []),
            ],
            "delivery_refs": [],
            "closure_refs": facts.get("completion_refs", [])
            + ([facts["lad_park_ref"]] if facts.get("lad_park_ref") else []),
            "restoration_ref": facts.get("restoration_ref"),
            "synchronization_ref": facts.get("synchronization_ref"),
            "status": status,
            "reason": facts.get(
                "hold_reason",
                "verified_local_candidate"
                if facts.get("accepted")
                else "explicit_close",
            ),
        }
        facts["disposition_ref"] = self.home.retain(
            fr.validate_record(disposition, disposition["format"])
        )
        return self._transition(state, facts, "RETAINED")

    def _validate_terminal_projections(self) -> None:
        if candidate.settlement_pending(self.home) is not None:
            raise fr.FactoryError("factory_settlement_projection_missing")
        self.home.base_ref()

    def _settle(self, state: dict, facts: dict) -> dict:
        from .factory_lad import Lane

        disposition = fr.validate_record(
            self.home.read_ref(facts["disposition_ref"]),
            "millrace-plus-factory-disposition/v1",
        )
        claim = self.home.read_ref(state["claim_ref"])
        if (
            disposition["input_ref"] != state["input_ref"]
            or disposition["candidate_ref"] != facts.get("candidate_ref")
            or disposition["prior_base_ref"] != claim["base_candidate_ref"]
            or disposition["review_ref"] != facts.get("review_ref")
        ):
            raise fr.FactoryError("factory_disposition_basis_changed")
        lease = candidate.materialization(self.home)
        if lease is not None and lease["state"] != "restored":
            raise fr.FactoryError("factory_materialization_unrestored")
        closure = Lane(self.home).parked(
            facts.get("lad_task_id"), caller_deadline=self.deadline
        )
        if not closure["closed"]:
            raise fr.FactoryError("factory_lane_not_parked")
        if disposition["status"] == "succeeded" and facts.get("candidate_ref"):
            if not facts.get("verification_ref") or not facts.get(
                "synchronization_completion_ref"
            ):
                raise fr.FactoryError("factory_acceptance_unproven")
            ci.validate_candidate_verification(
                self.home.ci.read_ref(facts["verification_ref"])
            )
        self._invalidate_pointer(facts)
        outcome_ref, _ = candidate.publish_outcome(self.home, state, facts, closure)
        facts["outcome_ref"] = outcome_ref
        self._save(state, facts, "settle")
        report = (
            f"# Factory job {state['job_id']}\n\n"
            f"{disposition['status']}: {disposition['reason']}\n\n"
        )
        if facts.get("candidate_ref"):
            report += (
                (
                    "Verified frozen candidate: "
                    if facts.get("accepted")
                    else "Retained unaccepted candidate: "
                )
                + facts["candidate_ref"]["sha256"]
                + ". Live validation source is restored.\n"
            )
        write_replace(
            self.home.workspace / "jobs" / state["job_id"] / "report.md",
            report.encode(),
        )
        return self._out("SETTLED", refs=[outcome_ref])

    def _hold_report(
        self, state: dict, facts: dict, reason: str, *, uncertain: bool = False
    ) -> dict:
        claim = self.home.read_ref(state["claim_ref"])
        human = (
            facts.get("assessment_ref") is not None
            and self.home.ci.read_ref(facts["assessment_ref"])["route"] == "HUMAN_HOLD"
        )
        proposal = {
            "candidate_ref": facts.get("candidate_ref"),
            "prior_base_ref": claim["base_candidate_ref"],
            "verification_refs": [facts["verification_ref"]]
            if facts.get("verification_ref")
            else [],
            "review_ref": facts.get("review_ref"),
            "closure_refs": facts.get("completion_refs", []),
            "restoration_ref": facts.get("restoration_ref"),
            "reason": reason,
        }
        outcome = candidate.outcome_record(
            self.home,
            state,
            facts,
            proposal,
            status="uncertain"
            if uncertain or facts.get("pending")
            else "held_human"
            if human
            else "held_evidence",
            terminal_ref=None,
        )
        ref = self.home.retain(outcome)
        root = self.home.workspace / "jobs" / state["job_id"]
        write_replace(root / "outcome.json", canonical(outcome))
        write_replace(
            root / "report.md",
            (
                f"# Factory job {state['job_id']}\n\n"
                f"{outcome['status']}: {reason}\n\n"
                "Await an explicit supported decision. "
                "Source restoration and job settlement require their retained proofs.\n"
            ).encode(),
        )
        return ref

    def _wait_intake(self) -> dict:
        path = self.home.home / "intake.sock"
        if os.path.lexists(path):
            raise fr.FactoryError("factory_intake_socket_exists")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.bind(str(path))
            info = path.lstat()
            try:
                if any(row["status"] == "queued" for row in self.home.intake.tasks()):
                    return self._out("WAKE")
                policy = strict_json(
                    (self.home.home / "preparation.json").read_bytes()
                )["idle_wait_policy"]
                end = self.deadline - policy["bookkeeping_seconds"]
                while time.monotonic() < end:
                    from .router import control

                    view = control.inspect_run(
                        self.context["instance_id"],
                        self.context["run_id"],
                        state_home=Path(self.context["state_home"]),
                    )
                    if view["status"] != "running" or view.get("run_gate") is not None:
                        return self._out("WAKE")
                    connection.settimeout(min(5, max(0, end - time.monotonic())))
                    try:
                        connection.recv(64)
                        return self._out("WAKE")
                    except TimeoutError:
                        pass
                return self._out("IDLE")
            finally:
                if path.exists() and (path.lstat().st_dev, path.lstat().st_ino) == (
                    info.st_dev,
                    info.st_ino,
                ):
                    path.unlink()

    def execute(self) -> dict:
        if self.node == "wait_intake":
            return self._wait_intake()
        if self.node == "claim":
            return self._claim()
        active = self.home.active()
        if self.node == "reconcile":
            if active is None:
                pending = candidate.settlement_pending(self.home)
                if pending is not None:
                    return self._out("SETTLE")
                self._validate_terminal_projections()
                return self._out("READY")
            self.home.reconcile_claim(active["task_id"])
            self.home.reconcile_decisions(active["task_id"])
            state = self.home.state(active["task_id"])
            if state["phase"] in NATIVE or state["phase"] not in ROUTES:
                raise fr.FactoryError("factory_reconcile_phase_invalid")
            return self._out(state["phase"].upper())
        if active is None and self.node == "settle":
            active = candidate.settlement_pending(self.home)
        if active is None:
            raise fr.FactoryError("factory_active_job_missing")
        state = self.home.state(active["task_id"])
        facts = self.home.facts(state)
        if self.node == "hold" and state["phase"] != "hold":
            # A declared native BLOCKED/REJECT edge reaches this metadata hold.
            # Acceptance of any source/product write remains in its normal guard.
            if state["phase"] == "verify_candidate" and facts.get(
                "review_projection_ref"
            ):
                _, completion = self._completion(
                    facts["review_context"], native="review_candidate"
                )
                accepted = facade.accept_candidate_review(
                    self.home.ci, facts["review_projection_ref"], completion
                )
                state = self._accept_role_reservation(
                    state,
                    facts["review_projection_ref"],
                    completion,
                    self.home.ci.retain(accepted),
                )
                facts = facts | {
                    "review_ref": accepted["review_ref"],
                    "review_acceptance_ref": self.home.ci.retain(accepted),
                }
            facts = facts | {
                "held_from_phase": state["phase"],
                "hold_reason": "native_blocked_or_rejected",
            }
            state = self._save(state, facts, "hold")
        if self.node != state["phase"]:
            raise fr.FactoryError("factory_phase_mismatch")
        if self.node == "hold":
            if facts.get("lad_task_id"):
                from .factory_lad import Lane

                parent = Lane(self.home).wait_paused_parent(
                    caller_deadline=self.deadline
                )
                if not parent["retained"]:
                    return self._out("PENDING", parent["reason"])
                facts = facts | {"held_parent_join_ref": self.home.retain(parent)}
            if facts.get("pending") and facts["pending"]["node"] == "probe":
                state, facts = self._accept_pending(state, facts)
            report_ref = self._hold_report(
                state, facts, facts.get("hold_reason") or "operator_decision_required"
            )
            self._save(state, facts | {"hold_outcome_ref": report_ref}, "hold")
            return self._out("WAIT_OPERATOR", facts.get("hold_reason"), [report_ref])
        if self.node == "wait_provider":
            # No selected L-R2 capability. Due time never proves an actual resume.
            return self._out("WAIT")
        methods = {
            "prepare_investigation": lambda: self._project(state, facts, "investigate"),
            "prepare_ci": lambda: self._project(state, facts, "repair"),
            "select_reproduction": lambda: self._select_check(
                state, facts, reproduce=True
            ),
            "select_check": lambda: self._select_check(state, facts, reproduce=False),
            "reproduce": lambda: self._check(state, facts, reproduce=True),
            "check_candidate": lambda: self._check(state, facts, reproduce=False),
            "guard_delivery": lambda: self._transition(state, facts, "LOCAL"),
            "probe": lambda: self._probe(state, facts),
        }
        method = methods.get(self.node)
        return method() if method else getattr(self, "_" + self.node)(state, facts)


def cc_acceptance(value):
    from .check_contracts import validate_completion_acceptance

    return validate_completion_acceptance(value, "public_core_completion")


def step(node: str, home: Path, script: Path) -> dict:
    try:
        runtime = Runtime(Home(home), script, node)
        try:
            return runtime.execute()
        except Exception as exc:
            # A diagnostic held view grants no closure, phase change or receipt.
            # Preserve the original failure if corruption also prevents reporting.
            try:
                active = runtime.home.active()
                if active is not None:
                    state = runtime.home.state(active["task_id"])
                    runtime._hold_report(
                        state,
                        runtime.home.facts(state),
                        getattr(exc, "code", "factory_operation_invalid"),
                        uncertain=True,
                    )
            except Exception:
                pass
            raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Core's ordinary error route holds on missing or corrupt facts.
        raise fr.FactoryError("factory_operation_invalid") from exc
