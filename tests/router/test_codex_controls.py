"""Adversarial Codex control, writer-hold and request-replay lifecycles.

Fresh fake subprocess groups supply cancellation/closing evidence. These tests
never invoke an installed harness or infer that a forced stop contains detached
writers. Core fault injection is confined to disposable test-owned state.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from codex_support import (
    ADAPTER_CONTRACT,
    ADAPTER_ID,
    FINAL,
    codex_flow,
    codex_instance,
    events,
    fake,
)
from millrace.router.execution import AdapterResult, RouterError
from millrace.router.store import Store
from plus_support import Crash, new_id, recover_run, start_run, store_path

from millrace_plus.router import control


def _inspect(instance: str, run: str, home: Path) -> dict[str, Any]:
    return control.inspect_run(instance, run, state_home=home)


def _durable(instance: str, home: Path, project: Path) -> tuple[Any, ...]:
    with sqlite3.connect(store_path(instance, home)) as db:
        rows = list(db.iterdump())
    owner = project / ".millrace-router-owner/owner.json"
    receipts = tuple(
        (str(p.relative_to(home)), p.read_bytes())
        for p in sorted((home / "orphan-receipts").glob("*.json"))
    )
    return rows, owner.read_bytes() if owner.exists() else None, receipts


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _owned_cleanup(pids: list[int]) -> None:
    for pid in pids:
        if _group_exists(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _finish_uncertain(instance: str, run: dict[str, Any], home: Path) -> dict[str, Any]:
    invocation = run["invocations"][0]
    receipt = control.reconcile_invocation(
        instance,
        str(run["id"]),
        invocation_id=str(invocation["id"]),
        reason="owned fake root/group reaped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=run["state_version"],
        plane_id="main",
    )
    assert receipt["status"] == "applied", receipt
    current = _inspect(instance, str(run["id"]), home)
    if current["status"] != "cancelled":
        receipt = control.resume_run(
            instance,
            str(run["id"]),
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
        )
        assert receipt["status"] == "applied", receipt
    return _inspect(instance, str(run["id"]), home)


@pytest.mark.parametrize(
    "case",
    [
        "cancel",
        "timeout",
        "closing_repeated_controls",
        "descendant_holds_stdout_until_forced_cleanup",
        "on_started_raises",
    ],
)
def test_cancel_timeout_closing_drain_and_reconciliation(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    workflow, project, home, native_home = codex_flow(single_flow)
    closing = project / "parent-exited"
    graph = json.loads(workflow.read_text())
    graph["limits"]["max_elapsed_seconds"] = 30
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = (
        1.0 if case == "timeout" else 15
    )
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    workflow.write_text(json.dumps(graph))
    if case in {
        "closing_repeated_controls",
        "descendant_holds_stdout_until_forced_cleanup",
    }:
        child = (
            "import os,signal,time;from pathlib import Path;"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);parent=os.getppid();"
            "\nwhile os.getppid()==parent: time.sleep(0.002)\n"
            f"Path({str(closing)!r}).write_text('parent exited')\n"
            "time.sleep(30)\n"
        )
        fake(
            workflow.parent,
            after=f"subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(0.03)",
        )
    else:
        fake(workflow.parent, before="time.sleep(30)")
    instance = codex_instance(workflow, project, home, native_home)
    adapter = control.resolve_adapter(ADAPTER_ID, ADAPTER_CONTRACT)
    original = type(adapter).invoke
    ids: dict[str, object] = {}
    pids: list[int] = []
    queued: list[dict[str, Any]] = []
    request_ids = [new_id(), new_id()]

    def instrument(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
        original_started = kwargs["on_started"]
        original_poll = kwargs["poll"]

        def started(pid: int) -> None:
            pids.append(pid)
            original_started(pid)
            if case == "on_started_raises":
                raise RuntimeError(
                    "fixture acknowledgement failure after active commit"
                )
            if case in {"cancel", "closing_repeated_controls"}:
                queued.append(
                    control.pause_run(
                        instance,
                        str(ids["run_id"]),
                        state_home=home,
                        request_id=request_ids[0],
                    )
                )
            if case == "cancel":
                queued.append(
                    control.cancel_run(
                        instance,
                        str(ids["run_id"]),
                        state_home=home,
                        request_id=request_ids[1],
                    )
                )

        def poll() -> bool:
            if (
                case == "closing_repeated_controls"
                and len(queued) == 1
                and closing.exists()
            ):
                queued.append(
                    control.cancel_run(
                        instance,
                        str(ids["run_id"]),
                        state_home=home,
                        request_id=request_ids[1],
                    )
                )
            return bool(original_poll())

        return original(self, *args, **dict(kwargs, on_started=started, poll=poll))

    started_at = time.monotonic()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(type(adapter), "invoke", instrument)
            paused = start_run(instance, state_home=home, on_started=ids.update)
        assert time.monotonic() - started_at < 8
        assert paused["status"] == "paused" and paused["count"] == 1
        assert paused["transitions"] == []
        assert (project / ".millrace-router-owner/owner.json").exists()
        invocation = paused["invocations"][0]
        assert invocation["pid"] in pids
        assert all(not _group_exists(pid) for pid in pids)
        if case == "on_started_raises":
            assert paused["pause_reason"] == "writer_unknown"
            assert paused["completions"] == []
            assert invocation["uncertainty_diagnostic"] == "adapter_exception"
        else:
            completion = paused["completions"][0]
            assert completion["stdout"] == ""
            assert completion["transport"] != "completed"
            assert completion["writer"] in {"may_live", "unknown"}
            if case == "timeout":
                assert completion["transport"] == "timeout"
            elif case in {"cancel", "closing_repeated_controls"}:
                assert completion["transport"] == "cancelled"
                assert len(queued) == 2
                assert all(row["status"] == "queued" for row in queued)
                actual = _inspect(instance, str(paused["id"]), home)
                assert [
                    row["status"]
                    for row in actual["controls"]
                    if row["request_id"] in request_ids
                ] == ["applied", "applied"]
        denied = control.resume_run(
            instance,
            str(paused["id"]),
            state_home=home,
            request_id=new_id(),
            expected_state_version=paused["state_version"],
        )
        assert denied["status"] == "refused"
        if case in {"cancel", "closing_repeated_controls"}:
            assert denied["code"] == "run_cancelled"
            assert paused["cancel_requested"] is True
            assert paused["run_gate"]["reason"] == "writer_unknown"
            assert paused["completions"][0]["writer"] == "may_live"
            assert paused["completions"][0]["effective_writer"] == "may_live"
            assert invocation["writer_state"] == "may_live"
        else:
            assert denied["code"] == "writer_unknown"
        current = _inspect(instance, str(paused["id"]), home)
        assert current["count"] == 1 and current["transitions"] == []
        if case in {"cancel", "closing_repeated_controls"}:
            assert current["cancel_requested"] is True
            assert current["completions"] == paused["completions"]
            assert current["invocations"][0]["writer_state"] == "may_live"
            assert (project / ".millrace-router-owner/owner.json").exists()
        final = _finish_uncertain(instance, current, home)
        assert final["status"] in {"failed", "cancelled"} and final["count"] == 1
        if case in {"cancel", "closing_repeated_controls"}:
            assert final["status"] == "cancelled"
            assert final["completions"][0]["writer"] == "may_live"
            assert final["completions"][0]["effective_writer"] == "stopped"
            assert final["invocations"][0]["writer_state"] == "stopped"
        assert len(final["transitions"]) <= 1
        assert not (project / ".millrace-router-owner/owner.json").exists()
        fake(workflow.parent)
        fresh = start_run(instance, state_home=home)
        assert fresh["status"] == "succeeded" and fresh["count"] == 1
        assert (
            _inspect(instance, str(final["id"]), home)["completions"]
            == final["completions"]
        )
    finally:
        _owned_cleanup(pids)


@pytest.mark.parametrize("case", ["natural_exit", "later_cancel", "later_timeout"])
def test_failed_turn_natural_exit_and_later_stop(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Native failure gets its own exit; a later stop still owns cleanup."""
    import millrace_plus.router.codex as codex

    failed_events = events()[:2] + [
        {"type": "error", "message": "native request failed"},
        {"type": "turn.failed", "error": {"message": "native request failed"}},
    ]
    after = (
        "time.sleep(0.05);os.write(2,b'native failure tail\\n')"
        if case == "natural_exit"
        else "time.sleep(30)"
    )
    workflow, project, home, native_home = codex_flow(
        single_flow, events=failed_events, final=None, after=after, exit_code=1
    )
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = (
        1 if case == "later_timeout" else 5
    )
    graph["limits"]["max_elapsed_seconds"] = 30
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native_home)
    ids: dict[str, object] = {}
    failed_at: list[float] = []
    queued: list[dict[str, Any]] = []
    original_event = codex.Stream.event
    original_invoke = codex.CodexExecAdapter.invoke

    def observe_failure(stream: Any, event: dict[str, object]) -> None:
        original_event(stream, event)
        if event.get("type") == "turn.failed":
            failed_at.append(time.monotonic())

    def invoke(self: Any, *args: Any, **kwargs: Any) -> AdapterResult:
        original_poll = kwargs["poll"]

        def poll() -> bool:
            if (
                case == "later_cancel"
                and failed_at
                and not queued
                and time.monotonic() - failed_at[0] >= 0.2
            ):
                queued.append(
                    control.cancel_run(
                        instance,
                        str(ids["run_id"]),
                        state_home=home,
                        request_id=new_id(),
                    )
                )
            return bool(original_poll())

        return original_invoke(self, *args, **dict(kwargs, poll=poll))

    with monkeypatch.context() as patch:
        patch.setattr(codex.Stream, "event", observe_failure)
        patch.setattr(codex.CodexExecAdapter, "invoke", invoke)
        result = start_run(instance, state_home=home, on_started=ids.update)
    assert failed_at
    assert result["count"] == 1 and len(result["completions"]) == 1
    completion = result["completions"][0]
    assert completion["transport"] == "execution_failure"
    assert completion["diagnostic"] == "codex_turn_failed"
    assert completion["stdout"] == ""
    assert (
        result["invocations"][0]["adapter_evidence"]["failure_code"]
        == "codex_turn_failed"
    )
    # Both validated native messages lead stderr; native text follows its header.
    records = (
        "[codex-jsonl error.message] native request failed\n"
        "[codex-jsonl turn.failed.error.message] native request failed\n"
    )
    assert completion["stderr"] == records + (
        "[codex-stderr native]\nnative failure tail\n" if case == "natural_exit" else ""
    )
    assert result["invocations"][0]["adapter_evidence"]["stderr_truncated"] is False
    if case == "natural_exit":
        assert (completion["writer"], completion["exit_code"]) == ("stopped", 1)
        assert "native failure tail" in completion["stderr"]
        assert result["status"] == "failed" and len(result["transitions"]) == 1
        assert result["writer_facts"] == []
        assert not (project / ".millrace-router-owner/owner.json").exists()
        assert (
            _inspect(instance, str(result["id"]), home)["completions"]
            == result["completions"]
        )
    else:
        assert result["status"] == "paused" and result["transitions"] == []
        assert completion["writer"] == "may_live"
        if case == "later_cancel":
            assert len(queued) == 1 and queued[0]["status"] == "queued"
        else:
            assert time.monotonic() - failed_at[0] >= 0.5
        result = _finish_uncertain(instance, result, home)
        assert result["status"] == ("cancelled" if case == "later_cancel" else "failed")
        assert result["count"] == 1
    fake(workflow.parent)
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded" and fresh["count"] == 1


