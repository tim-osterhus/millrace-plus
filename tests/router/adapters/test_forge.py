"""Forge parent cases: planning, probe, lifecycle, disposition and validation.

Every case uses the inert ``forge_peer.py`` executable. None imports or runs
the Forge SDK, and none contacts a backend. Rule IDs refer to the accepted R0
worker contract plus ``lab/tasks/v0232-forge-r2b-source/2026-11-14/
contract-clarifications.md``.
"""

from __future__ import annotations

import base64
import builtins
import copy
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
from forge_support import (
    ACCEPTED,
    ENV,
    ENV_NAMES,
    INVOCATION,
    KEY,
    LOOPBACK,
    PROFILE,
    SEMANTIC,
    URL_ENV,
    completed,
    configuration,
    create,
    facts,
    failed,
    fake,
    flow_env,
    forge_flow,
    forge_resolver,
    inspect,
    invoke,
    new_id,
    prepared,
    probe_line,
    request,
    row,
    start,
)
from millforge_support import with_handoff
from millrace.router import control as core
from millrace.router.execution import RouterError, SeamInput

from millrace_plus.router import CoreContractError
from millrace_plus.router import forge as adapter
from millrace_plus.router import forge_configuration as c
from millrace_plus.router.resolver import resolve_adapter


def code_of(error) -> str:
    return error.value.code


def resealed(evidence: dict, **changes: object) -> dict:
    body = {k: v for k, v in copy.deepcopy(evidence).items() if k != "receipt_sha256"}
    body.update(changes)
    return c.seal(body)


def refuses(plan, result, *, pid="same", **changes) -> None:
    selected = result.adapter_evidence["pid"] if pid == "same" else pid
    with pytest.raises(RouterError) as error:
        c.validate_completion(plan, replace(facts(plan, result, selected), **changes))
    assert code_of(error) == "completion_integrity"


def fast(monkeypatch) -> None:
    """Shrink deadline reserves so process-timeout cases finish quickly."""
    monkeypatch.setattr(c, "PROBE_RESERVE", 1.0)
    monkeypatch.setattr(c, "MAIN_RESERVE", 0.5)
    monkeypatch.setattr(c, "CLEANUP_RESERVE", 0.5)


# ---------------------------------------------------------------- planning


def test_plan_is_pure_and_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv(URL_ENV, "http://secret-host.invalid/v1")

    def forbidden(*args, **kwargs):
        pytest.fail("planning performed environment or process access")

    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    req = request(tmp_path)
    plan = c.plan(req)
    assert plan == c.plan(req)
    root = str(req.invocation_root)
    assert plan.argv == (
        "/absent/python3.12",
        "-I",
        "-B",
        "-X",
        "utf8",
        root + "/forge-worker.py",
        root + "/forge-request.json",
    )
    assert plan.environment == {}
    assert set(plan.generated_controls) == {c.WORKER_NAME, c.PROBE_NAME, c.REQUEST_NAME}
    assert c.digest(plan.generated_controls[c.WORKER_NAME]) == c.WORKER_SHA256
    assert c.digest(plan.generated_controls[c.PROBE_NAME]) == c.PROBE_SHA256
    body = json.loads(plan.generated_controls[c.REQUEST_NAME])
    assert body["backend"] == {
        "kind": "openai_compat",
        "base_url_env": URL_ENV,
        "auth": {"kind": "env", "name": "FORGE_API_KEY"},
        "model": "fixture-model",
    }
    assert body["workspace"] == plan.details["cwd"] == str(tmp_path)
    assert body["budgets"] == {k: v[0] for k, v in c.BUDGETS.items()}
    everything = (
        c.encoded(
            {
                "details": plan.details,
                "settings": plan.settings,
                "argv": list(plan.argv),
            }
        )
        + plan.generated_controls[c.REQUEST_NAME]
    )
    for value in (b"secret-host", LOOPBACK.encode(), KEY.encode()):
        assert value not in everything
    assert plan.settings["provenance"] == {
        "model": {"source": "profile", "policy": None}
    }


def test_forge_v2_plan_and_exact_control_identities(tmp_path):
    v1 = resolve_adapter("forge_runner", "millrace-forge-runner/v1")
    v2 = resolve_adapter("forge_runner", "millrace-forge-runner/v2")
    req = request(tmp_path)
    req_v2 = replace(req, adapter_contract=v2.adapter_contract)
    v2.validate_configuration(configuration(req_v2))
    first = v1.plan(req)
    second = v2.plan(req_v2)
    assert second == v2.plan(req_v2)
    assert first.argv == second.argv and first.settings == second.settings
    assert second.details == {
        **first.details,
        "contract": v2.adapter_contract,
        "worker_sha256": c.WORKER_SHA256_V2,
    }
    worker, probe = c.packaged_scripts()
    assert first.generated_controls[c.WORKER_NAME] == worker
    assert first.generated_controls[c.PROBE_NAME] == probe
    assert first.details["worker_sha256"] == c.WORKER_SHA256 != c.WORKER_SHA256_V2
    assert c.digest(second.generated_controls[c.WORKER_NAME]) == c.WORKER_SHA256_V2
    for name in (c.PROBE_NAME, c.REQUEST_NAME):
        assert first.generated_controls[name] == second.generated_controls[name]
    for selected, wrong_request in ((v1, req_v2), (v2, req)):
        with pytest.raises(RouterError) as error:
            selected.plan(wrong_request)
        assert code_of(error) == "unsupported_adapter_contract"
        with pytest.raises(RouterError) as error:
            selected.validate_configuration(configuration(wrong_request))
        assert code_of(error) == "unsupported_adapter_contract"
    for original, wrong_worker in ((first, second), (second, first)):
        forged = replace(
            original,
            generated_controls={
                **original.generated_controls,
                c.WORKER_NAME: wrong_worker.generated_controls[c.WORKER_NAME],
            },
        )
        with pytest.raises(RouterError) as error:
            c.launch_details(forged, contract=original.adapter_contract)
        assert code_of(error) == "forge_control_digest_mismatch"


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"provider": "openai"},
        {"thinking": "high"},
        {"effort": "low"},
        {"tools": []},
        {"stream": True},
        {"rescue": True},
        {"reasoning_replay": "full"},
        {"argv": ["x"]},
        {"temperature": 0.1},
        {"system_prompt": "x"},
        {"format": "millrace-forge-runner-profile/v2"},
        {"forge_version": "0.10.0"},
        {"backend": "anthropic"},
        {"base_url": "http://127.0.0.1:9/v1"},
        {"base_url": {"kind": "literal", "value": "http://127.0.0.1:9/v1"}},
        {"base_url": {"kind": "env", "name": "OTHER_URL"}},
        {"base_url": {"kind": "env", "name": "PATH"}},
        {"base_url": {"kind": "env", "name": "MILLRACE_SEAM_INPUT"}},
        {"base_url": {"kind": "env", "name": "MILLRACE_URL"}},
        {"base_url": {"kind": "env", "name": "PYTHONHOME"}},
        {"base_url": {"kind": "env", "name": "1BAD"}},
        {"base_url": {"kind": "env", "name": "A-B"}},
        {"auth": {"kind": "env", "name": URL_ENV}},
        {"auth": {"kind": "env", "value": "sk-literal"}},
        {"auth": {"kind": "bearer", "token": "sk-literal"}},
        {"auth": {"kind": "none", "name": "X"}},
        {"model": ""},
        {"model": "-x"},
        {"model": " x"},
        {"model": "x\n"},
        {"budgets": {"unknown": 1}},
    ],
)
def test_profile_refusals(tmp_path, change):
    req = request(tmp_path, profile={**PROFILE, **change})
    with pytest.raises(RouterError):
        c.plan(req)
    with pytest.raises(RouterError):
        c.validate_configuration(configuration(req))


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b"\xff",
        b"[]",
        b'{"format":"a","format":"b"}',
        pytest.param(b"x" * (c.PROFILE_MAX + 1), id="profile-max-plus-one"),
    ],
)
def test_profile_strict_bytes(tmp_path, raw):
    with pytest.raises(RouterError):
        c.plan(request(tmp_path, profile=raw))


def test_profile_size_limit_boundary(tmp_path):
    body = json.dumps(PROFILE).encode()
    exact = body + b" " * (c.PROFILE_MAX - len(body))
    assert len(exact) == c.PROFILE_MAX
    c.plan(request(tmp_path, profile=exact))  # exactly PROFILE_MAX bytes: accepted
    with pytest.raises(RouterError) as error:
        c.plan(request(tmp_path, profile=exact + b" "))
    assert code_of(error) == "invalid_forge_profile"


