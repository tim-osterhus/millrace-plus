"""L-Q1 engineering graph commands for a captured, explicitly enabled LAD copy.

Operations: `phase baseline|candidate` sequences one phase; `check PHASE D` runs
one captured check (or records why it did not run); `accept PHASE D` is the
check's distinct guard; `admit` compares the delta with artifact admission;
`finalize` writes the acceptance projection after Arbiter CLOSE.

Authority: Core's graph gives each check node one successor, its guard, and
routes only after a durable completion with a stopped writer. The guard also
needs this phase's exact reservation, the check's raw observed receipt and a
different Core invocation root. The check never certifies its own invocation.
Record logic is Plus `millrace_plus.engineering_feedback`; disabled copies
never import this module.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import time
from pathlib import Path

from lad_state import (
    LadError,
    _outside_source_at,
    _source_at,
    _source_context_at,
    assert_foreground,
    canonical,
    digest,
    emit,
    load,
    mark,
    read,
    roots,
    run,
    save,
    scope,
    shared_target,
)

STATE = "lad-state/engineering/state.json"
SESSION = "lad-state/engineering.json"
RECORDS = "lad-state/engineering/records"
OUTPUTS = "lad-state/engineering/outputs"
RUNS = "lad-state/engineering/runs"
OCCURRENCES = "lad-state/engineering/occurrences"
STATE_FORMAT = "millrace-lad-engineering-state/v1"
SESSION_FORMAT = "millrace-lad-engineering-session/v1"
ENTRIES = {
    "baseline": {"prepare_builder"},
    "candidate": {"prepare_checker", "prepare_doublechecker"},
}
_INVOCATION = re.compile(r"[0-9a-f]{32}\Z")
# Optional/required JSON role records accepted only in enabled copies.
ROLE_RECORDS = {
    "checker": {"maintenance-findings.json"},
    "doublechecker": {"maintenance-findings.json"},
    "updater": {"artifact-admission.json"},
    "arbiter": {"retained-verdict.json"},
}
# Blocking candidate classifications for a required check at review time.
_BLOCKING = {"regression", "unavailable", "not_comparable"}
# Phase outcomes that hold or recover without recording a completed phase.
_HELD = {"HUMAN_REQUIRED", "TECHNICAL_RECOVERY", "CONSULTANT"}
# Unavailable reasons that are ordinary technical prerequisites (existing
# Troubleshooter/Consultant recovery) versus protected foreign work (human).
_TECHNICAL = ("tool_unavailable:", "check_cwd_missing", "process_exit_unobserved")


def _plus():
    try:
        from millrace_plus import check_contracts as cc
        from millrace_plus import engineering_feedback as ef
    except ImportError as exc:
        raise LadError("engineering_requires_plus") from exc
    return ef, cc


def _code(exc: Exception) -> str:
    return getattr(exc, "code", None) or type(exc).__name__


def control_root() -> Path:
    return Path(__file__).resolve().parent.parent


def enabled() -> bool:
    return os.path.lexists(control_root() / "context/engineering-profile.json")


class Engineering:
    """Validated captured definitions for this copy plus its mutable cursor."""

    def __init__(self, foreground: Path) -> None:
        ef, cc = _plus()
        self.ef, self.cc = ef, cc
        self.foreground = foreground
        self.project = foreground.parent
        root = control_root()
        try:
            capture = ef.parse(read(root, ef.CAPTURE_PATH))
            if not isinstance(capture, dict) or capture.get("format") != ef.CAPTURE:
                raise LadError("engineering_capture_invalid")
            profile_raw = read(root, ef.PROFILE_PATH)
            self.profile = ef.load_profile(profile_raw)
            pins = {
                "profile_ref": (ef.PROFILE_PATH, profile_raw),
                "path_policy_ref": None,
                "requirements_ref": None,
                "node_map_ref": None,
                "resolved_checks_ref": None,
            }
            refs = {}
            for key in pins:
                pinned = ef.ref(capture[key])
                raw = read(root, pinned["path"])
                if digest(raw) != pinned["sha256"]:
                    raise LadError("engineering_capture_changed")
                refs[key] = (pinned, raw)
            self.capture = capture
            self.capture_sha256 = digest(read(root, ef.CAPTURE_PATH))
            self.profile_ref = refs["profile_ref"][0]
            self.policy_ref = self.profile["path_policy_ref"]
            policy_raw = refs["path_policy_ref"][1]
            if digest(policy_raw) != self.policy_ref["sha256"]:
                raise LadError("engineering_policy_changed")
            self.policy = cc.validate_path_policy(cc.parse(policy_raw))
            self.requirements_raw = refs["requirements_ref"][1]
            self.requirements = ef.load_requirements(self.requirements_raw)
            pin = self.profile["requirements_snapshot"]
            if digest(self.requirements_raw) != pin["sha256"]:
                raise LadError("engineering_requirements_changed")
            self.node_map = ef.validate_node_map(
                ef.parse(refs["node_map_ref"][1]), self.profile
            )
            self.resolved = ef.validate_resolved(
                ef.parse(refs["resolved_checks_ref"][1]), self.profile
            )
            self.graph = ef.parse(read(root, "workflow.json"))
            ef.graph_nodes_match(self.graph, self.node_map["entries"],
                                 self.resolved["checks"])
            # The declared families belong only to their pinned bindings; a
            # later graph edit must not inherit an old declaration.
            ef._validate_review_bindings(self.profile, self.graph)
        except (ef.EngineeringError, cc.CheckContractError) as exc:
            raise LadError(_code(exc)) from exc
        self.checks = [row["definition"] for row in self.resolved["checks"]]
        self.rows = {row["definition"]["id"]: row for row in self.resolved["checks"]}
        self.by_node = {
            entry["baseline_node"][3:]: entry for entry in self.node_map["entries"]
        }

    # -------------------------------------------------------------- refs/io

    def store(self, record: dict) -> dict:
        try:
            return self.ef.store_record(self.foreground, RECORDS, record)
        except self.ef.EngineeringError as exc:
            raise LadError(exc.code) from exc

    def load(self, pinned: dict, fmt: str | None = None) -> dict:
        raw = read(self.foreground, pinned["path"])
        if digest(raw) != pinned["sha256"]:
            raise LadError("engineering_record_changed")
        value = self.ef.parse(raw)
        if not isinstance(value, dict) or (fmt is not None and value.get("format")
                                           != fmt):
            raise LadError("engineering_record_changed")
        return value

    def session(self) -> dict:
        record = self.load_session_sidecar()
        if record is None:
            raise LadError("engineering_session_missing")
        return record

    def load_session_sidecar(self) -> dict | None:
        path = self.foreground / SESSION
        if not os.path.lexists(path):
            return None
        value = self.ef.parse(read(self.foreground, SESSION))
        if not isinstance(value, dict) or value.get("format") != SESSION_FORMAT:
            raise LadError("engineering_session_invalid")
        return value

    def state(self) -> dict:
        path = self.foreground / STATE
        if not os.path.lexists(path):
            session = self.session()
            return {"format": STATE_FORMAT, "session_id": session["session_id"],
                    "task_id": session["task_id"], "baseline": None, "open": None,
                    "attempts": {}, "candidates": {}, "latest_candidate": None,
                    "products": None, "admission": None, "acceptance": None,
                    "phases": []}
        value = self.ef.parse(read(self.foreground, STATE))
        if not isinstance(value, dict) or value.get("format") != STATE_FORMAT:
            raise LadError("engineering_state_invalid")
        try:
            self.ef.check_integrity(value, "engineering_state_integrity")
        except self.ef.EngineeringError as exc:
            raise LadError(exc.code) from exc
        value.pop("integrity_sha256")
        session = self.session()
        if (value["session_id"], value["task_id"]) != (
            session["session_id"], session["task_id"]
        ):
            raise LadError("engineering_state_identity")
        return value

    def save(self, value: dict) -> None:
        body = self.ef.with_integrity(dict(value))
        self.ef.replace_file(self.foreground, STATE, self.ef.record_bytes(body))

    # --------------------------------------------------------------- source

    def target(self) -> Path:
        if not hasattr(self, "_target"):
            selected = shared_target(self.foreground)
            self._target = (Path(selected["target_root"]) if selected is not None
                            else self.foreground / "target")
        return self._target

    def plan_paths(self, state: dict) -> list[str]:
        return sorted({path for row in state["plan"] for path in row["paths"]})

    def source(self, state: dict) -> tuple[dict, dict]:
        """Full selected source authority over the plan scope (files/outside/Git)."""
        target = self.target()
        paths = self.plan_paths(state)
        files = _source_at(self.foreground, target, paths)
        context = _source_context_at(
            target, _outside_source_at(self.foreground, target, paths)
        )
        record = self.ef.source_record(self.profile["repository"]["target_id"],
                                       files, context, paths)
        return record, self.store(record)

    def products(self) -> dict:
        try:
            return self.ef.observe_products(self.target(), self.policy)
        except self.ef.EngineeringError as exc:
            raise LadError(exc.code) from exc

    def observation(self, entries: dict) -> dict:
        return self.store(self.ef.observation_record(
            self.profile["repository"]["target_id"], entries
        ))

    def environment(self) -> dict:
        try:
            return self.store(self.ef.observe_environment(
                self.target(), self.policy, self.policy_ref
            ))
        except self.ef.EngineeringError as exc:
            raise LadError(exc.code) from exc

    def drift(self, state: dict, reserved_ref: dict) -> dict:
        """Current full source authority compared with an exact reserved record."""
        reserved = self.load(reserved_ref, self.ef.SOURCE)
        current, _ = self.source(state)
        return self.ef.authority_drift(reserved, current)

    def evidence(self, ref: dict, check_id: str, est: dict, phase: str,
                 source_ref: dict) -> dict:
        """Reload check evidence with its exact definition and phase identity."""
        try:
            return self.ef.validate_evidence(
                self.load(ref, self.ef.EVIDENCE),
                definition=self.rows[check_id]["definition"],
                execution_sha256=self.rows[check_id]["execution_sha256"],
                identity={"session_id": est["session_id"],
                          "task_id": est["task_id"], "phase": phase,
                          "check_id": check_id,
                          "profile_sha256": self.profile_ref["sha256"],
                          "source_ref": source_ref},
            )
        except self.ef.EngineeringError as exc:
            raise LadError(exc.code) from exc

    def execution(self, check_id: str) -> dict:
        return self.cc.execution_of(self.rows[check_id]["definition"],
                                    policy=self.policy, policy_ref=self.policy_ref)

    # ------------------------------------------------------------- deltas

    def baseline_record(self, est: dict) -> dict | None:
        if est["baseline"] is None:
            return None
        phase = self.load(est["baseline"], self.ef.PHASE)
        return self.load(phase["source_ref"], self.ef.SOURCE)

    def delta(self, est: dict, current: dict) -> dict:
        """Changed scoped paths since the task baseline (new scope counts changed)."""
        base = self.baseline_record(est)
        if base is None:
            raise LadError("engineering_baseline_required")
        before, after = base["files"], current["files"]
        changed = {}
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                changed[name] = after.get(name)
        for name, value in after.items():
            if name not in before and not any(
                name == path or name.startswith(path.rstrip("/") + "/")
                for path in base["paths"]
            ):
                changed[name] = value
        return changed


def task_id(foreground: Path, state: dict) -> str:
    selected = shared_target(foreground)
    return selected["task_id"] if selected is not None else state["session_id"]


# ------------------------------------------------------------ session init


def initialize(foreground: Path, state: dict, *, staging: Path | None) -> None:
    """Validate the enabled capture before any role; write the session sidecar."""
    engineering = Engineering(foreground)
    ef, cc = engineering.ef, engineering.cc
    selected = shared_target(foreground)
    root_ref = engineering.profile["repository"]["root_ref"]
    if (root_ref == "lad_target") != (selected is not None):
        raise LadError("engineering_target_mode_mismatch")
    target = engineering.target()
    history = Path(engineering.capture["history"]["home"])
    if target.exists() and (history.resolve() == target.resolve()
                            or target.resolve() in history.resolve().parents):
        raise LadError("history_home_inside_target")
    tracked = _tracked(target)
    try:
        cc.require_source(engineering.policy, tracked)
    except cc.CheckContractError as exc:
        raise LadError(exc.code) from exc
    artifact_policy = engineering.profile["artifact_policy"]
    record = {
        "format": SESSION_FORMAT,
        "session_id": state["session_id"],
        "task_id": task_id(foreground, state),
        "capture_sha256": engineering.capture_sha256,
        "profile_sha256": engineering.profile_ref["sha256"],
        "path_policy_sha256": engineering.policy_ref["sha256"],
        "requirements_sha256": digest(engineering.requirements_raw),
        "artifact_policy": artifact_policy,
    }
    raw = canonical(record) + b"\n"
    if staging is not None:
        from lad_state import atomic

        atomic(staging.parent, staging.name + "/engineering.json", raw)
        return
    existing = engineering.load_session_sidecar()
    if existing != record:
        raise LadError("engineering_capture_changed")
    del ef


def legacy_guard(foreground: Path) -> None:
    """A disabled copy must not carry an enabled session sidecar, and vice versa."""
    present = os.path.lexists(foreground / SESSION)
    if present and not enabled():
        raise LadError("engineering_capture_missing")


def _tracked(target: Path) -> list[str]:
    import subprocess

    if not (target / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(target), "ls-files", "-z"],
            env={"PATH": os.defpath, "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"},
            capture_output=True, timeout=10,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        raise LadError("source_git_observation_failed")
    return [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]


# ------------------------------------------------------------------ phases


def _open(engineering: Engineering, state: dict, est: dict, kind: str) -> dict:
    ef = engineering.ef
    entry = state["continuation"]
    record, source_ref = engineering.source(state)
    previous_ref = est["latest_candidate"] or est["baseline"]
    if kind == "candidate" and previous_ref is not None:
        previous = engineering.load(engineering.load(previous_ref, ef.PHASE)[
            "source_ref"
        ], ef.SOURCE)
        git_before, git_now = previous["git"] or {}, record["git"] or {}
        if previous["paths"] == record["paths"] and (
            previous["outside_sha256"] != record["outside_sha256"]
            or (previous["git"] is None) != (record["git"] is None)
            or any(git_before.get(k) != git_now.get(k) for k in ("base", "index"))
        ):
            # Roles write only inside the plan scope; outside or Git base/index
            # drift since the last checked phase is unexplained and holds.
            raise LadError("engineering_source_drift")
    products = engineering.products()
    # Only an explicit operator resume after a HUMAN_REQUIRED hold re-observes
    # product paths; technical recovery goes through role occurrences whose own
    # product immutability gate already applies.
    resumed = bool(est["phases"]) and est["phases"][-1]["outcome"] == "HUMAN_REQUIRED"
    if est["products"] is not None and not resumed:
        expected = engineering.load(est["products"], ef.OBSERVATION)["entries"]
        if expected != products:
            # A native role, import or other non-check writer changed a product path.
            raise LadError("undeclared_output")
    start = engineering.observation(products)
    if kind == "baseline":
        changed = engineering.plan_paths(state)
        scope_overlap = True
    else:
        changed = sorted(engineering.delta(est, record))
        scope_overlap = False
    selection = {}
    for definition in engineering.checks:
        if scope_overlap and definition["selection"]["when"] == "paths_changed":
            hits = [p for p in changed if any(
                ef.within(p, prefix) or ef.within(prefix, p)
                for prefix in definition["selection"]["paths"]
            )]
            chosen = (bool(hits), ("packet_paths:" + ",".join(hits))
                      if hits else "no_packet_path_selected")
        else:
            chosen = ef.selected(definition, changed)
        selection[definition["id"]] = {"selected": chosen[0], "reason": chosen[1]}
    key = kind + ":" + str(entry["occurrence_id"])
    attempt = est["attempts"].get(key, 0) + 1
    est["attempts"][key] = attempt
    return {
        "phase": kind,
        "attempt": attempt,
        "entry": entry,
        "source_ref": source_ref,
        "source_sha256": digest(canonical(record["files"])),
        "products_start_ref": start,
        "environment_start_ref": engineering.environment(),
        "selection": selection,
        "checks": [],
        "cursor": None,
    }


def _previous_evidence(est: dict, check_id: str) -> dict | None:
    for item in reversed(est["phases"]):
        if item["check_refs"].get(check_id) is not None:
            return item["check_refs"][check_id]
    return None


def _reserve(engineering: Engineering, state: dict, est: dict, opened: dict,
             definition: dict) -> dict:
    ef = engineering.ef
    definition_ref = engineering.store(definition)
    reservation = ef.check_input(
        session_id=state["session_id"], task_id=est["task_id"],
        phase=opened["phase"], check_id=definition["id"],
        profile_ref=engineering.profile_ref, definition_ref=definition_ref,
        source=opened["source_ref"], policy_ref=engineering.policy_ref,
        previous=_previous_evidence(est, definition["id"]),
    )
    input_ref = engineering.store(reservation)
    products = engineering.products()
    before = engineering.observation(products)
    layer = ef.products_record(
        input_id=reservation["input_id"], command_id=definition["id"],
        policy_ref=engineering.policy_ref, before_ref=before, after_ref=None,
        owned=[], completion_ref=None, previous_ref=None, status="reserved",
    )
    return {
        "check_id": definition["id"],
        "input_ref": input_ref,
        "input_id": reservation["input_id"],
        "layers": [engineering.store(layer)],
        "status": "reserved",
        "receipt_ref": None,
        "evidence_ref": None,
        "acceptance_ref": None,
    }


def _reverse(engineering: Engineering, opened: dict) -> list[dict]:
    """Return every owned path to phase-start absence; divergence holds."""
    ef = engineering.ef
    start = engineering.load(opened["products_start_ref"], ef.OBSERVATION)["entries"]
    owners: dict[str, tuple[dict, dict]] = {}
    for row in opened["checks"]:
        if not row["layers"]:
            continue
        accepted = engineering.load(row["layers"][-1], engineering.cc.PRODUCTS)
        if accepted["status"] != "accepted":
            raise LadError("engineering_layer_not_accepted")
        after = engineering.load(accepted["after_ref"], ef.OBSERVATION)["entries"]
        for path in accepted["owned_paths"]:
            owners[path] = (row, after.get(path))
    target = engineering.target()
    current = engineering.products()
    for path, (_, identity) in owners.items():
        if path in start:
            raise LadError("engineering_owned_path_was_foreign")
        if current.get(path) != identity:
            raise LadError("engineering_product_diverged")
    for path in sorted(owners, key=lambda p: (-p.count("/"), p)):
        location = target / path
        identity = owners[path][1]
        if identity is None:
            continue
        if identity["kind"] == "file":
            location.unlink()
        else:
            try:
                location.rmdir()
            except OSError as exc:
                raise LadError("engineering_product_diverged") from exc
    if engineering.products() != start:
        raise LadError("engineering_product_diverged")
    restored = []
    for row in opened["checks"]:
        if not row["layers"]:
            continue
        previous_ref = row["layers"][-1]
        previous_raw = read(engineering.foreground, previous_ref["path"])
        accepted = ef.parse(previous_raw)
        layer = dict(accepted)
        layer.update(status="restored", previous_ref=previous_ref)
        try:
            ef.transition(previous_raw, previous_ref, layer)
        except ef.EngineeringError as exc:
            raise LadError(exc.code) from exc
        row["layers"].append(engineering.store(layer))
        restored.append(row["layers"][-1])
    return restored


def _close(engineering: Engineering, state: dict, est: dict, opened: dict,
           outcome: str, comparisons: list[dict]) -> dict:
    ef = engineering.ef
    if engineering.drift(state, opened["source_ref"])["kinds"]:
        raise LadError("engineering_source_changed")
    restored = _reverse(engineering, opened)
    end = engineering.observation(engineering.products())
    record = {
        "format": ef.PHASE,
        "session_id": state["session_id"],
        "task_id": est["task_id"],
        "phase": opened["phase"],
        "attempt": opened["attempt"],
        "entry": opened["entry"],
        "source_ref": opened["source_ref"],
        "source_sha256": opened["source_sha256"],
        "selection": opened["selection"],
        "checks": [
            {key: row[key] for key in ("check_id", "input_ref", "receipt_ref",
                                       "evidence_ref", "acceptance_ref", "layers")}
            for row in opened["checks"]
        ],
        "comparisons": comparisons,
        "products_start_ref": opened["products_start_ref"],
        "products_end_ref": end,
        "environment_start_ref": opened["environment_start_ref"],
        "environment_end_ref": engineering.environment(),
        "restored_layer_refs": restored,
        # Advisory metrics come only from configured quality_observation checks;
        # an enabled metric without one is honestly not measured.
        "metrics": {
            name: ([d["id"] for d in engineering.checks
                    if d["purpose"] == "quality_observation"] or "not_measured")
            if enabled else "not_enabled"
            for name, enabled in engineering.profile["metrics"].items()
        },
        "outcome": outcome,
    }
    phase_ref = engineering.store(record)
    est["phases"].append({
        "ref": phase_ref,
        "phase": opened["phase"],
        "outcome": outcome,
        "check_refs": {row["check_id"]: row["acceptance_ref"]
                       for row in opened["checks"]
                       if engineering.evidence(
                           row["evidence_ref"], row["check_id"], est,
                           opened["phase"], opened["source_ref"]
                       )["status"] in ("pass", "fail")},
    })
    est["products"] = end
    est["open"] = None
    if outcome in _HELD:
        return phase_ref
    if opened["phase"] == "baseline":
        est["baseline"] = phase_ref
    else:
        est["candidates"][str(opened["entry"]["occurrence_id"])] = phase_ref
        est["latest_candidate"] = phase_ref
    return phase_ref


def _comparisons(engineering: Engineering, est: dict, opened: dict) -> list[dict]:
    ef = engineering.ef
    baseline = (engineering.load(est["baseline"], ef.PHASE)
                if est["baseline"] is not None else None)
    prior = {}
    if baseline is not None:
        for row in baseline["checks"]:
            prior[row["check_id"]] = row
    rows = []
    for row in opened["checks"]:
        evidence = engineering.evidence(row["evidence_ref"], row["check_id"], est,
                                        "candidate", opened["source_ref"])
        base_row = prior.get(row["check_id"])
        base = (engineering.evidence(base_row["evidence_ref"], row["check_id"], est,
                                     "baseline", baseline["source_ref"])
                if base_row else None)
        try:
            rows.append(ef.compare_checks(
                base, base_row["acceptance_ref"] if base_row else None,
                evidence, row["acceptance_ref"],
            ))
        except ef.EngineeringError as exc:
            raise LadError(exc.code) from exc
    return rows


def phase(kind: str) -> None:
    foreground, _ = roots()
    state = load(foreground)
    assert_foreground(state)
    engineering = Engineering(foreground)
    est = engineering.state()
    entry = state["continuation"]
    if entry is None or entry["node"] not in ENTRIES[kind]:
        raise LadError("engineering_entry_mismatch")
    if state["plan"] == [] or state["specification"] is None:
        raise LadError("engineering_plan_required")
    opened = est["open"]
    if opened is not None and (opened["phase"] != kind or opened["entry"] != entry):
        raise LadError("engineering_phase_conflict")
    if opened is None:
        if kind == "baseline" and est["baseline"] is not None:
            _unchanged_products(engineering, est)
            emit("READY", "Task baseline already recorded; products unchanged.")
            return
        if kind == "candidate" and est["latest_candidate"] is not None:
            # Immutable evidence at the exact same source is reused, never rerun.
            done = engineering.load(est["latest_candidate"], engineering.ef.PHASE)
            drift = engineering.drift(state, done["source_ref"])["kinds"]
            if drift and not {"files", "scope"} & set(drift):
                # Same checked files but outside/Git authority moved: hold.
                raise LadError("engineering_source_drift")
            if not drift:
                _unchanged_products(engineering, est)
                est["candidates"][str(entry["occurrence_id"])] = est[
                    "latest_candidate"
                ]
                engineering.save(est)
                emit(_candidate_outcome(entry, done["comparisons"], engineering),
                     "Exact candidate source replay; checks not rerun.")
                return
        opened = _open(engineering, state, est, kind)
        est["open"] = opened
        engineering.save(est)
    last = opened["checks"][-1] if opened["checks"] else None
    if (last is not None and last["status"] == "reserved"
            and last["receipt_ref"] is None and opened["cursor"] == last["check_id"]
            and not os.path.lexists(foreground / f"{RUNS}/{last['input_id']}")):
        # The guard was interrupted after reservation; the check never started.
        encoded = engineering.ef.node_names(last["check_id"])["baseline_node"][3:]
        emit("RUN_" + encoded, "Reserved check " + last["input_id"] + " (replay)")
        return
    for row in opened["checks"]:
        if row["status"] != "accepted":
            raise LadError("engineering_check_pending")
    done_ids = {row["check_id"] for row in opened["checks"]}
    pending = [d for d in engineering.checks if d["id"] not in done_ids]
    if pending:
        definition = pending[0]
        opened["checks"].append(_reserve(engineering, state, est, opened, definition))
        opened["cursor"] = definition["id"]
        engineering.save(est)
        encoded = engineering.ef.node_names(definition["id"])["baseline_node"][3:]
        emit("RUN_" + encoded, "Reserved check " + opened["checks"][-1]["input_id"])
        return
    human, technical = [], []
    for row in opened["checks"]:
        evidence = engineering.evidence(row["evidence_ref"], row["check_id"], est,
                                        kind, opened["source_ref"])
        definition = engineering.rows[row["check_id"]]["definition"]
        reason = evidence["unrun_reason"] or ""
        if reason == "foreign_product_present":
            # Protected preexisting work: only explicit human authority resolves it.
            human.append(row["check_id"])
        elif (definition["required"]
              and opened["selection"][row["check_id"]]["selected"]
              and evidence["status"] == "unavailable"):
            if not reason.startswith(_TECHNICAL):
                human.append(row["check_id"])
                continue
            technical.append({"check_id": row["check_id"], "unrun_reason": reason,
                              "evidence_ref": row["evidence_ref"],
                              "acceptance_ref": row["acceptance_ref"]})
    comparisons = (_comparisons(engineering, est, opened) if kind == "candidate"
                   else [])
    if human:
        outcome = "HUMAN_REQUIRED"
    elif technical:
        cause = digest(canonical({
            "kind": "engineering_prerequisite", "phase": kind,
            "checks": [(row["check_id"], row["unrun_reason"]) for row in technical],
        }))
        outcome = "CONSULTANT" if _repaired(state, cause) else "TECHNICAL_RECOVERY"
    else:
        outcome = ("READY" if kind == "baseline"
                   else _candidate_outcome(entry, comparisons, engineering))
    phase_ref = _close(engineering, state, est, opened, outcome, comparisons)
    engineering.save(est)
    if technical and not human:
        # Existing technical recovery with the exact failed operation/evidence;
        # a successful repair resumes this phase from its saved entry.
        state["pending"] = {
            "kind": "engineering_prerequisite",
            "target": "engineering_" + kind,
            "lineage": scope(state),
            "occurrence_id": entry["occurrence_id"],
            "cause_id": cause,
            "resume_continuation": entry,
            # Any saved route (for example a review repair) returns untouched.
            "resume_pending": state["pending"],
            "phase_ref": phase_ref,
            "checks": technical,
        }
        mark(state, "prepare_consultant" if outcome == "CONSULTANT"
             else "prepare_troubleshooter", entry["occurrence_id"])
        save(foreground, state)
    emit(outcome, "Engineering " + kind + " phase "
         + ("held: " + ",".join(human) if human
            else "technical prerequisite unavailable: "
            + ",".join(row["check_id"] for row in technical) if technical
            else "complete") + "; products reversed.")


def _repaired(state: dict, cause: str) -> bool:
    """An earlier verified Troubleshooter repair of this exact cause."""
    return any(
        state["occurrences"][oid]["role"] == "troubleshooter"
        and accepted["result"]["data"].get("cause_id") == cause
        and accepted["result"]["data"].get("repair_verified") is True
        for oid, accepted in state["accepted"].items()
    )


def _unchanged_products(engineering: Engineering, est: dict) -> None:
    if est["products"] is None:
        return
    expected = engineering.load(est["products"], engineering.ef.OBSERVATION)
    if expected["entries"] != engineering.products():
        raise LadError("undeclared_output")


def _candidate_outcome(entry: dict, comparisons: list[dict],
                       engineering: Engineering) -> str:
    finding = any(
        row["classification"] in ("regression", "preexisting_failure")
        or (row["classification"] in _BLOCKING
            and engineering.rows[row["check_id"]]["definition"]["required"])
        for row in comparisons
    )
    repair = entry["node"] == "prepare_doublechecker"
    return ("REPAIR_" if repair else "") + ("FINDINGS" if finding else "CHECKED")


# ------------------------------------------------------------------- checks


def _current(engineering: Engineering, kind: str, encoded: str, *,
             replay: bool = False) -> tuple[dict, dict, dict]:
    """The exact reserved check of this phase: the engineering continuation."""
    state = load(engineering.foreground)
    assert_foreground(state)
    est = engineering.state()
    opened = est["open"]
    entry = engineering.by_node.get(encoded)
    if (entry is None or opened is None or opened["phase"] != kind
            or not opened["checks"]
            or opened["entry"] != state["continuation"]):
        raise LadError("engineering_route_authority_missing")
    row = opened["checks"][-1]
    accepted_replay = (replay and opened["cursor"] is None
                       and row["status"] == "accepted")
    if row["check_id"] != entry["check_id"] or (
        opened["cursor"] != entry["check_id"] and not accepted_replay
    ):
        raise LadError("engineering_route_authority_missing")
    return est, opened, row


def invocation_root() -> Path:
    """This Core invocation's root from the public command-adapter argv layout."""
    script = Path(__file__).resolve()
    root = script.parent.parent.parent
    if (script.parent.parent.name != "controls" or root.parent.name != "invocations"
            or not _INVOCATION.fullmatch(root.name)):
        raise LadError("engineering_route_authority_missing")
    return root