def test_failure_diagnostics_persist_reload_replay_then_fresh_success(
    single_flow: tuple[Path, Path, Path],
) -> None:
    """Redacted causes are durable, replay-stable and absent from later success."""
    message = (
        "Synthetic route unavailable; bearer synthetic-bearer-4K8X; "
        "https://example.invalid/e?key=synthetic-query-4K8X; NUL\0"
    )
    failed_events = events()[:2] + [
        {"type": "error", "message": "earlier: " + message},
        {"type": "turn.failed", "error": {"message": message}},
    ]
    workflow, project, home, native_home = codex_flow(
        single_flow, events=failed_events, final=None, exit_code=1
    )
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native_home)
    redacted = (
        "Synthetic route unavailable; Bearer [REDACTED]; "
        "https://example.invalid/e?key=[REDACTED]; NUL\\x00"
    )
    expected = (
        f"[codex-jsonl error.message] earlier: {redacted}\n"
        f"[codex-jsonl turn.failed.error.message] {redacted}\n"
    )
    request = {
        "state_home": home,
        "request_id": new_id(),
        "expected_state_version": control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    }
    receipt = control.start_run(instance, **request)
    run_id = str(receipt["run_id"])
    failed = _inspect(instance, run_id, home)
    assert failed["status"] == "failed" and failed["count"] == 1
    completion = failed["completions"][0]
    assert (completion["transport"], completion["diagnostic"]) == (
        "execution_failure",
        "codex_turn_failed",
    )
    assert completion["stdout"] == "" and completion["stderr"] == expected
    assert "synthetic-bearer" not in completion["stderr"]
    assert "synthetic-query" not in completion["stderr"]
    durable = _durable(instance, home, project)
    assert control.start_run(instance, **request) == receipt
    assert _durable(instance, home, project) == durable
    assert _inspect(instance, run_id, home) == failed
    retry = events()
    retry.insert(2, {"type": "error", "message": "retry " + message})
    fake(workflow.parent, events=retry)
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded" and fresh["count"] == 1
    assert fresh["completions"][0]["stderr"] == ""
    later = _inspect(instance, run_id, home)
    assert {k: later[k] for k in ("status", "invocations", "completions")} == {
        k: failed[k] for k in ("status", "invocations", "completions")
    }


