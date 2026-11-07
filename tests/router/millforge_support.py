"""Finite captured profiles and ordinary CLI peers for Millforge bridge tests."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

from millrace.router.execution import (
    AdapterConfiguration,
    CompletionFacts,
    InvocationRequest,
)

from millrace_plus.router import millforge_configuration as c


def native_profile(*, mode="generate", endpoint="http://127.0.0.1:1/v1", tools=None):
    return {
        "schema": "millforge-profile/v2", "profile_id": "offline-m4", "revision": 1,
        "mode": mode, "runtime": {"pi_version": "1.4.0", "binding_version": 2},
        "model": {
            "provider": "local", "api": "openai-completions", "id": "operator-model",
            "base_url": endpoint, "auth": {"kind": "none"}, "context_tokens": 100000,
            "max_output_tokens": 1000,
            "metadata": {"source": "operator", "overrides": {
                "name": "Offline fixture", "input": ["text"], "cost": None,
                "reasoning": False, "thinkingLevelMap": {},
                "contextWindow": 100000, "maxTokens": 1000,
            }}, "compat": {"supportsFinishReason": True,
                           "maxTokensField": "max_tokens"},
        },
        "instructions": "Return the requested structured result.",
        "sampling": {"temperature": 0, "top_p": None}, "reasoning": "off",
        "tools": tools or [], "assets": [],
        "capabilities": {"extensions": [], "packages": []},
        "limits": {
            "model_requests": 1 if mode == "generate" else 3,
            "tool_calls": 0 if mode == "generate" else 2,
            "elapsed_seconds": 12, "model_timeout_seconds": 7,
            "tool_timeout_seconds": 7, "input_bytes": 1048576,
            "context_bytes": 2097152, "output_bytes": 65536,
            "tool_output_bytes": 262144, "trace_bytes": 1048576,
        },
        "terminal": {"format": "json-object", "required_keys": ["outcome"],
                     "outcome_key": "outcome", "outcomes": ["READY", "REJECT"]},
    }


def request(root: Path, *, executable="/absent/millforge", profile=None, **changes):
    selected = profile or native_profile()
    return replace(InvocationRequest(
        invocation_id="a" * 32, plane_id="main", node_id="draft",
        producing_revision="b" * 64, binding_id="native", adapter_id=c.ADAPTER_ID,
        adapter_contract=c.ADAPTER_CONTRACT, executable=executable,
        binding_config={"profile": "profile.json", "assets": {}},
        node_config={"prompt": "task.txt"}, runtime={}, workflow={}, plane={},
        common_settings={"effective": {"timeout_seconds": 40}},
        files={"profile.json": c.encoded(selected), "task.txt": b"Review the task."},
        host_paths={}, environment_names=(), outcomes=("READY", "REJECT"),
        result_contract="json-v1", project_root=root,
        invocation_root=root / "invocation",
    ), **changes)


def configuration(req):
    return AdapterConfiguration(
        req.adapter_id, req.adapter_contract, {req.binding_id: req.binding_config},
        {(req.plane_id, req.node_id): (req.binding_id, req.node_config)},
        req.runtime, req.workflow, {req.plane_id: req.plane}, req.files,
        req.host_paths, req.environment_names,
    )


def materialize(plan):
    root = Path(plan.details["output_path"]).parent
    root.mkdir(parents=True, exist_ok=True)
    for name, raw in plan.generated_controls.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def facts(plan, result, pid):
    return CompletionFacts(
        plan.invocation_id, pid, result.stdout, result.stderr, result.classification,
        result.writer_state, result.diagnostic, result.exit_code,
        result.adapter_evidence, "adapter",
    )


def fake_cli(root: Path, **options) -> Path:
    """Fake the public CLI boundary; installed proof uses actual native inspection."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "fake-millforge"
    path.with_suffix(".json").write_text(json.dumps(options))
    path.write_text(
        f"#!{sys.executable}\n"
        "import hashlib,json,sys,time\nfrom pathlib import Path\n"
        "sha=lambda b:hashlib.sha256(b).hexdigest()\n"
        "opts=json.loads(Path(__file__).with_suffix('.json').read_text())\n"
        "if sys.argv[1]=='run':\n"
        " args=dict(zip(sys.argv[2::2],sys.argv[3::2]))\n"
        " p=json.loads((Path(args['--profile'])/'profile.json').read_text())\n"
        " r=json.loads(Path(args['--request']).read_text())\n"
        " out=Path(args['--output-dir']);out.mkdir()\n"
        " if opts.get('sleep'):time.sleep(opts['sleep'])\n"
        " if opts.get('stale'):r['task']='different same-ID task'\n"
        " raw=json.dumps(r).encode();(out/'request.json').write_bytes(raw)\n"
        " closed={'worker':'closed','tools':'closed','writers':'closed',"
        "'inference':'response_returned','detached_effects':'none_observed'}\n"
        " if opts.get('uncertain'):closed['writers']='may_live'\n"
        " failed=opts.get('exit',0)==3\n"
        " if failed:closed['inference']='disconnected_unknown'\n"
        " output=opts.get('output',{'outcome':'REJECT'})\n"
        " result={'schema':'millforge-result/v3','request_id':r['request_id'],"
        "'profile_id':r['profile_id'],'profile_digest':r['profile_digest'],"
        "'mode':r['expected_mode'],'status':'failed' if failed else 'completed',"
        "'domain_outcome':None if failed else output.get('outcome'),"
        "'output':None if failed else output,"
        "'diagnostic':{'code':'INVALID_TERMINAL','message':'fixture'} if failed "
        "else None,'closure':closed,'artifacts':[{'path':'request.json',"
        "'size':len(raw),"
        "'sha256':sha(raw),'media_type':'application/json'}]}\n"
        " result.update(opts.get('result',{}))\n"
        " capture={'schema':'millforge-capture/v3','request_id':r['request_id'],"
        "'profile_digest':r['profile_digest'],'cwd':r['workspace'],"
        "'limits':p['limits'],"
        "'versions':{'millforge':'0.2.0','pi':'1.4.0'}}\n"
        " capture.update(opts.get('capture',{}))\n"
        " (out/'inspection.json').write_text(json.dumps({'result':result,"
        "'capture':capture,"
        "'trace_records':3}))\n"
        " if opts.get('missing'):(out/'request.json').unlink()\n"
        " if opts.get('stderr'):sys.stderr.write(opts['stderr'])\n"
        " print('completed-looking CLI output');sys.exit(opts.get('exit',0))\n"
        "if sys.argv[1]=='inspect':\n"
        " if opts.get('inspect_sleep'):time.sleep(opts['inspect_sleep'])\n"
        " if opts.get('inspect_exit'):sys.exit(opts['inspect_exit'])\n"
        " print('{' if opts.get('invalid') else "
        "(Path(sys.argv[3])/'inspection.json').read_text())\n"
    )
    path.chmod(0o700)
    return path


