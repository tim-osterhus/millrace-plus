from __future__ import annotations

import hashlib
import json

import pytest

from millrace_plus.demo import stages
from millrace_plus.demo.contracts import (
    DemoError,
    canonical_bytes,
    load_document,
    validate_report,
)
from millrace_plus.demo.estimate import build_baseline


def _owner(root):
    root = root.resolve()
    info = root.stat()
    owner = {
        "format": "millrace-demo-owner/v1",
        "demo_id": "d" * 32,
        "root": str(root),
        "root_device": info.st_dev,
        "root_inode": info.st_ino,
        "created_at": "2026-11-11T03:40:00+00:00",
        "child_observation": None,
    }
    marker = root / "owner.json"
    marker.write_bytes(canonical_bytes(owner))
    marker.chmod(0o600)
    return owner


def _session(tmp_path, baseline):
    _owner(tmp_path)
    instance, run, invocation = (str(n) * 32 for n in range(1, 4))
    revision = hashlib.sha256(b"fixture revision").hexdigest()
    descriptor = {
        "format": "millrace-demo-session/v1",
        "instance_id": instance,
        "run_id": run,
        "plane_id": "main",
        "invocation_id": invocation,
        "producing_revision_id": revision,
        "state_home": str(tmp_path / "state"),
        "baseline": {
            "reference": {
                "instance_id": instance,
                "run_id": run,
                "output_id": "baseline",
                "record_sha256": "a" * 64,
            },
            "json_sha256": hashlib.sha256(canonical_bytes(baseline)).hexdigest(),
            "report_sha256": hashlib.sha256(
                canonical_bytes(baseline["display"])
            ).hexdigest(),
        },
    }
    path = tmp_path.resolve() / "demo-session.json"
    path.write_bytes(canonical_bytes(descriptor))
    return path, descriptor


def _read_result(session, baseline):
    return {
        "reference": session["baseline"]["reference"],
        "producer": {"id": "millrace-plus-hardware-demo", "version": "1"},
        "label": "Hardware baseline",
        "published_at": "2026-11-11T03:40:00+00:00",
        "invocation": {
            "plane_id": "main",
            "node_id": "baseline",
            "invocation_id": session["invocation_id"],
            "producing_revision_id": session["producing_revision_id"],
        },
        "representation": {
            "name": "json",
            "format": baseline["format"],
            "media_type": "application/json",
            "sha256": session["baseline"]["json_sha256"],
            "data": baseline,
        },
    }


def test_session_accepts_retained_public_producer_identity(
    tmp_path, hardware, monkeypatch
):
    baseline = build_baseline(hardware)
    path, session = _session(tmp_path, baseline)
    # Safe public inspection from the installed command journey at baseline_saved.
    producer = {
        "plane_id": "main",
        "node_id": "baseline",
        "invocation_id": "82591284f4af4a82b06eaccf968ae272",
        "producing_revision_id": (
            "a9b8a4ed1e7e6c527dfc213ec4be195452ff2d852b86d2a3e0ed0f6bd8475d9f"
        ),
    }
    session.update({key: value for key, value in producer.items() if key != "node_id"})
    session["run_id"] = "b7597745fafe4734a8cc1e9c3369f0c9"
    session["baseline"]["reference"]["run_id"] = session["run_id"]
    path.write_bytes(canonical_bytes(session))
    observed = _read_result(session, baseline)
    observed["invocation"] = producer
    monkeypatch.setattr(stages, "read_output", lambda *a, **kw: observed)
    accepted = stages._session(path)
    assert accepted == session
    assert stages._captured_baseline(accepted) == baseline


@pytest.mark.parametrize(
    "field,value",
    [
        ("producing_revision_id", "4" * 32),
        ("producing_revision_id", "A" * 64),
        ("instance_id", "1" * 64),
        ("run_id", "2" * 64),
        ("invocation_id", "3" * 64),
    ],
)
def test_session_keeps_uuid_and_revision_digest_shapes_distinct(
    tmp_path, hardware, field, value
):
    path, session = _session(tmp_path, build_baseline(hardware))
    session[field] = value
    path.write_bytes(canonical_bytes(session))
    with pytest.raises(DemoError, match="invalid_demo_session"):
        stages._session(path)


