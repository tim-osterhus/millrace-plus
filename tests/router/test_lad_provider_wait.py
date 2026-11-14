"""Module-level B5B proofs for strict LAD provider-wait records (C2).

Every clock reading is an injected aware UTC ``datetime``; nothing sleeps. Bridge
records are real C1 ``native_diagnostic`` publications. Core views below use the
exact public ``inspect_run`` key names but are assembled in-process: they prove
record/refusal behavior only. Actual graph, PublicRuntime and service-child
proofs are C3.
"""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from millrace_plus import engineering_feedback as ef
from millrace_plus import provider_wait as pw

POLICY = json.dumps(
    {
        "format": "millrace-lad-provider-policy/v1",
        "first_retry_seconds": 900,
        "second_retry_seconds": 3600,
        "troubleshooter_binding": "claude",
        "automatic_wait": "optional",
    }
).encode()
T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
SESSION = "6f1c2f9e-0000-4000-8000-000000000001"
SETTINGS = {"model": "claude-opus-5-5", "reasoning_effort": "high"}
CLAUDE = ("claude_print", "millrace-claude-print/v1", "claude-print-json/v1")
BRIDGE = {
    "context_path": "lad-state/native-context.json",
    "outbox_path": "lad-state/native-diagnostics",
}
# Synthetic secret-bearing native stream; it must never reach provider state.
SECRET = "SECRET-SENTINEL-do-not-retain"
DETAILS = {
    "format": "millrace-claude-print-launch/v2",
    "diagnostic_bridge": BRIDGE,
    "plane_id": "foreground",
    "node_id": "builder",
    "producing_revision": "rev1",
}


def policy(raw: bytes = POLICY) -> dict[str, object]:
    return pw.load_policy(raw)


def authority(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "session_id": SESSION,
        "service": None,
        "packet_lineage": {"packet_id": "p1", "sequence": 1},
        "role": "builder",
        "pending_route": {"kind": "execution", "target": "builder"},
        "original_occurrence": "occ1",
        "input_sha256": "1" * 64,
        "request_sha256": "2" * 64,
        "source_sha256": "3" * 64,
        "guidance_sha256": "4" * 64,
        "policy_sha256": ef.sha256(POLICY),
        "binding": {
            "adapter_id": "claude_print",
            "contract": "millrace-claude-print/v1",
            "config_sha256": ef.sha256(ef.encode(SETTINGS)),
            "requested_model": "claude-opus-5-5",
            "requested_effort": "high",
            "resolved_model": "claude-opus-5-5",
            "resolved_effort": "high",
            "host_route_sha256": "5" * 64,
        },
        "native_owner": {
            "invocation_id": "inv1",
            "plane_id": "foreground",
            "node_id": "builder",
            "producing_revision": "rev1",
        },
    }
    values.update(changes)
    return pw.authority_record(**values)


def bridge(
    fg: Path,
    occ: str,
    inv: str,
    status: object = 429,
    failure: str = "claude_native_exit",
) -> tuple[dict[str, object], dict]:
    """Publish a genuine C1 bridge record and return (ref, record json)."""
    from millrace_plus.router import native_diagnostic as nd

    record = nd.NativeDiagnostic(
        context_id=occ,
        input_sha256="1" * 64,
        invocation_id=inv,
        plane_id="foreground",
        node_id="builder",
        producing_revision="rev1",
        adapter_id=CLAUDE[0],
        adapter_contract=CLAUDE[1],
        evidence_format="millrace-claude-print-result/v2",
        launch_sha256=nd.digest(nd.canonical(DETAILS)),
        transport="execution_failure",
        writer_state="stopped",
        failure_code=failure,
        protocol=CLAUDE[2],
        api_error_status=status if type(status) is int else None,
    )
    ref = nd.publish(fg, nd.BridgeConfig.load(BRIDGE), record)
    return ref.to_json(), record.to_json()


def facts(record: dict, ref: dict | None, **changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "adapter_id": CLAUDE[0],
        "adapter_contract": CLAUDE[1],
        "observed_version": "2.1.305",
        "transport": "execution_failure",
        "writer_state": "stopped",
        "failure_code": record["failure_code"],
        "native_diagnostic": record["native_diagnostic"],
        "bridge_ref": ref,
        "bridge_record": record if ref is not None else None,
        "effects_resolved": True,
    }
    values.update(changes)
    return values


def view(fg: Path, inv: str, ref: dict | None, status: object = 429) -> dict:
    """A Core-shaped full inspection view for one paused provider wait."""
    return {
        "state_version": 7,
        "quiescent": True,
        "active_revision": "rev1",
        "planes": {
            "foreground": {
                "status": "paused",
                "active_invocation": None,
                "resume_target": "provider_due_builder",
            }
        },
        "invocations": [
            {
                "id": inv,
                "plane_id": "foreground",
                "node": "builder",
                "revision_id": "rev1",
                "cwd": str(fg),
                "launch": {
                    "adapter_id": CLAUDE[0],
                    "adapter_contract": CLAUDE[1],
                    "settings": SETTINGS,
                    "details": DETAILS,
                    "producing_revision": "rev1",
                },
                "adapter_evidence": {
                    "format": "millrace-claude-print-result/v2",
                    "diagnostic_bridge": ref,
                    "native_diagnostic": {
                        "protocol": CLAUDE[2],
                        "api_error_status": status,
                    },
                },
            }
        ],
        # Exact Core `Store.inspection` completion keys (row columns plus
        # effective_writer and stdout_base64).
        "completions": [
            {
                "invocation_id": inv,
                "run_id": "run",
                "revision_id": "rev1",
                "plane_id": "foreground",
                "node": "builder",
                "transport": "execution_failure",
                "writer": "stopped",
                "diagnostic": "claude_native_exit",
                "exit_code": 1,
                "classification": None,
                "outcome": None,
                "effective_writer": "stopped",
                "stdout_base64": base64.b64encode(SECRET.encode()).decode(),
                "stdout": SECRET,
                "stderr": SECRET,
            }
        ],
        "transitions": [
            {
                "completion_id": inv,
                "node": "builder",
                "target": "classify_provider_builder",
                "revision_id": "rev1",
                "version": 6,
            }
        ],
    }


