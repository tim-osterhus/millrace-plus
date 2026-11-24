"""Owned Factory fixtures with actual Core/OS and the existing inert LAD roles."""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from cicd_support import git, make_target, write_json
from test_cicd_v2_records import REASON, config
from test_lad_service_journeys import (
    _command_lad_pin,
    _release_unrelated_fixture_parent,
)

from millrace_plus import catalog
from millrace_plus import check_contracts as cc
from millrace_plus import factory_runtime as factory
from millrace_plus import lad_service_runtime as lad
from millrace_plus.cicd_source import filesystem_identity
from millrace_plus.cicd_v2_runtime import V2Home, initialize, prepare
from millrace_plus.lad_service import Service
from millrace_plus.router import control


def task(root: Path, source_id: str, *, lad_route=False):
    value = {
        "format": "millrace-plus-factory-input/v1",
        "source_kind": "task",
        "source_id": source_id,
        "repository": "example/calc",
        "base_candidate_sha256": None,
        "request": "Original request: answer() returns 42."
        if lad_route
        else "Implement addition.",
        "criteria": [
            {
                "id": "answer" if lad_route else "sum",
                "text": "answer() returns 42" if lad_route else "2+3 returns 5",
                "origin_ref": None,
            }
        ],
        "feedback_ref": None,
        "evidence_refs": [],
        "scope_ref": None,
        "occurred_at": "2026-10-31T00:00:00Z",
    }
    return write_json(root / (source_id + ".json"), value)


