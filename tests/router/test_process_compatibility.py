"""Live process gates yield ordinary stopped preflight failures, never holds."""

from __future__ import annotations

import subprocess
import time
from types import SimpleNamespace

import pytest
from millrace.router.execution import CompletionFacts
from opencode_support import request as opencode_request
from test_codex_configuration import request as codex_request

from millrace_plus import router
from millrace_plus.router.codex import CodexExecAdapter
from millrace_plus.router.opencode import OpenCodeRunAdapter


@pytest.mark.parametrize("case", [
    "minimum", "older", "missing", "wrong", "post", "local", "later_dev", "dev",
    "none", "metadata_error", "import_error",
])
def test_public_capability_and_minimum_core_gate(tmp_path, monkeypatch, case) -> None:
    original = router.importlib.import_module

    def version(_):
        if case == "metadata_error":
            raise KeyError("Version")
        return {
            "older": "0.23.1", "post": "0.23.2.post1", "local": "0.23.2+fixture",
            "later_dev": "0.23.3.dev1", "dev": "0.23.2.dev1", "none": None,
        }.get(case, "0.23.2")

    monkeypatch.setattr(router.metadata, "version", version)

    def load(name):
        if name != "millrace.router.process":
            return original(name)
        if case == "missing":
            raise ImportError("test-owned absent process module")
        if case == "import_error":
            raise RuntimeError("test-owned broken process import")
        if case == "wrong":
            return SimpleNamespace(OWNED_PROCESS_CONTRACT="unsupported")
        return original(name)

    monkeypatch.setattr(router.importlib, "import_module", load)
    assert router.require_core() == "millrace-router-execution/v2"
    # Both unmigrated modules remain admitted through execution-v2.
    from millrace_plus.router.claude import ClaudePrintAdapter
    from millrace_plus.router.pi import PiJsonAdapter

    assert ClaudePrintAdapter.adapter_id == "claude_print"
    assert PiJsonAdapter.adapter_id == "pi_json"
    if case in {"minimum", "post", "local", "later_dev"}:
        assert router.require_owned_process() == "owned-process-v1"
        return
    code = {
        "older": "core_process_version",
        "missing": "core_process_missing",
        "wrong": "core_process_mismatch",
        "dev": "core_process_version", "none": "core_process_version",
        "metadata_error": "core_process_version",
        "import_error": "core_process_missing",
    }[case]
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("gate spawned a probe")
    )
    for adapter, req in (
        (CodexExecAdapter(), codex_request(tmp_path)),
        (OpenCodeRunAdapter(), opencode_request(tmp_path)),
    ):
        plan = adapter.plan(req)  # Pure planning does not query process capability.
        result = adapter.invoke(
            plan, cwd=tmp_path, env={}, deadline=time.time() + 2,
            poll=lambda: False, on_started=lambda _: pytest.fail("gate started child"),
        )
        assert (result.classification, result.writer_state, result.exit_code) == (
            "execution_failure", "stopped", None,
        )
        assert result.stdout == b""
        evidence = result.adapter_evidence
        assert evidence["phase"] == "preflight" and evidence["probe_pid"] is None
        assert evidence["failure_code"] == adapter.adapter_id.split("_")[0] + "_" + code
        adapter.validate_completion(plan, CompletionFacts(
            plan.invocation_id, None, result.stdout, result.stderr,
            result.classification, result.writer_state, result.diagnostic,
            result.exit_code, evidence, "adapter",
        ))


@pytest.mark.parametrize("native", ["codex", "opencode"])
def test_capability_refusal_is_counted_stopped_without_writer_hold(
    single_flow, monkeypatch, native
) -> None:
    from codex_support import codex_flow, codex_instance
    from opencode_support import flow
    from plus_support import create_instance, start_run

    from millrace_plus.router import control

    if native == "codex":
        workflow, project, home, native_home = codex_flow(single_flow)
        instance = codex_instance(workflow, project, home, native_home)
    else:
        workflow, project, home = flow(single_flow)
        made = create_instance(workflow, project, state_home=home)
        instance = str(made["instance_id"])
    monkeypatch.setattr(router.metadata, "version", lambda _: "0.23.1")
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("refusal launched native work")
    )
    result = start_run(instance, state_home=home)
    assert result["count"] == 1 and result["pause_reason"] != "writer_unknown"
    completion = result["completions"][0]
    assert completion["transport"] == "execution_failure"
    assert completion["writer"] == "stopped"
    assert result["invocations"][0]["pid"] is None
    assert result["invocations"][0]["writer_state"] == "stopped"
    assert completion["effective_writer"] == "stopped"
    assert result["writer_facts"] == []
    assert result["invocations"][0]["uncertainty_diagnostic"] is None
    # The old saved contract loads without a live helper-capability query.
    from millrace_plus.router import codex, opencode

    for consumer in (codex, opencode):
        monkeypatch.setattr(
            consumer, "require_owned_process",
            lambda: pytest.fail("reload capability query"),
        )
    reloaded = control.inspect_run(instance, str(result["id"]), state_home=home)
    assert reloaded["count"] == 1
