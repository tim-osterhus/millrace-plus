"""Actual public local setup/controls; command workers only, no native routes.

These functional journeys do not qualify 1-day/30-day retained-history costs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from public_runtime_support import runtime as runtime

from millrace_plus import catalog
from millrace_plus import lad_service_disposition as disposition
from millrace_plus import lad_service_runtime as service_runtime
from millrace_plus.lad_service import Service
from millrace_plus.lad_service_runtime import initialize
from millrace_plus.local_intake import canonical
from millrace_plus.subworkflow_persistent_records import validate_proof
from millrace_plus.subworkflow_records import digest
from millrace_plus.subworkflow_v2 import closed, has_join_proof


def test_public_setup_pin_handshake_and_queued_fifo_cancel(runtime):
    definition = runtime.root / "worker-definition.json"
    definition.write_bytes(
        canonical(
            {
                "selector": "bundled:lad",
                "version": catalog._plus_version(),
                "sha256": catalog.workflow_fingerprint(catalog.bundled_root() / "lad"),
            }
        )
        + b"\n"
    )
    home = runtime.root / "persistent-service"
    result = initialize(
        home, runtime.target, Path(runtime.profile["path"]), definition, runtime.home
    )
    assert result["activation_ready"] is True
    service = Service(home)
    intent, record = service.intake.lifecycle()
    assert intent["core_state_home"] == str(runtime.home)
    local = __import__("millrace_plus.router.control", fromlist=["inspect_instance"])
    parent = local.inspect_instance(result["instance_id"], state_home=runtime.home)
    remote = runtime.client.read(result["instance_id"], deadline=time.monotonic() + 2)
    for name in (
        "instance_id",
        "workflow_file",
        "project_root",
        "state_version",
        "path",
        "env_allowlist",
        "host_paths",
        "runtime_defaults",
        "operator_policy",
    ):
        assert (
            local.inspect_instance(result["instance_id"], state_home=runtime.home)[name]
            == remote[name]
        )
    assert parent["workflow_file"] == str(home / "workflow/workflow.json")
    assert record["start"]["reservation"] == "unsent"
    first, second = runtime.root / "first.md", runtime.root / "second.md"
    first.write_bytes(b"First original task\r\n")
    second.write_bytes(b"Second original task\n")
    one = service.submit("first", first)
    two = service.submit("second", second)
    assert (one["sequence"], two["sequence"]) == (1, 2)
    decision = uuid.uuid4().hex
    cancelled = service.cancel(one["task_id"], decision)
    assert cancelled["disposition"] == "cancelled-before-start"
    assert service.cancel(one["task_id"], decision) == cancelled
    rows = Service(home).intake.tasks()
    assert [row["status"] for row in rows] == ["cancelled", "queued"]
    assert [row["request_bytes"] for row in rows] == [
        first.read_bytes(),
        second.read_bytes(),
    ]
    assert rows[0]["claim"] is None and rows[1]["claim"] is None
    # The registered parent remains unused: this journey starts no LAD/provider.
    assert (
        local.inspect_instance(result["instance_id"], state_home=runtime.home)["runs"]
        == []
    )
    assert digest(service.config) == digest(Service(home).config)


def test_public_two_command_tasks_preserve_target_failure_and_explicit_abandonment(
    runtime, monkeypatch
):
    """Real Core/OS plus command peers; this does not qualify LAD role efficacy."""
    import copy

    from public_runtime_support import command_node, graph, write_json

    from millrace_plus import lad_service_runtime as service_runtime
    from millrace_plus.router import control

    # Source-test commands need the fixture's exact package roots. Installed
    # artifact journeys use their installed packages independently.
    ordinary_settings = service_runtime._settings
    monkeypatch.setattr(
        service_runtime,
        "_settings",
        lambda target: (
            ordinary_settings(target) | {"env_allowlist": ["HOME", "PYTHONPATH"]}
        ),
    )
    worker = runtime.catalog / "offline-failure"
    worker.mkdir()
    (worker / "step.py").write_text(
        "import json\nfrom pathlib import Path\n"
        'm=json.loads(Path("subworkflow-inputs.json").read_bytes())\n'
        't=Path(m["inputs"]["lad_target"]["target_root"])/"changes.txt"\n'
        'prior=t.read_bytes() if t.exists() else b""\n'
        't.write_bytes(prior+m["inputs"]["request_markdown"].encode())\n'
        'print(json.dumps({"outcome":"DONE",'
        '"summary":"command failure fixture","refs":[]}))\n'
    )
    definition = graph({"step": command_node("step.py", "DONE", {"finish": "failed"})})
    definition["control_files"] = ["step.py"]
    write_json(worker / "workflow.json", definition)
    sha = catalog.workflow_fingerprint(worker)
    pin = runtime.root / "offline-definition.json"
    write_json(
        pin,
        {
            "selector": "local:offline-failure",
            "version": "sha256:" + sha,
            "sha256": sha,
        },
    )
    home = runtime.root / "two-task-service"
    initialized = initialize(
        home, runtime.target, Path(runtime.profile["path"]), pin, runtime.home
    )
    service = Service(home)
    for key, text in (("first", b"A\r\n"), ("second", b"B\n")):
        request = runtime.root / (key + ".md")
        request.write_bytes(text)
        service.submit(key, request)
    started = runtime.send_parent(initialized["start_request"])
    parent_id, parent_run = initialized["instance_id"], started["run_id"]
    assert started["status"] == "applied"
    engine = service_runtime.Runtime(service)
    engine.parent()
    outcomes = []
    try:
        for index in range(2):

            def held():
                rows = service.intake.tasks()
                parent = control.inspect_run(
                    parent_id, parent_run, state_home=runtime.home
                )
                return (
                    rows[index]
                    if rows[index]["status"] == "held" and parent["status"] == "paused"
                    else None
                )

            task = runtime.until(held, seconds=30)
            journal, call = engine.call(task)
            child_run = control.inspect_run(
                call["instance_id"], call["run_id"], state_home=runtime.home
            )
            assert child_run["status"] == "failed"
            evidence = runtime.root / (str(index) + "-abandonment.json")
            write_json(
                evidence,
                {
                    "format": "millrace-plus-lad-abandonment-intent/v1",
                    "acknowledge_abandonment": True,
                    "expected_task_version": task["version"],
                    "expected_state_version": child_run["state_version"],
                },
            )
            answer = engine.decide(
                task["task_id"], "acknowledge-failed", uuid.uuid4().hex, evidence
            )
            assert answer["disposition"] == "abandoned-failed"
            outcomes.append((task["task_id"], call["instance_id"], call["run_id"]))
            if index == 0:
                parent = control.inspect_run(
                    parent_id, parent_run, state_home=runtime.home
                )
                resume = runtime.root / "recorded-parent-resume.json"
                write_json(
                    resume,
                    service_runtime._env(
                        "resume_run",
                        uuid.uuid4().hex,
                        instance=parent_id,
                        run=parent_run,
                        expected=parent["state_version"],
                    ),
                )
                assert parent["run_gate"] is None
                assert parent["planes"]["service"]["status"] == "paused"
                selected = json.loads(resume.read_bytes())
                selected.update(operation="resume_plane", plane_id="service")
                write_json(resume, selected)

                def resumed():
                    answer = engine.parent_control(resume)
                    receipt = answer["receipt"]
                    if receipt is not None and receipt["status"] == "refused":
                        pytest.fail(str(receipt))
                    return (
                        receipt if receipt and receipt["status"] == "applied" else None
                    )

                runtime.until(resumed, seconds=10)
        assert (
            len({row[1] for row in outcomes}) == 2
            and len({row[2] for row in outcomes}) == 2
        )
        rows = Service(home).intake.tasks()
        assert [row["request_bytes"] for row in rows] == [b"A\r\n", b"B\n"]
        assert [row["status"] for row in rows] == ["failed", "failed"]
        assert (runtime.target / "changes.txt").read_bytes() == b"A\r\nB\n"
        stop_id = uuid.uuid4().hex

        def stopped():
            result = service_runtime.Runtime(Service(home)).stop(stop_id)
            return result if result["status"] == "stopped" else None

        assert runtime.until(stopped, seconds=20)["status"] == "stopped"
    finally:
        # Preserve service-aware cleanup obligations and their exact IDs on failure.
        state = Service(home).status()
        write_json(runtime.root / "two-task-aftermath.json", copy.deepcopy(state))


def idle_service(runtime, monkeypatch):
    from millrace_plus import lad_service_runtime as service_runtime
    from millrace_plus.router import control

    # This journey accepts no task, hence never dispatches the pinned LAD worker.
    (runtime.project / "release").write_text("release unrelated fixture parent")
    ordinary = service_runtime._settings
    monkeypatch.setattr(
        service_runtime,
        "_settings",
        lambda target: ordinary(target) | {"env_allowlist": ["HOME", "PYTHONPATH"]},
    )
    pin = runtime.root / "idle-worker.json"
    pin.write_bytes(
        canonical(
            {
                "selector": "bundled:lad",
                "version": catalog._plus_version(),
                "sha256": catalog.workflow_fingerprint(catalog.bundled_root() / "lad"),
            }
        )
        + b"\n"
    )
    home = runtime.root / "idle-service"
    initialized = initialize(
        home, runtime.target, Path(runtime.profile["path"]), pin, runtime.home
    )
    receipt = runtime.send_parent(initialized["start_request"])
    runtime.until(
        lambda: Service(home).intake.lifecycle()[1]["run_id"] == receipt["run_id"],
        seconds=15,
    )
    engine = service_runtime.Runtime(Service(home))
    engine.parent()

    def waiter():
        view = control.inspect_run(
            initialized["instance_id"], receipt["run_id"], state_home=runtime.home
        )
        active = view["planes"]["service"]["active_invocation"]
        matches = [
            row
            for row in view["invocations"]
            if row["id"] == active
            and row["node"] == "wait_intake"
            and row["writer_state"] == "running"
        ]
        return (
            matches[0]["id"]
            if len(matches) == 1 and (home / "intake.sock").exists()
            else None
        )

    return home, engine, initialized["instance_id"], receipt["run_id"], waiter


def test_public_idle_pause_closes_socket_then_same_run_resume_and_wake(
    runtime, monkeypatch
):
    from public_runtime_support import write_json

    from millrace_plus import lad_service_runtime as service_runtime
    from millrace_plus.router import control
    from millrace_plus.subworkflow_v2 import closed

    home, engine, parent, run_id, waiter = idle_service(runtime, monkeypatch)
    stop_id = uuid.uuid4().hex
    stopped = False
    try:
        first = runtime.until(waiter, seconds=20)
        pause = runtime.root / "idle-pause.json"
        write_json(
            pause,
            service_runtime._env(
                "pause_run", uuid.uuid4().hex, instance=parent, run=run_id
            ),
        )
        engine.parent_control(pause)

        def paused():
            # Pending transport is observed again under the same saved request.
            service_runtime.Runtime(Service(home)).parent_control(pause)
            view = control.inspect_run(parent, run_id, state_home=runtime.home)
            return (
                view
                if view["status"] == "paused" and closed(view, attention=True)
                else None
            )

        runtime.until(paused, seconds=90)
        assert not (home / "intake.sock").exists()
        runtime.until(
            lambda: service_runtime.Runtime(Service(home)).parent_control(pause)[
                "holder_joined"
            ],
            seconds=10,
        )
        version = control.inspect_run(parent, run_id, state_home=runtime.home)[
            "state_version"
        ]
        resume = runtime.root / "idle-resume.json"
        write_json(
            resume,
            service_runtime._env(
                "resume_run",
                uuid.uuid4().hex,
                instance=parent,
                run=run_id,
                expected=version,
            ),
        )
        service_runtime.Runtime(Service(home)).parent_control(resume)
        second = runtime.until(
            lambda: value if (value := waiter()) and value != first else None,
            seconds=20,
        )
        before = (home / "intake.sqlite3").read_bytes()
        service_runtime.wake(engine.service)
        runtime.until(
            lambda: value if (value := waiter()) and value != second else None,
            seconds=20,
        )
        assert engine.service.intake.tasks() == []
        assert (home / "intake.sqlite3").read_bytes() == before
        def stop_result():
            answer = service_runtime.Runtime(Service(home)).stop(stop_id)
            return answer if answer["status"] == "stopped" else None

        assert runtime.until(stop_result, seconds=90)["status"] == "stopped"
        stopped = True
        assert not (home / "intake.sock").exists()
        assert (
            control.inspect_run(parent, run_id, state_home=runtime.home)["status"]
            == "cancelled"
        )
    finally:
        primary_error = sys.exc_info()[1]
        try:
            if not stopped:
                def cleanup_stop():
                    answer = service_runtime.Runtime(Service(home)).stop(stop_id)
                    return answer if answer["status"] == "stopped" else None

                runtime.until(cleanup_stop, seconds=90)
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise primary_error from cleanup_error
            raise


def test_public_idle_graceful_service_stop_unwinds_owned_socket(runtime, monkeypatch):
    from millrace_plus import lad_service_runtime as service_runtime
    from millrace_plus.router import control

    home, _, parent, run_id, waiter = idle_service(runtime, monkeypatch)
    runtime.until(waiter, seconds=20)
    stop_id = uuid.uuid4().hex

    def stopped():
        answer = service_runtime.Runtime(Service(home)).stop(stop_id)
        return answer if answer["status"] == "stopped" else None

    runtime.until(stopped, seconds=20)
    assert not (home / "intake.sock").exists()
    view = control.inspect_run(parent, run_id, state_home=runtime.home)
    assert view["status"] == "cancelled" and view["inflight_invocations"] == []


def _command_lad_pin(runtime, name):
    # Reuse the existing LAD test peer and graph rewrite, including every actual
    # worker script/guard. Definition files never double as the mutable project.
    from public_runtime_support import write_json
    from test_lad_workflow import fake_graph

    definition = runtime.catalog / name
    shutil.copytree(catalog.bundled_root() / "lad", definition)
    fake_graph(definition, "clean")
    graph = json.loads((definition / "workflow.json").read_bytes())
    assert set(graph["bindings"]) == {"command"}
    sha = catalog.workflow_fingerprint(definition)
    selected = {"selector": "local:" + name, "version": "sha256:" + sha, "sha256": sha}
    pin = runtime.root / (name + "-pin.json")
    write_json(pin, selected)
    return definition, pin, selected


def _release_unrelated_fixture_parent(runtime):
    from millrace_plus.router import control

    # This fixture's unrelated initial parent has a 110-second deadline. It does
    # not own the new service parent; release it before the two full LAD tasks.
    (runtime.project / "release").write_text("released before LAD success journey")
    runtime.until(
        lambda: closed(
            control.inspect_run(
                runtime.parent, runtime.parent_run, state_home=runtime.home
            )
        ),
        seconds=10,
    )
    runtime.retain_parent_joins()


def _tree_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def actual_lad_close(runtime):
    """Generate actual CLOSE/ACK through public Core; validation itself is read-only."""
    from public_runtime_support import write_json

    from millrace_plus.router import control

    _release_unrelated_fixture_parent(runtime)
    _, pin, selected = _command_lad_pin(runtime, "actual-lad-close")
    definition = runtime.root / "actual-lad-close-workflow"
    materialized = catalog.init_template(
        selected["selector"], definition, catalog_home=runtime.catalog
    )
    assert materialized["provenance"]["sha256"] == selected["sha256"]
    project = runtime.root / "standalone-lad-project"
    project.mkdir()
    (project / "foreground").mkdir()
    (project / "learning").mkdir()
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    made = control.create_instance(
        definition / "workflow.json",
        project,
        state_home=runtime.home,
        request_id=uuid.uuid4().hex,
        expected_state_version=0,
    )
    assert made["status"] == "applied", made
    instance = made["instance_id"]
    started = control.start_run(
        instance,
        state_home=runtime.home,
        request_id=uuid.uuid4().hex,
        expected_state_version=control.inspect_instance(
            instance, state_home=runtime.home
        )["state_version"],
    )
    assert started["status"] == "applied", started
    run = control.inspect_run(instance, started["run_id"], state_home=runtime.home)
    assert run["status"] == "succeeded" and closed(run), run
    assert len(run["seam_occurrences"]) == len(run["seam_acknowledgements"]) == 2
    write_json(runtime.root / "actual-lad-public-run.json", run)
    # Initialize an unused service solely to own immutable domain evidence.
    home = runtime.root / "domain-evidence-service"
    initialize(home, runtime.target, Path(runtime.profile["path"]), pin, runtime.home)
    service = Service(home)
    record = {
        "paths": {"workflow": str(definition), "project": str(project)},
        "definition": selected,
        "run_id": started["run_id"],
    }
    return service, record, run


def test_actual_lad_close_ack_finalize_is_read_only(actual_lad_close):
    service, record, run = actual_lad_close
    project = Path(record["paths"]["project"])
    definition = Path(record["paths"]["workflow"])
    before_project, before_definition = _tree_bytes(project), _tree_bytes(definition)
    evidence = disposition.domain_evidence(service, record, run)
    assert evidence["missing"] == [], evidence
    assert set(evidence["available"]) == {"ledger", "close", "ack"}
    state = disposition._load_worker(record)  # Real validator, never patched.
    assert state["terminal"] == "succeeded"
    assert state["request"] is state["pending"] is None
    assert state["continuation"]["node"] == "finalize"
    assert state["continuation"]["cleanup_required"] is False
    assert state["seam_seen"]["accepted_ack"]["route"] == "FINALIZE"
    for ref in evidence["available"].values():
        disposition.read_ref(service, ref)
    assert _tree_bytes(project) == before_project
    assert _tree_bytes(definition) == before_definition


def _corrupt_close_views(view):
    """Mutate copies of an ACTUAL view; never author Core rows or edit its store."""
    for seam in ("learning_request", "learning_ack"):
        original = next(
            row for row in view["seam_occurrences"] if row["seam_id"] == seam
        )
        body = json.loads(original["body"])
        ack = next(
            row for row in view["seam_acknowledgements"] if row["seam_id"] == seam
        )
        producer, consumer = body["completion_id"], ack["completion_id"]
        for table, key, selected in (
            ("seam_occurrences", "seam_id", seam),
            ("seam_acknowledgements", "seam_id", seam),
            ("seam_selections", "seam_id", seam),
            ("completions", "invocation_id", producer),
            ("completions", "invocation_id", consumer),
            ("invocations", "id", consumer),
            ("invocations", "id", producer),
        ):
            changed = copy.deepcopy(view)
            changed[table] = [row for row in changed[table] if row[key] != selected]
            yield seam + ":missing:" + table + ":" + str(selected), changed
            changed = copy.deepcopy(view)
            selected_row = next(row for row in changed[table] if row[key] == selected)
            changed[table].append(copy.deepcopy(selected_row))
            yield seam + ":duplicate:" + table + ":" + str(selected), changed
        for field in (
            "run_id",
            "sequence",
            "producer_invocation_id",
            "completion_id",
            "producing_revision",
            "producer_plane_id",
            "producer_node_id",
            "consumer_plane_id",
            "consumer_node_id",
            "producer_workspace",
            "format",
        ):
            changed = copy.deepcopy(view)
            row = next(
                row for row in changed["seam_occurrences"] if row["seam_id"] == seam
            )
            payload = json.loads(row["body"])
            payload[field] = payload[field] + 1 if field == "sequence" else "wrong"
            row["body"] = json.dumps(payload)
            yield seam + ":body:" + field, changed
        for field, value in (
            ("summary", "sha256=" + "0" * 64),
            ("outcome", "WRONG"),
            ("refs", ["wrong.json"]),
        ):
            changed = copy.deepcopy(view)
            row = next(
                row for row in changed["seam_occurrences"] if row["seam_id"] == seam
            )
            payload = json.loads(row["body"])
            payload["result"][field] = value
            row["body"] = json.dumps(payload)
            yield seam + ":body:result-" + field, changed
        for table, fields in (
            (
                "seam_occurrences",
                (
                    "run_id",
                    "seam_id",
                    "sequence",
                    "producer_invocation_id",
                    "completion_id",
                    "transition_id",
                ),
            ),
            ("seam_acknowledgements", ("run_id", "completion_id", "sequence")),
            (
                "seam_selections",
                ("run_id", "sequence", "consumer_plane_id", "consumer_node_id", "body"),
            ),
        ):
            for field in fields:
                changed = copy.deepcopy(view)
                row = next(row for row in changed[table] if row["seam_id"] == seam)
                row[field] = row[field] + 1 if field == "sequence" else "wrong"
                yield seam + ":" + table + ":" + field, changed
        changed = copy.deepcopy(view)
        next(row for row in changed["completions"] if row["invocation_id"] == producer)[
            "outcome"
        ] = "WRONG"
        yield seam + ":producer:outcome", changed
        for label, selected in (("producer", producer), ("consumer", consumer)):
            for field in ("node", "plane_id"):
                changed = copy.deepcopy(view)
                next(row for row in changed["invocations"] if row["id"] == selected)[
                    field
                ] = "wrong"
                yield seam + ":" + label + ":" + field, changed
    final = next(
        row["id"]
        for row in view["invocations"]
        if row["node"] == "finalize" and row["plane_id"] == "foreground"
    )
    for field in ("node", "plane_id", "id"):
        changed = copy.deepcopy(view)
        next(row for row in changed["invocations"] if row["id"] == final)[field] = (
            "wrong"
        )
        yield "finalize:" + field, changed
    changed = copy.deepcopy(view)
    next(row for row in changed["completions"] if row["invocation_id"] == final)[
        "outcome"
    ] = "WRONG"
    yield "finalize:outcome", changed
    changed = copy.deepcopy(view)
    changed["completions"] = [
        row for row in changed["completions"] if row["invocation_id"] != final
    ]
    yield "finalize:missing-completion", changed
    changed = copy.deepcopy(view)
    changed["completions"].append(
        copy.deepcopy(
            next(row for row in changed["completions"] if row["invocation_id"] == final)
        )
    )
    yield "finalize:duplicate-completion", changed


def test_actual_lad_close_selected_core_links_require_hold(actual_lad_close):
    service, record, run = actual_lad_close
    assert disposition.domain_evidence(service, record, run)["missing"] == []
    # Do not xfail newly exposed gaps: see proposal README before integration.
    for label, changed in _corrupt_close_views(run):
        assert disposition.domain_evidence(service, record, changed)["missing"], label
    assert disposition.domain_evidence(service, record, run)["missing"] == []


def test_actual_lad_close_domain_bytes_require_hold(actual_lad_close):
    service, record, run = actual_lad_close
    available = disposition.domain_evidence(service, record, run)["available"]
    project = Path(record["paths"]["project"])
    state = disposition._load_worker(record)
    request_id = state["seam_seen"]["accepted_ack"]["ack"]["request_id"]
    selected = [
        project / "foreground/lad-state/session.json",
        project / ("foreground/lad-state/seams/request-" + request_id + ".json"),
        project / ("learning/lad-state/seams/ack-" + request_id + ".json"),
    ]
    for path in selected:
        raw = path.read_bytes()
        try:
            # Valid JSON with one harmless whitespace change still changes the
            # pinned CLOSE/ACK bytes. The ledger case instead breaks integrity.
            changed = json.loads(raw)
            if path.name == "session.json":
                changed["integrity_sha256"] = "0" * 64
                path.write_text(json.dumps(changed))
            else:
                path.write_bytes(raw + b" ")
            assert disposition.domain_evidence(service, record, run)["missing"], path
        finally:
            path.write_bytes(raw)
    assert disposition.domain_evidence(service, record, run)["missing"] == []
    assert (
        available["ledger"]["sha256"]
        == hashlib.sha256(selected[0].read_bytes()).hexdigest()
    )


def test_actual_lad_close_worker_authority_requires_hold(actual_lad_close):
    service, record, run = actual_lad_close
    ledger = Path(record["paths"]["project"]) / "foreground/lad-state/session.json"
    original = ledger.read_bytes()
    # Use the worker's own validated load/save helpers, not a second ledger
    # validator or hand-written checksum codec. Each case restores exact bytes.
    edit = (
        "import json,runpy,sys\nfrom pathlib import Path\n"
        "module=runpy.run_path(sys.argv[1])\n"
        "load,save=module['load'],module['save']\n"
        "root=Path(sys.argv[2]); state=load(root)\n"
        "path,value=json.loads(sys.argv[3]); item=state\n"
        "for key in path[:-1]: item=item[key]\n"
        "item[path[-1]]=value; save(root,state)\n"
    )
    cases = [
        (["terminal"], "failed"),
        (["request"], {}),
        (["pending"], {}),
        (["continuation", "node"], "prepare_recon"),
        (["continuation", "cleanup_required"], True),
        (["seam_seen", "accepted_ack", "route"], "UPDATER"),
    ]
    for key in ("session_id", "kind", "terminal", "request_id", "request_sha256"):
        cases.append((["seam_seen", "accepted_ack", "ack", key], "wrong"))
    for key in (
        "run_id",
        "seam_id",
        "producer_plane_id",
        "producer_node_id",
        "consumer_plane_id",
        "consumer_node_id",
    ):
        cases.append((["seam_seen", "accepted_ack", "core", key], "wrong"))
    for path, value in cases:
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    edit,
                    str(
                        Path(record["paths"]["workflow"])
                        / "scripts"
                        / "lad_state.py"
                    ),
                    str(ledger.parent.parent),
                    json.dumps([path, value]),
                ],
                check=True,
                capture_output=True,
            )
            assert disposition.domain_evidence(service, record, run)["missing"], path
        finally:
            ledger.write_bytes(original)
    assert disposition.domain_evidence(service, record, run)["missing"] == []


def _terminal_proof(service, task):
    receipt = task["terminal_receipt"]
    matches = []
    for path in (service.home / "evidence").glob("disposition-*.json"):
        raw = path.read_bytes()
        proof = json.loads(raw)
        if (proof.get("task_id"), proof.get("request_id")) == (
            task["task_id"],
            receipt["request_id"],
        ):
            ref = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
            matches.append(json.loads(disposition.read_ref(service, ref)))
    assert len(matches) == 1, matches
    return matches[0]


def test_public_two_lad_command_tasks_close_ack_settle_and_continue(
    runtime, monkeypatch
):
    """Actual LAD protocol/Core/OS; deterministic peers, no native efficacy claim."""
    from public_runtime_support import write_json

    from millrace_plus.router import control

    _release_unrelated_fixture_parent(runtime)
    _, pin, _ = _command_lad_pin(runtime, "public-lad-clean")
    # Only the fixture needs source package visibility; the worker graph still
    # has exactly the command binding and calls the normal worker guards.
    original_settings = service_runtime._settings
    monkeypatch.setattr(
        service_runtime,
        "_settings",
        lambda target: (
            original_settings(target) | {"env_allowlist": ["HOME", "PYTHONPATH"]}
        ),
    )
    home = runtime.root / "normal-success-service"
    initialized = initialize(
        home, runtime.target, Path(runtime.profile["path"]), pin, runtime.home
    )
    service = Service(home)
    original_requests = [
        b"First original request: answer() returns 42.\r\n",
        b"Second original request: answer() returns 42.\n",
    ]
    for index, raw in enumerate(original_requests):
        path = runtime.root / ("success-request-" + str(index) + ".md")
        path.write_bytes(raw)
        service.submit("success-" + str(index), path)
    started = runtime.send_parent(initialized["start_request"])
    assert started["status"] == "applied", started
    parent, parent_run = initialized["instance_id"], started["run_id"]
    # Let the graph bind its exact start receipt before operator-side reads;
    # otherwise Runtime.parent and graph reconcile can race their lifecycle CAS.
    runtime.until(lambda: Service(home).intake.lifecycle()[1]["run_id"] == parent_run)
    engine = service_runtime.Runtime(service)
    engine.parent()
    stop_id = uuid.uuid4().hex
    stopped = False
    try:
        # No acknowledge-failed, decision override, manual settle, or parent
        # resume: ordinary graph settlement alone must release task one.
        tasks = runtime.until(
            lambda: (
                rows
                if len(rows := Service(home).intake.tasks()) == 2
                and all(row["status"] == "succeeded" for row in rows)
                else None
            ),
            seconds=1200,
        )
        assert [task["request_bytes"] for task in tasks] == original_requests
        identities, sessions, occurrence_sets = [], [], []
        for task in tasks:
            assert task["terminal_receipt"]["disposition"] == "succeeded"
            _, record = engine.call(task)
            run = control.inspect_run(
                record["instance_id"], record["run_id"], state_home=runtime.home
            )
            assert run["status"] == "succeeded" and closed(run), run
            assert all(has_join_proof(record, name) for name in ("create", "start"))
            assert disposition.domain_evidence(service, record, run)["missing"] == []
            state = disposition._load_worker(record)
            assert state["request"] is state["pending"] is None
            assert state["terminal"] == "succeeded"
            assert state["continuation"]["cleanup_required"] is False
            proof = _terminal_proof(service, task)
            assert proof["format"] == "millrace-plus-lad-terminal/v1"
            assert proof["operator_evidence"] is None
            assert proof["domain_evidence"]["missing"] == []
            assert set(proof["domain_evidence"]["available"]) == {
                "ledger",
                "close",
                "ack",
            }
            assert proof["task_version"] + 1 == task["version"]
            assert proof["claim_sha256"] == digest(task["claim"])
            assert (proof["instance_id"], proof["run_id"]) == (
                record["instance_id"],
                record["run_id"],
            )
            physical = proof["physical_evidence"]
            assert physical["view_proof"] is True and len(physical["sent_calls"]) == 2
            refs = physical["refs"]
            saved_journal = json.loads(disposition.read_ref(service, refs["journal"]))
            validate_proof(
                json.loads(disposition.read_ref(service, refs["view_proof"])),
                local_source=saved_journal["local_source"],
                observation=json.loads(
                    disposition.read_ref(service, refs["observation"])
                ),
                instance_view_bytes=disposition.read_ref(service, refs["instance"]),
                run_view_bytes=disposition.read_ref(service, refs["run"]),
            )
            for pair in physical["sent_calls"]:
                assert set(pair) == {"envelope", "joined"}
                sent = json.loads(disposition.read_ref(service, pair["envelope"]))
                joined = json.loads(disposition.read_ref(service, pair["joined"]))
                assert any(
                    row["request_id"] == sent["request_id"]
                    and row["holder_joined"] is True
                    for row in joined
                )
            identities.append(
                (record["instance_id"], record["run_id"], record["paths"]["project"])
            )
            sessions.append(state["session_id"])
            occurrence_sets.append(set(state["occurrences"]))
            write_json(runtime.root / (task["task_id"] + "-success-proof.json"), proof)
        assert all(len({row[index] for row in identities}) == 2 for index in range(3))
        assert len(set(sessions)) == 2 and not occurrence_sets[0] & occurrence_sets[1]
        assert (
            runtime.target / "answer.py"
        ).read_text() == "def answer():\n    return 42\n"
        parent_view = control.inspect_run(parent, parent_run, state_home=runtime.home)
        assert parent_view["status"] == "running", parent_view
        # Rereading validates both exact terminal receipts and their preserved
        # proof aftermath without incrementing task versions a second time.
        assert Service(home).intake.tasks() == tasks

        def stopping():
            answer = service_runtime.Runtime(Service(home)).stop(stop_id)
            return answer if answer["status"] == "stopped" else None

        assert runtime.until(stopping, seconds=90)["status"] == "stopped"
        stopped = True
        assert closed(control.inspect_run(parent, parent_run, state_home=runtime.home))
        assert Service(home).intake.tasks() == tasks
    finally:
        # Cleanup stays service-aware even when a new success assertion fails.
        # Retain exact state on unknown closure; never infer that OS shutdown
        # itself stopped the independently owned service child.
        primary_error = sys.exc_info()[1]
        try:
            try:
                if not stopped:

                    def cleanup_stop():
                        answer = service_runtime.Runtime(Service(home)).stop(stop_id)
                        return answer if answer["status"] == "stopped" else None

                    runtime.until(cleanup_stop, seconds=90)
            finally:
                write_json(
                    runtime.root / "normal-success-aftermath.json",
                    Service(home).status(),
                )
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise primary_error from cleanup_error
            raise


def _b4_command_lad_pin(runtime):
    from public_runtime_support import command_node, write_json

    definition, pin, _ = _command_lad_pin(runtime, "b4-real-lad")
    script = definition / "scripts/b4_tail.py"
    script.write_text(
        "import json,sys,time\nfrom pathlib import Path\n"
        "m=json.loads(Path('../subworkflow-inputs.json').read_bytes())\n"
        "selected='B4_RECOVER_FIRST' in m['inputs']['request_markdown']\n"
        "outcome='BYPASS'\n"
        "if selected and sys.argv[1]=='wait':\n"
        " Path('b4-live-started').write_text('real same-child live interval')\n"
        " while not Path('b4-release').exists():\n"
        "  time.sleep(.1)\n"
        " assert Path('b4-release').exists(),'bounded B4 release expired'\n"
        " outcome='WAITED'\n"
        "elif selected: outcome='PAUSE'\n"
        "print(json.dumps({'outcome':outcome,"
        "'summary':'B4 command-only tail','refs':[]}))\n"
    )
    graph = json.loads((definition / "workflow.json").read_bytes())
    graph["control_files"].append("scripts/b4_tail.py")
    nodes = graph["planes"]["foreground"]["nodes"]
    assert nodes["finalize"]["on"]["SUCCEEDED"] == {"finish": "succeeded"}
    nodes["finalize"]["on"]["SUCCEEDED"] = {"goto": "b4_pause_one"}
    for name, resume in (("b4_pause_one", "b4_pause_two"), ("b4_pause_two", "b4_wait")):
        node = command_node(
            "scripts/b4_tail.py",
            "PAUSE",
            {"pause": {"reason": name, "resume": resume}},
            args=[name],
            timeout=10,
        )
        node["outcomes"].append("BYPASS")
        node["on"]["BYPASS"] = {"finish": "succeeded"}
        nodes[name] = node
    nodes["b4_wait"] = command_node(
        "scripts/b4_tail.py",
        "WAITED",
        {"finish": "succeeded"},
        args=["wait"],
        # Only the owned release completes normally; the invocation stays finite.
        timeout=155,
    )
    write_json(definition / "workflow.json", graph)
    sha = catalog.workflow_fingerprint(definition)
    write_json(
        pin,
        {
            "selector": "local:" + definition.name,
            "version": "sha256:" + sha,
            "sha256": sha,
        },
    )
    return pin


def _b4_server_lost_join(runtime, monkeypatch, lost_request, held_request):
    """Withhold one join everywhere; retain actual receipts and all other truth."""
    workers = runtime.service.router_workers
    import threading

    original_observation, original_submit = workers._observation, workers.submit
    original_run = workers._run
    posts, withheld, false_after_return = [], [], []
    core_returned, release_join = threading.Event(), threading.Event()

    def run(call, operation):
        original_run(call, operation)
        if call.request["request_id"] == held_request:
            # Core has actually returned, but this real OS-owned worker has not.
            # _retire must truthfully remain false until the test releases it.
            # The test's guaranteed finally owns release on every failure path.
            core_returned.set()
            release_join.wait()

    def submit(home, request, **kwargs):
        posts.append((request["request_id"], request["operation"]))
        return original_submit(home, request, **kwargs)

    def observation(call, *, version=1):
        value = original_observation(call, version=version)
        if (
            version == 2
            and call.request["request_id"] == held_request
            and core_returned.is_set()
            and value.holder_joined is False
        ):
            false_after_return.append(copy.deepcopy(value.model_dump()))
        if version != 2 or call.request["request_id"] != lost_request:
            return value
        # Fault model: only ownership/join metadata for this exact envelope is
        # unavailable. POST call_observation and GET observe share this seam.
        # core_receipt is the actual OS-retained Core receipt, never authored.
        fields = value.model_dump()
        fields.update(
            source="core_receipt", phase="receipt_observed", holder_joined=None
        )
        withheld.append(copy.deepcopy(fields))
        return type(value)(**fields)

    monkeypatch.setattr(workers, "submit", submit)
    monkeypatch.setattr(workers, "_observation", observation)
    monkeypatch.setattr(workers, "_run", run)
    return posts, withheld, core_returned, release_join, false_after_return


def _b4_until(predicate, *, seconds):
    from millrace_plus.subworkflow_records import SubworkflowError

    # Public full-view reads at 1-second cadence, including the >60-second case.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = predicate()
        except SubworkflowError as exc:
            if exc.code != "observation_window_expired":
                raise
            value = None
        if value:
            return value
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise AssertionError("bounded B4 public observation expired")


def test_public_lost_maintenance_join_recovers_live_window_then_normal_settlement(
    runtime, monkeypatch
):
    import pytest
    from public_runtime_support import write_json

    from millrace_plus import lad_service_disposition as disposition
    from millrace_plus import lad_service_runtime as service_runtime
    from millrace_plus.local_intake import IntakeError
    from millrace_plus.router import control
    from millrace_plus.subworkflow_persistent_records import validate_proof
    from millrace_plus.subworkflow_v2 import closed, has_join_proof, reconcile

    _release_unrelated_fixture_parent(runtime)
    original_settings = service_runtime._settings
    monkeypatch.setattr(
        service_runtime,
        "_settings",
        lambda target: (
            original_settings(target) | {"env_allowlist": ["HOME", "PYTHONPATH"]}
        ),
    )
    pin = _b4_command_lad_pin(runtime)
    home = runtime.root / "b4-service"
    initialized = initialize(
        home, runtime.target, Path(runtime.profile["path"]), pin, runtime.home
    )
    requests = [
        b"B4_RECOVER_FIRST: answer() returns 42.\r\n",
        b"Automatically selected next task: answer() returns 42.\n",
    ]
    submitted = []
    for index, raw in enumerate(requests):
        path = runtime.root / ("b4-task-" + str(index) + ".md")
        path.write_bytes(raw)
        submitted.append(Service(home).submit("b4-task-" + str(index), path)["task_id"])
    first_decision, second_decision, run_decision = (uuid.uuid4().hex for _ in range(3))
    lost_id = service_runtime._operation_id(first_decision, "child-resume")
    held_id = service_runtime._operation_id(run_decision, "child-resume")
    posts, withheld, core_returned, release_join, false_after_return = (
        _b4_server_lost_join(runtime, monkeypatch, lost_id, held_id)
    )
    started = runtime.send_parent(initialized["start_request"])
    assert started["status"] == "applied", started
    parent, parent_run = initialized["instance_id"], started["run_id"]
    runtime.until(lambda: Service(home).intake.lifecycle()[1]["run_id"] == parent_run)
    first_task = submitted[0]
    live_workspace = None
    stopped = False
    stop_id = uuid.uuid4().hex

    def engine():
        return service_runtime.Runtime(Service(home))

    def parent_view():
        return control.inspect_run(parent, parent_run, state_home=runtime.home)

    def task():
        return Service(home).intake.tasks(first_task)[0]

    def child(*, refresh=False):
        active = engine()
        journal, record = active.call(task())
        if refresh:
            record = reconcile(
                journal.home,
                journal.key,
                client=active.client,
                _local_source=journal.local_source,
            )
        return record, control.inspect_run(
            record["instance_id"], record["run_id"], state_home=runtime.home
        )

    def held_at(node):
        row = task()
        if row["status"] != "held" or not closed(parent_view(), attention=True):
            return None
        record, view = child(refresh=True)
        return (
            (record, view)
            if closed(view, attention=True)
            and any(item["node"] == node for item in view["invocations"])
            and all(has_join_proof(record, name) for name in ("create", "start"))
            else None
        )

    try:
        record, initial_child = _b4_until(lambda: held_at("b4_pause_one"), seconds=600)
        initial_identity = (record["instance_id"], record["run_id"])
        live_workspace = Path(record["paths"]["project"]) / "foreground"
        assert initial_child["status"] == "paused"
        state = disposition._load_worker(record)
        assert (
            state["terminal"] == "succeeded"
            and state["continuation"]["cleanup_required"] is False
        )
        assert state["seam_seen"]["accepted_ack"]["route"] == "FINALIZE"
        assert all(has_join_proof(record, name) for name in ("create", "start"))

        # Save the ORIGINAL parent start's actual true by GET only. No parent
        # recovery or OS restart is part of this test; its holder is now closed.
        def remember_parent_start():
            active = engine()
            row = active.record["start"]
            assert row["reservation"] == "observed"
            service_runtime._send(
                active.service,
                active.intent,
                active.record,
                row,
                active.connection,
                time.monotonic() + 2,
            )
            return service_runtime._joined(active.service, row)

        _b4_until(remember_parent_start, seconds=10)
        operator = runtime.root / "b4-continue.txt"
        operator.write_text(
            "Continue only this exact child and preserved LAD continuation.\n"
        )

        def first_continue():
            try:
                return engine().decide(
                    first_task,
                    "continue",
                    first_decision,
                    operator,
                    resume_plane="foreground",
                )
            except IntakeError as exc:
                if exc.code != "child_resume_pending":
                    raise
                return None

        _b4_until(first_continue, seconds=90)

        # The original resume really runs to a SECOND graph pause. Its join is
        # withheld on the server for every client, so no earlier true can leak.
        def at_second_pause():
            saved, view = child()
            return (
                (saved, view)
                if view["status"] == "paused"
                and closed(view, attention=True)
                and any(row["node"] == "b4_pause_two" for row in view["invocations"])
                else None
            )

        record, second_pause = _b4_until(at_second_pause, seconds=15)

        def observe_lost_resume():
            active = engine()
            row = active.record["maintenance"][first_decision]["resume"]
            assert row["reservation"] != "unsent"
            service_runtime._send(
                active.service,
                active.intent,
                active.record,
                row,
                active.connection,
                time.monotonic() + 2,
            )
            return (
                row
                if row["receipt"] is not None and row["receipt"]["status"] == "applied"
                else None
            )

        prior = _b4_until(observe_lost_resume, seconds=10)
        assert prior["envelope"]["request_id"] == lost_id
        assert prior["receipt"]["status"] == "applied"
        assert prior["observations"][-1]["holder_joined"] is None
        assert not any(row["holder_joined"] is True for row in prior["observations"])
        assert (record["instance_id"], record["run_id"]) == initial_identity
        assert (
            second_pause.get("run_gate") is None
        )  # Node pause is not a full run gate.

        # Second explicit decision must establish real pause_run gate before a
        # fresh recover. A pending actual call is refreshed by the same request;
        # replay never submits a replacement envelope or borrows another true.
        def recover_and_continue():
            try:
                engine().decide(
                    first_task,
                    "continue",
                    second_decision,
                    operator,
                    resume_plane="foreground",
                )
            except IntakeError as exc:
                if exc.code not in {
                    "holder_known_unjoined",
                    "lost_call_receipt_unresolved",
                    "fresh_recovery_unresolved",
                    "child_writers_unresolved",
                    "child_resume_pending",
                    "separate_run_resume_required",
                }:
                    raise
                if exc.code == "separate_run_resume_required":
                    return Service(home).intake.lifecycle()[1]["maintenance"][
                        second_decision
                    ]
                return None
            pytest.fail("gated plane continuation did not retain held attention")

        fresh = _b4_until(recover_and_continue, seconds=90)
        assert fresh["pause"]["envelope"]["operation"] == "pause_run"
        assert fresh["recover"]["envelope"]["operation"] == "recover_run"
        assert fresh["resume"]["envelope"]["operation"] == "resume_plane"
        assert fresh["resume"]["envelope"]["plane_id"] == "foreground"
        assert fresh["attention"] == "separate_run_resume_required"
        assert fresh["recovery_proof"] is not None
        recovery = json.loads(
            disposition.read_ref(Service(home), fresh["recovery_proof"])
        )
        assert recovery["lost_request_ids"] == [lost_id]
        before = json.loads(disposition.read_ref(Service(home), recovery["before"]))
        assert (
            before["run"]["run_gate"]["request_id"]
            == fresh["pause"]["envelope"]["request_id"]
        )
        assert disposition.recovery_ready(before["run"])
        assert service_runtime._joined(Service(home), fresh["recover"])
        record, prepared = child()
        assert prepared["run_gate"] is not None
        assert prepared["planes"]["foreground"]["status"] == "ready"
        assert task()["status"] == "held"
        assert not (live_workspace / "b4-live-started").exists()
        before_posts = list(posts)
        with pytest.raises(IntakeError, match="separate_run_resume_required"):
            engine().decide(
                first_task,
                "continue",
                second_decision,
                operator,
                resume_plane="foreground",
            )
        assert posts == before_posts
        assert not (live_workspace / "b4-live-started").exists()

        # A new explicit default decision clears the gate. Accepted prior
        # recovery covers the original null without another pause/recover.
        def run_continue():
            try:
                result = engine().decide(first_task, "continue", run_decision, operator)
            except IntakeError as exc:
                if exc.code != "holder_known_unjoined":
                    raise
                return None
            resume = engine().record["maintenance"][run_decision]["resume"]
            receipt = resume["receipt"]
            if receipt is not None and receipt["status"] == "refused":
                pytest.fail(str(receipt))
            saved, view = child()
            current = task()
            return (
                result
                if resume["envelope"]["request_id"] == held_id
                and receipt is not None
                and receipt["status"] == "applied"
                and receipt["action"] == "resumed"
                and (saved["instance_id"], saved["run_id"]) == initial_identity
                and view["status"] == "running"
                and view.get("run_gate") is None
                and current["status"] == "running"
                and current["terminal_receipt"] is None
                and (live_workspace / "b4-live-started").is_file()
                else None
            )

        _b4_until(run_continue, seconds=90)
        run_item = Service(home).intake.lifecycle()[1]["maintenance"][run_decision]
        assert run_item["pause"]["envelope"] is None
        assert run_item["recover"]["envelope"] is None
        assert run_item["resume"]["envelope"]["operation"] == "resume_run"
        assert run_item["resume"]["envelope"]["request_id"] == held_id
        assert not service_runtime._joined(Service(home), run_item["resume"])
        _b4_until(lambda: (live_workspace / "b4-live-started").is_file(), seconds=10)
        record, view = child()
        assert (
            view["status"] == "running"
            and (record["instance_id"], record["run_id"]) == initial_identity
        )
        assert (
            disposition._recovery_for(engine(), record, lost_id)
            == fresh["recovery_proof"]
        )
        assert task()["status"] == "running" and task()["terminal_receipt"] is None

        # Reopen all service state, then resume the recorded parent. Its ordinary
        # service advance must cross one ORIGINAL 60-second observation boundary
        # while this same child is still running. No clock acceleration is used.
        baseline = {row["invocation_id"] for row in parent_view()["completions"]}
        resume = runtime.root / "b4-parent-resume.json"
        write_json(
            resume,
            service_runtime._env(
                "resume_run",
                uuid.uuid4().hex,
                instance=parent,
                run=parent_run,
                expected=parent_view()["state_version"],
            ),
        )
        selected = json.loads(resume.read_bytes())
        selected.update(operation="resume_plane", plane_id="service")
        write_json(resume, selected)
        boundary_started = time.monotonic()
        parent_applied = False

        def live_boundary():
            nonlocal parent_applied
            if not parent_applied:
                answer = engine().parent_control(resume)
                receipt = answer["receipt"]
                if receipt is None or receipt["status"] == "queued":
                    return None
                assert receipt["status"] == "applied", receipt
                parent_applied = True
            view = parent_view()
            advances = {
                row["id"] for row in view["invocations"] if row["node"] == "advance"
            }
            return next(
                (
                    row
                    for row in view["completions"]
                    if row["invocation_id"] not in baseline
                    and row["invocation_id"] in advances
                    and row["outcome"] == "CHILD_RUNNING"
                ),
                None,
            )

        boundary = _b4_until(live_boundary, seconds=80)
        assert record["request"]["observe_seconds"] == 60
        assert time.monotonic() - boundary_started >= 60
        record, still_live = child()
        assert (
            still_live["status"] == "running"
            and not (live_workspace / "b4-release").exists()
        )
        assert task()["status"] == "running" and task()["terminal_receipt"] is None
        run_item = Service(home).intake.lifecycle()[1]["maintenance"][run_decision]
        assert run_item["resume"]["observations"][-1]["holder_joined"] is False
        assert not service_runtime._joined(Service(home), run_item["resume"])
        write_json(runtime.root / "b4-observation-boundary.json", boundary)
        (live_workspace / "b4-release").write_text(
            "release only after actual service window boundary"
        )
        _b4_until(core_returned.is_set, seconds=10)

        def settling_behind_real_false():
            view = parent_view()
            active_id = view["planes"]["service"]["active_invocation"]
            return len(false_after_return) >= 2 and any(
                row["id"] == active_id and row["node"] == "settle"
                for row in view["invocations"]
            )

        _b4_until(settling_behind_real_false, seconds=15)
        assert child()[1]["status"] == "succeeded"
        assert task()["terminal_receipt"] is None and not release_join.is_set()
        assert all(
            row["request_id"] == held_id and row["holder_joined"] is False
            for row in false_after_return
        )
        # Only this actual OS thread return can permit physical true; the test
        # never authors a true response, proof or task disposition.
        release_join.set()

        # Later actual fresh-resume join + ordinary guarded settlement accept the
        # first task, then the graph automatically dispatches a distinct second.
        def succeeded_tasks():
            result = Service(home).intake.tasks()
            return (
                result if all(row["status"] == "succeeded" for row in result) else None
            )

        rows = _b4_until(succeeded_tasks, seconds=600)
        assert len(rows) == 2 and [row["request_bytes"] for row in rows] == requests
        assert all(
            row["terminal_receipt"]["disposition"] == "succeeded" for row in rows
        )
        current = engine()
        _, record = current.call(rows[0])
        lifecycle = current.record
        old, fresh = (
            lifecycle["maintenance"][first_decision]["resume"],
            lifecycle["maintenance"][second_decision],
        )
        assert old["observations"][-1]["holder_joined"] is None
        assert not any(row["holder_joined"] is True for row in old["observations"])
        assert service_runtime._joined(current.service, fresh["resume"])
        sent = disposition.task_sent_calls(record, lifecycle)
        assert [
            row["envelope"]["request_id"]
            for row in sent
            if not service_runtime._joined(current.service, row)
        ] == [lost_id]
        # Ordinary settlement can use exact recovery; abandonment cannot borrow
        # it to turn the original null into joined, even after full success.
        with pytest.raises(IntakeError, match="holder_unjoined"):
            disposition._physical(current, record, allow_recovery=False)
        physical = disposition._physical(current, record, allow_recovery=True)
        assert any(
            row.get("recovery") == fresh["recovery_proof"]
            for row in physical["sent_calls"]
        )
        assert (
            disposition.domain_evidence(current.service, record, child()[1])["missing"]
            == []
        )
        proof = _terminal_proof(current.service, rows[0])
        assert (
            proof["format"] == "millrace-plus-lad-terminal/v1"
            and proof["operator_evidence"] is None
        )
        assert proof["task_version"] + 1 == rows[0]["version"]
        assert (
            proof["domain_evidence"]["missing"] == []
            and proof["physical_evidence"]["view_proof"] is True
        )
        refs = proof["physical_evidence"]["refs"]
        saved_journal = json.loads(
            disposition.read_ref(current.service, refs["journal"])
        )
        validate_proof(
            json.loads(disposition.read_ref(current.service, refs["view_proof"])),
            local_source=saved_journal["local_source"],
            observation=json.loads(
                disposition.read_ref(current.service, refs["observation"])
            ),
            instance_view_bytes=disposition.read_ref(current.service, refs["instance"]),
            run_view_bytes=disposition.read_ref(current.service, refs["run"]),
        )
        assert any(
            row.get("recovery") == fresh["recovery_proof"]
            for row in proof["physical_evidence"]["sent_calls"]
        )
        _, next_record = current.call(rows[1])
        assert (
            next_record["instance_id"] != initial_identity[0]
            and next_record["run_id"] != initial_identity[1]
        )
        assert next_record["paths"]["project"] != record["paths"]["project"]
        first_state, next_state = (
            disposition._load_worker(record),
            disposition._load_worker(next_record),
        )
        assert next_state["session_id"] != first_state["session_id"]
        assert not set(first_state["occurrences"]) & set(next_state["occurrences"])
        assert all(has_join_proof(record, name) for name in ("create", "start"))
        before_posts = list(posts)
        with pytest.raises(IntakeError, match="separate_run_resume_required"):
            engine().decide(
                first_task,
                "continue",
                second_decision,
                operator,
                resume_plane="foreground",
            )
        assert posts == before_posts
        assert (
            Service(home).intake.lifecycle()[1]["maintenance"][second_decision][
                "attention"
            ]
            == "separate_run_resume_required"
        )
        # Controller effects never POST again on replay. The independently
        # selected exact-create replay remains allowed by the baseline contract.
        identifiers = [
            identifier
            for identifier, operation in posts
            if operation != "create_instance"
        ]
        assert identifiers.count(lost_id) == 1 and len(identifiers) == len(
            set(identifiers)
        )
        assert withheld and all(row["holder_joined"] is None for row in withheld)
        write_json(
            runtime.root / "b4-fault-injection.json",
            {
                "lost_request_id": lost_id,
                "posts": posts,
                "withheld": withheld,
                "actual_false_after_core_return": false_after_return,
                "terminal_proof": proof,
            },
        )

        def stopping():
            answer = engine().stop(stop_id)
            return answer if answer["status"] == "stopped" else None

        _b4_until(stopping, seconds=90)
        stopped = True
        assert Service(home).intake.tasks() == rows and closed(parent_view())
    finally:
        primary_error = sys.exc_info()[1]
        release_join.set()
        if live_workspace is not None:
            (live_workspace / "b4-release").write_text("owned failure cleanup release")
        try:
            try:
                if not stopped:

                    def cleanup_stop():
                        answer = engine().stop(stop_id)
                        return answer if answer["status"] == "stopped" else None

                    _b4_until(cleanup_stop, seconds=90)
            finally:
                write_json(
                    runtime.root / "b4-recovery-aftermath.json", Service(home).status()
                )
        except BaseException as cleanup_error:
            if primary_error is not None:
                raise primary_error from cleanup_error
            raise
