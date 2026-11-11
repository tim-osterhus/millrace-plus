"""Installed resolver proof with an ordinary fake; root schedules all builds."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from prime_support import prime_flow
from test_distribution import Env
from test_distribution import artifacts as artifacts


def test_installed_prime_public_contract_and_core_standalone(
    artifacts,  # noqa: F811 - imported pytest fixture
    single_flow,
):
    workflow, project, home = prime_flow(single_flow)
    frozen = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        workflow, workflow.parent / "prime.json", workflow.parent / "task.txt",
    )}
    for key in ("plus_wheel", "plus_sdist_wheel"):
        with zipfile.ZipFile(artifacts[key]) as archive:
            assert "millrace_plus/router/prime.py" in archive.namelist()
            assert "millrace_plus/router/prime_configuration.py" in archive.namelist()
    matched = Env(artifacts["root"] / "prime-matched", [
        artifacts["core_sdist_wheel"], artifacts["plus_sdist_wheel"],
    ])
    result = matched.py(
        "import json,uuid,sys\nfrom pathlib import Path\n"
        "from importlib import metadata\n"
        "from millrace_plus.router import control,require_owned_process\n"
        "from millrace_plus.router.resolver import resolve_adapter\n"
        "assert require_owned_process() == 'owned-process-v1'\n"
        "for pair in [('pi_json','millrace-pi-json/v1'),"
        "('codex_exec','millrace-codex-exec/v1'),"
        "('claude_print','millrace-claude-print/v1'),"
        "('opencode_run','millrace-opencode-run/v1'),"
        "('droid_exec','millrace-droid-exec/v1'),"
        "('prime_print','millrace-prime-print/v1')]:\n"
        " assert resolve_adapter(*pair).adapter_id == pair[0]\n"
        f"workflow=Path({str(workflow)!r})\n"
        f"project=Path({str(project)!r});home=Path({str(home)!r})\n"
        "new=lambda:uuid.uuid4().hex\n"
        "receipt=control.create_instance(workflow,project,state_home=home,"
        "request_id=new(),expected_state_version=0)\n"
        "assert receipt['status']=='applied',receipt\n"
        "instance=receipt['instance_id']\n"
        "version=control.inspect_instance(instance,state_home=home)['state_version']\n"
        "receipt=control.start_run(instance,state_home=home,request_id=new(),"
        "expected_state_version=version)\n"
        "assert receipt['status']=='applied',receipt\n"
        "run=control.inspect_run(instance,receipt['run_id'],state_home=home)\n"
        "assert run['status']=='succeeded',run\n"
        "native=json.loads(workflow.read_text())['bindings']['native']['executable']\n"
        "peer_config=Path(native+'.json')\n"
        "options=json.loads(peer_config.read_text())\n"
        "options['before']='time.sleep(30)'\n"
        "peer_config.write_text(json.dumps(options))\n"
        "version=control.inspect_instance(instance,state_home=home)['state_version']\n"
        "failed_receipt=control.start_run(instance,state_home=home,request_id=new(),"
        "expected_state_version=version)\n"
        "assert failed_receipt['status']=='applied',failed_receipt\n"
        "failed_id=failed_receipt['run_id']\n"
        "failed=control.inspect_run(instance,failed_id,state_home=home)\n"
        "assert failed['pause_reason']=='writer_unknown',failed\n"
        "assert failed['completions'][0]['transport']=='timeout'\n"
        "inv=failed['invocations'][0]\n"
        "assert inv['adapter_evidence']['group_state']=='closed'\n"
        "reconciled=control.reconcile_invocation(instance,failed['id'],"
        "invocation_id=inv['id'],plane_id='main',stopped=True,"
        "reason='exact ordinary fake group observed closed',state_home=home,"
        "request_id=new(),expected_state_version=failed['state_version'])\n"
        "assert reconciled['status']=='applied',reconciled\n"
        "cancelled=control.cancel_run(instance,failed['id'],state_home=home,"
        "request_id=new())\n"
        "assert cancelled['status']=='applied',cancelled\n"
        "options.pop('before')\n"
        "peer_config.write_text(json.dumps(options))\n"
        "version=control.inspect_instance(instance,state_home=home)['state_version']\n"
        "fresh_receipt=control.start_run(instance,state_home=home,request_id=new(),"
        "expected_state_version=version)\n"
        "assert fresh_receipt['status']=='applied',fresh_receipt\n"
        "fresh=control.inspect_run(instance,fresh_receipt['run_id'],state_home=home)\n"
        "assert fresh['status']=='succeeded',fresh\n"
        "assert fresh['invocations'][0]['id']!=inv['id']\n"
        "old=control.inspect_run(instance,failed['id'],state_home=home)\n"
        "assert old['completions'][0]['transport']=='timeout'\n"
        "assert old['transitions']==[]\n"
        "Path(native).unlink()\n"
        "later=control.inspect_run(instance,receipt['run_id'],state_home=home)\n"
        "assert later['status']=='succeeded'\n"
        "import millrace_plus.router.prime as prime\n"
        "assert Path(prime.__file__).is_relative_to(Path(sys.prefix))\n"
        "version=metadata.version('millrace-plus')\n"
        "print(json.dumps({'run':receipt['run_id'],'version':version}))\n"
    )
    assert json.loads(result.stdout.splitlines()[-1])["run"]
    matched.install(artifacts["plus_sdist_wheel"], force_reinstall=True)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
               for p, h in frozen.items())
    standalone = Env(artifacts["root"] / "prime-core-only", [artifacts["core_wheel"]])
    standalone.py(
        "import importlib.util\nfrom millrace.router import control\n"
        "from millrace.router.execution import EXECUTION_CONTRACT\n"
        "assert EXECUTION_CONTRACT=='millrace-router-execution/v2'\n"
        "assert importlib.util.find_spec('millrace_plus') is None\n"
    )
