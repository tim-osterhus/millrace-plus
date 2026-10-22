"""LAD domain accounting and real graph/seam execution with inert peers."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from millrace_plus import catalog
from millrace_plus.router import control

LAD = catalog.bundled_root() / "lad"


def copied(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(LAD, project)
    return project


def command(project: Path, script: str, *args: str) -> dict:
    return command_at(project, "foreground", script, *args)


def command_at(
    project: Path, plane: str, script: str, *args: str, seam: Path | None = None
) -> dict:
    environment = {
        "PATH": str(Path(sys.executable).parent),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if seam:
        environment["MILLRACE_SEAM_INPUT"] = str(seam)
    result = subprocess.run(
        [sys.executable, str(project / "scripts" / script), *args],
        cwd=project / plane,
        env=environment,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def ledger(project: Path) -> dict:
    return json.loads((project / "foreground/lad-state/session.json").read_bytes())


def bootstrap(project: Path) -> None:
    shutil.copyfile(
        Path(__file__).parent / "fixtures/lad_peer.py", project / "scripts/lad_peer.py"
    )
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "NEW_SESSION"
    )
    assert (
        command(project, "setup_intake.py", "--project-root", "..")["outcome"]
        == "INTAKE_READY_EXISTING"
    )


def step(
    project: Path, role: str, outcome: str | None = None, scenario: str = "clean"
) -> str:
    assert command(project, "prepare_role.py", role)["outcome"] == "READY"
    peer = command(
        project, "lad_peer.py", role, scenario, *([outcome] if outcome else [])
    )
    accepted = command(project, "accept_role.py", role, peer["outcome"])
    assert accepted["outcome"] != "REFUSED", accepted
    return accepted["outcome"]


def before_role(project: Path, role: str) -> None:
    bootstrap(project)
    if role == "recon":
        return
    if role == "consultant":
        assert step(project, "recon", "FINDINGS") == "CONSULTANT"
        return
    step(project, "recon")
    if role == "planner":
        return
    step(project, "planner")
    if role == "manager":
        return
    step(project, "manager")
    if role == "builder":
        return
    if role == "troubleshooter":
        assert step(project, "builder", "ERROR") == "TROUBLESHOOTER"
        return
    step(
        project,
        "builder",
        scenario="repair"
        if role in {"fixer", "doublechecker", "learning"}
        else "clean",
    )
    if role == "checker":
        return
    step(project, "checker")
    if role in {"fixer", "updater"}:
        return
    if role == "arbiter":
        step(project, "updater")
        return
    step(project, "fixer", scenario="gate")
    if role == "doublechecker":
        return
    assert role == "learning"
    step(project, "doublechecker")
    step(project, "fixer", scenario="gate")
    assert step(project, "doublechecker") == "ASSESS"


def test_all_roles_have_graph_routes() -> None:
    from millrace.router.graph import parse_graph

    graph = json.loads((LAD / "workflow.json").read_bytes())
    parsed = parse_graph((LAD / "workflow.json").read_bytes())
    assert parsed.max_invocations == 609
    for plane in graph["planes"].values():
        for role, node in plane["nodes"].items():
            if node["invoke"].get("binding") != "claude":
                continue
            assert (
                node["on_error"]
                == node["on_unknown"]
                == {"goto": "prepare_" + role + "_replacement"}
            )
            for outcome in node["outcomes"]:
                assert node["on"][outcome] == {
                    "goto": "accept_" + role + "_" + outcome.lower()
                }
                accept = plane["nodes"][node["on"][outcome]["goto"]]
                assert "REFUSED" in accept["outcomes"]
            assert "retry" not in json.dumps(node)
    assert graph["planes"]["foreground"]["nodes"]["emit_learning_request"]["on"][
        "REQUEST"
    ] == {"goto": "accept_learning_ack"}


ROLE_OUTCOMES = [
    (role, outcome)
    for plane in json.loads((LAD / "workflow.json").read_bytes())["planes"].values()
    for role, node in plane["nodes"].items()
    if node["invoke"].get("binding") == "claude" and role != "learning"
    for outcome in node["outcomes"]
]


@pytest.mark.parametrize(("role", "outcome"), ROLE_OUTCOMES)
def test_all_role_semantic_routes(tmp_path: Path, role: str, outcome: str) -> None:
    project = copied(tmp_path)
    before_role(project, role)
    if role == "checker" and outcome == "FINDINGS_ACTIONABLE":
        (project / "foreground/target/answer.py").write_text(
            "def answer():\n    return 0\n"
        )
    if role == "troubleshooter" and outcome == "PASS_REVIEW_REPAIRED":
        # That route needs a real inspectable repair and a review escalation.
        project2 = copied(tmp_path / "review")
        before_role(project2, "learning")
        state = ledger(project2)
        # A test-owned fixture before any Core daemon starts selects the recorded
        # review recovery. Native/acceptance evidence is still produced normally.
        state["pending"].update(kind="review", target="doublechecker")
        state["continuation"]["node"] = "prepare_troubleshooter"
        save_fixture(project2, state)
        project = project2
    assert command(project, "prepare_role.py", role)["outcome"] == "READY"
    command(project, "lad_peer.py", role, "clean", outcome)
    accepted = command(project, "accept_role.py", role, outcome)
    assert accepted["outcome"] != "REFUSED", accepted
    graph = json.loads((project / "workflow.json").read_bytes())
    expected = graph["planes"]["foreground"]["nodes"][
        "accept_" + role + "_" + outcome.lower()
    ]
    assert accepted["outcome"] in expected["outcomes"]
    snapshot = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "accept_role.py", role, outcome) == accepted
    assert (project / "foreground/lad-state/session.json").read_bytes() == snapshot


def save_fixture(project: Path, state: dict) -> None:
    import hashlib

    state.pop("integrity_sha256", None)
    state["integrity_sha256"] = hashlib.sha256(
        json.dumps(
            state, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    (project / "foreground/lad-state/session.json").write_text(json.dumps(state))


def test_arbiter_process_deviation_requires_human_choice(tmp_path: Path) -> None:
    project = copied(tmp_path)
    before_role(project, "arbiter")
    assert command(project, "prepare_role.py", "arbiter")["outcome"] == "READY"
    command(project, "lad_peer.py", "arbiter", "clean", "HUMAN_BLOCKED")
    state = ledger(project)
    oid = state["active"]["foreground"]
    occurrence = project / "foreground/lad-state/occurrences" / oid
    file = project / "foreground/lad-results" / oid / "result.json"
    blocked = json.loads(file.read_bytes())
    verdict = blocked["data"]["verdict"]
    assert verdict == {
        "answer": {
            "status": "pass",
            "evidence": "Original request requires answer() == 42; "
            "current target and accepted review agree.",
        }
    }
    blocked["human_evidence"] = {
        "type": "human_choice",
        "obstacle": "Test-owned prior evidence reports an operating-constraint "
        "deviation outside the stable answer criterion.",
        "attempted": "Checked the retained report and passing stable verdict; "
        "a source repair cannot undo the reported historical action.",
        "required_resolution": "Operator must decide how to disposition the "
        "reported process deviation without changing the stable criterion.",
    }
    assert blocked["findings"] == []
    snapshot = (project / "foreground/lad-state/session.json").read_bytes()
    for criterion, reason in [
        ("operating-constraint", "finding_identity"),
        ("answer", "remediation_gap_required"),
    ]:
        invalid = blocked | {
            "outcome": "FINDINGS",
            "human_evidence": None,
            "findings": [
                {
                    "id": "OPS-01",
                    "criterion": criterion,
                    "path": "prior-process-report.md",
                    "reproduction": "Read the test-owned prior deviation report.",
                    "evidence": blocked["human_evidence"]["obstacle"],
                }
            ],
        }
        file.write_text(json.dumps(invalid))
        refused = command(project, "accept_role.py", "arbiter", "FINDINGS")
        assert refused["outcome"] == "REFUSED"
        assert reason in refused["summary"]
        assert (project / "foreground/lad-state/session.json").read_bytes() == snapshot

    file.write_text(json.dumps(blocked))
    accepted = command(project, "accept_role.py", "arbiter", "HUMAN_BLOCKED")
    assert accepted["outcome"] == "PAUSE"
    held = ledger(project)
    assert held["criteria"] == state["criteria"]
    assert held["usage"] == state["usage"]
    assert held["usage"]["all"] == 7 and held["usage"]["arbiter"] == 1
    assert "remediation" not in held["usage"]
    assert held["terminal"] is None
    assert held["pending"]["kind"] == "human"
    assert held["pending"]["target"] == "arbiter"
    assert held["continuation"]["node"] == "human_pause"
    assert held["continuation"]["cleanup_required"] is True
    assert held["accepted"][oid]["result"]["data"]["verdict"] == verdict
    snapshot = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "accept_role.py", "arbiter", "HUMAN_BLOCKED") == accepted
    for _ in range(2):
        assert command(project, "boundaries.py", "human_pause")["outcome"] == "HOLD"
        assert (project / "foreground/lad-state/session.json").read_bytes() == snapshot
    nodes = json.loads((project / "workflow.json").read_bytes())["planes"][
        "foreground"
    ]["nodes"]
    assert nodes["accept_arbiter_human_blocked"]["on"]["PAUSE"] == {
        "goto": "human_pause"
    }
    assert "pause" in nodes["human_pause"]["on"]["HOLD"]
    for name in ("prompts/arbiter.md", "context/role-contracts.md"):
        guidance = (occurrence / "guidance" / name).read_text()
        assert "HUMAN_BLOCKED" in guidance
        assert "human_choice" in guidance
        assert "findings=[]" in guidance


@pytest.mark.parametrize("manifest_kind", ["helper", "paperclip", "direct"])
def test_all_launchers_bootstrap_and_reuse_session(
    tmp_path: Path, manifest_kind: str
) -> None:
    import hashlib

    project = copied(tmp_path)
    raw = "  Original request: answer() returns 42.\n\n  Exact post-redaction bytes.  "
    if manifest_kind == "direct":
        (project / "request.md").write_bytes(raw.encode())
        expected = None
    else:
        identity = {
            "call_home": "/test-owned/calls",
            "call_key": "helper" if manifest_kind == "helper" else "paperclip_1",
            "output_root": "/test-owned/project",
        }
        (project / "subworkflow-inputs.json").write_text(
            json.dumps(
                {
                    "format": "millrace-plus-subworkflow-inputs/v1",
                    "identity": identity,
                    "inputs": {"request_markdown": raw},
                }
            )
        )
        encoded = json.dumps(
            identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        expected = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "millrace-lad-session/v1/" + hashlib.sha256(encoded).hexdigest(),
            )
        )
    first = command(project, "init_session.py", "--project-root", "..")
    assert first["outcome"] == "NEW_SESSION"
    if expected:
        assert ledger(project)["session_id"] == expected
    assert (
        command(project, "setup_intake.py", "--project-root", "..")["outcome"]
        == "INTAKE_READY_EXISTING"
    )
    assert (project / "foreground/request.md").read_bytes() == raw.encode()
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "EXISTING_SESSION_PRISTINE"
    )
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    before = (project / "foreground/lad-state/session.json").read_bytes()
    used = command(project, "init_session.py", "--project-root", "..")
    assert used["outcome"] == "EXISTING_SESSION_WITH_WORK"
    assert ledger(project)["active"]["foreground"] in used["summary"]
    assert (project / "foreground/lad-state/session.json").read_bytes() == before


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_key",
        "missing_identity",
        "nonobject_inputs",
        "unknown_field",
        "wrong_version",
    ],
)
def test_malformed_present_manifest_refuses_fresh_fallback(
    tmp_path: Path, mutation: str
) -> None:
    project = copied(tmp_path)
    value = {
        "format": "millrace-plus-subworkflow-inputs/v1",
        "identity": {"call_key": "test"},
        "inputs": {"request_markdown": "answer() returns 42"},
    }
    if mutation == "missing_identity":
        value.pop("identity")
    elif mutation == "nonobject_inputs":
        value["inputs"] = []
    elif mutation == "unknown_field":
        value["extra"] = True
    elif mutation == "wrong_version":
        value["format"] = "v2"
    raw = json.dumps(value)
    if mutation == "duplicate_key":
        raw = raw[:-1] + ',"inputs":{}}'
    (project / "subworkflow-inputs.json").write_text(raw)
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "RECONCILE"
    )
    assert not (project / "foreground/lad-state").exists()


def test_server_local_missing_intake_and_alias_conflict(tmp_path: Path) -> None:
    project = copied(tmp_path)
    command(project, "init_session.py", "--project-root", "..")
    assert (
        command(project, "setup_intake.py", "--project-root", "..")["outcome"]
        == "INTAKE_MISSING"
    )
    outside = tmp_path / "outside.md"
    outside.write_text("answer() returns 42")
    (project / "request.md").symlink_to(outside)
    assert (
        command(project, "setup_intake.py", "--project-root", "..")["outcome"]
        == "INTAKE_CONFLICT"
    )
    assert not (project / "foreground/request.md").exists()


@pytest.mark.parametrize(
    "field",
    [
        "usage",
        "events",
        "active",
        "occurrences",
        "accepted",
        "limits",
        "identity_sha256",
        "continuation",
        "session_id",
        "pending",
        "baselines",
    ],
)
def test_corrupt_session_authority_never_replenishes(
    tmp_path: Path, field: str
) -> None:
    project = copied(tmp_path)
    before_role(project, "planner")
    state = ledger(project)
    state.pop(field)
    file = project / "foreground/lad-state/session.json"
    file.write_text(json.dumps(state))
    before = file.read_bytes()
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "RECONCILE"
    )
    assert command(project, "prepare_role.py", "planner")["outcome"] == "REFUSED"
    assert file.read_bytes() == before


@pytest.mark.parametrize(
    "field",
    [
        "occurrence_id",
        "role",
        "lineage",
        "input_sha256",
        "source_sha256",
        "guidance_sha256",
        "outcome",
        "artifacts",
        "extra",
    ],
)
def test_stale_or_malformed_role_result_preserves_debit(
    tmp_path: Path, field: str
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    command(project, "lad_peer.py", "recon", "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    file = project / "foreground/lad-results" / oid / "result.json"
    result = json.loads(file.read_bytes())
    result[field] = [] if field == "artifacts" else "wrong"
    file.write_text(json.dumps(result))
    before = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "REFUSED"
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    assert ledger(project)["usage"] == {"all": 1, "recon": 1}


def test_accepted_artifact_is_immutable_and_replay_is_once(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    step(project, "recon")
    state = ledger(project)
    oid = state["active"]["foreground"]
    before = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    artifact = project / "foreground/lad-results" / oid / "intake.md"
    artifact.write_text(artifact.read_text() + "Conflicting edit.\n")
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "REFUSED"
    assert command(project, "prepare_role.py", "planner")["outcome"] == "REFUSED"


def test_native_replacement_uses_shared_budget_and_normal_redirect_refuses(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    original = ledger(project)["active"]["foreground"]
    before = ledger(project)["usage"]
    assert command(project, "prepare_role.py", "recon")["outcome"] == "REFUSED"
    assert ledger(project)["usage"] == before
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "READY"
    )
    state = ledger(project)
    replacement = state["active"]["foreground"]
    assert replacement != original
    assert state["occurrences"][replacement]["replacement_of"] == original
    assert state["usage"] == {"all": 2, "recon": 2, "replacement:recon:unpacketized": 1}
    assert original in state["replacement_intents"]
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "REFUSED"
    )
    assert ledger(project)["usage"] == state["usage"]


def test_provenance_and_all_useful_role_assets() -> None:
    import hashlib

    provenance = json.loads((LAD / "provenance.json").read_bytes())
    assert len(provenance["inputs"]) == 44
    assert provenance["commit"] == "58e87f7d8941de144e29cdd619b41cff2a78b770"
    assert not (LAD / "AGENTS.md").exists()
    license_input = next(
        row for row in provenance["inputs"] if row["original_path"] == "LICENSE"
    )
    assert (
        hashlib.sha256(
            (LAD / "licenses/lad-factory-Apache-2.0.txt").read_bytes()
        ).hexdigest()
        == license_input["sha256"]
    )
    for row in provenance["inputs"]:
        assert len(row["sha256"]) == 64 and row["disposition"]
        assert all((LAD / name).is_file() for name in row["destinations"])
    prompts = list((LAD / "prompts").glob("*.md"))
    assert len(prompts) == 12
    for prompt in prompts:
        text = prompt.read_text()
        assert "Modified from" in text and "## Work" in text
        for heading in (
            "Role:",
            "Scope:",
            "Inputs from dispatch:",
            "Readable assets:",
            "Writable artifacts:",
            "Required evidence:",
            "Legal terminal markers rendered by runtime:",
            "Forbidden claims:",
            "How to return evidence:",
            "When to stop:",
        ):
            assert heading in text


def seam_envelope(project: Path, plane: str, result: dict, sequence: int = 1) -> Path:
    producer = "foreground" if plane == "learning" else "learning"
    name = "request" if plane == "learning" else "ack"
    envelope = {
        "format": "millrace-seam-input/v1",
        "run_id": "a" * 32,
        "consumer_plane_id": plane,
        "consumer_node_id": "receive_request"
        if plane == "learning"
        else "accept_learning_ack",
        "seam_id": "learning_" + name,
        "sequence": sequence,
        "producer_plane_id": producer,
        "producer_node_id": "emit_learning_request"
        if plane == "learning"
        else "emit_ack",
        "producer_invocation_id": "b" * 32,
        "producing_revision": "c" * 64,
        "completion_id": "b" * 32,
        "producer_workspace": str(project / producer),
        "result": result,
    }
    directory = project / ("test-seam-" + name)
    directory.mkdir(exist_ok=True)
    file = directory / "seam-input.json"
    file.write_text(json.dumps(envelope))
    return file


def learning_ready(project: Path) -> Path:
    before_role(project, "learning")
    request = command(project, "learning_seam.py", "emit_request")
    assert request["outcome"] == "REQUEST", request
    seam = seam_envelope(project, "learning", request)
    assert (
        command_at(project, "learning", "learning_seam.py", "receive", seam=seam)[
            "outcome"
        ]
        == "ASSESS"
    )
    assert (
        command_at(project, "learning", "learning_seam.py", "prepare")["outcome"]
        == "READY"
    )
    return seam


@pytest.mark.parametrize(
    "outcome",
    ["PASS", "FINDINGS", "ERROR_TRANSIENT_RESOLVED", "ERROR", "HUMAN_BLOCKED"],
)
def test_learning_semantic_routes_preserve_pending(
    tmp_path: Path, outcome: str
) -> None:
    project = copied(tmp_path)
    learning_ready(project)
    pending = ledger(project)["pending"]
    command_at(project, "learning", "lad_peer.py", "learning", "clean", outcome)
    accepted = command_at(project, "learning", "learning_seam.py", "accept", outcome)
    assert accepted["outcome"] == (
        "REPLACE_LEARNING" if outcome == "ERROR_TRANSIENT_RESOLVED" else "ACK"
    ), accepted
    assert ledger(project)["pending"] == pending
    if outcome == "ERROR_TRANSIENT_RESOLVED":
        assert (
            command_at(project, "learning", "learning_seam.py", "replace")["outcome"]
            == "READY"
        )
        command_at(project, "learning", "lad_peer.py", "learning", "clean", "ERROR")
        assert (
            command_at(project, "learning", "learning_seam.py", "accept", "ERROR")[
                "outcome"
            ]
            == "ACK"
        )
        assert ledger(project)["usage"]["learning:answer"] == 2
        assert ledger(project)["usage"]["replacement:learning:answer"] == 1
    acknowledgement = command_at(project, "learning", "learning_seam.py", "emit_ack")
    assert acknowledgement["outcome"] == "ACK", acknowledgement
    seam = seam_envelope(project, "foreground", acknowledgement)
    assert (
        command_at(project, "foreground", "learning_seam.py", "accept_ack", seam=seam)[
            "outcome"
        ]
        == "UPDATER"
    )
    assert ledger(project)["request"] is None
    state = (project / "foreground/lad-state/session.json").read_bytes()
    assert (
        command_at(project, "foreground", "learning_seam.py", "accept_ack", seam=seam)[
            "outcome"
        ]
        == "UPDATER"
    )
    assert (project / "foreground/lad-state/session.json").read_bytes() == state


@pytest.mark.parametrize(
    "mutation",
    [
        "request_id",
        "lineage",
        "source_sha256",
        "guidance_sha256",
        "pending_route",
        "sequence",
        "run_id",
        "producer_workspace",
    ],
)
def test_stale_wrong_or_conflicting_ack_cannot_advance(
    tmp_path: Path, mutation: str
) -> None:
    import hashlib

    project = copied(tmp_path)
    learning_ready(project)
    command_at(project, "learning", "lad_peer.py", "learning", "clean")
    command_at(project, "learning", "learning_seam.py", "accept", "PASS")
    result = command_at(project, "learning", "learning_seam.py", "emit_ack")
    seam = seam_envelope(project, "foreground", result)
    envelope = json.loads(seam.read_bytes())
    if mutation in {"sequence", "run_id", "producer_workspace"}:
        envelope[mutation] = 2 if mutation == "sequence" else "wrong"
    else:
        file = project / "learning" / result["refs"][0]
        ack = json.loads(file.read_bytes())
        ack[mutation] = "wrong"
        raw = json.dumps(ack).encode()
        file.write_bytes(raw)
        envelope["result"]["summary"] = "sha256=" + hashlib.sha256(raw).hexdigest()
    seam.write_text(json.dumps(envelope))
    before = (project / "foreground/lad-state/session.json").read_bytes()
    assert (
        command_at(project, "foreground", "learning_seam.py", "accept_ack", seam=seam)[
            "outcome"
        ]
        == "REFUSED"
    )
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    assert ledger(project)["pending"]["target"] == "updater"


def test_learning_interval_refuses_foreground_ledger_writes(tmp_path: Path) -> None:
    project = copied(tmp_path)
    learning_ready(project)
    before = (project / "foreground/lad-state/session.json").read_bytes()
    for script, args in [
        ("prepare_role.py", ["updater"]),
        ("boundaries.py", ["terminal_failure"]),
        ("accept_role.py", ["doublechecker", "PASS"]),
    ]:
        assert command(project, script, *args)["outcome"] == "REFUSED"
        assert (project / "foreground/lad-state/session.json").read_bytes() == before


@pytest.mark.parametrize("hard_limit", [False, True])
def test_small_gate_slice_original_run_reload_and_close(
    tmp_path: Path, hard_limit: bool
) -> None:
    project = copied(tmp_path)
    workflow = fake_graph(project, "gate")
    graph = json.loads(workflow.read_bytes())
    if hard_limit:
        graph["limits"]["max_invocations"] = 2
    foreground = graph["planes"]["foreground"]
    foreground["start"] = "emit_learning_request"
    foreground["nodes"] = {
        name: value
        for name, value in foreground["nodes"].items()
        if name
        in {
            "emit_learning_request",
            "accept_learning_ack",
            "terminal_failure",
            "finalize",
        }
    }
    for outcome in ("UPDATER", "TROUBLESHOOTER"):
        foreground["nodes"]["accept_learning_ack"]["on"][outcome] = {
            "goto": "terminal_failure"
        }
    # Test-only explicit pause after accepted assessment, before ACK. No graph
    # edits on resume: the exact original Core run retains the selected request.
    graph["planes"]["learning"]["nodes"]["accept_learning_pass"]["on"]["ACK"] = {
        "pause": {"reason": "inspect_gate", "resume": "emit_ack"}
    }
    workflow.write_text(json.dumps(graph))
    before_role(project, "learning")
    state = ledger(project)
    (project / "foreground/lad-state/operator-intent.json").write_text(
        json.dumps(
            {
                "format": "millrace-lad-operator-intent/v1",
                "session_id": state["session_id"],
                "decision": "terminal_failure",
                "reason": "This finite gate slice intentionally closes "
                "after assessment.",
            }
        )
    )
    home = tmp_path / "state"
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    started_run = control.start_run(
        instance,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    run_id = started_run["run_id"]
    paused = control.inspect_run(instance, run_id, state_home=home)
    assert paused["status"] == "paused", paused
    if hard_limit:
        assert paused["count"] == 2
        assert ledger(project)["continuation"]["cleanup_required"]
        assert ledger(project)["request"] is not None
        assert not any(row["node"] == "learning" for row in paused["invocations"])
        cancelled = control.cancel_plane(
            instance,
            run_id,
            plane_id="learning",
            state_home=home,
            request_id=uuid.uuid4().hex,
        )
        assert cancelled["status"] == "applied", cancelled
        control.cancel_run(
            instance, run_id, state_home=home, request_id=uuid.uuid4().hex
        )
        closed = control.inspect_run(instance, run_id, state_home=home)
        assert all(
            plane["active_invocation"] is None for plane in closed["planes"].values()
        )
        assert ledger(project)["continuation"]["cleanup_required"]
        return
    assert ledger(project)["request"]["core"]["run_id"] == run_id
    assert ledger(project)["request"]["stage"] == "received"
    assert paused["planes"]["foreground"]["cursor"] == "accept_learning_ack"
    assert not any(
        row["plane_id"] == "foreground" and row["node"] == "accept_learning_ack"
        for row in paused["invocations"]
    )
    assert len(paused["seam_occurrences"]) == 1
    before_usage = ledger(project)["usage"]
    # Repeated public inspect reloads; no new run, identity or role debit.
    assert control.inspect_run(instance, run_id, state_home=home) == paused
    resumed = control.resume_plane(
        instance,
        run_id,
        plane_id="learning",
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=paused["state_version"],
    )
    assert resumed["status"] == "applied", resumed
    after = control.inspect_run(instance, run_id, state_home=home)
    assert after["status"] == "failed", after
    assert {row["status"] for row in after["planes"].values()} == {
        "failed",
        "succeeded",
    }
    assert len(after["seam_occurrences"]) == len(after["seam_acknowledgements"]) == 4
    assert ledger(project)["usage"] == before_usage
    assert not ledger(project)["continuation"]["cleanup_required"]


@pytest.mark.parametrize(
    "key",
    [
        "all",
        "recon",
        "planner",
        "manager",
        "builder:original",
        "checker:original",
        "doublechecker:original",
        "updater:original",
        "fixer:original",
        "consultant",
        "troubleshooter:original",
        "learning:original",
        "arbiter",
        "planning_repair",
        "local_repair:original",
        "remediation",
        "replacement:checker:original",
    ],
)
def test_every_finite_budget_refuses_before_reserving(key: str) -> None:
    import runpy

    api = runpy.run_path(str(LAD / "scripts/lad_state.py"))
    state = api["new_state"]("local", "local")
    ceiling = api["CAPS"][key.split(":", 1)[0]]
    for _ in range(ceiling):
        api["debit"](state, [key], str(uuid.uuid4()), "test_fixture")
    before = json.dumps(state, sort_keys=True)
    with pytest.raises(api["LadError"], match="budget_exhausted"):
        api["debit"](state, [key], str(uuid.uuid4()), "test_fixture")
    assert json.dumps(state, sort_keys=True) == before


def test_planning_repair_and_remediation_never_replenish(tmp_path: Path) -> None:
    project = copied(tmp_path)
    before_role(project, "consultant")
    assert step(project, "consultant", "REPAIR_SPEC") == "PLANNER"
    assert ledger(project)["usage"]["planning_repair"] == 1
    assert step(project, "planner", "FINDINGS") == "CONSULTANT"
    command(project, "prepare_role.py", "consultant")
    command(project, "lad_peer.py", "consultant", "clean", "REPAIR_SPEC")
    assert (
        command(project, "accept_role.py", "consultant", "REPAIR_SPEC")["outcome"]
        == "REFUSED"
    )
    assert ledger(project)["usage"]["planning_repair"] == 1
    assert ledger(project)["usage"]["consultant"] == 2
    other = copied(tmp_path / "remediation")
    before_role(other, "arbiter")
    assert step(other, "arbiter", "FINDINGS") == "MANAGER"
    spent = ledger(other)["usage"].copy()
    assert spent["remediation"] == 1
    assert step(other, "manager") == "BUILDER"
    assert ledger(other)["plan"][0]["lineage"] == "answer"
    for role in ("builder", "checker", "updater"):
        step(other, role)
    command(other, "prepare_role.py", "arbiter")
    command(other, "lad_peer.py", "arbiter", "clean", "FINDINGS")
    assert (
        command(other, "accept_role.py", "arbiter", "FINDINGS")["outcome"] == "REFUSED"
    )
    assert ledger(other)["usage"]["remediation"] == 1
    assert ledger(other)["usage"]["builder:answer"] == spent["builder:answer"] + 1


def test_prerequisite_repair_uses_saved_role_replacement_debit(tmp_path: Path) -> None:
    project = copied(tmp_path)
    before_role(project, "troubleshooter")
    old = ledger(project)["pending"]["occurrence_id"]
    assert (
        step(project, "troubleshooter", "PASS_PREREQUISITE_REPAIRED")
        == "RESUME_BUILDER"
    )
    assert (
        command(project, "prepare_role.py", "builder", "--replacement")["outcome"]
        == "READY"
    )
    state = ledger(project)
    oid = state["active"]["foreground"]
    assert state["occurrences"][oid]["replacement_of"] == old
    assert state["usage"]["replacement:builder:answer"] == 1
    assert state["usage"]["local_repair:answer"] == 1
    assert state["usage"]["builder:answer"] == 2


def test_updater_changed_source_requires_review_before_acceptance(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    before_role(project, "updater")
    command(project, "prepare_role.py", "updater")
    (project / "foreground/target/answer.py").write_text(
        "def answer():\n    return 42  # updated docs example\n"
    )
    command(project, "lad_peer.py", "updater", "clean", "PASS")
    assert command(project, "accept_role.py", "updater", "PASS")["outcome"] == "REFUSED"
    command(
        project, "lad_peer.py", "updater", "clean", "REVIEW_INVALIDATED_SAME_BASELINE"
    )
    assert (
        command(
            project, "accept_role.py", "updater", "REVIEW_INVALIDATED_SAME_BASELINE"
        )["outcome"]
        == "DOUBLECHECKER"
    )
    assert step(project, "doublechecker") == "UPDATER"
    assert ledger(project)["pairs"] == {}
    assert step(project, "updater") == "ARBITER"


def test_scope_escape_and_baseline_mutation_refuse(tmp_path: Path) -> None:
    project = copied(tmp_path)
    before_role(project, "fixer")
    command(project, "prepare_role.py", "fixer")
    command(project, "lad_peer.py", "fixer", "repair")
    state = ledger(project)
    oid = state["active"]["foreground"]
    file = project / "foreground/lad-results" / oid / "result.json"
    value = json.loads(file.read_bytes())
    value["data"]["baseline"]["sha256"] = "f" * 64
    file.write_text(json.dumps(value))
    assert command(project, "accept_role.py", "fixer", "PASS")["outcome"] == "REFUSED"
    command(project, "lad_peer.py", "fixer", "repair")
    (project / "foreground/target/unowned.py").write_text("unauthorized = True\n")
    command(project, "lad_peer.py", "fixer", "repair")
    assert command(project, "accept_role.py", "fixer", "PASS")["outcome"] == "REFUSED"
    assert ledger(project)["usage"]["fixer:answer"] == 1


def test_interrupted_owned_bootstrap_staging_and_pristine_greenfield(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    staging = project / "foreground/.lad-init-session"
    staging.mkdir()
    (staging / "owner.json").write_text(
        json.dumps(
            {
                "format": "millrace-lad-initializer/v1",
                "identity": "local",
                "inputs": "local",
            }
        )
    )
    (staging / ".lad-write-interrupted").write_bytes(b"partial uncommitted state")
    (project / "request.md").write_text("answer() returns 42")
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "NEW_SESSION"
    )
    assert not staging.exists()
    assert (
        command(project, "setup_intake.py", "--project-root", "..", "--greenfield")[
            "outcome"
        ]
        == "INTAKE_READY_GREENFIELD"
    )
    assert command(project, "prepare_role.py", "recon")["outcome"] == "REFUSED"
    assert command(project, "prepare_role.py", "planner")["outcome"] == "READY"


def test_operator_intent_records_exact_continuation_without_dispatch(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    state = ledger(project)
    oid = state["active"]["foreground"]
    note = project / "foreground/lad-recovery/closed.md"
    note.parent.mkdir()
    note.write_text(
        f"Test-owned public writer inspection note.\nSession: {state['session_id']}\n"
        f"Occurrence: {oid}\nObserved no live writer; "
        "the fixture never invoked a native executable.\n"
    )
    assert (
        command(
            project,
            "boundaries.py",
            "record_replacement",
            "--role",
            "recon",
            "--occurrence",
            oid,
            "--evidence",
            "lad-recovery/closed.md",
        )["outcome"]
        == "INTENT_RECORDED"
    )
    assert ledger(project)["usage"] == state["usage"]
    assert ledger(project)["continuation"]["node"] == "prepare_recon_replacement"
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "READY"
    )
    assert ledger(project)["usage"]["replacement:recon:unpacketized"] == 1


def test_catalog_copy_and_stdlib_only_scripts(tmp_path: Path) -> None:
    import ast

    destination = tmp_path / "initialized"
    catalog.init_template("bundled:lad", destination, catalog_home=tmp_path / "catalog")
    assert (destination / "workflow.json").read_bytes() == (
        LAD / "workflow.json"
    ).read_bytes()
    for script in (destination / "scripts").glob("*.py"):
        tree = ast.parse(script.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name.split(".")[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            assert set(names) <= sys.stdlib_module_names | {
                "lad_state",
                "boundaries",
                "prepare_role",
                "accept_role",
            }
    # Ordinary isolated Python mode proves the copied folder's scripts import
    # their own modules without Plus or a service on sys.path.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-S",
            str(destination / "scripts/init_session.py"),
            "--project-root",
            "..",
        ],
        cwd=destination / "foreground",
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout)["outcome"] == "NEW_SESSION"


def test_interrupted_preparation_reuses_only_its_committed_reservation(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    # An absent active projection after a committed reservation is recoverable;
    # an unsafe conflicting projection is never overwritten or treated as fresh.
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    projection = project / "foreground/lad-state/active-role.json"
    projection.symlink_to(outside)
    assert command(project, "prepare_role.py", "recon")["outcome"] == "REFUSED"
    state = ledger(project)
    oid = state["active"]["foreground"]
    assert state["usage"] == {"all": 1, "recon": 1}
    assert outside.read_text() == "{}"
    projection.unlink()
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    assert ledger(project)["active"]["foreground"] == oid
    assert ledger(project)["usage"] == state["usage"]
    assert command(project, "prepare_role.py", "recon")["outcome"] == "REFUSED"


@pytest.mark.parametrize("role", ["planner", "learning"])
def test_role_resolves_durable_guidance_and_refs_from_cwd_only(
    tmp_path: Path, role: str
) -> None:
    project = copied(tmp_path)
    if role == "learning":
        learning_ready(project)
        workspace = project / "learning"
    else:
        before_role(project, "planner")
        assert command(project, "prepare_role.py", "planner")["outcome"] == "READY"
        workspace = project / "foreground"
    # A later invocation cannot depend on the preparation's controls directory.
    # Remove original instruction paths; the child receives only its cwd/input.
    for folder in ("context", "templates", "prompts"):
        (project / folder).rename(project / ("retired-" + folder))
    code = """import hashlib,json
