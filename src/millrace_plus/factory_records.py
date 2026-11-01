"""Pure Factory records. Shape validation grants no process or source authority."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping

from . import cicd_v2_records as ci
from .check_contracts import CheckContractError, validate_ref
from .cicd_records import CicdError
from .local_intake import canonical, identity

__all__ = [
    "FactoryError",
    "INPUT",
    "CONFIG",
    "STATE",
    "BASE",
    "validate_input",
    "validate_config",
    "subject_id",
    "source_key",
    "integrity",
    "validate_state",
    "validate_base",
    "validate_record",
    "require_fields",
    "ref",
    "text",
]
INPUT = "millrace-plus-factory-input/v1"
CONFIG = "millrace-plus-factory-config/v1"
STATE = "millrace-plus-factory-state/v1"
BASE = "millrace-plus-factory-base/v1"


class FactoryError(CicdError):
    """A selected Factory authority, state or lifecycle refusal."""


def require_fields(value: object, names: str, code: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise FactoryError(code)
    return value


def text(value: object, code: str = "invalid_factory_record") -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactoryError(code)
    return value


def ref(value: object, code: str = "invalid_factory_ref") -> dict:
    try:
        return validate_ref(value)
    except CheckContractError as exc:
        raise FactoryError(code) from exc


def _refs(value: object, code: str) -> list:
    if not isinstance(value, list):
        raise FactoryError(code)
    for item in value:
        ref(item, code)
    if len({canonical(item) for item in value}) != len(value):
        raise FactoryError(code)
    return value


def _record(value: object, fmt: str, names: str) -> dict:
    obj = require_fields(value, "format " + names, "invalid_factory_record")
    if obj["format"] != fmt:
        raise FactoryError("unsupported_factory_format")
    return obj


def _ci(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (ci.CicdV2Error, CheckContractError, ValueError) as exc:
        raise FactoryError(getattr(exc, "code", "invalid_factory_record")) from exc


def validate_input(value: object) -> dict:
    """Validate an original subject, without a service, CI intake or filesystem read."""
    obj = _record(
        value,
        INPUT,
        "source_kind source_id repository base_candidate_sha256 "
        "request criteria feedback_ref evidence_refs scope_ref occurred_at",
    )
    kind = obj["source_kind"]
    if kind not in {"task", "ci_event", "user_report", "production_observation"}:
        raise FactoryError("invalid_factory_source")
    text(obj["source_id"])
    _ci(ci._repository, obj["repository"], "invalid_repository")
    _ci(ci._optional_sha, obj["base_candidate_sha256"], "invalid_factory_base")
    _ci(ci._time, obj["occurred_at"], "invalid_factory_time")
    _refs(obj["evidence_refs"], "invalid_factory_evidence")
    if obj["scope_ref"] is not None:
        ref(obj["scope_ref"])
    if not isinstance(obj["criteria"], list):
        raise FactoryError("invalid_factory_criteria")
    seen = set()
    for row in obj["criteria"]:
        row = require_fields(row, "id text origin_ref", "invalid_factory_criteria")
        identifier = text(row["id"])
        if identifier in seen:
            raise FactoryError("duplicate_factory_criterion")
        seen.add(identifier)
        text(row["text"])
        if row["origin_ref"] is not None:
            ref(row["origin_ref"])
    if kind == "task":
        text(obj["request"], "blank_factory_request")
        if not seen or obj["feedback_ref"] is not None:
            raise FactoryError("invalid_factory_task")
    elif obj["request"] is not None or seen or obj["feedback_ref"] is None:
        raise FactoryError("invalid_factory_feedback")
    else:
        ref(obj["feedback_ref"])
    return obj


def subject_id(value: object) -> str:
    """Full immutable input digest, distinct from replay's original source identity."""
    return hashlib.sha256(canonical(validate_input(value))).hexdigest()


def source_key(value: object) -> str:
    obj = validate_input(value)
    raw = hashlib.sha256(
        canonical({key: obj[key] for key in ("source_kind", "source_id", "repository")})
    ).digest()
    # Preserve the full digest within Intake's existing identifier shape.
    return "f_" + base64.b32encode(raw).decode("ascii").rstrip("=")


def assignment_sha256(value: dict) -> str:
    """Assignment identity excludes source rekeying, timestamps and evidence."""
    subject = validate_input(value)
    return ci.digest(
        {
            key: subject[key]
            for key in (
                "source_kind",
                "repository",
                "request",
                "criteria",
                "feedback_ref",
                "scope_ref",
            )
        }
    )