def flow(single_flow, *, executable=None, profile=None, **options):
    workflow, project, home = single_flow
    graph = json.loads(workflow.read_text())
    executable = executable or str(fake_cli(workflow.parent, **options))
    selected = profile or native_profile()
    graph["bindings"] = {"native": {
        "adapter": c.ADAPTER_ID, "contract": c.ADAPTER_CONTRACT,
        "executable": executable, "config": {"profile": "profile.json", "assets": {}},
    }}
    graph["control_files"] = ["profile.json", "task.txt"]
    graph["limits"] = {"max_invocations": 6, "max_elapsed_seconds": 120}
    node = graph["planes"]["main"]["nodes"]["draft"]
    node["invoke"] = {"binding": "native", "config": {"prompt": "task.txt"},
                      "timeout_seconds": 40}
    node["outcomes"] = ["READY", "REJECT"]
    node["on"] = {"READY": {"finish": "succeeded"}, "REJECT": {"finish": "failed"}}
    (workflow.parent / "profile.json").write_bytes(c.encoded(selected))
    (workflow.parent / "task.txt").write_text("Review this task.")
    workflow.write_text(json.dumps(graph))
    return workflow, project, home


def with_handoff(workflow: Path, project: Path) -> None:
    graph = json.loads(workflow.read_text())
    graph["planes"]["consumer"] = graph["planes"].pop("main")
    graph["planes"]["consumer"]["workspace"] = "consumer"
    (project / "consumer").mkdir()
    (project / "producer").mkdir()
    consumer = graph["planes"]["consumer"]["nodes"]["draft"]
    producer = copy.deepcopy(consumer)
    producer["invoke"] = {"binding": "python", "config": {"script": "producer.py"}}
    producer["outcomes"] = ["READY"]
    producer["on"] = {"READY": {"finish": "succeeded"}}
    graph["planes"]["producer"] = {"workspace": "producer", "start": "draft",
                                    "nodes": {"draft": producer}}
    graph["bindings"]["python"] = {
        "adapter": "command", "contract": "millrace-command/v1",
        "executable": sys.executable, "config": {},
    }
    graph["control_files"].append("producer.py")
    graph["seams"] = {"handoff": {
        "producer": {"plane": "producer", "node": "draft", "outcome": "READY"},
        "consumer": {"plane": "consumer", "node": "draft"},
    }}
    (workflow.parent / "producer.py").write_text(
        "import json\nprint(json.dumps({'outcome':'READY',"
        "'summary':'M4_SEAM_PAYLOAD'}))\n"
    )
    workflow.write_text(json.dumps(graph))
