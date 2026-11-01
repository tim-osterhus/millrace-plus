"""Factory source provenance and logical bases over the existing CI candidate facade."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from . import check_contracts as cc
from . import cicd_candidates as candidates
from . import cicd_v2_records as ci
from . import factory_records as fr
from .cicd_source import (
    OWNER,
    filesystem_identity,
    foreign_dirty_paths,
    git_head,
    snapshot_policy,
)
from .local_intake import canonical, strict_json

__all__ = [
    "capture_tree",
    "materialization",
    "index_identity",
    "bootstrap_anchor",
    "development_baseline",
    "check_development",
    "local_source_binding",
    "validate_local_binding",
    "validate_export_producer",
    "selected_commands",
    "engineering_mapping",
    "render_request",
    "synchronize",
    "verification_context",
]


def capture_tree(home, target: Path) -> dict:
    observed = snapshot_policy(target, home.ci.policy, home.ci.guards)
    return candidates.tree_manifest(
        observed,
        repository=home.config["repository"],
        anchor_head=git_head(target),
        policy_ref=home.ci.config["path_policy_ref"],
        payload_refs=candidates.retain_payloads(target, observed, home.ci.workspace),
    )


def materialization(home) -> dict | None:
    """Read the exact lease and its operations without repairing records."""
    pointer = home.ci.home / "active-materialization.json"
    if not pointer.exists():
        return None
    lease = ci.validate_materialization(
        home.ci.read_ref(strict_json(pointer.read_bytes()))
    )
    if lease["owner_ref"] != home.ci.record["owner_ref"]:
        raise fr.FactoryError("materialization_owner_mismatch")
    for ref in lease["operation_refs"]:
        operation = ci.validate_materialization_operation(home.ci.read_ref(ref))
        if operation["materialization_id"] != lease["materialization_id"]:
            raise fr.FactoryError("operation_lease_mismatch")
    return lease


def index_identity(root: Path) -> str:
    """Hash logical staged entries/flags/intent, excluding refreshable stat caches."""
    from .cicd_source import _git_read

    path = _git_read(root, ["rev-parse", "--git-path", "index"], 30).decode().strip()
    index = Path(path)
    if not index.is_absolute():
        index = root / index
    if index.is_symlink():
        raise fr.FactoryError("linked_git_index")
    prefix = ["-c", "diff.autoRefreshIndex=false", "-c", "diff.orderFile=/dev/null"]
    staged = _git_read(root, prefix + ["ls-files", "--stage", "-z"], 30)
    flags = _git_read(root, prefix + ["ls-files", "-v", "-z"], 30)
    raw = [
        "diff",
        "--cached",
        "--raw",
        "-z",
        "--no-abbrev",
        "--no-renames",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--no-relative",
        "--ignore-submodules=none",
    ]
    visible = _git_read(root, prefix + raw + ["--ita-visible-in-index"], 30)
    invisible = _git_read(root, prefix + raw + ["--ita-invisible-in-index"], 30)
    return hashlib.sha256(
        canonical(
            {
                "format": "millrace-plus-factory-logical-index/v1",
                "staged": staged.hex(),
                "flags": flags.hex(),
                "ita_visible": visible.hex(),
                "ita_invisible": invisible.hex(),
            }
        )
    ).hexdigest()


def _target(home, root: Path) -> dict:
    owner_ref = (
        home.ci.record["owner_ref"]
        if root == home.ci.target
        else home.ci.retain_bytes((root / OWNER).read_bytes(), ".owner.json")
    )
    return candidates.target_record(root, owner_ref)


def development_baseline(home, *, deadline: float) -> dict:
    root = Path(home.config["development_lane"]["target_root"])
    observed = snapshot_policy(root, home.ci.policy, home.ci.guards)
    if observed["gitlinks"] or observed["special"] or observed["unsupported"]:
        raise fr.FactoryError("unsupported_development_source")
    head = git_head(root)
    if head != home.config["anchor"]["head_sha"]:
        raise fr.FactoryError("factory_anchor_changed")
    remaining = deadline - time.time()
    if remaining <= 0:
        raise fr.FactoryError("factory_source_observation_expired")
    foreign = foreign_dirty_paths(root, head, observed, remaining)
    tree = capture_tree(home, root)
    return {
        "target": _target(home, root),
        "head": head,
        "index_sha256": index_identity(root),
        "tree_ref": home.ci.retain(tree),
        "foreign_dirty_paths": foreign,
        "products": observed["products"],
        "environment": observed["environment"],
    }


def bootstrap_anchor(home, baseline: dict) -> dict:
    """Retain the operator's empty-delta source baseline, without task/check credit."""
    tree = capture_tree(home, home.ci.target)
    dev = home.ci.read_ref(baseline["tree_ref"])
    protected = set(baseline["foreign_dirty_paths"])
    clean_entries = [
        entry for entry in dev["entries"] if entry["path"] not in protected
    ]
    validation_entries = [
        entry for entry in tree["entries"] if entry["path"] not in protected
    ]
    if (
        clean_entries != validation_entries
        or dev["baseline_links"] != tree["baseline_links"]
    ):
        raise fr.FactoryError("factory_anchor_tree_mismatch")
    before_view = home.ci.observe()
    if foreign_dirty_paths(home.ci.target, tree["anchor_head"], before_view, 30):
        raise fr.FactoryError("validation_anchor_dirty")
    target = _target(home, home.ci.target)
    tree_ref = home.ci.retain(tree)
    anchor = ci.validate_anchor(
        {
            "format": ci.ANCHOR,
            "repository": tree["repository"],
            "anchor_head": tree["anchor_head"],
            "tree_ref": tree_ref,
            "source_target": baseline["target"],
            "verification_target": target,
            "config_ref": home.ci.config_ref,
            "authority_ref": home.ci.config["local_authority_ref"],
            "initialized_at": home.record["created_at"],
        }
    )
    before = candidates.before_manifest(
        before_view,
        target=target,
        anchor_head=tree["anchor_head"],
        logical_base_id=ci.tree_digest(tree),
        policy_ref=tree["path_policy_ref"],
        environment_ref=home.ci.retain({"environment": before_view["environment"]}),
        foreign_dirty=[],
        payload_refs=candidates.retain_payloads(
            home.ci.target, before_view, home.ci.workspace
        ),
    )
    candidate = {
        "format": ci.CANDIDATE,
        "candidate_id": "0" * 64,
        "origin_refs": [home.ci.retain(anchor)],
        "repository": tree["repository"],
        "anchor_head": tree["anchor_head"],
        "logical_base_id": ci.tree_digest(tree),
        "prior_candidate_id": None,
        "base_tree_sha256": ci.tree_digest(tree),
        "candidate_tree_sha256": ci.tree_digest(tree),
        "delta_ref": home.ci.retain(candidates.tree_delta(tree, tree)),
        "tree_manifest_ref": tree_ref,
        "before_manifest_ref": home.ci.retain(before),
        "source_target": baseline["target"],
        "verification_target": target,
        "config_ref": home.ci.config_ref,
        "assessment_ref": None,
    }
    candidate["candidate_id"] = ci.candidate_identity(candidate)
    return home.ci.retain(ci.validate_candidate(candidate))