from pathlib import Path
def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),
                      ensure_ascii=False).encode()
def digest(raw):
    return hashlib.sha256(raw).hexdigest()
cwd=Path.cwd()
item=json.loads(Path("lad-state/active-role.json").read_bytes())
oid=item["occurrence_id"]
assert item==json.loads(Path("lad-state/occurrences",oid,"input.json").read_bytes())
assert Path(item["workspace_root"])==cwd
root=Path(item["guidance_root"])
assert root==cwd/"lad-state"/"occurrences"/oid/"guidance"
manifest=json.loads((root/"manifest.json").read_bytes())
assert manifest["guidance_sha256"]==item["guidance_sha256"]
assert manifest["files"]==item["guidance_files"]
assert digest(canonical(manifest))==item["guidance_snapshot_sha256"]
for name,checksum in manifest["files"].items():
    assert digest((root/name).read_bytes())==checksum
assert len(manifest["files"])==11
assert "Inputs from dispatch:" in (root/("prompts/"+item["role"]+".md")).read_text()
assert "LAD role artifacts" in (root/"context/role-contracts.md").read_text()
assert item["accepted_input_refs"]
for accepted_id,ref in item["accepted_input_refs"].items():
    file=Path(item["foreground_root"]).parent/ref["workspace"]/ref["path"]
    result=json.loads(file.read_bytes())
    assert result["occurrence_id"]==accepted_id
    assert result["role"]==ref["role"]
    assert digest(canonical(result))==ref["sha256"]
    assert ref["sha256"]==item["accepted_inputs"][accepted_id]