def test_settings_namespace_and_model_required(tmp_path):
    bare = {k: v for k, v in PROFILE.items() if k != "model"}
    req = request(tmp_path, profile=bare)
    with pytest.raises(RouterError) as error:
        c.plan(req)
    assert code_of(error) == "forge_model_required"
    with pytest.raises(RouterError) as error:
        c.validate_configuration(configuration(req))
    assert code_of(error) == "forge_model_required"
    for layer, source in (
        ("runtime", "runtime"),
        ("workflow", "workflow"),
        ("plane", "plane"),
    ):
        plan = c.plan(replace(req, **{layer: {"model": layer}}))
        assert plan.settings["effective"] == {"model": layer}
        assert plan.settings["provenance"]["model"]["source"] == source
    plan = c.plan(
        replace(
            req,
            runtime={"model": "runtime"},
            workflow={"model": "wf"},
            plane={"model": "plane"},
            node_config={"prompt": "task.txt", "model": "node"},
        )
    )
    assert plan.settings["effective"] == {"model": "node"}
    assert plan.details["model"] == "node"
    assert (
        json.loads(plan.generated_controls[c.REQUEST_NAME])["backend"]["model"]
        == "node"
    )
    full = request(tmp_path)
    for layer in ("runtime", "workflow", "plane"):
        with pytest.raises(RouterError) as error:
            c.plan(replace(full, **{layer: {"thinking": "high"}}))
        assert code_of(error) == "unsupported_setting"
    with pytest.raises(RouterError) as error:
        c.plan(replace(full, node_config={"prompt": "task.txt", "thinking": "high"}))
    assert code_of(error) == "unsupported_setting"
    # A plane without any Forge node still has its Forge namespace validated.
    with pytest.raises(RouterError) as error:
        c.validate_configuration(
            configuration(
                full,
                planes={
                    "main": {},
                    "other": {"thinking": "high"},
                },
            )
        )
    assert code_of(error) == "unsupported_setting"
    # Other adapters' namespaces never reach Forge: Core passes only Forge's.
    c.validate_configuration(configuration(full, planes={"main": {}, "other": {}}))
    for bad in ("", "-m", " m", "m ", "m\x00", "m\r", "é" * 129):
        with pytest.raises(RouterError) as error:
            c.plan(replace(full, runtime={"model": bad}))
        assert code_of(error) == "invalid_forge_settings"


@pytest.mark.parametrize("key", sorted(c.BUDGETS))
def test_budget_bounds(tmp_path, key):
    _, low, high = c.BUDGETS[key]
    for bad in (low - 1, high + 1, True, float(low), str(low)):
        with pytest.raises(RouterError) as error:
            c.plan(request(tmp_path, profile={**PROFILE, "budgets": {key: bad}}))
        assert code_of(error) == "invalid_forge_budget"
    for good in (low, high):
        plan = c.plan(request(tmp_path, profile={**PROFILE, "budgets": {key: good}}))
        assert (
            json.loads(plan.generated_controls[c.REQUEST_NAME])["budgets"][key] == good
        )
        assert plan.details["budgets"][key] == good


@pytest.mark.parametrize("value", [None, [], [1], 16, "16", True])
def test_budget_object_shape(tmp_path, value):
    """N1: a non-object budgets value refuses with the budget code before spawn."""
    req = request(tmp_path, profile={**PROFILE, "budgets": value})
    with pytest.raises(RouterError) as error:
        c.plan(req)
    assert code_of(error) == "invalid_forge_budget"
    with pytest.raises(RouterError) as error:
        c.validate_configuration(configuration(req))
    assert code_of(error) == "invalid_forge_budget"
    assert not (tmp_path / "invocation").exists()  # Nothing was materialized.


def test_generated_control_bounds_and_worker_digest(tmp_path, monkeypatch):
    with pytest.raises(RouterError) as error:
        c.plan(request(tmp_path, prompt=b"x" * c.REQUEST_MAX))
    assert code_of(error) == "forge_request_too_large"
    plan = c.plan(request(tmp_path))
    assert sum(map(len, plan.generated_controls.values())) <= c.GENERATED_LIMIT
    worker, probe = c.packaged_scripts()
    for changed in ((worker + b"#", probe), (worker, probe + b"#"), (b"", probe)):
        monkeypatch.setattr(c, "packaged_scripts", lambda changed=changed: changed)
        with pytest.raises(RouterError) as error:
            c.plan(request(tmp_path))
        assert code_of(error) == "forge_control_digest_mismatch"
    for name in (c.WORKER_NAME, c.PROBE_NAME):
        forged = replace(
            plan,
            generated_controls={
                **plan.generated_controls,
                name: plan.generated_controls[name] + b"#",
            },
        )
        with pytest.raises(RouterError) as error:
            c.launch_details(forged)
        assert code_of(error) == "forge_control_digest_mismatch"
    for forged in (
        replace(plan, argv=(*plan.argv, "--extra")),
        replace(plan, environment={"X": "1"}),
        replace(
            plan, generated_controls={**plan.generated_controls, c.REQUEST_NAME: b"{}"}
        ),
        replace(plan, details={**plan.details, "model": "other"}),
        replace(plan, details={**plan.details, "cwd": "/elsewhere"}),
    ):
        with pytest.raises(RouterError) as error:
            c.launch_details(forged)
        assert code_of(error) == "invalid_launch_plan"


def test_seam_input_refused_at_reservation(single_flow, monkeypatch):
    req = request(Path("/tmp/forge-seam"))
    seam = SeamInput(
        req.invocation_root / "seam-input.json",
        b"{}",
        c.digest(b"{}"),
        "handoff",
        1,
        "2" * 32,
    )
    with pytest.raises(RouterError) as error:
        c.plan(replace(req, seam_input=seam))
    assert code_of(error) == "forge_seam_input_unsupported"
    c.validate_configuration(configuration(req))  # No seam topology is visible here.
    flow_env(monkeypatch)
    workflow, project, home = forge_flow(single_flow)
    with_handoff(workflow, project)
    run = start(create(workflow, project, home), home)
    assert "invalid_launch_plan" in json.dumps(run.get("run_gate"))
    assert all(
        item["launch"]["adapter_id"] != c.ADAPTER_ID for item in run["invocations"]
    )
    assert "forge_seam_input_unsupported" not in json.dumps(run)


def test_profile_and_revision_changes_affect_only_new_invocations(
    single_flow,
    monkeypatch,
):
    flow_env(monkeypatch)
    workflow, project, home = forge_flow(single_flow)
    instance = create(workflow, project, home)
    first = start(instance, home)
    assert first["status"] == "succeeded", first
    old = copy.deepcopy(first["invocations"][0])
    (workflow.parent / "forge.json").write_text(
        json.dumps(
            {
                **PROFILE,
                "model": "edited-model",
                "budgets": {"max_iterations": 3},
            }
        )
    )
    second = start(instance, home)
    assert second["status"] == "succeeded", second
    new = second["invocations"][0]
    assert new["launch"]["details"]["model"] == "edited-model"
    assert new["launch"]["details"]["budgets"]["max_iterations"] == 3
    assert old["launch"]["details"]["model"] == "fixture-model"
    assert inspect(instance, str(first["id"]), home)["invocations"][0] == old


def test_reload_identical_projection(single_flow, monkeypatch):
    flow_env(monkeypatch)
    workflow, project, home = forge_flow(single_flow)
    instance = create(workflow, project, home)
    run = start(instance, home)
    assert run["status"] == "succeeded", run
    binding = json.loads(workflow.read_text())["bindings"]["native"]["executable"]
    Path(binding).unlink()
    monkeypatch.delenv(URL_ENV)
    later = inspect(instance, str(run["id"]), home)
    # Retained run facts reload through pure planning, unchanged.
    assert set(later) == set(run)
    for key in ("status", "revision_id", "invocations", "completions", "transitions"):
        assert later[key] == run[key], key
    # Only Core's current-folder draft capture may differ: it resolves the binding
    # executable now, finds it missing and reports that as a draft diagnostic.
    assert {key for key in run if later[key] != run[key]} == {"draft_revision"}
    before, draft = run["draft_revision"], later["draft_revision"]
    assert before["diagnostic"] is None and before["revision_id"] is not None
    assert draft["diagnostic"] == "missing_executable"
    assert draft["revision_id"] is None and draft["files"] == before["files"]


