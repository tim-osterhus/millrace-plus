"""Finite public-CLI transport, uncertainty and retained completion integrity."""

from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import replace

import pytest
from millforge_support import facts, fake_cli, materialize, native_profile, request
from millrace.router.execution import RouterError
from millrace.router.process import OwnedProcessError, ProcessResult
from test_boundary import _no_live_access

from millrace_plus.router import CoreContractError
from millrace_plus.router import millforge as m
from millrace_plus.router import millforge_configuration as c


def prepared(root, **options):
    peer = fake_cli(root, **options)
    req = request(root, executable=str(peer))
    plan = c.plan(req)
    materialize(plan)
    return plan


def invoke(plan, root, *, poll=lambda: False, started=lambda pid: None, deadline=None):
    return m.MillforgeRunAdapter().invoke(
        plan, cwd=root, env={"PATH": os.environ.get("PATH", "")},
        deadline=deadline or time.time() + 40, poll=poll, on_started=started,
    )


def observed(*, pid=123, code=0, cause=None, **changes):
    body = dict(
        pid=pid, exit_code=code, stdout=b"", stderr=b"", stop_cause=cause,
        stdout_bytes=0, stderr_bytes=0, stdout_sha256=c.digest(b""),
        stderr_sha256=c.digest(b""), stdout_truncated=False, stderr_truncated=False,
        stdout_overflow=False, stderr_overflow=False, stdin_delivered=True,
        stdin_closed=True, stdout_eof=True, stderr_eof=True,
        root_reaped=pid is not None,
        cleanup_forced=False, group_state="closed", cleanup_confirmed=True,
    )
    body.update(changes)
    return ProcessResult(**body)


def test_negative_completed_outcome_and_separate_inspector_pid(tmp_path, monkeypatch):
    plan = prepared(tmp_path, stderr="PRIVATE_NATIVE_DIAGNOSTIC")
    started = []
    result = invoke(plan, tmp_path, started=started.append)
    assert (result.classification, result.writer_state, result.exit_code) == (
        "completed", "stopped", 0,
    )
    assert json.loads(result.stdout) == {"outcome": "REJECT"}
    assert len(started) == 1
    e = result.adapter_evidence
    assert e["main"]["pid"] == started[0] == e["pid"]
    assert e["inspection"]["pid"] != started[0]
    assert "PRIVATE_NATIVE_DIAGNOSTIC" not in json.dumps(e)
    assert e["main"]["stderr_bytes"] == len("PRIVATE_NATIVE_DIAGNOSTIC")
    assert result.stderr == b""
    with _no_live_access(monkeypatch):
        c.validate_completion(plan, facts(plan, result, started[0]))


def test_closed_failed_native_record_preserves_failure_and_no_semantics(tmp_path):
    plan = prepared(tmp_path, exit=3)
    started = []
    result = invoke(plan, tmp_path, started=started.append)
    assert (result.classification, result.writer_state, result.exit_code) == (
        "execution_failure", "stopped", 3,
    )
    assert result.stdout == b"" and result.diagnostic == "millforge_runtime_failed"
    closure = result.adapter_evidence["accepted"]["closure"]
    assert closure["inference"] == "disconnected_unknown"
    c.validate_completion(plan, facts(plan, result, started[0]))


def test_clean_exit3_then_cancel_preserves_original_failure(tmp_path, monkeypatch):
    plan = prepared(tmp_path)
    returned = False
    calls = []

    def exited(argv, cwd, env, deadline, poll, started):
        nonlocal returned
        calls.append(argv)
        assert argv[1] == "run"
        started(123)
        returned = True
        return observed(code=3)

    monkeypatch.setattr(m, "_run", exited)
    result = invoke(plan, tmp_path, poll=lambda: returned)
    assert (result.classification, result.diagnostic, result.writer_state) == (
        "execution_failure", "millforge_runtime_failed", "may_live",
    )
    assert result.stdout == b"" and result.exit_code == 3
    assert len(calls) == 1 and result.adapter_evidence["inspection"] is None
    c.validate_completion(plan, facts(plan, result, 123))