def check_development(home, base_ref: dict) -> dict:
    """Recheck accepted source plus exact original foreign paths and unchanged index."""
    baseline = home.read_ref(home.record["development_baseline_ref"])
    target = Path(baseline["target"]["root"])
    if filesystem_identity(target) != baseline["target"]["filesystem_identity"]:
        raise fr.FactoryError("development_identity_changed")
    if (
        git_head(target) != baseline["head"]
        or index_identity(target) != baseline["index_sha256"]
    ):
        raise fr.FactoryError("development_git_changed")
    base = fr.validate_base(home.read_ref(base_ref))
    accepted = ci.validate_candidate(home.ci.read_ref(base["candidate_ref"]))
    expected = ci.validate_tree(home.ci.read_ref(accepted["tree_manifest_ref"]))
    original = ci.validate_tree(home.ci.read_ref(baseline["tree_ref"]))
    actual = capture_tree(home, target)
    foreign = set(baseline["foreign_dirty_paths"])
    expected_by_path = {
        row["path"]: row for row in expected["entries"] if row["path"] not in foreign
    }
    expected_by_path.update(
        {row["path"]: row for row in original["entries"] if row["path"] in foreign}
    )
    expected = expected | {
        "entries": [expected_by_path[p] for p in sorted(expected_by_path)]
    }
    if actual != expected:
        raise fr.FactoryError("development_source_drift")
    observed = snapshot_policy(target, home.ci.policy, home.ci.guards)
    if observed["products"] != baseline["products"]:
        raise fr.FactoryError("development_product_drift")
    return {
        "tree_ref": home.ci.retain(actual),
        "index_sha256": baseline["index_sha256"],
        "products": observed["products"],
        "foreign_dirty_paths": sorted(foreign),
    }