def test_reload_v1_after_selecting_v2(single_flow, monkeypatch):
    flow_env(monkeypatch)
    workflow, project, home = forge_flow(single_flow)
    instance = create(workflow, project, home)
    first = start(instance, home)
    assert first["status"] == "succeeded", first
    graph = json.loads(workflow.read_text())
    binding = graph["bindings"]["native"]
    binding["contract"] = c.ADAPTER_CONTRACT_V2
    workflow.write_text(json.dumps(graph))
    second = start(instance, home)
    assert second["status"] == "succeeded", second
    assert second["revision_id"] != first["revision_id"]
    for run, contract, worker_sha256 in (
        (first, c.ADAPTER_CONTRACT, c.WORKER_SHA256),
        (second, c.ADAPTER_CONTRACT_V2, c.WORKER_SHA256_V2),
    ):
        details = run["invocations"][0]["launch"]["details"]
        assert details["contract"] == contract
        assert details["worker_sha256"] == worker_sha256
        assert details["probe_sha256"] == c.PROBE_SHA256

    Path(binding["executable"]).unlink()
    monkeypatch.delenv(URL_ENV)

    def forbidden(*args, **kwargs):
        pytest.fail("loading a retained v1 invocation started a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    reloaded = core.inspect_run(
        instance, str(first["id"]), state_home=home, adapter_resolver=resolve_adapter
    )
    for key in ("status", "revision_id", "invocations", "completions", "transitions"):
        assert reloaded[key] == first[key], key


def test_reload_refuses_changed_script_bytes(single_flow, monkeypatch):
    flow_env(monkeypatch)
    workflow, project, home = forge_flow(single_flow)
    instance = create(workflow, project, home)
    run = start(instance, home)
    assert run["status"] == "succeeded", run
    worker, probe = c.packaged_scripts()
    monkeypatch.setattr(c, "packaged_scripts", lambda: (worker + b"# changed\n", probe))
    with pytest.raises(RouterError) as error:
        inspect(instance, str(run["id"]), home)
    assert code_of(error) == "forge_control_digest_mismatch"


def test_reload_refuses_coherent_script_and_constant_change(single_flow, monkeypatch):
    flow_env(monkeypatch)
    workflow, project, home = forge_flow(single_flow)
    instance = create(workflow, project, home)
    run = start(instance, home)
    assert run["status"] == "succeeded", run
    worker, probe = c.packaged_scripts()
    changed = worker + b"# changed\n"
    monkeypatch.setattr(c, "packaged_scripts", lambda: (changed, probe))
    monkeypatch.setattr(c, "WORKER_SHA256", c.digest(changed))
    replanned = c.plan(request(project))  # plan() itself succeeds with new bytes.
    assert replanned.generated_controls[c.WORKER_NAME] == changed
    with pytest.raises(RouterError) as error:
        inspect(instance, str(run["id"]), home)
    assert code_of(error) == "invocation_integrity"


# ---------------------------------------------------------------- pre-spawn


def test_cwd_checked_before_spawn(tmp_path):
    starts = tmp_path / "starts"
    plan = prepared(tmp_path, starts=str(starts))
    assert plan.details["cwd"] == str(tmp_path)
    with pytest.raises(RouterError) as error:
        adapter.ForgeRunnerAdapter().invoke(
            plan,
            cwd=tmp_path / "elsewhere",
            env=ENV,
            deadline=time.time() + 60,
            poll=lambda: False,
            on_started=lambda pid: pytest.fail("spawned"),
        )
    assert code_of(error) == "invalid_launch_plan"
    assert not starts.exists()


def test_cancel_before_probe(tmp_path):
    starts = tmp_path / "starts"
    result = invoke(prepared(tmp_path, starts=str(starts)), tmp_path, poll=lambda: True)
    assert row(result) == ("cancelled", "forge_cancelled", "stopped", None)
    evidence = result.adapter_evidence
    assert evidence["probe"]["pid"] is None and evidence["probe_report"] is None
    assert evidence["main"] is None and evidence["pid"] is None
    assert not starts.exists()


def wrap_runs(monkeypatch, after_probe=None):
    """Record each owned-process call; run a hook after the probe returns."""
    calls = []
    original = adapter._run

    def recorded(argv, cwd, env, deadline, poll, started, **kwargs):
        calls.append({"argv": argv, "deadline": deadline, "started": started, **kwargs})
        result = original(argv, cwd, env, deadline, poll, started, **kwargs)
        if len(calls) == 1 and after_probe is not None:
            after_probe()
        return result

    monkeypatch.setattr(adapter, "_run", recorded)
    return calls


def test_cancel_after_probe_before_main(tmp_path, monkeypatch):
    starts = tmp_path / "starts"
    cancelled = []
    wrap_runs(monkeypatch, after_probe=lambda: cancelled.append(True))
    result = invoke(
        prepared(tmp_path, starts=str(starts)), tmp_path, poll=lambda: bool(cancelled)
    )
    assert row(result) == ("cancelled", "forge_cancelled", "stopped", None)
    evidence = result.adapter_evidence
    assert evidence["main"]["pid"] is None and evidence["start"] is not None
    assert evidence["pid"] is None and evidence["probe_report"] == ACCEPTED
    assert [json.loads(line)[0] for line in starts.read_text().splitlines()] == [
        "probe"
    ]


def test_deadline_refused_before_probe(tmp_path):
    starts = tmp_path / "starts"
    result = invoke(prepared(tmp_path, starts=str(starts)), tmp_path, timeout=24)
    assert row(result) == (
        "execution_failure",
        "forge_deadline_too_short",
        "stopped",
        None,
    )
    evidence = result.adapter_evidence
    assert evidence["refusal"] == "forge_deadline_too_short"
    assert evidence["probe"] is evidence["main"] is evidence["start"] is None
    assert not starts.exists()


def test_deadline_refused_after_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "MAIN_RESERVE", 10_000)
    starts = tmp_path / "starts"
    result = invoke(prepared(tmp_path, starts=str(starts)), tmp_path)
    assert row(result) == (
        "execution_failure",
        "forge_deadline_too_short",
        "stopped",
        None,
    )
    evidence = result.adapter_evidence
    assert evidence["probe_report"] == ACCEPTED and c.clean(evidence["probe"])
    assert evidence["main"] is None and evidence["start"] is None
    assert len(starts.read_text().splitlines()) == 1


def test_deadline_layering_values(tmp_path, monkeypatch):
    capture = tmp_path / "capture.json"
    calls = wrap_runs(monkeypatch)
    plan = prepared(tmp_path, capture=str(capture), wire=completed())
    deadline = time.time() + 60
    before = time.time()
    result = adapter.ForgeRunnerAdapter().invoke(
        plan,
        cwd=tmp_path,
        env=ENV,
        deadline=deadline,
        poll=lambda: False,
        on_started=lambda pid: None,
    )
    assert result.classification == "completed"
    probe_deadline = calls[0]["deadline"]
    assert before + 5 <= probe_deadline <= time.time() + 5
    assert calls[1]["deadline"] == deadline - 5
    captured = json.loads(capture.read_text())
    start_record = json.loads(base64.b64decode(captured["stdin"]))
    assert start_record == {
        "format": c.START_FORMAT,
        "invocation_id": plan.invocation_id,
        "process_deadline_unix": deadline - 5,
        "soft_deadline_unix": deadline - 5 - 3,
    }
    assert result.adapter_evidence["start"]["process_deadline_unix"] == deadline - 5
    assert calls[0]["stdin"] is None and calls[1]["stdin"] is not None
    assert calls[0]["stdout_limit"] == calls[0]["stderr_limit"] == 4096
    assert (calls[1]["stdout_limit"], calls[1]["stderr_limit"]) == (65537, 1048576)
    assert calls[0]["argv"] == c.probe_argv(
        plan.argv[0], str(Path(plan.argv[-1]).parent)
    )


def test_probe_does_not_announce(tmp_path):
    announced = []
    plan = prepared(tmp_path, wire=b"", exit_code=0)
    result = invoke(plan, tmp_path, started=announced.append)
    assert len(announced) == 1  # Only the main process announced.
    assert announced[0] == result.adapter_evidence["main"]["pid"]
    assert result.adapter_evidence["probe"]["pid"] not in announced


def test_main_announces_pid(tmp_path):
    announced = []
    result = invoke(prepared(tmp_path), tmp_path, started=announced.append)
    evidence = result.adapter_evidence
    assert announced == [evidence["pid"]] == [evidence["main"]["pid"]]


