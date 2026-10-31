"""L-Q1 engineering feedback: strict records, capture, checks, guards and history.

Inert peers and local commands only; no provider, OS service or native call.
Fake deterministic peers are ordinary source tests, not provider/B4 evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from test_lad_shared_target import shared  # noqa: F401 - pytest fixture
from test_lad_workflow import LAD, bootstrap, command, fake_graph, ledger, started, step

from millrace_plus import check_contracts as cc
from millrace_plus import engineering_feedback as ef
from millrace_plus.router import control

PYTHON = sys.executable
REASON = {"reason": "test command's real need", "origin": "test", "override": "edit"}
PRODUCT_RULE = {
    "surface": "product_paths",
    "prefixes": ["reports"],
    "reason": "deterministic check report",
    "origin": "test repository",
    "override": "capture another policy",
}
CORE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def policy_bytes(*extra: dict) -> bytes:
    stock = json.loads(cc.stock_path_policy())
    return cc.encode({"format": cc.PATH_POLICY, "rules": stock["rules"] + list(extra)})


def initial_history(home: Path, history_id: str = "repo") -> dict:
    record = ef.with_integrity({
        "format": ef.SNAPSHOT, "history_id": history_id, "sequence": 1,
        "repository_id": "repo", "previous_ref": None, "accepted_tasks": [],
        "requirements": [],
        "source_ref": {"target_id": "repo", "git_base": None, "index_sha256": None,
                       "scoped_files_sha256": "0" * 64, "outside_sha256": "0" * 64},
    })
    raw = ef.record_bytes(record)
    path = ef.snapshot_relative(history_id, 1, sha(raw))
    (home / path).parent.mkdir(parents=True, exist_ok=True)
    (home / path).write_bytes(raw)
    return {"history_id": history_id, "sequence": 1, "path": path, "sha256": sha(raw)}


def definition(identity: str, argv: list[str], policy_ref: dict, *,
               products: tuple[str, ...] = (), selection: dict | None = None,
               required: bool = True, timeout: float | None = None,
               requirement_ids: tuple[str, ...] = ()) -> dict:
    return {
        "id": identity, "purpose": "functional", "argv": argv, "cwd": ".",
        "environment": {"inherit_names": [], "set": {}},
        "timeout_seconds": timeout, "timeout_policy": REASON if timeout else None,
        "expected_exit_code": 0, "path_policy_ref": policy_ref,
        "expected_products": list(products), "inputs": [],
        "requirement_ids": list(requirement_ids),
        "selection": selection or {"when": "always", "paths": []},
        "required": required, "origin": "test" if required else None,
        "override": "test" if required else None,
    }


def standard_checks(policy_ref: dict, requirement_ids: tuple[str, ...] = ()) -> list:
    return [
        definition("answer-unit", [PYTHON, "-c",
                                   "import answer,sys;"
                                   "sys.exit(0 if answer.answer()==42 else 1)"],
                   policy_ref, requirement_ids=requirement_ids),
        definition("report", [PYTHON, "-c",
                              "import pathlib;p=pathlib.Path('reports');"
                              "p.mkdir(exist_ok=True);(p/'r.txt').write_text('ok')"],
                   policy_ref, products=("reports",), required=False),
        definition("docs-only", [PYTHON, "-c", "pass"], policy_ref,
                   selection={"when": "paths_changed", "paths": ["docs"]},
                   required=False),
    ]


def profile_record(pin: dict, policy_ref: dict, checks: list, **changes) -> dict:
    record = {
        "format": ef.PROFILE, "profile_id": "repo-profile", "revision": "1",
        "repository": {"target_id": "repo", "root_ref": "foreground/target",
                       "source_scope": ["."], "excluded_paths": []},
        "requirements_snapshot": pin, "checks": checks, "guidelines": [],
        "metrics": {"changed_functions": False, "duplication": False,
                    "nesting": False},
        "artifact_policy": {"deliverable_paths": ["."],
                            "evidence_root": "engineering-evidence",
                            "scratch_root": "engineering-scratch",
                            "retention_root": "engineering-retained"},
        "review_bindings": None, "path_policy_ref": policy_ref,
        "check_bookkeeping": {"seconds": 30, **REASON}, "family_policy": "none",
        "evaluation_export": {"enabled": False, "destination_ref": None},
    }
    record.update(changes)
    return record


def enabled_copy(tmp_path: Path, *, pin: dict | None = None, name: str = "project",
                 checks=None, policy: bytes | None = None, seed: Path | None = None,
                 **changes) -> tuple[Path, Path, dict]:
    project = tmp_path / name
    shutil.copytree(LAD, project)
    history = tmp_path / "history"
    history.mkdir(exist_ok=True)
    pin = pin or initial_history(history)
    inputs = tmp_path / ("inputs-" + name)
    inputs.mkdir()
    raw = policy if policy is not None else policy_bytes(PRODUCT_RULE)
    (inputs / "check-path-policy.json").write_bytes(raw)
    policy_ref = {"path": "check-path-policy.json", "sha256": sha(raw)}
    record = profile_record(
        pin, policy_ref,
        standard_checks(policy_ref) if checks is None else checks(policy_ref),
        **changes,
    )
    (inputs / "profile.json").write_bytes(cc.encode(record))
    if seed is not None:
        shutil.copytree(seed, project / "foreground/target")
    else:
        (project / "foreground/target").mkdir()
    result = ef.capture(project, inputs / "profile.json", history)
    return project, history, result


def node_of(check_id: str) -> str:
    return ef.node_names(check_id)["baseline_node"][3:]


def estate(project: Path) -> dict:
    return json.loads(
        (project / "foreground/lad-state/engineering/state.json").read_bytes()
    )


def record_at(project: Path, ref: dict) -> dict:
    raw = (project / "foreground" / ref["path"]).read_bytes()
    assert sha(raw) == ref["sha256"]
    return json.loads(raw)


def invocation(project: Path, tmp_path: Path) -> Path:
    """A fresh Core-shaped invocation root holding this copy's captured controls."""
    root = tmp_path / "state/invocations" / uuid.uuid4().hex / "controls"
    graph = json.loads((project / "workflow.json").read_bytes())
    for name in ["workflow.json", *graph["control_files"]]:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / name, destination)
    return root / "scripts/engineering_feedback.py"