def tree(fg: Path) -> dict[str, bytes]:
    root = fg / pw.ROOT
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class Flow:
    """Drive one operation through the pure API with an injected clock."""

    def __init__(self, tmp_path: Path, **changes: object) -> None:
        self.fg = tmp_path / "foreground"
        self.fg.mkdir(parents=True)
        pw.initialize_index(self.fg, session_id=SESSION, policy_raw=POLICY)
        self.authority = authority(**changes)
        self.op = str(self.authority["operation_id"])
        self.count = 0
        self.occurrence = "occ1"
        self.observation = ""
        # C3 owns the semantic reconciliation; C2 needs real bytes and digests.
        self.delta = ef.write_immutable(self.fg, "lad-state/delta.json", b"delta")
        self.effects = ef.write_immutable(self.fg, "lad-state/effects.json", b"fx")

    def fail(
        self, at: datetime, status: object = 429, *, floor=None, writer: str = "stopped"
    ) -> tuple[str, dict | None]:
        self.count += 1
        self.inv, self.status = f"inv{self.count}", status
        self.ref, self.record = bridge(self.fg, self.occurrence, self.inv, status)
        found = pw.classify(**facts(self.record, self.ref, writer_state=writer))
        return pw.classify_failure(
            self.fg,
            authority=self.authority,
            classification=found,
            occurrence_id=self.occurrence,
            diagnostic_ref=self.ref,
            failed_at=at,
            current_source_sha256="3" * 64,
            policy=policy(),
            provider_floor=floor,
        )

    def replay(
        self, at: datetime, found: pw.Classification | None = None
    ) -> tuple[str, dict | None]:
        """Re-present the latest exact completion with another clock reading."""
        return pw.classify_failure(
            self.fg,
            authority=self.authority,
            classification=found or pw.classify(**facts(self.record, self.ref)),
            occurrence_id=self.occurrence,
            diagnostic_ref=self.ref,
            failed_at=at,
            current_source_sha256="3" * 64,
            policy=policy(),
        )

    def events(self) -> tuple[dict, ...]:
        return pw.load_operation(self.fg, self.op).events

    def observe(self, at: datetime) -> dict:
        operation = pw.load_operation(self.fg, self.op)
        result = pw.observe_provider(
            self.fg,
            operation_id=self.op,
            observed_at=at,
            facts=pw.observation_facts(
                view(self.fg, self.inv, self.ref, self.status),
                authority=self.authority,
                failure=pw._latest_failure(operation),
                instance_id="inst",
                run_id="run",
                state_home="/state",
                plane_id="foreground",
                invocation_id=self.inv,
                expected_state_version=7,
                foreground=self.fg,
            ),
        )
        self.observation = str(result["observation"]["observation_id"])
        return result

    def due(
        self, at: datetime, restoration: bytes | None = None, raw: bytes = POLICY
    ) -> dict:
        return pw.check_provider_due(
            self.fg,
            operation_id=self.op,
            observation_id=self.observation,
            now=at,
            policy=policy(raw),
            restoration=restoration,
        )

    def reserve(self, kind: str = "retry", new: str | None = None) -> dict:
        new = new or f"occ{self.count + 1}"
        result = pw.reserve_provider_attempt(
            self.fg,
            operation_id=self.op,
            kind=kind,
            previous_occurrence=self.occurrence,
            new_occurrence=new,
            input_sha256="6" * 64,
            current_source_sha256="3" * 64,
            file_delta_ref=self.delta,
            effects_ref=self.effects,
        )
        if kind == "retry":
            self.occurrence = new
        return result

    def restoration(self, at: datetime, **changes: object) -> bytes:
        values: dict[str, object] = {
            "operation_id": self.op,
            "policy_sha256": ef.sha256(POLICY),
            "kind": "operator_confirmation",
            "actor": "operator",
            "request_id": "restore-1",
            "observed_at": pw.stamp(at),
            "evidence_ref": None,
            "statement": "Provider access restored.",
        }
        values.update(changes)
        return ef.encode(pw.restoration_record(**values))

    def tip(self) -> dict:
        return pw.load_operation(self.fg, self.op).tip


def test_provider_classification_is_conservative(tmp_path):
    fg = tmp_path / "foreground"
    fg.mkdir()
    rows = {
        429: ("provider_usage", "PROVIDER_WAIT"),
        500: ("provider_availability", "PROVIDER_WAIT"),
        502: ("provider_availability", "PROVIDER_WAIT"),
        503: ("provider_availability", "PROVIDER_WAIT"),
        504: ("provider_availability", "PROVIDER_WAIT"),
        529: ("provider_availability", "PROVIDER_WAIT"),
        401: ("provider_access", "PROVIDER_HUMAN_REQUIRED"),
        403: ("permission_required", "PROVIDER_HUMAN_REQUIRED"),
        418: ("unclassified", "ORDINARY_ERROR"),
        None: ("unclassified", "ORDINARY_ERROR"),
    }
    for number, (status, expected) in enumerate(rows.items()):
        ref, record = bridge(fg, "occ1", f"inv{number}", status)
        for version in ("2.1.296", "2.1.305"):
            found = pw.classify(**facts(record, ref, observed_version=version))
            assert (found.classification, found.outcome) == expected
    ref, record = bridge(fg, "occ1", "invx", 429)
    unclassified = ("unclassified", "ORDINARY_ERROR")
    for change in (
        {"observed_version": "2.1.200"},
        {"observed_version": None},
        {
            "failure_code": "claude_timeout",
            "bridge_record": {**record, "failure_code": "claude_timeout"},
        },
        {
            "adapter_id": "codex_exec",
            "adapter_contract": "millrace-codex-exec/v1",
            "native_diagnostic": {
                "protocol": "codex-exec-jsonl/v1",
                "api_error_status": None,
            },
            "bridge_record": {
                **record,
                "adapter_id": "codex_exec",
                "adapter_contract": "millrace-codex-exec/v1",
                "native_diagnostic": {
                    "protocol": "codex-exec-jsonl/v1",
                    "api_error_status": None,
                },
            },
        },
        {
            "native_diagnostic": {"protocol": CLAUDE[2], "api_error_status": True},
            "bridge_record": {
                **record,
                "native_diagnostic": {"protocol": CLAUDE[2], "api_error_status": True},
            },
        },
        # Native facts retained but the handoff is unavailable: never positive.
        {"bridge_ref": None, "bridge_record": None},
    ):
        found = pw.classify(**facts(record, ref, **change))
        assert (found.classification, found.outcome) == unclassified, change
    distinct = {
        "timeout": ({"transport": "timeout"}, "ORDINARY_ERROR"),
        "cancelled": ({"transport": "cancelled"}, "ORDINARY_ERROR"),
        "unknown_writer": ({"writer_state": "may_live"}, "UNCERTAIN"),
        "unknown_effects": ({"effects_resolved": False}, "UNCERTAIN"),
        "ordinary_error": (
            {"native_diagnostic": None, "bridge_ref": None, "bridge_record": None},
            "ORDINARY_ERROR",
        ),
    }
    for name, (change, outcome) in distinct.items():
        found = pw.classify(**facts(record, ref, **change))
        assert (found.classification, found.outcome) == (name, outcome)
    for change in (
        {"bridge_record": None},
        {
            "bridge_record": {
                **record,
                "native_diagnostic": {"protocol": CLAUDE[2], "api_error_status": 500},
            }
        },
        {"bridge_record": {**record, "writer_state": "may_live"}},
        {"native_diagnostic": None},
    ):
        found = pw.classify(**facts(record, ref, **change))
        assert (found.classification, found.outcome) == (None, "UNCERTAIN")
    with pytest.raises(pw.ProviderWaitError, match="provider_not_failure"):
        pw.classify(**facts(record, ref, transport="completed"))
    # Ordinary fallback without an existing operation creates no record at all.
    pw.initialize_index(fg, session_id=SESSION, policy_raw=POLICY)
    before = tree(fg)
    ordinary = pw.classify(**facts(record, None, bridge_record=None))
    assert pw.classify_failure(
        fg,
        authority=authority(),
        classification=ordinary,
        occurrence_id="occ1",
        diagnostic_ref=None,
        failed_at=T0,
        current_source_sha256="3" * 64,
        policy=policy(),
    ) == ("ORDINARY_ERROR", None)
    assert tree(fg) == before


