"""Cleanup depends on closure evidence, root identity and durable exports."""

import json

import pytest

from millrace_plus.demo import retention


def proofs(**overrides):
    return (
        dict(
            writers_stopped=True,
            child_exited=True,
            workers_drained=True,
            service_stopped=True,
            exports_verified=True,
        )
        | overrides
    )


@pytest.mark.parametrize("missing", list(proofs()))
def test_missing_proof_retains_everything(missing):
    root, owner = retention.create_root()
    (root / "workspace/data").write_bytes(b"kept")
    try:
        assert not retention.cleanup_root(root, owner, **proofs(**{missing: False}))
        assert (root / "workspace/data").read_bytes() == b"kept"
    finally:
        assert retention.cleanup_root(root, owner, **proofs())


def test_changed_marker_and_replaced_root_refuse(tmp_path):
    root, owner = retention.create_root()
    try:
        raw = (root / "owner.json").read_bytes()
        (root / "owner.json").write_text(json.dumps(owner | {"demo_id": "other"}))
        assert not retention.cleanup_root(root, owner, **proofs())
        (root / "owner.json").write_bytes(raw)
        wrong = owner | {"root_inode": owner["root_inode"] + 1}
        assert not retention.cleanup_root(root, wrong, **proofs())
    finally:
        assert retention.cleanup_root(root, owner, **proofs())


def test_exports_unique_verified_and_source_survives_failure(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_bytes(b"source")
    first, hashes = retention.export_results(
        tmp_path / "results", {"report.json": b"one"}
    )
    second, _ = retention.export_results(tmp_path / "results", {"report.json": b"two"})
    assert first != second and (first / "report.json").read_bytes() == b"one"
    assert len(hashes["report.json"]) == 64
    monkeypatch.setattr(
        retention.os, "fsync", lambda _: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        retention.export_results(
            tmp_path / "results", {"report.json": source.read_bytes()}
        )
    assert source.read_bytes() == b"source"


def test_cleanup_does_not_follow_workspace_symlink(tmp_path):
    root, owner = retention.create_root()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "file").write_bytes(b"preserved")
    (root / "workspace/link").symlink_to(unrelated, target_is_directory=True)
    assert retention.cleanup_root(root, owner, **proofs())
    assert (unrelated / "file").read_bytes() == b"preserved"