def validate_config(value: object) -> dict:
    obj = _record(
        value,
        CONFIG,
        "repository development_lane validation_lane anchor "
        "scope_policy check_policy role_profile provider_wait_policy "
        "delivery_policy local_authority timeouts resource_policy path_policy_ref",
    )
    _ci(ci._repository, obj["repository"], "invalid_repository")
    dev = require_fields(
        obj["development_lane"],
        "service_home target_root "
        "service_config_ref worker_definition os_profile target_identity "
        "service_id parent_instance_id",
        "invalid_development_lane",
    )
    val = require_fields(
        obj["validation_lane"],
        "workflow_home target_root config_ref target_identity service_id",
        "invalid_validation_lane",
    )
    for lane, fields in [
        (dev, ("service_home", "target_root")),
        (val, ("workflow_home", "target_root")),
    ]:
        for name in fields:
            _ci(ci._absolute, lane[name], "invalid_lane_path")
        identity(lane["service_id"])
        require_fields(
            lane["target_identity"], "path device inode", "invalid_target_identity"
        )
        for number in (
            lane["target_identity"]["device"],
            lane["target_identity"]["inode"],
        ):
            if type(number) is not int or number < 0:
                raise FactoryError("invalid_target_identity")
    identity(dev["parent_instance_id"])
    for item in (dev["service_config_ref"], dev["os_profile"], val["config_ref"]):
        # OS profile is an existing canonical local operator path/hash pair.
        if item is dev["os_profile"]:
            require_fields(item, "path sha256", "invalid_os_profile")
            _ci(ci._absolute, item["path"], "invalid_os_profile")
            _ci(ci._sha, item["sha256"], "invalid_os_profile")
        else:
            ref(item)
    definition = require_fields(
        dev["worker_definition"], "selector version sha256", "invalid_worker_definition"
    )
    for name in ("selector", "version"):
        text(definition[name])
    _ci(ci._sha, definition["sha256"], "invalid_worker_definition")
    anchor = require_fields(
        obj["anchor"],
        "repository object_format head_sha tree_sha",
        "invalid_factory_anchor",
    )
    if anchor["repository"] != obj["repository"] or anchor["object_format"] not in {
        "sha1",
        "sha256",
    }:
        raise FactoryError("invalid_factory_anchor")
    for name in ("head_sha", "tree_sha"):
        _ci(ci._revision, anchor[name], "invalid_factory_anchor")
        if len(anchor[name]) != (40 if anchor["object_format"] == "sha1" else 64):
            raise FactoryError("invalid_factory_anchor")
    scope = require_fields(
        obj["scope_policy"],
        "write_paths protected_paths risk_policy_ref",
        "invalid_factory_scope",
    )
    for name in ("write_paths", "protected_paths"):
        _ci(
            ci._paths,
            scope[name],
            "invalid_factory_scope",
            nonempty=name == "write_paths",
        )
    ref(scope["risk_policy_ref"])
    checks = require_fields(
        obj["check_policy"],
        "engineering_profile_ref "
        "requirements_snapshot_ref ci_policy_ref lq_command_map_ref",
        "invalid_factory_checks",
    )
    ref(checks["ci_policy_ref"])
    if (checks["engineering_profile_ref"] is None) != (
        checks["requirements_snapshot_ref"] is None
    ) or (checks["engineering_profile_ref"] is None) != (
        checks["lq_command_map_ref"] is None
    ):
        raise FactoryError("factory_engineering_mapping_required")
    for item in checks.values():
        if item is not None:
            ref(item)
    role = require_fields(
        obj["role_profile"],
        "profile_ref require_independent_review_family",
        "invalid_factory_roles",
    )
    ref(role["profile_ref"])
    if type(role["require_independent_review_family"]) is not bool:
        raise FactoryError("invalid_factory_roles")
    wait = require_fields(
        obj["provider_wait_policy"], "mode policy_ref", "invalid_factory_provider_wait"
    )
    if wait["mode"] not in {"ordinary_recovery", "capability_required"}:
        raise FactoryError("invalid_factory_provider_wait")
    if wait["mode"] == "capability_required":
        # L-R2 is unselected: a captured locator never creates that capability.
        raise FactoryError("factory_provider_wait_capability_unavailable")
    if wait["policy_ref"] is not None:
        ref(wait["policy_ref"])
    delivery = require_fields(
        obj["delivery_policy"],
        "mode config_ref authority_ref",
        "invalid_factory_delivery",
    )
    if delivery != {"mode": "local", "config_ref": None, "authority_ref": None}:
        raise FactoryError("unsupported_until_factory_f2")
    authority = require_fields(
        obj["local_authority"],
        "authority_ref allowed_operations",
        "invalid_factory_authority",
    )
    ref(authority["authority_ref"])
    allowed = _ci(ci._ids, authority["allowed_operations"], "invalid_factory_authority")
    if set(allowed) - {
        "reproduce",
        "ci_repair",
        "lad_task",
        "materialize_validation",
        "restore_validation",
        "synchronize_development",
        "restore_development",
    }:
        raise FactoryError("invalid_factory_authority")
    timeout = require_fields(
        obj["timeouts"],
        "node_deadlines observation_windows effect_observation",
        "invalid_factory_timeouts",
    )
    for key in ("node_deadlines", "observation_windows"):
        if not isinstance(timeout[key], dict):
            raise FactoryError("invalid_factory_timeouts")
        for name, policy in timeout[key].items():
            text(name)
            policy = require_fields(
                policy, "seconds origin reason override", "invalid_factory_timeouts"
            )
            _ci(ci._seconds, policy["seconds"], "invalid_factory_timeouts")
            for field in ("origin", "reason", "override"):
                text(policy[field])
    if timeout["effect_observation"] is not None:
        raise FactoryError("unsupported_until_factory_f2")
    _ci(ci._resource_policy, obj["resource_policy"])
    ref(obj["path_policy_ref"])
    return obj