def test_saved_baseline_to_immutable_fallback(tmp_path, hardware, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(stages, "collect_hardware", lambda: hardware)
    assert stages.run_stage("detect", workspace=workspace) == 0
    assert stages.run_stage("baseline", workspace=workspace) == 0
    baseline = load_document(workspace / "baseline.json")
    path, session = _session(tmp_path, baseline)
    monkeypatch.setattr(
        stages, "read_output", lambda *a, **kw: _read_result(session, baseline)
    )
    monkeypatch.setattr(
        stages, "_native_state", lambda s: stages._status("skipped", "not_selected")
    )
    assert stages.run_stage("skip", workspace=workspace, session_file=path) == 0
    (workspace / "baseline.json").chmod(0o600)
    (workspace / "baseline.json").write_text('{"changed":true}')
    assert stages.run_stage("fallback", workspace=workspace, session_file=path) == 0
    assert load_document(workspace / "baseline.json") == baseline
    assert stages.run_stage("render", workspace=workspace, session_file=path) == 0
    report = validate_report(load_document(workspace / "report.json"))
    assert report["baseline"] == baseline
    assert report["native"]["status"] == "skipped"
    assert report["demo_id"] == load_document(tmp_path / "owner.json")["demo_id"]
    assert report["demo_id"] != session["run_id"]
    for line in capsys.readouterr().out.splitlines():
        value = json.loads(line)
        assert set(value) == {"outcome", "summary", "refs"}
        assert all(not ref.startswith("/") and ".." not in ref for ref in value["refs"])


@pytest.mark.parametrize("replace_baseline", [False, True])
def test_native_candidate_fake_accept_and_replacement_reject(
    tmp_path, hardware, candidate, monkeypatch, replace_baseline
):
    baseline = build_baseline(hardware)
    path, session = _session(tmp_path, baseline)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "baseline.json").write_bytes(
        canonical_bytes({"modified": True} if replace_baseline else baseline)
    )
    (workspace / "candidate.json").write_bytes(canonical_bytes(candidate))
    monkeypatch.setattr(
        stages, "read_output", lambda *a, **kw: _read_result(session, baseline)
    )
    monkeypatch.setattr(
        stages, "_native_state", lambda s: stages._status("completed", phase="main")
    )
    assert (
        stages.run_stage(
            "validate", workspace=workspace, session_file=path, no_web=True
        )
        == 0
    )
    native = load_document(workspace / "native-status.json")
    assert native["status"] == ("rejected" if replace_baseline else "completed")
    if replace_baseline:
        assert load_document(workspace / "baseline.json") == baseline
    else:
        assert native["candidate"] == candidate
        assert stages.run_stage("render", workspace=workspace, session_file=path) == 0
        assert (
            validate_report(load_document(workspace / "report.json"))["native"][
                "candidate"
            ]
            == candidate
        )


@pytest.mark.parametrize("corrupt", ["reference", "digest", "invocation", "session"])
def test_immutable_reference_refusals(tmp_path, hardware, monkeypatch, corrupt):
    baseline = build_baseline(hardware)
    path, session = _session(tmp_path, baseline)
    observed = _read_result(session, baseline)
    if corrupt == "reference":
        observed["reference"] = {**observed["reference"], "record_sha256": "b" * 64}
    if corrupt == "digest":
        observed["representation"]["sha256"] = "b" * 64
    if corrupt == "invocation":
        observed["invocation"]["invocation_id"] = "5" * 32
    if corrupt == "session":
        session["credential_path"] = "not-permitted"
        path.write_bytes(canonical_bytes(session))
    monkeypatch.setattr(stages, "read_output", lambda *a, **kw: observed)
    assert stages.run_stage("skip", workspace=tmp_path, session_file=path) == 1
    assert not (tmp_path / "native-status.json").exists()


