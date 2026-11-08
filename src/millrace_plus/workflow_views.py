"""Generic immutable workflow output records (`millrace-workflow-output/v1`).

A trusted operator-side publisher supplies one exact record. `publish_output`
checks its execution links through the public Plus control facade, imported
lazily, then stores the canonical bytes once at
`{state_home}/workflow-views/instances/{instance}/runs/{run}/{output}.json`.
`list_outputs` and `read_output` verify stored bytes only and import no Core.

Records are display evidence. They prove neither task success, ownership,
writer closure nor producer provenance, and nothing here interprets a domain.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

__all__ = [
    "OUTPUT_FORMAT",
    "RECORD_LIMIT",
    "REPORT_FORMAT",
    "WORKFLOW_VIEWS_CONTRACT",
    "WorkflowViewsError",
    "canonical_json_bytes",
    "list_outputs",
    "publish_output",
    "read_output",
]

WORKFLOW_VIEWS_CONTRACT = "millrace-workflow-views/v1"
OUTPUT_FORMAT = "millrace-workflow-output/v1"
REPORT_FORMAT = "millrace-report-display/v1"
RECORD_LIMIT = 1_047_552
PAGE_LIMIT = 50

_CORE_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_NODE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_OUTPUT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_FORMAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|\+00:00)"
)
_NOT_PLAIN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")
_TEMP = re.compile(r"\.publish-[0-9a-f]{32}")
_CURSOR = re.compile(r"o([0-9a-f]{40})_([a-z0-9][a-z0-9_-]{0,63})")
_RECORD_KEYS = frozenset(
    {
        "format",
        "output_id",
        "instance_id",
        "run_id",
        "plane_id",
        "node_id",
        "invocation_id",
        "producing_revision_id",
        "producer",
        "label",
        "published_at",
        "representations",
    }
)
_REPRESENTATION_KEYS = frozenset({"format", "media_type", "sha256", "data"})
_REPORT_KEYS = frozenset({"title", "paragraphs", "facts", "tables", "sources"})
_LINKS = ("invocation_id", "plane_id", "node_id", "producing_revision_id")
# Every directory open, including the selected state home, follows no symlink.
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class WorkflowViewsError(Exception):
    """A typed refusal. `code` is the stable reason that OS maps to a status."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json_bytes(value: object) -> bytes:
    """Encode `millrace-canonical-json/v1`, refusing anything outside the rule.

    Only JSON values are accepted: objects with string keys, arrays, strings,
    finite numbers, booleans and null. Keys are sorted, Unicode is kept as
    given (no normalization) and the UTF-8 bytes have no trailing newline. An
    unpaired surrogate refuses. Every refusal is a `ValueError`.
    """
    try:
        _json_value(value)
        text = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except RecursionError as exc:
        raise ValueError("JSON value nests too deeply") from exc
    return text.encode("utf-8")


def _json_value(value: object) -> None:
    if value is None or any(type(value) is kind for kind in (bool, int, str)):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("nonfinite number")
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("non-string object key")
            _json_value(item)
        return
    raise ValueError("not a JSON value")


def _strict_json(raw: bytes, code: str = "invalid_output_record") -> object:
    """Parse UTF-8 JSON with no duplicate keys and no NaN/Infinity constants."""

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(name: str) -> object:
        raise ValueError("nonfinite number")

    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
    except (ValueError, RecursionError) as exc:
        raise WorkflowViewsError(code) from exc


def _match(pattern: re.Pattern[str], value: object) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _plain(value: object) -> bool:
    return type(value) is str and _NOT_PLAIN.search(value) is None


def _label(value: object) -> bool:
    return (
        _plain(value)
        and 1 <= len(cast(str, value)) <= 256
        and not any(char in cast(str, value) for char in "\t\n\r")
    )


def _all(value: object, check: Callable[[object], bool]) -> bool:
    return type(value) is list and all(check(item) for item in value)