def test_setup_failure_after_popen(tmp_path, monkeypatch):
    from millrace.router import process

    original, calls = process.selectors.DefaultSelector, []

    def selector():
        calls.append(1)
        if len(calls) == 2:
            raise OSError("deterministic setup failure after main Popen")
        return original()

    monkeypatch.setattr(process.selectors, "DefaultSelector", selector)
    result = invoke(prepared(tmp_path), tmp_path)
    assert result.diagnostic == "forge_io_failed"
    assert result.writer_state == "may_live"
    assert result.adapter_evidence["pid"] is None
    assert result.adapter_evidence["main"]["pid"] > 0


def test_spawn_failure(tmp_path, monkeypatch):
    from millrace.router import process

    original, calls = process.subprocess.Popen, []

    def popen(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("deterministic main spawn failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", popen)
    result = invoke(prepared(tmp_path), tmp_path)
    assert row(result) == ("execution_failure", "forge_spawn_failed", "stopped", None)
    assert result.adapter_evidence["main"]["pid"] is None


def test_on_started_failure_propagates(tmp_path):
    from millrace.router.process import OwnedProcessError

    def broken(pid):
        raise RuntimeError("test callback")

    with pytest.raises(OwnedProcessError) as caught:
        invoke(
            prepared(tmp_path, before="time.sleep(30)"),
            tmp_path,
            started=broken,
            validate=False,
        )
    assert caught.value.result.root_reaped


def test_owned_process_unavailable(tmp_path, monkeypatch):
    def unavailable():
        raise CoreContractError("core_process_missing", "test")

    monkeypatch.setattr(adapter, "require_owned_process", unavailable)
    result = invoke(prepared(tmp_path), tmp_path)
    assert row(result) == (
        "execution_failure",
        "forge_owned_process_unavailable",
        "stopped",
        None,
    )
    assert result.adapter_evidence["probe"] is None


def test_process_deadline_timeout(tmp_path, monkeypatch):
    fast(monkeypatch)
    result = invoke(prepared(tmp_path, before="time.sleep(30)"), tmp_path, timeout=3)
    assert row(result)[:3] == ("timeout", "forge_timeout", "may_live")
    assert result.adapter_evidence["envelope"] is None and result.stdout == b""


def test_late_exit_after_envelope_fails_closed(tmp_path, monkeypatch):
    fast(monkeypatch)
    # Models asyncio.run waiting on a default-executor thread after the envelope.
    after = "threading.Thread(target=time.sleep, args=(30,)).start()"
    result = invoke(prepared(tmp_path, after=after), tmp_path, timeout=3)
    assert row(result)[:3] == ("timeout", "forge_timeout", "may_live")
    assert result.adapter_evidence["main"]["stdout_sha256"] == c.digest(completed())
    assert result.adapter_evidence["envelope"] is None and result.stdout == b""


def test_parent_owned_cancellation(tmp_path):
    ready = tmp_path / "ready"
    before = f"Path({str(ready)!r}).write_text('x')\ntime.sleep(30)"
    result = invoke(prepared(tmp_path, before=before), tmp_path, poll=ready.exists)
    assert row(result)[:3] == ("cancelled", "forge_cancelled", "may_live")
    assert result.adapter_evidence["pid"] == result.adapter_evidence["main"]["pid"]


@pytest.mark.parametrize("mode", ["descendant", "ignore_term", "cancel_after_final"])
def test_cancel_after_final_and_unsettled_descendant(tmp_path, mode):
    ready = tmp_path / "ready"
    mark = f"Path({str(ready)!r}).write_text('x')\n"
    poll = lambda: False  # noqa: E731
    if mode == "descendant":
        after = (
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
            "start_new_session=False)"
        )
        expected = ("execution_failure", "forge_cleanup_unconfirmed", "may_live")
    elif mode == "ignore_term":
        after = (
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" + mark + "time.sleep(30)"
        )
        poll = ready.exists
        expected = ("cancelled", "forge_cancelled", "may_live")
    else:
        after = mark + "time.sleep(30)"
        poll = ready.exists
        expected = ("cancelled", "forge_cancelled", "may_live")
    result = invoke(prepared(tmp_path, after=after), tmp_path, poll=poll)
    assert row(result)[:3] == expected
    assert result.stdout == b"" and result.adapter_evidence["semantic"] is None


def test_may_live_hold_reconcile_and_fresh_recovery(single_flow, monkeypatch):
    fast(monkeypatch)
    flow_env(monkeypatch)
    workflow, project, home = forge_flow(single_flow, timeout=3)
    instance = create(workflow, project, home)
    assert start(instance, home)["status"] == "succeeded"
    fake(
        workflow.parent,
        echo_invocation=True,
        wire=completed(invocation_id="__INVOCATION__"),
        before="time.sleep(30)",
    )
    failed_run = start(instance, home)
    assert failed_run["pause_reason"] == "writer_unknown", failed_run
    invocation = failed_run["invocations"][0]
    raw = {
        k: v for k, v in failed_run["completions"][0].items() if k != "effective_writer"
    }
    receipt = core.reconcile_invocation(
        instance,
        str(failed_run["id"]),
        invocation_id=str(invocation["id"]),
        plane_id="main",
        stopped=True,
        reason="exact fake group verified closed",
        state_home=home,
        request_id=new_id(),
        expected_state_version=failed_run["state_version"],
        adapter_resolver=forge_resolver,
    )
    assert receipt["status"] == "applied", receipt
    core.cancel_run(
        instance,
        str(failed_run["id"]),
        state_home=home,
        request_id=new_id(),
        adapter_resolver=forge_resolver,
    )
    fake(
        workflow.parent,
        echo_invocation=True,
        wire=completed(invocation_id="__INVOCATION__"),
    )
    fresh = start(instance, home)
    assert fresh["status"] == "succeeded", fresh
    assert fresh["invocations"][0]["id"] != invocation["id"]
    later = inspect(instance, str(failed_run["id"]), home)
    assert {
        k: v for k, v in later["completions"][0].items() if k != "effective_writer"
    } == raw


# ---------------------------------------------------------------- probe grammar


def test_probe_accepted_line(tmp_path):
    result = invoke(prepared(tmp_path), tmp_path)
    evidence = result.adapter_evidence
    assert evidence["probe_report"] == ACCEPTED
    line = probe_line()
    assert line == (
        b'{"format":"millrace-forge-runner-probe/v1","python":"3.12.7",'
        b'"implementation":"cpython","forge_guardrails":"0.10.1",'
        b'"files_match":true}\n'
    )
    assert evidence["probe"]["stdout_sha256"] == c.digest(line)
    assert evidence["probe"]["stdout_bytes"] == len(line)


GOOD = probe_line()
GRAMMAR_FAILURES = [
    GOOD[:-1],
    GOOD + b"\n",
    GOOD[:-1] + b"\r\n",
    b"\xef\xbb\xbf" + GOOD,
    GOOD.replace(b"cpython", b"cpyth\xc3\xb6n"),
    GOOD.replace(b":", b": ", 1),
    GOOD.replace(
        b'"format":"millrace-forge-runner-probe/v1","python":"3.12.7"',
        b'"python":"3.12.7","format":"millrace-forge-runner-probe/v1"',
    ),
    GOOD[:-2] + b',"files_match":true}\n',
    GOOD.replace(b',"files_match":true', b""),
    GOOD[:-2] + b',"extra":1}\n',
    GOOD.replace(b"probe/v1", b"probe/v2"),
    GOOD.replace(b"true", b"1"),
    GOOD.replace(b'"3.12.7"', b'"3.12"'),
    GOOD.replace(b'"3.12.7"', b'"03.12.1"'),
    GOOD.replace(b'"3.12.7"', b"3"),
    b"",
]


@pytest.mark.parametrize("raw", GRAMMAR_FAILURES)
def test_probe_grammar_failures(tmp_path, raw):
    result = invoke(prepared(tmp_path, probe_stdout=raw), tmp_path)
    assert row(result) == ("execution_failure", "forge_probe_failed", "stopped", None)
    assert result.adapter_evidence["probe_report"] is None
    assert result.adapter_evidence["main"] is None


def test_probe_stderr_refuses(tmp_path):
    result = invoke(prepared(tmp_path, probe_stderr=b"warning"), tmp_path)
    assert result.diagnostic == "forge_probe_failed"
    assert result.adapter_evidence["probe_report"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"python": "3.11.9"},
        {"python": "2.7.18"},
        {"implementation": "pypy"},
        {"forge_guardrails": "0.10.0"},
        {"forge_guardrails": None},
        {"files_match": False},
    ],
)
def test_probe_value_mismatches(tmp_path, change):
    result = invoke(prepared(tmp_path, probe_stdout=probe_line(**change)), tmp_path)
    assert row(result) == ("execution_failure", "forge_probe_mismatch", "stopped", None)
    assert result.adapter_evidence["probe_report"] == {**ACCEPTED, **change}
    assert result.adapter_evidence["main"] is None