def provision(public, *, products=False, engineering=False, lad_hold=False):
    # Keep the existing outer public cleanup, and require its recorded actual
    # shutdown event/thread facts for this composed fixture's qualification.
    original_close = public.close

    def checked_close():
        original_close()
        shutdown = json.loads((public.root / "cleanup.json").read_bytes())[
            "os_shutdown"
        ]
        assert shutdown["stop_event_observed"] is True
        assert shutdown["server_thread_alive"] is False
        assert shutdown["drain_thread_alive"] is False

    public.close = checked_close
    _release_unrelated_fixture_parent(public)
    root = public.root / "factory-fixture"
    root.mkdir()
    val, head = make_target(root / "validation")
    if products:
        check_file = val / "checks/check_calc.py"
        check_file.write_text(
            "from pathlib import Path\n"
            "p=Path('build');p.mkdir(exist_ok=True)\n"
            "(p/'owned-report.txt').write_text('real check output')\n"
            + check_file.read_text()
        )
        git(val, "add", "checks/check_calc.py")
        git(
            val,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "Fixture selected check emits its product",
        )
        head = git(val, "rev-parse", "HEAD").strip()
        (val / "build").mkdir()
        (val / "build/foreign.bin").write_bytes(b"preserve foreign product\x00")
        (val / "opaque-environment").mkdir()
        (val / "opaque-environment/cache").write_bytes(b"opaque operator environment")
    public.target.rmdir()
    shutil.copytree(val, public.target)
    (public.target / "foreign.txt").write_bytes(b"original user work\n")
    definition, pin, selected = _command_lad_pin(public, "factory-inert-lad")
    if lad_hold:
        # Existing inert peer emits a real semantic human hold before source writes.
        graph_file = definition / "workflow.json"
        graph = json.loads(graph_file.read_bytes())
        # Deterministic per immutable request; do not change a definition or
        # consume a first-call marker between jobs/replays.
        selector = definition / "scripts/factory_recon.py"
        selector.write_text(
            "import runpy,sys\nfrom pathlib import Path\n"
            "peer=Path(__file__).with_name('lad_peer.py')\n"
            "follow_up='Follow-up LAD assignment' in Path('request.md').read_text()\n"
            "outcome='PASS' if follow_up else 'HUMAN_BLOCKED'\n"
            "sys.argv=[str(peer),'recon','clean',outcome]\n"
            "runpy.run_path(str(peer),run_name='__main__')\n"
        )
        graph["control_files"].append("scripts/factory_recon.py")
        graph["planes"]["foreground"]["nodes"]["recon"]["invoke"]["config"] = {
            "script": "scripts/factory_recon.py",
            "args": [],
        }
        write_json(graph_file, graph)
        digest = catalog.workflow_fingerprint(definition)
        selected = {**selected, "version": "sha256:" + digest, "sha256": digest}
        write_json(pin, selected)
    service_home = root / "lad"
    created = lad.initialize(
        service_home, public.target, Path(public.profile["path"]), pin, public.home
    )
    try:
        started = public.send_parent(created["start_request"])
        public.until(
            lambda: (
                Service(service_home).intake.lifecycle()[1]["run_id"]
                == started["run_id"]
            )
        )
        service = Service(service_home)
        engine = lad.Runtime(service)
        engine.parent()

        # Pause a bound intake waiter, not an in-flight claim: a claim that sees
        # the pause correctly returns claims_paused and leaves a semantic hold.
        def waiter_ready():
            view = control.inspect_run(
                created["instance_id"], started["run_id"], state_home=public.home
            )
            active = view["planes"]["service"]["active_invocation"]
            matches = [
                row
                for row in view["invocations"]
                if row["id"] == active
                and row["node"] == "wait_intake"
                and row["writer_state"] == "running"
            ]
            return len(matches) == 1 and (service_home / "intake.sock").is_socket()

        public.until(waiter_ready)
        pause = public.request(
            "pause_run", instance=created["instance_id"], run=started["run_id"]
        )
        pause_file = write_json(root / "pause.json", pause)
        public.until(
            lambda: engine.parent_control(pause_file)["holder_joined"], seconds=30
        )

        def paused_ready():
            from millrace_plus.subworkflow_v2 import closed
            from millrace_plus.workflow_os_client import validate_receipt

            view = control.inspect_run(
                created["instance_id"], started["run_id"], state_home=public.home
            )
            receipts = [
                row
                for row in view["controls"]
                if row["request_id"] == pause["request_id"]
            ]
            if len(receipts) != 1:
                return False
            current = validate_receipt(receipts[0], pause)
            return (
                current["status"] == "applied"
                and view["status"] == "paused"
                and view["quiescent"]
                and closed(view, attention=True)
            )

        public.until(paused_ready, seconds=30)
        # Outer fixture proof is separate from F1's production owner retention.
        public.retain_parent_joins()
        assert not service.intake.lifecycle()[1]["start"]["observations"]
        value = config()
        value.update(
            target_root=str(val), repository="example/calc", local_authority_ref=None
        )
        value["source_policy"]["mode"] = "factory"
        value["repair_paths"] = ["src", "answer.py"]
        value["protected_change_classes"] = []
        value["repair_policy"]["allowed_change_classes"] = [
            "delete",
            "mode_change",
            "binary",
        ]
        policy = cc.parse(cc.stock_path_policy())
        if products:
            policy["rules"].append(
                {"surface": "product_paths", "prefixes": ["build"], **REASON}
            )
            policy["rules"].append(
                {
                    "surface": "environment_paths",
                    "prefixes": ["opaque-environment"],
                    **REASON,
                }
            )
        raw = cc.encode(policy)
        (root / "path-policy.json").write_bytes(raw)
        policy_ref = cc.make_ref("path-policy.json", raw)
        value["path_policy_ref"] = policy_ref
        command = value["commands"][0]
        command.update(
            id="calc",
            argv=[sys.executable, "checks/check_calc.py"],
            path_policy_ref=policy_ref,
            expected_products=["build/owned-report.txt"] if products else [],
        )
        value["commands"] = [command]
        value["reproduction"]["command_ids"] = ["calc"]
        value["investigation"]["probe_command_ids"] = ["calc"]
        value["check_policy"] = {
            "mandatory_ids": ["calc"],
            "full_ids": ["calc"],
            "path_rules": [{"prefix": "src", "command_ids": ["calc"]}],
            "shared_contract_paths": [],
        }
        patterns = write_json(
            root / "failure-patterns.json",
            {
                "format": "millrace-plus-cicd-failure-patterns/v1",
                "patterns": [
                    {"id": "assert", "stream": "stdout", "literal": "expected 5"}
                ],
            },
        )
        value["reproduction"]["failure_patterns_ref"] = cc.make_ref(
            "failure-patterns.json", patterns.read_bytes()
        )
        peer = root / "peer"
        peer_source = str(Path(__file__).parent / "fixtures/factory_peer.py")
        peer.write_text(
            f"#!{sys.executable}\nimport runpy\n"
            f'runpy.run_path({peer_source!r},run_name="__main__")\n'
        )
        peer.chmod(0o700)
        templates = catalog.bundled_root() / "cicd-v2"
        for role, selection in [
            ("investigate", value["role_bindings"]["investigate"]),
            ("repair", value["role_bindings"]["repair"]),
            ("review", value["role_bindings"]["review"][0]),
        ]:
            selection["binding"]["executable"] = str(peer)
            for folder, key, suffix in [
                ("prompts", "prompt_ref", ".md"),
                ("skills", "skill_ref", ".md"),
                ("schemas", "result_schema_ref", ".json"),
            ]:
                name = folder + "/" + role + suffix
                destination = root / name
                destination.parent.mkdir(exist_ok=True)
                destination.write_bytes((templates / name).read_bytes())
                selection[key] = cc.make_ref(name, destination.read_bytes())
        engineering_policy = {
            "engineering_profile_ref": None,
            "requirements_snapshot_ref": None,
            "lq_command_map_ref": None,
        }
        if engineering:
            from test_lad_engineering_feedback import initial_history, profile_record

            from millrace_plus import engineering_feedback as ef

            history = root / "selected-history"
            history.mkdir()
            pin = initial_history(history)
            snapshot = json.loads((history / pin["path"]).read_bytes())
            snapshot = ef.with_integrity(snapshot | {"repository_id": "example/calc"})
            raw = ef.record_bytes(snapshot)
            snapshot_file = root / pin["path"]
            snapshot_file.parent.mkdir(parents=True, exist_ok=True)
            snapshot_file.write_bytes(raw)
            pin["sha256"] = ef.sha256(raw)
            extra = dict(command) | {
                "id": "lq-extra",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; from src.calc import add; "
                    "assert add(2,3)==5; p=Path('answer.py'); "
                    "assert not p.exists() or __import__('answer').answer()==42",
                ],
            }
            value["commands"].append(extra)
            definitions = []
            entries = []
            for selected_command in value["commands"]:
                definition = dict(
                    cc.execution_of(
                        selected_command, policy=cc.validate_path_policy(policy)
                    )
                ) | {
                    "id": selected_command["id"],
                    "purpose": "functional",
                    "inputs": [],
                    "requirement_ids": [],
                    "selection": {"when": "always", "paths": []},
                    "required": True,
                    "origin": "fixture selected profile",
                    "override": "edit selected profile",
                }
                if selected_command["id"] == "lq-extra":
                    definition["selection"] = {
                        "when": "paths_changed",
                        "paths": ["src"],
                    }
                definitions.append(definition)
                entries.append(
                    {
                        "lq_check_id": definition["id"],
                        "ci_command_id": selected_command["id"],
                        "execution_sha256": cc.execution_sha256(
                            cc.execution_of(selected_command)
                        ),
                    }
                )
            profile_value = profile_record(
                pin,
                policy_ref,
                definitions,
                repository={
                    "target_id": "example/calc",
                    "root_ref": "foreground/target",
                    "source_scope": ["."],
                    "excluded_paths": [],
                },
            )
            profile_file = write_json(root / "engineering-profile.json", profile_value)
            map_file = write_json(
                root / "command-map.json",
                {"format": cc.COMMAND_MAP, "entries": entries},
            )
            engineering_policy = {
                "engineering_profile_ref": cc.make_ref(
                    "engineering-profile.json", profile_file.read_bytes()
                ),
                "requirements_snapshot_ref": {
                    "path": pin["path"],
                    "sha256": pin["sha256"],
                },
                "lq_command_map_ref": cc.make_ref(
                    "command-map.json", map_file.read_bytes()
                ),
            }
        ci_config = write_json(root / "ci-config.json", value)
        ci_home = root / "ci"
        prepared = prepare(ci_home, val, ci_config)
        service_raw = (service_home / "service.json").read_bytes()
        service_ref = cc.make_ref("service.json", service_raw)
        (root / "service.json").write_bytes(service_raw)
        risk = write_json(
            root / "risk.json",
            {"protected_paths": ["src/auth"], "protected_change_classes": []},
        )
        profile = write_json(root / "role-profile.json", value["role_bindings"])
        policy_file = write_json(root / "ci-policy.json", value["check_policy"])
        anchor = {
            "repository": "example/calc",
            "object_format": git(val, "rev-parse", "--show-object-format").strip(),
            "head_sha": head,
            "tree_sha": git(val, "rev-parse", "HEAD^{tree}").strip(),
        }
        cfg = {
            "format": "millrace-plus-factory-config/v1",
            "repository": "example/calc",
            "development_lane": {
                "service_home": str(service_home),
                "target_root": str(public.target),
                "service_config_ref": service_ref,
                "worker_definition": selected,
                "os_profile": public.profile,
                "target_identity": filesystem_identity(public.target),
                "service_id": service.service_id,
                "parent_instance_id": created["instance_id"],
            },
            "validation_lane": {
                "workflow_home": str(ci_home),
                "target_root": str(val),
                "config_ref": prepared["prepared_config_ref"],
                "target_identity": filesystem_identity(val),
                "service_id": prepared["service_id"],
            },
            "anchor": anchor,
            "scope_policy": {
                "write_paths": ["src", "answer.py"],
                "protected_paths": ["src/auth"],
                "risk_policy_ref": cc.make_ref("risk.json", risk.read_bytes()),
            },
            "check_policy": {
                **engineering_policy,
                "ci_policy_ref": cc.make_ref(
                    "ci-policy.json", policy_file.read_bytes()
                ),
            },
            "role_profile": {
                "profile_ref": cc.make_ref("role-profile.json", profile.read_bytes()),
                "require_independent_review_family": False,
            },
            "provider_wait_policy": {"mode": "ordinary_recovery", "policy_ref": None},
            "delivery_policy": {
                "mode": "local",
                "config_ref": None,
                "authority_ref": None,
            },
            "local_authority": {
                "authority_ref": cc.make_ref("selected-authority.json", b"UNAPPROVED"),
                "allowed_operations": [
                    "reproduce",
                    "ci_repair",
                    "lad_task",
                    "materialize_validation",
                    "restore_validation",
                    "synchronize_development",
                    "restore_development",
                ],
            },
            "timeouts": {
                "node_deadlines": {},
                "observation_windows": {},
                "effect_observation": None,
            },
            "resource_policy": {"guards": []},
            "path_policy_ref": policy_ref,
        }
        factory_home = root / "factory"
        cfg_file = write_json(root / "factory-config.json", cfg)
        staged = factory.initialize(factory_home, cfg_file)
        assert (
            staged["authority_status"] == "UNAPPROVED"
            and not (factory_home / "factory.json").exists()
        )
        authority = Path(staged["suggested_authority_file"])
        (root / "selected-authority.json").write_bytes(authority.read_bytes())
        initialize(ci_home, val, ci_config, authority_file=authority)
        owned = V2Home(ci_home)
        cfg["validation_lane"]["config_ref"] = owned.config_ref
        cfg["local_authority"]["authority_ref"] = owned.config["local_authority_ref"]
        cfg_file = write_json(cfg_file, cfg)
        final = factory.initialize(factory_home, cfg_file)
        assert final["status"] == "initialized"
        return SimpleNamespace(
            public=public,
            root=root,
            home=factory_home,
            config=cfg_file,
            ci=owned,
            dev=public.target,
            val=val,
            service=service,
            engine=engine,
            parent=created["instance_id"],
            parent_run=started["run_id"],
            instance=final["instance_id"],
            state=factory_home / "state",
            head=head,
        )
    except BaseException:
        stop_id = uuid.uuid4().hex
        owned_engine = lad.Runtime(Service(service_home))
        try:
            stopped = public.until(
                lambda: (
                    result
                    if (result := owned_engine.stop(stop_id))["status"] == "stopped"
                    else None
                ),
                seconds=120,
            )
            write_json(root / "failed-provision-cleanup.json", stopped)
        except Exception as exc:
            write_json(
                root / "failed-provision-cleanup.json",
                {"closure": "unknown", "error": type(exc).__name__, "detail": str(exc)},
            )
        raise