def local_source_binding(home, state: dict, subject_ref: dict) -> dict:
    base_ref = home.read_ref(state["claim_ref"])["base_candidate_ref"]
    base = fr.validate_base(home.read_ref(base_ref))
    candidate = ci.validate_candidate(home.ci.read_ref(base["candidate_ref"]))
    subject = fr.validate_input(home.ci.read_ref(subject_ref))
    if subject["base_candidate_sha256"] not in {
        None,
        candidate["candidate_tree_sha256"],
    }:
        raise fr.FactoryError("factory_base_stale")
    if subject["source_kind"] != "task":
        original = home.ci.read_ref(subject["feedback_ref"])
        reported = (
            original.get("head_sha")
            if subject["source_kind"] == "ci_event"
            else original.get("affected_version", {}).get("head_sha")
        )
        # A reported external commit needs an actual publication/preparer producer.
        # F1's only positive producer is its accepted unpublished local tree.
        if reported is not None:
            raise fr.FactoryError("unbound_external_revision")
        # A release/artifact report also needs its real selected mapping producer.
        # An asserted local base digest alone cannot attest an external artifact.
        raise fr.FactoryError("factory_source_validator_unavailable")
    lease = materialization(home)
    if lease is None or lease["origin_base_candidate_ref"] != base["candidate_ref"]:
        raise fr.FactoryError("factory_materialization_mismatch")
    return ci.validate_source_binding(
        {
            "format": ci.SOURCE_BINDING,
            "mode": "factory",
            "input_ref": subject_ref,
            "anchor_head": home.config["anchor"]["head_sha"],
            "reported_head": None,
            "candidate_ref": base["candidate_ref"],
            "publication_ref": None,
            "preparer_ref": None,
            "logical_base_id": candidate["candidate_tree_sha256"],
            "materialization_ref": home.ci.retain(lease),
            "status": "bound",
        }
    )


def _factory_consumer(ci_home, subject_ref: dict):
    from .factory_runtime import Home

    matches = []
    for actor in ci_home.authority["actors"]:
        root = Path(actor["execution_home"])
        if not (root / "factory.json").exists():
            continue
        factory = Home(root)
        if factory.ci.home != ci_home.home:
            continue
        active = factory.active()
        if active is None:
            continue
        state = factory.state(active["task_id"])
        if state["input_ref"] == subject_ref:
            matches.append((factory, state))
    if len(matches) != 1:
        raise fr.FactoryError("factory_subject_owner_unavailable")
    return matches[0]


def validate_local_binding(ci_home, value: object) -> None:
    """Validate the accepted local source and lease against actual Factory facts."""
    binding = ci.validate_source_binding(value)
    if ci_home.config["source_policy"]["mode"] != "factory":
        raise fr.FactoryError("factory_source_policy_required")
    factory, state = _factory_consumer(ci_home, binding["input_ref"])
    expected = local_source_binding(factory, state, state["input_ref"])
    if binding != expected:
        raise fr.FactoryError("factory_source_binding_changed")
    check_development(
        factory, factory.read_ref(state["claim_ref"])["base_candidate_ref"]
    )