def test_completed_uncertain_writer_routes_only_after_resume(
    single_flow: tuple[Path, Path, Path],
) -> None:
    workflow, project, home, native_home = codex_flow(single_flow)
    graph = json.loads(workflow.read_text())
    graph["limits"]["max_elapsed_seconds"] = 30
    graph["planes"]["main"]["nodes"]["draft"]["invoke"]["timeout_seconds"] = 15
    workflow.write_text(json.dumps(graph))
    child = (
        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
    )
    fake(
        workflow.parent,
        after=(
            f"subprocess.Popen([sys.executable,'-c',{child!r}],stdin=subprocess.DEVNULL,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(0.03)"
        ),
    )
    instance = codex_instance(workflow, project, home, native_home)
    paused = start_run(instance, state_home=home)
    invocation = paused["invocations"][0]
    try:
        assert paused["pause_reason"] == "writer_unknown" and paused["count"] == 1
        assert paused["transitions"] == []
        assert paused["completions"][0]["transport"] == "completed"
        assert paused["completions"][0]["stdout"] == FINAL.decode()
        assert paused["completions"][0]["writer"] == "may_live"
        original_completion = {
            k: v for k, v in paused["completions"][0].items() if k != "effective_writer"
        }
        hold_path = project / ".millrace-router-owner/owner.json"
        hold = json.loads(hold_path.read_text())
        from millrace.router import control as core

        durable = _durable(instance, home, project)
        with pytest.raises(RouterError, match="unsupported_adapter_contract"):
            core.release_project_hold(
                project_root=project,
                hold_id=hold["hold_id"],
                owner_instance_id=instance,
                owner_run_id=str(paused["id"]),
                reason="missing resolver",
                stopped=True,
                state_home=home,
                request_id=new_id(),
            )
        assert _durable(instance, home, project) == durable
        blocked = control.start_run(
            instance,
            state_home=home,
            request_id=new_id(),
            expected_state_version=control.inspect_instance(instance, state_home=home)[
                "state_version"
            ],
        )
        assert (blocked["status"], blocked["code"]) == ("refused", "run_nonterminal")
        released = control.release_project_hold(
            project_root=project,
            hold_id=hold["hold_id"],
            owner_instance_id=instance,
            owner_run_id=str(paused["id"]),
            reason="owner is nonterminal",
            stopped=True,
            state_home=home,
            request_id=new_id(),
        )
        assert (released["status"], released["code"]) == (
            "refused",
            "owner_run_nonterminal",
        )
        replay = json.loads(
            (
                home / "orphan-receipts" / (str(released["request_id"]) + ".json")
            ).read_text()
        )["request"]
        durable = _durable(instance, home, project)
        with pytest.raises(RouterError, match="unsupported_adapter_contract"):
            core.execute_request(replay, state_home=home)
        assert _durable(instance, home, project) == durable
        assert control.execute_request(replay, state_home=home) == released
        current = _inspect(instance, str(paused["id"]), home)
        reconciled = control.reconcile_invocation(
            instance,
            str(paused["id"]),
            invocation_id=str(invocation["id"]),
            reason="owned fake root/group reaped",
            stopped=True,
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
            plane_id="main",
        )
        assert reconciled["status"] == "applied", reconciled
        current = _inspect(instance, str(paused["id"]), home)
        assert current["transitions"] == [] and current["count"] == 1
        assert current["status"] == "paused" and len(current["writer_facts"]) == 1
        assert {
            k: v
            for k, v in current["completions"][0].items()
            if k != "effective_writer"
        } == original_completion
        resumed = control.resume_run(
            instance,
            str(paused["id"]),
            state_home=home,
            request_id=new_id(),
            expected_state_version=current["state_version"],
        )
        assert resumed["status"] == "applied", resumed
        after = _inspect(instance, str(paused["id"]), home)
        assert after["status"] == "succeeded" and after["count"] == 1
        assert len(after["transitions"]) == 1
        assert {
            k: v for k, v in after["completions"][0].items() if k != "effective_writer"
        } == original_completion
        assert not hold_path.exists()
        assert _inspect(instance, str(paused["id"]), home) == after
    finally:
        _owned_cleanup([int(invocation["pid"])])