def run_at(project: Path, script: Path, *args: str) -> dict:
    child = subprocess.run(
        [PYTHON, str(script), *args], cwd=project / "foreground",
        env={"PATH": str(Path(PYTHON).parent), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, check=True, timeout=120,
    )
    return json.loads(child.stdout)


def to_builder(project: Path) -> None:
    """Direct harness: Recon, Planner and Manager accepted; baseline route next."""
    bootstrap(project)
    step(project, "recon")
    step(project, "planner")
    assert step(project, "manager") == "BUILDER"


# ------------------------------------------------------------------ profile


def test_engineering_profile_capture_and_legacy(tmp_path: Path) -> None:
    project, history, result = enabled_copy(tmp_path)
    assert result["status"] == "captured" and result["operator_ceiling_known"] is False
    graph = json.loads((project / "workflow.json").read_bytes())
    stock = json.loads((LAD / "workflow.json").read_bytes())
    nodes = graph["planes"]["foreground"]["nodes"]
    assert {"engineering_baseline", "engineering_candidate", "engineering_admit",
            "engineering_finalize"} <= set(nodes)
    assert nodes["accept_manager_pass"]["on"]["BUILDER"] == {
        "goto": "engineering_baseline"}
    assert nodes["accept_builder_pass"]["on"]["CHECKER"] == {
        "goto": "engineering_candidate"}
    assert nodes["accept_arbiter_pass"]["on"]["CLOSE"] == {
        "goto": "engineering_finalize"}
    for name in (ef.PROFILE_PATH, ef.CAPTURE_PATH, ef.RESOLVED_PATH, ef.NODE_MAP_PATH):
        assert name in graph["control_files"] and (project / name).is_file()
    # The stock graph keeps the exact legacy routes; only two controls were added.
    stock_nodes = stock["planes"]["foreground"]["nodes"]
    assert not set(ef.FIXED_NODES) & set(stock_nodes)
    assert stock_nodes["accept_manager_pass"]["on"]["BUILDER"] == {
        "goto": "prepare_builder"}
    # Identical inputs replay; a different profile cannot edit captured guidance.
    inputs = tmp_path / "inputs-project"
    again = ef.capture(project, inputs / "profile.json", history)
    assert again["graph_sha256"] == result["graph_sha256"]
    edited = json.loads((inputs / "profile.json").read_bytes())
    edited["revision"] = "2"
    (inputs / "profile.json").write_bytes(cc.encode(edited))
    with pytest.raises(ef.EngineeringError, match="immutable_conflict"):
        ef.capture(project, inputs / "profile.json", history)
    # Enabled init validates before any role and publishes the sidecar atomically.
    bootstrap(project)
    sidecar = json.loads(
        (project / "foreground/lad-state/engineering.json").read_bytes()
    )
    assert sidecar["profile_sha256"] == sha((project / ef.PROFILE_PATH).read_bytes())
    # A used copy refuses engineering-init instead of mutating guidance.
    with pytest.raises(ef.EngineeringError,
                       match="engineering_init_requires_quiescent_new_copy"):
        ef.capture(project, inputs / "profile.json", history)
    # A later context edit stales init; a missing capture with a sidecar refuses.
    (project / ef.PROFILE_PATH).write_bytes(cc.encode(edited))
    edited_init = command(project, "init_session.py", "--project-root", "..")
    assert edited_init["outcome"] == "RECONCILE"
    legacy = tmp_path / "legacy"
    shutil.copytree(LAD, legacy)
    bootstrap(legacy)
    assert not (legacy / "foreground/lad-state/engineering.json").exists()
    assert not (legacy / "foreground/lad-state/engineering").exists()
    refused = subprocess.run(
        [PYTHON, str(legacy / "scripts/engineering_feedback.py"), "admit"],
        cwd=legacy / "foreground", env={"PATH": str(Path(PYTHON).parent)},
        capture_output=True, check=True,
    )
    assert json.loads(refused.stdout)["outcome"] == "REFUSED"
    (legacy / "foreground/lad-state/engineering.json").write_bytes(b"{}\n")
    assert command(legacy, "init_session.py", "--project-root", "..")[
        "outcome"] == "RECONCILE"


@pytest.mark.parametrize("mutation", [
    "unknown_field", "version", "required_without_origin", "explicit_timeout_policy",
    "always_with_paths", "duplicate_check", "export_without_destination",
    "policy_ref_mismatch", "required_guideline_unbacked", "root_in_target",
])
def test_profile_strict_refusals(tmp_path: Path, mutation: str) -> None:
    raw = policy_bytes(PRODUCT_RULE)
    ref = {"path": "check-path-policy.json", "sha256": sha(raw)}
    record = profile_record(initial_history(tmp_path), ref, standard_checks(ref))
    first = record["checks"][0]
    if mutation == "unknown_field":
        record["extra"] = 1
    elif mutation == "version":
        record["format"] = "millrace-lad-engineering-profile/v2"
    elif mutation == "required_without_origin":
        first["origin"] = None
    elif mutation == "explicit_timeout_policy":
        first["timeout_seconds"] = 5
    elif mutation == "always_with_paths":
        first["selection"] = {"when": "always", "paths": ["src"]}
    elif mutation == "duplicate_check":
        record["checks"].append(dict(first))
    elif mutation == "export_without_destination":
        record["evaluation_export"] = {"enabled": True, "destination_ref": None}
    elif mutation == "policy_ref_mismatch":
        first["path_policy_ref"] = {"path": "other.json", "sha256": "1" * 64}
    elif mutation == "required_guideline_unbacked":
        record["guidelines"] = [{"id": "g", "text": "t", "source_ref": "nope:x",
                                 "mode": "required"}]
    else:
        record["artifact_policy"]["scratch_root"] = "target/scratch"
    if mutation == "required_guideline_unbacked":
        profile = ef.load_profile(cc.encode(record))
        with pytest.raises(ef.EngineeringError):
            ef.required_guidelines(profile, ef.load_requirements(
                (tmp_path / record["requirements_snapshot"]["path"]).read_bytes()
            ))
        return
    with pytest.raises(ef.EngineeringError):
        ef.load_profile(cc.encode(record))


# ---------------------------------------------------------- generated nodes


def test_engineering_generated_nodes_preserve_long_check_ids(tmp_path: Path) -> None:
    long_id = "ß-検査-" + "x" * 500 + "-✓"
    names = ef.node_names(long_id)
    digest = hashlib.sha256(long_id.encode("utf-8")).digest()
    import base64

    encoded = base64.b32encode(digest).decode().rstrip("=")
    assert len(encoded) == 52 and names["digest"] == digest.hex()
    for key, prefix in (("baseline_node", "eb_"), ("candidate_node", "ec_"),
                        ("baseline_accept_node", "ab_"),
                        ("candidate_accept_node", "ac_")):
        assert names[key] == prefix + encoded and len(names[key]) == 55
        assert CORE_IDENTIFIER.fullmatch(names[key])

    def checks(ref: dict) -> list:
        return [definition(long_id, [PYTHON, "-c", "pass"], ref)]

    project, _, _ = enabled_copy(tmp_path, checks=checks)
    mapping = json.loads((project / ef.NODE_MAP_PATH).read_bytes())
    assert mapping["entries"] == [names]
    assert mapping["entries"][0]["check_id"] == long_id
    resolved = json.loads((project / ef.RESOLVED_PATH).read_bytes())
    assert resolved["checks"][0]["definition"]["id"] == long_id
    nodes = json.loads((project / "workflow.json").read_bytes())["planes"][
        "foreground"]["nodes"]
    assert nodes[names["baseline_node"]]["on"]["PASS"] == {
        "goto": names["baseline_accept_node"]}
    assert nodes["engineering_candidate"]["on"]["RUN_" + encoded] == {
        "goto": names["candidate_node"]}
    # Core admits the expanded graph directly (no compiler/IR).
    fake_graph(project)
    made = control.create_instance(
        project / "workflow.json", project, state_home=tmp_path / "state",
        request_id=uuid.uuid4().hex, expected_state_version=0,
    )
    assert made["status"] == "applied", made
    # Conflicts refuse; nothing is truncated or renamed.
    graph = json.loads((LAD / "workflow.json").read_bytes())
    graph["planes"]["foreground"]["nodes"][names["baseline_node"]] = graph[
        "planes"]["foreground"]["nodes"]["finalize"]
    with pytest.raises(ef.EngineeringError, match="engineering_node_conflict"):
        ef.expand_graph(graph, [names], {long_id: {"budget_seconds": 31}}, [])
    graph = json.loads((LAD / "workflow.json").read_bytes())
    with pytest.raises(ef.EngineeringError, match="engineering_node_conflict"):
        ef.expand_graph(graph, [names, dict(names, check_id="other")],
                        {long_id: {"budget_seconds": 31},
                         "other": {"budget_seconds": 31}}, [])
    raw = policy_bytes()
    ref = {"path": "check-path-policy.json", "sha256": sha(raw)}
    duplicate = profile_record(initial_history(tmp_path / "h"), ref,
                               [definition("a", [PYTHON], ref)] * 2)
    with pytest.raises(ef.EngineeringError, match="duplicate_check_id"):
        ef.load_profile(cc.encode(duplicate))


# ------------------------------------------------------ deadlines/mapping


def test_engineering_check_deadlines_and_ci_mapping(tmp_path: Path) -> None:
    def checks(ref: dict) -> list:
        return [
            definition("inherited", [PYTHON, "-c", "pass"], ref),
            definition("explicit", [PYTHON, "-c", "pass"], ref, timeout=7),
        ]

    project, _, result = enabled_copy(tmp_path, checks=checks)
    resolved = json.loads((project / ef.RESOLVED_PATH).read_bytes())
    inherited, explicit = resolved["checks"]
    # Stock LAD workflow default supplies the inherited value; the generated
    # check node never feeds back into its own timeout.
    assert inherited["timeout_source"] == "workflow"
    assert inherited["definition"]["timeout_seconds"] == 1800
    assert explicit["timeout_source"] == "explicit"
    assert explicit["definition"]["timeout_policy"] == REASON
    nodes = json.loads((project / "workflow.json").read_bytes())["planes"][
        "foreground"]["nodes"]
    for row in resolved["checks"]:
        names = ef.node_names(row["definition"]["id"])
        budget = row["definition"]["timeout_seconds"] + 30
        assert nodes[names["baseline_node"]]["invoke"]["timeout_seconds"] == budget
        assert nodes[names["candidate_node"]]["invoke"]["timeout_seconds"] == budget
    assert [row["budget_seconds"] for row in result["checks"]] == [1830, 37]
    # A graph without plane/workflow defaults reaches Core's fallback + warning.
    graph = json.loads((LAD / "workflow.json").read_bytes())
    graph.pop("defaults")
    raw = policy_bytes()
    ref = {"path": "check-path-policy.json", "sha256": sha(raw)}
    profile = ef.load_profile(cc.encode(profile_record(
        initial_history(tmp_path / "h"), ref, checks(ref)
    )))
    policy = cc.validate_path_policy(cc.parse(raw))
    rows, _ = ef.resolve_checks(profile, {"path": "p", "sha256": "0" * 64}, graph,
                                policy)
    assert rows[0]["timeout_source"] == "fallback"
    assert rows[0]["timeout_warning"] == cc.TIMEOUT_DEFAULTED
    # A known operator ceiling below a derived check budget refuses (Q3).
    with pytest.raises(ef.EngineeringError,
                       match="operator_ceiling_clips_check_budget"):
        ef.resolve_checks(profile, {"path": "p", "sha256": "0" * 64}, graph, policy,
                          ceiling=100)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"operator_policy": {"timeout_ceiling_seconds": 50},
                                    "runtime_defaults": {"timeout_seconds": 11}}))
    known = ef._core_settings(settings)
    assert (known["timeout_ceiling_seconds"], known["runtime_timeout_seconds"]) == (
        50, 11)
    rows, budgets = ef.resolve_checks(
        profile, {"path": "p", "sha256": "0" * 64}, graph, policy,
        runtime_timeout=11, ceiling=50,
    )
    assert rows[0]["timeout_source"] == "runtime"
    assert [row["clipped"] for row in budgets] == [False, False]
    # Equal L-Q and CI execution objects map; any unequal field refuses.
    lq = {row["definition"]["id"]: row["definition"] for row in resolved["checks"]}
    command_row = {key: lq["explicit"][key] for key in cc.EXECUTION_FIELDS}
    digest = cc.execution_sha256(cc.execution_of(command_row))
    assert digest == explicit["execution_sha256"]
    mapping = {"format": cc.COMMAND_MAP, "entries": [
        {"lq_check_id": "explicit", "ci_command_id": "ci-explicit",
         "execution_sha256": digest}]}
    assert cc.verify_command_map(mapping, {"explicit": lq["explicit"]},
                                 {"ci-explicit": command_row}) == {
        "explicit": "ci-explicit"}
    changed = dict(command_row, cwd="src")
    with pytest.raises(cc.CheckContractError, match="command_map_execution_mismatch"):
        cc.verify_command_map(mapping, {"explicit": lq["explicit"]},
                              {"ci-explicit": changed})


# ------------------------------------------------------------ comparisons


def evidence(status: str, *, definition_sha: str = "a" * 64,
             environment_sha: str = "b" * 64) -> dict:
    ran = status in ("pass", "fail")
    return ef.evidence_record(
        check_id="c", definition_sha256=definition_sha, profile_sha256="c" * 64,
        task_id="t", session_id="s", occurrence_id="o", phase="baseline",
        source_ref={"path": "s.json", "sha256": "d" * 64}, execution_sha256="e" * 64,
        argv=["x"], cwd=".", environment_sha256=environment_sha,
        path_policy_ref={"path": "p.json", "sha256": "f" * 64},
        product_layer_refs=[{"path": "l.json", "sha256": "4" * 64}],
        invocation_deadline=None,
        timeout_seconds=1, tool_version=None,
        started_at="2026-10-31T00:00:00+00:00" if ran else None,
        finished_at="2026-10-31T00:00:01+00:00" if ran else None,
        exit_code=(0 if status == "pass" else 1) if ran else None, status=status,
        stdout_ref={"path": "o", "sha256": "1" * 64} if ran else None,
        stderr_ref={"path": "e", "sha256": "2" * 64} if ran else None,
        requirement_ids=[], selection_reason="always",
        unrun_reason=None if ran else "not_selected:x" if status == "not_selected"
        else "tool_unavailable:x",
    )


