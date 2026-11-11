"""CI-D1a strict protocol and exact authority refusal cases, all offline."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest
from cicd_support import git, new_id, write_json
from test_cicd_v2_records import _ref, config

from millrace_plus import cicd_effects as effects
from millrace_plus.cicd_v2_records import DECISION, CicdV2Error, digest, validate_config
from millrace_plus.local_intake import IntakeError, canonical, strict_json


def configure_effects(root: Path, scenario: str = "normal"):
    """An operator-selected fake peer, captured before normal prepare/init."""

    def configure(value):
        authority = write_json(
            root / "effect-authority.json",
            {
                "issuer": "offline fixture operator",
                "purpose": "fake ledger only",
                "target_root": value["target_root"],
            },
        )
        capability = write_json(
            root / "effect-capability.json",
            {
                "adapter": "offline fake ledger",
                "capability": "exact_inspection",
                "evidence": "test_cicd_effects_workflow public graph journeys",
            },
        )
        from millrace_plus import check_contracts as cc

        base = value["commands"][0]
        for operation in ("prepare", "execute", "inspect"):
            command = copy.deepcopy(base)
            command.update(
                id="effect-" + operation,
                expected_products=[],
                timeout_seconds=20,
                argv=[
                    sys.executable,
                    str(Path(__file__).parent / "fixtures/cicd_effect_peer.py"),
                    "--ledger",
                    str(root / "effect-ledger"),
                    "--scenario",
                    scenario,
                    "{request_file}",
                    "{result_file}",
                ],
            )
            value["commands"].append(command)
        value["external_policy"] = {
            "mode": "authorized",
            "effects": [
                {
                    "kind": "commit",
                    "target": {
                        "repository_root": value["target_root"],
                        "branch": git(
                            Path(value["target_root"]),
                            "symbolic-ref",
                            "--short",
                            "HEAD",
                        ).strip(),
                        "mode": "working_tree",
                    },
                }
            ],
            "adapters": {
                "commit": {
                    "prepare": "effect-prepare",
                    "execute": "effect-execute",
                    "inspect": "effect-inspect",
                    "capabilities": [
                        {
                            "id": "exact_inspection",
                            "evidence_ref": cc.make_ref(
                                capability.name, capability.read_bytes()
                            ),
                        }
                    ],
                }
            },
            "authority_ref": cc.make_ref(authority.name, authority.read_bytes()),
            "wait_policy_ref": None,
        }

    return configure


def approval_decision(home, input_id):
    state = home.load_state(input_id)
    basis = home.read_ref(home.status(input_id)["inputs"][0]["delivery_basis_ref"])
    shared = {
        "issuer": "offline fixture operator",
        "input_id": input_id,
        "candidate_id": None,
        "patch_sha256": basis["patch_ref"]["sha256"],
        "source_after_sha256": basis["source_after_sha256"],
        "config_sha256": home.config_sha256,
        "effects": [{"kind": "commit", "target": basis["target"]}],
        "authority_ref": basis["authority_ref"],
    }
    approval = effects.validate_approval(
        {
            "format": effects.APPROVAL,
            "approval_id": new_id(),
            **shared,
            "evidence_refs": [],
        }
    )
    permission = effects.validate_permission(
        {
            "format": effects.PERMISSION,
            "permission_id": new_id(),
            **shared,
            "approval_ref": home.retain(approval),
        }
    )
    return {
        "format": DECISION,
        "request_id": new_id(),
        "input_id": input_id,
        "expected_state_version": state["version"],
        "kind": "approve_delivery",
        "basis_sha256": state["hold_ref"]["sha256"],
        "target_stage": None,
        "changed_cause_ref": None,
        "permission_ref": home.retain(permission),
        "evidence_refs": [],
    }


def _target():
    return {
        "repository_root": "/srv/target",
        "branch": "main",
        "base_head": "a" * 40,
        "mode": "working_tree",
        "patch_sha256": "b" * 64,
        "source_after_sha256": "c" * 64,
    }


def _permission():
    return {
        "format": effects.PERMISSION,
        "permission_id": "permission",
        "issuer": "operator",
        "input_id": "i",
        "candidate_id": None,
        "patch_sha256": "b" * 64,
        "source_after_sha256": "c" * 64,
        "config_sha256": "d" * 64,
        "effects": [{"kind": "commit", "target": _target()}],
        "authority_ref": _ref("authority.json"),
        "approval_ref": _ref("approval.json"),
    }


def _request(operation="execute"):
    permission = _permission()
    request = {
        "format": effects.REQUEST,
        "operation": operation,
        "delivery_id": "e" * 64,
        "effect_id": "0" * 64,
        "kind": "commit",
        "input_id": "i",
        "candidate_id": None,
        "patch_sha256": permission["patch_sha256"],
        "source_after_sha256": permission["source_after_sha256"],
        "config_sha256": permission["config_sha256"],
        "permission_ref": _ref("permission.json"),
        "target": _target(),
        "prior_effect_refs": [],
        "evidence_refs": [],
    }
    request["effect_id"] = effects.effect_identity(request)
    return request


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(candidate_id="also-candidate"),
        lambda p: p.update(input_id=None),
        lambda p: p.update(extra="unselected"),
        lambda p: p.update(config_sha256="changed"),
        lambda p: p["effects"][0].update(kind="push"),
        lambda p: p["effects"][0]["target"].update(mode="retained_tree"),
    ],
)
def test_permission_is_strict_and_subject_is_xor(mutate):
    value = _permission()
    assert effects.validate_permission(copy.deepcopy(value)) == value
    mutate(value)
    with pytest.raises(CicdV2Error):
        effects.validate_permission(value)


@pytest.mark.parametrize(
    "key",
    ["target", "permission_ref", "patch_sha256", "source_after_sha256", "effect_id"],
)
def test_changed_effect_intent_refuses_without_new_identity(key):
    request = _request()
    if key == "target":
        request["target"]["branch"] = "other"
    elif key == "permission_ref":
        request[key] = _ref("other-permission.json")
    else:
        request[key] = "f" * 64
    with pytest.raises(CicdV2Error):
        effects.validate_request(request)


def test_operation_changes_preserve_effect_identity_and_saved_execute_intent():
    request = _request()
    assert effects.validate_request(request) == request
    for operation in ("prepare", "inspect"):
        observed = request | {"operation": operation}
        assert effects.validate_request(observed)["effect_id"] == request["effect_id"]
        assert digest(observed) != digest(request)


@pytest.mark.parametrize("candidate_subject", [False, True])
def test_candidate_or_retained_tree_request_refuses_before_dispatch(candidate_subject):
    request = _request()
    if candidate_subject:
        request.update(input_id=None, candidate_id="a" * 64)
    else:
        request["target"]["mode"] = "retained_tree"
    request["effect_id"] = effects.effect_identity(request)
    with pytest.raises(CicdV2Error, match="unsupported_until_full_ci_d1"):
        effects.validate_request(request)


@pytest.mark.parametrize(
    ("operation", "status"),
    [("prepare", "applied"), ("inspect", "prepared"), ("inspect", "refused")],
)
def test_result_operation_status_refuses(operation, status):
    request = _request(operation)
    result = {
        "format": effects.RESULT,
        "delivery_id": request["delivery_id"],
        "effect_id": request["effect_id"],
        "request_sha256": digest(request),
        "operation": operation,
        "status": status,
        "remote_id": None,
        "observed_head": None,
        "artifact_sha256": None,
        "evidence_refs": [],
        "observed_at": "2026-11-11T00:00:00Z",
        "diagnostic": None,
    }
    with pytest.raises(CicdV2Error):
        effects.validate_result(result, request)


def test_applied_and_absence_require_exact_evidence():
    request = _request("inspect")
    result = {
        "format": effects.RESULT,
        "delivery_id": request["delivery_id"],
        "effect_id": request["effect_id"],
        "request_sha256": digest(request),
        "operation": "inspect",
        "status": "applied",
        "remote_id": "fake",
        "observed_head": "a" * 40,
        "artifact_sha256": None,
        "evidence_refs": [],
        "observed_at": "2026-11-11T00:00:00Z",
        "diagnostic": None,
    }
    with pytest.raises(CicdV2Error, match="effect_evidence_missing"):
        effects.validate_result(result, request)
    proof = {
        "format": effects.EVIDENCE,
        "effect_id": request["effect_id"],
        "intent_sha256": digest(request | {"operation": "execute"}),
        "target_sha256": digest(request["target"]),
        "status": "applied",
        "remote_id": "fake",
        "observed_head": "a" * 40,
    }

    class Home:
        def resolve_ref(self, ref):
            return Path(ref["path"])

        def read_ref(self, ref):
            return proof

    result["evidence_refs"] = [_ref("proof.json")]
    effects._result_evidence(Home(), effects.validate_result(result, request), request)
    proof["target_sha256"] = "f" * 64
    with pytest.raises(CicdV2Error, match="effect_evidence_mismatch"):
        effects._result_evidence(Home(), result, request)


def _result_files(tmp_path):
    from millrace_plus.cicd_v2_runtime import V2Home

    home = object.__new__(V2Home)
    home.workspace = tmp_path
    request = _request("inspect")
    proof = {
        "format": effects.EVIDENCE,
        "effect_id": request["effect_id"],
        "intent_sha256": digest(request | {"operation": "execute"}),
        "target_sha256": digest(request["target"]),
        "status": "applied",
        "remote_id": "fake",
        "observed_head": "a" * 40,
    }
    proof_path = write_json(tmp_path / "adapter-proof.json", proof)
    detail_path = tmp_path / "adapter-detail.txt"
    detail_path.write_bytes(b"raw adapter evidence\n")
    result = {
        "format": effects.RESULT,
        "delivery_id": request["delivery_id"],
        "effect_id": request["effect_id"],
        "request_sha256": digest(request),
        "operation": "inspect",
        "status": "applied",
        "remote_id": "fake",
        "observed_head": "a" * 40,
        "artifact_sha256": None,
        "evidence_refs": [home.file_ref(proof_path), home.file_ref(detail_path)],
        "observed_at": "2026-11-11T00:00:00Z",
        "diagnostic": None,
    }
    # An adapter may emit valid noncanonical JSON. Its raw identity must survive.
    raw = json.dumps(result, indent=2).encode() + b"\n"
    return home, request, result, raw


def test_result_retains_all_proofs_without_changing_adapter_identity(tmp_path):
    home, request, result, raw = _result_files(tmp_path)
    original = copy.deepcopy(result)
    checked, result_ref, mappings = effects._retain_result(home, raw, request)
    assert checked == result == original
    assert home.resolve_ref(result_ref).read_bytes() == raw
    assert result_ref["sha256"] != digest(result)
    assert [row["original_ref"] for row in mappings] == result["evidence_refs"]
    for row in mappings:
        original_ref, retained_ref = row["original_ref"], row["retained_ref"]
        assert retained_ref == {
            "path": original_ref["sha256"] + ".effect-evidence.bin",
            "sha256": original_ref["sha256"],
        }
        assert home.resolve_ref(original_ref).read_bytes() == home.resolve_ref(
            retained_ref
        ).read_bytes()
    home.resolve_ref(result["evidence_refs"][0]).unlink()
    home.resolve_ref(result["evidence_refs"][1]).write_bytes(b"changed adapter detail")
    effects._result_evidence(home, checked, request, mappings)
    assert home.read_ref(result_ref) == original


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_required_retained_proof_never_falls_back_to_valid_original(tmp_path, damage):
    home, request, result, raw = _result_files(tmp_path)
    _, _, mappings = effects._retain_result(home, raw, request)
    retained = home.resolve_ref(mappings[1]["retained_ref"])
    if damage == "missing":
        retained.unlink()
    else:
        retained.write_bytes(b"corrupt retained detail")
    # Legacy reads retain original-path semantics and do not repair v2 copies.
    effects._result_evidence(home, result, request)
    with pytest.raises((OSError, CicdV2Error)):
        effects._result_evidence(home, result, request, mappings)
    if damage == "missing":
        assert not retained.exists()
    else:
        assert retained.read_bytes() == b"corrupt retained detail"


def test_partial_proof_retention_failure_does_not_publish_result(tmp_path, monkeypatch):
    home, request, result, raw = _result_files(tmp_path)
    retain_bytes = home.retain_bytes
    copies = []

    def fail_second_proof(proof, suffix=".bin"):
        if suffix == ".effect-evidence.bin":
            copies.append(proof)
            if len(copies) == 2:
                raise OSError("fixture retention unavailable")
        return retain_bytes(proof, suffix)

    monkeypatch.setattr(home, "retain_bytes", fail_second_proof)
    with pytest.raises(OSError, match="fixture retention unavailable"):
        effects._retain_result(home, raw, request)
    assert len(copies) == 2
    assert not (tmp_path / (hashlib.sha256(raw).hexdigest() + ".json")).exists()
    assert len(list(tmp_path.glob("*.effect-evidence.bin"))) == 1
    effects._result_evidence(home, result, request)


@pytest.mark.parametrize(
    "change", ["missing", "reordered", "original", "copy", "extra"]
)
def test_retained_mapping_requires_exact_order_coverage_and_fields(tmp_path, change):
    home, request, result, raw = _result_files(tmp_path)
    _, _, mappings = effects._retain_result(home, raw, request)
    if change == "missing":
        mappings.pop()
    elif change == "reordered":
        mappings.reverse()
    elif change == "original":
        mappings[0]["original_ref"] = mappings[1]["original_ref"]
    elif change == "copy":
        mappings[0]["retained_ref"] = mappings[0]["original_ref"]
    else:
        mappings[0]["unknown"] = True
    with pytest.raises(CicdV2Error, match="invalid_retained_effect_evidence"):
        effects._result_evidence(home, result, request, mappings)


def test_operation_record_discriminator_keeps_legacy_fieldset_strict():
    legacy = dict.fromkeys(
        "format delivery_id intent_ref previous_ref operation request_ref "
        "command_id command_sha256 argv_sha256 invocation process_ref "
        "result_ref completion_ref status".split()
    )
    legacy["format"] = effects.OPERATION
    retained = legacy | {
        "format": effects.RETAINED_OPERATION,
        "retained_evidence_refs": [],
    }
    assert effects._operation_record(legacy) == legacy
    assert effects._operation_record(retained) == retained
    for malformed in (
        legacy | {"retained_evidence_refs": []},
        legacy | {"format": effects.RETAINED_OPERATION},
        retained | {"unknown": True},
        legacy | {"format": "unknown"},
    ):
        with pytest.raises(CicdV2Error, match="invalid_effect_operation"):
            effects._operation_record(malformed)


def test_legacy_evidence_read_still_requires_original_and_never_migrates(tmp_path):
    home, request, result, _ = _result_files(tmp_path)
    before = set(tmp_path.iterdir())
    effects._result_evidence(home, result, request)
    assert set(tmp_path.iterdir()) == before
    home.resolve_ref(result["evidence_refs"][0]).unlink()
    with pytest.raises(OSError):
        effects._result_evidence(home, result, request)
    assert not list(tmp_path.glob("*.effect-evidence.bin"))


def test_default_and_empty_authorized_policy_never_select_effects():
    value = config()
    assert validate_config(copy.deepcopy(value)) == value
    value["external_policy"].update(
        mode="authorized", authority_ref=_ref("operator.json")
    )
    assert validate_config(copy.deepcopy(value)) == value
    with pytest.raises(IntakeError, match="invalid_json"):
        strict_json(canonical(_permission())[:-1] + b',"issuer":"duplicate"}')


def test_outer_stop_does_not_replace_missing_nested_process_closure():
    process = {
        "process_facts": {
            "root_reaped": True,
            "stdout_eof": True,
            "stderr_eof": True,
            "group_state": "closed",
        },
        "diagnostic": None,
    }

    class Home:
        def read_ref(self, ref):
            return process

    reserved = {"invocation": {"invocation_id": "exact"}, "process_ref": None}
    with pytest.raises(CicdV2Error, match="effect_process_closure_unknown"):
        effects._processes_closed(Home(), [reserved])
    observed = reserved | {"process_ref": _ref("process.json")}
    effects._processes_closed(Home(), [reserved, observed])
    process["process_facts"]["group_state"] = "unknown"
    with pytest.raises(CicdV2Error, match="effect_process_closure_unknown"):
        effects._processes_closed(Home(), [reserved, observed])
