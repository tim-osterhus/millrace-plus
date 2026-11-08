"""Generic immutable workflow outputs (V1): identity, immutability, discovery, read.

A non-stock command-only graph is authored here. An operator-side exporter pins
its own create/Start receipts and one authored gate at an observed state
version, then publishes through the public helper. Nothing is workflow-specific.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from millrace.router.execution import RouterError
from plus_support import new_id

from millrace_plus import workflow_views as views
from millrace_plus.router import cli, control

GATE = "export_gate"
PRODUCER = {"id": "example.measure-exporter", "version": "1.0.0"}
PUBLISHED_AT = "2026-11-07T12:00:00Z"
ITEM_KEYS = {
    "output_id",
    "availability",
    "reason",
    "record_sha256",
    "producer",
    "label",
    "published_at",
    "invocation",
    "representations",
}
MEASURE = """\
import json
from pathlib import Path

counter = Path("rounds.txt")
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
Path("out").mkdir(exist_ok=True)
payload = {"round": count, "items": 2 * count}
Path("out/latest.json").write_text(json.dumps(payload))
print(json.dumps({"outcome": "DONE" if count >= 5 else "READY"}))
"""


def authored_flow(root: Path) -> tuple[Path, Path, Path]:
    """A non-stock command-only graph with one authored, recurring export gate."""
    workflow, project = root / "authored", root / "project"
    workflow.mkdir(parents=True)
    project.mkdir()
    (workflow / "measure.py").write_text(MEASURE)
    (workflow / "settings.txt").write_text("threshold=1\n")
    node = {
        "invoke": {
            "binding": "python",
            "config": {"script": "measure.py", "args": []},
            "cwd": ".",
            "timeout_seconds": 5,
            "inputs": [],
        },
        "result": "json-v1",
        "outcomes": ["READY", "DONE"],
        "on": {
            "READY": {"pause": {"reason": GATE, "resume": "measure"}},
            "DONE": {"finish": "succeeded"},
        },
        "on_unknown": {"pause": {"reason": "unknown_outcome", "resume": "measure"}},
        "on_error": {"pause": {"reason": "measure_failed", "resume": "measure"}},
    }
    graph = {
        "format": "millrace-router/v3",
        "limits": {"max_invocations": 12, "max_elapsed_seconds": 60},
        "bindings": {
            "python": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": sys.executable,
                "config": {},
            }
        },
        "control_files": ["measure.py", "settings.txt"],
        "planes": {
            "main": {"workspace": ".", "start": "measure", "nodes": {"measure": node}}
        },
    }
    (workflow / "workflow.json").write_text(json.dumps(graph))
    return workflow / "workflow.json", project, root / "state"


def representation(format_id: str, data: object) -> dict[str, object]:
    digest = hashlib.sha256(views.canonical_json_bytes(data)).hexdigest()
    return {
        "format": format_id,
        "media_type": "application/json",
        "sha256": digest,
        "data": data,
    }


def report(title: str, items: str = "2") -> dict[str, object]:
    return {
        "title": title,
        "paragraphs": ["An operator-side exporter published this generic report."],
        "facts": [{"label": "Items", "value": items}],
        "tables": [
            {
                "caption": "Measurements",
                "columns": ["Round", "Items"],
                "rows": [["1", items]],
            }
        ],
        "sources": [{"label": "Local measurement", "url": "out/latest.json"}],
    }


class Exporter:
    """A deterministic operator-side publisher holding its own receipts.

    It pins the authored gate: plane `main` paused for GATE at an observed state
    version, with exactly one `measure` completion routed to that gate since
    the previous pinned version. It never guesses a newest completion.
    """

    def __init__(self, flow: tuple[Path, Path, Path]) -> None:
        workflow_file, self.project, self.home = flow
        self.workflow = workflow_file.parent
        created = control.create_instance(
            workflow_file,
            self.project,
            state_home=self.home,
            request_id=new_id(),
            expected_state_version=0,
        )
        assert created["status"] == "applied", created
        self.instance = str(created["instance_id"])
        version = control.inspect_instance(self.instance, state_home=self.home)[
            "state_version"
        ]
        started = control.start_run(
            self.instance,
            state_home=self.home,
            request_id=new_id(),
            expected_state_version=version,
        )
        assert started["status"] == "applied", started
        self.run = str(started["run_id"])
        self.run_dir = (
            self.home / "workflow-views/instances" / self.instance / "runs" / self.run
        )

    def summary(self) -> dict[str, object]:
        return control.inspect_run_page(self.instance, self.run, state_home=self.home)

    def boundary(self, after: int) -> tuple[int, dict[str, object]]:
        summary = self.summary()
        plane = summary["data"]["planes"]["main"]
        if (plane["status"], plane["pause_reason"]) != ("paused", GATE):
            raise LookupError("not paused at the authored gate")
        pinned = summary["state_version"]
        gate = {"pause": {"reason": GATE, "resume": "measure"}}
        found, cursor = [], None
        while True:
            page = control.inspect_run_page(
                self.instance,
                self.run,
                state_home=self.home,
                section="transitions",
                cursor=cursor,
            )
            for event in page["items"]:
                action = event.get("action")
                if (
                    event["event_kind"] == "transition"
                    and event["plane_id"] == "main"
                    and event["node_id"] == "measure"
                    and after < event["version"] <= pinned
                    and (json.loads(action) if isinstance(action, str) else action)
                    == gate
                ):
                    found.append(event)
            cursor = page["next_cursor"]
            if cursor is None or page["items"][-1]["version"] <= after:
                break
        if len(found) != 1:
            raise LookupError(f"{len(found)} candidate completions at this boundary")
        return pinned, found[0]

    def descriptor(
        self, output_id: str, event: dict[str, object], **changes: object
    ) -> dict[str, object]:
        payload = json.loads((self.project / "out/latest.json").read_text())
        title = f"Measurement round {payload['round']}"
        record = {
            "format": views.OUTPUT_FORMAT,
            "output_id": output_id,
            "instance_id": self.instance,
            "run_id": self.run,
            "plane_id": event["plane_id"],
            "node_id": event["node_id"],
            "invocation_id": event["completion_id"],
            "producing_revision_id": event["producing_revision_id"],
            "producer": dict(PRODUCER),
            "label": title,
            "published_at": PUBLISHED_AT,
            "representations": {
                "report": representation(
                    views.REPORT_FORMAT, report(title, str(payload["items"]))
                ),
                "json": representation("example.measure/v1", payload),
            },
        }
        record.update(changes)
        return record

    def resume(self) -> None:
        receipt = control.resume_plane(
            self.instance,
            self.run,
            plane_id="main",
            state_home=self.home,
            request_id=new_id(),
            expected_state_version=self.summary()["state_version"],
        )
        assert receipt["status"] == "applied", receipt


@pytest.fixture
def gate(tmp_path: Path) -> tuple[Exporter, dict[str, object]]:
    exporter = Exporter(authored_flow(tmp_path))
    return exporter, exporter.boundary(after=0)[1]


def refused(code: str, call, *args, **kwargs) -> None:
    with pytest.raises(views.WorkflowViewsError) as error:
        call(*args, **kwargs)
    assert error.value.code == code


def run_views(monkeypatch, capsys, *args: str) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["millrace-plus", "views", *args])
    with pytest.raises(SystemExit) as exit_info:
        cli.cli()
    output = capsys.readouterr()
    return exit_info.value.code, output.out, output.err


def test_operator_exporter_publishes_at_pinned_gates_across_recurrence_and_revision(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    exporter = Exporter(authored_flow(tmp_path))
    home = exporter.home
    first_version, first_event = exporter.boundary(after=0)
    saved = tmp_path / "round-1.json"
    saved.write_bytes(
        views.canonical_json_bytes(exporter.descriptor("round-1", first_event))
    )
    record = json.loads(saved.read_bytes())
    result = views.publish_output(record, state_home=home)
    record_sha256 = hashlib.sha256(saved.read_bytes()).hexdigest()
    assert result == {
        "reference": {
            "instance_id": exporter.instance,
            "run_id": exporter.run,
            "output_id": "round-1",
            "record_sha256": record_sha256,
        },
        "report_sha256": record["representations"]["report"]["sha256"],
        "json_sha256": record["representations"]["json"]["sha256"],
        "status": "published",
    }
    stored = exporter.run_dir / "round-1.json"
    assert stored.read_bytes() == saved.read_bytes()
    first_read = views.read_output(
        exporter.instance, exporter.run, "round-1", record_sha256, "report",
        state_home=home,
    )
    assert first_read["representation"] == {
        "name": "report",
        **record["representations"]["report"],
    }
    assert first_read["invocation"] == {
        "plane_id": "main",
        "node_id": "measure",
        "invocation_id": first_event["completion_id"],
        "producing_revision_id": first_event["producing_revision_id"],
    }

    # An explicit revision between gates: pause, submit, accept, resume.
    paused = control.pause_run(
        exporter.instance, exporter.run, state_home=home, request_id=new_id()
    )
    assert paused["status"] == "applied", paused
    (exporter.workflow / "settings.txt").write_text("threshold=2\n")
    submitted = control.submit_revision(
        exporter.instance,
        exporter.run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=exporter.summary()["state_version"],
    )
    assert submitted["status"] == "applied", submitted
    accepted = control.accept_revision(
        exporter.instance,
        exporter.run,
        candidate_id=str(submitted["candidate_id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=exporter.summary()["state_version"],
    )
    assert accepted["status"] == "applied", accepted
    exporter.resume()
    second_version, second_event = exporter.boundary(after=first_version)
    assert second_version > first_version
    assert second_event["producing_revision_id"] == accepted["revision_id"]
    assert second_event["producing_revision_id"] != first_event["producing_revision_id"]
    second = tmp_path / "round-2.json"
    second.write_bytes(
        views.canonical_json_bytes(exporter.descriptor("round-2", second_event))
    )
    code, out, err = run_views(
        monkeypatch, capsys, "publish", "--file", str(second), "--state-home", str(home)
    )
    assert (code, err) == (0, "")
    assert json.loads(out)["status"] == "published"

    # Recurrence works at each pinned version; two occurrences since the last
    # pinned boundary are ambiguous and refuse.
    exporter.resume()
    third_version, _ = exporter.boundary(after=second_version)
    exporter.resume()
    with pytest.raises(LookupError, match="2 candidate"):
        exporter.boundary(after=second_version)
    _, fourth_event = exporter.boundary(after=third_version)
    fourth = exporter.descriptor("round-4", fourth_event)
    assert views.publish_output(fourth, state_home=home)["status"] == "published"
    exporter.resume()
    assert exporter.summary()["data"]["status"] == "succeeded"

    # Later progress and the revision change leave the exact read unchanged.
    assert stored.read_bytes() == saved.read_bytes()
    assert (
        views.read_output(
            exporter.instance, exporter.run, "round-1", record_sha256, "report",
            state_home=home,
        )
        == first_read
    )
    listing = views.list_outputs(exporter.instance, exporter.run, state_home=home)
    assert [item["output_id"] for item in listing["items"]] == [
        "round-1",
        "round-2",
        "round-4",
    ]
    assert [item["invocation"]["invocation_id"] for item in listing["items"]] == [
        first_event["completion_id"],
        second_event["completion_id"],
        fourth_event["completion_id"],
    ]
    assert {item["availability"] for item in listing["items"]} == {"available"}
    assert listing["next_cursor"] is None
    # The saved descriptor retries identically, keeping published_at and bytes.
    again = views.publish_output(json.loads(saved.read_bytes()), state_home=home)
    assert again == {**result, "status": "already_published"}
    assert stored.read_bytes() == saved.read_bytes()


def test_canonical_rule_vectors_and_refusals() -> None:
    value = {"b": [1, 2.5, None, True, False], "a": "é", "c": {"z": "", "y": -0.0}}
    encoded = views.canonical_json_bytes(value)
    assert encoded == (
        b'{"a":"\xc3\xa9","b":[1,2.5,null,true,false],"c":{"y":-0.0,"z":""}}'
    )
    assert hashlib.sha256(encoded).hexdigest() == (
        "dc49001709042b1d9e4250deb4bdb47b17ab2967ea3613e961fec9aa21b82fa3"
    )
    assert views.canonical_json_bytes([]) == b"[]"
    assert views.canonical_json_bytes({}) == b"{}"
    assert views.canonical_json_bytes(1e16) == b"1e+16"
    # Unicode is never normalized; astral and line-separator code points stay raw.
    assert views.canonical_json_bytes("é") != views.canonical_json_bytes("é")
    assert views.canonical_json_bytes("\U0001f600 ") == (
        b'"\xf0\x9f\x98\x80\xe2\x80\xa8"'
    )
    for invalid in (
        {1: "non-string key"},
        {"nested": {(1, 2): "tuple key"}},
        float("nan"),
        [float("inf")],
        {"x": float("-inf")},
        "\ud800",
        {"\udfff": "unpaired surrogate key"},
        (1, 2),
        {"set": {1}},
        b"bytes",
    ):
        with pytest.raises(ValueError):
            views.canonical_json_bytes(invalid)
    deep: list[object] = []
    for _ in range(100_000):
        deep = [deep]
    with pytest.raises(ValueError):
        views.canonical_json_bytes(deep)


def shaped(**changes: object) -> dict[str, object]:
    record: dict[str, object] = {
        "format": views.OUTPUT_FORMAT,
        "output_id": "report-a",
        "instance_id": "a" * 32,
        "run_id": "b" * 32,
        "plane_id": "main",
        "node_id": "measure",
        "invocation_id": "c" * 32,
        "producing_revision_id": "d" * 64,
        "producer": dict(PRODUCER),
        "label": "Résumé 😀",
        "published_at": PUBLISHED_AT,
        "representations": {
            "report": representation(views.REPORT_FORMAT, report("Title")),
            "json": representation("example.opaque/v1", {"value": 1}),
        },
    }
    record.update(changes)
    return record


def with_report(data: object) -> dict[str, object]:
    return shaped(
        representations={"report": representation(views.REPORT_FORMAT, data)}
    )


def test_record_schema_is_strict_and_refuses_before_core(tmp_path: Path) -> None:
    home = tmp_path / "no-state-home"
    good = shaped()
    digest = good["representations"]["report"]["sha256"]
    bad_report = report("Title")
    bad_report["tables"][0]["rows"] = [["only one cell"]]
    unknown_key = dict(report("Title"), extra=[])
    missing_key = report("Title")
    del missing_key["sources"]
    no_columns = report("Title")
    no_columns["tables"][0].update(columns=[], rows=[])
    control_char = dict(report("Title"), paragraphs=["bell \x07"])
    numeric = dict(report("Title"), facts=[{"label": "Items", "value": 3}])
    missing_label = shaped()
    del missing_label["label"]
    cases = [
        dict(good, extra=True),
        missing_label,
        shaped(format="millrace-workflow-output/v2"),
        shaped(output_id="Report-A"),
        shaped(output_id="-report"),
        shaped(output_id="r" * 65),
        shaped(instance_id="A" * 32),
        shaped(run_id="b" * 31),
        shaped(invocation_id=7),
        shaped(plane_id="1main"),
        shaped(node_id=""),
        shaped(producing_revision_id="d" * 63),
        shaped(producer={"id": "x", "version": "1", "extra": "y"}),
        shaped(producer={"id": "", "version": "1"}),
        shaped(producer={"id": "x", "version": "1 0"}),
        shaped(label=""),
        shaped(label="two\nlines"),
        shaped(label="x" * 257),
        shaped(label="nul \x00"),
        shaped(label=True),
        shaped(published_at="2026-11-07 12:00:00Z"),
        shaped(published_at="2026-11-07T12:00:00+01:00"),
        shaped(published_at=True),
        shaped(representations={"json": good["representations"]["json"]}),
        shaped(representations={**good["representations"], "html": {}}),
        shaped(
            representations={
                "report": dict(
                    good["representations"]["report"], media_type="text/plain"
                )
            }
        ),
        shaped(
            representations={
                "report": dict(good["representations"]["report"], sha256="0" * 64)
            }
        ),
        shaped(
            representations={
                "report": dict(
                    good["representations"]["report"],
                    format="millrace-report-display/v2",
                )
            }
        ),
        with_report(bad_report),
        with_report(unknown_key),
        with_report(missing_key),
        with_report(no_columns),
        with_report(control_char),
        with_report(numeric),
        shaped(
            representations={
                **good["representations"],
                "json": representation("bad format", {"value": 1}),
            }
        ),
        shaped(
            representations={
                **good["representations"],
                "json": {
                    "format": "example.opaque/v1",
                    "media_type": "application/json",
                    "sha256": digest,
                    "data": {"x": float("nan")},
                },
            }
        ),
        shaped(
            representations={
                **good["representations"],
                "json": {
                    "format": "example.opaque/v1",
                    "media_type": "application/json",
                    "sha256": digest,
                    "data": {1: "non-string key"},
                },
            }
        ),
        shaped(label="lone \ud800"),
        ["not", "an", "object"],
    ]
    for case in cases:
        refused("invalid_output_record", views.publish_output, case, state_home=home)
    # A valid record reaches the exact Core lookup, which refuses the absent home.
    with pytest.raises(RouterError):
        views.publish_output(good, state_home=home)
    refused("invalid_request", views.publish_output, good, state_home=Path("rel"))
    assert not home.exists()


def test_record_limit_boundary_is_exact(gate) -> None:
    exporter, event = gate
    record = exporter.descriptor("limit", event)

    def padded(extra: int) -> dict[str, object]:
        value = copy.deepcopy(record)
        value["representations"]["json"] = representation(
            "example.pad/v1", {"pad": "x" * extra}
        )
        return value

    room = views.RECORD_LIMIT - len(views.canonical_json_bytes(padded(0)))
    at_limit, over = padded(room), padded(room + 1)
    assert len(views.canonical_json_bytes(at_limit)) == views.RECORD_LIMIT
    refused("output_too_large", views.publish_output, over, state_home=exporter.home)
    assert not (exporter.home / "workflow-views").exists()
    published = views.publish_output(at_limit, state_home=exporter.home)
    assert published["status"] == "published"
    stored = (exporter.run_dir / "limit.json").read_bytes()
    assert len(stored) == views.RECORD_LIMIT
    (item,) = views.list_outputs(
        exporter.instance, exporter.run, state_home=exporter.home
    )["items"]
    assert item["availability"] == "available"
    assert item["record_sha256"] == hashlib.sha256(stored).hexdigest()


def test_identity_must_match_the_exact_public_invocation(gate) -> None:
    exporter, event = gate
    for changes in (
        {"node_id": "other"},
        {"plane_id": "side"},
        {"producing_revision_id": "e" * 64},
        {"invocation_id": new_id()},
    ):
        record = exporter.descriptor("mismatch", event, **changes)
        refused(
            "output_identity_mismatch",
            views.publish_output,
            record,
            state_home=exporter.home,
        )
    for changes in ({"instance_id": new_id()}, {"run_id": new_id()}):
        record = exporter.descriptor("mismatch", event, **changes)
        with pytest.raises(RouterError):  # Core errors propagate unchanged.
            views.publish_output(record, state_home=exporter.home)
    assert not (exporter.home / "workflow-views").exists()


def test_identical_retry_conflict_and_two_writer_race(gate, monkeypatch) -> None:
    exporter, event = gate
    record = exporter.descriptor("report-a", event)
    first = views.publish_output(copy.deepcopy(record), state_home=exporter.home)
    target = exporter.run_dir / "report-a.json"
    stored = target.read_bytes()
    assert stored == views.canonical_json_bytes(record)
    assert views.publish_output(copy.deepcopy(record), state_home=exporter.home) == {
        **first,
        "status": "already_published",
    }
    changed = exporter.descriptor("report-a", event, label="A different label")
    refused("output_conflict", views.publish_output, changed, state_home=exporter.home)
    assert target.read_bytes() == stored
    # Owner-only directories and records.
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    current = exporter.run_dir
    while current != exporter.home:
        assert stat.S_IMODE(current.stat().st_mode) & 0o077 == 0, current
        current = current.parent

    # Two writers race for one new output ID: exactly one links.
    barrier = threading.Barrier(2, timeout=10)
    real_link = os.link

    def link(*args: object, **kwargs: object) -> None:
        barrier.wait()
        real_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    outcomes: dict[str, str] = {}

    def publish(label: str) -> None:
        try:
            outcomes[label] = views.publish_output(
                exporter.descriptor("raced", event, label=label),
                state_home=exporter.home,
            )["status"]
        except views.WorkflowViewsError as exc:
            outcomes[label] = exc.code

    writers = [
        threading.Thread(target=publish, args=(label,))
        for label in ("Writer one", "Writer two")
    ]
    for writer in writers:
        writer.start()
    for writer in writers:
        writer.join(30)
    assert sorted(outcomes.values()) == ["output_conflict", "published"]
    winner = next(label for label, status in outcomes.items() if status == "published")
    assert (exporter.run_dir / "raced.json").read_bytes() == views.canonical_json_bytes(
        exporter.descriptor("raced", event, label=winner)
    )
    assert sorted(path.name for path in exporter.run_dir.iterdir()) == [
        "raced.json",
        "report-a.json",
    ]


def test_post_link_fsync_failure_is_uncertain_and_retry_syncs_the_fixed_chain(
    gate, monkeypatch
) -> None:
    exporter, event = gate
    record = exporter.descriptor("durable", event)
    real_fsync = os.fsync

    def failing(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(errno.EIO, "injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", failing)
    refused(
        "output_publication_uncertain",
        views.publish_output,
        copy.deepcopy(record),
        state_home=exporter.home,
    )
    target = exporter.run_dir / "durable.json"
    stored = target.read_bytes()  # Visible, but never acknowledged durable.
    assert stored == views.canonical_json_bytes(record)
    assert [path.name for path in exporter.run_dir.iterdir()] == ["durable.json"]

    synced: list[tuple[int, int]] = []

    def spy(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if stat.S_ISDIR(info.st_mode):
            synced.append((info.st_dev, info.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", spy)
    retried = views.publish_output(copy.deepcopy(record), state_home=exporter.home)
    assert retried["status"] == "already_published"
    # The uncertain attempt created these directories; the retry still syncs
    # every entry on the record path: run, runs, instance, instances, root, home.
    root = exporter.home / "workflow-views"
    chain = [
        exporter.run_dir,
        exporter.run_dir.parent,
        exporter.run_dir.parent.parent,
        root / "instances",
        root,
        exporter.home,
    ]
    assert synced == [(os.stat(p).st_dev, os.stat(p).st_ino) for p in chain]
    assert target.read_bytes() == stored
    assert json.loads(stored)["published_at"] == PUBLISHED_AT


def test_failure_before_link_leaves_no_visible_record(gate, monkeypatch) -> None:
    exporter, event = gate

    def broken(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EIO, "injected link failure")

    monkeypatch.setattr(os, "link", broken)
    with pytest.raises(OSError):
        views.publish_output(
            exporter.descriptor("never", event), state_home=exporter.home
        )
    assert list(exporter.run_dir.iterdir()) == []
    listing = views.list_outputs(
        exporter.instance, exporter.run, state_home=exporter.home
    )
    assert listing["items"] == []


def test_listing_pages_by_output_id_and_binds_the_publication_set(gate) -> None:
    exporter, event = gate
    target = {"instance_id": exporter.instance, "run_id": exporter.run}
    home = exporter.home
    empty = views.list_outputs(exporter.instance, exporter.run, state_home=home)
    assert empty == {**target, "items": [], "next_cursor": None}
    assert not (home / "workflow-views").exists()
    assert views.list_outputs(new_id(), new_id(), state_home=home)["items"] == []
    for output_id in ("c-report", "a-report", "b-report"):
        record = exporter.descriptor(output_id, event, label=output_id)
        if output_id == "b-report":
            del record["representations"]["json"]
        views.publish_output(record, state_home=home)
    first = views.list_outputs(
        exporter.instance, exporter.run, state_home=home, limit=2
    )
    assert [item["output_id"] for item in first["items"]] == ["a-report", "b-report"]
    assert re.fullmatch(r"o[0-9a-f]{40}_b-report", first["next_cursor"])
    item = first["items"][0]
    raw = (exporter.run_dir / "a-report.json").read_bytes()
    record = json.loads(raw)
    assert set(item) == ITEM_KEYS
    assert item == {
        "output_id": "a-report",
        "availability": "available",
        "reason": None,
        "record_sha256": hashlib.sha256(raw).hexdigest(),
        "producer": PRODUCER,
        "label": "a-report",
        "published_at": PUBLISHED_AT,
        "invocation": {
            "plane_id": "main",
            "node_id": "measure",
            "invocation_id": event["completion_id"],
            "producing_revision_id": event["producing_revision_id"],
        },
        "representations": {
            name: {
                key: record["representations"][name][key]
                for key in ("format", "media_type", "sha256")
            }
            for name in ("report", "json")
        },
    }
    assert first["items"][1]["representations"]["json"] is None
    second = views.list_outputs(
        exporter.instance,
        exporter.run,
        state_home=home,
        limit=2,
        cursor=first["next_cursor"],
    )
    assert [item["output_id"] for item in second["items"]] == ["c-report"]
    assert second["next_cursor"] is None
    # A later publication changes the bound set; the old cursor refuses.
    views.publish_output(exporter.descriptor("d-report", event), state_home=home)
    refused(
        "outputs_changed",
        views.list_outputs,
        exporter.instance,
        exporter.run,
        state_home=home,
        limit=2,
        cursor=first["next_cursor"],
    )
    fresh = views.list_outputs(
        exporter.instance, exporter.run, state_home=home, limit=1
    )
    crafted = fresh["next_cursor"].rsplit("_", 1)[0] + "_zz-missing"
    for kwargs in (
        {"cursor": "x"},
        {"cursor": "o" + "g" * 40 + "_a-report"},
        {"cursor": crafted},
        {"limit": 0},
        {"limit": 51},
        {"limit": True},
        {"limit": "2"},
    ):
        refused(
            "invalid_request",
            views.list_outputs,
            exporter.instance,
            exporter.run,
            state_home=home,
            **kwargs,
        )
    refused(
        "invalid_request", views.list_outputs, "A" * 32, exporter.run, state_home=home
    )
    refused(
        "invalid_request",
        views.list_outputs,
        exporter.instance,
        exporter.run,
        state_home=Path("relative-state"),
    )


def test_integrity_refusals_ignored_temp_and_unavailable_items(gate) -> None:
    exporter, event = gate
    home, run = exporter.home, exporter.run_dir
    good = views.publish_output(
        exporter.descriptor("good", event), state_home=home
    )["reference"]["record_sha256"]
    bare = exporter.descriptor("bare", event)
    del bare["representations"]["json"]
    bare_sha256 = views.publish_output(bare, state_home=home)["reference"][
        "record_sha256"
    ]

    def ids() -> list[str]:
        listing = views.list_outputs(exporter.instance, exporter.run, state_home=home)
        return [item["output_id"] for item in listing["items"]]

    (run / (".publish-" + "0" * 32)).write_bytes(b'{"partial')  # ignored
    assert ids() == ["bare", "good"]
    for name, make in (
        ("notes.txt", lambda path: path.write_text("foreign")),
        ("folder.json", lambda path: path.mkdir()),
        ("link.json", lambda path: path.symlink_to(run / "good.json")),
        (".publish-short", lambda path: path.write_text("x")),
        ("Upper.json", lambda path: path.write_text("{}")),
    ):
        make(run / name)
        refused("output_integrity_error", ids)
        entry = run / name
        entry.rmdir() if entry.is_dir() and not entry.is_symlink() else entry.unlink()
    assert ids() == ["bare", "good"]

    raw = (run / "good.json").read_bytes()
    record = json.loads(raw)

    def plant(name: str, data: bytes, mode: int = 0o600) -> None:
        path = run / name
        path.write_bytes(data)
        os.chmod(path, mode)

    pretty = dict(record, output_id="pretty")
    loose = dict(record, output_id="loose")
    tampered = copy.deepcopy(dict(record, output_id="tampered"))
    tampered["representations"]["report"]["data"]["title"] = "Changed later"
    plant("corrupt.json", b'{"format":')
    plant("pretty.json", json.dumps(pretty, indent=2).encode())
    plant("moved.json", raw)  # bytes name another output ID
    plant("loose.json", views.canonical_json_bytes(loose), 0o644)
    plant("tampered.json", views.canonical_json_bytes(tampered))
    listing = views.list_outputs(exporter.instance, exporter.run, state_home=home)
    unavailable = {
        item["output_id"]: item
        for item in listing["items"]
        if item["availability"] == "unavailable"
    }
    assert set(unavailable) == {"corrupt", "loose", "moved", "pretty", "tampered"}
    for output_id, item in unavailable.items():
        assert item == {
            "output_id": output_id,
            "availability": "unavailable",
            "reason": "output_integrity_error",
            **dict.fromkeys(ITEM_KEYS - {"output_id", "availability", "reason"}),
        }

    def read(output_id: str, digest: str, name: str = "report") -> dict[str, object]:
        return views.read_output(
            exporter.instance, exporter.run, output_id, digest, name, state_home=home
        )

    assert read("good", good)["representation"]["data"] == record["representations"][
        "report"
    ]["data"]
    assert read("good", good, "json")["representation"] == {
        "name": "json",
        **record["representations"]["json"],
    }
    refused("output_integrity_error", read, "good", "0" * 64)
    refused("output_integrity_error", read, "moved", hashlib.sha256(raw).hexdigest())
    refused("output_integrity_error", read, "corrupt", "0" * 64)
    refused("output_not_found", read, "missing", good)
    refused("output_representation_unavailable", read, "bare", bare_sha256, "json")
    refused("invalid_request", read, "good", good, "html")
    refused(
        "output_not_found",
        views.read_output,
        new_id(),
        new_id(),
        "good",
        good,
        "report",
        state_home=home,
    )

    # A replaced root and a group-writable owned directory refuse everything.
    root = home / "workflow-views"
    elsewhere = home / "views-elsewhere"
    root.rename(elsewhere)
    root.symlink_to(elsewhere)
    refused("output_integrity_error", ids)
    refused("output_integrity_error", read, "good", good)
    refused(
        "output_integrity_error",
        views.publish_output,
        exporter.descriptor("after", event),
        state_home=home,
    )
    root.unlink()
    elsewhere.rename(root)
    os.chmod(run, 0o770)
    refused("output_integrity_error", ids)
    os.chmod(run, 0o700)
    assert "good" in ids()


def test_root_replaced_after_opening_refuses_acknowledgement_and_reads(
    gate, monkeypatch
) -> None:
    exporter, event = gate
    home = exporter.home
    root = home / "workflow-views"
    bound = exporter.descriptor("bound", event)
    digest = views.publish_output(copy.deepcopy(bound), state_home=home)[
        "reference"
    ]["record_sha256"]
    relative = exporter.run_dir.relative_to(root)
    real_chain = views._chain
    detached: list[Path] = []
    replacement = {"kind": "directory"}

    def chain_then_replace(*args: object, **kwargs: object) -> object:
        descriptors = real_chain(*args, **kwargs)
        target = home / f"detached-{len(detached)}"
        root.rename(target)
        detached.append(target)
        if replacement["kind"] == "directory":
            root.mkdir(mode=0o700)
        elif replacement["kind"] == "symlink":
            root.symlink_to(target, target_is_directory=True)
        return descriptors

    def restore() -> None:
        if root.is_symlink():
            root.unlink()
        elif root.is_dir():
            root.rmdir()  # the empty replacement
        detached[-1].rename(root)

    monkeypatch.setattr(views, "_chain", chain_then_replace)
    for kind in ("directory", "symlink", "missing"):
        replacement["kind"] = kind
        refused(
            "output_integrity_error",
            views.list_outputs,
            exporter.instance,
            exporter.run,
            state_home=home,
        )
        restore()
        refused(
            "output_integrity_error",
            views.read_output,
            exporter.instance,
            exporter.run,
            "bound",
            digest,
            "report",
            state_home=home,
        )
        restore()
    # A link under the detached root is visible there but never acknowledged.
    replacement["kind"] = "directory"
    later = exporter.descriptor("later", event)
    refused(
        "output_publication_uncertain",
        views.publish_output,
        copy.deepcopy(later),
        state_home=home,
    )
    stranded = detached[-1] / relative / "later.json"
    assert stranded.read_bytes() == views.canonical_json_bytes(later)
    monkeypatch.setattr(views, "_chain", real_chain)
    listing = views.list_outputs(exporter.instance, exporter.run, state_home=home)
    assert listing["items"] == []
    # The saved record then publishes under the selected root; nothing rolls back.
    retried = views.publish_output(copy.deepcopy(later), state_home=home)
    assert retried["status"] == "published"
    assert stranded.read_bytes() == views.canonical_json_bytes(later)
    assert (exporter.run_dir / "later.json").read_bytes() == stranded.read_bytes()


def test_selected_state_home_final_symlink_refuses(gate, tmp_path: Path) -> None:
    exporter, event = gate
    record = exporter.descriptor("homed", event)
    digest = views.publish_output(copy.deepcopy(record), state_home=exporter.home)[
        "reference"
    ]["record_sha256"]
    linked = tmp_path / "linked-state"
    linked.symlink_to(exporter.home, target_is_directory=True)
    target = (exporter.instance, exporter.run)
    refused("output_integrity_error", views.list_outputs, *target, state_home=linked)
    refused(
        "output_integrity_error",
        views.read_output,
        *target,
        "homed",
        digest,
        "report",
        state_home=linked,
    )
    # Core may resolve the link to verify identity; the helper never follows it.
    refused(
        "output_integrity_error",
        views.publish_output,
        exporter.descriptor("via-link", event),
        state_home=linked,
    )
    assert not (exporter.run_dir / "via-link.json").exists()
    not_directory = tmp_path / "state-file"
    not_directory.write_text("not a directory")
    refused(
        "output_integrity_error", views.list_outputs, *target, state_home=not_directory
    )
    # A missing home keeps the ordinary empty listing and not-found read.
    missing = tmp_path / "missing-state"
    assert views.list_outputs(*target, state_home=missing)["items"] == []
    refused(
        "output_not_found",
        views.read_output,
        *target,
        "homed",
        digest,
        "report",
        state_home=missing,
    )


def test_list_and_read_import_no_core_and_publish_imports_the_facade_lazily(
    gate,
) -> None:
    exporter, event = gate
    record = exporter.descriptor("isolated", event)
    digest = views.publish_output(copy.deepcopy(record), state_home=exporter.home)[
        "reference"
    ]["record_sha256"]
    saved = exporter.home.parent / "isolated.json"
    saved.write_bytes(views.canonical_json_bytes(record))
    probe = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "import millrace_plus.workflow_views as views\n"
        "home, instance, run, digest, saved = sys.argv[1:]\n"
        "views.list_outputs(instance, run, state_home=Path(home))\n"
        "views.read_output(instance, run, 'isolated', digest, 'report',\n"
        "                  state_home=Path(home))\n"
        "loaded = sorted(m for m in sys.modules if m == 'millrace'\n"
        "                or m.startswith('millrace.')\n"
        "                or m == 'millrace_plus.router.control')\n"
        "record = json.loads(Path(saved).read_bytes())\n"
        "status = views.publish_output(record, state_home=Path(home))['status']\n"
        "print(json.dumps([loaded, status,\n"
        "                  'millrace_plus.router.control' in sys.modules]))\n"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            probe,
            str(exporter.home),
            exporter.instance,
            exporter.run,
            digest,
            str(saved),
        ],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert json.loads(result.stdout) == [[], "already_published", True]


def test_views_publish_cli_uses_saved_bytes_and_structured_refusals(
    gate, tmp_path: Path, monkeypatch, capsys
) -> None:
    exporter, event = gate
    home = str(exporter.home)
    saved = tmp_path / "from-cli.json"
    saved.write_bytes(
        views.canonical_json_bytes(exporter.descriptor("from-cli", event))
    )
    before = saved.read_bytes()
    code, out, err = run_views(
        monkeypatch, capsys, "publish", "--file", str(saved), "--state-home", home
    )
    assert (code, err) == (0, "")
    result = json.loads(out)
    assert result["status"] == "published"
    assert result["reference"]["record_sha256"] == hashlib.sha256(before).hexdigest()
    # The saved bytes, including published_at, are used as given.
    assert (exporter.run_dir / "from-cli.json").read_bytes() == before
    assert saved.read_bytes() == before
    code, out, err = run_views(
        monkeypatch, capsys, "publish", "--file", str(saved), "--state-home", home
    )
    assert (code, json.loads(out)["status"]) == (0, "already_published")

    def failure(raw: bytes) -> dict[str, object]:
        candidate = tmp_path / "candidate.json"
        candidate.write_bytes(raw)
        code, out, err = run_views(
            monkeypatch, capsys, "publish", "--file", str(candidate), "--state-home",
            home,
        )
        assert (code, out) == (2, "")
        return json.loads(err)

    conflict = exporter.descriptor("from-cli", event, label="Another body")
    assert failure(views.canonical_json_bytes(conflict)) == {
        "code": "output_conflict",
        "message": "output_conflict",
    }
    for raw in (
        b'{"format":"duplicate",' + before[1:],
        b'{"value":NaN}',
        b"\xef\xbb\xbf" + before,
        before.decode().encode("utf-16"),
        b"[]",
        before + b" trailing",
    ):
        assert failure(raw)["code"] == "invalid_output_record"
    ghost = exporter.descriptor("ghost", event, instance_id=new_id())
    with pytest.raises(RouterError) as core_error:
        control.inspect_run_page(
            ghost["instance_id"], exporter.run, state_home=exporter.home
        )
    assert failure(views.canonical_json_bytes(ghost))["code"] == core_error.value.code
    code, out, err = run_views(
        monkeypatch,
        capsys,
        "publish",
        "--file",
        str(tmp_path / "absent.json"),
        "--state-home",
        home,
    )
    assert (code, json.loads(err)["code"]) == (2, "io_error")
    code, out, err = run_views(monkeypatch, capsys, "publish")
    assert (code, json.loads(err)["code"]) == (2, "usage_error")
    assert sorted(path.name for path in exporter.run_dir.iterdir()) == ["from-cli.json"]
