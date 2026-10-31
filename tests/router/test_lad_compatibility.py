"""Retained LAD readers and explicit domain publication, separate from graph runs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

ARCHIVE = Path(__file__).parent / "fixtures/lad_v1_reader_source.tar.gz"
ARCHIVE_SHA256 = "bb4e00c449f846c79d331e9d22c3078d11e235a004b1a9e8c9062c7459cbb5f0"
MANIFEST_SHA256 = "94ecfa8d90211a52955b2055deb0a7d42ac8bd7257bb476a3d5db463a9e9d8bc"


def frozen_source(tmp_path: Path) -> tuple[Path, dict]:
    """Verify and extract literal old source; never call candidate constructors."""
    assert hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() == ARCHIVE_SHA256
    destination = tmp_path / "old-reader"
    destination.mkdir()
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        members = archive.getmembers()
        assert len(members) == 41
        assert len({member.name for member in members}) == len(members)
        for member in members:
            relative = Path(member.name)
            assert member.isfile() and not relative.is_absolute()
            assert ".." not in relative.parts
            source = archive.extractfile(member)
            assert source is not None
            with source:
                raw = source.read()
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(member.mode)
    manifest_file = destination / "manifest.json"
    assert hashlib.sha256(manifest_file.read_bytes()).hexdigest() == MANIFEST_SHA256
    manifest = json.loads(manifest_file.read_bytes())
    rows = [
        dict(row, path="workflow/" + row["path"])
        for row in manifest["workflow_files"]
    ] + manifest["reference_files"]
    assert len(rows) == 40
    assert {"manifest.json", *(row["path"] for row in rows)} == {
        str(file.relative_to(destination))
        for file in destination.rglob("*")
        if file.is_file()
    }
    for row in rows:
        file = destination / row["path"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == row["sha256"]
        assert file.stat().st_size == row["size"]
        assert oct(file.stat().st_mode & 0o777) == row["mode"]
    return destination, manifest


def old_command(project: Path, script: str, *args: str) -> dict:
    child = subprocess.run(
        [sys.executable, "-B", "-S", str(project / "scripts" / script), *args],
        cwd=project / "foreground",
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    return json.loads(child.stdout)


@pytest.mark.parametrize("used", [False, True], ids=["pristine", "used"])
def test_frozen_v1_bootstrap_and_reload_are_byte_stable(
    tmp_path: Path, used: bool
) -> None:
    old, manifest = frozen_source(tmp_path)
    project = tmp_path / "project"
    shutil.copytree(old / "workflow", project)
    shutil.copyfile(old / "reference/lad_peer.py", project / "scripts/lad_peer.py")
    request = b"Original request: answer() returns 42.\n"
    (project / "request.md").write_bytes(request)
    assert old_command(project, "init_session.py", "--project-root", "..")[
        "outcome"
    ] == "NEW_SESSION"
    if used:
        assert old_command(project, "setup_intake.py", "--project-root", "..")[
            "outcome"
        ] == "INTAKE_READY_EXISTING"
        assert old_command(project, "prepare_role.py", "recon")["outcome"] == "READY"
        result = old_command(project, "lad_peer.py", "recon", "clean")
        assert old_command(project, "accept_role.py", "recon", result["outcome"])[
            "outcome"
        ] != "REFUSED"
        assert (project / "foreground/request.md").read_bytes() == request
    session = project / "foreground/lad-state/session.json"
    before = session.read_bytes()
    state = json.loads(before)
    assert state["format"] == "millrace-lad-session/v1"
    assert set(state) == set(manifest["v1_semantics"]["required_session_keys"])
    assert state["limits"] == manifest["v1_semantics"]["CAPS"]
    assert bool(state["events"]) == used
    if used:
        assert len(state["accepted"]) == 1
        assert state["occurrences"]
    outcome = old_command(project, "init_session.py", "--project-root", "..")[
        "outcome"
    ]
    assert outcome == (
        "EXISTING_SESSION_WITH_WORK" if used else "EXISTING_SESSION_PRISTINE"
    )
    assert session.read_bytes() == before


def genuine_v7(tmp_path: Path, scenario: str) -> tuple[Path, dict]:
    names = (
        "MILLRACE_LAD_OLD_PYTHON", "MILLRACE_LAD_OLD_WHEEL",
        "MILLRACE_LAD_V1_MANIFEST", "MILLRACE_LAD_V7_GENERATOR",
    )
    if not all(os.environ.get(name) for name in names):
        pytest.skip("Exact retained old Core inputs required for v7 integration")
    case = tmp_path / ("genuine-v7-" + scenario)
    child = subprocess.run(
        [os.environ[names[0]], "-B", "-I", os.environ[names[3]],
         os.environ[names[1]], os.environ[names[2]], str(case), scenario],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=90,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    manifest = json.loads((case / "manifest.json").read_bytes())
    assert manifest["old_wheel_sha256"] == (
        "27726a0d00fb258f27b2900c853319a749ca8506ac35ee2859b3bff8b44c7bc4"
    )
    return case, manifest


def install_candidate_scripts(project: Path) -> None:
    from millrace_plus import catalog

    for script in (catalog.bundled_root() / "lad/scripts").glob("*.py"):
        shutil.copyfile(script, project / "scripts" / script.name)
    # Candidate scripts require their explicit captured routing policy; it is
    # installed with them, never inferred for a retained copy.
    shutil.copyfile(
        catalog.bundled_root() / "lad/context/routing-policy.json",
        project / "context/routing-policy.json",
    )


def explicit_upgrade(
    case: Path, manifest: dict, *, isolated=False, version=None
) -> dict:
    from millrace_plus import catalog
    from millrace_plus.router import control

    home = case / manifest["state_home"]
    view = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    environment = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    if not isolated:
        # Match pytest's explicit source overlay for this intermediate proof.
        # Installed-package qualification repeats without this test-only path.
        environment["PYTHONPATH"] = str(Path(catalog.__file__).parents[1])
    child = subprocess.run(
        [sys.executable, "-B", *(["-S"] if isolated else []),
         str(case / manifest["project"] / "scripts/init_session.py"),
         "--project-root", "..", "--upgrade-session",
         "--session-id", manifest["session_id"],
         "--instance-id", manifest["instance_id"], "--run-id", manifest["run_id"],
         "--state-home", str(home), "--expected-state-version",
         str(view["state_version"] if version is None else version)],
        cwd=case / manifest["project"] / "foreground",
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    return json.loads(child.stdout)


@pytest.mark.parametrize("scenario", ["request", "ack"])
def test_explicit_upgrade_preserves_genuine_original_v7_seams(
    tmp_path: Path, scenario: str
) -> None:
    from millrace_plus.router import control

    case, manifest = genuine_v7(tmp_path, scenario)
    project = case / manifest["project"]
    file = project / "foreground/lad-state/session.json"
    old_raw = file.read_bytes()
    old = json.loads(old_raw)
    payloads = [(case / row["path"], (case / row["path"]).read_bytes())
                for row in manifest["payloads"]]
    home = case / manifest["state_home"]
    before = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    assert before["startup_cap"] == 40 and before["startup_elapsed"] == 1200
    install_candidate_scripts(project)
    assert explicit_upgrade(case, manifest)["outcome"] == "SESSION_UPGRADED"
    upgraded = json.loads(file.read_bytes())
    assert upgraded["format"] == "millrace-lad-session/v2"
    assert upgraded["occurrences"] == old["occurrences"]
    for key in (
        "session_id", "accepted", "criteria", "pending", "request", "seam_seen"
    ):
        assert upgraded[key] == old[key]
    assert upgraded["events"] == [{"id": row["id"], "kind": row["kind"]}
                                    for row in old["events"]]
    assert (project / "foreground" / upgraded["legacy"]["path"]).read_bytes() == old_raw
    record = project / "foreground" / upgraded["migration"]["path"]
    assert hashlib.sha256(record.read_bytes()).hexdigest() == (
        upgraded["migration"]["sha256"]
    )
    assert all(path.read_bytes() == raw for path, raw in payloads)
    stable = file.read_bytes()
    assert explicit_upgrade(case, manifest)["outcome"] == "SESSION_ALREADY_UPGRADED"
    assert file.read_bytes() == stable
    after = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    # Replaced script bytes legitimately change the unsubmitted draft view.
    assert {key: value for key, value in after.items() if key != "draft_revision"} == {
        key: value for key, value in before.items() if key != "draft_revision"
    }
    shutil.copyfile(
        Path(__file__).parent / "fixtures/lad_peer.py", project / "scripts/lad_peer.py"
    )
    graph_file = project / "workflow.json"
    graph = json.loads(graph_file.read_bytes())
    # Explicit adoption declares every candidate control file the scripts read.
    # Without the captured policy/presentation files, preparation refuses with
    # routing_policy_invalid before Learning runs (no silent adoption).
    graph["control_files"].extend([
        "scripts/session_compat.py", "scripts/presentation.py",
        "context/routing-policy.json",
    ])
    assert graph["format"] == "millrace-router/v3"
    assert graph["limits"]["max_invocations"] == 40
    graph_file.write_text(json.dumps(graph))
    submitted = control.submit_revision(
        manifest["instance_id"], manifest["run_id"], state_home=home,
        request_id=uuid.uuid4().hex, expected_state_version=after["state_version"],
    )
    assert submitted["status"] == "applied", submitted
    current = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    accepted = control.accept_revision(
        manifest["instance_id"], manifest["run_id"],
        candidate_id=submitted["candidate_id"],
        state_home=home, request_id=uuid.uuid4().hex,
        expected_state_version=current["state_version"],
    )
    assert accepted["status"] == "applied", accepted
    if scenario == "request":
        current = control.inspect_run(
            manifest["instance_id"], manifest["run_id"], state_home=home
        )
        resumed = control.resume_plane(
            manifest["instance_id"], manifest["run_id"], plane_id="learning",
            state_home=home, request_id=uuid.uuid4().hex,
            expected_state_version=current["state_version"],
        )
        assert resumed["status"] == "applied", resumed
    completed = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    progressed = json.loads(file.read_bytes())
    assert progressed["request"] is None
    assert progressed["pending"] is None
    assert all(progressed["accepted"].get(oid) == row
               for oid, row in old["accepted"].items())
    assert progressed["events"][:len(old["events"])] == upgraded["events"]
    assert completed["id"] == before["id"] and completed["count"] == 7
    assert completed["started_at"] == before["started_at"]
    assert completed["startup_deadline"] == before["startup_deadline"]
    assert completed["startup_cap"] == 40
    assert len(completed["seam_occurrences"]) == 2
    assert len(completed["seam_acknowledgements"]) == 2
    assert all(path.read_bytes() == raw for path, raw in payloads)


def test_upgrade_missing_runtime_or_stale_public_version_creates_no_archive(
    tmp_path: Path,
) -> None:
    case, manifest = genuine_v7(tmp_path, "request")
    project = case / manifest["project"]
    install_candidate_scripts(project)
    file = project / "foreground/lad-state/session.json"
    before = file.read_bytes()
    result = explicit_upgrade(case, manifest, isolated=True)
    assert result["outcome"] == "RECONCILE"
    assert "upgrade_runtime_unavailable" in result["summary"]
    result = explicit_upgrade(case, manifest, version=1)
    assert result["outcome"] == "RECONCILE"
    assert "stale_state_version" in result["summary"]
    assert file.read_bytes() == before
    assert not (project / "foreground/lad-state/legacy").exists()
    assert not (project / "foreground/lad-state/migrations").exists()


def rewrite_checksum(file: Path, state: dict) -> None:
    state.pop("integrity_sha256")
    state["integrity_sha256"] = hashlib.sha256(
        json.dumps(
            state, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    file.write_text(json.dumps(state))


def finite_old_domain_project(tmp_path: Path) -> Path:
    old, _ = frozen_source(tmp_path)
    project = tmp_path / "project"
    shutil.copytree(old / "workflow", project)
    shutil.copyfile(old / "reference/lad_peer.py", project / "scripts/lad_peer.py")
    file = project / "workflow.json"
    graph = json.loads(file.read_bytes())
    graph["bindings"].pop("claude")
    graph["bindings"]["command"]["executable"] = sys.executable
    graph["control_files"].append("scripts/lad_peer.py")
    for plane in graph["planes"].values():
        for name, node in plane["nodes"].items():
            if node["invoke"]["binding"] == "claude":
                node["invoke"].update(
                    binding="command",
                    config={"script": "scripts/lad_peer.py", "args": [name, "clean"]},
                )
    file.write_text(json.dumps(graph))  # Before any old guidance capture.
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    assert old_command(project, "init_session.py", "--project-root", "..")[
        "outcome"
    ] == "NEW_SESSION"
    assert old_command(project, "setup_intake.py", "--project-root", "..")[
        "outcome"
    ] == "INTAKE_READY_EXISTING"
    return project


def paused_current_core_for_old_domain(tmp_path: Path, project: Path) -> dict:
    from millrace_plus.router import control

    home = tmp_path / "state-home"
    made = control.create_instance(
        project / "workflow.json", project, state_home=home,
        request_id=uuid.uuid4().hex, expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    started = control.start_run(
        instance, state_home=home, request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    assert started["status"] == "applied", started
    view = control.inspect_run(instance, started["run_id"], state_home=home)
    assert view["status"] == "paused" and view["quiescent"] is True
    assert view["inflight_invocations"] == []
    assert all(row["effective_writer"] == "stopped" for row in view["completions"])
    state = json.loads((project / "foreground/lad-state/session.json").read_bytes())
    return {"state_home": "state-home", "project": "project",
            "instance_id": instance, "run_id": started["run_id"],
            "session_id": state["session_id"]}


@pytest.mark.parametrize("available", [False, True], ids=["unavailable", "restorable"])
def test_interrupted_legacy_guidance_restores_only_available_matching_bytes(
    tmp_path: Path, available: bool
) -> None:
    project = finite_old_domain_project(tmp_path)
    projection = project / "foreground/lad-state/active-role.json"
    blocker = tmp_path / "blocker.json"
    blocker.write_text("{}")
    projection.symlink_to(blocker)
    assert old_command(project, "prepare_role.py", "recon")["outcome"] == "REFUSED"
    projection.unlink()
    file = project / "foreground/lad-state/session.json"
    old = json.loads(file.read_bytes())
    oid = old["active"]["foreground"]
    item = old["occurrences"][oid]
    missing = Path(item["guidance_root"]) / "context/contracts.md"
    missing.unlink()
    if not available:
        # Change the only remaining runtime source BEFORE Core captures it.
        # No other old occurrence exists; reference archive is not a runtime
        # authority source that the initializer can silently select.
        (project / "context/contracts.md").write_text("Changed authored contracts.\n")
    manifest = paused_current_core_for_old_domain(tmp_path, project)
    install_candidate_scripts(project)
    assert explicit_upgrade(tmp_path, manifest)["outcome"] == "SESSION_UPGRADED"
    before = json.loads(file.read_bytes())
    child = subprocess.run(
        [sys.executable, "-B", "-S", str(project / "scripts/prepare_role.py"), "recon"],
        cwd=project / "foreground", env={"PATH": "/usr/bin:/bin"},
        capture_output=True, timeout=20,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    reply = json.loads(child.stdout)
    assert reply["outcome"] == "REFUSED", reply  # Evidence is not a dispatch grant.
    if available:
        assert hashlib.sha256(missing.read_bytes()).hexdigest() == (
            item["guidance_files"]["context/contracts.md"]
        )
        assert "legacy_fresh_replacement_required" in reply["summary"]
    else:
        assert not missing.exists()
        assert "legacy_guidance_unavailable" in reply["summary"]
    retained = json.loads(file.read_bytes())
    assert retained["occurrences"] == before["occurrences"]
    assert retained["events"] == before["events"]
    assert retained["active"] == before["active"]


@pytest.mark.parametrize(
    "window", ["archive", "mapping", "publication", "postpublication"]
)
def test_upgrade_fault_windows_release_context_and_replay_idempotently(
    tmp_path: Path, window: str
) -> None:
    from millrace_plus import catalog
    from millrace_plus.router import control

    case, manifest = genuine_v7(tmp_path, "request")
    project = case / manifest["project"]
    install_candidate_scripts(project)
    home = case / manifest["state_home"]
    view = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    file = project / "foreground/lad-state/session.json"
    old = file.read_bytes()
    program = """
