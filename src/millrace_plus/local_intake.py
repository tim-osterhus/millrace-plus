"""Transactional local FIFO storage. Workflow policy and execution stay outside it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

__all__ = ["IntakeError", "Intake", "canonical", "strict_json", "fields", "identity"]

KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
ACTIVE = frozenset({"claimed", "running", "held"})
STATES = ACTIVE | {"queued", "succeeded", "failed", "cancelled"}
RECEIPT = "millrace-plus-intake-receipt/v1"
_SCHEMA = """
CREATE TABLE metadata (
 service_id TEXT NOT NULL, schema_version INTEGER NOT NULL,
 authority_sha256 TEXT NOT NULL
);
CREATE TABLE tasks (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id TEXT NOT NULL UNIQUE, intake_key TEXT NOT NULL UNIQUE,
 request_bytes BLOB NOT NULL, request_sha256 TEXT NOT NULL,
 submitted_at TEXT NOT NULL, status TEXT NOT NULL,
 claim TEXT, terminal_receipt TEXT, version INTEGER NOT NULL,
 submission_receipt TEXT NOT NULL,
 CHECK (status IN (
  'queued','claimed','running','held','succeeded','failed','cancelled'
 )),
 CHECK (version > 0)
);
CREATE UNIQUE INDEX one_active ON tasks ((1))
 WHERE status IN ('claimed','running','held');
CREATE TABLE decisions (
 request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 intent TEXT NOT NULL, receipt TEXT NOT NULL
);
"""


class IntakeError(Exception):
    """Expected refusal. Retained state must be preserved for inspection."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def fields(value: object, names: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != names:
        raise IntakeError("invalid_fields")
    return cast(dict[str, object], value)


def identity(value: object) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise IntakeError("invalid_id")
    return value


def canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise IntakeError("invalid_json") from exc


def strict_json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise IntakeError("invalid_json")
            result[key] = value
        return result

    def constant(_: str) -> object:
        raise IntakeError("invalid_json")

    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
        canonical(value)  # Reject nonfinite exponent overflow and invalid Unicode.
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise IntakeError("invalid_json") from exc


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _date(value: object) -> None:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise IntakeError("invalid_intake") from exc


