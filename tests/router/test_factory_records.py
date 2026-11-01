"""Factory subjects preserve original criteria without acquiring CI authority."""

from __future__ import annotations

import copy

import pytest

from millrace_plus import factory_records as fr
from millrace_plus.local_intake import strict_json


def task():
    return {
        "format": "millrace-plus-factory-input/v1",
        "source_kind": "task",
        "source_id": "task-1",
        "repository": "example/calc",
        "base_candidate_sha256": None,
        "request": "Implement addition",
        "criteria": [{"id": "sum", "text": "2+3 returns 5", "origin_ref": None}],
        "feedback_ref": None,
        "evidence_refs": [],
        "scope_ref": None,
        "occurred_at": "2026-10-31T00:00:00Z",
    }


def test_exact_task_identity_and_original_criteria():
    value = task()
    assert fr.validate_input(value) == value
    assert fr.subject_id(value) == fr.subject_id(copy.deepcopy(value))
    changed = value | {"request": "A changed request"}
    assert fr.source_key(changed) == fr.source_key(value)
    assert fr.subject_id(changed) != fr.subject_id(value)


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"source_kind": "unknown"},
        {"request": " "},
        {"criteria": []},
        {"base_candidate_sha256": "f"},
        {"evidence_refs": [{"path": "../authority", "sha256": "a" * 64}]},
        {"criteria": [{"id": "a", "text": "x", "origin_ref": None}] * 2},
        {"feedback_ref": {"path": "a.json", "sha256": "a" * 64}},
    ],
)
def test_task_refuses_ambiguous_or_changed_authority(change):
    with pytest.raises(fr.FactoryError):
        fr.validate_input(task() | change)


def test_duplicate_keys_and_nonfinite_refuse():
    for raw in (b'{"format":1,"format":2}', b'{"value":NaN}'):
        with pytest.raises(Exception):
            strict_json(raw)


def test_feedback_does_not_invent_task_criteria():
    value = task() | {
        "source_kind": "user_report",
        "request": None,
        "criteria": [],
        "feedback_ref": {"path": "report.json", "sha256": "a" * 64},
    }
    assert fr.validate_input(value) == value
    with pytest.raises(fr.FactoryError):
        fr.validate_input(value | {"request": "repair it"})
    with pytest.raises(fr.FactoryError):
        fr.validate_input(value | {"criteria": task()["criteria"]})


def test_source_key_uses_full_identity_digest_in_the_existing_intake_shape():
    import base64
    import hashlib

    from millrace_plus.local_intake import KEY, canonical

    value = task()
    key = fr.source_key(value)
    assert KEY.fullmatch(key)
    assert (
        base64.b32decode(key[2:] + "====")
        == hashlib.sha256(
            canonical(
                {
                    field: value[field]
                    for field in ("source_kind", "source_id", "repository")
                }
            )
        ).digest()
    )


def test_assignment_identity_cannot_reset_failure_by_rekeying_or_timestamp():
    value = task()
    rekeyed = value | {"source_id": "another", "occurred_at": "2026-11-01T00:00:00Z"}
    assert fr.source_key(value) != fr.source_key(rekeyed)
    assert fr.assignment_sha256(value) == fr.assignment_sha256(rekeyed)
    assert fr.assignment_sha256(
        value | {"request": "A changed declared approach"}
    ) != fr.assignment_sha256(value)
