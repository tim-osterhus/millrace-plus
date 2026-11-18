"""Standalone CI commit intents and exact inspection through trusted argv."""

from __future__ import annotations

import copy
import hashlib
import os
import time
from collections.abc import Mapping
from typing import Any

from . import check_contracts as cc
from . import cicd_git_commit as local_git
from . import cicd_v2_records as vr
from .cicd_records import CicdError
from .cicd_source import (
    _git_read,
    foreign_dirty_paths,
    git_head,
    source_identity,
    write_exact,
)
from .local_intake import IntakeError, canonical, strict_json

APPROVAL = "millrace-plus-cicd-action-approval/v1"
PERMISSION = "millrace-plus-cicd-action-permission/v1"
REQUEST = "millrace-plus-cicd-effect-request/v2"
RESULT = "millrace-plus-cicd-effect-result/v2"
BASIS = "millrace-plus-cicd-delivery-basis/v1"
DELIVERY = "millrace-plus-cicd-delivery/v1"
OPERATION = "millrace-plus-cicd-effect-operation/v1"
RETAINED_OPERATION = "millrace-plus-cicd-effect-operation/v2"
EVIDENCE = "millrace-plus-cicd-effect-evidence/v1"


def _object(value: object, fields: str, fmt: str, code: str) -> dict[str, Any]:
    return vr._record(value, fields.split(), fmt, code)


def _fields(value: object, fields: tuple[str, ...], code: str) -> dict[str, Any]:
    return vr._fields(value, fields, code)


def _target(value: object) -> dict[str, Any]:
    code = "invalid_effect_target"
    fields = (
        "repository_root", "branch", "base_head", "mode", "patch_sha256",
        "source_after_sha256",
    )
    if isinstance(value, dict) and "commit_plan_ref" in value:
        fields += ("commit_plan_ref",)
    obj = _fields(value, fields, code)
    vr._absolute(obj["repository_root"], code)
    vr._text(obj["branch"], code)
    vr._revision(obj["base_head"], code)
    if obj["mode"] != "working_tree":
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    vr._sha(obj["patch_sha256"], code)
    vr._sha(obj["source_after_sha256"], code)
    if "commit_plan_ref" in obj:
        vr._ref(obj["commit_plan_ref"], code)
    return obj


def _effects(value: object) -> list[dict[str, Any]]:
    rows = vr._list(value, "invalid_effects", nonempty=True)
    if len(rows) != 1:
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    obj = _fields(rows[0], ("kind", "target"), "invalid_effects")
    if obj["kind"] != "commit":
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    _target(obj["target"])
    return [obj]


def validate_policy(value: object, config: Mapping[str, Any]) -> dict[str, Any]:
    """Capture fixed selectors; realized patch/source identity is approved later."""
    if value == vr.hold_external_policy():
        return vr.hold_external_policy()
    code = "invalid_external_policy"
    obj = _fields(
        value,
        (
            "mode",
            "effects",
            "adapters",
            "authority_ref",
            "wait_policy_ref",
        ),
        code,
    )
    if obj["mode"] != "authorized":
        raise vr.CicdV2Error(code)
    if config["source_policy"]["mode"] != "standalone":
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    vr._ref(obj["authority_ref"], code)
    if obj["wait_policy_ref"] is not None:
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    effects = vr._list(obj["effects"], code)
    adapters = obj["adapters"]
    if not effects:
        if adapters != {}:
            raise vr.CicdV2Error(code)
        return obj
    if len(effects) != 1:
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    effect = _fields(effects[0], ("kind", "target"), code)
    if effect["kind"] != "commit":
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    target = _fields(effect["target"], ("repository_root", "branch", "mode"), code)
    if target["mode"] != "working_tree":
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    if target["repository_root"] != config["target_root"]:
        raise vr.CicdV2Error("effect_target_mismatch")
    vr._text(target["branch"], code)
    adapter = _fields(adapters, ("commit",), code)["commit"]
    adapter_fields = ("prepare", "execute", "inspect", "capabilities")
    if isinstance(adapter, dict) and "backend" in adapter:
        adapter_fields += ("backend",)
        if adapter["backend"] != "local_git":
            raise vr.CicdV2Error(code)
    adapter = _fields(adapter, adapter_fields, code)
    commands = {row["id"]: row for row in config["commands"]}
    for operation in ("prepare", "execute", "inspect"):
        command = commands.get(adapter[operation])
        if command is None or command["expected_products"]:
            raise vr.CicdV2Error(code)
        argv = command["argv"]
        if argv.count("{request_file}") != 1 or argv.count("{result_file}") != 1:
            raise vr.CicdV2Error(code)
        if any(
            "{" in arg or "}" in arg
            for arg in argv
            if arg not in {"{request_file}", "{result_file}"}
        ):
            raise vr.CicdV2Error(code)
    capabilities = vr._list(adapter["capabilities"], code, nonempty=True)
    if len(capabilities) != 1:
        raise vr.CicdV2Error(code)
    capability = _fields(capabilities[0], ("id", "evidence_ref"), code)
    if capability["id"] != "exact_inspection":
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    vr._ref(capability["evidence_ref"], code)
    return obj


def validate_approval(value: object) -> dict[str, Any]:
    code = "invalid_action_approval"
    obj = _object(
        value,
        "format approval_id issuer input_id candidate_id patch_sha256 "
        "source_after_sha256 config_sha256 effects authority_ref evidence_refs",
        APPROVAL,
        code,
    )
    vr._text(obj["approval_id"], code)
    vr._text(obj["issuer"], code)
    vr._subject(obj["input_id"], obj["candidate_id"], code)
    for key in ("patch_sha256", "source_after_sha256", "config_sha256"):
        vr._sha(obj[key], code)
    _effects(obj["effects"])
    vr._ref(obj["authority_ref"], code)
    vr._refs(obj["evidence_refs"], code)
    return obj