def check(kind: str, encoded: str) -> None:
    started_at = time.time()
    foreground, _ = roots()
    engineering = Engineering(foreground)
    ef = engineering.ef
    est, opened, row = _current(engineering, kind, encoded)
    if row["receipt_ref"] is not None:
        receipt = engineering.load(row["receipt_ref"], ef.RECEIPT)
        emit(receipt["outcome"], "Exact check replay; nothing rerun.")
        return
    run_dir = f"{RUNS}/{row['input_id']}"
    if os.path.lexists(foreground / run_dir):
        emit("UNKNOWN", "An earlier attempt for this reservation has no receipt.")
        return
    root = invocation_root()
    state = load(foreground)
    if engineering.drift(state, opened["source_ref"])["kinds"]:
        # Nothing runs against source that differs from the exact reservation.
        emit("REFUSED", "LAD engineering hold: engineering_source_changed")
        return
    check_id = row["check_id"]
    definition = engineering.rows[check_id]["definition"]
    timeout = definition["timeout_seconds"]
    budget = engineering.rows[check_id]["node_budget"]["budget_seconds"]
    # Wrapper-internal upper bound only. Core's actual invocation deadline is not
    # handed to command scripts; Core enforces it by signalling this wrapper,
    # which then kills the check group at once (see on_signal below).
    wrapper_bound = started_at + budget
    selection = opened["selection"][check_id]
    reserved_ref = row["layers"][0]
    reserved = engineering.load(reserved_ref, engineering.cc.PRODUCTS)
    before = engineering.load(reserved["before_ref"], ef.OBSERVATION)["entries"]
    target = engineering.target()
    cwd = target / definition["cwd"] if definition["cwd"] != "." else target
    environment, environment_sha = ef.resolved_environment(definition, os.environ)
    execution = engineering.execution(check_id)
    owned_before = _owned_paths(engineering, opened)
    unrun = None
    if not selection["selected"]:
        unrun = "not_selected:" + selection["reason"]
    elif any(
        path not in owned_before and engineering.cc.product_write_allowed(
            engineering.policy, execution, path
        )
        for path in before
    ):
        unrun = "foreign_product_present"
    elif not cwd.is_dir():
        unrun = "check_cwd_missing"
    else:
        executable = ef.resolve_executable(definition["argv"][0], environment, cwd)
        if executable is None:
            unrun = "tool_unavailable:" + definition["argv"][0]
    facts = None
    process_started = time.time()
    if unrun is None:
        # Marks a process attempt before spawning; a later invocation without a
        # receipt can never rerun it and the guard holds unknown closure.
        os.makedirs(foreground / run_dir, mode=0o700)
        child = {"pid": None, "cancel": False}

        def on_signal(*_):
            # Core cancels/times out this wrapper with TERM and KILLs it shortly
            # after; the check runs in its own group, so stop that group now
            # rather than racing a graceful schedule against the wrapper's death.
            child["cancel"] = True
            if child["pid"] is not None:
                try:
                    os.killpg(child["pid"], signal.SIGKILL)
                except OSError:
                    pass

        signal.signal(signal.SIGTERM, on_signal)

        def started(pid: int) -> None:
            child["pid"] = pid
            if child["cancel"]:  # TERM arrived between spawn and this callback.
                on_signal()
            ef.write_immutable(foreground, run_dir + "/start.json", ef.record_bytes({
                "format": ef.START, "input_id": row["input_id"], "pid": pid,
                "invocation_root": str(root), "started_at": ef.utc_now(),
            }))

        process_started = time.time()
        try:
            facts = ef.run_check(
                definition["argv"], cwd=cwd, environment=environment,
                deadline=min(time.time() + timeout, wrapper_bound),
                cancelled=lambda: child["cancel"], on_started=started,
            )
        except ef.EngineeringError as exc:
            raise LadError(exc.code) from exc
        finally:
            child["pid"] = None  # Never signal a reaped, possibly reused group.
    finished_at = ef.utc_now()
    # A check may write only its declared products: any other source, outside
    # or Git effect is undeclared and can never be accepted.
    source_drift = engineering.drift(state, opened["source_ref"])
    after = engineering.products()
    changed = ef.changed_products(before, after)
    undeclared = [p for p in changed if not engineering.cc.product_write_allowed(
        engineering.policy, execution, p
    )]
    stdout_ref = stderr_ref = None
    if facts is not None:
        stdout_ref = ef.write_immutable(
            foreground, f"{OUTPUTS}/{digest(facts['stdout'])}", facts["stdout"]
        )
        stderr_ref = ef.write_immutable(
            foreground, f"{OUTPUTS}/{digest(facts['stderr'])}", facts["stderr"]
        )
    closed = facts is None or facts["cleanup_confirmed"]
    ran = facts is not None and facts["exit_code"] is not None
    if facts is not None and not ran:
        unrun = "process_exit_unobserved"
    status = ("not_selected" if not selection["selected"] else "unavailable"
              if not ran else ef.classify_status(
                  definition, facts["exit_code"], facts["stop_cause"],
                  facts["cleanup_confirmed"]))
    outcome = {"pass": "PASS", "fail": "FAIL", "unavailable": "UNAVAILABLE",
               "not_selected": "NOT_SELECTED"}[status]
    if undeclared or not closed or source_drift["kinds"]:
        outcome = "UNKNOWN"
    after_ref = engineering.observation(after)
    layer_status = "unknown" if (undeclared or not closed) else "observed"
    layer = ef.products_record(
        input_id=row["input_id"], command_id=check_id,
        policy_ref=engineering.policy_ref, before_ref=reserved["before_ref"],
        after_ref=after_ref, owned=[] if layer_status == "unknown" else changed,
        completion_ref=None, previous_ref=reserved_ref, status=layer_status,
        policy=engineering.policy if layer_status == "observed" else None,
        execution=execution if layer_status == "observed" else None,
    )
    layer_ref = engineering.store(layer)
    evidence = ef.evidence_record(
        check_id=check_id,
        definition_sha256=digest(canonical(definition)),
        profile_sha256=engineering.profile_ref["sha256"],
        task_id=est["task_id"], session_id=est["session_id"],
        occurrence_id=str(opened["entry"]["occurrence_id"]),
        phase=kind, source_ref=opened["source_ref"],
        execution_sha256=engineering.rows[check_id]["execution_sha256"],
        argv=definition["argv"], cwd=definition["cwd"],
        environment_sha256=environment_sha, path_policy_ref=engineering.policy_ref,
        product_layer_refs=[layer_ref],
        invocation_deadline=None, timeout_seconds=timeout,
        tool_version=None,
        started_at=_iso(process_started) if ran else None,
        finished_at=finished_at if ran else None,
        exit_code=facts["exit_code"] if ran else None,
        status=status, stdout_ref=stdout_ref if ran else None,
        stderr_ref=stderr_ref if ran else None,
        requirement_ids=definition["requirement_ids"],
        selection_reason=selection["reason"], unrun_reason=unrun,
    )
    evidence_ref = engineering.store(evidence)
    receipt = {
        "format": ef.RECEIPT,
        "input_id": row["input_id"],
        "check_id": check_id,
        "phase": kind,
        "invocation_root": str(root),
        "process": None if facts is None else {
            key: facts[key] for key in (
                "pid", "exit_code", "stop_cause", "stdout_bytes", "stderr_bytes",
                "stdout_sha256", "stderr_sha256", "stdout_truncated",
                "stderr_truncated", "group_state", "cleanup_confirmed",
            )
        },
        "unrun_reason": unrun,
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
        "undeclared_paths": undeclared,
        "source_drift": source_drift,
        "layer_ref": layer_ref,
        "evidence_ref": evidence_ref,
        "outcome": outcome,
    }
    receipt_ref = engineering.store(receipt)
    # The receipt names its own layer/evidence; acceptance belongs to the guard.
    row.update(receipt_ref=receipt_ref, evidence_ref=evidence_ref, status=layer_status)
    row["layers"].append(layer_ref)
    engineering.save(est)
    emit(outcome, "Observed check " + check_id + " (" + status + ").")


