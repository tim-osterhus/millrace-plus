"""Atomic storage proofs without Core, OS, providers, or workflow dispatch."""

from __future__ import annotations

import hashlib
import multiprocessing
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from millrace_plus.local_intake import (
    Intake,
    IntakeError,
    canonical,
    fields,
    strict_json,
)

SERVICE_ID = "83bfa191d799448cbcf6a663fc72c375"


def _payload(raw: bytes) -> None:
    if not isinstance(raw, bytes) or not raw.decode("utf-8").strip():
        raise IntakeError("invalid_payload")


def _claim(value: dict[str, object]) -> None:
    fields(value, {"task_id", "request_sha256"})


def _decision(value: dict[str, object]) -> None:
    fields(value, {"task_id", "decision", "evidence"})


def _receipt(request_id: str, intent: dict[str, object]) -> dict[str, object]:
    return {"request_id": request_id, "intent": intent}


def _aftermath(
    task: dict[str, object], intent: dict[str, object], receipt: dict[str, object]
) -> None:
    if intent["decision"] == "cancel-queued" and (
        task["status"] != "cancelled"
        or task["claim"] is not None
        or task["terminal_receipt"] != receipt
    ):
        raise IntakeError("invalid_intake")


def store(path: Path) -> Intake:
    return Intake(
        path,
        SERVICE_ID,
        validate_payload=_payload,
        validate_claim=_claim,
        validate_decision=_decision,
        validate_decision_aftermath=_aftermath,
        task_id_for_key=lambda key: (
            uuid.uuid5(uuid.UUID(hex=SERVICE_ID), "test/" + key).hex
        ),
        decision_receipt=_receipt,
        authority_sha256="a" * 64,
    )


def test_submit_atomic_fifo_identity_and_replay(tmp_path: Path) -> None:
    path = tmp_path / "intake.sqlite3"
    store(path).initialize()
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(
            pool.map(lambda _: store(path).submit("task_a", b"original\r\n"), range(16))
        )
    assert {receipt["sequence"] for receipt in receipts} == {1}
    assert sum(receipt["disposition"] == "accepted" for receipt in receipts) == 1
    assert {receipt["accepted_at"] for receipt in receipts} == {
        receipts[0]["accepted_at"]
    }
    expected = uuid.uuid5(uuid.UUID(hex=SERVICE_ID), "test/task_a").hex
    assert {receipt["task_id"] for receipt in receipts} == {expected}
    with pytest.raises(IntakeError, match="intake_key_conflict"):
        store(path).submit("task_a", b"original\n")
    with ThreadPoolExecutor(max_workers=8) as pool:
        other = list(
            pool.map(
                lambda n: store(path).submit("item_" + str(n), str(n).encode()),
                range(12),
            )
        )
    assert sorted(receipt["sequence"] for receipt in other) == list(range(2, 14))
    tasks = store(path).tasks()
    assert tasks[0]["request_bytes"] == b"original\r\n"
    assert len(tasks) == 13
    assert tasks[0]["request_sha256"] == hashlib.sha256(b"original\r\n").hexdigest()


def _build_claim(task: dict[str, object]) -> dict[str, object]:
    return {key: task[key] for key in ("task_id", "request_sha256")}


def test_single_active_fifo_claim_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "intake.sqlite3"
    store(path).initialize()
    first = store(path).submit("first", b"first")
    store(path).submit("second", b"second")

    def attempt(_: int) -> str:
        try:
            result = store(path).claim(_build_claim)
            assert result is not None
            return str(result["task_id"])
        except IntakeError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count(first["task_id"]) == 1
    assert results.count("active_task") == 7
    for status in ("claimed", "running", "held"):
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE tasks SET status=? WHERE task_id=?",
                (status, first["task_id"]),
            )
        with pytest.raises(IntakeError, match="active_task"):
            store(path).claim(_build_claim)
    tasks = store(path).tasks()
    assert tasks[1]["status"] == "queued"
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE tasks SET status='claimed' WHERE sequence=2")


