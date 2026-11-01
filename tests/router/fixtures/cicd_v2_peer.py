"""Deterministic roles; actual trusted checks and all guards are product code."""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path

from millrace_plus import check_contracts as cc
from millrace_plus.cicd_source import snapshot_policy, source_identity


def read(ref):
    path = Path(ref["path"])
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
    return json.loads(raw)


def main():
    pointer = json.loads(Path("active-role.json").read_text())
    document = read(pointer["input_ref"])
    subject = read(document["subject_ref"])
    target = Path(document["target_root"])
    role = document["role"]
    common = {"input_id": subject["input_id"], "attempt_id": document["attempt_id"]}
    if role == "investigate":
        evidence = []
        for ref in document.get("attempt_refs", []):
            attempt = read(ref)
            if attempt["kind"] == "reproduce" and attempt["status"] == "completed":
                evidence = read(attempt["artifact_ref"])["accepted_refs"]
        evidence = evidence or document.get("reproduction_refs", [])
        artifact = {
            "format": "millrace-plus-cicd-assessment/v2",
            **common,
            "subject_ref": document["subject_ref"],
            "reproduction_refs": evidence,
            "hypotheses": [
                {
                    "id": "wrong-operator",
                    "claim": "subtraction instead of addition",
                    "status": "supported",
                    "evidence_refs": evidence,
                }
            ],
            "proposed_paths": ["src/calc.py"],
            "change_classes": [],
            "risk": {
                "impact": "incorrect sum",
                "likelihood": "always for positive b",
                "scenario": "2+3 returns -1",
                "evidence_refs": evidence,
            },
            "route": "REPAIR_ALLOWED",
            "evidence_refs": evidence,
        }
        outcome = "ASSESSED"
    elif role == "repair":
        path = target / "src/calc.py"
        prior = path.read_text()
        after = prior.replace("return a - b", "return a + b")
        path.write_text(after)
        patch = "".join(
            difflib.unified_diff(
                prior.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/src/calc.py",
                tofile="b/src/calc.py",
            )
        )
        patch_path = Path(document["attempt_id"] + ".patch")
        patch_path.write_text(patch)
        config = read(document["config_ref"])
        policy = cc.validate_path_policy(read(config["path_policy_ref"]))
        artifact = {
            "format": "millrace-plus-cicd-repair/v2",
            **common,
            "assessment_ref": document["assessment_ref"],
            "source_before_sha256": document["source_before_sha256"],
            "source_after_sha256": source_identity(snapshot_policy(target, policy)),
            "patch_ref": {
                "path": str(patch_path),
                "sha256": hashlib.sha256(patch.encode()).hexdigest(),
            },
            "changed_paths": ["src/calc.py"],
            "change_classes": [],
            "summary": "use addition",
            "evidence_refs": [],
        }
        outcome = "PATCH_READY"
    else:
        plan = read(document["review_plan_ref"])
        repair = None
        for ref in document["attempt_refs"]:
            attempt = read(ref)
            if attempt["kind"] == "repair" and attempt["status"] == "completed":
                repair = read(attempt["artifact_ref"])
        artifact = {
            "format": "millrace-plus-cicd-review/v2",
            **common,
            "candidate_id": None,
            "materialization_id": None,
            "candidate_sha256": document["source_before_sha256"],
            "patch_ref": repair["patch_ref"],
            "checks_refs": plan["evidence_refs"],
            "verdict": "ACCEPT",
            "findings": [],
            "evidence_refs": [],
        }
        outcome = "ACCEPT"
    Path(document["output"]).write_text(json.dumps(artifact))
    print(
        json.dumps(
            {"outcome": outcome, "summary": "deterministic command peer", "refs": []}
        )
    )


