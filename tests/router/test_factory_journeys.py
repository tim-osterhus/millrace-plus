"""Actual composed public source lifecycle. Inert roles, real guards/Core/OS joins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from factory_support import close, provision, resume, start, task, until_jobs
from public_runtime_support import runtime as runtime

from millrace_plus import factory_runtime as factory
from millrace_plus.factory_candidate import check_development, index_identity
from millrace_plus.factory_cli import main, status
from millrace_plus.router import control


def test_public_two_jobs_ci_then_lad_preserve_criteria_source_and_real_closure(
    runtime, capsys
):
    f = provision(runtime, products=True, engineering=True)
    original_index = index_identity(f.dev)
    run = None
    try:
        first = task(f.root, "one")
        second = task(f.root, "two", lad_route=True)
        assert (
            main(["submit", "--factory-home", str(f.home), "--input", str(first)]) == 0
        )
        one = json.loads(capsys.readouterr().out)
        assert (
            main(["submit", "--factory-home", str(f.home), "--input", str(second)]) == 0
        )
        two = json.loads(capsys.readouterr().out)
        assert (one["sequence"], two["sequence"]) == (1, 2)
        run = start(f)
        rows = until_jobs(f, 2)
        assert [r["status"] for r in rows] == ["succeeded", "succeeded"]
        home = factory.Home(f.home)
        bases = home.base_chain(home.base_ref())
        assert [b["sequence"] for b in bases] == [3, 2, 1]
        assert check_development(home, home.base_ref())
        assert (f.dev / "src/calc.py").read_text().endswith("return a + b\n")
        assert (f.dev / "answer.py").read_text().find("42") >= 0
        assert (f.val / "src/calc.py").read_text().endswith("return a - b\n")
        assert not (f.val / "answer.py").exists()
        assert (f.dev / "foreign.txt").read_bytes() == b"original user work\n"
        assert index_identity(f.dev) == original_index
        for target in (f.dev, f.val):
            assert (
                target / "build/foreign.bin"
            ).read_bytes() == b"preserve foreign product\x00"
            assert not (target / "build/owned-report.txt").exists()
            assert (
                target / "opaque-environment/cache"
            ).read_bytes() == b"opaque operator environment"
        from millrace_plus import engineering_feedback as ef

        for row in rows:
            state = home.state(row["task_id"])
            facts = home.facts(state)
            outcome = home.read_ref(facts["outcome_ref"])
            assert len(outcome["engineering_refs"]["check_refs"]) == 2
            checks = [
                ef.validate_evidence(home.read_ref(ref))
                for ref in outcome["engineering_refs"]["check_refs"]
            ]
            assert {r["check_id"] for r in checks} == {"calc", "lq-extra"}
            assert all(
                r["status"] == "pass" and r["product_layer_refs"] for r in checks
            )
            reservations = home.read_ref(state["reservations_ref"])["reservations"]
            assert all(
                r["state"] == "accepted" and r["completion_ref"] for r in reservations
            )
            import hashlib

            job_root = home.workspace / "jobs" / row["task_id"]
            assert (job_root / "input.json").read_bytes() == row["request_bytes"]
            assert (
                json.loads((job_root / "scope.json").read_bytes())["job_id"]
                == row["task_id"]
            )
            assert (
                json.loads((job_root / "baseline.json").read_bytes())["job_id"]
                == row["task_id"]
            )
            assert (
                json.loads((job_root / "reservations.json").read_bytes())[
                    "reservations"
                ]
                == reservations
            )
            frozen = home.ci.read_ref(facts["candidate_ref"])
            for entry in home.ci.read_ref(frozen["tree_manifest_ref"])["entries"]:
                assert (
                    hashlib.sha256(
                        (job_root / "candidate/objects" / entry["sha256"]).read_bytes()
                    ).hexdigest()
                    == entry["sha256"]
                )
        lad_root = home.workspace / "jobs" / rows[1]["task_id"]
        binding = json.loads((lad_root / "lad-binding.json").read_bytes())
        assert binding["task_id"] == f.service.intake.tasks()[0]["task_id"]
        assert (
            home.read_ref(binding["terminal_ref"])
            == f.service.intake.tasks()[0]["terminal_receipt"]
        )
        assert binding["child_ref"] is not None and binding["join_refs"]
        assert not home.ci.intake.tasks()  # No fictitious CI events.
        assert len(f.service.intake.tasks()) == 1
        assert (
            f.service.intake.tasks()[0]["terminal_receipt"]["disposition"]
            == "succeeded"
        )
        before = status(home)
        assert not (home.workspace / "active-role.json").exists()
        assert (
            main(["submit", "--factory-home", str(f.home), "--input", str(first)]) == 0
        )
        capsys.readouterr()
        assert (
            status(factory.Home(f.home))["accepted_base_ref"]
            == before["accepted_base_ref"]
        )
        view = control.inspect_run(f.instance, run, state_home=f.state)
        names = [row["node"] for row in view["invocations"]]
        assert names.count("repair") == 1 and names.count("submit_lad") == 1
        assert names.count("review_candidate") == 2
        assert (
            names.index("guard_prepared_base")
            < names.index("investigate")
            < names.index("guard_investigation")
        )
    finally:
        close(f, run)


def test_bootstrap_replay_raw_intake_cancel_and_readonly_corruption(runtime):
    import uuid

    import pytest

    from millrace_plus import factory_records as fr
    from millrace_plus.factory_cli import cancel
    from millrace_plus.local_intake import IntakeError

    f = provision(runtime)
    try:
        home = factory.Home(f.home)
        base = home.base_ref()
        indices = (index_identity(f.dev), index_identity(f.val))
        same = factory.initialize(f.home, f.config)
        assert same["instance_id"] == f.instance and home.base_ref() == base
        assert control.inspect_instance(f.instance, state_home=f.state)["runs"] == []
        source = task(f.root, "queued")
        submitted = home.submit(source)
        assert home.submit(source) == submitted | {"disposition": "replayed"}
        raw = source.read_bytes()
        source.write_bytes(raw + b"\n")
        with pytest.raises(IntakeError):
            home.submit(source)
        source.write_bytes(raw)
        identifier = uuid.uuid4().hex
        receipt = cancel(home, submitted["task_id"], identifier)
        assert cancel(factory.Home(f.home), submitted["task_id"], identifier) == receipt
        assert home.intake.tasks()[0]["status"] == "cancelled"
        assert home.base_ref() == base
        pointer = f.home / "accepted-base.json"
        saved = pointer.read_bytes()
        corrupt = json.loads(saved)
        corrupt["sha256"] = "0" * 64
        pointer.write_text(json.dumps(corrupt))
        damaged = pointer.read_bytes()
        with pytest.raises(fr.FactoryError):
            status(factory.Home(f.home))
        assert pointer.read_bytes() == damaged
        pointer.write_bytes(saved)
        config_raw = f.config.read_bytes()
        changed = json.loads(config_raw)
        changed["role_profile"]["require_independent_review_family"] = True
        f.config.write_text(json.dumps(changed))
        with pytest.raises(fr.FactoryError, match="factory_preparation_conflict"):
            factory.initialize(f.home, f.config)
        f.config.write_bytes(config_raw)
        assert (index_identity(f.dev), index_identity(f.val)) == indices
        assert home.base_ref() == base
    finally:
        close(f, None)


def test_failed_check_close_restart_restores_products_before_next_job(runtime):
    import uuid

    from millrace_plus import cicd_v2_records as ci
    from millrace_plus.factory_cli import decide
    from millrace_plus.subworkflow_v2 import closed

    f = provision(runtime, products=True)
    run = None
    try:
        bad = task(f.root, "bad")
        value = json.loads(bad.read_bytes())
        value["request"] = "Implement bad addition"
        bad.write_text(json.dumps(value))
        home = factory.Home(f.home)
        submitted = home.submit(bad)
        home.submit(task(f.root, "good"))
        run = start(f)

        def held():
            state_path = home.workspace / "jobs" / submitted["task_id"] / "state.json"
            if not state_path.exists():
                return None
            state = factory.Home(f.home).state(submitted["task_id"])
            view = control.inspect_run(f.instance, run, state_home=f.state)
            return (
                state
                if state["phase"] == "hold" and view["status"] == "paused"
                else None
            )

        state = f.public.until(held, seconds=120)
        import pytest

        from millrace_plus import factory_records as fr

        state_file = home.workspace / "jobs" / submitted["task_id"] / "state.json"
        saved_state = state_file.read_bytes()
        state_file.unlink()
        with pytest.raises(fr.FactoryError, match="factory_claim_projection_advanced"):
            home.reconcile_claim(submitted["task_id"])
        assert not state_file.exists()
        state_file.write_bytes(saved_state)
        facts = home.facts(state)
        assert len(facts["checks"]) == 1 and facts["pending"] is None
        acceptance = home.ci.read_ref(facts["checks"][0])
        raw = ci.validate_check(home.ci.read_ref(acceptance["process_receipt_ref"]))
        assert raw["status"] == "failed" and raw["exit_code"] == 1
        assert (f.val / "build/owned-report.txt").exists()
        evidence = f.root / "close-evidence.json"
        evidence.write_text(
            json.dumps(
                {
                    "state_ref": home.retain(state),
                    "changed_cause_ref": None,
                    "lad_decision": None,
                    "lad_evidence_ref": None,
                    "resume_plane": None,
                }
            )
        )
        request_id = uuid.uuid4().hex
        receipt = decide(home, submitted["task_id"], "close", request_id, evidence)
        assert (
            decide(
                factory.Home(f.home),
                submitted["task_id"],
                "close",
                request_id,
                evidence,
            )
            == receipt
        )
        # Real executor restart retains the same job/claim and spent repair.
        control.cancel_run(
            f.instance, run, state_home=f.state, request_id=uuid.uuid4().hex
        )
        f.public.until(
            lambda: closed(control.inspect_run(f.instance, run, state_home=f.state)),
            seconds=30,
        )
        f.executor.join()
        assert not f.executor_errors
        old_run = run
        run = start(f)
        rows = until_jobs(f, 2, statuses=["failed", "succeeded"])
        assert [row["task_id"] for row in rows][0] == submitted["task_id"]
        home = factory.Home(f.home)
        assert len(home.base_chain(home.base_ref())) == 2
        assert not (f.val / "build/owned-report.txt").exists()
        assert (
            f.val / "build/foreign.bin"
        ).read_bytes() == b"preserve foreign product\x00"
        assert (f.dev / "src/calc.py").read_text().endswith("return a + b\n")
        assert (f.val / "src/calc.py").read_text().endswith("return a - b\n")
        old = control.inspect_run(f.instance, old_run, state_home=f.state)
        new = control.inspect_run(f.instance, run, state_home=f.state)
        assert sum(row["node"] == "repair" for row in old["invocations"]) == 1
        assert sum(row["node"] == "repair" for row in new["invocations"]) == 1
        failed = home.facts(home.state(submitted["task_id"]))
        assert failed["checks"] == facts["checks"] and not failed["accepted"]
    finally:
        close(f, run)


def test_claim_transaction_gap_reconciles_exact_projection_after_real_restart(runtime):
    import hashlib
    import uuid

    import pytest

    from millrace_plus import factory_records as fr
    from millrace_plus.local_intake import IntakeError
    from millrace_plus.subworkflow_v2 import closed

    f = provision(runtime)
    run = None
    try:
        home = factory.Home(f.home)
        source = task(f.root, "claim-gap")
        submitted = home.submit(source)
        state_file = home.workspace / "jobs" / submitted["task_id"] / "state.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_bytes(b"injected destination conflict")
        run = start(f)
        f.public.until(
            lambda: (
                control.inspect_run(f.instance, run, state_home=f.state)["status"]
                == "paused"
            ),
            seconds=120,
        )
        claimed = home.intake.tasks(submitted["task_id"])[0]
        assert claimed["claim"] is not None and claimed["terminal_receipt"] is None
        original = state_file.read_bytes()
        with pytest.raises((fr.FactoryError, IntakeError, ValueError)):
            status(factory.Home(f.home))
        assert state_file.read_bytes() == original
        proposal = json.loads(state_file.with_name("claim-state.json").read_bytes())
        assert proposal["claim_ref"] == claimed["claim"]["factory_claim_ref"]
        state_file.unlink()  # Explicitly remove only the test's injected conflict.
        control.cancel_run(
            f.instance, run, state_home=f.state, request_id=uuid.uuid4().hex
        )
        f.public.until(
            lambda: closed(control.inspect_run(f.instance, run, state_home=f.state)),
            seconds=30,
        )
        f.executor.join()
        assert not f.executor_errors
        old_run = run
        run = start(f)
        until_jobs(f, 1)
        home = factory.Home(f.home)
        state = home.state(submitted["task_id"])
        assert state["claim_ref"] == proposal["claim_ref"]
        outcome = home.read_ref(home.facts(state)["outcome_ref"])
        assert (
            outcome["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        )
        assert all(
            row["node"] != "repair"
            for row in control.inspect_run(f.instance, old_run, state_home=f.state)[
                "invocations"
            ]
        )
        assert (
            sum(
                row["node"] == "repair"
                for row in control.inspect_run(f.instance, run, state_home=f.state)[
                    "invocations"
                ]
            )
            == 1
        )
    finally:
        close(f, run)


def test_no_action_human_hold_repeat_guard_and_rejected_review_preserve_base(runtime):
    import uuid

    from millrace_plus.factory_cli import decide
    from millrace_plus.subworkflow_v2 import closed

    f = provision(runtime)
    run = None
    try:
        home = factory.Home(f.home)
        original_base = home.base_ref()
        submitted = []
        for source_id, request in (
            ("noop", "Inspect only: no action"),
            ("human", "Request human hold"),
            ("human-rekeyed", "Request human hold"),
            ("rejected", "Implement addition with review reject"),
            ("accepted", "Implement addition."),
        ):
            path = task(f.root, source_id)
            subject = json.loads(path.read_bytes())
            subject["request"] = request
            path.write_text(json.dumps(subject))
            submitted.append(home.submit(path))
        run = start(f)
        for index, expected_reason in (
            (1, "operator_decision_required"),
            (2, "repeated_failure_no_change"),
            (3, "native_blocked_or_rejected"),
        ):
            job = submitted[index]["task_id"]

            def held():
                path = home.workspace / "jobs" / job / "state.json"
                if not path.exists():
                    return None
                state = factory.Home(f.home).state(job)
                view = control.inspect_run(f.instance, run, state_home=f.state)
                return (
                    state
                    if state["phase"] == "hold" and view["status"] == "paused"
                    else None
                )

            state = f.public.until(held, seconds=120)
            home = factory.Home(f.home)
            facts = home.facts(state)
            outcome = home.read_ref(facts["hold_outcome_ref"])
            assert (
                outcome["terminal_receipt_ref"] is None
                and outcome["reason"] == expected_reason
            )
            assert outcome["status"] == (
                "held_human" if index == 1 else "held_evidence"
            )
            assert (
                home.base_ref() == original_base
                and home.intake.tasks(job)[0]["terminal_receipt"] is None
            )
            if index == 2:
                assert home.read_ref(state["reservations_ref"])["reservations"] == []
            if index == 3:
                assert home.ci.read_ref(outcome["review_ref"])["verdict"] == "REJECT"
            evidence = f.root / (job + "-close.json")
            evidence.write_text(
                json.dumps(
                    {
                        "state_ref": home.retain(state),
                        "changed_cause_ref": None,
                        "lad_decision": None,
                        "lad_evidence_ref": None,
                        "resume_plane": None,
                    }
                )
            )
            decide(home, job, "close", uuid.uuid4().hex, evidence)
            control.cancel_run(
                f.instance, run, state_home=f.state, request_id=uuid.uuid4().hex
            )
            f.public.until(
                lambda: closed(
                    control.inspect_run(f.instance, run, state_home=f.state)
                ),
                seconds=30,
            )
            f.executor.join()
            assert not f.executor_errors
            run = start(f)
        rows = until_jobs(
            f, 5, statuses=["succeeded", "failed", "failed", "failed", "succeeded"]
        )
        home = factory.Home(f.home)
        assert len(home.base_chain(home.base_ref())) == 2
        noop = home.read_ref(home.facts(home.state(rows[0]["task_id"]))["outcome_ref"])
        assert noop["status"] == "no_action" and noop["candidate_ref"] is None
        assert all(
            (home.workspace / "jobs" / row["task_id"] / "outcome.json").is_file()
            for row in rows
        )
        assert (f.dev / "src/calc.py").read_text().endswith("return a + b\n")
        assert (f.val / "src/calc.py").read_text().endswith("return a - b\n")
        assert not home.ci.intake.tasks()
    finally:
        close(f, run)


def test_typed_external_sources_hold_without_fake_ci_intake_or_mapping(runtime):
    import uuid

    from cicd_support import event
    from test_cicd_feedback import report

    from millrace_plus import check_contracts as cc
    from millrace_plus import cicd_v2_records as ci
    from millrace_plus.cicd_feedback import feedback_identity
    from millrace_plus.cicd_records import event_identity
    from millrace_plus.factory_cli import decide
    from millrace_plus.subworkflow_v2 import closed

    f = provision(runtime)
    run = None
    try:
        home = factory.Home(f.home)
        base = home.base_ref()
        tree = home.ci.read_ref(home.read_ref(base)["candidate_ref"])[
            "candidate_tree_sha256"
        ]
        source_rows = []
        external = "c" * 40
        for kind in ("ci_event", "user_report", "production_observation"):
            original_file = (
                event(f.root, external)
                if kind == "ci_event"
                else report(
                    f.root,
                    external,
                    report_id=kind,
                    kind="bug_report"
                    if kind == "user_report"
                    else "production_incident",
                )
            )
            original = json.loads(original_file.read_bytes())
            source_id = (
                event_identity(original)
                if kind == "ci_event"
                else ci.digest(feedback_identity(original))
            )
            path = task(f.root, "typed-" + kind)
            subject = json.loads(path.read_bytes()) | {
                "source_kind": kind,
                "source_id": source_id,
                "base_candidate_sha256": tree,
                "request": None,
                "criteria": [],
                "feedback_ref": cc.make_ref(
                    original_file.relative_to(f.root).as_posix(),
                    original_file.read_bytes(),
                ),
            }
            path.write_text(json.dumps(subject))
            source_rows.append(home.submit(path))
        run = start(f)
        for index, submitted in enumerate(source_rows):
            job = submitted["task_id"]

            def held():
                view = control.inspect_run(f.instance, run, state_home=f.state)
                active = factory.Home(f.home).active()
                return (
                    view
                    if active
                    and active["task_id"] == job
                    and view["status"] == "paused"
                    else None
                )

            view = f.public.until(held, seconds=120)
            assert "unbound_external_revision" in view["completions"][-1]["stderr"]
            assert not any(
                row["node"] in {"reproduce", "investigate", "repair", "submit_lad"}
                for row in view["invocations"]
            )
            home = factory.Home(f.home)
            state = home.state(job)
            assert home.base_ref() == base and not home.ci.intake.tasks()
            evidence = f.root / (job + "-close.json")
            evidence.write_text(
                json.dumps(
                    {
                        "state_ref": home.retain(state),
                        "changed_cause_ref": None,
                        "lad_decision": None,
                        "lad_evidence_ref": None,
                        "resume_plane": None,
                    }
                )
            )
            decide(home, job, "close", uuid.uuid4().hex, evidence)
            control.cancel_run(
                f.instance, run, state_home=f.state, request_id=uuid.uuid4().hex
            )
            f.public.until(
                lambda: closed(
                    control.inspect_run(f.instance, run, state_home=f.state)
                ),
                seconds=30,
            )
            f.executor.join()
            assert not f.executor_errors
            run = start(f)
        rows = until_jobs(f, 3, statuses=["failed"] * 3)
        assert len(rows) == 3 and factory.Home(f.home).base_ref() == base
        assert not f.service.intake.tasks()
    finally:
        close(f, run)


def test_current_task_cannot_regress_a_prior_accepted_criterion(runtime):
    import uuid

    from millrace_plus import cicd_v2_records as ci
    from millrace_plus.factory_cli import decide
    from millrace_plus.subworkflow_v2 import closed

    f = provision(runtime, products=True, engineering=True)
    run = None
    try:
        home = factory.Home(f.home)
        first = home.submit(task(f.root, "retain-sum"))
        path = task(f.root, "regression", lad_route=True)
        subject = json.loads(path.read_bytes())
        subject["request"] = "Add answer() returns 42; regress retained sum"
        path.write_text(json.dumps(subject))
        second = home.submit(path)
        run = start(f)

        def held():
            state_path = home.workspace / "jobs" / second["task_id"] / "state.json"
            if not state_path.exists():
                return None
            state = factory.Home(f.home).state(second["task_id"])
            view = control.inspect_run(f.instance, run, state_home=f.state)
            return (
                state
                if state["phase"] == "hold" and view["status"] == "paused"
                else None
            )

        state = f.public.until(held, seconds=120)
        home = factory.Home(f.home)
        facts = home.facts(state)
        assert home.intake.tasks(first["task_id"])[0]["status"] == "succeeded"
        obligations = home.read_ref(facts["obligations_ref"])
        assert any(
            row["id"] == first["task_id"] + ":sum" and row["status"] == "active"
            for row in obligations["requirements"]
        )
        assert home.read_ref(state["input_ref"])["criteria"][0]["id"] == "answer"
        checks = [
            ci.validate_check(
                home.ci.read_ref(home.ci.read_ref(ref)["process_receipt_ref"])
            )
            for ref in facts["checks"]
        ]
        assert {row["command_id"] for row in checks} == {"calc", "lq-extra"}
        assert (
            all(row["status"] == "failed" for row in checks) and not facts["accepted"]
        )
        assert (f.dev / "src/calc.py").read_text().endswith("return a + b\n")
        assert not (f.dev / "answer.py").exists()
        assert len(home.base_chain(home.base_ref())) == 2
        evidence = f.root / "regression-close.json"
        evidence.write_text(
            json.dumps(
                {
                    "state_ref": home.retain(state),
                    "changed_cause_ref": None,
                    "lad_decision": None,
                    "lad_evidence_ref": None,
                    "resume_plane": None,
                }
            )
        )
        decide(home, second["task_id"], "close", uuid.uuid4().hex, evidence)
        control.cancel_run(
            f.instance, run, state_home=f.state, request_id=uuid.uuid4().hex
        )
        f.public.until(
            lambda: closed(control.inspect_run(f.instance, run, state_home=f.state)),
            seconds=30,
        )
        f.executor.join()
        assert not f.executor_errors
        run = start(f)
        until_jobs(f, 2, statuses=["succeeded", "failed"])
        assert not (f.val / "answer.py").exists()
        assert (f.val / "src/calc.py").read_text().endswith("return a - b\n")
        report = (home.workspace / "jobs" / second["task_id"] / "report.md").read_text()
        assert (
            "Retained unaccepted candidate" in report
            and "Verified frozen candidate" not in report
        )
    finally:
        close(f, run)


def test_noncheck_product_write_stays_held_without_export_or_second_job(runtime):
    f = provision(runtime, products=True)
    run = None
    try:
        home = factory.Home(f.home)
        path = task(f.root, "noncheck")
        value = json.loads(path.read_bytes())
        value["request"] = "Implement addition with forbidden product"
        path.write_text(json.dumps(value))
        first = home.submit(path)
        second = home.submit(task(f.root, "queued-after-unknown"))
        base = home.base_ref()
        run = start(f)
        view = f.public.until(
            lambda: (
                result
                if (result := control.inspect_run(f.instance, run, state_home=f.state))[
                    "status"
                ]
                == "paused"
                else None
            ),
            seconds=120,
        )
        assert "noncheck_product_write" in view["completions"][-1]["stderr"]
        rows = home.intake.tasks()
        assert (
            rows[0]["task_id"] == first["task_id"]
            and rows[0]["terminal_receipt"] is None
        )
        assert rows[1]["task_id"] == second["task_id"] and rows[1]["status"] == "queued"
        assert not any(row["node"] == "export_candidate" for row in view["invocations"])
        assert home.base_ref() == base and (f.val / "build/noncheck.txt").exists()
        assert (f.dev / "src/calc.py").read_text().endswith("return a - b\n")
        output = json.loads(
            (home.workspace / "jobs" / first["task_id"] / "outcome.json").read_bytes()
        )
        assert (
            output["status"] == "uncertain" and output["terminal_receipt_ref"] is None
        )
    finally:
        close(f, run)


def test_lad_then_expired_os_observations_allow_next_job_and_normal_stop(
    runtime, capsys
):
    import time

    from cicd_support import git

    from millrace_plus import lad_service_runtime as lad
    from millrace_plus.factory_lad import Lane
    from millrace_plus.lad_service import Service

    f = provision(runtime, products=True, engineering=True)
    run = None
    try:
        for source_id, is_lad in [("expiry-one", False), ("expiry-two", True)]:
            path = task(f.root, source_id, lad_route=is_lad)
            assert (
                main(["submit", "--factory-home", str(f.home), "--input", str(path)])
                == 0
            )
            capsys.readouterr()
        run = start(f)
        rows = until_jobs(f, 2)
        home = factory.Home(f.home)
        accepted = home.base_ref()
        record = Service(f.service.home).intake.lifecycle()[1]
        controls = [
            entry["call"]
            for entry in record["maintenance"].values()
            if entry.get("scope") == "parent"
            and (
                home.workspace
                / "controls"
                / (entry["call"]["envelope"]["request_id"] + ".json")
            ).is_file()
        ]
        assert {row["envelope"]["operation"] for row in controls} == {
            "resume_run",
            "pause_run",
        }
        assert all(lad._joined(Service(f.service.home), row) for row in controls)
        before = {
            row["envelope"]["request_id"]: row["observations"] for row in controls
        }
        # Exercise the existing expiry, not a minimum job/model duration rule.
        ttl = runtime.service.router_workers.observation_ttl
        finish = time.monotonic() + ttl + 1
        while time.monotonic() < finish:
            time.sleep(min(10, finish - time.monotonic()))
        lane = Lane(factory.Home(f.home))
        fallback = [
            lane.runtime.connection.observe(
                row["envelope"], deadline=time.monotonic() + 30
            )
            for row in controls
        ]
        assert all(
            value["source"] == "core_receipt" and value["holder_joined"] is None
            for value in fallback
        )
        assert (
            lane.parked(None, caller_deadline=time.monotonic() + 30)["closed"] is True
        )
        for target in [f.dev, f.val]:
            path = target / "src/calc.py"
            path.write_bytes(path.read_bytes())
            git(target, "status", "--porcelain")
        third = task(f.root, "expiry-three")
        subject = json.loads(third.read_bytes())
        subject["request"] = "Inspect accepted source only: no action"
        third.write_text(json.dumps(subject))
        assert (
            main(["submit", "--factory-home", str(f.home), "--input", str(third)]) == 0
        )
        capsys.readouterr()
        final = until_jobs(f, 3)
        assert all(row["status"] == "succeeded" for row in final)
        assert factory.Home(f.home).base_ref() == accepted
        stored = Service(f.service.home).intake.lifecycle()[1]
        for key, observations in before.items():
            row = stored["maintenance"]["parent:" + key]["call"]
            assert row["observations"][
                : len(observations)
            ] == observations and lad._joined(Service(f.service.home), row)
        (f.root / "expiry-proof.json").write_text(
            json.dumps(
                {
                    "existing_ttl_seconds": ttl,
                    "jobs": [row["status"] for row in final],
                    "original_two_jobs": [row["task_id"] for row in rows],
                    "saved_controls": before,
                    "public_after_expiry": fallback,
                    "accepted_base_unchanged_by_no_action": True,
                },
                indent=2,
            )
        )
    finally:
        close(f, run)


@pytest.mark.parametrize(
    "expiry_gap,next_lad",
    [(False, False), (True, False), (False, True)],
    ids=["boundary", "expiry", "next-lad"],
)
def test_held_lad_retains_parent_joins_across_operator_and_idle_expiry(
    runtime, monkeypatch, expiry_gap, next_lad
):
    import time
    import uuid

    from millrace_plus import lad_service_runtime as lad
    from millrace_plus.factory_cli import decide
    from millrace_plus.factory_lad import Lane
    from millrace_plus.lad_service import Service
    from millrace_plus.subworkflow_v2 import closed

    posts = []
    workers = runtime.service.router_workers
    original_submit = workers.submit

    def submit(home, request, **kwargs):
        posts.append((request["request_id"], request["operation"]))
        return original_submit(home, request, **kwargs)

    monkeypatch.setattr(workers, "submit", submit)
    f = provision(runtime, lad_hold=True)
    run = None
    try:
        home = factory.Home(f.home)
        home.submit(task(f.root, "held-one"))
        submitted = home.submit(task(f.root, "held-two", lad_route=True))
        run = start(f)

        def held():
            path = home.workspace / "jobs" / submitted["task_id"] / "state.json"
            if not path.exists():
                return None
            current = factory.Home(f.home)
            state = current.state(submitted["task_id"])
            facts = current.facts(state)
            view = control.inspect_run(f.instance, run, state_home=f.state)
            if state["phase"] != "hold" or not closed(view, attention=True):
                return None
            return (state, facts) if facts.get("held_parent_join_ref") else None

        state, facts = runtime.until(held, seconds=1200)
        proof = home.read_ref(facts["held_parent_join_ref"])
        assert proof["retained"] is True
        assert f.service.intake.tasks()[0]["status"] == "held"
        original = Service(f.service.home).intake.lifecycle()[1]
        sent = {
            key: row["call"]
            for key, row in original["maintenance"].items()
            if row.get("scope") == "parent" and row["call"]["reservation"] != "unsent"
        }
        assert any(r["envelope"]["operation"] == "resume_run" for r in sent.values())
        assert all(lad._joined(Service(f.service.home), r) for r in sent.values())
        prefixes = {key: row["observations"] for key, row in sent.items()}
        old_intent = facts["parent_resume_ref"]
        initial_owner = lad.Runtime(Service(f.service.home))
        _, initial_call = initial_owner.call(
            initial_owner.service.intake.tasks(facts["lad_task_id"])[0]
        )
        child_ledger = json.loads(
            (
                Path(initial_call["paths"]["project"])
                / "foreground/lad-state/session.json"
            ).read_bytes()
        )
        child_view = control.inspect_run(
            initial_call["instance_id"], initial_call["run_id"], state_home=runtime.home
        )
        assert child_ledger["pending"]["kind"] == "human"
        assert child_ledger["continuation"]["node"] == "human_pause"
        assert child_ledger["continuation"]["cleanup_required"] is True
        plane = child_view["planes"]["foreground"]
        assert plane["status"] == "paused"
        assert plane["cursor"] == plane["resume_target"] == "human_pause"
        assert closed(child_view, attention=True)
        original_child_observations = initial_call["os_observations"]
        ttl = runtime.service.router_workers.observation_ttl

        def expire_and_observe(rows):
            if not expiry_gap:
                return []  # Fast actual-boundary/close-failed proof, no expiry claim.
            finish = time.monotonic() + ttl + 1
            while time.monotonic() < finish:
                time.sleep(min(10, finish - time.monotonic()))
            owner = lad.Runtime(Service(f.service.home))
            values = [
                owner.connection.observe(
                    row["envelope"], deadline=time.monotonic() + 30
                )
                for row in rows.values()
            ]
            assert all(
                v["source"] == "core_receipt" and v["holder_joined"] is None
                for v in values
            )
            return values

        first_fallback = expire_and_observe(sent)
        child_evidence = home.retain(
            {
                "format": "millrace-lad-operator-intent/v1",
                "session_id": child_ledger["session_id"],
                "decision": "terminal_failure",
                "reason": "Explicitly close the demonstrated unresolved human choice",
            }
        )
        child_file = f.root / "continue-child.json"
        child_file.write_text(
            json.dumps(
                {
                    "state_ref": home.retain(state),
                    "changed_cause_ref": None,
                    "lad_decision": "close-failed",
                    "lad_evidence_ref": child_evidence,
                    "resume_plane": None,
                }
            )
        )
        from millrace_plus.local_intake import IntakeError

        child_id = uuid.uuid4().hex

        def continue_child(request_id, evidence_file):
            try:
                decide(
                    factory.Home(f.home),
                    submitted["task_id"],
                    "continue-lad",
                    request_id,
                    evidence_file,
                )
                return "continued"
            except IntakeError as exc:
                if exc.code in {
                    "fresh_recovery_unresolved",
                    "lost_call_receipt_unresolved",
                }:
                    return None  # Same ID/evidence reobserves the original recover.
                if exc.code == "separate_run_resume_required":
                    return "run-resume-required"
                raise

        continuation = runtime.until(
            lambda: continue_child(child_id, child_file), seconds=1200
        )
        assert continuation == "continued", (
            "Unexpected child run gate: report it without inferring another action"
        )
        # No human wait between starting child work and activating its observer.
        current = factory.Home(f.home)
        state = current.state(submitted["task_id"])
        parent = control.inspect_run(f.parent, f.parent_run, state_home=runtime.home)
        envelope = {
            "format": "millrace-router-request/v2",
            "request_id": uuid.uuid4().hex,
            "operation": "resume_plane",
            "instance_id": f.parent,
            "run_id": f.parent_run,
            "plane_id": "service",
            "expected_state_version": parent["state_version"],
            "payload": {},
        }
        request_ref = home.retain(envelope)
        parent_file = f.root / "continue-parent.json"
        parent_file.write_text(
            json.dumps(
                {
                    "state_ref": home.retain(state),
                    "changed_cause_ref": None,
                    "lad_decision": "parent-control",
                    "lad_evidence_ref": request_ref,
                    "resume_plane": "service",
                }
            )
        )
        parent_id = uuid.uuid4().hex
        decide(current, submitted["task_id"], "continue-lad", parent_id, parent_file)
        before_dispatch = Service(f.service.home).intake.lifecycle()[1]
        assert "parent:" + envelope["request_id"] not in before_dispatch["maintenance"]
        assert not any(
            r["request_id"] == envelope["request_id"] for r in parent["controls"]
        )
        assert not (
            home.workspace / "decisions" / (parent_id + ".lad-result.json")
        ).exists()
        resume(f, run)
        # The failed child is still active until explicit failed acknowledgement.
        # Its ordinary Service hold lets production retain the new parent row.
        held_state, held_facts = runtime.until(held, seconds=1200)
        held_proof = home.read_ref(held_facts["held_parent_join_ref"])
        assert any(
            row["request_id"] == envelope["request_id"] and row["holder_joined"] is True
            for row in held_proof["owner_proofs"]["parent_controls"]
        )
        owner = lad.Runtime(Service(f.service.home))
        active_task = owner.service.intake.tasks(facts["lad_task_id"])[0]
        _, terminal_call = owner.call(active_task)
        terminal_view = control.inspect_run(
            terminal_call["instance_id"],
            terminal_call["run_id"],
            state_home=runtime.home,
        )
        from millrace_plus.lad_service_disposition import domain_evidence

        domain = domain_evidence(
            owner.service, terminal_call, terminal_view, _publish=False
        )
        assert domain["missing"] == []
        acknowledgement = f.root / "acknowledge-child-failed.json"
        acknowledgement.write_text(
            json.dumps(
                {
                    "state_ref": home.retain(held_state),
                    "changed_cause_ref": None,
                    "lad_decision": "acknowledge-failed",
                    "lad_evidence_ref": child_evidence,
                    "resume_plane": None,
                }
            )
        )
        acknowledge_id = uuid.uuid4().hex
        decide(
            factory.Home(f.home),
            submitted["task_id"],
            "continue-lad",
            acknowledge_id,
            acknowledgement,
        )
        settled_lad = f.service.intake.tasks()[0]
        assert (
            settled_lad["status"] == "failed"
            and settled_lad["terminal_receipt"]["disposition"] == "failed"
        )
        current = factory.Home(f.home)
        close_file = f.root / "close-factory-failed.json"
        close_file.write_text(
            json.dumps(
                {
                    "state_ref": current.retain(current.state(submitted["task_id"])),
                    "changed_cause_ref": None,
                    "lad_decision": None,
                    "lad_evidence_ref": None,
                    "resume_plane": None,
                }
            )
        )
        decide(current, submitted["task_id"], "close", uuid.uuid4().hex, close_file)
        resume(f, run)
        rows = until_jobs(f, 2, statuses=["succeeded", "failed"])
        current = factory.Home(f.home)
        final_facts = current.facts(current.state(submitted["task_id"]))
        assert old_intent in final_facts["parent_resume_history_refs"]
        assert final_facts["parent_resume_ref"] != old_intent
        stored = Service(f.service.home).intake.lifecycle()[1]
        all_sent = {
            key: row["call"]
            for key, row in stored["maintenance"].items()
            if row.get("scope") == "parent" and row["call"]["reservation"] != "unsent"
        }
        assert all_sent["parent:" + envelope["request_id"]]["envelope"] == envelope
        assert all(
            lad._joined(Service(f.service.home), row) for row in all_sent.values()
        )
        for key, prefix in prefixes.items():
            assert all_sent[key]["observations"][: len(prefix)] == prefix
        task_row = f.service.intake.tasks()[0]
        assert task_row["terminal_receipt"]["disposition"] == "failed"
        owner = lad.Runtime(Service(f.service.home))
        _, child_call = owner.call(task_row)
        assert (
            child_call["os_observations"][: len(original_child_observations)]
            == original_child_observations
        )
        child_control_rows = []
        for selected_id in [child_id]:
            if selected_id is None:
                continue
            item = stored["maintenance"][selected_id]
            for label in ["pause", "recover", "resume"]:
                row = item[label]
                if row["envelope"] is not None:
                    sent = row["envelope"]
                    assert posts.count((sent["request_id"], sent["operation"])) == 1
                    assert lad._joined(owner.service, row)
                    child_control_rows.append(row)
        accepted = current.base_ref()
        assert (
            len(current.base_chain(accepted)) == 2
        )  # Failed LAD job advances no base.
        assert not (f.dev / "answer.py").exists()
        second_fallback = expire_and_observe(all_sent)
        assert (
            Lane(current).parked(None, caller_deadline=time.monotonic() + 30)["closed"]
            is True
        )
        third = task(f.root, "held-three", lad_route=next_lad)
        subject = json.loads(third.read_bytes())
        subject["request"] = (
            "Original request: answer() returns 42. "
            "Follow-up LAD assignment after explicit failed close."
            if next_lad
            else "Inspect accepted source only: no action"
        )
        third.write_text(json.dumps(subject))
        third_receipt = current.submit(third)
        final = until_jobs(
            f,
            3,
            statuses=["succeeded", "failed", "succeeded"],
            invocation_deadline=next_lad,
        )
        next_proof = None
        latest = factory.Home(f.home)
        if next_lad:
            next_state = latest.state(third_receipt["task_id"])
            next_facts = latest.facts(next_state)
            next_intent = latest.read_ref(next_facts["parent_resume_ref"])
            next_envelope = latest.read_ref(next_intent["request_ref"])
            assert next_envelope["operation"] == "resume_plane"
            assert next_envelope["plane_id"] == "service"
            assert posts.count((next_envelope["request_id"], "resume_plane")) == 1
            next_task = f.service.intake.tasks(next_facts["lad_task_id"])[0]
            assert next_task["claim"] is not None
            assert next_task["status"] == "succeeded"
            assert next_task["terminal_receipt"]["disposition"] == "succeeded"
            owner = lad.Runtime(Service(f.service.home))
            _, next_call = owner.call(next_task)
            next_view = control.inspect_run(
                next_call["instance_id"], next_call["run_id"], state_home=runtime.home
            )
            next_domain = domain_evidence(
                owner.service, next_call, next_view, _publish=False
            )
            assert next_domain["missing"] == []
            assert (
                Lane(latest).parked(None, caller_deadline=time.monotonic() + 30)[
                    "closed"
                ]
                is True
            )
            assert len(latest.base_chain(latest.base_ref())) == 3
            assert (
                f.dev / "answer.py"
            ).read_text().strip() == "def answer():\n    return 42"
            next_parent = Service(f.service.home).intake.lifecycle()[1]["maintenance"][
                "parent:" + next_envelope["request_id"]
            ]["call"]
            assert next_parent["envelope"] == next_envelope
            assert lad._joined(Service(f.service.home), next_parent)
            next_proof = {
                "owner_parent_call": next_parent,
                "intent": next_intent,
                "envelope": next_envelope,
                "task_claim": next_task["claim"],
                "terminal_receipt": next_task["terminal_receipt"],
                "domain_evidence": next_domain,
                "base_ref": latest.base_ref(),
            }
        else:
            assert latest.base_ref() == accepted
        (f.root / "held-expiry-proof.json").write_text(
            json.dumps(
                {
                    "existing_ttl_seconds": ttl,
                    "actual_expiry_gaps": 2 if expiry_gap else 0,
                    "next_lad_proof": next_proof,
                    "retained_held_boundary": proof,
                    "first_public_fallback": first_fallback,
                    "second_public_fallback": second_fallback,
                    "original_observation_prefixes": prefixes,
                    "declared_parent_envelope": envelope,
                    "child_close_failed_decision_id": child_id,
                    "child_acknowledge_failed_id": acknowledge_id,
                    "domain_evidence": domain,
                    "semantic_hold_ledger": child_ledger,
                    "semantic_hold_view": child_view,
                    "child_controls": child_control_rows,
                    "actual_posts": posts,
                    "plan_only_before_reactivation": True,
                    "jobs": [r["status"] for r in final],
                    "first_jobs": [r["task_id"] for r in rows],
                    "terminal_receipt": task_row["terminal_receipt"],
                    "parent_resume_history_refs": final_facts[
                        "parent_resume_history_refs"
                    ],
                },
                indent=2,
            )
        )
    finally:
        close(f, run)