def test_engineering_check_comparison_and_unavailable() -> None:
    ref = {"path": "a", "sha256": "3" * 64}
    cases = [("pass", "pass", "unchanged_pass"), ("pass", "fail", "regression"),
             ("fail", "fail", "preexisting_failure"), ("fail", "pass", "improved"),
             (None, "pass", "not_comparable"), ("unavailable", "pass",
                                                "not_comparable"),
             ("pass", "unavailable", "unavailable"),
             ("pass", "not_selected", "not_selected")]
    for before, after, expected in cases:
        base = evidence(before) if before else None
        row = ef.compare_checks(base, ref if base else None, evidence(after), ref)
        assert row["classification"] == expected, (before, after)
        ef.validate_comparison(row)
    changed = ef.compare_checks(evidence("pass"), ref,
                                evidence("pass", definition_sha="9" * 64), ref)
    assert (changed["classification"], changed["reason"]) == (
        "not_comparable", "definition_changed")
    environment = ef.compare_checks(evidence("pass"), ref,
                                    evidence("pass", environment_sha="8" * 64), ref)
    assert environment["reason"] == "environment_changed"
    # Missing output, nonzero relabelled pass or broken integrity refuse.
    bad = evidence("fail")
    bad["status"] = "pass"
    with pytest.raises(ef.EngineeringError):
        ef.validate_evidence(bad)
    unrun = evidence("unavailable")
    unrun["stdout_ref"] = {"path": "o", "sha256": "1" * 64}
    with pytest.raises(ef.EngineeringError):
        ef.validate_evidence(ef.with_integrity(unrun))
    definition_row = {"expected_exit_code": 0}
    assert ef.classify_status(definition_row, 0, None, True) == "pass"
    assert ef.classify_status(definition_row, 0, None, False) == "fail"
    assert ef.classify_status(definition_row, 0, "timeout", True) == "fail"
    assert ef.classify_status(definition_row, 3, None, True) == "fail"
    assert ef.classify_status({"expected_exit_code": -15}, -15, None, True) == "pass"


# ------------------------------------------------------- findings/routes


def findings_record(rows: list[dict]) -> dict:
    return {"format": ef.FINDINGS, "occurrence_id": "o", "profile_sha256": "p" * 64,
            "source_sha256": "s" * 64, "findings": rows}


def finding(**changes) -> dict:
    row = {"id": "f1", "criterion_id": None, "guideline_id": None, "kind": "nesting",
           "location": "src/a.py:f", "evidence_refs": [], "likelihood": "plausible",
           "impact": "Harder to follow nested branches in f.",
           "proposed_action": "Inspect f during review.", "blocking": False}
    row.update(changes)
    return row


def test_maintenance_findings_do_not_invent_requirements() -> None:
    def check(rows: list[dict]) -> dict:
        return ef.validate_findings(
            findings_record(rows), occurrence_id="o", profile_sha256="p" * 64,
            source_sha256="s" * 64, criteria={"answer"},
            guidelines={"style": "advisory", "api": "required"},
        )

    check([finding()])  # Advisory metric observation: allowed, never blocking.
    evidence_ref = {"path": "e.json", "sha256": "4" * 64}
    for rows in (
        [finding(blocking=True)],
        [finding(blocking=True, criterion_id="answer", likelihood="hypothetical",
                 evidence_refs=[evidence_ref])],
        [finding(blocking=True, guideline_id="style", likelihood="demonstrated",
                 evidence_refs=[evidence_ref])],
    ):
        with pytest.raises(ef.EngineeringError, match="speculative_blocking_finding"):
            check(rows)
    with pytest.raises(ef.EngineeringError, match="finding_unknown_criterion"):
        check([finding(criterion_id="invented")])
    check([finding(blocking=True, criterion_id="answer", likelihood="demonstrated",
                   kind="regression", evidence_refs=[evidence_ref])])
    check([finding(blocking=True, guideline_id="api", likelihood="demonstrated",
                   kind="architecture", evidence_refs=[evidence_ref])])
    with pytest.raises(ef.EngineeringError, match="maintenance_findings_identity"):
        ef.validate_findings(dict(findings_record([]), occurrence_id="other"),
                             occurrence_id="o", profile_sha256="p" * 64,
                             source_sha256="s" * 64, criteria=set(), guidelines={})


def test_refactor_prerequisite_uses_existing_routes(tmp_path: Path) -> None:
    """A Manager packet stays inside captured scope; metrics never route a refactor."""
    project, _, _ = enabled_copy(
        tmp_path, repository={"target_id": "repo", "root_ref": "foreground/target",
                              "source_scope": ["answer.py", "lib"],
                              "excluded_paths": ["vendor"]},
    )
    graph = json.loads((project / "workflow.json").read_bytes())
    nodes = graph["planes"]["foreground"]["nodes"]
    # No standalone refactor stage or metric route; Consultant/Manager unchanged.
    assert not any("refactor" in name for name in nodes)
    assert nodes["accept_consultant_repair_packets"]["on"]["MANAGER"] == {
        "goto": "prepare_manager"}
    bootstrap(project)
    step(project, "recon")
    step(project, "planner")
    accepted = step(project, "manager")
    assert accepted == "BUILDER"
    state = ledger(project)
    assert [row["paths"] for row in state["plan"]] == [["answer.py"]]
    criteria = list(state["criteria"])
    # A packet path outside admitted scope or masked by policy is an invalid
    # Manager result (ordinary recovery), never a silent scope expansion.
    # A probe beside the copied scripts imports them as Core's command would.
    probe = project / "scripts/plan_gate_probe.py"
    probe.write_text("""
from pathlib import Path
import engineering_feedback as e
from lad_state import LadError
for paths in (['vendor/x.py'], ['other.py'], ['lib/__pycache__']):
    try:
        e.plan_gate(Path.cwd(), [{'paths': paths}])
    except LadError as exc:
        assert str(exc).startswith('finding_packet_'), exc
    else:
        raise AssertionError(paths)
e.plan_gate(Path.cwd(), [{'paths': ['lib/mod.py']}])
""")
    subprocess.run([PYTHON, str(probe)], cwd=project / "foreground",
                   env={"PATH": str(Path(PYTHON).parent)}, check=True,
                   capture_output=True)
    assert ledger(project)["criteria"] == criteria


# ---------------------------------------------------------- artifacts


def test_artifact_admission_preserves_foreign_work(tmp_path: Path) -> None:
    policy = {"deliverable_paths": ["src", "README.md"],
              "evidence_root": "evidence", "scratch_root": "scratch",
              "retention_root": "retained"}
    workspace = tmp_path / "fg"
    (workspace / "scratch/s1").mkdir(parents=True)
    (workspace / "scratch/s1/plan.md").write_text("temporary plan")
    (workspace / "scratch/other.md").write_text("not this session's")
    owned = sha((workspace / "scratch/s1/plan.md").read_bytes())

    def record(entries: list[dict]) -> dict:
        body = {"format": ef.ADMISSION, "session_id": "s1", "task_id": "t",
                "source_sha256": "a" * 64, "profile_sha256": "b" * 64,
                "entries": entries}
        return ef.validate_admission(ef.with_integrity(body), session_id="s1",
                                     task_id="t", source_sha256="a" * 64,
                                     profile_sha256="b" * 64)

    def entry(path: str, digest: str | None, kind: str = "deliverable",
              disposition: str = "admit") -> dict:
        return {"path": path, "sha256": digest, "class": kind,
                "authority_ref": "packet p", "disposition": disposition,
                "reason": "requested"}

    delta = {"src/a.py": "1" * 64, "README.md": "2" * 64}
    findings, removals = ef.admit_delta(
        record([entry("src/a.py", "1" * 64), entry("README.md", "2" * 64),
                entry("scratch/s1/plan.md", owned, "scratch", "remove_requested"),
                entry("retained/notes.md", None, "retained_context", "retain")]),
        delta, policy, session_id="s1", workspace=workspace)
    assert findings == [] and [row["path"] for row in removals] == [
        "scratch/s1/plan.md"]
    # Unlisted product change, transcript copied into product, foreign scratch.
    findings, removals = ef.admit_delta(
        record([entry("src/a.py", "1" * 64),
                entry("src/transcript.md", "3" * 64, "evidence"),
                entry("scratch/other.md", None, "scratch", "remove_requested")]),
        dict(delta, **{"src/transcript.md": "3" * 64}), policy, session_id="s1",
        workspace=workspace)
    assert "unlisted_product_change:README.md" in findings
    assert "artifact_outside_class_root:src/transcript.md" in findings
    assert "removal_not_owned_scratch:scratch/other.md" in findings
    assert "unlisted_product_change:src/transcript.md" in findings
    assert removals == []
    assert (workspace / "scratch/other.md").exists()
    findings, _ = ef.admit_delta(record([entry("src/a.py", "9" * 64),
                                         entry("README.md", "2" * 64)]),
                                 delta, policy, session_id="s1", workspace=workspace)
    assert findings == ["admission_digest_mismatch:src/a.py"]
    with pytest.raises(ef.EngineeringError, match="artifact_admission_identity"):
        ef.validate_admission(ef.with_integrity({
            "format": ef.ADMISSION, "session_id": "other", "task_id": "t",
            "source_sha256": "a" * 64, "profile_sha256": "b" * 64, "entries": []}),
            session_id="s1", task_id="t", source_sha256="a" * 64,
            profile_sha256="b" * 64)


# --------------------------------------------------------------- bindings


def test_engineering_review_binding_facts(tmp_path: Path) -> None:
    graph = json.loads((LAD / "workflow.json").read_bytes())
    raw = policy_bytes()
    ref = {"path": "check-path-policy.json", "sha256": sha(raw)}
    profile = ef.load_profile(cc.encode(profile_record(
        initial_history(tmp_path), ref, [],
        review_bindings={"builder": {"binding_ref": "claude",
                                     "configured_family": "anthropic"}},
    )))
    facts = ef.binding_facts(graph, profile, session_id="s", occurrence_id="o",
                             role="builder", graph_revision="r")
    assert facts["configured_family"] == "anthropic"
    assert facts["reported_model"] is None and facts["reported_effort"] is None
    assert facts["requested_model"] is None  # Stock profile inherits native defaults.
    ef.validate_binding_facts(facts)
    checker = ef.binding_facts(graph, profile, session_id="s", occurrence_id="o2",
                               role="checker", graph_revision="r")
    assert checker["configured_family"] is None  # Inherited: unknown, not inferred.
    # Different-family policy needs explicit known distinct declared families.
    for bindings, code in (
        ({"builder": {"binding_ref": "claude", "configured_family": "a"}},
         "configured_family_unknown"),
        ({"builder": {"binding_ref": "claude", "configured_family": "a"},
          "checker": {"binding_ref": "claude", "configured_family": "a"},
          "doublechecker": {"binding_ref": "claude", "configured_family": "b"}},
         "configured_family_not_different"),
        ({"builder": {"binding_ref": "command", "configured_family": "a"}},
         "review_binding_mismatch"),
    ):
        policy = "different_for_implementation_review"
        selected = ef.load_profile(cc.encode(profile_record(
            initial_history(tmp_path / code), ref, [], review_bindings=bindings,
            family_policy=policy,
        )))
        with pytest.raises(ef.EngineeringError, match=code):
            ef._validate_review_bindings(selected, graph)
    two = json.loads((LAD / "workflow.json").read_bytes())
    two["bindings"]["codex"] = dict(two["bindings"]["claude"], adapter="codex_exec",
                                    contract="millrace-codex-exec/v1")
    for role in ("checker", "doublechecker"):
        two["planes"]["foreground"]["nodes"][role]["invoke"]["binding"] = "codex"
    distinct = ef.load_profile(cc.encode(profile_record(
        initial_history(tmp_path / "distinct"), ref, [],
        family_policy="different_for_implementation_review",
        review_bindings={
            "builder": {"binding_ref": "claude", "configured_family": "anthropic"},
            "checker": {"binding_ref": "codex", "configured_family": "openai"},
            "doublechecker": {"binding_ref": "codex", "configured_family": "openai"},
        },
    )))
    ef._validate_review_bindings(distinct, two)
    reported = ef.binding_facts(two, distinct, session_id="s", occurrence_id="o3",
                                role="checker", graph_revision="r")
    assert reported["adapter_contract"] == "millrace-codex-exec/v1"
    assert reported["reported_model"] is None  # Codex reports no model: honest null.