def start(fixture):
    import threading

    receipts, results, errors = [], [], []
    request_id = uuid.uuid4().hex

    def execute():
        try:
            results.append(
                control.start_run(
                    fixture.instance,
                    state_home=fixture.state,
                    request_id=request_id,
                    expected_state_version=control.inspect_instance(
                        fixture.instance, state_home=fixture.state
                    )["state_version"],
                    on_started=lambda receipt: receipts.append(receipt),
                )
            )
        except BaseException as exc:
            errors.append(exc)

    fixture.executor = threading.Thread(target=execute)
    fixture.executor_errors = errors
    fixture.executor_results = results
    fixture.executor.start()

    def observed():
        if errors:
            raise errors[0]
        return receipts[0] if receipts else None

    receipt = fixture.public.until(observed)
    fixture.run_id = receipt["run_id"]
    view = control.inspect_instance(fixture.instance, state_home=fixture.state)
    controls = [row for row in view["controls"] if row["request_id"] == request_id]
    assert len(controls) == 1 and controls[0]["status"] == "applied", controls
    return fixture.run_id


def _join_factory_holders(fixture):
    holders = list(getattr(fixture, "resume_holders", []))
    if getattr(fixture, "executor", None) is not None:
        holders.insert(
            0,
            {
                "thread": fixture.executor,
                "results": fixture.executor_results,
                "errors": fixture.executor_errors,
                "request": None,
            },
        )
    facts = []
    for holder in holders:
        fixture.public.until(lambda: not holder["thread"].is_alive(), seconds=30)
        holder["thread"].join()
        assert not holder["errors"], holder["errors"]
        assert holder["results"]
        facts.append(
            {
                "request": holder["request"],
                "applied_control": holder.get("applied_control"),
                "returned": holder["results"],
                "joined": True,
            }
        )
    return facts