def _iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _owned_paths(engineering: Engineering, opened: dict) -> set[str]:
    owned = set()
    for row in opened["checks"][:-1]:
        layer = engineering.load(row["layers"][-1], engineering.cc.PRODUCTS)
        owned |= set(layer["owned_paths"])
    return owned


def accept(kind: str, encoded: str) -> None:
    foreground, _ = roots()
    engineering = Engineering(foreground)
    ef = engineering.ef
    est, opened, row = _current(engineering, kind, encoded, replay=True)
    if row["status"] == "accepted":
        emit("ACCEPTED", "Exact guard replay.")
        return
    root = invocation_root()
    entry = engineering.by_node[encoded]
    from_node = entry[kind + "_node"]
    to_node = entry[kind + "_accept_node"]
    if row["receipt_ref"] is None:
        start = _start(engineering, row)
        if start is not None and str(root) == start["invocation_root"]:
            raise LadError("engineering_route_authority_missing")
        # Physical fact only: a present group is not proven live (it may be an
        # unreaped zombie), and an absent group still leaves no exit evidence.
        physical = ("no_process_recorded" if start is None
                    else "group_absent" if ef.group_absent(start["pid"])
                    else "group_present_not_proven_live")
        _unknown(engineering, est, row, "check_receipt_missing", physical)
        emit("RECONCILE", "No check receipt; evidence and closure unproven ("
             + physical + "). Never rerun; the recorded group needs explicit "
             "operator cleanup when present.")
        return
    receipt = engineering.load(row["receipt_ref"], ef.RECEIPT)
    if (receipt["input_id"] != row["input_id"] or receipt["check_id"] != row["check_id"]
            or receipt["phase"] != kind):
        raise LadError("engineering_receipt_mismatch")
    try:
        reservation = ef.validate_check_input(engineering.load(row["input_ref"],
                                                               ef.CHECK_INPUT))
    except ef.EngineeringError as exc:
        raise LadError(exc.code) from exc
    # The exact reserved session/task/phase/check, source and policy authority.
    if (reservation["input_id"] != row["input_id"]
            or reservation["session_id"] != est["session_id"]
            or reservation["task_id"] != est["task_id"]
            or reservation["phase"] != kind
            or reservation["check_id"] != row["check_id"]
            or reservation["source_ref"] != opened["source_ref"]
            or reservation["path_policy_ref"] != engineering.policy_ref
            or reservation["profile_ref"] != engineering.profile_ref):
        raise LadError("engineering_reservation_mismatch")
    producer = Path(receipt["invocation_root"])
    if producer == root or producer.parent != root.parent:
        # The guard must be a later Core invocation, never the producing command.
        raise LadError("engineering_route_authority_missing")
    if receipt["source_drift"]["kinds"]:
        emit("RECONCILE", "Check changed source/outside/Git authority ("
             + ",".join(receipt["source_drift"]["kinds"]) + "); never accepted.")
        return
    engineering.evidence(receipt["evidence_ref"], row["check_id"], est, kind,
                         opened["source_ref"])
    layer_ref = row["layers"][-1]
    layer_raw = read(foreground, layer_ref["path"])
    layer = ef.parse(layer_raw)
    if layer["status"] == "unknown":
        if receipt["undeclared_paths"] or not _recoverable(engineering, row, receipt):
            emit("RECONCILE", "Check writer closure or product ownership unknown.")
            return
        layer = _reobserve(engineering, row, layer_ref, layer_raw)
        layer_ref = row["layers"][-1]
        layer_raw = read(foreground, layer_ref["path"])
    after = engineering.load(layer["after_ref"], ef.OBSERVATION)["entries"]
    if engineering.products() != after:
        raise LadError("engineering_product_diverged")
    if engineering.drift(load(foreground), opened["source_ref"])["kinds"]:
        raise LadError("engineering_source_changed")
    guard = {
        "format": ef.GUARD,
        "input_id": row["input_id"],
        "check_id": row["check_id"],
        "phase": kind,
        "from_node": from_node,
        "to_node": to_node,
        "reservation_ref": row["input_ref"],
        "process_receipt_ref": row["receipt_ref"],
        "observed_layer_ref": layer_ref,
        "guard_invocation_root": str(root),
        "producer_invocation_root": str(producer),
        "products_verified": True,
        "source_verified": True,
    }
    guard_ref = engineering.store(guard)
    acceptance = ef.acceptance_record(
        subject_ref=row["input_ref"], command_id=row["check_id"],
        receipt_ref=row["receipt_ref"], from_node=from_node, to_node=to_node,
        reservation_ref=row["input_ref"], guard_ref=guard_ref,
    )
    acceptance_ref = engineering.store(acceptance)
    accepted = dict(layer)
    accepted.update(status="accepted", completion_ref=acceptance_ref,
                    previous_ref=layer_ref)
    try:
        ef.transition(layer_raw, layer_ref, accepted)
    except ef.EngineeringError as exc:
        raise LadError(exc.code) from exc
    row["layers"].append(engineering.store(accepted))
    row.update(status="accepted", acceptance_ref=acceptance_ref)
    opened["cursor"] = None
    engineering.save(est)
    emit("ACCEPTED", "Accepted stopped check completion for " + row["check_id"])