def test_provider_operation_reload_and_binding_fences(tmp_path, monkeypatch):
    flow = Flow(tmp_path)
    outcome, first = flow.fail(T0)
    assert outcome == "PROVIDER_WAIT" and first is not None
    assert first["streak"] == 1 and first["core_locator"] is None
    assert first["due_at"] == (T0 + timedelta(seconds=900)).isoformat()
    assert flow.op == pw.operation_id(flow.authority)
    # The same exact completion replays its fact and never counts twice.
    found = pw.classify(
        **facts(json.loads((flow.fg / flow.ref["path"]).read_bytes()), flow.ref)
    )
    replay = pw.classify_failure(
        flow.fg,
        authority=flow.authority,
        classification=found,
        occurrence_id="occ1",
        diagnostic_ref=flow.ref,
        failed_at=T0,
        current_source_sha256="3" * 64,
        policy=policy(),
    )
    assert (
        replay == ("PROVIDER_WAIT", first)
        and len(pw.load_operation(flow.fg, flow.op).events) == 1
    )
    # A fresh completion without reservation (restart/new ID) refuses; no reset.
    saved = (flow.inv, flow.ref, flow.status)
    with pytest.raises(pw.ProviderWaitError, match="provider_attempt_unreserved"):
        flow.fail(T0 + timedelta(seconds=5))
    flow.inv, flow.ref, flow.status = saved
    assert flow.tip()["streak"] == 1
    # Binding, policy and role fences.
    other = authority(
        binding={**flow.authority["binding"], "resolved_model": "claude-sonnet-5-5"}
    )
    assert other["operation_id"] != flow.op
    with pytest.raises(pw.ProviderWaitError, match="provider_authority_stale"):
        pw.classify_failure(
            flow.fg,
            authority=flow.authority,
            classification=found,
            occurrence_id="occ1",
            diagnostic_ref=flow.ref,
            failed_at=T0,
            current_source_sha256="3" * 64,
            policy=policy(POLICY.replace(b"900", b"901")),
        )
    with pytest.raises(pw.ProviderWaitError, match="provider_role_excluded"):
        authority(role="learning")
    forged = {**flow.authority, "role": "checker"}
    with pytest.raises(pw.ProviderWaitError, match="provider_authority_invalid"):
        pw.classify_failure(
            flow.fg,
            authority=forged,
            classification=found,
            occurrence_id="occ1",
            diagnostic_ref=flow.ref,
            failed_at=T0,
            current_source_sha256="3" * 64,
            policy=policy(),
        )
    # Hostile mutations of every stored link refuse on reload.
    folder = flow.fg / pw.ROOT / "operations" / flow.op
    original = (folder / "authority.json").read_bytes()
    (folder / "authority.json").write_bytes(original.replace(b"builder", b"checker"))
    with pytest.raises(pw.ProviderWaitError, match="provider_authority_invalid"):
        pw.load_operation(flow.fg, flow.op)
    (folder / "authority.json").write_bytes(original)
    event = (folder / "events/1.json").read_bytes()
    (folder / "events/1.json").write_bytes(event.replace(b'"streak":1', b'"streak":0'))
    with pytest.raises(pw.ProviderWaitError, match="provider_chain_invalid"):
        pw.load_operation(flow.fg, flow.op)
    (folder / "events/1.json").write_bytes(event)
    (folder / "events/3.json").write_bytes(event)
    with pytest.raises(pw.ProviderWaitError, match="provider_chain_forked"):
        pw.load_operation(flow.fg, flow.op)
    (folder / "events/3.json").unlink()
    index_path = flow.fg / pw.ROOT / "index.json"
    index = index_path.read_bytes()
    index_path.write_bytes(
        index.replace(b'"sequence":1', b'"sequence":2').replace(
            b"/events/1.json", b"/events/2.json"
        )
    )
    with pytest.raises(pw.ProviderWaitError, match="provider_chain_forked"):
        pw.load_operation(flow.fg, flow.op)
    index_path.unlink()
    with pytest.raises(pw.ProviderWaitError, match="provider_index_missing"):
        pw.load_operation(flow.fg, flow.op)
    index_path.write_bytes(index)
    # A crash after the next event but before the index recovers that event only.
    flow.observe(T0 + timedelta(seconds=1))
    real = pw._publish_index
    monkeypatch.setattr(
        pw, "_publish_index", lambda *a: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        flow.due(T0 + timedelta(seconds=2))
    monkeypatch.setattr(pw, "_publish_index", real)
    with pytest.raises(pw.ProviderWaitError, match="provider_index_behind"):
        pw.load_operation(flow.fg, flow.op, recover=False)
    recovered = pw.load_operation(flow.fg, flow.op)
    assert recovered.tip["kind"] == "due_checked" and recovered.tip["streak"] == 1
    assert pw.load_operation(flow.fg, flow.op, recover=False).tip == recovered.tip
    with pytest.raises(pw.ProviderWaitError, match="provider_index_conflict"):
        pw.initialize_index(
            flow.fg, session_id=SESSION, policy_raw=POLICY.replace(b"900", b"901")
        )


def test_provider_due_floor_and_duplicate_resume(tmp_path):
    flow = Flow(tmp_path)
    flow.fail(T0)
    with pytest.raises(pw.ProviderWaitError, match="provider_observation_required"):
        flow.due(T0 + timedelta(hours=2))
    flow.observe(T0 + timedelta(seconds=1))
    early = flow.due(T0 + timedelta(seconds=899))
    assert early == {
        "operation_id": flow.op,
        "observation_id": flow.observation,
        "phase": "waiting",
        "due_at": (T0 + timedelta(seconds=900)).isoformat(),
        "operator_return_required": True,
        "eligible": False,
    }
    sequence = flow.tip()["sequence"]
    # Duplicate and backward-clock checks reuse the same fact and stay held.
    assert flow.due(T0 - timedelta(hours=1)) == early
    assert flow.tip()["sequence"] == sequence
    with pytest.raises(pw.ProviderWaitError, match="provider_clock_invalid"):
        flow.due(datetime(2026, 11, 13, 12, 30))
    with pytest.raises(pw.ProviderWaitError, match="provider_authority_stale"):
        flow.due(T0 + timedelta(hours=2), raw=POLICY.replace(b"900", b"60"))
    ready = flow.due(T0 + timedelta(seconds=900))
    assert ready["eligible"] is True and ready["phase"] == "retry_ready"
    assert flow.due(T0 + timedelta(seconds=901)) == ready
    # N7: an observe replay keeps its exact identity and reports the current tip.
    count = len(flow.events())
    observed = [e for e in flow.events() if e["kind"] == "observed"][-1]
    again = flow.observe(T0 + timedelta(minutes=30))
    assert again["event"] == observed and again["tip"]["phase"] == "retry_ready"
    assert again["observation"]["observed_at"] == pw.stamp(T0 + timedelta(seconds=1))
    assert len(flow.events()) == count
    flow.reserve()
    # Second failure: +3,600 from that failure, with a later provider floor.
    t1 = T0 + timedelta(hours=1)
    floor = t1 + timedelta(hours=2)
    outcome, second = flow.fail(t1, 503, floor=floor)
    assert outcome == "PROVIDER_WAIT" and second is not None
    assert second["streak"] == 2 and second["due_at"] == floor.isoformat()
    flow.observe(t1 + timedelta(seconds=1))
    assert flow.due(t1 + timedelta(seconds=3600))["eligible"] is False
    stale = flow.restoration(t1 - timedelta(seconds=1))
    with pytest.raises(pw.ProviderWaitError, match="provider_restoration_mismatch"):
        flow.due(t1 + timedelta(seconds=10), restoration=stale)
    restored = flow.restoration(t1 + timedelta(seconds=10))
    # Restoration waives the local delay but never the later provider floor.
    held = flow.due(t1 + timedelta(seconds=20), restoration=restored)
    assert held["eligible"] is False and held["phase"] == "waiting"
    kinds = [e["kind"] for e in pw.load_operation(flow.fg, flow.op).events]
    assert kinds.count("restoration_recorded") == 1
    assert flow.due(floor)["eligible"] is True
    with pytest.raises(pw.ProviderWaitError, match="provider_restoration_invalid"):
        flow.due(floor, restoration=restored.replace(b"operator", b"intruder"))


def test_provider_retry_reservation_crash_replay(tmp_path, monkeypatch):
    flow = Flow(tmp_path)
    flow.fail(T0)
    flow.observe(T0)
    with pytest.raises(pw.ProviderWaitError, match="provider_not_ready"):
        flow.reserve()
    flow.due(T0 + timedelta(seconds=900))
    with pytest.raises(pw.ProviderWaitError, match="provider_effects_unresolved"):
        pw.reserve_provider_attempt(
            flow.fg,
            operation_id=flow.op,
            kind="retry",
            previous_occurrence="occ1",
            new_occurrence="occ2",
            input_sha256="6" * 64,
            current_source_sha256="3" * 64,
            file_delta_ref=flow.delta,
            effects_ref=None,
        )  # type: ignore[arg-type]
    # N4: refs must name existing bytes with that digest; occurrences are fenced.
    for delta, previous, new in (
        ({"path": "lad-state/missing.json", "sha256": "7" * 64}, "occ1", "occ2"),
        ({**flow.delta, "sha256": "7" * 64}, "occ1", "occ2"),
    ):
        with pytest.raises(pw.ProviderWaitError, match="provider_effects_unresolved"):
            pw.reserve_provider_attempt(
                flow.fg,
                operation_id=flow.op,
                kind="retry",
                previous_occurrence=previous,
                new_occurrence=new,
                input_sha256="6" * 64,
                current_source_sha256="3" * 64,
                file_delta_ref=delta,
                effects_ref=flow.effects,
            )
    for previous, new in (("occ9", "occ2"), ("occ1", "occ1")):
        flow.occurrence = previous
        with pytest.raises(pw.ProviderWaitError, match="provider_reservation_conflict"):
            flow.reserve(new=new)
    flow.occurrence = "occ1"
    assert flow.tip()["kind"] == "due_checked"
    real = pw._append
    monkeypatch.setattr(
        pw, "_append", lambda *a, **k: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        flow.reserve(new="occ2")
    monkeypatch.setattr(pw, "_append", real)
    first = flow.reserve(new="occ2")
    assert first["event"]["kind"] == "retry_reserved" and first["event"]["attempt"] == 2
    flow.occurrence = "occ1"
    assert flow.reserve(new="occ2") == first
    # R5: a replay reuses the reservation only for the exact original request.
    other = ef.write_immutable(flow.fg, "lad-state/effects-2.json", b"fx2")
    for source, effects in (("9" * 64, flow.effects), ("3" * 64, other)):
        with pytest.raises(pw.ProviderWaitError, match="provider_reservation_conflict"):
            pw.reserve_provider_attempt(
                flow.fg,
                operation_id=flow.op,
                kind="retry",
                previous_occurrence="occ1",
                new_occurrence="occ2",
                input_sha256="6" * 64,
                current_source_sha256=source,
                file_delta_ref=flow.delta,
                effects_ref=effects,
            )
    assert flow.tip() == first["event"]
    flow.occurrence = "occ1"
    with pytest.raises(pw.ProviderWaitError, match="provider_reservation_conflict"):
        flow.reserve(new="occ3")
    flow.occurrence = "occ2"
    # A duplicate resume after reservation grants nothing new.
    assert flow.due(T0 + timedelta(hours=3))["eligible"] is False
    sequence = flow.tip()["sequence"]
    # An intervening ordinary failure preserves the streak and records nothing.
    ordinary = pw.Classification("ordinary_error", "ORDINARY_ERROR")
    assert pw.classify_failure(
        flow.fg,
        authority=flow.authority,
        classification=ordinary,
        occurrence_id="occ2",
        diagnostic_ref=None,
        failed_at=T0 + timedelta(hours=3),
        current_source_sha256="3" * 64,
        policy=policy(),
    ) == ("ORDINARY_ERROR", None)
    assert flow.tip()["sequence"] == sequence and flow.tip()["streak"] == 1
    # Unknown writer is recorded once, never counted and grants no retry.
    outcome, uncertain = flow.fail(T0 + timedelta(hours=4), writer="may_live")
    assert outcome == "UNCERTAIN" and uncertain is not None
    assert uncertain["kind"] == "uncertainty_recorded" and uncertain["streak"] == 1
    # B2: re-presenting that exact completion as positive stays UNCERTAIN.
    count = len(flow.events())
    positive = pw.Classification("provider_usage", "PROVIDER_WAIT")
    assert flow.replay(T0 + timedelta(hours=6), positive) == ("UNCERTAIN", uncertain)
    assert len(flow.events()) == count and flow.tip()["streak"] == 1
    assert flow.fail(T0 + timedelta(hours=4), writer="may_live")[1] == uncertain or (
        flow.tip()["kind"] == "uncertainty_recorded"
    )
    assert flow.tip()["streak"] == 1
    with pytest.raises(pw.ProviderWaitError, match="provider_reconciliation_required"):
        flow.due(T0 + timedelta(hours=5))
    # The reconciled same generation can still fail positively and count once.
    outcome, second = flow.fail(T0 + timedelta(hours=5))
    assert outcome == "PROVIDER_WAIT" and second is not None and second["streak"] == 2
    events = pw.load_operation(flow.fg, flow.op).events
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))