def resume(fixture, run_id):
    """Own the public resume loop in a tracked holder, as start() does."""
    import threading

    from millrace_plus.workflow_os_client import validate_receipt

    _join_factory_holders(fixture)
    view = control.inspect_run(fixture.instance, run_id, state_home=fixture.state)
    envelope = fixture.public.request(
        "resume_plane",
        instance=fixture.instance,
        run=run_id,
        expected=view["state_version"],
    )
    envelope["plane_id"] = "factory"
    holder = {"request": envelope, "results": [], "errors": []}

    def execute():
        try:
            holder["results"].append(
                control.resume_plane(
                    fixture.instance,
                    run_id,
                    plane_id="factory",
                    state_home=fixture.state,
                    request_id=envelope["request_id"],
                    expected_state_version=envelope["expected_state_version"],
                )
            )
        except BaseException as exc:
            holder["errors"].append(exc)

    holder["thread"] = threading.Thread(target=execute, daemon=False)
    fixture.resume_holders = [*getattr(fixture, "resume_holders", []), holder]
    holder["thread"].start()

    def applied():
        if holder["errors"]:
            raise holder["errors"][0]
        current = control.inspect_run(
            fixture.instance, run_id, state_home=fixture.state
        )
        rows = [
            row
            for row in current["controls"]
            if row["request_id"] == envelope["request_id"]
        ]
        if len(rows) != 1:
            return None
        receipt = validate_receipt(rows[0], envelope)
        if receipt["status"] == "queued":
            return None
        assert receipt["status"] == "applied", receipt
        return receipt

    holder["applied_control"] = fixture.public.until(applied, seconds=30)
    return holder["applied_control"]