def _start(engineering: Engineering, row: dict) -> dict | None:
    path = engineering.foreground / f"{RUNS}/{row['input_id']}/start.json"
    if not os.path.lexists(path):
        return None
    value = engineering.ef.parse(read(engineering.foreground,
                                      f"{RUNS}/{row['input_id']}/start.json"))
    if value.get("format") != engineering.ef.START or value["input_id"] != row[
        "input_id"
    ]:
        raise LadError("engineering_start_record")
    return value


def _unknown(engineering: Engineering, est: dict, row: dict, reason: str,
             physical: str | None = None) -> None:
    ef = engineering.ef
    previous_ref = row["layers"][-1]
    previous_raw = read(engineering.foreground, previous_ref["path"])
    previous = ef.parse(previous_raw)
    if previous["status"] == "unknown":
        if physical is not None and row.get("physical_group") != physical:
            row["physical_group"] = physical
            engineering.save(est)
        return
    layer = ef.products_record(
        input_id=row["input_id"], command_id=row["check_id"],
        policy_ref=engineering.policy_ref, before_ref=previous["before_ref"],
        after_ref=None, owned=[], completion_ref=None, previous_ref=previous_ref,
        status="unknown",
    )
    try:
        ef.transition(previous_raw, previous_ref, layer)
    except ef.EngineeringError as exc:
        raise LadError(exc.code) from exc
    row["layers"].append(engineering.store(layer))
    row["status"] = "unknown"
    row["unknown_reason"] = reason
    row["physical_group"] = physical
    engineering.save(est)