def test_pure_inspector_receives_only_path_and_main_keeps_declared_environment(
    tmp_path, monkeypatch,
):
    req = request(tmp_path, executable=str(fake_cli(tmp_path)),
                  environment_names=("M4_CREDENTIAL",))
    plan = c.plan(req)
    materialize(plan)
    original = m._run
    environments = []
    main_env = {"PATH": os.environ.get("PATH", ""),
                "M4_CREDENTIAL": "PRIVATE_CREDENTIAL_SENTINEL"}

    def track(argv, cwd, env, deadline, poll, started):
        environments.append((argv[1], dict(env)))
        return original(argv, cwd, env, deadline, poll, started)

    monkeypatch.setattr(m, "_run", track)
    started = []
    result = m.MillforgeRunAdapter().invoke(
        plan, cwd=tmp_path, env=main_env, deadline=time.time() + 40,
        poll=lambda: False, on_started=started.append,
    )
    assert result.classification == "completed"
    assert environments == [
        ("run", main_env), ("inspect", {"PATH": main_env["PATH"]}),
    ]
    assert "PRIVATE_CREDENTIAL_SENTINEL" not in json.dumps(result.adapter_evidence)
    c.validate_completion(plan, facts(plan, result, started[0]))


@pytest.mark.parametrize("options,code", [
    ({"exit": 2}, "millforge_cli_failed"),
    ({"exit": 7}, "millforge_nonzero_exit"),
    ({"exit": 124}, "millforge_timeout"),
    ({"exit": 130}, "millforge_cancelled"),
    ({"stale": True}, "millforge_identity_mismatch"),
    ({"missing": True}, "millforge_record_invalid"),
    ({"invalid": True}, "millforge_record_invalid"),
    ({"result": {"request_id": "f" * 32}}, "millforge_identity_mismatch"),
    ({"result": {"mode": "agent"}}, "millforge_identity_mismatch"),
    ({"capture": {"schema": "millforge-capture/v2"}}, "millforge_record_invalid"),
    ({"capture": {"versions": {"millforge": "older", "pi": "1.4.0"}}},
     "millforge_record_invalid"),
    ({"uncertain": True}, "millforge_cleanup_unconfirmed"),
    ({"inspect_exit": 2}, "millforge_inspect_failed"),
    ({"output": {"outcome": "READY", "extra": True}}, "millforge_semantic_invalid"),
])
def test_invalid_or_nonzero_records_never_supply_semantic_output(
    tmp_path, options, code,
):
    plan = prepared(tmp_path, **options)
    started = []
    result = invoke(plan, tmp_path, started=started.append)
    assert result.stdout == b"" and result.diagnostic == code
    assert result.writer_state == "may_live"
    if options.get("exit") in {2, 7, 124, 130}:
        assert result.adapter_evidence["inspection"] is None
    c.validate_completion(plan, facts(plan, result, started[0]))


@pytest.mark.parametrize("options", [{"inspect_exit": 2}, {"invalid": True},
                                     {"uncertain": True}, {"stale": True},
                                     {"result": {"status": "completed"}}])
def test_exit3_inspection_failure_never_replaces_original_failure(tmp_path, options):
    plan = prepared(tmp_path, exit=3, **options)
    started = []
    result = invoke(plan, tmp_path, started=started.append)
    assert result.diagnostic == "millforge_runtime_failed"
    assert result.classification == "execution_failure" and result.stdout == b""
    assert result.writer_state == "may_live"
    c.validate_completion(plan, facts(plan, result, started[0]))


@pytest.mark.parametrize("kind,code", [
    ("time", "millforge_insufficient_timeout"),
    ("output", "millforge_output_exists"),
    ("helper", "millforge_owned_process_unavailable"),
    ("auth", "millforge_auth_home_unavailable"),
])
def test_prelaunch_refusals_have_no_child_and_no_native_diagnostic(
    tmp_path, monkeypatch, kind, code,
):
    plan = prepared(tmp_path)
    if kind == "output":
        (tmp_path / "invocation" / c.OUTPUT_NAME).symlink_to(tmp_path / "missing")
    if kind == "helper":
        def absent():
            raise CoreContractError("core_process_missing", "fixture")
        monkeypatch.setattr(m, "require_owned_process", absent)
    if kind == "auth":
        p = native_profile()
        p["model"]["auth"] = {"kind": "oauth", "provider": "openai"}
        req = request(tmp_path, profile=p,
                      binding_config={"profile": "profile.json", "assets": {},
                                      "auth_home": "a"},
                      host_paths={"a": str(tmp_path / "absent-auth")})
        plan = c.plan(req)
    monkeypatch.setattr(m, "_run", lambda *a, **k: pytest.fail("prelaunch spawned"))
    result = invoke(plan, tmp_path,
                    deadline=time.time() + (1 if kind == "time" else 40))
    assert (result.diagnostic, result.writer_state, result.exit_code) == (
        code, "stopped", None,
    )
    c.validate_completion(plan, facts(plan, result, None))