def integrity(value: Mapping) -> dict:
    result = dict(value)
    result["integrity_sha256"] = hashlib.sha256(
        canonical({k: v for k, v in result.items() if k != "integrity_sha256"})
    ).hexdigest()
    return result


RECORDS = {
    STATE: "factory_id job_id input_ref claim_ref baseline_ref reservations_ref "
    "accepted_refs decision_refs phase version integrity_sha256",
    BASE: "factory_id sequence previous_ref candidate_ref source_binding_ref "
    "outcome_ref requirements_snapshot_ref integrity_sha256",
    "millrace-plus-factory-claim/v1": "factory_id job_id input_sha256 config_sha256 "
    "base_candidate_ref scope_ref parent_instance_id parent_run_id",
    "millrace-plus-factory-reservations/v1": "job_id reservations integrity_sha256",
    "millrace-plus-factory-baseline/v1": "job_id anchor accepted_candidate_ref "
    "development_ref validation_ref index_ref protected_dirty_ref scope_ref "
    "requirements_snapshot_ref",
    "millrace-plus-factory-scope/v1": "job_id input_ref config_ref write_paths "
    "protected_paths change_classes authority_ref requirements_ref rationale_refs",
    "millrace-plus-factory-obligations/v1": "job_id base_ref lq_snapshot_ref "
    "accepted_input_refs requirements integrity_sha256",
    "millrace-plus-factory-candidate-binding/v1": "factory_id job_id candidate_ref "
    "request_sha256 scope_ref producer development_before_ref protected_dirty_ref "
    "closure_refs source_binding_ref created_at",
    "millrace-plus-factory-lad-binding/v1": "factory_id job_id service_id "
    "service_config_sha256 task_id request_sha256 claim_ref call_home call_key "
    "definition parent_ref child_ref terminal_ref join_refs",
    "millrace-plus-factory-lad-parent-resume/v1": (
        "factory_id job_id service_id "
        "task_ref "
        "parent_instance_id parent_run_id request_ref request_sha256 state "
        "receipt_ref join_refs"
    ),
    "millrace-plus-factory-lad-request/v1": "request_id factory_id job_id operation "
    "lane_ref input_ref task_ref candidate_ref authority_ref control_ref evidence_refs",
    "millrace-plus-factory-lad-result/v1": "request_id request_sha256 operation job_id "
    "task_ref status artifact_refs closure_refs refusal",
    "millrace-plus-factory-outcome/v1": "factory_id job_id input_sha256 candidate_ref "
    "accepted_base_ref verification_refs review_ref delivery_refs "
    "health_refs rollback_refs "
    "incident_refs closure_refs restoration_ref engineering_refs terminal_receipt_ref "
    "status reason guidance_candidate_ref",
    "millrace-plus-factory-disposition/v1": "job_id input_ref candidate_ref "
    "prior_base_ref "
    "verification_refs review_ref engineering_refs delivery_refs closure_refs "
    "restoration_ref synchronization_ref status reason",
}


