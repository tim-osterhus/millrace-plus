"""Real active-run stop journeys at the published baseline capture gate.

The gate holds an active paused run, not an inflight native/helper writer. The
fixture changes no production graph, request, handle, writer read or cleanup.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from millrace_plus.demo import coordinator

# Sum the selected ready/detect/baseline/operator/drain observation safeguards.
_JOURNEY_OBSERVATION = 10 + 15 + 15 + 30 + 15
_RUNNER = """
import json
import sys
import time
from pathlib import Path
from millrace_plus.demo.coordinator import Coordinator, DemoOptions
from millrace_plus.demo.retention import write_private
from millrace_plus.demo.contracts import canonical_bytes

fixture = Path(sys.argv[1])
class AtCaptureGate(Coordinator):
    def _check_stop(self):
        super()._check_stop()
        if self.session_bytes is not None and not any(
            request["operation"] in {"resume_run", "resume_plane"}
            for request in self.requests
        ):
            write_private(fixture / "gate.json", canonical_bytes({
                "root": str(self.root), "endpoint": self.handle.endpoint,
                "instance_id": self.instance, "run_id": self.run_id,
            }))
            while True:
                super()._check_stop()
                time.sleep(0.02)

raise SystemExit(AtCaptureGate(DemoOptions(
    output=fixture / "results", no_open=True, interactive=True,
), {}).execute())
"""


def _source_environment():
    """Match source origins; installed proof needs no path override."""
    import millrace
    import millrace_os

    import millrace_plus

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    roots = [
        Path(module.__file__).resolve().parent.parent
        for module in (millrace, millrace_plus, millrace_os)
    ]
    if any(not root.is_relative_to(Path(sys.prefix)) for root in roots):
        env["PYTHONPATH"] = os.pathsep.join(map(str, roots))
    return env


def _wait_gate(process, path):
    deadline = time.monotonic() + _JOURNEY_OBSERVATION
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output, error = process.communicate()
            pytest.fail(
                f"coordinator exited before gate: {process.returncode} {output} {error}"
            )
        if path.exists():
            return json.loads(path.read_bytes())
        time.sleep(0.02)
    pytest.fail("baseline gate was not observed within selected startup safeguards")


@pytest.mark.parametrize("source", ["finish_request", "terminal_enter", "sigint"])
def test_real_active_run_stop_at_published_baseline(tmp_path, source):
    runner = tmp_path / "runner.py"
    runner.write_text(_RUNNER)
    with subprocess.Popen(
        [sys.executable, "-B", str(runner), str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_source_environment(),
    ) as process:
        try:
            gate = _wait_gate(process, tmp_path / "gate.json")
            root = Path(gate["root"])
            if source == "finish_request":
                bearer = json.loads((root / "os-config/credentials.json").read_bytes())[
                    "token"
                ]
                with httpx.Client(
                    base_url=gate["endpoint"], trust_env=False
                ) as browser:
                    nonce = browser.post(
                        "/api/v1/auth/bootstrap",
                        json={"recovery": False},
                        headers={"Authorization": "Bearer " + bearer},
                    ).json()["nonce"]
                    csrf = browser.post(
                        "/api/v1/auth/exchange",
                        json={"nonce": nonce, "recovery": False},
                        headers={"Origin": gate["endpoint"]},
                    ).json()["csrf"]
                    response = browser.post(
                        "/api/v1/demo/finish",
                        json={},
                        headers={"Origin": gate["endpoint"], "X-CSRF-Token": csrf},
                    )
                    assert response.status_code == 200
                    assert response.json()["requested"] is True
            elif source == "terminal_enter":
                process.stdin.write("\n")
                process.stdin.flush()
            else:
                process.send_signal(signal.SIGINT)
            output, error = process.communicate(timeout=_JOURNEY_OBSERVATION)
            assert process.returncode == 130, (output, error)
        finally:
            if process.poll() is None:
                # Interrupt this exact fixture parent through its supported signal path.
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=_JOURNEY_OBSERVATION)
    record_path = next((tmp_path / "results").glob("demo-*/execution.json"))
    record = json.loads(record_path.read_bytes())
    report = json.loads(record_path.with_name("report.json").read_bytes())
    assert record["completion"] == report["completion"] == "cancelled"
    assert report["demo_id"] == record["demo_id"]
    assert record["stop_source"] == source
    assert record["writers_stopped"] and record["child_exited"]
    assert (
        record["shutdown"]["workers_drained"] and record["shutdown"]["service_stopped"]
    )
    cancelled = [
        request
        for request in record["requests"]
        if request["operation"] == "cancel_run"
    ]
    assert len(cancelled) == 1 and cancelled[0]["status"] == "applied"
    assert cancelled[0]["holder_joined"] is True
    assert {node["node_id"] for node in record["nodes"]} == {"detect", "baseline"}
    assert all(node["writer"] == "stopped" for node in record["nodes"])
    assert all(
        request["operation"] not in {"resume_run", "resume_plane"}
        for request in record["requests"]
    )
    assert record["accepted_outputs"][0]["reference"]["output_id"] == "baseline"
    assert report["baseline"] == json.loads(
        record_path.with_name("baseline.json").read_bytes()
    )
    assert record["retention"]["cleanup"] == "removed" and not root.exists()


def test_real_active_run_parent_channel_eof_drains_without_fallback(tmp_path):
    """EOF pauses/drains an active run; it is not a user cancellation or exit 130."""
    owner = coordinator.Coordinator(
        coordinator.DemoOptions(output=tmp_path, no_open=True), {}
    )
    try:
        owner._start()
        observed = owner._observe()
        owner._await_join(observed)
        summary = owner._summary()
        assert summary["status"] == "paused"
        assert summary["planes"]["main"]["pause_reason"] == "baseline_saved"
        owner.handle.close()  # Real private-channel EOF; no mocked lifecycle handle.
        assert owner.handle.wait()
        shutdown = owner.handle.shutdown_result
        summary = owner._summary()
        rows = owner._invocations()
        assert shutdown["workers_drained"] and shutdown["service_stopped"]
        assert shutdown["child_exited"] and shutdown["cancel_requested"] is False
        assert summary["quiescent"] and not summary["inflight_invocations"]
        assert {row["node_id"] for row in rows} == {"detect", "baseline"}
        assert all(row["writer"]["effective"] == "stopped" for row in rows)
        assert all(
            request["operation"] not in {"resume_run", "resume_plane"}
            for request in owner.requests
        )
    finally:
        if owner.handle is not None:
            owner.handle.close()
            assert owner.handle.wait()
        if owner.root is not None:
            # Reuse normal export/closure rules; EOF already closed this handle.
            owner.error = "parent_channel_eof"
            owner._finish()


_INFLIGHT_RUNNER = """
import sys
import time
from pathlib import Path
from millrace_plus.demo import coordinator
from millrace_plus.demo.contracts import canonical_bytes
from millrace_plus.demo.retention import write_private