def validate_export_producer(ci_home, request: dict, basis: dict) -> None:
    """Revalidate exact Factory job/producer before CI freezes or seals source bytes."""
    factory, state = _factory_consumer(ci_home, request["subject_ref"])
    facts = factory.facts(state)
    claim = factory.read_ref(state["claim_ref"])
    base = fr.validate_base(factory.read_ref(claim["base_candidate_ref"]))
    if basis["prior_candidate_ref"] != base["candidate_ref"]:
        raise fr.FactoryError("factory_export_predecessor_changed")
    if facts.get("route") == "lad":
        from .factory_lad import Lane

        lane = Lane(factory)
        proof = lane.parked(
            facts.get("lad_task_id"), caller_deadline=time.monotonic() + 30
        )
        if not proof["closed"] or proof["task"]["status"] != "succeeded":
            raise fr.FactoryError("factory_lad_not_settled")
        if basis["before_manifest_ref"] != facts["development_before_ref"]:
            raise fr.FactoryError("factory_export_before_changed")
    elif facts.get("route") == "ci":
        if not facts.get("repair_projection_ref") or not facts.get(
            "repair_completion_ref"
        ):
            raise fr.FactoryError("factory_repair_producer_unproven")
    else:
        raise fr.FactoryError("factory_export_producer_unavailable")
    fr.validate_input(ci_home.read_ref(request["subject_ref"]))
    if facts["input_sha256"] != request["subject_ref"]["sha256"]:
        raise fr.FactoryError("factory_export_subject_changed")


def engineering_mapping(home) -> dict:
    """Resolve configured check identities without inferring model commands."""
    from . import engineering_feedback as engineering

    policy = home.config["check_policy"]
    if policy["engineering_profile_ref"] is None:
        return {"profile": None, "snapshot": None, "mapping": {}}
    profile_raw = home.raw_ref(policy["engineering_profile_ref"])
    profile = engineering.load_profile(profile_raw)
    snapshot = engineering.load_requirements(
        home.raw_ref(policy["requirements_snapshot_ref"])
    )
    if profile["path_policy_ref"] != home.config["path_policy_ref"]:
        raise fr.FactoryError("factory_path_policy_mismatch")
    pinned = profile["requirements_snapshot"]
    if (
        {"path": pinned["path"], "sha256": pinned["sha256"]}
        != policy["requirements_snapshot_ref"]
        or pinned["history_id"] != snapshot["history_id"]
        or pinned["sequence"] != snapshot["sequence"]
        or snapshot["repository_id"] != home.config["repository"]
        or profile["repository"]["target_id"] != home.config["repository"]
    ):
        raise fr.FactoryError("factory_engineering_snapshot_mismatch")
    mapping = home.read_ref(policy["lq_command_map_ref"])
    fr.require_fields(mapping, "format entries", "invalid_factory_command_map")
    if mapping["format"] != "millrace-check-command-map/v1":
        raise fr.FactoryError("invalid_factory_command_map")
    definitions = {row["id"]: row for row in profile["checks"]}
    requirement_ids = {row["id"] for row in snapshot["requirements"]}
    if any(
        set(row["requirement_ids"]) - requirement_ids for row in definitions.values()
    ):
        raise fr.FactoryError("factory_profile_requirement_unknown")
    commands = {row["id"]: row for row in home.ci.config["commands"]}
    by_lq = {}
    for row in mapping["entries"]:
        fr.require_fields(
            row,
            "lq_check_id ci_command_id execution_sha256",
            "invalid_factory_command_map",
        )
        lq, ci_id = row["lq_check_id"], row["ci_command_id"]
        if lq in by_lq or lq not in definitions or ci_id not in commands:
            raise fr.FactoryError("invalid_factory_command_map")
        definition = dict(definitions[lq])
        definition.update(
            cc.resolve_timeout(
                definition["timeout_seconds"], definition["timeout_policy"]
            )
        )
        expected = cc.execution_sha256(
            cc.execution_of(definition, policy=home.ci.policy)
        )
        actual = cc.execution_sha256(
            cc.execution_of(commands[ci_id], policy=home.ci.policy)
        )
        if row["execution_sha256"] != expected or actual != expected:
            raise fr.FactoryError("factory_command_mapping_mismatch")
        by_lq[lq] = row
    if set(by_lq) != set(definitions):
        raise fr.FactoryError("factory_command_mapping_incomplete")
    return {"profile": profile, "snapshot": snapshot, "mapping": by_lq}