def _texts(value: object, keys: frozenset[str]) -> bool:
    return (
        type(value) is dict
        and value.keys() == keys
        and all(_plain(value[key]) for key in keys)
    )


def _table(value: object) -> bool:
    if not (
        type(value) is dict
        and value.keys() == {"caption", "columns", "rows"}
        and _plain(value["caption"])
        and _all(value["columns"], _plain)
        and len(value["columns"]) >= 1
    ):
        return False
    width = len(value["columns"])
    return _all(
        value["rows"],
        lambda row: _all(row, _plain) and len(cast(list[object], row)) == width,
    )


def _report(value: object) -> bool:
    """`millrace-report-display/v1` data: fixed plain-text shape, no caps."""
    if not (
        type(value) is dict and value.keys() == _REPORT_KEYS and _plain(value["title"])
    ):
        return False
    facts, sources = frozenset({"label", "value"}), frozenset({"label", "url"})
    return (
        _all(value["paragraphs"], _plain)
        and _all(value["facts"], lambda item: _texts(item, facts))
        and _all(value["tables"], _table)
        and _all(value["sources"], lambda item: _texts(item, sources))
    )


def _representation(value: object, *, report: bool) -> str:
    if not (
        type(value) is dict
        and value.keys() == _REPRESENTATION_KEYS
        and value["media_type"] == "application/json"
        and _match(_SHA256, value["sha256"])
        and (
            (value["format"] == REPORT_FORMAT and _report(value["data"]))
            if report
            else _match(_FORMAT, value["format"])
        )
    ):
        raise WorkflowViewsError("invalid_output_record")
    try:
        digest = hashlib.sha256(canonical_json_bytes(value["data"])).hexdigest()
    except ValueError as exc:
        raise WorkflowViewsError("invalid_output_record") from exc
    if digest != value["sha256"]:
        raise WorkflowViewsError("invalid_output_record")
    return digest


def _check_record(record: object) -> tuple[bytes, str, str | None]:
    """Validate one record; return its canonical bytes and both data digests."""
    if not (type(record) is dict and record.keys() == _RECORD_KEYS):
        raise WorkflowViewsError("invalid_output_record")
    producer, representations = record["producer"], record["representations"]
    if not (
        record["format"] == OUTPUT_FORMAT
        and _match(_OUTPUT, record["output_id"])
        and all(
            _match(_CORE_ID, record[key])
            for key in ("instance_id", "run_id", "invocation_id")
        )
        and _match(_NODE, record["plane_id"])
        and _match(_NODE, record["node_id"])
        and _match(_SHA256, record["producing_revision_id"])
        and type(producer) is dict
        and producer.keys() == {"id", "version"}
        and _match(_FORMAT, producer["id"])
        and _match(_VERSION, producer["version"])
        and _label(record["label"])
        and _match(_TIMESTAMP, record["published_at"])
        and type(representations) is dict
        and "report" in representations
        and representations.keys() <= {"report", "json"}
    ):
        raise WorkflowViewsError("invalid_output_record")
    report_sha256 = _representation(representations["report"], report=True)
    json_sha256 = (
        _representation(representations["json"], report=False)
        if "json" in representations
        else None
    )
    try:
        encoded = canonical_json_bytes(record)
    except ValueError as exc:
        raise WorkflowViewsError("invalid_output_record") from exc
    if len(encoded) > RECORD_LIMIT:
        raise WorkflowViewsError("output_too_large")
    return encoded, report_sha256, json_sha256


def _require(valid: bool) -> None:
    if not valid:
        raise WorkflowViewsError("invalid_request")


def _home(state_home: Path) -> Path:
    home = Path(state_home)
    _require(home.is_absolute())
    return home


def _close(descriptors: list[int]) -> None:
    for descriptor in descriptors:
        os.close(descriptor)


