"""CI/CD workflow home, operator decisions and deterministic graph steps.

Core owns routing, controls and invocation history. This module owns the event
queue policy (through the shared local intake primitive), frozen source facts,
single-use reservations, guards and reports. Saved phase text never selects a
graph target: each step returns only an outcome its node declares.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from . import catalog
from .cicd_checks import BATCH, run_batch
from .cicd_records import (
    DIAGNOSES,
    MAX_EVENT,
    MAX_LOG,
    REPORT,
    SMALL_FILES,
    VERDICTS,
    CicdError,
    digest,
    event_from_key,
    event_identity,
    intake_key,
    read_bounded,
    read_log,
    ref,
    repairable,
    sha256_text,
    validate_config,
    validate_diagnosis,
    validate_event,
    validate_repair,
    validate_report,
    validate_review,
)
from .cicd_source import (
    canonical_dir,
    claim_target,
    copy_before,
    filesystem_identity,
    git_head,
    overlap,
    scope,
    snapshot,
    target_lock,
    write_exact,
    write_replace,
)
from .local_intake import Intake, IntakeError, canonical, identity, strict_json

__all__ = ["Home", "Runtime", "initialize", "step"]

SERVICE = "millrace-plus-cicd-service/v1"
INSTANCE = "millrace-plus-cicd-instance/v1"
PAYLOAD = "millrace-plus-cicd-intake/v1"
STATE = "millrace-plus-cicd-event-state/v1"
DECISION = "millrace-plus-cicd-decision/v1"
OPERATOR = "millrace-plus-cicd-operator-decision/v1"
ROLE_INPUT = "millrace-plus-cicd-role-input/v1"
ACTIVE_ROLE = "millrace-plus-cicd-active-role/v1"
DECISIONS = ("cancel", "close", "retry-checks", "settle")
NO_ACTION = ("success", "cancelled", "skipped")
TERMINAL = ("succeeded", "failed", "cancelled")
ACTIVE = ("claimed", "running", "held")
ROLE_OUTCOMES = {
    "diagnose": DIAGNOSES,
    "repair": ("PATCH_READY", "BLOCKED"),
    "review": VERDICTS,
}
DIAGNOSIS_HOLDS = {
    "LARGE_ISSUE": ("HOLD_LARGE", "large_issue", "held_large"),
    "UNREPRODUCED": ("HOLD_UNPROVEN", "unreproduced", "held_unreproduced"),
    "EXTERNAL_BLOCKER": ("HOLD_EXTERNAL", "external_blocker", "held_external"),
}
FREEZE_HOLDS = frozenset(
    {
        "head_mismatch",
        "target_not_git",
        "target_not_git_root",
        "source_too_large",
        "source_changed",
        "freeze_replay_conflict",
    }
)
WAIT_SECONDS = 300
WAIT_POLL = 1.0


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, code: str) -> dict[str, object]:
    try:
        value = strict_json(raw)
    except IntakeError as exc:
        raise CicdError(code) from exc
    if not isinstance(value, dict):
        raise CicdError(code)
    return cast(dict[str, object], value)


def _mapping(value: object, code: str = "invalid_state") -> dict[str, object]:
    if not isinstance(value, dict):
        raise CicdError(code)
    return cast(dict[str, object], value)


def _fields(value: object, names: set[str], code: str) -> dict[str, object]:
    obj = _mapping(value, code)
    if set(obj) != names:
        raise CicdError(code)
    return obj


def _result(outcome: str, summary: str | None = None) -> dict[str, object]:
    return {"outcome": outcome, "summary": summary or outcome, "refs": []}


def _validate_service(value: object, home: Path) -> dict[str, object]:
    code = "invalid_service_record"
    obj = _fields(
        value,
        {
            "format",
            "service_id",
            "service_home",
            "target_root",
            "target_identity",
            "config_sha256",
            "state_home",
            "create_request_id",
            "settings",
        },
        code,
    )
    if obj["format"] != SERVICE or obj["service_home"] != str(home):
        raise CicdError(code)
    identity(obj["service_id"])
    identity(obj["create_request_id"])
    sha256_text(obj["config_sha256"], code)
    settings = _fields(obj["settings"], {"path", "env_allowlist", "host_paths"}, code)
    hosts = _fields(settings["host_paths"], {"cicd_target", "cicd_agent"}, code)
    if hosts["cicd_target"] != obj["target_root"]:
        raise CicdError(code)
    if settings["env_allowlist"] != ["HOME"]:
        raise CicdError(code)
    return obj


def _new_state(event_id: str) -> dict[str, object]:
    return {
        "format": STATE,
        "event_id": event_id,
        "freeze": None,
        "disposition": None,
        "diagnosis": None,
        "repair": None,
        "batches": [],
        "hold": None,
        "applied": [],
        "report_sha256": None,
        "settlement": None,
    }


def _batches(state: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], state["batches"])


def _applied(state: dict[str, object], request_id: object) -> list[object]:
    applied = cast(list[object], state["applied"])
    if request_id is None or request_id in applied:
        return applied
    return [*applied, request_id]


class Home:
    """One owned CI/CD workflow home: frozen config, intake and event records."""

    def __init__(self, home: Path) -> None:
        self.home = canonical_dir(str(home), "invalid_workflow_home")
        raw = read_bounded(
            self.home / "cicd.json", MAX_EVENT, "invalid_service_record"
        )
        self.record = _validate_service(
            _json(raw, "invalid_service_record"), self.home
        )
        self.record_sha256 = _sha(raw)
        config_raw = read_bounded(
            self.home / "config.json", MAX_EVENT, "invalid_config"
        )
        if _sha(config_raw) != self.record["config_sha256"]:
            raise CicdError("config_changed")
        self.config = validate_config(config_raw)
        self.config_sha256 = _sha(config_raw)
        binding = cast(dict[str, object], self.config["repair_binding"])
        hosts = cast(dict[str, dict[str, object]], self.record["settings"])
        if (
            self.config["target_root"] != self.record["target_root"]
            or binding["executable"] != hosts["host_paths"]["cicd_agent"]
        ):
            raise CicdError("config_changed")
        self.target = canonical_dir(str(self.record["target_root"]), "invalid_target")
        self.target_identity = cast(dict[str, object], self.record["target_identity"])
        self.service_id = str(self.record["service_id"])
        self.workspace = self.home / "project" / "main"
        self.events = self.workspace / "events"
        self.intake = Intake(
            self.home / "intake.sqlite3",
            self.service_id,
            validate_payload=self._payload,
            validate_claim=self._claim,
            validate_decision=self._decision,
            validate_decision_aftermath=self._aftermath,
            task_id_for_key=lambda key: event_from_key(key)[:32],
            decision_receipt=self._receipt,
            authority_sha256=self.record_sha256,
        )

    # Intake callbacks: storage stays generic, CI policy stays here.
    @staticmethod
    def _payload(raw: bytes) -> None:
        code = "invalid_intake_payload"
        obj = _fields(
            _json(raw, code), {"format", "event_id", "event_sha256", "log_sha256"}, code
        )
        if obj["format"] != PAYLOAD or canonical(obj) != raw:
            raise CicdError(code)
        sha256_text(obj["event_id"], code)
        sha256_text(obj["event_sha256"], code)
        if obj["log_sha256"] is not None:
            sha256_text(obj["log_sha256"], code)

    @staticmethod
    def _claim(value: dict[str, object]) -> None:
        names = {"task_id", "request_sha256", "event_id"}
        obj = _fields(value, names, "invalid_claim")
        if str(obj["event_id"])[:32] != obj["task_id"]:
            raise CicdError("invalid_claim")

    @staticmethod
    def _decision(value: dict[str, object]) -> None:
        names = {"task_id", "decision", "evidence"}
        obj = _fields(value, names, "invalid_decision")
        identity(obj["task_id"])
        if obj["decision"] not in DECISIONS:
            raise CicdError("invalid_decision")
        if obj["decision"] == "cancel" and obj["evidence"] is not None:
            raise CicdError("invalid_decision")
        canonical(obj["evidence"])

    def _receipt(
        self, request_id: str, intent: dict[str, object]
    ) -> dict[str, object]:
        return {
            "format": DECISION,
            "service_id": self.service_id,
            "request_id": request_id,
            "task_id": intent["task_id"],
            "decision": intent["decision"],
            "evidence_sha256": digest(intent["evidence"]),
        }

    @staticmethod
    def _aftermath(
        task: dict[str, object], intent: dict[str, object], receipt: dict[str, object]
    ) -> None:
        terminal = task["terminal_receipt"] == receipt
        if intent["decision"] == "settle" and not terminal:
            raise CicdError("invalid_decision_aftermath")
        if terminal and (
            intent["decision"] not in {"cancel", "settle"}
            or task["status"] not in TERMINAL
            or (intent["decision"] == "cancel" and task["status"] != "cancelled")
        ):
            raise CicdError("invalid_decision_aftermath")

    @contextmanager
    def lock(self) -> Iterator[None]:
        with target_lock(
            self.target, self.target_identity, self.home, self.service_id
        ):
            yield

    def instance(self) -> str:
        code = "instance_missing"
        raw = read_bounded(self.home / "instance.json", 4096, code)
        names = {"format", "service_id", "instance_id", "create_request_id"}
        obj = _fields(_json(raw, code), names, code)
        if (
            obj["format"] != INSTANCE
            or obj["service_id"] != self.service_id
            or obj["create_request_id"] != self.record["create_request_id"]
        ):
            raise CicdError(code)
        return identity(obj["instance_id"])

    # Event records.
    def event_dir(self, event_id: str) -> Path:
        return self.events / sha256_text(event_id, "invalid_event_id")

    @contextmanager
    def _state_lock(self) -> Iterator[None]:
        descriptor = os.open(
            self.home / "state.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def load_state(self, event_id: str) -> dict[str, object] | None:
        path = self.event_dir(event_id) / "state.json"
        if not path.exists():
            return None
        raw = read_bounded(path, 4 * 1048576, "invalid_state")
        state = _json(raw, "invalid_state")
        if (
            state.get("format") != STATE
            or state.get("event_id") != event_id
            or type(state.get("version")) is not int
        ):
            raise CicdError("invalid_state")
        return state

    def save_state(
        self, state: dict[str, object], expected: int | None
    ) -> dict[str, object]:
        """Compare-and-swap one event record; reservations are never removed."""
        with self._state_lock():
            return self._save_state_locked(state, expected)

    def _save_state_locked(
        self, state: dict[str, object], expected: int | None
    ) -> dict[str, object]:
        """`save_state` for a caller already holding `_state_lock` (not reentrant)."""
        event_id = str(state["event_id"])
        current = self.load_state(event_id)
        if (current is None) != (expected is None) or (
            current is not None and current["version"] != expected
        ):
            raise CicdError("stale_event_state")
        saved = state | {"version": 1 if expected is None else expected + 1}
        path = self.event_dir(event_id) / "state.json"
        write_replace(path, canonical(saved) + b"\n")
        return saved

    def update(self, state: dict[str, object], **changes: object) -> dict[str, object]:
        return self.save_state(state | changes, cast(int, state["version"]))

    def file_ref(self, path: Path) -> dict[str, object]:
        return {
            "path": path.relative_to(self.workspace).as_posix(),
            "sha256": _sha(path.read_bytes()),
        }

    def verify_ref(self, value: object, code: str) -> Path:
        item = ref(value, code)
        current = self.workspace
        for part in Path(str(item["path"])).parts:
            current = current / part
            if current.is_symlink():
                raise CicdError(code)
        if _sha(read_bounded(current, 16 * 1048576, code)) != item["sha256"]:
            raise CicdError(code)
        return current

    # Operator surface.
    def submit(self, event_file: Path) -> dict[str, object]:
        if not event_file.is_absolute():
            event_file = Path.cwd() / event_file
        raw = read_bounded(event_file, MAX_EVENT, "invalid_event_file")
        event = validate_event(raw, str(self.config["repository"]))
        log = read_log(event_file, event)
        event_id = event_identity(event)
        payload = {
            "format": PAYLOAD,
            "event_id": event_id,
            "event_sha256": _sha(raw),
            "log_sha256": None if log is None else _sha(log),
        }
        with self.lock():
            directory = self.event_dir(event_id)
            # Exact bytes are retained before acceptance; conflicts refuse.
            write_exact(directory / "event.json", raw, "event_conflict")
            if log is not None:
                write_exact(directory / "log.txt", log, "event_conflict")
            receipt = self.intake.submit(intake_key(event_id), canonical(payload))
        return {"event_id": event_id, "receipt": receipt}

    def task(self, event_id: str) -> dict[str, object]:
        sha256_text(event_id, "invalid_event_id")
        task = self.intake.tasks(event_id[:32])[0]
        if event_from_key(str(task["intake_key"])) != event_id:
            raise CicdError("event_not_found")
        return task

    def status(self, event_id: str | None = None) -> dict[str, object]:
        tasks = self.intake.tasks() if event_id is None else [self.task(event_id)]
        rows = []
        for task in tasks:
            current = event_from_key(str(task["intake_key"]))
            state = self.load_state(current)
            report = self.event_dir(current) / "report.json"
            rows.append(
                {
                    "event_id": current,
                    "sequence": task["sequence"],
                    "status": task["status"],
                    "hold": None if state is None else state["hold"],
                    "report": self.file_ref(report) if report.exists() else None,
                    "terminal_receipt": task["terminal_receipt"],
                }
            )
        return {"service_id": self.service_id, "events": rows}

    def cancel(self, event_id: str, request_id: str) -> dict[str, object]:
        """Queued cancel is terminal now; active cancel settles at reconcile."""
        task = self.task(event_id)
        intent = {"task_id": task["task_id"], "decision": "cancel", "evidence": None}

        def check(current: dict[str, object]) -> None:
            if current["status"] in TERMINAL:
                raise CicdError("event_terminal")

        receipt = self.intake.decide(
            request_id, intent, check=check, cancel_queued=task["status"] == "queued"
        )
        if task["status"] in ACTIVE:
            self._append(event_id, request_id, "cancel", None)
        return receipt

    def decide(
        self, event_id: str, decision: str, request_id: str, evidence: Path
    ) -> dict[str, object]:
        if decision == "approve-publish":
            raise CicdError("publication_not_supported")
        if decision not in {"close", "retry-checks"}:
            raise CicdError("invalid_decision")
        identity(request_id)
        raw = read_bounded(evidence, MAX_EVENT, "invalid_evidence")
        try:
            blank = not raw.decode("utf-8").strip()
        except UnicodeError as exc:
            raise CicdError("invalid_evidence") from exc
        if blank:
            raise CicdError("invalid_evidence")
        task = self.task(event_id)
        state = self.load_state(event_id)
        path = self.event_dir(event_id) / "decisions" / (request_id + ".evidence")
        write_exact(path, raw, "decision_conflict")
        evidence_ref = self.file_ref(path)

        def check(current: dict[str, object]) -> None:
            if current["status"] != "held" or state is None:
                raise CicdError("event_not_held")
            if decision == "retry-checks":
                repair = state["repair"]
                batches = _batches(state)
                if (
                    not isinstance(repair, dict)
                    or repair["guard"] != "SCOPED"
                    or not batches
                    or batches[-1]["status"] == "reserved"
                ):
                    raise CicdError("retry_checks_unavailable")

        intent = {
            "task_id": task["task_id"],
            "decision": decision,
            "evidence": evidence_ref,
        }
        # Policy applies to a new request only; an exact replay (including one
        # whose record was never published) returns the stored receipt.
        if self.intake.decision(request_id) is None:
            if state is not None and self.pending(state, *DECISIONS) is not None:
                raise CicdError("decision_pending")
        receipt = self.intake.decide(request_id, intent, check=check)
        self._append(event_id, request_id, decision, evidence_ref)
        return receipt

    def _append(
        self,
        event_id: str,
        request_id: str,
        decision: str,
        evidence_ref: dict[str, object] | None,
    ) -> None:
        """Save one immutable operator decision; graph steps own `state.json`."""
        row = {
            "format": OPERATOR,
            "request_id": request_id,
            "decision": decision,
            "evidence_ref": evidence_ref,
        }
        path = self.event_dir(event_id) / "decisions" / (request_id + ".json")
        # Exact replay after an interrupted append is accepted; a change refuses.
        write_exact(path, canonical(row) + b"\n", "decision_conflict")

    def decisions(self, event_id: str) -> list[dict[str, object]]:
        """Accepted operator decisions, checked against their published records.

        The accepted Intake decisions for this event are the source; each must
        have exactly its published projection and no other record may exist.
        A missing projection refuses `decision_projection_missing` until an
        exact replay of the same request republishes it. Never writes.
        """
        code = "invalid_decision"
        folder = self.event_dir(event_id) / "decisions"
        accepted = [
            row
            for row in self.intake.decisions(event_id[:32])
            if _mapping(row["intent"], code)["decision"] != "settle"
        ]
        published = {
            path.name: path
            for path in (sorted(folder.glob("*.json")) if folder.is_dir() else [])
        }
        names = {str(row["request_id"]) + ".json" for row in accepted}
        if set(published) - names:
            raise CicdError("decision_unaccepted")
        rows = []
        for item in accepted:
            request_id, intent = str(item["request_id"]), _mapping(item["intent"])
            decision, evidence = intent["decision"], intent["evidence"]
            row = {
                "format": OPERATOR,
                "request_id": request_id,
                "decision": decision,
                "evidence_ref": evidence,
            }
            expected = f"events/{event_id}/decisions/{request_id}.evidence"
            if (
                decision not in {"cancel", "close", "retry-checks"}
                or (decision == "cancel") != (evidence is None)
                or (evidence is not None and ref(evidence, code)["path"] != expected)
            ):
                raise CicdError(code)
            path = published.get(request_id + ".json")
            if path is None:
                raise CicdError("decision_projection_missing")
            if read_bounded(path, MAX_EVENT, code) != canonical(row) + b"\n":
                raise CicdError("decision_conflict")
            if evidence is not None:
                self.verify_ref(evidence, "invalid_evidence")
            rows.append(row)
        return rows

    def pending(
        self, state: dict[str, object], *names: str
    ) -> dict[str, object] | None:
        applied = cast(list[object], state["applied"])
        for row in self.decisions(str(state["event_id"])):
            if row["request_id"] not in applied and row["decision"] in names:
                return row
        return None


def _settings(target: Path, executable: str) -> dict[str, object]:
    return {
        "path": list(
            dict.fromkeys([str(Path(sys.executable).parent), *os.get_exec_path()])
        ),
        "env_allowlist": ["HOME"],
        "host_paths": {"cicd_target": str(target), "cicd_agent": executable},
    }


def initialize(
    home: Path,
    target: Path,
    config_file: Path,
    state_home: Path | None = None,
    *,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    """Create the home, target claim, intake and Core instance. Starts nothing.

    The Core store defaults to `<home>/state`, owned by this home. An explicit
    store is frozen at first init; a replay without one reuses the frozen value.
    """
    from .router import control

    target = canonical_dir(str(target), "invalid_target")
    home = canonical_dir(str(home), "invalid_workflow_home", directory=False)
    if not home.parent.is_dir():
        raise CicdError("invalid_workflow_home")
    if state_home is None and (home / "cicd.json").is_file():
        state_home = Path(str(Home(home).record["state_home"]))
    explicit = state_home is not None
    state_home = canonical_dir(
        str(home / "state" if state_home is None else state_home),
        "invalid_state_home",
        directory=False,
    )
    if explicit and not state_home.parent.is_dir():
        raise CicdError("invalid_state_home")
    raw = read_bounded(config_file, MAX_EVENT, "invalid_config")
    config = validate_config(raw)
    if config["target_root"] != str(target):
        raise CicdError("target_mismatch")
    executable = str(cast(dict[str, object], config["repair_binding"])["executable"])
    if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise CicdError("invalid_repair_binding")
    for root in (home, state_home, *catalog.known_roots(catalog_home)):
        if overlap(target, root):
            raise CicdError("target_overlap")
    # The store may live inside the home, never in its workflow or project.
    for owned in (home / "workflow", home / "project"):
        if overlap(owned, state_home):
            raise CicdError("state_home_overlap")
    if state_home != home / "state" and overlap(home, state_home):
        raise CicdError("state_home_overlap")
    git_head(target)  # Read-only proof that the target is the exact Git root.
    if home.exists():
        current = Home(home)
        if (
            current.config_sha256 != _sha(raw)
            or current.record["state_home"] != str(state_home)
        ):
            raise CicdError("initialization_conflict")
    else:
        service_id = uuid.uuid4().hex
        record: dict[str, object] = {
            "format": SERVICE,
            "service_id": service_id,
            "service_home": str(home),
            "target_root": str(target),
            "target_identity": filesystem_identity(target),
            "config_sha256": _sha(raw),
            "state_home": str(state_home),
            "create_request_id": uuid.uuid5(uuid.UUID(service_id), "create").hex,
            "settings": _settings(target, executable),
        }
        home.mkdir(mode=0o700)
        write_exact(home / "config.json", raw)
        write_exact(home / "cicd.json", canonical(record) + b"\n")
        current = Home(home)
        claim_target(target, current.target_identity, home, service_id)
        current.intake.initialize()
        catalog.init_template(
            "bundled:cicd", home / "workflow", catalog_home=catalog_home
        )
        current.events.mkdir(parents=True)
    # Ownership must still be exact before Core captures the copy.
    with current.lock():
        if not (home / "workflow" / "workflow.json").is_file():
            raise CicdError("partial_initialization_hold")
    settings = cast(dict[str, object], current.record["settings"])
    receipt = control.create_instance(
        home / "workflow" / "workflow.json",
        home / "project",
        state_home=state_home,
        request_id=str(current.record["create_request_id"]),
        expected_state_version=0,
        path=tuple(cast(list[str], settings["path"])),
        env_allowlist=("HOME",),
        host_paths=cast(dict[str, str], settings["host_paths"]),
        catalog_home=catalog_home,
    )
    if receipt.get("status") != "applied":
        # Core's durable refusal code is preserved; nothing here retries it.
        raise CicdError(str(receipt.get("code") or "instance_create_refused"))
    instance_id = identity(receipt.get("instance_id"))
    record = {
        "format": INSTANCE,
        "service_id": current.service_id,
        "instance_id": instance_id,
        "create_request_id": current.record["create_request_id"],
    }
    write_exact(
        home / "instance.json", canonical(record) + b"\n", "initialization_conflict"
    )
    return {
        "service_id": current.service_id,
        "instance_id": instance_id,
        "workflow_home": str(home),
        "start": [
            "millrace-plus",
            "router",
            "--state-home",
            str(state_home),
            "start",
            "--instance",
            instance_id,
        ],
    }


class Runtime:
    """Deterministic graph steps. Each step reads Core facts fresh."""

    def __init__(self, home: Home, *, controls: Path) -> None:
        from .router import control

        self.home = home
        self.controls = controls
        self.control = control
        self.state_home = Path(str(home.record["state_home"]))
        self.instance_id = home.instance()

    # Core facts: public reads only; no Core state is ever edited here.
    def _run(self, run_id: str) -> dict[str, object]:
        return self.control.inspect_run(
            self.instance_id, run_id, state_home=self.state_home
        )

    def current(self, node: str) -> tuple[dict[str, object], dict[str, object]]:
        view = self.control.inspect_instance(
            self.instance_id, state_home=self.state_home
        )
        hosts = cast(dict[str, object], self.home.record["settings"])["host_paths"]
        project = str(self.home.home / "project")
        if view["host_paths"] != hosts or view["project_root"] != project:
            raise CicdError("instance_settings_changed")
        runs = [
            row
            for row in cast(list[dict[str, object]], view["runs"])
            if row["status"] not in TERMINAL
        ]
        if len(runs) != 1 or runs[0]["status"] != "running":
            raise CicdError("run_not_running")
        run = self._run(str(runs[0]["id"]))
        active = _mapping(_mapping(run["planes"])["main"]).get("active_invocation")
        mine = [
            row
            for row in cast(list[dict[str, object]], run["invocations"])
            if row["id"] == active
        ]
        if len(mine) != 1 or mine[0]["node"] != node or mine[0]["plane_id"] != "main":
            raise CicdError("invocation_mismatch")
        return run, mine[0]

    def completion(
        self, reservation: object, node: str
    ) -> dict[str, object] | None:
        """Accept only the exact next attempt after the reserving step."""
        item = _mapping(reservation)
        run = self._run(str(item["run_id"]))
        attempt = cast(int, item["after_attempt"]) + 1
        rows = [
            row
            for row in cast(list[dict[str, object]], run["invocations"])
            if row["plane_id"] == "main" and row["attempt"] == attempt
        ]
        if len(rows) != 1 or rows[0]["node"] != node:
            return None
        done = [
            row
            for row in cast(list[dict[str, object]], run["completions"])
            if row["invocation_id"] == rows[0]["id"]
        ]
        if len(done) != 1:
            return None
        row = done[0]
        if (
            row["transport"] != "completed"
            or row["classification"] != "semantic"
            or row["outcome"] not in ROLE_OUTCOMES[node]
            or row["effective_writer"] != "stopped"
            or row["node"] != node
        ):
            return None
        return {"invocation_id": rows[0]["id"], "outcome": row["outcome"]}

    def writers_stopped(
        self,
        run: dict[str, object],
        mine: dict[str, object],
        state: dict[str, object] | None,
    ) -> bool:
        runs = {str(run["id"])}
        if state is not None:
            for value in (state["diagnosis"], state["repair"], *_batches(state)):
                if isinstance(value, dict) and "run_id" in value:
                    runs.add(str(value["run_id"]))
        for run_id in runs:
            view = run if run_id == run["id"] else self._run(run_id)
            for row in cast(list[dict[str, object]], view["invocations"]):
                if row["id"] != mine["id"] and row["writer_state"] != "stopped":
                    return False
        return True

    # Shared helpers.
    def active(self) -> dict[str, object] | None:
        rows = [row for row in self.home.intake.tasks() if row["status"] in ACTIVE]
        if len(rows) > 1:
            raise CicdError("invalid_intake")
        return rows[0] if rows else None

    def event(self, task: dict[str, object]) -> tuple[str, dict[str, object]]:
        payload = _json(cast(bytes, task["request_bytes"]), "invalid_intake_payload")
        event_id = str(payload["event_id"])
        if event_from_key(str(task["intake_key"])) != event_id:
            raise CicdError("invalid_intake")
        directory = self.home.event_dir(event_id)
        raw = read_bounded(directory / "event.json", MAX_EVENT, "event_changed")
        if _sha(raw) != payload["event_sha256"]:
            raise CicdError("event_changed")
        if payload["log_sha256"] is not None:
            log = read_bounded(directory / "log.txt", MAX_LOG, "event_changed")
            if _sha(log) != payload["log_sha256"]:
                raise CicdError("event_changed")
        event = validate_event(raw, str(self.home.config["repository"]))
        if event_identity(event) != event_id:
            raise CicdError("event_changed")
        return event_id, event

    def transition(self, task: dict[str, object], status: str) -> None:
        if task["status"] != status:
            self.home.intake.transition(
                str(task["task_id"]),
                version=cast(int, task["version"]),
                claim=cast(dict[str, object], task["claim"]),
                status=status,
            )

    def source_digest(self) -> str:
        return digest(snapshot(self.home.target))

    def state(
        self, event_id: str, event: dict[str, object]
    ) -> dict[str, object] | None:
        """Load the event record with the shapes and copies steps rely on.

        The frozen HEAD and config digest are copies; they must equal the
        verified event and the frozen configuration, never stand on their own.
        """
        state = self.home.load_state(event_id)
        if state is None:
            return None
        code = "invalid_state"
        if set(state) != set(_new_state(event_id)) | {"version"}:
            raise CicdError(code)
        if state["freeze"] is not None:
            names = {"config_sha256", "head_sha", "source_before_sha256"}
            freeze = _fields(state["freeze"], names, code)
            if (
                freeze["head_sha"] != event["head_sha"]
                or freeze["config_sha256"] != self.home.config_sha256
            ):
                raise CicdError("freeze_mismatch")
        if state["disposition"] not in (None, "no_action"):
            raise CicdError(code)
        if state["settlement"] is not None:
            _fields(state["settlement"], {"status", "request_id"}, code)
        if not isinstance(state["applied"], list) or not isinstance(
            state["batches"], list
        ):
            raise CicdError(code)
        for request_id in cast(list[object], state["applied"]):
            identity(request_id)
        if state["report_sha256"] is not None:
            sha256_text(state["report_sha256"], code)
        return state

    def selected(
        self, event_id: str, event: dict[str, object], state: dict[str, object]
    ) -> dict[str, object] | None:
        """The terminal disposition its actual origin selects, or None.

        Origins: an accepted cancel, an accepted close of the exact held report,
        or a NO_ACTION input/diagnosis. A saved settlement must equal this.
        """
        cancel = self.home.pending(state, "cancel")
        close = self.home.pending(state, "close")
        if cancel is not None:
            selected = {"status": "cancelled", "request_id": cancel["request_id"]}
        elif close is not None:
            status = self.held_status(state)
            selected = {"status": status, "request_id": close["request_id"]}
        elif state["disposition"] == "no_action":
            self.no_action_origin(event_id, event, state)
            selected = {"status": "no_action", "request_id": None}
        else:
            selected = None
        if state["settlement"] is not None and state["settlement"] != selected:
            raise CicdError("settlement_mismatch")
        return selected

    def held_status(self, state: dict[str, object]) -> str:
        """The held status a close settles.

        Settlement then requires the rebuilt report to equal, byte for byte,
        the published held report whose digest the hold recorded.
        """
        hold = _mapping(state["hold"], "event_not_held")
        status = hold["status"]
        if status in {"no_action", "cancelled"} or state["report_sha256"] is None:
            raise CicdError("settlement_mismatch")
        return str(status)

    def no_action_origin(
        self, event_id: str, event: dict[str, object], state: dict[str, object]
    ) -> None:
        """NO_ACTION needs a no-action CI conclusion or an accepted diagnosis."""
        code = "settlement_mismatch"
        if state["hold"] is not None:
            raise CicdError(code)
        if event["conclusion"] in NO_ACTION:
            if state["diagnosis"] is not None:
                raise CicdError(code)
            return
        diagnosis = _mapping(state["diagnosis"], code)
        done = self.completion(diagnosis, "diagnose")
        if (
            diagnosis["guard"] != "NO_ACTION"
            or done is None
            or done["outcome"] != "NO_ACTION"
            or done["invocation_id"] != diagnosis["invocation_id"]
        ):
            raise CicdError(code)
        path = self.home.verify_ref(diagnosis["artifact"], code)
        artifact = validate_diagnosis(
            _json(read_bounded(path, MAX_EVENT, code), code),
            event_id,
            str(event["head_sha"]),
        )
        if artifact["classification"] != "NO_ACTION":
            raise CicdError(code)

    def source_basis(
        self, state: dict[str, object], expected: object
    ) -> str | None:
        """Hold reason unless the frozen HEAD and expected source still hold.

        Snapshots exclude `.git`, so a same-tree revision move is caught only
        by comparing HEAD. `expected` None checks the revision alone.
        """
        freeze = _mapping(state["freeze"])
        with self.home.lock():
            try:
                head = git_head(self.home.target)
            except CicdError as exc:
                return exc.code
            if head != freeze["head_sha"]:
                return "head_changed"
            if expected is not None and self.source_digest() != expected:
                return "source_drift"
        return None

    def hold(
        self, state: dict[str, object], reason: str, status: str
    ) -> dict[str, object]:
        return self.home.update(state, hold={"reason": reason, "status": status})

    def first_hold(
        self, state: dict[str, object], reason: str, status: str
    ) -> dict[str, object]:
        """Keep an earlier hold's report status; only record a new cause once."""
        return state if state["hold"] is not None else self.hold(state, reason, status)

    def reserve_role(
        self,
        state: dict[str, object],
        event_id: str,
        role: str,
        directory: str,
        mine: dict[str, object],
        **inputs: object,
    ) -> dict[str, object]:
        """Write the pinned role input and pointer; return its reservation."""
        current = self.home.load_state(event_id) or state
        if self.home.pending(current, "cancel") is not None:
            # A domain cancel is a stage-boundary request: no role starts after it.
            raise CicdError("operator_decision_pending")
        event_dir = self.home.event_dir(event_id)
        root = event_dir / directory
        names = {}
        for kind, target in (("prompts", "prompt.md"), ("skills", "skill.md")):
            source = self.controls / kind / (role + ".md")
            raw = read_bounded(source, 65536, "missing_control")
            write_exact(root / target, raw, "reservation_conflict")
            names[kind] = self.home.file_ref(root / target)
        freeze = _mapping(state["freeze"])
        log = event_dir / "log.txt"
        output = (root / "result.json").relative_to(self.home.workspace).as_posix()
        document = {
            "format": ROLE_INPUT,
            "role": role,
            "event_id": event_id,
            "head_sha": freeze["head_sha"],
            "config_sha256": self.home.config_sha256,
            "target_root": str(self.home.target),
            "repair_paths": self.home.config["repair_paths"],
            "protected_paths": self.home.config["protected_paths"],
            "event_ref": self.home.file_ref(event_dir / "event.json"),
            "log_ref": self.home.file_ref(log) if log.exists() else None,
            "prompt_ref": names["prompts"],
            "skill_ref": names["skills"],
            "output": output,
            "outcomes": list(ROLE_OUTCOMES[role]),
            "source_before_sha256": freeze["source_before_sha256"],
            "diagnosis_ref": inputs.get("diagnosis_ref"),
            "allowed_paths": inputs.get("allowed_paths", []),
            "patch_ref": inputs.get("patch_ref"),
            "checks_ref": inputs.get("checks_ref"),
        }
        raw_input = canonical(document) + b"\n"
        write_exact(root / "input.json", raw_input, "reservation_conflict")
        pointer = {
            "format": ACTIVE_ROLE,
            "event_id": event_id,
            "role": role,
            "input": (root / "input.json").relative_to(self.home.workspace).as_posix(),
        }
        write_replace(self.home.workspace / "active.json", canonical(pointer) + b"\n")
        return {
            "run_id": mine["run_id"],
            "after_attempt": mine["attempt"],
            "directory": directory,
            "invocation_id": None,
            "outcome": None,
            "artifact": None,
            "guard": None,
        }

    def artifact(self, event_id: str, reservation: dict[str, object]) -> object:
        directory = self.home.event_dir(event_id) / str(reservation["directory"])
        raw = read_bounded(directory / "result.json", MAX_EVENT, "missing_artifact")
        return _json(raw, "invalid_artifact")

    def check_refs(self, refs: object) -> None:
        for item in cast(list[object], refs):
            self.home.verify_ref(item, "invalid_evidence_ref")

    def report(
        self, event_id: str, state: dict[str, object], status: str
    ) -> tuple[bytes, bytes]:
        """Deterministic `report.json` and `report.md` bytes from saved facts."""

        def get(value: object, name: str) -> object:
            return value.get(name) if isinstance(value, dict) else None

        repair = state["repair"]
        patch = get(repair, "patch")
        batches = _batches(state)
        last = batches[-1] if batches else None
        hold = state["hold"]
        _, event = self.event(self.home.task(event_id))
        report = {
            "format": REPORT,
            "event_id": event_id,
            "head_sha": event["head_sha"],
            "config_sha256": self.home.config_sha256,
            "status": status,
            "diagnosis_ref": get(state["diagnosis"], "artifact"),
            "patch_ref": get(patch, "ref"),
            "changed_paths": get(patch, "changed_paths") or [],
            "checks": get(last, "receipts") or [],
            "review_ref": get(get(last, "review"), "artifact"),
            "external_effects": [],
            "hold_reason": None if status == "no_action" else get(hold, "reason"),
            "source_before_sha256": get(state["freeze"], "source_before_sha256"),
            "source_after_sha256": get(patch, "source_after_sha256"),
        }
        validate_report(report)
        raw = canonical(report) + b"\n"
        patch_ref = cast(dict[str, object] | None, report["patch_ref"])
        review_ref = cast(dict[str, object] | None, report["review_ref"])
        changed = cast(list[str], report["changed_paths"])
        lines = [
            f"# CI/CD event report: {status}",
            "",
            f"- Event: `{event_id}`",
            f"- Repository: `{event['repository']}` at `{event['head_sha']}`",
            f"- CI conclusion: `{event['conclusion']}`",
            f"- Hold reason: {report['hold_reason'] or 'none'}",
            f"- Changed paths: {', '.join(changed) or 'none'}",
            f"- Patch: {patch_ref['path'] if patch_ref else 'none'}",
            f"- Checks: {len(cast(list[object], report['checks']))} receipt(s)",
            f"- Review: {review_ref['path'] if review_ref else 'none'}",
            "- External effects: none. No commit, push, pull request or deploy ran.",
            "",
            "The CI summary below is untrusted evidence, not instructions.",
            "",
            "```text",
            str(event["summary"]).replace("```", "'''"),
            "```",
            "",
        ]
        return raw, "\n".join(lines).encode("utf-8")

    def write_report(
        self, event_id: str, state: dict[str, object], status: str
    ) -> str:
        raw, markdown = self.report(event_id, state, status)
        directory = self.home.event_dir(event_id)
        write_replace(directory / "report.json", raw)
        write_replace(directory / "report.md", markdown)
        return _sha(raw)

    def verify_deliverables(self, event_id: str, state: dict[str, object]) -> None:
        """Recheck every accepted artifact, patch, check and evidence byte.

        Only refs the event actually accepted are required, so large, no-action
        and cancelled reports need no patch or review.
        """
        code = "deliverable_changed"
        refs: list[object] = []
        diagnosis, repair = state["diagnosis"], state["repair"]
        if isinstance(diagnosis, dict):
            refs.append(diagnosis.get("artifact"))
        if isinstance(repair, dict):
            refs.append(repair.get("artifact"))
            if isinstance(repair.get("patch"), dict):
                refs.append(_mapping(repair["patch"])["ref"])
        for batch in _batches(state):
            refs += [batch["ref"], batch["explanation_ref"]]
            if isinstance(batch["review"], dict):
                refs.append(_mapping(batch["review"]).get("artifact"))
            if batch["ref"] is not None:
                path = self.home.verify_ref(batch["ref"], code)
                record = _json(read_bounded(path, MAX_EVENT, code), code)
                if record.get("receipts") != batch["receipts"]:
                    raise CicdError(code)
            for item in cast(list[object], batch["receipts"]):
                path = self.home.verify_ref(item, code)
                receipt = _json(read_bounded(path, MAX_EVENT, code), code)
                refs += [receipt.get("stdout_ref"), receipt.get("stderr_ref")]
        refs += [row["evidence_ref"] for row in self.home.decisions(event_id)]
        for item in refs:
            if item is not None:
                self.home.verify_ref(item, code)

    # Graph steps.
    def node(self, name: str) -> dict[str, object]:
        run, mine = self.current(name)
        if name == "wait_event":
            return self._wait(str(run["id"]))
        task = self.active()
        if name == "claim":
            if task is not None or not self.writers_stopped(run, mine, None):
                raise CicdError("claim_not_ready")
            return self._claim()
        if name == "reconcile":
            if task is None:
                return _result("READY")
            return self._reconcile(task, run, mine)
        if task is None:
            raise CicdError("active_event_missing")
        event_id, event = self.event(task)
        state = self.state(event_id, event)
        if name == "freeze":
            return self._freeze(task, event_id, event, state, mine)
        if state is None:
            raise CicdError("invalid_state")
        if name == "settle":
            return self._settle(task, event_id, event, state, run, mine)
        if name == "hold":
            return self._hold(task, event_id, state)
        if self.home.pending(state, "cancel") is not None:
            # Only reconcile consumes an operator stop; pause there first.
            raise CicdError("operator_decision_pending")
        if not self.writers_stopped(run, mine, state):
            # A configured check or role may still live; never build on it.
            raise CicdError("writer_not_stopped")
        if name == "guard_diagnosis":
            return self._guard_diagnosis(task, event_id, event, state, mine)
        if name == "guard_patch":
            return self._guard_patch(task, event_id, state)
        if name == "checks":
            return self._checks(task, event_id, state, mine)
        if name == "guard_publication":
            return self._guard_publication(event_id, state)
        raise CicdError("unknown_node")

    def _claim(self) -> dict[str, object]:
        def build(task: dict[str, object]) -> dict[str, object]:
            code = "invalid_intake_payload"
            payload = _json(cast(bytes, task["request_bytes"]), code)
            return {
                "task_id": task["task_id"],
                "request_sha256": task["request_sha256"],
                "event_id": payload["event_id"],
            }

        with self.home.lock():
            claim = self.home.intake.claim(build)
        if claim is None:
            return _result("EMPTY")
        event_id = str(claim["event_id"])
        if self.home.load_state(event_id) is None:
            self.home.save_state(_new_state(event_id), None)
        return _result("CLAIMED", event_id)

    def _wait(self, run_id: str) -> dict[str, object]:
        """Finite idle wait. No agent runs; committed intake or a control wakes it."""
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            if any(row["status"] == "queued" for row in self.home.intake.tasks()):
                return _result("WAKE", "event_queued")
            run = self._run(run_id)
            # Pause lets an active command finish. Its receipt may already be
            # applied, so the durable gate/plane state must also wake this wait.
            if run["run_gate"] is not None or _mapping(
                _mapping(run["planes"])["main"]
            )["status"] == "paused":
                return _result("WAKE", "control_applied")
            controls = cast(list[object], run.get("controls", []))
            if run.get("cancel_requested") or any(
                _mapping(row).get("status") == "queued" for row in controls
            ):
                return _result("WAKE", "control_queued")
            if time.monotonic() >= deadline:
                return _result("IDLE")
            time.sleep(WAIT_POLL)

    def _reconcile(
        self, task: dict[str, object], run: dict[str, object], mine: dict[str, object]
    ) -> dict[str, object]:
        event_id, event = self.event(task)
        state = self.state(event_id, event)
        if state is None:
            return _result("ACTIVE_FREEZE")
        if not self.writers_stopped(run, mine, state):
            self.first_hold(state, "writer_not_stopped", "uncertain")
            return _result("HOLD", "writer_not_stopped")
        try:
            selected = self.selected(event_id, event, state)
        except CicdError as exc:
            if exc.code != "decision_projection_missing":
                raise
            # An accepted request lost its record: hold, start nothing, and wait
            # for an exact replay of that same request to republish it.
            self.first_hold(state, exc.code, "uncertain")
            return _result("HOLD", exc.code)
        if selected is not None:
            return _result("ACTIVE_SETTLE")
        retry = self.home.pending(state, "retry-checks")
        if retry is not None:
            patch = _mapping(state["repair"] or {}).get("patch")
            batches = _batches(state)
            if (
                isinstance(patch, dict)
                and batches
                and batches[-1]["status"] != "reserved"
                and self.source_basis(state, patch["source_after_sha256"]) is None
            ):
                return _result("ACTIVE_CHECKS")
            # Refused retry is consumed; the earlier hold and evidence remain.
            state = self.home.update(
                state, applied=_applied(state, retry["request_id"])
            )
            self.first_hold(state, "retry_checks_refused", "uncertain")
            return _result("HOLD", "retry_checks_refused")
        if state["hold"] is not None:
            return _result("HOLD", str(_mapping(state["hold"])["reason"]))
        if state["freeze"] is None:
            return _result("ACTIVE_FREEZE")
        diagnosis = state["diagnosis"]
        if isinstance(diagnosis, dict) and diagnosis["guard"] is None:
            if self.completion(diagnosis, "diagnose") is not None:
                return _result("ACTIVE_DIAGNOSIS_GUARD")
            # A reserved attempt is spent even if it never launched.
            self.hold(state, "diagnosis_interrupted", "uncertain")
            return _result("HOLD", "diagnosis_interrupted")
        repair = state["repair"]
        if isinstance(repair, dict) and repair["guard"] is None:
            done = self.completion(repair, "repair")
            if done is not None and done["outcome"] == "PATCH_READY":
                return _result("ACTIVE_PATCH_GUARD")
            reason = "repair_blocked" if done else "repair_interrupted"
            self.hold(state, reason, "held_repair" if done else "uncertain")
            return _result("HOLD", reason)
        batches = _batches(state)
        if isinstance(repair, dict) and repair["guard"] == "SCOPED" and not batches:
            return _result("ACTIVE_CHECKS")
        if batches:
            last = batches[-1]
            review = last["review"]
            if last["status"] == "reserved":
                self.hold(state, "checks_interrupted", "uncertain")
                return _result("HOLD", "checks_interrupted")
            if last["status"] == "PASS" and review is None:
                patch = _mapping(_mapping(state["repair"])["patch"])
                basis = self.source_basis(state, patch["source_after_sha256"])
                if basis is not None:
                    self.hold(state, basis, "uncertain")
                    return _result("HOLD", basis)
                return self._reserve_review(state, event_id, mine, "ACTIVE_REVIEW")
            if last["status"] == "PASS" and _mapping(review)["guard"] is None:
                done = self.completion(review, "review")
                if done is not None and done["outcome"] == "ACCEPT":
                    return _result("ACTIVE_PUBLICATION")
                reason = "review_rejected" if done else "review_interrupted"
                self.hold(state, reason, "held_repair" if done else "uncertain")
                return _result("HOLD", reason)
        self.hold(state, "unexpected_state", "uncertain")
        return _result("HOLD", "unexpected_state")

    def _freeze(
        self,
        task: dict[str, object],
        event_id: str,
        event: dict[str, object],
        state: dict[str, object] | None,
        mine: dict[str, object],
    ) -> dict[str, object]:
        if state is None:
            state = self.home.save_state(_new_state(event_id), None)
        if state["freeze"] is not None or state["diagnosis"] is not None:
            raise CicdError("freeze_replay_conflict")
        if self.home.pending(state, "cancel") is not None:
            raise CicdError("operator_decision_pending")
        freeze: dict[str, object] = {
            "config_sha256": self.home.config_sha256,
            "head_sha": event["head_sha"],
            "source_before_sha256": None,
        }
        if event["conclusion"] in NO_ACTION:
            self.home.update(state, freeze=freeze, disposition="no_action")
            return _result("NO_ACTION", str(event["conclusion"]))
        directory = self.home.event_dir(event_id)
        try:
            with self.home.lock():
                if git_head(self.home.target) != event["head_sha"]:
                    raise CicdError("head_mismatch")
                before = snapshot(self.home.target)
                copy_before(
                    self.home.target, before, self.home.config, directory / "before"
                )
            write_exact(
                directory / "before.json",
                canonical(before) + b"\n",
                "freeze_replay_conflict",
            )
        except CicdError as exc:
            if exc.code not in FREEZE_HOLDS:
                raise
            state = self.home.update(state, freeze=freeze)
            self.hold(state, exc.code, "held_external")
            return _result("HOLD", exc.code)
        freeze["source_before_sha256"] = digest(before)
        state = self.home.update(state, freeze=freeze)
        reservation = self.reserve_role(state, event_id, "diagnose", "diagnose", mine)
        # The reservation is durable before the edge to the role node exists.
        self.home.update(state, diagnosis=reservation)
        self.transition(task, "running")
        return _result("READY")

    def _guard_diagnosis(
        self,
        task: dict[str, object],
        event_id: str,
        event: dict[str, object],
        state: dict[str, object],
        mine: dict[str, object],
    ) -> dict[str, object]:
        diagnosis = _mapping(state["diagnosis"])
        if diagnosis["guard"] is not None or state["repair"] is not None:
            raise CicdError("diagnosis_already_guarded")
        done = self.completion(diagnosis, "diagnose")
        if done is None:
            state = self.home.update(state, diagnosis=diagnosis | {"guard": "HOLD"})
            self.hold(state, "diagnosis_unaccepted", "uncertain")
            return _result("HOLD_UNPROVEN", "diagnosis_unaccepted")
        accepted = diagnosis | done
        freeze = _mapping(state["freeze"])
        # Every classification, including NO_ACTION and holds, needs the frozen
        # source and revision: a diagnosis that edited the target cannot settle.
        drift = self.source_basis(state, freeze["source_before_sha256"])
        if drift is not None:
            state = self.home.update(
                state, diagnosis=accepted | {"guard": "HOLD_UNPROVEN"}
            )
            self.hold(state, drift, "uncertain")
            return _result("HOLD_UNPROVEN", drift)
        try:
            artifact = validate_diagnosis(
                self.artifact(event_id, diagnosis), event_id, str(event["head_sha"])
            )
            if artifact["classification"] != done["outcome"]:
                raise CicdError("diagnosis_outcome_mismatch")
            self.check_refs(artifact["evidence_refs"])
        except CicdError as exc:
            state = self.home.update(
                state, diagnosis=accepted | {"guard": "HOLD_UNPROVEN"}
            )
            self.hold(state, exc.code, "held_unreproduced")
            return _result("HOLD_UNPROVEN", exc.code)
        result = self.home.event_dir(event_id) / str(diagnosis["directory"])
        accepted["artifact"] = self.home.file_ref(result / "result.json")
        outcome = str(done["outcome"])
        if outcome == "NO_ACTION":
            self.home.update(
                state,
                diagnosis=accepted | {"guard": "NO_ACTION"},
                disposition="no_action",
            )
            return _result("NO_ACTION")
        if outcome in DIAGNOSIS_HOLDS:
            guard, reason, status = DIAGNOSIS_HOLDS[outcome]
            state = self.home.update(state, diagnosis=accepted | {"guard": guard})
            self.hold(state, reason, status)
            return _result(guard, reason)
        proposed = [str(item) for item in cast(list[str], artifact["proposed_paths"])]
        if len(proposed) > SMALL_FILES or not all(
            repairable(path, self.home.config) for path in proposed
        ):
            large = accepted | {"guard": "HOLD_LARGE"}
            state = self.home.update(state, diagnosis=large)
            self.hold(state, "proposal_exceeds_small_repair", "held_large")
            return _result("HOLD_LARGE", "proposal_exceeds_small_repair")
        state = self.home.update(
            state, diagnosis=accepted | {"guard": "REPAIR_ALLOWED"}
        )
        reservation = self.reserve_role(
            state,
            event_id,
            "repair",
            "repair",
            mine,
            diagnosis_ref=accepted["artifact"],
            allowed_paths=proposed,
        )
        repair = reservation | {"allowed_paths": proposed, "patch": None}
        # The single repair attempt is durable before the edge to `repair` exists.
        self.home.update(state, repair=repair)
        self.transition(task, "running")
        return _result("REPAIR_ALLOWED")

    def _guard_patch(
        self, task: dict[str, object], event_id: str, state: dict[str, object]
    ) -> dict[str, object]:
        repair = _mapping(state["repair"])
        if repair["guard"] is not None:
            raise CicdError("patch_already_guarded")
        done = self.completion(repair, "repair")
        if done is None or done["outcome"] != "PATCH_READY":
            state = self.home.update(state, repair=repair | {"guard": "HOLD"})
            self.hold(state, "repair_unaccepted", "uncertain")
            return _result("HOLD", "repair_unaccepted")
        directory = self.home.event_dir(event_id)
        freeze = _mapping(state["freeze"])
        raw = read_bounded(directory / "before.json", 64 * 1048576, "invalid_state")
        before = cast(dict[str, dict[str, object]], _json(raw, "invalid_state"))
        with self.home.lock():
            after = snapshot(self.home.target)
        result = scope(
            self.home.config,
            cast(list[str], repair["allowed_paths"]),
            before,
            after,
            directory / "before",
            self.home.target,
        )
        evidence = {name: result[name] for name in ("changed_paths", "lines", "reason")}
        evidence["source_after_sha256"] = result["source_after_sha256"]
        write_replace(directory / "repair" / "scope.json", canonical(evidence) + b"\n")
        patch_ref = None
        if result["patch"] is not None:
            # Preserved even when the guard holds; never applied or published.
            write_exact(
                directory / "patch.diff", cast(bytes, result["patch"]), "patch_conflict"
            )
            patch_ref = self.home.file_ref(directory / "patch.diff")
        reason = cast(str | None, result["reason"])
        if reason is None:
            # The patch evidence above is kept; acceptance needs the frozen HEAD.
            reason = self.source_basis(state, None)
        accepted = repair | done
        if reason is None:
            try:
                artifact = validate_repair(
                    self.artifact(event_id, repair),
                    event_id,
                    self.home.config_sha256,
                    str(freeze["source_before_sha256"]),
                )
                claimed = sorted(cast(list[str], artifact["changed_paths"]))
                if claimed != result["changed_paths"]:
                    raise CicdError("claimed_paths_mismatch")
                if artifact["source_after_sha256"] != result["source_after_sha256"]:
                    raise CicdError("source_after_mismatch")
                self.check_refs(artifact["evidence_refs"])
                path = directory / str(repair["directory"]) / "result.json"
                accepted["artifact"] = self.home.file_ref(path)
            except CicdError as exc:
                reason = exc.code
        patch = None
        if patch_ref is not None:
            patch = {
                "ref": patch_ref,
                "changed_paths": result["changed_paths"],
                "lines": result["lines"],
                "source_after_sha256": result["source_after_sha256"],
            }
        if reason is not None or patch is None:
            reason = reason or "patch_unavailable"
            state = self.home.update(
                state, repair=accepted | {"guard": "HOLD", "patch": patch}
            )
            self.hold(state, reason, "held_repair")
            return _result("HOLD", reason)
        self.home.update(state, repair=accepted | {"guard": "SCOPED", "patch": patch})
        self.transition(task, "running")
        return _result("SCOPED")

    def _checks(
        self,
        task: dict[str, object],
        event_id: str,
        state: dict[str, object],
        mine: dict[str, object],
    ) -> dict[str, object]:
        repair = _mapping(state["repair"])
        patch = _mapping(repair["patch"], "patch_missing")
        batches = _batches(state)
        retry = self.home.pending(state, "retry-checks")
        if repair["guard"] != "SCOPED" or (retry is None and batches):
            raise CicdError("checks_not_reserved")
        if retry is not None and (not batches or batches[-1]["status"] == "reserved"):
            raise CicdError("checks_not_reserved")
        number = len(batches) + 1
        batch = {
            "batch": number,
            "run_id": mine["run_id"],
            "after_attempt": mine["attempt"],
            "status": "reserved",
            "explanation_ref": None if retry is None else retry["evidence_ref"],
            "ref": None,
            "receipts": [],
            "review": None,
        }
        applied = _applied(state, None if retry is None else retry["request_id"])
        # The batch is durable before any configured command executes.
        state = self.home.update(
            state, batches=[*batches, batch], applied=applied, hold=None
        )
        self.transition(task, "running")
        directory = self.home.event_dir(event_id) / "checks" / str(number)
        expected = str(patch["source_after_sha256"])
        basis = self.source_basis(state, expected)
        refs: list[dict[str, object]]
        if basis is not None:
            status, refs = "UNCERTAIN", []
        else:
            status, refs = run_batch(
                self.home.config,
                self.home.target,
                self.home.workspace,
                directory,
                event_id,
                expected,
                self.source_digest,
            )
        record = {
            "format": BATCH,
            "event_id": event_id,
            "batch": number,
            "source_sha256": expected,
            "status": status,
            "receipts": refs,
            "explanation_ref": batch["explanation_ref"],
        }
        if status == "PASS":
            # Checks run repository code; the revision must not have moved.
            basis = self.source_basis(state, expected)
            if basis is not None:
                status = record["status"] = "UNCERTAIN"
        raw = canonical(record) + b"\n"
        write_exact(directory / "batch.json", raw, "check_replay_conflict")
        batch = batch | {
            "status": status,
            "receipts": refs,
            "ref": self.home.file_ref(directory / "batch.json"),
        }
        state = self.home.update(state, batches=[*batches, batch])
        if status == "PASS":
            self._reserve_review(state, event_id, mine, "PASS")
            return _result("PASS")
        if status == "FAIL":
            self.hold(state, "checks_failed", "held_repair")
            return _result("FAIL")
        reason = basis or "checks_uncertain"
        self.hold(state, reason, "uncertain")
        return _result("UNCERTAIN", reason)

    def _reserve_review(
        self,
        state: dict[str, object],
        event_id: str,
        mine: dict[str, object],
        outcome: str,
    ) -> dict[str, object]:
        batches = _batches(state)
        last = batches[-1]
        if last["status"] != "PASS" or last["review"] is not None:
            raise CicdError("review_already_reserved")
        repair = _mapping(state["repair"])
        reservation = self.reserve_role(
            state,
            event_id,
            "review",
            "review-" + str(last["batch"]),
            mine,
            diagnosis_ref=_mapping(state["diagnosis"])["artifact"],
            allowed_paths=repair["allowed_paths"],
            patch_ref=_mapping(repair["patch"])["ref"],
            checks_ref=last["ref"],
        )
        # One fresh independent review per passing batch, saved before dispatch.
        last = last | {"review": reservation}
        self.home.update(state, batches=[*batches[:-1], last])
        return _result(outcome)

    def _guard_publication(
        self, event_id: str, state: dict[str, object]
    ) -> dict[str, object]:
        batches = _batches(state)
        last = batches[-1]
        review = _mapping(last["review"])
        if review["guard"] is not None:
            raise CicdError("review_already_guarded")
        patch = _mapping(_mapping(state["repair"])["patch"])
        done = self.completion(review, "review")
        reason = None
        if done is None or done["outcome"] != "ACCEPT":
            reason = "review_unaccepted"
        else:
            try:
                patch_path = self.home.verify_ref(patch["ref"], "stale_patch")
                self.home.verify_ref(last["ref"], "stale_checks")
                artifact = validate_review(
                    self.artifact(event_id, review),
                    event_id,
                    self.home.config_sha256,
                    _sha(patch_path.read_bytes()),
                    cast(dict[str, object], last["ref"]),
                )
                for finding in cast(list[dict[str, object]], artifact["findings"]):
                    self.check_refs(finding["evidence_refs"])
                basis = self.source_basis(state, patch["source_after_sha256"])
                if basis is not None:
                    raise CicdError(basis)
            except CicdError as exc:
                reason = exc.code
        accepted = review | (done or {})
        if reason is not None:
            last = last | {"review": accepted | {"guard": "STALE"}}
            state = self.home.update(state, batches=[*batches[:-1], last])
            self.hold(state, reason, "uncertain")
            return _result("STALE", reason)
        path = self.home.event_dir(event_id) / str(review["directory"])
        accepted["artifact"] = self.home.file_ref(path / "result.json")
        last = last | {"review": accepted | {"guard": "HUMAN_REQUIRED"}}
        state = self.home.update(state, batches=[*batches[:-1], last])
        # Hold mode: a checked local patch never authorizes an external effect.
        self.hold(state, "publication_requires_human", "checked_patch")
        return _result("HUMAN_REQUIRED")

    def _hold(
        self, task: dict[str, object], event_id: str, state: dict[str, object]
    ) -> dict[str, object]:
        if state["hold"] is None:
            # Native BLOCKED/REJECT routes here directly; bind it to its reservation.
            reason, status = "unexplained_hold", "uncertain"
            repair = state["repair"]
            batches = _batches(state)
            review = batches[-1]["review"] if batches else None
            if isinstance(review, dict) and review["guard"] is None:
                done = self.completion(review, "review")
                if done is not None and done["outcome"] != "ACCEPT":
                    reason = "review_" + str(done["outcome"]).lower()
                    status = "held_repair"
                    last = batches[-1] | {"review": review | done | {"guard": "HOLD"}}
                    state = self.home.update(state, batches=[*batches[:-1], last])
            elif isinstance(repair, dict) and repair["guard"] is None:
                done = self.completion(repair, "repair")
                if done is not None and done["outcome"] == "BLOCKED":
                    reason, status = "repair_blocked", "held_repair"
                    repair = repair | done | {"guard": "HOLD"}
                    state = self.home.update(state, repair=repair)
            state = self.hold(state, reason, status)
        hold = _mapping(state["hold"])
        published = self.write_report(event_id, state, str(hold["status"]))
        if state["report_sha256"] != published:
            # Close later settles exactly these bytes, never a silent rewrite.
            self.home.update(state, report_sha256=published)
        self.transition(task, "held")
        return _result("WAIT_OPERATOR", str(hold["reason"]))

    def _settle(
        self,
        task: dict[str, object],
        event_id: str,
        event: dict[str, object],
        state: dict[str, object],
        run: dict[str, object],
        mine: dict[str, object],
    ) -> dict[str, object]:
        if not self.writers_stopped(run, mine, state):
            raise CicdError("writer_not_stopped")
        # Recomputed on every replay; a saved settlement is only a checked copy.
        settlement = self.selected(event_id, event, state)
        if settlement is None:
            raise CicdError("settlement_not_selected")
        if state["settlement"] is None:
            state = self.home.update(state, settlement=settlement)
        status = str(settlement["status"])
        closing = settlement["request_id"] is not None and status != "cancelled"
        terminal = {
            "cancelled": "cancelled",
            "no_action": "succeeded",
            "checked_patch": "succeeded",
        }.get(status, "failed")
        name = "settle:" + str(settlement["request_id"])
        request = uuid.uuid5(uuid.UUID(str(task["task_id"])), name).hex
        # Lock order is target/home -> event state -> intake transaction. Every
        # basis below is re-read under both locks, and the terminal CAS runs
        # before either is released, so no decision, state or report change can
        # land between verification and release of the active slot.
        with self.home.lock(), self.home._state_lock():
            if self.home.load_state(event_id) != state:
                raise CicdError("stale_event_state")
            if self.selected(event_id, event, state) != settlement:
                raise CicdError("settlement_mismatch")
            self.verify_deliverables(event_id, state)
            raw, _ = self.report(event_id, state, status)
            report_sha256 = _sha(raw)
            path = self.home.event_dir(event_id) / "report.json"
            current = (
                _sha(read_bounded(path, MAX_EVENT, "report_changed"))
                if path.exists()
                else None
            )
            recorded = state["report_sha256"]
            allowed = {recorded, report_sha256}
            if (closing and report_sha256 != recorded) or current not in allowed:
                # Close settles the exact held report; a changed one refuses.
                raise CicdError("report_changed")
            if current != report_sha256:
                self.write_report(event_id, state, status)
            if recorded != report_sha256:
                state = self.home._save_state_locked(
                    state | {"report_sha256": report_sha256},
                    cast(int, state["version"]),
                )
            latest = self.home.task(event_id)
            if latest["version"] != task["version"] or latest["claim"] != task["claim"]:
                raise CicdError("stale_task")
            evidence = {
                "status": status,
                "report_sha256": report_sha256,
                "request_id": settlement["request_id"],
            }
            intent = {"task_id": task["task_id"], "decision": "settle"}
            # The report is saved before the terminal CAS releases the active slot.
            self.home.intake.decide(
                request,
                intent | {"evidence": evidence},
                check=lambda _task: None,
                terminal_status=terminal,
                expected_version=cast(int, task["version"]),
                expected_claim=cast(dict[str, object], task["claim"]),
            )
            # A crash before this marker replays the same request and receipt.
            self.home._save_state_locked(
                state | {"applied": _applied(state, settlement["request_id"])},
                cast(int, state["version"]),
            )
        return _result("SETTLED", status)


def step(node: str, controls: Path) -> dict[str, object]:
    """Bundled step entry. Core runs it in the plane workspace `<home>/project/main`."""
    workspace = Path.cwd()
    home = Home(workspace.parent.parent)
    if workspace != home.workspace:
        raise CicdError("invalid_workspace")
    return Runtime(home, controls=controls).node(node)