def test_queued_cancel_and_exact_decision_replay(tmp_path: Path) -> None:
    path = tmp_path / "intake.sqlite3"
    intake = store(path)
    intake.initialize()
    first = intake.submit("first", b"first")
    second = intake.submit("second", b"second")
    request_id = uuid.uuid4().hex
    intent = {
        "task_id": first["task_id"],
        "decision": "cancel-queued",
        "evidence": None,
    }
    receipt = intake.decide(
        request_id, intent, check=lambda _: None, cancel_queued=True
    )
    assert (
        intake.decide(
            request_id,
            intent,
            check=lambda _: pytest.fail("replay reran policy"),
            cancel_queued=True,
        )
        == receipt
    )
    with pytest.raises(IntakeError, match="decision_conflict"):
        intake.decide(
            request_id, intent | {"evidence": "changed"}, check=lambda _: None
        )
    row = intake.tasks(str(first["task_id"]))[0]
    assert row["status"] == "cancelled" and row["claim"] is None
    assert row["terminal_receipt"] == receipt
    assert intake.claim(_build_claim)["task_id"] == second["task_id"]
    with pytest.raises(IntakeError, match="task_not_queued"):
        intake.decide(
            uuid.uuid4().hex,
            intent | {"task_id": second["task_id"]},
            check=lambda _: None,
            cancel_queued=True,
        )


def test_intake_failure_preserves_committed_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "intake.sqlite3"
    intake = store(path)
    intake.initialize()
    original = intake.submit("first", b"keep")
    real_connect = sqlite3.connect

    class FailingCommit(sqlite3.Connection):
        def commit(self) -> None:
            raise sqlite3.OperationalError("simulated disk full at commit")

    def failing(*args, **kwargs):
        return real_connect(*args, **kwargs, factory=FailingCommit)

    monkeypatch.setattr(sqlite3, "connect", failing)
    with pytest.raises(IntakeError, match="intake_io_error"):
        intake.submit("lost", b"uncommitted")
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    assert intake.tasks()[0]["task_id"] == original["task_id"]
    assert len(intake.tasks()) == 1
    assert intake.submit("second", b"next")["sequence"] == 2
    raw = path.read_bytes()
    with pytest.raises(FileExistsError):
        intake.initialize()
    assert path.read_bytes() == raw


def _interrupted(path: str, phase: str) -> None:
    if phase == "before-commit":
        with store(Path(path))._connection(write=True) as connection:
            connection.execute(
                "UPDATE tasks SET request_bytes=? WHERE sequence=1", (b"broken",)
            )
            os._exit(23)
    store(Path(path)).submit("committed", b"durable")
    os._exit(24)


@pytest.mark.parametrize("phase", ["before-commit", "after-commit"])
def test_process_interruption_preserves_transaction_boundary(
    tmp_path: Path, phase: str
) -> None:
    path = tmp_path / "intake.sqlite3"
    intake = store(path)
    intake.initialize()
    intake.submit("first", b"original")
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_interrupted, args=(str(path), phase))
    process.start()
    process.join(15)
    if process.is_alive():
        process.kill()
        process.join(5)
        pytest.fail("interruption peer exceeded deadline")
    assert process.exitcode == (23 if phase == "before-commit" else 24)
    # A write connection performs SQLite hot-journal rollback after abrupt death.
    replay = intake.submit("first", b"original")
    assert replay["disposition"] == "replayed"
    tasks = intake.tasks()
    assert tasks[0]["request_bytes"] == b"original"
    assert len(tasks) == (1 if phase == "before-commit" else 2)


@pytest.mark.parametrize(
    "kind", ["garbage", "payload", "metadata", "receipt", "decision"]
)
def test_corrupt_intake_is_preserved(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "intake.sqlite3"
    intake = store(path)
    intake.initialize()
    receipt = intake.submit("first", b"original")
    if kind == "garbage":
        path.write_bytes(b"not a sqlite database")
    else:
        with sqlite3.connect(path) as connection:
            if kind == "payload":
                connection.execute("UPDATE tasks SET request_bytes=?", (b"changed",))
            elif kind == "metadata":
                connection.execute("UPDATE metadata SET authority_sha256='changed'")
            elif kind == "receipt":
                connection.execute("UPDATE tasks SET submission_receipt='{}'")
            else:
                connection.execute(
                    "INSERT INTO decisions VALUES (?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        receipt["task_id"],
                        "{}",
                        "{}",
                    ),
                )
    before = path.read_bytes()
    with pytest.raises(IntakeError):
        intake.submit("second", b"never accepted")
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":1e999}',
        b'{"a":"\\ud800"}',
    ],
)
def test_strict_json_rejects_duplicate_and_nonfinite_values(raw: bytes) -> None:
    with pytest.raises(IntakeError):
        strict_json(raw)