def selected_commands(home, paths: list[str], obligations: dict) -> list[str]:
    result = set(
        ci.select_checks(home.ci.config, paths, uncertain=False)["command_ids"]
    )
    mapping = engineering_mapping(home)
    if mapping["profile"] is not None:
        from .engineering_feedback import selected

        for definition in mapping["profile"]["checks"]:
            if selected(definition, paths) or set(definition["requirement_ids"]) & {
                row["id"]
                for row in obligations["requirements"]
                if row["status"] == "active"
            }:
                result.add(mapping["mapping"][definition["id"]]["ci_command_id"])
    # Prior accepted criteria remain active. Keep their actually accepted
    # command coverage even when this job's changed paths select a smaller set.
    for base in home.base_chain(obligations["base_ref"]):
        if base["outcome_ref"] is None:
            continue
        outcome = fr.validate_record(
            home.read_ref(base["outcome_ref"]), "millrace-plus-factory-outcome/v1"
        )
        for ref in outcome["verification_refs"]:
            verification = ci.validate_candidate_verification(home.ci.read_ref(ref))
            if verification["status"] != "verified":
                raise fr.FactoryError("factory_prior_verification_unaccepted")
            for accepted_ref in verification["checks_refs"]:
                accepted = cc.validate_completion_acceptance(
                    home.ci.read_ref(accepted_ref), "public_core_completion"
                )
                raw = ci.validate_check(
                    home.ci.read_ref(accepted["process_receipt_ref"])
                )
                command = next(
                    (
                        row
                        for row in home.ci.config["commands"]
                        if row["id"] == raw["command_id"]
                    ),
                    None,
                )
                if command is None or raw["command_sha256"] != cc.execution_sha256(
                    cc.execution_of(command, policy=home.ci.policy)
                ):
                    raise fr.FactoryError("factory_prior_command_changed")
                result.add(raw["command_id"])
    return sorted(result)


def engineering_evidence(
    home,
    state: dict,
    facts: dict,
    acceptance_ref: dict,
    product_ref: dict,
    *,
    baseline: bool,
) -> tuple[list, list]:
    """Adapt exact accepted CI process evidence; retain its native provenance."""
    from . import engineering_feedback as engineering

    selected = engineering_mapping(home)
    if selected["profile"] is None:
        return [], []
    accepted = cc.validate_completion_acceptance(
        home.ci.read_ref(acceptance_ref), "public_core_completion"
    )
    raw_ref = accepted["process_receipt_ref"]
    raw = ci.validate_check(home.ci.read_ref(raw_ref))
    product = cc.validate_products(home.ci.read_ref(product_ref))
    if product["status"] != "accepted" or product["completion_ref"] != acceptance_ref:
        raise fr.FactoryError("factory_engineering_products_unaccepted")
    refs, provenance = [], []
    definitions = {row["id"]: row for row in selected["profile"]["checks"]}
    for identifier, mapping in selected["mapping"].items():
        if mapping["ci_command_id"] != raw["command_id"]:
            continue
        definition = dict(definitions[identifier])
        definition.update(
            cc.resolve_timeout(
                definition["timeout_seconds"], definition["timeout_policy"]
            )
        )
        digest = mapping["execution_sha256"]
        if (
            raw["command_sha256"] != digest
            or raw["argv"] != definition["argv"]
            or raw["cwd"] != definition["cwd"]
        ):
            raise fr.FactoryError("factory_engineering_check_mismatch")
        candidate_ref = (
            facts["base_candidate_ref"] if baseline else facts["candidate_ref"]
        )
        source = ci.validate_candidate(home.ci.read_ref(candidate_ref))[
            "tree_manifest_ref"
        ]
        ran = raw["exit_code"] is not None and raw["finished_at"] is not None
        evidence = engineering.evidence_record(
            check_id=identifier,
            definition_sha256=ci.digest(definition),
            profile_sha256=home.config["check_policy"]["engineering_profile_ref"][
                "sha256"
            ],
            task_id=state["job_id"],
            session_id=home.factory_id,
            occurrence_id=raw["attempt_id"],
            phase="baseline" if baseline else "candidate",
            source_ref=source,
            execution_sha256=digest,
            argv=raw["argv"],
            cwd=raw["cwd"],
            environment_sha256=ci.digest(raw["environment_provenance"]),
            path_policy_ref=raw["path_policy_ref"],
            product_layer_refs=[product_ref],
            invocation_deadline=None,
            timeout_seconds=definition["timeout_seconds"],
            tool_version=None,
            started_at=raw["started_at"] if ran else None,
            finished_at=raw["finished_at"] if ran else None,
            exit_code=raw["exit_code"] if ran else None,
            status=("pass" if raw["status"] == "passed" else "fail")
            if ran
            else "unavailable",
            stdout_ref=raw["stdout_ref"] if ran else None,
            stderr_ref=raw["stderr_ref"] if ran else None,
            requirement_ids=definition["requirement_ids"],
            selection_reason="factory_shared_check_union",
            unrun_reason=None if ran else "ci_process_exit_unobserved",
        )
        engineering.validate_evidence(
            evidence,
            definition=definition,
            execution_sha256=digest,
            identity={
                "task_id": state["job_id"],
                "session_id": home.factory_id,
                "source_ref": source,
            },
        )
        evidence_ref = home.retain(evidence)
        refs.append(evidence_ref)
        provenance.append(
            home.retain(
                {
                    "format": "millrace-plus-factory-check-adapter/v1",
                    "evidence_ref": evidence_ref,
                    "process_receipt_ref": raw_ref,
                    "acceptance_ref": acceptance_ref,
                    "product_ref": product_ref,
                    "command_map_ref": home.config["check_policy"][
                        "lq_command_map_ref"
                    ],
                    "environment_identity": "sha256_ci_environment_provenance",
                    "original_product_refs": raw["product_refs"],
                }
            )
        )
    return refs, provenance


