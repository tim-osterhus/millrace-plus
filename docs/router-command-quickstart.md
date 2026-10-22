# Command-only router quickstart

For a Paperclip issue calling an independent pinned graph through the local OS
HTTP API, use the [Paperclip bridge guide](paperclip.md). It preserves exact
assignment bytes and reports verified graph results to the caller.

For a parent that calls independent pinned child workflows, use the
[subworkflow command guide](subworkflows.md). Its command-only composition
fixtures use 300 seconds wait, 15 seconds cleanup and a 325-second outer timeout;
native callers must choose their own longer finite budget.

This walkthrough authors a graph, copies it through a local catalog, and operates two independent instances through Millrace OS. Each instance has a producer plane and a checker plane connected by a declared seam. You then expand one instance, publish a paused pending revision, restart OS, and continue explicitly.

Use a matched Core/Plus/OS 0.23.0 environment with `python`, `millrace-plus`, and `millrace-os` on `PATH`. The formats here are graph v3, store v7, execution v2, and request v2. Package version numbers alone do not establish compatibility. Use fresh state and a POSIX host. No browser or model credentials are needed.

Run the blocks in order in **Bash**, from the same activated environment. Choose a fresh directory and an unused loopback port:

```bash
set -euo pipefail
umask 077
export DEMO="$HOME/millrace-command-demo"
export DEMO_PORT=18789
mkdir -m 700 "$DEMO"
mkdir "$DEMO/source" "$DEMO/catalog"
```

## 1. Author the complete workflow

The `command` binding runs the selected Python executable. A node's `config.script` names a captured workflow control file, while its working directory is its plane workspace. `config.args` can supply additional string arguments. Every script used below appears in `control_files`.

```bash
cat > "$DEMO/source/workflow.json" <<'JSON'
{
  "format": "millrace-router/v3",
  "limits": {
    "max_invocations": 8,
    "max_elapsed_seconds": 3600,
    "max_parallel_invocations": 2
  },
  "bindings": {
    "python": {
      "adapter": "command",
      "contract": "millrace-command/v1",
      "executable": "python",
      "config": {}
    }
  },
  "control_files": ["make.py", "check.py", "seal.py"],
  "planes": {
    "produce": {
      "workspace": "produce",
      "start": "make",
      "nodes": {
        "make": {
          "invoke": {"binding": "python", "timeout_seconds": 10, "config": {"script": "make.py"}},
          "result": "json-v1",
          "outcomes": ["READY"],
          "on": {"READY": {"finish": "succeeded"}},
          "on_error": {"pause": {"reason": "make_error", "resume": "make"}},
          "on_unknown": {"pause": {"reason": "make_unknown", "resume": "make"}}
        }
      }
    },
    "check": {
      "workspace": "check",
      "start": "check",
      "nodes": {
        "check": {
          "invoke": {"binding": "python", "timeout_seconds": 10, "config": {"script": "check.py"}},
          "result": "json-v1",
          "outcomes": ["PASS"],
          "on": {"PASS": {"pause": {"reason": "review_checked_total", "resume": "seal"}}},
          "on_error": {"pause": {"reason": "check_error", "resume": "check"}},
          "on_unknown": {"pause": {"reason": "check_unknown", "resume": "check"}}
        },
        "seal": {
          "invoke": {"binding": "python", "timeout_seconds": 10, "config": {"script": "seal.py"}},
          "result": "json-v1",
          "outcomes": ["DONE"],
          "on": {"DONE": {"finish": "succeeded"}},
          "on_error": {"pause": {"reason": "seal_error", "resume": "seal"}},
          "on_unknown": {"pause": {"reason": "seal_unknown", "resume": "seal"}}
        }
      }
    }
  },
  "seams": {
    "numbers": {
      "producer": {"plane": "produce", "node": "make", "outcome": "READY"},
      "consumer": {"plane": "check", "node": "check"}
    }
  }
}
JSON

cat > "$DEMO/source/make.py" <<'PY'
import json
from pathlib import Path

Path("numbers.json").write_text(json.dumps([19, 23]) + "\n")
print(json.dumps({"outcome": "READY", "refs": ["numbers.json"]}))
PY

cat > "$DEMO/source/check.py" <<'PY'
import json
import os
from pathlib import Path

handoff = json.loads(Path(os.environ["MILLRACE_SEAM_INPUT"]).read_text())
assert handoff["format"] == "millrace-seam-input/v1"
assert handoff["seam_id"] == "numbers"
assert handoff["producer_plane_id"] == "produce"
assert handoff["result"] == {"outcome": "READY", "refs": ["numbers.json"]}
source = Path(handoff["producer_workspace"]) / handoff["result"]["refs"][0]
values = json.loads(source.read_text())
assert values == [19, 23] and sum(values) == 42
Path("checked.json").write_text(json.dumps({"values": values, "total": sum(values)}) + "\n")
print(json.dumps({"outcome": "PASS", "refs": ["checked.json"]}))
PY

cat > "$DEMO/source/seal.py" <<'PY'
import json
from pathlib import Path

assert json.loads(Path("checked.json").read_text())["total"] == 42
Path("sealed.txt").write_text("checked total=42\n")
print(json.dumps({"outcome": "DONE", "refs": ["sealed.txt"]}))
PY
```