# -------------------------------------------------------------- history


def accepted(task: str, criteria: list[dict], raw: bytes = b"{}\n") -> dict:
    pinned = {"path": "records/" + sha(raw), "sha256": sha(raw)}
    return {
        "task_id": task, "session_id": "s-" + task, "request_ref": pinned,
        "criteria_ref": pinned, "acceptance_ref": pinned,
        "candidate_source_ref": pinned, "criteria": criteria,
        "source_ref": {"target_id": "repo", "git_base": None, "index_sha256": None,
                       "scoped_files_sha256": "5" * 64, "outside_sha256": "6" * 64},
        "records": {sha(raw): raw},
    }


def criterion(identity: str, supersedes: list[str] | None = None) -> dict:
    return {"id": identity, "text": identity + " holds",
            "request_quote": identity + " holds", "supersedes": supersedes or []}


def test_cumulative_requirements_across_independent_tasks(tmp_path: Path) -> None:
    home = tmp_path / "history"
    pin = initial_history(home)
    assert ef.initialize_history(home, pin) == "initialized"
    first = ef.publish_requirements(home, pin, accepted("A", [criterion("alpha")]))
    second = ef.publish_requirements(
        home, {"history_id": "repo", "sha256": first["sha256"]},
        accepted("B", [criterion("beta")]),
    )
    snapshot, _ = ef.read_snapshot(home, second["path"], second["sha256"])
    assert [row["id"] for row in ef.active_requirements(snapshot)] == [
        "A:alpha", "B:beta"]
    # Explicit supersession marks the old requirement; silent deletion is impossible.
    third = ef.publish_requirements(
        home, {"history_id": "repo", "sha256": second["sha256"]},
        accepted("C", [criterion("alpha2", ["A:alpha"])]),
    )
    snapshot, raw = ef.read_snapshot(home, third["path"], third["sha256"])
    rows = {row["id"]: row for row in snapshot["requirements"]}
    assert rows["A:alpha"]["status"] == "superseded"
    assert rows["C:alpha2"]["supersedes"] == ["A:alpha"]
    forged = json.loads(raw)
    forged["requirements"] = [row for row in forged["requirements"]
                              if row["id"] != "B:beta"]
    with pytest.raises(ef.EngineeringError, match="requirements_snapshot_integrity"):
        ef.load_requirements(ef.record_bytes(forged))
    orphan = ef.with_integrity(dict(forged, requirements=[
        dict(rows["B:beta"], origin_task_id="Z", id="Z:beta")]))
    with pytest.raises(ef.EngineeringError,
                       match="requirement_without_accepted_origin"):
        ef.load_requirements(ef.record_bytes(orphan))
    silent = ef.with_integrity(dict(json.loads(raw), requirements=[
        dict(row, status="superseded") if row["id"] == "B:beta" else row
        for row in json.loads(raw)["requirements"]]))
    with pytest.raises(ef.EngineeringError):
        ef.load_requirements(ef.record_bytes(silent))
    with pytest.raises(ef.EngineeringError,
                       match="supersession_without_active_requirement"):
        ef.publish_requirements(
            home, {"history_id": "repo", "sha256": third["sha256"]},
            accepted("D", [criterion("delta", ["A:alpha"])]),
        )


def test_engineering_history_cas_and_crash(tmp_path: Path) -> None:
    home = tmp_path / "history"
    pin = initial_history(home)
    ef.initialize_history(home, pin)
    task = accepted("A", [criterion("alpha")])
    # Crash after the immutable snapshot but before the index CAS: replay reuses it.
    prior, prior_raw = ef.read_snapshot(home, pin["path"], pin["sha256"])
    successor = ef.successor(prior, prior_raw, pin["path"], task)
    raw = ef.record_bytes(successor)
    path = ef.snapshot_relative("repo", 2, sha(raw))
    (home / path).write_bytes(raw)
    published = ef.publish_requirements(home, pin, task)
    assert published == {"status": "published", "path": path, "sha256": sha(raw),
                         "sequence": 2}
    assert ef.publish_requirements(home, pin, task)["status"] == "replayed"
    # A competing task from the same start never merges by order: stale.
    with pytest.raises(ef.EngineeringError, match="engineering_history_stale"):
        ef.publish_requirements(home, pin, accepted("B", [criterion("beta")]))
    with pytest.raises(ef.EngineeringError, match="task_already_published"):
        ef.publish_requirements(home, {"history_id": "repo", "sha256": sha(raw)}, task)
    # A busy publisher refuses rather than waiting; a broken chain refuses.
    import fcntl

    with open(home / "repo/.lock", "rb+") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ef.EngineeringError, match="engineering_history_busy"):
            ef.publish_requirements(home, {"history_id": "repo", "sha256": sha(raw)},
                                    accepted("B", [criterion("beta")]))
    (home / path).write_bytes(raw + b" ")
    with pytest.raises(ef.EngineeringError, match="requirements_snapshot_changed"):
        ef.chain(home, "repo")
    (home / path).write_bytes(raw)
    # Index initialization happens once; a missing index for sequence >1 refuses.
    other = tmp_path / "other"
    other_pin = initial_history(other)
    (other / "repo/index.json").unlink(missing_ok=True)
    assert ef.initialize_history(other, other_pin) == "initialized"
    assert ef.initialize_history(other, other_pin) == "head"


# ---------------------------------------------------- guards and products


def test_engineering_direct_helper_calls_refuse_route_authority(tmp_path: Path) -> None:
    project, _, _ = enabled_copy(tmp_path)
    to_builder(project)
    # Role preparation cannot bypass the declared baseline phase.
    phase = command(project, "engineering_feedback.py", "phase", "baseline")
    assert phase["outcome"].startswith("RUN_")
    assert command(project, "prepare_role.py", "builder")["outcome"] == "REFUSED"
    encoded = phase["outcome"][4:]
    for operation in ("check", "accept"):
        direct = command(project, "engineering_feedback.py", operation, "baseline",
                         encoded)
        assert direct["outcome"] == "REFUSED"
        assert "engineering_route_authority_missing" in direct["summary"]
    assert estate(project)["open"]["checks"][-1]["receipt_ref"] is None
    # A check invoked from a Core-shaped root records only observed facts; a guard
    # in that same root cannot accept it.
    script = invocation(project, tmp_path)
    assert run_at(project, script, "check", "baseline", encoded)["outcome"] == "FAIL"
    same = run_at(project, script, "accept", "baseline", encoded)
    assert same["outcome"] == "REFUSED" and "route_authority" in same["summary"]
    row = estate(project)["open"]["checks"][-1]
    receipt = record_at(project, row["receipt_ref"])
    layer = record_at(project, row["layers"][-1])
    assert layer["status"] == "observed" and layer["completion_ref"] is None
    assert receipt["invocation_root"] == str(script.parents[2])
    accepted_once = run_at(project, invocation(project, tmp_path), "accept",
                           "baseline", encoded)
    assert accepted_once["outcome"] == "ACCEPTED"
    row = estate(project)["open"]["checks"][-1]
    acceptance = record_at(project, row["acceptance_ref"])
    assert acceptance["acceptance"]["kind"] == "selected_stopped_route"
    assert acceptance["acceptance"]["from_node"] == "eb_" + encoded
    assert acceptance["acceptance"]["to_node"] == "ab_" + encoded
    assert acceptance["process_receipt_ref"] == row["receipt_ref"]
    guard = record_at(project, acceptance["acceptance"]["guard_ref"])
    assert guard["producer_invocation_root"] != guard["guard_invocation_root"]
    cc.validate_completion_acceptance(acceptance, "selected_stopped_route")
    # A raw observed layer never substitutes for accepted completion evidence.
    with pytest.raises(cc.CheckContractError):
        forged = dict(acceptance,
                      process_receipt_ref=acceptance["acceptance"]["guard_ref"])
        cc.validate_completion_acceptance(forged, "selected_stopped_route")
    # Exact replay neither reruns the check nor duplicates acceptance.
    assert run_at(project, invocation(project, tmp_path), "accept", "baseline",
                  encoded)["outcome"] == "ACCEPTED"
    assert estate(project)["open"]["checks"][-1]["acceptance_ref"] == row[
        "acceptance_ref"]


def test_engineering_path_policy_and_owned_products_across_jobs(tmp_path: Path) -> None:
    project, _, _ = enabled_copy(tmp_path)
    to_builder(project)
    target = project / "foreground/target"
    (target / "__pycache__").mkdir(parents=True)
    (target / "__pycache__/old.pyc").write_bytes(b"cache")
    report = node_of("report")
    first = command(project, "engineering_feedback.py", "phase", "baseline")
    assert first["outcome"] == "RUN_" + node_of("answer-unit")
    run_at(project, invocation(project, tmp_path), "check", "baseline",
           node_of("answer-unit"))
    run_at(project, invocation(project, tmp_path), "accept", "baseline",
           node_of("answer-unit"))
    assert command(project, "engineering_feedback.py", "phase", "baseline")[
        "outcome"] == "RUN_" + report
    assert run_at(project, invocation(project, tmp_path), "check", "baseline",
                  report)["outcome"] == "PASS"
    assert (target / "reports/r.txt").read_text() == "ok"
    # Diverged product between observation and guard holds; nothing is deleted.
    (target / "reports/r.txt").write_text("changed")
    held = run_at(project, invocation(project, tmp_path), "accept", "baseline", report)
    assert held["outcome"] == "RECONCILE"
    assert (target / "reports/r.txt").read_text() == "changed"
    (target / "reports/r.txt").write_text("ok")
    assert run_at(project, invocation(project, tmp_path), "accept", "baseline",
                  report)["outcome"] == "ACCEPTED"
    assert command(project, "engineering_feedback.py", "phase", "baseline")[
        "outcome"] == "RUN_" + node_of("docs-only")
    assert run_at(project, invocation(project, tmp_path), "check", "baseline",
                  node_of("docs-only"))["outcome"] == "NOT_SELECTED"
    run_at(project, invocation(project, tmp_path), "accept", "baseline",
           node_of("docs-only"))
    done = command(project, "engineering_feedback.py", "phase", "baseline")
    assert done["outcome"] == "READY"
    # Owned products reversed; opaque environment preserved in place.
    assert not (target / "reports").exists()
    assert (target / "__pycache__/old.pyc").read_bytes() == b"cache"
    state = estate(project)
    phase = record_at(project, state["baseline"])
    layers = [record_at(project, ref)["status"]
              for ref in phase["checks"][1]["layers"]]
    assert layers == ["reserved", "observed", "accepted", "restored"]
    environment = record_at(project, phase["environment_start_ref"])
    assert [row["prefix"] for row in environment["entries"]] == ["__pycache__"]
    # Source identity ignores declared environment/product paths.
    assert "__pycache__/old.pyc" not in record_at(project, phase["source_ref"])["files"]
    # The next native role prepares normally (the second job needs no cleanup).
    assert command(project, "prepare_role.py", "builder")["outcome"] == "READY"
    # A non-check writer changing a product path holds as undeclared output.
    (target / "reports").mkdir()
    (target / "reports/stray.txt").write_text("builder output")
    (target / "answer.py").write_text("def answer():\n    return 42\n")
    peer = command(project, "lad_peer.py", "builder", "clean")
    accepted = command(project, "accept_role.py", "builder", peer["outcome"])
    assert accepted["outcome"] == "REFUSED" and "undeclared_output" in accepted[
        "summary"]
    assert (target / "reports/stray.txt").exists()


