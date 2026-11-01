"""Configured checks: real RED/GREEN, finite deadlines and owned process closure."""

from __future__ import annotations

import json
import os
import py_compile
import signal
import sys
import time
from pathlib import Path

import pytest
from cicd_support import FIXED, config, make_target, run_check

from millrace_plus.cicd_checks import run_batch
from millrace_plus.cicd_records import CicdError, digest, validate_config
from millrace_plus.cicd_source import snapshot

EVENT = "a" * 64


def _check(check_id: str, code: str, timeout: int = 30, expected: int = 0) -> dict:
    return {
        "id": check_id,
        "argv": [sys.executable, "-c", code],
        "cwd": ".",
        "timeout_seconds": timeout,
        "expected_exit_code": expected,
    }


def _batch(
    tmp_path: Path, target: Path, number: int, checks: list[dict] | None = None
) -> tuple[str, list[dict[str, object]], Path]:
    data = config(target, tmp_path / "peer")
    if checks is not None:
        data["check_commands"] = checks
    selected = validate_config(json.dumps(data).encode())
    workspace = tmp_path / "ws"
    directory = workspace / "checks" / str(number)
    source = digest(snapshot(target))
    status, refs = run_batch(
        selected,
        target,
        workspace,
        directory,
        EVENT,
        source,
        lambda: digest(snapshot(target)),
    )
    return status, refs, workspace


def _receipt(workspace: Path, ref: dict[str, object]) -> dict[str, object]:
    return json.loads((workspace / str(ref["path"])).read_text())


def test_real_check_red_to_green(tmp_path: Path) -> None:
    target, _ = make_target(tmp_path)
    source = target / "src" / "calc.py"
    original = source.stat()
    cached = Path(
        py_compile.compile(
            str(source),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
        )
    )
    cache_bytes = cached.read_bytes()
    assert int.from_bytes(cache_bytes[4:8], "little") == 0
    assert run_check(target).returncode == 1
    status, refs, workspace = _batch(tmp_path, target, 1)
    assert status == "FAIL" and len(refs) == 1
    receipt = _receipt(workspace, refs[0])
    assert (receipt["status"], receipt["exit_code"]) == ("failed", 1)
    assert receipt["argv"] == [sys.executable, "checks/check_calc.py"]
    assert receipt["cwd"] == "."
    stdout = workspace / str(receipt["stdout_ref"]["path"])
    assert b"returned -1" in stdout.read_bytes()
    source.write_text(FIXED)
    # Force the collision; do not invalidate or remove the old defect cache.
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    repaired = source.stat()
    assert repaired.st_size == original.st_size == int.from_bytes(
        cache_bytes[12:16], "little"
    )
    assert int(repaired.st_mtime) & 0xFFFFFFFF == int.from_bytes(
        cache_bytes[8:12], "little"
    )
    assert source.read_text() == FIXED and cached.read_bytes() == cache_bytes
    assert run_check(target).returncode == 0
    status, refs, workspace = _batch(tmp_path, target, 2)
    assert status == "PASS"
    receipt = _receipt(workspace, refs[0])
    assert (receipt["status"], receipt["exit_code"]) == ("passed", 0)
    assert receipt["argv"] == [sys.executable, "checks/check_calc.py"]
    assert receipt["cwd"] == "."
    stdout = workspace / str(receipt["stdout_ref"]["path"])
    assert b"add(2, 3) == 5" in stdout.read_bytes()
    assert cached.read_bytes() == cache_bytes


@pytest.mark.parametrize(
    ("code", "timeout", "expected", "status", "batch"),
    [
        ("import time; time.sleep(5)", 1, 0, "timeout", "FAIL"),
        ("print('x' * 70000)", 30, 0, "uncertain", "UNCERTAIN"),
        ("import sys; sys.exit(3)", 30, 3, "passed", "PASS"),
        ("import sys; sys.exit(3)", 30, 0, "failed", "FAIL"),
    ],
    ids=["timeout", "overflow", "expected-nonzero", "failed"],
)
def test_check_failure_timeout_overflow_and_closure(
    tmp_path: Path, code: str, timeout: int, expected: int, status: str, batch: str
) -> None:
    target, _ = make_target(tmp_path)
    later = _check("later", "pass")
    result, refs, workspace = _batch(
        tmp_path, target, 1, [_check("first", code, timeout, expected), later]
    )
    assert result == batch
    assert _receipt(workspace, refs[0])["status"] == status
    # An uncertain receipt stops the batch; other outcomes run every check.
    assert len(refs) == (1 if status == "uncertain" else 2)