def test_read_only_status_does_not_change_database(tmp_path: Path) -> None:
    path = tmp_path / "intake.sqlite3"
    intake = store(path)
    intake.initialize()
    intake.submit("first", b"hello")
    before = path.read_bytes()
    assert intake.tasks()[0]["request_bytes"] == b"hello"
    assert path.read_bytes() == before
    assert not Path(str(path) + "-journal").exists()
    assert canonical({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_json_records_require_utf8(encoding: str) -> None:
    with pytest.raises(IntakeError, match="invalid_json"):
        strict_json('{"value":"retained"}'.encode(encoding))


def test_terminal_receipt_cannot_borrow_another_tasks_decision(tmp_path: Path) -> None:
    path = tmp_path / "intake.sqlite3"
    intake = store(path)
    intake.initialize()
    first = intake.submit("first", b"first")
    second = intake.submit("second", b"second")
    intent = {
        "task_id": first["task_id"],
        "decision": "cancel-queued",
        "evidence": None,
    }
    receipt = intake.decide(
        uuid.uuid4().hex, intent, check=lambda _: None, cancel_queued=True
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE tasks SET status='cancelled',terminal_receipt=? WHERE task_id=?",
            (canonical(receipt).decode(), second["task_id"]),
        )
    before = path.read_bytes()
    with pytest.raises(IntakeError, match="invalid_intake"):
        intake.claim(_build_claim)
    assert path.read_bytes() == before


def test_lifecycle_pin_and_record_cas_are_atomic(tmp_path: Path) -> None:
    intake = store(tmp_path / "intake.sqlite3")
    intake.initialize()
    intent = {"core_state_home": "/pinned/core", "create_request_id": uuid.uuid4().hex}
    initial = {"reservation": "unsent"}
    intake.initialize_lifecycle(intent, initial)
    assert intake.lifecycle() == (intent, initial)
    updated = {"reservation": "unknown"}
    intake.save_lifecycle(intent, initial, updated)
    with pytest.raises(IntakeError, match="lifecycle_conflict"):
        intake.save_lifecycle(intent, initial, {"reservation": "observed"})
    with pytest.raises(IntakeError, match="lifecycle_conflict"):
        intake.save_lifecycle(
            intent | {"core_state_home": "/another"}, updated, initial
        )
    assert store(intake.path).lifecycle() == (intent, updated)


def test_terminal_task_cas_and_decision_commit_once(tmp_path: Path) -> None:
    intake = store(tmp_path / "intake.sqlite3")
    intake.initialize()
    task_id = intake.submit("first", b"keep exact bytes")["task_id"]
    claim = intake.claim(_build_claim)
    row = intake.tasks(task_id)[0]
    request_id = uuid.uuid4().hex
    intent = {"task_id": task_id, "decision": "terminal", "evidence": {"checked": True}}
    with pytest.raises(IntakeError, match="stale_task"):
        intake.decide(
            request_id,
            intent,
            check=lambda _: None,
            terminal_status="failed",
            expected_version=row["version"] + 1,
            expected_claim=claim,
        )
    assert intake.tasks(task_id)[0]["status"] == "claimed"
    receipt = intake.decide(
        request_id,
        intent,
        check=lambda _: None,
        terminal_status="failed",
        expected_version=row["version"],
        expected_claim=claim,
    )
    assert store(intake.path).tasks(task_id)[0]["version"] == row["version"] + 1
    assert (
        intake.decide(
            request_id,
            intent,
            check=lambda _: pytest.fail("replay checked again"),
            terminal_status="failed",
            expected_version=row["version"],
            expected_claim=claim,
        )
        == receipt
    )
    assert intake.tasks(task_id)[0]["version"] == row["version"] + 1
    assert intake.tasks(task_id)[0]["request_bytes"] == b"keep exact bytes"


def test_combined_lifecycle_tasks_validates_once_and_preserves_conversions(
    tmp_path: Path, monkeypatch
) -> None:
    intake = store(tmp_path / "intake.sqlite3")
    intake.initialize()
    intent, record = {"home": "/pinned/core"}, {"stops": [], "maintenance": {}}
    intake.initialize_lifecycle(intent, record)
    intake.submit("first", b"exact original\r\n")
    intake.submit("second", "Second request: \u03c0\n".encode())
    intake.claim(_build_claim)
    expected = (*intake.lifecycle(), intake.tasks())
    validate = intake._validate
    calls = []

    def checked(connection):
        calls.append(connection)
        return validate(connection)

    monkeypatch.setattr(intake, "_validate", checked)
    observed = intake._lifecycle_tasks()
    assert observed == expected
    assert [row["sequence"] for row in observed[2]] == [1, 2]
    assert observed[2][0]["request_bytes"] == b"exact original\r\n"
    assert observed[2][0]["claim"] == _build_claim(observed[2][0])
    assert len(calls) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        calls[0].execute("SELECT 1")


def test_combined_lifecycle_tasks_keeps_one_snapshot_across_competing_commit(
    tmp_path: Path, monkeypatch
) -> None:
    intake = store(tmp_path / "intake.sqlite3")
    intake.initialize()
    intent, initial = {"home": "/pinned/core"}, {"stop": False}
    intake.initialize_lifecycle(intent, initial)
    task_id = intake.submit("first", b"original task\n")["task_id"]
    task = intake.tasks()[0]
    claim = _build_claim(task)
    updated = {"stop": True}
    # WAL is only this test's competing-commit fixture, never product policy.
    with sqlite3.connect(intake.path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    read_lifecycle = intake._read_lifecycle
    commits = []

    def commit_after_lifecycle(connection):
        result = read_lifecycle(connection)
        if not commits:
            with sqlite3.connect(intake.path) as contender:
                contender.execute(
                    "UPDATE lifecycle SET record=?", (canonical(updated).decode(),)
                )
                contender.execute(
                    "UPDATE tasks SET status='claimed',claim=?,version=version+1 "
                    "WHERE task_id=?", (canonical(claim).decode(), task_id)
                )
            commits.append(True)
        return result

    monkeypatch.setattr(intake, "_read_lifecycle", commit_after_lifecycle)
    first = intake._lifecycle_tasks()
    assert commits == [True]
    assert first == (intent, initial, [task])
    second = intake._lifecycle_tasks()
    assert second[:2] == (intent, updated)
    assert second[2][0]["status"] == "claimed"
    assert second[2][0]["claim"] == claim
    assert second[2][0]["version"] == task["version"] + 1


@pytest.mark.parametrize("fault", ["payload", "claim", "lifecycle_missing"])
def test_combined_lifecycle_tasks_preserves_refusal_and_bytes(
    tmp_path: Path, fault: str
) -> None:
    intake = store(tmp_path / "intake.sqlite3")
    intake.initialize()
    intake.initialize_lifecycle({"home": "/pinned/core"}, {"stop": False})
    intake.submit("first", b"original task\n")
    with sqlite3.connect(intake.path) as connection:
        if fault == "payload":
            connection.execute("UPDATE tasks SET request_bytes=?", (b"\xff",))
        elif fault == "claim":
            connection.execute("UPDATE tasks SET status='claimed',claim='{}'")
        else:
            connection.execute("DROP TABLE lifecycle")
    before = intake.path.read_bytes()
    with pytest.raises(IntakeError) as existing:
        intake.lifecycle()
    with pytest.raises(IntakeError) as combined:
        intake._lifecycle_tasks()
    assert combined.value.code == existing.value.code
    assert intake.path.read_bytes() == before
