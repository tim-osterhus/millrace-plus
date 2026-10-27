"""Separate v2 grammar, encoded limits and immutable replay authority."""

from __future__ import annotations

import copy
import hashlib

import pytest
from persistent_lad_support import FakeClient, advance, journal, setup

from millrace_plus import subworkflow_records as v1
from millrace_plus import subworkflow_v2_records as records
from millrace_plus.subworkflow_records import (
    SubworkflowError,
    canonical,
    digest,
    mapping,
)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    return setup(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "change",
    [
        {"format": "millrace-plus-subworkflow-request/v1"},
        {"wait_seconds": 1},
        {"extra": True},
        {"observe_seconds": None},
        {"observe_seconds": True},
        {"observe_seconds": 0},
        {"observe_seconds": 301},
        {"observe_seconds": 1.0},
        {"cleanup_seconds": 4},
        {"cleanup_seconds": 61},
        {"inputs": None},
        {"parent": {"instance_id": "parent"}},
        {"parent": {"instance_id": "A" * 32}},
        {"parent": {"instance_id": "1" * 32, "run_id": None}},
        {"parent": {"instance_id": "1" * 32, "state_home": "/state"}},
        {"output_ref": "a//b"},
        {"output_ref": "../result"},
        {"output_ref": "/result"},
        {"output_ref": "a\\b"},
        {"output_ref": "a/./b"},
    ],
)
def test_v2_strict_fields_types_and_parent_ids(fixture, change):
    request = {**fixture["request"], **change}
    with pytest.raises(SubworkflowError):
        records.parse_request(canonical(request), cwd=fixture["parent"])


@pytest.mark.parametrize("raw", [b'{"format":1,"format":2}', b'{"x":NaN}', b"\xff"])
def test_v2_duplicate_nonfinite_and_utf8_refusal(fixture, raw):
    with pytest.raises(SubworkflowError):
        records.parse_request(raw, cwd=fixture["parent"])


def test_v2_bounds_do_not_relax_v1(fixture):
    request = fixture["request"]
    request["inputs"] = {"request_markdown": "\x01" * 65536}
    normalized = records.parse_request(canonical(request), cwd=fixture["parent"])
    manifest = records.input_manifest(normalized)
    assert manifest["format"] == "millrace-plus-subworkflow-inputs/v1"
    assert set(manifest) == {"format", "identity", "inputs"}
    assert 73728 < len(canonical(manifest)) < records.INPUT_LIMIT
    old = {
        k: v for k, v in request.items() if k not in {"os_profile", "observe_seconds"}
    }
    old["format"] = v1.REQUEST_FORMAT
    old["parent"] = {**old["parent"], "state_home": str(fixture["root"] / "state")}
    with pytest.raises(SubworkflowError, match="record_too_large"):
        v1.parse_request(canonical(old), cwd=fixture["parent"])
    old["inputs"] = {}
    old_bytes = canonical(old)
    before = v1.parse_request(old_bytes, cwd=fixture["parent"])
    assert v1.parse_request(old_bytes, cwd=fixture["parent"]) == before
    assert before["wait_seconds"] == 300
    assert records.JOURNAL_LIMIT == 2097152
    assert records.RESULT_LIMIT == 262144
    assert str(v1.NAMESPACE) == "d0ad2e51-b41f-5a75-886a-a17623fb5db4"
    assert v1.request_id(normalized, "start_run") == v1.request_id(before, "start_run")


def test_input_write_once_and_larger_caller_limit(fixture):
    request = copy.deepcopy(fixture["request"])
    request["inputs"] = {"request_markdown": "\x01" * 65536}
    parsed = records.parse_request(canonical(request), cwd=fixture["parent"])
    file = fixture["parent"] / "input.json"
    records.write_inputs(file, parsed)
    original = file.read_bytes()
    assert original.endswith(b"\n") and not original.endswith(b"\n\n")
    records.write_inputs(file, parsed)
    assert file.read_bytes() == original
    parsed["inputs"] = {}
    with pytest.raises(SubworkflowError, match="input_manifest_conflict"):
        records.write_inputs(file, parsed)
    assert file.read_bytes() == original


def test_max_task_escaped_capacity_and_overflow(fixture):
    advance(fixture, FakeClient(fixture))
    saved = journal(fixture).load()
    records.preflight_capacity(saved)
    oversized = copy.deepcopy(saved)
    mapping(mapping(oversized["request"])["inputs"])["target_metadata"] = "x" * 1048576
    with pytest.raises(SubworkflowError, match="record_too_large"):
        records.preflight_capacity(oversized)
    assert journal(fixture).load() == saved


