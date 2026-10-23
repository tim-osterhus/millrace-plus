"""Test-only HTTP/auth projection over canonical OS RouterClient/RouterWorkers.

This peer exercises real public controls and worker ownership, not FastAPI routing.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from millrace_plus import catalog


@contextmanager
def peer(root):
    pytest.importorskip(
        "pydantic", reason="optional canonical OS test peer requires OS dependencies"
    )
    from millrace_os.router_client import RouterClient, RouterUnavailable
    from millrace_os.router_workers import RouterWorkers
    fixture = SimpleNamespace(
        operations=[], requests=[], lose=set(), failures={}, token="x" * 48, rotate=None
    )
    fixture.client = RouterClient({"local": root / "os-state"})
    fixture.workers = RouterWorkers(fixture.client)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.respond()

        def do_GET(self):
            self.respond()

        def respond(self):
            parsed = urlsplit(self.path)
            try:
                if self.headers.get("Authorization") != "Bearer " + fixture.token:
                    self.send_error(401)
                    return
                if fixture.rotate:
                    fixture.rotate()
                    fixture.rotate = None
                if parsed.path in fixture.failures:
                    self.send_error(fixture.failures[parsed.path])
                    return
                if getattr(fixture, "redirect", None):
                    self.send_response(302)
                    self.send_header("Location", fixture.redirect)
                    self.end_headers()
                    return
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                prefix = "/api/v1/router/"
                if self.command == "POST":
                    body = json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    )
                    assert body["home"] == "local"
                    request = body["request"]
                    fixture.operations.append(request["operation"])
                    fixture.requests.append(request)
                    call = fixture.workers.submit("local", request)
                    value = fixture.workers.wait_observation(call).model_dump()
                    if request["operation"] in fixture.lose:
                        fixture.lose.remove(request["operation"])
                        if getattr(fixture, "restart_after_loss", False):
                            fixture.workers.request_shutdown()
                            fixture.workers.drain()
                            fixture.workers = RouterWorkers(fixture.client)
                        self.connection.shutdown(socket.SHUT_RDWR)
                        self.connection.close()
                        return
                elif parsed.path.startswith(prefix + "requests/"):
                    value = fixture.workers.observe(
                        "local",
                        parsed.path.rsplit("/", 1)[-1],
                        instance_id=query.get("instance_id"),
                        run_id=query.get("run_id"),
                    ).model_dump()
                else:
                    parts = parsed.path[len(prefix) :].split("/")
                    data = (
                        fixture.client.inspect_run("local", parts[1], parts[3])
                        if len(parts) == 4
                        else fixture.client.inspect_instance("local", parts[1])
                    )
                    if len(parts) == 4 and getattr(fixture, "read_patch", None):
                        data = fixture.read_patch(data)
                    value = {
                        "contract": "millrace-os-router-read/v1",
                        "home": "local",
                        "data": data,
                        "source_provider": "core",
                        "authority": "core-state-home",
                        "observed_at": "test",
                    }
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except RouterUnavailable as exc:
                self.send_error(exc.status)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    fixture.origin = "http://127.0.0.1:" + str(server.server_port)
    try:
        yield fixture
    finally:
        fixture.workers.request_shutdown(cancel=True)
        fixture.workers.drain()
        server.shutdown()
        server.server_close()
        worker.join(3)
        assert not worker.is_alive()


def small_graph():
    return {
        "format": "millrace-router/v3",
        "limits": {"max_invocations": 4, "max_elapsed_seconds": 20},
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
                        "outcomes": ["READY", "HOLD"],
                        "on": {
                            "READY": {"finish": "succeeded"},
                            "HOLD": {"pause": {"reason": "human", "resume": "step"}},
                        },
                        "on_error": {"pause": {"reason": "error", "resume": "step"}},
                        "on_unknown": {
                            "pause": {"reason": "unknown", "resume": "step"}
                        },
                    }
                },
            }
        },
    }


@pytest.fixture
def paperclip(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(root / "home"))
    definition = root / "catalog/small"
    definition.mkdir(parents=True)
    (definition / "workflow.json").write_text(json.dumps(small_graph()))
    (definition / "step.py").write_text(
        "import json,hashlib\nfrom pathlib import Path\n"
        "m=json.loads(Path('subworkflow-inputs.json').read_bytes())\n"
        "request=m['inputs']['request_markdown'].encode()\n"
        "Path('request.md').write_bytes(request)\n"
        "Path('answer.md').write_bytes(request)\n"
        "assert Path('answer.md').read_bytes()==Path('request.md').read_bytes()\n"
        "print(json.dumps({'outcome':'READY','summary':'checked exact request',"
        "'refs':['answer.md']}))\n"
    )
    catalog.set_catalog_home(definition.parent)
    fingerprint = catalog.workflow_fingerprint(definition)
    with peer(root) as fixture:
        fixture.definition = definition
        fixture.root = root
        fixture.credential = root / "os-credential.json"
        fixture.credential.write_text(json.dumps({"token": fixture.token}))
        fixture.credential.chmod(0o600)
        fixture.config = {
            "format": "millrace-paperclip-bridge-config/v1",
            "paperclip_origin": "https://paperclip.example",
            "os_origin": fixture.origin,
            "os_home": "local",
            "os_credential_file": str(fixture.credential),
            "definition": {
                "selector": "local:small",
                "version": "sha256:" + fingerprint,
                "sha256": fingerprint,
            },
            "destination_root": str(root / "destinations"),
            "binding_state_root": str(root / "bindings"),
            "helper_executable": str(Path(sys.executable).parent / "millrace-plus"),
            "instance_settings": {
                "path": list(
                    dict.fromkeys(
                        [str(Path(sys.executable).resolve().parent), "/usr/bin", "/bin"]
                    )
                ),
                "env_allowlist": [],
                "host_paths": {},
            },
            "wait_seconds": 5,
            "cleanup_seconds": 5,
        }
        fixture.assignment = {
            "format": "millrace-paperclip-context/v1",
            "company_id": "company-1",
            "agent_id": "agent-1",
            "issue_id": "issue-1",
            "attempt_id": "attempt-1",
            "description": "Redacted café\r\nUseful brief\n",
        }
        fixture.config_file, fixture.assignment_file = (
            root / "config.json",
            root / "assignment.json",
        )

        def write():
            fixture.config_file.write_text(json.dumps(fixture.config))
            fixture.assignment_file.write_text(json.dumps(fixture.assignment))

        fixture.write = write

        def script(source):
            (definition / "step.py").write_text(source)
            pin = catalog.workflow_fingerprint(definition)
            fixture.config["definition"].update(version="sha256:" + pin, sha256=pin)
            write()

        fixture.script = script
        write()
        yield fixture
