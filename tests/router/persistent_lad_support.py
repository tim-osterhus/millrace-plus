"""Pure fake OS and clock fixtures; no Core/OS runtime or native worker imports."""

from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

from millrace_plus import catalog
from millrace_plus.subworkflow_records import canonical, public_request_digest

PARENT = "1" * 32
CHILD = "2" * 32
RUN = "3" * 32
INVOCATION = "4" * 32


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def capabilities(home="local"):
    return {
        "contract": "millrace-os-router-observation-capabilities/v1",
        "home": home,
        "observation_contracts": ["millrace-os-router-call/v2"],
        "holder_joined_supported": True,
    }


def receipt(envelope, *, status="applied", instance=CHILD, run=None):
    return {
        "request_id": envelope["request_id"],
        "plane_id": None,
        "operation": envelope["operation"],
        "request_digest": public_request_digest(envelope),
        "instance_id": instance,
        "run_id": run,
        "status": status,
    }


def observation(
    envelope, *, joined=True, core=None, home="local", source="os_owned_call"
):
    return {
        "contract": "millrace-os-router-call/v2",
        "home": home,
        "request_id": envelope["request_id"],
        "request_digest": public_request_digest(envelope),
        "instance_id": core.get("instance_id") if core else envelope["instance_id"],
        "run_id": core.get("run_id") if core else envelope["run_id"],
        "source": source,
        "phase": "call_returned" if joined else "call_active",
        "core_receipt": core,
        "holder_joined": joined,
        "observed_at": "2026-10-26T00:00:00Z",
    }


def setup(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(root / "user"))
    parent, workflow = root / "parent", root / "parent-workflow"
    parent.mkdir()
    workflow.mkdir()
    definition = root / "catalog" / "child"
    definition.mkdir(parents=True)
    graph = {
        "format": "millrace-router/v4",
        "limits": {},
        "bindings": {
            "python": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": sys.executable,
                "config": {},
            }
        },
        "control_files": ["step.py"],
        "defaults": {"timeout_seconds": 10},
        "planes": {
            "main": {
                "workspace": ".",
                "start": "step",
                "nodes": {
                    "step": {
                        "invoke": {
                            "binding": "python",
                            "cwd": ".",
                            "config": {"script": "step.py", "args": []},
                        },
                        "result": "json-v1",
                        "outcomes": ["DONE"],
                        "on": {"DONE": {"finish": "succeeded"}},
                        "on_error": {"pause": {"reason": "error", "resume": "step"}},
                        "on_unknown": {
                            "pause": {"reason": "unknown", "resume": "step"}
                        },
                    }
                },
            }
        },
    }
    (definition / "workflow.json").write_bytes(canonical(graph) + b"\n")
    (definition / "step.py").write_text(
        'print(\'{"outcome":"DONE","summary":"fixture","refs":[]}\')\n'
    )
    sha = catalog.workflow_fingerprint(definition)
    credential = root / "credential.json"
    credential.write_bytes(canonical({"token": "a" * 32}))
    credential.chmod(0o600)
    profile = root / "profile.json"
    profile.write_bytes(
        canonical(
            {
                "format": "millrace-plus-workflow-os-profile/v1",
                "os_origin": "http://127.0.0.1:8731",
                "os_home": "local",
                "os_credential_file": str(credential),
            }
        )
        + b"\n"
    )
    request = {
        "format": "millrace-plus-subworkflow-request/v2",
        "call_key": "task_1",
        "call_home": str(root / "calls"),
        "parent": {"instance_id": PARENT},
        "definition": {
            "selector": "local:child",
            "version": "sha256:" + sha,
            "sha256": sha,
        },
        "output_ref": "results/task_1.json",
        "os_profile": {
            "path": str(profile),
            "sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
        },
        "observe_seconds": 1,
        "cleanup_seconds": 5,
        "inputs": {"request_markdown": "Exact task\r\n", "nested": {"null": None}},
    }
    file = root / "request.json"
    file.write_bytes(canonical(request) + b"\n")
    from millrace_plus import subworkflow_v2

    monkeypatch.setattr(subworkflow_v2, "_definition", lambda request: definition)

    def initialize(selector, destination):
        import shutil

        shutil.copytree(definition, destination)
        return {"provenance": {"sha256": sha}}

    monkeypatch.setattr(catalog, "init_template", initialize)
    settings = {
        "path": ["/usr/bin", "/bin"],
        "env_allowlist": [],
        "host_paths": {},
        "runtime_defaults": {},
        "operator_policy": {},
    }
    return {
        "root": root,
        "parent": parent,
        "workflow": workflow,
        "definition": definition,
        "request": request,
        "file": file,
        "profile": profile,
        "credential": credential,
        "settings": settings,
    }