def validate_record(value: object, fmt: str) -> dict:
    """Validate outer shape and links. Consumers check actual cross-record facts."""
    if fmt not in RECORDS:
        raise FactoryError("unsupported_factory_format")
    obj = _record(value, fmt, RECORDS[fmt])
    for key, item in obj.items():
        if key in {
            "factory_id",
            "job_id",
            "service_id",
            "parent_instance_id",
            "parent_run_id",
        }:
            identity(item)
        elif key.endswith("_ref") and item is not None:
            ref(item)
        elif key.endswith("_refs") and isinstance(item, list):
            _refs(item, "invalid_factory_refs")
        elif key.endswith("_sha256"):
            _ci(ci._sha, item, "invalid_factory_digest")
    if "integrity_sha256" in obj and integrity(obj) != obj:
        raise FactoryError("factory_integrity_mismatch")
    for key in ("sequence", "version"):
        if key in obj and (type(obj[key]) is not int or obj[key] < 1):
            raise FactoryError("invalid_factory_version")
    if fmt == "millrace-plus-factory-outcome/v1":
        text(obj["reason"], "invalid_factory_outcome")
        if obj["status"] not in {
            "no_action",
            "local_candidate",
            "delivered_healthy",
            "rolled_back",
            "held_evidence",
            "held_human",
            "cancelled",
            "uncertain",
        }:
            raise FactoryError("invalid_factory_outcome")
        engineering = require_fields(
            obj["engineering_refs"],
            "profile_ref snapshot_ref obligations_ref check_refs lad_acceptance_ref",
            "invalid_factory_engineering_refs",
        )
        for key, item in engineering.items():
            if key == "check_refs":
                _refs(item, "invalid_factory_engineering_refs")
            elif item is not None:
                ref(item)
    if fmt == "millrace-plus-factory-reservations/v1":
        rows = obj["reservations"]
        if not isinstance(rows, list):
            raise FactoryError("invalid_factory_reservations")
        seen = set()
        for row in rows:
            require_fields(
                row,
                "id operation input_sha256 scope_sha256 base_candidate_id "
                "expected_attempt producer_context definition_ref profile_ref "
                "request_ref "
                "result_ref completion_ref acceptance_ref state",
                "invalid_factory_reservation",
            )
            text(row["id"])
            if row["id"] in seen or row["operation"] not in {
                "investigate",
                "repair",
                "review",
            }:
                raise FactoryError("invalid_factory_reservation")
            seen.add(row["id"])
            for field in ("input_sha256", "scope_sha256", "base_candidate_id"):
                _ci(ci._sha, row[field], "invalid_factory_reservation")
            for field in ("definition_ref", "profile_ref", "request_ref", "result_ref"):
                ref(row[field])
            if type(row["expected_attempt"]) is not int or row["expected_attempt"] < 1:
                raise FactoryError("invalid_factory_reservation")
            if row["state"] not in {"reserved", "accepted"}:
                raise FactoryError("invalid_factory_reservation")
            for field in ("completion_ref", "acceptance_ref"):
                if row[field] is not None:
                    ref(row[field])
                if (row[field] is not None) != (row["state"] == "accepted"):
                    raise FactoryError("invalid_factory_reservation")
            if not isinstance(row["producer_context"], dict):
                raise FactoryError("invalid_factory_reservation")
    if fmt == STATE:
        text(obj["phase"])
        if not isinstance(obj["accepted_refs"], dict):
            raise FactoryError("invalid_factory_accepted_refs")
        for item in obj["accepted_refs"].values():
            ref(item)
    return obj


def validate_state(value: object) -> dict:
    return validate_record(value, STATE)


def validate_base(value: object) -> dict:
    obj = validate_record(value, BASE)
    if (obj["sequence"] == 1) != (obj["previous_ref"] is None):
        raise FactoryError("factory_base_lineage_mismatch")
    if (obj["sequence"] == 1) != (obj["outcome_ref"] is None):
        raise FactoryError("factory_base_outcome_mismatch")
    return obj