def render_request(subject: dict, obligations: dict) -> bytes:
    """Render the current request and original obligations as ordinary LAD data."""
    subject = fr.validate_input(subject)
    lines = [
        subject["request"] or "Investigate the retained feedback and its evidence.",
        "",
        "Original current acceptance criteria:",
    ]
    for row in subject["criteria"]:
        lines.append(f"- {row['id']}: {row['text']}")
    lines += ["", "Retained active acceptance obligations:"]
    for row in obligations["requirements"]:
        if row["status"] == "active":
            lines.append(f"- {row['id']}: {row['text']}")
            lines.append("  Origin: " + canonical(row["evidence_refs"]).decode())
    raw = ("\n".join(lines) + "\n").encode()
    return raw


def synchronize(home, state: dict, *, accepted: bool, deadline: float) -> dict:
    """Apply/reverse only the exact job delta while its dedicated LAD lane is parked."""
    from .factory_lad import Lane
    from .lad_service import target_lock

    facts = home.facts(state)
    lane = Lane(home)
    proof = lane.parked(facts.get("lad_task_id"), caller_deadline=deadline)
    if not proof["closed"]:
        raise fr.FactoryError("factory_lane_not_parked")
    candidate_ref = facts.get("candidate_ref")
    if candidate_ref is None:
        return {"status": "unchanged", "closure": proof}
    candidate = ci.validate_candidate(home.ci.read_ref(candidate_ref))
    delta = ci.validate_delta(home.ci.read_ref(candidate["delta_ref"]))
    baseline = home.read_ref(home.record["development_baseline_ref"])
    if set(baseline["foreign_dirty_paths"]) & {row["path"] for row in delta["changes"]}:
        raise fr.FactoryError("candidate_overlap_with_prior_work")
    with target_lock(lane.service.config, protected_roots=(home.home, home.ci.home)):
        # Recheck local public state under ownership exclusion. Join GETs and
        # all control sends stay outside this brief source mutation lock.
        from .subworkflow_v2 import closed

        _, parent = lane.runtime.parent(_deadline=deadline)
        if (
            not closed(parent, attention=True)
            or parent["status"] != "paused"
            or parent["id"] != proof["parent"]["id"]
            or parent["controls"] != proof["parent"]["controls"]
            or any(
                row["status"] in {"queued", "claimed", "running", "held"}
                for row in lane.service.intake.tasks()
            )
        ):
            raise fr.FactoryError("factory_lad_pause_race")
        root = Path(lane.service.config["target_root"])
        if (
            index_identity(root) != baseline["index_sha256"]
            or git_head(root) != baseline["head"]
        ):
            raise fr.FactoryError("development_git_changed")
        before_products = snapshot_policy(root, home.ci.policy, home.ci.guards)[
            "products"
        ]
        if before_products != baseline["products"]:
            raise fr.FactoryError("undeclared_output")
        if facts["route"] == "ci" and accepted:
            candidates.apply_delta(root, delta, home.ci.workspace, inverse=False)
        elif facts["route"] == "lad" and not accepted:
            candidates.apply_delta(root, delta, home.ci.workspace, inverse=True)
        actual = capture_tree(home, root)
        expected_ref = (
            candidate_ref
            if accepted
            else home.read_ref(home.read_ref(state["claim_ref"])["base_candidate_ref"])[
                "candidate_ref"
            ]
        )
        expected = ci.validate_candidate(home.ci.read_ref(expected_ref))
        tree = ci.validate_tree(home.ci.read_ref(expected["tree_manifest_ref"]))
        original = ci.validate_tree(home.ci.read_ref(baseline["tree_ref"]))
        foreign = set(baseline["foreign_dirty_paths"])
        entries = {
            row["path"]: row for row in tree["entries"] if row["path"] not in foreign
        }
        entries.update(
            {row["path"]: row for row in original["entries"] if row["path"] in foreign}
        )
        if actual != tree | {"entries": [entries[p] for p in sorted(entries)]}:
            raise fr.FactoryError("development_synchronization_mismatch")
        return {
            "status": "accepted" if accepted else "restored_unaccepted",
            "candidate_ref": candidate_ref,
            "tree_ref": home.ci.retain(actual),
            "closure_ref": home.retain(proof),
            "index_sha256": index_identity(root),
        }


