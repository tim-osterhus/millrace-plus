"""Operator controls refuse unknown intent without implicit setup or role launch."""

from __future__ import annotations

from millrace_plus.factory_cli import main


def test_missing_factory_status_never_initializes(tmp_path, capsys):
    home = tmp_path / "absent"
    assert main(["status", "--factory-home", str(home)]) == 2
    assert not home.exists()
    assert capsys.readouterr().err


def test_cli_usage_refuses_unknown_operation(capsys):
    assert main(["deliver"]) == 2
    assert "usage_error" in capsys.readouterr().err


def test_lad_forward_freezes_intent_before_interruption_and_exact_replay(
    tmp_path, monkeypatch
):
    import contextlib
    import hashlib
    from types import SimpleNamespace

    import pytest

    from millrace_plus import factory_cli as cli
    from millrace_plus import factory_lad
    from millrace_plus.cicd_records import CicdError
    from millrace_plus.local_intake import canonical, strict_json

    job_id, request_id = "a" * 32, "b" * 32
    facts = {"lad_task_id": "lad-job"}
    records = {}
    accepted = {}
    forwarded = []
    applied = []

    def retain(value):
        raw = canonical(value)
        ref = {
            "path": "records/" + hashlib.sha256(raw).hexdigest(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        records[ref["path"]] = raw
        return ref

    state = cli.fr.integrity(
        {
            "format": cli.fr.STATE,
            "factory_id": "c" * 32,
            "job_id": job_id,
            "input_ref": retain({"input": True}),
            "claim_ref": retain({"claim": True}),
            "baseline_ref": None,
            "reservations_ref": retain({"reservations": []}),
            "accepted_refs": {"facts": retain(facts)},
            "decision_refs": [],
            "phase": "hold",
            "version": 1,
        }
    )
    state_ref = retain(state)
    lad_ref = retain({"original_attention": "exact-owner-evidence"})
    evidence = {
        "state_ref": state_ref,
        "changed_cause_ref": None,
        "lad_decision": "retry",
        "lad_evidence_ref": lad_ref,
        "resume_plane": "foreground",
    }
    evidence_file = tmp_path / "evidence.json"
    evidence_file.write_bytes(canonical(evidence))
    plan_path = tmp_path / "decisions" / (request_id + ".plan.json")

    def intake_decide(request_id, intent, *, check):
        accepted[request_id] = {"receipt": {"request_id": request_id}, "intent": intent}
        return accepted[request_id]["receipt"]

    home = SimpleNamespace(
        workspace=tmp_path,
        intake=SimpleNamespace(
            tasks=lambda job: [{"status": "held"}],
            decision=lambda request_id, intent: accepted.get(request_id),
            decide=intake_decide,
        ),
        state=lambda job: (
            strict_json((tmp_path / "jobs" / job / "state.json").read_bytes())
            if (tmp_path / "jobs" / job / "state.json").exists()
            else state
        ),
        facts=lambda value: strict_json(
            records[value["accepted_refs"]["facts"]["path"]]
        ),
        retain=retain,
        raw_ref=lambda ref: records[ref["path"]],
        read_ref=lambda ref: strict_json(records[ref["path"]]),
        lock=contextlib.nullcontext,
        _receipt=lambda request_id, intent: {"request_id": request_id},
    )

    def apply(request_id, receipt):
        saved = cli.Home.apply_decision_plan(home, request_id, receipt)
        applied.append(receipt)
        return saved

    home.apply_decision_plan = apply

    def forward(task, decision, request_id, path, **kwargs):
        plan = strict_json(plan_path.read_bytes())
        intent = strict_json(records[plan["facts_ref"]["path"]])[
            "lad_continuation_intent_ref"
        ]
        assert strict_json(records[intent["path"]]) == {
            "task_id": task,
            "decision": decision,
            "request_id": request_id,
            "evidence_ref": lad_ref,
            "resume_plane": kwargs["resume_plane"],
        }
        forwarded.append(
            (task, decision, request_id, path.read_bytes(), kwargs["resume_plane"])
        )
        if len(forwarded) == 1:
            raise RuntimeError("interrupted after owning acceptance")
        assert forwarded[-1] == forwarded[0]
        return {"request_id": request_id}

    monkeypatch.setattr(
        factory_lad,
        "Lane",
        lambda home: SimpleNamespace(runtime=SimpleNamespace(decide=forward)),
    )
    monkeypatch.setattr(cli, "wake", lambda home: None)
    with pytest.raises(RuntimeError, match="interrupted"):
        cli.decide(home, job_id, "continue-lad", request_id, evidence_file)
    original_plan = plan_path.read_bytes()
    assert not accepted and not applied

    evidence_file.write_bytes(
        canonical(evidence | {"changed_cause_ref": retain({"changed": True})})
    )
    with pytest.raises(CicdError, match="factory_record_conflict"):
        cli.decide(home, job_id, "continue-lad", request_id, evidence_file)
    assert len(forwarded) == 1 and plan_path.read_bytes() == original_plan

    evidence_file.write_bytes(canonical(evidence))
    receipt = cli.decide(home, job_id, "continue-lad", request_id, evidence_file)
    assert len(forwarded) == 2 and applied == [receipt]
    projected = home.facts(home.state(job_id))["lad_continuation_ref"]
    assert home.read_ref(projected) == {"request_id": request_id}
    assert strict_json(
        (tmp_path / "decisions" / (request_id + ".lad-result.json")).read_bytes()
    ) == {"request_id": request_id}
    assert (
        cli.decide(home, job_id, "continue-lad", request_id, evidence_file) == receipt
    )
    assert len(forwarded) == 2 and plan_path.read_bytes() == original_plan


def test_parent_control_continuation_is_plan_only_and_preserves_prior_intents(
    tmp_path, monkeypatch
):
    import contextlib
    import hashlib
    from types import SimpleNamespace

    from millrace_plus import factory_cli as cli
    from millrace_plus import factory_lad, subworkflow_v2
    from millrace_plus.local_intake import canonical, strict_json
    from millrace_plus.router import control

    job, decision = "a" * 32, "b" * 32
    records, accepted, applied = {}, {}, []

    def retain(value):
        raw = canonical(value)
        ref = {
            "path": "records/" + hashlib.sha256(raw).hexdigest(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        records[ref["path"]] = raw
        return ref

    old = retain({"original": "initial resume"})
    task_ref = retain({"task_id": "c" * 32, "request_sha256": "d" * 64})
    parent_ref = retain(
        {"operation": "resume_plane", "plane_id": "service", "request_id": "e" * 32}
    )
    state = {"phase": "hold", "job_id": job}
    evidence = {
        "state_ref": retain(state),
        "changed_cause_ref": None,
        "lad_decision": "parent-control",
        "lad_evidence_ref": parent_ref,
        "resume_plane": "service",
    }
    file = tmp_path / "evidence.json"
    file.write_bytes(canonical(evidence))
    (tmp_path / "execution.json").write_bytes(
        canonical({"instance_id": "f" * 32, "state_home": str(tmp_path)})
    )
    facts = {
        "lad_task_id": "c" * 32,
        "lad_task_ref": task_ref,
        "parent_resume_ref": old,
        "held_parent_join_ref": old,
    }

    def accept(request_id, intent, **kwargs):
        accepted[request_id] = {"receipt": {"id": request_id}}
        return accepted[request_id]["receipt"]

    home = SimpleNamespace(
        home=tmp_path,
        workspace=tmp_path,
        intake=SimpleNamespace(
            tasks=lambda job: [{"status": "held"}],
            decision=lambda request_id, intent: accepted.get(request_id),
            decide=accept,
        ),
        state=lambda job: state,
        facts=lambda state: dict(facts),
        retain=retain,
        raw_ref=lambda ref: records[ref["path"]],
        read_ref=lambda ref: strict_json(records[ref["path"]]),
        lock=contextlib.nullcontext,
        apply_decision_plan=lambda *a: applied.append(a),
    )
    monkeypatch.setattr(
        control,
        "inspect_instance",
        lambda *a, **k: {"runs": [{"id": "run", "status": "paused"}]},
    )
    monkeypatch.setattr(control, "inspect_run", lambda *a, **k: {"status": "paused"})
    monkeypatch.setattr(subworkflow_v2, "closed", lambda *a, **k: True)
    calls = []

    def prepare(job_id, selected, request, **kwargs):
        calls.append((job_id, selected, request, kwargs["fresh"]))
        return {"declared": request}

    def forbidden(*a, **k):
        raise AssertionError("CLI must never forward this parent control")

    monkeypatch.setattr(
        factory_lad,
        "Lane",
        lambda home: SimpleNamespace(
            parent_resume_intent=prepare,
            runtime=SimpleNamespace(decide=forbidden, parent_control=forbidden),
        ),
    )
    monkeypatch.setattr(cli, "wake", lambda home: None)
    receipt = cli.decide(home, job, "continue-lad", decision, file)
    plan = strict_json(
        (tmp_path / "decisions" / (decision + ".plan.json")).read_bytes()
    )
    planned = home.read_ref(plan["facts_ref"])
    assert plan["phase"] == "resume_lad_parent" and planned[
        "parent_resume_history_refs"
    ] == [old]
    assert (
        "held_parent_join_ref" not in planned and "lad_continuation_ref" not in planned
    )
    assert not (tmp_path / "decisions" / (decision + ".lad-result.json")).exists()
    assert calls == [(job, task_ref, parent_ref, True)]
    assert cli.decide(home, job, "continue-lad", decision, file) == receipt
    assert len(calls) == 1 and applied
