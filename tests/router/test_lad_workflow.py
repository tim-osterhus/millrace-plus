"""LAD domain accounting and real graph/seam execution with inert peers."""

from __future__ import annotations

import hashlib
import json
import os
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


def removed_operation(project: Path, plane: str, script: str, *args: str) -> bool:
    """A removed CLI operation exits as a command error and writes nothing."""
    before = (project / "foreground/lad-state/session.json").read_bytes()
    child = subprocess.run(
        [sys.executable, str(project / "scripts" / script), *args],
        cwd=project / plane,
        env={"PATH": str(Path(sys.executable).parent)},
        capture_output=True,
    )
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    return child.returncode == 0


def ledger(project: Path) -> dict:
    return json.loads((project / "foreground/lad-state/session.json").read_bytes())


def role_occurrences(state: dict, role: str) -> list[dict]:
    return [
        state["occurrences"][event["id"]]
        for event in state["events"]
        if event["kind"] == "role" and state["occurrences"][event["id"]]["role"] == role
    ]


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
            if node["invoke"].get("binding") != "claude" or role.startswith(
                "provider_diagnosis_"
            ):
                continue
            # One nonrecursive assessment: Learning records failure, never replaces.
            failure = (
                "record_learning_failure"
                if role == "learning"
                else "prepare_" + role + "_replacement"
            )
            # Only foreground native on_error is rewired to its provider classifier.
            assert node["on_unknown"] == {"goto": failure}
            assert node["on_error"] == (
                {"goto": failure}
                if role == "learning"
                else {"goto": "classify_provider_" + role}
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
    learning = graph["planes"]["learning"]["nodes"]
    assert "prepare_learning_replacement" not in learning
    assert not any(
        action.get("goto") == "learning"
        for name, node in learning.items()
        if name != "prepare_learning"
        for action in [*node["on"].values(), node["on_error"], node["on_unknown"]]
    )
    for name, diagnostic in (
        ("record_learning_failure", "native_completion_failed"),
        ("record_learning_invalid", "invalid_assessment"),
        ("record_learning_prepare_failure", "preparation_failed"),
    ):
        node = learning[name]
        assert node["invoke"]["config"] == {
            "script": "scripts/learning_seam.py",
            "args": ["fail", "--diagnostic", diagnostic],
        }
        assert node["on"]["ACK"] == {"goto": "emit_ack"}
        assert "pause" in node["on"]["REFUSED"] and "pause" in node["on_error"]
    assert (
        learning["prepare_learning"]["on_error"]
        == {"goto": "record_learning_prepare_failure"}
        == learning["prepare_learning"]["on_unknown"]
    )
    for name, node in learning.items():
        if name.startswith("accept_learning_"):
            assert node["outcomes"] == ["ACK", "REFUSED", "INVALID_RESULT"]
            assert node["on"]["INVALID_RESULT"] == {"goto": "record_learning_invalid"}


# Ordinary semantic roles only. The provider-only native diagnosis nodes are
# excluded exactly as in test_all_roles_have_graph_routes; they are covered by
# the provider template and third-failure graph cases.
ROLE_OUTCOMES = [
    (role, outcome)
    for plane in json.loads((LAD / "workflow.json").read_bytes())["planes"].values()
    for role, node in plane["nodes"].items()
    if node["invoke"].get("binding") == "claude"
    and role != "learning"
    and not role.startswith("provider_diagnosis_")
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
    assert (
        command(project, "accept_role.py", "recon", "PASS")["outcome"]
        == "INVALID_RESULT"
    )
    assert ledger(project)["events"] == before_events
    assert file.read_bytes() == rejected
    assert oid not in ledger(project)["accepted"]
    assert (
        ledger(project)["replacement_intents"][oid]["reason"]
        == "invalid_semantic_result"
    )
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
        "context/contracts.md",
        "context/transitions.md",
        "context/routing.md",
        "context/nested-models.md",
        "prompts/planner.md",
        "prompts/checker.md",
        "adapter-templates/claude-code/README.md",
    }
    for row in corrected["inputs"]:
        assert len(row["sha256"]) == 64 and row["bytes"] > 0
        assert corrected["commit"] in row["source_url"]
        assert row["disposition"]
        assert all((LAD / name).is_file() for name in row["destinations"])
    followup = provenance["routing_followup_reference"]
    assert followup["commit"] == "e4d587290ceeaa886c6486ae03bd9a15831cf691"
    assert followup["compared_from"] == corrected["commit"]
    assert {row["original_path"] for row in followup["inputs"]} == {
        "context/transitions.md",
        "context/contracts.md",
        "prompts/learning.md",
    }
    for row in followup["inputs"]:
        assert len(row["sha256"]) == 64 and row["bytes"] > 0 and row["disposition"]
        assert followup["commit"] in row["source_url"]
        assert all((LAD / name).is_file() for name in row["destinations"])
    destinations = provenance["adaptation"]["destination_hashes"]
    assert len({row["path"] for row in destinations}) == len(destinations)
    assert {"context/routing-policy.json", "scripts/presentation.py"} <= {
        row["path"] for row in destinations
    }
    assert "scripts/session_compat.py" in {row["path"] for row in destinations}
    assert {"context/provider-policy.json", "scripts/provider_wait.py"} <= {
        row["path"] for row in destinations
    }
    assert not {"PROVENANCE.md", "provenance.json"} & {
        row["path"] for row in destinations
    }
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
    # A resolved transient error is a failed assessment, not renewed authority.
    assert accepted["outcome"] == "ACK", accepted
    assert ledger(project)["pending"] == pending
    assert ledger(project)["learning"]["answer"]["disposition"] == (
        "passed"
        if outcome == "PASS"
        else "findings"
        if outcome == "FINDINGS"
        else "failed"
    )
    assert not removed_operation(project, "learning", "learning_seam.py", "replace")
    assert len(role_occurrences(ledger(project), "learning")) == 1
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
    assert (
        sum(row["kind"] == "planning_repair" for row in ledger(project)["events"]) == 2
    )
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
    assert (
        command(project, "accept_role.py", "consultant", "REPAIR_SPEC")["outcome"]
        == "PLANNER"
    )
    assert (
        sum(row["kind"] == "planning_repair" for row in ledger(project)["events"]) == 3
    )


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
    result["source_sha256"] = hashlib.sha256(
        json.dumps(
            files, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
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
    assert (
        ledger(project)["occurrences"][ledger(project)["active"]["foreground"]][
            "replacement_of"
        ]
        == oid
    )


def test_catalog_copy_and_stdlib_only_scripts(tmp_path: Path) -> None:
    import ast

    destination = tmp_path / "initialized"
    catalog.init_template("bundled:lad", destination, catalog_home=tmp_path / "catalog")
    assert (destination / "workflow.json").read_bytes() == (
        LAD / "workflow.json"
    ).read_bytes()
    # Package boundary: copied scripts import the Python stdlib and their own
    # sibling scripts at module scope. A Plus import is always a lazy
    # ``from millrace_plus... import NAME`` inside the exact function that
    # needs it (or a module-level ``if TYPE_CHECKING:`` block), with the exact
    # module, name and alias listed here. A routing-policy/v1 copy never
    # reaches these functions (proved in isolated mode below). Scripts never
    # import Core or another third-party package directly.
    siblings = {
        "lad_state",
        "boundaries",
        "prepare_role",
        "accept_role",
        "session_compat",
        "engineering_feedback",
        "provider_wait",
    }
    pw = ("millrace_plus", "provider_wait", "pw")
    nd = ("millrace_plus.router", "native_diagnostic", "nd")
    ef = ("millrace_plus", "engineering_feedback", "ef")
    cc = ("millrace_plus", "check_contracts", "cc")
    plus_imports = {
        ("accept_role.py", "reconcile_provider_success"): {pw, nd},
        ("engineering_feedback.py", "_plus"): {cc, ef},
        ("init_session.py", "_provider_index"): {pw},
        ("lad_state.py", "_source_path"): {cc},
        ("lad_state.py", "engineering_policy"): {cc, ef},
        ("lad_state.py", "provider_policy"): {pw},
        ("lad_state.py", "shared_target"): {
            ("millrace_plus.lad_service", "validate_shared_target", None),
            ("millrace_plus.local_intake", "IntakeError", None),
            ("millrace_plus.subworkflow_records", "SubworkflowError", None),
        },
        ("prepare_role.py", "_prepare_provider"): {pw},
        ("provider_wait.py", "TYPE_CHECKING"): {
            ("millrace_plus.provider_wait", "Operation", None),
            ("millrace_plus.router.native_diagnostic", "DiagnosticRecord", None),
        },
        ("provider_wait.py", "_record"): {nd},
        ("provider_wait.py", "_service"): {ef},
        ("provider_wait.py", "classify"): {pw, nd},
        ("provider_wait.py", "classify_replay"): {pw, nd},
        ("provider_wait.py", "_pending"): {pw},
        ("provider_wait.py", "due"): {pw, ("millrace_plus.router", "control", None)},
        ("provider_wait.py", "accept_diagnosis"): {pw},
        ("provider_wait.py", "human"): {pw},
        ("session_compat.py", "upgrade_session"): {
            ("millrace_plus.router", "control", "public")
        },
    }
    for script in (destination / "scripts").glob("*.py"):
        tree = ast.parse(script.read_text())
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {item.name.split(".")[0] for item in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
            else:
                continue
            if "millrace_plus" not in roots:
                assert roots <= sys.stdlib_module_names | siblings, (
                    script.name,
                    sorted(roots - sys.stdlib_module_names - siblings),
                )
                continue
            assert isinstance(node, ast.ImportFrom) and roots == {"millrace_plus"}, (
                script.name,
                node.lineno,
            )
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                parent = parents.get(parent)
            if parent is not None:
                scope = parent.name
            else:
                guard = parents.get(node)
                assert (
                    isinstance(guard, ast.If)
                    and isinstance(guard.test, ast.Name)
                    and guard.test.id == "TYPE_CHECKING"
                    and parents.get(guard) is tree
                ), (script.name, node.lineno, "module-level Plus import")
                scope = "TYPE_CHECKING"
            found = {(node.module, item.name, item.asname) for item in node.names}
            expected = plus_imports.get((script.name, scope))
            assert expected is not None and found <= expected, (
                script.name,
                scope,
                sorted(found, key=str),
            )
    # Ordinary isolated Python mode proves a copy with an explicit captured
    # routing-policy/v1 imports only its own modules, without Plus or a
    # service on sys.path. A new stock v2 copy requires installed Plus and
    # refuses before any session write when it is absent.
    import hashlib

    legacy = (
        json.dumps(
            {
                "format": "millrace-lad-routing-policy/v1",
                "revision": 1,
                "presentation_revision": "millrace-lad-presentation/v1",
                "learning": "once-per-packet",
                "provider_recovery": "disabled",
            },
            indent=2,
        )
        + "\n"
    ).encode()
    assert hashlib.sha256(legacy).hexdigest() == (
        "430433a3fca296d11d4a57136c8b8f9ac4cda6c5867ad93da26157d723c83036"
    )
    stock = tmp_path / "stock-v2"
    catalog.init_template("bundled:lad", stock, catalog_home=tmp_path / "catalog-v2")
    (destination / "context/routing-policy.json").write_bytes(legacy)
    for copy, expected in ((destination, "NEW_SESSION"), (stock, "RECONCILE")):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-S",
                str(copy / "scripts/init_session.py"),
                "--project-root",
                "..",
            ],
            cwd=copy / "foreground",
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            check=True,
        )
        reply = json.loads(result.stdout)
        assert reply["outcome"] == expected, reply
    assert "provider_recovery_requires_plus" in reply["summary"]
    assert not (stock / "foreground/lad-state").exists()


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


@pytest.mark.parametrize("role", ["planner", "manager", "learning"])
def test_role_resolves_durable_guidance_and_refs_from_cwd_only(
    tmp_path: Path, role: str
) -> None:
    project = copied(tmp_path)
    if role == "learning":
        learning_ready(project)
        workspace = project / "learning"
    else:
        before_role(project, role)
        assert command(project, "prepare_role.py", role)["outcome"] == "READY"
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
if item["role"]=="manager":
    packet_template=(root/"templates/packet.md").read_bytes()
    assert digest(packet_template)==item["guidance_files"]["templates/packet.md"]
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
@pytest.mark.parametrize("role", ["recon", "manager"])
def test_corrupt_durable_guidance_refuses_without_new_occurrence(
    tmp_path: Path, mutation: str, role: str
) -> None:
    project = copied(tmp_path)
    before_role(project, role)
    command(project, "prepare_role.py", role)
    command(project, "lad_peer.py", role, "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    root = Path(state["occurrences"][oid]["guidance_root"])
    if mutation == "file":
        file = root / (
            "templates/packet.md" if role == "manager" else "context/contracts.md"
        )
        file.write_text(file.read_text() + "Conflicting guidance change.\n")
    else:
        file = root / "manifest.json"
        manifest = json.loads(file.read_bytes())
        manifest["guidance_sha256"] = "0" * 64
        file.write_text(json.dumps(manifest))
    before = (project / "foreground/lad-state/session.json").read_bytes()
    assert command(project, "accept_role.py", role, "PASS")["outcome"] == "REFUSED"
    assert (
        command(project, "init_session.py", "--project-root", "..")["outcome"]
        == "RECONCILE"
    )
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    assert len(role_occurrences(ledger(project), role)) == 1


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
        # Native on_error passes through the provider classifier; without a
        # bridge export it selects the same ordinary replacement preparation.
        "classify_provider_recon",
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
            # Unselected goto and pause.resume targets alike (for example the
            # classifier's unused PROVIDER_WAIT resume at provider_due_recon).
            resume = action.get("pause", {}).get("resume")
            if ("goto" in action and action["goto"] not in plane["nodes"]) or (
                resume is not None and resume not in plane["nodes"]
            ):
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
        "classify_provider_recon",
        "prepare_recon_replacement",
        "recon",
        "accept_recon_pass",
    ]
    assert len(role_occurrences(ledger(project), "recon")) == 2
    assert (
        sum(
            row["replacement_of"] is not None
            for row in role_occurrences(ledger(project), "recon")
        )
        == 1
    )
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
    assert graph["defaults"] == {"timeout_seconds": 1800}
    invocations = [
        node["invoke"]
        for plane in graph["planes"].values()
        for node in plane["nodes"].values()
    ]
    assert {invoke["binding"] for invoke in invocations} == {"command", "claude"}
    commands = [invoke for invoke in invocations if invoke["binding"] == "command"]
    native = [invoke for invoke in invocations if invoke["binding"] == "claude"]
    # Inventory, not a cap: the 96/12 L-R1 graph plus the C3 provider routes
    # (11 roles x 6 command guards and 11 provider-diagnosis native nodes).
    assert len(commands) == 96 + 66 and len(native) == 12 + 11
    assert all(invoke["timeout_seconds"] == 90 for invoke in commands)
    assert all(invoke["timeout_seconds"] == 1800 for invoke in native)
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
    policy = project / "context/provider-policy.json"
    if policy.exists():
        # The rebound diagnosis nodes keep a valid captured diagnosis binding.
        value = json.loads(policy.read_bytes())
        value["troubleshooter_binding"] = "command"
        policy.write_text(json.dumps(value, indent=2) + "\n")
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
        artifact.write_text(
            artifact.read_text()
            + "\nScope addition: "
            + relative
            + " is an explicitly owned relevant asset.\n"
        )
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
        [
            sys.executable,
            "-B",
            "-S",
            "-c",
            program,
            str(project / "foreground"),
            "target/" + relative,
        ],
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
            [
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "fixture baseline",
            ],
        ]:
            child = subprocess.run(
                ["git", "-C", str(target), *args],
                env={
                    "PATH": "/usr/bin:/bin",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                },
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
            "path": relative,
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
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
            "binding": "fixture-binding",
            "facility_version": "fixture-v1",
            "task_id": task_id,
            "parent_task_id": task_parent,
            "terminal_state": state,
            "result_ref": result_ref,
            "collected": not (defect == "uncollected_child" and task_id == "child"),
            "started_at": "2026-10-25T00:00:00+00:00",
            "historylog_ref": history_ref,
        }
        terminal_ref = write("helper-authority/" + task_id + "-terminal.json", terminal)
        tasks.append(
            {
                "task_id": task_id,
                "parent_task_id": task_parent,
                "role": "fixture-helper",
                "work_area": area,
                "model": None,
                "effort": None,
                "result_ref": result_ref,
                "terminal_state": (
                    "running" if defect == "live_task" and task_id == "beta" else state
                ),
                "terminal_observation_ref": terminal_ref,
            }
        )
    if defect == "duplicate_inventory":
        tasks.append(dict(tasks[0]))
    inventory = write(
        "helper-authority/inventory.json",
        {
            "format": "millrace-lad-facility-inventory/v1",
            "binding": "fixture-binding",
            "facility_version": "fixture-v1",
            "parent_task_id": parent,
            "complete": True,
            "tasks": tasks,
        },
    )
    qualification = write(
        "helper-authority/qualification.json",
        {
            "format": "millrace-lad-helper-qualification/v1",
            "scope": "offline_validator_fixture",
            "binding": "fixture-binding",
            "facility_version": "fixture-v1",
            "parent_task_id": parent,
            "inventory_ref": inventory,
        },
    )
    return qualification, tasks


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "empty_self_report",
        "missing_child",
        "wrong_parent",
        "live_task",
        "uncollected_child",
        "duplicate_inventory",
        "forged_terminal",
        "missing_integration",
        "large_helper_log",
    ],
)
def test_helper_inventory_terminal_collection_and_integration_gate(
    tmp_path: Path, defect: str
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    qualification, tasks = helper_authority(project, defect)
    original = (project / "foreground/lad-state/session.json").read_bytes()
    refused = command(
        project,
        "prepare_role.py",
        "recon",
        "--helper-mode",
        "native_helpers",
        "--helper-qualification",
        qualification["path"],
    )
    assert refused["outcome"] == "REFUSED"
    assert "native_helpers_not_qualified" in refused["summary"]
    assert (project / "foreground/lad-state/session.json").read_bytes() == original
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    # The qualification check must precede the existing READY reuse shortcut too.
    refused = command(
        project,
        "prepare_role.py",
        "recon",
        "--helper-mode",
        "native_helpers",
        "--helper-qualification",
        qualification["path"],
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
        "format": "millrace-lad-helper-evidence/v1",
        "occurrence_id": oid,
        "parent_task_id": (
            "wrong-parent" if defect == "wrong_parent" else "fixture-parent"
        ),
        "binding": "fixture-binding",
        "facility_version": "fixture-v1",
        "inventory_ref": qual["inventory_ref"],
        "tasks": tasks,
        "integration_ref": {
            "path": str(integration.relative_to(workspace)),
            "sha256": hashlib.sha256(integration.read_bytes()).hexdigest(),
        },
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
        [
            sys.executable,
            "-B",
            "-S",
            "-c",
            program,
            str(workspace),
            str(fixture_result),
            qualification["path"],
        ],
        cwd=project / "scripts",
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=10,
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
        project,
        "prepare_role.py",
        "planner",
        "--helper-mode",
        "native_helpers",
        "--helper-qualification",
        qualification["path"],
    )
    assert reply["outcome"] == "REFUSED", reply
    assert (project / "foreground/lad-state/session.json").read_bytes() == before


def test_invalid_result_fresh_owner_then_same_repaired_cause_consults(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    owners = []
    for index, expected in enumerate(
        ("INVALID_RESULT", "INVALID_REPEAT", "INVALID_CONSULT")
    ):
        assert (
            command(
                project,
                "prepare_role.py",
                "recon",
                *(["--replacement"] if index else []),
            )["outcome"]
            == "READY"
        )
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
            assert (
                command(project, "prepare_role.py", "troubleshooter")["outcome"]
                == "READY"
            )
            command(project, "lad_peer.py", "troubleshooter", "clean")
            assert (
                command(
                    project,
                    "accept_role.py",
                    "troubleshooter",
                    "PASS_PREREQUISITE_REPAIRED",
                )["outcome"]
                == "RESUME_RECON"
            )
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
        assert (
            command(
                project,
                "prepare_role.py",
                "builder",
                *(["--replacement"] if index else []),
            )["outcome"]
            == "READY"
        )
        command(project, "lad_peer.py", "builder", "clean", "ERROR")
        state = ledger(project)
        oid = state["active"]["foreground"]
        file = project / "foreground/lad-results" / oid / "result.json"
        result = json.loads(file.read_bytes())
        result["data"]["failure"]["operation"] = (
            "read fixture-prerequisite"
            if index == 0 or same_cause
            else "other local check"
        )
        file.write_text(json.dumps(result))
        reply = command(project, "accept_role.py", "builder", "ERROR")
        assert reply["outcome"] == (
            "CONSULTANT" if index and same_cause else "TROUBLESHOOTER"
        ), reply
        if index == 0:
            assert (
                command(project, "prepare_role.py", "troubleshooter")["outcome"]
                == "READY"
            )
            prerequisite.write_text('{"available":true}')
            command(project, "lad_peer.py", "troubleshooter", "clean")
            assert (
                command(
                    project,
                    "accept_role.py",
                    "troubleshooter",
                    "PASS_PREREQUISITE_REPAIRED",
                )["outcome"]
                == "RESUME_BUILDER"
            )
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
    request = (
        "Original required functions:\n"
        + "\n".join(name + "() returns 42" for name in names)
        + "\n"
    ).encode()
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
        assert (
            command(project, "accept_role.py", "builder", "PASS")["outcome"]
            == "CHECKER"
        )
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
        ["init", "--quiet"],
        ["add", "--", ".gitignore"],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture ignore baseline",
        ],
        ["check-ignore", ".env"],
    ]:
        child = subprocess.run(
            ["git", "-C", str(target), *args],
            env={
                "PATH": "/usr/bin:/bin",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
            capture_output=True,
            timeout=10,
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
        project,
        "prepare_role.py" if entry == "prepare" else "accept_role.py",
        "updater",
        *([] if entry == "prepare" else ["PASS"]),
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
    assert (
        observation_file.read_bytes()
        == json.dumps(
            observation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        + b"\n"
    )
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


@pytest.mark.parametrize(
    "damage",
    [
        "missing_backup",
        "corrupt_backup",
        "backup_symlink",
        "projection_symlink",
        "other_artifact",
        "outside_source",
        "wrong_owner",
    ],
)
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
        artifact = next(
            row for row in checker_result["artifacts"] if row["path"] == "review.md"
        )
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
    program = """
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
"""
    child = subprocess.run(
        [
            sys.executable,
            "-B",
            "-S",
            "-c",
            program,
            str(foreground),
            window,
            baseline["path"],
        ],
        cwd=project / "scripts",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 81, child.stdout + child.stderr
    assert (
        command(project, "prepare_role.py", "updater")["outcome"] == "BASELINE_TAMPER"
    )
    after = ledger(project)
    assert after["events"] == state["events"]
    assert after["accepted"] == state["accepted"]
    assert after["baselines"] == state["baselines"]
    assert after["continuation"]["node"] == "prepare_consultant"
    assert projection.read_bytes() == original
    assert target.stat().st_mtime_ns == modified
    assert (
        len(list((foreground / "lad-state/baseline-pins").glob("*-restoration.json")))
        == 1
    )
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
    observations = list(
        (foreground / "lad-state/baseline-pins").glob("*-restoration.json")
    )
    assert len(observations) == 1
    observed = json.loads(observations[0].read_bytes())
    assert (
        b"Unauthorized new expectation"
        in (foreground / observed["observed"]["path"]).read_bytes()
    )
    nodes = [row["node"] for row in view["invocations"]]
    first_accept = nodes.index("accept_updater_pass")
    assert nodes[first_accept + 1 : first_accept + 5] == [
        "prepare_consultant",
        "consultant",
        "accept_consultant_repair_execution",
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
        assert (
            command(project, "prepare_role.py", role, "--replacement")["outcome"]
            == "READY"
        )
    foreground = project / "foreground"
    state = ledger(project)
    oid = state["active"]["foreground"]
    occurrence = state["occurrences"][oid]
    occurrence["helper_mode"] = "native_helpers"
    occurrence["helper_qualification"] = {
        "path": "fixture-only.json",
        "sha256": "f" * 64,
    }
    occurrence.pop("input_sha256")
    occurrence["input_sha256"] = hashlib.sha256(
        json.dumps(
            occurrence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    output = foreground / "lad-state/occurrences" / oid
    (output / "ready.json").unlink()
    (output / "input.json").write_text(json.dumps(occurrence))
    (foreground / "lad-state/active-role.json").write_text(json.dumps(occurrence))
    state.pop("integrity_sha256")
    state["integrity_sha256"] = hashlib.sha256(
        json.dumps(
            state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
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
    assert (
        "baseline_owner_mismatch"
        if tampered_baseline
        else "native_helpers_not_qualified"
    ) in reply["summary"]
    assert not (output / "ready.json").exists()
    assert session.read_bytes() == before
    if tampered_baseline:
        assert baseline.read_bytes().startswith(b"Bad baseline;")
        assert not list(
            (foreground / "lad-state/baseline-pins").glob("*-restoration.json")
        )


def test_accepted_replacement_owner_cannot_rebase_on_stale_pending(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    original = ledger(project)["active"]["foreground"]
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "READY"
    )
    replacement = ledger(project)["active"]["foreground"]
    assert replacement != original
    command(project, "lad_peer.py", "recon", "clean")
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    state = ledger(project)
    assert state["pending"]["occurrence_id"] == original
    assert state["pending"]["target"] == "recon"
    assert (
        state["occurrences"][original]["lineage"]
        == (state["occurrences"][replacement]["lineage"])
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
        assert (
            command(project, "prepare_role.py", "updater")["outcome"]
            == "BASELINE_TAMPER"
        )
        saved_pending = ledger(project)["pending"]
        restoration = saved_pending["restoration"]
        observed = json.loads((foreground / restoration["path"]).read_bytes())
        forbidden += [
            restoration["path"].encode(),
            observed["observed"]["path"].encode(),
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
    assert command(project, "accept_role.py", "consultant", selected_outcome)[
        "outcome"
    ] == ("CHECKER" if recovery == "baseline" else "PLANNER")
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
                evidence.write_bytes(
                    evidence.read_bytes()
                    + b"\n"
                    + marker.encode()
                    + b"\n"
                    + consultant.encode()
                )
                artifact["sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
            if role == "manager":
                value["data"]["plan"][0]["input_refs"] = [
                    {
                        "occurrence_id": consultant,
                        "sha256": accepted_consultant["sha256"],
                    }
                ]
            role_file.write_text(json.dumps(value))
            assert command(project, "accept_role.py", role, "PASS")["outcome"] == route
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
        "id",
        "lineage",
        "criteria",
        "depends_on",
        "paths",
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
            payloads.append(
                (workspace / Path(ref["path"]).parent / artifact["path"]).read_bytes()
            )
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
    item["helper_qualification"] = {"path": "fixture-only.json", "sha256": "f" * 64}
    item.pop("input_sha256")
    item["input_sha256"] = hashlib.sha256(
        json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
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
    state["integrity_sha256"] = hashlib.sha256(
        json.dumps(
            state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
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
    assert not list((foreground / "lad-state/baseline-pins").glob("*-restoration.json"))


# --- B5A: finite presentation tolerance and one nonrecursive Learning assessment


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def lad_module(name: str):
    """Load a bundled stdlib-only LAD script module by file, leaking no names."""
    import importlib.util

    names = tuple(dict.fromkeys(("lad_state", name)))
    saved = {key: sys.modules.pop(key, None) for key in names}
    try:
        for key in names:
            spec = importlib.util.spec_from_file_location(
                key, LAD / "scripts" / (key + ".py")
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[key] = module
            spec.loader.exec_module(module)
        return sys.modules[name]
    finally:
        for key, module in saved.items():
            sys.modules.pop(key, None)
            if module is not None:
                sys.modules[key] = module


SID, OID, OTHER = (str(uuid.uuid4()) for _ in range(3))
AUTHORITY = {
    "session_id": SID,
    "occurrence_id": OID,
    "role": "builder",
    "lineage": "answer",
    "outcome": "PASS",
    "input_sha256": "a" * 64,
    "guidance_sha256": "b" * 64,
    "sources": {"c" * 64, "d" * 64},
    "baselines": {None},
}
BODY = "## Evidence\nRan check.\n## Outcome\nPASS\n"
LEGAL_VIEWS = {
    "canonical": BODY,
    "short_plain": "Evidence: ok\nOutcome: PASS",
    "bom_crlf_blank": "﻿\r\n\r\n## evidence ##\r\nok\r\n### OUTCOME\r\nPASS\r\n\r\n",
    "legacy_suffix": "## Evidence and observed checks\nran x\n## Outcome\nPASS\n",
    "bold_list": "- **Evidence:** ran x\n* **Outcome**: PASS\n",
    "levels": "# Evidence\nx\n###### Outcome ###\nPASS\n",
    "inline_heading": "## Evidence: ran x\n## Outcome: PASS\n",
    "identical_duplicate": BODY + "## Evidence\nRan check.\n",
    "predecessor_refs": (
        f"## Evidence\nFixer occurrence: {OTHER}\nSee {OTHER} in prose.\n"
        f"> Occurrence: {OTHER}\n```\nSession: {OTHER}\n## Outcome\n```\n"
        "## Outcome\nPASS\n"
    ),
    "explicit_identity": (
        f"Session: {SID}\nOccurrence: `{OID.upper()}`\nRole: Builder\n"
        "Lineage: answer\n" + BODY
    ),
    "combined_slash": (
        f"Session/occurrence/role/lineage: `{SID} / {OID} / builder / answer`\n" + BODY
    ),
    "combined_keys": (
        f"session/occurrence/role/lineage: lineage=answer, role=builder, "
        f"occurrence={OID}, session={SID}\n" + BODY
    ),
    "template_placeholders": (
        "Session/occurrence/role/lineage: `<exact prepared input>`\n"
        "Input/source/guidance/baseline identities: `<exact values>`\n" + BODY
    ),
    "digest_identity": (
        f"Input/source/guidance/baseline identities: {'a' * 64} / {'d' * 64} / "
        f"{'b' * 64} / null\n" + BODY
    ),
    "restored_history": (
        f"# builder retained summary\nSession: {SID}\nOccurrence: {OID}\n" + BODY
    ),
    # Identities inside list-item code are references; labels outside still count.
    "list_fence_unrelated_identity": (
        BODY + f"- ```\n  Occurrence: {OTHER}\n  Session: {OTHER}\n  ```\n"
    ),
    "bullet_labels_beside_list_fence": (
        "- **Evidence:** ran x\n- ```\n  ## Outcome\n  fake\n  ```\n"
        "* **Outcome**: PASS\n"
    ),
}
CORRUPT_VIEWS = {
    "fenced_only": ("```\n" + BODY + "```\n", "presentation_evidence_fenced_only"),
    "list_fence_backtick": (
        "- ```\n  ## Evidence\n  quoted example only\n  ## Outcome\n  PASS\n  ```\n",
        "presentation_evidence_fenced_only",
    ),
    "list_fence_tilde": (
        "* ~~~text\n  Evidence: x\n  Outcome: PASS\n  ~~~\n",
        "presentation_evidence_fenced_only",
    ),
    "nested_list_fence": (
        "1. - ```\n     ## Evidence\n     x\n     ## Outcome\n     PASS\n     ```\n",
        "presentation_evidence_fenced_only",
    ),
    "list_quote": (
        "- > ## Evidence\n  > x\n## Outcome\nPASS\n",
        "presentation_evidence_quoted_only",
    ),
    "list_fence_wrong_identity_outside": (
        f"- ```\n  Occurrence: {OID}\n  ```\nOccurrence: {OTHER}\n" + BODY,
        "presentation_identity_occurrence",
    ),
    "quoted_only": (
        "> ## Evidence\n> x\n## Outcome\nPASS\n",
        "presentation_evidence_quoted_only",
    ),
    "midline": (
        "Summary ## Evidence checked.\n## Outcome\nPASS\n",
        "presentation_evidence_ambiguous_midline",
    ),
    "prose_alias": (
        "## Notes\nthe evidence is good\n## Outcome\nPASS\n",
        "presentation_evidence_missing",
    ),
    "empty_heading": (
        "## Evidence\n\n## Outcome\nPASS\n",
        "presentation_evidence_empty",
    ),
    "empty_plain": ("## Evidence\nx\nOutcome:\n", "presentation_outcome_empty"),
    "conflicting": (
        BODY + "## Evidence\nOther.\n",
        "presentation_evidence_conflicting",
    ),
    "wrong_occurrence": (
        f"Occurrence: {OTHER}\n" + BODY,
        "presentation_identity_occurrence",
    ),
    "wrong_session": (f"Session: {OTHER}\n" + BODY, "presentation_identity_session"),
    "wrong_role": ("Role: checker\n" + BODY, "presentation_identity_role"),
    "wrong_lineage": ("Lineage: other\n" + BODY, "presentation_identity_lineage"),
    "combined_wrong_part": (
        f"Session/occurrence/role/lineage: {SID} / {OID} / checker / answer\n" + BODY,
        "presentation_identity_role",
    ),
    "duplicate_conflicting_identity": (
        f"Occurrence: {OID}\nOccurrence: {OTHER}\n" + BODY,
        "presentation_identity_occurrence",
    ),
    "malformed_scalar": (
        f"Session: {SID} (current)\n" + BODY,
        "presentation_malformed_identity",
    ),
    "malformed_prose_role": (
        "Role: the builder writes code\n" + BODY,
        "presentation_malformed_identity",
    ),
    "malformed_parts": (
        f"Session/occurrence/role/lineage: {SID} / {OID} / x\n" + BODY,
        "presentation_malformed_identity",
    ),
    "malformed_keys": (
        f"Session/occurrence/role/lineage: session={SID} role=builder\n" + BODY,
        "presentation_malformed_identity",
    ),
    "wrong_digest": (
        f"Input/source/guidance/baseline identities: {'e' * 64} / {'d' * 64} / "
        f"{'b' * 64} / null\n" + BODY,
        "presentation_identity_input",
    ),
    "midline_header_delimiter": (
        BODY + "<!-- millrace-lad-generated-header/v1 -->\n",
        "presentation_generated_header_conflict",
    ),
}


@pytest.mark.parametrize("case", sorted(LEGAL_VIEWS))
def test_presentation_legal_views_are_finite_and_idempotent(case: str) -> None:
    presentation = lad_module("presentation")
    raw = LEGAL_VIEWS[case].encode()
    view, applied = presentation.view(raw, AUTHORITY)
    assert raw == LEGAL_VIEWS[case].encode()  # The raw input is never edited.
    again, _ = presentation.view(view, AUTHORITY)
    assert again == view and view.endswith(b"\n")
    assert b"\r" not in view and not view.startswith("﻿".encode())
    assert applied["rules"] == sorted(set(applied["rules"]))
    explicit = case in {"explicit_identity", "combined_slash", "combined_keys"}
    assert applied["generated"] == (
        []
        if explicit
        else (
            ["role", "lineage", "outcome"]
            if case == "restored_history"
            else ["session_id", "occurrence_id", "role", "lineage", "outcome"]
        )
    )
    assert view.startswith(presentation.BEGIN.encode()) == (not explicit)
    if case == "bom_crlf_blank":
        assert {
            "utf8_bom_removed",
            "line_endings_lf",
            "outer_blank_lines_trimmed",
            "label_canonicalized",
        } <= set(applied["rules"])
    if case == "identical_duplicate":
        assert "identical_duplicate_evidence" in applied["rules"]


@pytest.mark.parametrize("case", sorted(CORRUPT_VIEWS))
def test_presentation_corrupt_views_refuse(case: str) -> None:
    presentation = lad_module("presentation")
    raw, code = CORRUPT_VIEWS[case]
    with pytest.raises(presentation.LadError) as refused:
        presentation.view(raw.encode(), AUTHORITY)
    assert str(refused.value) == code


def test_presentation_header_and_encoding_refusals() -> None:
    presentation = lad_module("presentation")
    forged = presentation.header(dict(AUTHORITY, occurrence_id=OTHER)) + BODY
    for raw, code in (
        (forged.encode(), "presentation_generated_header_conflict"),
        (b"\xff" + BODY.encode(), "presentation_utf8_required"),
    ):
        with pytest.raises(presentation.LadError) as refused:
            presentation.view(raw, AUTHORITY)
        assert str(refused.value) == code
    # The exact generated header is recognized once, never duplicated.
    view, _ = presentation.view(BODY.encode(), AUTHORITY)
    assert view.count(presentation.BEGIN.encode()) == 1
    assert presentation.view(view, AUTHORITY)[0] == view


def rewrite_artifact(project: Path, plane: str, name: str, raw: bytes) -> Path:
    """Replace one prepared artifact and pin its exact raw digest in result.json."""
    oid = ledger(project)["active"][plane]
    output = project / plane / "lad-results" / oid
    (output / name).parent.mkdir(parents=True, exist_ok=True)
    (output / name).write_bytes(raw)
    file = output / "result.json"
    result = json.loads(file.read_bytes())
    for row in result["artifacts"]:
        if row["path"] == name:
            row["sha256"] = digest(raw)
    file.write_bytes(canonical(result) + b"\n")
    return file


def presentation_file(project: Path, plane: str, oid: str) -> Path:
    raw = (project / plane / "lad-results" / oid / "result.json").read_bytes()
    return project / plane / "lad-state/presentation" / oid / (digest(raw) + ".json")


@pytest.mark.parametrize("variant", ["bom_crlf_blank", "short_plain", "bold_list"])
def test_presentation_preserves_raw_evidence(tmp_path: Path, variant: str) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    command(project, "lad_peer.py", "recon", "clean")
    oid = ledger(project)["active"]["foreground"]
    raw = LEGAL_VIEWS[variant].encode()
    assert len(raw.strip()) < 100 or variant == "bom_crlf_blank"
    result_file = rewrite_artifact(project, "foreground", "intake.md", raw)
    retained = result_file.read_bytes()
    reply = command(project, "accept_role.py", "recon", "PASS")
    assert reply["outcome"] == "PLANNER", reply
    state = ledger(project)
    artifact = project / "foreground/lad-results" / oid / "intake.md"
    assert artifact.read_bytes() == raw and result_file.read_bytes() == retained
    assert {"path": "intake.md", "sha256": digest(raw)} in state["accepted"][oid][
        "result"
    ]["artifacts"]
    record_file = presentation_file(project, "foreground", oid)
    record = json.loads(record_file.read_bytes())
    presentation = lad_module("presentation")
    row = next(item for item in record["artifacts"] if item["path"] == "intake.md")
    assert row["sha256"] == digest(raw)
    occurrence = state["occurrences"][oid]
    expected_view, applied = presentation.view(
        raw,
        {
            "session_id": occurrence["session_id"],
            "occurrence_id": oid,
            "role": "recon",
            "lineage": occurrence["lineage"],
            "outcome": "PASS",
            "input_sha256": occurrence["input_sha256"],
            "guidance_sha256": occurrence["guidance_sha256"],
            "sources": {occurrence["source_sha256"]},
            "baselines": {None},
        },
    )
    assert row["view_sha256"] == digest(expected_view) != digest(raw)
    assert row["rules"] == applied["rules"]
    assert record["result"] == {
        "path": f"lad-results/{oid}/result.json",
        "sha256": digest(retained),
    }
    assert record["policy"]["path"] == "context/routing-policy.json"
    assert not any(
        path.name.endswith(".view") or "presentation" in path.name
        for path in (project / "foreground/lad-results" / oid).rglob("*")
    )
    # Exact replay recomputes the same record and mutates nothing.
    session = (project / "foreground/lad-state/session.json").read_bytes()
    record_raw = record_file.read_bytes()
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    assert (project / "foreground/lad-state/session.json").read_bytes() == session
    assert record_file.read_bytes() == record_raw
    # Missing or conflicting accepted provenance refuses on reload and replay.
    record_file.unlink()
    refused = command(project, "prepare_role.py", "planner")
    assert refused["outcome"] == "REFUSED"
    assert "accepted_presentation_missing" in refused["summary"]
    record_file.write_bytes(record_raw)
    altered = dict(
        record,
        artifacts=[dict(item, view_sha256="0" * 64) for item in record["artifacts"]],
    )
    record_file.write_bytes(canonical(altered) + b"\n")
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "REFUSED"
    record_file.write_bytes(
        canonical(
            dict(
                record,
                result={
                    "path": record["result"]["path"],
                    "sha256": "0" * 64,
                },
            )
        )
        + b"\n"
    )
    assert (
        "accepted_presentation_conflict"
        in command(project, "prepare_role.py", "planner")["summary"]
    )
    record_file.write_bytes(record_raw)
    assert command(project, "prepare_role.py", "planner")["outcome"] == "READY"


RECORD_MUTATIONS = {
    "view_sha256": lambda r: r["artifacts"][0].update(view_sha256="0" * 64),
    "rules": lambda r: r["artifacts"][0].update(rules=["forged"]),
    "generated": lambda r: r["artifacts"][0].update(generated=["forged"]),
    "policy_sha256": lambda r: r["policy"].update(sha256="0" * 64),
    "policy_path": lambda r: r["policy"].update(path="../forged"),
    "policy_revision": lambda r: r["policy"].update(revision=True),
    "authority_path": lambda r: r["authority"].update(path="../forged"),
    "authority_sha256": lambda r: r["authority"].update(sha256="0" * 64),
}
LOAD_PROGRAM = """import json, sys
from pathlib import Path
import lad_state
try:
    lad_state.load(Path(sys.argv[1]))
except lad_state.LadError as error:
    print(json.dumps({"outcome": "REFUSED", "diagnostic": str(error)}))
else:
    print(json.dumps({"outcome": "LOADED"}))
"""


def ordinary_load(project: Path) -> dict:
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", LOAD_PROGRAM, str(project / "foreground")],
        cwd=project / "scripts",
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": str(Path(sys.executable).parent), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert child.returncode == 0, child.stdout + child.stderr
    return json.loads(child.stdout)


@pytest.mark.parametrize("mutation", [*sorted(RECORD_MUTATIONS), "input_projection"])
def test_ordinary_load_and_next_prepare_verify_complete_presentation_record(
    tmp_path: Path, mutation: str
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    step(project, "recon")
    state = ledger(project)
    oid = state["active"]["foreground"]
    occurrence = state["occurrences"][oid]
    record_file = presentation_file(project, "foreground", oid)
    original = record_file.read_bytes()
    record = json.loads(original)
    # The record pins the occurrence's captured policy and exact input projection.
    assert record["policy"] == {
        "path": "context/routing-policy.json",
        "revision": 2,
        "sha256": occurrence["guidance_files"]["context/routing-policy.json"],
    }
    projection = project / "foreground/lad-state/occurrences" / oid / "input.json"
    assert record["authority"] == {
        "path": f"lad-state/occurrences/{oid}/input.json",
        "sha256": digest(projection.read_bytes()),
    }
    assert ordinary_load(project) == {"outcome": "LOADED"}
    session = learning_state_bytes(project)
    if mutation == "input_projection":
        # Same parsed occurrence, different bytes: not the pinned projection.
        restore, target = projection.read_bytes(), projection
        projection.write_text(json.dumps(occurrence, indent=1))
    else:
        restore, target = original, record_file
        RECORD_MUTATIONS[mutation](record)
        record_file.write_bytes(canonical(record) + b"\n")
    assert ordinary_load(project) == {
        "outcome": "REFUSED",
        "diagnostic": "accepted_presentation_conflict",
    }
    refused = command(project, "prepare_role.py", "planner")
    assert refused["outcome"] == "REFUSED"
    assert "accepted_presentation_conflict" in refused["summary"]
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "REFUSED"
    assert learning_state_bytes(project) == session
    assert len(role_occurrences(ledger(project), "planner")) == 0
    # Restoring the exact retained bytes restores ordinary progress.
    target.write_bytes(restore)
    assert ordinary_load(project) == {"outcome": "LOADED"}
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    assert command(project, "prepare_role.py", "planner")["outcome"] == "READY"


def test_presentation_check_does_not_depend_on_how_lad_state_was_loaded(
    tmp_path: Path,
) -> None:
    """A run_path-loaded copy validates with its own sibling view module."""
    project = copied(tmp_path)
    bootstrap(project)
    step(project, "recon")
    oid = ledger(project)["active"]["foreground"]
    program = """import json, runpy, sys
module = runpy.run_path(sys.argv[1])
try:
    module["load"](__import__("pathlib").Path(sys.argv[2]))
except module["LadError"] as error:
    print(json.dumps({"outcome": "REFUSED", "diagnostic": str(error)}))
else:
    print(json.dumps({"outcome": "LOADED"}))
"""

    def load_by_path() -> dict:
        child = subprocess.run(
            [
                sys.executable,
                "-B",
                "-S",
                "-c",
                program,
                str(project / "scripts/lad_state.py"),
                str(project / "foreground"),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": str(Path(sys.executable).parent)},
        )
        assert child.returncode == 0, child.stdout + child.stderr
        return json.loads(child.stdout)

    assert load_by_path() == {"outcome": "LOADED"}
    record_file = presentation_file(project, "foreground", oid)
    original = record_file.read_bytes()
    record = json.loads(original)
    record["artifacts"][0]["rules"] = ["forged"]
    record_file.write_bytes(canonical(record) + b"\n")
    assert load_by_path() == {
        "outcome": "REFUSED",
        "diagnostic": "accepted_presentation_conflict",
    }
    record_file.write_bytes(original)
    assert load_by_path() == {"outcome": "LOADED"}


def test_presentation_record_crash_reuses_and_conflict_refuses(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    command(project, "lad_peer.py", "recon", "clean")
    oid = ledger(project)["active"]["foreground"]
    record_file = presentation_file(project, "foreground", oid)
    program = """
import sys
from pathlib import Path
import accept_role
class FixtureCrash(BaseException):
    pass
def save(root, state):
    raise FixtureCrash()
accept_role.save = save
try:
    accept_role.accept(Path(sys.argv[1]), Path(sys.argv[1]), "recon", "PASS")
except FixtureCrash:
    sys.exit(81)
raise AssertionError("fault window not reached")
"""
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", program, str(project / "foreground")],
        cwd=project / "scripts",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 81, child.stdout + child.stderr
    published = record_file.read_bytes()
    assert oid not in ledger(project)["accepted"]
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    assert record_file.read_bytes() == published
    # A conflicting preexisting record for a fresh result is never overwritten.
    project2 = copied(tmp_path / "conflict")
    bootstrap(project2)
    command(project2, "prepare_role.py", "recon")
    command(project2, "lad_peer.py", "recon", "clean")
    oid2 = ledger(project2)["active"]["foreground"]
    forged = presentation_file(project2, "foreground", oid2)
    forged.parent.mkdir(parents=True)
    forged.write_bytes(b'{"format":"millrace-lad-presentation/v1"}\n')
    assert command(project2, "accept_role.py", "recon", "PASS")["outcome"] == "REFUSED"
    assert oid2 not in ledger(project2)["accepted"]
    assert forged.read_bytes() == b'{"format":"millrace-lad-presentation/v1"}\n'


@pytest.mark.parametrize(
    "case", ["wrong_occurrence", "fenced_only", "empty_heading", "list_fence_backtick"]
)
def test_presentation_refusal_uses_invalid_result_recovery(
    tmp_path: Path, case: str
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    command(project, "lad_peer.py", "recon", "clean")
    oid = ledger(project)["active"]["foreground"]
    raw = CORRUPT_VIEWS[case][0].replace(OTHER, str(uuid.uuid4())).encode()
    result_file = rewrite_artifact(project, "foreground", "intake.md", raw)
    rejected = result_file.read_bytes()
    reply = command(project, "accept_role.py", "recon", "PASS")
    assert reply["outcome"] == "INVALID_RESULT", reply
    state = ledger(project)
    assert oid not in state["accepted"]
    assert state["replacement_intents"][oid]["diagnostic"] == CORRUPT_VIEWS[case][1]
    assert result_file.read_bytes() == rejected
    assert not (project / "foreground/lad-state/presentation" / oid).exists()


def manager_packet(project: Path, packet: str) -> tuple[str, bytes]:
    before_role(project, "manager")
    assert command(project, "prepare_role.py", "manager")["outcome"] == "READY"
    command(project, "lad_peer.py", "manager", "clean")
    raw = (
        "# Packet draft\n" + packet + "\n## Evidence\nWhole-plan check is complete.\n"
        "## Outcome\nPASS\n"
    ).encode()
    rewrite_artifact(project, "foreground", "packet.md", raw)
    return ledger(project)["active"]["foreground"], raw


@pytest.mark.parametrize(
    "packet",
    [
        "Development check: `python -c 'import answer; assert answer.answer() == 42'`",
        "- **Development check:** load answer and assert 42",
        "development CHECK: load answer and assert 42",
        "## Development check\nLoad answer and assert 42; no future work needed.",
        "Development check: same check\nDevelopment check: same check",
    ],
)
def test_development_check_label_tolerance(tmp_path: Path, packet: str) -> None:
    project = copied(tmp_path)
    oid, raw = manager_packet(project, packet)
    reply = command(project, "accept_role.py", "manager", "PASS")
    assert reply["outcome"] == "BUILDER", reply
    plan = ledger(project)["plan"]
    assert plan[0]["markdown"] == "packet.md"
    assert plan[0]["markdown_sha256"] == digest(raw)  # Raw draft bytes stay pinned.
    assert plan[0]["owner_occurrence"] == oid


@pytest.mark.parametrize(
    ("packet", "code"),
    [
        ("No check is described here.", "packet_development_check_required"),
        (
            "We ran it. Development check: load answer",
            "packet_development_check_ambiguous_midline",
        ),
        (
            "```\nDevelopment check: load answer\n```",
            "packet_development_check_fenced_only",
        ),
        (
            "- ```\n  Development check: fake command only\n  ```",
            "packet_development_check_fenced_only",
        ),
        (
            "1. ~~~\n   Development check: fake command only\n   ~~~",
            "packet_development_check_fenced_only",
        ),
        ("Development check:\n- load answer", "packet_development_check_empty"),
        (
            "Development check: one\nDevelopment check: two",
            "packet_development_check_conflicting",
        ),
    ],
)
def test_development_check_absent_or_ambiguous_recovers_without_plan(
    tmp_path: Path, packet: str, code: str
) -> None:
    project = copied(tmp_path)
    oid, _ = manager_packet(project, packet)
    reply = command(project, "accept_role.py", "manager", "PASS")
    assert reply["outcome"] == "INVALID_RESULT", reply
    state = ledger(project)
    assert state["plan"] == [] and oid not in state["accepted"]
    assert state["replacement_intents"][oid]["diagnostic"] == code


def test_development_check_tolerance_keeps_plan_semantic_guards(tmp_path: Path) -> None:
    project = copied(tmp_path)
    manager_packet(project, "Development check: load answer and assert 42")
    oid = ledger(project)["active"]["foreground"]
    file = project / "foreground/lad-results" / oid / "result.json"
    result = json.loads(file.read_bytes())
    result["data"]["plan"][0]["paths"] = ["../escape.py"]
    file.write_bytes(canonical(result) + b"\n")
    reply = command(project, "accept_role.py", "manager", "PASS")
    assert reply["outcome"] == "REFUSED" and "packet_path_escape" in reply["summary"]
    assert ledger(project)["plan"] == []


def checker_variant(project: Path, *, freeze_from_view: bool = False) -> bytes:
    before_role(project, "checker")
    assert command(project, "prepare_role.py", "checker")["outcome"] == "READY"
    command(project, "lad_peer.py", "checker", "clean")
    state = ledger(project)
    oid = state["active"]["foreground"]
    output = project / "foreground/lad-results" / oid
    raw = (output / "baseline.md").read_bytes().replace(b"\n", b"\r\n")
    raw = raw.replace(b"## Evidence", b"**Evidence:**")
    rewrite_artifact(project, "foreground", "baseline.md", raw)
    pinned = raw if not freeze_from_view else raw.replace(b"\r\n", b"\n")
    freeze = json.loads((output / "baseline-freeze.json").read_bytes())
    freeze["sha256"] = digest(pinned)
    freeze_raw = canonical(freeze) + b"\n"
    file = rewrite_artifact(project, "foreground", "baseline-freeze.json", freeze_raw)
    result = json.loads(file.read_bytes())
    result["data"]["baseline_freeze"] = {
        "path": "baseline-freeze.json",
        "sha256": digest(freeze_raw),
    }
    file.write_bytes(canonical(result) + b"\n")
    return raw


def test_presentation_cannot_relax_baseline_authority(tmp_path: Path) -> None:
    project = copied(tmp_path)
    raw = checker_variant(project)
    oid = ledger(project)["active"]["foreground"]
    assert command(project, "accept_role.py", "checker", "PASS")["outcome"] == "UPDATER"
    state = ledger(project)
    baseline = state["baselines"]["answer"]
    foreground = project / "foreground"
    assert baseline["sha256"] == digest(raw) and baseline["occurrence_id"] == oid
    backup = foreground / ("lad-state/baseline-pins/" + oid + ".md")
    assert backup.read_bytes() == raw  # The raw CRLF bytes, never the LF view.
    # A freeze over the normalized view is not the requirement-side raw identity.
    project2 = copied(tmp_path / "view-freeze")
    checker_variant(project2, freeze_from_view=True)
    reply = command(project2, "accept_role.py", "checker", "PASS")
    assert (
        reply["outcome"] == "REFUSED" and "baseline_freeze_mismatch" in reply["summary"]
    )
    assert ledger(project2)["baselines"] == {}
    assert not (project2 / "foreground/lad-state/baseline-pins").exists()
    # A worker hash naming the view instead of raw bytes is integrity failure.
    project3 = copied(tmp_path / "view-digest")
    checker_variant(project3)
    oid3 = ledger(project3)["active"]["foreground"]
    file = project3 / "foreground/lad-results" / oid3 / "result.json"
    result = json.loads(file.read_bytes())
    for row in result["artifacts"]:
        if row["path"] == "baseline.md":
            row["sha256"] = digest(raw.replace(b"\r\n", b"\n"))
    file.write_bytes(canonical(result) + b"\n")
    reply = command(project3, "accept_role.py", "checker", "PASS")
    assert reply["outcome"] == "INVALID_RESULT"
    assert ledger(project3)["replacement_intents"][oid3]["diagnostic"] == (
        "incomplete_or_wrong_occurrence_artifact"
    )
    assert ledger(project3)["baselines"] == {}


def learning_fail(project: Path, diagnostic: str) -> dict:
    return command_at(
        project, "learning", "learning_seam.py", "fail", "--diagnostic", diagnostic
    )


def learning_ack(project: Path, sequence: int = 1) -> str:
    acknowledgement = command_at(project, "learning", "learning_seam.py", "emit_ack")
    assert acknowledgement["outcome"] == "ACK", acknowledgement
    seam = seam_envelope(project, "foreground", acknowledgement, sequence)
    return command_at(
        project, "foreground", "learning_seam.py", "accept_ack", seam=seam
    )["outcome"]


def test_learning_once_after_second_pair(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    for role in ("recon", "planner", "manager"):
        step(project, role)
    step(project, "builder", scenario="unresolved")
    assert step(project, "checker") == "FIXER"
    step(project, "fixer", scenario="unresolved")
    assert step(project, "doublechecker") == "FIXER"
    assert len(ledger(project)["pairs"]["answer"]) == 1
    # An invalid Fixer result is not an evaluated pair.
    command(project, "prepare_role.py", "fixer")
    command(project, "lad_peer.py", "fixer", "unresolved")
    invalid = ledger(project)["active"]["foreground"]
    file = project / "foreground/lad-results" / invalid / "result.json"
    file.write_bytes(
        file.read_bytes().replace(b'"lineage":"answer"', b'"lineage":"other"')
    )
    assert command(project, "accept_role.py", "fixer", "PASS")["outcome"] == (
        "INVALID_RESULT"
    )
    assert len(ledger(project)["pairs"]["answer"]) == 1
    assert (
        command(project, "prepare_role.py", "fixer", "--replacement")["outcome"]
        == "READY"
    )
    command(project, "lad_peer.py", "fixer", "unresolved")
    assert command(project, "accept_role.py", "fixer", "PASS")["outcome"] == (
        "DOUBLECHECKER"
    )
    assert step(project, "doublechecker") == "ASSESS"
    state = ledger(project)
    assert len(state["pairs"]["answer"]) == 2 and state["pending"]["target"] == (
        "troubleshooter"
    )
    # Exact replay of the triggering acceptance returns ASSESS, adding no pair.
    snapshot = (project / "foreground/lad-state/session.json").read_bytes()
    assert (
        command(project, "accept_role.py", "doublechecker", "FINDINGS_ACTIONABLE")[
            "outcome"
        ]
        == "ASSESS"
    )
    assert (project / "foreground/lad-state/session.json").read_bytes() == snapshot
    request = command(project, "learning_seam.py", "emit_request")
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
    # Native Learning error (no result): one failed disposition, then the ACK.
    assert learning_fail(project, "native_completion_failed")["outcome"] == "ACK"
    assert learning_ack(project) == "TROUBLESHOOTER"
    # The resolved review does not reenter Learning for this packet.
    assert step(project, "troubleshooter") == "DOUBLECHECKER"
    assert step(project, "doublechecker") == "UPDATER"
    state = ledger(project)
    assert len(state["pairs"]["answer"]) == 2
    assert len(role_occurrences(state, "learning")) == 1
    assert state["learning"]["answer"]["disposition"] == "failed"
    assert state["request"] is None


def learning_state_bytes(project: Path) -> bytes:
    return (project / "foreground/lad-state/session.json").read_bytes()


@pytest.mark.parametrize(
    "diagnostic", ["native_completion_failed", "invalid_assessment"]
)
def test_learning_failure_ack_replay_and_aftermath(
    tmp_path: Path, diagnostic: str
) -> None:
    project = copied(tmp_path)
    learning_ready(project)
    state = ledger(project)
    oid, request = state["active"]["learning"], state["request"]
    pending = state["pending"]
    if diagnostic == "invalid_assessment":
        command_at(project, "learning", "lad_peer.py", "learning", "clean")
        result = project / "learning/lad-results" / oid / "result.json"
        changed = json.loads(result.read_bytes())
        changed["data"]["pending_route"] = dict(pending, target="consultant")
        result.write_bytes(canonical(changed) + b"\n")
        rejected = result.read_bytes()
        assert (
            command_at(project, "learning", "learning_seam.py", "accept", "PASS")[
                "outcome"
            ]
            == "INVALID_RESULT"
        )
        after = ledger(project)
        assert after["continuation"]["node"] == "record_learning_failure"
        assert after["replacement_intents"][oid]["decision"] == "INVALID_RESULT"
        assert oid not in after["accepted"] and result.read_bytes() == rejected
        # A rejected assessment cannot be retried or re-accepted for this REQUEST.
        assert (
            command_at(project, "learning", "learning_seam.py", "prepare")["outcome"]
            == "REFUSED"
        )
        assert learning_fail(project, "native_completion_failed")["outcome"] == (
            "REFUSED"
        )
    assert learning_fail(project, diagnostic)["outcome"] == "ACK"
    state = ledger(project)
    assert state["learning"]["answer"] == {
        "disposition": "failed",
        "occurrence_id": oid,
        "diagnostic": diagnostic,
    }
    assert state["request"] == dict(request, stage="received")
    assert state["pending"] == pending and "learning" not in state["active"]
    assert state["continuation"]["node"] == "emit_ack"
    # Crash after disposition save: exact replay emits the same ACK, no mutation.
    snapshot = learning_state_bytes(project)
    assert learning_fail(project, diagnostic)["outcome"] == "ACK"
    assert learning_state_bytes(project) == snapshot
    other = (
        "invalid_assessment"
        if diagnostic == "native_completion_failed"
        else "native_completion_failed"
    )
    assert learning_fail(project, other)["outcome"] == "REFUSED"
    # No second assessment can follow the recorded failure.
    assert (
        command_at(project, "learning", "learning_seam.py", "prepare")["outcome"]
        == "REFUSED"
    )
    command_at(project, "learning", "lad_peer.py", "learning", "clean")
    assert (
        command_at(project, "learning", "learning_seam.py", "accept", "PASS")["outcome"]
        == "REFUSED"
    )
    assert learning_state_bytes(project) == snapshot
    acknowledgement = command_at(project, "learning", "learning_seam.py", "emit_ack")
    ack_file = project / "learning" / acknowledgement["refs"][0]
    assert json.loads(ack_file.read_bytes())["learning"] == state["learning"]["answer"]
    # After ACK publication, replay of the helper still returns the same ACK.
    assert learning_fail(project, diagnostic)["outcome"] == "ACK"
    seam = seam_envelope(project, "foreground", acknowledgement)
    accept = ("foreground", "learning_seam.py", "accept_ack")
    assert command_at(project, *accept, seam=seam)["outcome"] == "UPDATER"
    after_ack = learning_state_bytes(project)
    assert command_at(project, *accept, seam=seam)["outcome"] == "UPDATER"
    assert learning_state_bytes(project) == after_ack
    assert learning_fail(project, diagnostic)["outcome"] == "REFUSED"
    final = ledger(project)
    assert final["request"] is None and final["pending"] is None
    assert len(role_occurrences(final, "learning")) == 1
    assert all(
        row["replacement_of"] is None for row in role_occurrences(final, "learning")
    )
    assert final["seam_seen"]["accepted_ack"]["ack"]["request_id"] == request["id"]
    # Lawful aftermath: the saved route continues and closes normally.
    assert step(project, "updater") == "ARBITER"
    assert step(project, "arbiter") == "CLOSE"


@pytest.mark.parametrize("change", ["pending_route", "unresolved_prerequisite"])
def test_invalid_learning_evidence_is_one_failed_assessment(
    tmp_path: Path, change: str
) -> None:
    project = copied(tmp_path)
    learning_ready(project)
    oid = ledger(project)["active"]["learning"]
    outcome = (
        "ERROR_TRANSIENT_RESOLVED" if change == "unresolved_prerequisite" else ("PASS")
    )
    command_at(project, "learning", "lad_peer.py", "learning", "clean", outcome)
    result = project / "learning/lad-results" / oid / "result.json"
    changed = json.loads(result.read_bytes())
    if change == "pending_route":
        changed["data"]["pending_route"] = None
    else:
        changed["data"]["failure"]["prerequisite_resolved"] = False
    result.write_bytes(canonical(changed) + b"\n")
    assert (
        command_at(project, "learning", "learning_seam.py", "accept", outcome)[
            "outcome"
        ]
        == "INVALID_RESULT"
    )
    assert learning_fail(project, "invalid_assessment")["outcome"] == "ACK"
    assert ledger(project)["learning"]["answer"]["diagnostic"] == "invalid_assessment"
    assert learning_ack(project) == "UPDATER"
    assert len(role_occurrences(ledger(project), "learning")) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "projection",
        "source",
        "guidance",
        "pending",
        "recorded",
        "occurrence",
        "prepare_failed_after_ready",
        "invalid_without_rejection",
        "wrong_plane_prepare",
    ],
)
def test_learning_failure_corruption_holds(tmp_path: Path, mutation: str) -> None:
    project = copied(tmp_path)
    learning_ready(project)
    state = ledger(project)
    diagnostic = "native_completion_failed"
    if mutation == "projection":
        file = project / "learning/lad-state/current-request.json"
        projection = json.loads(file.read_bytes())
        projection["id"] = str(uuid.uuid4())
        file.write_bytes(canonical(projection) + b"\n")
    elif mutation == "source":
        (project / "foreground/target/answer.py").write_text("def answer():\n  pass\n")
    elif mutation == "guidance":
        prompt = project / "prompts/learning.md"
        prompt.write_bytes(prompt.read_bytes() + b"\nLocal edit.\n")
    elif mutation == "pending":
        state["pending"] = dict(state["pending"], target="consultant")
        save_fixture(project, state)
    elif mutation == "recorded":
        state["learning"]["answer"] = {
            "disposition": "passed",
            "occurrence_id": state["active"]["learning"],
        }
        save_fixture(project, state)
    elif mutation == "occurrence":
        state["continuation"]["occurrence_id"] = str(uuid.uuid4())
        save_fixture(project, state)
    elif mutation == "prepare_failed_after_ready":
        diagnostic = "preparation_failed"
    elif mutation == "invalid_without_rejection":
        diagnostic = "invalid_assessment"
    elif mutation == "wrong_plane_prepare":
        before = learning_state_bytes(project)
        for args in (["learning"], ["learning", "--replacement"]):
            assert command(project, "prepare_role.py", *args)["outcome"] == "REFUSED"
        assert learning_state_bytes(project) == before
        return
    before = learning_state_bytes(project)
    reply = learning_fail(project, diagnostic)
    assert reply["outcome"] == "REFUSED", reply
    assert learning_state_bytes(project) == before
    if mutation != "recorded":  # emit_ack itself never records a disposition.
        assert (
            command_at(project, "learning", "learning_seam.py", "emit_ack")["outcome"]
            == "REFUSED"
        )


def received_request(project: Path) -> None:
    before_role(project, "learning")
    request = command(project, "learning_seam.py", "emit_request")
    seam = seam_envelope(project, "learning", request)
    assert (
        command_at(project, "learning", "learning_seam.py", "receive", seam=seam)[
            "outcome"
        ]
        == "ASSESS"
    )


def test_learning_preparation_failure_records_exact_reservation(tmp_path: Path) -> None:
    project = copied(tmp_path)
    received_request(project)
    base = copied(tmp_path / "base")
    received_request(base)
    # Failure before any reservation: explicit null owner, original ACK.
    assert learning_fail(project, "native_completion_failed")["outcome"] == "REFUSED"
    assert learning_fail(project, "preparation_failed")["outcome"] == "ACK"
    state = ledger(project)
    assert state["learning"]["answer"] == {
        "disposition": "failed",
        "occurrence_id": None,
        "diagnostic": "preparation_failed",
    }
    assert role_occurrences(state, "learning") == []
    assert learning_ack(project) == "UPDATER"
    # Crash after reservation, before READY: the incomplete owner is recorded.
    program = """
import sys
from pathlib import Path
import prepare_role
class FixtureCrash(BaseException):
    pass
def materialize(workspace, state, occurrence):
    raise FixtureCrash()
prepare_role.materialize = materialize
import learning_seam
try:
    learning_seam.learning_prepare()
except FixtureCrash:
    sys.exit(81)
raise AssertionError("fault window not reached")
"""
    child = subprocess.run(
        [sys.executable, "-B", "-S", "-c", program],
        cwd=base / "learning",
        env={
            "PATH": str(Path(sys.executable).parent),
            "PYTHONPATH": str(base / "scripts"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 81, child.stdout + child.stderr
    reserved = ledger(base)["active"]["learning"]
    assert not (
        base / "learning/lad-state/occurrences" / reserved / "ready.json"
    ).exists()
    assert learning_fail(base, "native_completion_failed")["outcome"] == "REFUSED"
    assert learning_fail(base, "preparation_failed")["outcome"] == "ACK"
    recorded = ledger(base)
    assert recorded["learning"]["answer"]["occurrence_id"] == reserved
    assert "learning" not in recorded["active"]
    assert len(role_occurrences(recorded, "learning")) == 1
    assert learning_ack(base) == "UPDATER"


@pytest.mark.parametrize("failure", ["native", "invalid", "prepare"])
def test_real_graph_learning_failure_acks_once_then_next_packet_assesses(
    tmp_path: Path, failure: str
) -> None:
    project = copied(tmp_path)
    scenario = "two_gates_" + failure
    (project / "request.md").write_text(
        "Original request: answer() returns 42 and answer_1() returns 42.\n"
    )
    workflow = fake_graph(project, scenario)
    graph = json.loads(workflow.read_bytes())
    if failure == "prepare":
        graph["planes"]["learning"]["nodes"]["prepare_learning"]["invoke"]["config"] = {
            "script": "scripts/lad_peer.py",
            "args": ["prepare_learning", scenario],
        }
        workflow.write_text(json.dumps(graph))
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
    receipt = control.start_run(
        instance,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    view = control.inspect_run(instance, receipt["run_id"], state_home=home)
    assert view["status"] == "succeeded", view
    state = ledger(project)
    assert state["terminal"] == "succeeded" and state["request"] is None
    diagnostic = {
        "native": "native_completion_failed",
        "invalid": "invalid_assessment",
        "prepare": "preparation_failed",
    }[failure]
    first, second = state["learning"]["answer"], state["learning"]["answer_1"]
    assert first["disposition"] == "failed" and first["diagnostic"] == diagnostic
    assert second["disposition"] == "passed"
    learning = role_occurrences(state, "learning")
    assert len(learning) == (1 if failure == "prepare" else 2)
    assert all(row["replacement_of"] is None for row in learning)
    assert (first["occurrence_id"] is None) == (failure == "prepare")
    assert second["occurrence_id"] == learning[-1]["occurrence_id"]
    assert {len(state["pairs"][name]) for name in ("answer", "answer_1")} == {2}
    nodes = [(row["plane_id"], row["node"]) for row in view["invocations"]]
    helper = {
        "native": "record_learning_failure",
        "invalid": "record_learning_invalid",
        "prepare": "record_learning_prepare_failure",
    }[failure]
    assert nodes.count(("learning", helper)) == 1
    assert nodes.count(("learning", "learning")) == len(learning)
    assert not any(
        node.endswith("_replacement") and plane == "learning" for plane, node in nodes
    )
    # Two assessments plus CLOSE, each with exactly one REQUEST and one ACK.
    assert len(view["seam_occurrences"]) == len(view["seam_acknowledgements"]) == 6
    for name in ("answer", "answer_1"):
        namespace = {}
        exec((project / f"foreground/target/{name}.py").read_text(), namespace)
        assert namespace[name]() == 42


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra_field",
        "revision_2",
        "revision_bool",
        "provider_reference",
        "learning_mode",
        "duplicate_key",
        "not_json",
    ],
)
def test_routing_policy_capture_and_legacy_preservation(
    tmp_path: Path, mutation: str
) -> None:
    graph = json.loads((LAD / "workflow.json").read_bytes())
    assert {"context/routing-policy.json", "scripts/presentation.py"} <= set(
        graph["control_files"]
    )
    assert {"context/provider-policy.json", "scripts/provider_wait.py"} <= set(
        graph["control_files"]
    )
    assert json.loads((LAD / "context/routing-policy.json").read_bytes()) == {
        "format": "millrace-lad-routing-policy/v2",
        "revision": 2,
        "presentation_revision": "millrace-lad-presentation/v1",
        "learning": "once-per-packet",
        "provider_recovery": "enabled",
        "provider_policy": "context/provider-policy.json",
    }
    assert json.loads((LAD / "context/provider-policy.json").read_bytes()) == {
        "format": "millrace-lad-provider-policy/v1",
        "first_retry_seconds": 900,
        "second_retry_seconds": 3600,
        "troubleshooter_binding": "claude",
        "automatic_wait": "optional",
    }
    # The exact legacy v1 object stays valid and strict; mutations refuse.
    shipped = (
        json.dumps(
            {
                "format": "millrace-lad-routing-policy/v1",
                "revision": 1,
                "presentation_revision": "millrace-lad-presentation/v1",
                "learning": "once-per-packet",
                "provider_recovery": "disabled",
            },
            indent=2,
        )
        + "\n"
    ).encode()
    project = copied(tmp_path)
    policy = project / "context/routing-policy.json"
    policy.write_bytes(shipped)
    value = json.loads(shipped)
    if mutation == "missing":
        policy.unlink()
    elif mutation == "not_json":
        policy.write_text("not json")
    elif mutation == "duplicate_key":
        policy.write_text(
            shipped.decode().replace('"revision": 1,', '"revision": 1, "revision": 1,')
        )
    else:
        value.update(
            {
                "extra_field": {"extra": True},
                "revision_2": {"revision": 2},
                "revision_bool": {"revision": True},
                "provider_reference": {
                    "provider_recovery": {"settings": "context/provider-policy.json"}
                },
                "learning_mode": {"learning": "per-pair"},
            }[mutation]
        )
        policy.write_text(json.dumps(value))
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    reply = command(project, "init_session.py", "--project-root", "..")
    assert reply["outcome"] == "RECONCILE" and "routing_policy" in reply["summary"]
    assert not (project / "foreground/lad-state").exists()
    # An initialized copy also refuses preparation before any reservation.
    fresh = copied(tmp_path / "fresh")
    (fresh / "context/routing-policy.json").write_bytes(shipped)
    bootstrap(fresh)
    if policy.exists():
        shutil.copyfile(policy, fresh / "context/routing-policy.json")
    else:
        (fresh / "context/routing-policy.json").unlink()
    before = learning_state_bytes(fresh)
    assert command(fresh, "prepare_role.py", "recon")["outcome"] == "REFUSED"
    assert learning_state_bytes(fresh) == before


def test_policy_edit_stales_in_flight_role_and_learning_request(tmp_path: Path) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    command(project, "prepare_role.py", "recon")
    command(project, "lad_peer.py", "recon", "clean")
    oid = ledger(project)["active"]["foreground"]
    policy = project / "context/routing-policy.json"
    original = policy.read_bytes()
    # Same valid policy, different captured bytes: guidance is stale.
    policy.write_text(json.dumps(json.loads(original)))
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "REFUSED"
    assert oid not in ledger(project)["accepted"]
    policy.write_bytes(original)
    assert command(project, "accept_role.py", "recon", "PASS")["outcome"] == "PLANNER"
    learning = copied(tmp_path / "learning")
    learning_ready(learning)
    edited = learning / "context/routing-policy.json"
    edited.write_text(json.dumps(json.loads(edited.read_bytes())))
    before = learning_state_bytes(learning)
    assert learning_fail(learning, "native_completion_failed")["outcome"] == "REFUSED"
    assert (
        command_at(learning, "learning", "learning_seam.py", "emit_ack")["outcome"]
        == "REFUSED"
    )
    assert learning_state_bytes(learning) == before


# --------------------------------------------------------------- provider wait

PROVIDER_ROLES = (
    "recon",
    "planner",
    "manager",
    "builder",
    "checker",
    "fixer",
    "doublechecker",
    "troubleshooter",
    "consultant",
    "updater",
    "arbiter",
)


def test_provider_node_template_has_complete_routes() -> None:
    import copy

    import lad_provider_template as tpl
    from millrace.router.graph import parse_graph

    raw = (LAD / "workflow.json").read_bytes()
    installed = json.loads(raw)
    nodes = installed["planes"]["foreground"]["nodes"]
    assert tpl.FOREGROUND_ROLES == PROVIDER_ROLES
    # Contract the expansion back to the inspected 95-node graph, then re-expand.
    base = copy.deepcopy(installed)
    foreground = base["planes"]["foreground"]["nodes"]
    for role in PROVIDER_ROLES:
        for name in tpl.provider_nodes(role, "claude"):
            del foreground[name]
        assert foreground[role]["on_error"] == {"goto": "classify_provider_" + role}
        foreground[role]["on_error"] = {"goto": f"prepare_{role}_replacement"}
        del foreground[role]["invoke"]["config"]["diagnostic_bridge"]
    base["control_files"] = [
        name for name in base["control_files"] if name not in tpl.CONTROL_FILES
    ]
    assert len(foreground) == 95 and len(nodes) == 172
    assert tpl.serialize(tpl.expand(base)) == raw  # no runtime graph mutation
    wait = {"pause": {"reason": "provider_wait_operator_return"}}
    for role in PROVIDER_ROLES:
        due = f"provider_due_{role}"
        wait["pause"]["resume"] = due
        human = {
            "pause": {
                "reason": "provider_human_resolution_required",
                "resume": due,
            }
        }
        uncertain = {"pause": {"reason": "lad_reconciliation_required"}}
        assert nodes[role]["invoke"]["config"]["diagnostic_bridge"] == {
            "context_path": "lad-state/native-context.json",
            "outbox_path": "lad-state/native-diagnostics",
        }
        assert nodes[role]["on_unknown"] == {"goto": f"prepare_{role}_replacement"}
        table = {
            f"classify_provider_{role}": {
                "ORDINARY_ERROR": {"goto": f"prepare_{role}_replacement"},
                "PROVIDER_WAIT": wait,
                "PROVIDER_HUMAN_REQUIRED": {"goto": f"provider_human_{role}"},
                "UNCERTAIN": uncertain,
            },
            due: {
                "PROVIDER_WAIT": wait,
                "RETRY_READY": {"goto": f"prepare_provider_retry_{role}"},
                "DIAGNOSIS_READY": {"goto": f"prepare_provider_diagnosis_{role}"},
                "PROVIDER_HUMAN_REQUIRED": {"goto": f"provider_human_{role}"},
                "UNCERTAIN": uncertain,
            },
            f"prepare_provider_retry_{role}": {
                "READY": {"goto": role},
                "PROVIDER_HUMAN_REQUIRED": {"goto": f"provider_human_{role}"},
                "UNCERTAIN": uncertain,
            },
            f"prepare_provider_diagnosis_{role}": {
                "READY": {"goto": f"provider_diagnosis_{role}"},
                "PROVIDER_HUMAN_REQUIRED": {"goto": f"provider_human_{role}"},
                "UNCERTAIN": uncertain,
            },
            f"accept_provider_diagnosis_{role}": {
                "RETRY_READY": {"goto": due},
                "PROVIDER_HUMAN_REQUIRED": {"goto": f"provider_human_{role}"},
                "UNCERTAIN": uncertain,
            },
            f"provider_human_{role}": {"HUMAN_REQUIRED": human, "UNCERTAIN": uncertain},
        }
        for name, routes in table.items():
            node = nodes[name]
            assert node["on"] == routes and node["outcomes"] == list(routes)
            assert node["invoke"]["config"]["script"] == "scripts/provider_wait.py"
            assert node["on_error"] == {
                "pause": {"reason": "lad_deterministic_failure"}
            }
            assert node["on_unknown"] == {
                "pause": {"reason": "lad_unknown_deterministic_outcome"}
            }
        diagnosis = nodes[f"provider_diagnosis_{role}"]
        stock = base["planes"]["foreground"]["nodes"]["troubleshooter"]
        assert diagnosis["invoke"] == stock["invoke"]  # prompt/settings/inputs
        assert diagnosis["outcomes"] == stock["outcomes"]
        assert set(map(json.dumps, diagnosis["on"].values())) == {
            json.dumps({"goto": f"accept_provider_diagnosis_{role}"})
        }
        assert (
            diagnosis["on_error"]
            == diagnosis["on_unknown"]
            == {"goto": f"provider_human_{role}"}
        )
    for plane in installed["planes"].values():
        names = set(plane["nodes"])
        for node in plane["nodes"].values():
            assert set(tpl.targets(node)) <= names  # no dangling target
    assert "diagnostic_bridge" not in json.dumps(installed["planes"]["learning"])
    assert not any("learning" in name and "provider" in name for name in nodes)
    parse_graph(raw)
    for missing in ("provider_human_builder", "provider_due_arbiter"):
        broken = copy.deepcopy(installed)
        del broken["planes"]["foreground"]["nodes"][missing]
        with pytest.raises(Exception):
            parse_graph(json.dumps(broken).encode())
    with pytest.raises(ValueError):
        tpl.provider_nodes("learning", "claude")


def test_provider_retry_does_not_consume_ordinary_replacement_escalation(
    tmp_path: Path,
) -> None:
    project = copied(tmp_path)
    bootstrap(project)
    step(project, "recon")
    before = (project / "foreground/lad-state/session.json").read_bytes()
    reply = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            "from pathlib import Path; from lad_state import run, emit;"
            "from prepare_role import prepare;"
            "run(lambda: emit(prepare(Path.cwd(), Path.cwd(), 'planner',"
            " replacement=True, provider_intent={'operation_id': '0' * 64,"
            " 'kind': 'retry'})['occurrence_id']))",
            str(project / "scripts"),
        ],
        cwd=project / "foreground",
        capture_output=True,
        check=True,
        env={
            "PATH": str(Path(sys.executable).parent),
            "PYTHONPATH": str(project / "scripts"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    result = json.loads(reply.stdout)
    assert result["outcome"] == "REFUSED"
    assert "provider_intent_excludes_replacement" in result["summary"]
    assert (project / "foreground/lad-state/session.json").read_bytes() == before
    assert ledger(project)["replacement_intents"] == {}
    # Every copy initializes its provider index before any role, empty.
    index = json.loads(
        (project / "foreground/lad-state/provider/index.json").read_bytes()
    )
    assert index["operations"] == {}


def test_provider_same_operation_success_ends_streak(tmp_path: Path) -> None:
    import lad_provider_support as support

    from millrace_plus import provider_wait as pw

    project = support.provider_project(tmp_path, [429, "success"])
    home = tmp_path / "state"
    instance, run, view = support.start(project, home)
    assert view["status"] == "paused", view
    assert support.paused_at(view, "provider_due_recon")
    state = ledger(project)
    original = state["continuation"]["occurrence_id"]
    assert state["continuation"] == {
        "node": "provider_due_recon",
        "occurrence_id": original,
        "lineage": state["continuation"]["lineage"],
        "cleanup_required": True,
    }
    assert state["request"] is None and state["terminal"] is None
    operation = support.pending(project, "recon")
    first = operation.events[0]
    assert (first["kind"], first["classification"], first["streak"]) == (
        "failure_classified",
        "provider_usage",
        1,
    )
    assert operation.authority["original_occurrence"] == original
    assert first["core_locator"] is None  # unbound until public observation
    # A pending policy edit stales authority; exact restoration clears it.
    policy = project / "context/provider-policy.json"
    captured = policy.read_bytes()
    policy.write_bytes(
        captured.replace(b'"first_retry_seconds": 1', b'"first_retry_seconds": 2')
    )
    with pytest.raises(pw.ProviderWaitError) as stale:
        support.observe(project, home, instance, run, view, "recon")
    assert stale.value.code == "provider_authority_stale"
    policy.write_bytes(captured)
    observed = support.observe(project, home, instance, run, view, "recon")
    assert observed["phase"] == "waiting" and observed["operator_return_required"]
    due = support.restore(project, operation.id, str(observed["observation_id"]))
    assert due["eligible"] is True and due["phase"] == "retry_ready"
    final = support.resume(
        instance,
        run,
        home,
        control.inspect_run(
            instance,
            run,
            state_home=home,
        ),
    )
    assert final["status"] == "succeeded", final
    kinds = [event["kind"] for event in support.events(project, "recon")]
    assert kinds.count("retry_reserved") == 1 and kinds[-1] == "succeeded"
    assert pw.pending_operation(project / "foreground", role="recon") is None
    state = ledger(project)
    assert original not in state["replacement_intents"]  # no ordinary escalation
    retry = [
        oid
        for oid, row in state["occurrences"].items()
        if row["role"] == "recon" and oid != original
    ]
    assert len(retry) == 1 and state["occurrences"][retry[0]]["replacement_of"] is None
    assert retry[0] in state["accepted"]
    recon = [row for row in final["invocations"] if row["node"] == "recon"]
    assert len(recon) == 2  # exactly one provider retry, no raw native rerun


def test_provider_unavailable_handoff_preserves_native_facts(tmp_path: Path) -> None:
    import lad_provider_support as support

    project = support.provider_project(
        tmp_path, [{"status": 429, "readonly_outbox": True}, "success"]
    )
    home = tmp_path / "state"
    try:
        instance, run, view = support.start(project, home)
    finally:
        for folder in (project / "foreground/lad-state/native-diagnostics").glob("*"):
            folder.chmod(0o700)
    assert view["status"] == "succeeded", view
    first = next(row for row in view["invocations"] if row["node"] == "recon")
    evidence = first["adapter_evidence"]
    assert evidence["native_diagnostic"] == {
        "protocol": "claude-print-json/v1",
        "api_error_status": 429,
    }
    assert evidence["diagnostic_bridge"] is None
    completion = next(
        row for row in view["completions"] if row["invocation_id"] == first["id"]
    )
    assert completion["transport"] != "completed"
    assert completion["effective_writer"] == "stopped"
    targets = [
        row["target"]
        for row in view["transitions"]
        if row["completion_id"] == first["id"]
    ]
    assert targets == ["classify_provider_recon"]
    # Ordinary guarded recovery ran; no provider operation, count or retry exists.
    index = json.loads(
        (project / "foreground/lad-state/provider/index.json").read_bytes()
    )
    assert index["operations"] == {}
    state = ledger(project)
    assert any(
        row["role"] == "recon" and row["replacement_of"] is not None
        for row in state["occurrences"].values()
    )


@pytest.mark.parametrize("status", [401, 403])
def test_provider_access_and_permission_enter_specific_human_hold(
    tmp_path: Path, status: int
) -> None:
    import lad_provider_support as support

    project = support.provider_project(tmp_path, [status])
    home = tmp_path / "state"
    _, _, view = support.start(project, home)
    assert view["status"] == "paused", view
    assert support.paused_at(view, "provider_due_recon")
    operation = support.pending(project, "recon")
    assert operation.tip["phase"] == "human_required"
    assert operation.tip["streak"] == 0  # never a timed count
    assert operation.events[0]["classification"] == (
        "provider_access" if status == 401 else "permission_required"
    )
    assert ledger(project)["continuation"]["node"] == "provider_due_recon"


DIAGNOSIS_CASES = {
    # case: (diagnosis CLI step, operator restoration before the diagnosis)
    "failed": ("ordinary-error", False),
    "unrestored": ("success", False),
    "wrong_outcome": ({"outcome": "PASS_REVIEW_REPAIRED"}, True),
    "restored": ("success", True),
}


@pytest.mark.parametrize("case", sorted(DIAGNOSIS_CASES))
def test_provider_third_failure_recovery_routes(tmp_path: Path, case: str) -> None:
    """Three known failures -> exactly one Troubleshooter diagnosis.

    The diagnosis is resolved only by ``PASS_PREREQUISITE_REPAIRED`` echoing all
    four repair fields of its input pending *and* a restoration the operator
    recorded through ``provider-due`` after the latest failure. Then it returns
    through the due guard to one same-operation retry whose success closes the
    operation. A failed dispatch, a valid result without restoration, or a
    wrong outcome with restoration holds for a person. Never a fourth timer,
    recursion or Consultant.
    """
    import lad_provider_support as support

    step, restore_first = DIAGNOSIS_CASES[case]
    project = support.provider_project(tmp_path, [429, 503, 529, step, "success"])
    _hold_recon_before_accept(project)
    home = tmp_path / "state"
    instance, run, view = support.start(project, home)
    for _ in range(2):
        assert support.paused_at(view, "provider_due_recon"), view
        observed = support.observe(project, home, instance, run, view, "recon")
        operation = support.pending(project, "recon")
        due = support.restore(project, operation.id, str(observed["observation_id"]))
        assert due["eligible"] is True
        view = support.resume(
            instance, run, home, control.inspect_run(instance, run, state_home=home)
        )
    assert support.paused_at(view, "provider_due_recon"), view
    operation = support.pending(project, "recon")
    assert operation.tip["streak"] == 3 and operation.tip["due_at"] is None
    observed = support.observe(project, home, instance, run, view, "recon")
    assert observed["phase"] == "diagnosis_required"
    if restore_first:
        # User authority through the supported provider-due operation, recorded
        # after the latest failure and before resuming into the diagnosis.
        due = support.restore(project, operation.id, str(observed["observation_id"]))
        assert due["phase"] == "diagnosis_required" and due["eligible"] is True
    view = support.resume(
        instance, run, home, control.inspect_run(instance, run, state_home=home)
    )
    assert view["status"] == "paused", view
    events = support.events(project, "recon")
    kinds = [event["kind"] for event in events]
    assert kinds.count("diagnosis_reserved") == 1
    assert kinds.count("diagnosis_recorded") == 1
    recorded = next(e for e in events if e["kind"] == "diagnosis_recorded")
    nodes = [row["node"] for row in view["invocations"]]
    assert nodes.count("provider_diagnosis_recon") == 1
    assert nodes.count("prepare_provider_diagnosis_recon") == 1
    assert not any(node.startswith("prepare_consultant") for node in nodes)
    # No recursion: the diagnosis is never classified, re-diagnosed or replaced.
    assert not any(
        node in {"prepare_troubleshooter", "classify_provider_troubleshooter"}
        or node.startswith("prepare_provider_diagnosis_troubleshooter")
        for node in nodes
    )
    state = ledger(project)
    (diagnosis,) = role_occurrences(state, "troubleshooter")
    assert diagnosis["occurrence_id"] not in state["accepted"]
    recon = role_occurrences(state, "recon")
    # The diagnosis input carries the operation's execution pending exactly.
    assert diagnosis["pending"] == {
        "kind": "execution",
        "target": "recon",
        "lineage": recon[2]["lineage"],
        "occurrence_id": recon[2]["occurrence_id"],
        "cause_id": operation.id,
    }
    result = project / f"foreground/lad-results/{diagnosis['occurrence_id']}"
    if case != "failed":
        data = json.loads((result / "result.json").read_bytes())["data"]
        assert {
            key: data[key]
            for key in (
                "trigger_occurrence_id",
                "cause_id",
                "resume_role",
                "repair_verified",
            )
        } == {
            "trigger_occurrence_id": recon[2]["occurrence_id"],
            "cause_id": operation.id,
            "resume_role": "recon",
            "repair_verified": True,
        }
    if case != "restored":
        assert support.paused_at(view, "provider_due_recon")
        assert recorded["phase"] == "human_required"
        assert recorded["restoration_ref"] is None
        assert support.pending(project, "recon").tip["phase"] == "human_required"
        assert kinds.count("retry_reserved") == 2 and "succeeded" not in kinds
        assert nodes.count("recon") == 3
        assert len(recon) == 3 and len({row["input_sha256"] for row in recon}) == 3
        assert state["continuation"]["node"] == "provider_due_recon"
        return
    # Resolved: the recorded restoration ref, then due -> one retry -> success.
    restoration = [e for e in events if e["kind"] == "restoration_recorded"][-1]
    assert recorded["phase"] == "retry_ready"
    assert recorded["restoration_ref"] == restoration["restoration_ref"]
    assert restoration["sequence"] > max(
        e["sequence"] for e in events if e["kind"] == "failure_classified"
    )
    assert kinds.count("retry_reserved") == 3 and "succeeded" not in kinds
    assert str(view["planes"]["foreground"].get("resume_target")).startswith(
        "accept_recon_"
    )
    assert nodes.count("recon") == 4
    assert len(recon) == 4 and len({row["input_sha256"] for row in recon}) == 4
    retry = recon[3]["occurrence_id"]
    assert recon[3]["replacement_of"] is None
    assert recon[3]["pending"] == operation.authority["pending_route"]["pending"]
    outcome = json.loads(
        (project / f"foreground/lad-results/{retry}/result.json").read_bytes()
    )["outcome"]
    routes = [
        command(project, "accept_role.py", "recon", outcome)["outcome"]
        for _ in range(2)
    ]
    assert routes[0] == routes[1] == ledger(project)["accepted"][retry]["route"]
    closed_events = support.events(project, "recon")
    assert [e["kind"] for e in closed_events].count("succeeded") == 1
    assert closed_events[-1]["kind"] == "succeeded"
    assert closed_events[-1]["occurrence_id"] == retry


def _hold_recon_before_accept(project: Path) -> None:
    """Test-owned graph edit before any run: pause after a recon native result.

    Each original accept target is kept as the pause's resume target, so every
    stock node stays reachable under Core graph admission.
    """
    workflow = project / "workflow.json"
    graph = json.loads(workflow.read_bytes())
    node = graph["planes"]["foreground"]["nodes"]["recon"]
    for outcome in node["outcomes"]:
        node["on"][outcome] = {
            "pause": {
                "reason": "test_hold_before_accept",
                "resume": node["on"][outcome]["goto"],
            }
        }
    workflow.write_text(json.dumps(graph, indent=2) + "\n")


def test_provider_success_acceptance_crash_replay(tmp_path: Path) -> None:
    """Crash after the durable accepted save; exact replay publishes once."""
    import lad_provider_support as support

    project = support.provider_project(tmp_path, [429, "success"])
    _hold_recon_before_accept(project)
    home = tmp_path / "state"
    instance, run, view = support.start(project, home)
    observed = support.observe(project, home, instance, run, view, "recon")
    operation = support.pending(project, "recon")
    support.restore(project, operation.id, str(observed["observation_id"]))
    held = support.resume(
        instance,
        run,
        home,
        control.inspect_run(
            instance,
            run,
            state_home=home,
        ),
    )
    assert held["status"] == "paused", held  # real retry success, not accepted
    assert str(held["planes"]["foreground"].get("resume_target")).startswith(
        "accept_recon_"
    )
    retry = ledger(project)["active"]["foreground"]
    assert support.events(project, "recon")[-1]["kind"] == "retry_reserved"
    invocations = len(held["invocations"])
    intents = ledger(project)["replacement_intents"]
    outcome = json.loads(
        (project / f"foreground/lad-results/{retry}/result.json").read_bytes()
    )["outcome"]
    crashed = support.script(
        project,
        "import json, accept_role\n"
        "class Crash(BaseException):\n    pass\n"
        "def interrupted(*args, **kwargs):\n    raise Crash()\n"
        "accept_role.reconcile_provider_success = interrupted\n"
        "try:\n"
        "    accept_role.accept_checked(\n"
        f"        foreground, foreground, 'recon', {outcome!r}\n"
        "    )\n"
        "    print(json.dumps({'result': 'returned'}))\n"
        "except Crash:\n    print(json.dumps({'result': 'crashed'}))\n",
    )
    assert crashed == {"result": "crashed"}
    state = ledger(project)
    accepted = state["accepted"][retry]
    route = accepted["route"]
    assert "succeeded" not in [e["kind"] for e in support.events(project, "recon")]
    for _ in range(2):
        replay = command(project, "accept_role.py", "recon", outcome)
        assert replay["outcome"] == route
    kinds = [event["kind"] for event in support.events(project, "recon")]
    assert kinds.count("succeeded") == 1 and kinds[-1] == "succeeded"
    after = ledger(project)
    assert after["accepted"][retry] == accepted
    assert after["replacement_intents"] == intents
    final = control.inspect_run(instance, run, state_home=home)
    assert len(final["invocations"]) == invocations  # no native rerun


def test_provider_ordinary_replacement_success_ends_same_streak(
    tmp_path: Path,
) -> None:
    import lad_provider_support as support

    project = support.provider_project(tmp_path, [429, "ordinary-error", "success"])
    home = tmp_path / "state"
    instance, run, view = support.start(project, home)
    observed = support.observe(project, home, instance, run, view, "recon")
    operation = support.pending(project, "recon")
    support.restore(project, operation.id, str(observed["observation_id"]))
    final = support.resume(
        instance,
        run,
        home,
        control.inspect_run(
            instance,
            run,
            state_home=home,
        ),
    )
    assert final["status"] == "succeeded", final
    state = ledger(project)
    original, retry, replacement = (
        row["occurrence_id"] for row in role_occurrences(state, "recon")
    )
    rows = state["occurrences"]
    assert original == operation.authority["original_occurrence"]
    assert (
        len(
            {
                rows[key]["input_sha256"]
                for key in rows
                if key
                in {
                    original,
                    retry,
                    replacement,
                }
            }
        )
        == 3
    )
    assert rows[retry]["replacement_of"] is None
    assert rows[replacement]["replacement_of"] == retry
    assert set(state["replacement_intents"]) == {retry}  # one ordinary intent
    events = support.events(project, "recon")
    kinds = [event["kind"] for event in events]
    assert kinds.count("failure_classified") == 1  # ordinary error not counted
    assert kinds.count("retry_reserved") == 1
    assert kinds.count("succeeded") == 1
    assert events[-1]["occurrence_id"] == replacement
    assert events[-1]["streak"] == 1
    # A later attempt's export carrying the original input digest is refused by
    # the exact occurrence fence (isolated owned bytes, no store edit).
    refused = support.script(
        project,
        "import json, provider_wait as helper\n"
        "from lad_state import canonical, digest, load\n"
        "state = load(foreground)\n"
        f"oid, first = {replacement!r}, {original!r}\n"
        "(ref, raw), = helper.exports(foreground, oid)\n"
        "forged = json.loads(raw)\n"
        "forged['input_sha256'] = state['occurrences'][first]['input_sha256']\n"
        "forged_raw = canonical(forged)\n"
        "forged_ref = {'path': ref['path'], 'sha256': digest(forged_raw)}\n"
        "try:\n"
        "    occurrence = state['occurrences'][oid]\n"
        "    helper._record(forged_ref, forged_raw, oid, occurrence)\n"
        "    print(json.dumps({'code': None}))\n"
        "except helper.Hold as exc:\n"
        "    print(json.dumps({'code': str(exc)}))\n",
    )
    assert refused == {"code": "provider_record_mismatch"}


def _hook_fixture(tmp_path: Path, role: str, *, original: str | None = None):
    """Command-driven session: failed generation A, ordinary replacement B."""
    import lad_provider_support as support

    project = copied(tmp_path)
    before_role(project, role)
    assert command(project, "prepare_role.py", role)["outcome"] == "READY"
    first = ledger(project)["active"]["foreground"]
    operation = support.seed_failure(project, first, original=original)
    assert (
        command(project, "prepare_role.py", role, "--replacement")["outcome"] == "READY"
    )
    second = ledger(project)["active"]["foreground"]
    peer = command(project, "lad_peer.py", role, "clean")
    return project, operation, first, second, peer["outcome"]


@pytest.mark.parametrize("role", ["recon", "arbiter"])
def test_provider_arbiter_close_consumes_success(tmp_path: Path, role: str) -> None:
    """Own valid native success + durable acceptance ends the streak, including
    Arbiter success routed directly to CLOSE with no later preparation."""
    import lad_provider_support as support

    from millrace_plus import provider_wait as pw

    project, operation, first, second, outcome = _hook_fixture(tmp_path, role)
    support.export(project, second, transport="completed")
    accepted = command(project, "accept_role.py", role, outcome)
    route = accepted["outcome"]
    assert route not in {"REFUSED", "INVALID_RESULT"}, accepted
    if role == "arbiter":
        assert route == "CLOSE"
        assert ledger(project)["continuation"]["node"] == "emit_learning_request"
    loaded = pw.load_operation(project / "foreground", operation)
    assert loaded.tip["kind"] == "succeeded"
    assert loaded.tip["occurrence_id"] == second
    assert command(project, "accept_role.py", role, outcome)["outcome"] == route
    events = pw.load_operation(project / "foreground", operation).events
    kinds = [event["kind"] for event in events]
    assert kinds.count("succeeded") == 1
    assert set(ledger(project)["replacement_intents"]) == {first}


@pytest.mark.parametrize("case", ["conflict", "binding", "missing", "unrelated"])
def test_provider_success_hook_refuses_conflict_and_excludes_unrelated(
    tmp_path: Path, case: str
) -> None:
    import lad_provider_support as support

    from millrace_plus import provider_wait as pw

    project, operation, first, second, outcome = _hook_fixture(
        tmp_path,
        "recon",
        original="unrelated-original" if case == "unrelated" else None,
    )
    if case in {"conflict", "unrelated"}:
        support.export(project, second, transport="completed")
    if case == "conflict":
        support.export(project, second, transport="completed")
    if case == "binding":
        support.export(project, second, transport="completed", settings="6" * 64)
    reply = command(project, "accept_role.py", "recon", outcome)
    state = ledger(project)
    tip = pw.load_operation(project / "foreground", operation).tip
    assert tip["kind"] != "succeeded"  # the pending streak stays unresolved
    # Durable acceptance survives; no invalid-result or replacement authority.
    assert second in state["accepted"]
    assert set(state["replacement_intents"]) == {first}
    assert state["pending"] is None or state["pending"]["kind"] != "invalid_result"
    if case in {"conflict", "binding"}:
        assert reply["outcome"] == "REFUSED"
        assert "provider_success_" in reply["summary"]
    else:
        assert reply["outcome"] == state["accepted"][second]["route"]


def test_provider_success_hook_excludes_diagnosis_and_learning(
    tmp_path: Path,
) -> None:
    import lad_provider_support as support

    project = copied(tmp_path)
    found = support.script(
        project,
        "import json, accept_role, provider_wait as helper\n"
        "from types import SimpleNamespace\n"
        "from lad_state import canonical, digest\n"
        "learning = accept_role.reconcile_provider_success(foreground,"
        " {'accepted': {'L': {}}}, {'occurrence_id': 'L', 'role': 'learning',"
        " 'format': 'millrace-lad-input/v2'})\n"
        "prep = {'kind': 'diagnosis', 'new_occurrence': 'D',"
        " 'input_sha256': '1' * 64}\n"
        "raw = canonical(prep)\n"
        "(foreground / 'diagnosis-preparation.json').write_bytes(raw)\n"
        "event = {'kind': 'diagnosis_reserved', 'preparation_ref':"
        " {'path': 'diagnosis-preparation.json', 'sha256': digest(raw)}}\n"
        "operation = SimpleNamespace(authority={'original_occurrence': 'A'},"
        " events=[event])\n"
        "state = {'occurrences': {'D': {'role': 'troubleshooter',"
        " 'input_sha256': '1' * 64, 'replacement_of': None,"
        " 'replacement_intent': None}}}\n"
        "lineage = helper.attempt_lineage(foreground, state, operation, 'D')\n"
        "print(json.dumps({'learning': learning, 'diagnosis': lineage}))\n",
    )
    assert found == {"learning": None, "diagnosis": None}


@pytest.mark.parametrize("automatic_wait", ["optional", "required"])
@pytest.mark.parametrize("fault", ["missing", "mismatch"])
def test_provider_policy_missing_diagnosis_binding_refuses_before_role(
    tmp_path: Path, automatic_wait: str, fault: str
) -> None:
    project = copied(tmp_path)
    policy = json.loads((project / "context/provider-policy.json").read_bytes())
    policy["automatic_wait"] = automatic_wait
    if fault == "missing":
        policy["troubleshooter_binding"] = "absent"
    else:
        workflow = project / "workflow.json"
        graph = json.loads(workflow.read_bytes())
        graph["planes"]["foreground"]["nodes"]["provider_diagnosis_builder"]["invoke"][
            "binding"
        ] = "command"
        workflow.write_text(json.dumps(graph, indent=2) + "\n")
    (project / "context/provider-policy.json").write_text(json.dumps(policy))
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    reply = command(project, "init_session.py", "--project-root", "..")
    assert reply["outcome"] == "RECONCILE"
    assert (
        "provider_troubleshooter_binding_missing"
        if fault == "missing"
        else "provider_troubleshooter_binding_mismatch"
    ) in reply["summary"]
    assert not (project / "foreground/lad-state").exists()


def test_provider_required_wait_capability_matrix(tmp_path: Path) -> None:
    """Required waiting: stock claude_print passes; an unsupported role refuses."""
    for name, unsupported in (("supported", False), ("unsupported", True)):
        project = copied(tmp_path / name)
        policy = json.loads((project / "context/provider-policy.json").read_bytes())
        policy["automatic_wait"] = "required"
        (project / "context/provider-policy.json").write_text(json.dumps(policy))
        if unsupported:
            workflow = project / "workflow.json"
            graph = json.loads(workflow.read_bytes())
            graph["planes"]["foreground"]["nodes"]["builder"]["invoke"]["binding"] = (
                "command"
            )
            workflow.write_text(json.dumps(graph, indent=2) + "\n")
        (project / "request.md").write_text("Original request: answer() returns 42.\n")
        reply = command(project, "init_session.py", "--project-root", "..")
        if unsupported:
            assert reply["outcome"] == "RECONCILE"
            assert "provider_classification_unsupported" in reply["summary"]
            assert not (project / "foreground/lad-state").exists()
        else:
            assert reply["outcome"] == "NEW_SESSION", reply


def test_provider_retry_after_ordinary_replacement_keeps_authority_route(
    tmp_path: Path,
) -> None:
    """429 -> retry -> ordinary failure -> replacement -> 429 -> retry -> success.

    The second retry keeps the operation's saved pending route, not the ordinary
    replacement's carrier, so its success closes the same operation (exact replay
    included). Resuming the second failure before observing it re-pauses for
    observation rather than holding for reconciliation.
    """
    import lad_provider_support as support

    project = support.provider_project(
        tmp_path, [429, "ordinary-error", 429, "success"]
    )
    _hold_recon_before_accept(project)
    home = tmp_path / "state"
    instance, run, view = support.start(project, home)
    for cycle in range(2):
        assert support.paused_at(view, "provider_due_recon"), view
        if cycle == 1:
            view = support.resume(
                instance, run, home, control.inspect_run(instance, run, state_home=home)
            )
            assert support.paused_at(view, "provider_due_recon"), view
            view = control.inspect_run(instance, run, state_home=home)
        observed = support.observe(project, home, instance, run, view, "recon")
        operation = support.pending(project, "recon")
        support.restore(project, operation.id, str(observed["observation_id"]))
        view = support.resume(
            instance, run, home, control.inspect_run(instance, run, state_home=home)
        )
    assert view["status"] == "paused", view
    assert str(view["planes"]["foreground"].get("resume_target")).startswith(
        "accept_recon_"
    )
    state = ledger(project)
    original, first_retry, replacement, second_retry = (
        row["occurrence_id"] for row in role_occurrences(state, "recon")
    )
    rows = state["occurrences"]
    route = support.pending(project, "recon").authority["pending_route"]
    assert route == {"node": "recon", "pending": rows[original]["pending"]}
    assert rows[replacement]["replacement_of"] == first_retry
    assert rows[replacement]["pending"] != route["pending"]
    assert rows[second_retry]["replacement_of"] is None
    assert rows[second_retry]["pending"] == route["pending"]
    assert (
        len(
            {
                rows[key]["input_sha256"]
                for key in (original, first_retry, replacement, second_retry)
            }
        )
        == 4
    )
    outcome = json.loads(
        (project / f"foreground/lad-results/{second_retry}/result.json").read_bytes()
    )["outcome"]
    routes = [
        command(project, "accept_role.py", "recon", outcome)["outcome"]
        for _ in range(2)
    ]
    assert routes[0] == routes[1] == ledger(project)["accepted"][second_retry]["route"]
    events = support.events(project, "recon")
    kinds = [event["kind"] for event in events]
    assert kinds.count("failure_classified") == 2
    assert kinds.count("retry_reserved") == 2
    assert kinds.count("succeeded") == 1 and kinds[-1] == "succeeded"
    assert "uncertainty_recorded" not in kinds
    assert events[-1]["occurrence_id"] == second_retry
    assert set(ledger(project)["replacement_intents"]) == {first_retry}


def test_provider_guard_retained_evidence_dispositions(tmp_path: Path) -> None:
    """Exact original replay, export without context, v1 record with an
    operation and a corrupt retained observation, in isolated command sessions."""
    from datetime import UTC, datetime

    import lad_provider_support as support

    from millrace_plus import provider_wait as pw
    from millrace_plus.router import native_diagnostic as nd

    def recon_attempt(name: str) -> tuple[Path, str]:
        project = copied(tmp_path / name)
        bootstrap(project)
        assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
        return project, ledger(project)["active"]["foreground"]

    def guard(project: Path, action: str) -> str:
        return support.script(
            project,
            "import json, provider_wait as helper\n"
            "try:\n"
            f"    out = helper.{action}(foreground, foreground.parent, 'recon')\n"
            "except helper.Hold as exc:\n"
            "    out = 'HOLD:' + str(exc)\n"
            "print(json.dumps({'outcome': out}))\n",
        )["outcome"]

    # A crash after the first failure fact but before the session save replays
    # that exact recorded failure; no uncertainty is appended.
    project, original = recon_attempt("replay")
    support.export(project, original, transport="execution_failure", status=429)
    crashed = support.script(
        project,
        "import json, provider_wait as helper\n"
        "class Crash(BaseException):\n    pass\n"
        "real = helper.save\n"
        "def interrupted(*args, **kwargs):\n    raise Crash()\n"
        "helper.save = interrupted\n"
        "try:\n"
        "    helper.classify(foreground, foreground.parent, 'recon')\n"
        "except Crash:\n    pass\n"
        "helper.save = real\n"
        "out = helper.classify(foreground, foreground.parent, 'recon')\n"
        "print(json.dumps({'outcome': out}))\n",
    )
    assert crashed == {"outcome": "PROVIDER_WAIT"}
    operation = support.pending(project, "recon")
    assert [event["kind"] for event in operation.events] == ["failure_classified"]
    assert ledger(project)["continuation"]["node"] == "provider_due_recon"
    # A corrupt retained observation holds; it is never reported as waiting.
    failure = operation.events[0]
    reference = dict(failure["diagnostic_ref"])
    observed = pw.observe_provider(
        project / "foreground",
        operation_id=operation.id,
        observed_at=datetime.now(UTC),
        facts={
            "instance_id": "instance",
            "run_id": "run",
            "state_home": "/state",
            "workspace": str(project / "foreground"),
            "expected_state_version": 7,
            "observed_state_version": 7,
            "active_revision": "revision-1",
            "plane_id": "foreground",
            "invocation_id": Path(reference["path"]).stem,
            "completion": {"invocation_id": Path(reference["path"]).stem},
            "transition": {"completion_id": Path(reference["path"]).stem},
            "bridge_ref": reference,
            "writer_state": "stopped",
        },
    )
    retained = project / "foreground" / observed["event"]["observation_ref"]["path"]
    corrupt = retained.read_bytes() + b" "
    retained.unlink()
    retained.write_bytes(corrupt)
    assert guard(project, "due") == "HOLD:provider_observation_invalid"
    # An export whose context file is gone is invalid retained state; a refused
    # preflight (no context, no export) stays ordinary recovery.
    project, oid = recon_attempt("context")
    support.export(project, oid, transport="execution_failure", status=429)
    (project / "foreground/lad-state/native-context.json").unlink()
    assert guard(project, "classify") == "HOLD:provider_context_missing_with_export"
    project, _ = recon_attempt("preflight")
    (project / "foreground/lad-state/native-context.json").unlink()
    assert guard(project, "classify") == "ORDINARY_ERROR"
    # A valid v1 record is ordinary recovery and leaves an existing operation
    # untouched: no count, no uncertainty, no retry authority.
    project, first = recon_attempt("legacy")
    seeded = support.seed_failure(project, first)
    assert (
        command(project, "prepare_role.py", "recon", "--replacement")["outcome"]
        == "READY"
    )
    second = ledger(project)["active"]["foreground"]
    occurrence = ledger(project)["occurrences"][second]
    nd.publish(
        project / "foreground",
        nd.BridgeConfig.load(support.BRIDGE),
        nd.NativeDiagnostic(
            context_id=second,
            input_sha256=occurrence["input_sha256"],
            invocation_id=uuid.uuid4().hex,
            plane_id="foreground",
            node_id="recon",
            producing_revision="revision-1",
            adapter_id="claude_print",
            adapter_contract="millrace-claude-print/v1",
            evidence_format="millrace-claude-print-result/v2",
            launch_sha256="4" * 64,
            transport="execution_failure",
            writer_state="stopped",
            failure_code="claude_native_error",
            protocol=nd.CLAUDE_PROTOCOL,
            api_error_status=429,
        ),
    )
    before = pw.load_operation(project / "foreground", seeded).events
    assert guard(project, "classify") == "ORDINARY_ERROR"
    assert pw.load_operation(project / "foreground", seeded).events == before


def test_provider_diagnosis_contract_fields(tmp_path: Path) -> None:
    """The diagnosis acceptor requires all four existing repair fields and a
    repaired prerequisite; the captured contract instructs exactly that."""
    import lad_provider_support as support

    contracts = (LAD / "context/role-contracts.md").read_text()
    for phrase in (
        "Provider diagnosis: when `input.pending.cause_id` names a provider operation",
        "`trigger_occurrence_id`, `cause_id`, `resume_role`, `repair_verified=true`",
        "restoration for that operation",
    ):
        assert phrase in " ".join(contracts.split())
    found = support.script(
        copied(tmp_path),
        "import json, provider_wait as helper\n"
        "pending = {'kind': 'execution', 'target': 'recon', 'lineage': 'l',"
        " 'occurrence_id': 'failed-1', 'cause_id': 'operation-1'}\n"
        "good = {'trigger_occurrence_id': 'failed-1', 'cause_id': 'operation-1',"
        " 'resume_role': 'recon', 'repair_verified': True}\n"
        "cases = [('PASS_PREREQUISITE_REPAIRED', good)]\n"
        "cases.append(('PASS_REVIEW_REPAIRED', good))\n"
        "for key, value in (('trigger_occurrence_id', 'other'),"
        " ('cause_id', 'other'), ('resume_role', 'builder'),"
        " ('repair_verified', 'true')):\n"
        "    cases.append(('PASS_PREREQUISITE_REPAIRED', {**good, key: value}))\n"
        "print(json.dumps([helper.diagnosis_repaired(o, d, pending)"
        " for o, d in cases]))\n",
    )
    assert found == [True, False, False, False, False, False]


# ------------------------------------------- L-R2 correctness closeout (D1-D3)


def _provider_index_bytes(project: Path) -> bytes:
    return (project / "foreground/lad-state/provider/index.json").read_bytes()


def _session_bytes(project: Path) -> bytes:
    return (project / "foreground/lad-state/session.json").read_bytes()


def _orphaned_builder_operation(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Packet 1 Builder 429 opens an operation. Its ordinary replacement then
    succeeds without a bridge export, so the operation stays unfinished (the
    accepted hook has no verified success evidence) while the session moves
    on to an unrelated packet 2 Builder occurrence. Commands only."""
    import lad_provider_support as support

    project = copied(tmp_path)
    names = ["answer"] + ["answer_" + str(index) for index in range(1, 61)]
    request = (
        "Original required functions:\n"
        + "\n".join(name + "() returns 42" for name in names)
        + "\n"
    ).encode()
    bootstrap(project, request=request)
    assert step(project, "recon") == "PLANNER"
    assert step(project, "planner", scenario="many_packets") == "MANAGER"
    assert step(project, "manager", scenario="many_packets") == "BUILDER"
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    first = ledger(project)["active"]["foreground"]
    operation = support.seed_failure(project, first)
    assert (
        command(project, "prepare_role.py", "builder", "--replacement")["outcome"]
        == "READY"
    )
    peer = command(project, "lad_peer.py", "builder", "many_packets")
    accepted = command(project, "accept_role.py", "builder", peer["outcome"])
    assert accepted["outcome"] not in {"REFUSED", "INVALID_RESULT"}, accepted
    for role in ("checker", "updater"):
        step(project, role, scenario="many_packets")
    assert ledger(project)["packet_index"] == 1
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    later = ledger(project)["active"]["foreground"]
    rows = ledger(project)["occurrences"]
    assert rows[later]["replacement_of"] is None
    assert rows[later]["lineage"] != rows[first]["lineage"]
    pending = support.pending(project, "builder")
    assert pending.id == operation
    assert pending.tip["kind"] == "failure_classified"
    return project, operation, first, later


UNRELATED_FAILURES = {
    # case: (transport, api_error_status, expected classifier outcome)
    "timeout": ("timeout", None, "ORDINARY_ERROR"),
    "cancelled": ("cancelled", None, "ORDINARY_ERROR"),
    "unclassified_status": ("execution_failure", 418, "ORDINARY_ERROR"),
    "absent_status": ("execution_failure", None, "ORDINARY_ERROR"),
    "provider_usage": ("execution_failure", 429, "UNCERTAIN"),
    "provider_access": ("execution_failure", 401, "UNCERTAIN"),
}


@pytest.mark.parametrize("case", sorted(UNRELATED_FAILURES))
def test_provider_stale_operation_keeps_unrelated_ordinary_routing(
    tmp_path: Path, case: str
) -> None:
    """D1: an unfinished operation does not capture a later unrelated failure.

    A same-role occurrence outside the operation's lineage with a nonpositive
    class keeps today's ordinary route; the old operation is neither recorded,
    changed nor cleared. A positive provider class on that occurrence still
    holds without counting or retry authority.
    """
    import lad_provider_support as support

    project, operation, _, later = _orphaned_builder_operation(tmp_path)
    transport, status, expected = UNRELATED_FAILURES[case]
    support.export(project, later, transport=transport, status=status)
    events = support.pending(project, "builder").events
    index = _provider_index_bytes(project)
    session = _session_bytes(project)
    reply = command(project, "provider_wait.py", "classify", "builder")
    assert reply["outcome"] == expected, reply
    assert _session_bytes(project) == session  # no provider mark or save
    after = support.pending(project, "builder")
    assert after.id == operation
    if expected == "UNCERTAIN":
        added = after.events[len(events) :]
        assert after.events[: len(events)] == events
        assert [event["kind"] for event in added] == ["uncertainty_recorded"]
        assert added[0]["occurrence_id"] == later
        assert added[0]["streak"] == events[-1]["streak"]
        return
    assert after.events == events
    assert _provider_index_bytes(project) == index
    # The ordinary replacement route proceeds; the old operation stays as it was.
    assert (
        command(project, "prepare_role.py", "builder", "--replacement")["outcome"]
        == "READY"
    )
    replacement = ledger(project)["active"]["foreground"]
    assert ledger(project)["occurrences"][replacement]["replacement_of"] == later
    peer = command(project, "lad_peer.py", "builder", "many_packets")
    accepted = command(project, "accept_role.py", "builder", peer["outcome"])
    assert accepted["outcome"] not in {"REFUSED", "INVALID_RESULT"}, accepted
    assert support.pending(project, "builder").events == events
    assert _provider_index_bytes(project) == index


@pytest.mark.parametrize("binding", ["same", "mutated"])
def test_provider_linked_attempt_ordinary_error_keeps_identity_fence(
    tmp_path: Path, binding: str
) -> None:
    """A later failure inside the operation's own lineage still compares the
    pinned binding: an ordinary error with the same binding routes ordinarily
    without a provider fact; a mutated binding holds."""
    import lad_provider_support as support

    project, operation, _, second, _ = _hook_fixture(tmp_path, "recon")
    support.export(
        project,
        second,
        transport="timeout",
        settings=support.SETTINGS_SHA if binding == "same" else "6" * 64,
    )
    events = support.pending(project, "recon").events
    reply = command(project, "provider_wait.py", "classify", "recon")
    after = support.pending(project, "recon")
    assert after.id == operation
    if binding == "same":
        assert reply["outcome"] == "ORDINARY_ERROR", reply
        assert after.events == events
    else:
        assert reply["outcome"] == "UNCERTAIN", reply
        assert [e["kind"] for e in after.events[len(events) :]] == [
            "uncertainty_recorded"
        ]
        assert after.tip["streak"] == events[-1]["streak"]


SUCCESS_INTERRUPTION = """import json, accept_role
from millrace_plus import provider_wait as pw
class Crash(BaseException):
    pass
real = pw._publish_index
def interrupted(foreground, index, op, event, raw):
    if event["kind"] == "succeeded":
        raise Crash()  # after the immutable event write, before the index write
    return real(foreground, index, op, event, raw)
pw._publish_index = interrupted
try:
    accept_role.accept_checked(foreground, foreground, "recon", OUTCOME)
    print(json.dumps({"result": "returned"}))
except Crash:
    print(json.dumps({"result": "crashed"}))
"""


@pytest.mark.parametrize("case", ["accepted_replay", "next_guard", "corrupt_tip"])
def test_provider_interrupted_index_publication_recovers_in_graph_helpers(
    tmp_path: Path, case: str
) -> None:
    """D2: an interrupted event-then-index publication is repaired by the
    graph helpers' own lookup (accepted-result replay or the next guard),
    without an operator ``provider-due``. Only the unique valid next event is
    adopted; a corrupt unindexed tip still holds. The public read-only lookup
    keeps refusing an index-behind store without writing."""
    import lad_provider_support as support

    from millrace_plus import provider_wait as pw

    project, operation, first, second, outcome = _hook_fixture(tmp_path, "recon")
    support.export(project, second, transport="completed")
    crashed = support.script(
        project, SUCCESS_INTERRUPTION.replace("OUTCOME", repr(outcome))
    )
    assert crashed == {"result": "crashed"}
    foreground = project / "foreground"
    folder = foreground / f"lad-state/provider/operations/{operation}/events"
    unindexed = folder / "2.json"
    assert json.loads(unindexed.read_bytes())["kind"] == "succeeded"
    index = _provider_index_bytes(project)
    assert json.loads(index)["operations"][operation]["sequence"] == 1
    state = ledger(project)
    accepted = state["accepted"][second]
    intents = state["replacement_intents"]
    for lookup in (
        lambda: pw.load_operation(foreground, operation, recover=False),
        lambda: pw.pending_operation(foreground, role="recon"),
    ):
        with pytest.raises(pw.ProviderWaitError) as behind:
            lookup()
        assert behind.value.code == "provider_index_behind"
        assert _provider_index_bytes(project) == index
    if case == "corrupt_tip":
        tampered = json.loads(unindexed.read_bytes())
        tampered["streak"] = 0
        raw = json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode()
        unindexed.unlink()
        unindexed.write_bytes(raw)
        for _ in range(2):
            replay = command(project, "accept_role.py", "recon", outcome)
            assert replay["outcome"] == "REFUSED", replay
            assert "provider_success_" in replay["summary"]
            assert _provider_index_bytes(project) == index
            assert unindexed.read_bytes() == raw
        assert ledger(project)["accepted"][second] == accepted
        return
    if case == "accepted_replay":
        routes = [
            command(project, "accept_role.py", "recon", outcome)["outcome"]
            for _ in range(2)
        ]
        assert routes == [accepted["route"]] * 2, routes
    else:
        # The next guard's own operation lookup repairs the index first.
        assert command(project, "prepare_role.py", "planner")["outcome"] == "READY"
        planner = ledger(project)["active"]["foreground"]
        support.export(project, planner, transport="timeout")
        reply = command(project, "provider_wait.py", "classify", "planner")
        assert reply["outcome"] == "ORDINARY_ERROR", reply
    loaded = pw.load_operation(foreground, operation, recover=False)
    kinds = [event["kind"] for event in loaded.events]
    assert kinds == ["failure_classified", "succeeded"]
    assert loaded.tip["occurrence_id"] == second
    assert sorted(os.listdir(folder)) == ["1.json", "2.json"]
    tip = json.loads(_provider_index_bytes(project))["operations"][operation]
    assert tip["sequence"] == 2
    assert tip["sha256"] == hashlib.sha256(unindexed.read_bytes()).hexdigest()
    assert pw.pending_operation(foreground, role="recon") is None
    after = ledger(project)
    assert after["accepted"][second] == accepted
    assert after["replacement_intents"] == intents
    assert first not in after["accepted"]


CLASSIFY_REPLAYS = ("wait", "human", "second_export", "changed_export", "guidance")


@pytest.mark.parametrize("case", [*CLASSIFY_REPLAYS, "advanced"])
def test_provider_classifier_replay_after_own_save(tmp_path: Path, case: str) -> None:
    """D3: re-running ``classify`` after its own session save returns the exact
    saved outcome with no new fact, count, mark or save. A conflicting
    retained export, changed guidance or a later operation fact holds."""
    from datetime import UTC, datetime

    import lad_provider_support as support

    from millrace_plus import provider_wait as pw

    project = copied(tmp_path)
    bootstrap(project)
    assert command(project, "prepare_role.py", "recon")["outcome"] == "READY"
    oid = ledger(project)["active"]["foreground"]
    status = 401 if case == "human" else 429
    ref, _ = support.export(project, oid, transport="execution_failure", status=status)
    expected = "PROVIDER_HUMAN_REQUIRED" if case == "human" else "PROVIDER_WAIT"
    first = command(project, "provider_wait.py", "classify", "recon")
    assert first["outcome"] == expected, first
    continuation = ledger(project)["continuation"]
    assert continuation["node"] == (
        "provider_human_recon" if case == "human" else "provider_due_recon"
    )
    assert continuation["occurrence_id"] == oid
    foreground = project / "foreground"
    if case == "second_export":
        support.export(project, oid, transport="execution_failure", status=status)
    elif case == "changed_export":
        path = foreground / ref["path"]
        original = path.read_bytes()
        changed = original.replace(b'"api_error_status":429', b'"api_error_status":503')
        assert changed != original
        path.unlink()
        path.write_bytes(changed)
    elif case == "guidance":
        prompt = project / "prompts/recon.md"
        prompt.write_bytes(prompt.read_bytes() + b"\nEdited after classification.\n")
    elif case == "advanced":
        operation = support.pending(project, "recon")
        reference = dict(operation.events[0]["diagnostic_ref"])
        invocation = Path(reference["path"]).stem
        pw.observe_provider(
            foreground,
            operation_id=operation.id,
            observed_at=datetime.now(UTC),
            facts={
                "instance_id": "instance",
                "run_id": "run",
                "state_home": "/state",
                "workspace": str(foreground),
                "expected_state_version": 7,
                "observed_state_version": 7,
                "active_revision": "revision-1",
                "plane_id": "foreground",
                "invocation_id": invocation,
                "completion": {"invocation_id": invocation},
                "transition": {"completion_id": invocation},
                "bridge_ref": reference,
                "writer_state": "stopped",
            },
        )
    events = support.pending(project, "recon").events
    index = _provider_index_bytes(project)
    session = _session_bytes(project)
    replies = [
        command(project, "provider_wait.py", "classify", "recon") for _ in range(2)
    ]
    if case in {"wait", "human"}:
        assert [reply["outcome"] for reply in replies] == [expected] * 2, replies
    else:
        assert [reply["outcome"] for reply in replies] == ["UNCERTAIN"] * 2, replies
    assert support.pending(project, "recon").events == events
    assert _provider_index_bytes(project) == index
    assert _session_bytes(project) == session


def _hold_diagnosis_error(project: Path) -> None:
    """Test-owned graph edit before any run: pause after the provider diagnosis
    node's native error, keeping its original human target as the resume."""
    workflow = project / "workflow.json"
    graph = json.loads(workflow.read_bytes())
    node = graph["planes"]["foreground"]["nodes"]["provider_diagnosis_recon"]
    assert node["on_error"] == {"goto": "provider_human_recon"}
    node["on_error"] = {
        "pause": {"reason": "test_hold_before_human", "resume": "provider_human_recon"}
    }
    workflow.write_text(json.dumps(graph, indent=2) + "\n")


HUMAN_INTERRUPTION = """import json, provider_wait as helper
class Crash(BaseException):
    pass
def interrupted(*args, **kwargs):
    raise Crash()  # after the provider diagnosis fact, before the session save
helper.save = interrupted
try:
    helper.human(foreground, "recon")
    print(json.dumps({"result": "returned"}))
except Crash:
    print(json.dumps({"result": "crashed"}))
"""


@pytest.mark.parametrize("case", ["interrupted", "resolved_conflict"])
def test_provider_diagnosis_failure_human_replay_after_record(
    tmp_path: Path, case: str
) -> None:
    """D3: the diagnosis branch of ``human`` re-run after its own provider
    record returns the saved HUMAN_REQUIRED once, without another diagnosis
    fact, and the graph then pauses for the person. A recorded resolved
    diagnosis is conflicting authority for this native error and holds."""
    import lad_provider_support as support

    from millrace_plus import provider_wait as pw

    project = support.provider_project(
        tmp_path, [429, 503, 529, "ordinary-error", "success"]
    )
    _hold_diagnosis_error(project)
    home = tmp_path / "state"
    instance, run, view = support.start(project, home)
    for _ in range(2):
        assert support.paused_at(view, "provider_due_recon"), view
        observed = support.observe(project, home, instance, run, view, "recon")
        operation = support.pending(project, "recon")
        due = support.restore(project, operation.id, str(observed["observation_id"]))
        assert due["eligible"] is True
        view = support.resume(
            instance, run, home, control.inspect_run(instance, run, state_home=home)
        )
    operation = support.pending(project, "recon")
    assert operation.tip["streak"] == 3 and operation.tip["due_at"] is None
    observed = support.observe(project, home, instance, run, view, "recon")
    assert observed["phase"] == "diagnosis_required"
    if case == "resolved_conflict":
        support.restore(project, operation.id, str(observed["observation_id"]))
    view = support.resume(
        instance, run, home, control.inspect_run(instance, run, state_home=home)
    )
    assert support.paused_at(view, "provider_human_recon"), view
    state = ledger(project)
    diagnosis = state["continuation"]["occurrence_id"]
    assert state["continuation"]["node"] == "provider_diagnosis_recon"
    assert state["occurrences"][diagnosis]["role"] == "troubleshooter"
    failed = role_occurrences(state, "recon")[2]["occurrence_id"]
    assert support.pending(project, "recon").tip["kind"] == "diagnosis_reserved"
    if case == "interrupted":
        crashed = support.script(project, HUMAN_INTERRUPTION)
        assert crashed == {"result": "crashed"}
    else:
        restoration = [
            e
            for e in support.events(project, "recon")
            if e["kind"] == "restoration_recorded"
        ][-1]
        pw.record_diagnosis(
            project / "foreground",
            operation_id=operation.id,
            occurrence_id=diagnosis,
            resolved=True,
            restoration_ref=restoration["restoration_ref"],
        )
    events = support.events(project, "recon")
    assert [e["kind"] for e in events].count("diagnosis_recorded") == 1
    assert ledger(project)["continuation"]["node"] == "provider_diagnosis_recon"
    session = _session_bytes(project)
    first = command(project, "provider_wait.py", "human", "recon")
    if case == "resolved_conflict":
        again = command(project, "provider_wait.py", "human", "recon")
        for reply in (first, again):
            assert reply["outcome"] == "UNCERTAIN", reply
            assert "provider_human_cause_missing" in reply["summary"]
        assert support.events(project, "recon") == events
        assert _session_bytes(project) == session
        return
    assert first["outcome"] == "HUMAN_REQUIRED", first
    assert support.events(project, "recon") == events
    state = ledger(project)
    assert state["continuation"]["node"] == "provider_due_recon"
    assert state["continuation"]["occurrence_id"] == failed
    assert diagnosis not in state["accepted"]
    saved = _session_bytes(project)
    again = command(project, "provider_wait.py", "human", "recon")
    assert again["outcome"] == "HUMAN_REQUIRED", again
    assert _session_bytes(project) == saved
    assert support.events(project, "recon") == events
    final = support.resume(
        instance, run, home, control.inspect_run(instance, run, state_home=home)
    )
    assert support.paused_at(final, "provider_due_recon"), final
    nodes = [row["node"] for row in final["invocations"]]
    assert nodes.count("provider_diagnosis_recon") == 1
    assert nodes.count("provider_human_recon") == 1
    assert not any(node.startswith("prepare_consultant") for node in nodes)
    assert support.events(project, "recon") == events
    assert support.pending(project, "recon").tip["phase"] == "human_required"


def test_provider_third_failure_with_troubleshooter_pending_diagnoses_once(
    tmp_path: Path,
) -> None:
    """§7.4: a pending Troubleshooter generation's third known failure gets
    exactly one separately identified diagnosis Troubleshooter; its failure
    goes to the human hold, never a classifier, replacement or Consultant."""
    import lad_provider_support as support

    project = support.provider_project(
        tmp_path,
        [
            "success",
            "success",
            "success",
            {"outcome": "ERROR"},
            429,
            503,
            529,
            "ordinary-error",
            "success",
        ],
    )
    home = tmp_path / "state"
    instance, run, view = support.start(project, home)
    for _ in range(2):
        assert support.paused_at(view, "provider_due_troubleshooter"), view
        observed = support.observe(project, home, instance, run, view, "troubleshooter")
        operation = support.pending(project, "troubleshooter")
        due = support.restore(project, operation.id, str(observed["observation_id"]))
        assert due["eligible"] is True
        view = support.resume(
            instance, run, home, control.inspect_run(instance, run, state_home=home)
        )
    assert support.paused_at(view, "provider_due_troubleshooter"), view
    operation = support.pending(project, "troubleshooter")
    assert operation.tip["streak"] == 3 and operation.tip["due_at"] is None
    observed = support.observe(project, home, instance, run, view, "troubleshooter")
    assert observed["phase"] == "diagnosis_required"
    view = support.resume(
        instance, run, home, control.inspect_run(instance, run, state_home=home)
    )
    assert support.paused_at(view, "provider_due_troubleshooter"), view
    events = support.events(project, "troubleshooter")
    kinds = [event["kind"] for event in events]
    assert kinds.count("diagnosis_reserved") == 1
    assert kinds.count("diagnosis_recorded") == 1
    assert kinds.count("retry_reserved") == 2 and "succeeded" not in kinds
    recorded = next(e for e in events if e["kind"] == "diagnosis_recorded")
    assert recorded["phase"] == "human_required"
    assert recorded["restoration_ref"] is None
    nodes = [row["node"] for row in view["invocations"]]
    assert nodes.count("troubleshooter") == 3
    assert nodes.count("classify_provider_troubleshooter") == 3
    assert nodes.count("prepare_provider_diagnosis_troubleshooter") == 1
    assert nodes.count("provider_diagnosis_troubleshooter") == 1
    assert nodes.count("provider_human_troubleshooter") == 1
    assert "prepare_troubleshooter_replacement" not in nodes
    assert not any(node.startswith("prepare_consultant") for node in nodes)
    state = ledger(project)
    rows = role_occurrences(state, "troubleshooter")
    assert len(rows) == 4
    route = operation.authority["pending_route"]
    assert route == {"node": "troubleshooter", "pending": rows[0]["pending"]}
    for retry in rows[1:3]:
        assert retry["replacement_of"] is None
        assert retry["pending"] == route["pending"]
    diagnosis = rows[3]
    assert diagnosis["pending"] == {
        "kind": "execution",
        "target": "troubleshooter",
        "lineage": rows[2]["lineage"],
        "occurrence_id": rows[2]["occurrence_id"],
        "cause_id": operation.id,
    }
    assert diagnosis["occurrence_id"] not in state["accepted"]
    assert state["continuation"]["node"] == "provider_due_troubleshooter"
    assert state["continuation"]["occurrence_id"] == rows[2]["occurrence_id"]
    assert support.pending(project, "troubleshooter").tip["phase"] == "human_required"


LINEAGE_PROGRAM = """import json, provider_wait as helper
from lad_state import canonical, digest
completion = {"invocation_id": "inv-1", "effective_writer": "stopped"}
transition = {"completion_id": "inv-1", "node": "recon", "revision_id": "rev-1"}
for name, value in (("completion.json", completion), ("transition.json", transition)):
    (foreground / name).write_bytes(canonical(value))
def ref(name, value):
    return {"path": name, "sha256": digest(canonical(value))}
observation = {
    "active_revision": "rev-1",
    "invocation_id": "inv-1",
    "observed_state_version": 5,
    "instance_id": "instance-1",
    "run_id": "run-1",
    "completion_ref": ref("completion.json", completion),
    "transition_ref": ref("transition.json", transition),
}
pinned = {"request_id": "p", "operation": "pause_plane", "status": "applied",
          "instance_id": "instance-1", "run_id": "run-1", "plane_id": "foreground"}
event = {"control_refs": [ref("pinned.json", pinned)]}
def receipt(**changes):
    value = {"request_id": "q", "operation": "resume_plane", "status": "applied",
             "instance_id": "instance-1", "run_id": "run-1", "plane_id": "foreground"}
    value.update(changes)
    return value
def view(*controls, moves=(), node="provider_due_recon"):
    return {
        "active_revision": "rev-1",
        "planes": {"foreground": {"active_invocation": "guard-1"}},
        "invocations": [{"id": "guard-1", "node": node}],
        "completions": [dict(completion)],
        "transitions": [dict(transition)],
        "operator_moves": list(moves),
        "controls": [pinned, *controls],
    }
cases = {
    "pinned_only": view(),
    "raw_resume_plane": view(receipt()),
    "refused_resume": view(receipt(status="refused")),
    "run_resume": view(receipt(operation="resume_run", plane_id=None)),
    "recover_run": view(receipt(operation="recover_run", plane_id=None)),
    "cancel_run": view(receipt(operation="cancel_run", plane_id=None)),
    "redirect_plane": view(receipt(operation="redirect_plane")),
    "queued": view(receipt(status="queued")),
    "other_run": view(receipt(run_id="run-2")),
    "other_instance": view(receipt(instance_id="instance-2")),
    "learning_plane": view(receipt(plane_id="learning")),
    "move_before": view(moves=[{"version": 5}]),
    "move_after": view(moves=[{"version": 6}]),
    "not_due_guard": view(node="provider_human_recon"),
}
found = {}
for name, value in cases.items():
    try:
        found[name] = len(helper.lineage_controls(
            foreground, value, event, observation, "recon"))
    except helper.Hold as exc:
        found[name] = str(exc)
print(json.dumps(found))
"""


def test_provider_due_lineage_checks_run_controls_not_service_journal(
    tmp_path: Path,
) -> None:
    """§7.6 boundary: the due guard checks only resume/receipt lineage of the
    observed run. It admits any applied or refused pause/resume/recover
    receipt of that run, including an unjournaled raw resume, and refuses
    other operations, targets and later operator moves. Service journal
    legality stays with ``known_controls`` at settlement (generic service
    tests), which rejects an unjournaled child control as
    ``unknown_child_control``."""
    import lad_provider_support as support

    found = support.script(copied(tmp_path), LINEAGE_PROGRAM)
    assert found == {
        "pinned_only": 0,
        "raw_resume_plane": 1,
        "refused_resume": 1,
        "run_resume": 1,
        "recover_run": 1,
        "cancel_run": "provider_unknown_control",
        "redirect_plane": "provider_unknown_control",
        "queued": "provider_unknown_control",
        "other_run": "provider_unknown_control",
        "other_instance": "provider_unknown_control",
        "learning_plane": "provider_unknown_control",
        "move_before": 0,
        "move_after": "provider_operator_move",
        "not_due_guard": "provider_guard_invocation",
    }


def test_provider_pause_meets_unchanged_service_continuation_gate(
    tmp_path: Path,
) -> None:
    """The actual provider wait pause satisfies the unchanged service
    ``_unfinished_continuation_ready`` gate only at its exact cursor; a cursor
    mismatch, live writer, nonquiescent run or other continuation refuses. A
    separate child run gate is accepted only for the run-level decision."""
    import copy

    import lad_provider_support as support

    from millrace_plus.lad_service_runtime import _unfinished_continuation_ready

    project = support.provider_project(tmp_path, [429])
    _, _, view = support.start(project, tmp_path / "state")
    assert support.paused_at(view, "provider_due_recon"), view
    assert view["quiescent"] is True and view.get("run_gate") is None
    state = ledger(project)
    assert state["continuation"]["node"] == "provider_due_recon"
    assert _unfinished_continuation_ready(state, view, "foreground") is True

    def changed_view(**plane: object) -> dict:
        value = copy.deepcopy(view)
        value["planes"]["foreground"].update(plane)
        return value

    def changed_state(**continuation: object) -> dict:
        value = copy.deepcopy(state)
        value["continuation"].update(continuation)
        return value

    gated = changed_view(
        status="ready", resume_target=None, cursor="provider_due_recon"
    )
    gated["run_gate"] = {"reason": "operator_pause", "request_id": uuid.uuid4().hex}
    other_cursor = copy.deepcopy(gated)
    other_cursor["planes"]["foreground"]["cursor"] = "recon"
    quiet = copy.deepcopy(view)
    quiet["quiescent"] = False
    refusals = {
        "default_without_run_gate": (state, view, None),
        "other_plane": (state, view, "learning"),
        "cursor_mismatch": (
            state,
            changed_view(resume_target="provider_human_recon"),
            "foreground",
        ),
        "live_writer": (
            state,
            changed_view(active_invocation=uuid.uuid4().hex),
            "foreground",
        ),
        "not_quiescent": (state, quiet, "foreground"),
        "classifier_continuation": (
            changed_state(node="classify_provider_recon"),
            view,
            "foreground",
        ),
        "cleanup_not_required": (
            changed_state(cleanup_required=False),
            view,
            "foreground",
        ),
        "gated_plane_resume": (state, gated, "foreground"),
        "gated_other_cursor": (state, other_cursor, None),
    }
    for name, (selected, run, plane) in refusals.items():
        assert _unfinished_continuation_ready(selected, run, plane) is False, name
    assert _unfinished_continuation_ready(state, gated, None) is True


def test_captured_contracts_state_command_python_imports() -> None:
    """Captured guidance names the actual import boundary: routing-policy/v1
    commands are standard-library-only, while v2 command Python must import
    installed Plus and Core."""
    contracts = " ".join((LAD / "context/contracts.md").read_text().split())
    harness = " ".join((LAD / "context/harness.md").read_text().split())
    both = "must import both `millrace_plus` and Core (`millrace`)"
    assert "They run without importing Plus or OS." not in contracts
    assert "Ordinary copied commands remain standard-library-only" not in harness
    for text in (contracts, harness):
        assert both in text
        assert "`provider_recovery_requires_plus`" in text
        assert "A layout with Plus but not Core is not qualified." in text
    assert "routing-policy/v1 run with `python3 -S`" in contracts
    assert "They do not import Plus, Core or OS." in contracts
    assert "never load Plus or Core" in harness