def verification_context(ci_home, candidate_ref: dict) -> dict | None:
    """Read the current F1 obligations and exact configured check union."""
    frozen = ci.validate_candidate(ci_home.read_ref(candidate_ref))
    subjects = [
        ref
        for ref in frozen["origin_refs"]
        if ci_home.read_ref(ref).get("format") == fr.INPUT
    ]
    if not subjects:
        return None
    if len(subjects) != 1:
        raise fr.FactoryError("factory_candidate_subject_ambiguous")
    factory, state = _factory_consumer(ci_home, subjects[0])
    facts = factory.facts(state)
    if facts.get("candidate_ref") != candidate_ref:
        raise fr.FactoryError("factory_verification_candidate_changed")
    delta = ci.validate_delta(ci_home.read_ref(frozen["delta_ref"]))
    obligations = factory.read_ref(facts["obligations_ref"])
    return {
        "input_ref": state["input_ref"],
        "obligations_ref": facts["obligations_ref"],
        "scope_ref": facts["scope_ref"],
        "candidate_binding_ref": facts["candidate_binding_ref"],
        "command_ids": selected_commands(
            factory, [row["path"] for row in delta["changes"]], obligations
        ),
        "review_binding_ids": [ci_home.config["role_bindings"]["review"][0]["id"]],
    }


def outcome_input(home, outcome: dict) -> dict:
    """Resolve the original input through its exact terminal receipt chain."""
    outcome = fr.validate_record(outcome, "millrace-plus-factory-outcome/v1")
    receipt = home.read_ref(outcome["terminal_receipt_ref"])
    proposal = fr.validate_record(
        home.read_ref(receipt["evidence_ref"]), "millrace-plus-factory-disposition/v1"
    )
    fr.validate_input(home.read_ref(proposal["input_ref"]))
    if (
        receipt["job_id"] != outcome["job_id"]
        or proposal["job_id"] != outcome["job_id"]
        or proposal["input_ref"]["sha256"] != outcome["input_sha256"]
    ):
        raise fr.FactoryError("factory_outcome_input_changed")
    return proposal["input_ref"]


def settlement_pending(home) -> dict | None:
    """Identify a same-intent terminal publication gap without repairing records."""
    pending = []
    for task in home.intake.tasks():
        if task["terminal_receipt"] is None or task["claim"] is None:
            continue
        state = home.state(task["task_id"])
        facts = home.facts(state)
        proposal_ref = facts.get("disposition_ref")
        if proposal_ref is None or state["phase"] != "settle":
            raise fr.FactoryError("factory_settlement_projection_changed")
        intent = {
            "task_id": task["task_id"],
            "decision": "settle",
            "evidence": proposal_ref,
        }
        if task["terminal_receipt"]["intent_sha256"] != ci.digest(intent):
            raise fr.FactoryError("factory_terminal_intent_changed")
        if facts.get("outcome_ref") is None:
            pending.append(task)
            continue
        outcome = fr.validate_record(
            home.read_ref(facts["outcome_ref"]), "millrace-plus-factory-outcome/v1"
        )
        if (
            home.read_ref(outcome["terminal_receipt_ref"]) != task["terminal_receipt"]
            or outcome_input(home, outcome) != state["input_ref"]
        ):
            raise fr.FactoryError("factory_settlement_projection_changed")
        if facts.get("accepted") and not any(
            row["outcome_ref"] == facts["outcome_ref"]
            for row in home.base_chain(home.base_ref())
        ):
            pending.append(task)
    if len(pending) > 1:
        raise fr.FactoryError("factory_multiple_publication_gaps")
    return pending[0] if pending else None


