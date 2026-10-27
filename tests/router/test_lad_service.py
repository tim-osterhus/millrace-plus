"""Finite service policy, target ownership and fake-client child-call proofs."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from millrace_plus.lad_service import (
    OWNER,
    Service,
    claim_target,
    materialize_exact,
    target_lock,
    validate_request,
    validate_service,
)
from millrace_plus.local_intake import IntakeError, canonical, strict_json


def make_service(
    root: Path, *, config_updates: dict[str, object] | None = None
) -> Service:
    """Owned fixture records only. This is not a supported service setup command."""
    home, target = root / "service", root / "target"
    home.mkdir(parents=True)
    target.mkdir()
    (target / "dirty.txt").write_bytes(b"user-owned dirty file\n")
    info = target.stat()
    config = {
        "format": "millrace-plus-lad-service/v1",
        "service_id": uuid.uuid4().hex,
        "service_home": str(home),
        "target_root": str(target),
        "target_identity": {
            "path": str(target),
            "device": info.st_dev,
            "inode": info.st_ino,
        },
        "os_home": "local",
        "os_profile": {"path": str(root / "profile.json"), "sha256": "a" * 64},
        "worker_definition": {
            "selector": "bundled:lad",
            "version": "0.23.2",
            "sha256": "b" * 64,
        },
        "settings_sha256": "c" * 64,
    }
    config.update(config_updates or {})
    (home / "service.json").write_bytes(canonical(config) + b"\n")
    service = Service(home)
    claim_target(service.config, protected_roots=(home,))
    service.intake.initialize()
    return service


def submit(
    service: Service, key: str, payload: bytes = b"original request\r\n"
) -> dict[str, object]:
    request = service.home.parent / (key + ".md")
    request.write_bytes(payload)
    return service.submit(key, request)


def test_claim_materialization_is_exact_and_restartable(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    receipt = submit(service, "first")
    claim = service.claim_next(writer_guard=lambda: None)
    assert claim is not None
    assert claim["task_id"] == receipt["task_id"]
    assert claim["call_key"] == "task_" + str(receipt["task_id"])
    root = service.home / "tasks" / str(receipt["task_id"])
    root.mkdir(parents=True)
    # Crash gap after request but before the claim file is materialized.
    materialize_exact(root / "request.md", b"original request\r\n")
    reopened = Service(service.home)
    reopened.materialize_task(str(receipt["task_id"]))
    assert (root / "request.md").read_bytes() == b"original request\r\n"
    assert strict_json((root / "claim.json").read_bytes()) == claim
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    reopened.materialize_task(str(receipt["task_id"]))
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before
    (root / "request.md").write_bytes(b"conflicting file")
    with pytest.raises(IntakeError, match="materialization_conflict"):
        reopened.materialize_task(str(receipt["task_id"]))
    assert (root / "request.md").read_bytes() == b"conflicting file"
    assert reopened.intake.tasks()[0]["status"] == "claimed"
    assert not (service.home / "calls").exists()


def test_each_task_preserves_separate_request_authority(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    first = submit(service, "first", b"task one")
    second = submit(service, "second", b"task two")
    assert first["task_id"] != second["task_id"]
    assert second["sequence"] == first["sequence"] + 1
    assert (
        first["task_id"]
        == uuid.uuid5(uuid.UUID(hex=service.service_id), "lad-task/first").hex
    )
    service.cancel(str(first["task_id"]), uuid.uuid4().hex)
    assert service.claim_next(writer_guard=lambda: None)["task_id"] == second["task_id"]
    service.materialize_task(str(second["task_id"]))
    rows = service.intake.tasks()
    assert [row["request_bytes"] for row in rows] == [b"task one", b"task two"]
    assert not (service.home / "tasks" / str(first["task_id"])).exists()


def test_target_owner_preserves_dirty_files_and_refuses_other_owner(
    tmp_path: Path,
) -> None:
    service = make_service(tmp_path)
    target = Path(str(service.config["target_root"]))
    before = (target / "dirty.txt").read_bytes()
    marker = (target / OWNER).read_bytes()
    owner = claim_target(service.config, protected_roots=(service.home,))
    assert owner["workflow_kind"] == "lad"
    assert (target / OWNER).read_bytes() == marker
    other = dict(service.config, service_id=uuid.uuid4().hex)
    with pytest.raises(IntakeError, match="target_owner_conflict"):
        claim_target(other, protected_roots=(service.home,))
    with pytest.raises(IntakeError, match="target_owner_conflict"):
        claim_target(service.config, protected_roots=(service.home,), kind="cicd")
    assert (target / "dirty.txt").read_bytes() == before
    assert (target / OWNER).read_bytes() == marker


def test_exclusive_target_creation_competes_without_overwrite(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    target = Path(str(service.config["target_root"]))
    # Isolated fixture before any claim, never production cleanup.
    (target / OWNER).unlink()
    configs = [dict(service.config, service_id=uuid.uuid4().hex) for _ in range(8)]

    def attempt(config: dict[str, object]) -> str:
        try:
            owner = claim_target(config, protected_roots=(service.home,))
            return str(owner["service_id"])
        except IntakeError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, configs))
    refusals = {"target_owner_conflict", "invalid_json"}
    assert sum(result not in refusals for result in results) == 1
    owner = strict_json((target / OWNER).read_bytes())
    assert owner["service_id"] in results
    assert (target / "dirty.txt").read_bytes() == b"user-owned dirty file\n"


def test_target_lock_is_short_and_excludes_competing_claim(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    first = submit(service, "first")
    with target_lock(service.config, protected_roots=(service.home,)):
        with pytest.raises(IntakeError, match="target_busy"):
            service.claim_next(
                writer_guard=lambda: pytest.fail("busy marker reached writer check")
            )
    assert service.claim_next(writer_guard=lambda: None)["task_id"] == first["task_id"]


def test_unknown_writer_blocks_before_claim_or_materialization(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    submit(service, "first")

    def unknown() -> None:
        raise IntakeError("writer_unknown")

    with pytest.raises(IntakeError, match="writer_unknown"):
        service.claim_next(writer_guard=unknown)
    assert service.intake.tasks()[0]["status"] == "queued"
    assert not (service.home / "tasks").exists()
    assert not (service.home / "calls").exists()


@pytest.mark.parametrize(
    "kind", ["alias", "marker_link", "marker_hardlink", "changed_target", "overlap"]
)
def test_target_refusals_preserve_target(tmp_path: Path, kind: str) -> None:
    service = make_service(tmp_path)
    target = Path(str(service.config["target_root"]))
    dirty = (target / "dirty.txt").read_bytes()
    if kind == "alias":
        alias = tmp_path / "alias"
        alias.symlink_to(target, target_is_directory=True)
        service.config["target_root"] = str(alias)
    elif kind in {"marker_link", "marker_hardlink"}:
        saved = tmp_path / "saved-owner"
        (target / OWNER).rename(saved)
        if kind == "marker_link":
            (target / OWNER).symlink_to(saved)
        else:
            os.link(saved, target / OWNER)
    elif kind == "changed_target":
        target.rename(tmp_path / "old-target")
        target.mkdir()
        (target / "dirty.txt").write_bytes(dirty)
    else:
        with pytest.raises(IntakeError, match="target_overlap"):
            claim_target(service.config, protected_roots=(target / "catalog",))
        return
    with pytest.raises((IntakeError, OSError)):
        with target_lock(service.config, protected_roots=(service.home,)):
            pytest.fail("unsafe target acquired")
    assert (target / "dirty.txt").read_bytes() == dirty


def test_service_configuration_is_frozen_in_database(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    submit(service, "first")
    path = service.home / "service.json"
    config = strict_json(path.read_bytes())
    config["settings_sha256"] = "d" * 64
    path.write_bytes(canonical(config) + b"\n")
    before = (service.home / "intake.sqlite3").read_bytes()
    with pytest.raises(IntakeError, match="invalid_intake"):
        Service(service.home).status()
    assert (service.home / "intake.sqlite3").read_bytes() == before


@pytest.mark.parametrize("payload", [b"", b" \r\n", b"\xff", b"a" * 65537])
def test_request_refusals_precede_intake_mutation(
    tmp_path: Path, payload: bytes
) -> None:
    service = make_service(tmp_path)
    before = (service.home / "intake.sqlite3").read_bytes()
    with pytest.raises(IntakeError):
        submit(service, "bad", payload)
    assert (service.home / "intake.sqlite3").read_bytes() == before


def test_maximum_utf8_request_and_exact_replay(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    payload = ("é" * 32768).encode("utf-8")
    first = submit(service, "maximum", payload)
    replay = submit(service, "maximum", payload)
    assert replay == first | {"disposition": "replayed"}
    assert service.intake.tasks()[0]["request_bytes"] == payload
    assert first["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    validate_request(b"\x01" * 65535 + b"x")


def test_strict_service_records_and_materialized_links_refuse(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(IntakeError, match="invalid_fields"):
        validate_service(service.config | {"extra": True})
    with pytest.raises(IntakeError):
        validate_service(
            service.config
            | {
                "target_identity": {
                    "path": service.config["target_root"],
                    "device": True,
                    "inode": 1,
                }
            }
        )
    original = tmp_path / "original"
    original.write_bytes(b"same")
    linked = tmp_path / "linked"
    linked.symlink_to(original)
    with pytest.raises(IntakeError):
        materialize_exact(linked, b"same")
    assert original.read_bytes() == b"same"


def test_failed_materialization_retains_claim_and_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(tmp_path)
    receipt = submit(service, "first")
    service.claim_next(writer_guard=lambda: None)
    real_fsync = os.fsync

    def fail(_: int) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError):
        service.materialize_task(str(receipt["task_id"]))
    monkeypatch.setattr(os, "fsync", real_fsync)
    root = service.home / "tasks" / str(receipt["task_id"])
    assert (root / "request.md").read_bytes() == b"original request\r\n"
    assert not (root / "claim.json").exists()
    assert service.intake.tasks()[0]["status"] == "claimed"
    service.materialize_task(str(receipt["task_id"]))
    assert (root / "claim.json").is_file()


def test_public_lad_cli_runs_with_core_truly_absent(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    request = tmp_path / "request.md"
    request.write_bytes(b"Core-free original request\r\n")
    plus_source = Path(__file__).resolve().parents[2] / "src"
    env = dict(os.environ, PYTHONPATH=str(plus_source), PYTHONDONTWRITEBYTECODE="1")
    # -S removes installed packages. The only supplied source root is Plus.
    # cli() is the installed console script's public Python entrypoint.
    entrypoint = """