fixture = Path(sys.argv[1])
original_materialize = coordinator.materialize

def controlled_detect(workspace, **kwargs):
    workflow = original_materialize(workspace, **kwargs)
    stage = workspace / "scripts/stage.py"
    original = stage.read_text()
    prefix = "import sys, time\\nfrom pathlib import Path\\n"
    prefix += "if sys.argv[1] == 'detect':\\n"
    prefix += (
        "    Path(" + repr(str(fixture / "inflight.json")) + ").write_text('{}')\\n"
    )
    prefix += "    while True: time.sleep(0.1)\\n"
    stage.write_text(prefix + original)
    return workflow

coordinator.materialize = controlled_detect
class InflightCoordinator(coordinator.Coordinator):
    def _start(self):
        super()._start()
        write_private(fixture / "ready.json", canonical_bytes({"root": str(self.root)}))

raise SystemExit(InflightCoordinator(coordinator.DemoOptions(
    output=fixture / "results", no_open=True, interactive=True,
), {}).execute())
"""


def test_real_inflight_command_enter_cancels_drains_and_never_falls_back(tmp_path):
    """The controlled detect command keeps its existing 15-second node deadline."""
    runner = tmp_path / "inflight-runner.py"
    runner.write_text(_INFLIGHT_RUNNER)
    with subprocess.Popen(
        [sys.executable, "-B", str(runner), str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_source_environment(),
    ) as process:
        try:
            ready = _wait_gate(process, tmp_path / "ready.json")
            _wait_gate(process, tmp_path / "inflight.json")
            root = Path(ready["root"])
            process.stdin.write("\n")
            process.stdin.flush()
            output, error = process.communicate(timeout=_JOURNEY_OBSERVATION)
            assert process.returncode == 130, (output, error)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=_JOURNEY_OBSERVATION)
    record_path = next((tmp_path / "results").glob("demo-*/execution.json"))
    record = json.loads(record_path.read_bytes())
    assert (
        record["completion"] == "cancelled"
        and record["stop_source"] == "terminal_enter"
    )
    assert record["writers_stopped"] and record["child_exited"]
    assert (
        record["shutdown"]["workers_drained"] and record["shutdown"]["service_stopped"]
    )
    assert {node["node_id"] for node in record["nodes"]} == {"detect"}
    assert record["nodes"][0]["writer"] == "stopped"
    cancelled = [
        request
        for request in record["requests"]
        if request["operation"] == "cancel_run"
    ]
    assert len(cancelled) == 1 and cancelled[0]["status"] == "applied"
    assert cancelled[0]["holder_joined"] is True
    assert record["retention"]["cleanup"] == "removed" and not root.exists()
