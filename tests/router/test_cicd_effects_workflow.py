"""Real public CI-v2 graph with fake external effects and retained aftermath.

These journeys also run against the installed package. The fake peer writes
only its owned ledger. Repository HEAD/index and foreign work stay unchanged.
"""

from __future__ import annotations

import copy
import json

import pytest
from cicd_support import event, git, new_id, write_json
from cicd_v2_support import initialized
from test_cicd_effects import approval_decision, configure_effects
from test_cicd_workflow import _resume, _run, _start, drive

from millrace_plus import cicd_cli
from millrace_plus import cicd_effects as effects
from millrace_plus.cicd_v2_records import CicdV2Error
from millrace_plus.cicd_v2_runtime import Runtime, V2Home
from millrace_plus.router import control


def _checked(root, scenario="normal"):
    fx = initialized(root, configure=configure_effects(root, scenario))
    home = V2Home(fx["home"])
    fx["git_before"] = git(home.target, "rev-parse", "HEAD")
    fx["index_before"] = git(home.target, "ls-files", "--stage", "-z")
    accepted = home.submit(event(root, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch", _run(fx)
    assert not (root / "effect-ledger").exists()
    return fx, home, accepted["input_id"]


def _approve(fx, home, input_id):
    decision = approval_decision(home, input_id)
    path = write_json(fx["root"] / "approve-delivery.json", decision)
    args = [
        "decide",
        "--workflow-home",
        str(fx["home"]),
        "--event",
        input_id,
        "--request-id",
        decision["request_id"],
        "--decision",
        "approve_delivery",
        "--evidence",
        str(path),
    ]
    assert cicd_cli.main(args) == 0
    return decision


def _ledger(fx):
    path = fx["root"] / "effect-ledger/operations.jsonl"
    return (
        []
        if not path.exists()
        else [json.loads(line) for line in path.read_text().splitlines()]
    )


def _unchanged_git(fx):
    assert git(fx["target"], "rev-parse", "HEAD") == fx["git_before"]
    assert git(fx["target"], "ls-files", "--stage", "-z") == fx["index_before"]
    assert (fx["target"] / "src/notes.txt").read_text() == "calculator notes\n"


def _cancel(fx, input_id):
    assert (
        cicd_cli.main(
            [
                "cancel",
                "--workflow-home",
                str(fx["home"]),
                "--event",
                input_id,
                "--request-id",
                new_id(),
            ]
        )
        == 0
    )


def _stop(fx):
    view = _run(fx)
    receipt = control.cancel_run(
        str(fx["instance"]),
        str(view["id"]),
        state_home=fx["state"],
        request_id=new_id(),
    )
    assert receipt["status"] != "refused", receipt
    stopped = control.inspect_run(
        str(fx["instance"]), str(view["id"]), state_home=fx["state"]
    )
    assert stopped["quiescent"]
    assert all(row["writer_state"] == "stopped" for row in stopped["invocations"])
    return stopped


def _next_no_action(fx, home):
    second = home.submit(
        event(
            fx["root"],
            fx["head"],
            name="after-effect",
            run_id="after-effect",
            conclusion="success",
        )
    )
    drive(fx, _resume)
    assert V2Home(fx["home"]).task(second["input_id"])["status"] == "succeeded"
    return second


def test_public_approved_fake_commit_completes_and_terminal_replay_advances_fifo(
    tmp_path,
):
    fx, home, input_id = _checked(tmp_path)
    decision = _approve(fx, home, input_id)
    view = drive(fx, _resume)
    reloaded = V2Home(fx["home"])
    task = reloaded.task(input_id)
    assert task["status"] == "succeeded", view
    receipt = task["terminal_receipt"]
    report = reloaded.read_ref(reloaded.load_state(input_id)["settlement_ref"])
    assert report["status"] == "published"
    assert report["delivery_refs"]
    assert reloaded.decide(decision) == home.decide(decision)
    assert reloaded.task(input_id)["terminal_receipt"] == receipt
    nodes = [row["node"] for row in view["invocations"]]
    assert (
        nodes.index("prepare_effect")
        < nodes.index("execute_effect")
        < nodes.index("settle")
    )
    assert [row["operation"] for row in _ledger(fx)] == ["prepare", "execute"]
    assert _ledger(fx)[-1]["saved_before_execute"] is True
    assert all(row["writer_state"] == "stopped" for row in view["invocations"])
    _next_no_action(fx, reloaded)
    assert V2Home(fx["home"]).task(input_id)["terminal_receipt"] == receipt
    _unchanged_git(fx)
    _stop(fx)


@pytest.mark.parametrize("authorized_empty", [False, True])
def test_default_and_empty_effects_hold_without_peer_dispatch(
    tmp_path, authorized_empty
):
    def configure(value):
        configure_effects(tmp_path)(value)
        value["external_policy"]["effects"] = []
        value["external_policy"]["adapters"] = {}
        if not authorized_empty:
            value["external_policy"] = effects.vr.hold_external_policy()

    fx = initialized(tmp_path, configure=configure)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    assert home.read_ref(state["hold_ref"])["status"] == "checked_patch"
    assert home.status(accepted["input_id"])["inputs"][0]["delivery_basis_ref"] is None
    assert not _ledger(fx)
    assert not any(row["node"].endswith("_effect") for row in _run(fx)["invocations"])
    _stop(fx)


@pytest.mark.parametrize("scenario", ["lost_applied", "malformed_applied"])
def test_public_lost_applied_response_restarts_and_inspects_without_resend(
    tmp_path, scenario
):
    fx, home, input_id = _checked(tmp_path, scenario)
    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held", _run(fx)
    state = home.load_state(input_id)
    delivery, operations = effects.history(home, state)
    intent = home.read_ref(delivery["intent_ref"])
    assert operations[-1]["operation"] == "execute"
    assert operations[-1]["process_ref"] is not None
    process = home.read_ref(operations[-1]["process_ref"])
    assert process["process_facts"]["group_state"] == "closed"
    assert process["process_facts"]["root_reaped"] is True
    assert effects.disposition(home, state) == "inspect"
    assert [row["operation"] for row in _ledger(fx)] == ["prepare", "execute"]
    old = _stop(fx)
    # A new public run starts at reconcile, reading the original run's exact
    # completion and the durable possible-send intent. No direct state edits.
    view = drive(fx, _start)
    restarted = V2Home(fx["home"])
    assert restarted.task(input_id)["status"] == "succeeded", view
    assert old["id"] != view["id"]
    operations = _ledger(fx)
    assert [row["operation"] for row in operations] == ["prepare", "execute", "inspect"]
    assert {row["effect_id"] for row in operations} == {intent["effect_id"]}
    assert len({row["intent_sha256"] for row in operations}) == 1
    assert "execute_effect" not in [row["node"] for row in view["invocations"]]
    assert "inspect_effect" in [row["node"] for row in view["invocations"]]
    _unchanged_git(fx)
    _stop(fx)


def test_public_identical_retry_only_after_exact_not_applied_inspection(tmp_path):
    fx, home, input_id = _checked(tmp_path, "not_applied_once")
    _approve(fx, home, input_id)
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    assert [row["operation"] for row in _ledger(fx)] == ["prepare", "execute"]
    drive(fx, _resume)
    assert V2Home(fx["home"]).task(input_id)["status"] == "succeeded"
    observations = _ledger(fx)
    assert [row["operation"] for row in observations] == [
        "prepare",
        "execute",
        "inspect",
        "execute",
    ]
    assert len({row["effect_id"] for row in observations}) == 1
    assert len({row["intent_sha256"] for row in observations}) == 1
    _unchanged_git(fx)
    _stop(fx)


@pytest.mark.parametrize("possible_send", [False, True])
def test_public_cancel_preserves_effect_aftermath_and_next_input(
    tmp_path, possible_send
):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    if possible_send:
        _resume(fx)
        assert home.task(input_id)["status"] == "held"
    _cancel(fx, input_id)
    drive(fx, _resume)
    reloaded = V2Home(fx["home"])
    task = reloaded.task(input_id)
    assert task["status"] == "cancelled", _run(fx)
    receipt = task["terminal_receipt"]
    report = reloaded.read_ref(reloaded.load_state(input_id)["settlement_ref"])
    assert report["status"] == "cancelled"
    expected = ["prepare", "execute", "inspect"] if possible_send else []
    assert [row["operation"] for row in _ledger(fx)] == expected
    if possible_send:
        _, operations = effects.history(reloaded, reloaded.load_state(input_id))
        assert reloaded.read_ref(operations[-1]["result_ref"])["status"] == "applied"
    _next_no_action(fx, reloaded)
    assert V2Home(fx["home"]).task(input_id)["terminal_receipt"] == receipt
    _unchanged_git(fx)
    _stop(fx)


def test_pending_cancel_stays_held_and_cannot_resend(tmp_path):
    fx, home, input_id = _checked(tmp_path, "pending")
    _approve(fx, home, input_id)
    _resume(fx)
    _cancel(fx, input_id)
    _resume(fx)
    assert V2Home(fx["home"]).task(input_id)["status"] == "held"
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    assert effects.disposition(home, home.load_state(input_id)) == "inspect"
    _unchanged_git(fx)
    _stop(fx)


def test_authority_mutations_refuse_before_any_effect_peer(tmp_path):
    fx, home, input_id = _checked(tmp_path)
    decision = approval_decision(home, input_id)
    permission = home.read_ref(decision["permission_ref"])
    changes = [
        lambda p: p.update(input_id="foreign"),
        lambda p: p.update(candidate_id="foreign"),
        lambda p: p.update(config_sha256="f" * 64),
        lambda p: p.update(source_after_sha256="f" * 64),
        lambda p: p.update(patch_sha256="f" * 64),
        lambda p: p.update(authority_ref=home.retain({"foreign": "authority"})),
        lambda p: p["effects"][0]["target"].update(branch="foreign"),
        lambda p: p.update(
            approval_ref=home.retain(
                home.read_ref(p["approval_ref"]) | {"issuer": "foreign"}
            )
        ),
    ]
    for change in changes:
        mutated = copy.deepcopy(permission)
        change(mutated)
        changed = decision | {
            "request_id": new_id(),
            "permission_ref": home.retain(mutated),
        }
        with pytest.raises(CicdV2Error):
            home.decide(changed)
    assert not _ledger(fx)
    assert home.task(input_id)["status"] == "held"
    assert not home.load_state(input_id)["delivery_refs"]
    _stop(fx)


@pytest.mark.parametrize("advance", ["held_report", "state_version"])
def test_accepted_approval_rechecks_consuming_report_and_state(tmp_path, advance):
    fx, home, input_id = _checked(tmp_path)
    decision = _approve(fx, home, input_id)
    runtime = Runtime(home, fx["home"] / "workflow")
    state = home.load_state(input_id)
    # Force the old admission-to-consumption interleaving through the actual
    # domain CAS/report writers. No private Core or intake store edit occurs.
    with home.lock(), home.state_lock():
        if advance == "held_report":
            runtime._hold(state, "later_guard_report", "checked_patch")
        else:
            runtime._save(state)
    changed = home.load_state(input_id)
    assert changed["version"] == decision["expected_state_version"] + 1
    if advance == "held_report":
        assert changed["hold_ref"]["sha256"] != decision["basis_sha256"]
    else:
        assert changed["hold_ref"]["sha256"] == decision["basis_sha256"]
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    assert not home.load_state(input_id)["delivery_refs"]
    assert not _ledger(fx)
    assert not any(
        row["node"].endswith("_effect") for row in _run(fx)["invocations"]
    )
    _stop(fx)


def test_factory_source_mode_refuses_effects_before_preparation(tmp_path):
    def configure(value):
        configure_effects(tmp_path)(value)
        value["source_policy"]["mode"] = "factory"

    with pytest.raises(CicdV2Error, match="unsupported_until_full_ci_d1"):
        initialized(tmp_path, configure=configure)
    assert not (tmp_path / "home").exists()
    assert not (tmp_path / "effect-ledger").exists()


def test_source_drift_allows_inspection_but_holds_publication(tmp_path):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    _resume(fx)
    (fx["target"] / "src/notes.txt").write_text("foreign later edit\n")
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    _, operations = effects.history(home, home.load_state(input_id))
    assert home.read_ref(operations[-1]["result_ref"])["status"] == "applied"
    assert (fx["target"] / "src/notes.txt").read_text() == "foreign later edit\n"
    _stop(fx)


def _accepted_applied_inspection(tmp_path):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    _resume(fx)
    notes = fx["target"] / "src/notes.txt"
    original_notes = notes.read_bytes()
    notes.write_text("owned drift keeps the accepted inspection held\n")
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    _, operations = effects.history(home, home.load_state(input_id))
    assert operations[-1]["operation"] == "inspect"
    assert operations[-1]["status"] == "observed"
    # Source/check validation holds before the delivery guard accepts the
    # inspection. Public reconcile accepts its stopped completion first.
    _resume(fx)
    assert home.task(input_id)["status"] == "held"
    _, operations = effects.history(home, home.load_state(input_id))
    observed = operations[-1]
    assert observed["operation"] == "inspect" and observed["status"] == "accepted"
    assert observed["format"] == effects.RETAINED_OPERATION
    result = home.read_ref(observed["result_ref"])
    assert result["status"] == "applied"
    assert [row["original_ref"] for row in observed["retained_evidence_refs"]] == (
        result["evidence_refs"]
    )
    intent = home.read_ref(observed["request_ref"])
    adapter_result = home.workspace / (
        intent["effect_id"]
        + "-"
        + observed["invocation"]["invocation_id"]
        + ".effect-result.json"
    )
    assert home.resolve_ref(observed["result_ref"]).read_bytes() == (
        adapter_result.read_bytes()
    )
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    return fx, home, input_id, observed, original_notes


@pytest.mark.parametrize(
    ("damage", "terminal"), [("missing", "succeeded"), ("changed", "cancelled")]
)
def test_accepted_proof_survives_original_loss_and_public_settlement(
    tmp_path, damage, terminal
):
    fx, home, input_id, observed, original_notes = _accepted_applied_inspection(
        tmp_path
    )
    result = home.read_ref(observed["result_ref"])
    mapping = observed["retained_evidence_refs"][0]
    original = home.resolve_ref(mapping["original_ref"])
    retained = home.resolve_ref(mapping["retained_ref"])
    assert original != retained
    proof_bytes = retained.read_bytes()
    if damage == "missing":
        original.unlink()
    else:
        original.write_bytes(b"adapter replaced its proof after observation\n")
    reloaded = V2Home(fx["home"])
    state = reloaded.load_state(input_id)
    _, inspected = effects.history(reloaded, state)
    assert inspected[-1] == observed
    assert reloaded.read_ref(inspected[-1]["result_ref"]) == result
    assert reloaded.status(input_id)["inputs"][0]["state"]["delivery_refs"] == (
        state["delivery_refs"]
    )
    (fx["target"] / "src/notes.txt").write_bytes(original_notes)
    if terminal == "cancelled":
        _cancel(fx, input_id)
    view = drive(fx, _resume)
    settled = V2Home(fx["home"])
    task = settled.task(input_id)
    assert task["status"] == terminal, view
    report = settled.read_ref(settled.load_state(input_id)["settlement_ref"])
    assert report["status"] == ("cancelled" if terminal == "cancelled" else "published")
    assert report["delivery_refs"]
    assert settled.task(input_id)["terminal_receipt"] == task["terminal_receipt"]
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    assert retained.read_bytes() == proof_bytes
    if damage == "missing":
        assert not original.exists()
    else:
        assert original.read_bytes() != proof_bytes
    _unchanged_git(fx)
    _stop(fx)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_public_required_retained_proof_damage_refuses_with_valid_original(
    tmp_path, damage
):
    fx, home, input_id, observed, _ = _accepted_applied_inspection(tmp_path)
    mapping = observed["retained_evidence_refs"][0]
    original = home.resolve_ref(mapping["original_ref"])
    proof_bytes = original.read_bytes()
    retained = home.resolve_ref(mapping["retained_ref"])
    if damage == "missing":
        retained.unlink()
    else:
        retained.write_bytes(b"corrupt required retained proof\n")
    reloaded = V2Home(fx["home"])
    with pytest.raises((OSError, CicdV2Error)):
        effects.history(reloaded, reloaded.load_state(input_id))
    _resume(fx)
    assert _run(fx)["planes"]["main"]["status"] == "paused"
    assert original.read_bytes() == proof_bytes
    assert [row["operation"] for row in _ledger(fx)] == [
        "prepare",
        "execute",
        "inspect",
    ]
    if damage == "missing":
        assert not retained.exists()
    else:
        assert retained.read_bytes() == b"corrupt required retained proof\n"
    _stop(fx)


def test_direct_execute_reentry_cannot_dispatch_a_possible_send(tmp_path, monkeypatch):
    fx, home, input_id = _checked(tmp_path, "lost_applied")
    _approve(fx, home, input_id)
    _resume(fx)
    runtime = Runtime(home, fx["home"] / "workflow")
    state = home.load_state(input_id)
    _, operations = effects.history(home, state)
    context = operations[-1]["invocation"]
    # Real possible-send/public completion evidence, with only the caller's
    # current-invocation gate bypassed to target the duplicate-send refusal.
    monkeypatch.setattr(runtime, "_stopped", lambda *args: None)
    with pytest.raises(CicdV2Error, match="effect_inspection_required"):
        effects.operation(runtime, "execute", context)
    assert sum(row["operation"] == "execute" for row in _ledger(fx)) == 1
    _stop(fx)
