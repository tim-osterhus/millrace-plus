"""CI v2 graph derivation against Core's own direct graph admission.

The derived graph is parsed by Core `parse_graph` (no compiler exists), so
edge, outcome and reachability rules are Core's, not a Plus echo.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_cicd_v2_records import _selection, config

from millrace_plus.cicd_v2_records import (
    CicdV2Error,
    check_node_budget,
    reproduction_node_budget,
)
from millrace_plus.cicd_v2_runtime import derive_graph, review_nodes

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "src/millrace_plus/workflows/cicd-v2/workflow.json"
)


def _template() -> dict[str, object]:
    return json.loads(TEMPLATE.read_text())


def _admitted(graph: dict[str, object]):
    from millrace.router.graph import parse_graph

    return parse_graph(json.dumps(graph).encode("utf-8"))


def test_single_reviewer_graph_is_admitted_with_static_budgets() -> None:
    value = config()
    template = _template()
    before = copy.deepcopy(template)
    graph = derive_graph(template, value)
    assert template == before  # The bundled template is never mutated.
    _admitted(graph)
    nodes = graph["planes"]["main"]["nodes"]
    assert "review" not in nodes and "guard_review" not in nodes
    assert nodes["guard_checks"]["on"]["PASS"] == {"goto": "review_1"}
    assert nodes["guard_review_1"]["on"]["ACCEPT"] == {"goto": "guard_delivery"}
    assert nodes["review_1"]["invoke"]["binding"] == "review_1"
    assert (
        graph["bindings"]["review_1"] == value["role_bindings"]["review"][0]["binding"]
    )
    assert (
        nodes["checks"]["invoke"]["timeout_seconds"]
        == check_node_budget(value)["effective_seconds"]
        == 45 + 60 + 120 + 300 + 30
    )
    assert nodes["reproduce"]["invoke"]["timeout_seconds"] == 120 + 30
    # Deterministic nodes keep Core's timeout hierarchy (no copied v1 constants).
    assert "timeout_seconds" not in nodes["reconcile"]["invoke"]
    assert "timeout_seconds" not in nodes["wait_event"]["invoke"]
    assert reproduction_node_budget(value)["effective_seconds"] == 150


def test_two_reviewers_expand_in_declared_order() -> None:
    value = config()
    value["role_bindings"]["review"] = [_selection("first"), _selection("second")]
    graph = derive_graph(_template(), value)
    _admitted(graph)
    nodes = graph["planes"]["main"]["nodes"]
    assert nodes["guard_review_1"]["on"]["ACCEPT"] == {"goto": "review_2"}
    assert nodes["guard_review_2"]["on"]["ACCEPT"] == {"goto": "guard_delivery"}
    for k in (1, 2):
        assert nodes[f"review_{k}"]["on"] == {
            outcome: {"goto": f"guard_review_{k}"}
            for outcome in ("ACCEPT", "REJECT", "BLOCKED")
        }
        for outcome in ("REJECT", "BLOCKED", "STALE"):
            assert nodes[f"guard_review_{k}"]["on"][outcome] == {"goto": "hold"}
        routes = nodes["reconcile"]["on"]
        assert routes[f"REVIEW_READY_{k}"] == {"goto": f"review_{k}"}
        assert routes[f"REVIEW_RESULT_{k}"] == {"goto": f"guard_review_{k}"}
    assert "REVIEW_READY" not in nodes["reconcile"]["outcomes"]
    assert [graph["bindings"][f"review_{k}"]["executable"] for k in (1, 2)] == [
        "tool",
        "tool",
    ]


def test_many_reviewers_have_no_count_cap_and_zero_refuses() -> None:
    assert review_nodes(12)[-1] == ("review_12", "guard_review_12")
    with pytest.raises(CicdV2Error):
        review_nodes(0)
    value = config()
    value["role_bindings"]["review"] = []
    with pytest.raises(CicdV2Error):
        derive_graph(_template(), value)


def test_template_without_review_family_refuses() -> None:
    template = _template()
    del template["planes"]["main"]["nodes"]["guard_review"]
    with pytest.raises(CicdV2Error) as refused:
        derive_graph(template, config())
    assert refused.value.code == "invalid_template"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_idle_wait_returns_idle_before_the_invocation_deadline() -> None:
    from millrace_plus.cicd_v2_records import idle_wait_policy
    from millrace_plus.cicd_v2_runtime import idle_wait

    clock = _Clock()
    policy = idle_wait_policy(config())  # deadline-aware result-emission reserve
    result = idle_wait(
        policy,
        1100.0,
        queued=lambda: False,
        control=lambda: None,
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert result == {"outcome": "IDLE", "reason": None}
    assert clock.now == 1099.0  # deadline minus bookkeeping, never past it
    assert max(clock.sleeps) == 1 and len(clock.sleeps) == 99


def test_idle_wait_wakes_on_intake_or_control_at_the_captured_cadence() -> None:
    from millrace_plus.cicd_v2_runtime import idle_wait

    clock = _Clock()
    policy = {"poll_seconds": 0.5, "bookkeeping_seconds": 5}
    seen = iter([False, False, True])
    woke = idle_wait(
        policy,
        2000.0,
        queued=lambda: next(seen),
        control=lambda: None,
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert woke == {"outcome": "WAKE", "reason": "event_queued"}
    assert clock.sleeps == [0.5, 0.5]
    paused = idle_wait(
        policy,
        2000.0,
        queued=lambda: False,
        control=lambda: "control_applied",
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert paused == {"outcome": "WAKE", "reason": "control_applied"}


@pytest.mark.parametrize("timeout", [0.01, 0.5, 1, 2, 15, 30])
def test_idle_default_sleeps_for_legal_short_deadlines(timeout) -> None:
    from millrace_plus.cicd_v2_records import idle_wait_policy
    from millrace_plus.cicd_v2_runtime import idle_wait

    clock = _Clock()
    policy = idle_wait_policy(config(), timeout_seconds=timeout)
    idle_wait(
        policy,
        clock.now + timeout,
        queued=lambda: False,
        control=lambda: None,
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert clock.sleeps and 0 < sum(clock.sleeps) < timeout


def test_explicit_incompatible_idle_reserve_refuses_before_dispatch() -> None:
    from millrace_plus.cicd_v2_records import idle_wait_policy

    value = config()
    value["node_deadlines"]["wait_event"] = {
        "poll_seconds": 1,
        "bookkeeping_seconds": 2,
        "reason": "emit",
        "origin": "operator",
        "override": "new init",
    }
    with pytest.raises(CicdV2Error, match="idle_wait_budget_incompatible"):
        idle_wait_policy(value, timeout_seconds=2)


def test_prepare_requires_explicit_authority_then_initializes_exact_actor(tmp_path):
    import hashlib
    import sys

    from cicd_support import make_target, write_json

    from millrace_plus import check_contracts as cc
    from millrace_plus.cicd_v2_runtime import V2Home, initialize, prepare

    target, _ = make_target(tmp_path)
    value = config()
    value["target_root"] = str(target)
    value["repository"] = "example/calc"
    value["local_authority_ref"] = None
    policy = cc.stock_path_policy()
    (tmp_path / "path-policy.json").write_bytes(policy)
    value["path_policy_ref"] = cc.make_ref("path-policy.json", policy)
    for command in value["commands"]:
        command["path_policy_ref"] = value["path_policy_ref"]
    patterns = {
        "format": "millrace-plus-cicd-failure-patterns/v1",
        "patterns": [{"id": "assert", "stream": "stdout", "literal": "expected 5"}],
    }
    path = write_json(tmp_path / "failure-patterns.json", patterns)
    value["reproduction"]["failure_patterns_ref"] = cc.make_ref(
        "failure-patterns.json", path.read_bytes()
    )
    template = Path(__file__).parents[2] / "src/millrace_plus/workflows/cicd-v2"
    for role, selection in [
        ("investigate", value["role_bindings"]["investigate"]),
        ("repair", value["role_bindings"]["repair"]),
        ("review", value["role_bindings"]["review"][0]),
    ]:
        selection["binding"]["executable"] = sys.executable
        for kind, key, suffix in [
            ("prompts", "prompt_ref", ".md"),
            ("skills", "skill_ref", ".md"),
            ("schemas", "result_schema_ref", ".json"),
        ]:
            name = f"{kind}/{role}{suffix}"
            destination = tmp_path / name
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes((template / name).read_bytes())
            selection[key] = cc.make_ref(name, destination.read_bytes())
    config_file = write_json(tmp_path / "config.json", value)
    home = tmp_path / "home"
    prepared = prepare(home, target, config_file)
    assert prepared["authority_status"] == "UNAPPROVED"
    assert not (home / "execution.json").exists()
    assert prepare(home, target, config_file) == prepared
    with pytest.raises(CicdV2Error, match="authority_selection_required"):
        initialize(home, target, config_file)
    authority = Path(prepared["suggested_authority_file"])
    result = initialize(home, target, config_file, authority_file=authority)
    loaded = V2Home(home)
    assert result["instance_id"] == loaded.instance()
    assert initialize(home, target, config_file, authority_file=authority) == result
    assert (
        hashlib.sha256((home / "workflow/workflow.json").read_bytes()).hexdigest()
        == prepared["definition_ref"]["sha256"]
    )


def test_real_reproduction_repair_checks_and_review_hold(tmp_path):
    from cicd_support import event, run_check
    from cicd_v2_support import initialized
    from test_cicd_workflow import _run, _start

    from millrace_plus.cicd_v2_runtime import V2Home

    fx = initialized(tmp_path)
    assert run_check(fx["target"]).returncode == 1
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    view = _run(fx)
    assert view["planes"]["main"]["status"] == "paused", view
    state = home.load_state(accepted["input_id"])
    assert state["hold_ref"] is not None, view
    report = home.read_ref(state["hold_ref"])
    assert report["status"] == "checked_patch", (report, view)
    assert run_check(fx["target"]).returncode == 0
    nodes = [row["node"] for row in view["invocations"]]
    assert (
        nodes.index("reproduce")
        < nodes.index("repair")
        < nodes.index("checks")
        < nodes.index("review_1")
    )


def test_real_close_settles_exact_report_and_retains_patch(tmp_path, monkeypatch):
    import cicd_support
    from cicd_support import event, new_id
    from cicd_v2_support import initialized
    from test_cicd_workflow import _resume, _start, drive

    from millrace_plus.cicd_v2_records import DECISION
    from millrace_plus.cicd_v2_runtime import V2Home

    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        (
            "from pathlib import Path\n"
            "Path('build').mkdir(exist_ok=True)\n"
            "Path('build/check.txt').write_text('owned check output')\n"
            + cicd_support.CHECK
        ),
    )
    fx = initialized(tmp_path, products=True)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    state = home.load_state(accepted["input_id"])
    decision = {
        "format": DECISION,
        "request_id": new_id(),
        "input_id": accepted["input_id"],
        "expected_state_version": state["version"],
        "kind": "close",
        "basis_sha256": state["hold_ref"]["sha256"],
        "target_stage": None,
        "changed_cause_ref": None,
        "permission_ref": None,
        "evidence_refs": [],
    }
    receipt = home.decide(decision)
    assert home.decide(decision) == receipt
    drive(fx, _resume)
    assert home.task(accepted["input_id"])["status"] == "succeeded"
    assert home.task(accepted["input_id"])["terminal_receipt"] is not None
    assert (fx["target"] / "src/calc.py").read_text().endswith("return a + b\n")
    assert home.load_state(accepted["input_id"])["hold_ref"] == state["hold_ref"]

    assert not (fx["target"] / "build").exists()
    assert home.load_state(accepted["input_id"])["product_layer_refs"]
    second = home.submit(event(tmp_path, fx["head"], name="second", run_id="second"))
    _resume(fx)
    second_state = home.load_state(second["input_id"])
    assert home.read_ref(second_state["hold_ref"])["status"] == "held_unreproduced"
    assert (fx["target"] / "build/check.txt").read_text() == "owned check output"
    decision.update(
        request_id=new_id(),
        input_id=second["input_id"],
        expected_state_version=second_state["version"],
        basis_sha256=second_state["hold_ref"]["sha256"],
    )
    home.decide(decision)
    drive(fx, _resume)
    assert home.task(second["input_id"])["status"] == "failed"
    assert not (fx["target"] / "build").exists()
    assert (fx["target"] / "src/calc.py").read_text().endswith("return a + b\n")


def test_v2_cli_status_and_queued_cancel_are_inert(tmp_path, capsys):
    from cicd_support import event, new_id
    from cicd_v2_support import initialized

    from millrace_plus.cicd_cli import main
    from millrace_plus.cicd_v2_runtime import V2Home

    fx = initialized(tmp_path)
    home = V2Home(fx["home"])
    accepted = home.submit(event(tmp_path, fx["head"]))
    assert main(["status", "--workflow-home", str(fx["home"])]) == 0
    assert json.loads(capsys.readouterr().out)["inputs"][0]["status"] == "queued"
    assert (
        main(
            [
                "cancel",
                "--workflow-home",
                str(fx["home"]),
                "--event",
                accepted["input_id"],
                "--request-id",
                new_id(),
            ]
        )
        == 0
    )
    assert home.task(accepted["input_id"])["status"] == "cancelled"
    assert home.load_state(accepted["input_id"]) is None


@pytest.mark.parametrize(
    "mode", ["import", "native", "interrupted_import", "never_started_native"]
)
def test_public_candidate_export_import_check_review_verify_restore(
    tmp_path, monkeypatch, mode
):
    import shutil
    import sys

    import cicd_support
    from cicd_support import event, make_target
    from cicd_v2_support import initialized
    from test_cicd_workflow import _resume, _start

    from millrace_plus import cicd_v2_runtime as runtime
    from millrace_plus.cicd_source import claim_target, filesystem_identity
    from millrace_plus.cicd_v2_runtime import V2Home
    from millrace_plus.router import control

    # Two fresh owned fixture repositories, with the same actual anchor commit.
    monkeypatch.setitem(cicd_support.GIT_ENV, "GIT_AUTHOR_DATE", "2001-01-01T00:00:00Z")
    monkeypatch.setitem(
        cicd_support.GIT_ENV, "GIT_COMMITTER_DATE", "2001-01-01T00:00:00Z"
    )
    monkeypatch.setattr(
        cicd_support,
        "CHECK",
        (
            "from pathlib import Path as ProductPath\n"
            "ProductPath('build').mkdir(exist_ok=True)\n"
            "ProductPath('build/check.txt').write_bytes(ProductPath('src/calc.py').read_bytes())\n"
            + cicd_support.CHECK
        ),
    )
    source, source_head = make_target(tmp_path / "development")
    source_home = tmp_path / "development-owner"
    source_home.mkdir()
    claim_target(source, filesystem_identity(source), source_home, "d" * 32)
    original_derive = runtime.derive_graph
    original_copy = shutil.copytree
    names = [
        "wait_event",
        "export_anchor",
        "prepare_base",
        "guard_base",
        "base_check",
        "guard_base_check",
        "change_source",
        "export_job",
        "import_job",
        "guard_import",
        "final_check",
        "guard_final_check",
        "prepare_review",
        "review_candidate",
        "guard_review",
        "verify_job",
        "restore_job",
        "guard_restore",
    ]

    if mode in {"native", "never_started_native"}:
        start = names.index("change_source")
        stop = names.index("final_check")
        names[start:stop] = [
            "prepare_investigation",
            "investigate",
            "guard_investigation",
            "prepare_role",
            "repair",
            "guard_native_repair",
            "export_job",
            "guard_seal",
        ]

    if mode == "interrupted_import":
        names[names.index("final_check") : names.index("restore_job")] = []

    def derive(template, config):
        graph = original_derive(template, config)
        graph["bindings"]["command"]["executable"] = sys.executable
        nodes = {}
        for index, name in enumerate(names):
            outcome = (
                "ACCEPT"
                if name == "review_candidate"
                else "PATCH_READY"
                if name == "repair"
                else "ASSESSED"
                if name == "investigate"
                else "DONE"
            )
            invoke = (
                {"binding": "review_1", "config": {"args": ["candidate-review"]}}
                if name == "review_candidate"
                else {
                    "binding": "command",
                    "config": {"script": "scripts/facade.py", "args": ["facade", name]},
                }
            )
            if name in {"repair", "investigate"}:
                invoke = {
                    "binding": name,
                    "inputs": ["active-role.json"],
                    "config": {"args": [name]},
                }
            nodes[name] = {
                "invoke": invoke,
                "result": "json-v1",
                "outcomes": [outcome],
                "on": {
                    outcome: {"goto": names[index + 1]}
                    if index + 1 < len(names)
                    else {"finish": "succeeded"}
                },
                "on_error": {"pause": {"reason": "fixture_failure", "resume": name}},
                "on_unknown": {"pause": {"reason": "fixture_unknown", "resume": name}},
            }
        if mode == "never_started_native":
            nodes["prepare_role"]["on"]["DONE"] = {
                "pause": {"reason": "fixture_never_dispatched", "resume": "repair"}
            }
            nodes["wait_event"]["outcomes"].append("RESTORE")
            nodes["wait_event"]["on"]["RESTORE"] = {"goto": "restore_job"}
        graph["planes"]["main"]["start"] = names[0]
        graph["planes"]["main"]["nodes"] = nodes
        graph["control_files"] += ["scripts/facade.py", "context/facade-fixture.json"]
        return graph

    def copy(src, dst, *args, **kwargs):
        result = original_copy(src, dst, *args, **kwargs)
        if Path(dst).name == "workflow":
            text = (Path(__file__).parent / "fixtures/cicd_v2_peer.py").read_text()
            if mode == "never_started_native":
                text = text.replace(
                    '    source = Path(fixture["source"])',
                    '    source = Path(fixture["source"])\n'
                    '    if node == "wait_event" '
                    'and state.get("prepare_role_context"):\n'
                    '        state["candidate_ref"] = state["anchor_candidate_ref"]\n'
                    "        state_file.write_bytes(canonical(state))\n"
                    '        print(json.dumps({"outcome": "RESTORE", '
                    '"summary": "explicit restore", "refs": []}))\n'
                    "        return",
                )
            (Path(dst) / "scripts/facade.py").write_text(text)
            (Path(dst) / "context").mkdir(exist_ok=True)
            (Path(dst) / "context/facade-fixture.json").write_text(
                json.dumps({"source": str(source), "mode": mode})
            )
        return result

    monkeypatch.setattr(runtime, "derive_graph", derive)
    monkeypatch.setattr(shutil, "copytree", copy)
    fx = initialized(tmp_path, products=True)
    assert fx["head"] == source_head
    home = V2Home(fx["home"])
    home.submit(event(tmp_path, fx["head"]))
    _start(fx)
    instance = control.inspect_instance(fx["instance"], state_home=fx["state"])
    run_id = instance["runs"][0]["id"]
    view = control.inspect_run(fx["instance"], run_id, state_home=fx["state"])
    if mode == "never_started_native":
        from cicd_support import new_id

        from millrace_plus import cicd_candidates as facade
        from millrace_plus.cicd_records import CicdError

        assert view["status"] == "paused", view
        lease = facade._lease(home)
        repair = home.read_ref(lease["operation_refs"][-1])
        assert repair["operation"] == "repair" and repair["status"] == "reserved"
        with pytest.raises(CicdError, match="prior_role_writer_unknown"):
            facade._prior_roles_closed(home)
        control.cancel_run(
            fx["instance"], run_id, state_home=fx["state"], request_id=new_id()
        )
        terminal = control.inspect_run(fx["instance"], run_id, state_home=fx["state"])
        assert terminal["quiescent"] and all(
            row["node"] != "repair" for row in terminal["invocations"]
        )
        _start(fx)
        instance = control.inspect_instance(fx["instance"], state_home=fx["state"])
        run_id = instance["runs"][-1]["id"]
        view = control.inspect_run(fx["instance"], run_id, state_home=fx["state"])
        restored = facade._lease(home)
        assert restored["state"] == "restored"
        receipts = [home.read_ref(ref) for ref in restored["operation_refs"]]
        assert [row for row in receipts if row["operation"] == "repair"] == [repair]
        plan = home.read_ref(home.read_ref(restored["restoration_ref"])["plan_ref"])
        assert len(plan["never_started_refs"]) == 1
        proof = home.read_ref(plan["never_started_refs"][0])
        assert proof["kind"] == "projection_never_started"
        assert home.read_ref(proof["public_view_ref"])["id"] == terminal["id"]
    if mode == "interrupted_import":
        from millrace_plus import cicd_candidates as facade

        assert view["status"] == "paused", view
        lease = facade._lease(home)
        operation = home.read_ref(lease["operation_refs"][-1])
        assert operation["operation"] == "import" and operation["status"] == "reserved"
        assert (home.target / "src/calc.py").read_text().endswith("return a + b\n")
        assert not (home.target / "src/details.txt").exists()
        expected = (home.target / "src/calc.py").read_bytes()
        (home.target / "src/calc.py").write_text("foreign third value\n")
        _resume(fx)
        assert facade._lease(home) == lease
        assert (home.target / "src/calc.py").read_text() == "foreign third value\n"
        (home.target / "src/calc.py").write_bytes(expected)
        _resume(fx)
        view = control.inspect_run(fx["instance"], run_id, state_home=fx["state"])
        restored = facade._lease(home)
        receipts = [home.read_ref(ref) for ref in restored["operation_refs"]]
        imports = [row for row in receipts if row["operation"] == "import"]
        assert len({row["operation_id"] for row in imports}) == 1
        assert any(row["previous_receipt_ref"] is not None for row in imports)
        assert restored["state"] == "restored"
        assert sum(row["node"] == "import_job" for row in view["invocations"]) == 3
    assert view["status"] == "succeeded", view
    evidence = json.loads((home.workspace / "facade-test-state.json").read_text())
    if mode not in {"interrupted_import", "never_started_native"}:
        assert home.read_ref(evidence["verification_ref"])["status"] == "verified"
    assert (home.target / "src/calc.py").read_text().endswith("return a - b\n")
    assert (
        (source / "src/calc.py")
        .read_text()
        .endswith(
            "return a - b\n"
            if mode in {"native", "never_started_native"}
            else "return a + b\n"
        )
    )
    assert not (home.target / "build").exists()
    assert all(row["writer_state"] == "stopped" for row in view["invocations"])