def test_foreign_product_and_unknown_closure_hold_with_lawful_recovery(
    tmp_path: Path,
) -> None:
    project, _, _ = enabled_copy(tmp_path)
    to_builder(project)
    target = project / "foreground/target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "reports").mkdir()
    (target / "reports/user.txt").write_text("user owned")
    command(project, "engineering_feedback.py", "phase", "baseline")
    run_at(project, invocation(project, tmp_path), "check", "baseline",
           node_of("answer-unit"))
    run_at(project, invocation(project, tmp_path), "accept", "baseline",
           node_of("answer-unit"))
    command(project, "engineering_feedback.py", "phase", "baseline")
    foreign = run_at(project, invocation(project, tmp_path), "check", "baseline",
                     node_of("report"))
    assert foreign["outcome"] == "UNAVAILABLE"
    assert (target / "reports/user.txt").read_text() == "user owned"
    # Unknown closure: a crafted unknown layer for the finished answer-unit run
    # recovers only after the recorded process group is observed absent.
    state = estate(project)
    first = state["open"]["checks"][0]
    receipt = record_at(project, first["receipt_ref"])
    assert receipt["process"]["cleanup_confirmed"] is True
    assert ef.group_absent(receipt["process"]["pid"])
    # A missing receipt with a recorded start stays held; no rerun or deletion.
    row = state["open"]["checks"][-1]
    run_at(project, invocation(project, tmp_path), "accept", "baseline",
           node_of("report"))
    command(project, "engineering_feedback.py", "phase", "baseline")
    run_at(project, invocation(project, tmp_path), "check", "baseline",
           node_of("docs-only"))
    run_at(project, invocation(project, tmp_path), "accept", "baseline",
           node_of("docs-only"))
    held = command(project, "engineering_feedback.py", "phase", "baseline")
    assert held["outcome"] == "HUMAN_REQUIRED"
    assert (target / "reports/user.txt").read_text() == "user owned"
    del row
    # Resume after the operator removes the foreign file opens a new attempt.
    (target / "reports/user.txt").unlink()
    (target / "reports").rmdir()
    again = command(project, "engineering_feedback.py", "phase", "baseline")
    assert again["outcome"] == "RUN_" + node_of("answer-unit")
    assert estate(project)["open"]["attempt"] == 2
    # Missing receipt after a recorded start: unknown, resumable hold, no rerun.
    row = estate(project)["open"]["checks"][-1]
    run_dir = project / "foreground/lad-state/engineering/runs" / row["input_id"]
    run_dir.mkdir(parents=True)
    probe = subprocess.Popen([PYTHON, "-c", "pass"])
    probe.wait()
    (run_dir / "start.json").write_bytes(ef.record_bytes({
        "format": ef.START, "input_id": row["input_id"], "pid": probe.pid,
        "invocation_root": "/elsewhere", "started_at": ef.utc_now()}))
    assert run_at(project, invocation(project, tmp_path), "check", "baseline",
                  node_of("answer-unit"))["outcome"] == "UNKNOWN"
    for _ in range(2):
        unknown = run_at(project, invocation(project, tmp_path), "accept",
                         "baseline", node_of("answer-unit"))
        assert unknown["outcome"] == "RECONCILE"
    layers = [record_at(project, ref)["status"] for ref in estate(project)["open"][
        "checks"][-1]["layers"]]
    assert layers == ["reserved", "unknown"]


def test_unknown_layer_reobserves_only_after_proven_closure(tmp_path: Path) -> None:
    project, _, _ = enabled_copy(tmp_path)
    to_builder(project)
    command(project, "engineering_feedback.py", "phase", "baseline")
    encoded = node_of("answer-unit")
    run_at(project, invocation(project, tmp_path), "check", "baseline", encoded)
    path = project / "foreground/lad-state/engineering/state.json"
    state = json.loads(path.read_bytes())
    row = state["open"]["checks"][-1]
    observed = record_at(project, row["layers"][-1])
    reserved_ref = row["layers"][0]
    unknown = dict(observed, status="unknown", owned_paths=[], after_ref=None,
                   previous_ref=reserved_ref)
    raw = ef.record_bytes(unknown)
    relative = f"lad-state/engineering/records/{sha(raw)}.json"
    (project / "foreground" / relative).write_bytes(raw)
    row["layers"] = [reserved_ref, {"path": relative, "sha256": sha(raw)}]
    row["status"] = "unknown"
    receipt = record_at(project, row["receipt_ref"])
    assert ef.group_absent(receipt["process"]["pid"])
    path.write_bytes(ef.record_bytes(ef.with_integrity(
        {k: v for k, v in state.items() if k != "integrity_sha256"})))
    assert run_at(project, invocation(project, tmp_path), "accept", "baseline",
                  encoded)["outcome"] == "ACCEPTED"
    layers = [record_at(project, ref)["status"]
              for ref in estate(project)["open"]["checks"][-1]["layers"]]
    assert layers == ["reserved", "unknown", "observed", "accepted"]
    # Tampered engineering state integrity refuses before any guard work.
    state = json.loads(path.read_bytes())
    state["open"]["cursor"] = "other"
    path.write_bytes(ef.record_bytes(state))
    assert command(project, "engineering_feedback.py", "phase", "baseline")[
        "outcome"] == "REFUSED"


def test_changed_generated_graph_or_capture_refuses(tmp_path: Path) -> None:
    project, _, _ = enabled_copy(tmp_path)
    graph_path = project / "workflow.json"
    graph = json.loads(graph_path.read_bytes())
    names = ef.node_names("answer-unit")
    graph["planes"]["foreground"]["nodes"][names["baseline_node"]]["on"]["PASS"] = {
        "goto": "engineering_baseline"}
    graph_path.write_text(json.dumps(graph))
    bootstrap_failed = command(project, "init_session.py", "--project-root", "..")
    assert bootstrap_failed["outcome"] == "RECONCILE"
    assert "engineering_graph_changed" in bootstrap_failed["summary"]


# ----------------------------------------------------------- real graphs


def finished(project: Path, home: Path, scenario: str = "clean") -> tuple[str, dict]:
    (project / "request.md").write_text("Original request: answer() returns 42.\n")
    instance, view = started(project, home, scenario)
    return instance, view