class Intake:
    """One local database, immutable payloads, and a transaction per operation."""

    def __init__(
        self, path: Path, service_id: str, *,
        validate_payload: Callable[[bytes], None],
        validate_claim: Callable[[dict[str, object]], None],
        validate_decision: Callable[[dict[str, object]], None],
        validate_decision_aftermath: Callable[
            [dict[str, object], dict[str, object], dict[str, object]], None
        ],
        task_id_for_key: Callable[[str], str],
        decision_receipt: Callable[[str, dict[str, object]], dict[str, object]],
        authority_sha256: str,
    ) -> None:
        self.path = path
        self.service_id = identity(service_id)
        self.validate_payload = validate_payload
        self.validate_claim = validate_claim
        self.validate_decision = validate_decision
        self.validate_decision_aftermath = validate_decision_aftermath
        self.task_id_for_key = task_id_for_key
        self.decision_receipt = decision_receipt
        self.authority_sha256 = authority_sha256

    def initialize(self) -> None:
        """Exclusively create storage. Never repair or replace an existing file."""
        self._path_check(missing=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        # Failed initialization remains visible. A later open must refuse it.
        try:
            with sqlite3.connect(self.path) as connection:
                connection.executescript(_SCHEMA)
                connection.execute(
                    "INSERT INTO metadata VALUES (?, 1, ?)",
                    (self.service_id, self.authority_sha256),
                )
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except sqlite3.Error as exc:
            raise IntakeError("intake_io_error") from exc

    def _path_check(self, *, missing: bool = False) -> None:
        if not self.path.is_absolute() or self.path != self.path.resolve():
            raise IntakeError("noncanonical_intake")
        for parent in self.path.parents:
            if parent.is_symlink():
                raise IntakeError("linked_intake")
        if missing and not self.path.exists():
            return
        try:
            info = self.path.lstat()
        except OSError as exc:
            raise IntakeError("intake_missing") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntakeError("linked_intake")
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.is_symlink():
                raise IntakeError("linked_intake")

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        self._path_check()
        connection = None
        try:
            mode = "rw" if write else "ro"
            connection = sqlite3.connect(
                self.path.as_uri() + "?mode=" + mode, uri=True, timeout=10,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if write:
                connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            self._validate(connection)
            yield connection
            connection.commit()
        except sqlite3.Error as exc:
            raise IntakeError("intake_io_error") from exc
        except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
            raise IntakeError("invalid_intake") from exc
        finally:
            if connection is not None:
                connection.close()  # Rolls back any incomplete transaction.

    def _task(self, row: sqlite3.Row) -> dict[str, object]:
        payload = row["request_bytes"]
        if not isinstance(payload, bytes):
            raise IntakeError("invalid_intake")
        self.validate_payload(payload)
        expected = self.task_id_for_key(row["intake_key"])
        if (
            not KEY.fullmatch(row["intake_key"])
            or row["task_id"] != expected
            or row["request_sha256"] != hashlib.sha256(payload).hexdigest()
            or type(row["sequence"]) is not int or row["sequence"] < 1
            or type(row["version"]) is not int or row["version"] < 1
            or row["status"] not in STATES
        ):
            raise IntakeError("invalid_intake")
        _date(row["submitted_at"])
        claim = None if row["claim"] is None else strict_json(row["claim"].encode())
        if claim is not None:
            self.validate_claim(cast(dict[str, object], claim))
            if (
                claim["task_id"] != row["task_id"]
                or claim["request_sha256"] != row["request_sha256"]
            ):
                raise IntakeError("invalid_intake")
        if row["status"] in ACTIVE and claim is None:
            raise IntakeError("invalid_intake")
        terminal = (
            None if row["terminal_receipt"] is None
            else strict_json(row["terminal_receipt"].encode())
        )
        if row["status"] in ACTIVE and terminal is not None:
            raise IntakeError("invalid_intake")
        if row["status"] in {"succeeded", "failed", "cancelled"} and terminal is None:
            raise IntakeError("invalid_intake")
        if row["status"] == "queued" and (claim is not None or terminal is not None):
            raise IntakeError("invalid_intake")
        receipt = fields(strict_json(row["submission_receipt"].encode()), {
            "format", "service_id", "task_id", "sequence", "intake_key",
            "payload_sha256", "disposition", "accepted_at",
        })
        expected_receipt = self._receipt(dict(row))
        if receipt != expected_receipt:
            raise IntakeError("invalid_intake")
        return {
            key: value for key, value in dict(row).items()
            if key not in {"submission_receipt", "claim", "terminal_receipt"}
        } | {"claim": claim, "terminal_receipt": terminal}

    def _validate(self, connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
            raise IntakeError("invalid_intake")
        metadata = connection.execute("SELECT * FROM metadata").fetchall()
        if len(metadata) != 1 or tuple(metadata[0]) != (
            self.service_id, 1, self.authority_sha256
        ):
            raise IntakeError("invalid_intake")
        active = 0
        tasks = {}
        for row in connection.execute("SELECT * FROM tasks ORDER BY sequence"):
            task = self._task(row)
            tasks[task["task_id"]] = task
            active += task["status"] in ACTIVE
        if active > 1:
            raise IntakeError("invalid_intake")
        decision_receipts: dict[str, list[object]] = {}
        for row in connection.execute("SELECT * FROM decisions"):
            identity(row["request_id"])
            intent = cast(dict[str, object], strict_json(row["intent"].encode()))
            self.validate_decision(intent)
            if intent["task_id"] not in tasks or intent["task_id"] != row["task_id"]:
                raise IntakeError("invalid_intake")
            receipt = strict_json(row["receipt"].encode())
            if receipt != self.decision_receipt(row["request_id"], intent):
                raise IntakeError("invalid_intake")
            self.validate_decision_aftermath(
                tasks[row["task_id"]], intent, cast(dict[str, object], receipt)
            )
            decision_receipts.setdefault(row["task_id"], []).append(receipt)
        for task in tasks.values():
            if (
                task["terminal_receipt"] is not None
                and task["terminal_receipt"] not in decision_receipts.get(
                    str(task["task_id"]), []
                )
            ):
                raise IntakeError("invalid_intake")

    def _receipt(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "format": RECEIPT, "service_id": self.service_id,
            "task_id": row["task_id"], "sequence": row["sequence"],
            "intake_key": row["intake_key"], "payload_sha256": row["request_sha256"],
            "disposition": "accepted", "accepted_at": row["submitted_at"],
        }

    def submit(self, key: str, payload: bytes) -> dict[str, object]:
        self.validate_payload(payload)
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise IntakeError("invalid_intake_key")
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE intake_key=?", (key,)
            ).fetchone()
            if row is not None:
                if row["request_bytes"] != payload:
                    raise IntakeError("intake_key_conflict")
                return self._receipt(dict(row)) | {"disposition": "replayed"}
            task_id = identity(self.task_id_for_key(key))
            submitted = _timestamp()
            digest = hashlib.sha256(payload).hexdigest()
            cursor = connection.execute(
                "INSERT INTO tasks (task_id,intake_key,request_bytes,request_sha256,"
                "submitted_at,status,version,submission_receipt) "
                "VALUES (?,?,?,?,?,'queued',1,'')",
                (task_id, key, payload, digest, submitted),
            )
            receipt = self._receipt({
                "task_id": task_id, "sequence": cursor.lastrowid, "intake_key": key,
                "request_sha256": digest, "submitted_at": submitted,
            })
            connection.execute(
                "UPDATE tasks SET submission_receipt=? WHERE task_id=?",
                (canonical(receipt).decode(), task_id),
            )
            return receipt

    def tasks(self, task_id: str | None = None) -> list[dict[str, object]]:
        if task_id is not None:
            identity(task_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE (? IS NULL OR task_id=?) ORDER BY sequence",
                (task_id, task_id),
            ).fetchall()
            if task_id is not None and not rows:
                raise IntakeError("task_not_found")
            return [self._task(row) for row in rows]

    def claim(
        self, build: Callable[[dict[str, object]], dict[str, object]]
    ) -> dict[str, object] | None:
        """Commit the FIFO claim before the caller may materialize or dispatch."""
        with self._connection(write=True) as connection:
            if connection.execute(
                "SELECT 1 FROM tasks "
                "WHERE status IN ('claimed','running','held')"
            ).fetchone():
                raise IntakeError("active_task")
            row = connection.execute(
                "SELECT * FROM tasks WHERE status='queued' "
                "ORDER BY sequence LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            task = self._task(row)
            claim = build(task)
            self.validate_claim(claim)
            if (
                claim["task_id"] != task["task_id"]
                or claim["request_sha256"] != task["request_sha256"]
            ):
                raise IntakeError("claim_conflict")
            connection.execute(
                "UPDATE tasks SET claim=?,status='claimed',version=version+1 "
                "WHERE task_id=?",
                (canonical(claim).decode(), task["task_id"]),
            )
            return claim

    def decide(
        self, request_id: str, intent: dict[str, object], *,
        check: Callable[[dict[str, object]], None], cancel_queued: bool = False,
    ) -> dict[str, object]:
        """Save exact intent and receipt atomically after domain checks."""
        identity(request_id)
        self.validate_decision(intent)
        with self._connection(write=True) as connection:
            previous = connection.execute(
                "SELECT * FROM decisions WHERE request_id=?", (request_id,)
            ).fetchone()
            if previous is not None:
                if previous["intent"] != canonical(intent).decode():
                    raise IntakeError("decision_conflict")
                return cast(
                    dict[str, object], strict_json(previous["receipt"].encode())
                )
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id=?", (intent["task_id"],)
            ).fetchone()
            if row is None:
                raise IntakeError("task_not_found")
            task = self._task(row)
            check(task)
            receipt = self.decision_receipt(request_id, intent)
            if cancel_queued:
                if task["status"] != "queued":
                    raise IntakeError("task_not_queued")
                connection.execute(
                    "UPDATE tasks SET status='cancelled',terminal_receipt=?,"
                    "version=version+1 WHERE task_id=?",
                    (canonical(receipt).decode(), task["task_id"]),
                )
            connection.execute("INSERT INTO decisions VALUES (?,?,?,?)", (
                request_id, intent["task_id"],
                canonical(intent).decode(), canonical(receipt).decode(),
            ))
            updated = connection.execute(
                "SELECT * FROM tasks WHERE task_id=?", (intent["task_id"],)
            ).fetchone()
            self.validate_decision_aftermath(self._task(updated), intent, receipt)
            return receipt