@pytest.mark.parametrize(
    "crash_at", ["reserved", "active", "raw_completion", "queued_drain"]
)
def test_facade_replay_recovery_and_no_native_resume(
    single_flow: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, crash_at: str
) -> None:
    from millrace.router import control as core
    from millrace.router.controller import Controller

    workflow, project, home, native_home = codex_flow(single_flow)
    marker = project / "main-launches"
    fake(
        workflow.parent,
        before=(
            f"counter=Path({str(marker)!r});"
            "counter.write_text(str(int(counter.read_text())+1 "
            "if counter.exists() else 1))"
        ),
    )
    graph = json.loads(workflow.read_text())
    graph["planes"]["main"]["nodes"]["draft"]["on_error"] = {"finish": "failed"}
    workflow.write_text(json.dumps(graph))
    instance = codex_instance(workflow, project, home, native_home)
    ids: dict[str, object] = {}
    queued_request = {
        "format": "millrace-router-request/v2",
        "operation": "cancel_run",
        "instance_id": instance,
        "run_id": None,
        "request_id": new_id(),
        "expected_state_version": None,
        "payload": {},
        "plane_id": None,
    }
    with monkeypatch.context() as patch:
        if crash_at == "queued_drain":
            original_poll = Controller.poll
            fired: list[bool] = []

            def crash_closing(holder: Controller, active: bool) -> bool:
                terminal = holder.store.db.execute(
                    "SELECT body FROM runs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if (
                    not active
                    and terminal is not None
                    and json.loads(terminal[0])["status"] == "succeeded"
                    and not fired
                ):
                    fired.append(True)
                    queued_request["run_id"] = str(ids["run_id"])
                    queued = control.execute_request(
                        dict(queued_request), state_home=home
                    )
                    assert queued["status"] == "queued", queued
                    raise Crash()
                return original_poll(holder, active)

            patch.setattr(Controller, "poll", crash_closing)
        else:
            method = {
                "reserved": "reserve",
                "active": "active",
                "raw_completion": "complete",
            }[crash_at]
            original = getattr(Store, method)

            def crash(store: Store, *args: Any, **kwargs: Any) -> Any:
                result = original(store, *args, **kwargs)
                raise Crash(result)

            patch.setattr(Store, method, crash)
        with pytest.raises(Crash):
            start_run(instance, state_home=home, on_started=ids.update)
    run_id = str(ids["run_id"])
    launches = int(marker.read_text()) if marker.exists() else 0
    before = _inspect(instance, run_id, home)
    assert before["count"] == 1
    recovered = recover_run(instance, run_id, state_home=home)
    assert (int(marker.read_text()) if marker.exists() else 0) == launches
    assert recovered["count"] == 1
    if crash_at in {"reserved", "active"}:
        assert recovered["pause_reason"] == "writer_unknown"
        assert recovered["transitions"] == [] and recovered["completions"] == []
        recovered = _finish_uncertain(instance, recovered, home)
        assert recovered["status"] == "failed"
        assert recovered["invocations"][0]["origin"] == "core_interruption"
    else:
        assert recovered["status"] == "succeeded"
        assert len(recovered["transitions"]) == 1
    if crash_at == "queued_drain":
        receipt = control.execute_request(dict(queued_request), state_home=home)
        assert receipt["status"] == "applied"
        durable = _durable(instance, home, project)
        assert control.execute_request(dict(queued_request), state_home=home) == receipt
        assert _durable(instance, home, project) == durable
        assert (
            sum(
                row["request_id"] == queued_request["request_id"]
                for row in _inspect(instance, run_id, home)["controls"]
            )
            == 1
        )
        with pytest.raises(RouterError, match="request_id_reused"):
            control.execute_request(
                dict(queued_request, operation="pause_run"), state_home=home
            )
        assert _durable(instance, home, project) == durable
        with pytest.raises(RouterError, match="unsupported_adapter_contract"):
            core.execute_request(dict(queued_request), state_home=home)
        assert _durable(instance, home, project) == durable
    fresh = start_run(instance, state_home=home)
    assert fresh["status"] == "succeeded" and fresh["count"] == 1
    assert int(marker.read_text()) == launches + 1
    assert _inspect(instance, run_id, home)["count"] == 1