print("durable guidance and accepted refs verified from cwd/input only")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-S", "-c", code],
        cwd=workspace,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        check=True,
    )
    assert b"cwd/input only" in result.stdout


@pytest.mark.parametrize("mutation", ["file", "manifest"])
def test_corrupt_durable_guidance_refuses_without_new_debit(
    tmp_path: Path, mutation: str
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    command(project, "lad_peer.py", "recon", "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    root = Path(state["occurrences"][oid]["guidance_root"])
    if mutation == "file":
        file = root / "context/contracts.md"
        file.write_text(file.read_text() + "Conflicting guidance change.\n")
    else:
        file = root / "manifest.json"
        manifest = json.loads(file.read_bytes())
        manifest["guidance_sha256"] = "0" * 64
        file.write_text(json.dumps(manifest))
    before = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "REFUSED"
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "RECONCILE"
    )
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    assert ledger(project)["usage"] == {"all": 1, "recon": 1}


def test_native_error_follows_replacement_preparation(tmp_path: Path) -> None:
    project = copied(tmp_path)
    workflow = fake_graph(project, "technical_once")
    graph = json.loads(workflow.read_bytes())
    plane = graph["planes"]["foreground"]
    names = {
        "init_session",
        "setup_intake",
        "prepare_recon",
        "prepare_recon_replacement",
        "recon",
    }
    names.update(name for name in plane["nodes"] if name.startswith("accept_recon_"))
    plane["nodes"] = {
        name: node for name, node in plane["nodes"].items() if name in names
    }
    plane["nodes"]["setup_intake"]["on"]["INTAKE_READY_GREENFIELD"] = {
        "goto": "prepare_recon"
    }
    for name, node in plane["nodes"].items():
        if name.startswith("accept_recon_"):
            for outcome in node["outcomes"]:
                if outcome != "REFUSED":
                    node["on"][outcome] = {"finish": "succeeded"}
    graph["planes"] = {"foreground": plane}
    graph["seams"] = {}
    graph["limits"]["max_parallel_invocations"] = 1
    workflow.write_text(json.dumps(graph))
    (project / "request.md").write_text("answer() returns 42")
    home = tmp_path / "state"
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    started_run = control.start_run(
        instance,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    view = control.inspect_run(instance, started_run["run_id"], state_home=home)
    assert view["status"] == "succeeded", view
    nodes = [row["node"] for row in view["invocations"]]
    assert nodes == [
        "init_session",
        "setup_intake",
        "prepare_recon",
        "recon",
        "prepare_recon_replacement",
        "recon",
        "accept_recon_pass",
    ]
    assert ledger(project)["usage"] == {
        "all": 2,
        "recon": 2,
        "replacement:recon:unpacketized": 1,
    }
    assert len(ledger(project)["accepted"]) == 1


def test_missing_or_corrupt_session_never_resets_work(tmp_path: Path) -> None:
    project = copied(tmp_path)
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "NEW_SESSION"
    )
    session = project / "foreground/lad-state/session.json"
    before = session.read_bytes()
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "EXISTING_SESSION_PRISTINE"
    )
    assert session.read_bytes() == before
    session.unlink()
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "RECONCILE"
    )
    assert not session.exists()