def test_provider_third_failure_single_diagnosis(tmp_path):
    for resolved in (False, True):
        flow = Flow(tmp_path / str(resolved))
        at = T0
        for streak in (1, 2):
            flow.fail(at)
            flow.observe(at)
            flow.due(at + timedelta(hours=2))
            flow.reserve()
            at += timedelta(hours=3)
        outcome, third = flow.fail(at)
        assert outcome == "PROVIDER_WAIT" and third is not None
        assert third["streak"] == 3 and third["due_at"] is None
        assert flow.observe(at)["event"]["phase"] == "diagnosis_required"
        with pytest.raises(pw.ProviderWaitError, match="provider_not_ready"):
            flow.reserve()
        assert flow.due(at)["eligible"] is True
        reserved = flow.reserve("diagnosis", new="diag1")
        assert reserved["preparation"]["kind"] == "diagnosis"
        raw = flow.restoration(at + timedelta(minutes=1))
        written = ef.write_immutable(
            flow.fg, f"{pw.ROOT}/restorations/{ef.sha256(raw)}.json", raw
        )
        ref = written if resolved else None
        # N3: an unresolved diagnosis cannot install restoration authority.
        with pytest.raises(pw.ProviderWaitError, match="provider_diagnosis_invalid"):
            pw.record_diagnosis(
                flow.fg,
                operation_id=flow.op,
                occurrence_id="diag1",
                resolved=False,
                restoration_ref=written,
            )
        with pytest.raises(pw.ProviderWaitError, match="provider_restoration_required"):
            pw.record_diagnosis(
                flow.fg,
                operation_id=flow.op,
                occurrence_id="diag1",
                resolved=True,
                restoration_ref=None,
            )
        recorded = pw.record_diagnosis(
            flow.fg,
            operation_id=flow.op,
            occurrence_id="diag1",
            resolved=resolved,
            restoration_ref=ref,
        )
        assert recorded == pw.record_diagnosis(
            flow.fg,
            operation_id=flow.op,
            occurrence_id="diag1",
            resolved=resolved,
            restoration_ref=ref,
        )
        if not resolved:
            assert recorded["phase"] == "human_required"
            held = flow.due(at + timedelta(days=1))
            assert held["eligible"] is False and held["phase"] == "human_required"
            continue
        assert flow.due(at + timedelta(minutes=2))["phase"] == "retry_ready"
        flow.reserve()
        # A further provider failure after diagnosis holds for a human.
        outcome, after = flow.fail(at + timedelta(hours=1))
        assert outcome == "PROVIDER_HUMAN_REQUIRED" and after is not None
        assert after["kind"] == "human_required" and after["streak"] == 4
        # B2: a replay with a later clock keeps the recorded human outcome.
        count = len(flow.events())
        assert flow.replay(at + timedelta(hours=2)) == (
            "PROVIDER_HUMAN_REQUIRED",
            after,
        )
        assert len(flow.events()) == count
        assert flow.observe(at + timedelta(hours=1))["event"]["phase"] == (
            "human_required"
        )
        assert flow.due(at + timedelta(days=1))["eligible"] is False
        with pytest.raises(pw.ProviderWaitError, match="provider_not_ready"):
            flow.reserve("diagnosis", new="diag2")
    access = Flow(tmp_path / "access")
    outcome, event = access.fail(T0, 401)
    assert outcome == "PROVIDER_HUMAN_REQUIRED" and event is not None
    assert (event["streak"], event["phase"], event["due_at"]) == (
        0,
        "human_required",
        None,
    )


