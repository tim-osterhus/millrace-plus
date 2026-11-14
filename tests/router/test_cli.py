"""Source CLI proofs: both Plus entrypoints wrap the same generic Core `main`.

`python -m millrace_plus.router ...` and the `millrace-plus router ...` console
function (`millrace_plus.router.cli:cli`) pass the explicit Plus resolver to
Core's CLI. Envelopes, receipts and exit codes stay Core's. The generic Core
module alone (no resolver) refuses the same Plus-backed instance. A missing or
non-candidate Core is refused with a JSON diagnostic, never a traceback.
Installed console scripts are proven separately in `test_distribution.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from plus_support import native_flow, pi_instance

from millrace_plus.router import cli

SOURCES = os.environ.get("PYTHONPATH", "")


def test_paperclip_cli_strict_error_and_dispatch(tmp_path, monkeypatch, capsys):
    config, assignment = tmp_path / "config.json", tmp_path / "assignment.json"
    config.write_text('{"format":1,"format":2}')
    assignment.write_text("{}")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "millrace-plus",
            "paperclip",
            "run",
            "--config",
            str(config),
            "--assignment",
            str(assignment),
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.cli()
    assert exit_info.value.code == 5
    output = capsys.readouterr()
    assert output.out == "" and json.loads(output.err)["code"] == "invalid_json"
    from millrace_plus.integrations import paperclip_bridge

    monkeypatch.setattr(
        paperclip_bridge,
        "run",
        lambda *_: {"status": "attention_required", "summary": "busy"},
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.cli()
    assert exit_info.value.code == 4
    assert json.loads(capsys.readouterr().out)["status"] == "attention_required"


def _run(*args: str, pythonpath: str = SOURCES, flags: tuple[str, ...] = ()):
    env = dict(os.environ, PYTHONPATH=pythonpath, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [sys.executable, *flags, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _lines(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_subworkflow_installed_console_and_strict_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from subworkflow_support import setup

    monkeypatch.setenv("HOME", str(tmp_path.resolve() / "user"))
    fixture = setup(tmp_path / "helper-cli")
    env = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    console = str(Path(sys.executable).parent / "millrace-plus")
    result = subprocess.run(
        [console, "subworkflow", "call", "--request", str(fixture["file"])],
        cwd=fixture["project"],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert json.loads(result.stdout)["outcome"] == "CHILD_SUCCEEDED"
    malformed = Path(str(fixture["file"]))
    malformed.write_text('{"format":1,"format":2}')
    refused = subprocess.run(
        [console, "subworkflow", "call", "--request", str(malformed)],
        cwd=fixture["project"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert refused.returncode == 2 and refused.stdout == ""
    assert json.loads(refused.stderr)["code"] == "invalid_json"
    assert "Traceback" not in refused.stderr


def test_module_and_console_wrappers_share_core_main(
    single_flow: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workflow, project, home, agent = native_flow(single_flow)
    instance = pi_instance(workflow, project, home, agent)
    common = ["--state-home", str(home), "--instance", instance]

    started = _run("-m", "millrace_plus.router", "start", *common)
    assert started.returncode == 0, started.stderr
    envelope, receipt = _lines(started.stdout)[0], _lines(started.stdout)[-1]
    request = envelope["request_envelope"]
    assert isinstance(request, dict) and request["operation"] == "start_run"
    assert receipt["status"] == "applied"
    run = str(receipt["run_id"])

    replayed = _run(
        "-m",
        "millrace_plus.router",
        "replay",
        "--state-home",
        str(home),
        "--envelope",
        json.dumps(request),
    )
    assert replayed.returncode == 0, replayed.stderr
    assert _lines(replayed.stdout)[-1] == receipt

    status = _run("-m", "millrace_plus.router", "status", *common, "--run", run)
    assert status.returncode == 0 and _lines(status.stdout)[-1]["status"] == "succeeded"

    # The console function is the same wrapper with the explicit namespace.
    monkeypatch.setattr(sys, "argv", ["millrace-plus", "router", "validate", *common])
    with pytest.raises(SystemExit) as exit_code:
        cli.cli()
    assert exit_code.value.code == 0
    assert _lines(capsys.readouterr().out)[-1]["valid"] is True
    monkeypatch.setattr(sys, "argv", ["millrace-plus", "validate", *common])
    with pytest.raises(SystemExit) as exit_code:
        cli.cli()
    assert exit_code.value.code == 2
    assert "usage: millrace-plus router" in capsys.readouterr().err

    # Generic Core CLI without the Plus resolver refuses before any write.
    database = home / "instances" / instance / "state.sqlite3"
    before = database.read_bytes()
    for command in (
        ["status", *common, "--run", run],
        ["cancel", *common, "--run", run],
    ):
        refused = _run("-m", "millrace.router.cli", *command)
        assert refused.returncode == 2
        assert _lines(refused.stderr)[-1]["code"] == "unsupported_adapter_contract"
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "case,code",
    [
        ("absent", "core_absent"),
        ("older", "core_contract_missing"),
        ("wrong", "core_contract_mismatch"),
    ],
)
def test_cli_refuses_non_candidate_core_with_diagnostic(
    tmp_path: Path, case: str, code: str
) -> None:
    stub = tmp_path / "stub"
    stub.mkdir()
    if case in {"older", "wrong"}:
        (stub / "millrace").mkdir()
        (stub / "millrace" / "__init__.py").write_text("")
    if case == "wrong":
        (stub / "millrace" / "router").mkdir()
        (stub / "millrace" / "router" / "__init__.py").write_text("")
        (stub / "millrace" / "router" / "execution.py").write_text(
            'EXECUTION_CONTRACT = "millrace-router-execution/v0"\n'
        )
    plus_src = str(Path(cli.__file__).resolve().parents[2])
    # -S keeps site-packages (including any preinstalled legacy runtime) out.
    result = _run(
        "-m",
        "millrace_plus.router",
        "instance",
        "list",
        pythonpath=os.pathsep.join([str(stub), plus_src]),
        flags=("-S",),
    )
    assert result.returncode == 2 and result.stdout == ""
    assert _lines(result.stderr)[-1]["code"] == code
    assert "Traceback" not in result.stderr


def test_lad_local_commands_without_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from test_lad_service import make_service

    service = make_service(tmp_path)
    request = tmp_path / "request.md"
    request.write_bytes(b"Original request\r\n")
    monkeypatch.setattr(
        cli, "require_core", lambda: pytest.fail("local intake imported Core")
    )

    def invoke(*args: str) -> tuple[int, object]:
        monkeypatch.setattr(sys, "argv", ["millrace-plus", "lad", *args])
        with pytest.raises(SystemExit) as result:
            cli.cli()
        output = capsys.readouterr()
        assert output.err == ""
        return result.value.code, json.loads(output.out)

    code, receipt = invoke(
        "submit",
        "--service-home",
        str(service.home),
        "--key",
        "first",
        "--request",
        str(request),
    )
    assert code == 0 and receipt["disposition"] == "accepted"
    code, state = invoke(
        "status", "--service-home", str(service.home), "--task", receipt["task_id"]
    )
    assert code == 0 and state["tasks"][0]["request_bytes"] == "Original request\r\n"
    request_id = "35fc1b35786c442ba6b633b3a38e84c7"
    code, cancelled = invoke(
        "cancel",
        "--service-home",
        str(service.home),
        "--task",
        receipt["task_id"],
        "--request-id",
        request_id,
    )
    assert code == 0 and cancelled["disposition"] == "cancelled-before-start"
    assert service.intake.tasks()[0]["claim"] is None
    assert not (service.home / "calls").exists()


def test_lad_cli_refuses_foundation_activation_without_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from test_lad_service import make_service, submit

    service = make_service(tmp_path)
    receipt = submit(service, "first")
    service.claim_next(writer_guard=lambda: None)  # Fixture guard only, no dispatch.
    for tail, expected in (
        (
            [
                "cancel",
                "--service-home",
                str(service.home),
                "--task",
                receipt["task_id"],
                "--request-id",
                "35fc1b35786c442ba6b633b3a38e84c7",
            ],
            "lifecycle_missing",
        ),
        (["init"], "usage_error"),
        (["decide"], "usage_error"),
    ):
        monkeypatch.setattr(sys, "argv", ["millrace-plus", "lad", *tail])
        with pytest.raises(SystemExit) as result:
            cli.cli()
        assert result.value.code == 2
        output = capsys.readouterr()
        assert output.out == "" and json.loads(output.err)["code"] == expected
        assert "Traceback" not in output.err
    assert service.intake.tasks()[0]["status"] == "claimed"


def test_lad_cli_missing_service_has_json_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "millrace-plus",
            "lad",
            "status",
            "--service-home",
            str(tmp_path),
        ],
    )
    with pytest.raises(SystemExit) as result:
        cli.cli()
    assert result.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and json.loads(output.err)["code"] == "io_error"


def test_subworkflow_advance_cli_uses_exact_request_surface(
    tmp_path, monkeypatch, capsys
):
    from millrace_plus import subworkflows

    selected = tmp_path / "request.json"
    calls = []

    def advance(file):
        calls.append(file)
        return {
            "outcome": "CHILD_RUNNING",
            "summary": "CHILD_RUNNING",
            "refs": ["result.json"],
        }

    monkeypatch.setattr(subworkflows, "advance", advance)
    assert cli._subworkflow_main(["advance", "--request", str(selected)]) == 0
    assert calls == [selected]
    assert json.loads(capsys.readouterr().out)["outcome"] == "CHILD_RUNNING"


@pytest.mark.parametrize("request_id", ["bad", "f" * 31, "G" * 32])
def test_subworkflow_cancel_invalid_id_precedes_journal_and_core(
    tmp_path, monkeypatch, capsys, request_id
):
    monkeypatch.setattr(cli, "require_core", lambda: pytest.fail("Core probed"))
    assert (
        cli._subworkflow_main(
            [
                "cancel",
                "--call-home",
                str(tmp_path / "missing"),
                "--call-key",
                "child",
                "--request-id",
                request_id,
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == "" and json.loads(output.err)["code"] == "invalid_id"
    assert not (tmp_path / "missing").exists()


def test_subworkflow_cancel_valid_v2_skips_core(tmp_path, monkeypatch, capsys):
    from millrace_plus import subworkflow_v2

    root = tmp_path / "calls" / "child"
    root.mkdir(parents=True)
    (root / "call.json").write_text('{"format":"millrace-plus-subworkflow-call/v2"}')
    monkeypatch.setattr(cli, "require_core", lambda: pytest.fail("Core probed"))
    calls = []

    def cancel(home, key, request_id):
        calls.append((home, key, request_id))
        return {"format": "millrace-plus-subworkflow-call/v2"}

    monkeypatch.setattr(subworkflow_v2, "cancel", cancel)
    assert (
        cli._subworkflow_main(
            [
                "cancel",
                "--call-home",
                str(root.parent),
                "--call-key",
                "child",
                "--request-id",
                "a" * 32,
            ]
        )
        == 0
    )
    assert calls == [(root.parent, "child", "a" * 32)]
    assert json.loads(capsys.readouterr().out)["format"].endswith("/v2")


def test_lad_init_and_decision_cli_are_thin_public_dispatch(
    tmp_path, monkeypatch, capsys
):
    from millrace_plus import lad_service_runtime

    calls = []

    def initialize(*args):
        calls.append(args)
        return {"activation_ready": True, "instance_id": "a" * 32}

    monkeypatch.setattr(lad_service_runtime, "initialize", initialize)
    values = [
        tmp_path / name
        for name in ("service", "target", "profile", "definition", "core")
    ]
    argv = ["millrace-plus", "lad", "init"]
    for name, value in zip(
        (
            "service-home",
            "target",
            "os-profile",
            "worker-definition",
            "core-state-home",
        ),
        values,
    ):
        argv.extend(["--" + name, str(value)])
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as result:
        cli.cli()
    assert result.value.code == 0
    assert calls == [tuple(values)]
    output = capsys.readouterr()
    assert json.loads(output.out)["activation_ready"] is True and output.err == ""


@pytest.mark.parametrize("plane", [None, "foreground", "learning"])
def test_continue_cli_forwards_explicit_plane_without_reading_opaque_notes(
    tmp_path, monkeypatch, capsys, plane
):
    from millrace_plus import lad_service, lad_service_runtime

    calls = []
    evidence = tmp_path / "notes.txt"
    evidence.write_text('{"operation":"resume_plane","plane_id":"notes-only"}')
    service = object()
    monkeypatch.setattr(lad_service, "Service", lambda home: service)

    class Runtime:
        def __init__(self, given):
            assert given is service

        def decide(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"disposition": "continuation-requested"}

    monkeypatch.setattr(lad_service_runtime, "Runtime", Runtime)
    argv = [
        "millrace-plus",
        "lad",
        "decide",
        "--service-home",
        str(tmp_path),
        "--task",
        "a" * 32,
        "--decision",
        "continue",
        "--request-id",
        "b" * 32,
        "--evidence",
        str(evidence),
    ]
    if plane is not None:
        argv.extend(["--resume-plane", plane])
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as result:
        cli.cli()
    assert result.value.code == 0
    assert calls == [
        (("a" * 32, "continue", "b" * 32, evidence), {"resume_plane": plane})
    ]
    assert (
        json.loads(capsys.readouterr().out)["disposition"] == "continuation-requested"
    )


@pytest.mark.parametrize(
    "decision,plane", [("close-failed", "foreground"), ("continue", "foreign")]
)
def test_resume_plane_cli_invalid_selection_refuses_before_service(
    tmp_path, monkeypatch, capsys, decision, plane
):
    from millrace_plus import lad_service

    monkeypatch.setattr(
        lad_service,
        "Service",
        lambda _: pytest.fail("invalid selection loaded service"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "millrace-plus",
            "lad",
            "decide",
            "--service-home",
            str(tmp_path),
            "--task",
            "a" * 32,
            "--decision",
            decision,
            "--request-id",
            "b" * 32,
            "--evidence",
            str(tmp_path / "notes.txt"),
            "--resume-plane",
            plane,
        ],
    )
    with pytest.raises(SystemExit) as result:
        cli.cli()
    assert result.value.code == 2
    assert capsys.readouterr().out == ""


def test_generic_subworkflow_persistent_marker_refuses_before_core(
    tmp_path, monkeypatch, capsys
):
    from millrace_plus.subworkflow_records import canonical

    home = tmp_path / "calls"
    root = home / "task"
    root.mkdir(parents=True)
    (root / "call.json").write_bytes(
        canonical({"format": "millrace-plus-lad-subworkflow-call/v1"})
    )
    monkeypatch.setattr(
        cli,
        "require_core",
        lambda: pytest.fail("generic persistent marker imported Core"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "millrace-plus",
            "subworkflow",
            "inspect",
            "--call-home",
            str(home),
            "--call-key",
            "task",
        ],
    )
    with pytest.raises(SystemExit) as result:
        cli.cli()
    assert result.value.code == 2
    assert (
        json.loads(capsys.readouterr().err)["code"]
        == "persistent_service_authority_required"
    )


def test_lad_parent_control_cli_forwards_one_request_file(
    tmp_path, monkeypatch, capsys
):
    from test_lad_service import make_service

    from millrace_plus import lad_service_runtime

    service = make_service(tmp_path)
    calls = []

    class Runtime:
        def __init__(self, selected):
            assert selected.home == service.home

        def parent_control(self, path):
            calls.append(path)
            return {"request_id": "a" * 32, "status": "pending", "holder_joined": False}

    monkeypatch.setattr(lad_service_runtime, "Runtime", Runtime)
    path = tmp_path / "parent-request.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "millrace-plus",
            "lad",
            "parent-control",
            "--service-home",
            str(service.home),
            "--request",
            str(path),
        ],
    )
    with pytest.raises(SystemExit) as result:
        cli.cli()
    assert result.value.code == 0 and calls == [path]
    assert json.loads(capsys.readouterr().out)["holder_joined"] is False


def test_lad_provider_cli_flags_are_thin_dispatch(tmp_path, monkeypatch, capsys):
    from millrace_plus import provider_wait

    calls = []

    def observe(project, **kwargs):
        calls.append(("observe", project, kwargs))
        return {"operation_id": "o", "operator_return_required": True}

    def due(project, **kwargs):
        calls.append(("due", project, kwargs))
        return {
            "operation_id": "o",
            "operator_return_required": True,
            "eligible": False,
        }

    monkeypatch.setattr(provider_wait, "observe_snapshot", observe)
    monkeypatch.setattr(provider_wait, "provider_due", due)
    base = ["millrace-plus", "lad"]
    observe_argv = base + [
        "provider-observe",
        "--project-root",
        str(tmp_path / "copy"),
        "--state-home",
        str(tmp_path / "state"),
        "--instance",
        "i1",
        "--run",
        "r1",
        "--plane",
        "foreground",
        "--operation",
        "a" * 64,
        "--invocation",
        "v1",
        "--expected-state-version",
        "7",
        "--service-home",
        str(tmp_path / "service"),
        "--task",
        "t1",
    ]
    due_argv = base + [
        "provider-due",
        "--project-root",
        str(tmp_path / "copy"),
        "--operation",
        "a" * 64,
        "--observation",
        "b" * 64,
        "--restoration",
        str(tmp_path / "restoration.json"),
    ]
    for argv in (observe_argv, due_argv):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as result:
            cli.cli()
        assert result.value.code == 0
        output = capsys.readouterr()
        assert json.loads(output.out)["operator_return_required"] is True
        assert output.err == ""
    (kind, project, kwargs), (due_kind, due_project, due_kwargs) = calls
    assert (kind, due_kind) == ("observe", "due")
    assert project == due_project == tmp_path / "copy"
    now = kwargs.pop("observed_at")
    assert now.utcoffset().total_seconds() == 0
    assert kwargs == {
        "state_home": tmp_path / "state",
        "instance_id": "i1",
        "run_id": "r1",
        "plane_id": "foreground",
        "operation_id": "a" * 64,
        "invocation_id": "v1",
        "expected_state_version": 7,
        "service_home": tmp_path / "service",
        "task": "t1",
    }
    assert due_kwargs.pop("now").utcoffset().total_seconds() == 0
    assert due_kwargs == {
        "operation_id": "a" * 64,
        "observation_id": "b" * 64,
        "restoration": tmp_path / "restoration.json",
    }


def test_lad_provider_cli_refusal_is_json_exit_2(tmp_path, monkeypatch, capsys):
    from millrace_plus import provider_wait

    def refuse(project, **kwargs):
        raise provider_wait.ProviderWaitError("provider_observation_required")

    monkeypatch.setattr(provider_wait, "provider_due", refuse)
    argv = [
        "millrace-plus",
        "lad",
        "provider-due",
        "--project-root",
        str(tmp_path),
        "--operation",
        "a" * 64,
        "--observation",
        "b" * 64,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as result:
        cli.cli()
    assert result.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["code"] == "provider_observation_required"
    observe = [
        "millrace-plus",
        "lad",
        "provider-observe",
        "--project-root",
        str(tmp_path),
        "--state-home",
        str(tmp_path),
        "--instance",
        "i",
        "--run",
        "r",
        "--operation",
        "a" * 64,
        "--invocation",
        "v",
        "--expected-state-version",
        "1",
    ]
    for extra in (
        ["--plane", "learning"],
        ["--plane", "foreground", "--task", "t1"],
        [],
    ):
        monkeypatch.setattr(sys, "argv", observe + extra)
        with pytest.raises(SystemExit) as result:
            cli.cli()
        assert result.value.code == 2
        assert json.loads(capsys.readouterr().err)["code"] == "usage_error"