def validate_permission(value: object) -> dict[str, Any]:
    code = "invalid_action_permission"
    obj = _object(
        value,
        "format permission_id issuer input_id candidate_id patch_sha256 "
        "source_after_sha256 config_sha256 effects authority_ref approval_ref",
        PERMISSION,
        code,
    )
    vr._text(obj["permission_id"], code)
    vr._text(obj["issuer"], code)
    vr._subject(obj["input_id"], obj["candidate_id"], code)
    for key in ("patch_sha256", "source_after_sha256", "config_sha256"):
        vr._sha(obj[key], code)
    _effects(obj["effects"])
    vr._ref(obj["authority_ref"], code)
    vr._ref(obj["approval_ref"], code)
    return obj


def effect_identity(request: Mapping[str, Any]) -> str:
    return vr.digest(
        {
            key: request[key]
            for key in (
                "delivery_id",
                "kind",
                "target",
                "input_id",
                "candidate_id",
                "patch_sha256",
                "source_after_sha256",
            )
        }
        | {"permission_sha256": request["permission_ref"]["sha256"]}
    )


def validate_request(value: object, operation: str | None = None) -> dict[str, Any]:
    code = "invalid_effect_request"
    obj = _object(
        value,
        "format operation delivery_id effect_id kind input_id candidate_id "
        "patch_sha256 source_after_sha256 config_sha256 permission_ref target "
        "prior_effect_refs evidence_refs",
        REQUEST,
        code,
    )
    vr._enum(obj["operation"], ("prepare", "execute", "inspect"), code)
    if operation is not None and obj["operation"] != operation:
        raise vr.CicdV2Error("effect_operation_mismatch")
    vr._subject(obj["input_id"], obj["candidate_id"], code)
    if obj["candidate_id"] is not None or obj["kind"] != "commit":
        raise vr.CicdV2Error("unsupported_until_full_ci_d1")
    for key in (
        "delivery_id",
        "effect_id",
        "patch_sha256",
        "source_after_sha256",
        "config_sha256",
    ):
        vr._sha(obj[key], code)
    vr._ref(obj["permission_ref"], code)
    target = _target(obj["target"])
    if any(target[key] != obj[key] for key in ("patch_sha256", "source_after_sha256")):
        raise vr.CicdV2Error("effect_target_mismatch")
    vr._refs(obj["prior_effect_refs"], code)
    vr._refs(obj["evidence_refs"], code)
    if obj["effect_id"] != effect_identity(obj):
        raise vr.CicdV2Error("effect_identity_mismatch")
    return obj


def validate_result(value: object, request: Mapping[str, Any]) -> dict[str, Any]:
    code = "invalid_effect_result"
    obj = _object(
        value,
        "format delivery_id effect_id request_sha256 operation status remote_id "
        "observed_head artifact_sha256 evidence_refs observed_at diagnostic",
        RESULT,
        code,
    )
    if any(
        obj[key] != request[key] for key in ("delivery_id", "effect_id", "operation")
    ):
        raise vr.CicdV2Error("effect_result_mismatch")
    if obj["request_sha256"] != vr.digest(request):
        raise vr.CicdV2Error("effect_result_mismatch")
    allowed = {
        "prepare": ("prepared", "refused", "uncertain"),
        "execute": ("applied", "not_applied", "pending", "refused", "uncertain"),
        "inspect": ("applied", "not_applied", "pending", "uncertain"),
    }
    vr._enum(obj["status"], allowed[request["operation"]], code)
    vr._optional_text(obj["remote_id"], code)
    vr._optional_revision(obj["observed_head"], code)
    vr._optional_sha(obj["artifact_sha256"], code)
    refs = vr._refs(obj["evidence_refs"], code)
    vr._time(obj["observed_at"], code)
    vr._optional_text(obj["diagnostic"], code)
    if obj["status"] in {"applied", "not_applied"} and not refs:
        raise vr.CicdV2Error("effect_evidence_missing")
    if obj["status"] == "applied" and (
        obj["remote_id"] is None or obj["observed_head"] is None
    ):
        raise vr.CicdV2Error("effect_evidence_missing")
    return obj


def _result_evidence(
    home: Any,
    result: Mapping[str, Any],
    request: Mapping[str, Any],
    retained_evidence_refs: object | None = None,
) -> None:
    refs = result["evidence_refs"]
    if retained_evidence_refs is not None:
        code = "invalid_retained_effect_evidence"
        mappings = vr._list(retained_evidence_refs, code)
        if len(mappings) != len(refs):
            raise vr.CicdV2Error(code)
        retained = []
        for original, value in zip(refs, mappings, strict=True):
            row = _fields(value, ("original_ref", "retained_ref"), code)
            vr._ref(row["original_ref"], code)
            vr._ref(row["retained_ref"], code)
            if row["original_ref"] != original or row["retained_ref"] != {
                "path": original["sha256"] + ".effect-evidence.bin",
                "sha256": original["sha256"],
            }:
                raise vr.CicdV2Error(code)
            retained.append(row["retained_ref"])
        refs = retained
    for ref in refs:
        home.resolve_ref(ref)
    if result["status"] not in {"applied", "not_applied"}:
        return
    proof = _object(
        home.read_ref(refs[0]),
        "format effect_id intent_sha256 target_sha256 status remote_id observed_head",
        EVIDENCE,
        "invalid_effect_evidence",
    )
    expected = {
        "effect_id": request["effect_id"],
        "intent_sha256": vr.digest(dict(request) | {"operation": "execute"}),
        "target_sha256": vr.digest(request["target"]),
        "status": result["status"],
        "remote_id": result["remote_id"],
        "observed_head": result["observed_head"],
    }
    if any(proof[key] != value for key, value in expected.items()):
        raise vr.CicdV2Error("effect_evidence_mismatch")
    if result["status"] == "not_applied" and (
        proof["remote_id"] is not None or proof["observed_head"] is not None
    ):
        raise vr.CicdV2Error("effect_absence_unproven")
    if result["status"] == "applied" and "commit_plan_ref" in request["target"]:
        plan = local_git.plan_for(home, request["target"])
        if {result["observed_head"], result["remote_id"]} != {plan["commit"]}:
            raise vr.CicdV2Error("effect_evidence_mismatch")