class FakeClient:
    def __init__(self, fixture):
        self.fixture = fixture
        self.events = []
        self.responses = {}
        self.sent = []
        self.instance_controls = []
        self.status = "running"
        self.writer = "stopped"
        self.joined = True
        self.receipt_only = False
        self.fail_submit = None
        self.fail_observe = None
        self.run_override = {}
        self.start_request_id = None
        self.instance, self.run = CHILD, RUN
        self.at = 0

    def capabilities(self, *, deadline):
        self.events.append(("capabilities", deadline))
        return capabilities()

    def submit(self, envelope, *, deadline):
        self.events.append(("submit", copy.deepcopy(envelope)))
        self.sent.append(copy.deepcopy(envelope))
        operation = envelope["operation"]
        core = receipt(
            envelope,
            instance=self.instance,
            run=self.run if operation == "start_run" else None,
        )
        if operation == "start_run":
            self.start_request_id = envelope["request_id"]
            self.instance_controls = [core]
        response = observation(envelope, joined=self.joined, core=core)
        self.responses[envelope["request_id"]] = response
        if self.fail_submit is not None and operation == self.fail_submit:
            from millrace_plus.subworkflow_records import SubworkflowError

            raise SubworkflowError("os_unavailable")
        return copy.deepcopy(response)

    def observe(self, envelope, *, deadline):
        self.events.append(("observe", copy.deepcopy(envelope)))
        if self.fail_observe:
            from millrace_plus.subworkflow_records import SubworkflowError

            raise SubworkflowError(self.fail_observe)
        response = copy.deepcopy(self.responses[envelope["request_id"]])
        self.at += 1
        response["observed_at"] = f"2026-10-26T00:00:{self.at % 60:02d}Z"
        if self.receipt_only:
            response.update(
                source="core_receipt", phase="receipt_observed", holder_joined=None
            )
        return response

    def read(self, instance, run=None, *, deadline):
        self.events.append(("read", instance, run))
        f = self.fixture
        if instance == PARENT:
            return {
                "instance_id": PARENT,
                "project_root": str(f["parent"]),
                "workflow_file": str(f["workflow"] / "workflow.json"),
                "state_version": 1,
                **f["settings"],
            }
        request = f["request"]
        root = Path(str(request["call_home"])) / str(request["call_key"])
        if run is None:
            return {
                "instance_id": instance,
                "project_root": str(root / "project"),
                "workflow_file": str(root / "workflow" / "workflow.json"),
                "state_version": 1,
                "controls": self.instance_controls,
                **f["settings"],
            }
        return {
            "id": run,
            "instance_id": instance,
            "start_request_id": self.start_request_id,
            "status": self.status,
            "state_version": 3,
            "quiescent": self.status != "running",
            "inflight_invocations": [],
            "invocations": [
                {"id": INVOCATION, "status": "succeeded", "writer_state": self.writer},
            ],
            "completions": [
                {"invocation_id": INVOCATION, "effective_writer": self.writer}
            ],
            "controls": [],
            "planes": {},
            **self.run_override,
        }


def write_request(fixture):
    fixture["file"].write_bytes(canonical(fixture["request"]) + b"\n")


def journal(fixture):
    from millrace_plus.subworkflow_v2_records import Journal

    request = fixture["request"]
    return Journal(Path(str(request["call_home"])), str(request["call_key"]))


def advance(fixture, client, clock=None):
    from millrace_plus.subworkflow_v2 import advance as operation

    timer = clock or Clock()
    return operation(
        fixture["file"],
        expected_home="local",
        claim_sha256="a" * 64,
        cwd=fixture["parent"],
        client=client,
        clock=timer,
        sleep=timer.sleep,
    )