def test_bootstrap_and_exact_intake_bytes(tmp_path: Path) -> None:
    project = copied(tmp_path)
    raw = b"  Original request.\n\nKeep exact bytes.  \n"
    (project / "request.md").write_bytes(raw)
    command(project, "init_session.py", "--project-root", "..")
    assert (
        command(project, "setup_intake.py", "--project-root", "..")["outcome"]
        == "INTAKE_READY_EXISTING"
    )
    assert (project / "foreground/request.md").read_bytes() == raw
    (project / "request.md").write_bytes(b"changed")
    assert (
        command(project, "setup_intake.py", "--project-root", "..")["outcome"]
        == "INTAKE_CONFLICT"
    )
    assert (project / "foreground/request.md").read_bytes() == raw


def test_graph_uses_bound609_and_disjoint_planes() -> None:
    graph = json.loads((LAD / "workflow.json").read_bytes())
    assert graph["limits"] == {
        "max_invocations": 2 + 180 + 120 + 300 + 1 + 6,
        "max_elapsed_seconds": 35900,
        "max_parallel_invocations": 2,
    }
    assert graph["planes"]["foreground"]["start"] == "init_session"
    assert graph["planes"]["learning"]["start"] == "receive_request"
    used = graph["planes"]["foreground"]["nodes"]["init_session"]["on"][
        "EXISTING_SESSION_WITH_WORK"
    ]
    assert "resume" not in used["pause"]
    assert len({p["workspace"] for p in graph["planes"].values()}) == 2