def test_exact_request_body_limit_precedes_journal_creation(fixture):
    fixture["settings"]["runtime_defaults"] = {"padding": "x" * 4096}
    with pytest.raises(SubworkflowError, match="os_request_limit"):
        advance(fixture, FakeClient(fixture))
    assert not journal(fixture).root.exists()
    assert not (fixture["parent"] / "results").exists()


def test_journal_preserves_raw_bytes_and_three_distinct_digests(fixture):
    raw = b"  " + canonical(fixture["request"]) + b"\n\n"
    fixture["file"].write_bytes(raw)
    advance(fixture, FakeClient(fixture))
    saved = journal(fixture).load()
    assert saved["request_raw"].encode() == raw
    assert saved["request_sha256"] == digest(saved["request"])
    assert saved["claim_sha256"] == "a" * 64
    assert saved["request_sha256"] != hashlib.sha256(raw).hexdigest()
    assert "state" not in mapping(saved["paths"])
    assert "controller" not in canonical(saved).decode()
    assert "Bearer " not in canonical(saved).decode()
    assert "token" not in saved


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(claim_sha256="b" * 64),
        lambda row: mapping(row["paths"]).update(state="/tmp/forbidden"),
        lambda row: mapping(mapping(row["envelopes"])["start"]).update(run_id="3" * 32),
        lambda row: mapping(row["reservations"]).update(start="unsent_wrong"),
        lambda row: mapping(row["receipts"]).update(start={"status": "applied"}),
        lambda row: mapping(row["observation"]).update(start_request_id="f" * 32),
    ],
)
def test_journal_replay_rejects_authority_or_identity_mutation(fixture, mutation):
    advance(fixture, FakeClient(fixture))
    storage = journal(fixture)
    original = storage.path.read_bytes()
    row = storage.load()
    mutation(row)
    with pytest.raises(SubworkflowError):
        storage.save(row)
    assert storage.path.read_bytes() == original


@pytest.mark.parametrize(
    "field,value",
    [
        ("home", "other"),
        ("request_id", "f" * 32),
        ("request_digest", "f" * 64),
        ("instance_id", "f" * 32),
        ("contract", "millrace-os-router-call/v1"),
        ("holder_joined", 1),
        ("source", "core_receipt"),
        ("phase", "invented_join"),
    ],
)
def test_proven_create_reuse_rejects_invalid_retained_authority(fixture, field, value):
    advance(fixture, FakeClient(fixture))
    storage = journal(fixture)
    original = storage.path.read_bytes()
    saved = storage.load()
    create_id = saved["envelopes"]["create"]["request_id"]
    proof = next(
        row for row in saved["os_observations"] if row["request_id"] == create_id
    )
    proof[field] = value
    with pytest.raises(SubworkflowError):
        storage.save(saved)
    assert storage.path.read_bytes() == original


def test_proven_create_history_keeps_later_null_in_journal_and_result(fixture):
    advance(fixture, FakeClient(fixture))
    storage = journal(fixture)
    saved = storage.load()
    create_id = saved["envelopes"]["create"]["request_id"]
    original = copy.deepcopy(saved["os_observations"])
    later = copy.deepcopy(
        next(row for row in original if row["request_id"] == create_id)
    )
    later.update(
        source="core_receipt",
        phase="receipt_observed",
        holder_joined=None,
        observed_at="2026-10-26T00:01:00Z",
    )
    saved["os_observations"].append(later)
    storage.save(saved)
    reloaded = storage.load()
    assert reloaded["os_observations"] == original + [later]
    manifest = records.result_manifest(reloaded, "CHILD_RUNNING", None)
    assert [
        row["holder_joined"]
        for row in manifest["os_observations"]
        if row["request_id"] == create_id
    ] == [True, None]
    assert records.validate_result(manifest) == manifest


def test_record_hash_and_encoded_journal_bound(fixture):
    advance(fixture, FakeClient(fixture))
    storage = journal(fixture)
    row = storage.load()
    row["record_sha256"] = "0" * 64
    with pytest.raises(SubworkflowError, match="invalid_journal"):
        records.validate_journal(row, storage.home, storage.key)
    with pytest.raises(SubworkflowError, match="record_too_large"):
        records.parse_journal(
            b" " * (records.JOURNAL_LIMIT + 1), home=storage.home, key=storage.key
        )


