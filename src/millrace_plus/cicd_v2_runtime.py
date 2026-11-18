"""CI v2 home, fixed graph derivation and deterministic lifecycle operations.

`derive_graph` turns the bundled `workflows/cicd-v2` template and one captured
`millrace-plus-cicd-config/v2` into the exact graph Core captures at init.
It is pure data: no Core call and no dispatch. Captured derivation includes:

- Amendment A4: the ordered `role_bindings.review` array expands into
  `review_<k>` → `guard_review_<k>` pairs (k = 1..n, with no count cap). Each
  guard's ACCEPT moves to the next pair and the last to `guard_delivery`.
  Reconcile gains matching `REVIEW_READY_<k>`/`REVIEW_RESULT_<k>` outcomes.
- Role nodes use the captured binding of their configured selection.
- `reproduce` and `checks` carry the init-static budgets of spec 7.2: the
  timeout sum over the unique maximum selectable set plus the captured
  bookkeeping. Other nodes keep Core's existing timeout hierarchy, including
  its 1,800-second fallback with `timeout_defaulted`.

V2Home retains captured authority, input and append-only evidence. Runtime
executes one declared deterministic node at a time; Core owns scheduling.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import os
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from . import check_contracts as cc
from . import cicd_v2_records as vr
from .cicd_records import (
    CicdError,
    _validate_event,
    event_from_key,
    event_identity,
    intake_key,
)
from .cicd_source import (
    _git_read,
    canonical_dir,
    filesystem_identity,
    git_head,
    snapshot_policy,
    source_identity,
    target_lock,
    write_exact,
    write_replace,
)
from .cicd_v2_records import (
    CicdV2Error,
    check_node_budget,
    reproduction_node_budget,
    validate_config,
)
from .local_intake import Intake, IntakeError, canonical, strict_json

__all__ = [
    "V2Home",
    "Runtime",
    "prepare",
    "initialize",
    "step",
    "derive_graph",
    "review_nodes",
    "idle_wait",
]


def review_nodes(count: int) -> list[tuple[str, str]]:
    """Ordered `(review_<k>, guard_review_<k>)` node names for `count` reviewers."""
    if type(count) is not int or count < 1:
        raise CicdV2Error("invalid_role_bindings")
    return [(f"review_{k}", f"guard_review_{k}") for k in range(1, count + 1)]


def derive_graph(
    template: Mapping[str, object], config: Mapping[str, object]
) -> dict[str, object]:
    """The exact graph for one captured v2 config; the template is not mutated."""
    checked = validate_config(copy.deepcopy(dict(config)))
    graph = copy.deepcopy(dict(template))
    plane = cast(dict[str, object], cast(dict[str, object], graph["planes"])["main"])
    nodes = cast(dict[str, dict[str, object]], plane["nodes"])
    bindings = cast(dict[str, object], graph["bindings"])
    roles = cast(dict[str, object], checked["role_bindings"])
    reviewers = cast(list[dict[str, object]], roles["review"])
    if "review" not in nodes or "guard_review" not in nodes:
        raise CicdV2Error("invalid_template")
    review, guard = nodes.pop("review"), nodes.pop("guard_review")
    bindings.pop("review", None)
    pairs = review_nodes(len(reviewers))
    for index, (name, guard_name) in enumerate(pairs):
        following = pairs[index + 1][0] if index + 1 < len(pairs) else "guard_delivery"
        node = copy.deepcopy(review)
        invoke = cast(dict[str, object], node["invoke"])
        invoke["binding"] = name
        invoke["config"] = {"args": [name]}
        node["on"] = {
            outcome: {"goto": guard_name}
            for outcome in cast(list[str], node["outcomes"])
        }
        nodes[name] = node
        guard_node = copy.deepcopy(guard)
        guard_invoke = cast(dict[str, object], guard_node["invoke"])
        guard_invoke["config"] = {"script": "scripts/step.py", "args": [guard_name]}
        routes = cast(dict[str, object], guard_node["on"])
        routes["ACCEPT"] = {"goto": following}
        nodes[guard_name] = guard_node
        bindings[name] = copy.deepcopy(reviewers[index]["binding"])
    for role in ("investigate", "repair"):
        bindings[role] = copy.deepcopy(cast(dict[str, object], roles[role])["binding"])
    checks_pass = cast(dict[str, object], nodes["guard_checks"]["on"])
    checks_pass["PASS"] = {"goto": pairs[0][0]}
    reconcile = nodes["reconcile"]
    outcomes = [
        outcome
        for outcome in cast(list[str], reconcile["outcomes"])
        if outcome not in {"REVIEW_READY", "REVIEW_RESULT"}
    ]
    routes = cast(dict[str, object], reconcile["on"])
    routes.pop("REVIEW_READY")
    routes.pop("REVIEW_RESULT")
    for k, (name, guard_name) in enumerate(pairs, start=1):
        outcomes += [f"REVIEW_READY_{k}", f"REVIEW_RESULT_{k}"]
        routes[f"REVIEW_READY_{k}"] = {"goto": name}
        routes[f"REVIEW_RESULT_{k}"] = {"goto": guard_name}
    reconcile["outcomes"] = outcomes
    selections = [("investigate", roles["investigate"]), ("repair", roles["repair"])]
    selections += [(name, reviewers[k]) for k, (name, _) in enumerate(pairs)]
    controls = set(cast(list[str], graph.get("control_files", [])))
    for name, value in selections:
        selection = cast(dict[str, object], value)
        binding = cast(dict[str, object], bindings[name])
        if binding["adapter"] != "command":
            binding["config"] = cast(dict[str, object], binding["config"]) | {
                "profile": cast(dict[str, object], selection["profile_ref"])["path"]
            }
            cast(dict[str, object], nodes[name]["invoke"])["config"] = {
                "prompt": cast(dict[str, object], selection["prompt_ref"])["path"]
            }
        for key in ("profile_ref", "prompt_ref", "skill_ref", "result_schema_ref"):
            if selection[key] is not None:
                controls.add(str(cast(dict[str, object], selection[key])["path"]))
    graph["control_files"] = sorted(controls)
    cast(list[str], reconcile["outcomes"]).append("FREEZE_READY")
    routes["FREEZE_READY"] = {"goto": "freeze"}
    for outcome, target in (
        ("INVESTIGATION_READY", "investigate"),
        ("REPAIR_READY", "repair"),
    ):
        cast(list[str], reconcile["outcomes"]).append(outcome)
        routes[outcome] = {"goto": target}
    for name, budget in (
        ("reproduce", reproduction_node_budget(checked)),
        ("checks", check_node_budget(checked)),
    ):
        invoke = cast(dict[str, object], nodes[name]["invoke"])
        invoke["timeout_seconds"] = budget["effective_seconds"]
    return graph


def idle_wait(
    policy: Mapping[str, object],
    invocation_deadline: float,
    *,
    queued: Callable[[], bool],
    control: Callable[[], str | None],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, object]:
    """Amendment A3 idle wait: WAKE on committed intake or control, else IDLE.

    The window is the wait_event invocation's own absolute deadline (wall
    clock, from the existing timeout hierarchy) minus the captured
    `bookkeeping_seconds`, so IDLE returns before Core's deadline stop. The
    captured `poll_seconds` sets the observation cadence. No model runs, and
    there is no separate window or overall limit.
    """
    entered = clock()
    remaining = invocation_deadline - entered
    margin = float(cast(float, policy["bookkeeping_seconds"]))
    poll = float(cast(float, policy["poll_seconds"]))
    if policy.get("defaulted"):
        margin = min(margin, remaining / 2)
    if remaining <= 0 or poll <= 0 or margin < 0 or margin >= remaining:
        raise CicdV2Error("idle_wait_budget_incompatible")
    end = invocation_deadline - margin
    slept = False
    while True:
        if queued():
            return {"outcome": "WAKE", "reason": "event_queued"}
        reason = control()
        if reason is not None:
            return {"outcome": "WAKE", "reason": reason}
        now = clock()
        if now >= end:
            if not slept:
                raise CicdV2Error("idle_wait_window_exhausted")
            return {"outcome": "IDLE", "reason": None}
        sleep(min(poll, end - now))
        slept = True


SERVICE = "millrace-plus-cicd-service/v2"
EXECUTION = "millrace-plus-local-execution/v1"
ACTIVE = {"claimed", "running", "held"}
TERMINAL = {"succeeded", "failed", "cancelled"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path, *, limit: int | None = None) -> bytes:
    """Read a regular file without traversing links or accepting hardlinks."""
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise CicdV2Error("unsafe_record_path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CicdV2Error("unsafe_record_path")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read() if limit is None else stream.read(limit + 1)
            if limit is not None and len(raw) > limit:
                raise CicdV2Error("input_guard_exceeded")
            return raw
    finally:
        os.close(fd)


def _object(raw: bytes) -> dict[str, object]:
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise CicdV2Error("invalid_record")
    return value


def _validate_repair_profile(
    config: Mapping[str, object], assets: Path, *, workflow: Path | None = None,
) -> None:
    """Admit Droid writes only from the exact validated captured profile."""
    roles = cast(dict[str, object], config["role_bindings"])
    selection = cast(dict[str, object], roles["repair"])
    binding = cast(dict[str, object], selection["binding"])
    if binding["adapter"] != "droid_exec":
        return
    from .router import droid_configuration as droid

    ref = cc.validate_ref(selection["profile_ref"])
    raw = _read(assets / str(ref["path"]), limit=droid.CONTROL_LIMIT)
    if _sha(raw) != ref["sha256"]:
        raise CicdV2Error("asset_changed")
    try:
        profile = droid.profile(raw)
    except droid.RouterError as exc:
        raise CicdV2Error("invalid_repair_profile") from exc
    if profile["permission_mode"] != "auto_medium":
        raise CicdV2Error("binding_cannot_write")
    if workflow is not None and _read(workflow / str(ref["path"])) != raw:
        raise CicdV2Error("prepared_control_changed")


def _out(outcome: str, reason: str | None = None) -> dict[str, object]:
    return {"outcome": outcome, "summary": reason or outcome, "refs": []}


def _new_state(input_id: str) -> dict[str, object]:
    return {
        "format": vr.STATE,
        "input_id": input_id,
        "version": 1,
        "phase": "freeze",
        "source_binding_ref": None,
        "attempt_refs": [],
        "current_attempt_id": None,
        "basis_sha256": None,
        "assessment_ref": None,
        "candidate_ref": None,
        "check_plan_ref": None,
        "review_plan_ref": None,
        "delivery_refs": [],
        "health_refs": [],
        "product_layer_refs": [],
        "hold_ref": None,
        "settlement_ref": None,
        "applied_decision_ids": [],
    }


def _guard_values(config: Mapping[str, object]) -> dict[str, int | float]:
    supported = {
        "input": "bytes",
        "input.log": "bytes",
        "input.evidence": "bytes",
        "command.stdout": "bytes",
        "command.stderr": "bytes",
        "snapshot.bytes": "bytes",
        "snapshot.entries": "entries",
    }
    result = {}
    for record in config["resource_policy"]["guards"]:
        surface = record["surface"]
        if surface not in supported:
            raise CicdV2Error("unsupported_resource_guard")
        if record["measure"] != supported[surface] or surface in result:
            raise CicdV2Error("resource_guard_measure_mismatch")
        result[surface] = record["value"]
    return result


class V2Home:
    """Captured v2 authority, FIFO input and versioned state for one target.

    Every writer acquires target ownership, then the state lock, then an intake
    transaction. Public observations never create or repair missing records.
    """

    def __init__(self, home: Path) -> None:
        self.home = canonical_dir(str(home), "invalid_workflow_home")
        if (self.home / "cicd.json").exists():
            raise CicdV2Error("versioned_home_conflict")
        self.record = _object(_read(self.home / "cicd-v2.json"))
        if self.record.get("format") != SERVICE or self.record.get(
            "service_home"
        ) != str(self.home):
            raise CicdV2Error("invalid_service_record")
        self.workspace = self.home / "project" / "main"
        self.config_ref = cast(dict[str, object], self.record["config_ref"])
        self.config = vr.validate_config(self.read_ref(self.config_ref))
        _validate_repair_profile(
            self.config, self.workspace, workflow=self.home / "workflow"
        )
        self.config_sha256 = str(self.config_ref["sha256"])
        self.target = canonical_dir(str(self.config["target_root"]), "invalid_target")
        self.target_identity = cast(dict[str, object], self.record["target_identity"])
        if filesystem_identity(self.target) != self.target_identity:
            raise CicdV2Error("target_identity_changed")
        self.service_id = str(self.record["service_id"])
        self.policy = cc.validate_path_policy(
            self.read_ref(self.config["path_policy_ref"])
        )
        self.authority = vr.validate_local_authority(
            self.read_ref(self.config["local_authority_ref"])
        )
        if (
            self.authority["target_root"] != str(self.target)
            or self.authority["path_policy_ref"] != self.config["path_policy_ref"]
        ):
            raise CicdV2Error("authority_mismatch")
        self.guards = _guard_values(self.config)
        self.intake = Intake(
            self.home / "intake.sqlite3",
            self.service_id,
            validate_payload=self._payload,
            validate_claim=self._claim,
            validate_decision=self._decision,
            validate_decision_aftermath=self._aftermath,
            task_id_for_key=lambda key: event_from_key(key)[:32],
            decision_receipt=self._receipt,
            authority_sha256=_sha(_read(self.home / "cicd-v2.json")),
        )

    @staticmethod
    def _payload(raw: bytes) -> None:
        obj = _object(raw)
        if set(obj) != {"input_id", "original_ref", "log_ref"} or canonical(obj) != raw:
            raise CicdV2Error("invalid_intake_payload")
        vr._sha(obj["input_id"], "invalid_input_id")
        cc.validate_ref(obj["original_ref"])
        if obj["log_ref"] is not None:
            cc.validate_ref(obj["log_ref"])

    @staticmethod
    def _claim(value: dict[str, object]) -> None:
        if (
            set(value) != {"task_id", "request_sha256", "input_id"}
            or value["task_id"] != str(value["input_id"])[:32]
        ):
            raise CicdV2Error("invalid_claim")

    @staticmethod
    def _decision(value: dict[str, object]) -> None:
        if set(value) != {"task_id", "decision", "evidence"}:
            raise CicdV2Error("invalid_decision")
        if value["decision"] == "settle":
            cc.validate_ref(value["evidence"])
        else:
            record = vr.require_ci_r1_decision(value["evidence"])
            if record["kind"] != value["decision"]:
                raise CicdV2Error("invalid_decision")

    def _receipt(self, request_id: str, intent: dict[str, object]) -> dict[str, object]:
        return {
            "format": "millrace-plus-cicd-decision-receipt/v2",
            "service_id": self.service_id,
            "request_id": request_id,
            "task_id": intent["task_id"],
            "kind": intent["decision"],
            "intent_sha256": vr.digest(intent),
        }

    @staticmethod
    def _aftermath(
        task: dict[str, object], intent: dict[str, object], receipt: dict[str, object]
    ) -> None:
        if task["terminal_receipt"] == receipt and intent["decision"] not in {
            "settle",
            "cancel",
        }:
            raise CicdV2Error("invalid_decision_aftermath")

    @contextmanager
    def lock(self) -> Iterator[None]:
        with target_lock(self.target, self.target_identity, self.home, self.service_id):
            yield

    @contextmanager
    def state_lock(self) -> Iterator[None]:
        fd = os.open(
            self.home / "state.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            # No waiting on a lock held by an executing peer or controller.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CicdV2Error("state_busy") from exc
            yield
        finally:
            os.close(fd)

    def file_ref(self, path: Path) -> dict[str, object]:
        return cc.make_ref(path.relative_to(self.workspace).as_posix(), _read(path))

    def resolve_ref(self, value: object) -> Path:
        item = cc.validate_ref(value)
        path = self.workspace / str(item["path"])
        if _sha(_read(path)) != item["sha256"]:
            raise CicdV2Error("record_changed")
        return path

    def read_ref(self, value: object) -> dict[str, object]:
        return _object(_read(self.resolve_ref(value)))

    def retain(self, value: object) -> dict[str, object]:
        raw = canonical(value)
        return self.retain_bytes(raw, ".json")

    def retain_bytes(self, raw: bytes, suffix: str = ".bin") -> dict[str, object]:
        path = self.workspace / (_sha(raw) + suffix)
        write_exact(path, raw, "record_conflict")
        return self.file_ref(path)

    def state_path(self, input_id: str) -> Path:
        vr._sha(input_id, "invalid_input_id")
        return self.workspace / (input_id + ".state.json")

    def load_state(self, input_id: str) -> dict[str, object] | None:
        path = self.state_path(input_id)
        if not path.exists():
            return None
        state = vr.validate_state(_object(_read(path)), phases=self.phases())
        if state["input_id"] != input_id:
            raise CicdV2Error("state_subject_mismatch")
        # Traversing every retained link makes missing/corrupt evidence a hold.
        for key in (
            "source_binding_ref",
            "assessment_ref",
            "candidate_ref",
            "check_plan_ref",
            "review_plan_ref",
            "hold_ref",
            "settlement_ref",
        ):
            if state[key] is not None:
                self.resolve_ref(state[key])
        for key in (
            "attempt_refs",
            "delivery_refs",
            "health_refs",
            "product_layer_refs",
        ):
            for item in cast(list[object], state[key]):
                self.resolve_ref(item)
        return state

    def phases(self) -> tuple[str, ...]:
        graph = self.read_ref(self.record["definition_ref"])
        return tuple(
            cast(
                dict[str, object],
                cast(dict[str, dict[str, object]], graph["planes"])["main"]["nodes"],
            )
        )

    def save_locked(
        self, state: dict[str, object], expected: int | None
    ) -> dict[str, object]:
        current = self.load_state(str(state["input_id"]))
        if (
            (current is None) != (expected is None)
            or current is not None
            and current["version"] != expected
        ):
            raise CicdV2Error("stale_state")
        saved = state | {"version": 1 if expected is None else expected + 1}
        vr.validate_state(saved, phases=self.phases())
        write_replace(self.state_path(str(state["input_id"])), canonical(saved))
        return saved

    def task(self, input_id: str) -> dict[str, object]:
        vr._sha(input_id, "invalid_input_id")
        rows = self.intake.tasks(input_id[:32])
        if len(rows) != 1 or rows[0]["intake_key"] != intake_key(input_id):
            raise CicdV2Error("input_not_found")
        return rows[0]

    def input(self, input_id: str) -> dict[str, object]:
        task = self.task(input_id)
        payload = _object(cast(bytes, task["request_bytes"]))
        original = self.read_ref(payload["original_ref"])
        original_raw = _read(self.resolve_ref(payload["original_ref"]))
        if original.get("format") == "millrace-plus-cicd-feedback/v1":
            from .cicd_feedback import (
                feedback_identity,
                feedback_refs,
                validate_feedback,
            )

            report = validate_feedback(original, str(self.config["repository"]))
            kind = str(report["kind"])
            identity = feedback_identity(report)
            if (
                vr.input_identity(kind, identity) != input_id
                or payload["log_ref"] is not None
            ):
                raise CicdV2Error("input_changed")
            parent = Path(str(payload["original_ref"]["path"])).parent
            refs = [
                {
                    "path": (parent / str(ref["path"])).as_posix(),
                    "sha256": ref["sha256"],
                }
                for ref in feedback_refs(report)
            ]
            for ref in refs:
                self.resolve_ref(ref)
            expected = vr.validate_input(
                {
                    "format": vr.INPUT,
                    "input_id": input_id,
                    "kind": kind,
                    "original_ref": payload["original_ref"],
                    "evidence_refs": refs,
                    "accepted_sequence": task["sequence"],
                    "repository": report["repository"],
                    "source_identity": identity,
                }
            )
        else:
            event = _validate_event(
                original_raw, str(self.config["repository"]), legacy_limits=False
            )
            if event_identity(event) != input_id:
                raise CicdV2Error("input_changed")
            if (payload["log_ref"] is None) != (event["log"] is None):
                raise CicdV2Error("input_log_mismatch")
            if payload["log_ref"] is not None:
                expected_log_path = (
                    Path(str(payload["original_ref"]["path"])).parent
                    / str(event["log"]["path"])
                ).as_posix()
                if payload["log_ref"] != {
                    "path": expected_log_path,
                    "sha256": event["log"]["sha256"],
                }:
                    raise CicdV2Error("input_log_mismatch")
                self.resolve_ref(payload["log_ref"])
            expected = vr.validate_input(
                {
                    "format": vr.INPUT,
                    "input_id": input_id,
                    "kind": "ci_event",
                    "original_ref": payload["original_ref"],
                    "evidence_refs": []
                    if payload["log_ref"] is None
                    else [payload["log_ref"]],
                    "accepted_sequence": task["sequence"],
                    "repository": original["repository"],
                    "source_identity": {
                        key: original[key]
                        for key in (
                            "source",
                            "repository",
                            "run_id",
                            "attempt",
                            "job_id",
                            "head_sha",
                        )
                    },
                }
            )
        path = self.workspace / (input_id + ".input.json")
        if _read(path) != canonical(expected):
            raise CicdV2Error("input_projection_missing_or_changed")
        return expected

    def submit(self, event_file: Path) -> dict[str, object]:
        guard = self.guards.get("input")
        raw = _read(event_file.absolute(), limit=guard)
        if guard is not None and len(raw) > guard:
            raise CicdV2Error("input_guard_exceeded")
        event = _validate_event(
            raw, str(self.config["repository"]), legacy_limits=False
        )
        log = None
        if event["log"] is not None:
            source = event_file.absolute().parent / str(event["log"]["path"])
            try:
                log = _read(source, limit=self.guards.get("input.log"))
            except CicdV2Error as exc:
                if exc.code == "input_guard_exceeded":
                    raise CicdV2Error("input_log_guard_exceeded") from exc
                raise
            try:
                log.decode("utf-8")
            except UnicodeError as exc:
                raise CicdV2Error("invalid_event_log") from exc
            if _sha(log) != event["log"]["sha256"]:
                raise CicdV2Error("event_log_digest_mismatch")
        input_id = event_identity(event)
        with self.lock(), self.state_lock():
            original_dir = self.workspace / "originals" / input_id
            original_path = original_dir / "event.json"
            write_exact(original_path, raw, "input_conflict")
            original_ref = self.file_ref(original_path)
            log_ref = None
            if log is not None:
                original_log = original_dir / str(event["log"]["path"])
                write_exact(original_log, log, "input_conflict")
                log_ref = self.file_ref(original_log)
            payload = {
                "input_id": input_id,
                "original_ref": original_ref,
                "log_ref": log_ref,
            }
            receipt = self.intake.submit(intake_key(input_id), canonical(payload))
            record = vr.validate_input(
                {
                    "format": vr.INPUT,
                    "input_id": input_id,
                    "kind": "ci_event",
                    "original_ref": original_ref,
                    "evidence_refs": [] if log_ref is None else [log_ref],
                    "accepted_sequence": receipt["sequence"],
                    "repository": event["repository"],
                    "source_identity": {
                        key: event[key]
                        for key in (
                            "source",
                            "repository",
                            "run_id",
                            "attempt",
                            "job_id",
                            "head_sha",
                        )
                    },
                }
            )
            write_exact(
                self.workspace / (input_id + ".input.json"),
                canonical(record),
                "input_conflict",
            )
        return {"input_id": input_id, "event_id": input_id, "receipt": receipt}

    def submit_report(self, report_file: Path) -> dict[str, object]:
        from .cicd_feedback import submit_report

        return submit_report(self, report_file)

    def correlate(self, record_file: Path) -> dict[str, object]:
        from .cicd_feedback import record_correlation

        return record_correlation(self, record_file)

    def status(self, input_id: str | None = None) -> dict[str, object]:
        from .cicd_feedback import correlations

        rows = self.intake.tasks() if input_id is None else [self.task(input_id)]
        results = []
        for task in rows:
            current = event_from_key(str(task["intake_key"]))
            state = self.load_state(current)
            self.input(current)
            results.append(
                {
                    "input_id": current,
                    "sequence": task["sequence"],
                    "status": task["status"],
                    "state": state,
                    "terminal_receipt": task["terminal_receipt"],
                    "correlation_refs": correlations(self, current),
                    "decision_dispositions": self.decision_dispositions(current),
                    "delivery_basis_ref": self._delivery_basis(current),
                }
            )
        return {"service_id": self.service_id, "inputs": results}

    def _delivery_basis(self, input_id):
        from .cicd_effects import basis_ref

        return basis_ref(self, input_id)

    def decision_dispositions(self, input_id):
        result = []
        decisions = {row["request_id"]: row for row in self.decisions(input_id)}
        for path in sorted(
            self.workspace.glob(input_id + "-*.decision-disposition.json")
        ):
            record = self.read_ref(_object(_read(path)))
            decision = vr.require_ci_r1_decision(self.read_ref(record["decision_ref"]))
            plan = self.read_ref(record["settlement_plan_ref"])
            task = self.task(input_id)
            receipt = self.intake.decision(plan["request_id"], plan["intent"])
            if (
                record["status"] != "superseded_by_settlement"
                or record["input_id"] != input_id
                or decision["input_id"] != input_id
                or decisions.get(decision["request_id"]) != decision
                or decision["kind"] != "cancel"
                or plan["input_id"] != input_id
                or plan["request_id"] != record["settlement_request_id"]
                or task["task_id"] != plan["task_id"]
                or task["claim"] != plan["claim"]
                or task["version"] != plan["task_version"] + 1
                or task["status"] != plan["terminal"]
                or receipt is None
                or receipt["receipt"] != record["terminal_receipt"]
                or task["terminal_receipt"] != record["terminal_receipt"]
                or plan["decision"] == decision
            ):
                raise CicdV2Error("decision_disposition_changed")
            result.append(record)
        return result

    def decisions(self, input_id: str) -> list[dict[str, object]]:
        result = []
        for row in self.intake.decisions(input_id[:32]):
            intent = cast(dict[str, object], row["intent"])
            if intent["decision"] == "settle":
                continue
            decision = vr.require_ci_r1_decision(intent["evidence"])
            path = self.workspace / (
                vr.digest(decision["request_id"])[:32] + ".decision.json"
            )
            if not path.exists():
                raise CicdV2Error("decision_projection_missing")
            if _read(path) != canonical(decision):
                raise CicdV2Error("decision_conflict")
            for item in cast(list[object], decision["evidence_refs"]):
                self.resolve_ref(item)
            for key in ("changed_cause_ref", "permission_ref"):
                if decision[key] is not None:
                    self.resolve_ref(decision[key])
            result.append(decision)
        return result

    def decide(self, decision: object) -> dict[str, object]:
        record = vr.require_ci_r1_decision(decision)
        input_id = str(record["input_id"])
        request_key = vr.digest(record["request_id"])[:32]
        with self.lock(), self.state_lock():
            task = self.task(input_id)
            state = self.load_state(input_id)
            intent = {
                "task_id": task["task_id"],
                "decision": record["kind"],
                "evidence": record,
            }
            previous = self.intake.decision(request_key)
            if previous is None:
                if task["status"] in TERMINAL:
                    raise CicdV2Error("input_terminal")
                if record["kind"] == "cancel":
                    expected = task["version"] if state is None else state["version"]
                    basis = (
                        self.file_ref(self.workspace / (input_id + ".input.json"))[
                            "sha256"
                        ]
                        if state is None
                        else state["hold_ref"]["sha256"]
                        if state["hold_ref"] is not None
                        else state["basis_sha256"]
                        or self.file_ref(self.workspace / (input_id + ".input.json"))[
                            "sha256"
                        ]
                    )
                    if (
                        record["expected_state_version"] != expected
                        or record["basis_sha256"] != basis
                    ):
                        raise CicdV2Error("stale_cancel_basis")
                if record["kind"] != "cancel":
                    if (
                        state is None
                        or state["version"] != record["expected_state_version"]
                    ):
                        raise CicdV2Error("stale_state")
                    if task["status"] != "held" or state["hold_ref"] is None:
                        raise CicdV2Error("input_not_held")
                    report = self.read_ref(state["hold_ref"])
                    if record["basis_sha256"] != state["hold_ref"]["sha256"]:
                        raise CicdV2Error("decision_basis_mismatch")
                    vr.validate_report(report)
                    pending = [
                        d
                        for d in self.decisions(input_id)
                        if d["request_id"] not in state["applied_decision_ids"]
                    ]
                    if pending:
                        raise CicdV2Error("decision_pending")
                for item in cast(list[object], record["evidence_refs"]):
                    self.resolve_ref(item)
                if record["changed_cause_ref"] is not None:
                    self.resolve_ref(record["changed_cause_ref"])
                if record["kind"] == "close" and state["delivery_refs"]:
                    from .cicd_effects import disposition

                    if disposition(self, state) == "inspect":
                        raise CicdV2Error("effect_unresolved")
                if record["kind"] == "approve_delivery":
                    from .cicd_effects import check_permission

                    check_permission(self, state, record)
                if record["kind"] == "continue" and state["delivery_refs"]:
                    raise CicdV2Error("delivery_already_approved")
                if record["kind"] == "continue":
                    Runtime(self, self.home / "workflow")._continuation_basis(
                        state, record
                    )
            receipt = self.intake.decide(
                request_key,
                intent,
                check=lambda _: None,
                cancel_queued=task["status"] == "queued" and record["kind"] == "cancel",
            )
            write_exact(
                self.workspace / (request_key + ".decision.json"),
                canonical(record),
                "decision_conflict",
            )
            return receipt

    def instance(self) -> str:
        manifest = _object(_read(self.home / "preparation.json"))
        _verify_preparation(self.home, manifest)
        descriptor = _object(_read(self.home / "execution.json"))
        binding = vr.validate_actor_binding(
            _object(_read(self.workspace / "actor-binding.json"))
        )
        vr.verify_actor_binding(self.authority, binding)
        receipt = self.read_ref(binding["create_receipt_ref"])
        validation = self.read_ref(descriptor["validation_ref"])
        if validation.get("valid") is not True or validation.get(
            "revision_id"
        ) != descriptor.get("validated_draft_id"):
            raise CicdV2Error("captured_revision_changed")
        if (
            descriptor.get("execution_home") != str(self.home)
            or descriptor.get("format") != EXECUTION
            or descriptor.get("instance_id") != binding["instance_id"]
            or receipt.get("instance_id") != binding["instance_id"]
            or binding["authority_ref"] != self.config["local_authority_ref"]
            or binding["definition_ref"] != manifest["definition_ref"]
            or binding["create_request_id"] != manifest["create_request_id"]
            or descriptor.get("state_home") != self.record["state_home"]
            or descriptor.get("state_home") != manifest["state_home"]
            or descriptor.get("project_root") != str(self.home / "project")
            or descriptor.get("host_paths") != manifest["settings"]["host_paths"]
            or descriptor.get("control_hashes") != manifest["control_hashes"]
            or descriptor.get("poll_seconds")
            != manifest["idle_wait_policy"]["poll_seconds"]
        ):
            raise CicdV2Error("invalid_execution_descriptor")
        return str(binding["instance_id"])

    def observe(self) -> dict[str, object]:
        return snapshot_policy(self.target, self.policy, self.guards)


class Runtime:
    """One declared graph operation; routing and process lifecycle stay in Core."""

    def __init__(self, home: V2Home, controls: Path) -> None:
        from .router import control

        self.home, self.controls, self.control = home, controls, control
        self.instance_id = home.instance()
        self.state_home = Path(str(home.record["state_home"]))

    def _run(self, run_id: str) -> dict[str, object]:
        return self.control.inspect_run(
            self.instance_id, run_id, state_home=self.state_home
        )

    def current(self, node: str) -> dict[str, object]:
        from .cicd_candidates import bind_invocation

        return bind_invocation(
            self.home.home,
            "main",
            node,
            self.controls / "scripts" / "step.py",
            os.getpid(),
            Path.cwd(),
        )

    def _active(self) -> dict[str, object] | None:
        rows = [row for row in self.home.intake.tasks() if row["status"] in ACTIVE]
        if len(rows) > 1:
            raise CicdV2Error("invalid_intake")
        return rows[0] if rows else None

    def _state(self, input_id: str) -> dict[str, object]:
        state = self.home.load_state(input_id)
        if state is None:
            raise CicdV2Error("state_missing")
        return state

    def _save(self, state: dict[str, object], **changes: object) -> dict[str, object]:
        return self.home.save_locked(state | changes, cast(int, state["version"]))

    def _input_ref(self, input_id: str) -> dict[str, object]:
        self.home.input(input_id)
        return self.home.file_ref(self.home.workspace / (input_id + ".input.json"))

    def _attempts(self, state: dict[str, object]) -> dict[str, dict[str, object]]:
        result = {}
        for item in cast(list[object], state["attempt_refs"]):
            record = vr.validate_attempt(self.home.read_ref(item))
            if (
                record["input_id"] != state["input_id"]
                or record["config_sha256"] != self.home.config_sha256
            ):
                raise CicdV2Error("attempt_basis_mismatch")
            result[str(record["attempt_id"])] = record
        return result

    def _attempt(self, state: dict[str, object]) -> dict[str, object]:
        attempts = self._attempts(state)
        current = attempts.get(str(state["current_attempt_id"]))
        if current is None:
            raise CicdV2Error("attempt_missing")
        return current

    def _record_attempt(
        self, state: dict[str, object], attempt: dict[str, object], **changes: object
    ) -> dict[str, object]:
        checked = vr.validate_attempt(attempt)
        return self._save(
            state,
            attempt_refs=[
                *cast(list[object], state["attempt_refs"]),
                self.home.retain(checked),
            ],
            current_attempt_id=checked["attempt_id"],
            **changes,
        )

    def _reserve(
        self,
        state: dict[str, object],
        kind: str,
        invocation: dict[str, object],
        *,
        future: bool = False,
        changed: object = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        import uuid

        current = self._attempts(state).get(str(state["current_attempt_id"]))
        if current is not None and current["status"] in {
            "reserved",
            "running",
            "unknown",
        }:
            raise CicdV2Error("attempt_already_reserved")
        if changed is None and kind in {"reproduce", "probe", "checks"}:
            applied = set(state["applied_decision_ids"])
            decisions = [
                row
                for row in self.home.decisions(str(state["input_id"]))
                if row["request_id"] in applied
                and row["kind"] == "continue"
                and row["target_stage"] == kind
            ]
            if decisions:
                changed = decisions[-1]["changed_cause_ref"]
        source = source_identity(self.home.observe())
        attempt_id = uuid.uuid4().hex
        attempt = vr.validate_attempt(
            {
                "format": vr.ATTEMPT,
                "input_id": state["input_id"],
                "subject_ref": self._input_ref(str(state["input_id"])),
                "attempt_id": attempt_id,
                "parent_attempt_id": None if current is None else current["attempt_id"],
                "kind": kind,
                "basis_sha256": state["basis_sha256"]
                or vr.digest({"source": source, "config": self.home.config_sha256}),
                "source_sha256": source,
                "config_sha256": self.home.config_sha256,
                "changed_cause_ref": changed,
                "reservation": {
                    "core_invocation_id": None
                    if future
                    else invocation["invocation_id"],
                    "core_attempt_id": None if future else invocation["attempt"],
                    "request_id": attempt_id,
                },
                "completion_ref": None,
                "artifact_ref": None,
                "status": "reserved" if future else "running",
            }
        )
        self.home.retain_bytes(canonical(invocation), ".invocation.json")
        preparation = {
            "invocation": invocation,
            "node": kind,
            "expected_attempt": invocation["attempt"] + (1 if future else 0),
            "attempt_ref": self.home.retain(attempt),
        }
        write_exact(
            self.home.workspace / (attempt_id + ".preparation.json"),
            canonical(preparation),
        )
        return self._record_attempt(
            state, attempt, phase=kind if kind != "review" else "review_1"
        ), attempt

    def _preparation(self, attempt: dict[str, object]) -> dict[str, object]:
        base_path = self.home.workspace / (
            str(attempt["attempt_id"]) + ".preparation.json"
        )
        original = _object(_read(base_path))
        reserved = vr.validate_attempt(self.home.read_ref(original["attempt_ref"]))
        if any(
            reserved[key] != attempt[key]
            for key in (
                "input_id",
                "subject_ref",
                "attempt_id",
                "parent_attempt_id",
                "kind",
                "basis_sha256",
                "source_sha256",
                "config_sha256",
                "changed_cause_ref",
            )
        ):
            raise CicdV2Error("attempt_preparation_changed")
        pointer = self.home.workspace / (str(attempt["attempt_id"]) + ".dispatch.json")
        if not pointer.exists():
            return original
        selected = self.home.read_ref(_object(_read(pointer)))
        current = selected
        seen = set()
        while "previous_ref" in current:
            ref = cc.validate_ref(current["previous_ref"])
            if ref["sha256"] in seen:
                raise CicdV2Error("dispatch_projection_cycle")
            seen.add(ref["sha256"])
            if (
                current["attempt_ref"] != original["attempt_ref"]
                or current["node"] != original["node"]
            ):
                raise CicdV2Error("dispatch_projection_changed")
            current = self.home.read_ref(ref)
        if current != original:
            raise CicdV2Error("dispatch_origin_changed")
        return selected

    def _completion(
        self, attempt: dict[str, object], node: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        preparation = self._preparation(attempt)
        context = cast(dict[str, object], preparation["invocation"])
        view = self._run(str(context["run_id"]))
        preparers = [
            row for row in view["invocations"] if row["id"] == context["invocation_id"]
        ]
        if len(preparers) != 1 or any(
            preparers[0].get(key) != context[key]
            for key in (
                "plane_id",
                "node",
                "attempt",
                "pid",
                "cwd",
                "argv",
                "deadline",
                "revision_id",
            )
        ):
            raise CicdV2Error("producing_context_changed")
        rows = [
            row
            for row in cast(list[dict[str, object]], view["invocations"])
            if row["plane_id"] == context["plane_id"]
            and row["attempt"] == preparation["expected_attempt"]
        ]
        if (
            len(rows) != 1
            or rows[0]["node"] != node
            or rows[0].get("writer_state") != "stopped"
        ):
            raise CicdV2Error("writer_not_stopped")
        invocation = rows[0]
        if invocation["revision_id"] != context["revision_id"]:
            raise CicdV2Error("producing_revision_changed")
        completed = [
            row
            for row in cast(list[dict[str, object]], view["completions"])
            if row["invocation_id"] == invocation["id"]
        ]
        if len(completed) != 1 or completed[0].get("effective_writer") != "stopped":
            raise CicdV2Error("completion_missing_or_unknown")
        done = completed[0]
        # Exact public producing rows, not a submitted claim about root exit.
        evidence = {
            "instance_id": self.instance_id,
            "state_home": str(self.state_home),
            "run_id": context["run_id"],
            "invocation": invocation,
            "completion": done,
        }
        self.home.retain(evidence)
        return invocation, done

    def _finish(
        self,
        state: dict[str, object],
        attempt: dict[str, object],
        node: str,
        artifact: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        invocation, done = self._completion(attempt, node)
        if done["transport"] != "completed" or done["classification"] != "semantic":
            raise CicdV2Error("attempt_not_semantically_completed")
        finished = attempt | {
            "reservation": cast(dict[str, object], attempt["reservation"])
            | {
                "core_invocation_id": invocation["id"],
                "core_attempt_id": invocation["attempt"],
            },
            "completion_ref": self.home.retain(
                {"invocation": invocation, "completion": done}
            ),
            "artifact_ref": self.home.retain(artifact),
            "status": "completed",
        }
        return self._record_attempt(state, finished), finished

    def _source(
        self, state: dict[str, object], expected: str | None = None
    ) -> dict[str, object]:
        view = self.home.observe()
        binding = vr.validate_source_binding(
            self.home.read_ref(state["source_binding_ref"])
        )
        if git_head(self.home.target) != binding["anchor_head"]:
            raise CicdV2Error("source_head_changed")
        if expected is not None and source_identity(view) != expected:
            raise CicdV2Error("source_changed")
        return view

    def _stopped(self, state: dict[str, object], mine: dict[str, object]) -> None:
        runs = {str(mine["run_id"])}
        for attempt in self._attempts(state).values():
            prep = _object(
                _read(
                    self.home.workspace
                    / (str(attempt["attempt_id"]) + ".preparation.json")
                )
            )
            runs.add(str(cast(dict[str, object], prep["invocation"])["run_id"]))
        if state["delivery_refs"]:
            from .cicd_effects import history

            _, operations = history(self.home, state)
            runs.update(str(row["invocation"]["run_id"]) for row in operations)
        for run_id in runs:
            view = self._run(run_id)
            for row in cast(list[dict[str, object]], view["invocations"]):
                if (
                    row["id"] != mine["invocation_id"]
                    and row.get("writer_state") != "stopped"
                ):
                    raise CicdV2Error("writer_not_stopped")

    def _report(
        self, state: dict[str, object], reason: str | None, status: str = "uncertain"
    ) -> dict[str, object]:
        attempts = self._attempts(state)
        checks, reviews, patch = [], [], None
        before = after = None
        for item in attempts.values():
            if item["artifact_ref"] is None:
                continue
            artifact = self.home.read_ref(item["artifact_ref"])
            if artifact.get("format") == vr.REPAIR:
                patch = artifact["patch_ref"]
                before, after = (
                    artifact["source_before_sha256"],
                    artifact["source_after_sha256"],
                )
            if artifact.get("format") == vr.REVIEW:
                reviews.append(item["artifact_ref"])
            checks.extend(cast(list[object], artifact.get("accepted_refs", [])))
        return vr.validate_report(
            {
                "format": vr.REPORT,
                "input_id": state["input_id"],
                "config_sha256": self.home.config_sha256,
                "status": status,
                "source_before_sha256": before,
                "source_after_sha256": after,
                "assessment_ref": state["assessment_ref"],
                "attempt_refs": state["attempt_refs"],
                "patch_ref": patch,
                "checks_refs": checks,
                "review_refs": reviews,
                "correlation_refs": [],
                "candidate_ref": state["candidate_ref"],
                "delivery_refs": state["delivery_refs"],
                "health_refs": [],
                "incident_refs": [],
                "hold_reason": reason,
            }
        )

    def _hold(
        self, state: dict[str, object], reason: str, status: str = "uncertain"
    ) -> dict[str, object]:
        current = self._attempts(state).get(str(state["current_attempt_id"]))
        if (
            current is not None
            and current["kind"] in {"investigate", "repair", "review"}
            and current["status"] != "completed"
        ):
            role_path = self.home.workspace / (
                str(current["attempt_id"]) + ".role.json"
            )
            preparation = _object(_read(role_path))
            selected_node = (
                ("review_" + str(preparation["reviewer"]))
                if current["kind"] == "review"
                else str(current["kind"])
            )
            try:
                invocation, completion = self._completion(current, selected_node)
            except CicdV2Error as exc:
                if exc.code not in {
                    "writer_not_stopped",
                    "completion_missing_or_unknown",
                }:
                    raise
                # A hold is not completion or proof that dispatch never happened.
                invocation = completion = None
            if invocation is not None:
                snapshot = self.home.read_ref(preparation["snapshot_ref"])
                if self.home.observe()["products"] != snapshot["products"]:
                    reason = "noncheck_product_write"
                result_path = self.home.workspace / str(preparation["result"])
                rejected = None
                if result_path.exists():
                    try:
                        rejected = self.home.file_ref(result_path)
                    except (OSError, CicdV2Error):
                        pass  # Preserve the unsafe output in place; never follow it.
                rejection_ref = self.home.retain(
                    {"rejected_result_ref": rejected, "reason": reason}
                )
                finished = current | {
                    "status": "completed",
                    "artifact_ref": rejection_ref,
                    "reservation": current["reservation"]
                    | {
                        "core_invocation_id": invocation["id"],
                        "core_attempt_id": invocation["attempt"],
                    },
                    "completion_ref": self.home.retain(
                        {"invocation": invocation, "completion": completion}
                    ),
                }
                state = self._record_attempt(state, finished)
        report_ref = self.home.retain(self._report(state, reason, status))
        state = self._save(state, phase="hold", hold_ref=report_ref)
        task = self.home.task(str(state["input_id"]))
        if task["status"] != "held":
            self.home.intake.transition(
                str(task["task_id"]),
                version=cast(int, task["version"]),
                claim=cast(dict[str, object], task["claim"]),
                status="held",
            )
        return _out("HOLD", reason)

    def _prepare_role(
        self,
        state: dict[str, object],
        role: str,
        invocation: dict[str, object],
        *,
        reviewer: int = 1,
        changed: object = None,
    ) -> dict[str, object]:
        if any(
            row["kind"] == "cancel"
            and row["request_id"] not in state["applied_decision_ids"]
            for row in self.home.decisions(str(state["input_id"]))
        ):
            raise CicdV2Error("operator_decision_pending")
        roles = cast(dict[str, object], self.home.config["role_bindings"])
        selection = cast(
            dict[str, object],
            roles[role]
            if role != "review"
            else cast(list[object], roles[role])[reviewer - 1],
        )
        if selection["id"] not in self.home.authority["operations"]:
            raise CicdV2Error("binding_not_authorized")
        if role == "repair":
            _validate_repair_profile(
                self.home.config, self.home.workspace,
                workflow=self.home.home / "workflow",
            )
        state, attempt = self._reserve(
            state, role, invocation, future=True, changed=changed
        )
        input_id, attempt_id = str(state["input_id"]), str(attempt["attempt_id"])
        for key in ("prompt_ref", "skill_ref", "result_schema_ref", "profile_ref"):
            if selection[key] is not None:
                self.home.resolve_ref(selection[key])
        document = {
            "format": "millrace-plus-cicd-role-input/v2",
            "input_id": input_id,
            "subject_ref": self._input_ref(input_id),
            "role": role,
            "attempt_id": attempt_id,
            "basis_sha256": attempt["basis_sha256"],
            "source_before_sha256": attempt["source_sha256"],
            "config_ref": self.home.config_ref,
            "target_root": str(self.home.target),
            "source_binding_ref": state["source_binding_ref"],
            "assessment_ref": state["assessment_ref"],
            "check_plan_ref": state["check_plan_ref"],
            "review_plan_ref": state["review_plan_ref"],
            "attempt_refs": state["attempt_refs"],
            "repair_paths": self.home.config["repair_paths"],
            "protected_paths": self.home.config["protected_paths"],
            "prompt_ref": selection["prompt_ref"],
            "skill_ref": selection["skill_ref"],
            "result_schema_ref": selection["result_schema_ref"],
            "output": attempt_id + ".result.json",
        }
        input_ref = self.home.retain(document)
        projection = {
            "format": vr.SUBJECT_PROJECTION,
            "subject_ref": self._input_ref(input_id),
            "subject_kind": "ci_input",
            "subject_id": input_id,
            "role": role,
            "attempt_id": attempt_id,
            "binding_id": selection["id"],
            "input_ref": input_ref,
            "prompt_ref": selection["prompt_ref"],
            "skill_ref": selection["skill_ref"],
            "result_schema_ref": selection["result_schema_ref"],
            "basis_sha256": attempt["basis_sha256"],
            "criteria_ref": None,
            "source_binding_ref": state["source_binding_ref"],
        }
        # Review has a separate projection contract in the reusable facade. The
        # standalone input lane retains this explicit input without a candidate.
        if role != "review":
            vr.validate_subject_projection(projection)
        projection_ref = self.home.retain(projection)
        pointer = {
            "format": vr.ACTIVE_ROLE,
            "subject_id": input_id,
            "role": role,
            "attempt_id": attempt_id,
            "input_ref": input_ref,
            "projection_ref": projection_ref,
            "basis_sha256": attempt["basis_sha256"],
        }
        if role != "review":
            vr.validate_active_role(pointer)
        write_replace(self.home.workspace / "active-role.json", canonical(pointer))
        preparation_path = self.home.workspace / (attempt_id + ".role.json")
        write_exact(
            preparation_path,
            canonical(
                {
                    "pointer": pointer,
                    "reviewer": reviewer,
                    "result": document["output"],
                    "snapshot_ref": self.home.retain(self.home.observe()),
                }
            ),
            "reservation_conflict",
        )
        return state

    def _role_artifact(
        self, state: dict[str, object], role: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        attempt = self._attempt(state)
        if attempt["kind"] != role:
            raise CicdV2Error("attempt_kind_mismatch")
        prep = _object(
            _read(self.home.workspace / (str(attempt["attempt_id"]) + ".role.json"))
        )
        pointer = prep["pointer"]
        if _object(_read(self.home.workspace / "active-role.json")) != pointer:
            raise CicdV2Error("role_projection_changed")
        for key in ("input_ref", "projection_ref"):
            self.home.resolve_ref(cast(dict[str, object], pointer)[key])
        artifact = _object(_read(self.home.workspace / str(prep["result"])))
        if (
            artifact.get("input_id") != state["input_id"]
            or artifact.get("attempt_id") != attempt["attempt_id"]
        ):
            raise CicdV2Error("role_result_basis_mismatch")
        return attempt, artifact

    def node(self, node: str) -> dict[str, object]:
        mine = self.current(node)
        if node == "wait_event":
            import time

            policy = cast(dict[str, object], self.home.record["idle_wait_policy"])

            def control() -> str | None:
                view = self._run(str(mine["run_id"]))
                if view.get("run_gate") or view.get("cancel_requested"):
                    return "control_applied"
                if any(
                    row.get("status") == "queued"
                    for row in cast(list[dict[str, object]], view.get("controls", []))
                ):
                    return "control_queued"
                return None

            result = idle_wait(
                policy,
                cast(float, mine["deadline"]),
                queued=lambda: any(
                    row["status"] == "queued" for row in self.home.intake.tasks()
                ),
                control=control,
                clock=time.time,
                sleep=time.sleep,
            )
            return _out(str(result["outcome"]), cast(str | None, result["reason"]))
        if node in {"prepare_effect", "execute_effect", "inspect_effect"}:
            from .cicd_effects import operation

            return operation(self, node.removesuffix("_effect"), mine)
        if node in {"reproduce", "checks", "probe"}:
            return self._commands(node, mine)
        with self.home.lock(), self.home.state_lock():
            if node in {"claim", "reconcile"}:
                self._finish_settlements()
            task = self._active()
            if node == "claim":
                if task is not None:
                    raise CicdV2Error("active_input")
                lease = self.home.home / "active-materialization.json"
                if (
                    lease.exists()
                    and self.home.read_ref(_object(_read(lease)))["state"] != "restored"
                ):
                    raise CicdV2Error("target_lease_active")
                claim = self.home.intake.claim(
                    lambda row: {
                        "task_id": row["task_id"],
                        "request_sha256": row["request_sha256"],
                        "input_id": event_from_key(str(row["intake_key"])),
                    }
                )
                if claim is None:
                    return _out("EMPTY")
                input_id = str(claim["input_id"])
                if self.home.load_state(input_id) is None:
                    self.home.save_locked(_new_state(input_id), None)
                write_exact(
                    self.home.workspace / (input_id + ".claim.json"),
                    canonical(
                        {
                            "claim": claim,
                            "invocation": mine,
                            "input_ref": self._input_ref(input_id),
                        }
                    ),
                    "claim_conflict",
                )
                return _out("CLAIMED")
            if task is None:
                if node == "reconcile":
                    return _out("READY")
                raise CicdV2Error("active_input_missing")
            input_id = event_from_key(str(task["intake_key"]))
            self.home.input(input_id)
            state = self._state(input_id)
            if node == "freeze":
                return self._freeze(state, mine)
            self._stopped(state, mine)
            if node == "reconcile":
                return self._reconcile(state, mine)
            if node == "hold":
                active_attempt = self._attempts(state).get(
                    str(state["current_attempt_id"])
                )
                if (
                    active_attempt is not None
                    and active_attempt["kind"] == "probe"
                    and active_attempt["status"] == "running"
                ):
                    self._guard_commands(state, mine, "probe")
                    return _out("WAIT_OPERATOR")
                if state["hold_ref"] is None:
                    self._hold(state, "stage_blocked")
                return _out("WAIT_OPERATOR")
            if node == "settle":
                return self._settle(state, task, mine)
            if node == "guard_reproduction":
                return self._guard_commands(state, mine, "reproduce")
            if node == "guard_checks":
                return self._guard_commands(state, mine, "checks")
            if node in {"guard_assessment", "guard_patch"} or node.startswith(
                "guard_review_"
            ):
                try:
                    if node == "guard_assessment":
                        return self._guard_assessment(state, mine)
                    if node == "guard_patch":
                        return self._guard_patch(state, mine)
                    return self._guard_review(state, mine, int(node.rsplit("_", 1)[1]))
                except (
                    OSError,
                    CicdV2Error,
                    cc.CheckContractError,
                    IntakeError,
                ) as exc:
                    self._hold(
                        self._state(input_id),
                        "operator_decision_pending"
                        if getattr(exc, "code", None) == "operator_decision_pending"
                        else "role_artifact_invalid:"
                        + getattr(exc, "code", type(exc).__name__),
                    )
                    return _out("STALE" if node.startswith("guard_review_") else "HOLD")
            if node == "guard_delivery":
                from .cicd_effects import is_local_git

                if not is_local_git(self.home):
                    return self._guard_delivery(state, mine)
                try:
                    return self._guard_delivery(state, mine)
                except (OSError, CicdError, CicdV2Error) as exc:
                    return self._hold(
                        self._state(input_id),
                        getattr(exc, "code", "local_git_io_error"),
                    )
            raise CicdV2Error("unknown_node")

    def _freeze(
        self, state: dict[str, object], mine: dict[str, object]
    ) -> dict[str, object]:
        from .cicd_candidates import retain_payloads

        input_id = str(state["input_id"])
        record = self.home.input(input_id)
        original = self.home.read_ref(record["original_ref"])
        reported_head = (
            original["head_sha"]
            if record["kind"] == "ci_event"
            else original["affected_version"]["head_sha"]
        )
        head = git_head(self.home.target)
        mode = cast(dict[str, object], self.home.config["source_policy"])["mode"]
        if mode != "standalone":
            # Captured refs/configured preparer names are not producing authority.
            # F1 may supply its selected local validator; publication/preparer
            # mappings remain unavailable until their actual producer exists.
            return self._hold(
                state, "factory_source_validator_unavailable", "held_stale"
            )
        else:
            if reported_head is None:
                return self._hold(state, "source_binding_unproven", "held_stale")
            if head != reported_head:
                return self._hold(state, "head_mismatch", "held_stale")
            binding = vr.validate_source_binding(
                {
                    "format": vr.SOURCE_BINDING,
                    "mode": "standalone",
                    "input_ref": self._input_ref(input_id),
                    "anchor_head": head,
                    "reported_head": reported_head,
                    "candidate_ref": None,
                    "publication_ref": None,
                    "preparer_ref": None,
                    "logical_base_id": None,
                    "materialization_ref": None,
                    "status": "bound",
                }
            )
        view = self.home.observe()
        if view.get("special") or view.get("gitlinks"):
            return self._hold(state, "unsupported_source_shape", "held_stale")
        if any(vr.structural_forbidden(path) for path in view["source"]):
            return self._hold(state, "structural_source_path_forbidden", "held_risk")
        payloads = retain_payloads(self.home.target, view, self.home.workspace)
        frozen = {
            "snapshot": view,
            "head": head,
            "payloads": payloads,
            "config_ref": self.home.config_ref,
            "input_ref": self._input_ref(input_id),
        }
        from .cicd_effects import capture_git, is_local_git, selected

        if selected(self.home):
            try:
                frozen["delivery_git"] = capture_git(self.home, view, mine)
            except (OSError, CicdError, CicdV2Error) as exc:
                if not is_local_git(self.home):
                    raise
                return self._hold(
                    state, getattr(exc, "code", "local_git_io_error"), "held_stale"
                )
        freeze_ref = self.home.retain(frozen)
        write_exact(
            self.home.workspace / (input_id + ".freeze.json"),
            canonical(freeze_ref),
            "freeze_conflict",
        )
        source = source_identity(view)
        state = self._save(
            state,
            source_binding_ref=self.home.retain(binding),
            basis_sha256=vr.digest(
                {
                    "source": source,
                    "config": self.home.config_sha256,
                    "input": self._input_ref(input_id),
                }
            ),
            phase="reproduce",
        )
        if record["kind"] == "ci_event" and original["conclusion"] in {
            "success",
            "cancelled",
            "skipped",
        }:
            report_ref = self.home.retain(self._report(state, None, "no_action"))
            self._save(state, settlement_ref=report_ref, phase="settle")
            return _out("NO_ACTION")
        return _out("READY")

    def _environment(self, snapshot: dict[str, object]) -> dict[str, object]:
        from datetime import UTC, datetime

        prefixes = {
            prefix
            for prefix, surface in self.home.policy.literals
            if surface == "environment_paths"
        }
        prefixes |= set(cast(dict[str, object], snapshot["environment"]))
        record = {
            "format": cc.ENVIRONMENT,
            "path_policy_ref": self.home.config["path_policy_ref"],
            "target_root": str(self.home.target),
            "observed_at": datetime.now(UTC).isoformat(),
            "entries": [
                cc.environment_entry(
                    prefix,
                    os.lstat(self.home.target / prefix)
                    if os.path.lexists(self.home.target / prefix)
                    else None,
                    link_target=os.readlink(self.home.target / prefix)
                    if (self.home.target / prefix).is_symlink()
                    else None,
                )
                for prefix in sorted(prefixes)
            ],
        }
        return cc.validate_environment(
            record,
            policy=self.home.policy,
            policy_ref=self.home.config["path_policy_ref"],
        )

    def _commands(self, node: str, mine: dict[str, object]) -> dict[str, object]:
        import time

        from . import cicd_v2_checks as checks

        with self.home.lock(), self.home.state_lock():
            task = self._active()
            if task is None:
                raise CicdV2Error("active_input_missing")
            input_id = event_from_key(str(task["intake_key"]))
            state = self._state(input_id)
            self._stopped(state, mine)
            if any(
                row["kind"] == "cancel"
                and row["request_id"] not in state["applied_decision_ids"]
                for row in self.home.decisions(input_id)
            ):
                raise CicdV2Error("operator_decision_pending")
            self._source(state)
            if node == "checks":
                plan = vr.verify_check_plan(
                    self.home.config, self.home.read_ref(state["check_plan_ref"])
                )
                ids = plan["command_ids"]
            else:
                field = "reproduction" if node == "reproduce" else "investigation"
                key = "command_ids" if node == "reproduce" else "probe_command_ids"
                ids = cast(dict[str, object], self.home.config[field])[key]
            if not ids:
                raise CicdV2Error("configured_commands_required")
            state, attempt = self._reserve(state, node, mine)
            attempt_id = str(attempt["attempt_id"])
        patterns = vr.validate_failure_patterns(
            self.home.read_ref(
                cast(dict[str, object], self.home.config["reproduction"])[
                    "failure_patterns_ref"
                ]
            )
        )
        matcher = cast(
            dict[str, object],
            cast(dict[str, object], self.home.config["reproduction"])[
                "failure_matcher"
            ],
        )
        by_id = {
            str(row["id"]): row
            for row in cast(list[dict[str, object]], self.home.config["commands"])
        }
        results = []
        not_started = None
        prior_owned = set()
        earlier_images = {}
        for item in cast(list[object], state["product_layer_refs"]):
            layer = cc.validate_products(self.home.read_ref(item))
            if layer["status"] == "accepted":
                prior_owned.update(cast(list[str], layer["owned_paths"]))
                products = self.home.read_ref(layer["after_ref"])["products"]
                earlier_images.update(
                    {path: products[path] for path in layer["owned_paths"]}
                )
        with checks.TermPropagation() as term:
            for identifier in cast(list[str], ids):
                command = by_id[identifier]
                try:
                    with self.home.lock(), self.home.state_lock():
                        if self._state(input_id) != state:
                            raise CicdV2Error("stale_state")
                        before = self._source(
                            state, cast(str, attempt["source_sha256"])
                        )
                        checks.product_preflight(
                            self.home.policy,
                            command,
                            before["products"],
                            earlier_images,
                        )
                        before_ref = self.home.retain(before)
                        environment_before = self.home.retain(self._environment(before))
                        images = {
                            path: self.home.retain_bytes(_read(self.home.target / path))
                            for path, entry in cast(
                                dict[str, dict[str, object]], before["products"]
                            ).items()
                            if entry.get("kind") == "file"
                        }
                        images_ref = self.home.retain(images)
                        reserved = checks.product_layer(
                            input_id=input_id,
                            materialization_id=None,
                            command=command,
                            policy=self.home.policy,
                            policy_ref=self.home.config["path_policy_ref"],
                            before_ref=before_ref,
                            after_ref=None,
                            owned_paths=[],
                            completion_ref=None,
                            previous_ref=None,
                            status="reserved",
                        )
                        reserved_ref = self.home.retain(reserved)
                        intent = {
                            "command_id": identifier,
                            "layer_ref": reserved_ref,
                            "beforeimages_ref": images_ref,
                            "invocation": mine,
                        }
                        write_exact(
                            self.home.workspace
                            / (
                                attempt_id
                                + "-"
                                + vr.digest(identifier)
                                + ".command.json"
                            ),
                            canonical(intent),
                        )
                except (CicdError, CicdV2Error) as exc:
                    if exc.code not in {"target_busy", "state_busy"}:
                        raise
                    not_started = {
                        "reason": exc.code,
                        "invocation": mine,
                        "preparation_ref": self.home.file_ref(
                            self.home.workspace / (attempt_id + ".preparation.json")
                        ),
                        "config_ref": self.home.config_ref,
                        "ordered_command_ids": ids,
                        "remaining_command_ids": list(ids[len(results) :]),
                    }
                    break

                def started(pid: int) -> None:
                    term.started(pid)
                    write_exact(
                        self.home.workspace
                        / (attempt_id + "-" + vr.digest(identifier) + ".pid.json"),
                        canonical(
                            {
                                "pid": pid,
                                "pgid": pid,
                                "attempt_id": attempt_id,
                                "command_id": identifier,
                                "outer_invocation": mine,
                                "observed_at": time.time(),
                            }
                        ),
                    )

                observed = checks.run_command(
                    self.home.target,
                    self.home.policy,
                    command,
                    self.home.guards,
                    base_environment=os.environ,
                    deadline=min(
                        cast(float, mine["deadline"]),
                        time.time() + cast(float, command["timeout_seconds"]),
                    ),
                    cancelled=term.cancelled,
                    on_started=started,
                )
                term.pid = None
                ownership = checks.product_ownership(observed, prior_owned)
                after_ref = self.home.retain(observed["after"])
                layer = checks.product_layer(
                    input_id=input_id,
                    materialization_id=None,
                    command=command,
                    policy=self.home.policy,
                    policy_ref=self.home.config["path_policy_ref"],
                    before_ref=before_ref,
                    after_ref=after_ref,
                    owned_paths=cast(list[str], ownership["owned_paths"]),
                    completion_ref=None,
                    previous_ref=reserved_ref,
                    status="observed",
                )
                cc.validate_product_transition(
                    _read(self.home.resolve_ref(reserved_ref)), reserved_ref, layer
                )
                layer_ref = self.home.retain(layer)
                environment_after = self.home.retain(
                    self._environment(cast(dict[str, object], observed["after"]))
                )
                matched = checks.matched_failures(
                    cast(bytes, observed["stdout"]),
                    cast(bytes, observed["stderr"]),
                    cast(list[str], matcher["evidence_pattern_ids"]),
                    patterns,
                )
                status = observed["status"]
                if matched and status == "passed":
                    status = "failed"
                receipt = vr.validate_check(
                    {
                        "format": vr.CHECK,
                        "input_id": input_id,
                        "candidate_id": None,
                        "materialization_id": None,
                        "attempt_id": attempt_id,
                        "command_id": identifier,
                        **{
                            key: observed[key]
                            for key in (
                                "command_sha256",
                                "argv",
                                "cwd",
                                "environment_provenance",
                                "source_before_sha256",
                                "source_after_sha256",
                                "started_at",
                                "finished_at",
                                "exit_code",
                                "process_facts",
                                "diagnostic",
                            )
                        },
                        "path_policy_ref": self.home.config["path_policy_ref"],
                        "environment_before_ref": environment_before,
                        "environment_after_ref": environment_after,
                        "product_refs": [layer_ref],
                        "head_sha": git_head(self.home.target),
                        "status": status,
                        "stdout_ref": self.home.retain_bytes(
                            cast(bytes, observed["stdout"]), ".stdout"
                        ),
                        "stderr_ref": self.home.retain_bytes(
                            cast(bytes, observed["stderr"]), ".stderr"
                        ),
                        "matched_failure_ids": matched,
                    }
                )
                results.append(
                    {
                        "receipt_ref": self.home.retain(receipt),
                        "layer_ref": layer_ref,
                        "beforeimages_ref": images_ref,
                        "hold": ownership["hold"],
                    }
                )
                prior_owned.update(cast(list[str], ownership["owned_paths"]))
                earlier_images.update(
                    {
                        path: observed["after"]["products"][path]
                        for path in ownership["owned_paths"]
                    }
                )
                if node == "probe" and observed["products"]["declared"]:
                    results[-1]["hold"] = "probe_product_write"
                if (
                    source_identity(cast(dict[str, object], observed["after"]))
                    != attempt["source_sha256"]
                    or ownership["hold"]
                ):
                    break
        artifact = {
            "format": "millrace-plus-cicd-command-batch/v2",
            "input_id": input_id,
            "attempt_id": attempt_id,
            "node": node,
            "results": results,
            "accepted_refs": [],
        }
        if not_started is not None:
            artifact["not_started"] = not_started
        write_exact(
            self.home.workspace / (attempt_id + ".batch.json"), canonical(artifact)
        )
        if not_started is not None:
            return _out("RECORDED" if node == "checks" else "UNCERTAIN")
        if node == "checks":
            return _out("RECORDED")
        raw = [
            vr.validate_check(self.home.read_ref(item["receipt_ref"]))
            for item in results
        ]
        if any(row["status"] == "infrastructure" for row in raw):
            return _out("INFRASTRUCTURE")
        if any(row["status"] in {"uncertain", "timeout"} for row in raw):
            return _out("UNCERTAIN")
        if node == "probe":
            return _out("EVIDENCE")
        reproduced = len(raw) == len(ids) and all(
            row["exit_code"] in matcher["failing_exit_codes"]
            and set(row["matched_failure_ids"]) == set(matcher["evidence_pattern_ids"])
            for row in raw
        )
        return _out("REPRODUCED" if reproduced else "UNREPRODUCED")

    def _guard_commands(
        self, state: dict[str, object], mine: dict[str, object], kind: str
    ) -> dict[str, object]:
        from . import cicd_v2_checks as checks

        attempt = self._attempt(state)
        if attempt["kind"] != kind:
            raise CicdV2Error("attempt_kind_mismatch")
        invocation, done = self._completion(attempt, kind)
        artifact = _object(
            _read(self.home.workspace / (str(attempt["attempt_id"]) + ".batch.json"))
        )
        if (
            artifact.get("attempt_id") != attempt["attempt_id"]
            or artifact.get("input_id") != state["input_id"]
        ):
            raise CicdV2Error("check_basis_mismatch")
        not_started = artifact.get("not_started")
        if not_started is not None:
            preparation_path = self.home.workspace / (
                str(attempt["attempt_id"]) + ".preparation.json"
            )
            preparation = _object(_read(preparation_path))
            if kind == "checks":
                ids = vr.verify_check_plan(
                    self.home.config, self.home.read_ref(state["check_plan_ref"])
                )["command_ids"]
            else:
                ids = self.home.config[
                    "reproduction" if kind == "reproduce" else "investigation"
                ]["command_ids" if kind == "reproduce" else "probe_command_ids"]
            prefix = [
                vr.validate_check(self.home.read_ref(item["receipt_ref"]))["command_id"]
                for item in artifact["results"]
            ]
            if (
                set(not_started)
                != {
                    "reason",
                    "invocation",
                    "preparation_ref",
                    "config_ref",
                    "ordered_command_ids",
                    "remaining_command_ids",
                }
                or not_started["reason"] not in {"target_busy", "state_busy"}
                or artifact.get("node") != kind
                or not_started["invocation"] != preparation["invocation"]
                or preparation["invocation"]["invocation_id"] != invocation["id"]
                or not_started["preparation_ref"]
                != self.home.file_ref(preparation_path)
                or not_started["config_ref"] != self.home.config_ref
                or not_started["ordered_command_ids"] != ids
                or prefix != ids[: len(prefix)]
                or len(prefix) >= len(ids)
                or not_started["remaining_command_ids"] != ids[len(prefix) :]
                or done.get("transport") != "completed"
                or done.get("classification") != "semantic"
            ):
                raise CicdV2Error("check_not_started_unproven")
        if attempt["status"] == "completed":
            saved = self.home.read_ref(attempt["artifact_ref"])
            if (
                saved.get("attempt_id") != attempt["attempt_id"]
                or saved.get("results") != artifact["results"]
                or saved.get("not_started") != not_started
            ):
                raise CicdV2Error("check_acceptance_changed")
            accepted = saved.get("accepted_refs", [])
            layers = saved.get("accepted_product_refs", [])
            if len(accepted) != len(artifact["results"]) or len(layers) != len(
                accepted
            ):
                return self._hold(state, "check_acceptance_incomplete")
            for ref in [*accepted, *layers]:
                self.home.resolve_ref(ref)
            if any(ref not in state["product_layer_refs"] for ref in layers):
                raise CicdV2Error("check_product_accounting_changed")
            receipts = [
                vr.validate_check(self.home.read_ref(item["receipt_ref"]))
                for item in artifact["results"]
            ]
            hold = None
        else:
            accepted, layers = [], []
            receipts = []
            hold = None
            by_id = {
                str(row["id"]): row
                for row in cast(list[dict[str, object]], self.home.config["commands"])
            }
            violations = [
                item["hold"] for item in artifact["results"] if item["hold"] is not None
            ]
            if any(
                vr.validate_check(self.home.read_ref(item["receipt_ref"]))[
                    "source_after_sha256"
                ]
                != attempt["source_sha256"]
                for item in artifact["results"]
            ):
                violations.append("check_source_write")
            if violations:
                # Still require actual nested process closure before completing the
                # attempt; the output remains observed and permanently unowned.
                for item in artifact["results"]:
                    receipt = vr.validate_check(self.home.read_ref(item["receipt_ref"]))
                    facts = receipt["process_facts"]
                    if (
                        not all(
                            facts[key]
                            for key in ("root_reaped", "stdout_eof", "stderr_eof")
                        )
                        or facts["group_state"] != "closed"
                    ):
                        if not_started is not None:
                            return self._hold(state, "check_writer_unknown")
                        raise CicdV2Error("check_writer_unknown")
                finished = attempt | {
                    "status": "completed",
                    "artifact_ref": self.home.retain(artifact),
                    "completion_ref": self.home.retain(
                        {"invocation": invocation, "completion": done}
                    ),
                    "reservation": attempt["reservation"]
                    | {
                        "core_invocation_id": invocation["id"],
                        "core_attempt_id": invocation["attempt"],
                    },
                }
                state = self._record_attempt(state, finished)
                reason = (
                    "noncheck_product_write"
                    if "probe_product_write" in violations
                    else str(violations[0])
                )
                self._hold(state, reason)
                return _out("UNCERTAIN" if kind == "checks" else "HOLD")
            for item in cast(list[dict[str, object]], artifact["results"]):
                receipt = vr.validate_check(self.home.read_ref(item["receipt_ref"]))
                command = by_id.get(str(receipt["command_id"]))
                if (
                    receipt["attempt_id"] != attempt["attempt_id"]
                    or receipt["source_before_sha256"] != attempt["source_sha256"]
                    or command is None
                    or receipt["command_sha256"]
                    != cc.execution_sha256(
                        cc.execution_of(command, policy=self.home.policy)
                    )
                    or receipt["argv"] != command["argv"]
                    or receipt["cwd"] != command["cwd"]
                    or receipt["path_policy_ref"] != self.home.config["path_policy_ref"]
                ):
                    raise CicdV2Error("check_basis_mismatch")
                facts = cast(dict[str, object], receipt["process_facts"])
                if (
                    not all(
                        facts[k] for k in ("root_reaped", "stdout_eof", "stderr_eof")
                    )
                    or facts["group_state"] != "closed"
                ):
                    if not_started is not None:
                        return self._hold(state, "check_writer_unknown")
                    raise CicdV2Error("check_writer_unknown")
                observed = cc.validate_products(self.home.read_ref(item["layer_ref"]))
                acceptance = checks.completion_acceptance(
                    subject_ref=attempt["subject_ref"],
                    command_id=str(receipt["command_id"]),
                    process_receipt_ref=item["receipt_ref"],
                    invocation_ref=self.home.retain(invocation),
                    completion_ref=self.home.retain(done),
                    guard_ref=self.home.retain(mine),
                )
                acceptance_ref = self.home.retain(acceptance)
                layer = observed | {
                    "status": "accepted",
                    "completion_ref": acceptance_ref,
                    "previous_ref": item["layer_ref"],
                }
                cc.validate_products(
                    layer,
                    policy=self.home.policy,
                    policy_ref=self.home.config["path_policy_ref"],
                    execution=by_id[str(receipt["command_id"])],
                )
                cc.validate_product_transition(
                    _read(self.home.resolve_ref(item["layer_ref"])),
                    item["layer_ref"],
                    layer,
                )
                layers.append(self.home.retain(layer))
                accepted.append(acceptance_ref)
                receipts.append(receipt)
                hold = hold or item["hold"]
            # Accounting never promotes a failed functional result to pass.
            artifact = artifact | {
                "accepted_refs": accepted,
                "accepted_product_refs": layers,
            }
            finished = attempt | {
                "reservation": cast(dict[str, object], attempt["reservation"])
                | {
                    "core_invocation_id": invocation["id"],
                    "core_attempt_id": invocation["attempt"],
                },
                "status": "completed",
                "completion_ref": self.home.retain(
                    {"invocation": invocation, "completion": done}
                ),
                "artifact_ref": self.home.retain(artifact),
            }
            state = self._record_attempt(
                state,
                finished,
                product_layer_refs=[
                    *cast(list[object], state["product_layer_refs"]),
                    *layers,
                ],
            )
        self._source(state, cast(str, attempt["source_sha256"]))
        if not_started is not None:
            self._hold(state, "command_not_started:" + not_started["reason"])
            return _out("UNCERTAIN" if kind == "checks" else "HOLD")
        if hold:
            return self._hold(state, str(hold))
        if any(
            row["kind"] == "cancel"
            and row["request_id"] not in state["applied_decision_ids"]
            for row in self.home.decisions(str(state["input_id"]))
        ):
            self._hold(state, "operator_decision_pending")
            return _out("UNCERTAIN" if kind == "checks" else "HOLD")
        if kind == "probe":
            return self._hold(state, "probe_evidence_requires_continuation")
        if kind == "reproduce":
            self._prepare_role(state, "investigate", mine)
            return _out(
                "OBSERVED"
                if done.get("outcome") == "REPRODUCED"
                else "NEEDS_INVESTIGATION"
            )
        plan = vr.verify_check_plan(
            self.home.config, self.home.read_ref(state["check_plan_ref"])
        )
        passed = (
            done.get("transport") == "completed"
            and done.get("classification") == "semantic"
            and [row["command_id"] for row in receipts] == plan["command_ids"]
            and all(
                row["status"] == "passed" and not row["matched_failure_ids"]
                for row in receipts
            )
        )
        if not passed:
            self._hold(state, "checks_failed", "held_repair")
            return _out("FAIL")
        roles = cast(dict[str, object], self.home.config["role_bindings"])
        plan_ref = self.home.retain(
            vr.validate_review_plan(
                {
                    "format": vr.REVIEW_PLAN,
                    "input_id": state["input_id"],
                    "candidate_id": None,
                    "basis_sha256": state["basis_sha256"],
                    "binding_ids": [
                        row["id"]
                        for row in cast(list[dict[str, object]], roles["review"])
                    ],
                    "evidence_refs": accepted,
                }
            )
        )
        state = self._save(state, review_plan_ref=plan_ref)
        self._prepare_role(state, "review", mine)
        return _out("PASS")

    def _guard_assessment(
        self, state: dict[str, object], mine: dict[str, object]
    ) -> dict[str, object]:
        attempt, artifact = self._role_artifact(state, "investigate")
        vr.validate_assessment(artifact)
        if artifact["subject_ref"] != self._input_ref(str(state["input_id"])):
            raise CicdV2Error("assessment_subject_mismatch")
        self._source(state, cast(str, attempt["source_sha256"]))
        role_preparation = _object(
            _read(self.home.workspace / (str(attempt["attempt_id"]) + ".role.json"))
        )
        if (
            self.home.observe()["products"]
            != self.home.read_ref(role_preparation["snapshot_ref"])["products"]
        ):
            return self._hold(state, "noncheck_product_write", "held_risk")
        for item in cast(list[object], artifact["evidence_refs"]):
            self.home.resolve_ref(item)
        state, finished = self._finish(state, attempt, "investigate", artifact)
        state = self._save(state, assessment_ref=finished["artifact_ref"])
        route = str(artifact["route"])
        if route == "NO_ACTION":
            self._save(
                state,
                settlement_ref=self.home.retain(self._report(state, None, "no_action")),
                phase="settle",
            )
            return _out("NO_ACTION")
        if route == "LAD_CANDIDATE":
            self._hold(state, "systemic_work_requires_factory", "lad_candidate")
            return _out(route)
        if route == "INVESTIGATE":
            ids = cast(dict[str, object], self.home.config["investigation"])[
                "probe_command_ids"
            ]
            if ids:
                self._save(state, phase="probe")
                return _out("PROBE_REQUIRED")
            return self._hold(state, "no_new_probe")
        if route != "REPAIR_ALLOWED":
            self._hold(state, "human_risk_decision", "held_risk")
            return _out("HUMAN_HOLD")
        reproduction_refs = []
        for earlier in self._attempts(state).values():
            if earlier["kind"] == "reproduce" and earlier["status"] == "completed":
                batch = self.home.read_ref(earlier["artifact_ref"])
                completion = self.home.read_ref(earlier["completion_ref"])
                if (
                    completion["completion"].get("outcome") == "REPRODUCED"
                    and earlier["source_sha256"] == attempt["source_sha256"]
                ):
                    reproduction_refs.extend(cast(list[object], batch["accepted_refs"]))
        if not reproduction_refs or artifact["reproduction_refs"] != reproduction_refs:
            return self._hold(state, "reproduction_not_proven", "held_unreproduced")
        if not any(
            item["status"] == "supported"
            for item in cast(list[dict[str, object]], artifact["hypotheses"])
        ):
            return self._hold(state, "supported_cause_required", "held_risk")
        paths = cast(list[str], artifact["proposed_paths"])
        if not paths or any(not self._path_allowed(path) for path in paths):
            return self._hold(state, "repair_scope_forbidden", "held_risk")
        if not self._classes_allowed(
            paths, cast(list[str], artifact["change_classes"])
        ):
            return self._hold(state, "protected_change_class", "held_risk")
        self._prepare_role(state, "repair", mine)
        return _out("REPAIR_ALLOWED")

    def _path_allowed(self, path: str) -> bool:
        def within(prefix: str) -> bool:
            return path == prefix or path.startswith(prefix + "/")

        return (
            not vr.structural_forbidden(path)
            and cc.classify(self.home.policy, path) == "source"
            and any(
                within(prefix)
                for prefix in cast(list[str], self.home.config["repair_paths"])
            )
            and any(within(prefix) for prefix in self.home.authority["repair_paths"])
            and not any(
                within(prefix)
                for prefix in cast(list[str], self.home.config["protected_paths"])
            )
        )

    def _classes_allowed(self, paths: list[str], classes: list[str]) -> bool:
        allowed = cast(dict[str, object], self.home.config["repair_policy"])[
            "allowed_change_classes"
        ]
        protected = self.home.config["protected_change_classes"]
        exceptions = cast(list[dict[str, object]], self.home.config["class_exceptions"])
        for kind in classes:
            if kind in protected:
                if not all(
                    any(
                        item["class"] == kind
                        and any(
                            path == prefix or path.startswith(prefix + "/")
                            for prefix in cast(list[str], item["paths"])
                        )
                        for item in exceptions
                    )
                    for path in paths
                ):
                    return False
            elif kind not in allowed:
                return False
        return True

    def _guard_patch(
        self, state: dict[str, object], mine: dict[str, object]
    ) -> dict[str, object]:
        from .cicd_candidates import retain_payloads

        attempt, artifact = self._role_artifact(state, "repair")
        vr.validate_repair(artifact)
        if artifact["assessment_ref"] != state["assessment_ref"]:
            raise CicdV2Error("repair_assessment_mismatch")
        snapshot = self._source(state)
        frozen_ref = _object(
            _read(self.home.workspace / (str(state["input_id"]) + ".freeze.json"))
        )
        frozen = self.home.read_ref(frozen_ref)
        before = cast(dict[str, object], frozen["snapshot"])
        old = cast(dict[str, dict[str, object]], before["source"])
        new = cast(dict[str, dict[str, object]], snapshot["source"])
        changed = sorted(
            path for path in set(old) | set(new) if old.get(path) != new.get(path)
        )
        assessment = vr.validate_assessment(self.home.read_ref(state["assessment_ref"]))
        paths = cast(list[str], assessment["proposed_paths"])
        if (
            not changed
            or any(
                not self._path_allowed(path)
                or not any(
                    path == prefix or path.startswith(prefix + "/") for prefix in paths
                )
                for path in changed
            )
            or snapshot["links"] != before["links"]
            or snapshot["products"]
            != self.home.read_ref(
                _object(
                    _read(
                        self.home.workspace
                        / (str(attempt["attempt_id"]) + ".role.json")
                    )
                )["snapshot_ref"]
            )["products"]
        ):
            return self._hold(state, "patch_scope_mismatch", "held_repair")
        payloads = retain_payloads(self.home.target, snapshot, self.home.workspace)
        binary = [
            path
            for path in changed
            if path in new and b"\0" in _read(self.home.resolve_ref(payloads[path]))
        ]
        detected = vr.detect_change_classes(
            {path: (old.get(path), new.get(path)) for path in changed},
            binary_paths=binary,
        )
        classes = vr.effective_change_classes(
            {kind for values in detected.values() for kind in values},
            cast(list[str], assessment["change_classes"]),
        )
        classes = vr.effective_change_classes(
            classes, cast(list[str], artifact["change_classes"])
        )
        if not self._classes_allowed(changed, classes):
            return self._hold(state, "protected_change_class", "held_risk")
        if (
            artifact["changed_paths"] != changed
            or artifact["source_before_sha256"] != attempt["source_sha256"]
            or artifact["source_after_sha256"] != source_identity(snapshot)
        ):
            raise CicdV2Error("repair_basis_mismatch")
        self.home.resolve_ref(artifact["patch_ref"])
        # Compare the full delta; a worker patch grants no authority.
        delta_ref = self.home.retain(
            {
                "before": old,
                "after": new,
                "payloads": payloads,
                "changed_paths": changed,
                "change_classes": classes,
            }
        )
        write_exact(
            self.home.workspace / (str(attempt["attempt_id"]) + ".observed-delta.json"),
            canonical(delta_ref),
        )
        caps = cast(
            list[dict[str, object]],
            cast(dict[str, object], self.home.config["repair_policy"])["optional_caps"],
        )
        for cap in caps:
            if cap["kind"] == "files" and len(changed) > cap["value"]:
                return self._hold(state, "selected_file_cap", "held_repair")
            if cap["kind"] == "cost":
                return self._hold(state, "spend_measurement_unavailable", "held_repair")
            if cap["kind"] == "changed_lines":
                import difflib

                count = 0
                for path in changed:
                    prior = (
                        b""
                        if path not in old
                        else _read(
                            self.home.resolve_ref(
                                cast(dict[str, object], frozen["payloads"])[path]
                            )
                        )
                    )
                    after = (
                        b""
                        if path not in new
                        else _read(self.home.resolve_ref(payloads[path]))
                    )
                    count += sum(
                        1
                        for line in difflib.ndiff(
                            prior.decode("utf-8", "replace").splitlines(),
                            after.decode("utf-8", "replace").splitlines(),
                        )
                        if line[:2] in {"- ", "+ "}
                    )
                if count > cap["value"]:
                    return self._hold(state, "selected_changed_line_cap", "held_repair")
        state, _ = self._finish(state, attempt, "repair", artifact)
        basis = vr.digest(
            {
                "source": source_identity(snapshot),
                "config": self.home.config_sha256,
                "assessment": state["assessment_ref"],
                "repair": artifact,
            }
        )
        selection = vr.select_checks(self.home.config, changed, uncertain=False)
        plan = vr.verify_check_plan(
            self.home.config,
            {
                "format": vr.CHECK_PLAN,
                "input_id": state["input_id"],
                "candidate_id": None,
                "basis_sha256": basis,
                "command_ids": selection["command_ids"],
                "selection_reason": selection["selection_reason"],
                "evidence_refs": [delta_ref],
            },
        )
        self._save(
            state,
            basis_sha256=basis,
            check_plan_ref=self.home.retain(plan),
            phase="checks",
        )
        return _out("SCOPED")

    def _guard_review(
        self, state: dict[str, object], mine: dict[str, object], reviewer: int
    ) -> dict[str, object]:
        attempt, artifact = self._role_artifact(state, "review")
        vr.validate_review(artifact)
        self._source(state, cast(str, attempt["source_sha256"]))
        role_preparation = _object(
            _read(self.home.workspace / (str(attempt["attempt_id"]) + ".role.json"))
        )
        if (
            self.home.observe()["products"]
            != self.home.read_ref(role_preparation["snapshot_ref"])["products"]
        ):
            self._hold(state, "noncheck_product_write", "held_risk")
            return _out("STALE")
        plan = vr.verify_review_plan(
            self.home.config, self.home.read_ref(state["review_plan_ref"])
        )
        if (
            artifact["candidate_sha256"] != attempt["source_sha256"]
            or artifact["checks_refs"] != plan["evidence_refs"]
        ):
            raise CicdV2Error("review_basis_mismatch")
        repairs = [
            row
            for row in self._attempts(state).values()
            if row["kind"] == "repair"
            and row["status"] == "completed"
            and self.home.read_ref(row["artifact_ref"]).get("source_after_sha256")
            == attempt["source_sha256"]
        ]
        if (
            not repairs
            or artifact["patch_ref"]
            != self.home.read_ref(repairs[-1]["artifact_ref"])["patch_ref"]
        ):
            raise CicdV2Error("review_patch_mismatch")
        state, finished = self._finish(state, attempt, f"review_{reviewer}", artifact)
        done = self.home.read_ref(finished["completion_ref"])["completion"]
        if done["outcome"] != artifact["verdict"]:
            raise CicdV2Error("review_outcome_mismatch")
        if artifact["verdict"] != "ACCEPT":
            self._hold(
                state, "review_" + str(artifact["verdict"]).lower(), "held_repair"
            )
            return _out(str(artifact["verdict"]))
        if reviewer < len(plan["binding_ids"]):
            self._prepare_role(state, "review", mine, reviewer=reviewer + 1)
        else:
            self._save(state, phase="guard_delivery")
        return _out("ACCEPT")

    def _guard_delivery(
        self, state: dict[str, object], mine: dict[str, object]
    ) -> dict[str, object]:
        """Recheck the exact post-patch checks and ordered review chain."""
        from . import cicd_effects as effects

        view = (
            self.home.observe()
            if state["delivery_refs"] and effects.is_local_git(self.home)
            else self._source(state)
        )
        source = source_identity(view)
        check_plan = vr.verify_check_plan(
            self.home.config, self.home.read_ref(state["check_plan_ref"])
        )
        review_plan = vr.verify_review_plan(
            self.home.config, self.home.read_ref(state["review_plan_ref"])
        )
        if (
            check_plan["basis_sha256"] != state["basis_sha256"]
            or review_plan["basis_sha256"] != state["basis_sha256"]
        ):
            raise CicdV2Error("delivery_basis_mismatch")
        attempts = list(self._attempts(state).values())
        matching = [
            row
            for row in attempts
            if row["kind"] == "checks"
            and row["status"] == "completed"
            and row["basis_sha256"] == state["basis_sha256"]
            and row["source_sha256"] == source
            and self.home.read_ref(row["artifact_ref"]).get("accepted_refs")
            == review_plan["evidence_refs"]
        ]
        if len(matching) != 1:
            raise CicdV2Error("delivery_checks_unproven")
        check_attempt = matching[0]
        batch = self.home.read_ref(check_attempt["artifact_ref"])
        if batch.get("accepted_refs") != review_plan["evidence_refs"]:
            raise CicdV2Error("delivery_checks_unproven")
        receipts = [
            vr.validate_check(self.home.read_ref(item["receipt_ref"]))
            for item in batch["results"]
        ]
        if [row["command_id"] for row in receipts] != check_plan["command_ids"] or any(
            row["status"] != "passed"
            or row["matched_failure_ids"]
            or row["source_before_sha256"] != source
            or row["source_after_sha256"] != source
            for row in receipts
        ):
            raise CicdV2Error("delivery_checks_unproven")
        _, done = self._completion(check_attempt, "checks")
        if (
            done.get("classification") != "semantic"
            or done.get("transport") != "completed"
        ):
            raise CicdV2Error("delivery_checks_unproven")
        previous = check_attempt["attempt_id"]
        by_id = {row["attempt_id"]: row for row in attempts}
        repair = by_id.get(check_attempt["parent_attempt_id"])
        while repair is not None and repair["kind"] != "repair":
            repair = by_id.get(repair["parent_attempt_id"])
        if repair is None or repair["status"] != "completed":
            raise CicdV2Error("delivery_patch_unproven")
        patch = vr.validate_repair(self.home.read_ref(repair["artifact_ref"]))
        self.home.resolve_ref(patch["patch_ref"])
        for index, binding in enumerate(review_plan["binding_ids"], 1):
            reviews = [
                row
                for row in attempts
                if row["kind"] == "review"
                and row["parent_attempt_id"] == previous
                and row["status"] == "completed"
                and row["source_sha256"] == source
                and row["basis_sha256"] == state["basis_sha256"]
            ]
            if len(reviews) != 1:
                raise CicdV2Error("delivery_reviews_unproven")
            attempt = reviews[0]
            prep = _object(
                _read(self.home.workspace / (str(attempt["attempt_id"]) + ".role.json"))
            )
            projection = self.home.read_ref(prep["pointer"]["projection_ref"])
            review = vr.validate_review(self.home.read_ref(attempt["artifact_ref"]))
            _, done = self._completion(attempt, f"review_{index}")
            if (
                projection["binding_id"] != binding
                or review["verdict"] != "ACCEPT"
                or done.get("outcome") != "ACCEPT"
                or done.get("classification") != "semantic"
                or done.get("transport") != "completed"
                or review["checks_refs"] != review_plan["evidence_refs"]
                or review["patch_ref"] != patch["patch_ref"]
                or review["candidate_sha256"] != source
            ):
                raise CicdV2Error("delivery_reviews_unproven")
            previous = attempt["attempt_id"]
        if state["current_attempt_id"] != previous:
            raise CicdV2Error("delivery_attempt_mismatch")
        if state["delivery_refs"]:
            return effects.guard(self, state, mine)
        if effects.selected(self.home):
            try:
                effects.freeze_basis(self, state, patch, mine)
            except (CicdError, CicdV2Error) as exc:
                return self._hold(state, exc.code, "held_repair")
        self._hold(state, "delivery_requires_operator", "checked_patch")
        return _out("HUMAN_REQUIRED")

    def _reconcile(
        self, state: dict[str, object], mine: dict[str, object]
    ) -> dict[str, object]:
        active_attempt = self._attempts(state).get(str(state["current_attempt_id"]))
        if (
            active_attempt is not None
            and active_attempt["kind"] in {"reproduce", "checks", "probe"}
            and active_attempt["status"] != "completed"
        ):
            batch = self.home.workspace / (
                str(active_attempt["attempt_id"]) + ".batch.json"
            )
            if not batch.exists():
                return self._hold(state, "nested_check_evidence_missing")
            return _out(
                {
                    "reproduce": "REPRO_RESULT",
                    "checks": "CHECK_RESULT",
                    "probe": "HOLD",
                }[active_attempt["kind"]]
            )
        if (
            active_attempt is not None
            and active_attempt["kind"] in {"investigate", "repair", "review"}
            and active_attempt["status"] != "completed"
        ):
            prep = _object(
                _read(
                    self.home.workspace
                    / (str(active_attempt["attempt_id"]) + ".role.json")
                )
            )
            role_node = (
                "review_" + str(prep["reviewer"])
                if active_attempt["kind"] == "review"
                else str(active_attempt["kind"])
            )
            try:
                _, completion = self._completion(active_attempt, role_node)
            except CicdV2Error as exc:
                if exc.code not in {
                    "writer_not_stopped",
                    "completion_missing_or_unknown",
                }:
                    raise
            else:
                if (
                    completion.get("transport") != "completed"
                    or completion.get("classification") != "semantic"
                ):
                    return self._hold(state, "role_execution_failed")
                try:
                    _, artifact = self._role_artifact(
                        state, str(active_attempt["kind"])
                    )
                    {
                        "investigate": vr.validate_assessment,
                        "repair": vr.validate_repair,
                        "review": vr.validate_review,
                    }[active_attempt["kind"]](artifact)
                except (
                    OSError,
                    CicdV2Error,
                    cc.CheckContractError,
                    IntakeError,
                ) as exc:
                    return self._hold(
                        state,
                        "role_artifact_invalid:"
                        + getattr(exc, "code", type(exc).__name__),
                    )
        if state["delivery_refs"]:
            from . import cicd_effects as effects

            state = effects.accept_observation(self, state)
            pending_stop = any(
                row["kind"] in {"close", "cancel"}
                and row["request_id"] not in state["applied_decision_ids"]
                for row in self.home.decisions(str(state["input_id"]))
            )
            _, operations = effects.history(self.home, state)
            possible = any(row["operation"] == "execute" for row in operations)
            choice = effects.disposition(self.home, state)
            if not pending_stop or (possible and choice not in {"complete", "execute"}):
                return effects.reconcile(self, state)
        plan_path = self.home.workspace / (str(state["input_id"]) + ".settlement.json")
        if plan_path.exists():
            plan = _object(_read(plan_path))
            self._settlement_basis(state, self.home.task(str(state["input_id"])), plan)
            return _out("SETTLE_READY")
        decisions = [
            row
            for row in self.home.decisions(str(state["input_id"]))
            if row["request_id"] not in state["applied_decision_ids"]
        ]
        if decisions:
            decision = decisions[0]
            if decision["kind"] in {"close", "cancel"}:
                if state["hold_ref"] is None and decision["kind"] == "close":
                    raise CicdV2Error("held_report_missing")
                report_ref = (
                    state["hold_ref"]
                    if decision["kind"] == "close"
                    else self.home.retain(self._report(state, None, "cancelled"))
                )
                self._save(state, settlement_ref=report_ref, phase="settle")
                return _out("SETTLE_READY")
            if decision["kind"] == "approve_delivery":
                from .cicd_effects import approve

                approve(self, state, decision)
                return _out("DELIVERY_READY")
            return self._continue(state, decision, mine)
        if state["settlement_ref"] is not None:
            return _out("SETTLE_READY")
        if state["hold_ref"] is not None:
            return _out("HOLD")
        if state["source_binding_ref"] is None:
            claim_path = self.home.workspace / (str(state["input_id"]) + ".claim.json")
            claim = _object(_read(claim_path))
            expected_state = _new_state(str(state["input_id"]))
            if (
                state != expected_state
                or claim["claim"] != self.home.task(str(state["input_id"]))["claim"]
                or claim["input_ref"] != self._input_ref(str(state["input_id"]))
            ):
                raise CicdV2Error("freeze_incomplete")
            prior_context = claim["invocation"]
            prior = self._run(str(prior_context["run_id"]))
            producers = [
                row
                for row in prior["invocations"]
                if row["id"] == prior_context["invocation_id"]
                and row["node"] == "claim"
            ]
            completed = [
                row
                for row in prior["completions"]
                if row["invocation_id"] == prior_context["invocation_id"]
            ]
            if (
                len(producers) != 1
                or len(completed) != 1
                or any(
                    producers[0].get(key) != prior_context[key]
                    for key in (
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
                or producers[0].get("writer_state") != "stopped"
                or completed[0].get("effective_writer") != "stopped"
                or completed[0].get("transport") != "completed"
                or completed[0].get("classification") != "semantic"
                or completed[0].get("outcome") != "CLAIMED"
            ):
                return self._hold(state, "claim_completion_unproven")
            if any(
                row["attempt"] > prior_context["attempt"] and row["node"] != "reconcile"
                for row in prior["invocations"]
            ):
                return self._hold(state, "freeze_dispatch_possible")
            if (
                self.home.workspace / (str(state["input_id"]) + ".freeze.json")
            ).exists():
                raise CicdV2Error("freeze_projection_incomplete")
            return _out("FREEZE_READY")
        current = self._attempts(state).get(str(state["current_attempt_id"]))
        if current is None:
            return _out("FROZEN")
        preparation = self._preparation(current)
        context = cast(dict[str, object], preparation["invocation"])
        view = self._run(str(context["run_id"]))
        rows = [
            row
            for row in cast(list[dict[str, object]], view["invocations"])
            if row["plane_id"] == context["plane_id"]
            and row["attempt"] == preparation["expected_attempt"]
            and row["node"]
            == (
                "review_"
                + str(
                    _object(
                        _read(
                            self.home.workspace
                            / (str(current["attempt_id"]) + ".role.json")
                        )
                    )["reviewer"]
                )
                if current["kind"] == "review"
                else current["kind"]
            )
        ]
        if not rows:
            if current["status"] != "reserved":
                return self._hold(state, "interrupted_attempt")
            prior = [
                row
                for row in cast(list[dict[str, object]], view["invocations"])
                if row["id"] == context["invocation_id"]
            ]
            completions = [
                row
                for row in cast(list[dict[str, object]], view["completions"])
                if row["invocation_id"] == context["invocation_id"]
            ]
            # Complete public history plus serial sequencing proves no next
            # native attempt began. Any intervening invocation other than this
            # reconcile means that simple proof no longer holds.
            after = [
                row
                for row in cast(list[dict[str, object]], view["invocations"])
                if row["attempt"] > context["attempt"]
                and row["id"] != mine["invocation_id"]
                and (row["node"] != "reconcile" or row.get("writer_state") != "stopped")
            ]
            if (
                len(prior) != 1
                or len(completions) != 1
                or after
                or prior[0].get("writer_state") != "stopped"
                or completions[0].get("effective_writer") != "stopped"
                or completions[0].get("classification") != "semantic"
                or completions[0].get("transport") != "completed"
            ):
                return self._hold(state, "prepared_dispatch_unknown")
            role_prep = _object(
                _read(self.home.workspace / (str(current["attempt_id"]) + ".role.json"))
            )
            if (
                _object(_read(self.home.workspace / "active-role.json"))
                != role_prep["pointer"]
            ):
                return self._hold(state, "role_projection_changed")
            recovered = dict(preparation) | {
                "invocation": mine,
                "expected_attempt": mine["attempt"] + 1,
                "previous_ref": self.home.retain(preparation),
                "never_started_view_ref": self.home.retain(view),
            }
            write_replace(
                self.home.workspace / (str(current["attempt_id"]) + ".dispatch.json"),
                canonical(self.home.retain(recovered)),
            )
            mapping = {"investigate": "INVESTIGATION_READY", "repair": "REPAIR_READY"}
            if current["kind"] == "review":
                return _out("REVIEW_READY_" + str(role_prep["reviewer"]))
            return _out(mapping[str(current["kind"])])
        self._completion(current, str(rows[0]["node"]))
        routes = {
            "reproduce": "REPRO_RESULT",
            "investigate": "INVESTIGATION_RESULT",
            "repair": "REPAIR_RESULT",
            "checks": "CHECK_RESULT",
        }
        if current["kind"] == "review":
            return _out("REVIEW_RESULT_" + str(rows[0]["node"]).rsplit("_", 1)[1])
        if current["kind"] == "probe":
            return self._hold(state, "probe_evidence_requires_continuation")
        return _out(routes[str(current["kind"])])

    def _continuation_basis(
        self, state: dict[str, object], decision: dict[str, object]
    ) -> Path:
        """Validate factual novelty without changing decisions or source state."""
        cause = self.home.read_ref(decision["changed_cause_ref"])
        target = str(decision["target_stage"])
        current = self._attempt(state)
        if current["status"] != "completed":
            raise CicdV2Error("prior_attempt_unaccounted")
        view = self._source(state)
        source = source_identity(view)
        refs = cast(list[object], cause.get("evidence_refs", []))
        facts = [cause] if "format" in cause else []
        facts += [self.home.read_ref(item) for item in refs]
        findings = set()
        known_records = set()
        checks = []
        for attempt in self._attempts(state).values():
            if attempt["status"] != "completed" or attempt["artifact_ref"] is None:
                continue
            artifact = self.home.read_ref(attempt["artifact_ref"])
            known_records.add(vr.digest(artifact))
            if artifact.get("format") == vr.REVIEW:
                findings.update(
                    str(item["id"])
                    for item in artifact["findings"]
                    if item["status"] == "unresolved"
                )
            for item in cast(list[dict[str, object]], artifact.get("results", [])):
                receipt = vr.validate_check(self.home.read_ref(item["receipt_ref"]))
                checks.append(receipt)
                known_records.add(vr.digest(receipt))
        selected_findings = findings & set(
            cast(list[str], cause.get("finding_ids", []))
        )
        environment_changed = False
        current_environment = self._environment(view)
        prior_environment = []
        for receipt in checks:
            if receipt["environment_after_ref"] is not None:
                prior_environment.append(
                    self.home.read_ref(receipt["environment_after_ref"])["entries"]
                )
        for fact in facts:
            if fact.get("format") == cc.ENVIRONMENT:
                cc.validate_environment(
                    fact,
                    policy=self.home.policy,
                    policy_ref=self.home.config["path_policy_ref"],
                )
                environment_changed |= (
                    fact["target_root"] == str(self.home.target)
                    and fact["entries"] == current_environment["entries"]
                    and bool(prior_environment)
                    and fact["entries"] != prior_environment[-1]
                )
        current_records = set()
        if current["artifact_ref"] is not None:
            artifact = self.home.read_ref(current["artifact_ref"])
            current_records.add(vr.digest(artifact))
            for item in artifact.get("results", []):
                current_records.add(vr.digest(self.home.read_ref(item["receipt_ref"])))
        new_evidence = current["kind"] != target and any(
            vr.digest(fact) in current_records for fact in facts
        )
        changed_source = source != current["source_sha256"]
        actionable = {
            "reproduce": environment_changed or changed_source,
            "investigate": new_evidence or environment_changed,
            "probe": new_evidence,
            "repair": bool(selected_findings),
            "checks": environment_changed or changed_source,
            "review": new_evidence
            and any(fact.get("format") == vr.CHECK for fact in facts),
        }[target]
        # Normalize factual identities, never wording, timestamps or diagnostics.
        normalized = sorted(
            (
                str(row["command_id"]),
                str(row["command_sha256"]),
                str(row["exit_code"]),
                tuple(sorted(row["matched_failure_ids"])),
            )
            for row in checks
        )
        fingerprint = vr.digest(
            {
                "stage": target,
                "source": source,
                "config": self.home.config_sha256,
                "checks": normalized,
                "findings": sorted(selected_findings),
                "environment": current_environment["entries"]
                if environment_changed
                else None,
            }
        )
        history = self.home.workspace / (
            str(state["input_id"]) + ".continuation-" + fingerprint + ".json"
        )
        if not actionable or history.exists():
            raise CicdV2Error("unchanged_cause")
        return history

    def _continue(
        self,
        state: dict[str, object],
        decision: dict[str, object],
        mine: dict[str, object],
    ) -> dict[str, object]:
        history = self._continuation_basis(state, decision)
        target = str(decision["target_stage"])
        write_exact(history, canonical(decision))
        state = self._save(
            state,
            hold_ref=None,
            applied_decision_ids=[
                *state["applied_decision_ids"],
                decision["request_id"],
            ],
        )
        task = self.home.task(str(state["input_id"]))
        self.home.intake.transition(
            str(task["task_id"]),
            version=cast(int, task["version"]),
            claim=cast(dict[str, object], task["claim"]),
            status="running",
        )
        if target in {"investigate", "repair", "review"}:
            self._prepare_role(
                state, target, mine, changed=decision["changed_cause_ref"]
            )
            return _out(
                {
                    "investigate": "INVESTIGATION_READY",
                    "repair": "REPAIR_READY",
                    "review": "REVIEW_READY_1",
                }[target]
            )
        self._save(state, phase=target)
        return _out(
            {"reproduce": "FROZEN", "probe": "PROBE_READY", "checks": "CHECK_READY"}[
                target
            ]
        )

    def _settle(
        self, state: dict[str, object], task: dict[str, object], mine: dict[str, object]
    ) -> dict[str, object]:
        self._stopped(state, mine)
        if any(
            attempt["status"] != "completed"
            for attempt in self._attempts(state).values()
        ):
            raise CicdV2Error("attempt_not_accounted")
        report_ref = state["settlement_ref"]
        if report_ref is None:
            raise CicdV2Error("settlement_missing")
        report = vr.validate_report(self.home.read_ref(report_ref))
        held = (
            None
            if state["hold_ref"] is None
            else vr.validate_report(self.home.read_ref(state["hold_ref"]))
        )
        if held is not None and held["hold_reason"] in {
            "noncheck_product_write",
            "foreign_product_overlap",
            "undeclared_output",
            "product_drift",
            "check_source_write",
        }:
            raise CicdV2Error("product_divergence_requires_disposition")
        pending = [
            row
            for row in self.home.decisions(str(state["input_id"]))
            if row["request_id"] not in state["applied_decision_ids"]
        ]
        plan_path = self.home.workspace / (str(state["input_id"]) + ".settlement.json")
        existing_plan = _object(_read(plan_path)) if plan_path.exists() else None
        selected = (
            existing_plan["decision"]
            if existing_plan is not None
            else (pending[0] if pending else None)
        )
        if report["status"] == "published":
            from .cicd_effects import publication_decision

            selected = publication_decision(self.home, state)
        from .cicd_effects import is_local_git, settlement_guard

        settlement_guard(self, state, mine, published=report["status"] == "published")
        if selected is not None and selected["kind"] not in {
            "close", "cancel", "approve_delivery"
        }:
            raise CicdV2Error("settlement_decision_mismatch")
        if selected is None and report["status"] != "no_action":
            raise CicdV2Error("settlement_authority_missing")
        if (
            selected is not None
            and selected["kind"] == "close"
            and selected["basis_sha256"] != report_ref["sha256"]
        ):
            raise CicdV2Error("settlement_basis_mismatch")
        # Pin authority and the original CAS basis before any target mutation.
        for key in ("patch_ref", "assessment_ref"):
            if report[key] is not None:
                self.home.resolve_ref(report[key])
        for key in ("checks_refs", "review_refs", "attempt_refs", "delivery_refs"):
            for ref in report[key]:
                self.home.resolve_ref(ref)
        if (
            report["input_id"] != state["input_id"]
            or report["config_sha256"] != self.home.config_sha256
            or report["attempt_refs"] != state["attempt_refs"]
            or report["assessment_ref"] != state["assessment_ref"]
            or report["delivery_refs"] != state["delivery_refs"]
        ):
            raise CicdV2Error("settlement_report_mismatch")
        if (
            selected is not None
            and selected["kind"] == "close"
            and report_ref != state["hold_ref"]
        ):
            raise CicdV2Error("settlement_basis_mismatch")
        if (
            selected is not None
            and selected["kind"] == "cancel"
            and report != self._report(state, None, "cancelled")
        ):
            raise CicdV2Error("settlement_report_mismatch")
        if selected is None:
            subject = self.home.input(str(state["input_id"]))
            original = self.home.read_ref(subject["original_ref"])
            no_action = subject["kind"] == "ci_event" and original["conclusion"] in {
                "success",
                "cancelled",
                "skipped",
            }
            if state["assessment_ref"] is not None:
                no_action |= (
                    vr.validate_assessment(self.home.read_ref(state["assessment_ref"]))[
                        "route"
                    ]
                    == "NO_ACTION"
                )
            if not no_action or report != self._report(state, None, "no_action"):
                raise CicdV2Error("settlement_origin_mismatch")
        latest = self.home.task(str(state["input_id"]))
        if latest["version"] != task["version"] or latest["claim"] != task["claim"]:
            raise CicdV2Error("stale_task")
        request = vr.digest(
            {"input": state["input_id"], "report": report_ref, "decision": selected}
        )[:32]
        terminal = (
            "cancelled"
            if selected is not None and selected["kind"] == "cancel"
            else (
                "succeeded"
                if report["status"] in {"no_action", "checked_patch", "published"}
                else "failed"
            )
        )
        snapshot = self.home.observe()
        git = self._cleanup_git(mine)
        self._cleanup_products_untracked(state, git["tracked_paths"])
        plan_path = self.home.workspace / (str(state["input_id"]) + ".settlement.json")
        if plan_path.exists():
            plan = _object(_read(plan_path))
        else:
            plan = {
                "input_id": state["input_id"],
                "task_id": task["task_id"],
                "task_version": task["version"],
                "claim": task["claim"],
                "report_ref": report_ref,
                "decision": selected,
                "request_id": request,
                "intent": {
                    "task_id": task["task_id"],
                    "decision": "settle",
                    "evidence": report_ref,
                },
                "terminal": terminal,
                "origin_state_ref": self.home.retain(state),
                "source_sha256": source_identity(snapshot),
                "head": git["head"],
                "index_sha256": git["index_sha256"],
                "products_before": snapshot["products"],
            }
            write_exact(plan_path, canonical(plan), "settlement_plan_conflict")
        self._settlement_basis(state, task, plan)
        self._cleanup_unchanged(plan, snapshot, git)
        state = self._reverse_settlement_products(state, plan)
        self._settlement_basis(state, self.home.task(str(state["input_id"])), plan)
        self._settlement_boundary(state, plan)
        self._cleanup_unchanged(plan, self.home.observe(), self._cleanup_git(mine))
        if report["status"] == "published" and is_local_git(self.home):
            settlement_guard(self, state, mine, published=True)
        self.home.intake.decide(
            plan["request_id"],
            plan["intent"],
            check=lambda _: None,
            terminal_status=plan["terminal"],
            expected_version=plan["task_version"],
            expected_claim=plan["claim"],
        )
        self._project_settlement(state, self.home.task(str(state["input_id"])), plan)
        return _out("SETTLED")

    def _cleanup_git(self, mine):
        import time

        def read(args):
            remaining = mine["deadline"] - time.time()
            if remaining <= 0:
                raise CicdV2Error("invocation_deadline_expired")
            return _git_read(self.home.target, args, remaining)

        head = git_head(self.home.target)
        index = read(["ls-files", "--stage", "-z"])
        tracked = read(["ls-files", "-z"]) + read(
            ["ls-tree", "-r", "--name-only", "-z", head]
        )
        try:
            paths = sorted(
                {path.decode("utf-8") for path in tracked.split(b"\0") if path}
            )
        except UnicodeDecodeError as exc:
            raise CicdV2Error("cleanup_git_path_unknown") from exc
        return {"head": head, "index_sha256": _sha(index), "tracked_paths": paths}

    def _cleanup_products_untracked(self, state, tracked):
        for ref in state["product_layer_refs"]:
            layer = cc.validate_products(self.home.read_ref(ref))
            if any(
                path == source
                or path.startswith(source + "/")
                or source.startswith(path + "/")
                for path in layer["owned_paths"]
                for source in tracked
            ):
                raise CicdV2Error("owned_product_now_tracked")

    @staticmethod
    def _cleanup_unchanged(plan, snapshot, git):
        if (
            source_identity(snapshot) != plan["source_sha256"]
            or git["head"] != plan["head"]
            or git["index_sha256"] != plan["index_sha256"]
        ):
            raise CicdV2Error("settlement_source_changed")

    def _settlement_basis(self, state, task, plan, *, terminal=False):
        origin = self.home.read_ref(plan["origin_state_ref"])
        report = vr.validate_report(self.home.read_ref(plan["report_ref"]))
        decision = plan["decision"]
        if (
            plan["input_id"] != state["input_id"]
            or task["task_id"] != plan["task_id"]
            or origin["input_id"] != state["input_id"]
            or state["settlement_ref"] != plan["report_ref"]
            or origin["settlement_ref"] != plan["report_ref"]
            or task["claim"] != plan["claim"]
            or report["input_id"] != state["input_id"]
            or report["config_sha256"] != self.home.config_sha256
            or report["attempt_refs"] != state["attempt_refs"]
            or origin["attempt_refs"] != state["attempt_refs"]
            or report["delivery_refs"] != state["delivery_refs"]
            or plan["intent"]
            != {
                "task_id": task["task_id"],
                "decision": "settle",
                "evidence": plan["report_ref"],
            }
            or plan["request_id"]
            != vr.digest(
                {
                    "input": state["input_id"],
                    "report": plan["report_ref"],
                    "decision": decision,
                }
            )[:32]
        ):
            raise CicdV2Error("settlement_plan_mismatch")
        expected = (
            "cancelled"
            if decision is not None and decision["kind"] == "cancel"
            else (
                "succeeded"
                if report["status"] in {"no_action", "checked_patch", "published"}
                else "failed"
            )
        )
        if plan["terminal"] != expected:
            raise CicdV2Error("settlement_origin_mismatch")
        if decision is not None:
            saved = [
                row
                for row in self.home.decisions(str(state["input_id"]))
                if row["request_id"] == decision["request_id"]
            ]
            if saved != [decision] or decision["kind"] not in {
                "close", "cancel", "approve_delivery"
            }:
                raise CicdV2Error("settlement_decision_mismatch")
            if decision["kind"] == "close" and (
                state["hold_ref"] != plan["report_ref"]
                or decision["basis_sha256"] != plan["report_ref"]["sha256"]
            ):
                raise CicdV2Error("settlement_basis_mismatch")
        if report["status"] == "published":
            from .cicd_effects import disposition, publication_decision

            if (decision != publication_decision(self.home, state)
                    or disposition(self.home, state) != "complete"):
                raise CicdV2Error("settlement_delivery_mismatch")
        for key in ("patch_ref", "assessment_ref"):
            if report[key] is not None:
                self.home.resolve_ref(report[key])
        for key in ("checks_refs", "review_refs", "attempt_refs", "delivery_refs"):
            for ref in report[key]:
                self.home.resolve_ref(ref)
        if terminal:
            receipt = self.home.intake.decision(plan["request_id"])
            if (
                receipt is None
                or receipt["intent"] != plan["intent"]
                or receipt["receipt"] != task["terminal_receipt"]
                or task["status"] != plan["terminal"]
                or task["version"] != plan["task_version"] + 1
            ):
                raise CicdV2Error("settlement_receipt_mismatch")
        elif task["version"] != plan["task_version"] or task["status"] in TERMINAL:
            raise CicdV2Error("stale_task")

    def _settlement_layers(self, state, plan):
        original = self.home.read_ref(plan["origin_state_ref"])["product_layer_refs"]
        if len(original) != len(state["product_layer_refs"]):
            raise CicdV2Error("product_accounting_incomplete")
        result, suffix = [], False
        for accepted_ref, current_ref in zip(
            original, state["product_layer_refs"], strict=True
        ):
            accepted = cc.validate_products(self.home.read_ref(accepted_ref))
            if accepted["status"] != "accepted":
                raise CicdV2Error("product_accounting_incomplete")
            restored = accepted | {"status": "restored", "previous_ref": accepted_ref}
            raw = canonical(restored)
            restored_ref = cc.make_ref(_sha(raw) + ".json", raw)
            if current_ref == accepted_ref and not suffix:
                done = False
            elif current_ref == restored_ref:
                if self.home.read_ref(current_ref) != restored:
                    raise CicdV2Error("product_restoration_changed")
                done = suffix = True
            else:
                raise CicdV2Error("product_restoration_order")
            result.append((accepted_ref, accepted, restored_ref, restored, done))
        return result

    def _settlement_boundary(self, state, plan, *, partial=None):
        rows = self._settlement_layers(state, plan)
        expected = dict(
            plan["products_before"]
            if not rows
            else self.home.read_ref(rows[0][1]["before_ref"])["products"]
        )
        for _, layer, _, _, done in rows:
            if not done:
                after = self.home.read_ref(layer["after_ref"])["products"]
                for path in layer["owned_paths"]:
                    expected[path] = after[path]
        current = self.home.observe()["products"]
        before = (
            {}
            if partial is None
            else self.home.read_ref(partial["before_ref"])["products"]
        )
        paths = set() if partial is None else set(partial["owned_paths"])
        for path in set(expected) | set(current):
            if current.get(path) != expected.get(path) and not (
                path in paths and current.get(path) == before.get(path)
            ):
                raise CicdV2Error("product_drift")

    def _reverse_settlement_products(self, state, plan):
        from . import cicd_v2_checks as checks

        while True:
            rows = self._settlement_layers(state, plan)
            outstanding = [index for index, row in enumerate(rows) if not row[4]]
            if not outstanding:
                self._settlement_boundary(state, plan)
                return state
            index = outstanding[-1]
            ref, layer, restored_ref, restored, _ = rows[index]
            # Probe the exact deterministic record without publishing evidence.
            if os.path.lexists(self.home.workspace / restored_ref["path"]):
                if self.home.read_ref(restored_ref) != restored:
                    raise CicdV2Error("product_restoration_changed")
                refs = list(state["product_layer_refs"])
                refs[index] = restored_ref
                candidate = state | {"product_layer_refs": refs}
                self._settlement_boundary(candidate, plan)
                state = self._save(state, product_layer_refs=refs)
                continue
            self._settlement_boundary(state, plan, partial=layer)
            before = self.home.read_ref(layer["before_ref"])
            after = self.home.read_ref(layer["after_ref"])
            images = {}
            observed = self.home.read_ref(layer["previous_ref"])
            reserved_ref = observed["previous_ref"]
            for attempt in self._attempts(state).values():
                path = self.home.workspace / (
                    str(attempt["attempt_id"])
                    + "-"
                    + vr.digest(layer["command_id"])
                    + ".command.json"
                )
                if path.exists():
                    intent = _object(_read(path))
                    if (
                        intent["layer_ref"] == reserved_ref
                        and self.home.read_ref(reserved_ref)["before_ref"]
                        == layer["before_ref"]
                    ):
                        images = {
                            name: _read(self.home.resolve_ref(image))
                            for name, image in self.home.read_ref(
                                intent["beforeimages_ref"]
                            ).items()
                        }
            result = checks.reverse_products(
                self.home.target,
                layer["owned_paths"],
                after["products"],
                before["products"],
                self.home.observe()["products"],
                images,
                allow_already_reversed=True,
            )
            if result["status"] != "reversed":
                raise CicdV2Error(str(result["reason"]))
            cc.validate_product_transition(
                _read(self.home.resolve_ref(ref)), ref, restored
            )
            self.home.retain(restored)
            refs = list(state["product_layer_refs"])
            refs[index] = restored_ref
            state = self._save(state, product_layer_refs=refs)

    def _project_settlement(self, state, task, plan):
        self._settlement_basis(state, task, plan, terminal=True)
        if any(not row[4] for row in self._settlement_layers(state, plan)):
            raise CicdV2Error("product_restoration_incomplete")
        applied = list(state["applied_decision_ids"])
        if (
            plan["decision"] is not None
            and plan["decision"]["request_id"] not in applied
        ):
            state = self._save(
                state, applied_decision_ids=[*applied, plan["decision"]["request_id"]]
            )
        plan_ref = self.home.file_ref(
            self.home.workspace / (str(state["input_id"]) + ".settlement.json")
        )
        for decision in self.home.decisions(str(state["input_id"])):
            if decision["request_id"] in state["applied_decision_ids"]:
                continue
            if decision["kind"] != "cancel":
                raise CicdV2Error("settlement_decision_mismatch")
            key = vr.digest(decision["request_id"])[:32]
            disposition = {
                "status": "superseded_by_settlement",
                "input_id": state["input_id"],
                "decision_ref": self.home.file_ref(
                    self.home.workspace / (key + ".decision.json")
                ),
                "settlement_plan_ref": plan_ref,
                "settlement_request_id": plan["request_id"],
                "terminal_receipt": task["terminal_receipt"],
            }
            write_exact(
                self.home.workspace
                / (str(state["input_id"]) + "-" + key + ".decision-disposition.json"),
                canonical(self.home.retain(disposition)),
                "decision_disposition_changed",
            )

    def _finish_settlements(self):
        # Writer recovery of exact OLD terminal inputs; no target write or CAS.
        for path in sorted(self.home.workspace.glob("*.settlement.json")):
            plan = _object(_read(path))
            task = self.home.task(str(plan["input_id"]))
            if task["status"] in TERMINAL:
                self._project_settlement(self._state(str(plan["input_id"])), task, plan)


def _capture_refs(value: object, source: Path, destination: Path) -> None:
    """Capture explicit refs, retaining their exact relative identity and bytes."""
    if isinstance(value, dict):
        if set(value) == {"path", "sha256"}:
            item = cc.validate_ref(value)
            path = source / str(item["path"])
            raw = _read(path)
            if _sha(raw) != item["sha256"]:
                raise CicdV2Error("asset_changed")
            write_exact(destination / str(item["path"]), raw, "asset_conflict")
        else:
            for key, child in value.items():
                if key != "local_authority_ref":
                    _capture_refs(child, source, destination)
    elif isinstance(value, list):
        for child in value:
            _capture_refs(child, source, destination)


def prepare(
    home: Path, target: Path, config_file: Path, state_home: Path | None = None
) -> dict[str, object]:
    """Prepare bootstrap metadata/claim; suggested authority remains unselected.

    This operation starts no Core instance or writer and mutates no repository
    source/index. ``initialize`` must explicitly select exact operator authority.
    """
    import shutil
    import sys
    import uuid

    from .cicd_source import OWNER, claim_target, overlap

    home = canonical_dir(str(home.absolute()), "invalid_workflow_home", directory=False)
    target = canonical_dir(str(target.absolute()), "invalid_target")
    if (home / "cicd.json").exists():
        raise CicdV2Error("versioned_home_conflict")
    state_home = canonical_dir(
        str(state_home or home / "state"), "invalid_state_home", directory=False
    )
    if (
        overlap(home, target)
        or overlap(target, state_home)
        or overlap(state_home, home / "project")
        or overlap(state_home, home / "workflow")
    ):
        raise CicdV2Error("target_overlap")
    authored_raw = _read(config_file.absolute())
    authored = _object(authored_raw)
    config = copy.deepcopy(authored)
    if config.get("target_root") != str(target):
        raise CicdV2Error("target_mismatch")
    # Authority is excluded from graph derivation and the preparation identity.
    config["local_authority_ref"] = cc.make_ref("pending-authority.json", b"unapproved")
    for command in cast(list[dict[str, object]], config["commands"]):
        resolved = cc.resolve_timeout(
            command["timeout_seconds"], command["timeout_policy"]
        )
        command.update(
            {key: resolved[key] for key in ("timeout_seconds", "timeout_policy")}
        )
    vr.validate_config(config)
    _guard_values(config)
    _validate_repair_profile(config, config_file.absolute().parent)
    template_root = Path(__file__).parent / "workflows" / "cicd-v2"
    template = _object(_read(template_root / "workflow.json"))
    graph = derive_graph(template, config)
    if config["source_policy"]["mode"] == "factory":
        graph["control_files"] = sorted(
            {*graph["control_files"], "schemas/factory-input.json"}
        )
    wait_node = cast(
        dict[str, object],
        cast(dict[str, dict[str, object]], graph["planes"])["main"]["nodes"][
            "wait_event"
        ],
    )
    timeout = cc.resolve_timeout(
        cast(dict[str, object], wait_node["invoke"]).get("timeout_seconds"), None
    )
    idle_policy = vr.idle_wait_policy(
        config, timeout_seconds=cast(float, timeout["timeout_seconds"])
    )
    inputs_sha = vr.digest(
        {
            "config": config,
            "graph": graph,
            "state_home": str(state_home),
            "target": str(target),
        }
    )
    if home.exists() and (home / "preparation.json").exists():
        previous = _object(_read(home / "preparation.json"))
        if previous.get("inputs_sha256") != inputs_sha or previous.get(
            "target_identity"
        ) != filesystem_identity(target):
            raise CicdV2Error("preparation_conflict")
        _verify_preparation(home, previous)
        claim_target(
            target,
            cast(dict[str, object], previous["target_identity"]),
            home,
            str(previous["service_id"]),
        )
        return previous
    if home.exists() and any(home.iterdir()):
        raise CicdV2Error("workflow_home_not_empty")
    home.mkdir(mode=0o700, exist_ok=True)
    workspace = home / "project" / "main"
    workspace.mkdir(parents=True)
    workflow = home / "workflow"
    shutil.copytree(template_root, workflow)
    if config["source_policy"]["mode"] == "factory":
        factory_schema = _read(
            template_root.parent / "software-factory/schemas/input.json"
        )
        write_exact(workflow / "schemas/factory-input.json", factory_schema)
        write_exact(workspace / "schemas/factory-input.json", factory_schema)
    _capture_refs(config, config_file.absolute().parent, workspace)
    policy = cc.validate_path_policy(
        _object(_read(workspace / str(config["path_policy_ref"]["path"])))
    )
    vr.validate_config(config, policy=policy)
    for relative in cast(list[str], graph["control_files"]):
        if relative == "scripts/step.py":
            continue
        captured = workspace / relative
        if captured.exists():
            write_exact(workflow / relative, _read(captured), "control_conflict")
        elif not (workflow / relative).is_file():
            raise CicdV2Error("missing_control_asset")
        else:
            write_exact(
                workspace / relative, _read(workflow / relative), "control_conflict"
            )
    definition_raw = canonical(graph)
    write_replace(workflow / "workflow.json", definition_raw)
    write_exact(workspace / "definition.json", definition_raw)
    service_id = uuid.uuid4().hex
    target_identity = filesystem_identity(target)
    head = git_head(target)
    claim_target(target, target_identity, home, service_id)
    owner_raw = _read(target / OWNER)
    write_exact(workspace / "owner.json", owner_raw)
    owner_ref = cc.make_ref("owner.json", owner_raw)
    definition_ref = cc.make_ref("definition.json", definition_raw)
    settings = {
        "path": list(
            dict.fromkeys([str(Path(sys.executable).parent), *os.get_exec_path()])
        ),
        "env_allowlist": ["HOME"],
        "host_paths": {"cicd_target": str(target)},
    }
    create_request = uuid.uuid5(uuid.UUID(service_id), "create-v2").hex
    operations = [
        *vr.CANDIDATE_OPERATIONS,
        *[str(row["id"]) for row in config["commands"]],
        str(config["role_bindings"]["investigate"]["id"]),
        str(config["role_bindings"]["repair"]["id"]),
        *[str(row["id"]) for row in config["role_bindings"]["review"]],
    ]
    suggested = vr.validate_local_authority(
        {
            "format": vr.LOCAL_AUTHORITY,
            "issuer": "UNAPPROVED preparation suggestion",
            "target_root": str(target),
            "owner_ref": owner_ref,
            "actors": [
                {
                    "execution_home": str(home),
                    "instance_id": None,
                    "definition_ref": definition_ref,
                }
            ],
            "operations": list(dict.fromkeys(operations)),
            "repair_paths": config["repair_paths"],
            "path_policy_ref": config["path_policy_ref"],
            "origin": (
                "UNAPPROVED bootstrap suggestion; explicit operator selection required"
            ),
            "override": (
                "select an exact authority file at init; no automatic selection"
            ),
        }
    )
    write_exact(workspace / "UNAPPROVED-authority.json", canonical(suggested))
    config.pop("local_authority_ref")
    write_exact(workspace / "prepared-config.json", canonical(config))
    control_hashes = {
        name: _sha(_read(workflow / name))
        for name in cast(list[str], graph["control_files"])
    }
    manifest = {
        "format": "millrace-plus-cicd-preparation/v1",
        "service_id": service_id,
        "service_home": str(home),
        "inputs_sha256": inputs_sha,
        "target_root": str(target),
        "target_identity": target_identity,
        "head": head,
        "owner_ref": owner_ref,
        "definition_ref": definition_ref,
        "state_home": str(state_home),
        "create_request_id": create_request,
        "settings": settings,
        "idle_wait_policy": idle_policy,
        "prepared_config_ref": cc.make_ref("prepared-config.json", canonical(config)),
        "control_hashes": control_hashes,
        "suggested_authority_file": str(workspace / "UNAPPROVED-authority.json"),
        "authority_status": "UNAPPROVED",
    }
    write_exact(home / "preparation.json", canonical(manifest))
    _verify_preparation(home, manifest)
    return manifest


def _verify_preparation(home: Path, manifest: dict[str, object]) -> None:
    from .cicd_source import OWNER

    if manifest.get("service_home") != str(home):
        raise CicdV2Error("preparation_home_mismatch")
    workspace = home / "project" / "main"
    for key in ("owner_ref", "definition_ref", "prepared_config_ref"):
        item = cc.validate_ref(manifest[key])
        if _sha(_read(workspace / str(item["path"]))) != item["sha256"]:
            raise CicdV2Error("preparation_changed")
    if _read(Path(str(manifest["target_root"])) / OWNER) != _read(
        workspace / str(manifest["owner_ref"]["path"])
    ):
        raise CicdV2Error("target_owner_changed")
    if (
        filesystem_identity(Path(str(manifest["target_root"])))
        != manifest["target_identity"]
    ):
        raise CicdV2Error("target_identity_changed")
    for name, digest in cast(dict[str, str], manifest["control_hashes"]).items():
        if _sha(_read(home / "workflow" / name)) != digest:
            raise CicdV2Error("prepared_control_changed")
    if _read(home / "workflow" / "workflow.json") != _read(
        workspace / "definition.json"
    ):
        raise CicdV2Error("prepared_definition_changed")
    config = _object(_read(workspace / "prepared-config.json"))
    _capture_refs(
        config, workspace, workspace
    )  # exact replay only; no changed bytes accepted


def initialize(
    home: Path,
    target: Path,
    config_file: Path,
    state_home: Path | None = None,
    *,
    authority_file: Path | None = None,
    catalog_home: Path | None = None,
) -> dict[str, object]:
    """Select exact bootstrap authority, create once and bind its actual actor."""
    from .router import control

    manifest = prepare(home, target, config_file, state_home)
    home = Path(str(manifest["service_home"]))
    workspace = home / "project" / "main"
    if authority_file is None:
        authored = _object(_read(config_file.absolute()))
        selected = authored.get("local_authority_ref")
        if selected is None:
            raise CicdV2Error("authority_selection_required")
        item = cc.validate_ref(selected)
        authority_file = config_file.absolute().parent / str(item["path"])
        if _sha(_read(authority_file)) != item["sha256"]:
            raise CicdV2Error("authority_changed")
    raw = _read(authority_file.absolute())
    authority = vr.validate_local_authority(_object(raw))
    expected_actor = {
        "execution_home": str(home),
        "instance_id": None,
        "definition_ref": manifest["definition_ref"],
    }
    if (
        authority["target_root"] != manifest["target_root"]
        or authority["owner_ref"] != manifest["owner_ref"]
        or authority["path_policy_ref"]
        != _object(_read(workspace / "prepared-config.json"))["path_policy_ref"]
        or expected_actor not in authority["actors"]
    ):
        raise CicdV2Error("authority_preparation_mismatch")
    authority_ref = cc.make_ref("selected-authority.json", raw)
    write_exact(
        workspace / "selected-authority.json", raw, "authority_selection_conflict"
    )
    config = _object(_read(workspace / "prepared-config.json")) | {
        "local_authority_ref": authority_ref
    }
    vr.validate_config(config)
    write_exact(workspace / "config.json", canonical(config), "initialization_conflict")
    record = {
        "format": SERVICE,
        "service_home": str(home),
        "service_id": manifest["service_id"],
        "config_ref": cc.make_ref("config.json", canonical(config)),
        "target_identity": manifest["target_identity"],
        "state_home": manifest["state_home"],
        "definition_ref": manifest["definition_ref"],
        "owner_ref": manifest["owner_ref"],
        "create_request_id": manifest["create_request_id"],
        "idle_wait_policy": manifest["idle_wait_policy"],
    }
    write_exact(home / "cicd-v2.json", canonical(record), "initialization_conflict")
    current = V2Home(home)
    if not current.intake.path.exists():
        current.intake.initialize()
    else:
        current.intake.tasks()  # validate the existing bound store on replay
    with current.lock(), current.state_lock():
        _verify_preparation(home, manifest)
        settings = cast(dict[str, object], manifest["settings"])
        receipt = control.create_instance(
            home / "workflow" / "workflow.json",
            home / "project",
            state_home=Path(str(manifest["state_home"])),
            request_id=str(manifest["create_request_id"]),
            expected_state_version=0,
            path=tuple(settings["path"]),
            env_allowlist=tuple(settings["env_allowlist"]),
            host_paths=settings["host_paths"],
            catalog_home=catalog_home,
        )
        if receipt.get("status") != "applied":
            raise CicdV2Error(str(receipt.get("code") or "instance_create_refused"))
        receipt_raw = canonical(receipt)
        write_exact(
            workspace / "create-receipt.json", receipt_raw, "initialization_conflict"
        )
        binding = vr.validate_actor_binding(
            {
                "format": vr.ACTOR_BINDING,
                "authority_ref": authority_ref,
                "execution_home": str(home),
                "instance_id": receipt["instance_id"],
                "definition_ref": manifest["definition_ref"],
                "create_request_id": manifest["create_request_id"],
                "create_receipt_ref": cc.make_ref("create-receipt.json", receipt_raw),
            },
        )
        vr.verify_actor_binding(authority, binding)
        write_exact(
            workspace / "actor-binding.json",
            canonical(binding),
            "actor_binding_conflict",
        )
        validation = control.validate_revision(
            str(receipt["instance_id"]), state_home=Path(str(manifest["state_home"]))
        )
        if validation.get("valid") is not True or not isinstance(
            validation.get("revision_id"), str
        ):
            raise CicdV2Error("captured_revision_invalid")
        if (home / "execution.json").exists():
            previous_execution = _object(_read(home / "execution.json"))
            validation_ref = previous_execution["validation_ref"]
            retained_validation = current.read_ref(validation_ref)
            if (
                retained_validation.get("valid") is not True
                or retained_validation["draft_revision"]["files"]
                != validation["draft_revision"]["files"]
            ):
                raise CicdV2Error("captured_revision_changed")
        else:
            validation_ref = current.retain(validation)
        execution = {
            "format": EXECUTION,
            "execution_home": str(home),
            "instance_id": receipt["instance_id"],
            "state_home": manifest["state_home"],
            "project_root": str(home / "project"),
            "host_paths": settings["host_paths"],
            "control_hashes": manifest["control_hashes"],
            "poll_seconds": manifest["idle_wait_policy"]["poll_seconds"],
            "validated_draft_id": current.read_ref(validation_ref)["revision_id"],
            "validation_ref": validation_ref,
        }
        write_exact(
            home / "execution.json", canonical(execution), "actor_binding_conflict"
        )
    return {
        "service_id": current.service_id,
        "instance_id": receipt["instance_id"],
        "workflow_home": str(home),
    }


def step(node: str, controls: Path) -> dict[str, object]:
    """Execute one deterministic node from its pinned invocation controls."""
    workspace = Path.cwd()
    home = V2Home(workspace.parent.parent)
    if workspace != home.workspace:
        raise CicdV2Error("invalid_workspace")
    return Runtime(home, controls).node(node)