def _chain(
    home: Path, instance_id: str, run_id: str, *, create: bool
) -> list[int] | None:
    """Open the state home and each owned directory down to the run directory.

    The state home's final component must be a directory, not a symlink. The
    owned components are `workflow-views`, `instances`, the instance ID, `runs`
    and the run ID. Each must be a real directory (no symlink) owned by this
    user and not group/other writable. Reads return None when a component is
    absent; publication creates it with mode 0700.
    """
    try:
        descriptors = [os.open(home, _DIRECTORY)]
    except FileNotFoundError:
        if create:
            raise
        return None
    except OSError as exc:  # a symlink or a non-directory
        raise WorkflowViewsError("output_integrity_error") from exc
    try:
        for name in ("workflow-views", "instances", instance_id, "runs", run_id):
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
            try:
                descriptor = os.open(name, _DIRECTORY, dir_fd=descriptors[-1])
            except FileNotFoundError:
                if create:
                    raise
                _close(descriptors)
                return None
            except OSError as exc:  # a symlink or a non-directory
                raise WorkflowViewsError("output_integrity_error") from exc
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise WorkflowViewsError("output_integrity_error")
    except BaseException:
        _close(descriptors)
        raise
    return descriptors


def _bound(descriptors: list[int]) -> bool:
    """Whether the state home still names the opened `workflow-views` root.

    Open descriptors cannot be redirected, but the root entry can be removed,
    replaced or made a symlink after opening. Each result is checked against
    the live entry before it is acknowledged or returned.
    """
    try:
        live = os.stat("workflow-views", dir_fd=descriptors[0], follow_symlinks=False)
    except OSError:
        return False
    opened = os.fstat(descriptors[1])
    return (live.st_dev, live.st_ino) == (opened.st_dev, opened.st_ino)


def _stored(run: int, name: str) -> bytes:
    """Read one record file: no symlink, regular, owned, mode 0600, bounded."""
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=run
        )
    except FileNotFoundError:
        raise WorkflowViewsError("output_not_found") from None
    except OSError as exc:
        raise WorkflowViewsError("output_integrity_error") from exc
    chunks: list[bytes] = []
    total = 0
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > RECORD_LIMIT
        ):
            raise WorkflowViewsError("output_integrity_error")
        while total <= RECORD_LIMIT:
            chunk = os.read(descriptor, RECORD_LIMIT + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        os.close(descriptor)
    if total > RECORD_LIMIT:
        raise WorkflowViewsError("output_integrity_error")
    return b"".join(chunks)


def _load(
    run: int, instance_id: str, run_id: str, output_id: str
) -> tuple[dict[str, Any], bytes]:
    """A stored record whose bytes are canonical and whose path identity matches."""
    raw = _stored(run, output_id + ".json")
    record = _strict_json(raw, "output_integrity_error")
    try:
        encoded = _check_record(record)[0]
    except WorkflowViewsError as exc:
        raise WorkflowViewsError("output_integrity_error") from exc
    checked = cast(dict[str, Any], record)
    if encoded != raw or (
        checked["instance_id"],
        checked["run_id"],
        checked["output_id"],
    ) != (instance_id, run_id, output_id):
        raise WorkflowViewsError("output_integrity_error")
    return checked, raw


def _discard(run: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=run)
    except OSError:
        pass  # An ignored `.publish-*` temporary is never discovered.


def _link_once(run: int, name: str, encoded: bytes) -> str:
    """Write a temporary, then link it to `name` without ever overwriting."""
    temporary = ".publish-" + uuid.uuid4().hex
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY | os.O_CLOEXEC,
        0o600,
        dir_fd=run,
    )
    try:
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(encoded)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, name, src_dir_fd=run, dst_dir_fd=run)
            status = "published"
        except FileExistsError:
            status = "already_published" if _stored(run, name) == encoded else ""
    except BaseException:
        _discard(run, temporary)  # Nothing was linked by this call.
        raise
    if not status:
        _discard(run, temporary)
        raise WorkflowViewsError("output_conflict")
    try:
        os.unlink(temporary, dir_fd=run)
    except OSError as exc:  # The record is visible but not acknowledged.
        raise WorkflowViewsError("output_publication_uncertain") from exc
    return status


