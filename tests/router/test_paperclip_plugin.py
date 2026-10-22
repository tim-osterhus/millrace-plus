"""Pinned loader boundary and process/context ownership without a Paperclip account."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[5]
TASK = WORKSPACE / "lab/tasks/v0231-implementation/2026-10-21"
NODE = TASK / "scratch/tools/node-v24.11.0-darwin-arm64/bin/node"
PLUGIN = (
    Path(__file__).resolve().parents[2] / "src/millrace_plus/integrations/paperclip"
)
PEER = Path(__file__).parent / "fixtures/paperclip_peer.mjs"
LOADER = TASK / "r2-preparation/upstream/server__src__adapters__plugin-loader.ts"
pytestmark = pytest.mark.skipif(
    not NODE.exists() or not LOADER.exists(),
    reason="task-owned pinned Node/loader profile is unavailable",
)
pytest_plugins = ["paperclip_support"]


@pytest.fixture
def plugin(tmp_path):
    root = tmp_path.resolve()
    loader = root / "plugin-loader.ts"
    assert (
        hashlib.sha256(LOADER.read_bytes()).hexdigest()
        == "4191cb56168efdc77e39cb08ec7121a6142ba96340ebbcab168dd016aabf3024"
    )
    shutil.copyfile(LOADER, loader)
    helper = root / "helper"
    capture = root / "captured.json"
    helper.write_text(
        "#!" + sys.executable + "\n"
        "import json,sys,os,stat\nfrom pathlib import Path\n"
        "a=json.loads(Path(sys.argv[sys.argv.index('--assignment')+1]).read_bytes())\n"
        f"Path({str(capture)!r}).write_text(json.dumps({{'assignment':a,'env_names':sorted(os.environ),'argv':sys.argv[1:],'mode':stat.S_IMODE(Path(sys.argv[-1]).stat().st_mode)}}))\n"
        "r={'format':'millrace-paperclip-result/v1',"
        "'assignment':{'paperclip_origin':'https://paperclip.example',"
        "'assignment_revision':1,'company_id':a['company_id'],'issue_id':a['issue_id']},"
        "'attempt_id':a['attempt_id'],'agent_id':a['agent_id'],'os_home':'local',"
        "'assignment_sha256':'a'*64,'binding_digest':'b'*64,'graph_revision':'c'*64,"
        "'instance_id':'instance-1','run_id':'run-1','result_root':'/test/result',"
        "'summary':'checked','status':'succeeded','refs':[]}\n"
        "print(json.dumps(r))\n"
    )
    helper.chmod(0o700)
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "format": "millrace-paperclip-bridge-config/v1",
                "paperclip_origin": "https://paperclip.example",
                "os_origin": "http://127.0.0.1:8765",
                "os_home": "local",
                "os_credential_file": str(root / "credential.json"),
                "definition": {
                    "selector": "local:small",
                    "version": "sha256:" + "a" * 64,
                    "sha256": "a" * 64,
                },
                "destination_root": str(root / "dest"),
                "binding_state_root": str(root / "bindings"),
                "helper_executable": str(helper),
                "instance_settings": {
                    "path": ["/usr/bin", "/bin"],
                    "env_allowlist": ["LANG", "PAPERCLIP_API_TOKEN", "INSTAGRAM_TOKEN"],
                    "host_paths": {},
                },
                "wait_seconds": 1,
                "cleanup_seconds": 5,
            }
        )
    )
    context = {
        "agent": {
            "companyId": "company-1",
            "id": "agent-1",
            "adapterConfig": {"secret": "not projected"},
        },
        "runId": "attempt-1",
        "config": {"bridgeConfigFile": str(config)},
        "context": {
            "paperclipIssue": {
                "id": "issue-1",
                "description": "post-redaction café\r\n",
            },
            "issueId": "issue-1",
        },
        "agentIdentity": {"privateKeyPem": "not projected"},
        "runtimeTools": {"token": "not projected"},
    }

    def run(**options):
        home = options.pop("home", root)
        input_file = root / "input.json"
        input_file.write_text(json.dumps({"context": context, **options}))
        env = {
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(root),
            "LANG": "C",
            "PAPERCLIP_API_TOKEN": "test-value-never-forwarded",
            "INSTAGRAM_TOKEN": "test-value-never-forwarded",
        }
        process = subprocess.run(
            [str(NODE), str(PEER), str(PLUGIN), str(loader), str(input_file)],
            env=env,
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)["result"]

    return root, context, capture, helper, run


def test_pinned_local_loader_and_context_projection(plugin) -> None:
    root, context, capture, _, run = plugin
    result = run()
    assert result["exitCode"] == 0
    saved = json.loads(capture.read_bytes())
    assert set(saved["assignment"]) == {
        "format",
        "company_id",
        "agent_id",
        "issue_id",
        "attempt_id",
        "description",
    }
    assert (
        saved["assignment"]["description"]
        == context["context"]["paperclipIssue"]["description"]
    )
    assert saved["mode"] == 0o600
    assert saved["argv"][:2] == ["paperclip", "run"]
    assert not Path(saved["argv"][-1]).exists()
    assert not list(root.glob("millrace-paperclip-*"))


def test_plugin_environment_excludes_paperclip_credentials(plugin) -> None:
    _, _, capture, _, run = plugin
    assert run()["exitCode"] == 0
    names = json.loads(capture.read_bytes())["env_names"]
    assert "LANG" in names and "HOME" in names
    assert not any("TOKEN" in name or "PAPERCLIP" in name for name in names)
    diagnostics = run(diagnostics=True)
    assert diagnostics["status"] == "pass"
    assert "INSTAGRAM" not in json.dumps(diagnostics)


@pytest.mark.parametrize("options", [{"abortBefore": True}, {"abortReady": True}])
def test_abort_registration_precedes_helper_dispatch(plugin, options) -> None:
    _, _, capture, _, run = plugin
    result = run(**options)
    assert result["exitCode"] == 3
    assert not capture.exists()


@pytest.mark.parametrize(
    "field", ["company", "agent", "attempt", "issue", "description", "conflict"]
)
def test_server_context_binds_exact_issue(plugin, field) -> None:
    _, context, capture, _, run = plugin
    if field == "company":
        del context["agent"]["companyId"]
    elif field == "agent":
        del context["agent"]["id"]
    elif field == "attempt":
        del context["runId"]
    elif field == "issue":
        del context["context"]["paperclipIssue"]["id"]
    elif field == "description":
        context["context"]["paperclipIssue"]["description"] = None
    else:
        context["context"]["taskId"] = "wrong-issue"
    assert run()["exitCode"] == 5
    assert not capture.exists()


@pytest.mark.parametrize(
    "source", ["print('not json')", "print('x'*262145)", "import os;os._exit(0)"]
)
def test_plugin_limits_and_nonzero_status_mapping(plugin, source) -> None:
    _, _, _, helper, run = plugin
    helper.write_text("#!" + sys.executable + "\n" + source + "\n")
    result = run()
    assert result["exitCode"] == 6 and result["resultJson"]["status"] == "uncertain"


@pytest.mark.parametrize(
    "status,code",
    [
        ("failed", 1),
        ("cancelled", 3),
        ("attention_required", 4),
        ("refused", 5),
        ("uncertain", 6),
    ],
)
def test_plugin_preserves_distinct_nonzero_results(plugin, status, code) -> None:
    _, _, _, helper, run = plugin
    helper.write_text(
        helper.read_text().replace("'status':'succeeded'", f"'status':{status!r}")
        + f"sys.exit({code})\n"
    )
    result = run()
    assert result["exitCode"] == code and result["resultJson"]["status"] == status


def test_malformed_helper_success_cannot_report_zero(plugin) -> None:
    _, _, _, helper, run = plugin
    helper.write_text(
        "#!"
        + sys.executable
        + "\nimport json\nprint(json.dumps({'format':'millrace-paperclip-result/v1',"
        "'status':'succeeded','attempt_id':'attempt-1',"
        "'assignment':{'company_id':'company-1','issue_id':'issue-1'},"
        "'summary':'claimed','refs':[]}))\n"
    )
    assert run()["exitCode"] == 6


def _real_helper(paperclip):
    helper = paperclip.root / "bridge-console"
    source = Path(__file__).resolve().parents[2] / "src"
    helper.write_text(
        "#!" + sys.executable + "\nimport os,sys\n"
        "env={'HOME':os.environ['HOME'],'PATH':os.environ['PATH'],"
        "'PYTHONDONTWRITEBYTECODE':'1',"
        f"'PYTHONPATH':{str(source)!r}}}\n"
        "if 'TMPDIR' in os.environ: env['TMPDIR']=os.environ['TMPDIR']\n"
        "os.execve(sys.executable,[sys.executable,'-c',"
        "'from millrace_plus.router.cli import cli;cli()',*sys.argv[1:]],env)\n"
    )
    helper.chmod(0o700)
    paperclip.config["helper_executable"] = str(helper)
    paperclip.write()


def test_loaded_plugin_runs_useful_graph_once(plugin, paperclip) -> None:
    _, context, _, _, run = plugin
    _real_helper(paperclip)
    context["config"]["bridgeConfigFile"] = str(paperclip.config_file)
    context["context"]["paperclipIssue"]["description"] = paperclip.assignment[
        "description"
    ]
    first = run(home=paperclip.root / "home")
    assert first["exitCode"] == 0, first
    context["runId"] = "attempt-2"
    second = run(home=paperclip.root / "home")
    assert second["resultJson"]["run_id"] == first["resultJson"]["run_id"]
    assert paperclip.operations.count("start_run") == 1
    assert first["resultJson"]["refs"] == ["project/answer.md"]


def test_loaded_plugin_active_abort_waits_for_public_closure(plugin, paperclip) -> None:
    _, context, _, _, run = plugin
    paperclip.script('import time\ntime.sleep(20)\nprint(\'{"outcome":"READY"}\')\n')
    _real_helper(paperclip)
    context["config"]["bridgeConfigFile"] = str(paperclip.config_file)
    result = run(home=paperclip.root / "home", abortAfter=600)
    assert result["exitCode"] == 3, result
    report = result["resultJson"]
    view = paperclip.client.inspect_run(
        "local", report["instance_id"], report["run_id"]
    )
    assert view["status"] == "cancelled" and view["quiescent"]
    assert all(row["writer_state"] == "stopped" for row in view["invocations"])
