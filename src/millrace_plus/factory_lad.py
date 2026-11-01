"""Exact dedicated-LAD lane operations. The service retains child ownership."""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from pathlib import Path

from . import factory_records as fr
from .cicd_source import OWNER, filesystem_identity
from .factory_runtime import write_exact
from .lad_service import Service, _owner_check
from .lad_service_disposition import task_sent_calls, verify_terminal_current
from .lad_service_runtime import Runtime, _joined, _parent_envelope
from .local_intake import IntakeError, canonical, strict_json
from .subworkflow_v2 import closed
from .workflow_os_client import validate_call, validate_receipt

__all__ = ["Lane"]


class Lane:
    """Wrap an already initialized service with exact Factory/job/lane intent."""

    def __init__(self, home) -> None:
        self.home = home
        self.config = home.config["development_lane"]
        self.service = Service(Path(self.config["service_home"]))
        self.runtime = Runtime(self.service)
        self._pins()

    def _pins(self) -> None:
        raw = (self.service.home / "service.json").read_bytes()
        expected = self.config
        actual = self.service.config
        if (
            hashlib.sha256(raw).hexdigest() != expected["service_config_ref"]["sha256"]
            or actual["service_id"] != expected["service_id"]
            or actual["target_root"] != expected["target_root"]
            or actual["worker_definition"] != expected["worker_definition"]
            or actual["os_profile"] != expected["os_profile"]
            or filesystem_identity(Path(actual["target_root"]))
            != expected["target_identity"]
            or self.runtime.record["instance_id"] != expected["parent_instance_id"]
        ):
            raise fr.FactoryError("factory_lad_lane_changed")
        # Read lifetime ownership without holding a lock across observations.
        marker = Path(actual["target_root"]) / OWNER
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_nlink != 1:
            raise fr.FactoryError("factory_lad_owner_missing")
        _owner_check(strict_json(marker.read_bytes()), actual, "lad")

    def _deadline(self, caller_deadline: float) -> None:
        if time.monotonic() >= caller_deadline:
            raise fr.FactoryError("factory_lad_observation_expired")

    def _task(self, task_id: str) -> dict:
        rows = self.service.intake.tasks(task_id)
        if len(rows) != 1:
            raise fr.FactoryError("factory_lad_task_missing")
        return rows[0]

    def _current_join(self, row: dict, deadline: float, observed: list) -> bool:
        expected = self._expected_receipts.get(row["envelope"]["request_id"])

        def validate(value):
            value = validate_call(
                value, str(self.service.config["os_home"]), row["envelope"]
            )
            receipt = value["core_receipt"]
            if (
                receipt is not None
                and expected is not None
                and receipt["status"] in {"applied", "refused"}
                and receipt != expected
            ):
                raise fr.FactoryError("factory_lad_receipt_conflict")
            return value

        for value in row["observations"]:
            validate(value)
        if _joined(self.service, row):
            observed.extend(row["observations"])
            return True
        self._deadline(deadline)
        # Exact GET only. Retain fresh proof without editing the service journal.
        value = validate(
            self.runtime.connection.observe(row["envelope"], deadline=deadline)
        )
        observed.append(value)
        return value["source"] == "os_owned_call" and value["holder_joined"] is True

    def _known_parent_controls(self, instance: dict, parent: dict) -> None:
        known = {}
        parent_rows = [self.runtime.record[key] for key in ("create", "start")]
        for stop in self.runtime.record["stops"].values():
            parent_rows.extend(stop[key] for key in ("pause", "cancel"))
        parent_rows.extend(
            entry["call"]
            for entry in self.runtime.record["maintenance"].values()
            if entry.get("scope") == "parent"
        )
        for row in parent_rows:
            if row["envelope"] is not None:
                known[row["envelope"]["request_id"]] = row["envelope"]
        for receipt in [*instance["controls"], *parent["controls"]]:
            envelope = known.get(receipt["request_id"])
            if envelope is None:
                raise fr.FactoryError("factory_lad_unknown_parent_control")
            validate_receipt(receipt, envelope)
        self._expected_receipts = {
            row["request_id"]: row
            for row in [*instance["controls"], *parent["controls"]]
        }

    def retain_paused_parent(self, *, caller_deadline: float) -> dict:
        """Observe a stable parent boundary, independent of task settlement."""
        self._deadline(caller_deadline)
        # A prior CAS failure or another owner command must not leave a stale
        # in-memory record as the source of a later positive proof.
        self.runtime = Runtime(self.service)
        self._pins()
        instance, parent = self.runtime.parent(_deadline=caller_deadline)
        if parent["instance_id"] != self.config["parent_instance_id"]:
            raise fr.FactoryError("factory_lad_parent_changed")
        if not closed(parent, attention=True):
            return {"retained": False, "reason": "factory_lad_parent_draining"}
        try:
            retained = self.runtime.retain_parent_joins(_deadline=caller_deadline)
        except IntakeError as exc:
            if exc.code != "lifecycle_conflict":
                raise
            return {"retained": False, "reason": "factory_lad_parent_observation_race"}
        instance, parent = self.runtime.parent(_deadline=caller_deadline)
        if not closed(parent, attention=True):
            return {"retained": False, "reason": "factory_lad_parent_draining"}
        self._known_parent_controls(instance, parent)
        positive = retained["holder_joined"] is True and all(
            row["holder_joined"] is True for row in retained["parent_controls"]
        )
        return {
            "retained": positive,
            "reason": None if positive else "factory_lad_parent_unjoined",
            "instance": instance,
            "parent": parent,
            "owner_proofs": retained,
        }

    def parked(self, task_id: str | None, *, caller_deadline: float) -> dict:
        """Retain parent joins first, then require the actual settled parked lane."""
        retained = self.retain_paused_parent(caller_deadline=caller_deadline)
        if not retained["retained"]:
            return {"closed": False, "reason": retained["reason"], "task": None}
        instance, parent = retained["instance"], retained["parent"]
        tasks = self.service.intake.tasks()
        if any(row["task_id"] not in self.home.lad_tasks() for row in tasks):
            raise fr.FactoryError("factory_lad_foreign_task")
        if any(
            row["status"] in {"queued", "claimed", "running", "held"} for row in tasks
        ):
            return {"closed": False, "reason": "factory_lad_unsettled", "task": None}
        if parent["status"] != "paused":
            return {
                "closed": False,
                "reason": "factory_lad_parent_not_paused",
                "task": None,
            }
        joined_observations = []
        current_controls = {row["request_id"]: row for row in parent["controls"]}
        pauses = []
        for entry in self.runtime.record["maintenance"].values():
            if entry.get("scope") != "parent":
                continue
            row = entry["call"]
            envelope = row["envelope"]
            if (
                envelope["instance_id"] != parent["instance_id"]
                or envelope["run_id"] != parent["id"]
            ):
                continue
            if not self._current_join(row, caller_deadline, joined_observations):
                return {
                    "closed": False,
                    "reason": "factory_lad_parent_unjoined",
                    "task": None,
                }
            # A queued OS receipt can precede its later public Core application.
            # Keep the cached receipt and the exact joined envelope unchanged.
            receipt = current_controls.get(envelope["request_id"])
            if receipt is not None:
                validate_receipt(receipt, envelope)
            if (
                envelope["operation"] == "pause_run"
                and receipt is not None
                and receipt["status"] == "applied"
            ):
                pauses.append(
                    {
                        "envelope": envelope,
                        "receipt": receipt,
                        "observations": row["observations"],
                    }
                )
        if not pauses:
            return {
                "closed": False,
                "reason": "factory_lad_pause_receipt_required",
                "task": None,
            }
        for key in ("create", "start"):
            if not self._current_join(
                self.runtime.record[key], caller_deadline, joined_observations
            ):
                return {
                    "closed": False,
                    "reason": "factory_lad_parent_unjoined",
                    "task": None,
                }
        task = None
        calls = []
        if task_id is not None:
            task = self._task(task_id)
            verify_terminal_current(self.runtime, task)
            journal, record = self.runtime.call(task, _deadline=caller_deadline)
            with journal.lock(observer=True):
                record = journal.load()
                for row in task_sent_calls(record, self.runtime.record):
                    if not self._current_join(
                        row, caller_deadline, joined_observations
                    ):
                        raise fr.FactoryError("factory_lad_task_unjoined")
                    calls.append(
                        {
                            "envelope": row["envelope"],
                            "receipt": row["receipt"],
                            "observations": row["observations"],
                        }
                    )
        return {
            "closed": True,
            "task": self._serial_task(task),
            "parent": parent,
            "instance": instance,
            "pause_refs": pauses,
            "call_refs": calls,
            "parent_join_observations": joined_observations,
        }

    @staticmethod
    def _serial_task(task: dict | None) -> dict | None:
        if task is None:
            return None
        return task | {"request_bytes": task["request_bytes"].decode()}

    def bind_lane(self, request_file: Path, *, caller_deadline: float) -> dict:
        request = self._request(request_file, "bind_lane")
        proof = self.parked(None, caller_deadline=caller_deadline)
        return self._result(
            request, "completed" if proof["closed"] else "pending", proof
        )

    def _request(self, file: Path, operation: str) -> dict:
        request = fr.validate_record(
            strict_json(file.read_bytes()), "millrace-plus-factory-lad-request/v1"
        )
        if (
            request["factory_id"] != self.home.factory_id
            or request["operation"] != operation
            or request["authority_ref"]
            != self.home.config["local_authority"]["authority_ref"]
            or self.home.read_ref(request["lane_ref"]) != self.config
        ):
            raise fr.FactoryError("factory_lad_request_mismatch")
        for key in ("input_ref", "task_ref", "candidate_ref", "control_ref"):
            if request[key] is not None:
                self.home.raw_ref(request[key])
        return request

    def _result(self, request: dict, status: str, proof: dict) -> dict:
        result = {
            "format": "millrace-plus-factory-lad-result/v1",
            "request_id": request["request_id"],
            "request_sha256": hashlib.sha256(canonical(request)).hexdigest(),
            "operation": request["operation"],
            "job_id": request["job_id"],
            "task_ref": request["task_ref"],
            "status": status,
            "artifact_refs": [self.home.retain(proof)],
            "closure_refs": [self.home.retain(proof)] if proof.get("closed") else [],
            "refusal": proof.get("reason"),
        }
        return fr.validate_record(result, result["format"])

    def submit_task(self, request_file: Path, *, caller_deadline: float) -> dict:
        request = self._request(request_file, "submit_task")
        self._deadline(caller_deadline)
        if request["input_ref"] is None:
            raise fr.FactoryError("factory_lad_input_required")
        raw = self.home.raw_ref(request["input_ref"])
        path = self.home.workspace / "requests" / (request["job_id"] + ".md")
        write_exact(path, raw)
        key = "t_" + base64.b32encode(
            hashlib.sha256(
                canonical(
                    {"factory_id": self.home.factory_id, "job_id": request["job_id"]}
                )
            ).digest()
        ).decode("ascii").rstrip("=")
        # Exact submission replay never allocates a second task.
        receipt = self.service.submit(key, path)
        return self._result(request, "completed", {"submission": receipt})

    def parent_resume_intent(
        self,
        job_id: str,
        task_ref: dict,
        request_ref: dict,
        *,
        caller_deadline: float,
        fresh: bool,
    ) -> dict:
        """Bind an explicit service-plane resume, without sending it."""
        self._deadline(caller_deadline)
        self._pins()
        sent = _parent_envelope(self.home.read_ref(request_ref))
        if sent["operation"] != "resume_plane" or sent["plane_id"] != "service":
            raise fr.FactoryError("factory_exact_service_resume_required")
        active = self.home.active()
        if active is None or active["task_id"] != job_id:
            raise fr.FactoryError("factory_lad_job_changed")
        selected = self.home.read_ref(task_ref)
        task = self._task(selected["task_id"])
        if task["request_sha256"] != selected["request_sha256"]:
            raise fr.FactoryError("factory_lad_task_changed")
        _, parent = self.runtime.parent(_deadline=caller_deadline)
        if (
            sent["instance_id"] != parent["instance_id"]
            or sent["run_id"] != parent["id"]
        ):
            raise fr.FactoryError("factory_lad_parent_changed")
        saved = self.runtime.record["maintenance"].get("parent:" + sent["request_id"])
        if saved is not None:
            if fresh or saved["scope"] != "parent" or saved["call"]["envelope"] != sent:
                raise fr.FactoryError("factory_lad_parent_control_id_spent")
        if saved is None or saved["call"]["reservation"] == "unsent":
            if task.get("claim") is None:
                raise fr.FactoryError("factory_lad_task_unclaimed")
            self.runtime.call(task, _deadline=caller_deadline)
            active_task = self.runtime.active()
            if (
                task["status"] not in {"claimed", "running", "held"}
                or active_task is None
                or active_task["task_id"] != task["task_id"]
            ):
                raise fr.FactoryError("factory_lad_task_changed")
            if (
                parent["status"] != "paused"
                or not closed(parent, attention=True)
                or parent.get("run_gate") is not None
                or parent["planes"]["service"]["status"] != "paused"
                or sent["expected_state_version"] != parent["state_version"]
            ):
                raise fr.FactoryError("factory_lad_service_resume_stale")
            if any(
                row["request_id"] == sent["request_id"] for row in parent["controls"]
            ):
                raise fr.FactoryError("factory_lad_parent_control_id_spent")
        return fr.validate_record(
            {
                "format": "millrace-plus-factory-lad-parent-resume/v1",
                "factory_id": self.home.factory_id,
                "job_id": job_id,
                "service_id": self.config["service_id"],
                "task_ref": task_ref,
                "parent_instance_id": parent["instance_id"],
                "parent_run_id": parent["id"],
                "request_ref": request_ref,
                "request_sha256": hashlib.sha256(canonical(sent)).hexdigest(),
                "state": "unsent",
                "receipt_ref": None,
                "join_refs": [],
            },
            "millrace-plus-factory-lad-parent-resume/v1",
        )

    def _control(
        self, request: dict, operation: str, *, caller_deadline: float
    ) -> dict:
        self._deadline(caller_deadline)
        if request["control_ref"] is not None:
            control_ref = request["control_ref"]
            raw = self.home.raw_ref(control_ref)
            envelope = strict_json(raw)
        else:
            _, parent = self.runtime.parent(_deadline=caller_deadline)
            envelope = {
                "format": "millrace-router-request/v2",
                "request_id": uuid.uuid5(
                    uuid.UUID(hex=self.home.factory_id),
                    request["job_id"] + "/" + operation,
                ).hex,
                "operation": operation,
                "instance_id": parent["instance_id"],
                "run_id": parent["id"],
                "expected_state_version": parent["state_version"],
                "plane_id": None,
                "payload": {},
            }
            control_ref = self.home.retain(envelope)
            raw = canonical(envelope)
        path = self.home.workspace / "controls" / (envelope["request_id"] + ".json")
        write_exact(path, raw)
        # parent_control owns durable unsent/unknown/observed + GET-only replay.
        result = self.runtime.parent_control(path, _deadline=caller_deadline)
        return {"control_ref": control_ref, "result": result}

    def _automatic_queued_resume(
        self, request: dict, script: Path | None, *, caller_deadline: float
    ) -> None:
        """Validate the selected graph invocation and original accepted task."""
        self._deadline(caller_deadline)
        import os

        from . import cicd_candidates as facade

        if script is None:
            raise fr.FactoryError("factory_lad_task_unclaimed")
        # The actual active script/PID/CWD grants the automatic path, not the
        # presence or shape of an intent file or a caller-supplied boolean.
        facade.bind_invocation(
            self.home.home,
            "factory",
            "resume_lad_parent",
            script,
            os.getpid(),
            Path.cwd(),
        )
        active = self.home.active()
        state = self.home.state(request["job_id"])
        facts = self.home.facts(state)
        original = self.home.intake.tasks(request["job_id"])[0]
        task = self._task(self.home.read_ref(request["task_ref"])["task_id"])
        intent = fr.validate_record(
            self.home.read_ref(facts["parent_resume_ref"]),
            "millrace-plus-factory-lad-parent-resume/v1",
        )
        envelope = _parent_envelope(self.home.read_ref(request["control_ref"]))
        if (
            active is None
            or active["task_id"] != request["job_id"]
            or state["phase"] != "resume_lad_parent"
            or original["request_bytes"] != self.home.raw_ref(state["input_ref"])
            or facts["lad_task_ref"] != request["task_ref"]
            or facts["lad_task_id"] != task["task_id"]
            or task["request_bytes"] != self.home.raw_ref(facts["lad_rendered_ref"])
            or task["request_sha256"]
            != self.home.read_ref(request["task_ref"])["request_sha256"]
            or intent["factory_id"] != self.home.factory_id
            or intent["job_id"] != request["job_id"]
            or intent["service_id"] != self.config["service_id"]
            or intent["task_ref"] != request["task_ref"]
            or intent["request_ref"] != request["control_ref"]
            or intent["request_sha256"]
            != hashlib.sha256(canonical(envelope)).hexdigest()
            or intent["parent_instance_id"] != envelope["instance_id"]
            or intent["parent_run_id"] != envelope["run_id"]
            or envelope["operation"] != "resume_plane"
            or envelope["plane_id"] != "service"
        ):
            raise fr.FactoryError("factory_lad_automatic_resume_mismatch")
        self._pins()
        _, parent = self.runtime.parent(_deadline=caller_deadline)
        if (
            envelope["instance_id"] != parent["instance_id"]
            or envelope["run_id"] != parent["id"]
        ):
            raise fr.FactoryError("factory_lad_parent_changed")
        saved = self.runtime.record["maintenance"].get(
            "parent:" + envelope["request_id"]
        )
        if saved is not None and (
            saved.get("scope") != "parent"
            or saved["call"]["reservation"] not in {"unsent", "unknown", "observed"}
            or saved["call"]["envelope"] != envelope
            or saved["request_raw"]
            != self.home.raw_ref(request["control_ref"]).decode()
        ):
            raise fr.FactoryError("factory_lad_parent_control_id_spent")
        if saved is None or saved["call"]["reservation"] == "unsent":
            if task["status"] != "queued" or task["claim"] is not None:
                raise fr.FactoryError("factory_lad_automatic_resume_mismatch")
            plane = parent["planes"]["service"]
            if (
                not closed(parent, attention=True)
                or parent["status"] != "paused"
                or parent.get("run_gate") is not None
                or plane["status"] != "paused"
                or not (plane.get("resume_target") or plane.get("cursor"))
                or parent["state_version"] != envelope["expected_state_version"]
            ):
                raise fr.FactoryError("factory_lad_service_resume_stale")

    def resume_parent(
        self, request_file: Path, *, caller_deadline: float, script: Path | None = None
    ) -> dict:
        request = self._request(request_file, "resume_parent")
        if request["task_ref"] is None or request["control_ref"] is None:
            raise fr.FactoryError("factory_lad_resume_intent_required")
        envelope = self.home.read_ref(request["control_ref"])
        graph_sent = False
        if envelope["operation"] == "resume_plane" and script is not None:
            saved = self.runtime.record["maintenance"].get(
                "parent:" + envelope["request_id"]
            )
            if saved is not None:
                reservation = saved["call"]["reservation"]
                if reservation not in {"unsent", "unknown", "observed"}:
                    raise fr.FactoryError("factory_lad_parent_control_state_invalid")
                graph_sent = reservation in {"unknown", "observed"}
        if envelope["operation"] == "resume_plane" and (
            graph_sent
            or self._task(self.home.read_ref(request["task_ref"])["task_id"]).get(
                "claim"
            )
            is None
        ):
            self._automatic_queued_resume(
                request, script, caller_deadline=caller_deadline
            )
        elif envelope["operation"] == "resume_plane":
            self.parent_resume_intent(
                request["job_id"],
                request["task_ref"],
                request["control_ref"],
                caller_deadline=caller_deadline,
                fresh=False,
            )
        try:
            proof = self._control(
                request, envelope["operation"], caller_deadline=caller_deadline
            )
        except IntakeError as exc:
            if exc.code not in {"target_busy", "lifecycle_conflict"}:
                raise
            # No losing observation grants proof. The graph reenters with a
            # fresh Lane/Runtime and this exact intent through the owner API.
            return self._result(
                request,
                "pending",
                {
                    "control_ref": request["control_ref"],
                    "reason": "factory_lad_parent_busy"
                    if exc.code == "target_busy"
                    else "factory_lad_parent_observation_race",
                },
            )
        result = proof["result"]
        receipt = result["receipt"]
        if receipt is not None and receipt["status"] == "refused":
            return self._result(
                request, "held", proof | {"reason": "factory_lad_resume_refused"}
            )
        return self._result(
            request,
            "completed"
            if receipt is not None and receipt["status"] == "applied"
            else "pending",
            proof,
        )

    def _observation_end(self, caller_deadline: float) -> float:
        window = self.home.config["timeouts"]["observation_windows"].get("observe_lad")
        return min(
            caller_deadline, time.monotonic() + (window["seconds"] if window else 60)
        )

    def wait_paused_parent(self, *, caller_deadline: float) -> dict:
        """Use the existing observation window before another hold invocation."""
        end = self._observation_end(caller_deadline)
        proof = self.retain_paused_parent(caller_deadline=caller_deadline)
        while not proof["retained"]:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return proof
            time.sleep(min(5, remaining))
            if time.monotonic() >= end:
                return proof
            proof = self.retain_paused_parent(caller_deadline=caller_deadline)
        return proof

    def observe_task(self, request_file: Path, *, caller_deadline: float) -> dict:
        request = self._request(request_file, "observe_task")
        task_id = self.home.read_ref(request["task_ref"])["task_id"]
        end = self._observation_end(caller_deadline)
        last = None
        while True:
            self._deadline(caller_deadline)
            task = self._task(task_id)
            if (
                task["request_sha256"]
                != self.home.read_ref(request["task_ref"])["request_sha256"]
            ):
                raise fr.FactoryError("factory_lad_task_changed")
            if task["status"] == "succeeded":
                verify_terminal_current(self.runtime, task)
                return self._result(
                    request, "completed", {"task": self._serial_task(task)}
                )
            # A Service error pause can leave intake running/claimed. Observe
            # the actual parent boundary without relabeling that task projection.
            retained = self.retain_paused_parent(caller_deadline=caller_deadline)
            if (
                task["status"] in {"held", "failed", "cancelled"}
                or retained["retained"]
            ):
                if not retained["retained"]:
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        return self._result(
                            request,
                            "pending",
                            {
                                "task": self._serial_task(task),
                                "parent_retention": retained,
                            },
                        )
                    time.sleep(min(5, remaining))
                    continue
                return self._result(
                    request,
                    "held",
                    {
                        "task": self._serial_task(task),
                        "reason": "factory_lad_" + task["status"]
                        if task["status"] in {"held", "failed", "cancelled"}
                        else "factory_lad_parent_attention",
                    },
                )
            serial = self._serial_task(task)
            if serial != last:
                self.home.retain(serial)
                last = serial
            remaining = end - time.monotonic()
            if remaining <= 0:
                return self._result(request, "pending", {"task": serial})
            time.sleep(min(5, remaining))

    def _binding(
        self, task_id: str, *, caller_deadline: float, closure: dict | None = None
    ) -> dict | None:
        """Retain this exact service claim/call; absent child facts stay null."""
        task = self._task(task_id)
        claim = task["claim"]
        if claim is None:
            return None
        call_file = Path(claim["call_home"]) / claim["call_key"] / "call.json"
        record = None
        if call_file.exists():
            _, record = self.runtime.call(task, _deadline=caller_deadline)
        binding = {
            "format": "millrace-plus-factory-lad-binding/v1",
            "factory_id": self.home.factory_id,
            "job_id": self.home.active()["task_id"],
            "service_id": self.service.service_id,
            "service_config_sha256": self.config["service_config_ref"]["sha256"],
            "task_id": task_id,
            "request_sha256": task["request_sha256"],
            "claim_ref": self.home.retain(claim),
            "call_home": claim["call_home"],
            "call_key": claim["call_key"],
            "definition": claim["definition"],
            "parent_ref": self.home.retain(
                {"intent": self.runtime.intent, "record": self.runtime.record}
            ),
            "child_ref": self.home.retain(record) if record is not None else None,
            "terminal_ref": self.home.retain(task["terminal_receipt"])
            if task["terminal_receipt"] is not None
            else None,
            "join_refs": [
                self.home.retain(row) for row in (closure or {}).get("call_refs", [])
            ],
        }
        return self.home.retain(fr.validate_record(binding, binding["format"]))

    def park_lane(self, request_file: Path, *, caller_deadline: float) -> dict:
        request = self._request(request_file, "park_lane")
        task_id = self.home.read_ref(request["task_ref"])["task_id"]
        task = self._task(task_id)
        if task["status"] not in {"succeeded", "failed", "cancelled"}:
            return self._result(
                request, "held", {"reason": "factory_lad_task_unsettled"}
            )
        verify_terminal_current(self.runtime, task)
        if request["control_ref"] is None:
            raise fr.FactoryError("factory_lad_pause_intent_required")
        self._control(request, "pause_run", caller_deadline=caller_deadline)
        window = self.home.config["timeouts"]["observation_windows"].get("park_lad")
        end = min(
            caller_deadline, time.monotonic() + (window["seconds"] if window else 60)
        )
        while True:
            proof = self.parked(task_id, caller_deadline=caller_deadline)
            if proof["closed"]:
                return self._result(request, "completed", proof)
            remaining = end - time.monotonic()
            if remaining <= 0:
                return self._result(request, "pending", proof)
            time.sleep(min(5, remaining))

    def cancel_task(self, request_file: Path, *, caller_deadline: float) -> dict:
        request = self._request(request_file, "cancel_task")
        self._deadline(caller_deadline)
        task_id = self.home.read_ref(request["task_ref"])["task_id"]
        return self._result(
            request,
            "pending",
            {"cancel": self.runtime.cancel(task_id, request["request_id"])},
        )

    def accept_candidate(self, request_file: Path, *, caller_deadline: float) -> dict:
        return self._synchronize(request_file, True, caller_deadline)

    def restore_candidate(self, request_file: Path, *, caller_deadline: float) -> dict:
        return self._synchronize(request_file, False, caller_deadline)

    def _synchronize(self, request_file: Path, accepted: bool, deadline: float) -> dict:
        from .factory_candidate import synchronize

        request = self._request(
            request_file, "accept_candidate" if accepted else "restore_candidate"
        )
        proof = synchronize(
            self.home,
            self.home.state(request["job_id"]),
            accepted=accepted,
            deadline=deadline,
        )
        return self._result(request, "completed", proof)