def until_jobs(fixture, count, *, statuses=None, invocation_deadline=False):
    """Observe `count` Factory rows reach `statuses` within the 1200s window.

    `invocation_deadline` is only for one selected final integration wait. Rows
    already terminal with another status, or a Factory hold, fail at once. If the
    window ends while a job invocation is in flight, its Core-recorded deadline
    becomes the one final bound; later invocations never extend it.
    """
    import time

    expected = statuses if statuses is not None else ["succeeded"] * count

    def latest_view():
        run = control.inspect_instance(fixture.instance, state_home=fixture.state)[
            "runs"
        ][-1]
        return control.inspect_run(
            fixture.instance, run["id"], state_home=fixture.state
        )

    def done():
        home = factory.Home(fixture.home)
        rows = home.intake.tasks()
        if len(rows) != count:
            return None
        view = latest_view()
        if view["status"] == "paused":
            raise AssertionError(
                json.dumps(view.get("planes"))
                + "\n"
                + json.dumps([r for r in view["completions"][-4:]])
            )
        actual = [row["status"] for row in rows]
        if invocation_deadline and (
            any(
                status in {"succeeded", "failed", "cancelled"} and status != wanted
                for status, wanted in zip(actual, expected, strict=True)
            )
            or "held" in actual
            or any(plane.get("cursor") == "hold" for plane in view["planes"].values())
        ):
            raise AssertionError(json.dumps([actual, view.get("planes")]))
        return rows if actual == expected else None

    try:
        return fixture.public.until(done, seconds=1200)
    except AssertionError as exc:
        if not invocation_deadline or exc.args != (
            "bounded public runtime observation expired",
        ):
            raise
        active = {"claimed", "running"} & {
            row["status"] for row in factory.Home(fixture.home).intake.tasks()
        }
        view = latest_view()
        deadlines = [
            row["deadline"]
            for row in view["invocations"]
            if row["id"] in view["inflight_invocations"] and row["deadline"] is not None
        ]
        if not active or not deadlines:
            raise
        return fixture.public.until(done, seconds=min(deadlines) - time.time())


