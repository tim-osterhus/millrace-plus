"""Installed M4 proof from explicit prebuilt artifacts and a loopback-only peer.

No artifact-building fixture is used. Each child runs python -I outside the
source tree and checks both package origins before calling public controls.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from millforge_support import native_profile
from test_distribution import Env

INPUTS = (
    "MILLFORGE_M4_CORE_WHEEL", "MILLFORGE_M4_PLUS_WHEEL",
    "MILLFORGE_M4_BIN", "MILLFORGE_M4_NODE",
)


@pytest.fixture(scope="module")
def installed_millforge(tmp_path_factory):
    selected = {key: os.environ.get(key) for key in INPUTS}
    if not any(selected.values()):
        pytest.skip("installed M4 proof requires explicit prebuilt artifact inputs")
    assert all(selected.values()), selected
    paths = {key: Path(value).absolute() for key, value in selected.items()}
    assert all(path.is_file() for path in paths.values()), paths
    root = tmp_path_factory.mktemp("installed-millforge")
    env = Env(root / "env", [paths[INPUTS[0]], paths[INPUTS[1]]])
    script = root / "installed-proof.py"
    script.write_text(INSTALLED_PROOF)
    profile = root / "profile-template.json"
    profile.write_text(json.dumps(native_profile()))
    origins = root / "expected-origins.json"
    expected = {}
    members = {
        INPUTS[0]: ["millrace/router/execution.py", "millrace/router/process.py"],
        INPUTS[1]: ["millrace_plus/router/millforge.py",
                    "millrace_plus/router/millforge_configuration.py",
                    "millrace_plus/router/resolver.py"],
    }
    for key, names in members.items():
        with zipfile.ZipFile(paths[key]) as archive:
            expected.update({name: hashlib.sha256(archive.read(name)).hexdigest()
                             for name in names})
    origins.write_text(json.dumps(expected))
    return env, paths, script, profile, origins


@pytest.mark.parametrize("scenario", [
    "negative", "agent", "handoff", "failed_record",
    "missing", "corrupt", "cancel_fresh",
])
def test_installed_public_millforge_graph(installed_millforge, scenario):
    env, paths, script, profile, origins = installed_millforge
    root = env.cwd / scenario
    child_env = {
        "PATH": f"{paths[INPUTS[3]].parent}:/usr/bin:/bin",
        "HOME": str(env.home), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [str(env.python), "-I", str(script), scenario, str(root),
         str(paths[INPUTS[2]]), str(paths[INPUTS[3]]), str(profile),
         str(env.root), str(origins)],
        cwd=env.cwd, env=child_env, text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, (scenario, result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["scenario"] == scenario
    assert report["peer_closed"] is True
    assert report["provider_requests"] > 0
    output = os.environ.get("MILLFORGE_M4_EVIDENCE_DIR")
    if output:
        destination = Path(output) / scenario
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(root, destination)
        (destination / "subprocess.json").write_text(json.dumps({
            "exit": result.returncode, "stdout": report, "stderr": result.stderr,
        }, indent=2))


INSTALLED_PROOF = r'''
import base64
import ctypes
import hashlib
import http.server
import importlib.metadata
import json
import os
import shlex
import signal
import subprocess
import sys
import sysconfig
import threading
import time
import uuid
from pathlib import Path

from millrace.router import execution
from millrace_plus.router import control, millforge
from millrace_plus.router.resolver import resolve_adapter

scenario, root_arg, native, node, template, env_root, expected_origins = sys.argv[1:]
root = Path(root_arg)
root.mkdir()
assert Path(execution.__file__).resolve().is_relative_to(Path(env_root).resolve())
assert Path(millforge.__file__).resolve().is_relative_to(Path(env_root).resolve())
adapter = resolve_adapter('millforge_run', 'millrace-millforge-run/v1')
assert adapter.adapter_id == 'millforge_run'
expected = json.loads(Path(expected_origins).read_text())
for member, digest in expected.items():
    installed = Path(sysconfig.get_paths()['purelib']) / member
    assert hashlib.sha256(installed.read_bytes()).hexdigest() == digest
assert importlib.metadata.version('millrace-ai') == '0.23.2'
assert importlib.metadata.version('millrace-plus') == '0.23.2'
assert Path(native).is_absolute() and Path(node).is_absolute()
assert sys.platform == 'linux'
package = next(parent / 'package.json' for parent in Path(native).resolve().parents
               if (parent / 'package.json').is_file()
               and json.loads((parent / 'package.json').read_text()).get('name')
               == '@millrace-ai/millforge')
assert json.loads(package.read_text())['version'] == '0.2.0'
node_version = subprocess.run(
    [node, '--version'], check=True, capture_output=True, text=True).stdout.strip()
assert int(node_version.split('.')[0][1:]) >= 22
# Reap test-owned orphaned native workers if the foreground CLI is killed.
# This is a Linux fixture ownership aid, never an adapter process-adoption API.
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
report = {'scenario': scenario, 'node': node_version,
          'core_origin': execution.__file__, 'plus_origin': millforge.__file__,
          'native_cli': str(Path(native).resolve()),
          'native_cli_sha256': hashlib.sha256(Path(native).read_bytes()).hexdigest(),
          'installed_module_hashes': expected}
views = []
requests = []
peer_mode = scenario
marker = None


def uid():
    return uuid.uuid4().hex


def until(call, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = call()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError('fixture boundary did not arrive')


class Peer(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        assert self.path == '/v1/chat/completions'
        assert 'Authorization' not in self.headers and 'x-api-key' not in self.headers
        size = int(self.headers['Content-Length'])
        assert size <= 2 * 1024 * 1024
        body = json.loads(self.rfile.read(size))
        assert body['model'] == 'operator-model'
        text = json.dumps(body, ensure_ascii=False)
        requests.append({'handoff_seen': 'M4_SEAM_PAYLOAD' in text,
                         'tool_result_seen': 'TOOL_PROOF_TEXT' in text})
        tool = None
        tool_seen = any(m.get('role') == 'tool' for m in body['messages'])
        if peer_mode == 'agent' and not tool_seen:
            tool = {'name': 'read', 'arguments': json.dumps({'path': 'evidence.txt'})}
        elif peer_mode == 'cancel_fresh':
            command = (shlex.quote(sys.executable) + ' -c ' +
                       shlex.quote(
                           "import json,os,time;from pathlib import Path;"
                           "Path('cancel-marker.json').write_text(json.dumps({"
                           "'pid':os.getpid(),'ppid':os.getppid(),"
                           "'pgid':os.getpgrp()}));"
                           "time.sleep(60)"))
            tool = {'name': 'bash', 'arguments': json.dumps({'command': command})}
        if tool:
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'm4-tool',
                     'type': 'function', 'function': tool}]}
            stop = 'tool_calls'
        else:
            outcome = 'REJECT' if peer_mode == 'negative' else 'READY'
            if peer_mode == 'handoff':
                outcome = 'READY' if 'M4_SEAM_PAYLOAD' in text else 'REJECT'
            answer = ('not a JSON object' if peer_mode == 'failed_record'
                      else json.dumps({'outcome': outcome}))
            delta = {'role': 'assistant', 'content': answer}
            stop = 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        def emit(delta, finish=None):
            frame = {'id': 'm4-message', 'object': 'chat.completion.chunk',
                     'created': 1,
                     'model': 'operator-model', 'choices': [{'index': 0, 'delta': delta,
                                                           'finish_reason': finish}]}
            self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
        emit(delta)
        emit({}, stop)
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Peer)
server_thread = threading.Thread(target=server.serve_forever)
server_thread.start()
endpoint = 'http://127.0.0.1:' + str(server.server_address[1]) + '/v1'


def node_config(binding='native'):
    return {'invoke': {'binding': binding, 'config': {'prompt': 'task.txt'},
                       'timeout_seconds': 40},
            'result': 'json-v1', 'outcomes': ['READY', 'REJECT'],
            'on': {'READY': {'finish': 'succeeded'}, 'REJECT': {'finish': 'failed'}},
            'on_error': {'pause': {'reason': 'native_error', 'resume': 'step'}},
            'on_unknown': {'pause': {'reason': 'native_unknown', 'resume': 'step'}}}


def prepare(name, mode='generate', tools=None, handoff=False, sabotage=None):
    folder = root / name
    folder.mkdir()
    workflow = folder / 'workflow'
    workflow.mkdir()
    project = folder / 'project'
    project.mkdir()
    home = folder / 'state'
    profile = json.loads(Path(template).read_text())
    profile['model']['base_url'] = endpoint
    profile['mode'] = mode
    profile['tools'] = tools or []
    profile['limits']['model_requests'] = 1 if mode == 'generate' else 3
    profile['limits']['tool_calls'] = 0 if mode == 'generate' else 2
    (workflow / 'profile.json').write_text(json.dumps(profile))
    (workflow / 'task.txt').write_text('Return only the declared structured outcome.')
    (project / 'evidence.txt').write_text('TOOL_PROOF_TEXT')
    executable = native
    if sabotage:
        wrapper = folder / 'fixture-forwarder'
        wrapper.write_text('#!' + sys.executable + '\n' +
            'import pathlib,subprocess,sys\n' +
            'result=subprocess.run([' + repr(native) + ',*sys.argv[1:]])\n' +
            "if sys.argv[1]=='run' and result.returncode==0:\n" +
            " out=pathlib.Path(sys.argv[sys.argv.index('--output-dir')+1])\n" +
            (" (out/'result.json').unlink()\n" if sabotage == 'missing' else
             " (out/'trace.jsonl').write_text('{corrupted\\n')\n") +
            'sys.exit(result.returncode)\n')
        wrapper.chmod(0o700)
        executable = str(wrapper)
    graph = {'format': 'millrace-router/v3',
             'limits': {'max_invocations': 6, 'max_elapsed_seconds': 120,
                        'max_parallel_invocations': 1},
             'bindings': {'native': {'adapter': 'millforge_run',
                'contract': 'millrace-millforge-run/v1', 'executable': executable,
                'config': {'profile': 'profile.json', 'assets': {}}}},
             'control_files': ['profile.json', 'task.txt'],
             'planes': {'main': {'workspace': '.', 'start': 'step',
                                  'nodes': {'step': node_config()}}}}
    if handoff:
        (project / 'producer').mkdir()
        (project / 'consumer').mkdir()
        graph['planes']['consumer'] = graph['planes'].pop('main')
        graph['planes']['consumer']['workspace'] = 'consumer'
        producer = {'invoke': {'binding': 'python', 'config': {'script': 'producer.py'},
                               'timeout_seconds': 5},
                    'result': 'json-v1', 'outcomes': ['READY'],
                    'on': {'READY': {'finish': 'succeeded'}},
                    'on_error': {'finish': 'failed'},
                    'on_unknown': {'finish': 'failed'}}
        graph['planes']['producer'] = {'workspace': 'producer', 'start': 'step',
                                       'nodes': {'step': producer}}
        graph['bindings']['python'] = {'adapter': 'command',
                                       'contract': 'millrace-command/v1',
                                       'executable': sys.executable, 'config': {}}
        graph['control_files'].append('producer.py')
        (workflow / 'producer.py').write_text(
            "import json;print(json.dumps({'outcome':'READY',"
            "'summary':'M4_SEAM_PAYLOAD'}))\n")
        graph['seams'] = {'handoff': {'producer': {'plane': 'producer', 'node': 'step',
                                                 'outcome': 'READY'},
                                     'consumer': {'plane': 'consumer', 'node': 'step'}}}
    file = workflow / 'workflow.json'
    file.write_text(json.dumps(graph))
    made = control.create_instance(file, project, state_home=home, request_id=uid(),
        expected_state_version=0, path=tuple(os.environ['PATH'].split(os.pathsep)))
    assert made['status'] == 'applied', made
    return str(made['instance_id']), project, home


def start(instance, home, on_started=None):
    before = control.inspect_instance(instance, state_home=home)
    receipt = control.start_run(instance, state_home=home, request_id=uid(),
        expected_state_version=before['state_version'], on_started=on_started)
    assert receipt['status'] == 'applied', receipt
    view = control.inspect_run(instance, str(receipt['run_id']), state_home=home)
    views.append(view)
    return view


def native_invocation(view):
    return next(row for row in view['invocations']
                if row['launch']['adapter_id'] == 'millforge_run')


def local_records(view):
    invocation = native_invocation(view)
    directory = Path(invocation['launch']['details']['output_path'])
    result = json.loads((directory / 'result.json').read_text())
    assert result['schema'] == 'millforge-result/v3'
    assert result['request_id'] == invocation['id']
    capture = json.loads((directory / 'capture.json').read_text())
    assert capture['versions']['node'] == node_version.removeprefix('v')
    return result, json.loads((directory / 'request.json').read_text())


def record_identity(pid):
    raw = Path('/proc/' + str(pid) + '/stat').read_text().rsplit(') ', 1)[1].split()
    return {'pid': pid, 'pgid': int(raw[2]), 'start_ticks': raw[19]}


def exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def cleanup_owned(records):
    groups = set()
    observed_groups = {row['pgid'] for row in records}
    for row in records:
        if not exists(row['pid']):
            continue
        current = record_identity(row['pid'])
        assert current == row, (current, row)
        assert row['pgid'] != os.getpgrp()
        groups.add(row['pgid'])
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    def closed():
        while True:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    break
            except ChildProcessError:
                break
        for group in observed_groups:
            try:
                os.killpg(group, 0)
                return False
            except ProcessLookupError:
                pass
        return all(not exists(row['pid']) for row in records)
    until(closed, seconds=5)
    report['owned_processes'] = records
    report['owned_pids_absent'] = True
    report['owned_groups_absent'] = sorted(observed_groups)


try:
    if scenario == 'cancel_fresh':
        instance, project, home = prepare('cancel', 'agent', ['bash'])
        boundary = {}
        finished = {}
        started = threading.Event()
        def announced(receipt):
            boundary['run_id'] = str(receipt['run_id'])
            started.set()
        def execute():
            try:
                finished['view'] = start(instance, home, announced)
            except BaseException as error:
                finished['error'] = error
        runner = threading.Thread(target=execute)
        runner.start()
        records = []
        try:
            assert started.wait(5)
            marker_file = project / 'cancel-marker.json'
            until(lambda: marker_file.exists())
            marker = json.loads(marker_file.read_text())
            active = control.inspect_run(instance, boundary['run_id'], state_home=home)
            invocation = native_invocation(active)
            cli_pid = int(invocation['pid'])
            workers = [int(item) for item in Path(
                '/proc/' + str(cli_pid) + '/task/' + str(cli_pid) +
                '/children').read_text().split()]
            assert workers
            pids = {cli_pid, *workers, marker['pid'], marker['ppid']}
            records = [record_identity(pid) for pid in pids]
            receipt = control.cancel_run(instance, boundary['run_id'],
                                         state_home=home, request_id=uid())
            assert receipt['status'] in {'applied', 'queued'}, receipt
            runner.join(15)
            assert not runner.is_alive()
            assert 'error' not in finished, finished
            held = finished['view']
            completion = held['completions'][0]
            assert completion['transport'] == 'cancelled', held
            assert completion['stdout'] == ''
            assert completion['effective_writer'] == 'may_live'
            assert native_invocation(held)['adapter_evidence']['inspection'] is None
        finally:
            if runner.is_alive() and boundary.get('run_id'):
                control.cancel_run(instance, boundary['run_id'],
                                   state_home=home, request_id=uid())
                runner.join(15)
            cleanup_owned(records)
        current = control.inspect_run(instance, boundary['run_id'], state_home=home)
        accepted = control.reconcile_invocation(
            instance, boundary['run_id'], plane_id='main',
            invocation_id=invocation['id'], stopped=True,
            reason='exact fixture PID/start identities closed and reaped',
            state_home=home,
            request_id=uid(), expected_state_version=current['state_version'])
        assert accepted['status'] == 'applied', accepted
        control.cancel_run(instance, boundary['run_id'],
                           state_home=home, request_id=uid())
        peer_mode = 'fresh'
        fresh = start(instance, home)
        assert fresh['status'] == 'succeeded', fresh
        assert native_invocation(fresh)['id'] != invocation['id']
        report['cancel_transport'] = completion['transport']
        report['raw_writer'] = completion['effective_writer']
        report['fresh_status'] = fresh['status']
    else:
        mode = 'agent' if scenario == 'agent' else 'generate'
        instance, project, home = prepare(
            'run', mode, ['read'] if mode == 'agent' else [],
            handoff=scenario == 'handoff',
            sabotage=scenario if scenario in {'missing', 'corrupt'} else None)
        view = start(instance, home)
        invocation = native_invocation(view)
        completion = next(row for row in view['completions']
                          if row['invocation_id'] == invocation['id'])
        evidence = invocation['adapter_evidence']
        if scenario in {'missing', 'corrupt'}:
            assert completion['transport'] == 'protocol_failure', view
            assert completion['stdout'] == ''
            assert completion['effective_writer'] == 'may_live'
            assert evidence['accepted'] is None
            assert evidence['failure_code'] == 'millforge_inspect_failed'
        elif scenario == 'failed_record':
            assert completion['transport'] == 'execution_failure', view
            assert view['status'] == 'paused'
            assert view['planes']['main']['pause_reason'] == 'native_error'
            assert completion['stdout'] == ''
            assert completion['effective_writer'] == 'stopped'
            assert evidence['main']['exit_code'] == 3
            assert evidence['accepted']['status'] == 'failed'
            assert evidence['failure_code'] == 'millforge_runtime_failed'
            saved, request = local_records(view)
            assert saved['diagnostic']['code'] == 'INVALID_TERMINAL'
            assert all(saved['closure'][key] == 'closed'
                       for key in ('worker', 'tools', 'writers'))
        else:
            expected_status = 'failed' if scenario == 'negative' else 'succeeded'
            assert view['status'] == expected_status, view
            assert completion['classification'] == 'semantic'
            assert completion['transport'] == 'completed'
            assert completion['effective_writer'] == 'stopped'
            saved, request = local_records(view)
            assert saved['status'] == 'completed'
            if scenario == 'negative':
                assert completion['outcome'] == 'REJECT'
                assert saved['domain_outcome'] == 'REJECT'
            if scenario == 'agent':
                assert saved['counts']['tool_calls'] == 1
                assert requests[-1]['tool_result_seen']
            if scenario == 'handoff':
                supplied = base64.b64decode(
                    invocation['launch']['seam_input']['content']).decode('utf-8')
                assert request['context'] == [supplied]
                assert requests[-1]['handoff_seen']
                assert view['seam_acknowledgements']
            else:
                assert request['context'] == []
        report.update(status=view['status'], transport=completion['transport'],
                      writer=completion['effective_writer'],
                      failure=evidence['failure_code'])
finally:
    server.shutdown()
    server.server_close()
    server_thread.join(5)
    assert not server_thread.is_alive()
report['provider_requests'] = len(requests)
report['peer_closed'] = True
report['observations'] = requests
for index, view in enumerate(views):
    file = root / ('public-view-' + str(index) + '.json')
    file.write_text(json.dumps(view, indent=2))
(root / 'report.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report))
'''