def test_missing_check_executable_cannot_pass(tmp_path: Path) -> None:
    target, _ = make_target(tmp_path)
    missing = dict(_check("missing", "pass"), argv=["/nonexistent/tool"])
    result, refs, workspace = _batch(tmp_path, target, 1, [missing])
    assert result == "UNCERTAIN"
    receipt = _receipt(workspace, refs[0])
    assert (receipt["status"], receipt["exit_code"]) == ("uncertain", None)


def test_check_source_drift(tmp_path: Path) -> None:
    target, _ = make_target(tmp_path)
    mutate = "open('src/calc.py', 'a').write('# changed by check\\n')"
    result, refs, workspace = _batch(tmp_path, target, 1, [_check("drift", mutate)])
    assert result == "UNCERTAIN"
    assert _receipt(workspace, refs[0])["status"] == "uncertain"
    caches = (
        "import pathlib; "
        "pathlib.Path('src/__pycache__').mkdir(exist_ok=True); "
        "pathlib.Path('src/__pycache__/x.pyc').write_bytes(b'x'); "
        "pathlib.Path('.pytest_cache').mkdir(exist_ok=True); "
        "pathlib.Path('.pytest_cache/v').write_text('x')"
    )
    result, refs, workspace = _batch(tmp_path, target, 2, [_check("cache", caches)])
    assert result == "PASS"


def test_receipts_are_immutable_replay(tmp_path: Path) -> None:
    target, _ = make_target(tmp_path)
    status, refs, workspace = _batch(tmp_path, target, 1)
    first = (workspace / str(refs[0]["path"])).read_bytes()
    (target / "src" / "calc.py").write_text(FIXED)
    with pytest.raises(CicdError) as conflict:
        _batch(tmp_path, target, 1)
    assert conflict.value.code == "check_replay_conflict"
    assert (workspace / str(refs[0]["path"])).read_bytes() == first
    assert status == "FAIL"


def _reap_recorded(path: Path) -> None:
    """Kill a test-owned descendant recorded by PID, if it is still alive."""
    if path.exists():
        try:
            os.kill(int(path.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_check_stays_in_callers_process_group(tmp_path: Path) -> None:
    """F1: the check never leaves the Core-owned command group of its step."""
    target, _ = make_target(tmp_path)
    record = tmp_path / "pgid.txt"
    code = (
        "import os, pathlib; "
        f"pathlib.Path({str(record)!r}).write_text(str(os.getpgid(0)))"
    )
    result, _, _ = _batch(tmp_path, target, 1, [_check("group", code)])
    assert result == "PASS"
    assert int(record.read_text()) == os.getpgid(0)


def test_live_descendant_holding_output_is_uncertain(tmp_path: Path) -> None:
    """F1: a root exit with a live descendant is never reported as joined."""
    target, _ = make_target(tmp_path)
    child_pid = tmp_path / "descendant.pid"
    holder = (
        "import os, pathlib, time; "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    code = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {holder!r}]); "
        "print('root done')"
    )
    started = time.monotonic()
    try:
        result, refs, workspace = _batch(
            tmp_path, target, 1, [_check("held", code, 60), _check("later", "pass")]
        )
    finally:
        _reap_recorded(child_pid)
    assert result == "UNCERTAIN"
    receipt = _receipt(workspace, refs[0])
    assert receipt["status"] == "uncertain"
    assert len(refs) == 1  # The batch stops; the next check never runs.
    # Detected from the still-open output, well before the 60 s check deadline.
    assert time.monotonic() - started < 30


def test_timeout_stops_root_and_reports_open_descendant(tmp_path: Path) -> None:
    """F1: local timeout cleanup reports a surviving output holder truthfully."""
    target, _ = make_target(tmp_path)
    child_pid = tmp_path / "descendant.pid"
    holder = (
        "import os, pathlib, signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {holder!r}]); "
        "time.sleep(60)"
    )
    try:
        result, refs, workspace = _batch(tmp_path, target, 1, [_check("slow", code, 2)])
    finally:
        _reap_recorded(child_pid)
    assert result == "UNCERTAIN"
    assert _receipt(workspace, refs[0])["status"] == "uncertain"
