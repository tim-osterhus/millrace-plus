"""Typed feedback identity, advisory FIFO and actual stopped workflow cases."""

from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cicd_support import new_id, write_json
from cicd_v2_support import initialized
from test_cicd_workflow import _resume, _run, _start, drive

from millrace_plus import cicd_cli
from millrace_plus.cicd_feedback import (
    CORRELATION,
    FEEDBACK,
    feedback_identity,
    validate_feedback,
)
from millrace_plus.cicd_v2_records import DECISION, CicdV2Error, input_identity
from millrace_plus.cicd_v2_runtime import V2Home
from millrace_plus.local_intake import IntakeError, canonical
from millrace_plus.router import control


def report(root, head, *, report_id="report-1", kind="bug_report"):
    value = {
        "format": FEEDBACK,
        "kind": kind,
        "origin": {"producer": "local-monitor", "origin_refs": []},
        "report_id": report_id,
        "repository": "example/calc",
        "affected_version": {
            "release": "v1",
            "head_sha": head,
            "artifact_sha256": None,
        },
        "environment": {
            "name": "staging",
            "platform": "Linux",
            "configuration_ref": None,
        },
        "summary": "Addition returned the wrong value",
        "reproduction_steps": ["Add 2 and 3"],
        "evidence_refs": [],
        "impact": {
            "observed": "2 + 3 = -1",
            "affected_scope": "addition",
            "severity_hint": "high",
        },
        "occurred_at": "2026-10-31T00:00:00Z",
        "redaction": {
            "performed": True,
            "notes": "Local fixture, reviewed for secrets",
        },
    }
    return write_json(root / (report_id + ".json"), value)


def decision(home, input_id, *, kind="close", **changes):
    state = home.load_state(input_id)
    return {
        "format": DECISION,
        "request_id": new_id(),
        "input_id": input_id,
        "expected_state_version": state["version"],
        "kind": kind,
        "basis_sha256": state["hold_ref"]["sha256"],
        "target_stage": None,
        "changed_cause_ref": None,
        "permission_ref": None,
        "evidence_refs": [],
        **changes,
    }


def correlation(root, ids, *, identity="hypothesis-1", disposition="suggested"):
    return write_json(
        root / (identity + ".json"),
        {
            "format": CORRELATION,
            "correlation_id": identity,
            "input_ids": ids,
            "hypothesis": "May share addition defect",
            "evidence_refs": [],
            "confidence": "unconfirmed",
            "disposition": disposition,
            "actor": "test operator",
        },
    )


@pytest.mark.parametrize("kind", ["bug_report", "production_incident"])
def test_exact_dedup_preserves_correlated_fifo(tmp_path, kind):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    path = report(tmp_path, fx["head"], kind=kind)
    raw = path.read_bytes()
    first = home.submit_report(path)
    assert home.submit_report(path) == first
    expected_id = input_identity(kind, feedback_identity(json.loads(raw)))
    assert first["input_id"] == expected_id
    second = home.submit_report(
        report(tmp_path, fx["head"], report_id="near-duplicate", kind=kind)
    )
    before = home.intake.tasks()
    saved = home.correlate(correlation(tmp_path, [expected_id, second["input_id"]]))
    assert home.correlate(tmp_path / "hypothesis-1.json") == saved
    assert home.intake.tasks() == before
    assert [row["sequence"] for row in home.status()["inputs"]] == [1, 2]
    typed = home.input(expected_id)
    assert typed["kind"] == kind
    assert home.resolve_ref(typed["original_ref"]).read_bytes() == raw
    assert home.status(expected_id)["inputs"][0]["correlation_refs"] == [
        saved["correlation_ref"]
    ]
    # Changed raw bytes conflict even when the canonical report is unchanged.
    path.write_bytes(raw + b"\n")
    with pytest.raises(Exception, match="input_conflict"):
        home.submit_report(path)
    assert len(home.intake.tasks()) == 2


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda v: v.update(head_sha="0" * 40), "invalid_feedback"),
        (lambda v: v.update(repository="other/repo"), "repository_mismatch"),
        (lambda v: v.pop("redaction"), "invalid_feedback"),
        (lambda v: v["redaction"].update(performed=1), "invalid_feedback"),
        (
            lambda v: v["affected_version"].update(release=None, head_sha=None),
            "affected_version_unknown",
        ),
        (lambda v: v["origin"].update(producer=""), "invalid_feedback"),
        (lambda v: v.update(occurred_at="yesterday"), "invalid_feedback"),
        (lambda v: v.update(reproduction_steps=[" "]), "invalid_feedback"),
        (lambda v: v["impact"].update(severity_hint="urgent"), "invalid_feedback"),
        (
            lambda v: v.update(
                evidence_refs=[{"path": "../secret", "sha256": "0" * 64}]
            ),
            "invalid_feedback",
        ),
    ],
)
def test_feedback_refuses_invalid_before_admission(tmp_path, mutation, code):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    path = report(tmp_path, fx["head"])
    value = json.loads(path.read_bytes())
    mutation(value)
    path.write_bytes(canonical(value))
    with pytest.raises(CicdV2Error, match=code):
        home.submit_report(path)
    assert home.intake.tasks() == []