def fake_graph(project: Path, scenario: str = "clean") -> Path:
    peer = project / "scripts/lad_peer.py"
    shutil.copyfile(Path(__file__).parent / "fixtures/lad_peer.py", peer)
    workflow = project / "workflow.json"
    graph = json.loads(workflow.read_bytes())
    graph["control_files"].append("scripts/lad_peer.py")
    graph["bindings"].pop("claude")
    graph["bindings"]["command"]["executable"] = sys.executable
    for plane in graph["planes"].values():
        for name, node in plane["nodes"].items():
            if node["invoke"].get("binding") == "claude":
                node["invoke"].update(
                    binding="command",
                    config={"script": "scripts/lad_peer.py", "args": [name, scenario]},
                )
    workflow.write_text(json.dumps(graph))
    return workflow


def started(project: Path, home: Path, scenario: str = "clean") -> tuple[str, dict]:
    workflow = fake_graph(project, scenario)
    made = control.create_instance(
        workflow,
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    receipt = control.start_run(
        instance,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    assert receipt["status"] == "applied", receipt
    return instance, control.inspect_run(instance, receipt["run_id"], state_home=home)


@pytest.mark.parametrize("scenario", ["clean", "repair", "gate", "unresolved"])
def test_real_graph_useful_roles_gate_and_close(tmp_path: Path, scenario: str) -> None:
    project = copied(tmp_path)
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    instance, view = started(project, tmp_path / "state", scenario)
    assert view["status"] == "succeeded", view
    assert {p["status"] for p in view["planes"].values()} == {"succeeded"}
    state = json.loads((project / "foreground/lad-state/session.json").read_bytes())
    assert (
        state["terminal"] == "succeeded"
        and not state["continuation"]["cleanup_required"]
    )
    assert state["request"] is None
    assert state["criteria"][0]["request_quote"] == "answer() returns 42"
    namespace = {}
    exec((project / "foreground/target/answer.py").read_text(), namespace)
    assert namespace["answer"]() == 42
    nodes = [(row["plane_id"], row["node"]) for row in view["invocations"]]
    if scenario in {"gate", "unresolved"}:
        assert len(state["pairs"]["answer"]) == 2
        assert state["usage"]["learning:answer"] == 1
        request = nodes.index(("foreground", "emit_learning_request"))
        ack = nodes.index(("learning", "emit_ack"))
        assert all(plane == "learning" for plane, _ in nodes[request + 1 : ack + 1])
        assert state["learning"]["answer"]["disposition"] == "passed"
    else:
        assert not state["learning"]
    assert len(view["seam_occurrences"]) == (
        4 if scenario in {"gate", "unresolved"} else 2
    )
    assert len(view["seam_acknowledgements"]) == len(view["seam_occurrences"])
    # A second run on the used copy pauses without replenishing any debit.
    before = (project / "foreground/lad-state/session.json").read_bytes()
    receipt = control.start_run(
        instance,
        state_home=tmp_path / "state",
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(
            instance, state_home=tmp_path / "state"
        )["state_version"],
    )
    second = control.inspect_run(
        instance, receipt["run_id"], state_home=tmp_path / "state"
    )
    assert second["status"] == "paused" and [
        row["node"] for row in second["invocations"]
    ] == ["init_session"]
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    cancelled = control.cancel_run(
        instance,
        receipt["run_id"],
        state_home=tmp_path / "state",
        request_id=uuid.uuid4().hex,
    )
    assert cancelled["status"] == "applied", cancelled
    closed = control.inspect_run(
        instance, receipt["run_id"], state_home=tmp_path / "state"
    )
    assert all(
        plane["active_invocation"] is None for plane in closed["planes"].values()
    )