import json,sys
from pathlib import Path
import session_compat,lad_state
class FixtureCrash(BaseException):pass
window=sys.argv[2]
original=session_compat.atomic
def atomic(root,relative,raw,**kwargs):
    if ((window=='archive' and relative.startswith('lad-state/legacy/')) or
        (window=='mapping' and relative=='lad-state/migrations/v1-to-v2.json') or
        (window=='publication' and relative=='lad-state/session.json')):
        raise FixtureCrash()
    return original(root,relative,raw,**kwargs)
session_compat.atomic=atomic
original_load=lad_state.load
def load(root):
    current=json.loads(Path(root,'lad-state/session.json').read_bytes())
    if window=='postpublication' and current['format']=='millrace-lad-session/v2':
        raise FixtureCrash()
    return original_load(root)
lad_state.load=load
try:
    session_compat.upgrade_session(
        Path(sys.argv[1]),session_id=sys.argv[3],instance_id=sys.argv[4],
        run_id=sys.argv[5],state_home=Path(sys.argv[6]),
        expected_state_version=int(sys.argv[7]))
except FixtureCrash:
    print('owned fixture crash observed after context unwound')
else:
    raise AssertionError('fault window not exercised')
"""
    child = subprocess.run(
        [sys.executable, "-B", "-c", program, str(project / "foreground"), window,
         manifest["session_id"], manifest["instance_id"], manifest["run_id"],
         str(home), str(view["state_version"])],
        cwd=project / "scripts",
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(catalog.__file__).parents[1])},
        capture_output=True, timeout=30,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    if window != "postpublication":
        assert file.read_bytes() == old
    else:
        assert json.loads(file.read_bytes())["format"] == "millrace-lad-session/v2"
    result = explicit_upgrade(case, manifest)
    assert result["outcome"] == (
        "SESSION_ALREADY_UPGRADED" if window == "postpublication"
        else "SESSION_UPGRADED"
    ), result
    stable = file.read_bytes()
    assert explicit_upgrade(case, manifest)["outcome"] == "SESSION_ALREADY_UPGRADED"
    assert file.read_bytes() == stable


def test_old_reader_positive_but_invalid_conversion_never_replaces_v1(
    tmp_path: Path,
) -> None:
    case, manifest = genuine_v7(tmp_path, "request")
    project = case / manifest["project"]
    file = project / "foreground/lad-state/session.json"
    state = json.loads(file.read_bytes())
    state["events"][0]["kind"] = "planning_repair"
    rewrite_checksum(file, state)
    before = file.read_bytes()
    # The literal old reader's permissive kind semantics remain unchanged.
    assert old_command(project, "init_session.py", "--project-root", "..")[
        "outcome"
    ] == "EXISTING_SESSION_WITH_WORK"
    assert file.read_bytes() == before
    install_candidate_scripts(project)
    result = explicit_upgrade(case, manifest)
    assert result["outcome"] == "RECONCILE", result
    assert "occurrence_role_history_mismatch" in result["summary"]
    assert file.read_bytes() == before
    assert not (project / "foreground/lad-state/legacy").exists()
    assert not (project / "foreground/lad-state/migrations").exists()


def test_migrated_old_accepted_subset_cannot_be_removed(tmp_path: Path) -> None:
    case, manifest = genuine_v7(tmp_path, "request")
    project = case / manifest["project"]
    install_candidate_scripts(project)
    assert explicit_upgrade(case, manifest)["outcome"] == "SESSION_UPGRADED"
    file = project / "foreground/lad-state/session.json"
    state = json.loads(file.read_bytes())
    state["accepted"].pop(next(iter(state["accepted"])))
    rewrite_checksum(file, state)
    before = file.read_bytes()
    result = explicit_upgrade(case, manifest)
    assert result["outcome"] == "RECONCILE", result
    assert "legacy_history_or_accepted_changed" in result["summary"]
    assert file.read_bytes() == before


def test_migrated_nonrole_history_survives_new_progress_and_refuses_deletion(
    tmp_path: Path,
) -> None:
    from millrace_plus.router import control

    old, _ = frozen_source(tmp_path)
    project = tmp_path / "project"
    shutil.copytree(old / "workflow", project)
    shutil.copyfile(old / "reference/lad_peer.py", project / "scripts/lad_peer.py")
    graph_file = project / "workflow.json"
    graph = json.loads(graph_file.read_bytes())
    graph["bindings"].pop("claude")
    graph["bindings"]["command"]["executable"] = sys.executable
    graph["control_files"].append("scripts/lad_peer.py")
    for plane in graph["planes"].values():
        for name, node in plane["nodes"].items():
            if node["invoke"]["binding"] == "claude":
                node["invoke"].update(
                    binding="command",
                    config={"script": "scripts/lad_peer.py", "args": [name, "clean"]},
                )
    graph_file.write_text(json.dumps(graph))  # Before any old guidance is captured.
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    assert old_command(project, "init_session.py", "--project-root", "..")[
        "outcome"
    ] == "NEW_SESSION"
    assert old_command(project, "setup_intake.py", "--project-root", "..")[
        "outcome"
    ] == "INTAKE_READY_EXISTING"
    for role, outcome in (("recon", "FINDINGS"), ("consultant", "REPAIR_SPEC")):
        assert old_command(project, "prepare_role.py", role)["outcome"] == "READY"
        old_command(project, "lad_peer.py", role, "clean", outcome)
        assert old_command(
            project, "accept_role.py", role, outcome
        )["outcome"] != "REFUSED"
    file = project / "foreground/lad-state/session.json"
    old_state = json.loads(file.read_bytes())
    assert any(row["kind"] == "planning_repair" for row in old_state["events"])
    home = tmp_path / "state-home"
    made = control.create_instance(
        graph_file, project, state_home=home, request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    started = control.start_run(
        instance, state_home=home, request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    assert started["status"] == "applied", started
    manifest = {"state_home": "state-home", "project": "project",
                "instance_id": instance, "run_id": started["run_id"],
                "session_id": old_state["session_id"]}
    # This is genuine old domain-v1 over fresh current finite-v3/store-v8,
    # separate from the retained-wheel genuine-v7 REQUEST/ACK integration.
    install_candidate_scripts(project)
    assert explicit_upgrade(tmp_path, manifest)["outcome"] == "SESSION_UPGRADED"
    shutil.copyfile(
        Path(__file__).parent / "fixtures/lad_peer.py", project / "scripts/lad_peer.py"
    )
    assert old_command(project, "prepare_role.py", "planner")["outcome"] == "READY"
    old_command(project, "lad_peer.py", "planner", "clean")
    assert old_command(
        project, "accept_role.py", "planner", "PASS"
    )["outcome"] == "MANAGER"
    progressed = json.loads(file.read_bytes())
    assert len(progressed["events"]) == len(old_state["events"]) + 1
    progressed["events"] = [row for row in progressed["events"]
                            if row["kind"] != "planning_repair"]
    rewrite_checksum(file, progressed)
    before = file.read_bytes()
    result = old_command(project, "init_session.py", "--project-root", "..")
    assert result["outcome"] == "RECONCILE", result
    assert "legacy_history_or_accepted_changed" in result["summary"]
    assert file.read_bytes() == before


@pytest.mark.parametrize("change", ["v4_tag", "workspace", "cursor", "outcome"])
def test_upgrade_rejects_incompatible_authored_revision_before_publication(
    tmp_path: Path, change: str
) -> None:
    from millrace_plus.router import control

    case, manifest = genuine_v7(tmp_path, "request")
    project = case / manifest["project"]
    install_candidate_scripts(project)
    workflow = project / "workflow.json"
    graph = json.loads(workflow.read_bytes())
    if change == "v4_tag":
        graph["format"] = "millrace-router/v4"  # Same topology and finite bounds.
    elif change == "workspace":
        (project / "other-learning").mkdir()
        graph["planes"]["learning"]["workspace"] = "other-learning"
    elif change == "cursor":
        nodes = graph["planes"]["learning"]["nodes"]
        nodes["replacement_prepare"] = nodes.pop("prepare_learning")
        nodes["receive_request"]["on"]["ASSESS"]["pause"]["resume"] = (
            "replacement_prepare"
        )
    else:
        node = graph["planes"]["learning"]["nodes"]["emit_ack"]
        node["outcomes"].remove("ACK")
        node["on"].pop("ACK")  # The retained seam still requires its exact ACK outcome.
    workflow.write_text(json.dumps(graph))
    home = case / manifest["state_home"]
    view = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    if change == "v4_tag":
        assert view["draft_revision"]["diagnostic"] == "unsupported_graph"
    file = project / "foreground/lad-state/session.json"
    before = file.read_bytes()
    result = explicit_upgrade(case, manifest)
    assert result["outcome"] == "RECONCILE", result
    assert "upgrade_incompatible_authored_revision" in result["summary"]
    assert file.read_bytes() == before
    assert not (project / "foreground/lad-state/legacy").exists()
    assert not (project / "foreground/lad-state/migrations").exists()


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
def test_frozen_v1_debit_primitive_retains_original_ceiling(
    tmp_path: Path, key: str
) -> None:
    import runpy

    retained, _ = frozen_source(tmp_path)
    api = runpy.run_path(str(retained / "workflow/scripts/lad_state.py"))
    state = api["new_state"]("local", "local")
    ceiling = api["CAPS"][key.split(":", 1)[0]]
    for _ in range(ceiling):
        api["debit"](state, [key], str(uuid.uuid4()), "test_fixture")
    before = json.dumps(state, sort_keys=True)
    with pytest.raises(api["LadError"], match="budget_exhausted"):
        api["debit"](state, [key], str(uuid.uuid4()), "test_fixture")
    assert json.dumps(state, sort_keys=True) == before


@pytest.mark.parametrize("fact", [
    "missing_quiescent", "active", "inflight", "unknown_writer",
    "unknown_completion", "missing_draft", "validation_version", "wrong_project",
])
def test_upgrade_refuses_missing_or_contradictory_held_public_facts(
    tmp_path: Path, fact: str
) -> None:
    from millrace_plus import catalog
    from millrace_plus.router import control

    case, manifest = genuine_v7(tmp_path, "request")
    project = case / manifest["project"]
    install_candidate_scripts(project)
    file = project / "foreground/lad-state/session.json"
    before = file.read_bytes()
    home = case / manifest["state_home"]
    view = control.inspect_run(
        manifest["instance_id"], manifest["run_id"], state_home=home
    )
    # Exercise the domain's refusal of missing/unknown/contradictory facts at
    # its real public seam. The real context still owns both locks; the altered
    # projection is a negative validator fixture, not fabricated Core evidence.
    program = '''
import copy,sys
from contextlib import contextmanager
from pathlib import Path
from millrace_plus.router import control
from lad_state import LadError
import session_compat
real_guard=control.exclusive_run_snapshot
real_validation=control.validate_revision
real_identity=control.inspect_instance
fact=sys.argv[2]
@contextmanager
def guard(*args,**kwargs):
    with real_guard(*args,**kwargs) as original:
        view=copy.deepcopy(original)
        if fact=='missing_quiescent': view.pop('quiescent')
        elif fact=='active': view['status']='running'
        elif fact=='inflight': view['inflight_invocations']=[{'unknown':True}]
        elif fact=='unknown_writer': view['invocations'][0]['writer_state']='unknown'
        elif fact=='unknown_completion':
            view['completions'][0]['effective_writer']='unknown'
        elif fact=='missing_draft': view.pop('draft_revision')
        yield view
def validation(*args,**kwargs):
    value=real_validation(*args,**kwargs)
    if fact=='validation_version':
        value['validation_context']['run_state_version']+=1
    return value
def identity(*args,**kwargs):
    value=real_identity(*args,**kwargs)
    if fact=='wrong_project':
        value['project_root']=str(Path(sys.argv[1])/'wrong-project')
    return value
control.exclusive_run_snapshot=guard
control.validate_revision=validation
control.inspect_instance=identity
try:
    session_compat.upgrade_session(
        Path(sys.argv[1]),session_id=sys.argv[3],instance_id=sys.argv[4],
        run_id=sys.argv[5],
        state_home=Path(sys.argv[6]),expected_state_version=int(sys.argv[7]))
except LadError:
    pass
else:
    raise AssertionError('invalid public facts allowed publication')
# Context cleanup must release exclusion after every refusal.
with real_guard(sys.argv[4],sys.argv[5],state_home=Path(sys.argv[6]),
                expected_state_version=int(sys.argv[7])):
    pass
'''
    child = subprocess.run(
        [sys.executable, "-B", "-c", program, str(project / "foreground"), fact,
         manifest["session_id"], manifest["instance_id"], manifest["run_id"],
         str(home), str(view["state_version"])],
        cwd=project / "scripts", capture_output=True, timeout=30,
        env={"PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(catalog.__file__).parents[1])},
    )
    assert child.returncode == 0, child.stdout + child.stderr
    assert file.read_bytes() == before
    assert not (project / "foreground/lad-state/legacy").exists()
    assert not (project / "foreground/lad-state/migrations").exists()