A successful `json-v1` command exits zero and prints one JSON object to stdout. Required `outcome` must match the node's declared outcomes. Optional `summary` is a string, and optional `refs` is a list of relative file paths under that plane's workspace. Send diagnostic text to stderr. For example, the producer prints `{"outcome":"READY","refs":["numbers.json"]}`.

The seam selects the producer's `READY` result for the exact consumer node. Core supplies its JSON envelope through `MILLRACE_SEAM_INPUT`. References identify producer files, whose bytes are not frozen by the seam. This producer leaves its output unchanged while the separate checker reads and verifies it.

## 2. Catalog, initialize, and copy

Use a local command-only entry. Catalog destinations and instance workspaces remain separate. See [Local workflow catalog](catalog.md) for selector and ownership rules.

```bash
millrace-plus catalog copy "$DEMO/source" "$DEMO/catalog/total-check" --catalog-home "$DEMO/catalog"
millrace-plus catalog list --catalog-home "$DEMO/catalog"
millrace-plus catalog init local:total-check "$DEMO/workflow-a" --catalog-home "$DEMO/catalog"
millrace-plus catalog copy "$DEMO/workflow-a" "$DEMO/workflow-b" --catalog-home "$DEMO/catalog"
mkdir -p "$DEMO/project-a/produce" "$DEMO/project-a/check"
mkdir -p "$DEMO/project-b/produce" "$DEMO/project-b/check"
```

## 3. Operate, edit, expand, and recover

The following client example calls only the installed public OS CLI. Its small helpers save exact request files and poll observations. They retry only OS `router_busy` admission refusals. They never change a saved body, refresh a stale version, or retry a Core-refused receipt.

All endpoint, OS state, credential, and Core-home selections are explicit. Leave the browser closed. The service creates the bearer credential and the CLI reads that same file.