def _retain_result(
    home: Any, raw: bytes, request: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    from .cicd_v2_runtime import _read

    result = validate_result(strict_json(raw), request)
    mappings = []
    for original in result["evidence_refs"]:
        proof = _read(home.resolve_ref(original))
        if hashlib.sha256(proof).hexdigest() != original["sha256"]:
            raise vr.CicdV2Error("effect_evidence_mismatch")
        mappings.append(
            {
                "original_ref": original,
                "retained_ref": home.retain_bytes(proof, ".effect-evidence.bin"),
            }
        )
    _result_evidence(home, result, request, mappings)
    return result, home.retain_bytes(raw, ".json"), mappings


def _operation_record(value: object) -> dict[str, Any]:
    fields = (
        "format delivery_id intent_ref previous_ref operation request_ref "
        "command_id command_sha256 argv_sha256 invocation process_ref "
        "result_ref completion_ref status"
    )
    fmt = value.get("format") if isinstance(value, dict) else None
    if fmt == RETAINED_OPERATION:
        fields += " retained_evidence_refs"
    else:
        fmt = OPERATION
    return _object(value, fields, fmt, "invalid_effect_operation")


def selected(home: Any) -> bool:
    policy = home.config["external_policy"]
    return policy["mode"] == "authorized" and bool(policy["effects"])


def is_local_git(home: Any) -> bool:
    adapter = home.config["external_policy"]["adapters"].get("commit", {})
    return adapter.get("backend") == "local_git"


def git_facts(home: Any, invocation: Mapping[str, Any]) -> dict[str, Any]:
    def read(args: list[str]) -> bytes:
        remaining = invocation["deadline"] - time.time()
        if remaining <= 0:
            raise vr.CicdV2Error("invocation_deadline_expired")
        return _git_read(home.target, args, remaining)

    return {
        "head": git_head(home.target),
        "branch": read(["symbolic-ref", "--short", "HEAD"]).decode("utf-8").strip(),
        "index_sha256": hashlib.sha256(read(["ls-files", "--stage", "-z"])).hexdigest(),
        "staged": bool(read(["diff", "--cached", "--name-only", "-z"])),
    }


def capture_git(
    home: Any, snapshot: dict[str, Any], invocation: Mapping[str, Any]
) -> dict[str, Any]:
    facts = git_facts(home, invocation)
    remaining = invocation["deadline"] - time.time()
    if remaining <= 0:
        raise vr.CicdV2Error("invocation_deadline_expired")
    baseline = facts | {
        "dirty_paths": foreign_dirty_paths(
            home.target, facts["head"], snapshot, remaining
        )
    }
    if is_local_git(home):
        branch = home.config["external_policy"]["effects"][0]["target"]["branch"]
        baseline["local_git"] = local_git.baseline(
            home.target, branch, invocation["deadline"]
        )
    return baseline


def basis_ref(home: Any, input_id: str) -> dict[str, Any] | None:
    path = home.workspace / (input_id + ".delivery-basis.json")
    if not path.exists():
        return None
    return home.file_ref(path)


def _frozen(home: Any, input_id: str) -> dict[str, Any]:
    from .cicd_v2_runtime import _read

    return home.read_ref(
        strict_json(_read(home.workspace / (input_id + ".freeze.json")))
    )


def freeze_basis(
    runtime: Any,
    state: dict[str, Any],
    patch: dict[str, Any],
    invocation: Mapping[str, Any],
) -> dict[str, Any]:
    home = runtime.home
    policy = home.config["external_policy"]
    facts = git_facts(home, invocation)
    frozen = _frozen(home, state["input_id"])
    initial = frozen.get("delivery_git")
    if initial is None:
        raise vr.CicdV2Error("delivery_baseline_missing")
    if facts["staged"] or initial["staged"]:
        raise vr.CicdV2Error("delivery_staged_changes")
    if any(path in initial["dirty_paths"] for path in patch["changed_paths"]):
        raise vr.CicdV2Error("delivery_foreign_patch_overlap")
    if any(facts[key] != initial[key] for key in ("head", "branch", "index_sha256")):
        raise vr.CicdV2Error("delivery_git_changed")
    selector = policy["effects"][0]["target"]
    if facts["branch"] != selector["branch"]:
        raise vr.CicdV2Error("effect_target_mismatch")
    view = runtime._source(state, patch["source_after_sha256"])
    source = source_identity(view)
    target = selector | {
        "base_head": facts["head"],
        "patch_sha256": patch["patch_ref"]["sha256"],
        "source_after_sha256": source,
    }
    if is_local_git(home):
        target["commit_plan_ref"] = local_git.freeze_plan(
            home, state["input_id"], target, patch, view, initial["local_git"],
            invocation["deadline"],
        )
    _target(target)
    basis = {
        "format": BASIS,
        "input_id": state["input_id"],
        "config_ref": home.config_ref,
        "authority_ref": policy["authority_ref"],
        "patch_ref": patch["patch_ref"],
        "source_after_sha256": source,
        "target": target,
        "git": facts,
        "check_plan_ref": state["check_plan_ref"],
        "review_plan_ref": state["review_plan_ref"],
        "attempt_refs": state["attempt_refs"],
        "claim": home.task(state["input_id"])["claim"],
    }
    home.resolve_ref(policy["authority_ref"])
    for capability in policy["adapters"]["commit"]["capabilities"]:
        home.resolve_ref(capability["evidence_ref"])
    path = home.workspace / (state["input_id"] + ".delivery-basis.json")
    write_exact(path, canonical(basis), "delivery_basis_conflict")
    return home.file_ref(path)


def check_permission(
    home: Any, state: Mapping[str, Any], decision: Mapping[str, Any]
) -> dict[str, Any]:
    if not selected(home):
        raise vr.CicdV2Error("delivery_requires_authorized_policy")
    if state["delivery_refs"]:
        raise vr.CicdV2Error("delivery_already_approved")
    if (
        state["hold_ref"] is None
        or decision["basis_sha256"] != state["hold_ref"]["sha256"]
    ):
        raise vr.CicdV2Error("delivery_decision_basis_changed")
    if decision["expected_state_version"] != state["version"]:
        raise vr.CicdV2Error("delivery_decision_state_changed")
    ref = basis_ref(home, state["input_id"])
    if ref is None:
        raise vr.CicdV2Error("delivery_basis_missing")
    basis = home.read_ref(ref)
    report = vr.validate_report(home.read_ref(state["hold_ref"]))
    if (
        report["status"] != "checked_patch"
        or report["patch_ref"] != basis["patch_ref"]
        or report["source_after_sha256"] != basis["source_after_sha256"]
    ):
        raise vr.CicdV2Error("delivery_basis_changed")
    permission = validate_permission(home.read_ref(decision["permission_ref"]))
    approval = validate_approval(home.read_ref(permission["approval_ref"]))
    expected = {
        "input_id": state["input_id"],
        "candidate_id": None,
        "patch_sha256": basis["patch_ref"]["sha256"],
        "source_after_sha256": basis["source_after_sha256"],
        "config_sha256": home.config_sha256,
        "effects": [{"kind": "commit", "target": basis["target"]}],
        "authority_ref": home.config["external_policy"]["authority_ref"],
    }
    if any(
        permission[key] != value or approval[key] != value
        for key, value in expected.items()
    ):
        raise vr.CicdV2Error("action_permission_mismatch")
    if permission["issuer"] != approval["issuer"]:
        raise vr.CicdV2Error("action_approval_mismatch")
    home.resolve_ref(permission["authority_ref"])
    for item in approval["evidence_refs"]:
        home.resolve_ref(item)
    if (
        basis["config_ref"] != home.config_ref
        or basis["attempt_refs"] != state["attempt_refs"]
        or basis["claim"] != home.task(state["input_id"])["claim"]
    ):
        raise vr.CicdV2Error("delivery_basis_changed")
    _basis_authority(home, state, basis)
    return permission


def _basis_authority(
    home: Any, state: Mapping[str, Any], value: object
) -> dict[str, Any]:
    basis = _object(
        value,
        "format input_id config_ref authority_ref patch_ref source_after_sha256 "
        "target git check_plan_ref review_plan_ref attempt_refs claim",
        BASIS,
        "invalid_delivery_basis",
    )
    binding = vr.validate_source_binding(home.read_ref(state["source_binding_ref"]))
    selector = home.config["external_policy"]["effects"][0]["target"]
    expected = selector | {
        "base_head": binding["anchor_head"],
        "patch_sha256": basis["patch_ref"]["sha256"],
        "source_after_sha256": basis["source_after_sha256"],
    }
    frozen = _frozen(home, state["input_id"])
    initial = frozen["delivery_git"]
    if is_local_git(home):
        target = _target(basis["target"])
        plan = local_git.plan_for(home, target)
        if (
            plan["baseline"] != initial.get("local_git")
            or plan["input_id"] != state["input_id"]
        ):
            raise vr.CicdV2Error("delivery_basis_changed")
        expected["commit_plan_ref"] = target["commit_plan_ref"]
    if basis["target"] != expected:
        raise vr.CicdV2Error("delivery_basis_changed")
    if (
        basis["git"]
        != {key: initial[key] for key in ("head", "branch", "index_sha256", "staged")}
        or initial["staged"]
    ):
        raise vr.CicdV2Error("delivery_basis_changed")
    return basis


def approve(
    runtime: Any, state: dict[str, Any], decision: dict[str, Any]
) -> dict[str, Any]:
    home = runtime.home
    permission = check_permission(home, state, decision)
    ref = basis_ref(home, state["input_id"])
    delivery_id = vr.digest(
        {
            "input_id": state["input_id"],
            "basis_ref": ref,
            "permission_ref": decision["permission_ref"],
        }
    )
    request = {
        "format": REQUEST,
        "operation": "execute",
        "delivery_id": delivery_id,
        "effect_id": "0" * 64,
        "kind": "commit",
        "input_id": state["input_id"],
        "candidate_id": None,
        "patch_sha256": permission["patch_sha256"],
        "source_after_sha256": permission["source_after_sha256"],
        "config_sha256": home.config_sha256,
        "permission_ref": decision["permission_ref"],
        "target": permission["effects"][0]["target"],
        "prior_effect_refs": [],
        "evidence_refs": [ref],
    }
    request["effect_id"] = effect_identity(request)
    validate_request(request)
    delivery = {
        "format": DELIVERY,
        "delivery_id": delivery_id,
        "input_id": state["input_id"],
        "basis_ref": ref,
        "permission_ref": decision["permission_ref"],
        "decision_ref": home.retain(decision),
        "intent_ref": home.retain(request),
    }
    state = runtime._save(
        state,
        delivery_refs=[home.retain(delivery)],
        hold_ref=None,
        phase="guard_delivery",
        applied_decision_ids=[*state["applied_decision_ids"], decision["request_id"]],
    )
    task = home.task(state["input_id"])
    home.intake.transition(
        task["task_id"], version=task["version"], claim=task["claim"], status="running"
    )
    return state


def history(
    home: Any, state: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    refs = state["delivery_refs"]
    if not refs:
        raise vr.CicdV2Error("delivery_missing")
    delivery = _object(
        home.read_ref(refs[0]),
        "format delivery_id input_id basis_ref permission_ref decision_ref intent_ref",
        DELIVERY,
        "invalid_delivery",
    )
    intent = validate_request(home.read_ref(delivery["intent_ref"]), "execute")
    if (
        delivery["input_id"] != state["input_id"]
        or delivery["delivery_id"] != intent["delivery_id"]
    ):
        raise vr.CicdV2Error("delivery_subject_mismatch")
    basis = _basis_authority(home, state, home.read_ref(delivery["basis_ref"]))
    if delivery["delivery_id"] != vr.digest(
        {
            "input_id": state["input_id"],
            "basis_ref": delivery["basis_ref"],
            "permission_ref": delivery["permission_ref"],
        }
    ):
        raise vr.CicdV2Error("delivery_identity_changed")
    permission = validate_permission(home.read_ref(delivery["permission_ref"]))
    decision = vr.validate_decision(home.read_ref(delivery["decision_ref"]))
    approval = validate_approval(home.read_ref(permission["approval_ref"]))
    if (
        decision not in home.decisions(state["input_id"])
        or decision["kind"] != "approve_delivery"
        or decision["request_id"] not in state["applied_decision_ids"]
        or decision["permission_ref"] != delivery["permission_ref"]
        or intent["permission_ref"] != delivery["permission_ref"]
        or intent["evidence_refs"] != [delivery["basis_ref"]]
        or basis["format"] != BASIS
        or basis["config_ref"] != home.config_ref
        or basis["input_id"] != state["input_id"]
        or basis["attempt_refs"] != state["attempt_refs"]
        or basis["claim"] != home.task(state["input_id"])["claim"]
    ):
        raise vr.CicdV2Error("delivery_authority_changed")
    expected = {
        "input_id": state["input_id"],
        "candidate_id": None,
        "patch_sha256": basis["patch_ref"]["sha256"],
        "source_after_sha256": basis["source_after_sha256"],
        "config_sha256": home.config_sha256,
        "authority_ref": basis["authority_ref"],
        "effects": [{"kind": "commit", "target": basis["target"]}],
    }
    if any(
        permission[key] != value or approval[key] != value
        for key, value in expected.items()
    ):
        raise vr.CicdV2Error("delivery_authority_changed")
    if (
        permission["issuer"] != approval["issuer"]
        or basis["authority_ref"] != home.config["external_policy"]["authority_ref"]
        or any(
            intent[key] != permission[key]
            for key in (
                "input_id",
                "candidate_id",
                "patch_sha256",
                "source_after_sha256",
                "config_sha256",
            )
        )
        or intent["target"] != basis["target"]
    ):
        raise vr.CicdV2Error("delivery_authority_changed")
    for item in [
        basis["patch_ref"],
        basis["authority_ref"],
        *approval["evidence_refs"],
    ]:
        home.resolve_ref(item)
    operations = []
    previous = refs[0]
    for ref in refs[1:]:
        record = _operation_record(home.read_ref(ref))
        if (
            record["previous_ref"] != previous
            or record["intent_ref"] != delivery["intent_ref"]
            or record["delivery_id"] != delivery["delivery_id"]
        ):
            raise vr.CicdV2Error("effect_history_conflict")
        request = validate_request(
            home.read_ref(record["request_ref"]), record["operation"]
        )
        if request != intent | {"operation": record["operation"]}:
            raise vr.CicdV2Error("effect_intent_changed")
        vr._enum(
            record["status"],
            ("reserved", "observed", "accepted"),
            "invalid_effect_operation",
        )
        context = _fields(
            record["invocation"],
            (
                "instance_id",
                "state_home",
                "run_id",
                "plane_id",
                "node",
                "attempt",
                "invocation_id",
                "revision_id",
                "controls_root",
                "pid",
                "cwd",
                "argv",
                "deadline",
                "public_run_ref",
            ),
            "invalid_effect_context",
        )
        if (
            context["instance_id"] != home.instance()
            or context["state_home"] != str(home.record["state_home"])
            or context["plane_id"] != "main"
            or context["node"] != record["operation"] + "_effect"
        ):
            raise vr.CicdV2Error("effect_context_changed")
        command_id = home.config["external_policy"]["adapters"]["commit"][
            record["operation"]
        ]
        command = copy.deepcopy(
            next(row for row in home.config["commands"] if row["id"] == command_id)
        )
        result_path = home.workspace / (
            intent["effect_id"] + "-" + context["invocation_id"] + ".effect-result.json"
        )
        replacements = {
            "{request_file}": str(home.resolve_ref(record["request_ref"])),
            "{result_file}": str(result_path),
        }
        command["argv"] = [replacements.get(arg, arg) for arg in command["argv"]]
        if (
            record["command_id"] != command_id
            or record["argv_sha256"] != vr.digest(command["argv"])
            or record["command_sha256"]
            != cc.execution_sha256(cc.execution_of(command, policy=home.policy))
        ):
            raise vr.CicdV2Error("effect_command_changed")
        prior = operations[-1] if operations else None
        same = prior is not None and prior["invocation"] == context
        if same:
            allowed = {"reserved": "observed", "observed": "accepted"}
            if record["status"] != allowed.get(prior["status"]):
                raise vr.CicdV2Error("effect_history_conflict")
            stable = (
                "format",
                "operation",
                "request_ref",
                "command_id",
                "command_sha256",
                "argv_sha256",
            )
            if any(record[key] != prior[key] for key in stable):
                raise vr.CicdV2Error("effect_intent_changed")
            if record["status"] == "accepted" and any(
                record[key] != prior[key] for key in ("process_ref", "result_ref")
            ):
                raise vr.CicdV2Error("effect_result_mismatch")
            if (
                record["status"] == "accepted"
                and record["format"] == RETAINED_OPERATION
                and record["retained_evidence_refs"] != prior["retained_evidence_refs"]
            ):
                raise vr.CicdV2Error("effect_result_mismatch")
        elif (
            record["status"] != "reserved"
            or record["process_ref"] is not None
            or record["result_ref"] is not None
        ):
            raise vr.CicdV2Error("effect_history_conflict")
        if not same:
            permitted = "prepare" if prior is None else None
            if prior is not None:
                prior_result = (
                    None
                    if prior["result_ref"] is None
                    else home.read_ref(prior["result_ref"])
                )
                if (
                    prior["status"] == "accepted"
                    and prior_result["status"] == "applied"
                ):
                    permitted = None
                elif prior["status"] == "accepted" and (
                    prior["operation"] == "prepare"
                    and prior_result["status"] == "prepared"
                    or prior["operation"] == "inspect"
                    and prior_result["status"] == "not_applied"
                ):
                    permitted = "execute"
                elif any(row["operation"] == "execute" for row in operations):
                    permitted = "inspect"
            if record["operation"] != permitted:
                raise vr.CicdV2Error("effect_history_conflict")
        if record["status"] == "reserved" and record["completion_ref"] is not None:
            raise vr.CicdV2Error("effect_history_conflict")
        if record["status"] == "accepted" and (
            record["completion_ref"] is None or record["result_ref"] is None
        ):
            raise vr.CicdV2Error("effect_history_conflict")
        for key in ("command_sha256", "argv_sha256"):
            vr._sha(record[key], "invalid_effect_operation")
        for key in ("process_ref", "result_ref", "completion_ref"):
            if record[key] is not None:
                home.resolve_ref(record[key])
        if record["result_ref"] is not None:
            result = validate_result(home.read_ref(record["result_ref"]), request)
            if record["format"] == RETAINED_OPERATION:
                # A new record always requires its own retained copies. Legacy
                # records retain original-path semantics without read repair.
                mappings = record["retained_evidence_refs"]
                vr._list(mappings, "invalid_retained_effect_evidence")
                _result_evidence(home, result, request, mappings)
            else:
                _result_evidence(home, result, request)
        elif (
            record["format"] == RETAINED_OPERATION
            and record["retained_evidence_refs"] != []
        ):
            raise vr.CicdV2Error("invalid_retained_effect_evidence")
        if record["process_ref"] is not None:
            process = home.read_ref(record["process_ref"])
            if (
                process["command_sha256"] != record["command_sha256"]
                or vr.digest(process["argv"]) != record["argv_sha256"]
            ):
                raise vr.CicdV2Error("effect_command_changed")
            if record["result_ref"] is not None and process["status"] != "passed":
                raise vr.CicdV2Error("effect_process_unproven")
        operations.append(record)
        previous = ref
    return delivery, operations


def _append(
    runtime: Any, state: dict[str, Any], record: dict[str, Any], **changes: Any
) -> dict[str, Any]:
    record = record | {"previous_ref": state["delivery_refs"][-1]}
    return runtime._save(
        state,
        delivery_refs=[*state["delivery_refs"], runtime.home.retain(record)],
        **changes,
    )


def _completion(runtime: Any, record: Mapping[str, Any]) -> dict[str, Any]:
    context = record["invocation"]
    view = runtime._run(context["run_id"])
    rows = [row for row in view["invocations"] if row["id"] == context["invocation_id"]]
    done = [
        row
        for row in view["completions"]
        if row["invocation_id"] == context["invocation_id"]
    ]
    if (
        len(rows) != 1
        or len(done) != 1
        or rows[0].get("writer_state") != "stopped"
        or done[0].get("effective_writer") != "stopped"
    ):
        raise vr.CicdV2Error("effect_writer_not_stopped")
    if any(
        rows[0].get(key) != context[key]
        for key in (
            "plane_id",
            "node",
            "attempt",
            "pid",
            "cwd",
            "argv",
            "deadline",
            "revision_id",
        )
    ):
        raise vr.CicdV2Error("effect_context_changed")
    proof = {"invocation": rows[0], "completion": done[0]}
    if (
        record["completion_ref"] is not None
        and runtime.home.read_ref(record["completion_ref"]) != proof
    ):
        raise vr.CicdV2Error("effect_completion_changed")
    if record["status"] == "accepted":
        result = runtime.home.read_ref(record["result_ref"])
        if (
            done[0].get("classification") != "semantic"
            or done[0].get("transport") != "completed"
            or done[0].get("outcome") != result["status"].upper()
        ):
            raise vr.CicdV2Error("effect_completion_changed")
    return proof


def _processes_closed(home: Any, operations: list[dict[str, Any]]) -> None:
    latest = {row["invocation"]["invocation_id"]: row for row in operations}
    for row in latest.values():
        if row["process_ref"] is None:
            raise vr.CicdV2Error("effect_process_closure_unknown")
        process = home.read_ref(row["process_ref"])
        facts = process["process_facts"]
        if not (
            facts["root_reaped"]
            and facts["stdout_eof"]
            and facts["stderr_eof"]
            and facts["group_state"] == "closed"
            and process["diagnostic"] != "writer_not_closed"
        ):
            raise vr.CicdV2Error("effect_process_closure_unknown")


def accept_observation(runtime: Any, state: dict[str, Any]) -> dict[str, Any]:
    _, operations = history(runtime.home, state)
    if not operations:
        return state
    last = operations[-1]
    completion = _completion(runtime, last)
    if last["status"] == "accepted":
        return state
    if last["status"] != "observed" or last["result_ref"] is None:
        return state  # Possible-send reservation remains inspect-only.
    done = completion["completion"]
    result = runtime.home.read_ref(last["result_ref"])
    if (
        done.get("classification") != "semantic"
        or done.get("transport") != "completed"
        or done.get("outcome") != result["status"].upper()
    ):
        return state
    return _append(
        runtime,
        state,
        last
        | {"status": "accepted", "completion_ref": runtime.home.retain(completion)},
    )


def disposition(home: Any, state: Mapping[str, Any]) -> str:
    _, operations = history(home, state)
    if not operations:
        return "prepare"
    last = operations[-1]
    # A later prepare cannot erase a possible execute. All call admission uses
    # this same chain, and an interrupted execute is always inspect-only.
    if last["status"] != "accepted":
        return (
            "inspect"
            if any(row["operation"] == "execute" for row in operations)
            else "held"
        )
    result = home.read_ref(last["result_ref"])
    if result["status"] == "applied":
        return "complete"
    if last["operation"] == "prepare" and result["status"] == "prepared":
        return "execute"
    if last["operation"] == "inspect" and result["status"] == "not_applied":
        return "execute"
    return (
        "inspect"
        if any(row["operation"] == "execute" for row in operations)
        else "held"
    )


def current_basis(
    runtime: Any, state: dict[str, Any], invocation: Mapping[str, Any]
) -> None:
    home = runtime.home
    delivery, _ = history(home, state)
    basis = home.read_ref(delivery["basis_ref"])
    report = runtime._report(state, None, "checked_patch")
    if (
        report["patch_ref"] != basis["patch_ref"]
        or report["source_after_sha256"] != basis["source_after_sha256"]
    ):
        raise vr.CicdV2Error("delivery_basis_changed")
    if is_local_git(home):
        if source_identity(home.observe()) != basis["source_after_sha256"]:
            raise vr.CicdV2Error("source_changed")
        completed = disposition(home, state) == "complete"
        expected = "applied" if completed else "not_applied"
        if local_git.observe(home, basis["target"], invocation["deadline"]) != expected:
            raise vr.CicdV2Error("delivery_git_changed")
    else:
        runtime._source(state, basis["source_after_sha256"])
        if git_facts(home, invocation) != basis["git"]:
            raise vr.CicdV2Error("delivery_git_changed")
    if (
        state["check_plan_ref"] != basis["check_plan_ref"]
        or state["review_plan_ref"] != basis["review_plan_ref"]
    ):
        raise vr.CicdV2Error("delivery_basis_changed")


def guard(
    runtime: Any, state: dict[str, Any], invocation: Mapping[str, Any]
) -> dict[str, Any]:
    state = accept_observation(runtime, state)
    current_basis(runtime, state, invocation)
    choice = disposition(runtime.home, state)
    stopping = [
        row
        for row in runtime.home.decisions(state["input_id"])
        if row["kind"] in {"cancel", "close"}
        and row["request_id"] not in state["applied_decision_ids"]
    ]
    if stopping:
        settlement_guard(runtime, state, invocation)
        report = (
            runtime.home.retain(runtime._report(state, None, "cancelled"))
            if stopping[0]["kind"] == "cancel"
            else state["hold_ref"]
        )
        runtime._save(state, settlement_ref=report, phase="settle")
        return {"outcome": "COMPLETE", "summary": "operator_stop", "refs": []}
    if choice == "complete":
        report = runtime.home.retain(runtime._report(state, None, "published"))
        runtime._save(state, settlement_ref=report, phase="settle")
        return {"outcome": "COMPLETE", "summary": "COMPLETE", "refs": []}
    if choice in {"prepare", "execute"}:
        return {
            "outcome": "PREPARE_REQUIRED" if choice == "prepare" else "AUTHORIZED",
            "summary": "exact_saved_effect",
            "refs": [],
        }
    return runtime._hold(state, "effect_inspection_required")


def operation(
    runtime: Any,
    name: str,
    invocation: dict[str, Any],
    supplied: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One operation. A durable execute reservation precedes any possible send."""
    from . import cicd_v2_checks as checks

    home = runtime.home
    with home.lock(), home.state_lock():
        task = runtime._active()
        if task is None:
            raise vr.CicdV2Error("active_input_missing")
        from .cicd_records import event_from_key

        state = runtime._state(event_from_key(task["intake_key"]))
        runtime._stopped(state, invocation)
        state = accept_observation(runtime, state)
        delivery, operations = history(home, state)
        _processes_closed(home, operations)
        choice = disposition(home, state)
        if name not in {"prepare", "execute", "inspect"}:
            raise vr.CicdV2Error("effect_operation_mismatch")
        if name != "inspect" and choice != name:
            raise vr.CicdV2Error("effect_inspection_required")
        if name == "inspect" and (
            choice != "inspect"
            or not any(row["operation"] == "execute" for row in operations)
        ):
            raise vr.CicdV2Error("effect_send_not_reserved")
        fresh = runtime.current(name + "_effect")
        if any(
            fresh[key] != invocation[key] for key in fresh if key != "public_run_ref"
        ):
            raise vr.CicdV2Error("effect_context_changed")
        if name != "inspect":
            if any(
                row["kind"] in {"cancel", "close"}
                and row["request_id"] not in state["applied_decision_ids"]
                for row in home.decisions(state["input_id"])
            ):
                raise vr.CicdV2Error("operator_decision_pending")
            current_basis(runtime, state, invocation)
        intent = home.read_ref(delivery["intent_ref"])
        request = validate_request(intent | {"operation": name}, name)
        if supplied is not None and supplied != request:
            raise vr.CicdV2Error("effect_intent_changed")
        command_id = home.config["external_policy"]["adapters"]["commit"][name]
        command = copy.deepcopy(
            next(row for row in home.config["commands"] if row["id"] == command_id)
        )
        request_ref = home.retain(request)
        stem = request["effect_id"] + "-" + invocation["invocation_id"]
        result_path = home.workspace / (stem + ".effect-result.json")
        if result_path.exists():
            raise vr.CicdV2Error("effect_operation_already_started")
        replacements = {
            "{request_file}": str(home.resolve_ref(request_ref)),
            "{result_file}": str(result_path),
        }
        command["argv"] = [replacements.get(arg, arg) for arg in command["argv"]]
        record = {
            "format": RETAINED_OPERATION,
            "delivery_id": delivery["delivery_id"],
            "intent_ref": delivery["intent_ref"],
            "operation": name,
            "request_ref": request_ref,
            "command_id": command_id,
            "command_sha256": cc.execution_sha256(
                cc.execution_of(command, policy=home.policy)
            ),
            "argv_sha256": vr.digest(command["argv"]),
            "invocation": invocation,
            "process_ref": None,
            "result_ref": None,
            "retained_evidence_refs": [],
            "completion_ref": None,
            "status": "reserved",
            "previous_ref": state["delivery_refs"][-1],
        }
        state = _append(
            runtime,
            state,
            record,
            phase={
                "prepare": "prepare_effect",
                "execute": "execute_effect",
                "inspect": "inspect_effect",
            }[name],
        )
    with checks.TermPropagation() as term:
        observed = checks.run_command(
            home.target,
            home.policy,
            command,
            home.guards,
            base_environment=os.environ,
            deadline=min(
                invocation["deadline"], time.time() + command["timeout_seconds"]
            ),
            cancelled=term.cancelled,
            on_started=term.started,
        )
        term.pid = None
    process = {
        key: value for key, value in observed.items() if key not in {"stdout", "stderr"}
    }
    process["stdout_ref"] = home.retain_bytes(observed["stdout"], ".stdout")
    process["stderr_ref"] = home.retain_bytes(observed["stderr"], ".stderr")
    result_ref = None
    retained_evidence_refs = []
    status = "uncertain"
    try:
        from .cicd_v2_runtime import _read

        raw = _read(result_path, limit=home.guards.get("input"))
        result, result_ref, retained_evidence_refs = _retain_result(home, raw, request)
        if observed["status"] == "passed":
            status = result["status"]
    except (OSError, ValueError, vr.CicdV2Error, CicdError, IntakeError):
        pass  # Preserve malformed/missing response and the possible-send record.
    with home.lock(), home.state_lock():
        latest = runtime._state(state["input_id"])
        if latest != state:
            raise vr.CicdV2Error("effect_state_changed")
        _append(
            runtime,
            state,
            record
            | {
                "status": "observed",
                "process_ref": home.retain(process),
                "result_ref": result_ref if status != "uncertain" else None,
                "retained_evidence_refs": (
                    retained_evidence_refs if status != "uncertain" else []
                ),
            },
        )
    outcome = {
        "prepared": "PREPARED",
        "applied": "APPLIED",
        "not_applied": "NOT_APPLIED",
        "pending": "PENDING",
        "refused": "REFUSED",
    }.get(status, "UNCERTAIN")
    return {"outcome": outcome, "summary": status, "refs": []}


def reconcile(runtime: Any, state: dict[str, Any]) -> dict[str, Any]:
    state = accept_observation(runtime, state)
    _, operations = history(runtime.home, state)
    try:
        _processes_closed(runtime.home, operations)
    except vr.CicdV2Error as exc:
        return runtime._hold(state, exc.code)
    choice = disposition(runtime.home, state)
    if choice == "inspect":
        # A completed uncertain inspection is held until an explicit resume;
        # reconcile is precisely that public resume boundary.
        return {
            "outcome": "EFFECT_UNKNOWN",
            "summary": "inspect_saved_effect",
            "refs": [],
        }
    if choice in {"prepare", "execute", "complete"}:
        return {"outcome": "DELIVERY_READY", "summary": "delivery_saved", "refs": []}
    return runtime._hold(state, "effect_preparation_uncertain")


def settlement_guard(
    runtime: Any,
    state: dict[str, Any],
    invocation: Mapping[str, Any],
    *,
    published: bool = False,
) -> None:
    if not state["delivery_refs"]:
        if published:
            raise vr.CicdV2Error("delivery_missing")
        return
    _, operations = history(runtime.home, state)
    _processes_closed(runtime.home, operations)
    for row in operations:
        _completion(runtime, row)
    choice = disposition(runtime.home, state)
    if not any(row["operation"] == "execute" for row in operations) and not published:
        return
    if choice not in {"complete", "execute", "prepare"}:
        raise vr.CicdV2Error("effect_unresolved")
    if published and choice != "complete":
        raise vr.CicdV2Error("effect_unresolved")
    # Not-applied inspection, or preparation before any execute, may close.
    if choice == "execute" and operations[-1]["operation"] == "execute":
        raise vr.CicdV2Error("effect_unresolved")
    if published:
        current_basis(runtime, state, invocation)


def publication_decision(home: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    delivery, _ = history(home, state)
    return home.read_ref(delivery["decision_ref"])


def operate_delivery(
    runtime: Any, request: dict[str, Any], invocation: dict[str, Any]
) -> dict[str, Any]:
    """Thin command facade; graph operations and public binding remain identical."""
    from datetime import UTC, datetime

    validate_request(request)
    operation(runtime, request["operation"], invocation, supplied=request)
    state = runtime.home.load_state(request["input_id"])
    _, operations = history(runtime.home, state)
    last = operations[-1]
    if last["result_ref"] is not None:
        return runtime.home.read_ref(last["result_ref"])
    return validate_result(
        {
            "format": RESULT,
            "delivery_id": request["delivery_id"],
            "effect_id": request["effect_id"],
            "request_sha256": vr.digest(request),
            "operation": request["operation"],
            "status": "uncertain",
            "remote_id": None,
            "observed_head": None,
            "artifact_sha256": None,
            "evidence_refs": [],
            "observed_at": datetime.now(UTC).isoformat(),
            "diagnostic": "effect_response_unavailable",
        },
        request,
    )