def _recoverable(engineering: Engineering, row: dict, receipt: dict) -> bool:
    """Closure is proven only by observing the recorded process group absent."""
    process = receipt["process"]
    if process is None or process["pid"] is None:
        return False
    return engineering.ef.group_absent(process["pid"])


def _reobserve(engineering: Engineering, row: dict, previous_ref: dict,
               previous_raw: bytes) -> dict:
    """Append a fresh observation after proven closure (unknown → observed)."""
    ef = engineering.ef
    previous = ef.parse(previous_raw)
    before = engineering.load(previous["before_ref"], ef.OBSERVATION)["entries"]
    after = engineering.products()
    execution = engineering.execution(row["check_id"])
    changed = ef.changed_products(before, after)
    layer = ef.products_record(
        input_id=row["input_id"], command_id=row["check_id"],
        policy_ref=engineering.policy_ref, before_ref=previous["before_ref"],
        after_ref=engineering.observation(after), owned=changed, completion_ref=None,
        previous_ref=previous_ref, status="observed", policy=engineering.policy,
        execution=execution,
    )
    try:
        ef.transition(previous_raw, previous_ref, layer)
    except ef.EngineeringError as exc:
        raise LadError(exc.code) from exc
    row["layers"].append(engineering.store(layer))
    return layer


