"""Declared Factory routes preserve guard and spent-role boundaries."""

from __future__ import annotations

import json
from pathlib import Path

from millrace_plus.factory_runtime import NATIVE, ROUTES


def test_stock_local_graph_has_exact_selected_routes_and_stopped_check_guards():
    path = (
        Path(__file__).parents[2]
        / "src/millrace_plus/workflows/software-factory/workflow.json"
    )
    graph = json.loads(path.read_bytes())
    nodes = graph["planes"]["factory"]["nodes"]
    assert graph["limits"] == {"max_parallel_invocations": 1}
    assert graph["planes"]["factory"]["workspace"] == "factory"
    for name, edges in ROUTES.items():
        assert set(nodes[name]["outcomes"]) == set(edges)
        for outcome, target in edges.items():
            assert nodes[name]["on"][outcome] == (
                {"pause": {"reason": target[1:], "resume": "reconcile"}}
                if target.startswith("@")
                else {"goto": target}
            )
    for node, guard in [
        ("reproduce", "select_reproduction"),
        ("check_candidate", "select_check"),
    ]:
        assert nodes[node]["on_error"] == nodes[node]["on_unknown"] == {"goto": guard}
        assert nodes[guard]["on"]["CLOSE_READY"] == {"goto": "restore_validation"}
    assert not (
        set(NATIVE)
        & {r["goto"] for r in nodes["reconcile"]["on"].values() if "goto" in r}
    )
    assert nodes["hold"]["on"]["WAIT_OPERATOR"]["pause"]["resume"] == "reconcile"
    assert not any(name in nodes for name in ["execute_effect", "rollback", "health"])


def test_stale_pointer_inspects_later_consuming_runs_before_reuse(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    import pytest

    from millrace_plus import factory_records as fr
    from millrace_plus._factory_steps import Runtime
    from millrace_plus.router import control

    workspace, ci = tmp_path / "factory", tmp_path / "ci"
    workspace.mkdir()
    ci.mkdir()
    ref = {"path": "projection.json", "sha256": "a" * 64}
    pointer = {
        "format": "millrace-plus-cicd-active-role/v2",
        "subject_id": "subject",
        "role": "repair",
        "attempt_id": "attempt",
        "input_ref": ref,
        "projection_ref": ref,
        "basis_sha256": "b" * 64,
    }
    path = workspace / "active-role.json"
    path.write_text(json.dumps(pointer))
    (ci / "attempt.facade-publication.json").write_text(
        json.dumps({"consuming_workspace": str(workspace), "pointer": pointer})
    )
    (ci / "attempt.facade-role.json").write_text(
        json.dumps(
            {
                "preparer": {
                    "instance_id": "instance",
                    "run_id": "origin",
                    "state_home": str(tmp_path),
                }
            }
        )
    )
    runtime = object.__new__(Runtime)
    runtime.home = SimpleNamespace(
        workspace=workspace, ci=SimpleNamespace(workspace=ci)
    )
    monkeypatch.setattr(
        control,
        "inspect_instance",
        lambda *a, **k: {"runs": [{"id": "origin"}, {"id": "later"}]},
    )
    visited = []

    def view(instance, run, **kwargs):
        visited.append(run)
        return {
            "invocations": [{"node": "repair", "writer_state": "stopped"}]
            if run == "later"
            else []
        }

    monkeypatch.setattr(control, "inspect_run", view)
    with pytest.raises(
        fr.FactoryError, match="factory_stale_projection_later_consumer"
    ):
        runtime._invalidate_pointer()
    assert path.exists() and visited == ["origin", "later"]
    monkeypatch.setattr(control, "inspect_run", lambda *a, **k: {"invocations": []})
    runtime._invalidate_pointer()
    assert not path.exists()


def test_one_close_accepts_stopped_prepare_base_then_routes_restore_without_route(
    monkeypatch,
):
    from types import SimpleNamespace

    from millrace_plus import factory_candidate
    from millrace_plus._factory_steps import Runtime

    step = object.__new__(Runtime)
    step.home = SimpleNamespace()
    seen = []

    # Following guard has accepted the exact stopped prepare-base operation.
    def accept(state, facts):
        assert facts["pending"]["node"] == "prepare_baseline"
        seen.append("accept-stopped-operation")
        return state, facts | {"pending": None}

    step._accept_pending = accept
    step._transition = lambda state, facts, outcome: (facts, outcome)
    monkeypatch.setattr(
        factory_candidate, "materialization", lambda home: {"state": "base_ready"}
    )
    facts, outcome = step._select_check(
        {},
        {"pending": {"node": "prepare_baseline"}, "close_requested": True},
        reproduce=False,
    )
    assert seen == ["accept-stopped-operation"] and facts["pending"] is None
    assert outcome == "CLOSE_READY" and "route" not in facts
    assert ROUTES["select_check"][outcome] == "restore_validation"


def test_stock_lad_human_hold_declares_existing_self_resume_target():
    graph = json.loads(
        (
            Path(__file__).parents[2] / "src/millrace_plus/workflows/lad/workflow.json"
        ).read_bytes()
    )
    assert graph["planes"]["foreground"]["nodes"]["human_pause"]["on"]["HOLD"] == {
        "pause": {"reason": "lad_human_decision_required", "resume": "human_pause"}
    }