def test_provider_success_reconciliation_lineage(tmp_path):
    def success(occ: str, lineage: dict, **changes: object) -> dict:
        values: dict[str, object] = {
            key: flow.authority[key]
            for key in (
                "role",
                "packet_lineage",
                "pending_route",
                "binding",
                "source_sha256",
            )
        }
        values.update(
            current_source_sha256="3" * 64,
            occurrence_id=occ,
            input_sha256="6" * 64,
            lineage=lineage,
            transport="completed",
            writer_state="stopped",
            accepted=True,
            native_owner={**flow.authority["native_owner"], "invocation_id": "invS"},
        )
        values.update(changes)
        return values

    flow = Flow(tmp_path / "retry")
    flow.fail(T0)
    flow.observe(T0)
    flow.due(T0 + timedelta(hours=1))
    reserved = flow.reserve(new="occ2")
    retry = {"kind": "retry", "preparation_ref": reserved["event"]["preparation_ref"]}
    before = flow.tip()
    for bad, code in (
        (success("occ2", retry, role="checker"), "provider_success_unrelated"),
        (
            success("occ2", retry, packet_lineage={"packet_id": "p2"}),
            "provider_success_unrelated",
        ),
        (
            success("occ2", retry, transport="execution_failure"),
            "provider_success_unverified",
        ),
        (success("occ2", retry, accepted=False), "provider_success_unverified"),
        (success("occ9", retry), "provider_success_unrelated"),
        (success("occ2", retry, input_sha256="9" * 64), "provider_success_unrelated"),
        # Retry lineage keeps the recorded-source membership rule.
        (
            success("occ2", retry, current_source_sha256="9" * 64),
            "provider_success_unrelated",
        ),
        (
            success("occ2", retry, current_source_sha256="zz"),
            "provider_success_invalid",
        ),
        (
            success("occR", {"kind": "replacement", "chain": ["occ1", ["occR"]]}),
            "provider_success_invalid",
        ),
        (
            success("occX", {"kind": "replacement", "chain": ["occZ", "occX"]}),
            "provider_success_unrelated",
        ),
    ):
        with pytest.raises(pw.ProviderWaitError, match=code):
            pw.reconcile_provider_success(flow.fg, operation_id=flow.op, success=bad)
    assert flow.tip() == before
    done = pw.reconcile_provider_success(
        flow.fg, operation_id=flow.op, success=success("occ2", retry)
    )
    assert done["kind"] == "succeeded" and done["streak"] == 1
    assert (
        pw.reconcile_provider_success(
            flow.fg, operation_id=flow.op, success=success("occ2", retry)
        )
        == done
    )
    with pytest.raises(pw.ProviderWaitError, match="provider_success_conflict"):
        pw.reconcile_provider_success(
            flow.fg, operation_id=flow.op, success=success("occ3", retry)
        )
    # Ordinary replacement after an intervening nonprovider failure also ends it,
    # even when that failure's partial edits changed the reconciled scoped source.
    replaced = Flow(tmp_path / "replacement")
    flow = replaced
    flow.fail(T0)
    ordinary = pw.Classification("timeout", "ORDINARY_ERROR")
    assert pw.classify_failure(
        flow.fg,
        authority=flow.authority,
        classification=ordinary,
        occurrence_id="occ1",
        diagnostic_ref=None,
        failed_at=T0 + timedelta(minutes=1),
        current_source_sha256="9" * 64,
        policy=policy(),
    ) == ("ORDINARY_ERROR", None)
    replacement = {"kind": "replacement", "chain": ["occ1", "occR"]}
    with pytest.raises(pw.ProviderWaitError, match="provider_success_unrelated"):
        pw.reconcile_provider_success(
            flow.fg,
            operation_id=flow.op,
            success=success("occR", replacement, pending_route={"kind": "other"}),
        )
    ended = pw.reconcile_provider_success(
        flow.fg,
        operation_id=flow.op,
        success=success("occR", replacement, current_source_sha256="9" * 64),
    )
    assert ended["phase"] == "succeeded"
    assert ended["current_source_sha256"] == "9" * 64
    # R8: retry -> nonprovider timeout with partial edits -> ordinary replacement.
    flow = Flow(tmp_path / "retry-replaced")
    flow.fail(T0)
    flow.observe(T0)
    flow.due(T0 + timedelta(hours=1))
    flow.reserve(new="occ2")
    assert pw.classify_failure(
        flow.fg,
        authority=flow.authority,
        classification=ordinary,
        occurrence_id="occ2",
        diagnostic_ref=None,
        failed_at=T0 + timedelta(hours=2),
        current_source_sha256="9" * 64,
        policy=policy(),
    ) == ("ORDINARY_ERROR", None)
    # R3: a chain whose tail is the retry generation is a mislabelled retry.
    with pytest.raises(pw.ProviderWaitError, match="provider_success_unrelated"):
        pw.reconcile_provider_success(
            flow.fg,
            operation_id=flow.op,
            success=success(
                "occ2",
                {"kind": "replacement", "chain": ["occ1", "occ2"]},
                current_source_sha256="9" * 64,
            ),
        )
    assert flow.tip()["kind"] == "retry_reserved"
    anchored = pw.reconcile_provider_success(
        flow.fg,
        operation_id=flow.op,
        success=success(
            "occ2R",
            {"kind": "replacement", "chain": ["occ2", "occ2R"]},
            current_source_sha256="9" * 64,
        ),
    )
    assert anchored["phase"] == "succeeded" and anchored["occurrence_id"] == "occ2R"
    # Diagnosis success never ends the pending generation's streak.
    diag = Flow(tmp_path / "diagnosis")
    flow = diag
    at = T0
    for _ in range(2):
        flow.fail(at)
        flow.observe(at)
        flow.due(at + timedelta(hours=2))
        flow.reserve()
        at += timedelta(hours=3)
    flow.fail(at)
    flow.observe(at)
    flow.due(at)
    prep = flow.reserve("diagnosis", new="diag1")
    with pytest.raises(pw.ProviderWaitError, match="provider_success_unrelated"):
        pw.reconcile_provider_success(
            flow.fg,
            operation_id=flow.op,
            success=success(
                "diag1",
                {"kind": "retry", "preparation_ref": prep["event"]["preparation_ref"]},
            ),
        )
    with pytest.raises(pw.ProviderWaitError, match="provider_success_unrelated"):
        pw.reconcile_provider_success(
            flow.fg,
            operation_id=flow.op,
            success=success(
                "occR", {"kind": "replacement", "chain": ["diag1", "occR"]}
            ),
        )
    assert flow.tip()["phase"] == "diagnosis_reserved"