def test_probe_row3_vs_row4_recomputable(tmp_path, monkeypatch):
    row3 = invoke(
        prepared(tmp_path, probe_stdout=GOOD.replace(b"3.12.7", b"3.12")), tmp_path
    )
    row4 = invoke(
        prepared(tmp_path, probe_stdout=probe_line(python="3.11.9")), tmp_path
    )
    assert row3.adapter_evidence["probe_report"] is None
    assert row4.adapter_evidence["probe_report"] is not None

    def forbidden(*args, **kwargs):
        pytest.fail("validation performed live access")

    plan = prepared(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    for result in (row3, row4):
        c.validate_completion(plan, facts(plan, result, None))


def test_probe_report_tampering(tmp_path):
    plan = prepared(tmp_path, probe_stdout=probe_line(python="3.11.9"))
    row4 = invoke(plan, tmp_path)
    nulled = resealed(row4.adapter_evidence, probe_report=None)
    refuses(plan, row4, evidence=nulled)  # Recomputes to row 3, recorded row 4.
    plan = prepared(tmp_path, probe_stdout=GOOD.replace(b"3.12.7", b"3.12"))
    row3 = invoke(plan, tmp_path)
    added = resealed(row3.adapter_evidence, probe_report=ACCEPTED)
    refuses(plan, row3, evidence=added)  # Fails the probe digest binding.
    # C2: a grammar-invalid report with matching digests is still refused.
    raw = GOOD.replace(b"3.12.7", b"3.12")
    probe = {
        **row3.adapter_evidence["probe"],
        "stdout_bytes": len(raw),
        "stdout_sha256": c.digest(raw),
    }
    bad = resealed(
        row3.adapter_evidence, probe=probe, probe_report={**ACCEPTED, "python": "3.12"}
    )
    refuses(plan, row3, evidence=bad)


@pytest.mark.parametrize(
    "options",
    [
        {"probe_exit": 1},
        {"probe_before": "time.sleep(30)"},
        {"probe_stdout": b"x" * 4097},
        {"probe_stderr": b"x" * 4097},
        {
            "probe_before": (
                "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"
            )
        },
    ],
)
def test_probe_process_failures(tmp_path, options):
    result = invoke(prepared(tmp_path, **options), tmp_path)
    assert result.classification == "execution_failure"
    assert result.diagnostic == "forge_probe_failed"
    assert result.adapter_evidence["probe_report"] is None
    assert result.adapter_evidence["main"] is None
    assert result.writer_state == c.writer(result.adapter_evidence["probe"])


# ---------------------------------------------------------------- output


def test_completed_envelope_commits(tmp_path):
    result = invoke(prepared(tmp_path), tmp_path)
    assert row(result) == ("completed", "", "stopped", 0)
    assert result.stdout == SEMANTIC.encode() and result.stderr == b""
    evidence = result.adapter_evidence
    assert evidence["envelope"]["parse"] == "ok"
    assert evidence["semantic"] == {
        "size": len(SEMANTIC),
        "sha256": c.digest(SEMANTIC.encode()),
    }
    assert evidence["failure_code"] is None


def test_stdout_limit_boundary(tmp_path):
    base = len(completed(semantic=""))
    semantic = "x" * (c.STDOUT_LIMIT - base)
    exact = completed(semantic=semantic)
    assert len(exact) == 65537
    over = exact + b"y"
    result = invoke(prepared(tmp_path, wire=exact), tmp_path)
    assert row(result) == ("completed", "", "stopped", 0)
    assert result.adapter_evidence["main"]["stdout_bytes"] == 65537
    assert result.adapter_evidence["envelope"]["bytes"] == 65537
    assert result.stdout == semantic.encode()
    result = invoke(prepared(tmp_path, wire=over), tmp_path)
    assert row(result)[:3] == ("protocol_failure", "forge_stdout_limit", "may_live")


def test_stderr_not_retained_and_core_payload_bound(tmp_path):
    secret = b"sk-secret-do-not-persist"
    result = invoke(
        prepared(tmp_path, stderr=secret, wire=failed("forge_sdk_error"), exit_code=3),
        tmp_path,
    )
    assert result.stderr == b"[forge] forge_sdk_error"
    assert secret not in result.stderr
    assert secret.decode() not in json.dumps(result.adapter_evidence)
    result = invoke(prepared(tmp_path, stderr=b"x" * (c.STDERR_LIMIT + 1)), tmp_path)
    assert row(result)[:3] == ("protocol_failure", "forge_stderr_limit", "may_live")
    assert len(result.stderr) <= 65536
    assert len(c.encoded(result.adapter_evidence)) <= c.EVIDENCE_LIMIT


MALFORMED = [
    completed()[:-1],
    completed() + b"\n",
    completed() + completed(),
    b"\xef\xbb\xbf" + completed(),
    b"\xff\n",
    completed().replace(b'"status"', b'"status":"failed","status"'),
    completed()[:-2] + b',"extra":1}\n',
    completed().replace(b',"failure_code":null', b""),
    failed("forge_sdk_error").replace(b"forge_sdk_error", b"forge_unknown"),
    completed().replace(b":", b": ", 1),
    completed().replace(b'"format":"millrace-forge-runner-worker/v1",', b"")[:-2]
    + b',"format":"millrace-forge-runner-worker/v1"}\n',
    completed(semantic="x" * 65537),
    completed().replace(b'"semantic":"', b'"semantic":1,"x":"'),
]


def test_envelope_missing_and_malformed(tmp_path):
    result = invoke(prepared(tmp_path, wire=b""), tmp_path)
    assert row(result) == ("protocol_failure", "forge_envelope_missing", "stopped", 0)
    assert result.adapter_evidence["envelope"]["parse"] == "missing"
    for raw in MALFORMED:
        result = invoke(prepared(tmp_path, wire=raw), tmp_path)
        if len(raw) > c.STDOUT_LIMIT:
            assert result.diagnostic == "forge_stdout_limit"
            continue
        assert row(result) == (
            "protocol_failure",
            "forge_envelope_malformed",
            "stopped",
            0,
        ), raw
        assert result.adapter_evidence["envelope"]["parse"] == "malformed"


@pytest.mark.parametrize("bad", [[], {}, ["forge_sdk_error"], {"code": "x"}])
def test_envelope_failure_code_types_are_malformed(tmp_path, bad):
    """B1: a list or dict failure_code is row 13, never an escaping TypeError."""
    raw = c.envelope_line(INVOCATION, "failed", None, bad)  # type: ignore[arg-type]
    result = invoke(prepared(tmp_path, wire=raw, exit_code=3), tmp_path)
    assert row(result) == ("protocol_failure", "forge_envelope_malformed", "stopped", 3)
    envelope = result.adapter_evidence["envelope"]
    assert (envelope["parse"], envelope["status"], envelope["worker_failure_code"]) == (
        "malformed",
        None,
        None,
    )
    assert envelope["bytes"] == len(raw) and envelope["sha256"] == c.digest(raw)


def test_retained_counts_are_exact_bounded_integers(tmp_path):
    """B2: equal floats and coherent over-limit counts refuse after resealing."""
    root = tmp_path / "completed"
    plan = prepared(root)
    result = invoke(plan, root)
    evidence = result.adapter_evidence
    for changes in (
        {
            "envelope": {
                **evidence["envelope"],
                "bytes": float(evidence["envelope"]["bytes"]),
            }
        },
        {
            "semantic": {
                **evidence["semantic"],
                "size": float(evidence["semantic"]["size"]),
            }
        },
        {"semantic": {**evidence["semantic"], "size": True}},
    ):
        refuses(plan, result, evidence=resealed(evidence, **changes))
    root = tmp_path / "malformed"
    plan = prepared(root, wire=completed()[:-1])
    result = invoke(plan, root)
    evidence = result.adapter_evidence
    assert evidence["envelope"]["parse"] == "malformed"
    over = c.STDOUT_LIMIT + 1
    coherent = resealed(
        evidence,
        main={**evidence["main"], "stdout_bytes": over},
        envelope={**evidence["envelope"], "bytes": over},
    )
    refuses(plan, result, evidence=coherent)


def test_exit_envelope_contradictions(tmp_path):
    for wire, exit_code, expected in (
        (completed(), 3, "forge_envelope_contradiction"),
        (failed("forge_sdk_error"), 0, "forge_envelope_contradiction"),
        (completed(invocation_id="f" * 32), 0, "forge_envelope_contradiction"),
        (completed(), 1, "forge_nonzero_exit"),
        (completed(), 137, "forge_nonzero_exit"),
    ):
        result = invoke(prepared(tmp_path, wire=wire, exit_code=exit_code), tmp_path)
        assert result.diagnostic == expected
        assert result.classification in {"protocol_failure", "execution_failure"}
        assert result.writer_state == "stopped" and result.stdout == b""


def test_envelope_parsed_only_when_clean(tmp_path):
    result = invoke(prepared(tmp_path, exit_code=1), tmp_path)
    assert result.adapter_evidence["envelope"] is None
    assert result.diagnostic == "forge_nonzero_exit"
    after = "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"
    result = invoke(prepared(tmp_path, after=after), tmp_path)
    assert result.adapter_evidence["envelope"] is None
    assert result.diagnostic == "forge_cleanup_unconfirmed"


def test_startup_stdout_fails_closed(tmp_path):
    # Stdout from a .pth file or sitecustomize precedes the envelope (W-EN-01).
    result = invoke(prepared(tmp_path, before="os.write(1, b'startup\\n')"), tmp_path)
    assert row(result) == ("protocol_failure", "forge_envelope_malformed", "stopped", 0)


def test_worker_failure_classification(tmp_path):
    result = invoke(
        prepared(tmp_path, wire=failed("forge_deadline_exceeded"), exit_code=3),
        tmp_path,
    )
    assert row(result) == ("timeout", "forge_deadline_exceeded", "stopped", 3)
    for code in sorted(c.WORKER_CODES - {"forge_deadline_exceeded"}):
        result = invoke(prepared(tmp_path, wire=failed(code), exit_code=3), tmp_path)
        assert row(result) == ("execution_failure", code, "stopped", 3)
        assert result.stderr == ("[forge] " + code).encode()


def process(**changes) -> dict:
    base = {
        "pid": 10,
        "exit_code": 0,
        "stop_cause": None,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "stdout_sha256": c.digest(b""),
        "stderr_sha256": c.digest(b""),
        "stdout_truncated": False,
        "stderr_truncated": False,
        "stdout_overflow": False,
        "stderr_overflow": False,
        "stdin_closed": True,
        "stdout_eof": True,
        "stderr_eof": True,
        "root_reaped": True,
        "cleanup_forced": False,
        "group_state": "closed",
        "cleanup_confirmed": True,
    }
    return {**base, **changes}


UNSPAWNED = process(
    pid=None,
    exit_code=None,
    stop_cause="cancelled",
    root_reaped=False,
    stdout_eof=False,
    stderr_eof=False,
)


def test_writer_state_rule():
    assert c.writer() == "stopped"
    assert c.writer(None, None) == "stopped"
    assert c.writer(UNSPAWNED) == "stopped"
    assert c.writer(process(), UNSPAWNED) == "stopped"
    assert c.writer(process(), process()) == "stopped"
    for spawned in (
        process(stop_cause="timeout"),
        process(cleanup_forced=True),
        process(cleanup_confirmed=False, group_state="present"),
    ):
        assert c.writer(process(), spawned) == "may_live"
        assert c.writer(spawned, None) == "may_live"
    # The adapter never reports `unknown`; Core assigns it to interruptions.
    assert {expected[2] for *_, expected in ROWS} == {"stopped", "may_live"}


ENVELOPE_OK = {
    "bytes": 1,
    "sha256": "0" * 64,
    "parse": "ok",
    "status": "completed",
    "worker_failure_code": None,
}
ROWS = [
    (
        1,
        "forge_owned_process_unavailable",
        None,
        None,
        None,
        None,
        ("execution_failure", "forge_owned_process_unavailable", "stopped"),
    ),
    (
        1,
        "forge_deadline_too_short",
        process(),
        ACCEPTED,
        None,
        None,
        ("execution_failure", "forge_deadline_too_short", "stopped"),
    ),
    (2, None, UNSPAWNED, None, None, None, ("cancelled", "forge_cancelled", "stopped")),
    (
        2,
        None,
        process(stop_cause="cancelled"),
        None,
        None,
        None,
        ("cancelled", "forge_cancelled", "may_live"),
    ),
    (
        3,
        None,
        process(exit_code=1),
        None,
        None,
        None,
        ("execution_failure", "forge_probe_failed", "stopped"),
    ),
    (
        3,
        None,
        process(stop_cause="timeout"),
        None,
        None,
        None,
        ("execution_failure", "forge_probe_failed", "may_live"),
    ),
    (
        3,
        None,
        process(),
        None,
        None,
        None,
        ("execution_failure", "forge_probe_failed", "stopped"),
    ),
    (
        4,
        None,
        process(),
        {**ACCEPTED, "files_match": False},
        None,
        None,
        ("execution_failure", "forge_probe_mismatch", "stopped"),
    ),
    (
        5,
        None,
        process(),
        ACCEPTED,
        process(stop_cause="cancelled"),
        None,
        ("cancelled", "forge_cancelled", "may_live"),
    ),
    (
        6,
        None,
        process(),
        ACCEPTED,
        UNSPAWNED,
        None,
        ("cancelled", "forge_cancelled", "stopped"),
    ),
    (
        7,
        None,
        process(),
        ACCEPTED,
        process(stop_cause="timeout"),
        None,
        ("timeout", "forge_timeout", "may_live"),
    ),
    (
        8,
        None,
        process(),
        ACCEPTED,
        process(stop_cause="stdout_overflow"),
        None,
        ("protocol_failure", "forge_stdout_limit", "may_live"),
    ),
    (
        8,
        None,
        process(),
        ACCEPTED,
        process(stop_cause="stderr_overflow"),
        None,
        ("protocol_failure", "forge_stderr_limit", "may_live"),
    ),
    (
        9,
        None,
        process(),
        ACCEPTED,
        {**UNSPAWNED, "stop_cause": "start_failed"},
        None,
        ("execution_failure", "forge_spawn_failed", "stopped"),
    ),
    (
        9,
        None,
        process(),
        ACCEPTED,
        {**UNSPAWNED, "stop_cause": "unsupported_platform"},
        None,
        ("execution_failure", "forge_unsupported_platform", "stopped"),
    ),
    (
        9,
        None,
        process(),
        ACCEPTED,
        process(stop_cause="io_failed"),
        None,
        ("execution_failure", "forge_io_failed", "may_live"),
    ),
    (
        10,
        None,
        process(),
        ACCEPTED,
        process(cleanup_forced=True),
        None,
        ("execution_failure", "forge_cleanup_unconfirmed", "may_live"),
    ),
    (
        11,
        None,
        process(),
        ACCEPTED,
        process(exit_code=1),
        None,
        ("execution_failure", "forge_nonzero_exit", "stopped"),
    ),
    (
        12,
        None,
        process(),
        ACCEPTED,
        process(),
        {**ENVELOPE_OK, "parse": "missing"},
        ("protocol_failure", "forge_envelope_missing", "stopped"),
    ),
    (
        13,
        None,
        process(),
        ACCEPTED,
        process(),
        {**ENVELOPE_OK, "parse": "malformed"},
        ("protocol_failure", "forge_envelope_malformed", "stopped"),
    ),
    (
        14,
        None,
        process(),
        ACCEPTED,
        process(),
        {**ENVELOPE_OK, "parse": "contradiction"},
        ("protocol_failure", "forge_envelope_contradiction", "stopped"),
    ),
    (
        15,
        None,
        process(),
        ACCEPTED,
        process(exit_code=3),
        {
            **ENVELOPE_OK,
            "status": "failed",
            "worker_failure_code": "forge_deadline_exceeded",
        },
        ("timeout", "forge_deadline_exceeded", "stopped"),
    ),
    (
        16,
        None,
        process(),
        ACCEPTED,
        process(exit_code=3),
        {**ENVELOPE_OK, "status": "failed", "worker_failure_code": "forge_sdk_error"},
        ("execution_failure", "forge_sdk_error", "stopped"),
    ),
    (
        17,
        None,
        process(),
        ACCEPTED,
        process(),
        ENVELOPE_OK,
        ("completed", None, "stopped"),
    ),
]


@pytest.mark.parametrize("number,refusal,probe,report,main,envelope,expected", ROWS)
def test_disposition_table_rows(
    number, refusal, probe, report, main, envelope, expected
):
    assert c.disposition(refusal, probe, report, main, envelope) == expected, number


# ---------------------------------------------------------------- validation


# ---- B3: valid sealed completion records for all 17 rows ------------------

ROW_FIELDS = (
    "refusal",
    "probe",
    "probe_report",
    "main",
    "start",
    "envelope",
    "semantic",
    "failure_code",
)
DESCENDANT = "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])"
MARK = "Path('ready').write_text('x')\ntime.sleep(30)"  # Relative to the peer cwd.


def nonnull_fields(number: int, name: str) -> set[str]:
    """W-RV-01 null conditions for one disposition row."""
    present = {"failure_code"} if number != 17 else {"semantic"}
    if number == 1:
        present |= {"refusal"}
        if name == "short_after":
            present |= {"probe", "probe_report"}
        return present
    present |= {"probe"}
    if number == 4 or number >= 5:
        present |= {"probe_report"}
    if number >= 5:
        present |= {"main", "start"}
    if number >= 12:
        present |= {"envelope"}
    return present


def _unavailable(m, root):
    def refuse():
        raise CoreContractError("core_process_missing", "test")

    m.setattr(adapter, "require_owned_process", refuse)


def _between(m, root):
    flag: list[bool] = []
    wrap_runs(m, after_probe=lambda: flag.append(True))
    return lambda: bool(flag)


def _second_popen_fails(m, root):
    from millrace.router import process

    original, calls = process.subprocess.Popen, []

    def popen(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("deterministic main spawn failure")
        return original(*args, **kwargs)

    m.setattr(process.subprocess, "Popen", popen)


SIGNALLED = "signalled"
ANY = "any"
# (row, case, peer options, setup, timeout, expected transport, diagnostic,
#  writer, exit code, announced pid)
ROW_CASES = [
    (
        1,
        "unavailable",
        {},
        _unavailable,
        60,
        "execution_failure",
        "forge_owned_process_unavailable",
        "stopped",
        None,
        False,
    ),
    (
        1,
        "short_before",
        {},
        None,
        24,
        "execution_failure",
        "forge_deadline_too_short",
        "stopped",
        None,
        False,
    ),
    (
        1,
        "short_after",
        {},
        lambda m, r: m.setattr(c, "MAIN_RESERVE", 10_000),
        60,
        "execution_failure",
        "forge_deadline_too_short",
        "stopped",
        None,
        False,
    ),
    (
        2,
        "cancel_before",
        {},
        lambda m, r: lambda: True,
        60,
        "cancelled",
        "forge_cancelled",
        "stopped",
        None,
        False,
    ),
    (
        3,
        "probe_exit",
        {"probe_exit": 1},
        None,
        60,
        "execution_failure",
        "forge_probe_failed",
        "stopped",
        None,
        False,
    ),
    (
        4,
        "probe_mismatch",
        {"probe_stdout": probe_line(files_match=False)},
        None,
        60,
        "execution_failure",
        "forge_probe_mismatch",
        "stopped",
        None,
        False,
    ),
    (
        5,
        "cancel_main",
        {"before": MARK},
        lambda m, r: (r / "ready").exists,
        60,
        "cancelled",
        "forge_cancelled",
        "may_live",
        SIGNALLED,
        True,
    ),
    (
        6,
        "cancel_between",
        {},
        _between,
        60,
        "cancelled",
        "forge_cancelled",
        "stopped",
        None,
        False,
    ),
    (
        7,
        "timeout",
        {"before": "time.sleep(30)"},
        lambda m, r: fast(m),
        3,
        "timeout",
        "forge_timeout",
        "may_live",
        SIGNALLED,
        True,
    ),
    (
        8,
        "overflow",
        {"wire": b"x" * (c.STDOUT_LIMIT + 1)},
        None,
        60,
        "protocol_failure",
        "forge_stdout_limit",
        "may_live",
        ANY,
        True,
    ),
    (
        9,
        "spawn_failed",
        {},
        _second_popen_fails,
        60,
        "execution_failure",
        "forge_spawn_failed",
        "stopped",
        None,
        False,
    ),
    (
        10,
        "descendant",
        {"after": DESCENDANT},
        None,
        60,
        "execution_failure",
        "forge_cleanup_unconfirmed",
        "may_live",
        0,
        True,
    ),
    (
        11,
        "nonzero",
        {"exit_code": 1},
        None,
        60,
        "execution_failure",
        "forge_nonzero_exit",
        "stopped",
        1,
        True,
    ),
    (
        12,
        "missing",
        {"wire": b""},
        None,
        60,
        "protocol_failure",
        "forge_envelope_missing",
        "stopped",
        0,
        True,
    ),
    (
        13,
        "malformed",
        {"wire": completed()[:-1]},
        None,
        60,
        "protocol_failure",
        "forge_envelope_malformed",
        "stopped",
        0,
        True,
    ),
    (
        14,
        "contradiction",
        {"exit_code": 3},
        None,
        60,
        "protocol_failure",
        "forge_envelope_contradiction",
        "stopped",
        3,
        True,
    ),
    (
        15,
        "deadline",
        {"wire": failed("forge_deadline_exceeded"), "exit_code": 3},
        None,
        60,
        "timeout",
        "forge_deadline_exceeded",
        "stopped",
        3,
        True,
    ),
    (
        16,
        "worker_code",
        {"wire": failed("forge_sdk_error"), "exit_code": 3},
        None,
        60,
        "execution_failure",
        "forge_sdk_error",
        "stopped",
        3,
        True,
    ),
    (17, "completed", {}, None, 60, "completed", "", "stopped", 0, True),
]


class ForbiddenEnviron(dict):
    """Stands in for os.environ; any read or write fails the test."""

    def _fail(self, *args, **kwargs):
        pytest.fail("pure validation read the environment")

    __getitem__ = __contains__ = __iter__ = __len__ = get = keys = items = _fail
    values = copy = setdefault = __setitem__ = _fail


def collect_row_records(tmp_path, monkeypatch) -> list:
    """Run each case in its own directory and invoke before preparing the next."""
    records = []
    for case in ROW_CASES:
        number, name, options, setup, timeout = case[:5]
        root = tmp_path / name
        with monkeypatch.context() as m:
            poll = setup(m, root) if setup is not None else None
            plan = prepared(root, **options)
            result = invoke(plan, root, timeout=timeout, poll=poll or (lambda: False))
        records.append((case, plan, result))
    return records


def test_validate_completion_is_pure(tmp_path, monkeypatch):
    records = collect_row_records(tmp_path, monkeypatch)
    assert sorted({case[0] for case, _, _ in records}) == list(range(1, 18))
    for case, plan, result in records:
        number, name = case[:2]
        transport, diagnostic, writer, exit_code, announced = case[5:]
        evidence = result.adapter_evidence
        assert (result.classification, result.diagnostic, result.writer_state) == (
            transport,
            diagnostic,
            writer,
        ), name
        if exit_code == SIGNALLED:
            assert type(result.exit_code) is int and result.exit_code < 0, name
        elif exit_code != ANY:
            assert result.exit_code == exit_code, name
        main = evidence["main"]
        assert result.exit_code == (main["exit_code"] if main else None), name
        assert (evidence["pid"] is not None) is announced, name
        if announced:
            assert evidence["pid"] == main["pid"], name
        if number in {6, 9}:
            assert main["pid"] is None, name
        assert {k for k in ROW_FIELDS if evidence[k] is not None} == nonnull_fields(
            number, name
        ), name
        assert evidence["receipt_sha256"] == c.seal(evidence)["receipt_sha256"], name

    def forbidden(*args, **kwargs):
        pytest.fail("pure validation performed live access")

    with monkeypatch.context() as m:
        for target, attr in (
            (subprocess, "Popen"),
            (os, "getenv"),
            (os, "stat"),
            (os, "open"),
            (os, "listdir"),
            (builtins, "open"),
            (Path, "read_bytes"),
            (Path, "stat"),
            (time, "time"),
            (time, "monotonic"),
            (c, "packaged_scripts"),
        ):
            m.setattr(target, attr, forbidden)
        m.setattr(os, "environ", ForbiddenEnviron())
        for case, plan, result in records:
            selected = facts(plan, result, result.adapter_evidence["pid"])
            c.validate_completion(plan, selected)
            adapter.ForgeRunnerAdapter().validate_completion(plan, selected)


def test_pid_chain_corruption(tmp_path):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    pid = result.adapter_evidence["pid"]
    refuses(plan, result, pid=pid + 1)
    refuses(plan, result, pid=None)
    main = {**result.adapter_evidence["main"], "pid": pid + 1}
    refuses(
        plan, result, evidence=resealed(result.adapter_evidence, main=main), pid=pid
    )
    cancelled = invoke(plan, tmp_path, poll=lambda: True)
    refuses(
        plan, cancelled, evidence=resealed(cancelled.adapter_evidence, pid=7), pid=7
    )


def test_refusal_consistency(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "MAIN_RESERVE", 10_000)
    plan = prepared(tmp_path)
    late = invoke(plan, tmp_path)
    monkeypatch.undo()
    assert late.diagnostic == "forge_deadline_too_short"
    unavailable = resealed(
        late.adapter_evidence,
        refusal="forge_owned_process_unavailable",
        failure_code="forge_owned_process_unavailable",
    )
    refuses(
        plan,
        late,
        evidence=unavailable,
        diagnostic="forge_owned_process_unavailable",
        stderr=b"[forge] forge_owned_process_unavailable",
    )
    rejected = resealed(
        late.adapter_evidence, probe_report={**ACCEPTED, "files_match": False}
    )
    refuses(plan, late, evidence=rejected)
    ok = invoke(plan, tmp_path)
    with_refusal = resealed(ok.adapter_evidence, refusal="forge_deadline_too_short")
    refuses(plan, ok, evidence=with_refusal)


def test_envelope_and_probe_binding(tmp_path):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    evidence = result.adapter_evidence
    envelope = {**evidence["envelope"], "sha256": "0" * 64}
    refuses(plan, result, evidence=resealed(evidence, envelope=envelope))
    refuses(
        plan,
        result,
        stdout=b'{"outcome":"READY","summary":"forged","refs":[]}',
        evidence=resealed(
            evidence,
            semantic={
                "size": 49,
                "sha256": c.digest(b'{"outcome":"READY","summary":"forged","refs":[]}'),
            },
        ),
    )
    report = {**ACCEPTED, "python": "3.12.8"}
    refuses(plan, result, evidence=resealed(evidence, probe_report=report))


@pytest.mark.parametrize(
    "key,value",
    [
        ("launch_sha256", "0" * 64),
        ("invocation_id", "f" * 32),
        ("format", "millrace-forge-runner-result/v2"),
        ("failure_code", "forge_timeout"),
        ("semantic", None),
        ("start", None),
        ("envelope", None),
        ("refusal", "forge_deadline_too_short"),
    ],
)
def test_completion_evidence_corruption(tmp_path, key, value):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    evidence = copy.deepcopy(result.adapter_evidence)
    evidence[key] = value
    refuses(plan, result, evidence=evidence)  # Seal breaks.
    refuses(plan, result, evidence=resealed(result.adapter_evidence, **{key: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("transport", "execution_failure"),
        ("diagnostic", "forge_timeout"),
        ("writer_state", "may_live"),
        ("exit_code", 3),
        ("stdout", b"{}"),
        ("stderr", b"[forge] x"),
        ("origin", "core_preparation"),
        ("invocation_id", "f" * 32),
    ],
)
def test_completion_facts_corruption(tmp_path, field, value):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    refuses(plan, result, **{field: value})


@pytest.mark.parametrize(
    "key,value",
    [
        ("exit_code", 1),
        ("stop_cause", "timeout"),
        ("stdout_bytes", 0),
        ("stdout_sha256", "0" * 64),
        ("group_state", "running"),
        ("cleanup_forced", True),
        ("root_reaped", False),
        ("stdout_eof", False),
    ],
)
def test_main_process_fact_corruption(tmp_path, key, value):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    main = {**result.adapter_evidence["main"], key: value}
    refuses(plan, result, evidence=resealed(result.adapter_evidence, main=main))


def test_result_record_size_bound(tmp_path):
    plan = prepared(tmp_path)
    result = invoke(plan, tmp_path)
    assert len(c.encoded(result.adapter_evidence)) <= c.EVIDENCE_LIMIT
    with pytest.raises(RouterError):
        c.seal({**result.adapter_evidence, "padding": "x" * c.EVIDENCE_LIMIT})
    padded = {**result.adapter_evidence, "padding": "x" * c.EVIDENCE_LIMIT}
    refuses(plan, result, evidence=padded)


# ------------------------------------------------------------- section 6 aliases (C3)


def test_version_probe_bounds_and_mismatch(tmp_path):
    assert invoke(prepared(tmp_path), tmp_path).classification == "completed"
    for raw, code in (
        (GOOD.replace(b"3.12.7", b"3.12"), "forge_probe_failed"),
        (b"x" * 4097, "forge_probe_failed"),
        (probe_line(forge_guardrails="0.10.0"), "forge_probe_mismatch"),
    ):
        assert invoke(prepared(tmp_path, probe_stdout=raw), tmp_path).diagnostic == code


def test_finality_and_failure_traces(tmp_path):
    for wire, exit_code, code in (
        (completed(), 0, ""),
        (b"", 0, "forge_envelope_missing"),
        (completed()[:-1], 0, "forge_envelope_malformed"),
        (completed(), 3, "forge_envelope_contradiction"),
        (failed("forge_max_iterations"), 3, "forge_max_iterations"),
    ):
        result = invoke(prepared(tmp_path, wire=wire, exit_code=exit_code), tmp_path)
        assert result.diagnostic == code


def test_cancel_before_during_after_final(tmp_path):
    before = invoke(prepared(tmp_path), tmp_path, poll=lambda: True)
    assert row(before) == ("cancelled", "forge_cancelled", "stopped", None)
    ready = tmp_path / "ready"
    mark = f"Path({str(ready)!r}).write_text('x')\ntime.sleep(30)"
    during = invoke(prepared(tmp_path, before=mark), tmp_path, poll=ready.exists)
    assert row(during)[:3] == ("cancelled", "forge_cancelled", "may_live")
    ready.unlink()
    after = invoke(prepared(tmp_path, after=mark), tmp_path, poll=ready.exists)
    assert row(after)[:3] == ("cancelled", "forge_cancelled", "may_live")


def test_capture_overflow_and_redacted_failure(tmp_path):
    over = invoke(prepared(tmp_path, wire=b"x" * (c.STDOUT_LIMIT + 1)), tmp_path)
    assert over.diagnostic == "forge_stdout_limit" and over.stdout == b""
    secret = b"sk-secret-do-not-persist"
    redacted = invoke(
        prepared(tmp_path, stderr=secret, wire=failed("forge_sdk_error"), exit_code=3),
        tmp_path,
    )
    assert secret not in redacted.stderr
    assert secret.decode() not in json.dumps(redacted.adapter_evidence)


def test_constants_match_contract():
    assert (c.ADAPTER_ID, c.ADAPTER_CONTRACT) == (
        "forge_runner",
        "millrace-forge-runner/v1",
    )
    assert INVOCATION == request(Path("/tmp")).invocation_id
    assert ENV_NAMES == ("FORGE_BASE_URL", "FORGE_API_KEY")
    assert c.STDOUT_LIMIT == 65537 and c.PROBE_LIMIT == 4096
    assert c.REQUEST_MAX + c.WORKER_SCRIPT_MAX + c.PROBE_SCRIPT_MAX == c.GENERATED_LIMIT
    assert (c.PROBE_RESERVE, c.MAIN_RESERVE, c.CLEANUP_RESERVE, c.SOFT_RESERVE) == (
        25,
        20,
        5,
        3,
    )


def test_plus_resolver_selects_exact_pair():
    selected = resolve_adapter(c.ADAPTER_ID, c.ADAPTER_CONTRACT)
    assert type(selected) is adapter.ForgeRunnerAdapter
    selected_v2 = resolve_adapter(c.ADAPTER_ID, c.ADAPTER_CONTRACT_V2)
    assert type(selected_v2) is adapter.ForgeRunnerV2Adapter
    selected_v3 = resolve_adapter(c.ADAPTER_ID, c.ADAPTER_CONTRACT_V3)
    assert type(selected_v3) is adapter.ForgeRunnerV3Adapter
    for contract in ("millrace-forge-runner/v4", "millrace-forge-runner"):
        with pytest.raises(RouterError) as error:
            resolve_adapter(c.ADAPTER_ID, contract)
        assert code_of(error) == "unsupported_adapter_contract"
    with pytest.raises(RouterError) as error:
        resolve_adapter("forge", c.ADAPTER_CONTRACT_V2)
    assert code_of(error) == "unsupported_adapter_contract"
