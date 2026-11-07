"""Public Plus/Core routing, captured handoff and honest writer holds."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from millforge_support import fake_cli, flow, with_handoff
from millrace.router import control as core_control
from plus_support import create_instance, new_id, start_run

from millrace_plus.router import control
from millrace_plus.router.resolver import resolve_adapter


def test_exact_pair_and_core_only_refusal(single_flow):
    adapter = resolve_adapter("millforge_run", "millrace-millforge-run/v1")
    assert adapter.adapter_id == "millforge_run"
    workflow, project, home = flow(single_flow)
    refused = core_control.create_instance(
        workflow, project, state_home=home, request_id=new_id(),
        expected_state_version=0,
    )
    assert refused["status"] == "refused"
    assert refused["code"] == "unsupported_adapter_contract"
    assert not list(home.glob("instances/*/state.sqlite3"))


@pytest.mark.parametrize(
    "outcome,status", [("READY", "succeeded"), ("REJECT", "failed")],
)
def test_completed_domain_outcome_selects_declared_graph_branch(
    single_flow, outcome, status,
):
    workflow, project, home = flow(single_flow, output={"outcome": outcome})
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == status
    assert result["count"] == 1
    completion = result["completions"][0]
    assert completion["classification"] == "semantic"
    assert completion["outcome"] == outcome
    assert completion["transport"] == "completed"
    assert completion["effective_writer"] == "stopped"
    assert json.loads(completion["stdout"]) == {"outcome": outcome}
    accepted = result["invocations"][0]["adapter_evidence"]["accepted"]
    assert accepted["status"] == "completed"
    assert accepted["domain_outcome"] == outcome


@pytest.mark.parametrize("options", [{"exit": 3}, {"exit": 3, "invalid": True}])
def test_native_failure_never_takes_negative_semantic_branch(single_flow, options):
    workflow, project, home = flow(single_flow, **options)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    completion = result["completions"][0]
    assert result["status"] == "paused"
    assert completion["transport"] == "execution_failure"
    assert completion["stdout"] == ""
    assert completion["outcome"] is None
    evidence = result["invocations"][0]["adapter_evidence"]
    assert evidence["failure_code"] == "millforge_runtime_failed"
    if options.get("invalid"):
        assert completion["effective_writer"] == "may_live"
        assert result["pause_reason"] == "writer_unknown"
        assert evidence["accepted"] is None
    else:
        assert completion["effective_writer"] == "stopped"
        assert result["planes"]["main"]["pause_reason"] == "execution_error"
        assert evidence["accepted"]["status"] == "failed"


@pytest.mark.parametrize(
    "options", [{"missing": True}, {"invalid": True}, {"stale": True}],
)
def test_invalid_public_record_holds_without_semantics(single_flow, options):
    workflow, project, home = flow(single_flow, **options)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["pause_reason"] == "writer_unknown"
    assert result["count"] == 1
    assert result["completions"][0]["stdout"] == ""
    assert result["completions"][0]["effective_writer"] == "may_live"
    assert result["transitions"] == []
    blocked = control.start_run(
        instance, state_home=home, request_id=new_id(),
        expected_state_version=result["state_version"],
    )
    assert blocked["status"] == "refused"
    later = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert later["count"] == 1
    assert later["invocations"] == result["invocations"]


def test_selected_handoff_is_exact_context_and_historical_reload_is_pure(single_flow):
    workflow, project, home = flow(single_flow, output={"outcome": "READY"})
    with_handoff(workflow, project)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    result = start_run(instance, state_home=home)
    assert result["status"] == "succeeded"
    consumer = next(
        row for row in result["invocations"] if row["plane_id"] == "consumer"
    )
    launch = consumer["launch"]
    supplied = base64.b64decode(launch["seam_input"]["content"])
    saved_file = Path(launch["details"]["output_path"]) / "request.json"
    saved = json.loads(saved_file.read_text())
    assert saved["context"] == [supplied.decode("utf-8")]
    assert "M4_SEAM_PAYLOAD" in saved["context"][0]
    assert launch["details"]["seam"]["sha256"] == launch["seam_input"]["sha256"]
    assert "seam" not in launch["settings"]
    Path(json.loads(workflow.read_text())["bindings"]["native"]["executable"]).unlink()
    (workflow.parent / "profile.json").unlink()
    (workflow.parent / "task.txt").unlink()
    historical = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert historical["invocations"] == result["invocations"]
    assert historical["completions"] == result["completions"]
    assert historical["seam_acknowledgements"] == result["seam_acknowledgements"]


def test_guarded_reconciliation_does_not_turn_failed_attempt_into_success(single_flow):
    workflow, project, home = flow(single_flow, invalid=True)
    instance = str(create_instance(workflow, project, state_home=home)["instance_id"])
    held = start_run(instance, state_home=home)
    invocation = held["invocations"][0]
    # This ordinary peer has no detached children. Core's helper already proved
    # its group absent; the test supplies that exact external fixture fact.
    assert invocation["adapter_evidence"]["main"]["cleanup_confirmed"]
    receipt = control.reconcile_invocation(
        instance, str(held["id"]), plane_id="main", invocation_id=invocation["id"],
        stopped=True, reason="owned fake CLI has no worker and its group closed",
        state_home=home, request_id=new_id(),
        expected_state_version=held["state_version"],
    )
    assert receipt["status"] == "applied"
    observed = control.inspect_run(instance, str(held["id"]), state_home=home)
    assert observed["count"] == 1
    assert observed["completions"][0]["stdout"] == ""
    assert observed["completions"][0]["effective_writer"] == "stopped"
    control.cancel_run(instance, str(held["id"]), state_home=home, request_id=new_id())
    fake_cli(workflow.parent, output={"outcome": "READY"})
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded"
    assert fresh["invocations"][0]["id"] != invocation["id"]
