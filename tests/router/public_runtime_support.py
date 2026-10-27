"""Owned real TCP OS/Core fixtures; no fake clients or provider processes."""

from __future__ import annotations

import hashlib
import os
import socket
import sys
import time
import uuid
from pathlib import Path

import pytest

from millrace_plus import catalog, subworkflows
from millrace_plus.subworkflow_records import canonical
from millrace_plus.subworkflow_v2 import closed, has_join_proof
from millrace_plus.workflow_os_client import WorkflowOSClient


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value) + b"\n")


def command_node(script: str, outcome: str, action: dict, *, args=(), timeout=10):
    return {
        "invoke": {
            "binding": "command",
            "cwd": ".",
            "timeout_seconds": timeout,
            "config": {"script": script, "args": list(args)},
        },
        "result": "json-v1",
        "outcomes": [outcome],
        "on": {outcome: action},
        "on_error": {"pause": {"reason": "offline_error"}},
        "on_unknown": {"pause": {"reason": "offline_unknown"}},
    }


def graph(nodes: dict, *, workspace=".", start="step") -> dict:
    return {
        "format": "millrace-router/v4",
        "limits": {"max_parallel_invocations": 1},
        "bindings": {
            "command": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": sys.executable,
                "config": {},
            }
        },
        "control_files": [],
        "planes": {"main": {"workspace": workspace, "start": start, "nodes": nodes}},
    }


def instance_hashes(home: Path, instance: str) -> dict[str, str]:
    """Hash quiescent files in place; never copy/open a live SQLite store."""
    root = home / "instances" / instance
    files = [root / "state.sqlite3", *sorted(root.glob("invocations/*/controls/**/*"))]
    assert files[0].is_file()
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
        if path.is_file()
    }