def outcome_record(
    home,
    state: dict,
    facts: dict,
    proposal: dict,
    *,
    status: str,
    terminal_ref: dict | None,
    lad_acceptance_ref: dict | None = None,
) -> dict:
    """One output shape for truthful held views and exact terminal publication."""
    engineering = home.config["check_policy"]
    record = {
        "format": "millrace-plus-factory-outcome/v1",
        "factory_id": home.factory_id,
        "job_id": state["job_id"],
        "input_sha256": facts["input_sha256"],
        "candidate_ref": proposal["candidate_ref"],
        "accepted_base_ref": proposal["prior_base_ref"],
        "verification_refs": proposal["verification_refs"],
        "review_ref": proposal["review_ref"],
        "delivery_refs": [],
        "health_refs": [],
        "rollback_refs": [],
        "incident_refs": [],
        "closure_refs": proposal["closure_refs"],
        "restoration_ref": proposal["restoration_ref"],
        "engineering_refs": {
            "profile_ref": engineering["engineering_profile_ref"],
            "snapshot_ref": engineering["requirements_snapshot_ref"],
            "obligations_ref": facts.get("obligations_ref"),
            "check_refs": facts.get("engineering_refs", []),
            "lad_acceptance_ref": lad_acceptance_ref,
        },
        "terminal_receipt_ref": terminal_ref,
        "status": status,
        "reason": proposal["reason"],
        "guidance_candidate_ref": None,
    }
    return fr.validate_record(record, record["format"])


def publish_outcome(home, state: dict, facts: dict, closure: dict) -> tuple[dict, dict]:
    """Publish the saved disposition, receipt, outcome and successor."""
    import uuid

    disposition = fr.validate_record(
        home.read_ref(facts["disposition_ref"]), "millrace-plus-factory-disposition/v1"
    )
    task = home.intake.tasks(state["job_id"])[0]
    intent = {
        "task_id": state["job_id"],
        "decision": "settle",
        "evidence": facts["disposition_ref"],
    }
    receipt = home.intake.decide(
        uuid.uuid5(uuid.UUID(hex=home.factory_id), state["job_id"] + "/settle").hex,
        intent,
        check=lambda row: None,
        terminal_status=disposition["status"],
        expected_version=task["version"],
        expected_claim=task["claim"],
    )
    outcome = outcome_record(
        home,
        state,
        facts,
        disposition,
        status="local_candidate"
        if facts.get("accepted")
        else "no_action"
        if facts.get("no_action")
        else "cancelled"
        if facts.get("cancel_requested")
        else "held_evidence",
        terminal_ref=home.retain(receipt),
        lad_acceptance_ref=home.retain(closure["task"]["terminal_receipt"])
        if closure.get("task") is not None
        else None,
    )
    outcome_ref = home.retain(fr.validate_record(outcome, outcome["format"]))
    from .factory_runtime import write_replace

    write_replace(
        home.workspace / "jobs" / state["job_id"] / "outcome.json", canonical(outcome)
    )
    if facts.get("accepted"):
        old = fr.validate_base(home.read_ref(disposition["prior_base_ref"]))
        base = fr.integrity(
            {
                "format": fr.BASE,
                "factory_id": home.factory_id,
                "sequence": old["sequence"] + 1,
                "previous_ref": disposition["prior_base_ref"],
                "candidate_ref": facts["candidate_ref"],
                "source_binding_ref": facts["source_binding_ref"],
                "outcome_ref": outcome_ref,
                "requirements_snapshot_ref": old["requirements_snapshot_ref"],
            }
        )
        next_ref = home.retain(fr.validate_base(base))
        current = home.base_ref()
        if current not in (disposition["prior_base_ref"], next_ref):
            raise fr.FactoryError("factory_base_conflict")
        write_replace(home.home / "accepted-base.json", canonical(next_ref))
    return outcome_ref, outcome