# ------------------------------------------------------- review/route hooks


def latest_candidate(engineering: Engineering, state: dict) -> tuple[dict, dict]:
    """The latest candidate phase, only at the exact full source authority.

    Changed checked files mean stale checks; unchanged files with changed outside
    or Git authority is unexplained drift. Both hold; neither silently moves the
    checked authority forward.
    """
    est = engineering.state()
    if est["open"] is not None:
        raise LadError("engineering_phase_pending")
    if est["latest_candidate"] is None:
        raise LadError("engineering_candidate_checks_required")
    phase_record = engineering.load(est["latest_candidate"], engineering.ef.PHASE)
    drift = engineering.drift(state, phase_record["source_ref"])["kinds"]
    if {"files", "scope"} & set(drift):
        raise LadError("engineering_candidate_source_changed")
    if drift:
        raise LadError("engineering_source_drift")
    return est, phase_record


def review_gate(foreground: Path, state: dict, occurrence: dict, result: dict,
                artifacts: dict) -> None:
    """Deterministic engineering obligations for Doublechecker/Updater/Arbiter PASS."""
    engineering = Engineering(foreground)
    ef = engineering.ef
    role, outcome = occurrence["role"], result["outcome"]
    findings = None
    if "maintenance-findings.json" in artifacts and role in {"checker",
                                                              "doublechecker"}:
        try:
            findings = ef.validate_findings(
                ef.parse(artifacts["maintenance-findings.json"]),
                occurrence_id=occurrence["occurrence_id"],
                profile_sha256=engineering.profile_ref["sha256"],
                source_sha256=result["source_sha256"],
                criteria={row["id"] for row in state["criteria"]},
                guidelines={row["id"]: row["mode"]
                            for row in engineering.profile["guidelines"]},
            )
        except ef.EngineeringError as exc:
            raise LadError("finding_" + exc.code) from exc
    if outcome != "PASS" or role not in {"doublechecker", "updater"}:
        return
    _, record = latest_candidate(engineering, state)
    for row in record["comparisons"]:
        definition = engineering.rows[row["check_id"]]["definition"]
        if row["classification"] == "regression" or (
            definition["required"] and row["classification"] in _BLOCKING
        ):
            raise LadError("pass_with_engineering_regression")
        if definition["required"] and row["classification"] == "preexisting_failure":
            if role != "doublechecker" or not _disposed(findings, row, state):
                raise LadError("pass_with_engineering_regression")
    if role == "updater":
        if not any(item["selected"] for item in record["selection"].values()) and (
            not isinstance(result["data"].get("no_check_disposition"), str)
            or not result["data"]["no_check_disposition"].strip()
        ):
            raise LadError("pass_with_engineering_no_check_disposition")
        if "artifact-admission.json" not in artifacts:
            raise LadError("missing_required_artifact")
        try:
            ef.validate_admission(
                ef.parse(artifacts["artifact-admission.json"]),
                session_id=state["session_id"], task_id=engineering.session()[
                    "task_id"
                ], source_sha256=result["source_sha256"],
                profile_sha256=engineering.profile_ref["sha256"],
            )
        except ef.EngineeringError as exc:
            raise LadError("finding_" + exc.code) from exc