def _check_identity(record: dict[str, Any], home: Path) -> None:
    """Require the exact public invocation row; Core errors propagate."""
    # Imported here: only publication reads Core, through the public facade.
    from millrace_plus.router import control

    page = control.inspect_run_page(
        record["instance_id"],
        record["run_id"],
        state_home=home,
        section="invocations",
        invocation_id=record["invocation_id"],
    )
    items = cast(list[dict[str, object]], page["items"])
    if len(items) != 1 or any(items[0][key] != record[key] for key in _LINKS):
        raise WorkflowViewsError("output_identity_mismatch")


def _reference(record: dict[str, Any], record_sha256: str) -> dict[str, str]:
    return {
        "instance_id": record["instance_id"],
        "run_id": record["run_id"],
        "output_id": record["output_id"],
        "record_sha256": record_sha256,
    }


def _invocation(record: dict[str, Any]) -> dict[str, str]:
    return {
        "plane_id": record["plane_id"],
        "node_id": record["node_id"],
        "invocation_id": record["invocation_id"],
        "producing_revision_id": record["producing_revision_id"],
    }


def publish_output(record: dict[str, Any], *, state_home: Path) -> dict[str, Any]:
    """Publish one exact record once. A different record for its ID refuses.

    Order: strict record validation, the exact invocation lookup, owned
    directories, then a temporary file that is fsynced and linked into place.
    `published` and `already_published` both mean the record bytes and every
    directory entry on their path were fsynced: the run directory, then
    `runs`, the instance directory, `instances`, `workflow-views` and the state
    home. That includes directories an earlier uncertain attempt created. The
    state home must also still name the opened `workflow-views` root.

    A failure before the link leaves no visible record. A failure after it,
    including a root found removed, replaced or symlinked, raises
    `output_publication_uncertain`; nothing is rolled back. Retry with the same
    saved record, unchanged: it returns `already_published`, or `published`
    when the earlier link went under a root that is no longer selected.
    """
    encoded, report_sha256, json_sha256 = _check_record(record)
    home = _home(state_home)
    _check_identity(record, home)
    descriptors = cast(
        list[int], _chain(home, record["instance_id"], record["run_id"], create=True)
    )
    try:
        status = _link_once(descriptors[-1], record["output_id"] + ".json", encoded)
        try:
            for descriptor in reversed(descriptors):
                os.fsync(descriptor)
        except OSError as exc:
            raise WorkflowViewsError("output_publication_uncertain") from exc
        if not _bound(descriptors):
            # Linked under a root that is no longer selected: never acknowledged.
            raise WorkflowViewsError("output_publication_uncertain")
    finally:
        _close(descriptors)
    return {
        "reference": _reference(record, hashlib.sha256(encoded).hexdigest()),
        "report_sha256": report_sha256,
        "json_sha256": json_sha256,
        "status": status,
    }


def _entries(run: int) -> list[str]:
    """Sorted output IDs; `.publish-*` temporaries are ignored."""
    output_ids: list[str] = []
    with os.scandir(run) as entries:
        for entry in entries:
            if _TEMP.fullmatch(entry.name):
                continue
            stem = entry.name.removesuffix(".json")
            if (
                stem == entry.name
                or not _match(_OUTPUT, stem)
                or not entry.is_file(follow_symlinks=False)
            ):
                raise WorkflowViewsError("output_integrity_error")
            output_ids.append(stem)
    return sorted(output_ids)


def _metadata(representation: dict[str, Any] | None) -> dict[str, Any] | None:
    if representation is None:
        return None
    return {key: representation[key] for key in ("format", "media_type", "sha256")}