def test_evidence_lineage_and_reload_corruption_never_heals(tmp_path):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    first_path = report(tmp_path, fx["head"])
    renamed = tmp_path / "report.json"
    first_path.rename(renamed)
    first_path = renamed
    first = home.submit_report(first_path)
    path = report(tmp_path, fx["head"], report_id="corrected")
    value = json.loads(path.read_bytes())
    evidence = tmp_path / "actual.txt"
    evidence.write_text("observed failure\n")
    ref = {
        "path": evidence.name,
        "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
    }
    value["evidence_refs"] = [ref]
    value["origin"]["origin_refs"] = [
        {
            "path": first_path.name,
            "sha256": hashlib.sha256(first_path.read_bytes()).hexdigest(),
        }
    ]
    value["environment"]["configuration_ref"] = ref
    path.write_bytes(canonical(value))
    second = home.submit_report(path)
    typed = home.input(second["input_id"])
    assert len(typed["evidence_refs"]) == 2
    assert home.read_ref(typed["evidence_refs"][0]) == json.loads(
        first_path.read_bytes()
    )
    assert first["receipt"]["sequence"] < second["receipt"]["sequence"]
    original = home.resolve_ref(typed["original_ref"])
    retained = home.resolve_ref(typed["evidence_refs"][1])
    retained.write_bytes(b"corrupt")
    before = {p: p.read_bytes() for p in home.workspace.rglob("*") if p.is_file()}
    with pytest.raises(CicdV2Error, match="record_changed"):
        home.status()
    assert before == {
        p: p.read_bytes() for p in home.workspace.rglob("*") if p.is_file()
    }
    assert original.read_bytes() == path.read_bytes()


@pytest.mark.parametrize("shape", ["hash", "symlink", "hardlink", "missing"])
def test_external_evidence_must_be_exact_regular_files(tmp_path, shape):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    path = report(tmp_path, fx["head"])
    value = json.loads(path.read_bytes())
    actual = tmp_path / "evidence.txt"
    actual.write_bytes(b"fact")
    selected = actual
    if shape == "symlink":
        selected = tmp_path / "link"
        selected.symlink_to(actual)
    if shape == "hardlink":
        selected = tmp_path / "link"
        selected.hardlink_to(actual)
    value["evidence_refs"] = [
        {"path": selected.name, "sha256": hashlib.sha256(b"fact").hexdigest()}
    ]
    if shape == "hash":
        actual.write_bytes(b"changed")
    if shape == "missing":
        actual.unlink()
    path.write_bytes(canonical(value))
    with pytest.raises((CicdV2Error, OSError)):
        home.submit_report(path)
    assert home.intake.tasks() == []


def test_concurrent_submission_preserves_exact_receipt_and_conflict(tmp_path):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    path = report(tmp_path, fx["head"])

    def submit(_):
        try:
            return V2Home(fx["home"]).submit_report(path)
        except Exception as exc:
            assert exc.code in {"target_busy", "state_busy"}
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, range(2)))
    receipt = home.submit_report(path)
    assert all(item is None or item == receipt for item in results)
    assert len(home.intake.tasks()) == 1
    changed = tmp_path / "conflict.json"
    value = json.loads(path.read_bytes())
    value["summary"] = "different observation"
    changed.write_bytes(canonical(value))
    with ThreadPoolExecutor(2) as pool:
        good = pool.submit(home.submit_report, path)
        bad = pool.submit(V2Home(fx["home"]).submit_report, changed)
        try:
            assert good.result() == receipt
        except Exception as exc:
            assert exc.code == "target_busy"
        with pytest.raises(Exception, match="input_conflict|target_busy"):
            bad.result()
    assert home.submit_report(path) == receipt