def _disposed(findings: dict | None, row: dict, state: dict) -> bool:
    """A known pre-existing required failure needs an explicit tied disposition."""
    if findings is None:
        return False
    criteria = {item["id"] for item in state["criteria"]}
    # Content-addressed records: the digest names the exact accepted evidence.
    return any(
        item["criterion_id"] in criteria and item["blocking"] is False
        and any(entry["sha256"] == row["candidate_ref"]["sha256"]
                for entry in item["evidence_refs"])
        for item in findings["findings"]
    )


def superseded(engineering: Engineering, state: dict) -> dict[str, list[str]]:
    """Exact `Supersedes: <id>` directives quoted by current original criteria."""
    active = {row["id"] for row in engineering.ef.active_requirements(
        engineering.requirements
    )}
    pattern = re.compile(r"(?m)^Supersedes: (\S+)\s*$")
    result: dict[str, list[str]] = {}
    for row in state["criteria"]:
        ids = pattern.findall(row["request_quote"])
        for item in ids:
            if item not in active:
                raise LadError("supersession_without_active_requirement")
        if ids:
            result[row["id"]] = sorted(set(ids))
    return result


def retained_ids(engineering: Engineering, state: dict) -> set[str]:
    gone = {item for ids in superseded(engineering, state).values() for item in ids}
    return {row["id"] for row in engineering.ef.active_requirements(
        engineering.requirements
    )} - gone


def arbiter_gate(foreground: Path, state: dict, occurrence: dict, result: dict,
                 artifacts: dict) -> None:
    engineering = Engineering(foreground)
    ef = engineering.ef
    if "retained-verdict.json" not in artifacts:
        raise LadError("missing_required_artifact")
    try:
        verdict = ef.validate_retained(
            ef.parse(artifacts["retained-verdict.json"]),
            occurrence_id=occurrence["occurrence_id"],
            requirements_sha256=digest(engineering.requirements_raw),
            source_sha256=result["source_sha256"],
            retained=retained_ids(engineering, state),
        )
    except ef.EngineeringError as exc:
        raise LadError("finding_" + exc.code) from exc
    retained_gap = any(row["status"] != "pass" for row in verdict["verdict"].values())
    original = result["data"].get("verdict")
    original_gap = isinstance(original, dict) and any(
        isinstance(row, dict) and row.get("status") == "gap"
        for row in original.values()
    )
    if result["outcome"] == "PASS":
        if retained_gap:
            raise LadError("pass_with_retained_requirement_gap")
        est = engineering.state()
        if est["admission"] is None:
            raise LadError("engineering_admission_required")
        latest_candidate(engineering, state)
    elif result["outcome"] == "FINDINGS" and retained_gap and not original_gap:
        # A prior accepted requirement regressed without a current-criterion gap:
        # remediation cannot target it, so it needs a human decision.
        raise LadError("finding_retained_gap_requires_human")


def role_gate(foreground: Path, state: dict, occurrence: dict, result: dict,
              artifacts: dict) -> None:
    role, outcome = occurrence["role"], result["outcome"]
    if role == "manager" and outcome == "PASS":
        plan = result["data"].get("plan")
        if isinstance(plan, list):
            plan_gate(foreground, plan)
    elif role in {"checker", "doublechecker", "updater"}:
        review_gate(foreground, state, occurrence, result, artifacts)
    elif role == "arbiter" and outcome in {"PASS", "FINDINGS"}:
        arbiter_gate(foreground, state, occurrence, result, artifacts)


def plan_gate(foreground: Path, plan: list[dict]) -> None:
    """Packet paths are source within the profile scope, never cache/product."""
    engineering = Engineering(foreground)
    repository = engineering.profile["repository"]
    paths = sorted({
        path for row in plan if isinstance(row, dict)
        and isinstance(row.get("paths"), list)
        for path in row["paths"] if isinstance(path, str)
    })
    try:
        engineering.cc.require_source(engineering.policy, paths)
    except engineering.cc.CheckContractError as exc:
        raise LadError("finding_packet_" + exc.code) from exc
    for path in paths:
        if not any(engineering.ef.within(path, scope)
                   for scope in repository["source_scope"]) or any(
            engineering.ef.within(path, excluded)
            or engineering.ef.within(excluded, path)
            for excluded in repository["excluded_paths"]
        ):
            raise LadError("finding_packet_path_outside_engineering_scope")


def products_unchanged(foreground: Path, occurrence: dict) -> None:
    """No non-check writer may change a product path during a role occurrence."""
    engineering = Engineering(foreground)
    sidecar = _sidecar(engineering, occurrence["occurrence_id"])
    expected = engineering.load(sidecar["products_ref"], engineering.ef.OBSERVATION)
    if expected["entries"] != engineering.products():
        raise LadError("undeclared_output")


def _sidecar(engineering: Engineering, oid: str) -> dict:
    relative = f"{OCCURRENCES}/{oid}.json"
    value = engineering.ef.parse(read(engineering.foreground, relative))
    if not isinstance(value, dict) or value.get("occurrence_id") != oid:
        raise LadError("engineering_occurrence_sidecar")
    return value