def test_provider_observation_view_refusals_publish_nothing(tmp_path, monkeypatch):
    flow = Flow(tmp_path)
    flow.fail(T0)
    operation = pw.load_operation(flow.fg, flow.op)
    failure = pw._latest_failure(operation)
    before = tree(flow.fg)

    def check(mutate, code: str) -> None:
        current = view(flow.fg, flow.inv, flow.ref)
        mutate(current)
        with pytest.raises(pw.ProviderWaitError, match=code):
            pw.observation_facts(
                current,
                authority=flow.authority,
                failure=failure,
                instance_id="inst",
                run_id="run",
                state_home="/state",
                plane_id="foreground",
                invocation_id=flow.inv,
                expected_state_version=7,
                foreground=flow.fg,
            )
        assert tree(flow.fg) == before

    completion = lambda v: v["completions"][0]  # noqa: E731
    invocation = lambda v: v["invocations"][0]  # noqa: E731
    check(
        lambda v: completion(v).update(effective_writer="may_live"),
        "provider_writer_unresolved",
    )
    check(
        lambda v: v["planes"]["foreground"].update(resume_target="builder"),
        "provider_continuation_mismatch",
    )
    check(
        lambda v: v["planes"]["foreground"].update(status="running"),
        "provider_continuation_mismatch",
    )
    check(
        lambda v: v["transitions"][0].update(target="prepare_builder_replacement"),
        "provider_observation_mismatch",
    )
    check(lambda v: v.update(state_version=8), "provider_observation_mismatch")
    check(lambda v: completion(v).pop("exit_code"), "provider_observation_mismatch")
    check(
        lambda v: v["transitions"].append(dict(v["transitions"][0])),
        "provider_observation_mismatch",
    )
    check(
        lambda v: invocation(v)["launch"].update(settings={"model": "other"}),
        "provider_observation_mismatch",
    )
    check(
        lambda v: invocation(v).update(revision_id="rev2"),
        "provider_observation_mismatch",
    )
    check(
        lambda v: invocation(v).update(cwd=str(tmp_path)), "provider_workspace_mismatch"
    )
    check(
        lambda v: invocation(v)["adapter_evidence"].update(diagnostic_bridge=None),
        "provider_bridge_required",
    )
    check(
        lambda v: invocation(v)["adapter_evidence"].update(
            native_diagnostic={"protocol": CLAUDE[2], "api_error_status": 500}
        ),
        "provider_bridge_mismatch",
    )
    raw = (flow.fg / flow.ref["path"]).read_bytes()
    (flow.fg / flow.ref["path"]).write_bytes(raw.replace(b"429", b"503"))
    check(lambda v: None, "provider_bridge_mismatch")
    (flow.fg / flow.ref["path"]).write_bytes(raw)
    # Snapshot entry refusals publish nothing (Core refusal injected at the seam).
    project = tmp_path
    (project / "context").mkdir()
    (project / "context/provider-policy.json").write_bytes(POLICY)
    from millrace.router.execution import RouterError

    from millrace_plus.router import control

    for code in ("stale_state_version", "queued_controls", "run_nonterminal"):

        @contextmanager
        def refused(*args, _code=code, **kwargs):
            raise RouterError(_code)
            yield {}

        monkeypatch.setattr(control, "exclusive_run_snapshot", refused)
        with pytest.raises(RouterError, match=code):
            pw.observe_snapshot(
                project,
                state_home=tmp_path / "state",
                instance_id="inst",
                run_id="run",
                plane_id="foreground",
                operation_id=flow.op,
                invocation_id=flow.inv,
                expected_state_version=7,
                observed_at=T0,
            )
        assert tree(flow.fg) == before
    with pytest.raises(
        pw.ProviderWaitError, match="provider_service_selector_mismatch"
    ):
        pw.observe_snapshot(
            project,
            state_home=tmp_path / "state",
            instance_id="inst",
            run_id="run",
            plane_id="foreground",
            operation_id=flow.op,
            invocation_id=flow.inv,
            expected_state_version=7,
            observed_at=T0,
            service_home=tmp_path / "service",
            task="a" * 32,
        )
    assert tree(flow.fg) == before
    # A valid observation binds the locator once; another locator refuses.
    result = flow.observe(T0)
    # N1: the retained completion fact carries no raw native stream.
    fact = json.loads(
        (flow.fg / result["observation"]["completion_ref"]["path"]).read_bytes()
    )
    assert set(fact) == set(pw.COMPLETION_FACT_KEYS)
    assert fact["exit_code"] == 1 and fact["effective_writer"] == "stopped"
    encoded = base64.b64encode(SECRET.encode())
    for raw_bytes in tree(flow.fg).values():
        assert SECRET.encode() not in raw_bytes and encoded not in raw_bytes
    bound = flow.tip()["core_locator"]
    assert bound == {
        "instance_id": "inst",
        "run_id": "run",
        "state_home": "/state",
        "plane_id": "foreground",
    }
    assert flow.observe(T0 + timedelta(seconds=5))["event"] == flow.tip()
    moved = pw.observation_facts(
        view(flow.fg, flow.inv, flow.ref),
        authority=flow.authority,
        failure=failure,
        instance_id="other",
        run_id="run",
        state_home="/state",
        plane_id="foreground",
        invocation_id=flow.inv,
        expected_state_version=7,
        foreground=flow.fg,
    )
    with pytest.raises(pw.ProviderWaitError, match="provider_locator_mismatch"):
        pw.observe_provider(flow.fg, operation_id=flow.op, facts=moved, observed_at=T0)


