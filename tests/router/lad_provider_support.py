"""Provider-wait LAD graph fixtures: real ``claude_print`` with an inert fake CLI.

The copied stock graph keeps binding key ``claude``, adapter ``claude_print``,
contract ``millrace-claude-print/v1``, the captured profile/version pin and the
real Plus resolver; only the executable points at the owned scripted peer
(``fixtures/lad_provider_claude.py``), following ``claude_support.fake``. The
existing ``fake_graph`` command rebinding is deliberately not used: it cannot
produce adapter evidence or bridge exports.

Clock seam: subprocess graph guards read the real clock, so these fixtures
capture a small positive *test* provider policy before any operation exists
and use explicit operator restoration records. Shipped 900/3600 values are
never changed, nothing sleeps and no live timestamp is edited.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from millrace_plus import catalog
from millrace_plus import provider_wait as pw
from millrace_plus.router import control

LAD = catalog.bundled_root() / "lad"
FIXTURES = Path(__file__).parent / "fixtures"
VERSION = "2.1.296 (Claude Code)\n"
TEST_POLICY = {
    "format": "millrace-lad-provider-policy/v1",
    "first_retry_seconds": 1,
    "second_retry_seconds": 1,
    "troubleshooter_binding": "claude",
    "automatic_wait": "optional",
}


def fake_cli(
    root: Path, plan: list[object], *, scripts: Path, scenario: str = "clean"
) -> Path:
    """Install the scripted peer; ``plan`` steps are statuses or ``"success"``."""
    root.mkdir(parents=True, exist_ok=True)
    executable = root / "fake-claude"
    source = FIXTURES / "lad_provider_claude.py"
    executable.write_text(f"#!{sys.executable}\n" + source.read_text())
    executable.chmod(0o700)
    Path(str(executable) + ".json").write_text(
        json.dumps(
            {
                "version": VERSION,
                "plan": plan,
                "counter": str(root / "count"),
                "peer": str(FIXTURES / "lad_peer.py"),
                "scripts": str(scripts),
                "scenario": scenario,
            }
        )
    )
    return executable


def provider_project(
    tmp_path: Path,
    plan: list[object],
    *,
    policy: dict[str, object] | None = None,
    request: str = "Original request: answer() returns 42.\n",
) -> Path:
    """One owned copied LAD definition with the real native binding retained."""
    project = tmp_path / "project"
    shutil.copytree(LAD, project)
    executable = fake_cli(tmp_path / "native", plan, scripts=project / "scripts")
    workflow = project / "workflow.json"
    graph = json.loads(workflow.read_bytes())
    graph["bindings"]["claude"]["executable"] = str(executable)
    graph["bindings"]["command"]["executable"] = sys.executable
    workflow.write_text(json.dumps(graph, indent=2) + "\n")
    (project / "context/provider-policy.json").write_text(
        json.dumps(policy or TEST_POLICY, indent=2) + "\n"
    )
    (project / "request.md").write_text(request)
    return project


def start(project: Path, home: Path) -> tuple[str, str, dict]:
    made = control.create_instance(
        project / "workflow.json",
        project,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = str(made["instance_id"])
    receipt = control.start_run(
        instance,
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    assert receipt["status"] == "applied", receipt
    run = str(receipt["run_id"])
    return instance, run, control.inspect_run(instance, run, state_home=home)


def pending(project: Path, role: str) -> pw.Operation:
    operation = pw.pending_operation(project / "foreground", role=role)
    assert operation is not None
    return operation


def observe(
    project: Path, home: Path, instance: str, run: str, view: dict, role: str
) -> dict[str, object]:
    """Exact public ``provider-observe`` with explicit locator and version."""
    operation = pending(project, role)
    return pw.observe_snapshot(
        project,
        state_home=home,
        instance_id=instance,
        run_id=run,
        plane_id="foreground",
        operation_id=operation.id,
        invocation_id=failed_invocation(operation),
        expected_state_version=int(view["state_version"]),
        observed_at=datetime.now(UTC),
    )


def failed_invocation(operation: pw.Operation) -> str:
    """The latest recorded failure's actual invocation (its export file name)."""
    failure = next(
        event
        for event in reversed(operation.events)
        if event["kind"] in {"failure_classified", "human_required"}
        and event["diagnostic_ref"] is not None
    )
    return Path(str(failure["diagnostic_ref"]["path"])).stem


def restore(project: Path, operation_id: str, observation_id: str) -> dict:
    """Explicit operator restoration confirmation (user authority, not native).

    The record binds the operation's pinned policy digest, so it works for a
    service child whose policy lives under a separate workflow root.
    """
    operation = pw.load_operation(project / "foreground", operation_id)
    record = pw.restoration_record(
        operation_id=operation_id,
        policy_sha256=operation.authority["policy_sha256"],
        kind="operator_confirmation",
        actor="test-operator",
        request_id=uuid.uuid4().hex,
        observed_at=pw.stamp(datetime.now(UTC)),
        evidence_ref=None,
        statement="Operator confirms provider access restored (offline fixture).",
    )
    path = project.parent / f"restoration-{uuid.uuid4().hex}.json"
    path.write_bytes(pw._encode(record))
    return pw.provider_due(
        project,
        operation_id=operation_id,
        observation_id=observation_id,
        now=datetime.now(UTC),
        restoration=path,
    )


def resume(instance: str, run: str, home: Path, view: dict) -> dict:
    """Standalone public resume of the paused foreground at the observed version."""
    receipt = control.resume_plane(
        instance,
        run,
        plane_id="foreground",
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=int(view["state_version"]),
    )
    assert receipt["status"] == "applied", receipt
    return control.inspect_run(instance, run, state_home=home)