def test_real_graph_baseline_candidate_admit_finalize_and_publish(
    tmp_path: Path,
) -> None:
    out = tmp_path / "export-out"
    project, history, _ = enabled_copy(
        tmp_path, evaluation_export={"enabled": True, "destination_ref": str(out)},
    )
    home = tmp_path / "state"
    instance, view = finished(project, home)
    assert view["status"] == "succeeded", view
    nodes = [row["node"] for row in view["invocations"]]
    names = {check: ef.node_names(check) for check in ("answer-unit", "report",
                                                      "docs-only")}
    for check, entry in names.items():
        for key in ("baseline_node", "baseline_accept_node", "candidate_node",
                    "candidate_accept_node"):
            assert entry[key] in nodes, (check, key)
        # Each generated check node is followed only by its distinct guard.
        for index, node in enumerate(nodes):
            if node == entry["baseline_node"]:
                assert nodes[index + 1] == entry["baseline_accept_node"]
            if node == entry["candidate_node"]:
                assert nodes[index + 1] == entry["candidate_accept_node"]
    order = [n for n in nodes if n in {"engineering_baseline", "builder",
                                       "engineering_candidate", "checker",
                                       "engineering_admit", "arbiter",
                                       "engineering_finalize", "emit_learning_request"}]
    assert order.index("engineering_baseline") < order.index("builder")
    assert order.index("builder") < order.index("engineering_candidate")
    assert order.index("engineering_candidate") < order.index("checker")
    assert order.index("engineering_admit") < order.index("arbiter")
    assert order.index("engineering_finalize") < order.index("emit_learning_request")
    state = estate(project)
    baseline = record_at(project, state["baseline"])
    candidate = record_at(project, state["latest_candidate"])
    statuses = {row["check_id"]: record_at(project, row["evidence_ref"])["status"]
                for row in baseline["checks"]}
    assert statuses == {"answer-unit": "fail", "report": "pass",
                        "docs-only": "not_selected"}
    classes = {row["check_id"]: row["classification"]
               for row in candidate["comparisons"]}
    assert classes == {"answer-unit": "improved", "report": "unchanged_pass",
                       "docs-only": "not_selected"}
    assert not (project / "foreground/target/reports").exists()
    evidence = record_at(project, candidate["checks"][0]["evidence_ref"])
    assert evidence["timeout_seconds"] == 1800 and evidence["exit_code"] == 0
    assert record_at(project, candidate["checks"][0]["acceptance_ref"])[
        "acceptance"]["kind"] == "selected_stopped_route"
    # Exact acceptance projection pins both verdicts, admission and source.
    projection = json.loads(
        (project / "foreground/lad-state/engineering/acceptance.json").read_bytes())
    ef.validate_acceptance(projection)
    for key in ("original_verdict_ref", "retained_verdict_ref",
                "artifact_admission_ref", "candidate_source_ref"):
        assert sha((project / projection[key]["path"]).read_bytes()) == projection[
            key]["sha256"]
    # Publication only through public stopped-success facts; replay reuses digest.
    pin = json.loads((project / ef.CAPTURE_PATH).read_bytes())["history"]
    run_id = view["id"]
    version = control.inspect_run(instance, run_id, state_home=home)["state_version"]
    with pytest.raises(ef.EngineeringError,
                       match="engineering_snapshot_not_captured"):
        ef.publish_task(project, history, "f" * 64, state_home=home,
                        instance_id=instance, run_id=run_id,
                        expected_state_version=version)
    with pytest.raises(ef.EngineeringError):
        ef.publish_task(project, history, pin["sha256"], state_home=home,
                        instance_id=instance, run_id=run_id,
                        expected_state_version=version + 1)
    published = ef.publish_task(project, history, pin["sha256"], state_home=home,
                                instance_id=instance, run_id=run_id,
                                expected_state_version=version)
    assert published["status"] == "published" and published["sequence"] == 2
    again = ef.publish_task(project, history, pin["sha256"], state_home=home,
                            instance_id=instance, run_id=run_id,
                            expected_state_version=version)
    assert again["status"] == "replayed" and again["sha256"] == published["sha256"]
    snapshot, _ = ef.read_snapshot(history, published["path"], published["sha256"])
    session = ledger(project)["session_id"]
    assert [row["id"] for row in snapshot["requirements"]] == [session + ":answer"]
    # Explicit read-only export of accepted records.
    export_project, export_history, _ = enabled_copy(
        tmp_path / "exp", evaluation_export={"enabled": True,
                                             "destination_ref": str(tmp_path / "out")},
    )
    with pytest.raises(ef.EngineeringError, match="history_home_mismatch"):
        ef.export_evaluation(export_project, history, published["sha256"],
                             tmp_path / "out")
    del export_history
    second, history_b, _ = enabled_copy(
        tmp_path, name="second", pin={"history_id": "repo", "sequence": 2,
                                      "path": published["path"],
                                      "sha256": published["sha256"]},
        seed=project / "foreground/target",
    )
    assert history_b == history
    instance_b, view_b = finished(second, tmp_path / "state-b", "repair")
    assert view_b["status"] == "succeeded", view_b
    state_b = estate(second)
    first_candidate = record_at(second, {
        "path": state_b["phases"][1]["ref"]["path"],
        "sha256": state_b["phases"][1]["ref"]["sha256"]})
    assert {row["check_id"]: row["classification"]
            for row in first_candidate["comparisons"]}["answer-unit"] == "regression"
    assert first_candidate["outcome"] == "FINDINGS"
    final = record_at(second, state_b["latest_candidate"])
    assert final["outcome"] == "REPAIR_CHECKED"
    retained = json.loads((second / json.loads(
        (second / "foreground/lad-state/engineering/acceptance.json").read_bytes()
    )["retained_verdict_ref"]["path"]).read_bytes())
    assert set(retained["verdict"]) == {session + ":answer"}
    version_b = control.inspect_run(instance_b, view_b["id"],
                                    state_home=tmp_path / "state-b")["state_version"]
    third = ef.publish_task(second, history, published["sha256"],
                            state_home=tmp_path / "state-b", instance_id=instance_b,
                            run_id=view_b["id"], expected_state_version=version_b)
    snapshot, _ = ef.read_snapshot(history, third["path"], third["sha256"])
    assert [row["id"] for row in ef.active_requirements(snapshot)] == [
        session + ":answer", ledger(second)["session_id"] + ":answer"]
    # The first task's later publication against its old pin is now stale.
    with pytest.raises(ef.EngineeringError, match="engineering_history_stale"):
        ef.publish_requirements(history, {"history_id": "repo",
                                          "sha256": pin["sha256"]},
                                dict(ef.accepted_task(project), task_id="other"))
    # Read-only export of genuine accepted records with the selected hooks.
    before = {p: p.read_bytes() for p in history.rglob("*") if p.is_file()}
    manifest = ef.export_evaluation(project, history, third["sha256"], out)
    assert {p: p.read_bytes() for p in history.rglob("*") if p.is_file()} == before
    report = json.loads((out / "report.json").read_bytes())
    assert [task["session_id"] for task in report["tasks"]] == [
        session, ledger(second)["session_id"]]
    for task in report["tasks"]:
        roles = {row["role"] for row in task["bindings"]}
        assert {"builder", "checker", "updater", "arbiter"} <= roles
        for row in task["bindings"]:
            # Real captured facts (inert peers ran through the command binding);
            # unreported values stay null, never inferred.
            assert row["binding_id"] == "command"
            assert row["adapter_contract"] == "millrace-command/v1"
            assert row["requested_effort"] is None and row["reported_model"] is None
        checks = {row["check_id"]: row for row in task["checks"]}
        assert set(checks) == {"answer-unit", "report", "docs-only"}
        assert checks["answer-unit"]["timeout_seconds"] == 1800
        assert checks["answer-unit"]["candidate"]["status"] == "pass"
        assert isinstance(checks["answer-unit"]["candidate"]["elapsed_seconds"], float)
        assert checks["docs-only"]["baseline"]["elapsed_seconds"] is None
        assert set(task["source_conditions"]) == {"baseline", "candidate"}
        assert task["cost"] is None and task["tokens"] is None
    first, latter = report["tasks"]
    assert {row["check_id"]: row["classification"] for row in first["comparisons"]}[
        "answer-unit"] == "improved"
    # Every exported ref resolves to exported bytes; origins map content digests
    # back to the original copy-relative paths (self-consistent, no dangling ref).
    for row in manifest["output_refs"]:
        assert sha((out / row["path"]).read_bytes()) == row["sha256"]
    projection = first["acceptance"]
    for key in ("original_verdict_ref", "retained_verdict_ref",
                "artifact_admission_ref", "candidate_source_ref"):
        digest = projection[key]["sha256"]
        assert first["ref_map"][digest] == projection[key]["path"]
        assert sha((out / "records" / digest).read_bytes()) == digest
    assert {row["value"] for row in manifest["omissions"]} >= {
        "reported_model", "cost", "tokens", "task_elapsed_seconds"}
    del latter
    # The selected provenance of every accepted check resolves inside the export
    # by content digest, and its links agree: tagged acceptance -> reservation,
    # raw process receipt and guard -> evidence, definition, policy, source and
    # product layers. Walked independently of the exporter's own code.
    def exported(value: dict) -> dict:
        raw = (out / "records" / value["sha256"]).read_bytes()
        assert sha(raw) == value["sha256"]
        return json.loads(raw)

    unexported = {(row["owner"], row["field"]): row for row in manifest["omissions"]
                  if row["value"] == "unexported_ref"}
    for task in report["tasks"]:
        for check in task["checks"]:
            for kind in ("baseline", "candidate"):
                links = check[kind]["links"]
                tagged_record = exported(links["acceptance_ref"])
                tagged = tagged_record["acceptance"]
                assert tagged["kind"] == "selected_stopped_route"
                assert (tagged_record["subject_ref"]["sha256"]
                        == tagged["reservation_ref"]["sha256"]
                        == links["reservation_ref"]["sha256"])
                assert (tagged_record["process_receipt_ref"]["sha256"]
                        == links["process_receipt_ref"]["sha256"])
                assert tagged["guard_ref"]["sha256"] == links["guard_ref"]["sha256"]
                guard = exported(links["guard_ref"])
                receipt = exported(links["process_receipt_ref"])
                reservation = exported(links["reservation_ref"])
                record = exported(links["evidence_ref"])
                assert guard["reservation_ref"] == tagged["reservation_ref"]
                assert guard["process_receipt_ref"] == tagged_record[
                    "process_receipt_ref"]
                assert receipt["evidence_ref"]["sha256"] == links["evidence_ref"][
                    "sha256"]
                assert receipt["input_id"] == reservation["input_id"] == guard[
                    "input_id"]
                assert exported(reservation["definition_ref"])["id"] == check[
                    "check_id"]
                for linked in (guard["observed_layer_ref"], receipt["layer_ref"],
                               reservation["source_ref"], reservation["profile_ref"],
                               reservation["path_policy_ref"], record["source_ref"],
                               record["path_policy_ref"],
                               *record["product_layer_refs"]):
                    exported(linked)
                for layer_ref in record["product_layer_refs"]:
                    layer = exported(layer_ref)
                    for key in ("before_ref", "after_ref", "previous_ref",
                                "completion_ref"):
                        if layer[key] is not None:
                            exported(layer[key])
                assert record["status"] == check[kind]["status"]
                for key in ("stdout_ref", "stderr_ref"):
                    if record[key] is not None:
                        owner = "records/" + links["evidence_ref"]["sha256"]
                        assert unexported[(owner, "." + key)]["reason"] == (
                            "raw_check_output")
    # Every ref inside an exported record resolves here or is a listed omission
    # with its origin; no declared selected link is ever among the omissions.
    present = {path.name for path in (out / "records").iterdir()}

    def refs(value: object, trail: str = ""):
        if isinstance(value, dict):
            if set(value) == {"path", "sha256"}:
                yield trail, value
                return
            for key, child in value.items():
                yield from refs(child, f"{trail}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from refs(child, f"{trail}[{index}]")

    for path in [out / "snapshot.json", *(out / "records").iterdir()]:
        try:
            value = json.loads(path.read_bytes())
        except ValueError:
            continue
        owner = "snapshot.json" if path.name == "snapshot.json" else (
            "records/" + path.name)
        declared = {route[0] for route in ef._LINKS.get(
            value.get("format") if isinstance(value, dict) else "", ())}
        for trail, pinned in refs(value):
            if pinned["sha256"] in present:
                continue
            row = unexported[(owner, trail)]
            assert row["ref"] == pinned and row["owner_origin"] is not None
            assert re.match(r"\.([^.\[]*)", trail).group(1) not in declared, row
    assert {row["reason"] for row in unexported.values()} <= {
        "raw_check_output", "graph_asset", "role_artifact", "native_evidence",
        "earlier_history_snapshot", "not_selected"}
    # Exact original bytes: each gathered digest's origin in its own copy.
    for task, copy_root in zip(report["tasks"], (project, second), strict=True):
        for digest, origin in task["ref_map"].items():
            assert sha((copy_root / origin).read_bytes()) == digest
    # Corruption on genuine accepted records refuses verification: a
    # contradictory pass at an unexpected exit, and a foreign receipt link.
    task_a = ef.accepted_task(project)
    store = dict(task_a["records"])
    bundle_a = json.loads(store[task_a["bundle_ref"]["sha256"]])
    rows_a = {row["definition"]["id"]: row for row in json.loads(
        store[bundle_a["resolved_checks_ref"]["sha256"]])["checks"]}
    by_phase = {item["phase"]: {row["check_id"]: row for row in item["checks"]}
                for item in bundle_a["phases"]}
    passed = by_phase["candidate"]["answer-unit"]
    identity = {"session_id": session, "task_id": bundle_a["task_id"],
                "phase": "candidate", "check_id": "answer-unit",
                "profile_sha256": bundle_a["projected_refs"]["profile_ref"]["sha256"]}
    good, _ = ef._check_links(store.__getitem__, passed, rows_a["answer-unit"],
                              identity)
    assert good["status"] == "pass" and good["exit_code"] == 0
    forged = ef.record_bytes(ef.with_integrity(dict(good, exit_code=7)))
    store[sha(forged)] = forged
    with pytest.raises(ef.EngineeringError, match="check_evidence_status_mismatch"):
        ef._check_links(store.__getitem__, dict(passed, evidence_ref={
            "path": "records/" + sha(forged), "sha256": sha(forged)}),
            rows_a["answer-unit"], identity)
    with pytest.raises(ef.EngineeringError, match="engineering_provenance_mismatch"):
        ef._check_links(store.__getitem__, dict(
            passed, process_receipt_ref=by_phase["baseline"]["answer-unit"][
                "process_receipt_ref"]), rows_a["answer-unit"], identity)
    # A changed gathered record in the copy refuses publication collection.
    guard_origin = first["ref_map"][passed["guard_ref"]["sha256"]]
    (project / guard_origin).write_bytes(b"{}\n")
    with pytest.raises(ef.EngineeringError, match="engineering_acceptance_changed"):
        ef.accepted_task(project)


def test_engineering_export_is_explicit_and_read_only(tmp_path: Path) -> None:
    out = tmp_path / "out"
    project, history, _ = enabled_copy(
        tmp_path, evaluation_export={"enabled": True, "destination_ref": str(out)},
    )
    pin = json.loads((project / ef.CAPTURE_PATH).read_bytes())["history"]
    ef.initialize_history(history, pin)
    task = accepted("A", [criterion("alpha")], ef.record_bytes({"format": "x"}))
    published = ef.publish_requirements(history, pin, task)
    before = {p: p.read_bytes() for p in history.rglob("*") if p.is_file()}
    with pytest.raises(ef.EngineeringError, match="engineering_export_destination"):
        ef.export_evaluation(project, history, published["sha256"], tmp_path / "x")
    with pytest.raises(ef.EngineeringError, match="requirements_snapshot_not_indexed"):
        ef.export_evaluation(project, history, "a" * 64, out)
    manifest = ef.export_evaluation(project, history, published["sha256"], out)
    assert manifest["format"] == ef.EXPORT
    report = json.loads((out / "report.json").read_bytes())
    assert report["tasks"][0]["cost"] is None and report["tasks"][0]["tokens"] is None
    assert report["tasks"][0]["bindings"] is None  # Honest: no bundle published.
    assert {row["value"] for row in manifest["omissions"]} == {
        "task_elapsed_seconds", "cost", "tokens", "evidence_bundle", "unexported_ref"}
    # The earlier snapshot is history, not exported: listed with its origin.
    [prior] = [row for row in manifest["omissions"] if row["value"] == "unexported_ref"]
    assert (prior["owner"], prior["field"], prior["reason"]) == (
        "snapshot.json", ".previous_ref", "earlier_history_snapshot")
    assert prior["ref"] == {"path": pin["path"], "sha256": pin["sha256"]}
    assert prior["owner_origin"] == published["path"]
    for row in manifest["output_refs"]:
        assert sha((out / row["path"]).read_bytes()) == row["sha256"]
    assert {p: p.read_bytes() for p in history.rglob("*") if p.is_file()} == before
    with pytest.raises(ef.EngineeringError,
                       match="engineering_export_output_not_empty"):
        ef.export_evaluation(project, history, published["sha256"], out)
    disabled, history_d, _ = enabled_copy(tmp_path / "d")
    with pytest.raises(ef.EngineeringError, match="engineering_export_not_enabled"):
        ef.export_evaluation(disabled, history_d, published["sha256"], out)


def test_engineering_cli_is_thin_dispatch(tmp_path: Path, monkeypatch) -> None:
    from millrace_plus.router import cli

    calls = []
    monkeypatch.setattr(ef, "capture", lambda *a, **k: calls.append(("init", a, k))
                        or {"status": "captured"})
    monkeypatch.setattr(ef, "publish_task", lambda *a, **k: calls.append(
        ("publish", a, k)) or {"status": "published"})
    assert cli._lad_main(["engineering-init", "--project-root", "p", "--profile", "f",
                          "--history-home", "h"]) == 0
    assert cli._lad_main([
        "engineering-publish", "--project-root", "p", "--history-home", "h",
        "--snapshot", "a" * 64, "--state-home", "s", "--instance", "i", "--run", "r",
        "--expected-state-version", "3",
    ]) == 0
    assert calls[1][2]["expected_state_version"] == 3
    assert calls[1][2]["service_home"] is None
    with pytest.raises(Exception):
        cli._lad_main(["engineering-publish", "--project-root", "p", "--history-home",
                       "h", "--snapshot", "a" * 64, "--state-home", "s", "--instance",
                       "i", "--run", "r", "--expected-state-version", "3",
                       "--task", "t"])


# ----------------------------------------------------------- repair-1 cases


def drive_phase(project: Path, tmp_path: Path, kind: str) -> list[dict]:
    """One phase through Core-shaped check/guard roots until it stops sequencing."""
    events = []
    while True:
        event = command(project, "engineering_feedback.py", "phase", kind)
        events.append(event)
        if not event["outcome"].startswith("RUN_"):
            return events
        encoded = event["outcome"][4:]
        events.append(run_at(project, invocation(project, tmp_path), "check", kind,
                             encoded))
        events.append(run_at(project, invocation(project, tmp_path), "accept", kind,
                             encoded))
        if events[-1]["outcome"] != "ACCEPTED":
            return events


@pytest.mark.parametrize("written, kind", [("rogue.txt", "outside"),
                                           ("answer.py", "files")])
def test_check_source_writes_never_receive_acceptance(
    tmp_path: Path, written: str, kind: str
) -> None:
    def checks(ref: dict) -> list:
        return [definition("writer", [PYTHON, "-c",
                                      "from pathlib import Path;"
                                      f"Path({written!r}).write_text('undeclared')"],
                           ref)]

    project, _, _ = enabled_copy(tmp_path, checks=checks)
    to_builder(project)
    target = project / "foreground/target"
    event = command(project, "engineering_feedback.py", "phase", "baseline")
    encoded = event["outcome"][4:]
    assert run_at(project, invocation(project, tmp_path), "check", "baseline",
                  encoded)["outcome"] == "UNKNOWN"
    row = estate(project)["open"]["checks"][-1]
    receipt = record_at(project, row["receipt_ref"])
    assert kind in receipt["source_drift"]["kinds"]
    for _ in range(2):  # Resumed guard keeps holding; nothing is accepted.
        held = run_at(project, invocation(project, tmp_path), "accept", "baseline",
                      encoded)
        assert held["outcome"] == "RECONCILE" and kind in held["summary"]
    row = estate(project)["open"]["checks"][-1]
    assert row["status"] != "accepted" and row["acceptance_ref"] is None
    assert (target / written).read_text() == "undeclared"  # Preserved, not deleted.
    assert command(project, "engineering_feedback.py", "phase", "baseline")[
        "outcome"] == "REFUSED"
    assert command(project, "prepare_role.py", "builder")["outcome"] == "REFUSED"


def test_outside_or_git_drift_after_checks_holds_replay_and_review(
    tmp_path: Path,
) -> None:
    def checks(ref: dict) -> list:
        return [definition("pass", [PYTHON, "-c", "pass"], ref)]

    project, _, _ = enabled_copy(tmp_path, checks=checks)
    target = project / "foreground/target"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
           "-C", str(target)]
    subprocess.run([*git, "init", "-q"], check=True)
    (target / "README.md").write_text("tracked outside the packet\n")
    subprocess.run([*git, "add", "README.md"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    to_builder(project)
    assert drive_phase(project, tmp_path, "baseline")[-1]["outcome"] == "READY"
    assert step(project, "builder") == "CHECKER"
    assert drive_phase(project, tmp_path, "candidate")[-1]["outcome"] == "CHECKED"
    checked = estate(project)["latest_candidate"]
    # Unexplained outside drift: exact replay and review preparation hold.
    (target / "unrelated-user.txt").write_text("drift")
    replay = command(project, "engineering_feedback.py", "phase", "candidate")
    assert replay["outcome"] == "RECONCILE" and "source_drift" in replay["summary"]
    prepared = command(project, "prepare_role.py", "checker")
    assert prepared["outcome"] == "REFUSED"
    assert "engineering_source_drift" in prepared["summary"]
    (target / "unrelated-user.txt").unlink()
    # Git base drift with identical files also holds.
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "moved"], check=True)
    assert command(project, "engineering_feedback.py", "phase", "candidate")[
        "outcome"] == "RECONCILE"
    subprocess.run([*git, "reset", "-q", "--soft", "HEAD~1"], check=True)
    # Exact authority again: replay reuses the same evidence; review proceeds.
    again = command(project, "engineering_feedback.py", "phase", "candidate")
    assert again["outcome"] == "CHECKED" and "not rerun" in again["summary"]
    assert estate(project)["latest_candidate"] == checked
    assert command(project, "prepare_role.py", "checker")["outcome"] == "READY"