import importlib.util
import sys
assert importlib.util.find_spec("millrace") is None
from millrace_plus.router.cli import cli
try:
    cli()
except SystemExit:
    assert importlib.util.find_spec("millrace") is None
    assert not any(k == "millrace" or k.startswith("millrace.") for k in sys.modules)
    raise
"""

    def invoke(*arguments: str) -> object:
        result = subprocess.run(
            [sys.executable, "-S", "-c", entrypoint, "lad", *arguments],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8")
        assert result.stderr == b""
        return strict_json(result.stdout)

    receipt = invoke(
        "submit",
        "--service-home",
        str(service.home),
        "--key",
        "first",
        "--request",
        str(request),
    )
    assert receipt["disposition"] == "accepted"
    task_id = str(receipt["task_id"])
    state = invoke(
        "status",
        "--service-home",
        str(service.home),
        "--task",
        task_id,
    )
    assert state["tasks"][0]["request_bytes"] == "Core-free original request\r\n"
    cancelled = invoke(
        "cancel",
        "--service-home",
        str(service.home),
        "--task",
        task_id,
        "--request-id",
        uuid.uuid4().hex,
    )
    assert cancelled["disposition"] == "cancelled-before-start"
    state = invoke("status", "--service-home", str(service.home))
    assert state["tasks"][0]["status"] == "cancelled"
    assert state["tasks"][0]["claim"] is None
    assert not (service.home / "calls").exists()


@pytest.mark.parametrize("os_home", ["1", "prod.local", "9" + "a" * 95])
def test_service_accepts_existing_os_home_names(tmp_path: Path, os_home: str) -> None:
    service = make_service(tmp_path)
    assert validate_service(service.config | {"os_home": os_home})["os_home"] == os_home


@pytest.mark.parametrize(
    "os_home", ["", "a/b", "a" * 97, ".local", "_local", "-local", "a b"]
)
def test_service_refuses_invalid_os_home_names(tmp_path: Path, os_home: str) -> None:
    service = make_service(tmp_path)
    with pytest.raises(IntakeError):
        validate_service(service.config | {"os_home": os_home})


@pytest.mark.parametrize("invalid_id", ["f" * 31, "f" * 33, "G" * 32])
def test_os_home_names_do_not_relax_service_or_task_ids(
    tmp_path: Path, invalid_id: str
) -> None:
    service = make_service(tmp_path)
    with pytest.raises(IntakeError, match="invalid_id"):
        validate_service(service.config | {"service_id": invalid_id})
    with pytest.raises(IntakeError, match="invalid_id"):
        service.status(invalid_id)


@pytest.mark.parametrize("key", ["1", "prod.local", "a" * 65])
def test_os_home_names_do_not_relax_intake_keys(tmp_path: Path, key: str) -> None:
    service = make_service(tmp_path)
    database = service.home / "intake.sqlite3"
    before = database.read_bytes()
    with pytest.raises(IntakeError, match="invalid_intake_key"):
        service.intake.submit(key, b"request")
    assert database.read_bytes() == before


@pytest.mark.parametrize("mutation", ["succeeded", "failed", "attached_claim"])
@pytest.mark.parametrize("operation", ["reload", "submit", "claim"])
def test_queued_cancel_aftermath_corruption_refuses_and_preserves_evidence(
    tmp_path: Path, mutation: str, operation: str
) -> None:
    service = make_service(tmp_path)
    accepted = submit(service, "first")
    task_id = str(accepted["task_id"])
    cancelled = service.cancel(task_id, uuid.uuid4().hex)
    task = service.intake.tasks(task_id)[0]
    assert task["status"] == "cancelled" and task["claim"] is None
    assert task["terminal_receipt"] == cancelled
    database = service.home / "intake.sqlite3"
    # Isolated retained-state corruption. This is never a supported mutation.
    with sqlite3.connect(database) as connection:
        if mutation == "attached_claim":
            claim = service._build_claim(task)
            connection.execute(
                "UPDATE tasks SET claim=? WHERE task_id=?",
                (canonical(claim).decode(), task_id),
            )
        else:
            connection.execute(
                "UPDATE tasks SET status=? WHERE task_id=?", (mutation, task_id)
            )
    before = database.read_bytes()
    reopened = Service(service.home)
    with pytest.raises(IntakeError, match="invalid_intake"):
        if operation == "reload":
            reopened.status()
        elif operation == "submit":
            reopened.submit("later", tmp_path / "first.md")
        else:
            # Fixture guard only. No child or controller operation is dispatched.
            reopened.claim_next(writer_guard=lambda: None)
    assert database.read_bytes() == before
    assert not (service.home / "calls").exists()
    assert not (service.home / "tasks").exists()


@pytest.mark.parametrize(
    "change",
    ["claim_sha256", "request_sha256", "service_id", "worker_definition_sha256"],
)
def test_shared_validator_refuses_changed_active_authority_before_call_read(
    tmp_path, change
):
    from millrace_plus.lad_service import validate_shared_target

    service = make_service(tmp_path)
    receipt = submit(service, "one")
    claim = service.claim_next(writer_guard=lambda: None)
    service.materialize_task(str(receipt["task_id"]))
    config = service.config
    record = {
        "format": "millrace-lad-target/v1",
        "service_id": service.service_id,
        "task_id": receipt["task_id"],
        "request_sha256": claim["request_sha256"],
        "target_root": config["target_root"],
        "target_identity": config["target_identity"],
        "claim_sha256": hashlib.sha256(canonical(claim)).hexdigest(),
        "parent_instance_id": "1" * 32,
        "parent_run_id": "2" * 32,
        "worker_definition_sha256": config["worker_definition"]["sha256"],
    }
    record[change] = "f" * (32 if change == "service_id" else 64)
    before = service.status()
    with pytest.raises(IntakeError):
        validate_shared_target(record, project=tmp_path)
    assert service.status() == before
    assert not (service.home / "calls").exists()
    assert (
        Path(config["target_root"]) / "dirty.txt"
    ).read_bytes() == b"user-owned dirty file\n"


def test_advance_task_dispatches_exact_validated_bytes_after_request_swap(
    tmp_path, monkeypatch
):
    import copy

    from persistent_lad_support import PARENT, Clock, FakeClient, journal, setup

    fixture = setup(tmp_path, monkeypatch)
    service_root = tmp_path / "owned"
    target_root = service_root / "target"
    fixture["settings"]["host_paths"]["lad_target"] = str(target_root)
    service = make_service(
        service_root,
        config_updates={
            "os_profile": fixture["request"]["os_profile"],
            "worker_definition": fixture["request"]["definition"],
            "settings_sha256": hashlib.sha256(
                canonical(fixture["settings"])
            ).hexdigest(),
        },
    )
    accepted = submit(service, "one", b"Exact task\r\n")
    task_id = str(accepted["task_id"])
    claim = service.claim_next(writer_guard=lambda: None)
    assert claim is not None
    service.materialize_task(task_id)
    target = {
        "format": "millrace-lad-target/v1",
        "service_id": service.service_id,
        "task_id": task_id,
        "request_sha256": claim["request_sha256"],
        "target_root": service.config["target_root"],
        "target_identity": service.config["target_identity"],
        "claim_sha256": hashlib.sha256(canonical(claim)).hexdigest(),
        "parent_instance_id": PARENT,
        "parent_run_id": "5" * 32,
        "worker_definition_sha256": service.config["worker_definition"]["sha256"],
    }
    request = fixture["request"]
    for key in ("call_home", "call_key", "definition", "output_ref"):
        request[key] = claim[key]
    request["parent"]["run_id"] = target["parent_run_id"]
    request["inputs"]["lad_target"] = target
    raw = canonical(request) + b"\n"
    fixture["file"].write_bytes(raw)
    foreign_request = copy.deepcopy(request)
    foreign_request["inputs"]["request_markdown"] = "unvalidated replacement"
    foreign = tmp_path / "foreign.json"
    foreign.write_bytes(canonical(foreign_request))

    class SwappingClient(FakeClient):
        swapped = False

        def read(self, instance, run=None, *, deadline):
            if instance == PARENT and not self.swapped:
                self.swapped = True
                fixture["file"].unlink()
                fixture["file"].symlink_to(foreign)
            if instance == PARENT and run is not None:
                return {"id": run, "instance_id": instance, "planes": {}}
            return super().read(instance, run, deadline=deadline)

    connection, timer = SwappingClient(fixture), Clock()
    result = service.advance_task(
        task_id,
        fixture["file"],
        cwd=fixture["parent"],
        client=connection,
        clock=timer,
        sleep=timer.sleep,
    )
    assert connection.swapped and fixture["file"].is_symlink()
    assert result["outcome"] == "CHILD_RUNNING"
    saved = journal(fixture).load()
    assert saved["request_raw"] == raw.decode("utf-8")
    assert saved["request"]["inputs"] == request["inputs"]
    assert saved["claim_sha256"] == target["claim_sha256"]
    assert [row["operation"] for row in connection.sent] == [
        "create_instance",
        "start_run",
    ]
    inputs = strict_json(
        (Path(saved["paths"]["project"]) / "subworkflow-inputs.json").read_bytes()
    )
    assert inputs["inputs"]["request_markdown"] == "Exact task\r\n"
    assert foreign.read_bytes() == canonical(foreign_request)
