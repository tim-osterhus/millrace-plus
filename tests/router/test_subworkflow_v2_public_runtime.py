"""Actual OS/Core v2 mechanics; no full LAD service or native qualification."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

import pytest
from public_runtime_support import (
    command_node,
    graph,
    instance_hashes,
    write_json,
)
from public_runtime_support import (
    runtime as runtime,
)

from millrace_plus import catalog, subworkflows
from millrace_plus.lad_service import Service, claim_target
from millrace_plus.subworkflow_records import SubworkflowError, canonical, digest
from millrace_plus.subworkflow_v2 import SETTINGS, has_join_proof
from millrace_plus.workflow_os_client import WorkflowOSClient


def test_v2_public_os_two_children_share_home_and_preserve_sibling(runtime):
    definition = runtime.catalog / "echo"
    definition.mkdir()
    (definition / "echo.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "m=json.loads(Path('subworkflow-inputs.json').read_bytes())\n"
        "Path('answer.txt').write_bytes(m['inputs']['request_markdown'].encode())\n"
        "print(json.dumps({'outcome':'DONE','summary':'saved','refs':['answer.txt']}))\n"
    )
    value = graph({"step": command_node("echo.py", "DONE", {"finish": "succeeded"})})
    value["control_files"] = ["echo.py"]
    write_json(definition / "workflow.json", value)
    original = catalog.workflow_fingerprint(definition)
    first_request = runtime.call_request(definition, key="first", text="A\r\n")
    first, first_run = runtime.run_child(first_request)
    first_instance = runtime.client.read(
        first["instance_id"], deadline=time.monotonic() + 2
    )
    before = instance_hashes(runtime.home, first["instance_id"])
    first_output = Path(first["paths"]["project"]) / "answer.txt"
    assert first_output.read_bytes() == b"A\r\n"

    second_request = runtime.call_request(definition, key="second", text="B\n")
    second, _ = runtime.run_child(second_request)
    assert first["instance_id"] != second["instance_id"]
    assert first["run_id"] != second["run_id"]
    assert first["paths"]["project"] != second["paths"]["project"]
    assert first["paths"]["workflow"] != second["paths"]["workflow"]
    assert (Path(second["paths"]["project"]) / "answer.txt").read_bytes() == b"B\n"
    assert not (Path(first_request["call_home"]) / "first/state").exists()
    for saved in (first, second):
        assert (
            runtime.home / "instances" / saved["instance_id"] / "state.sqlite3"
        ).is_file()
        assert (
            catalog.workflow_fingerprint(Path(saved["paths"]["workflow"])) == original
        )
        retained = subworkflows.inspect(
            Path(saved["request"]["call_home"]), saved["request"]["call_key"]
        )
        assert retained["instance_id"] == saved["instance_id"]
    assert (
        runtime.client.read(first["instance_id"], deadline=time.monotonic() + 2)
        == first_instance
    )
    assert (
        runtime.client.read(
            first["instance_id"], first["run_id"], deadline=time.monotonic() + 2
        )
        == first_run
    )
    assert instance_hashes(runtime.home, first["instance_id"]) == before
    assert first_output.read_bytes() == b"A\r\n"
    assert catalog.workflow_fingerprint(definition) == original


def test_public_proven_create_expiry_preserves_live_start_join(runtime, monkeypatch):
    definition = runtime.catalog / "expire-create"
    definition.mkdir()
    (definition / "step.py").write_text(
        "import json,time\nfrom pathlib import Path\n"
        "Path('started').write_text('live')\n"
        "deadline=time.monotonic()+45\n"
        "while not Path('release').exists():\n"
        "    assert time.monotonic()<deadline\n"
        "    time.sleep(0.05)\n"
        "print(json.dumps({'outcome':'DONE','summary':'released','refs':[]}))\n"
    )
    value = graph(
        {"step": command_node("step.py", "DONE", {"finish": "succeeded"}, timeout=60)}
    )
    value["control_files"] = ["step.py"]
    write_json(definition / "workflow.json", value)
    request = runtime.call_request(definition, key="expired-create", text="Release.")
    request["observe_seconds"] = 2
    path = runtime.project / "expired-create-request.json"
    write_json(path, request)
    pair = (Path(request["call_home"]), request["call_key"])
    runtime.children.append(pair)
    release = pair[0] / pair[1] / "project/release"
    posted, observed = [], []
    submit, observe = WorkflowOSClient.submit, WorkflowOSClient.observe

    def track_submit(client, envelope, *, deadline):
        posted.append(copy.deepcopy(envelope))
        return submit(client, envelope, deadline=deadline)

    def track_observe(client, envelope, *, deadline):
        observed.append(copy.deepcopy(envelope))
        return observe(client, envelope, deadline=deadline)

    monkeypatch.setattr(WorkflowOSClient, "submit", track_submit)
    monkeypatch.setattr(WorkflowOSClient, "observe", track_observe)
    try:

        def live():
            assert subworkflows.advance(path)["outcome"] == "CHILD_RUNNING"
            saved = subworkflows.inspect(*pair)
            return (
                saved
                if saved["run_id"] is not None
                and (release.parent / "started").is_file()
                and has_join_proof(saved, "create")
                else None
            )

        before = runtime.until(live, seconds=15)
        create, start = before["envelopes"]["create"], before["envelopes"]["start"]
        assert create["instance_id"] is create["run_id"] is None
        assert not has_join_proof(before, "start")
        active = runtime.client.observe(start, deadline=time.monotonic() + 2)
        assert active["source"] == "os_owned_call"
        assert active["holder_joined"] is False
        assert active["core_receipt"]["status"] == "applied"
        workers = runtime.service.router_workers
        assert workers.observation_ttl == 300
        with workers._lock:
            call = workers._calls[workers._key("proof", create["request_id"])]
            assert call.request == create and call.joined
            assert call.finished_at is not None
            start_call = workers._calls[workers._key("proof", start["request_id"])]
            assert not start_call.joined
            # Age only this fixture's already joined call past its real TTL.
            # OS's ordinary retirement path performs eviction on the next GET.
            call.finished_at -= workers.observation_ttl + 1
        with pytest.raises(SubworkflowError, match="os_http_404"):
            runtime.client.observe(create, deadline=time.monotonic() + 2)
        after_expiry = len(observed)
        assert subworkflows.advance(path)["outcome"] == "CHILD_RUNNING"
        held = subworkflows.inspect(*pair)
        assert not has_join_proof(held, "start")
        still_active = runtime.client.observe(start, deadline=time.monotonic() + 2)
        assert still_active["holder_joined"] is False
        create_history = [
            r for r in held["os_observations"]
            if r["request_id"] == create["request_id"]
        ]
        assert create_history == [
            r
            for r in before["os_observations"]
            if r["request_id"] == create["request_id"]
        ]
        release.touch()

        def joined():
            row = runtime.client.observe(start, deadline=time.monotonic() + 2)
            return row if row["holder_joined"] is True else None

        actual_join = runtime.until(joined, seconds=15)
        assert not has_join_proof(subworkflows.inspect(*pair), "start")
        assert subworkflows.advance(path)["outcome"] == "CHILD_SUCCEEDED"
        saved, run = runtime.assert_child_closed(*pair)
        assert has_join_proof(saved, "start") and run["status"] == "succeeded"
        assert all(row["operation"] == "start_run" for row in observed[after_expiry:])
        assert [row["operation"] for row in posted] == ["create_instance", "start_run"]
        assert len({row["request_id"] for row in posted}) == 2
        assert workers.observation_ttl == 300
        write_json(
            runtime.root / "child-proof/proven-create-expiry.json",
            {
                "create_proof_before_expiry": before["os_observations"],
                "start_false_before_expiry": active,
                "start_false_after_expiry": still_active,
                "actual_start_join_after_release": actual_join,
                "final_history": saved["os_observations"],
                "posted": posted,
            },
        )
    finally:
        if release.parent.is_dir():
            release.touch()


def test_shared_lad_initializer_runs_from_core_capture_and_uses_pinned_target(runtime):
    bundled = catalog.bundled_root() / "lad"
    definition = runtime.catalog / "shared-init"
    shutil.copytree(bundled, definition, ignore=shutil.ignore_patterns("__pycache__"))
    original = json.loads((definition / "workflow.json").read_bytes())
    for name in ("PROVENANCE.md", "provenance.json"):
        (definition / name).unlink()
    (definition / "README.md").write_text(
        "Offline command-only bootstrap subset; no role or CLOSE/ACK qualification.\n"
        "All copied LAD scripts retain their bundled bytes. The graph and this\n"
        "fixture probe are local test changes with their own definition hash.\n"
    )
    probe = Path(__file__).parent / "fixtures/lad_bootstrap_probe.py"
    shutil.copyfile(probe, definition / "scripts/lad_bootstrap_probe.py")
    initializer = copy.deepcopy(
        original["planes"]["foreground"]["nodes"]["init_session"]
    )
    initializer["on"] = {
        name: (
            {"goto": "useful_probe"}
            if name == "NEW_SESSION"
            else {"pause": {"reason": "unexpected_bootstrap"}}
        )
        for name in initializer["outcomes"]
    }
    value = graph({}, start="init_session")
    value["control_files"] = original["control_files"] + [
        "scripts/lad_bootstrap_probe.py"
    ]
    value["planes"] = {
        "foreground": {
            "workspace": "foreground",
            "start": "init_session",
            "nodes": {
                "init_session": initializer,
                "useful_probe": command_node(
                    "scripts/lad_bootstrap_probe.py", "DONE", {"finish": "succeeded"}
                ),
            },
        },
        "learning": {
            "workspace": "learning",
            "start": "offline_learning",
            "nodes": {
                "offline_learning": command_node(
                    "scripts/lad_bootstrap_probe.py",
                    "DONE",
                    {"finish": "succeeded"},
                    args=("--learning",),
                ),
            },
        },
    }
    write_json(definition / "workflow.json", value)
    script_bytes = {
        str(path.relative_to(bundled)): path.read_bytes()
        for path in (bundled / "scripts").glob("*.py")
    }
    assert all(
        (definition / name).read_bytes() == raw for name, raw in script_bytes.items()
    )
    (runtime.target / "input.txt").write_bytes(b"41\n")
    home = runtime.root / "service-records"
    home.mkdir()
    request = runtime.call_request(
        definition,
        key="placeholder",
        text="Increment 41 to 42.\r\n",
        call_home=home / "calls",
    )
    parent = runtime.client.read(runtime.parent, deadline=time.monotonic() + 2)
    info = runtime.target.stat()
    config = {
        "format": "millrace-plus-lad-service/v1",
        "service_id": uuid.uuid4().hex,
        "service_home": str(home),
        "target_root": str(runtime.target),
        "target_identity": {
            "path": str(runtime.target),
            "device": info.st_dev,
            "inode": info.st_ino,
        },
        "os_home": "proof",
        "os_profile": runtime.profile,
        "worker_definition": request["definition"],
        "settings_sha256": digest({name: parent[name] for name in SETTINGS}),
    }
    write_json(home / "service.json", config)
    service = Service(home)
    service.intake.initialize()
    claim_target(
        config,
        protected_roots=(
            home,
            runtime.home,
            runtime.project,
            runtime.workflow,
            runtime.catalog,
        ),
    )
    task_file = runtime.root / "task.md"
    task_file.write_bytes(request["inputs"]["request_markdown"].encode())
    task = service.submit("bootstrap", task_file)["task_id"]

    def first_claim():
        assert runtime.children == []
        assert not (home / "calls").exists()
        assert [row["status"] for row in service.intake.tasks()] == ["queued"]

    claim = service.claim_next(writer_guard=first_claim)
    assert claim is not None
    assert set(claim) == {
        "task_id",
        "request_sha256",
        "definition",
        "call_key",
        "call_home",
        "output_ref",
        "create_request_id",
        "start_request_id",
    }
    service.materialize_task(task)
    target_record = {
        "format": "millrace-lad-target/v1",
        "service_id": service.service_id,
        "task_id": task,
        "request_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest(),
        "target_root": str(runtime.target),
        "target_identity": config["target_identity"],
        "claim_sha256": digest(claim),
        "parent_instance_id": runtime.parent,
        "parent_run_id": runtime.parent_run,
        "worker_definition_sha256": request["definition"]["sha256"],
    }
    request.update(call_key=claim["call_key"], output_ref=claim["output_ref"])
    request["inputs"]["lad_target"] = target_record
    assert not (home / "calls" / claim["call_key"] / "project/lad-target.json").exists()
    saved, run = runtime.run_child(
        request,
        operation=lambda path: service.advance_task(
            task,
            path,
            cwd=runtime.project,
        ),
    )
    project, workflow = (
        Path(saved["paths"]["project"]),
        Path(saved["paths"]["workflow"]),
    )
    assert (project / "lad-target.json").read_bytes() == canonical(
        target_record
    ) + b"\n"
    assert (project / "foreground/lad-state/session.json").is_file()
    init = [row for row in run["invocations"] if row["node"] == "init_session"]
    assert len(init) == 1 and init[0]["writer_state"] == "stopped"
    init = init[0]
    capture = (
        runtime.home
        / "instances"
        / saved["instance_id"]
        / "invocations"
        / init["id"]
        / "controls"
    )
    script = capture / "scripts/init_session.py"
    assert init["argv"][1] == str(script)
    assert script != workflow / "scripts/init_session.py"
    raw = script_bytes["scripts/init_session.py"]
    assert (
        script.read_bytes()
        == (workflow / "scripts/init_session.py").read_bytes()
        == raw
    )
    assert (
        base64.b64decode(
            run["draft_revision"]["files"]["scripts/init_session.py"], validate=True
        )
        == raw
    )
    assert init["revision_id"] == run["revision_id"] == run["startup_revision_id"]
    completed = [
        row for row in run["completions"] if row["invocation_id"] == init["id"]
    ]
    assert len(completed) == 1
    assert json.loads(completed[0]["stdout"])["outcome"] == "NEW_SESSION"
    proof = json.loads((project / "foreground/bootstrap-proof.json").read_bytes())
    ledger = json.loads((project / "foreground/lad-state/session.json").read_bytes())
    assert proof["target_root"] == str(runtime.target)
    assert proof["claim_sha256"] == digest(claim)
    assert proof["session_id"] == ledger["session_id"] and ledger["terminal"] is None
    assert (runtime.target / "answer.txt").read_bytes() == b"42\n"
    assert proof["answer_sha256"] == hashlib.sha256(b"42\n").hexdigest()
    assert (home / "tasks" / task / "request.md").read_bytes() == task_file.read_bytes()
    assert (home / "tasks" / task / "claim.json").read_bytes() == canonical(
        claim
    ) + b"\n"
    assert service.intake.tasks(task)[0]["status"] == "claimed"
    assert (runtime.target / ".millrace-plus-target-owner.json").is_file()
    assert all(
        (workflow / name).read_bytes() == content
        for name, content in script_bytes.items()
    )