def test_deadline_clipping_inspection_and_preparation_loss(tmp_path, monkeypatch):
    plan = prepared(tmp_path)
    original = m._run
    deadlines = []
    def track(argv, cwd, env, deadline, poll, started):
        deadlines.append((argv[1], deadline))
        return original(argv, cwd, env, deadline, poll, started)
    monkeypatch.setattr(m, "_run", track)
    deadline = time.time() + 40
    result = invoke(plan, tmp_path, deadline=deadline)
    assert result.classification == "completed"
    assert deadlines[0] == ("run", deadline - 10)
    assert deadlines[1][0] == "inspect"
    assert deadlines[1][1] <= deadline - 5
    assert deadlines[1][1] <= time.time() + 5


@pytest.mark.parametrize("cause,code,transport", [
    ("timeout", "millforge_timeout", "timeout"),
    ("cancelled", "millforge_cancelled", "cancelled"),
    ("stdout_overflow", "millforge_stdout_limit", "protocol_failure"),
    ("stderr_overflow", "millforge_stderr_limit", "protocol_failure"),
    ("io_failed", "millforge_io_failed", "execution_failure"),
])
def test_helper_stop_precedence_and_detached_writer_uncertainty(
    tmp_path, monkeypatch, cause, code, transport,
):
    plan = prepared(tmp_path)
    calls = []
    def stopped(argv, cwd, env, deadline, poll, started):
        calls.append(argv)
        started(123)
        return observed(cause=cause, cleanup_forced=True)
    monkeypatch.setattr(m, "_run", stopped)
    result = invoke(plan, tmp_path)
    assert (result.classification, result.diagnostic, result.writer_state) == (
        transport, code, "may_live",
    )
    assert len(calls) == 1 and result.stdout == b""
    c.validate_completion(plan, facts(plan, result, 123))


def test_forced_root_cleanup_never_claims_detached_worker_stopped(
    tmp_path, monkeypatch,
):
    plan = prepared(tmp_path)
    def forced(argv, cwd, env, deadline, poll, started):
        started(123)
        return observed(cleanup_forced=True)
    monkeypatch.setattr(m, "_run", forced)
    result = invoke(plan, tmp_path)
    assert result.diagnostic == "millforge_cleanup_unconfirmed"
    assert result.writer_state == "may_live"
    c.validate_completion(plan, facts(plan, result, 123))


def test_active_cancellation_and_callback_failure_use_owned_helper(tmp_path):
    plan = prepared(tmp_path, sleep=20)
    active = []
    result = invoke(plan, tmp_path, started=active.append, poll=lambda: bool(active))
    assert result.classification == "cancelled" and result.writer_state == "may_live"
    assert result.adapter_evidence["main"]["cleanup_confirmed"]
    assert result.adapter_evidence["inspection"] is None
    c.validate_completion(plan, facts(plan, result, active[0]))
    second = prepared(tmp_path / "second", sleep=20)
    def broken(pid):
        raise RuntimeError("caller callback")
    with pytest.raises(OwnedProcessError) as caught:
        invoke(second, tmp_path, started=broken)
    assert caught.value.result.cleanup_confirmed
    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.parametrize("key", ["profile_digest", "mode", "domain_outcome", "closure",
                                "result_sha256", "final", "main"])
def test_retained_evidence_corruption_refuses_without_live_reads(
    tmp_path, monkeypatch, key,
):
    plan = prepared(tmp_path)
    started = []
    result = invoke(plan, tmp_path, started=started.append)
    corrupt = copy.deepcopy(result.adapter_evidence)
    if key == "main":
        corrupt["main"]["cleanup_forced"] = True
    elif key == "closure":
        corrupt["accepted"]["closure"]["writers"] = "may_live"
    elif key == "final":
        corrupt["accepted"]["final"]["sha256"] = "f" * 64
    else:
        corrupt["accepted"][key] = "changed"
    bad = replace(result, adapter_evidence=corrupt)
    with (_no_live_access(monkeypatch),
          pytest.raises(RouterError, match="completion_integrity")):
        c.validate_completion(plan, facts(plan, bad, started[0]))


def test_inspect_timeout_preserves_exit3_failure(tmp_path, monkeypatch):
    plan = prepared(tmp_path, exit=3)
    original = m._run
    def timed(argv, cwd, env, deadline, poll, started):
        if argv[1] == "inspect":
            return observed(pid=456, code=-15, cause="timeout", cleanup_forced=True)
        return original(argv, cwd, env, deadline, poll, started)
    monkeypatch.setattr(m, "_run", timed)
    started = []
    result = invoke(plan, tmp_path, started=started.append)
    assert result.diagnostic == "millforge_runtime_failed"
    assert result.writer_state == "may_live"
    c.validate_completion(plan, facts(plan, result, started[0]))
