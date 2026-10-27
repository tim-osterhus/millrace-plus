"""LAD domain accounting and real graph/seam execution with inert peers."""

from __future__ import annotations

import hashlib
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


def role_occurrences(state: dict, role: str) -> list[dict]:
    return [state["occurrences"][event["id"]] for event in state["events"]
            if event["kind"] == "role"
            and state["occurrences"][event["id"]]["role"] == role]


def bootstrap(project: Path, *, request: bytes | None = None) -> None:
    shutil.copyfile(
        Path(__file__).parent / "fixtures/lad_peer.py", project / "scripts/lad_peer.py"
    )
    (project / "request.md").write_bytes(
        request if request is not None else b"Original request: answer() returns 42.\n"
    )
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
    assert parsed.format == "millrace-router/v4"
    assert parsed.max_invocations is None and parsed.max_elapsed is None
    assert parsed.max_parallel == 2
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
        if reason == "finding_identity":
            assert refused["outcome"] == "INVALID_RESULT"
            assert ledger(project)["replacement_intents"][oid]["diagnostic"] == reason
        else:
            assert refused["outcome"] == "REFUSED"
            assert reason in refused["summary"]
        assert ledger(project)["events"] == state["events"]
        assert ledger(project)["criteria"] == state["criteria"]
        assert oid not in ledger(project)["accepted"]

    file.write_text(json.dumps(blocked))
    accepted = command(project, "accept_role.py", "arbiter", "HUMAN_BLOCKED")
    assert accepted["outcome"] == "PAUSE"
    held = ledger(project)
    assert held["criteria"] == state["criteria"]
    assert held["events"] == state["events"]
    assert len(held["occurrences"]) == 7 and len(role_occurrences(held, "arbiter")) == 1
    assert all(row["kind"] != "remediation" for row in held["events"])
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
                "millrace-lad-session/v2/" + hashlib.sha256(encoded).hexdigest(),
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
        "legacy",
        "events",
        "active",
        "occurrences",
        "accepted",
        "migration",
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
def test_invalid_role_result_records_recovery_without_new_dispatch(
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
    before_events = state["events"]
    rejected = file.read_bytes()
    assert command(
        project, "accept_role.py", "recon", "PASS"
    )["outcome"] == "INVALID_RESULT"
    assert ledger(project)["events"] == before_events
    assert file.read_bytes() == rejected
    assert oid not in ledger(project)["accepted"]
    assert (ledger(project)["replacement_intents"][oid]["reason"]
            == "invalid_semantic_result")
    assert len(role_occurrences(ledger(project), "recon")) == 1


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


def test_native_replacement_records_fresh_owner_and_normal_redirect_refuses(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    original = ledger(project)["active"]["foreground"]
    before = ledger(project)["events"]
    assert command(project, "prepare_role.py", "recon")["outcome"] == "REFUSED"
    assert ledger(project)["events"] == before
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "READY"
    )
    state = ledger(project)
    replacement = state["active"]["foreground"]
    assert replacement != original
    assert state["occurrences"][replacement]["replacement_of"] == original
    assert len(role_occurrences(state, "recon")) == 2
    assert original in state["replacement_intents"]
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "TECHNICAL_RECOVERY"
    )
    assert ledger(project)["events"] == state["events"]
    assert ledger(project)["pending"]["occurrence_id"] == replacement
    assert ledger(project)["active"]["foreground"] == replacement
    assert ledger(project)["continuation"]["node"] == "prepare_troubleshooter"


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
    corrected = provenance["corrected_reference"]
    assert corrected["commit"] == "a7e5a19a319786fb3138fb938764c5d77607b7b0"
    assert {row["original_path"] for row in corrected["inputs"]} == {
        "context/contracts.md", "context/transitions.md", "context/routing.md",
        "context/nested-models.md", "prompts/planner.md", "prompts/checker.md",
        "adapter-templates/claude-code/README.md",
    }
    for row in corrected["inputs"]:
        assert len(row["sha256"]) == 64 and row["bytes"] > 0
        assert corrected["commit"] in row["source_url"]
        assert row["disposition"]
        assert all((LAD / name).is_file() for name in row["destinations"])
    destinations = provenance["adaptation"]["destination_hashes"]
    assert len({row["path"] for row in destinations}) == len(destinations)
    assert "scripts/session_compat.py" in {row["path"] for row in destinations}
    for row in destinations:
        raw = (LAD / row["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
        assert len(raw) == row["bytes"]
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
        assert len(role_occurrences(ledger(project), "learning")) == 2
        assert sum(row["replacement_of"] is not None
                   for row in role_occurrences(ledger(project), "learning")) == 1
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
    for outcome in ("CONSULTANT", "BASELINE_TAMPER"):
        foreground["nodes"]["accept_learning_ack"]["on"][outcome] = {
            "pause": {"reason": "fixture_unselected_recovery"}
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
    before_history = ledger(project)["events"]
    # Repeated public inspect reloads; no new run, identity or role occurrence.
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
    assert ledger(project)["events"] == before_history
    assert not ledger(project)["continuation"]["cleanup_required"]




def test_planning_repairs_require_new_steps_not_a_capacity_allowance(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    before_role(project, "consultant")
    assert step(project, "consultant", "REPAIR_SPEC") == "PLANNER"
    assert step(project, "planner", "FINDINGS") == "CONSULTANT"
    assert step(project, "consultant", "REPAIR_SPEC") == "PLANNER"
    assert sum(
        row["kind"] == "planning_repair" for row in ledger(project)["events"]
    ) == 2
    assert step(project, "planner", "FINDINGS") == "CONSULTANT"
    assert command(project, "prepare_role.py", "consultant")["outcome"] == "READY"
    command(project, "lad_peer.py", "consultant", "clean", "REPAIR_SPEC")
    before = (project / "foreground/lad-state/session.json").read_bytes()
    refused = command(project, "accept_role.py", "consultant", "REPAIR_SPEC")
    assert refused["outcome"] == "REFUSED"
    assert "unchanged_consultant_step" in refused["summary"]
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    state = ledger(project)
    assert len(role_occurrences(state, "consultant")) == 3  # Beyond old session cap2.
    oid = state["active"]["foreground"]
    result_file = project / "foreground/lad-results" / oid / "result.json"
    result = json.loads(result_file.read_bytes())
    result["data"]["next_step"] = (
        "Inspect the cited input gap and add its missing prerequisite proof."
    )
    evidence = result_file.parent / "diagnosis.md"
    evidence.write_text(
        evidence.read_text() + "\n" + result["data"]["next_step"] + "\n"
    )
    checksum = hashlib.sha256(evidence.read_bytes()).hexdigest()
    for ref in result["artifacts"]:
        if ref["path"] == "diagnosis.md":
            ref["sha256"] = checksum
    result["data"]["next_evidence"]["sha256"] = checksum
    result_file.write_text(json.dumps(result))
    assert command(
        project, "accept_role.py", "consultant", "REPAIR_SPEC"
    )["outcome"] == "PLANNER"
    assert sum(
        row["kind"] == "planning_repair" for row in ledger(project)["events"]
    ) == 3


def test_repeated_original_gap_needs_a_materially_new_review_basis(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    before_role(project, "arbiter")
    original = ledger(project)["criteria"]
    assert step(project, "arbiter", "FINDINGS") == "MANAGER"
    assert step(project, "manager") == "BUILDER"
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    command(project, "lad_peer.py", "builder", "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    result_file = project / "foreground/lad-results" / oid / "result.json"
    result = json.loads(result_file.read_bytes())
    old_source = result["source_sha256"]
    target = project / "foreground/target/answer.py"
    target.write_text("def answer():\n    return 6 * 7\n")
    files = {"answer.py": hashlib.sha256(target.read_bytes()).hexdigest()}
    result["source_sha256"] = hashlib.sha256(json.dumps(
        files, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    for artifact in result["artifacts"]:
        file = result_file.parent / artifact["path"]
        file.write_text(file.read_text().replace(old_source, result["source_sha256"]))
        artifact["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
    result_file.write_text(json.dumps(result))
    assert command(project, "accept_role.py", "builder", "PASS")["outcome"] == "CHECKER"
    assert step(project, "checker") == "UPDATER"
    assert step(project, "updater") == "ARBITER"
    assert step(project, "arbiter", "FINDINGS") == "MANAGER"
    assert sum(row["kind"] == "remediation" for row in ledger(project)["events"]) == 2
    assert ledger(project)["criteria"] == original
    assert step(project, "manager") == "BUILDER"
    for role in ("builder", "checker", "updater"):
        step(project, role)
    assert step(project, "arbiter", "FINDINGS") == "CONSULTANT"
    assert sum(row["kind"] == "remediation" for row in ledger(project)["events"]) == 2
    assert ledger(project)["criteria"] == original


def test_prerequisite_repair_preserves_saved_role_and_replacement_link(
    tmp_path: Path,
) -> None:
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
    assert state["occurrences"][oid]["replacement_intent"]["old_occurrence_id"] == old
    assert state["pending"]["occurrence_id"] == old
    assert len(role_occurrences(state, "builder")) == 2


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
    assert len(role_occurrences(ledger(project), "fixer")) == 1


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
    assert ledger(project)["events"] == state["events"]
    assert ledger(project)["continuation"]["node"] == "prepare_recon_replacement"
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "READY"
    )
    assert ledger(project)["occurrences"][ledger(project)["active"]["foreground"]][
        "replacement_of"
    ] == oid


def test_catalog_copy_and_stdlib_only_scripts(tmp_path: Path) -> None:
    import ast

    destination = tmp_path / "initialized"
    catalog.init_template("bundled:lad", destination, catalog_home=tmp_path / "catalog")
    assert (destination / "workflow.json").read_bytes() == (
        LAD / "workflow.json"
    ).read_bytes()
    for script in (destination / "scripts").glob("*.py"):
        tree = ast.parse(script.read_text())
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name.split(".")[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            if set(names) == {"millrace_plus"}:
                parent = parents.get(node)
                while parent is not None and not isinstance(parent, ast.FunctionDef):
                    parent = parents.get(parent)
                assert isinstance(node, ast.ImportFrom)
                if script.name == "session_compat.py":
                    assert parent is not None and parent.name == "upgrade_session"
                    assert node.module == "millrace_plus.router"
                else:
                    assert script.name == "lad_state.py"
                    assert parent is not None and parent.name == "shared_target"
                    assert len(node.names) == 1
                    assert node.names[0].asname is None
                    assert (
                        node.module,
                        node.names[0].name,
                    ) in {
                        ("millrace_plus.lad_service", "validate_shared_target"),
                        ("millrace_plus.local_intake", "IntakeError"),
                        ("millrace_plus.subworkflow_records", "SubworkflowError"),
                    }
            else:
                assert set(names) <= sys.stdlib_module_names | {
                    "lad_state", "boundaries", "prepare_role", "accept_role",
                    "session_compat",
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
    assert len(state["occurrences"]) == 1 and len(state["events"]) == 1
    assert outside.read_text() == "{}"
    projection.unlink()
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    assert ledger(project)["active"]["foreground"] == oid
    assert ledger(project)["events"] == state["events"]
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
assert "context/role-contracts.md" in manifest["files"]
assert "templates/result.md" in manifest["files"]
assert not any(name.startswith("prompts/") and name!="prompts/"+item["role"]+".md"
               for name in manifest["files"])
assert "Inputs from dispatch:" in (root/("prompts/"+item["role"]+".md")).read_text()
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
def test_corrupt_durable_guidance_refuses_without_new_occurrence(
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
    assert len(role_occurrences(ledger(project), "recon")) == 1


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
    for node in plane["nodes"].values():
        for outcome, action in node["on"].items():
            if "goto" in action and action["goto"] not in plane["nodes"]:
                node["on"][outcome] = {
                    "pause": {"reason": "fixture_unselected_recovery"}
                }
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
    assert len(role_occurrences(ledger(project), "recon")) == 2
    assert sum(row["replacement_of"] is not None
               for row in role_occurrences(ledger(project), "recon")) == 1
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


def test_graph_omits_overall_bounds_and_keeps_finite_stages() -> None:
    graph = json.loads((LAD / "workflow.json").read_bytes())
    assert graph["format"] == "millrace-router/v4"
    assert graph["limits"] == {"max_parallel_invocations": 2}
    for plane in graph["planes"].values():
        for node in plane["nodes"].values():
            assert node["invoke"]["timeout_seconds"] == (
                1800 if node["invoke"]["binding"] == "claude" else 10
            )
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
        assert len(role_occurrences(state, "learning")) == 1
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
    # A second run pauses without resetting history or creating new work.
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


def test_new_v2_session_has_history_without_capacity_authority(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    state = ledger(project)
    assert state["format"] == "millrace-lad-session/v2"
    assert "legacy" in state and state["legacy"] is None
    assert "migration" in state and state["migration"] is None
    assert "limits" not in state and "usage" not in state
    assert state["events"] == [] and state["occurrences"] == {}
    before = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "init_session.py", "--project-root", "..")["outcome"] == (
        "EXISTING_SESSION_PRISTINE"
    )
    assert (project / "foreground/lad-state/session.json").read_bytes() == before


def test_direct_occurrence_requires_no_helper_facility(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    state = ledger(project)
    oid = state["active"]["foreground"]
    item = state["occurrences"][oid]
    assert item["format"] == "millrace-lad-input/v2"
    assert item.get("helper_mode") == "direct"
    assert "helper_qualification" in item and item["helper_qualification"] is None
    assert {"id", "kind"} == set(state["events"][-1])
    assert state["events"][-1] == {"id": oid, "kind": "role"}
    # A second visible dispatch still needs reconciliation/new occurrence.
    before = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "prepare_role.py", "recon")["outcome"] == "REFUSED"
    assert (project / "foreground/lad-state/session.json").read_bytes() == before


def test_checker_prefreeze_context_excludes_builder_appendix(tmp_path: Path) -> None:
    project = copied(tmp_path)
    before_role(project, "builder")
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    command(project, "lad_peer.py", "builder", "clean")
    oid = ledger(project)["active"]["foreground"]
    output = project / "foreground/lad-results" / oid
    result_file = output / "result.json"
    result = json.loads(result_file.read_bytes())
    marker = "HOSTILE_BUILDER_DERIVED_TEST_DIFF_APPENDIX_42"
    # This is valid worker evidence before acceptance, not mutation of an
    # already accepted artifact that would merely exercise old immutability.
    for artifact in result["artifacts"]:
        if artifact["path"] == "implementation.md":
            file = output / artifact["path"]
            raw = file.read_bytes() + ("\n" + marker + "\n").encode()
            file.write_bytes(raw)
            artifact["sha256"] = hashlib.sha256(raw).hexdigest()
    result_file.write_text(json.dumps(result))
    assert command(project, "accept_role.py", "builder", "PASS")["outcome"] != "REFUSED"
    assert command(project, "prepare_role.py", "checker")["outcome"] == "READY"
    state = ledger(project)
    checker = state["occurrences"][state["active"]["foreground"]]
    assert oid not in checker["accepted_input_refs"]
    assert all(
        ref["role"] not in {"builder", "fixer", "doublechecker"}
        for ref in checker["accepted_input_refs"].values()
    )
    guidance = Path(checker["guidance_root"])
    for name in checker["guidance_files"]:
        assert marker not in (guidance / name).read_text()


@pytest.mark.parametrize("relative", ["large.bin", "dist/data.bin"])
def test_scoped_large_source_does_not_relax_artifact_io(
    tmp_path: Path, relative: str
) -> None:
    project = copied(tmp_path)
    before_role(project, "manager")
    assert command(project, "prepare_role.py", "manager")["outcome"] == "READY"
    command(project, "lad_peer.py", "manager", "clean")
    oid = ledger(project)["active"]["foreground"]
    result_file = project / "foreground/lad-results" / oid / "result.json"
    result = json.loads(result_file.read_bytes())
    result["data"]["plan"][0]["paths"].append(relative)
    for ref in result["artifacts"]:
        artifact = result_file.parent / ref["path"]
        artifact.write_text(artifact.read_text() + "\nScope addition: " + relative
                            + " is an explicitly owned relevant asset.\n")
        ref["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    result_file.write_text(json.dumps(result))
    assert command(project, "accept_role.py", "manager", "PASS")["outcome"] != "REFUSED"
    target = project / "foreground/target"
    target.mkdir(exist_ok=True)
    raw = b"x" * (4 * 1024 * 1024 + 1)
    (target / relative).parent.mkdir(parents=True, exist_ok=True)
    (target / relative).write_bytes(raw)
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    state = ledger(project)
    item = state["occurrences"][state["active"]["foreground"]]
    assert item["source_files"][relative] == hashlib.sha256(raw).hexdigest()
    program = """
import sys
from pathlib import Path
from lad_state import LadError,read
try:
    read(Path(sys.argv[1]),sys.argv[2])
except LadError as exc:
    assert str(exc)=='file_too_large'
else:
    raise AssertionError('scoped source relaxed strict artifact/control IO')
"""
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", program, str(project / "foreground"),
         "target/" + relative],
        cwd=project / "scripts",
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 0, child.stdout + child.stderr


def test_v2_reader_does_not_derive_shape_from_new_constructor(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    program = """
import sys
from pathlib import Path
import lad_state
def forbidden(*args):
    raise AssertionError('reader called mutable constructor')
lad_state.new_state=forbidden
assert lad_state.load(Path(sys.argv[1]))['format']=='millrace-lad-session/v2'
"""
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", program, str(project / "foreground")],
        cwd=project / "scripts",
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=10,
    )
    assert child.returncode == 0, child.stdout + child.stderr


def test_recorded_events_have_no_capacity_admission(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    program = """
import sys
from pathlib import Path
from lad_state import LadError,load,record_event,save
state=load(Path(sys.argv[1]))
for index in range(65):
    record_event(state,'repair-'+str(index),'planning_repair')
save(Path(sys.argv[1]),state)
assert len(load(Path(sys.argv[1]))['events'])==65
try:
    record_event(state,'repair-64','planning_repair')
except LadError as exc:
    assert str(exc)=='duplicate_history_event'
else:
    raise AssertionError('duplicate history accepted')
"""
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", program, str(project / "foreground")],
        cwd=project / "scripts",
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=10,
    )
    assert child.returncode == 0, child.stdout + child.stderr


def test_parent_baseline_pin_rejects_worker_digest_forgery(tmp_path: Path) -> None:
    project = copied(tmp_path)
    before_role(project, "checker")
    assert step(project, "checker") == "UPDATER"
    state = ledger(project)
    baseline = state["baselines"]["answer"]
    parent_pin = project / "foreground" / baseline["parent_pin"]["path"]
    retained = parent_pin.read_bytes()
    baseline_file = project / "foreground" / baseline["path"]
    baseline_file.write_bytes(baseline_file.read_bytes() + b"\nForged expectations.\n")
    oid = baseline["occurrence_id"]
    output = project / "foreground/lad-results" / oid
    result_file = output / "result.json"
    result = json.loads(result_file.read_bytes())
    forged = hashlib.sha256(baseline_file.read_bytes()).hexdigest()
    for artifact in result["artifacts"]:
        if artifact["path"] == "baseline.md":
            artifact["sha256"] = forged
    freeze_file = output / "baseline-freeze.json"
    freeze = json.loads(freeze_file.read_bytes())
    freeze["sha256"] = forged
    freeze_file.write_text(json.dumps(freeze))
    result["data"]["baseline_freeze"]["sha256"] = hashlib.sha256(
        freeze_file.read_bytes()
    ).hexdigest()
    for artifact in result["artifacts"]:
        if artifact["path"] == "baseline-freeze.json":
            artifact["sha256"] = result["data"]["baseline_freeze"]["sha256"]
    result_file.write_text(json.dumps(result))
    # Keep the other recorded worker claims coherent to isolate the independent
    # parent pin, rather than only repeating accepted-output tamper coverage.
    state["accepted"][oid]["result"] = result
    state["accepted"][oid]["sha256"] = hashlib.sha256(
        json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    state["baselines"]["answer"]["sha256"] = forged
    state.pop("integrity_sha256")
    state["integrity_sha256"] = hashlib.sha256(
        json.dumps(
            state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    (project / "foreground/lad-state/session.json").write_text(json.dumps(state))
    assert command(project, "prepare_role.py", "updater")["outcome"] == "REFUSED"
    assert parent_pin.read_bytes() == retained
    assert ledger(project)["packet_index"] == state["packet_index"]


@pytest.mark.parametrize("git", [False, True], ids=["nongit", "git"])
@pytest.mark.parametrize("change", ["dirty_again", "new", "expected_cache"])
def test_outside_packet_writes_are_distinct_from_check_products(
    tmp_path: Path, git: bool, change: str
) -> None:
    project = copied(tmp_path)
    target = project / "foreground/target"
    target.mkdir()
    outside = target / "outside.txt"
    outside.write_text("Original user file.\n")
    if git:
        for args in [
            ["init", "--quiet"],
            ["add", "--", "outside.txt"],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "commit", "--quiet", "-m", "fixture baseline"],
        ]:
            child = subprocess.run(
                ["git", "-C", str(target), *args],
                env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                     "GIT_CONFIG_GLOBAL": "/dev/null"},
                capture_output=True,
                timeout=10,
            )
            assert child.returncode == 0, child.stderr
    outside.write_text("User's preexisting dirty work.\n")
    before_role(project, "builder")
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    command(project, "lad_peer.py", "builder", "clean")
    before = (project / "foreground/lad-state/session.json").read_bytes()
    if change == "dirty_again":
        outside.write_text("Unauthorized overwrite of already dirty user work.\n")
    elif change == "new":
        (target / "unauthorized.txt").write_text("Not owned by the packet.\n")
    else:
        cache = target / "__pycache__"
        cache.mkdir()
        (cache / "expected.pyc").write_bytes(b"reproducible check cache")
    reply = command(project, "accept_role.py", "builder", "PASS")
    if change == "expected_cache":
        assert reply["outcome"] == "CHECKER", reply
        assert outside.read_text() == "User's preexisting dirty work.\n"
    else:
        assert reply["outcome"] == "REFUSED", reply
        assert "out_of_scope_source_change" in reply["summary"]
        assert (project / "foreground/lad-state/session.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["planning_repair", "remediation"])
def test_v2_role_event_cannot_be_relabelled_with_coherent_checksum(
    tmp_path: Path, kind: str
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    state = ledger(project)
    state["events"][0]["kind"] = kind
    state.pop("integrity_sha256")
    state["integrity_sha256"] = hashlib.sha256(
        json.dumps(
            state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    file = project / "foreground/lad-state/session.json"
    file.write_text(json.dumps(state))
    before = file.read_bytes()
    result = command(project, "init_session.py", "--project-root", "..")
    assert result["outcome"] == "RECONCILE", result
    assert "occurrence_role_history_mismatch" in result["summary"]
    assert file.read_bytes() == before


def helper_authority(project: Path, defect: str) -> tuple[dict, list[dict]]:
    """Inert independently pinned facility records, never live qualification."""
    workspace = project / "foreground"

    def write(relative: str, value: dict | str) -> dict:
        file = workspace / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(value) if isinstance(value, dict) else value)
        return {
            "path": relative, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()
        }

    tasks = []
    parent = "fixture-parent"
    for task_id, task_parent, state, area in (
        ("alpha", parent, "completed", "helpers/alpha"),
        ("child", "alpha", "cancelled", "helpers/alpha/child"),
        ("beta", parent, "failed", "helpers/beta"),
    ):
        result_ref = write(area + "/result.md", f"Collected {task_id}: {state}.")
        history = f"Recon task {task_id}; parent {task_parent}; role fixture-helper.\n"
        if defect == "large_helper_log" and task_id == "child":
            history += "x" * (5120 - len(history.encode()))
        history_ref = write(area + "/historylog/Recon_0000-102526.md", history)
        terminal = {
            "format": "millrace-lad-facility-terminal/v1",
            "binding": "fixture-binding", "facility_version": "fixture-v1",
            "task_id": task_id, "parent_task_id": task_parent,
            "terminal_state": state, "result_ref": result_ref,
            "collected": not (defect == "uncollected_child" and task_id == "child"),
            "started_at": "2026-10-25T00:00:00+00:00", "historylog_ref": history_ref,
        }
        terminal_ref = write("helper-authority/" + task_id + "-terminal.json", terminal)
        tasks.append({
            "task_id": task_id, "parent_task_id": task_parent, "role": "fixture-helper",
            "work_area": area, "model": None, "effort": None, "result_ref": result_ref,
            "terminal_state": (
                "running" if defect == "live_task" and task_id == "beta" else state
            ),
            "terminal_observation_ref": terminal_ref,
        })
    if defect == "duplicate_inventory":
        tasks.append(dict(tasks[0]))
    inventory = write("helper-authority/inventory.json", {
        "format": "millrace-lad-facility-inventory/v1",
        "binding": "fixture-binding", "facility_version": "fixture-v1",
        "parent_task_id": parent, "complete": True, "tasks": tasks,
    })
    qualification = write("helper-authority/qualification.json", {
        "format": "millrace-lad-helper-qualification/v1",
        "scope": "offline_validator_fixture", "binding": "fixture-binding",
        "facility_version": "fixture-v1", "parent_task_id": parent,
        "inventory_ref": inventory,
    })
    return qualification, tasks


@pytest.mark.parametrize("defect", [
    "none", "empty_self_report", "missing_child", "wrong_parent", "live_task",
    "uncollected_child", "duplicate_inventory", "forged_terminal",
    "missing_integration",
    "large_helper_log",
])
def test_helper_inventory_terminal_collection_and_integration_gate(
    tmp_path: Path, defect: str
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    qualification, tasks = helper_authority(project, defect)
    original = (project / "foreground/lad-state/session.json").read_bytes()
    refused = command(
        project, "prepare_role.py", "recon", "--helper-mode", "native_helpers",
        "--helper-qualification", qualification["path"],
    )
    assert refused["outcome"] == "REFUSED"
    assert "native_helpers_not_qualified" in refused["summary"]
    assert (project / "foreground/lad-state/session.json").read_bytes() == original
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    # The qualification check must precede the existing READY reuse shortcut too.
    refused = command(
        project, "prepare_role.py", "recon", "--helper-mode", "native_helpers",
        "--helper-qualification", qualification["path"],
    )
    assert refused["outcome"] == "REFUSED"
    assert "native_helpers_not_qualified" in refused["summary"]
    command(project, "lad_peer.py", "recon", "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    workspace = project / "foreground"
    result_file = workspace / "lad-results" / oid / "result.json"
    result = json.loads(result_file.read_bytes())
    qual = json.loads((workspace / qualification["path"]).read_bytes())
    integration = workspace / "lad-results" / oid / "integration.md"
    integration.write_text(
        f"Integrated occurrence {oid}, parent fixture-parent. "
        + ("alpha child beta" if defect != "missing_integration" else "alpha beta")
    )
    evidence = {
        "format": "millrace-lad-helper-evidence/v1", "occurrence_id": oid,
        "parent_task_id": (
            "wrong-parent" if defect == "wrong_parent" else "fixture-parent"
        ),
        "binding": "fixture-binding", "facility_version": "fixture-v1",
        "inventory_ref": qual["inventory_ref"], "tasks": tasks,
        "integration_ref": {"path": str(integration.relative_to(workspace)),
                            "sha256": hashlib.sha256(
                                integration.read_bytes()
                            ).hexdigest()},
    }
    if defect == "empty_self_report":
        evidence["tasks"] = []
    elif defect == "missing_child":
        evidence["tasks"] = [row for row in tasks if row["task_id"] != "child"]
    elif defect == "forged_terminal":
        (workspace / tasks[0]["terminal_observation_ref"]["path"]).write_text("{}")
    file = result_file.parent / "helper-evidence.json"
    file.write_text(json.dumps(evidence))
    result["helper_evidence"] = {
        "path": str(file.relative_to(workspace)),
        "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
    }
    result["helper_mode"] = "native_helpers"
    fixture_result = result_file.parent / "fixture-validation-result.json"
    fixture_result.write_text(json.dumps(result))
    before = (workspace / "lad-state/session.json").read_bytes()
    program = """
import json,sys
from pathlib import Path
from accept_role import validate_helpers,validate_execution_evidence
from lad_state import LadError
workspace=Path(sys.argv[1])
item=json.loads((workspace/'lad-state/active-role.json').read_bytes())
item['helper_mode']='native_helpers'
path=sys.argv[3]
import hashlib
item['helper_qualification']={
    'path':path,'sha256':hashlib.sha256((workspace/path).read_bytes()).hexdigest()
}
result=json.loads(Path(sys.argv[2]).read_bytes())
try:
    validate_helpers(workspace,item,result)
except (LadError,ValueError,KeyError,TypeError):
    structural=False
else:
    structural=True
try:
    validate_execution_evidence(workspace,item,result)
except LadError as exc:
    assert str(exc)=='native_helpers_not_qualified'
else:
    raise AssertionError('fixture qualification became production acceptance')
print(json.dumps({'structural':structural}))
"""
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", program, str(workspace),
         str(fixture_result), qualification["path"]],
        cwd=project / "scripts", env={"PATH": "/usr/bin:/bin"},
        capture_output=True, timeout=10,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert json.loads(child.stdout)["structural"] == (defect == "none")
    assert (workspace / "lad-state/session.json").read_bytes() == before
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    assert oid in ledger(project)["accepted"]


def test_planner_cannot_select_helpers(tmp_path: Path) -> None:
    project = copied(tmp_path)
    before_role(project, "planner")
    qualification, _ = helper_authority(project, "none")
    before = (project / "foreground/lad-state/session.json").read_bytes()
    reply = command(
        project, "prepare_role.py", "planner", "--helper-mode", "native_helpers",
        "--helper-qualification", qualification["path"],
    )
    assert reply["outcome"] == "REFUSED", reply
    assert (project / "foreground/lad-state/session.json").read_bytes() == before


def test_invalid_result_fresh_owner_then_same_repaired_cause_consults(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    owners = []
    for index, expected in enumerate((
        "INVALID_RESULT", "INVALID_REPEAT", "INVALID_CONSULT"
    )):
        assert command(
            project, "prepare_role.py", "recon", *(["--replacement"] if index else [])
        )["outcome"] == "READY"
        command(project, "lad_peer.py", "recon", "clean")
        state = ledger(project)
        oid = state["active"]["foreground"]
        assert oid not in owners
        if owners:
            assert state["occurrences"][oid]["replacement_of"] == owners[-1]
        owners.append(oid)
        file = project / "foreground/lad-results" / oid / "result.json"
        result = json.loads(file.read_bytes())
        result["occurrence_id"] = "wrong-occurrence"
        file.write_text(json.dumps(result))
        rejected = file.read_bytes()
        reply = command(project, "accept_role.py", "recon", "PASS")
        assert reply["outcome"] == expected, reply
        assert file.read_bytes() == rejected
        assert oid not in ledger(project)["accepted"]
        assert ledger(project)["packet_index"] == 0
        if index == 1:
            assert command(
                project, "prepare_role.py", "troubleshooter"
            )["outcome"] == "READY"
            command(project, "lad_peer.py", "troubleshooter", "clean")
            assert command(
                project, "accept_role.py", "troubleshooter",
                "PASS_PREREQUISITE_REPAIRED"
            )["outcome"] == "RESUME_RECON"
    assert ledger(project)["continuation"]["node"] == "prepare_consultant"


@pytest.mark.parametrize("same_cause", [False, True])
def test_repaired_cause_recurrence_is_distinct_from_unrelated_failure(
    tmp_path: Path, same_cause: bool
) -> None:
    project = copied(tmp_path)
    before_role(project, "builder")
    prerequisite = project / "foreground/fixture-prerequisite.json"
    prerequisite.write_text('{"available":false}')
    for index in range(2):
        assert command(
            project, "prepare_role.py", "builder", *(["--replacement"] if index else [])
        )["outcome"] == "READY"
        command(project, "lad_peer.py", "builder", "clean", "ERROR")
        state = ledger(project)
        oid = state["active"]["foreground"]
        file = project / "foreground/lad-results" / oid / "result.json"
        result = json.loads(file.read_bytes())
        result["data"]["failure"]["operation"] = (
            "read fixture-prerequisite" if index == 0 or same_cause
            else "other local check"
        )
        file.write_text(json.dumps(result))
        reply = command(project, "accept_role.py", "builder", "ERROR")
        assert reply["outcome"] == (
            "CONSULTANT" if index and same_cause else "TROUBLESHOOTER"
        ), reply
        if index == 0:
            assert command(
                project, "prepare_role.py", "troubleshooter"
            )["outcome"] == "READY"
            prerequisite.write_text('{"available":true}')
            command(project, "lad_peer.py", "troubleshooter", "clean")
            assert command(
                project, "accept_role.py", "troubleshooter",
                "PASS_PREREQUISITE_REPAIRED"
            )["outcome"] == "RESUME_BUILDER"
    assert ledger(project)["packet_index"] == 0


def test_missing_history_restores_paperwork_without_replaying_code(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    before_role(project, "builder")
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    command(project, "lad_peer.py", "builder", "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    output = project / "foreground/lad-results" / oid
    result_file = output / "result.json"
    retained = result_file.read_bytes()
    result = json.loads(retained)
    history = next(
        row for row in result["artifacts"] if row["path"].startswith("historylog/")
    )
    (output / history["path"]).unlink()
    target = project / "foreground/target/answer.py"
    source, modified = target.read_bytes(), target.stat().st_mtime_ns
    reply = command(project, "accept_role.py", "builder", "PASS")
    assert reply["outcome"] == "CHECKER", reply
    assert target.read_bytes() == source and target.stat().st_mtime_ns == modified
    assert ledger(project)["events"] == state["events"]
    preserved = project / "foreground/lad-state/rejections" / oid
    assert any(file.read_bytes() == retained for file in preserved.glob("*.json"))
    assert command(project, "accept_role.py", "builder", "PASS")["outcome"] == "CHECKER"


@pytest.mark.parametrize("size", [5119, 5120])
def test_historylog_limit_counts_strict_utf8_bytes(tmp_path: Path, size: int) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    command(project, "lad_peer.py", "recon", "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    output = project / "foreground/lad-results" / oid
    file = output / "result.json"
    result = json.loads(file.read_bytes())
    history = next(
        row for row in result["artifacts"] if row["path"].startswith("historylog/")
    )
    log = output / history["path"]
    raw = log.read_bytes()
    raw += "é".encode() * ((size - len(raw)) // 2)
    raw += b"x" * (size - len(raw))
    assert len(raw) == size and len(raw.decode()) < size
    log.write_bytes(raw)
    history["sha256"] = hashlib.sha256(raw).hexdigest()
    file.write_text(json.dumps(result))
    reply = command(project, "accept_role.py", "recon", "PASS")
    assert reply["outcome"] == ("PLANNER" if size == 5119 else "INVALID_RESULT"), reply
    assert (oid in ledger(project)["accepted"]) == (size == 5119)


def test_manager_accepts_more_than_sixty_meaningful_ordered_packets(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    names = ["answer"] + ["answer_" + str(index) for index in range(1, 61)]
    request = ("Original required functions:\n" + "\n".join(
        name + "() returns 42" for name in names
    ) + "\n").encode()
    bootstrap(project, request=request)
    assert step(project, "recon") == "PLANNER"
    assert step(project, "planner", scenario="many_packets") == "MANAGER"
    assert step(project, "manager", scenario="many_packets") == "BUILDER"
    retained = ledger(project)
    assert len(retained["plan"]) == len(retained["criteria"]) == 61
    assert [row["id"] for row in retained["criteria"]] == names
    assert [row["lineage"] for row in retained["plan"]] == names
    assert retained["packet_index"] == 0
    # Twenty-one actual packet completions cross the former all=60 role
    # ceiling (three setup roles + 63 owned preparations/acceptances). The remaining
    # forty original packets stay intact; this is a finite useful journey.
    for _ in range(21):
        for role in ("builder", "checker", "updater"):
            step(project, role, scenario="many_packets")
    progressed = ledger(project)
    assert all(row["accepted"] is True for row in progressed["plan"][:21])
    assert progressed["plan"][21:] == retained["plan"][21:]
    assert progressed["packet_index"] == 21
    assert progressed["criteria"] == retained["criteria"]
    assert len(role_occurrences(progressed, "manager")) == 1
    assert len(progressed["events"]) == len(progressed["occurrences"]) == 66
    assert len({row["id"] for row in progressed["events"]}) == 66
    assert len(progressed["accepted"]) == 66
    for name in names[:21]:
        namespace = {}
        exec((project / "foreground/target" / (name + ".py")).read_text(), namespace)
        assert namespace[name]() == 42
    assert "limits" not in progressed and "usage" not in progressed
    assert (project / "foreground/request.md").read_bytes() == request


@pytest.mark.parametrize("owned", [False, True], ids=["outside", "owned"])
def test_source_links_are_observed_but_never_grant_escaping_writes(
    tmp_path: Path, owned: bool
) -> None:
    project = copied(tmp_path)
    target = project / "foreground/target"
    target.mkdir()
    protected = tmp_path / "external-user.txt"
    protected.write_text("User bytes outside the task.\n")
    link = target / ("answer.py" if owned else "reference-link")
    link.symlink_to(protected)
    before_role(project, "builder")
    before = (project / "foreground/lad-state/session.json").read_bytes()
    result = command(project, "prepare_role.py", "builder")
    if owned:
        assert result["outcome"] == "REFUSED", result
        assert "writable_source_link_requires_reconciliation" in result["summary"]
        assert (project / "foreground/lad-state/session.json").read_bytes() == before
    else:
        assert result["outcome"] == "READY", result
        command(project, "lad_peer.py", "builder", "clean")
        assert command(
            project, "accept_role.py", "builder", "PASS"
        )["outcome"] == "CHECKER"
    assert protected.read_text() == "User bytes outside the task.\n"


def test_ignored_protected_user_input_is_not_a_disposable_check_product(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    target = project / "foreground/target"
    target.mkdir()
    protected = target / ".env"
    protected.write_text("fixture-setting=original\n")
    (target / ".gitignore").write_text(".env\n")
    for args in [
        ["init", "--quiet"], ["add", "--", ".gitignore"],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--quiet", "-m", "fixture ignore baseline"],
        ["check-ignore", ".env"],
    ]:
        child = subprocess.run(
            ["git", "-C", str(target), *args],
            env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
                 "GIT_CONFIG_GLOBAL": "/dev/null"},
            capture_output=True, timeout=10,
        )
        assert child.returncode == 0, child.stderr
    before_role(project, "builder")
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    command(project, "lad_peer.py", "builder", "clean")
    before = (project / "foreground/lad-state/session.json").read_bytes()
    protected.write_text("fixture-setting=unauthorized\n")
    result = command(project, "accept_role.py", "builder", "PASS")
    assert result["outcome"] == "REFUSED", result
    assert "out_of_scope_source_change" in result["summary"]
    assert (project / "foreground/lad-state/session.json").read_bytes() == before


@pytest.mark.parametrize("missing", [False, True], ids=["changed", "missing"])
@pytest.mark.parametrize("entry", ["prepare", "accept"])
def test_baseline_tamper_restores_original_then_consultant_fresh_checker(
    tmp_path: Path, missing: bool, entry: str
) -> None:
    project = copied(tmp_path)
    before_role(project, "updater")
    foreground = project / "foreground"
    state = ledger(project)
    baseline = state["baselines"]["answer"]
    original = (foreground / baseline["path"]).read_bytes()
    pin = (foreground / baseline["parent_pin"]["path"]).read_bytes()
    accepted = dict(state["accepted"])
    target = foreground / "target/answer.py"
    target_raw, modified = target.read_bytes(), target.stat().st_mtime_ns
    if entry == "accept":
        assert command(project, "prepare_role.py", "updater")["outcome"] == "READY"
        command(project, "lad_peer.py", "updater", "clean")
        state = ledger(project)
    projection = foreground / baseline["path"]
    bad = original + b"\nWorker replacement expectations are forbidden.\n"
    if missing:
        projection.unlink()
    else:
        projection.write_bytes(bad)
    reply = command(
        project, "prepare_role.py" if entry == "prepare" else "accept_role.py",
        "updater", *([] if entry == "prepare" else ["PASS"]),
    )
    assert reply["outcome"] == "BASELINE_TAMPER", reply
    restored = ledger(project)
    assert restored["baselines"] == state["baselines"]
    assert restored["accepted"] == accepted
    assert restored["events"] == state["events"]
    assert restored["pending"]["kind"] == "baseline_tamper"
    assert restored["continuation"]["node"] == "prepare_consultant"
    assert "answer" not in restored["reviews"]
    assert projection.read_bytes() == original
    assert (foreground / baseline["parent_pin"]["path"]).read_bytes() == pin
    owner = state["active"]["foreground"]
    observation_file = foreground / f"lad-state/baseline-pins/{owner}-restoration.json"
    observation = json.loads(observation_file.read_bytes())
    if missing:
        assert observation["observed"] is None
    else:
        assert (foreground / observation["observed"]["path"]).read_bytes() == bad
    assert step(project, "consultant", "REPAIR_EXECUTION") == "CHECKER"
    assert step(project, "checker") == "UPDATER"
    after = ledger(project)
    assert after["baselines"] == state["baselines"]
    assert after["pending"] is None
    assert len(role_occurrences(after, "checker")) == 2
    assert after["reviews"]["answer"]["occurrence_id"] != baseline["occurrence_id"]
    assert projection.read_bytes() == original
    assert observation_file.read_bytes() == json.dumps(
        observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode() + b"\n"
    assert target.read_bytes() == target_raw and target.stat().st_mtime_ns == modified
    consultant = role_occurrences(after, "consultant")[0]
    ref = consultant["pending"]["restoration"]
    assert ref["sha256"] == hashlib.sha256(observation_file.read_bytes()).hexdigest()
    snapshot = (foreground / "lad-state/session.json").read_bytes()
    if missing:
        observation_file.write_bytes(observation_file.read_bytes() + b" ")
    else:
        (foreground / observation["observed"]["path"]).write_bytes(b"Lost observation")
    assert command(project, "prepare_role.py", "updater")["outcome"] == "REFUSED"
    assert (foreground / "lad-state/session.json").read_bytes() == snapshot
    assert projection.read_bytes() == original
    graph = json.loads((project / "workflow.json").read_bytes())
    nodes = graph["planes"]["foreground"]["nodes"]
    assert nodes["prepare_updater"]["on"]["BASELINE_TAMPER"] == {
        "goto": "prepare_consultant"
    }
    assert nodes["accept_consultant_repair_execution"]["on"]["CHECKER"] == {
        "goto": "prepare_checker"
    }


@pytest.mark.parametrize("damage", [
    "missing_backup", "corrupt_backup", "backup_symlink", "projection_symlink",
    "other_artifact", "outside_source", "wrong_owner",
])
def test_baseline_restoration_refuses_unverified_authority(
    tmp_path: Path, damage: str
) -> None:
    project = copied(tmp_path)
    before_role(project, "updater")
    foreground = project / "foreground"
    state = ledger(project)
    baseline = state["baselines"]["answer"]
    projection = foreground / baseline["path"]
    original = projection.read_bytes()
    backup = foreground / (
        "lad-state/baseline-pins/" + baseline["occurrence_id"] + ".md"
    )
    projection.write_bytes(b"Observed baseline tamper.\n")
    if damage == "missing_backup":
        backup.unlink()
    elif damage == "corrupt_backup":
        backup.write_bytes(b"Not the original expectations.\n")
    elif damage == "backup_symlink":
        backup.unlink()
        backup.symlink_to(projection)
    elif damage == "projection_symlink":
        projection.unlink()
        projection.symlink_to(backup)
    elif damage == "other_artifact":
        checker_result = state["accepted"][baseline["occurrence_id"]]["result"]
        artifact = next(row for row in checker_result["artifacts"]
                        if row["path"] == "review.md")
        (projection.parent / artifact["path"]).write_bytes(b"Corrupt review too.\n")
    elif damage == "outside_source":
        (foreground / "target/unapproved.py").write_text("unauthorized = True\n")
    session_before = (foreground / "lad-state/session.json").read_bytes()
    reply = command(
        project, "prepare_role.py", "planner" if damage == "wrong_owner" else "updater"
    )
    assert reply["outcome"] == "REFUSED", reply
    assert (foreground / "lad-state/session.json").read_bytes() == session_before
    assert not list((foreground / "lad-state/baseline-pins").glob("*-restoration.json"))
    if damage != "projection_symlink":
        assert projection.read_bytes() != original


@pytest.mark.parametrize("window", ["observation", "restored", "routed"])
def test_baseline_restoration_interruption_finishes_route_without_replay(
    tmp_path: Path, window: str
) -> None:
    project = copied(tmp_path)
    before_role(project, "updater")
    foreground = project / "foreground"
    state = ledger(project)
    baseline = state["baselines"]["answer"]
    projection = foreground / baseline["path"]
    original = projection.read_bytes()
    projection.write_bytes(original + b"\nBad replacement observed.\n")
    target = foreground / "target/answer.py"
    modified = target.stat().st_mtime_ns
    program = '''
import sys
from pathlib import Path
import accept_role
class FixtureCrash(BaseException):
    pass
real_atomic, real_save = accept_role.atomic, accept_role.save
window, baseline = sys.argv[2:4]
def atomic(root, name, raw, **kwargs):
    real_atomic(root, name, raw, **kwargs)
    if (window == 'observation' and name.endswith('-restoration.json')) or (
        window == 'restored' and name == baseline
    ):
        raise FixtureCrash()
def save(root, state):
    real_save(root, state)
    if window == 'routed':
        raise FixtureCrash()
accept_role.atomic, accept_role.save = atomic, save
try:
    accept_role.guard_baseline(Path(sys.argv[1]), 'updater', preparing=True)
except FixtureCrash:
    sys.exit(81)
raise AssertionError('fault window not reached')
'''
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", program,
         str(foreground), window, baseline["path"]],
        cwd=project / "scripts", capture_output=True, text=True, timeout=10,
    )
    assert child.returncode == 81, child.stdout + child.stderr
    assert command(
        project, "prepare_role.py", "updater"
    )["outcome"] == "BASELINE_TAMPER"
    after = ledger(project)
    assert after["events"] == state["events"]
    assert after["accepted"] == state["accepted"]
    assert after["baselines"] == state["baselines"]
    assert after["continuation"]["node"] == "prepare_consultant"
    assert projection.read_bytes() == original
    assert target.stat().st_mtime_ns == modified
    assert len(list((foreground / "lad-state/baseline-pins").glob(
        "*-restoration.json"
    ))) == 1
    assert step(project, "consultant", "REPAIR_EXECUTION") == "CHECKER"


def test_real_graph_baseline_tamper_routes_consultant_and_new_checker(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    _, view = started(project, tmp_path / "state", "baseline_tamper")
    assert view["status"] == "succeeded", view
    state = ledger(project)
    assert state["terminal"] == "succeeded" and state["request"] is None
    assert len(role_occurrences(state, "checker")) == 2
    assert len(role_occurrences(state, "consultant")) == 1
    updaters = role_occurrences(state, "updater")
    assert len(updaters) == 2
    assert updaters[0]["occurrence_id"] not in state["accepted"]
    assert updaters[1]["occurrence_id"] in state["accepted"]
    baseline = state["baselines"]["answer"]
    foreground = project / "foreground"
    backup = foreground / (
        "lad-state/baseline-pins/" + baseline["occurrence_id"] + ".md"
    )
    assert (foreground / baseline["path"]).read_bytes() == backup.read_bytes()
    observations = list((foreground / "lad-state/baseline-pins").glob(
        "*-restoration.json"
    ))
    assert len(observations) == 1
    observed = json.loads(observations[0].read_bytes())
    assert b"Unauthorized new expectation" in (
        foreground / observed["observed"]["path"]
    ).read_bytes()
    nodes = [row["node"] for row in view["invocations"]]
    first_accept = nodes.index("accept_updater_pass")
    assert nodes[first_accept + 1:first_accept + 5] == [
        "prepare_consultant", "consultant", "accept_consultant_repair_execution",
        "prepare_checker",
    ]
    assert len(view["seam_occurrences"]) == 2
    assert len(view["seam_acknowledgements"]) == 2
    namespace = {}
    exec((foreground / "target/answer.py").read_text(), namespace)
    assert namespace["answer"]() == 42


@pytest.mark.parametrize("replacement", [False, True])
@pytest.mark.parametrize("tampered_baseline", [False, True])
def test_interrupted_stored_fixture_helper_never_rematerializes_ready(
    tmp_path: Path, replacement: bool, tampered_baseline: bool
) -> None:
    project = copied(tmp_path)
    role = "updater" if tampered_baseline else "recon"
    if tampered_baseline:
        before_role(project, "updater")
    else:
        bootstrap(project)
    assert command(project, "prepare_role.py", role)["outcome"] == "READY"
    if replacement:
        assert command(
            project, "prepare_role.py", role, "--replacement"
        )["outcome"] == "READY"
    foreground = project / "foreground"
    state = ledger(project)
    oid = state["active"]["foreground"]
    occurrence = state["occurrences"][oid]
    occurrence["helper_mode"] = "native_helpers"
    occurrence["helper_qualification"] = {
        "path": "fixture-only.json", "sha256": "f" * 64
    }
    occurrence.pop("input_sha256")
    occurrence["input_sha256"] = hashlib.sha256(json.dumps(
        occurrence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    output = foreground / "lad-state/occurrences" / oid
    (output / "ready.json").unlink()
    (output / "input.json").write_text(json.dumps(occurrence))
    (foreground / "lad-state/active-role.json").write_text(json.dumps(occurrence))
    state.pop("integrity_sha256")
    state["integrity_sha256"] = hashlib.sha256(json.dumps(
        state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    session = foreground / "lad-state/session.json"
    session.write_text(json.dumps(state))
    if tampered_baseline:
        baseline = foreground / state["baselines"]["answer"]["path"]
        baseline.write_bytes(b"Bad baseline; unsupported owner cannot restore it.\n")
    before = session.read_bytes()
    reply = command(
        project, "prepare_role.py", role, *(["--replacement"] if replacement else [])
    )
    assert reply["outcome"] == "REFUSED", reply
    assert ("baseline_owner_mismatch" if tampered_baseline
            else "native_helpers_not_qualified") in reply["summary"]
    assert not (output / "ready.json").exists()
    assert session.read_bytes() == before
    if tampered_baseline:
        assert baseline.read_bytes().startswith(b"Bad baseline;")
        assert not list((foreground / "lad-state/baseline-pins").glob(
            "*-restoration.json"
        ))


def test_accepted_replacement_owner_cannot_rebase_on_stale_pending(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    original = ledger(project)["active"]["foreground"]
    assert command(
        project, "prepare_role.py", "recon", "--replacement"
    )["outcome"] == "READY"
    replacement = ledger(project)["active"]["foreground"]
    assert replacement != original
    command(project, "lad_peer.py", "recon", "clean")
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    state = ledger(project)
    assert state["pending"]["occurrence_id"] == original
    assert state["pending"]["target"] == "recon"
    assert state["occurrences"][original]["lineage"] == (
        state["occurrences"][replacement]["lineage"]
    )
    assert replacement in state["accepted"] and original not in state["accepted"]
    session = project / "foreground/lad-state/session.json"
    before = session.read_bytes()
    rejected = command(project, "prepare_role.py", "recon", "--replacement")
    assert rejected["outcome"] == "REFUSED", rejected
    assert "replacement_route_not_selected" in rejected["summary"]
    assert session.read_bytes() == before
    assert len(role_occurrences(ledger(project), "recon")) == 2
    assert ledger(project)["active"]["foreground"] == replacement


@pytest.mark.parametrize("recovery", ["baseline", "planning"])
def test_fresh_checker_excludes_valid_hostile_consultant_and_tamper_carriers(
    tmp_path: Path, recovery: str
) -> None:
    project = copied(tmp_path)
    foreground = project / "foreground"
    marker = "HOSTILE_CONSULTANT_IMPLEMENTATION_DERIVED_APPENDIX_42"
    bad_marker = "HOSTILE_BASELINE_EXPECTATION_REPLACEMENT_42"
    forbidden = [marker.encode(), bad_marker.encode()]
    if recovery == "baseline":
        before_role(project, "updater")
        original_baseline = ledger(project)["baselines"]["answer"]
        baseline = foreground / original_baseline["path"]
        retained_baseline = baseline.read_bytes()
        baseline.write_bytes(retained_baseline + bad_marker.encode())
        assert command(
            project, "prepare_role.py", "updater"
        )["outcome"] == "BASELINE_TAMPER"
        saved_pending = ledger(project)["pending"]
        restoration = saved_pending["restoration"]
        observed = json.loads((foreground / restoration["path"]).read_bytes())
        forbidden += [
            restoration["path"].encode(), observed["observed"]["path"].encode()
        ]
        selected_outcome = "REPAIR_EXECUTION"
    else:
        before_role(project, "consultant")
        saved_pending = ledger(project)["pending"]
        selected_outcome = "REPAIR_SPEC"
    assert command(project, "prepare_role.py", "consultant")["outcome"] == "READY"
    command(project, "lad_peer.py", "consultant", "clean", selected_outcome)
    consultant = ledger(project)["active"]["foreground"]
    output = foreground / "lad-results" / consultant
    result_file = output / "result.json"
    result = json.loads(result_file.read_bytes())
    diagnosis = output / "diagnosis.md"
    # Valid hostile evidence is supplied BEFORE acceptance. Recompute every
    # affected digest so this exercises selective exposure, not immutability.
    diagnosis.write_bytes(diagnosis.read_bytes() + b"\n" + b"\n".join(forbidden))
    checksum = hashlib.sha256(diagnosis.read_bytes()).hexdigest()
    for artifact in result["artifacts"]:
        if artifact["path"] == "diagnosis.md":
            artifact["sha256"] = checksum
    result["data"]["next_evidence"]["sha256"] = checksum
    result_file.write_text(json.dumps(result))
    assert command(
        project, "accept_role.py", "consultant", selected_outcome
    )["outcome"] == ("CHECKER" if recovery == "baseline" else "PLANNER")
    accepted_consultant = ledger(project)["accepted"][consultant]
    if recovery == "planning":
        # Requirement-role envelopes/Markdown may themselves carry the accepted
        # Consultant ref. A role-name whitelist must not expose them wholesale.
        for role, route in (("planner", "MANAGER"), ("manager", "BUILDER")):
            assert command(project, "prepare_role.py", role)["outcome"] == "READY"
            command(project, "lad_peer.py", role, "clean")
            owner = ledger(project)["active"]["foreground"]
            role_file = foreground / "lad-results" / owner / "result.json"
            value = json.loads(role_file.read_bytes())
            for artifact in value["artifacts"]:
                evidence = role_file.parent / artifact["path"]
                evidence.write_bytes(evidence.read_bytes() + b"\n" + marker.encode()
                                     + b"\n" + consultant.encode())
                artifact["sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
            if role == "manager":
                value["data"]["plan"][0]["input_refs"] = [{
                    "occurrence_id": consultant,
                    "sha256": accepted_consultant["sha256"],
                }]
            role_file.write_text(json.dumps(value))
            assert command(
                project, "accept_role.py", role, "PASS"
            )["outcome"] == route
        assert step(project, "builder") == "CHECKER"
    assert command(project, "prepare_role.py", "checker")["outcome"] == "READY"
    state = ledger(project)
    checker = state["occurrences"][state["active"]["foreground"]]
    assert checker["pending"] is None
    assert checker["fixer"] is None and checker["finding_ids"] == []
    assert checker["review"] is None
    assert checker["accepted_inputs"] == checker["accepted_input_refs"] == {}
    assert checker["specification"] is None
    assert set(checker["packet"]) == {
        "id", "lineage", "criteria", "depends_on", "paths"
    }
    assert checker["criteria"] == state["criteria"]
    for key, value in checker["packet"].items():
        assert value == state["plan"][state["packet_index"]][key]
    materialized = foreground / "lad-state/occurrences" / checker["occurrence_id"]
    payloads = [p.read_bytes() for p in materialized.rglob("*") if p.is_file()]
    payloads.append((foreground / "lad-state/active-role.json").read_bytes())
    for ref in checker["accepted_input_refs"].values():
        workspace = project / ref["workspace"]
        raw = (workspace / ref["path"]).read_bytes()
        payloads.append(raw)
        accepted_result = json.loads(raw)
        for artifact in accepted_result["artifacts"]:
            payloads.append((workspace / Path(ref["path"]).parent / artifact["path"])
                            .read_bytes())
    for raw in payloads:
        assert consultant.encode() not in raw
        assert all(value not in raw for value in forbidden)
    assert state["accepted"][consultant] == accepted_consultant
    assert marker.encode() in diagnosis.read_bytes()
    if recovery == "baseline":
        assert state["pending"] == saved_pending
        assert state["occurrences"][consultant]["pending"] == saved_pending
        assert (foreground / restoration["path"]).is_file()
        assert baseline.read_bytes() == retained_baseline
    command(project, "lad_peer.py", "checker", "clean")
    assert command(project, "accept_role.py", "checker", "PASS")["outcome"] == "UPDATER"
    assert step(project, "updater") == "ARBITER"
    after = ledger(project)
    assert after["plan"][0]["accepted"] is True
    assert after["accepted"][consultant] == accepted_consultant
    if recovery == "baseline":
        assert after["baselines"]["answer"] == original_baseline
        assert (foreground / restoration["path"]).is_file()


def test_matching_baseline_owner_with_unsupported_helpers_cannot_restore(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    before_role(project, "updater")
    assert command(project, "prepare_role.py", "updater")["outcome"] == "READY"
    command(project, "lad_peer.py", "updater", "clean")
    foreground = project / "foreground"
    state = ledger(project)
    oid = state["active"]["foreground"]
    item = state["occurrences"][oid]
    assert item["role"] == "updater" and state["continuation"]["node"] == "updater"
    item["helper_mode"] = "native_helpers"
    item["helper_qualification"] = {
        "path": "fixture-only.json", "sha256": "f" * 64
    }
    item.pop("input_sha256")
    item["input_sha256"] = hashlib.sha256(json.dumps(
        item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    (foreground / "lad-state/occurrences" / oid / "input.json").write_text(
        json.dumps(item)
    )
    (foreground / "lad-state/active-role.json").write_text(json.dumps(item))
    result_file = foreground / "lad-results" / oid / "result.json"
    result = json.loads(result_file.read_bytes())
    result["input_sha256"] = item["input_sha256"]
    result["helper_mode"] = "native_helpers"
    result_file.write_text(json.dumps(result))
    state.pop("integrity_sha256")
    state["integrity_sha256"] = hashlib.sha256(json.dumps(
        state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()).hexdigest()
    session = foreground / "lad-state/session.json"
    session.write_text(json.dumps(state))
    baseline = foreground / state["baselines"]["answer"]["path"]
    bad = b"Unsupported owner must never restore these observed bad bytes.\n"
    baseline.write_bytes(bad)
    before = session.read_bytes()
    ready = foreground / "lad-state/occurrences" / oid / "ready.json"
    ready_before = ready.read_bytes()
    reply = command(project, "accept_role.py", "updater", "PASS")
    assert reply["outcome"] == "REFUSED", reply
    assert "baseline_writer_reconciliation_required" in reply["summary"]
    assert session.read_bytes() == before and baseline.read_bytes() == bad
    assert ready.read_bytes() == ready_before
    assert not list((foreground / "lad-state/baseline-pins").glob(
        "*-restoration.json"
    ))
