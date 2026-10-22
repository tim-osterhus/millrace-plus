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