def close(fixture, run_id):
    run_id = run_id or getattr(fixture, "run_id", None)
    # Close the idle Factory and drain the owning service through public controls.
    from millrace_plus.subworkflow_v2 import closed

    if run_id is not None:
        result = control.cancel_run(
            fixture.instance,
            run_id,
            state_home=fixture.state,
            request_id=uuid.uuid4().hex,
        )
        assert result["status"] in {"applied", "queued"}, result
        fixture.public.until(
            lambda: closed(
                control.inspect_run(fixture.instance, run_id, state_home=fixture.state)
            ),
            seconds=30,
        )
    holder_facts = _join_factory_holders(fixture)
    stop_id = uuid.uuid4().hex
    result = fixture.public.until(
        lambda: (
            result
            if (result := lad.Runtime(Service(fixture.service.home)).stop(stop_id))[
                "status"
            ]
            == "stopped"
            else None
        ),
        seconds=120,
    )
    write_json(
        fixture.root / "cleanup.json",
        {
            "service": result,
            "factory_instance_id": fixture.instance,
            "factory_run_id": run_id,
            "factory_holders": holder_facts,
            "factory_executor_joined": not getattr(
                fixture, "executor", SimpleNamespace(is_alive=lambda: False)
            ).is_alive(),
        },
    )
    return result
