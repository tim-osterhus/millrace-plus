"""Strict identity, public controls and settled helper outcomes."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from subworkflow_support import setup, write_request

from millrace_plus import subworkflow_records as records
from millrace_plus import subworkflows


@pytest.fixture
def helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "case")
    monkeypatch.chdir(fixture["project"])
    return fixture


@pytest.mark.parametrize(
    "patch",
    [
        {"extra": True},
        {"wait_seconds": True},
        {"wait_seconds": 0},
        {"cleanup_seconds": 61},
        {"call_key": "../escape"},
        {"definition": {"selector": "remote:x", "version": "1", "sha256": "a" * 64}},
        {"output_ref": "../escape"},
        {"call_home": "relative"},
    ],
)
def test_strict_request_contract(
    helper: dict[str, object], patch: dict[str, object]
) -> None:
    value = {**helper["request"], **patch}
    with pytest.raises(records.SubworkflowError):
        records.parse_request(json.dumps(value).encode(), cwd=Path.cwd())
    assert not (Path(str(helper["root"])) / "calls").exists()


@pytest.mark.parametrize(
    "raw", [b'{"format":1,"format":2}', b'{"n":NaN}', b"[]", b"\xff", b" " * 65537]
)
def test_strict_json_boundaries(raw: bytes) -> None:
    with pytest.raises(records.SubworkflowError):
        records.parse_request(raw, cwd=Path.cwd())


def test_call_replay_and_conflicting_fingerprint(helper: dict[str, object]) -> None:
    first = subworkflows.call(Path(str(helper["file"])))
    assert first["outcome"] == "CHILD_SUCCEEDED", first
    manifest = json.loads(Path(first["refs"][0]).read_bytes())
    assert manifest["status"] == "succeeded"
    assert manifest["run_id"] and manifest["instance_id"]
    request = helper["request"]
    saved = subworkflows.inspect(Path(request["call_home"]), request["call_key"])
    assert saved["phase"] == "terminal"
    assert saved["observation"]["quiescent"] is True
    assert subworkflows.call(Path(str(helper["file"]))) == first
    request["inputs"]["request_markdown"] = "Changed"
    write_request(helper)
    with pytest.raises(records.SubworkflowError, match="call_conflict"):
        subworkflows.call(Path(str(helper["file"])))
    again = subworkflows.inspect(Path(request["call_home"]), request["call_key"])
    assert again["instance_id"] == saved["instance_id"]
    assert again["run_id"] == saved["run_id"]


def test_inputs_manifest_is_exact_and_stable(helper: dict[str, object]) -> None:
    result = subworkflows.call(Path(str(helper["file"])))
    request = helper["request"]
    root = Path(request["call_home"]) / request["call_key"]
    inputs = json.loads((root / "project/subworkflow-inputs.json").read_bytes())
    assert set(inputs) == {"format", "identity", "inputs"}
    assert inputs["format"] == "millrace-plus-subworkflow-inputs/v1"
    assert inputs["inputs"] == request["inputs"]
    assert (root / "project/answer.md").read_bytes() == b"Exact goal\r\n\n"
    saved = subworkflows.inspect(Path(request["call_home"]), request["call_key"])
    assert saved["settings"]["path"]
    assert saved["envelopes"]["create"]["payload"]["path"] == saved["settings"]["path"]
    assert result["refs"] == [request["output_ref"]]
    # Public observations contain command output; helper records cannot retain it.
    assert "stdout" not in json.dumps(saved["observation"])
    assert "base64" not in json.dumps(saved["observation"])


@pytest.mark.parametrize("kind", ["parent", "ancestor", "alias", "catalog", "output"])
def test_parent_child_root_and_catalog_refusals(
    helper: dict[str, object],
    kind: str,
) -> None:
    request = helper["request"]
    project = Path(str(helper["project"]))
    if kind == "parent":
        request["call_home"] = str(project)
    elif kind == "ancestor":
        request["call_home"] = str(project.parent)
    elif kind == "alias":
        alias = Path(str(helper["root"])) / "alias"
        alias.symlink_to(project, target_is_directory=True)
        request["call_home"] = str(alias)
    elif kind == "catalog":
        request["call_home"] = str(helper["local"])
    else:
        (project / "results").symlink_to(helper["local"], target_is_directory=True)
    write_request(helper)
    expected = {
        "parent": "parent_child_overlap",
        "ancestor": "parent_child_overlap",
        "alias": "noncanonical_path",
        "catalog": "catalog_ownership",
        "output": "noncanonical_path",
    }[kind]
    with pytest.raises(records.SubworkflowError, match=expected):
        subworkflows.call(Path(str(helper["file"])))
    home = Path(request["call_home"])
    assert not (home / request["call_key"]).exists()
    assert not (home / ("." + request["call_key"] + ".journal.lock")).exists()
    assert not (home / ("." + request["call_key"] + ".observer.lock")).exists()


def test_definition_pin_and_copy_verification(helper: dict[str, object]) -> None:
    definition = Path(str(helper["definition"]))
    (definition / "step.py").write_text("changed source\n")
    with pytest.raises(records.SubworkflowError, match="definition_mismatch"):
        subworkflows.call(Path(str(helper["file"])))
    assert not Path(helper["request"]["call_home"]).exists()


def test_bundled_version_mismatch_refuses_before_call_files(
    helper: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = helper["request"]
    request["definition"].update(selector="bundled:document-review", version="0.0.0")
    write_request(helper)

    def no_catalog_lookup(*args, **kwargs):
        raise AssertionError("version mismatch must refuse before catalog lookup")

    monkeypatch.setattr(subworkflows.catalog, "list_templates", no_catalog_lookup)
    with pytest.raises(records.SubworkflowError, match="definition_mismatch"):
        subworkflows.call(Path(str(helper["file"])))
    assert not Path(request["call_home"]).exists()


def test_paused_child_then_later_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid

    from millrace_plus.router import control

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(
        tmp_path / "pause",
        script=(
            "from pathlib import Path\nimport json\n"
            "out='READY' if Path('approved').exists() else 'HOLD'\n"
            "print(json.dumps({'outcome':out}))\n"
        ),
    )
    monkeypatch.chdir(fixture["project"])
    result = subworkflows.call(Path(str(fixture["file"])))
    assert result["outcome"] == "CHILD_ATTENTION"
    req = fixture["request"]
    saved = subworkflows.inspect(Path(req["call_home"]), req["call_key"])
    project = Path(saved["paths"]["project"])
    (project / "approved").touch()
    home = Path(saved["paths"]["state"])
    view = control.inspect_run(saved["instance_id"], saved["run_id"], state_home=home)
    control.resume_plane(
        saved["instance_id"],
        saved["run_id"],
        state_home=home,
        request_id=uuid.uuid4().hex,
        expected_state_version=view["state_version"],
        plane_id="main",
    )
    assert subworkflows.call(Path(str(fixture["file"])))["outcome"] == "CHILD_SUCCEEDED"


def test_completion_requires_exact_terminal_and_writer_closure() -> None:
    view = {
        "id": "a" * 32,
        "status": "succeeded",
        "quiescent": True,
        "inflight_invocations": [],
        "invocations": [{"writer_state": "stopped"}],
        "completions": [{"effective_writer": "stopped"}],
    }
    assert subworkflows.closed(view)
    for patch in (
        {"quiescent": False},
        {"status": "waiting"},
        {"invocations": [{"writer_state": "may_live"}]},
        {"completions": [{"effective_writer": "may_live"}]},
        {"inflight_invocations": ["active"]},
    ):
        assert not subworkflows.closed({**view, **patch})


def test_bounded_records_and_protocol_output(helper: dict[str, object]) -> None:
    with pytest.raises(records.SubworkflowError):
        records.atomic_json(
            Path(str(helper["root"])) / "large.json", {"x": "a" * 50}, limit=10
        )
    assert not (Path(str(helper["root"])) / "large.json").exists()
    original = copy.deepcopy(helper["request"])
    result = subworkflows.call(Path(str(helper["file"])))
    assert set(result) == {"outcome", "summary", "refs"}
    assert len(result["summary"]) <= 4096
    assert helper["request"] == original


def test_concurrent_duplicate_has_one_observer(helper: dict[str, object]) -> None:
    request = helper["request"]
    subworkflows.prepare(Path(str(helper["file"])))
    journal = records.Journal(Path(request["call_home"]), request["call_key"])
    with journal.lock(observer=True):
        with pytest.raises(records.SubworkflowError, match="call_busy"):
            subworkflows.call(Path(str(helper["file"])))
        observed = subworkflows.inspect(Path(request["call_home"]), request["call_key"])
        assert observed["instance_id"] is None
    assert subworkflows.call(Path(str(helper["file"])))["outcome"] == "CHILD_SUCCEEDED"


def test_settled_replay_ignores_reused_controller_pid(
    helper: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    from millrace_plus.router import control

    result = subworkflows.call(Path(str(helper["file"])))
    assert result["outcome"] == "CHILD_SUCCEEDED"
    req = helper["request"]
    journal = records.Journal(Path(req["call_home"]), req["call_key"])
    saved = journal.load()
    # Recovered controllers can settle through an absent PID without a physical
    # join by this helper. The original actual controllers have already joined.
    saved["controllers"][-1]["joined"] = False
    monkeypatch.setattr(subworkflows, "alive", lambda _: False)
    assert subworkflows._controllers_closed(saved)
    journal.save(saved)
    before_journal = journal.path.read_bytes()
    manifest = Path(saved["output_manifest"])
    before_manifest = manifest.read_bytes()
    home = Path(saved["paths"]["state"])
    before_instance = control.inspect_instance(saved["instance_id"], state_home=home)
    before_run = control.inspect_run(
        saved["instance_id"], saved["run_id"], state_home=home
    )
    monkeypatch.setattr(subworkflows, "alive", lambda _: True)
    monkeypatch.setattr(subworkflows, "seconds_left", lambda _: -1)

    def no_launch(*args, **kwargs):
        raise AssertionError("settled replay must never launch another controller")

    monkeypatch.setattr(subworkflows, "_launch", no_launch)
    assert subworkflows.call(Path(str(helper["file"]))) == result
    assert journal.path.read_bytes() == before_journal
    assert manifest.read_bytes() == before_manifest
    after = journal.load()
    assert after["cancel_intent"] is None
    assert after["envelopes"]["cancel"] is None and after["cancellations"] == {}
    assert (
        control.inspect_instance(saved["instance_id"], state_home=home)
        == before_instance
    )
    assert (
        control.inspect_run(saved["instance_id"], saved["run_id"], state_home=home)
        == before_run
    )


@pytest.mark.parametrize("boundary", ["prepared", "create", "start", "starting"])
def test_cancel_before_start_prevents_later_dispatch(
    helper: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    import uuid

    request = helper["request"]
    if boundary == "starting":
        from millrace_plus import catalog

        source = Path(str(helper["definition"]))
        script = source / "step.py"
        script.write_text("import time;time.sleep(.5)\n" + script.read_text())
        pin = catalog.workflow_fingerprint(source)
        request["definition"].update(sha256=pin, version="sha256:" + pin)
        write_request(helper)
    cancel_id = uuid.uuid4().hex
    if boundary == "prepared":
        subworkflows.prepare(Path(str(helper["file"])))
        subworkflows.cancel(Path(request["call_home"]), request["call_key"], cancel_id)
    else:
        original = subworkflows._launch

        def inject(journal, operation, **kwargs):
            if boundary == "start" and operation == "start":
                subworkflows.cancel(
                    Path(request["call_home"]), request["call_key"], cancel_id
                )
            process = original(journal, operation, **kwargs)
            if (boundary == "create" and operation == "create") or (
                boundary == "starting" and operation == "start"
            ):
                assert journal.load()["phase"] == (
                    "creating" if operation == "create" else "starting"
                )
                subworkflows.cancel(
                    Path(request["call_home"]), request["call_key"], cancel_id
                )
            return process

        monkeypatch.setattr(subworkflows, "_launch", inject)
    result = subworkflows.call(Path(str(helper["file"])))
    assert result["outcome"] == "CHILD_CANCELLED", result
    saved = subworkflows.inspect(Path(request["call_home"]), request["call_key"])
    if boundary == "starting":
        assert saved["run_id"]
        assert sum(row["operation"] == "start" for row in saved["controllers"]) == 1
    else:
        assert saved["run_id"] is None
        assert not any(row["operation"] == "start" for row in saved["controllers"])
    assert saved["cancel_intent"]["request_id"] == cancel_id


@pytest.mark.parametrize("mode", ["forced", "graceful"])
def test_forced_parent_stop_retains_surviving_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import os
    import signal
    import subprocess
    import sys
    import uuid

    from subworkflow_support import wait_for

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(
        tmp_path / "forced",
        script=(
            "from pathlib import Path\nimport time,json\n"
            "Path('active').touch()\n"
            "while not Path('release').exists(): time.sleep(.05)\n"
            "print(json.dumps({'outcome':'READY'}))\n"
        ),
    )
    request = fixture["request"]
    env = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    helper_process = subprocess.Popen(
        [
            str(Path(sys.executable).parent / "millrace-plus"),
            "subworkflow",
            "call",
            "--request",
            str(fixture["file"]),
        ],
        cwd=fixture["project"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    home, key = Path(request["call_home"]), request["call_key"]

    def learned():
        try:
            saved = subworkflows.inspect(home, key)
        except records.SubworkflowError:
            return None
        return (
            saved
            if saved["run_id"] and (Path(saved["paths"]["project"]) / "active").exists()
            else None
        )

    try:
        saved = wait_for(learned)
        os.kill(
            helper_process.pid, signal.SIGKILL if mode == "forced" else signal.SIGTERM
        )
        stdout, stderr = helper_process.communicate(timeout=8)
        if mode == "graceful":
            assert helper_process.returncode == 0, stderr
            assert json.loads(stdout)["outcome"] == "CHILD_CANCELLED"
            final = subworkflows.inspect(home, key)
            assert final["phase"] == "terminal"
            assert final["observation"]["status"] == "cancelled"
            assert all(row["joined"] for row in final["controllers"])
            return
        after = subworkflows.reconcile(home, key)
        assert after["run_id"] == saved["run_id"]
        assert after["phase"] == "cleanup_required"
        assert after["observation"]["inflight_invocations"]
        subworkflows.cancel(home, key, uuid.uuid4().hex)

        def settled():
            view = subworkflows.reconcile(home, key)
            return view if view["phase"] == "terminal" else None

        closed = wait_for(settled)
        assert closed["observation"]["status"] == "cancelled"
        assert not closed["observation"]["inflight_invocations"]
    finally:
        if helper_process.poll() is None:
            helper_process.kill()
            helper_process.communicate(timeout=5)
        if "saved" in locals():
            # Public safety control is the only shutdown path for the child.
            remaining = subworkflows.inspect(home, key)
            if not subworkflows.closed(remaining["observation"]):
                subworkflows.cancel(home, key, uuid.uuid4().hex)


def test_record_and_result_limits_refuse_success(helper: dict[str, object]) -> None:
    from millrace_plus import subworkflow_process

    result = subworkflows.call(Path(str(helper["file"])))
    req = helper["request"]
    journal = records.Journal(Path(req["call_home"]), req["call_key"])
    saved = journal.load()
    prior = journal.path.read_bytes()
    saved["diagnostic"] = "x" * 1048576
    with pytest.raises(records.SubworkflowError, match="record_too_large"):
        journal.save(saved)
    assert journal.path.read_bytes() == prior
    assert result["outcome"] == "CHILD_SUCCEEDED"
    assert subworkflow_process.LIMIT == 65536


def test_detached_controller_drains_bounded_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess
    import sys

    from millrace_plus import subworkflow_process

    real = subprocess.Popen
    observed = []

    def peer(command, **kwargs):
        observed.append(kwargs["start_new_session"])
        return real(
            [
                sys.executable,
                "-c",
                "import os;os.write(1,b'a'*200000);os.write(2,b'b'*200000)",
            ],
            **kwargs,
        )

    monkeypatch.setattr(subworkflow_process.subprocess, "Popen", peer)
    process = subworkflow_process.ControlProcess(
        {}, tmp_path, tmp_path / "logs", "create", env={"PATH": "/usr/bin:/bin"}
    )
    process.start()
    assert process.finish(5)
    assert process.overflow and observed == [True]
    assert all(len(raw) == 65536 for raw in process.output.values())
    assert all(path.stat().st_size == 65536 for path in (tmp_path / "logs").iterdir())


def test_parent_launch_snapshot_and_child_validation(
    helper: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace_plus.router import control

    request = helper["request"]
    parent = request["parent"]
    public = control.inspect_instance(
        parent["instance_id"], state_home=Path(parent["state_home"])
    )
    prepared = subworkflows.prepare(Path(str(helper["file"])))
    assert prepared["parent_state_version"] == public["state_version"]
    assert prepared["settings"] == {
        k: public[k]
        for k in (
            "path",
            "env_allowlist",
            "host_paths",
            "runtime_defaults",
            "operator_policy",
        )
    }
    monkeypatch.setattr(control, "validate_revision", lambda *a, **k: {"valid": False})
    result = subworkflows.call(Path(str(helper["file"])))
    assert result["outcome"] == "CHILD_REFUSED"
    saved = subworkflows.inspect(Path(request["call_home"]), request["call_key"])
    assert saved["run_id"] is None
    assert not any(row["operation"] == "start" for row in saved["controllers"])


def test_distinct_explicit_cancel_ids_are_retained_without_deadline_extension(
    helper: dict[str, object],
) -> None:
    import uuid

    request = helper["request"]
    subworkflows.prepare(Path(str(helper["file"])))
    first, second = uuid.uuid4().hex, uuid.uuid4().hex
    one = subworkflows.cancel(Path(request["call_home"]), request["call_key"], first)
    two = subworkflows.cancel(Path(request["call_home"]), request["call_key"], second)
    assert set(two["cancellations"]) == {first, second}
    assert one["cleanup_deadline_at"] == two["cleanup_deadline_at"]
    assert two["cancel_intent"]["request_id"] == first


def test_result_overflow_retains_diagnostic_and_never_success(
    helper: dict[str, object],
) -> None:
    subworkflows.call(Path(str(helper["file"])))
    req = helper["request"]
    journal = records.Journal(Path(req["call_home"]), req["call_key"])
    saved = journal.load()
    # A long legal run can have many distinct invocation/writer facts even
    # though its domain transcripts are deliberately omitted.
    saved["observation"]["invocations"] = [
        {"id": f"{index:032x}", "status": "completed", "writer_state": "stopped"}
        for index in range(3500)
    ]
    saved["observation"]["completions"] = []
    with journal.lock():
        journal.save(saved)
    result = subworkflows._result(journal, "CHILD_SUCCEEDED")
    assert result["outcome"] == "CHILD_UNCERTAIN"
    final = journal.load()
    assert final["phase"] == "cleanup_required"
    assert final["diagnostic"] == "result_limit"
    assert Path(final["output_manifest"]).stat().st_size <= 262144


def test_preexisting_child_project_is_never_adopted(helper: dict[str, object]) -> None:
    saved = subworkflows.prepare(Path(str(helper["file"])))
    project = Path(saved["paths"]["project"])
    project.mkdir()
    sentinel = project / "foreign"
    sentinel.write_text("preserve")
    with pytest.raises(records.SubworkflowError, match="child_copy_not_owned"):
        subworkflows.call(Path(str(helper["file"])))
    assert sentinel.read_text() == "preserve"
    assert not Path(saved["paths"]["state"]).exists()