def paused_at(view: dict, node: str) -> bool:
    plane = view["planes"]["foreground"]
    return plane["status"] == "paused" and plane.get("resume_target") == node


def events(project: Path, role: str) -> list[dict]:
    index = json.loads(
        (project / "foreground/lad-state/provider/index.json").read_bytes()
    )
    found = []
    for op in index["operations"]:
        operation = pw.load_operation(project / "foreground", op, recover=False)
        if operation.authority["role"] == role:
            found.extend(operation.events)
    return found


# ------------------------------------------- unit-level success-hook fixtures
#
# These build an operation over a command-driven copied session (no Core run):
# a synthetic but strict v2 bridge export for an exact LAD occurrence, then the
# C2 record calls. The projections below restate the C3 helper's authority
# contract independently (packet lineage, saved pending route, binding facts).

BRIDGE = {
    "context_path": "lad-state/native-context.json",
    "outbox_path": "lad-state/native-diagnostics",
}
SETTINGS_SHA = "5" * 64
HOST_ROUTE_SHA = "7" * 64


def _ledger(project: Path) -> dict:
    return json.loads((project / "foreground/lad-state/session.json").read_bytes())


def export(
    project: Path,
    oid: str,
    *,
    transport: str,
    status: int | None = None,
    settings: str = SETTINGS_SHA,
    invocation: str | None = None,
    input_sha256: str | None = None,
) -> tuple[dict, object]:
    """Publish one exact v2 terminal record for occurrence ``oid``."""
    from millrace_plus.router import native_diagnostic as nd

    occurrence = _ledger(project)["occurrences"][oid]
    record = nd.NativeDiagnosticV2(
        diagnostic=nd.NativeDiagnostic(
            context_id=oid,
            input_sha256=input_sha256 or occurrence["input_sha256"],
            invocation_id=invocation or uuid.uuid4().hex,
            plane_id="foreground",
            node_id=occurrence["role"],
            producing_revision="revision-1",
            adapter_id="claude_print",
            adapter_contract="millrace-claude-print/v1",
            evidence_format="millrace-claude-print-result/v2",
            launch_sha256="4" * 64,
            transport=transport,
            writer_state="stopped",
            failure_code=None if transport == "completed" else "claude_native_error",
            protocol=nd.CLAUDE_PROTOCOL,
            api_error_status=status,
        ),
        settings_sha256=settings,
        observed_version="2.1.296",
        requested_model=None,
        requested_effort=None,
        resolved_model=None,
        resolved_effort=None,
        host_route_sha256=HOST_ROUTE_SHA,
    )
    ref = nd.publish(project / "foreground", nd.BridgeConfig.load(BRIDGE), record)
    return ref.to_json(), record


def seed_failure(project: Path, oid: str, *, original: str | None = None) -> str:
    """Create the operation from one positive 429 failure of occurrence ``oid``.

    ``original`` names a different original occurrence (an unrelated-success
    fixture); by default the failed occurrence itself is the original.
    """
    foreground = project / "foreground"
    state = _ledger(project)
    occurrence = state["occurrences"][oid]
    ref, record = export(project, oid, transport="execution_failure", status=429)
    facts = record.to_json()
    policy = pw.load_policy((project / "context/provider-policy.json").read_bytes())
    authority = pw.authority_record(
        session_id=state["session_id"],
        service=None,
        packet_lineage={
            "lineage": occurrence["lineage"],
            "packet_id": None
            if occurrence["packet"] is None
            else occurrence["packet"]["id"],
        },
        role=occurrence["role"],
        pending_route={"node": occurrence["role"], "pending": occurrence["pending"]},
        original_occurrence=original or oid,
        input_sha256=occurrence["input_sha256"],
        request_sha256=occurrence["request_sha256"],
        source_sha256=occurrence["source_sha256"],
        guidance_sha256=occurrence["guidance_sha256"],
        policy_sha256=policy["sha256"],
        binding={
            "adapter_id": "claude_print",
            "contract": "millrace-claude-print/v1",
            "config_sha256": SETTINGS_SHA,
            "requested_model": None,
            "requested_effort": None,
            "resolved_model": None,
            "resolved_effort": None,
            "host_route_sha256": HOST_ROUTE_SHA,
        },
        native_owner={
            key: facts[key]
            for key in ("invocation_id", "plane_id", "node_id", "producing_revision")
        },
    )
    found = pw.classify(
        adapter_id="claude_print",
        adapter_contract="millrace-claude-print/v1",
        observed_version=facts["observed_version"],
        transport=facts["transport"],
        writer_state=facts["writer_state"],
        failure_code=facts["failure_code"],
        native_diagnostic=facts["native_diagnostic"],
        bridge_ref=ref,
        bridge_record=facts,
        effects_resolved=True,
    )
    outcome, _ = pw.classify_failure(
        foreground,
        authority=authority,
        classification=found,
        occurrence_id=oid,
        diagnostic_ref=ref,
        failed_at=datetime.now(UTC),
        current_source_sha256=occurrence["source_sha256"],
        policy=policy,
    )
    assert outcome == "PROVIDER_WAIT"
    return str(authority["operation_id"])


def script(project: Path, code: str) -> dict:
    """Run ``code`` against this copy's own scripts in an isolated subprocess.

    The code receives ``foreground`` and must print one JSON object.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from pathlib import Path; foreground = Path.cwd()\n" + code,
            str(project / "scripts"),
        ],
        cwd=project / "foreground",
        env={"PATH": str(Path(sys.executable).parent), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout.decode().strip().splitlines()[-1])