```bash
python - <<'PY'
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

root = Path(os.environ["DEMO"]).resolve()
cli = str(Path(sys.executable).parent / "millrace-os")
flags = ["--host", "127.0.0.1", "--port", os.environ["DEMO_PORT"],
         "--config-dir", str(root / "os-config"), "--state-dir", str(root / "os-state"),
         "--credential-path", str(root / "os-config/credentials.json")]
serve = [cli, "serve", *flags, "--router-state-home", "demo=" + str(root / "core")]
(root / "requests").mkdir()
child = None


def until(read, ready, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if ready(value):
            return value
        time.sleep(0.05)
    raise RuntimeError("Observation deadline expired; inspect saved requests and OS log")


def router(*args):
    for attempt in range(100):
        process = subprocess.run([cli, "router", *args, *flags, "--home", "demo"],
                                 capture_output=True, text=True, timeout=10)
        value = json.loads(process.stdout)
        if process.returncode and value.get("code") == "router_busy":
            time.sleep(0.05)
            continue
        if process.returncode:
            raise RuntimeError(value)
        return value
    raise RuntimeError("OS slot still busy; wait for the holder to return before continuing")


def start_os():
    global child
    with (root / "os.log").open("a") as log:
        child = subprocess.Popen(serve, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    def ready():
        if child.poll() is not None:
            raise RuntimeError("OS exited; inspect os.log")
        return subprocess.run([cli, "readiness", *flags], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5).returncode
    until(ready, lambda code: code == 0)


def stop_os():
    global child
    if child is not None and child.poll() is None:
        subprocess.run([cli, "stop", "--state-dir", str(root / "os-state"),
                        "--timeout", "30"], check=True)
        assert child.wait(timeout=5) == 0
    child = None


def read(instance, run=None):
    args = ["inspect", "--instance", instance]
    return router(*args, *(["--run", run] if run else []))["data"]


def send(operation, instance=None, run=None, plane=None, version=None, payload=None):
    body = {"format": "millrace-router-request/v2", "operation": operation,
            "instance_id": instance, "run_id": run, "plane_id": plane,
            "request_id": uuid4().hex, "expected_state_version": version,
            "payload": {} if payload is None else payload}
    path = root / "requests" / (operation + "-" + body["request_id"] + ".json")
    path.write_text(json.dumps(body, indent=2) + "\n")
    router("request", "--file", str(path))
    def observe():
        value = router("request", "--request-id", body["request_id"])
        if value["phase"] == "call_failed":
            raise RuntimeError(value)
        return value
    result = until(observe, lambda value: value["core_receipt"] is not None
                   and value["core_receipt"]["status"] != "queued"
                   and value["phase"] == "call_returned")
    receipt = result["core_receipt"]
    assert receipt["request_id"] == body["request_id"] and receipt["status"] == "applied", receipt
    return receipt


try:
    start_os()
    instances, runs = {}, {}
    for name, mode in (("a", "locked"), ("b", "live")):
        # Create uses version zero and no target IDs. Start uses the instance version.
        created = send("create_instance", version=0, payload={
            "workflow_file": str(root / ("workflow-" + name) / "workflow.json"),
            "project_root": str(root / ("project-" + name)),
            "path": [str(Path(sys.executable).parent), "/usr/bin", "/bin"],
            "env_allowlist": [], "host_paths": {"python": sys.executable}})
        instance = instances[name] = created["instance_id"]
        runs[name] = send("start_run", instance, version=read(instance)["state_version"],
                          payload={"edit_mode": mode})["run_id"]
        paused = read(instance, runs[name])
        assert paused["status"] == "paused" and paused["quiescent"] and paused["count"] == 2
        assert paused["planes"]["produce"]["status"] == "succeeded"
        assert paused["planes"]["check"]["status"] == "paused"
        assert json.loads((root / ("project-" + name) / "check/checked.json").read_text())["total"] == 42
    assert instances["a"] != instances["b"] and len(router("instances")["data"]) == 2
    (root / "ids.json").write_text(json.dumps({"instances": instances, "runs": runs}, indent=2))
    a, ar, b, br = instances["a"], runs["a"], instances["b"], runs["b"]
    before = read(a, ar)

    # Complete B independently. A remains paused for editing.
    send("resume_plane", b, br, plane="check", version=read(b, br)["state_version"])
    b_final = read(b, br)
    assert b_final["status"] == "succeeded" and b_final["count"] == 3
    b_bytes = (root / "workflow-b/workflow.json").read_bytes()

    # Expand A's local route. Plane/workspace identities and the seam stay fixed.
    edited = root / "edit"
    edited.mkdir()
    for source in (root / "source").iterdir():
        (edited / source.name).write_bytes(source.read_bytes())
    graph = json.loads((edited / "workflow.json").read_text())
    nodes = graph["planes"]["check"]["nodes"]
    nodes["seal"]["on"]["DONE"] = {"goto": "archive"}
    nodes["archive"] = copy.deepcopy(nodes["seal"])
    nodes["archive"]["invoke"]["config"] = {"script": "archive.py"}
    nodes["archive"]["on"]["DONE"] = {"finish": "succeeded"}
    nodes["archive"]["on_error"] = {"pause": {"reason": "archive_error", "resume": "archive"}}
    nodes["archive"]["on_unknown"] = {"pause": {"reason": "archive_unknown", "resume": "archive"}}
    graph["control_files"].append("archive.py")
    (edited / "workflow.json").write_text(json.dumps(graph, indent=2) + "\n")
    (edited / "archive.py").write_text('''import json
from pathlib import Path
assert Path("sealed.txt").read_text() == "checked total=42\\n"
Path("archive.txt").write_text("archived checked total=42\\n")
print(json.dumps({"outcome": "DONE", "refs": ["archive.txt"]}))
''')
    draft = router("draft", "create")["draft_id"]
    for source in sorted(edited.iterdir()):
        router("draft", "put", "--draft", draft, "--path", source.name, "--file", str(source))
    validation = router("draft", "validate", "--draft", draft, "--instance", a, "--run", ar)
    assert validation["data"]["valid"]
    apply_args = ["draft", "apply", "--draft", draft, "--instance", a, "--run", ar,
                  "--base-state-version", str(read(a, ar)["state_version"]), "--request-id", uuid4().hex]
    publication = until(lambda: router(*apply_args)["data"],
                        lambda value: value.get("status") == "paused_pending")
    pending = read(a, ar)
    assert pending["status"] == "paused" and pending["count"] == 2 and pending["quiescent"]
    assert pending["pending_revision"]["candidate_id"] == publication["candidate_id"]
    assert not (root / "project-a/check/archive.txt").exists()
    (root / "a-pending.json").write_text(json.dumps(pending, indent=2))

    # Supported same-state recovery: stop gracefully, restart, inspect, then recover.
    stop_os()
    start_os()
    restored = read(a, ar)
    assert restored["status"] == "paused" and restored["quiescent"] and restored["count"] == 2
    assert restored["pending_revision"] == pending["pending_revision"]
    assert restored["invocations"] == pending["invocations"] and read(b, br) == b_final
    send("recover_run", a, ar, version=read(a, ar)["state_version"])
    send("accept_revision", a, ar, version=read(a, ar)["state_version"],
         payload={"candidate_id": publication["candidate_id"]})
    held = read(a, ar)
    assert held["run_gate"] is None and held["planes"]["check"]["status"] == "paused"
    send("resume_plane", a, ar, plane="check", version=held["state_version"])
    a_final = read(a, ar)
    assert a_final["status"] == "succeeded" and a_final["count"] == 4
    assert a_final["invocations"][:2] == before["invocations"]
    assert read(b, br) == b_final and (root / "workflow-b/workflow.json").read_bytes() == b_bytes
    assert (root / "project-a/check/archive.txt").read_text() == "archived checked total=42\n"
    assert (root / "project-b/check/sealed.txt").read_text() == "checked total=42\n"
    assert all(v["writer_state"] == "stopped" for run in (a_final, b_final) for v in run["invocations"])
    for name, run in (("a", a_final), ("b", b_final)):
        (root / (name + "-final.json")).write_text(json.dumps(run, indent=2))
    print(json.dumps({"instances": instances, "runs": runs,
                      "a_invocations": a_final["count"], "b_invocations": b_final["count"],
                      "checked_total": 42, "evidence": str(root)}, indent=2))
finally:
    stop_os()
PY
```