class PublicRuntime:
    """One owned foreground OS lifecycle and one genuinely active parent."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.home = self.root / "core"
        self.project = self.root / "parent-project"
        self.workflow = self.root / "parent-workflow"
        self.catalog = self.root / "catalog"
        self.target = self.root / "target"
        for path in (self.project, self.workflow, self.catalog, self.target):
            path.mkdir(parents=True)
        self.parent: str | None = None
        self.parent_run: str | None = None
        self.parent_calls: list[dict] = []
        self.children: list[tuple[Path, str]] = []
        self.service = None
        self.started_at = time.monotonic()

    def start(self) -> None:
        from millrace_os.config import ApplicationConfig
        from millrace_os.credentials import CredentialStore
        from millrace_os.service import Service

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = ApplicationConfig(
            config_dir=self.root / "os-config",
            state_dir=self.root / "os-state",
            credential_path=self.root / "os-config/credentials.json",
            port=port,
            router_state_homes={"proof": self.home},
        )
        self.service = Service(config)
        self.service.start()
        CredentialStore(config.credential_path).ensure()
        assert config.credential_path.stat().st_mode & 0o077 == 0
        profile = self.root / "profile.json"
        write_json(
            profile,
            {
                "format": "millrace-plus-workflow-os-profile/v1",
                "os_origin": self.service.browser_endpoint.rstrip("/"),
                "os_home": "proof",
                "os_credential_file": str(config.credential_path),
            },
        )
        self.profile = {
            "path": str(profile),
            "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        }
        self.client = WorkflowOSClient(self.profile, "proof")
        self.client.capabilities(deadline=time.monotonic() + 5)
        self._start_parent()

    def until(self, predicate, *, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError("bounded public runtime observation expired")

    def request(
        self, operation, *, instance=None, run=None, expected=None, payload=None
    ):
        return {
            "format": "millrace-router-request/v2",
            "operation": operation,
            "instance_id": instance,
            "run_id": run,
            "plane_id": None,
            "request_id": uuid.uuid4().hex,
            "expected_state_version": expected,
            "payload": payload or {},
        }

    def send_parent(self, envelope: dict, *, joined=False) -> dict:
        self.parent_calls.append(envelope)
        write_json(
            self.root / "parent-requests" / (envelope["request_id"] + ".json"), envelope
        )
        first = self.client.submit(envelope, deadline=time.monotonic() + 5)

        def complete():
            nonlocal first
            value, first = first, None
            value = value or self.client.observe(
                envelope, deadline=time.monotonic() + 2
            )
            receipt = value["core_receipt"]
            if receipt and receipt["status"] != "queued":
                if not joined or value["holder_joined"] is True:
                    write_json(
                        self.root
                        / "parent-observations"
                        / (envelope["request_id"] + ".json"),
                        value,
                    )
                    return receipt
            return None

        return self.until(complete)

    def _start_parent(self) -> None:
        script = self.workflow / "wait.py"
        script.write_text(
            "import json,time\nfrom pathlib import Path\n"
            "Path('started').write_text('active')\nend=time.monotonic()+110\n"
            "while not Path('release').exists() and time.monotonic()<end:\n"
            " time.sleep(.02)\n"
            "assert Path('release').exists(),'parent fixture deadline'\n"
            "print(json.dumps({'outcome':'READY','summary':'released','refs':[]}))\n"
        )
        definition = graph(
            {
                "step": command_node(
                    "wait.py",
                    "READY",
                    {"finish": "succeeded"},
                    timeout=115,
                )
            }
        )
        definition["control_files"] = ["wait.py"]
        write_json(self.workflow / "workflow.json", definition)
        created = self.send_parent(
            self.request(
                "create_instance",
                expected=0,
                payload={
                    "workflow_file": str(self.workflow / "workflow.json"),
                    "project_root": str(self.project),
                    "path": [str(Path(sys.executable).parent), "/usr/bin", "/bin"],
                    "env_allowlist": ["HOME", "PYTHONPATH"],
                    "host_paths": {"lad_target": str(self.target)},
                },
            ),
            joined=True,
        )
        assert created["status"] == "applied", created
        self.parent = created["instance_id"]
        started = self.send_parent(
            self.request(
                "start_run",
                instance=self.parent,
                expected=1,
                payload={"edit_mode": "locked"},
            )
        )
        assert started["status"] == "applied", started
        self.parent_run = started["run_id"]

        def active():
            run = self.client.read(
                self.parent, self.parent_run, deadline=time.monotonic() + 2
            )
            return (
                (self.project / "started").exists()
                and run["status"] == "running"
                and run["inflight_invocations"]
            )

        self.until(active)

    def call_request(self, definition: Path, *, key: str, text: str, call_home=None):
        digest = catalog.workflow_fingerprint(definition)
        request = {
            "format": "millrace-plus-subworkflow-request/v2",
            "call_key": key,
            "call_home": str(call_home or self.root / "calls"),
            "parent": {
                "instance_id": self.parent,
                "run_id": self.parent_run,
                "plane_id": "main",
            },
            "definition": {
                "selector": "local:" + definition.name,
                "version": "sha256:" + digest,
                "sha256": digest,
            },
            "output_ref": "results/" + key + ".json",
            "os_profile": self.profile,
            "observe_seconds": 15,
            "cleanup_seconds": 5,
            "inputs": {"request_markdown": text},
        }
        return request

    def run_child(self, request: dict, *, operation=None):
        path = self.project / (request["call_key"] + "-request.json")
        write_json(path, request)
        pair = (Path(request["call_home"]), request["call_key"])
        self.children.append(pair)
        for _ in range(3):
            assert time.monotonic() - self.started_at < 90, "active fixture deadline"
            result = operation(path) if operation else subworkflows.advance(path)
            if result["outcome"] != "CHILD_RUNNING":
                break
        assert result["outcome"] == "CHILD_SUCCEEDED", result
        return self.assert_child_closed(*pair)

    def assert_child_closed(self, home: Path, key: str):
        saved = subworkflows.inspect(home, key)
        assert saved["instance_id"] and saved["run_id"], saved["diagnostic"]
        run = self.client.read(
            saved["instance_id"], saved["run_id"], deadline=time.monotonic() + 2
        )
        assert closed(run), (run["status"], run["quiescent"])
        names = [
            name
            for name in ("create", "start")
            if saved["reservations"][name] != "unsent"
        ]
        names += [
            "cancel:" + name
            for name, row in saved["cancellations"].items()
            if row["reservation"] != "unsent"
        ]
        assert all(has_join_proof(saved, name) for name in names), names
        assert run["start_request_id"] == saved["envelopes"]["start"]["request_id"]
        write_json(
            self.root / "child-proof" / (key + ".json"),
            {
                "instance_id": saved["instance_id"],
                "run_id": saved["run_id"],
                "receipts": saved["receipts"],
                "os_observations": saved["os_observations"],
            },
        )
        return saved, run

    def close(self) -> None:
        if self.service is None:
            return
        errors = []
        (self.project / "release").write_text("owned fixture cleanup")
        for home, key in self.children:
            try:
                saved = subworkflows.reconcile(home, key)
                if saved["saved_result"]["outcome"] != "CHILD_SUCCEEDED":
                    subworkflows.cancel(home, key, uuid.uuid4().hex)
                    subworkflows.reconcile(home, key)
                self.assert_child_closed(home, key)
            except Exception as exc:
                errors.append(
                    {"call_key": key, "error": getattr(exc, "code", type(exc).__name__)}
                )
        if self.parent and self.parent_run:
            try:

                def settled():
                    view = self.client.read(
                        self.parent, self.parent_run, deadline=time.monotonic() + 2
                    )
                    return view if closed(view) else None

                self.until(settled)
                for envelope in self.parent_calls:

                    def joined(envelope=envelope):
                        value = self.client.observe(
                            envelope, deadline=time.monotonic() + 2
                        )
                        return value if value["holder_joined"] is True else None

                    value = self.until(joined)
                    write_json(
                        self.root
                        / "parent-observations"
                        / (envelope["request_id"] + ".json"),
                        value,
                    )
            except Exception as exc:
                errors.append(
                    {
                        "parent": self.parent,
                        "error": getattr(exc, "code", type(exc).__name__),
                    }
                )
        elif self.parent_calls:
            errors.append({"parent": "unknown_start_or_create_identity"})
        write_json(
            self.root / "cleanup.json",
            {
                "closure": "unknown" if errors else "publicly_proven",
                "errors": errors,
                "parent_instance_id": self.parent,
                "parent_run_id": self.parent_run,
            },
        )
        if errors:
            self.service.request_shutdown()
            self.service.wait_for_stop(10)
        else:
            self.service.stop()
        assert not errors, "Unknown fixture cleanup; retain owned cleanup.json/state"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    os_module = pytest.importorskip(
        "millrace_os", reason="optional actual public OS/Core proof"
    )
    import millrace

    import millrace_plus

    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    roots = {
        str(Path(module.__file__).resolve().parent.parent)
        for module in (millrace, millrace_plus)
    }
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(sorted(roots)))
    owned = PublicRuntime(tmp_path / "runtime")
    write_json(
        owned.root / "environment.json",
        {
            "python": sys.executable,
            "python_version": sys.version,
            "packages": {
                module.__name__: str(Path(module.__file__).resolve())
                for module in (millrace, millrace_plus, os_module)
            },
            "captured_pythonpath": sorted(roots),
        },
    )
    catalog.set_catalog_home(owned.catalog)
    try:
        owned.start()
        monkeypatch.chdir(owned.project)
        yield owned
    finally:
        owned.close()