def test_captured_binding_drift_refuses_before_dispatch(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(LAD, project)
    (project / "foreground/target").mkdir()
    history = tmp_path / "history"
    history.mkdir()
    pin = initial_history(history)
    raw = policy_bytes()
    (tmp_path / "check-path-policy.json").write_bytes(raw)
    ref = {"path": "check-path-policy.json", "sha256": sha(raw)}
    graph = json.loads((project / "workflow.json").read_bytes())
    graph["bindings"]["codex"] = dict(graph["bindings"]["claude"], adapter="codex_exec",
                                      contract="millrace-codex-exec/v1")
    for role in ("checker", "doublechecker"):
        graph["planes"]["foreground"]["nodes"][role]["invoke"]["binding"] = "codex"
    (project / "workflow.json").write_text(json.dumps(graph))
    profile = profile_record(
        pin, ref, [], family_policy="different_for_implementation_review",
        review_bindings={
            "builder": {"binding_ref": "claude", "configured_family": "anthropic"},
            "checker": {"binding_ref": "codex", "configured_family": "openai"},
            "doublechecker": {"binding_ref": "codex", "configured_family": "openai"},
        },
    )
    (tmp_path / "profile.json").write_bytes(cc.encode(profile))
    ef.capture(project, tmp_path / "profile.json", history)
    graph = json.loads((project / "workflow.json").read_bytes())
    graph["planes"]["foreground"]["nodes"]["checker"]["invoke"]["binding"] = "claude"
    (project / "workflow.json").write_text(json.dumps(graph))
    init = command(project, "init_session.py", "--project-root", "..")
    assert init["outcome"] == "RECONCILE"
    assert "review_binding_mismatch" in init["summary"]
    with pytest.raises(ef.EngineeringError, match="review_binding_mismatch"):
        ef.binding_facts(graph, ef.load_profile(cc.encode(profile)), session_id="s",
                         occurrence_id="o", role="checker", graph_revision="r")


def test_strict_evidence_fields_and_definition_correspondence() -> None:
    base = evidence("pass")
    for key, value in (("timeout_seconds", 0), ("invocation_deadline", "not-a-time"),
                       ("argv", []), ("cwd", "/escape"), ("requirement_ids", [None]),
                       ("started_at", "yesterday"), ("exit_code", 256),
                       ("product_layer_refs", [])):
        with pytest.raises(ef.EngineeringError):
            ef.validate_evidence(ef.with_integrity(dict(base, **{key: value})))
    finished_first = dict(base, started_at="2026-10-31T00:00:02+00:00")
    with pytest.raises(ef.EngineeringError):
        ef.validate_evidence(ef.with_integrity(finished_first))
    ref = {"path": "p.json", "sha256": "f" * 64}
    definition_row = dict(definition("c", ["x"], ref), timeout_seconds=1,
                          timeout_policy=REASON)
    matching = ef.with_integrity(dict(
        base, definition_sha256=sha(cc.encode(definition_row)),
    ))
    ef.validate_evidence(matching, definition=definition_row,
                         identity={"session_id": "s", "phase": "baseline"})
    with pytest.raises(ef.EngineeringError, match="definition_mismatch"):
        ef.validate_evidence(ef.with_integrity(dict(matching, argv=["y"])),
                             definition=definition_row)
    with pytest.raises(ef.EngineeringError, match="identity_mismatch"):
        ef.validate_evidence(matching, identity={"session_id": "other"})


@pytest.mark.parametrize(("expected", "status", "exit_code", "valid"), [
    (0, "pass", 0, True),
    (0, "pass", 7, False),  # integrity-correct contradictory pass
    (0, "fail", 0, True),  # an unconfirmed stop or cancellation fails at any exit
    (0, "fail", 7, True),
    (3, "pass", 3, True),  # configured nonzero expectation
    (3, "pass", 0, False),
    (3, "fail", 0, True),
    (-15, "pass", -15, True),  # signed POSIX signal expectation
    (-15, "pass", 15, False),
    (0, "unavailable", None, True),
    (0, "not_selected", None, True),
])
def test_evidence_pass_requires_captured_expected_exit(
    expected: int, status: str, exit_code: int | None, valid: bool,
) -> None:
    ref = {"path": "p.json", "sha256": "f" * 64}
    row = dict(definition("c", ["x"], ref), timeout_seconds=1, timeout_policy=REASON,
               expected_exit_code=expected)
    record = ef.with_integrity(dict(evidence(status), exit_code=exit_code,
                                    definition_sha256=sha(cc.encode(row))))
    ef.validate_evidence(record)  # Structure alone cannot know the expectation.
    identity = {"session_id": "s", "task_id": "t", "check_id": "c",
                "phase": "baseline", "source_ref": record["source_ref"]}
    if valid:
        assert ef.validate_evidence(record, definition=row, execution_sha256="e" * 64,
                                    identity=identity)["status"] == status
    else:
        with pytest.raises(ef.EngineeringError,
                           match="check_evidence_status_mismatch"):
            ef.validate_evidence(record, definition=row, execution_sha256="e" * 64,
                                 identity=identity)


def test_missing_technical_prerequisite_uses_existing_recovery_routes(
    tmp_path: Path,
) -> None:
    def checks(ref: dict) -> list:
        return [definition("missing", ["/missing/selected-tool"], ref)]

    project, _, _ = enabled_copy(tmp_path, checks=checks)
    to_builder(project)
    graph = json.loads((project / "workflow.json").read_bytes())
    nodes = graph["planes"]["foreground"]["nodes"]
    assert nodes["engineering_baseline"]["on"]["TECHNICAL_RECOVERY"] == {
        "goto": "prepare_troubleshooter"}
    assert nodes[ef.RESUME_NODE]["on"]["RESUME_ENGINEERING_BASELINE"] == {
        "goto": "engineering_baseline"}
    first = drive_phase(project, tmp_path, "baseline")
    assert [e["outcome"] for e in first][-3:] == ["UNAVAILABLE", "ACCEPTED",
                                                   "TECHNICAL_RECOVERY"]
    state = ledger(project)
    assert state["continuation"]["node"] == "prepare_troubleshooter"
    pending = state["pending"]
    assert pending["kind"] == "engineering_prerequisite"
    assert pending["target"] == "engineering_baseline"
    assert pending["checks"][0]["unrun_reason"].startswith("tool_unavailable:")
    entry = pending["resume_continuation"]
    assert step(project, "troubleshooter") == "RESUME_ENGINEERING_BASELINE"
    state = ledger(project)
    assert state["continuation"] == entry and state["pending"] is None
    # The same unrepaired cause after a verified repair goes to Consultant, then
    # a concrete human decision; it is never a pass or an auto-install.
    second = drive_phase(project, tmp_path, "baseline")
    assert second[-1]["outcome"] == "CONSULTANT"
    assert estate(project)["baseline"] is None
    assert ledger(project)["continuation"]["node"] == "prepare_consultant"
    assert step(project, "consultant", "HUMAN_BLOCKED") == "PAUSE"


def test_cancelled_check_stops_its_group_and_never_claims_success(
    tmp_path: Path,
) -> None:
    import time

    from millrace_plus.router import process as owned

    ready = tmp_path / "ready"

    def checks(ref: dict) -> list:
        return [definition("cancel", [PYTHON, "-c",
                                      "import os,signal,time,pathlib;"
                                      "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                                      f"pathlib.Path({str(ready)!r})"
                                      ".write_text(str(os.getpid()));"
                                      "time.sleep(600)"], ref, timeout=60)]

    project, _, _ = enabled_copy(tmp_path, checks=checks)
    to_builder(project)
    encoded = command(project, "engineering_feedback.py", "phase", "baseline")[
        "outcome"][4:]
    script = invocation(project, tmp_path)
    # The same owned-process helper Core's command adapter uses: readiness
    # cancels the wrapper (TERM, KILL after its existing escalation interval).
    result = owned.run_owned_process(
        owned.ProcessRequest(
            (PYTHON, str(script), "check", "baseline", encoded),
            project / "foreground",
            {"PATH": str(Path(PYTHON).parent), "PYTHONDONTWRITEBYTECODE": "1"},
            None, time.time() + 90, stdin_limit=1, stdout_capture_limit=65536,
            stderr_capture_limit=65536, stdout_fail_limit=65536,
            stderr_fail_limit=None,
        ),
        poll=ready.exists,
        on_started=lambda pid: None,
    )
    assert result.stop_cause == "cancelled"
    pid = int(ready.read_text())
    end = time.monotonic() + 10
    while not ef.group_absent(pid) and time.monotonic() < end:
        time.sleep(0.05)
    assert ef.group_absent(pid)  # Physically stopped by the wrapper, not by QA.
    guard = run_at(project, invocation(project, tmp_path), "accept", "baseline",
                   encoded)
    row = estate(project)["open"]["checks"][-1]
    if row["receipt_ref"] is None:
        assert guard["outcome"] == "RECONCILE" and "group_absent" in guard["summary"]
    else:
        receipt = record_at(project, row["receipt_ref"])
        assert receipt["process"]["stop_cause"] == "cancelled"
        assert receipt["process"]["cleanup_confirmed"] is True
        assert record_at(project, row["evidence_ref"])["status"] == "fail"
        assert record_at(project, row["evidence_ref"])["invocation_deadline"] is None
        assert guard["outcome"] == "ACCEPTED"
    replay = run_at(project, invocation(project, tmp_path), "check", "baseline",
                    encoded)
    # Same-reservation replay never starts a second process.
    assert replay["outcome"] in {"UNKNOWN", "FAIL", "REFUSED"}
    runs = project / "foreground/lad-state/engineering/runs"
    assert [p.name for p in (runs / row["input_id"]).iterdir()] == ["start.json"]



def test_service_publication_derives_subject_and_requires_settlement(
    shared, monkeypatch, tmp_path: Path  # noqa: F811 - the imported pytest fixture
) -> None:
    import contextlib

    from test_lad_shared_target import bootstrap as shared_bootstrap

    from millrace_plus import local_intake

    assert shared_bootstrap(shared) == shared["record"]
    project, service = shared["project"], shared["service"]
    subject = shared["record"]["task_id"]
    assert ef.service_subject(project) == shared["record"]
    history = tmp_path / "history-home"
    monkeypatch.setattr(ef, "accepted_task", lambda path: {
        "task_id": subject, "bundle_ref": {"path": "records/b", "sha256": "b" * 64},
        "capture": {"history": {"home": str(history), "history_id": "repo",
                                "sha256": "c" * 64}},
    })
    published = []
    monkeypatch.setattr(ef, "publish_requirements",
                        lambda *a, **k: published.append(subject)
                        or {"status": "published"})

    from persistent_lad_support import CHILD, RUN

    @contextlib.contextmanager
    def snapshot(*args, **kwargs):
        yield {"id": RUN, "instance_id": CHILD, "status": "succeeded",
               "quiescent": True, "inflight_invocations": [], "invocations": [],
               "completions": []}

    monkeypatch.setattr(control, "exclusive_run_snapshot", snapshot)
    monkeypatch.setattr(control, "inspect_instance", lambda *a, **k: {
        "project_root": str(project), "workflow_file": str(project / "workflow.json")})

    def publish(**selectors):
        return ef.publish_task(project, history, "c" * 64, state_home=tmp_path,
                               instance_id=CHILD, run_id=RUN,
                               expected_state_version=1, **selectors)

    # Omitted selectors no longer bypass settlement of a service-owned subject.
    with pytest.raises(ef.EngineeringError,
                       match="engineering_publish_requires_settled_task"):
        publish()
    with pytest.raises(ef.EngineeringError, match="selector_mismatch"):
        publish(service_home=service.home, task="f" * 32)
    with pytest.raises(ef.EngineeringError, match="selector_mismatch"):
        publish(service_home=tmp_path / "other-home", task=subject)
    assert published == []
    # Positive branch only: the fixture overrides this task's terminal fields as
    # if normal CLOSE/ACK settlement had committed (real refusals above).
    real_tasks = local_intake.Intake.tasks

    def settled(self, task_id=None):
        return [dict(row, status="succeeded", terminal_receipt={"status": "applied"})
                if row["task_id"] == subject else row
                for row in real_tasks(self, task_id)]

    monkeypatch.setattr(local_intake.Intake, "tasks", settled)
    assert publish()["status"] == "published"
    assert publish(service_home=service.home, task=subject)["status"] == "published"
    assert published == [subject, subject]
    # A standalone copy cannot claim service selectors, and keeps publishing.
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    assert ef.service_subject(standalone) is None
    (standalone / "lad-target.json").write_text("{}\n")
    with pytest.raises(ef.EngineeringError, match="service_subject_conflict"):
        ef.service_subject(standalone)