def facade_main(node):
    """Exercise real public facade commands/guards in one test-owned graph."""
    import os
    from datetime import UTC, datetime

    from millrace_plus import cicd_candidates as facade
    from millrace_plus import cicd_v2_records as vr
    from millrace_plus.cicd_source import OWNER, foreign_dirty_paths
    from millrace_plus.cicd_v2_runtime import Runtime, V2Home
    from millrace_plus.local_intake import canonical
    from millrace_plus.router import control

    home = V2Home(Path.cwd().parent.parent)
    controls = Path(__file__).resolve().parents[1]
    context = facade.bind_invocation(
        home.home, "main", node, Path(__file__).resolve(), os.getpid(), Path.cwd()
    )
    state_file = home.workspace / "facade-test-state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    fixture = json.loads((controls / "context/facade-fixture.json").read_text())
    source = Path(fixture["source"])

    def previous(name):
        producer = state[name + "_context"]
        view = control.inspect_run(
            producer["instance_id"],
            producer["run_id"],
            state_home=Path(producer["state_home"]),
        )
        (done,) = [
            row
            for row in view["completions"]
            if row["invocation_id"] == producer["invocation_id"]
        ]
        assert done["effective_writer"] == "stopped"
        return home.retain(done)

    def request(
        operation, *, candidate=None, materialization=None, version=None, **values
    ):
        result = {
            "format": vr.CANDIDATE_REQUEST,
            "request_id": node,
            "operation": operation,
            "candidate_ref": candidate,
            "subject_ref": None,
            "export_basis_ref": None,
            "role": None,
            "materialization_id": materialization,
            "expected_materialization_version": version,
            "authority_ref": home.config["local_authority_ref"],
            "approval_ref": home.config["local_authority_ref"],
            "command_id": None,
            "role_binding_id": None,
            "expected_target_sha256": source_identity(home.observe()),
            "evidence_refs": [],
        }
        if operation in {"export", "review", "verify"}:
            result["authority_ref"] = result["approval_ref"] = None
        if operation == "export":
            result["expected_target_sha256"] = None
        result.update(values)
        return vr.validate_candidate_request(result)

    if node in {"snapshot_anchor", "wait_event"}:
        source_view = snapshot_policy(source, home.policy, home.guards)
        source_owner = home.retain_bytes((source / OWNER).read_bytes(), ".owner.json")
        source_target = facade.target_record(source, source_owner)
        target = facade.target_record(home.target, home.record["owner_ref"])
        tree = facade._tree(home, source)
        assert tree == facade._tree(home, home.target)
        tree_ref = home.retain(tree)
        environment = Runtime(home, controls)._environment(home.observe()) | {
            "target_root": str(source)
        }
        before = facade.before_manifest(
            source_view,
            target=source_target,
            anchor_head=tree["anchor_head"],
            logical_base_id=vr.tree_digest(tree),
            policy_ref=home.config["path_policy_ref"],
            environment_ref=home.retain(environment),
            foreign_dirty=foreign_dirty_paths(
                source,
                tree["anchor_head"],
                source_view,
                context["deadline"] - __import__("time").time(),
            ),
            payload_refs=facade.retain_payloads(source, source_view, home.workspace),
        )
        anchor = vr.validate_anchor(
            {
                "format": vr.ANCHOR,
                "repository": home.config["repository"],
                "anchor_head": tree["anchor_head"],
                "tree_ref": tree_ref,
                "source_target": source_target,
                "verification_target": target,
                "config_ref": home.config_ref,
                "authority_ref": home.config["local_authority_ref"],
                "initialized_at": datetime.now(UTC).isoformat(),
            }
        )
        state.update(
            source_target=source_target,
            target=target,
            original_tree_ref=tree_ref,
            before_ref=home.retain(before),
            anchor_ref=home.retain(anchor),
            snapshot_anchor_context=context,
        )
    elif node in {"export_anchor", "export_job"}:
        anchor = node == "export_anchor"
        producer_name = "snapshot_anchor" if anchor else "change_source"
        completion_ref = previous(producer_name)
        proof_ref = home.retain(
            {
                "invocation_context": state[producer_name + "_context"],
                "completion_ref": completion_ref,
            }
        )
        tree_ref = state["original_tree_ref"] if anchor else state["source_after_ref"]
        prior = None if anchor else state["anchor_candidate_ref"]
        basis = vr.validate_export_basis(
            {
                "format": vr.EXPORT_BASIS,
                "subject_ref": state["anchor_ref"] if anchor else state["subject_ref"],
                "source_target": state["source_target"],
                "verification_target": state["target"],
                "anchor_head": home.read_ref(tree_ref)["anchor_head"],
                "logical_base_id": vr.tree_digest(
                    home.read_ref(state["original_tree_ref"])
                ),
                "prior_candidate_ref": prior,
                "before_manifest_ref": state["before_ref"],
                "source_after_manifest_ref": tree_ref,
                "config_ref": home.config_ref,
                "assessment_ref": None if anchor else state["assessment_ref"],
                "authority_ref": home.config["local_authority_ref"],
                "writer_facts": {
                    "core_invocation_id": state[producer_name + "_context"][
                        "invocation_id"
                    ],
                    "writer_state": "stopped",
                },
            }
        )
        result = facade.operate_candidate(
            home,
            request(
                "export",
                subject_ref=basis["subject_ref"],
                export_basis_ref=home.retain(basis),
                evidence_refs=[proof_ref],
                **(
                    {
                        "materialization": state["materialization_id"],
                        "version": state["version"],
                    }
                    if not anchor and fixture["mode"] == "native"
                    else {}
                ),
            ),
            controls,
            context,
        )
        state["anchor_candidate_ref" if anchor else "candidate_ref"] = result[
            "artifact_refs"
        ][0]
    elif node in {
        "prepare_base",
        "import_job",
        "base_check",
        "final_check",
        "restore_job",
    }:
        operation = {
            "prepare_base": "prepare-base",
            "import_job": "import",
            "base_check": "check",
            "final_check": "check",
            "restore_job": "restore",
        }[node]
        is_base = node in {"prepare_base", "base_check"}
        candidate_ref = (
            state["anchor_candidate_ref"] if is_base else state["candidate_ref"]
        )
        kwargs = (
            {}
            if node == "prepare_base"
            else {
                "materialization": state["materialization_id"],
                "version": state["version"],
            }
        )
        if operation == "check":
            kwargs["command_id"] = "calc"
        selected_request = request(operation, candidate=candidate_ref, **kwargs)
        if fixture["mode"] == "interrupted_import" and node == "import_job":
            request_path = home.workspace / "interrupted-import-request.json"
            marker = home.workspace / "import-fault-issued"
            if request_path.exists():
                selected_request = json.loads(request_path.read_bytes())
            else:
                request_path.write_bytes(canonical(selected_request))
            if not marker.exists():
                marker.write_text("fault after first actual path write")
                apply = facade.apply_delta

                def partial(target, delta, bundle, **options):
                    apply(
                        target,
                        dict(delta) | {"changes": delta["changes"][:1]},
                        bundle,
                        **options,
                    )
                    raise RuntimeError(
                        "fixture interrupted after first actual import write"
                    )

                facade.apply_delta = partial
        if fixture["mode"] == "native" and node == "prepare_base":
            import contextlib
            import io

            from millrace_plus.cicd_cli import main as candidate_cli

            request_path = home.workspace / "cli-prepare-request.json"
            result_path = home.workspace / "cli-prepare-result.json"
            request_path.write_bytes(canonical(selected_request))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = candidate_cli(
                    [
                        "candidate",
                        "--workflow-home",
                        str(home.home),
                        "--operation",
                        operation,
                        "--request",
                        str(request_path),
                        "--result",
                        str(result_path),
                        "--execution-home",
                        str(home.home),
                        "--node",
                        node,
                        "--plane",
                        "main",
                        "--script",
                        str(Path(__file__).resolve()),
                    ]
                )
            assert code == 0, output.getvalue()
            result = json.loads(result_path.read_bytes())
            assert json.loads(output.getvalue()) == result
        else:
            result = facade.operate_candidate(home, selected_request, controls, context)
        state.update(
            materialization_id=result["materialization_id"],
            version=result["materialization_version"],
        )
        operations = [
            home.read_ref(ref)
            for ref in result["artifact_refs"]
            if home.read_ref(ref).get("format") == vr.MATERIALIZATION_OPERATION
        ]
        state[node + "_operation_id"] = operations[0]["operation_id"]
        state[node + "_context"] = context
    elif node in {
        "guard_base",
        "guard_import",
        "guard_base_check",
        "guard_final_check",
        "guard_restore",
    }:
        producer = {
            "guard_base": "prepare_base",
            "guard_import": "import_job",
            "guard_base_check": "base_check",
            "guard_final_check": "final_check",
            "guard_restore": "restore_job",
        }[node]
        completion_ref = previous(producer)
        if fixture["mode"] == "native" and node == "guard_final_check":
            from millrace_plus.cicd_records import CicdError

            try:
                facade.operate_candidate(
                    home,
                    request(
                        "review",
                        candidate=state["candidate_ref"],
                        materialization=state["materialization_id"],
                        version=state["version"],
                        role_binding_id="review",
                    ),
                    controls,
                    context,
                )
            except CicdError as exc:
                assert exc.code == "prior_operation_unaccounted", exc.code
            else:
                raise AssertionError(
                    "review prepared before check ownership acceptance"
                )
        if fixture["mode"] == "native" and node == "guard_base":
            from millrace_plus.cicd_records import CicdError

            before = facade._lease(home, state["materialization_id"])
            forged = home.read_ref(completion_ref) | {"outcome": "forged"}
            for ref, version, code in (
                (home.retain(forged), state["version"], "completion_mismatch"),
                (completion_ref, state["version"] + 1, "stale_materialization"),
            ):
                try:
                    facade.accept_materialization_operation(
                        home,
                        state["materialization_id"],
                        state[producer + "_operation_id"],
                        ref,
                        version,
                    )
                except CicdError as exc:
                    assert exc.code == code, exc.code
                else:
                    raise AssertionError("foreign acceptance was admitted")
                assert facade._lease(home, state["materialization_id"]) == before
        lease = facade.accept_materialization_operation(
            home,
            state["materialization_id"],
            state[producer + "_operation_id"],
            completion_ref,
            state["version"],
        )
        state["version"] = lease["version"]
        state.setdefault("completion_refs", []).append(completion_ref)
        if node == "guard_import":
            lease = facade.accept_materialization(
                home,
                state["materialization_id"],
                state["candidate_ref"],
                state["completion_refs"],
                state["version"],
            )
            state["version"] = lease["version"]
        if node in {"guard_base_check", "guard_final_check"}:
            accepted = json.loads(
                (
                    home.workspace
                    / (state[producer + "_operation_id"] + ".check-accepted.json")
                ).read_text()
            )
            state[producer + "_acceptance_ref"] = accepted["acceptance_ref"]
        if node == "guard_restore":
            assert lease["state"] == "restored"
            assert (home.target / "src/calc.py").read_text().endswith("return a - b\n")
    elif node == "prepare_investigation":
        (input_path,) = home.workspace.glob("*.input.json")
        subject_ref = home.file_ref(input_path)
        tree = facade._tree(home, home.target)
        binding = vr.validate_source_binding(
            {
                "format": vr.SOURCE_BINDING,
                "mode": "standalone",
                "input_ref": subject_ref,
                "anchor_head": tree["anchor_head"],
                "reported_head": tree["anchor_head"],
                "candidate_ref": None,
                "publication_ref": None,
                "preparer_ref": None,
                "logical_base_id": None,
                "materialization_ref": None,
                "status": "bound",
            }
        )
        basis = vr.validate_role_basis(
            {
                "format": vr.ROLE_BASIS,
                "subject_schema_ref": home.file_ref(
                    home.workspace / "schemas/input.json"
                ),
                "source_binding_ref": home.retain(binding),
                "source_before_sha256": source_identity(home.observe()),
                "config_sha256": home.config_sha256,
                "assessment_ref": None,
                "reproduction_refs": [state["base_check_acceptance_ref"]],
                "criteria_ref": None,
                "scope_ref": None,
                "public_writer_refs": [state["base_check_acceptance_ref"]],
            },
            role="investigate",
        )
        result = facade.operate_candidate(
            home,
            request(
                "project-role",
                subject_ref=subject_ref,
                role="investigate",
                materialization=state["materialization_id"],
                version=state["version"],
                role_binding_id="investigate",
                evidence_refs=[home.retain(basis)],
            ),
            controls,
            context,
        )
        state["investigation_projection_ref"] = result["artifact_refs"][0]
    elif node == "guard_investigation":
        view = control.inspect_run(
            context["instance_id"],
            context["run_id"],
            state_home=Path(context["state_home"]),
        )
        (done,) = [row for row in view["completions"] if row["node"] == "investigate"]
        accepted = facade.accept_candidate_role(
            home, state["investigation_projection_ref"], home.retain(done)
        )
        assert (
            facade.accept_candidate_role(
                home, state["investigation_projection_ref"], home.retain(done)
            )
            == accepted
        )
        state["accepted_assessment_ref"] = accepted["artifact_ref"]
        state["accepted_investigation_ref"] = home.retain(accepted)
    elif node in {"change_source", "prepare_role"}:
        (input_path,) = home.workspace.glob("*.input.json")
        subject_ref = home.file_ref(input_path)
        repro = [state["base_check_acceptance_ref"]]
        assessment = vr.validate_assessment(
            {
                "format": vr.ASSESSMENT,
                "input_id": home.read_ref(subject_ref)["input_id"],
                "subject_ref": subject_ref,
                "attempt_id": context["invocation_id"],
                "reproduction_refs": repro,
                "hypotheses": [
                    {
                        "id": "wrong-operator",
                        "claim": "subtraction instead of addition",
                        "status": "supported",
                        "evidence_refs": repro,
                    }
                ],
                "proposed_paths": ["src/calc.py"],
                "change_classes": [],
                "risk": {
                    "impact": "incorrect sum",
                    "likelihood": "observed",
                    "scenario": "2+3 returns -1",
                    "evidence_refs": repro,
                },
                "route": "REPAIR_ALLOWED",
                "evidence_refs": repro,
            }
        )
        if node == "change_source":
            source_file = source / "src/calc.py"
            source_file.write_text(
                source_file.read_text().replace("return a - b", "return a + b")
            )
            if fixture["mode"] == "interrupted_import":
                (source / "src/details.txt").write_text("new candidate detail\n")
                assessment["proposed_paths"] = ["src"]
            state.update(
                subject_ref=subject_ref,
                assessment_ref=home.retain(assessment),
                source_after_ref=home.retain(facade._tree(home, source)),
                change_source_context=context,
            )
        else:
            assessment = vr.validate_assessment(
                home.read_ref(state["accepted_assessment_ref"])
            )
            state.update(
                subject_ref=subject_ref, assessment_ref=state["accepted_assessment_ref"]
            )
            view = home.observe()
            tree = facade._tree(home, home.target)
            before = facade.before_manifest(
                view,
                target=state["target"],
                anchor_head=tree["anchor_head"],
                logical_base_id=vr.tree_digest(tree),
                policy_ref=home.config["path_policy_ref"],
                environment_ref=home.retain(Runtime(home, controls)._environment(view)),
                foreign_dirty=[],
                payload_refs=facade.retain_payloads(home.target, view, home.workspace),
            )
            state["before_ref"] = home.retain(before)
            binding = vr.validate_source_binding(
                {
                    "format": vr.SOURCE_BINDING,
                    "mode": "standalone",
                    "input_ref": subject_ref,
                    "anchor_head": tree["anchor_head"],
                    "reported_head": tree["anchor_head"],
                    "candidate_ref": None,
                    "publication_ref": None,
                    "preparer_ref": None,
                    "logical_base_id": None,
                    "materialization_ref": None,
                    "status": "bound",
                }
            )
            basis = vr.validate_role_basis(
                {
                    "format": vr.ROLE_BASIS,
                    "subject_schema_ref": home.file_ref(
                        home.workspace / "schemas/input.json"
                    ),
                    "source_binding_ref": home.retain(binding),
                    "source_before_sha256": source_identity(view),
                    "config_sha256": home.config_sha256,
                    "assessment_ref": state["assessment_ref"],
                    "reproduction_refs": repro,
                    "criteria_ref": None,
                    "scope_ref": state["assessment_ref"],
                    "public_writer_refs": [state["accepted_investigation_ref"], *repro],
                },
                role="repair",
            )
            result = facade.operate_candidate(
                home,
                request(
                    "project-role",
                    subject_ref=subject_ref,
                    role="repair",
                    materialization=state["materialization_id"],
                    version=state["version"],
                    role_binding_id="repair",
                    evidence_refs=[home.retain(basis)],
                ),
                controls,
                context,
            )
            state["version"] = result["materialization_version"]
            state["repair_projection_ref"] = result["artifact_refs"][0]
            state["prepare_role_context"] = context
    elif node == "guard_native_repair":
        view = control.inspect_run(
            context["instance_id"],
            context["run_id"],
            state_home=Path(context["state_home"]),
        )
        (producer,) = [row for row in view["invocations"] if row["node"] == "repair"]
        (done,) = [
            row for row in view["completions"] if row["invocation_id"] == producer["id"]
        ]
        lease = facade._lease(home, state["materialization_id"])
        operations = {
            home.read_ref(ref)["operation_id"]: home.read_ref(ref)
            for ref in lease["operation_refs"]
        }
        (repair,) = [
            operation
            for operation in operations.values()
            if operation["operation"] == "repair"
        ]
        completion_ref = home.retain(done)
        lease = facade.accept_materialization_operation(
            home,
            state["materialization_id"],
            repair["operation_id"],
            completion_ref,
            state["version"],
        )
        state["version"] = lease["version"]
        state["completion_refs"].append(completion_ref)
        public_context = dict(state["prepare_role_context"]) | {
            "node": "repair",
            "attempt": producer["attempt"],
            "invocation_id": producer["id"],
            "pid": producer["pid"],
            "cwd": producer["cwd"],
            "argv": producer["argv"],
            "deadline": producer["deadline"],
            "revision_id": producer["revision_id"],
            "public_run_ref": hashlib.sha256(canonical(view)).hexdigest(),
        }
        home.retain(view)
        state.update(
            source_after_ref=home.retain(facade._tree(home, home.target)),
            change_source_context=public_context,
        )
    elif node == "guard_seal":
        lease = facade.accept_materialization(
            home,
            state["materialization_id"],
            state["candidate_ref"],
            state["completion_refs"],
            state["version"],
        )
        state["version"] = lease["version"]
    elif node == "prepare_review":
        result = facade.operate_candidate(
            home,
            request(
                "review",
                candidate=state["candidate_ref"],
                materialization=state["materialization_id"],
                version=state["version"],
                role_binding_id="review",
                evidence_refs=[state["final_check_acceptance_ref"]],
            ),
            controls,
            context,
        )
        state["review_projection_ref"] = result["artifact_refs"][0]
    elif node == "guard_review":
        projection = home.read_ref(state["review_projection_ref"])
        document = home.read_ref(projection["input_ref"])
        review_ref = home.file_ref(home.workspace / document["output"])
        view = control.inspect_run(
            context["instance_id"],
            context["run_id"],
            state_home=Path(context["state_home"]),
        )
        (done,) = [
            row for row in view["completions"] if row["node"] == "review_candidate"
        ]
        assert done["outcome"] == "ACCEPT" and done["effective_writer"] == "stopped"
        accepted = facade.accept_candidate_review(
            home, state["review_projection_ref"], home.retain(done)
        )
        assert accepted["review_ref"]["sha256"] == review_ref["sha256"]
        assert (
            home.resolve_ref(accepted["review_ref"]).read_bytes()
            == home.resolve_ref(review_ref).read_bytes()
        )
        assert (
            facade.accept_candidate_review(
                home, state["review_projection_ref"], home.retain(done)
            )
            == accepted
        )
        state["review_ref"] = accepted["review_ref"]
        state["review_proof_ref"] = home.retain(accepted)
    elif node == "verify_job":
        result = facade.operate_candidate(
            home,
            request(
                "verify",
                candidate=state["candidate_ref"],
                materialization=state["materialization_id"],
                version=state["version"],
                evidence_refs=[
                    state["final_check_acceptance_ref"],
                    state["review_ref"],
                    state["review_proof_ref"],
                ],
            ),
            controls,
            context,
        )
        state["verification_ref"] = result["artifact_refs"][0]
    else:
        raise AssertionError(node)
    state_file.write_bytes(canonical(state))
    print(json.dumps({"outcome": "DONE", "summary": node, "refs": []}))


def candidate_review_main():
    from millrace_plus.cicd_v2_runtime import V2Home

    home = V2Home(Path.cwd().parent.parent)
    pointer = json.loads(Path("active-role.json").read_text())
    projection = home.read_ref(pointer["projection_ref"])
    document = home.read_ref(projection["input_ref"])
    candidate = home.read_ref(document["candidate_ref"])
    artifact = {
        "format": "millrace-plus-cicd-review/v2",
        "input_id": None,
        "candidate_id": candidate["candidate_id"],
        "materialization_id": document["materialization_id"],
        "attempt_id": document["attempt_id"],
        "candidate_sha256": source_identity(home.observe()),
        "patch_ref": candidate["delta_ref"],
        "checks_refs": document["checks_refs"],
        "verdict": "ACCEPT",
        "findings": [],
        "evidence_refs": [],
    }
    Path(document["output"]).write_text(json.dumps(artifact))
    print(
        json.dumps({"outcome": "ACCEPT", "summary": "candidate reviewed", "refs": []})
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "facade":
        facade_main(sys.argv[2])
    elif len(sys.argv) > 1 and sys.argv[1] == "candidate-review":
        candidate_review_main()
    else:
        main()