@pytest.mark.parametrize("reported_head", ["0" * 40, None])
def test_stale_or_unmapped_report_holds_without_reproduction(tmp_path, reported_head):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    accepted = home.submit_report(report(tmp_path, reported_head))
    _start(fx)
    view = _run(fx)
    state = home.load_state(accepted["input_id"])
    held = home.read_ref(state["hold_ref"])
    assert held["status"] == "held_stale"
    assert held["hold_reason"] == (
        "head_mismatch" if reported_head else "source_binding_unproven"
    )
    assert not any(
        row["node"] in {"reproduce", "repair"} for row in view["invocations"]
    )
    control.cancel_run(
        fx["instance"], view["id"], state_home=fx["state"], request_id=new_id()
    )


def test_report_real_repair_explicit_refinement_and_fifo_aftermath(
    tmp_path, monkeypatch
):
    import cicd_support

    from millrace_plus.cicd_v2_runtime import Runtime

    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        "from pathlib import Path\n"
        "print('prerequisite', Path('dependency-state/version').read_text() "
        "if Path('dependency-state/version').exists() else 'v1')\n"
        "Path('build').mkdir(exist_ok=True)\nPath('build/check.txt').write_text('owned')\n"
        + cicd_support.CHECK,
    )
    fx = initialized(tmp_path, products=True, environment_paths=["dependency-state"])
    home = V2Home(fx["home"])
    first = home.submit_report(report(tmp_path, fx["head"]))["input_id"]
    second = home.submit_report(
        report(tmp_path, fx["head"], report_id="incident", kind="production_incident")
    )["input_id"]
    _start(fx)
    state = home.load_state(first)
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
    assert home.task(second)["status"] == "queued"
    attempts_before = len(state["attempt_refs"])
    unchanged = home.retain({"summary": "try again"})
    with pytest.raises(CicdV2Error, match="unchanged_cause"):
        home.decide(
            decision(
                home,
                first,
                kind="continue",
                target_stage="checks",
                changed_cause_ref=unchanged,
            )
        )
    assert len(home.load_state(first)["attempt_refs"]) == attempts_before
    (home.target / "dependency-state").mkdir()
    (home.target / "dependency-state/version").write_text("v2")
    cause = home.retain(
        Runtime(home, home.home / "workflow")._environment(home.observe())
    )
    home.decide(
        decision(
            home, first, kind="continue", target_stage="checks", changed_cause_ref=cause
        )
    )
    _resume(fx)
    final = home.load_state(first)
    fresh = [
        a
        for a in Runtime(home, home.home / "workflow")._attempts(final).values()
        if a["changed_cause_ref"] == cause
    ]
    assert len(fresh) == 1 and fresh[0]["status"] == "completed"
    assert home.read_ref(final["hold_ref"])["status"] == "checked_patch"
    first_close = decision(home, first)
    receipt = home.decide(first_close)
    _resume(fx)
    assert home.task(first)["status"] == "succeeded"
    second_state = home.load_state(second)
    assert home.read_ref(second_state["hold_ref"])["status"] == "held_unreproduced"
    home.decide(decision(home, second))
    drive(fx, _resume)
    loaded = V2Home(home.home)
    assert loaded.task(second)["status"] == "failed"
    assert loaded.decide(first_close) == receipt
    assert (
        loaded.task(first)["terminal_receipt"] == home.task(first)["terminal_receipt"]
    )
    assert not (home.target / "build").exists()
    assert (home.target / "src/calc.py").read_text().endswith("return a + b\n")
    view = _run(fx)
    control.cancel_run(
        fx["instance"], view["id"], state_home=fx["state"], request_id=new_id()
    )
    closed = control.inspect_run(fx["instance"], view["id"], state_home=fx["state"])
    assert closed["quiescent"]
    assert all(row["writer_state"] == "stopped" for row in closed["invocations"])