def prepare_context(foreground: Path, state: dict, occurrence: dict) -> dict:
    """Pin engineering refs for one occurrence before its durable publication."""
    engineering = Engineering(foreground)
    ef = engineering.ef
    est = engineering.state()
    if est["open"] is not None:
        raise LadError("engineering_phase_pending")
    role = occurrence["role"]
    oid = occurrence["occurrence_id"]
    relative = f"{OCCURRENCES}/{oid}.json"
    if os.path.lexists(foreground / relative):
        return _sidecar(engineering, oid)
    if role in {"checker", "doublechecker", "updater", "arbiter"}:
        # Review roles see exactly the checked authority; drift or stale checks
        # hold before dispatch instead of being absorbed by a fresh snapshot.
        latest_candidate(engineering, state)
    facts = ef.binding_facts(
        engineering.graph, engineering.profile, session_id=state["session_id"],
        occurrence_id=oid, role=role,
        graph_revision=digest(read(control_root(), "workflow.json")),
    )
    if (engineering.profile["family_policy"] != "none"
            and role in {"builder", "checker", "doublechecker"}
            and facts["configured_family"] is None):
        raise LadError("configured_family_unknown")
    # Pre-freeze Checker receives requirements/profile only; post-freeze roles
    # receive accepted check evidence refs (never raw passes).
    exposed = role != "checker" and role != "learning"
    baseline = []
    candidate = []
    if exposed and est["baseline"] is not None:
        baseline = [_copy_ref(row["acceptance_ref"]) for row in engineering.load(
            est["baseline"], ef.PHASE
        )["checks"]]
    if exposed and est["latest_candidate"] is not None:
        candidate = [_copy_ref(row["acceptance_ref"]) for row in engineering.load(
            est["latest_candidate"], ef.PHASE
        )["checks"]]
    policy_raw = canonical(engineering.profile["artifact_policy"]) + b"\n"
    artifact_policy_ref = ef.write_immutable(
        foreground, "lad-state/engineering/artifact-policy.json", policy_raw
    )
    # Context refs are relative to the copy root (the parent of foreground/).
    context = ef.context_record(
        occurrence_id=oid,
        profile_ref=engineering.profile_ref,
        requirements_ref=ef.make_ref(ef.SNAPSHOT_COPY, engineering.requirements_raw),
        baseline_checks=baseline,
        candidate_checks=candidate,
        binding_facts_ref=_copy_ref(engineering.store(facts)),
        artifact_policy_ref=_copy_ref(artifact_policy_ref),
    )
    target = engineering.target()
    from lad_state import _git_facts

    sidecar = {
        "format": "millrace-lad-engineering-occurrence/v1",
        "occurrence_id": oid,
        "role": role,
        "context_ref": engineering.store(context),
        "context": context,
        "products_ref": engineering.observation(engineering.products()),
        "raw_git": _git_facts(target, None),
        "retained_requirements": sorted(retained_ids(engineering, state))
        if state["criteria"] else sorted(
            row["id"] for row in ef.active_requirements(engineering.requirements)
        ),
    }
    ef.write_immutable(foreground, relative, ef.record_bytes(sidecar))
    return sidecar


def _copy_ref(pinned: dict) -> dict:
    return {"path": "foreground/" + pinned["path"], "sha256": pinned["sha256"]}


def materialize_context(foreground: Path, workspace: Path, oid: str) -> str:
    engineering = Engineering(foreground)
    sidecar = _sidecar(engineering, oid)
    raw = engineering.ef.record_bytes(sidecar["context"])
    path = f"lad-state/occurrences/{oid}/engineering-context.json"
    engineering.ef.write_immutable(workspace, path, raw)
    return (
        f"Engineering context: {path} (SHA-256 {digest(raw)}). It pins the "
        "captured profile, requirements snapshot and accepted check evidence refs "
        "relative to the copy root; records under foreground/lad-state/engineering "
        "use foreground-relative refs internally.\n"
    )


# ---------------------------------------------------------- admit/finalize


def admit() -> None:
    foreground, _ = roots()
    state = load(foreground)
    assert_foreground(state)
    engineering = Engineering(foreground)
    ef = engineering.ef
    if state["continuation"]["node"] != "prepare_arbiter":
        raise LadError("engineering_entry_mismatch")
    est = engineering.state()
    _, candidate = latest_candidate(engineering, state)
    record, source_ref = engineering.source(state)
    updater = _latest_accepted(state, "updater")
    if updater is None:
        raise LadError("engineering_updater_admission_required")
    admission_ref, admission_raw = _artifact(foreground, updater,
                                             "artifact-admission.json")
    try:
        admission = ef.validate_admission(
            ef.parse(admission_raw), session_id=state["session_id"],
            task_id=est["task_id"],
            source_sha256=state["accepted"][updater]["result"]["source_sha256"],
            profile_sha256=engineering.profile_ref["sha256"],
        )
        findings, removals = ef.admit_delta(
            admission, engineering.delta(est, record),
            engineering.profile["artifact_policy"], session_id=state["session_id"],
            workspace=foreground,
        )
    except ef.EngineeringError as exc:
        raise LadError(exc.code) from exc
    if findings:
        emit("FINDINGS", "Artifact admission hold: " + "; ".join(findings[:8]))
        return
    for row in removals:
        (foreground / row["path"]).unlink(missing_ok=True)
    est["admission"] = {
        "admission_ref": admission_ref,
        "source_ref": source_ref,
        "candidate_phase": est["latest_candidate"],
        "removed": [row["path"] for row in removals],
    }
    del candidate
    engineering.save(est)
    emit("ADMITTED", "Actual scoped delta matches artifact admission.")


def _latest_accepted(state: dict, role: str) -> str | None:
    for event in reversed(state["events"]):
        oid = event["id"]
        if (event["kind"] == "role" and oid in state["accepted"]
                and state["occurrences"][oid]["role"] == role):
            return oid
    return None


def _artifact(foreground: Path, oid: str, name: str) -> tuple[dict, bytes]:
    relative = f"lad-results/{oid}/{name}"
    raw = read(foreground, relative)
    return {"path": "foreground/" + relative, "sha256": digest(raw)}, raw


def finalize() -> None:
    foreground, project = roots()
    state = load(foreground)
    engineering = Engineering(foreground)
    ef = engineering.ef
    est = engineering.state()
    if (state["continuation"]["node"] != "emit_learning_request"
            or state["terminal"] != "succeeded"):
        raise LadError("engineering_entry_mismatch")
    arbiter = state["continuation"]["occurrence_id"]
    if (arbiter not in state["accepted"]
            or state["occurrences"][arbiter]["role"] != "arbiter"
            or state["accepted"][arbiter]["result"]["outcome"] != "PASS"):
        raise LadError("engineering_arbiter_pass_required")
    if est["acceptance"] is not None:
        emit("FINALIZED", "Exact acceptance projection replay.")
        return
    _, candidate = latest_candidate(engineering, state)
    record, source_ref = engineering.source(state)
    admission = est["admission"]
    if admission is None or engineering.drift(state, admission["source_ref"])[
        "kinds"
    ] or admission["candidate_phase"] != est["latest_candidate"]:
        raise LadError("engineering_admission_source_changed")
    original_ref, _ = _artifact(foreground, arbiter, "result.json")
    retained_ref, retained_raw = _artifact(foreground, arbiter, "retained-verdict.json")
    retained = ef.parse(retained_raw)
    if any(row["status"] != "pass" for row in retained["verdict"].values()):
        raise LadError("retained_requirement_not_passed")
    projection = ef.acceptance_projection(
        session_id=state["session_id"], task_id=est["task_id"],
        profile_ref=engineering.profile_ref,
        requirements_ref=ef.make_ref(ef.SNAPSHOT_COPY, engineering.requirements_raw),
        candidate_source_ref={"path": "foreground/" + source_ref["path"],
                              "sha256": source_ref["sha256"]},
        original_verdict_ref=original_ref,
        retained_verdict_ref=retained_ref,
        artifact_admission_ref=admission["admission_ref"],
    )
    ref = ef.write_immutable(foreground, "lad-state/engineering/acceptance.json",
                             ef.record_bytes(projection))
    est["acceptance"] = {"ref": ref, "candidate_phase": est["latest_candidate"],
                         "superseded": superseded(engineering, state)}
    del candidate, project
    engineering.save(est)
    emit("FINALIZED", "Engineering acceptance projection written; history "
         "publication remains a separate explicit operation.")


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser("phase").add_argument("kind",
                                              choices=["baseline", "candidate"])
    for name in ("check", "accept"):
        child = commands.add_parser(name)
        child.add_argument("kind", choices=["baseline", "candidate"])
        child.add_argument("node")
    commands.add_parser("admit")
    commands.add_parser("finalize")
    args = parser.parse_args()
    if not enabled():
        raise LadError("engineering_profile_not_captured")
    if args.operation == "phase":
        phase(args.kind)
    elif args.operation == "check":
        check(args.kind, args.node)
    elif args.operation == "accept":
        accept(args.kind, args.node)
    elif args.operation == "admit":
        admit()
    else:
        finalize()


if __name__ == "__main__":
    def checked() -> None:
        try:
            main()
        except LadError as exc:
            # Undeclared/diverged products and unknown closure are holds.
            code = str(exc)
            if code in {"undeclared_output", "engineering_product_diverged",
                        "engineering_owned_path_was_foreign",
                        "product_link_or_special", "engineering_source_changed",
                        "engineering_source_drift"}:
                emit("RECONCILE", "LAD engineering hold: " + code)
                return
            raise

    run(checked)
