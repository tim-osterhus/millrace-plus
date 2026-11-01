"""Run exactly the operator-configured checks with finite deadlines and receipts."""

from __future__ import annotations

import hashlib
import os
import selectors
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .cicd_records import CHECK, CicdError, validate_check
from .cicd_source import write_exact
from .local_intake import canonical

__all__ = ["BATCH", "run_batch", "check_env"]

BATCH = "millrace-plus-cicd-check-batch/v1"
LOG_LIMIT = 65536
ROOT_GRACE = 1.0
EOF_GRACE = 2.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def check_env() -> dict[str, str]:
    """Checks inherit only PATH and HOME from the step; never the full environment."""
    env = {"PATH": os.environ.get("PATH", os.defpath)}
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    return env


def _ref(workspace: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _stop_root(process: subprocess.Popen[bytes]) -> bool:
    """Stop and reap only the check root. Returns whether the root was reaped.

    The check shares its step's Core-owned process group, so signalling that
    group would also stop the step. Core's own group cleanup owns everything
    else; this function never claims descendants closed.
    """
    for stop in (process.terminate, process.kill):
        if process.poll() is not None:
            return True
        stop()
        try:
            process.wait(ROOT_GRACE)
            return True
        except subprocess.TimeoutExpired:
            continue
    return process.poll() is not None


def _drain(
    selector: selectors.BaseSelector,
    output: dict[str, bytearray],
    seconds: float,
) -> str | None:
    """Read until both streams reach EOF, the limit, or `seconds` pass."""
    end = time.monotonic() + seconds
    while selector.get_map():
        remaining = end - time.monotonic()
        if remaining <= 0:
            return None
        for key, _ in selector.select(min(0.1, remaining)):
            try:
                chunk = os.read(key.fd, 65536)
            except BlockingIOError:
                continue
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            buffer = output[key.data]
            overflow = len(buffer) + len(chunk) > LOG_LIMIT
            buffer.extend(chunk[: max(0, LOG_LIMIT - len(buffer))])
            if overflow:
                return "overflow"  # Overflowing output cannot pass.
    return "eof"


def _run(
    argv: list[str], cwd: Path, env: dict[str, str], seconds: int
) -> dict[str, object]:
    """Run one check inside the caller's Core-owned process group.

    No new session or group is created: Core's cancellation and timeout of the
    `checks` step reach the check and anything it starts. Locally, a check is
    joined only when its root is reaped and both output streams reached EOF.
    A descendant still holding output leaves the receipt `uncertain`.
    """
    deadline = time.monotonic() + seconds
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return {
            "exit_code": None,
            "stdout": b"",
            "stderr": b"start_failed\n",
            "cause": "start_failed",
            "joined": False,
        }
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    cause = None
    try:
        for name in output:
            pipe = getattr(process, name)
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, name)
        while cause is None:
            finished = _drain(selector, output, min(0.1, deadline - time.monotonic()))
            if finished == "overflow":
                cause = "overflow"
            elif finished == "eof":
                # Output closed; wait for the root itself within the deadline.
                try:
                    process.wait(max(0.0, deadline - time.monotonic()))
                    break
                except subprocess.TimeoutExpired:
                    cause = "timeout"
            elif process.poll() is not None:
                # The root exited; give inherited output a short EOF grace.
                if _drain(selector, output, EOF_GRACE) != "eof":
                    cause = "output_held_open"
                break
            elif time.monotonic() >= deadline:
                cause = "timeout"
    finally:
        # Also runs on interruption (the step's SIGTERM handler raises).
        reaped = _stop_root(process)
    eof = cause is None or _drain(selector, output, EOF_GRACE) == "eof"
    for name in output:
        getattr(process, name).close()
    selector.close()
    return {
        "exit_code": process.returncode,
        "stdout": bytes(output["stdout"]),
        "stderr": bytes(output["stderr"]),
        "cause": cause,
        "joined": reaped and eof and cause != "output_held_open",
    }


def _status(result: dict[str, object], expected: int) -> str:
    if not result["joined"] or result["cause"] in {"start_failed", "overflow"}:
        return "uncertain"  # Unknown closure or output never passes.
    if result["cause"] == "timeout":
        return "timeout"
    return "passed" if result["exit_code"] == expected else "failed"


def run_batch(
    config: dict[str, object],
    target: Path,
    workspace: Path,
    directory: Path,
    event_id: str,
    source_sha256: str,
    source_digest: Callable[[], str],
) -> tuple[str, list[dict[str, object]]]:
    """Run each configured check once; stop at the first uncertain receipt.

    Returns PASS, FAIL or UNCERTAIN with exact receipt refs. A check passes only
    with the expected exit, a confirmed joined process group and unchanged source.
    """
    refs: list[dict[str, object]] = []
    statuses: list[str] = []
    env = check_env()
    for item in cast(list[dict[str, object]], config["check_commands"]):
        check_id = str(item["id"])
        argv = [str(value) for value in cast(list[object], item["argv"])]
        cwd = target if item["cwd"] == "." else target / str(item["cwd"])
        started = _now()
        stdout = stderr = b""
        exit_code: int | None = None
        if (
            not cwd.is_dir()
            or any(part.is_symlink() for part in (cwd, *cwd.parents) if part != target)
            or shutil.which(argv[0], path=env["PATH"]) is None
        ):
            status = "uncertain"
            stderr = b"check_not_runnable\n"
        else:
            result = _run(argv, cwd, env, cast(int, item["timeout_seconds"]))
            stdout = cast(bytes, result["stdout"])
            stderr = cast(bytes, result["stderr"])
            exit_code = cast(int | None, result["exit_code"])
            status = _status(result, cast(int, item["expected_exit_code"]))
        finished = _now()
        if status == "passed" and source_digest() != source_sha256:
            status = "uncertain"
            stderr += b"\nsource_changed_during_check\n"
        out_path = directory / (check_id + ".stdout")
        err_path = directory / (check_id + ".stderr")
        write_exact(out_path, stdout[:LOG_LIMIT], "check_replay_conflict")
        write_exact(err_path, stderr[:LOG_LIMIT], "check_replay_conflict")
        receipt = {
            "format": CHECK,
            "event_id": event_id,
            "check_id": check_id,
            "argv": argv,
            "cwd": str(item["cwd"]),
            "source_sha256": source_sha256,
            "started_at": started,
            "finished_at": finished,
            "exit_code": exit_code,
            "status": status,
            "stdout_ref": _ref(workspace, out_path),
            "stderr_ref": _ref(workspace, err_path),
        }
        validate_check(receipt)
        path = directory / (check_id + ".json")
        write_exact(path, canonical(receipt) + b"\n", "check_replay_conflict")
        refs.append(_ref(workspace, path))
        statuses.append(status)
        if status == "uncertain":
            break
    if "uncertain" in statuses:
        return "UNCERTAIN", refs
    if any(status != "passed" for status in statuses):
        return "FAIL", refs
    if len(statuses) != len(cast(list[object], config["check_commands"])):
        raise CicdError("check_batch_incomplete")
    return "PASS", refs