def test_submit_report_cli_and_advisory_corruption(tmp_path, capsys):
    fx = initialized(tmp_path)
    path = report(tmp_path, fx["head"])
    assert (
        cicd_cli.main(
            ["submit-report", "--workflow-home", str(fx["home"]), "--report", str(path)]
        )
        == 0
    )
    accepted = json.loads(capsys.readouterr().out)
    home = V2Home(fx["home"])
    record = correlation(tmp_path, [accepted["input_id"]])
    assert (
        cicd_cli.main(
            [
                "correlate",
                "--workflow-home",
                str(fx["home"]),
                "--correlation",
                str(record),
            ]
        )
        == 0
    )
    ref = json.loads(capsys.readouterr().out)["correlation_ref"]
    before = home.intake.tasks()
    value = json.loads(record.read_bytes())
    value["disposition"] = "human_confirmed"
    record.write_bytes(canonical(value))
    with pytest.raises(Exception, match="correlation_conflict"):
        home.correlate(record)
    bad = copy.deepcopy(value)
    bad["input_ids"] = ["0" * 64]
    bad["correlation_id"] = "missing"
    with pytest.raises(
        (CicdV2Error, IntakeError), match="input_not_found|task_not_found"
    ):
        home.correlate(write_json(tmp_path / "missing.json", bad))
    assert home.intake.tasks() == before
    retained = home.resolve_ref(ref)
    retained.write_bytes(canonical(bad))
    with pytest.raises(CicdV2Error, match="record_changed"):
        home.status()
    assert retained.read_bytes() == canonical(bad)


def test_strict_feedback_json_and_schema(tmp_path):
    from millrace_plus.local_intake import IntakeError, strict_json

    path = report(tmp_path, "0" * 40)
    raw = path.read_bytes()
    with pytest.raises(IntakeError):
        strict_json(raw.replace(b'"format":', b'"format":"bad", "format":', 1))
    value = json.loads(raw)
    assert validate_feedback(value) == value
    schemas = Path(__file__).parents[2] / "src/millrace_plus/workflows/cicd-v2/schemas"
    assert json.loads((schemas / "input.json").read_bytes())["properties"]["kind"][
        "enum"
    ] == ["ci_event", "bug_report", "production_incident"]
    assert (
        json.loads((schemas / "feedback.json").read_bytes())["additionalProperties"]
        is False
    )


def test_explicit_false_redaction_is_retained_without_secret_absence_claim(tmp_path):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    path = report(tmp_path, fx["head"])
    value = json.loads(path.read_bytes())
    value["redaction"] = {"performed": False, "notes": "No redaction performed"}
    path.write_bytes(canonical(value))
    accepted = home.submit_report(path)
    original = home.read_ref(home.input(accepted["input_id"])["original_ref"])
    assert original["redaction"] == value["redaction"]


def test_missing_projection_read_refuses_without_repair(tmp_path):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    accepted = home.submit_report(report(tmp_path, fx["head"]))
    path = home.workspace / (accepted["input_id"] + ".input.json")
    path.unlink()
    with pytest.raises(FileNotFoundError):
        home.status()
    assert not path.exists()


def test_feedback_is_explicitly_unavailable_in_v1_home(tmp_path, capsys):
    from cicd_support import initialized as v1_initialized

    fx = v1_initialized(tmp_path)
    path = report(tmp_path, fx["head"])
    result = cicd_cli.main(
        ["submit-report", "--workflow-home", str(fx["home"]), "--report", str(path)]
    )
    assert result == 2
    assert json.loads(capsys.readouterr().err)["code"] == "feedback_requires_v2"


def test_correlation_evidence_can_retain_prior_record_without_reclassifying_it(
    tmp_path,
):
    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    accepted = home.submit_report(report(tmp_path, fx["head"]))
    path = correlation(tmp_path, [accepted["input_id"]])
    first = home.correlate(path)["correlation_ref"]
    source = tmp_path / "prior.correlation.json"
    source.write_bytes(home.resolve_ref(first).read_bytes())
    path = correlation(
        tmp_path,
        [accepted["input_id"]],
        identity="follow-up",
        disposition="human_confirmed",
    )
    value = json.loads(path.read_bytes())
    value["evidence_refs"] = [
        {"path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    ]
    path.write_bytes(canonical(value))
    second = home.correlate(path)["correlation_ref"]
    refs = home.status(accepted["input_id"])["inputs"][0]["correlation_refs"]
    assert set(map(canonical, refs)) == set(map(canonical, [first, second]))
    home.resolve_ref(first).unlink()
    with pytest.raises(FileNotFoundError):
        home.status()