The expected result is total **42**, checked in a separate invocation. B finishes with three invocations. Expanded A finishes with four. Its earlier invocations and B's workflow/history stay unchanged. The output files, saved requests, inspections, and `os.log` remain in `DEMO` for inspection.

Apply does not accept or resume. It leaves A paused with a pending candidate. Explicit acceptance clears the run gate when allowed, but the graph-paused checker still needs `resume_plane`. Acceptance can itself continue runnable planes in other graphs. Do not add an unconditional extra `resume_run` after acceptance.

The example waits on HTTP call observations. A 202 response or queued receipt does not mean applied, and an applied receipt does not mean its holder returned. Repeating the exact Apply command observes its retained call. Local observations last only for the service lifetime, expire 300 seconds after call return, and retain at most 128 joined calls. Public Core receipts remain separate durable facts.

## Recovery and refusal recipes

Each saved request has exactly the eight request-v2 fields shown in `send`. Use a fresh 32-lowercase-hex request ID for a new action. Preserve the exact body for deliberate replay. Current versions come from inspection, not from editing an old saved request.

| Action | Target fields | Expected version | Payload |
|---|---|---|---|
| `create_instance` | all IDs null | `0` | `workflow_file`, `project_root`, `path`, `env_allowlist`, `host_paths`, as above |
| `start_run` | instance, run/plane null | current instance version | `{"edit_mode":"locked"}` or `{"edit_mode":"live"}` |
| `pause_run`, `cancel_run` | instance and run, plane null | null | `{}` |
| `recover_run` | instance and run, plane null | current run version | `{}` |
| `accept_revision` | instance and run, plane null | current run version | `{"candidate_id":"64-hex candidate from pending_revision"}` |
| `resume_run` | instance and run, plane null | current run version | `{}`. Clears an explicit run gate, preserving plane pauses. |
| `resume_plane` | instance, run, exact plane | current run version | `{}` |
| `reconcile_invocation` | instance, run, exact plane | current run version | `{"invocation_id":"32-hex ID","reason":"specific stopped-writer evidence","stopped":true}` |

Inspect a known durable receipt through the same configured OS endpoint:

```bash
millrace-os router request --home demo --request-id REQUEST_ID --instance INSTANCE_ID --run RUN_ID \
  --host 127.0.0.1 --port "$DEMO_PORT" --config-dir "$DEMO/os-config" \
  --state-dir "$DEMO/os-state" --credential-path "$DEMO/os-config/credentials.json"
```

If Apply stops after a partial publication, retain its marker and frozen files. After resolving the refusal, use the supported repair command:

```bash
millrace-os router draft repair-publication --home demo --instance INSTANCE_ID \
  --host 127.0.0.1 --port "$DEMO_PORT" --config-dir "$DEMO/os-config" \
  --state-dir "$DEMO/os-state" --credential-path "$DEMO/os-config/credentials.json"
```

Repair uses the frozen publication and stays paused/pending for a nonterminal run. Missing frozen evidence refuses repair. A completed repair is not retrieved by repeating repair. Inspect Core's pending state when a repair initially returned 202.

The restart above is graceful and already quiescent. Forced OS death can leave the OS SQLite recovery shell instead of normal router routes. Explicitly selecting a fresh owned OS state directory with the same Core home permits Core inspection, but does not restore old OS drafts automatically. An uncertain `unknown`/`may_live` writer must be verified stopped and reconciled before continuation. Never assert `stopped: true` merely because a response or client process ended.

While a sibling holder is active, a continuation may return OS `router_busy` (429). Wait for its return before retrying. For a stale Core receipt, inspect and decide a new action with a new ID and current version. Neither refusal grants permission to replay an uncertain invocation.