def _item(run: int, instance_id: str, run_id: str, output_id: str) -> dict[str, Any]:
    try:
        record, raw = _load(run, instance_id, run_id, output_id)
    except WorkflowViewsError:
        return {
            "output_id": output_id,
            "availability": "unavailable",
            "reason": "output_integrity_error",
            "record_sha256": None,
            "producer": None,
            "label": None,
            "published_at": None,
            "invocation": None,
            "representations": None,
        }
    representations = record["representations"]
    return {
        "output_id": output_id,
        "availability": "available",
        "reason": None,
        "record_sha256": hashlib.sha256(raw).hexdigest(),
        "producer": record["producer"],
        "label": record["label"],
        "published_at": record["published_at"],
        "invocation": _invocation(record),
        "representations": {
            "report": _metadata(representations["report"]),
            "json": _metadata(representations.get("json")),
        },
    }


def list_outputs(
    instance_id: str,
    run_id: str,
    *,
    state_home: Path,
    limit: int = PAGE_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One page of a run's records sorted by output ID, verified from bytes.

    A missing run directory is an empty page. Any entry other than an output
    file or an ignored temporary refuses the whole listing. The cursor binds
    the exact set of output IDs; a later publication makes it
    `outputs_changed`. Each listed record is fully verified, and one that
    fails is reported unavailable without content. A root removed, replaced
    or made a symlink after opening refuses `output_integrity_error`.
    """
    _require(
        _match(_CORE_ID, instance_id)
        and _match(_CORE_ID, run_id)
        and type(limit) is int
        and 1 <= limit <= PAGE_LIMIT
        and (cursor is None or type(cursor) is str)
    )
    descriptors = _chain(_home(state_home), instance_id, run_id, create=False)
    try:
        output_ids = [] if descriptors is None else _entries(descriptors[-1])
        digest = hashlib.sha256(
            canonical_json_bytes(
                {"instance_id": instance_id, "run_id": run_id, "output_ids": output_ids}
            )
        ).hexdigest()[:40]
        start = 0
        if cursor is not None:
            match = _CURSOR.fullmatch(cursor)
            _require(match is not None)
            if cast(re.Match[str], match).group(1) != digest:
                raise WorkflowViewsError("outputs_changed")
            last = cast(re.Match[str], match).group(2)
            _require(last in output_ids)
            start = output_ids.index(last) + 1
        page = output_ids[start : start + limit]
        items = [
            _item(cast(list[int], descriptors)[-1], instance_id, run_id, output_id)
            for output_id in page
        ]
        if descriptors is not None and not _bound(descriptors):
            raise WorkflowViewsError("output_integrity_error")
    finally:
        if descriptors is not None:
            _close(descriptors)
    more = start + limit < len(output_ids)
    return {
        "instance_id": instance_id,
        "run_id": run_id,
        "items": items,
        "next_cursor": f"o{digest}_{page[-1]}" if more else None,
    }


def read_output(
    instance_id: str,
    run_id: str,
    output_id: str,
    record_sha256: str,
    representation: str,
    *,
    state_home: Path,
) -> dict[str, Any]:
    """One representation of the exact record whose stored digest was given."""
    _require(
        _match(_CORE_ID, instance_id)
        and _match(_CORE_ID, run_id)
        and _match(_OUTPUT, output_id)
        and _match(_SHA256, record_sha256)
        and representation in ("report", "json")
    )
    descriptors = _chain(_home(state_home), instance_id, run_id, create=False)
    if descriptors is None:
        raise WorkflowViewsError("output_not_found")
    try:
        record, raw = _load(descriptors[-1], instance_id, run_id, output_id)
        if not _bound(descriptors):
            raise WorkflowViewsError("output_integrity_error")
    finally:
        _close(descriptors)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != record_sha256:
        raise WorkflowViewsError("output_integrity_error")
    selected = record["representations"].get(representation)
    if selected is None:
        raise WorkflowViewsError("output_representation_unavailable")
    return {
        "reference": _reference(record, digest),
        "producer": record["producer"],
        "label": record["label"],
        "published_at": record["published_at"],
        "invocation": _invocation(record),
        "representation": {"name": representation, **selected},
    }