def test_unavailable_is_valid_unknown_report(tmp_path):
    owner = _owner(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_file = tmp_path.resolve() / "demo-session.json"
    assert not session_file.exists()
    assert (
        stages.run_stage("unavailable", workspace=workspace, session_file=session_file)
        == 0
    )
    report = validate_report(load_document(workspace / "report.json"))
    assert report["demo_id"] == owner["demo_id"]
    assert report["native"]["status"] == "unavailable"
    assert all(
        e["scenarios"][0]["fit_status"] == "unknown"
        for e in report["baseline"]["entries"]
    )


@pytest.mark.parametrize(
    "corrupt",
    ["missing", "malformed", "demo_id", "root", "inode", "symlink", "permissions"],
)
def test_unavailable_refuses_invalid_owner_before_writing(tmp_path, corrupt):
    owner = _owner(tmp_path)
    marker = tmp_path / "owner.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if corrupt == "missing":
        marker.unlink()
    elif corrupt == "malformed":
        marker.write_bytes(b"{}")
    elif corrupt == "demo_id":
        marker.write_bytes(canonical_bytes(owner | {"demo_id": "invalid"}))
    elif corrupt == "root":
        marker.write_bytes(canonical_bytes(owner | {"root": str(workspace)}))
    elif corrupt == "inode":
        marker.write_bytes(
            canonical_bytes(owner | {"root_inode": owner["root_inode"] + 1})
        )
    elif corrupt == "symlink":
        copied = tmp_path / "other-owner.json"
        marker.rename(copied)
        marker.symlink_to(copied)
    else:
        marker.chmod(0o644)
    assert (
        stages.run_stage(
            "unavailable",
            workspace=workspace,
            session_file=tmp_path.resolve() / "demo-session.json",
        )
        == 1
    )
    assert not (workspace / "baseline.json").exists()
    assert not (workspace / "report.json").exists()


def test_unavailable_refuses_a_replaced_root(tmp_path):
    root = tmp_path / "demo"
    root.mkdir()
    _owner(root)
    original = tmp_path / "preserved-demo"
    root.rename(original)
    root.mkdir()
    (root / "workspace").mkdir()
    marker = root / "owner.json"
    marker.write_bytes((original / "owner.json").read_bytes())
    marker.chmod(0o600)
    assert (
        stages.run_stage(
            "unavailable",
            workspace=root / "workspace",
            session_file=root.resolve() / "demo-session.json",
        )
        == 1
    )
    assert not (root / "workspace/report.json").exists()


@pytest.mark.parametrize("writer", ["may_live", "unknown"])
def test_uncertain_writer_refuses_before_fallback(
    tmp_path, hardware, monkeypatch, writer
):
    from millrace_plus.router import control

    baseline = build_baseline(hardware)
    _, session = _session(tmp_path, baseline)
    monkeypatch.setattr(
        control,
        "inspect_run",
        lambda *a, **kw: {
            "invocations": [{"node": "native", "id": "6" * 32, "writer_state": writer}],
            "completions": [],
        },
    )
    with pytest.raises(DemoError, match="writer_unconfirmed"):
        stages._native_state(session)


@pytest.mark.parametrize(
    "phase,transport,classification,outcome,expected",
    [
        ("preflight", "exited", "execution_failure", None, "preparation_failed"),
        ("probe", "exited", "execution_failure", None, "preparation_failed"),
        ("main", "timeout", "execution_failure", None, "timed_out"),
        ("main", "exited", "protocol_failure", None, "protocol_failure"),
        ("main", "exited", "semantic", "DECLINED", "unavailable"),
        ("main", "exited", "semantic", "PROPOSED", "completed"),
    ],
)
def test_factual_native_status_from_public_inspection(
    tmp_path, hardware, monkeypatch, phase, transport, classification, outcome, expected
):
    from millrace_plus.router import control

    baseline = build_baseline(hardware)
    _, session = _session(tmp_path, baseline)
    invocation_id = "6" * 32
    monkeypatch.setattr(
        control,
        "inspect_run",
        lambda *a, **kw: {
            "invocations": [
                {
                    "node": "native",
                    "id": invocation_id,
                    "writer_state": "stopped",
                    "adapter_evidence": {"phase": phase},
                }
            ],
            "completions": [
                {
                    "invocation_id": invocation_id,
                    "transport": transport,
                    "classification": classification,
                    "outcome": outcome,
                }
            ],
        },
    )
    observed = stages._native_state(session)
    assert observed["status"] == expected and observed["phase"] == phase