@pytest.mark.parametrize("outcome", sorted(records.OUTCOMES))
def test_result_manifest_exact_shape_for_all_eight_outcomes(fixture, outcome):
    advance(fixture, FakeClient(fixture))
    saved = journal(fixture).load()
    manifest = records.result_manifest(saved, outcome, None)
    raw = canonical(manifest) + b"\n"
    assert records.parse_result(raw) == manifest
    assert set(manifest) == {
        "format",
        "call_identity",
        "call_key",
        "definition",
        "instance_id",
        "run_id",
        "dispatch",
        "status",
        "observation",
        "receipts",
        "os_observations",
        "outcome",
        "diagnostic",
    }
    assert manifest["dispatch"] == "started"
    assert all(
        set(row) == records.REFERENCE_FIELDS for row in manifest["os_observations"]
    )
    assert all(row["holder_joined"] is True for row in manifest["os_observations"])
    with pytest.raises(SubworkflowError):
        records.parse_result(canonical({**manifest, "controllers": []}))


def test_p_l1_claim_bytes_remain_exact(fixture):
    # Generic calls consume a digest; they cannot append service/OS/observation fields.
    claim = {
        "task_id": "f" * 32,
        "request_sha256": hashlib.sha256(b"Exact task\r\n").hexdigest(),
        "definition": fixture["request"]["definition"],
        "call_key": "task_1",
        "call_home": fixture["request"]["call_home"],
        "output_ref": "results/task_1.json",
        "create_request_id": v1.request_id(fixture["request"], "create_instance"),
        "start_request_id": v1.request_id(fixture["request"], "start_run"),
    }
    before = canonical(claim)
    assert len(claim) == 8
    from persistent_lad_support import Clock

    from millrace_plus.subworkflow_v2 import advance as operation

    clock = Clock()
    operation(
        fixture["file"],
        expected_home="local",
        claim_sha256=digest(claim),
        cwd=fixture["parent"],
        client=FakeClient(fixture),
        clock=clock,
        sleep=clock.sleep,
    )
    assert canonical(claim) == before
    assert journal(fixture).load()["claim_sha256"] == digest(claim)


@pytest.mark.parametrize(
    "encoding",
    [
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "utf-32",
        "utf-32-le",
        "utf-32-be",
        "utf-8-sig",
    ],
)
def test_non_utf8_v2_files_refuse_without_mutation(fixture, encoding):
    client = FakeClient(fixture)
    request = fixture["request"]
    fixture["file"].write_bytes(canonical(request).decode("utf-8").encode(encoding))
    with pytest.raises(SubworkflowError, match="invalid_json"):
        advance(fixture, client)
    assert not journal(fixture).home.exists()
    assert client.events == []
    fixture["file"].write_bytes(canonical(request) + b"\n")
    advance(fixture, client)
    storage = journal(fixture)
    before = storage.path.read_bytes()
    encoded = before.decode("utf-8").encode(encoding)
    with pytest.raises(SubworkflowError, match="invalid_json"):
        records.parse_journal(encoded, home=storage.home, key=storage.key)
    result_file = fixture["parent"] / "results/task_1.json"
    result = result_file.read_bytes()
    with pytest.raises(SubworkflowError, match="invalid_json"):
        records.parse_result(result.decode("utf-8").encode(encoding))
    parsed = records.parse_request(canonical(request), cwd=fixture["parent"])
    manifest = canonical(records.input_manifest(parsed)) + b"\n"
    input_file = fixture["parent"] / "encoded-inputs.json"
    encoded_input = manifest.decode("utf-8").encode(encoding)
    input_file.write_bytes(encoded_input)
    with pytest.raises(SubworkflowError, match="input_manifest_conflict"):
        records.write_inputs(input_file, parsed)
    assert input_file.read_bytes() == encoded_input
    assert storage.path.read_bytes() == before
    assert result_file.read_bytes() == result