def test_provider_observation_crash_reuses_published_bytes(tmp_path, monkeypatch):
    flow = Flow(tmp_path / "crash")
    flow.fail(T0)
    real = pw._append
    monkeypatch.setattr(
        pw, "_append", lambda *a, **k: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        flow.observe(T0 + timedelta(seconds=1))
    monkeypatch.setattr(pw, "_append", real)
    published = tree(flow.fg)
    assert [e["kind"] for e in flow.events()] == ["failure_classified"]
    # A later retry reuses the published bytes and time and appends one event.
    result = flow.observe(T0 + timedelta(hours=1))
    assert result["observation"]["observed_at"] == pw.stamp(T0 + timedelta(seconds=1))
    assert [e["kind"] for e in flow.events()] == ["failure_classified", "observed"]
    assert flow.observe(T0 + timedelta(hours=2))["event"] == result["event"]
    assert len(flow.events()) == 2
    path = result["event"]["observation_ref"]["path"]
    assert (
        tree(flow.fg)[path.removeprefix(pw.ROOT + "/")]
        == published[path.removeprefix(pw.ROOT + "/")]
    )
    # A different self-consistent record at that identity refuses.
    other = Flow(tmp_path / "conflict")
    other.fail(T0)
    monkeypatch.setattr(
        pw, "_append", lambda *a, **k: (_ for _ in ()).throw(OSError("crash"))
    )
    with pytest.raises(OSError):
        other.observe(T0)
    monkeypatch.setattr(pw, "_append", real)
    folder = other.fg / pw.ROOT / "observations"
    (target,) = folder.iterdir()
    forged = json.loads(target.read_bytes())
    forged["active_revision"] = "rev9"
    target.write_bytes(ef.encode(pw._sealed(forged)))
    with pytest.raises(pw.ProviderWaitError, match="provider_observation_conflict"):
        other.observe(T0 + timedelta(hours=1))
    assert [e["kind"] for e in other.events()] == ["failure_classified"]


def test_provider_unindexed_first_event_recovery(tmp_path, monkeypatch):
    real = pw._publish_index

    def crash(*args, **kwargs):
        raise OSError("crash")

    for status, outcome, phase in (
        (429, "PROVIDER_WAIT", "reconcile"),
        (401, "PROVIDER_HUMAN_REQUIRED", "human_required"),
        (403, "PROVIDER_HUMAN_REQUIRED", "human_required"),
    ):
        flow = Flow(tmp_path / str(status))
        monkeypatch.setattr(pw, "_publish_index", crash)
        with pytest.raises(OSError):
            flow.fail(T0, status)
        monkeypatch.setattr(pw, "_publish_index", real)
        with pytest.raises(pw.ProviderWaitError, match="provider_operation_missing"):
            pw.load_operation(flow.fg, flow.op)
        # The retry clock differs; the original time and outcome are retained.
        got, event = flow.replay(T0 + timedelta(minutes=5))
        assert got == outcome and event is not None
        assert (event["failed_at"], event["phase"]) == (pw.stamp(T0), phase)
        assert flow.events() == (event,)
        assert flow.replay(T0 + timedelta(minutes=10)) == (outcome, event)
    # Mismatched occurrence or classification never adopts the unindexed fact.
    flow = Flow(tmp_path / "mismatch")
    monkeypatch.setattr(pw, "_publish_index", crash)
    with pytest.raises(OSError):
        flow.fail(T0)
    monkeypatch.setattr(pw, "_publish_index", real)
    for occurrence, found in (
        ("occZ", pw.Classification("provider_usage", "PROVIDER_WAIT")),
        ("occ1", pw.Classification("provider_availability", "PROVIDER_WAIT")),
    ):
        with pytest.raises(pw.ProviderWaitError, match="provider_unindexed_conflict"):
            pw.classify_failure(
                flow.fg,
                authority=flow.authority,
                classification=found,
                occurrence_id=occurrence,
                diagnostic_ref=flow.ref,
                failed_at=T0,
                current_source_sha256="3" * 64,
                policy=policy(),
            )
    # A gap (no first event) refuses rather than restarting as fresh.
    events = flow.fg / pw.ROOT / "operations" / flow.op / "events"
    (events / "1.json").rename(events / "2.json")
    with pytest.raises(pw.ProviderWaitError, match="provider_chain_forked"):
        flow.replay(T0)
    # A lost index entry over longer history is never recovered as fresh.
    longer = Flow(tmp_path / "longer")
    longer.fail(T0)
    longer.observe(T0)
    index = pw._index(longer.fg)
    index["operations"] = {}
    (longer.fg / pw.ROOT / "index.json").write_bytes(ef.encode(index))
    with pytest.raises(pw.ProviderWaitError, match="provider_unindexed_conflict"):
        longer.replay(T0)
    # A missing index over existing operations is not recreated empty.
    (longer.fg / pw.ROOT / "index.json").unlink()
    with pytest.raises(pw.ProviderWaitError, match="provider_index_missing"):
        pw.initialize_index(longer.fg, session_id=SESSION, policy_raw=POLICY)
    assert not (longer.fg / pw.ROOT / "index.json").exists()


def test_provider_uncertainty_is_never_relabelled_waiting(tmp_path):
    flow = Flow(tmp_path)
    flow.fail(T0)
    saved = (flow.inv, flow.ref, flow.status, flow.record)
    outcome, uncertain = flow.fail(T0 + timedelta(minutes=1), writer="may_live")
    assert outcome == "UNCERTAIN" and uncertain is not None
    flow.inv, flow.ref, flow.status, flow.record = saved
    before = tree(flow.fg)
    with pytest.raises(pw.ProviderWaitError, match="provider_reconciliation_required"):
        flow.observe(T0 + timedelta(minutes=2))
    assert tree(flow.fg) == before
    assert flow.tip() == uncertain and uncertain["streak"] == 1
    with pytest.raises(pw.ProviderWaitError, match="provider_observation_required"):
        flow.due(T0 + timedelta(days=1))


def test_provider_malformed_inputs_refuse_structurally(tmp_path):
    with pytest.raises(pw.ProviderWaitError, match="provider_role_excluded"):
        authority(role=["builder"])
    bad = json.loads(POLICY)
    bad["automatic_wait"] = ["optional"]
    with pytest.raises(pw.ProviderWaitError, match="provider_policy_invalid"):
        pw.load_policy(json.dumps(bad).encode())
    flow = Flow(tmp_path)
    ref, record = bridge(flow.fg, "occ1", "invM", 429)
    unclassified = ("unclassified", "ORDINARY_ERROR")
    found = pw.classify(**facts(record, ref, observed_version=["2.1.305"]))
    assert (found.classification, found.outcome) == unclassified
    listed = ["claude_native_exit"]
    found = pw.classify(
        **facts(
            record,
            ref,
            failure_code=listed,
            bridge_record={**record, "failure_code": listed},
        )
    )
    assert (found.classification, found.outcome) == unclassified
    with pytest.raises(pw.ProviderWaitError, match="provider_restoration_invalid"):
        pw.restoration_record(
            operation_id=flow.op,
            policy_sha256=ef.sha256(POLICY),
            kind=["operator_confirmation"],
            actor="operator",
            request_id="restore-1",
            observed_at=pw.stamp(T0),
            evidence_ref=None,
            statement="Restored.",
        )
    flow.fail(T0)
    path = flow.fg / pw.ROOT / "operations" / flow.op / "events" / "1.json"
    original = path.read_bytes()
    for key, value in (
        ("kind", ["failure_classified"]),
        ("phase", {"reconcile": 1}),
        ("classification", ["provider_usage"]),
    ):
        event = json.loads(original)
        event[key] = value
        path.write_bytes(ef.encode(pw._sealed(event)))
        with pytest.raises(pw.ProviderWaitError, match="provider_chain_invalid"):
            pw.load_operation(flow.fg, flow.op)
    path.write_bytes(original)
    assert pw.load_operation(flow.fg, flow.op).tip["kind"] == "failure_classified"


def test_provider_unresolved_diagnosis_outlives_earlier_restoration(tmp_path):
    flow = Flow(tmp_path)
    at = T0
    for _ in range(2):
        flow.fail(at)
        flow.observe(at)
        flow.due(at + timedelta(hours=2))
        flow.reserve()
        at += timedelta(hours=3)
    flow.fail(at)
    flow.observe(at)
    # R1: an operator files a valid restoration before the diagnosis runs.
    early = flow.restoration(at + timedelta(minutes=1))
    assert flow.due(at + timedelta(minutes=2), restoration=early)["eligible"] is True
    flow.reserve("diagnosis", new="diag1")
    # R2: a restoration older than the latest failure cannot resolve it.
    stale_raw = flow.restoration(at - timedelta(minutes=1), request_id="restore-0")
    stale = ef.write_immutable(
        flow.fg, f"{pw.ROOT}/restorations/{ef.sha256(stale_raw)}.json", stale_raw
    )
    with pytest.raises(pw.ProviderWaitError, match="provider_restoration_mismatch"):
        pw.record_diagnosis(
            flow.fg,
            operation_id=flow.op,
            occurrence_id="diag1",
            resolved=True,
            restoration_ref=stale,
        )
    recorded = pw.record_diagnosis(
        flow.fg,
        operation_id=flow.op,
        occurrence_id="diag1",
        resolved=False,
        restoration_ref=None,
    )
    assert recorded["phase"] == "human_required" and recorded["streak"] == 3
    # The earlier restoration does not outlive the unresolved diagnosis.
    held = flow.due(at + timedelta(days=1))
    assert held["eligible"] is False and held["phase"] == "human_required"
    with pytest.raises(pw.ProviderWaitError, match="provider_not_ready"):
        flow.reserve()
    again = flow.due(at + timedelta(days=1), restoration=early)
    assert again["eligible"] is False and again["phase"] == "human_required"
    # R7: a new restoration dated after the injected reading refuses.
    before = tree(flow.fg)
    future = flow.restoration(at + timedelta(days=3), request_id="restore-2")
    with pytest.raises(pw.ProviderWaitError, match="provider_restoration_mismatch"):
        flow.due(at + timedelta(days=2), restoration=future)
    assert tree(flow.fg) == before
    # A genuinely new restoration after the hold releases it, once.
    fresh = flow.restoration(at + timedelta(days=2), request_id="restore-3")
    released = flow.due(at + timedelta(days=2), restoration=fresh)
    assert released["eligible"] is True and released["phase"] == "retry_ready"
    reserved = flow.reserve()
    assert reserved["event"]["kind"] == "retry_reserved"
    assert reserved["event"]["streak"] == 3


def test_provider_null_ref_uncertainty_replays_across_chain(tmp_path):
    flow = Flow(tmp_path)
    flow.fail(T0)
    unknown = pw.Classification("unknown_effects", "UNCERTAIN")

    def uncertain() -> tuple[str, dict | None]:
        return pw.classify_failure(
            flow.fg,
            authority=flow.authority,
            classification=unknown,
            occurrence_id="occ1",
            diagnostic_ref=None,
            failed_at=T0 + timedelta(minutes=1),
            current_source_sha256="3" * 64,
            policy=policy(),
        )

    outcome, first = uncertain()
    assert outcome == "UNCERTAIN" and first is not None
    # The same generation then fails positively and is counted once.
    outcome, counted = flow.fail(T0 + timedelta(minutes=2))
    assert outcome == "PROVIDER_WAIT" and counted is not None
    assert counted["streak"] == 2
    events = flow.events()
    # R4: a late exact replay of the null-ref uncertainty reuses its fact.
    assert uncertain() == ("UNCERTAIN", first)
    assert flow.events() == events and flow.tip()["streak"] == 2


def test_provider_malformed_api_values_refuse_before_publication(tmp_path):
    flow = Flow(tmp_path / "api")
    flow.fail(T0)
    before = tree(flow.fg)
    with pytest.raises(pw.ProviderWaitError, match="provider_preparation_invalid"):
        pw.reserve_provider_attempt(
            flow.fg,
            operation_id=flow.op,
            kind=["retry"],
            previous_occurrence="occ1",
            new_occurrence="occ2",
            input_sha256="6" * 64,
            current_source_sha256="3" * 64,
            file_delta_ref=flow.delta,
            effects_ref=flow.effects,
        )
    for bad in (
        pw.Classification(["provider_usage"], "PROVIDER_WAIT"),
        pw.Classification("provider_usage", ["PROVIDER_WAIT"]),
        pw.Classification("rate_limited", "PROVIDER_WAIT"),
    ):
        with pytest.raises(
            pw.ProviderWaitError, match="provider_classification_invalid"
        ):
            pw.classify_failure(
                flow.fg,
                authority=flow.authority,
                classification=bad,
                occurrence_id="occ1",
                diagnostic_ref=flow.ref,
                failed_at=T0,
                current_source_sha256="3" * 64,
                policy=policy(),
            )
    operation = pw.load_operation(flow.fg, flow.op)
    for key in ("invocations", "completions", "transitions"):
        current = view(flow.fg, flow.inv, flow.ref)
        del current[key]
        with pytest.raises(pw.ProviderWaitError, match="provider_observation_mismatch"):
            pw.observation_facts(
                current,
                authority=flow.authority,
                failure=pw._latest_failure(operation),
                instance_id="inst",
                run_id="run",
                state_home="/state",
                plane_id="foreground",
                invocation_id=flow.inv,
                expected_state_version=7,
                foreground=flow.fg,
            )
    assert tree(flow.fg) == before
    # A non-directory operations path refuses rather than raising OSError.
    other = tmp_path / "file-operations" / "foreground"
    (other / pw.ROOT).mkdir(parents=True)
    (other / pw.ROOT / "operations").write_bytes(b"not a directory")
    with pytest.raises(pw.ProviderWaitError, match="provider_unsafe_path"):
        pw.initialize_index(other, session_id=SESSION, policy_raw=POLICY)
    assert not (other / pw.ROOT / "index.json").exists()
