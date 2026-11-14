"""Raw-first, revision and public-control aftermath for the Plus Claude adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from claude_support import ADAPTER_CONTRACT, ADAPTER_ID, envelope, fake, flow
from millrace.router.execution import AdapterResult, RouterError
from millrace.router.store import Store
from plus_support import Crash, create_instance, new_id, start_run, store_path

from millrace_plus.router import claude as claude_adapter
from millrace_plus.router import control
from millrace_plus.router import native_diagnostic as nd
from millrace_plus.router.claude import ClaudePrintAdapter


def test_resolver_three_exact_pairs() -> None:
    assert isinstance(
        control.resolve_adapter(ADAPTER_ID, ADAPTER_CONTRACT), ClaudePrintAdapter
    )
    for pair in ((ADAPTER_ID, "wrong"), ("unknown", ADAPTER_CONTRACT)):
        with pytest.raises(RouterError):
            control.resolve_adapter(*pair)
    assert (
        control.resolve_adapter("pi_json", "millrace-pi-json/v1").adapter_id
        == "pi_json"
    )
    assert (
        control.resolve_adapter("codex_exec", "millrace-codex-exec/v1").adapter_id
        == "codex_exec"
    )


@pytest.mark.parametrize("version", ["2.1.296", "2.1.305"])
@pytest.mark.parametrize(
    "semantic,status,classification",
    [
        ({"outcome": "READY", "summary": "done", "refs": []}, "succeeded", "semantic"),
        ({"outcome": "OTHER"}, "failed", "unknown"),
        ({"summary": "missing"}, "failed", "protocol_failure"),
        ({"outcome": "READY", "refs": ["../escape"]}, "failed", "protocol_failure"),
        ({"outcome": "READY", "extra": 1}, "failed", "protocol_failure"),
    ],
)
def test_raw_first_completion_and_corruption(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    semantic: dict[str, Any],
    status: str,
    classification: str,
    version: str,
) -> None:
    workflow, project, home = flow(
        single_flow,
        claude_version=version,
        version=f"{version} (Claude Code)\n".encode(),
        wire=json.dumps(envelope(structured_output=semantic)).encode(),
    )
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    ids = {}
    original = Store.complete

    def crash(store: Store, *args: Any, **kwargs: Any) -> None:
        original(store, *args, **kwargs)
        raise Crash()

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    path = store_path(instance, home)
    raw = json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT stdout,transport,classification FROM completions"
        ).fetchone() == (raw, "completed", None)
        assert db.execute("SELECT count(*) FROM transitions").fetchone()[0] == 0
        captured = json.loads(db.execute("SELECT body FROM invocations").fetchone()[0])
        assert captured["launch"]["details"]["expected_version"] == version
        assert captured["adapter_evidence"]["observed_version"] == version
    run = control.inspect_run(instance, str(ids["run_id"]), state_home=home)
    receipt = control.recover_run(
        instance,
        str(run["id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=run["state_version"],
    )
    assert receipt["status"] == "applied"
    recovered = control.inspect_run(instance, str(run["id"]), state_home=home)
    assert (
        recovered["status"] == status
        and recovered["completions"][0]["classification"] == classification
    )
    assert len(recovered["transitions"]) == 1
    # Historical reload uses captured inputs, not the executable or environment.
    for name in ("fake-claude", "fake-claude.json", "claude.json", "task.txt"):
        (workflow.parent / name).unlink()
    with monkeypatch.context() as patch:
        patch.setattr(
            subprocess, "Popen", lambda *a, **k: pytest.fail("reload spawned process")
        )
        cold = control.inspect_run(instance, str(run["id"]), state_home=home)
        for key in ("completions", "invocations", "transitions"):
            assert cold[key] == recovered[key]
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT id,body FROM invocations").fetchone()
        body = json.loads(row[1])
        body["adapter_evidence"]["semantic_sha256"] = "0" * 64
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(body), row[0])
        )
        before = list(db.iterdump())
    with pytest.raises(RouterError):
        control.inspect_run(instance, str(run["id"]), state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


@pytest.mark.parametrize(
    "mutation", ["argv", "settings", "evidence", "launch_version", "observed_version"]
)
def test_producing_launch_authority_refuses(
    single_flow: tuple[Path, Path, Path], mutation: str
) -> None:
    workflow, project, home = flow(single_flow)
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    with sqlite3.connect(store_path(instance, home)) as db:
        row = db.execute("SELECT id,body FROM invocations").fetchone()
        body = json.loads(row[1])
        if mutation == "argv":
            body["launch"]["argv"].append("--model")
        if mutation == "settings":
            body["launch"]["settings"]["effective"]["model"] = "forged"
        if mutation == "evidence":
            body["adapter_evidence"]["invocation_id"] = "copied"
        if mutation == "launch_version":
            body["launch"]["details"]["expected_version"] = "2.1.305"
        if mutation == "observed_version":
            body["adapter_evidence"]["observed_version"] = "2.1.305"
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(body), row[0])
        )
    with pytest.raises(RouterError):
        control.inspect_run(instance, str(run["id"]), state_home=home)


@pytest.mark.parametrize(
    "mode,version",
    [
        ("cancel", "2.1.296"),
        ("cancel", "2.1.305"),
        ("legacy_cancel", "2.1.296"),
        ("legacy_cancel", "2.1.305"),
        ("locked", "2.1.296"),
        ("live", "2.1.296"),
    ],
)
def test_controls_recovery_and_revision_authority(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    version: str,
) -> None:
    if mode not in {"cancel", "legacy_cancel"}:
        _revision_case(single_flow, monkeypatch, mode)
        return
    workflow, project, home = flow(
        single_flow,
        before="time.sleep(10)",
        claude_version=version,
        version=f"{version} (Claude Code)\n".encode(),
    )
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    ids = {}
    cancel_id = new_id()
    original = ClaudePrintAdapter.invoke

    def instrument(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
        callback = kwargs["on_started"]

        def started(pid: int) -> None:
            callback(pid)
            assert (
                control.cancel_run(
                    instance, str(ids["run_id"]), state_home=home, request_id=cancel_id
                )["status"]
                == "queued"
            )

        result = original(self, *args, **{**kwargs, "on_started": started})
        if mode == "legacy_cancel":
            # Preserve the pre-amendment result shape as a historical fixture.
            evidence = dict(result.adapter_evidence or {})
            del evidence["cleanup"]
            return replace(result, writer_state="may_live", adapter_evidence=evidence)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ClaudePrintAdapter, "invoke", instrument)
        cancelled = start_run(instance, state_home=home, on_started=ids.update)
    assert cancelled["completions"][0]["transport"] == "cancelled"
    current = control.inspect_run(instance, str(ids["run_id"]), state_home=home)
    target = current["invocations"][0]
    if mode == "legacy_cancel":
        assert target["writer_state"] == "may_live"
        assert "cleanup" not in target["adapter_evidence"]
        receipt = control.reconcile_invocation(
            instance,
            str(current["id"]),
            invocation_id=target["id"],
            stopped=True,
            reason="owned fake root/group stopped",
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
            plane_id="main",
        )
        assert receipt["status"] == "applied"
    else:
        assert target["writer_state"] == "stopped"
        assert target["adapter_evidence"]["cleanup"]["return_reason"] == "confirmed"
    current = control.inspect_run(instance, str(current["id"]), state_home=home)
    assert current["status"] == "cancelled"
    assert current["quiescent"] and not current["inflight_invocations"]
    assert current["completions"][0]["writer"] == (
        "may_live" if mode == "legacy_cancel" else "stopped"
    )
    assert (
        control.cancel_run(
            instance, str(current["id"]), state_home=home, request_id=cancel_id
        )["status"]
        == "applied"
    )
    fake(workflow.parent, version=f"{version} (Claude Code)\n".encode())
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded" and fresh["id"] != current["id"]
    assert (
        control.inspect_run(instance, str(current["id"]), state_home=home)[
            "completions"
        ]
        == current["completions"]
    )
    for name in ("fake-claude", "fake-claude.json", "claude.json", "task.txt"):
        (workflow.parent / name).unlink()
    with monkeypatch.context() as patch:
        patch.setattr(
            subprocess, "Popen", lambda *a, **kw: pytest.fail("reload spawned process")
        )
        cold = control.inspect_run(instance, str(current["id"]), state_home=home)
        for key in ("completions", "invocations", "transitions"):
            assert cold[key] == current[key]
    if mode == "cancel":
        path = store_path(instance, home)
        with sqlite3.connect(path) as db:
            row = db.execute(
                "SELECT body FROM invocations WHERE id=?", (target["id"],)
            ).fetchone()
            body = json.loads(row[0])
            body["adapter_evidence"]["cleanup"]["root_pid"] += 1
            db.execute(
                "UPDATE invocations SET body=? WHERE id=?",
                (json.dumps(body), target["id"]),
            )
            before = list(db.iterdump())
        with pytest.raises(RouterError, match="completion_integrity"):
            control.inspect_run(instance, str(current["id"]), state_home=home)
        with sqlite3.connect(path) as db:
            assert list(db.iterdump()) == before


def _revision_case(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    workflow, project, home = flow(single_flow)
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    original = ClaudePrintAdapter.invoke

    def edit_after(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
        result = original(self, *args, **kwargs)
        graph = json.loads(workflow.read_text())
        node = graph["planes"]["main"]["nodes"]["draft"]
        node["invoke"] = {
            "binding": "python",
            "config": {"script": "scripts/draft.py", "args": ["good"]},
            "timeout_seconds": 3,
        }
        graph["planes"]["main"]["nodes"]["consumer"] = copy.deepcopy(node)
        node["on"] = {"READY": {"goto": "consumer"}}
        graph["planes"]["main"]["nodes"]["consumer"]["on"] = {
            "READY": {"finish": "succeeded"}
        }
        workflow.write_text(json.dumps(graph))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ClaudePrintAdapter, "invoke", edit_after)
        result = start_run(instance, state_home=home, edit_mode=mode)
    assert result["invocations"][0]["launch_settings"]["adapter"]["effective"] == {}
    assert result["completions"][0]["transport"] == "completed"
    if mode == "locked":
        assert result["pause_reason"] == "locked_edit" and result["transitions"] == []
        accepted = control.accept_revision(
            instance,
            str(result["id"]),
            candidate_id=result["pending_revision"]["candidate_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
        )
        assert accepted["status"] == "applied"
    final = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert final["status"] == "succeeded" and final["count"] == 2
    assert final["invocations"][0]["revision_id"] != final["revision_id"]
    assert final["invocations"][1]["launch"]["adapter_id"] == "command"
    assert final["transitions"][0]["revision_id"] == final["revision_id"]


BRIDGE = {
    "context_path": "state/native-context.json",
    "outbox_path": "state/native-diagnostics",
}
CONTEXT_ID = "00000000-0000-4000-8000-0000000000bb"


def _bridged_flow(
    single: tuple[Path, Path, Path], *, context: bool = True, **options: Any
) -> tuple[Path, Path, Path]:
    workflow, project, home = flow(single, **options)
    data = json.loads(workflow.read_text())
    node_config = data["planes"]["main"]["nodes"]["draft"]["invoke"]["config"]
    node_config["diagnostic_bridge"] = BRIDGE
    workflow.write_text(json.dumps(data))
    if context:
        path = project / BRIDGE["context_path"]
        path.parent.mkdir(parents=True)
        value = {
            "format": "millrace-native-context/v1",
            "context_id": CONTEXT_ID,
            "input_sha256": "c" * 64,
        }
        path.write_text(json.dumps(value))
    return workflow, project, home


def _invocation_body(instance: str, home: Path) -> dict[str, Any]:
    with sqlite3.connect(store_path(instance, home)) as db:
        raw = db.execute("SELECT body FROM invocations").fetchone()[0]
    return dict(json.loads(raw))


@pytest.mark.parametrize("version", ["2.1.296", "2.1.305"])
@pytest.mark.parametrize("case", ["success", "provider_status"])
def test_bridge_v2_store_roundtrip_reload_and_corruption(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    case: str,
) -> None:
    value = (
        envelope()
        if case == "success"
        else envelope(is_error=True, api_error_status=429, structured_output=None)
    )
    workflow, project, home = _bridged_flow(
        single_flow,
        claude_version=version,
        version=f"{version} (Claude Code)\n".encode(),
        wire=json.dumps(value).encode(),
    )
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    run = start_run(instance, state_home=home)
    assert run["status"] == ("succeeded" if case == "success" else "failed")
    current = control.inspect_run(instance, str(run["id"]), state_home=home)
    invocation = current["invocations"][0]
    details = invocation["launch"]["details"]
    assert details["format"] == "millrace-claude-print-launch/v2"
    assert (details["plane_id"], details["node_id"]) == ("main", "draft")
    assert details["producing_revision"] == invocation["launch"]["producing_revision"]
    assert details["diagnostic_bridge"] == BRIDGE
    evidence = invocation["adapter_evidence"]
    status = None if case == "success" else 429
    assert evidence["format"] == "millrace-claude-print-result/v2"
    assert evidence["native_diagnostic"] == {
        "protocol": "claude-print-json/v1",
        "api_error_status": status,
    }
    # A later consumer can verify the actual sidecar from public reads alone.
    ref = nd.DiagnosticBridgeRef(**evidence["diagnostic_bridge"])
    raw = (project / ref.path).read_bytes()
    bridge = nd.BridgeConfig.load(BRIDGE)
    record = nd.verify_record(
        raw,
        ref,
        bridge,
        {
            "context_id": CONTEXT_ID,
            "input_sha256": "c" * 64,
            "invocation_id": invocation["id"],
            "launch_sha256": hashlib.sha256(nd.canonical(details)).hexdigest(),
            "failure_code": evidence["failure_code"],
            "native_diagnostic": evidence["native_diagnostic"],
        },
    )
    assert record.producing_revision == invocation["launch"]["producing_revision"]
    # A well-formed but forged status is caught only against the actual sidecar.
    forged = {**evidence["native_diagnostic"], "api_error_status": 503}
    with pytest.raises(RouterError, match="native_diagnostic_mismatch"):
        nd.verify_record(raw, ref, bridge, {"native_diagnostic": forged})
    # Cold reload is pure over retained evidence: no sidecar, context or process.
    shutil.rmtree(project / "state")
    with monkeypatch.context() as patch:
        patch.setattr(
            subprocess, "Popen", lambda *a, **k: pytest.fail("reload spawned process")
        )
        cold = control.inspect_run(instance, str(run["id"]), state_home=home)
    for key in ("completions", "invocations", "transitions"):
        assert cold[key] == current[key]
    path = store_path(instance, home)
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT id,body FROM invocations").fetchone()
        body = json.loads(row[1])
        # A status on a success, or a bool status, fails pure validation.
        body["adapter_evidence"]["native_diagnostic"]["api_error_status"] = (
            429 if case == "success" else True
        )
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(body), row[0])
        )
        before = list(db.iterdump())
    with pytest.raises(RouterError):
        control.inspect_run(instance, str(run["id"]), state_home=home)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


def test_bridge_missing_context_is_ordinary_stopped_failure(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, project, home = _bridged_flow(single_flow, context=False)
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    native: list[object] = []

    def forbidden(*args: object, **kwargs: object) -> None:
        native.append(args)
        raise AssertionError("native process started")

    monkeypatch.setattr(claude_adapter, "_process", forbidden)
    run = start_run(instance, state_home=home)
    assert native == []
    # Ordinary error routing, not adapter_exception/unknown uncertainty.
    assert run["status"] == "failed"
    with sqlite3.connect(store_path(instance, home)) as db:
        assert db.execute(
            "SELECT transport,writer,diagnostic,exit_code FROM completions"
        ).fetchone() == (
            "execution_failure",
            "stopped",
            "claude_bridge_context_missing",
            None,
        )
    evidence = _invocation_body(instance, home)["adapter_evidence"]
    assert evidence["phase"] == "preflight" and evidence["probe_pid"] is None
    assert evidence["native_diagnostic"] is None
    assert evidence["diagnostic_bridge"] is None
    assert not (project / BRIDGE["outbox_path"]).exists()
    first = control.inspect_run(instance, str(run["id"]), state_home=home)
    again = control.inspect_run(instance, str(run["id"]), state_home=home)
    assert again["completions"] == first["completions"]
    assert again["invocations"] == first["invocations"]


def test_bridge_disabled_store_capture_stays_v1(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home = flow(single_flow)
    instance = create_instance(workflow, project, state_home=home)["instance_id"]
    run = start_run(instance, state_home=home)
    assert run["status"] == "succeeded"
    body = _invocation_body(instance, home)
    assert body["launch"]["details"]["format"] == "millrace-claude-print-launch/v1"
    assert set(body["launch"]["details"]) == claude_adapter.LAUNCH_FIELDS
    assert body["adapter_evidence"]["format"] == "millrace-claude-print-result/v1"
    assert set(body["adapter_evidence"]) == claude_adapter.EVIDENCE_FIELDS
    assert not (project / "state").exists()