@pytest.mark.parametrize("evidence", ["receipt", "history"])
def test_unsent_rollback_conflicts_with_retained_dispatch_evidence(fixture, evidence):
    client = FakeClient(fixture)
    advance(fixture, client)
    storage = journal(fixture)
    before = storage.path.read_bytes()
    row = storage.load()
    mapping(row["reservations"])["start"] = "unsent"
    if evidence == "receipt":
        start_id = mapping(mapping(row["envelopes"])["start"])["request_id"]
        row["os_observations"] = [
            item for item in row["os_observations"] if item["request_id"] != start_id
        ]
    else:
        mapping(row["receipts"]).pop("start")
        row.update(run_id=None, observation={})
    with pytest.raises(SubworkflowError, match="invalid_reservation"):
        storage.save(row)
    assert storage.path.read_bytes() == before
    row["record_sha256"] = digest(
        {k: v for k, v in row.items() if k != "record_sha256"}
    )
    corrupted = canonical(row) + b"\n"
    storage.path.write_bytes(corrupted)
    sends = copy.deepcopy(client.sent)
    with pytest.raises(SubworkflowError, match="invalid_reservation"):
        storage.load()
    with pytest.raises(SubworkflowError, match="invalid_reservation"):
        advance(fixture, client)
    assert storage.path.read_bytes() == corrupted
    assert client.sent == sends


@pytest.mark.parametrize(
    "mutation", ["intent", "alias", "reservation", "target", "receipt", "missing"]
)
def test_cancel_record_authority_corruption_preserves_previous_bytes(fixture, mutation):
    from millrace_plus import subworkflow_v2 as helper
    from millrace_plus.subworkflow_records import envelope

    advance(fixture, FakeClient(fixture))
    storage = journal(fixture)
    saved = storage.load()
    identifier = "b" * 32
    created = saved["created_at"]
    sent = envelope(
        "cancel_run",
        saved["request"],
        instance_id=saved["instance_id"],
        run_id=saved["run_id"],
        explicit_id=identifier,
    )
    saved["cancel_intent"] = {"request_id": identifier, "created_at": created}
    saved["cancellations"] = {
        identifier: {
            "created_at": created,
            "envelope": sent,
            "receipt": None,
            "reservation": "unsent",
        }
    }
    helper._cancel_aliases(saved)
    storage.save(saved)
    before = storage.path.read_bytes()
    changed = copy.deepcopy(saved)
    row = changed["cancellations"][identifier]
    if mutation == "intent":
        changed["cancel_intent"]["request_id"] = "c" * 32
    elif mutation == "alias":
        changed["envelopes"]["cancel"] = None
    elif mutation == "reservation":
        row["reservation"] = "observed"
    elif mutation == "target":
        row["envelope"]["run_id"] = "c" * 32
    elif mutation == "receipt":
        row["receipt"] = {"request_id": identifier}
    else:
        del changed["cancel_intent"]
    with pytest.raises(SubworkflowError):
        storage.save(changed)
    assert storage.path.read_bytes() == before


def test_result_projection_is_fresh_and_omits_only_top_level_draft(fixture):
    client = FakeClient(fixture)
    client.status = "succeeded"
    advance(fixture, client)
    saved = journal(fixture).load()
    raw = saved["observation"]
    raw["draft_revision"] = {"files": {"captured": "x" * 325000}}
    raw["nested"] = {"draft_revision": "preserved", "zero": 0, "null": None}
    before = canonical(raw)
    result = records.result_manifest(saved, "CHILD_SUCCEEDED", None)
    projected = result["observation"]
    assert projected is not raw
    assert set(projected) == set(raw) - {"draft_revision"}
    assert all(projected[name] == raw[name] for name in projected)
    assert canonical(raw) == before
    assert records.validate_result(result) == result
    projected["projection_only"] = True
    assert "projection_only" not in raw
    assert canonical(raw) == before


def test_projected_result_limit_still_includes_final_lf(fixture):
    client = FakeClient(fixture)
    client.status = "succeeded"
    advance(fixture, client)
    saved = journal(fixture).load()
    saved["observation"]["draft_revision"] = {"files": {"captured": "x" * 325000}}
    before = canonical(saved["observation"])
    result = records.result_manifest(saved, "CHILD_SUCCEEDED", None)
    result["observation"]["padding"] = ""
    remaining = records.RESULT_LIMIT - len(canonical(result)) - 1
    assert remaining > 0
    result["observation"]["padding"] = "x" * remaining
    assert len(canonical(result) + b"\n") == 262144
    records.validate_result(result)
    result["observation"]["padding"] += "x"
    with pytest.raises(SubworkflowError, match="record_too_large"):
        records.validate_result(result)
    assert canonical(saved["observation"]) == before
