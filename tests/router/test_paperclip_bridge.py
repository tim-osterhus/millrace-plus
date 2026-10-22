"""Assignment authority and useful command-graph work over the OS HTTP boundary."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest

from millrace_plus.integrations import paperclip_bridge as bridge
from millrace_plus.integrations import paperclip_records as records

pytest_plugins = ["paperclip_support"]


def test_strict_assignment_preserves_redacted_bytes() -> None:
    value = {
        "format": "millrace-paperclip-context/v1",
        "company_id": "company-1",
        "agent_id": "agent-1",
        "issue_id": "issue-1",
        "attempt_id": "attempt-1",
        "description": "Exact redacted café\r\n\n",
    }
    assert records.parse_assignment(records.canonical(value)) == value
    for patch in ({"extra": 1}, {"description": None}, {"issue_id": "../x"}):
        with pytest.raises(records.BridgeError):
            records.parse_assignment(records.canonical({**value, **patch}))


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]", b"\xff", b" " * 65537]
)
def test_strict_json_boundaries(raw: bytes) -> None:
    with pytest.raises(records.BridgeError):
        records.parse_assignment(raw)


def test_assignment_duplicate_and_result_correlation(paperclip) -> None:
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert first["status"] == "succeeded", first
    assert first["refs"] == ["project/answer.md"]
    assert first["terminal_outcome"] == "READY"
    artifact = Path(first["result_root"]) / first["refs"][0]
    assert artifact.read_bytes() == paperclip.assignment["description"].encode()
    paperclip.assignment["attempt_id"] = "attempt-2"
    paperclip.write()
    second = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert second["run_id"] == first["run_id"]
    assert second["instance_id"] == first["instance_id"]
    assert second["attempt_id"] == "attempt-2"
    assert paperclip.operations.count("create_instance") == 1
    assert paperclip.operations.count("start_run") == 1


def test_bridge_config_copy_and_input_delivery(paperclip) -> None:
    result = bridge.run(paperclip.config_file, paperclip.assignment_file)
    root = Path(result["result_root"])
    manifest = json.loads((root / "project/subworkflow-inputs.json").read_bytes())
    assert manifest["inputs"]["request_markdown"] == paperclip.assignment["description"]
    assert "attempt_id" not in manifest["identity"]
    assert manifest["identity"]["assignment_revision"] == 1
    saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    assert saved["envelopes"]["create"]["payload"]["env_allowlist"] == []
    assert saved["phase"] == "terminal"
    assert "stdout" not in json.dumps(saved["observation"])
    assert root / "workflow" != paperclip.definition


def test_shared_config_and_description_conflict_preserve_target(paperclip) -> None:
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    original = copy.deepcopy(paperclip.config)
    for patch in (
        {"wait_seconds": 6},
        {
            "instance_settings": {
                **original["instance_settings"],
                "env_allowlist": ["LANG"],
            }
        },
    ):
        paperclip.config = {**original, **patch}
        paperclip.write()
        result = bridge.run(paperclip.config_file, paperclip.assignment_file)
        assert result["status"] == "refused"
        assert result["diagnostic"] == "assignment_conflict"
        assert result["instance_id"] == first["instance_id"]
    paperclip.config = original
    paperclip.assignment["description"] += "changed"
    paperclip.write()
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "refused"
    )
    assert paperclip.operations.count("start_run") == 1


def test_per_issue_revision_and_busy_observer(paperclip) -> None:
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    paperclip.config["assignment_revisions"] = {"other-issue": 9}
    paperclip.write()
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["run_id"]
        == first["run_id"]
    )
    assignment = records.freeze(
        records.parse_config(records.canonical(paperclip.config)), paperclip.assignment
    )
    store = records.Store(assignment)
    before = store.load()["deadline_at"]
    with store.lock(observer=True):
        busy = bridge.run(paperclip.config_file, paperclip.assignment_file)
        assert (
            busy["status"] == "attention_required"
            and busy["diagnostic"] == "attention_busy"
        )
        assert (
            bridge.inspect(paperclip.config_file, paperclip.assignment_file)[
                "deadline_at"
            ]
            == before
        )
    paperclip.assignment["issue_id"] = "other-issue"
    paperclip.write()
    other = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert other["run_id"] != first["run_id"]
    paperclip.assignment["issue_id"] = "issue-1"
    paperclip.config["assignment_revisions"]["issue-1"] = 2
    paperclip.write()
    reopened = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert reopened["run_id"] not in {first["run_id"], other["run_id"]}
    assert reopened["assignment"]["assignment_revision"] == 2


def test_new_revision_refuses_paused_binding_without_mutation(paperclip) -> None:
    paperclip.script('print(\'{"outcome":"HOLD"}\')\n')
    first = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert first["status"] == "attention_required"
    before = list(paperclip.operations)
    paperclip.config["assignment_revisions"] = {"issue-1": 2}
    paperclip.write()
    refused = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert (
        refused["status"] == "refused"
        and refused["diagnostic"] == "prior_binding_unsettled"
    )
    assert paperclip.operations == before


def test_cancel_requires_child_closure(paperclip) -> None:
    paperclip.script('import time\ntime.sleep(20)\nprint(\'{"outcome":"READY"}\')\n')
    cancelled = threading.Event()
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            bridge.run(
                paperclip.config_file,
                paperclip.assignment_file,
                cancelled=cancelled.is_set,
            )
        )
    )
    worker.start()
    from subworkflow_support import wait_for

    wait_for(lambda: "start_run" in paperclip.operations)
    cancelled.set()
    worker.join(10)
    assert not worker.is_alive()
    assert result[0]["status"] == "cancelled", result
    saved = bridge.inspect(paperclip.config_file, paperclip.assignment_file)
    assert saved["observation"]["quiescent"]
    assert all(
        row["writer_state"] == "stopped" for row in saved["observation"]["invocations"]
    )
    assert saved["envelopes"]["cancel"]["run_id"] == result[0]["run_id"]


def test_early_cancel_prevents_create_and_start(paperclip) -> None:
    result = bridge.run(
        paperclip.config_file, paperclip.assignment_file, cancelled=lambda: True
    )
    assert result["status"] == "cancelled"
    assert paperclip.operations == []


def test_credential_rotation_and_shared_config_refusal(paperclip) -> None:
    def rotate():
        paperclip.token = "y" * 48
        paperclip.credential.write_text(json.dumps({"token": paperclip.token}))

    paperclip.rotate = rotate
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "succeeded"
    )
    paperclip.assignment["issue_id"] = "issue-2"
    paperclip.write()
    assert (
        bridge.run(paperclip.config_file, paperclip.assignment_file)["status"]
        == "succeeded"
    )
    before = list(paperclip.operations)
    paperclip.config["wait_seconds"] = 6
    for issue in ("issue-1", "issue-2"):
        paperclip.assignment["issue_id"] = issue
        paperclip.write()
        assert (
            bridge.run(paperclip.config_file, paperclip.assignment_file)["diagnostic"]
            == "assignment_conflict"
        )
    assert paperclip.operations == before


def test_empty_refs_are_graph_owned_and_invalid_artifact_preserves_core(
    paperclip,
) -> None:
    paperclip.script('print(\'{"outcome":"READY"}\')\n')
    result = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert result["status"] == "succeeded" and result["refs"] == []
    # The reporting boundary checks files again even if the graph previously succeeded.
    paperclip.assignment["issue_id"] = "issue-2"
    paperclip.script(
        'from pathlib import Path\nPath("answer").write_text("useful")\n'
        'print(\'{"outcome":"READY","refs":["answer"]}\')\n'
    )
    second = bridge.run(paperclip.config_file, paperclip.assignment_file)
    Path(second["result_root"], "project/answer").unlink()
    report = bridge.run(paperclip.config_file, paperclip.assignment_file)
    assert report["status"] == "refused"
    assert (
        paperclip.client.inspect_run("local", second["instance_id"], second["run_id"])[
            "status"
        ]
        == "succeeded"
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"extra": True},
        {"os_origin": "https://127.0.0.1"},
        {"os_origin": "http://example.com"},
        {"paperclip_origin": "https://example.com/"},
        {"wait_seconds": True},
        {"cleanup_seconds": 61},
        {"assignment_revisions": {"issue-1": 0}},
        {
            "instance_settings": {
                "path": [],
                "env_allowlist": [],
                "host_paths": {},
                "guess": True,
            }
        },
    ],
)
def test_config_refusals_have_no_effects(paperclip, patch) -> None:
    with pytest.raises(records.BridgeError):
        records.parse_config(records.canonical({**paperclip.config, **patch}))
    assert paperclip.operations == []
    assert not Path(paperclip.config["binding_state_root"]).exists()
