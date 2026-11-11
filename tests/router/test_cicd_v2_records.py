"""CI v2 config records: CI-R1 subset, deterministic selection and budgets.

The config fixture is a complete `millrace-plus-cicd-config/v2` object. Cases
mutate one authority field at a time and assert the exact refusal; selection
and budget cases assert the spec 7.2 arithmetic, not an implementation echo.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json

import pytest

from millrace_plus import check_contracts as cc
from millrace_plus.cicd_v2_records import (
    ATTEMPT,
    CANDIDATE,
    CANDIDATE_REQUEST,
    CHANGE_CLASSES,
    CHECK,
    CONFIG,
    DECISION,
    DELTA,
    MATERIALIZATION,
    MATERIALIZATION_OPERATION,
    TREE,
    CicdV2Error,
    candidate_identity,
    check_node_budget,
    config_digest,
    hold_external_policy,
    reproduction_node_budget,
    require_ci_r1_decision,
    select_checks,
    tree_digest,
    validate_attempt,
    validate_candidate,
    validate_candidate_request,
    validate_check,
    validate_config,
    validate_decision,
    validate_delta,
    validate_materialization,
    validate_materialization_operation,
    validate_tree,
)

REASON = {"reason": "test", "origin": "test fixture", "override": "new capture"}
POLICY_RAW = cc.stock_path_policy()
POLICY_REF = cc.make_ref("path-policy.json", POLICY_RAW)


def _ref(name: str) -> dict[str, object]:
    return {"path": name, "sha256": hashlib.sha256(name.encode()).hexdigest()}


def _command(identifier: str, seconds: int) -> dict[str, object]:
    return {
        "id": identifier,
        "argv": ["python3", f"checks/{identifier}.py"],
        "cwd": ".",
        "environment": {"inherit_names": ["PATH"], "set": {}},
        "timeout_seconds": seconds,
        "timeout_policy": REASON,
        "expected_exit_code": 0,
        "path_policy_ref": POLICY_REF,
        "expected_products": [],
    }


def _selection(identifier: str, adapter: str = "command") -> dict[str, object]:
    contract = {
        "command": "millrace-command/v1",
        "droid_exec": "millrace-droid-exec/v1",
        "codex_exec": "millrace-codex-exec/v1",
    }[adapter]
    native = adapter != "command"
    return {
        "id": identifier,
        "binding": {
            "adapter": adapter,
            "contract": contract,
            "executable": "tool",
            "config": {},
        },
        "profile_ref": _ref(f"profiles/{identifier}.json") if native else None,
        "prompt_ref": _ref(f"prompts/{identifier}.md"),
        "skill_ref": _ref(f"skills/{identifier}.md"),
        "result_schema_ref": _ref(f"schemas/{identifier}.json"),
    }


def config() -> dict[str, object]:
    return {
        "format": CONFIG,
        "target_root": "/srv/target",
        "repository": "acme/calc",
        "source_policy": {
            "mode": "standalone",
            "known_binding_refs": [],
            "trusted_preparer_command_id": None,
        },
        "path_policy_ref": POLICY_REF,
        "local_authority_ref": _ref("local-authority.json"),
        "node_deadlines": {
            "checks": {"bookkeeping_seconds": 30} | REASON,
            "wait_effect": None,
            "wait_rollback": None,
            "wait_event": None,
        },
        "repair_paths": ["src"],
        "protected_paths": ["src/auth"],
        "protected_change_classes": list(CHANGE_CLASSES),
        "class_exceptions": [],
        "commands": [
            _command("repro", 120),
            _command("unit", 300),
            _command("docs", 45),
            _command("lint", 60),
            _command("probe", 20),
        ],
        "reproduction": {
            "command_ids": ["repro"],
            "failure_patterns_ref": _ref("failure-patterns.json"),
            "failure_matcher": {
                "kind": "exit_and_evidence",
                "failing_exit_codes": [1],
                "evidence_pattern_ids": ["assert"],
            },
        },
        "investigation": {"probe_command_ids": ["probe"]},
        "check_policy": {
            "mandatory_ids": ["unit"],
            "path_rules": [
                {"prefix": "src", "command_ids": ["unit"]},
                {"prefix": "docs", "command_ids": ["docs"]},
            ],
            "shared_contract_paths": ["src/contracts"],
            "full_ids": ["unit", "docs", "lint"],
        },
        "role_bindings": {
            "investigate": _selection("investigate"),
            "repair": _selection("repair"),
            "review": [_selection("review")],
        },
        "repair_policy": {"allowed_change_classes": [], "optional_caps": []},
        "resource_policy": {
            "guards": [
                {"surface": "command.stdout", "measure": "bytes", "value": 65536}
                | REASON,
                {"surface": "command.stderr", "measure": "bytes", "value": 65536}
                | REASON,
            ]
        },
        "external_policy": hold_external_policy(),
        "health_policy": None,
        "quality_policy": None,
    }


def _refused(value: dict[str, object], code: str) -> None:
    with pytest.raises(CicdV2Error) as refused:
        validate_config(json.loads(json.dumps(value)))
    assert refused.value.code == code


def test_complete_config_validates_and_identity_is_exact_bytes() -> None:
    value = config()
    policy = cc.validate_path_policy(cc.parse(POLICY_RAW))
    assert validate_config(copy.deepcopy(value), policy=policy) == value
    raw = cc.encode(value)
    assert config_digest(raw) == hashlib.sha256(raw).hexdigest()
    # Any byte difference is a different identity, even if semantically equal.
    assert config_digest(raw + b"\n") != config_digest(raw)


def test_static_check_budget_sums_the_unique_maximum_selectable_set() -> None:
    budget = check_node_budget(config())
    # full ∪ mandatory ∪ reproduction = docs, lint, repro, unit (probe excluded).
    assert budget["command_ids"] == ["docs", "lint", "repro", "unit"]
    assert budget["bookkeeping_seconds"] == 30
    assert (budget["reason"], budget["origin"], budget["override"]) == (
        "test",
        "test fixture",
        "new capture",
    )
    assert 45 + 60 + 120 + 300 + 30 in budget.values()
    repro = reproduction_node_budget(config())
    assert repro["command_ids"] == ["repro"]
    assert 120 + 30 in repro.values()


@pytest.mark.parametrize(
    ("paths", "uncertain", "expected", "reason"),
    [
        (["src/calc.py"], False, ["repro", "unit"], "path_rules"),
        (
            ["src/calc.py", "docs/guide.md"],
            False,
            ["docs", "repro", "unit"],
            "path_rules",
        ),
        (["README.md"], False, ["docs", "lint", "repro", "unit"], "unmatched_path"),
        (
            ["src/contracts/api.py"],
            False,
            ["docs", "lint", "repro", "unit"],
            "shared_contract_path",
        ),
        (["src/calc.py"], True, ["docs", "lint", "repro", "unit"], "uncertain"),
        ([], False, ["docs", "lint", "repro", "unit"], "empty_change_set"),
    ],
)
def test_check_selection_is_mandatory_union_rules_and_reproduction(
    paths: list[str], uncertain: bool, expected: list[str], reason: str
) -> None:
    assert select_checks(config(), paths, uncertain=uncertain) == {
        "command_ids": expected,
        "selection_reason": reason,
    }


def test_full_ids_must_cover_every_rule_mandatory_and_quality_command() -> None:
    value = config()
    value["check_policy"]["full_ids"] = ["unit", "lint"]  # docs rule not covered
    _refused(value, "full_ids_incomplete")
    value = config()
    value["quality_policy"] = {"command_ids": ["probe"], "evidence_schema_refs": []}
    _refused(value, "full_ids_incomplete")
    value["check_policy"]["full_ids"] = ["unit", "docs", "lint", "probe"]
    validate_config(value)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda c: c["external_policy"].update(mode="authorized"),
            "unsupported_until_ci_d1",
        ),
        (
            lambda c: c.update(health_policy={"environment": "prod"}),
            "unsupported_until_ci_d2",
        ),
        (
            lambda c: c["node_deadlines"].update(
                wait_effect={"bookkeeping_seconds": 5} | REASON
            ),
            "unsupported_until_ci_d1",
        ),
        (
            lambda c: c["node_deadlines"].update(
                wait_rollback={"bookkeeping_seconds": 5} | REASON
            ),
            "unsupported_until_ci_d2",
        ),
    ],
)
def test_ci_r1_refuses_effect_and_health_configuration(mutate, code: str) -> None:
    value = config()
    mutate(value)
    _refused(value, code)


def test_droid_role_structure_requires_separate_profile_content_admission() -> None:
    value = config()
    value["role_bindings"]["investigate"] = _selection("droid-look", "droid_exec")
    value["role_bindings"]["review"] = [
        _selection("review"),
        _selection("droid-review", "droid_exec"),
    ]
    validate_config(value)
    value["role_bindings"]["repair"] = _selection("droid-fix", "droid_exec")
    # These are digest refs, not profile contents. Preparation/home load/dispatch
    # must resolve them before granting write authority; structural parsing cannot.
    validate_config(value)
    value["role_bindings"]["repair"]["profile_ref"] = None
    _refused(value, "invalid_role_bindings")


def test_native_binding_requires_captured_profile_and_known_contract() -> None:
    value = config()
    native = _selection("codex-fix", "codex_exec")
    value["role_bindings"]["repair"] = native | {"profile_ref": None}
    _refused(value, "invalid_role_bindings")
    value["role_bindings"]["repair"] = native | {
        "binding": native["binding"] | {"contract": "millrace-codex-exec/v9"}
    }
    _refused(value, "unsupported_binding")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda c: c.update(extra=True), "invalid_config_v2"),
        (lambda c: c.pop("quality_policy"), "invalid_config_v2"),
        (lambda c: c["commands"][0].update(timeout_seconds=True), "invalid_execution"),
        (lambda c: c["commands"][0].update(timeout_seconds=None), "invalid_execution"),
        (lambda c: c["commands"].append(_command("unit", 1)), "duplicate_command_id"),
        (
            lambda c: c["reproduction"].update(command_ids=["missing"]),
            "unknown_command_id",
        ),
        (lambda c: c["repair_paths"].clear(), "invalid_repair_paths"),
        (lambda c: c["repair_paths"].append(".git/hooks"), "invalid_repair_paths"),
        (
            lambda c: c["repair_policy"].update(allowed_change_classes=["auth"]),
            "invalid_repair_policy",
        ),
        (
            lambda c: c["repair_policy"]["optional_caps"].append(
                {"kind": "files", "value": 3}
            ),
            "invalid_repair_policy",
        ),
        (
            lambda c: c["resource_policy"]["guards"].append(
                {"surface": "command.stdout", "measure": "bytes", "value": 1} | REASON
            ),
            "invalid_resource_policy",
        ),
    ],
)
def test_strict_config_fields_refuse_one_mutation_at_a_time(mutate, code: str) -> None:
    value = config()
    mutate(value)
    _refused(value, code)


def test_operator_cap_needs_reason_and_omission_means_no_cap() -> None:
    value = config()
    validate_config(value)  # No optional cap is a stock default.
    value["repair_policy"]["optional_caps"] = [
        {"kind": "changed_lines", "value": 400} | REASON
    ]
    validate_config(value)


def test_class_exception_requires_listed_repair_path_and_never_structure() -> None:
    value = config()
    value["class_exceptions"] = [
        {
            "class": "dependency",
            "paths": ["src/requirements.txt"],
            "authority_ref": _ref("authority.json"),
        }
        | REASON
    ]
    validate_config(value)
    value["class_exceptions"][0]["paths"] = ["vendor/lock.json"]
    _refused(value, "invalid_class_exceptions")
    value["class_exceptions"][0]["paths"] = ["src/.git/config"]
    _refused(value, "structural_not_exceptable")


# Candidate facade, attempt, decision and check records (spec 7.2-7.5). Each
# family has one legal fixture; cases mutate one field at a time and assert the
# exact refusal code. Draft/spec divergences are marked `# SPEC: <line>`.

HEAD = hashlib.sha256(b"anchor-head").hexdigest()[:40]
REG = 0o100644
LNK = 0o120777


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _refuses(validate, value: dict[str, object], code: str) -> None:
    with pytest.raises(CicdV2Error) as refused:
        validate(json.loads(json.dumps(value)))
    assert refused.value.code == code


def _entry(path: str, body: str | None = None) -> dict[str, object]:
    sha = _sha(path if body is None else body)
    payload = {"path": f"payloads/{sha}", "sha256": sha}
    return {"path": path, "mode": REG, "sha256": sha, "payload_ref": payload}


def _link(path: str, raw: bytes) -> dict[str, object]:
    return {
        "path": path,
        "mode": LNK,
        "target": base64.b64encode(raw).decode("ascii"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _tree() -> dict[str, object]:
    return {
        "format": TREE,
        "repository": "acme/calc",
        "anchor_head": HEAD,
        "path_policy_ref": POLICY_REF,
        "entries": [_entry("README.md"), _entry("src/calc.py")],
        # Opaque raw readlink bytes; never decoded into authority or followed.
        "baseline_links": [
            _link("docs/current", b"v1/guide"),
            _link("vendor/lib", b"../shared/\xff"),
        ],
    }


def test_tree_digest_is_canonical_whole_manifest_including_links() -> None:
    tree = _tree()
    assert validate_tree(copy.deepcopy(tree)) == tree
    assert tree_digest(tree) == hashlib.sha256(cc.encode(tree)).hexdigest()
    relinked = _tree()
    relinked["baseline_links"][0] = _link("docs/current", b"v2/guide")
    validate_tree(relinked)
    assert tree_digest(relinked) != tree_digest(tree)
    executable = _tree()
    executable["entries"][1]["mode"] = 0o100755
    assert tree_digest(executable) != tree_digest(tree)


# Same decoded bytes, nonzero trailing bits: not the canonical base64 spelling.
_LOOSE_B64 = base64.b64encode(b"v1/guide").decode("ascii")[:-2] + "V="


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.update(extra=None),
        lambda t: t.pop("baseline_links"),
        lambda t: t.update(format="millrace-plus-cicd-tree/v2"),
        lambda t: t.update(anchor_head=HEAD[:-1]),
        lambda t: t["entries"].reverse(),
        lambda t: t["entries"].append(_entry("src/calc.py")),
        lambda t: t["entries"][0].update(mode=True),
        lambda t: t["entries"][0].update(mode=LNK),
        lambda t: t["entries"][0].update(path="src/../README.md"),
        lambda t: t["entries"][0].update(unknown=1),
        lambda t: t["entries"].insert(0, _entry(".git/config")),
        lambda t: t["entries"][0].update(payload_ref={"path": "payloads/x"}),
        lambda t: t["entries"].insert(1, _entry("docs/current/guide.md")),
        lambda t: t["baseline_links"].reverse(),
        lambda t: t["baseline_links"].append(_link("vendor/lib", b"other")),
        lambda t: t["baseline_links"][0].update(mode=REG),
        lambda t: t["baseline_links"][0].update(mode=True),
        lambda t: t["baseline_links"][0].update(target=_LOOSE_B64),
        lambda t: t["baseline_links"][0].update(target="djEvZ3VpZGU=\n"),
        lambda t: t["baseline_links"][0].update(
            sha256=_sha(t["baseline_links"][0]["target"])
        ),
    ],
)
def test_tree_refuses_one_corruption_at_a_time(mutate) -> None:
    tree = _tree()
    mutate(tree)
    _refuses(validate_tree, tree, "invalid_tree")


def test_tree_entry_payload_ref_must_pin_the_exact_entry_bytes() -> None:
    # SPEC: 342 "exact retained payloads": a payload Ref whose hash is not the
    # entry's content hash is a broken in-record link (spec 225).
    tree = _tree()
    tree["entries"][0]["payload_ref"] = _ref("payloads/README.md")
    _refuses(validate_tree, tree, "invalid_tree")


def _delta() -> dict[str, object]:
    return {
        "format": DELTA,
        "base_tree_sha256": _sha("tree-base"),
        "candidate_tree_sha256": _sha("tree-candidate"),
        "changes": [
            {"path": "README.md", "before": _entry("README.md"), "after": None},
            {
                "path": "src/calc.py",
                "before": _entry("src/calc.py"),
                "after": _entry("src/calc.py", "fixed calc"),
            },
            {"path": "src/new.py", "before": None, "after": _entry("src/new.py")},
        ],
    }


def test_delta_sorted_changes_and_empty_exactly_for_equal_trees() -> None:
    delta = _delta()
    assert validate_delta(copy.deepcopy(delta)) == delta
    same = _delta() | {"changes": []}
    same["candidate_tree_sha256"] = same["base_tree_sha256"]
    assert validate_delta(copy.deepcopy(same)) == same
    mode_only = _delta()
    mode_only["changes"] = [
        {
            "path": "run.sh",
            "before": _entry("run.sh"),
            "after": _entry("run.sh") | {"mode": 0o100755},
        }
    ]
    validate_delta(mode_only)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=None),
        lambda d: d.pop("changes"),
        lambda d: d.update(base_tree_sha256="x" * 64),
        lambda d: d["changes"].reverse(),
        lambda d: d["changes"].append(copy.deepcopy(d["changes"][0])),
        lambda d: d["changes"][0].update(before=None),
        lambda d: d["changes"][1].update(after=_entry("src/calc.py")),
        lambda d: d["changes"][2].update(after=_entry("src/other.py")),
        lambda d: d["changes"][2]["after"].update(mode=True),
        lambda d: d["changes"][2]["after"].update(mode=LNK),
        lambda d: d["changes"][0].update(kind="delete"),
        lambda d: d["changes"][0].update(path="/etc/passwd"),
        lambda d: d.update(changes=[]),
        lambda d: d.update(candidate_tree_sha256=d["base_tree_sha256"]),
    ],
)
def test_delta_refuses_one_corruption_at_a_time(mutate) -> None:
    delta = _delta()
    mutate(delta)
    _refuses(validate_delta, delta, "invalid_delta")


def _target(root: str, inode: int) -> dict[str, object]:
    return {
        "root": root,
        "filesystem_identity": {"path": root, "device": 7, "inode": inode},
        "owner_ref": _ref("owner.json"),
    }


def _candidate(*, anchor: bool = False) -> dict[str, object]:
    base = _sha("tree-base")
    value: dict[str, object] = {
        "format": CANDIDATE,
        "candidate_id": "0" * 64,
        "origin_refs": [_ref("anchor.json" if anchor else "export-basis.json")],
        "repository": "acme/calc",
        "anchor_head": HEAD,
        "logical_base_id": base,
        "prior_candidate_id": None if anchor else _sha("prior-candidate"),
        "base_tree_sha256": base,
        "candidate_tree_sha256": base if anchor else _sha("tree-candidate"),
        "delta_ref": _ref("delta.json"),
        "tree_manifest_ref": _ref("tree.json"),
        "before_manifest_ref": _ref("before.json"),
        "source_target": _target("/srv/dev", 11),
        "verification_target": _target("/srv/validate", 12),
        "config_ref": _ref("config.json"),
        "assessment_ref": None if anchor else _ref("assessment.json"),
    }
    value["candidate_id"] = candidate_identity(value)
    return value


def test_candidate_identity_and_anchor_without_assessment() -> None:
    value = _candidate()
    unsigned = {key: item for key, item in value.items() if key != "candidate_id"}
    assert value["candidate_id"] == hashlib.sha256(cc.encode(unsigned)).hexdigest()
    assert validate_candidate(copy.deepcopy(value)) == value
    anchor = _candidate(anchor=True)
    assert anchor["assessment_ref"] is None and anchor["prior_candidate_id"] is None
    assert validate_candidate(copy.deepcopy(anchor)) == anchor
    assert anchor["candidate_id"] != value["candidate_id"]


@pytest.mark.parametrize(
    ("mutate", "rehash", "code"),
    [
        (
            lambda c: c.update(candidate_id=_sha("forged")),
            False,
            "candidate_identity_mismatch",
        ),
        (
            lambda c: c.update(config_ref=_ref("other-config.json")),
            False,
            "candidate_identity_mismatch",
        ),
        (
            lambda c: c["origin_refs"].append(_ref("lad-export.json")),
            False,
            "candidate_identity_mismatch",
        ),
        (
            lambda c: c.update(assessment_ref=None),
            True,
            "candidate_assessment_required",
        ),
        # No predecessor, but the trees differ: not the empty-delta anchor.
        (
            lambda c: c.update(assessment_ref=None, prior_candidate_id=None),
            True,
            "candidate_assessment_required",
        ),
        (
            lambda c: c.update(logical_base_id=_sha("other-base")),
            True,
            "invalid_candidate",
        ),
        (lambda c: c.update(origin_refs=[]), True, "invalid_candidate"),
        (
            lambda c: c["origin_refs"].append(c["origin_refs"][0]),
            True,
            "invalid_candidate",
        ),
        (lambda c: c.update(prior_candidate_id="prior"), True, "invalid_candidate"),
        (
            lambda c: c.update(verification_target=copy.deepcopy(c["source_target"])),
            True,
            "invalid_candidate",
        ),
        (
            lambda c: c["verification_target"]["filesystem_identity"].update(inode=11),
            True,
            "invalid_candidate",
        ),
        (
            lambda c: c["source_target"]["filesystem_identity"].update(device=True),
            True,
            "invalid_candidate",
        ),
        (
            lambda c: c.update(format="millrace-plus-cicd-candidate/v2"),
            True,
            "invalid_candidate",
        ),
        (lambda c: c.update(extra=None), False, "invalid_candidate"),
        (lambda c: c.pop("assessment_ref"), False, "invalid_candidate"),
    ],
)
def test_candidate_refuses_one_corruption_at_a_time(
    mutate, rehash: bool, code: str
) -> None:
    value = _candidate()
    mutate(value)
    if rehash:
        value["candidate_id"] = candidate_identity(value)
    _refuses(validate_candidate, value, code)


_LEASE_STATES = (
    "reserved",
    "base_ready",
    "repair_reserved",
    "partial",
    "sealed",
    "restored",
    "unknown",
)


def _lease(state: str = "sealed") -> dict[str, object]:
    bound = state in {"sealed", "restored"}
    reserved = state == "reserved"
    restoration = _ref("restoration.json") if state == "restored" else None
    return {
        "format": MATERIALIZATION,
        "materialization_id": "mat-1",
        "version": 4,
        "origin_base_candidate_ref": _ref("candidates/base.json"),
        "candidate_ref": _ref("candidates/final.json") if bound else None,
        "request_sha256": _sha("prepare-base-request"),
        "owner_ref": _ref("owner.json"),
        "target_before_sha256": _sha("target-before"),
        "target_after_sha256": None if reserved else _sha("target-after"),
        "beforeimages_ref": None if reserved else _ref("beforeimages.json"),
        "operation_refs": [] if reserved else [_ref("operations/1.json")],
        "state": state,
        "restoration_ref": restoration,
    }


@pytest.mark.parametrize("state", _LEASE_STATES)
def test_materialization_lease_states_validate(state: str) -> None:
    lease = _lease(state)
    assert validate_materialization(copy.deepcopy(lease)) == lease


@pytest.mark.parametrize(
    ("state", "mutate"),
    [
        ("sealed", lambda m: m.update(version=0)),
        ("sealed", lambda m: m.update(version=True)),
        ("sealed", lambda m: m.update(version=4.0)),
        ("sealed", lambda m: m.update(state="committed")),
        # Candidate is null during base preparation and repair; bound at seal.
        ("reserved", lambda m: m.update(candidate_ref=_ref("candidates/c.json"))),
        ("base_ready", lambda m: m.update(candidate_ref=_ref("candidates/c.json"))),
        (
            "repair_reserved",
            lambda m: m.update(candidate_ref=_ref("candidates/c.json")),
        ),
        ("sealed", lambda m: m.update(candidate_ref=None)),
        # Restoration ref exactly when restored.
        ("sealed", lambda m: m.update(restoration_ref=_ref("restoration.json"))),
        ("restored", lambda m: m.update(restoration_ref=None)),
        ("base_ready", lambda m: m.update(beforeimages_ref=None)),
        ("sealed", lambda m: m.update(origin_base_candidate_ref=None)),
        ("sealed", lambda m: m["operation_refs"].append(m["operation_refs"][0])),
        ("sealed", lambda m: m.update(request_sha256=None)),
        ("sealed", lambda m: m.update(extra=None)),
        ("sealed", lambda m: m.pop("restoration_ref")),
    ],
)
def test_materialization_refuses_one_corruption_at_a_time(state: str, mutate) -> None:
    lease = _lease(state)
    mutate(lease)
    _refuses(validate_materialization, lease, "invalid_materialization")


def _operation(status: str = "applied", operation: str = "import") -> dict[str, object]:
    known = status in {"applied", "restored"}
    previous = None if status == "reserved" else _ref("operations/reserved.json")
    restoration = _ref("restoration.json") if status == "restored" else None
    return {
        "format": MATERIALIZATION_OPERATION,
        "operation_id": f"{operation}-{status}",
        "materialization_id": "mat-1",
        "operation": operation,
        "request_sha256": _sha("import-request"),
        "before_tree_sha256": _sha("tree-base"),
        "after_tree_sha256": _sha("tree-candidate") if known else None,
        "beforeimages_ref": _ref("beforeimages.json"),
        "allowed_writes_ref": _ref("allowed-writes.json"),
        "write_intent_ref": _ref("write-intent.json"),
        "completion_ref": _ref("completion.json") if known else None,
        "previous_receipt_ref": previous,
        "status": status,
        "restoration_ref": restoration,
    }


@pytest.mark.parametrize("status", ["reserved", "applied", "unknown", "restored"])
def test_materialization_operation_receipts_validate(status: str) -> None:
    for operation in ("prepare_base", "import", "repair", "check_products"):
        receipt = _operation(status, operation)
        assert validate_materialization_operation(copy.deepcopy(receipt)) == receipt
    validate_materialization_operation(_operation(status, "restore"))


@pytest.mark.parametrize(
    ("status", "mutate"),
    [
        # Only the initial reservation lacks a previous receipt.
        (
            "reserved",
            lambda o: o.update(previous_receipt_ref=_ref("operations/0.json")),
        ),
        ("applied", lambda o: o.update(previous_receipt_ref=None)),
        ("unknown", lambda o: o.update(previous_receipt_ref=None)),
        ("restored", lambda o: o.update(previous_receipt_ref=None)),
        # After tree and completion stay null until actually known.
        ("reserved", lambda o: o.update(after_tree_sha256=_sha("tree-after"))),
        ("reserved", lambda o: o.update(completion_ref=_ref("completion.json"))),
        ("applied", lambda o: o.update(completion_ref=None)),
        ("applied", lambda o: o.update(after_tree_sha256=None)),
        ("restored", lambda o: o.update(restoration_ref=None)),
        ("applied", lambda o: o.update(restoration_ref=_ref("restoration.json"))),
        ("applied", lambda o: o.update(operation="checkout")),
        ("applied", lambda o: o.update(status="done")),
        ("applied", lambda o: o.update(operation_id=True)),
        ("applied", lambda o: o.update(beforeimages_ref=None)),
        ("applied", lambda o: o.update(write_intent_ref=None)),
        ("applied", lambda o: o.update(extra=None)),
        ("applied", lambda o: o.pop("restoration_ref")),
    ],
)
def test_materialization_operation_refuses_one_corruption_at_a_time(
    status: str, mutate
) -> None:
    receipt = _operation(status)
    mutate(receipt)
    _refuses(
        validate_materialization_operation,
        receipt,
        "invalid_materialization_operation",
    )


_OPERATIONS = (
    "export",
    "prepare-base",
    "import",
    "project-role",
    "check",
    "review",
    "verify",
    "restore",
)
_UNLEASED = {"materialization_id": None, "expected_materialization_version": None}
_UNAUTHORIZED = {"authority_ref": None, "approval_ref": None}


def _request(operation: str) -> dict[str, object]:
    value: dict[str, object] = {
        "format": CANDIDATE_REQUEST,
        "request_id": f"request-{operation}",
        "operation": operation,
        "candidate_ref": _ref("candidates/final.json"),
        "subject_ref": None,
        "export_basis_ref": None,
        "role": None,
        "materialization_id": "mat-1",
        "expected_materialization_version": 3,
        "authority_ref": _ref("local-authority.json"),
        "approval_ref": _ref("approval.json"),
        "command_id": None,
        "role_binding_id": None,
        "expected_target_sha256": _sha("target-current"),
        "evidence_refs": [],
    }
    if operation == "export":
        # Edits neither workarea; its before manifest supplies the target digest.
        value |= (
            _UNLEASED
            | _UNAUTHORIZED
            | {
                "candidate_ref": None,
                "subject_ref": _ref("subject.json"),
                "export_basis_ref": _ref("export-basis.json"),
                "expected_target_sha256": None,
            }
        )
    elif operation == "prepare-base":
        # Creates the lease and names its accepted base candidate.
        value |= _UNLEASED | {"candidate_ref": _ref("candidates/base.json")}
    elif operation == "project-role":
        value |= (
            _UNLEASED
            | _UNAUTHORIZED
            | {
                "candidate_ref": None,
                "subject_ref": _ref("subject.json"),
                "role": "investigate",
                "role_binding_id": "investigate",
            }
        )
    elif operation == "check":
        value["command_id"] = "unit"
    elif operation == "review":
        value |= _UNAUTHORIZED | {"role_binding_id": "review"}
    elif operation == "verify":
        value |= _UNAUTHORIZED
    return value


@pytest.mark.parametrize("operation", _OPERATIONS)
def test_candidate_request_operation_nullability_validates(operation: str) -> None:
    value = _request(operation)
    validated = validate_candidate_request(copy.deepcopy(value), operation=operation)
    assert validated == value
    # The recorded operation must match the CLI argument.
    other = "verify" if operation == "restore" else "restore"
    with pytest.raises(CicdV2Error) as refused:
        validate_candidate_request(value, operation=other)
    assert refused.value.code == "operation_mismatch"


def test_project_role_repair_appends_to_the_existing_authorized_lease() -> None:
    value = _request("project-role") | {
        "role": "repair",
        "role_binding_id": "repair",
        "materialization_id": "mat-1",
        "expected_materialization_version": 2,
        "authority_ref": _ref("local-authority.json"),
        "approval_ref": _ref("approval.json"),
    }
    validate_candidate_request(copy.deepcopy(value), operation="project-role")
    _refuses(validate_candidate_request, value | _UNLEASED, "invalid_candidate_request")


@pytest.mark.parametrize(
    ("operation", "mutate"),
    [
        # Export: subject + export basis, no candidate, no expected digest.
        ("export", lambda r: r.update(candidate_ref=_ref("candidates/c.json"))),
        ("export", lambda r: r.update(subject_ref=None)),
        ("export", lambda r: r.update(export_basis_ref=None)),
        ("export", lambda r: r.update(expected_target_sha256=_sha("target"))),
        # Every later operation names a candidate, never a subject or basis.
        ("prepare-base", lambda r: r.update(candidate_ref=None)),
        ("import", lambda r: r.update(candidate_ref=None)),
        ("verify", lambda r: r.update(candidate_ref=None)),
        ("import", lambda r: r.update(subject_ref=_ref("subject.json"))),
        ("check", lambda r: r.update(export_basis_ref=_ref("export-basis.json"))),
        ("project-role", lambda r: r.update(subject_ref=None)),
        # Role only for project-role.
        ("import", lambda r: r.update(role="repair")),
        ("project-role", lambda r: r.update(role=None)),
        ("project-role", lambda r: r.update(role="review")),
        # Command ID only (and always) for check.
        ("check", lambda r: r.update(command_id=None)),
        ("review", lambda r: r.update(command_id="unit")),
        ("import", lambda r: r.update(command_id="unit")),
        # Binding ID only (and always) for review/project-role.
        ("review", lambda r: r.update(role_binding_id=None)),
        ("project-role", lambda r: r.update(role_binding_id=None)),
        ("check", lambda r: r.update(role_binding_id="review")),
        # Verify is read-only; target-writing operations need authority+approval.
        (
            "verify",
            lambda r: r.update(
                authority_ref=_ref("local-authority.json"),
                approval_ref=_ref("approval.json"),
            ),
        ),
        ("prepare-base", lambda r: r.update(_UNAUTHORIZED)),
        ("import", lambda r: r.update(_UNAUTHORIZED)),
        ("check", lambda r: r.update(_UNAUTHORIZED)),
        ("restore", lambda r: r.update(_UNAUTHORIZED)),
        ("restore", lambda r: r.update(approval_ref=None)),
        ("import", lambda r: r.update(authority_ref=None)),
        # Later operations require the exact current lease and positive version.
        ("import", lambda r: r.update(_UNLEASED)),
        ("check", lambda r: r.update(_UNLEASED)),
        ("review", lambda r: r.update(_UNLEASED)),
        ("verify", lambda r: r.update(_UNLEASED)),
        ("restore", lambda r: r.update(_UNLEASED)),
        ("import", lambda r: r.update(expected_materialization_version=None)),
        ("import", lambda r: r.update(materialization_id=None)),
        ("import", lambda r: r.update(expected_materialization_version=0)),
        ("import", lambda r: r.update(expected_materialization_version=True)),
        ("prepare-base", lambda r: r.update(expected_materialization_version=1)),
        # Expected target digest is mandatory except export.
        ("import", lambda r: r.update(expected_target_sha256=None)),
        ("project-role", lambda r: r.update(expected_target_sha256=None)),
        ("verify", lambda r: r.update(expected_target_sha256="F" * 64)),
        ("import", lambda r: r.update(operation="deliver")),
        ("import", lambda r: r.update(request_id=" ")),
        ("import", lambda r: r["evidence_refs"].extend([_ref("e.json")] * 2)),
        ("import", lambda r: r.update(extra=None)),
        ("import", lambda r: r.pop("evidence_refs")),
    ],
)
def test_candidate_request_refuses_one_mutation_at_a_time(
    operation: str, mutate
) -> None:
    value = _request(operation)
    mutate(value)
    _refuses(validate_candidate_request, value, "invalid_candidate_request")


def _attempt(status: str = "completed") -> dict[str, object]:
    dispatched = status != "reserved"
    completed = status == "completed"
    return {
        "format": ATTEMPT,
        "input_id": "input-1",
        "subject_ref": _ref("input.json"),
        "attempt_id": "attempt-2",
        "parent_attempt_id": "attempt-1",
        "kind": "repair",
        "basis_sha256": _sha("basis"),
        "source_sha256": _sha("source"),
        "config_sha256": _sha("config"),
        "changed_cause_ref": _ref("changed-cause.json"),
        "reservation": {
            "core_invocation_id": "invocation-9" if dispatched else None,
            "core_attempt_id": 1 if dispatched else None,
            "request_id": "request-2",
        },
        "completion_ref": _ref("completion.json") if completed else None,
        "artifact_ref": _ref("repair.json") if completed else None,
        "status": status,
    }


@pytest.mark.parametrize(
    "status", ["reserved", "running", "completed", "unknown", "refused"]
)
def test_attempt_statuses_validate_and_first_attempt_has_no_parent(status: str) -> None:
    attempt = _attempt(status)
    assert validate_attempt(copy.deepcopy(attempt)) == attempt
    first = _attempt(status) | {"parent_attempt_id": None, "changed_cause_ref": None}
    validate_attempt(first)


@pytest.mark.parametrize(
    ("status", "mutate"),
    [
        # Invocation/attempt are null only before dispatch, and only together.
        (
            "running",
            lambda a: a["reservation"].update(
                core_invocation_id=None, core_attempt_id=None
            ),
        ),
        (
            "completed",
            lambda a: a["reservation"].update(
                core_invocation_id=None, core_attempt_id=None
            ),
        ),
        ("running", lambda a: a["reservation"].update(core_attempt_id=None)),
        ("reserved", lambda a: a["reservation"].update(core_attempt_id=1)),
        ("running", lambda a: a["reservation"].update(core_attempt_id=True)),
        ("running", lambda a: a["reservation"].update(core_attempt_id=0)),
        ("running", lambda a: a["reservation"].update(request_id=None)),
        ("running", lambda a: a["reservation"].update(extra=None)),
        # Completion/artifact null until observed; completed needs completion.
        ("completed", lambda a: a.update(completion_ref=None)),
        ("reserved", lambda a: a.update(completion_ref=_ref("completion.json"))),
        ("running", lambda a: a.update(artifact_ref=_ref("repair.json"))),
        # A changed-cause continuation is linked to its predecessor.
        ("reserved", lambda a: a.update(parent_attempt_id=None)),
        ("reserved", lambda a: a.update(parent_attempt_id="attempt-2")),
        ("reserved", lambda a: a.update(kind="deploy")),
        ("reserved", lambda a: a.update(status="done")),
        ("reserved", lambda a: a.update(basis_sha256=_sha("basis")[:-1])),
        ("reserved", lambda a: a.update(extra=None)),
        ("reserved", lambda a: a.pop("artifact_ref")),
    ],
)
def test_attempt_refuses_one_corruption_at_a_time(status: str, mutate) -> None:
    attempt = _attempt(status)
    mutate(attempt)
    _refuses(validate_attempt, attempt, "invalid_attempt")


def _decision(kind: str = "continue", stage: str = "repair") -> dict[str, object]:
    resume = kind == "continue"
    effect = kind in {"approve_delivery", "approve_rollback"}
    return {
        "format": DECISION,
        "request_id": f"decide-{kind}",
        "input_id": "input-1",
        "expected_state_version": 7,
        "kind": kind,
        "basis_sha256": _sha("held-report"),
        "target_stage": stage if resume else None,
        "changed_cause_ref": _ref("changed-cause.json") if resume else None,
        "permission_ref": _ref("permission.json") if effect else None,
        "evidence_refs": [_ref("hold-report.json")],
    }


@pytest.mark.parametrize(
    "stage", ["reproduce", "investigate", "probe", "repair", "checks", "review"]
)
def test_continue_decision_needs_changed_cause_and_allowed_stage(stage: str) -> None:
    decision = _decision("continue", stage)
    assert require_ci_r1_decision(copy.deepcopy(decision)) == decision
    _refuses(
        validate_decision,
        decision | {"changed_cause_ref": None},
        "invalid_decision_v2",
    )


def test_ci_r1_applies_close_and_cancel_but_not_effect_approvals() -> None:
    for kind in ("close", "cancel"):
        decision = _decision(kind)
        assert require_ci_r1_decision(copy.deepcopy(decision)) == decision
    for kind, code in (
        ("approve_delivery", "unsupported_until_ci_d1"),
        ("approve_rollback", "unsupported_until_ci_d2"),
    ):
        decision = _decision(kind)
        validate_decision(copy.deepcopy(decision))  # Structurally legal.
        _refuses(require_ci_r1_decision, decision, code)


@pytest.mark.parametrize(
    ("kind", "mutate"),
    [
        ("continue", lambda d: d.update(target_stage="settle")),
        ("continue", lambda d: d.update(target_stage="guard_delivery")),
        ("continue", lambda d: d.update(target_stage=None)),
        ("close", lambda d: d.update(target_stage="repair")),
        ("cancel", lambda d: d.update(target_stage="checks")),
        ("approve_delivery", lambda d: d.update(permission_ref=None)),
        ("approve_rollback", lambda d: d.update(permission_ref=None)),
        ("close", lambda d: d.update(permission_ref=_ref("permission.json"))),
        ("continue", lambda d: d.update(permission_ref=_ref("permission.json"))),
        ("close", lambda d: d.update(expected_state_version=0)),
        ("close", lambda d: d.update(expected_state_version=True)),
        ("close", lambda d: d.update(kind="merge")),
        ("close", lambda d: d.update(input_id="")),
        ("close", lambda d: d["evidence_refs"].append(d["evidence_refs"][0])),
        ("close", lambda d: d.update(extra=None)),
        ("close", lambda d: d.pop("permission_ref")),
    ],
)
def test_decision_refuses_one_corruption_at_a_time(kind: str, mutate) -> None:
    decision = _decision(kind)
    mutate(decision)
    _refuses(validate_decision, decision, "invalid_decision_v2")


def _check(subject: str = "input") -> dict[str, object]:
    candidate = subject == "candidate"
    return {
        "format": CHECK,
        "input_id": None if candidate else "input-1",
        "candidate_id": _sha("candidate") if candidate else None,
        "materialization_id": "mat-1" if candidate else None,
        "attempt_id": "attempt-3",
        "command_id": "unit",
        "command_sha256": _sha("unit-execution"),
        "argv": ["python3", "checks/unit.py"],
        "cwd": ".",
        "environment_provenance": {
            "inherited_names": ["PATH"],
            "inherited_value_sha256": _sha("PATH=/usr/bin"),
            "set_value_sha256": _sha("{}"),
            "path_observation_refs": [_ref("env/before.json")],
        },
        "path_policy_ref": POLICY_REF,
        "environment_before_ref": _ref("env/before.json"),
        "environment_after_ref": _ref("env/after.json"),
        "product_refs": [],
        "head_sha": HEAD,
        "source_before_sha256": _sha("source"),
        "source_after_sha256": _sha("source"),
        "started_at": "2026-10-31T12:00:00Z",
        "finished_at": "2026-10-31T12:00:05Z",
        "exit_code": 0,
        "status": "passed",
        "process_facts": {
            "root_reaped": True,
            "stdout_eof": True,
            "stderr_eof": True,
            "group_state": "closed",
        },
        "stdout_ref": _ref("logs/stdout.txt"),
        "stderr_ref": _ref("logs/stderr.txt"),
        "matched_failure_ids": [],
        "diagnostic": None,
    }


def test_check_has_exactly_one_subject_and_candidate_names_its_lease() -> None:
    for subject in ("input", "candidate"):
        check = _check(subject)
        assert validate_check(copy.deepcopy(check)) == check


def test_non_pass_check_retains_incomplete_facts_without_promotion() -> None:
    failed = _check() | {
        "status": "failed",
        "exit_code": 1,
        "stdout_ref": None,
        "environment_after_ref": None,
        "matched_failure_ids": ["assert"],
        "process_facts": {
            "root_reaped": True,
            "stdout_eof": False,
            "stderr_eof": True,
            "group_state": "may_live",
        },
    }
    validate_check(copy.deepcopy(failed))
    timeout = failed | {
        "status": "timeout",
        "exit_code": None,
        "finished_at": None,
        "source_after_sha256": None,
        "diagnostic": "deadline reached",
    }
    timeout["process_facts"] = timeout["process_facts"] | {"group_state": "unknown"}
    validate_check(timeout)


@pytest.mark.parametrize(
    ("subject", "mutate"),
    [
        (
            "input",
            lambda c: c.update(
                candidate_id=_sha("candidate"), materialization_id="mat-1"
            ),
        ),
        ("input", lambda c: c.update(input_id=None)),
        ("input", lambda c: c.update(materialization_id="mat-1")),
        ("candidate", lambda c: c.update(materialization_id=None)),
        ("candidate", lambda c: c.update(input_id="input-1")),
        ("candidate", lambda c: c.update(candidate_id="candidate-1")),
    ],
)
def test_check_refuses_anything_but_exactly_one_subject(subject: str, mutate) -> None:
    check = _check(subject)
    mutate(check)
    _refuses(validate_check, check, "invalid_check_v2")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c["process_facts"].update(root_reaped=False),
        lambda c: c["process_facts"].update(stdout_eof=False),
        lambda c: c["process_facts"].update(stderr_eof=False),
        lambda c: c["process_facts"].update(group_state="may_live"),
        lambda c: c["process_facts"].update(group_state="unknown"),
        lambda c: c.update(stdout_ref=None),
        lambda c: c.update(stderr_ref=None),
        lambda c: c.update(environment_after_ref=None),
        lambda c: c.update(source_after_sha256=None),
        lambda c: c.update(source_after_sha256=_sha("source-edited")),
        lambda c: c.update(matched_failure_ids=["assert"]),
    ],
)
def test_check_pass_requires_complete_closed_process_facts(mutate) -> None:
    check = _check()
    mutate(check)
    _refuses(validate_check, check, "check_pass_unproven")


@pytest.mark.parametrize(
    "mutate",
    [
        # A pass without an observed exit or end is never a pass.
        lambda c: c.update(exit_code=None),
        lambda c: c.update(finished_at=None),
        lambda c: c.update(exit_code=True),
        lambda c: c.update(exit_code=0.0),
        lambda c: c["process_facts"].update(root_reaped=1),
        lambda c: c["process_facts"].update(group_state="open"),
        lambda c: c["process_facts"].update(extra=None),
        lambda c: c.update(status="skipped"),
        lambda c: c.update(argv=[]),
        lambda c: c.update(cwd="../outside"),
        lambda c: c.update(environment_before_ref=None),
        lambda c: c["environment_provenance"].update(inherited_names=["A=B"]),
        lambda c: c["environment_provenance"].pop("set_value_sha256"),
        lambda c: c.update(started_at="2026-10-31T12:00:00"),
        lambda c: c.update(head_sha=HEAD[:-1] + "A"),
        lambda c: c.update(extra=None),
        lambda c: c.pop("diagnostic"),
    ],
)
def test_check_strict_fields_refuse_one_mutation_at_a_time(mutate) -> None:
    check = _check()
    mutate(check)
    _refuses(validate_check, check, "invalid_check_v2")


def test_change_class_detection_covers_every_v1_protected_name_and_pattern() -> None:
    """v2 moves v1 CI/dependency/migration/deploy names into classes (R4 parity)."""
    from millrace_plus import cicd_records
    from millrace_plus.cicd_v2_records import (
        detect_change_classes,
        structural_forbidden,
    )

    entry = {"kind": "file", "mode": 0o644, "sha256": "1" * 64}
    edited = {"kind": "file", "mode": 0o644, "sha256": "2" * 64}
    for name in sorted(cicd_records._PROTECTED_NAMES):
        path = (
            f"pkg/{name}/x"
            if name in {".github", ".circleci", "migrations", "alembic"}
            else f"pkg/{name}"
        )
        classes = detect_change_classes({path: (entry, edited)})[path]
        assert classes, name  # Every v1 protected name still yields a class.
        if structural_forbidden(path):
            assert "credentials" in classes, name
    for sample in (
        "requirements-dev.txt",
        ".env.local",
        "deploy.pem",
        "id_ed25519",
        "db_secret.json",
    ):
        path = f"pkg/{sample}"
        assert cicd_records.fixed_protected(path), sample
        assert detect_change_classes({path: (entry, edited)})[path], sample


def test_change_classes_follow_actual_bytes_not_claims() -> None:
    from millrace_plus.cicd_v2_records import detect_change_classes

    old = {"kind": "file", "mode": 0o644, "sha256": "a" * 64}
    changes = {
        "src/calc.py": (old | {"sha256": "b" * 64}, old | {"sha256": "c" * 64}),
        "src/old.py": (old, None),
        "src/new.py": (None, old),
        "src/gone.py": (old | {"sha256": "d" * 64}, None),
        "src/run.sh": (
            old | {"sha256": "e" * 64},
            old | {"sha256": "e" * 64, "mode": 0o755},
        ),
        "src/logo.png": (None, old | {"sha256": "f" * 64}),
        "pyproject.toml": (old | {"sha256": "0" * 64}, old | {"sha256": "9" * 64}),
    }
    classes = detect_change_classes(changes, binary_paths={"src/logo.png"})
    assert classes == {
        "pyproject.toml": ["dependency"],
        "src/calc.py": [],
        "src/gone.py": ["delete"],
        "src/logo.png": ["binary"],
        "src/new.py": ["rename"],
        "src/old.py": ["rename"],
        "src/run.sh": ["mode_change"],
    }


def test_idle_wait_defaults_carry_provenance_and_capture_is_editable() -> None:
    from millrace_plus.cicd_v2_records import IDLE_WAIT_DEFAULT, idle_wait_policy

    value = config()
    validate_config(value)
    default = idle_wait_policy(value)
    assert default["defaulted"] is True
    assert (default["poll_seconds"], default["bookkeeping_seconds"]) == (1, 1)
    assert "effective invocation timeout / 2" in str(default["origin"])
    assert set(IDLE_WAIT_DEFAULT) == {
        "poll_seconds",
        "bookkeeping_seconds",
        "reason",
        "origin",
        "override",
    }
    chosen = {"poll_seconds": 0.25, "bookkeeping_seconds": 5} | REASON
    value["node_deadlines"]["wait_event"] = chosen
    validate_config(json.loads(json.dumps(value)))
    assert idle_wait_policy(value) == chosen | {"defaulted": False}
    for bad in (
        {"poll_seconds": 0, "bookkeeping_seconds": 5} | REASON,
        {"poll_seconds": 1, "bookkeeping_seconds": 5},
        {"poll_seconds": True, "bookkeeping_seconds": 5} | REASON,
    ):
        value["node_deadlines"]["wait_event"] = bad
        _refused(value, "invalid_node_deadlines")
    value["node_deadlines"].pop("wait_event")
    _refused(value, "invalid_node_deadlines")


def test_effective_change_classes_are_add_only() -> None:
    from millrace_plus.cicd_v2_records import effective_change_classes

    # A declaration may add a semantic class but never erase a detected one.
    assert effective_change_classes(["dependency"], []) == ["dependency"]
    assert effective_change_classes(["delete"], ["auth"]) == ["auth", "delete"]
    assert effective_change_classes([], []) == []
    with pytest.raises(CicdV2Error) as refused:
        effective_change_classes([], ["low_risk"])
    assert refused.value.code == "invalid_change_classes"


def test_state_phase_admits_ordered_reviewer_pairs_only() -> None:
    from millrace_plus.cicd_v2_records import validate_state

    state = {
        "format": "millrace-plus-cicd-state/v2",
        "input_id": "i" * 64,
        "version": 1,
        "phase": "review_2",
        "source_binding_ref": None,
        "attempt_refs": [],
        "current_attempt_id": None,
        "basis_sha256": None,
        "assessment_ref": None,
        "candidate_ref": None,
        "check_plan_ref": None,
        "review_plan_ref": None,
        "delivery_refs": [],
        "health_refs": [],
        "product_layer_refs": [],
        "hold_ref": None,
        "settlement_ref": None,
        "applied_decision_ids": [],
    }
    graph = ["reconcile", "review_1", "guard_review_1", "review_2", "guard_review_2"]
    assert validate_state(dict(state), phases=graph)["phase"] == "review_2"
    for bad in (["review_0"], ["review_x"], ["publish"]):
        with pytest.raises(CicdV2Error):
            validate_state(dict(state), phases=bad)
    with pytest.raises(CicdV2Error):
        validate_state(dict(state) | {"phase": "review_3"}, phases=graph)


# S1 review: assessment, repair, review, report, source binding, authority,
# input and plan records (spec 7.2-7.6). Imports stay local, like the state case.


def _v2():
    from millrace_plus import cicd_v2_records

    return cicd_v2_records


def _assessment(route: str = "REPAIR_ALLOWED") -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-assessment/v2",
        "input_id": "i" * 64,
        "subject_ref": _ref("subject.json"),
        "attempt_id": "attempt-1",
        "reproduction_refs": [_ref("reproduce.json")],
        "hypotheses": [
            {
                "id": "h1",
                "claim": "sum drops the carry",
                "status": "supported",
                "evidence_refs": [_ref("h1.log")],
            },
            {
                "id": "h2",
                "claim": "stale fixture",
                "status": "refuted",
                "evidence_refs": [],
            },
        ],
        "proposed_paths": ["src/calc.py"],
        "change_classes": [],
        "risk": {
            "impact": "wrong totals",
            "likelihood": "certain for carried input",
            "scenario": "add(9, 1)",
            "evidence_refs": [],
        },
        "route": route,
        "evidence_refs": [],
    }


@pytest.mark.parametrize("route", _v2().ROUTES)
def test_assessment_routes_validate_and_only_repair_needs_cause(route: str) -> None:
    value = _assessment(route)
    assert _v2().validate_assessment(copy.deepcopy(value)) == value
    if route != "REPAIR_ALLOWED":
        value.update(reproduction_refs=[], proposed_paths=[])
        value["hypotheses"] = value["hypotheses"][1:]
        assert _v2().validate_assessment(copy.deepcopy(value)) == value


def test_assessment_empty_reproduction_only_under_explicit_task_allowance() -> None:
    validate = _v2().validate_assessment
    value = _assessment()
    value["reproduction_refs"] = []
    assert validate(copy.deepcopy(value), allow_empty_reproduction=True) == value
    _refuses(validate, value, "unsupported_repair")
    with pytest.raises(CicdV2Error) as refused:
        validate(copy.deepcopy(value), allow_empty_reproduction=1)
    assert refused.value.code == "invalid_assessment"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(reproduction_refs=[]),
        lambda d: d.update(proposed_paths=[]),
        lambda d: d.update(hypotheses=d["hypotheses"][1:]),
        lambda d: d["hypotheses"][0].update(status="unresolved"),
        lambda d: d.update(hypotheses=[]),
    ],
)
def test_repair_route_requires_reproduction_supported_cause_and_paths(
    mutate,
) -> None:
    value = _assessment()
    mutate(value)
    _refuses(_v2().validate_assessment, value, "unsupported_repair")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(route="REPAIR"),
        lambda d: d.update(route="repair_allowed"),
        lambda d: d.update(route=None),
        lambda d: d["hypotheses"][0].update(status="likely"),
        lambda d: d["hypotheses"][1].update(id="h1"),
        lambda d: d["hypotheses"][0].update(claim=" "),
        lambda d: d["hypotheses"][0].pop("evidence_refs"),
        lambda d: d["risk"].pop("scenario"),
        lambda d: d["risk"].update(severity="high"),
        lambda d: d.update(proposed_paths=["../src/calc.py"]),
        lambda d: d.update(proposed_paths=["src/calc.py", "src/calc.py"]),
        lambda d: d.update(change_classes=["style"]),
        lambda d: d.update(change_classes=["auth", "auth"]),
        lambda d: d.update(attempt_id=True),
        lambda d: d.update(subject_ref=None),
        lambda d: d.update(reproduction_refs=d["reproduction_refs"] * 2),
        lambda d: d.update(format="millrace-plus-cicd-assessment/v1"),
        lambda d: d.update(score=1),
        lambda d: d.pop("route"),
    ],
)
def test_assessment_refuses_one_mutation_at_a_time(mutate) -> None:
    value = _assessment()
    mutate(value)
    _refuses(_v2().validate_assessment, value, "invalid_assessment")


def _repair() -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-repair/v2",
        "input_id": "i" * 64,
        "attempt_id": "attempt-2",
        "assessment_ref": _ref("assessment.json"),
        "source_before_sha256": _sha("before"),
        "source_after_sha256": _sha("after"),
        "patch_ref": _ref("patch.diff"),
        "changed_paths": ["src/calc.py"],
        "change_classes": [],
        "summary": "carry the overflow digit",
        "evidence_refs": [_ref("repair.log")],
    }


def test_repair_record_validates() -> None:
    value = _repair()
    assert _v2().validate_repair(copy.deepcopy(value)) == value


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(source_after_sha256=d["source_before_sha256"]),
        lambda d: d.update(source_before_sha256=_sha("before").upper()),
        lambda d: d.update(changed_paths=[]),
        lambda d: d.update(changed_paths=["src/calc.py", "src/calc.py"]),
        lambda d: d.update(changed_paths=["/src/calc.py"]),
        lambda d: d.update(change_classes=["refactor"]),
        lambda d: d.update(patch_ref=None),
        lambda d: d.update(assessment_ref=None),
        lambda d: d.update(summary=""),
        lambda d: d.update(attempt_id=False),
        lambda d: d.update(evidence_refs=d["evidence_refs"] * 2),
        lambda d: d.update(lines_changed=1),
        lambda d: d.pop("patch_ref"),
    ],
)
def test_repair_refuses_one_mutation_at_a_time(mutate) -> None:
    value = _repair()
    mutate(value)
    _refuses(_v2().validate_repair, value, "invalid_repair_v2")


def _finding(identifier: str, status: str) -> dict[str, object]:
    resolved = None if status == "unresolved" else _ref(f"{identifier}-fix.json")
    return {
        "id": identifier,
        "scenario": "negative operand",
        "evidence_refs": [_ref(f"{identifier}.log")],
        "impact": "wrong sign",
        "likelihood": "low",
        "status": status,
        "resolution_ref": resolved,
    }


def _review(verdict: str = "ACCEPT", subject: str = "candidate") -> dict[str, object]:
    candidate = subject == "candidate"
    return {
        "format": "millrace-plus-cicd-review/v2",
        "input_id": None if candidate else "input-1",
        "candidate_id": _sha("candidate") if candidate else None,
        "materialization_id": "lease-1" if candidate else None,
        "attempt_id": "attempt-3",
        "candidate_sha256": _sha("candidate"),
        "patch_ref": _ref("patch.diff"),
        "checks_refs": [_ref("check-unit.json")],
        "verdict": verdict,
        "findings": [_finding("f1", "resolved"), _finding("f2", "withdrawn")],
        "evidence_refs": [],
    }


def test_review_accept_needs_zero_unresolved_and_names_one_subject() -> None:
    validate = _v2().validate_review
    for subject in ("candidate", "input"):
        value = _review(subject=subject)
        assert validate(copy.deepcopy(value)) == value
    rejected = _review("REJECT")
    rejected["findings"].append(_finding("f3", "unresolved"))
    assert validate(copy.deepcopy(rejected)) == rejected
    blocked = _review("BLOCKED") | {"checks_refs": [], "findings": []}
    assert validate(copy.deepcopy(blocked)) == blocked
    accepted = copy.deepcopy(rejected) | {"verdict": "ACCEPT"}
    _refuses(validate, accepted, "review_unresolved_findings")


@pytest.mark.parametrize(
    "mutate",
    [
        # resolution_ref is null exactly when the finding is unresolved.
        lambda d: d["findings"][0].update(status="unresolved"),
        lambda d: d["findings"][0].update(resolution_ref=None),
        lambda d: d["findings"][1].update(resolution_ref=None),
        lambda d: d["findings"][0].update(status="accepted_risk"),
        lambda d: d["findings"][0].update(evidence_refs=[]),
        lambda d: d["findings"][0].update(scenario=" "),
        lambda d: d["findings"][1].update(id="f1"),
        lambda d: d["findings"][0].update(severity="high"),
        lambda d: d["findings"][0].pop("likelihood"),
        lambda d: d.update(checks_refs=[]),
        lambda d: d.update(verdict="accept"),
        lambda d: d.update(input_id="input-1"),
        lambda d: d.update(candidate_id=None),
        lambda d: d.update(materialization_id=None),
        lambda d: d.update(candidate_sha256=True),
        lambda d: d.update(patch_ref=None),
        lambda d: d.update(extra=None),
        lambda d: d.pop("findings"),
    ],
)
def test_review_refuses_one_mutation_at_a_time(mutate) -> None:
    value = _review()
    mutate(value)
    _refuses(_v2().validate_review, value, "invalid_review_v2")


def test_input_review_never_names_a_materialization() -> None:
    value = _review(subject="input") | {"materialization_id": "lease-1"}
    _refuses(_v2().validate_review, value, "invalid_review_v2")


def _report(status: str = "checked_patch") -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-report/v2",
        "input_id": "input-1",
        "config_sha256": _sha("config"),
        "status": status,
        "source_before_sha256": _sha("before"),
        "source_after_sha256": _sha("after"),
        "assessment_ref": _ref("assessment.json"),
        "attempt_refs": [_ref("attempt-1.json")],
        "patch_ref": _ref("patch.diff"),
        "checks_refs": [_ref("check-unit.json")],
        "review_refs": [_ref("review.json")],
        "correlation_refs": [],
        "candidate_ref": None,
        "delivery_refs": [],
        "health_refs": [],
        "incident_refs": [],
        "hold_reason": None,
    }


def _report_with_facts(status: str) -> dict[str, object]:
    value = _report(status)
    if status.startswith("held_"):
        value["hold_reason"] = "operator review required"
    if status in {"published", "healthy", "rolled_back"}:
        value["delivery_refs"] = [_ref("delivery.json")]
    if status in {"healthy", "rolled_back"}:
        value["health_refs"] = [_ref("health.json")]
    return value


@pytest.mark.parametrize("status", _v2().REPORT_STATUSES)
def test_report_statuses_validate_with_their_required_facts(status: str) -> None:
    value = _report_with_facts(status)
    assert _v2().validate_report(copy.deepcopy(value)) == value


def test_report_null_and_empty_record_absent_facts() -> None:
    value = _report("no_action")
    for key in (
        "source_before_sha256",
        "source_after_sha256",
        "assessment_ref",
        "patch_ref",
    ):
        value[key] = None
    for key in ("attempt_refs", "checks_refs", "review_refs"):
        value[key] = []
    assert _v2().validate_report(copy.deepcopy(value)) == value
    # Published proves selected delivery effects only, never health.
    published = _report_with_facts("published")
    assert published["health_refs"] == []
    assert _v2().validate_report(copy.deepcopy(published)) == published


@pytest.mark.parametrize(
    ("status", "mutate"),
    [
        ("held_risk", lambda d: d.update(hold_reason=None)),
        ("held_unreproduced", lambda d: d.update(hold_reason=" ")),
        ("checked_patch", lambda d: d.update(patch_ref=None)),
        ("checked_patch", lambda d: d.update(checks_refs=[])),
        ("checked_patch", lambda d: d.update(review_refs=[])),
        ("published", lambda d: d.update(delivery_refs=[])),
        ("healthy", lambda d: d.update(health_refs=[])),
        ("rolled_back", lambda d: d.update(health_refs=[])),
        ("checked_patch", lambda d: d.update(status="merged")),
        ("checked_patch", lambda d: d.update(config_sha256=None)),
        ("checked_patch", lambda d: d.update(source_before_sha256=True)),
        ("checked_patch", lambda d: d.update(attempt_refs=d["attempt_refs"] * 2)),
        ("checked_patch", lambda d: d.update(candidate_ref="candidate.json")),
        ("checked_patch", lambda d: d.update(model_effective=True)),
        ("checked_patch", lambda d: d.pop("incident_refs")),
    ],
)
def test_report_refuses_one_mutation_at_a_time(status: str, mutate) -> None:
    value = _report_with_facts(status)
    mutate(value)
    _refuses(_v2().validate_report, value, "invalid_report_v2")


ANCHOR_B = _sha("anchor-b")[:40]
COMMIT_C = _sha("commit-c")[:40]


def _source_binding(mode: str = "standalone") -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-source-binding/v1",
        "mode": mode,
        "input_ref": _ref("input.json"),
        "anchor_head": ANCHOR_B,
        "reported_head": ANCHOR_B,
        "candidate_ref": None,
        "publication_ref": None,
        "preparer_ref": None,
        "logical_base_id": None,
        "materialization_ref": None,
        "status": "bound",
    }


def _factory_binding(route: str) -> dict[str, object]:
    value = _source_binding("factory") | {"logical_base_id": _sha("logical")}
    if route == "known":
        value.update(
            reported_head=COMMIT_C,
            publication_ref=_ref("publication.json"),
            candidate_ref=_ref("candidate.json"),
            materialization_ref=_ref("materialization.json"),
        )
    elif route == "external":
        value.update(reported_head=COMMIT_C, preparer_ref=_ref("preparer.json"))
    else:
        value.update(
            reported_head=None,
            candidate_ref=_ref("candidate.json"),
            materialization_ref=_ref("materialization.json"),
        )
    return value


def test_source_binding_standalone_and_factory_routes_validate() -> None:
    validate = _v2().validate_source_binding
    standalone = _source_binding()
    assert validate(copy.deepcopy(standalone)) == standalone
    held = _source_binding() | {"reported_head": COMMIT_C, "status": "held"}
    assert validate(copy.deepcopy(held)) == held
    for route in ("known", "external", "unpublished"):
        value = _factory_binding(route)
        assert validate(copy.deepcopy(value)) == value
        # Observations keep actual anchor B; they never report HEAD=C.
        assert value["anchor_head"] == ANCHOR_B
    uncertain = _factory_binding("known") | {
        "materialization_ref": None,
        "status": "uncertain",
    }
    assert validate(copy.deepcopy(uncertain)) == uncertain


@pytest.mark.parametrize(
    "value",
    [
        _source_binding() | {"reported_head": COMMIT_C},
        _source_binding() | {"reported_head": None},
        # SPEC: 336 - standalone CI binds only actual HEAD equal to reported head.
        _source_binding()
        | {"reported_head": COMMIT_C, "preparer_ref": _ref("preparer.json")},
        _factory_binding("known") | {"materialization_ref": None},
        _factory_binding("known") | {"candidate_ref": None},
        _factory_binding("external") | {"preparer_ref": None},
    ],
)
def test_bound_source_binding_requires_its_exact_route_evidence(value) -> None:
    _refuses(_v2().validate_source_binding, value, "source_binding_unproven")


@pytest.mark.parametrize(
    "value",
    [
        _source_binding() | {"candidate_ref": _ref("candidate.json")},
        _source_binding() | {"logical_base_id": _sha("logical")},
        _source_binding() | {"materialization_ref": _ref("m.json")},
        _factory_binding("known") | {"logical_base_id": None},
        _factory_binding("known") | {"preparer_ref": _ref("preparer.json")},
        _factory_binding("known") | {"reported_head": None},
        _source_binding() | {"mode": "hybrid"},
        _source_binding() | {"status": "ok"},
        _source_binding() | {"status": True},
        _source_binding() | {"anchor_head": ANCHOR_B.upper()},
        _source_binding() | {"anchor_head": ANCHOR_B[:39]},
        _source_binding() | {"input_ref": None},
        _source_binding() | {"head": ANCHOR_B},
        {k: v for k, v in _source_binding().items() if k != "status"},
    ],
)
def test_source_binding_refuses_one_mutation_at_a_time(value) -> None:
    _refuses(_v2().validate_source_binding, value, "invalid_source_binding")


def _authority(instance: str | None = None) -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-local-authority/v1",
        "issuer": "operator",
        "target_root": "/srv/target",
        "owner_ref": _ref("owner.json"),
        "actors": [
            {
                "execution_home": "/srv/ci-home",
                "instance_id": instance,
                "definition_ref": _ref("definition.json"),
            }
        ],
        "operations": ["prepare-base", "check", "repair"],
        "repair_paths": ["src"],
        "path_policy_ref": POLICY_REF,
        "origin": "operator capture",
        "override": "new capture",
    }


def _actor_binding(instance: str = "instance-1") -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-actor-binding/v1",
        "authority_ref": _ref("local-authority.json"),
        "execution_home": "/srv/ci-home",
        "instance_id": instance,
        "definition_ref": _ref("definition.json"),
        "create_request_id": "create-1",
        "create_receipt_ref": _ref("create-receipt.json"),
    }


def test_actor_binding_binds_a_pre_init_actor_once_to_its_created_instance() -> None:
    v2 = _v2()
    for instance in (None, "instance-1"):
        authority = _authority(instance)
        assert v2.validate_local_authority(copy.deepcopy(authority)) == authority
        actor = v2.verify_actor_binding(authority, _actor_binding())
        assert actor == authority["actors"][0]
    binding = _actor_binding()
    assert v2.validate_actor_binding(copy.deepcopy(binding)) == binding


@pytest.mark.parametrize(
    ("authority", "binding"),
    [
        (_authority("instance-1"), _actor_binding("instance-2")),
        (_authority(), _actor_binding() | {"execution_home": "/srv/other"}),
        (_authority(), _actor_binding() | {"definition_ref": _ref("other.json")}),
    ],
)
def test_actor_binding_never_selects_another_instance_or_actor(
    authority, binding
) -> None:
    with pytest.raises(CicdV2Error) as refused:
        _v2().verify_actor_binding(authority, binding)
    assert refused.value.code == "actor_binding_mismatch"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(actors=[]),
        lambda d: d["actors"].append(dict(d["actors"][0], instance_id="other")),
        lambda d: d["actors"][0].update(execution_home="srv/ci-home"),
        lambda d: d["actors"][0].update(definition_ref=None),
        lambda d: d["actors"][0].pop("instance_id"),
        lambda d: d.update(operations=[]),
        lambda d: d.update(operations=["check", "check"]),
        lambda d: d.update(repair_paths=[]),
        lambda d: d.update(repair_paths=[".git"]),
        lambda d: d.update(target_root="srv/target"),
        lambda d: d.update(owner_ref=None),
        lambda d: d.update(issuer=True),
        lambda d: d.update(origin=""),
        lambda d: d.update(owner_marker_grants=True),
        lambda d: d.pop("override"),
    ],
)
def test_local_authority_refuses_one_mutation_at_a_time(mutate) -> None:
    value = _authority()
    mutate(value)
    _refuses(_v2().validate_local_authority, value, "invalid_local_authority")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(instance_id=None),
        lambda d: d.update(create_receipt_ref=None),
        lambda d: d.update(create_request_id=1),
        lambda d: d.update(authority_ref=None),
        lambda d: d.update(execution_home="relative/home"),
        lambda d: d.update(latest=True),
        lambda d: d.pop("create_request_id"),
    ],
)
def test_actor_binding_refuses_one_mutation_at_a_time(mutate) -> None:
    value = _actor_binding()
    mutate(value)
    _refuses(_v2().validate_actor_binding, value, "invalid_actor_binding")


def _source_identity(kind: str) -> dict[str, object]:
    if kind == "ci_event":
        return {
            "source": "github-actions",
            "repository": "acme/calc",
            "run_id": "101",
            "attempt": 1,
            "job_id": "unit",
            "head_sha": _sha("head")[:40],
        }
    return {
        "kind": kind,
        "producer": "support",
        "report_id": "report-7",
        "repository": "acme/calc",
    }


def _input(kind: str = "ci_event") -> dict[str, object]:
    identity = _source_identity(kind)
    return {
        "format": "millrace-plus-cicd-input/v2",
        "input_id": _v2().input_identity(kind, copy.deepcopy(identity)),
        "kind": kind,
        "original_ref": _ref("original.json"),
        "evidence_refs": [],
        "accepted_sequence": 1,
        "repository": "acme/calc",
        "source_identity": identity,
    }


@pytest.mark.parametrize("kind", _v2().INPUT_KINDS)
def test_input_id_is_the_exact_typed_source_identity(kind: str) -> None:
    v2 = _v2()
    value = _input(kind)
    assert v2.validate_input(copy.deepcopy(value)) == value
    assert value["input_id"] == v2.input_identity(kind, value["source_identity"])
    assert value["input_id"] == v2.digest(value["source_identity"])
    others = {v2.input_identity(k, _source_identity(k)) for k in v2.INPUT_KINDS}
    assert len(others) == len(v2.INPUT_KINDS)


@pytest.mark.parametrize(
    ("kind", "mutate", "code"),
    [
        ("ci_event", lambda d: d.update(input_id=_sha("other")), "input_identity"),
        (
            "ci_event",
            lambda d: d["source_identity"].update(run_id="102"),
            "input_identity",
        ),
        (
            "bug_report",
            lambda d: d["source_identity"].update(report_id="r-8"),
            "input_identity",
        ),
        ("ci_event", lambda d: d.update(repository="acme/other"), "repository"),
        ("ci_event", lambda d: d.update(kind="incident"), "invalid"),
        ("ci_event", lambda d: d.update(kind="bug_report"), "invalid"),
        ("bug_report", lambda d: d.update(kind="production_incident"), "invalid"),
        ("ci_event", lambda d: d["source_identity"].update(attempt=True), "invalid"),
        (
            "ci_event",
            lambda d: d["source_identity"].update(source="jenkins"),
            "invalid",
        ),
        ("ci_event", lambda d: d["source_identity"].update(extra=1), "invalid"),
        ("ci_event", lambda d: d.update(accepted_sequence=True), "invalid"),
        ("ci_event", lambda d: d.update(accepted_sequence=0), "invalid"),
        ("ci_event", lambda d: d.update(input_id=d["input_id"].upper()), "invalid"),
        ("ci_event", lambda d: d.update(original_ref=None), "invalid"),
        ("ci_event", lambda d: d.update(severity="high"), "invalid"),
        ("ci_event", lambda d: d.pop("evidence_refs"), "invalid"),
    ],
)
def test_input_refuses_one_mutation_at_a_time(kind: str, mutate, code: str) -> None:
    value = _input(kind)
    mutate(value)
    expected = {
        "input_identity": "input_identity_mismatch",
        "repository": "repository_mismatch",
        "invalid": "invalid_input_v2",
    }[code]
    _refuses(_v2().validate_input, value, expected)


def _check_plan(*ids: str) -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-check-plan/v1",
        "input_id": "input-1",
        "candidate_id": None,
        "basis_sha256": _sha("basis"),
        "command_ids": list(ids or ("unit", "repro")),
        "selection_reason": "path_rules",
        "evidence_refs": [],
    }


def _review_plan(*ids: str) -> dict[str, object]:
    return {
        "format": "millrace-plus-cicd-review-plan/v1",
        "input_id": None,
        "candidate_id": _sha("candidate"),
        "basis_sha256": _sha("basis"),
        "binding_ids": list(ids or ("review",)),
        "evidence_refs": [],
    }


def test_check_plan_stays_within_known_ids_and_keeps_required_ones() -> None:
    v2 = _v2()
    for ids in (("unit", "repro"), ("repro", "unit", "docs", "lint")):
        plan = _check_plan(*ids)
        assert v2.verify_check_plan(config(), copy.deepcopy(plan)) == plan
    candidate = _check_plan() | {"input_id": None, "candidate_id": _sha("c")}
    assert v2.validate_check_plan(copy.deepcopy(candidate)) == candidate
    review = _review_plan()
    assert v2.verify_review_plan(config(), copy.deepcopy(review)) == review


@pytest.mark.parametrize(
    ("ids", "code"),
    [
        # `probe` is a configured command, but never a selectable check.
        (("unit", "repro", "probe"), "check_plan_unknown_command"),
        (("unit", "repro", "deploy"), "check_plan_unknown_command"),
        (("unit",), "check_plan_incomplete"),
        (("repro", "docs", "lint"), "check_plan_incomplete"),
    ],
)
def test_verify_check_plan_never_adds_or_drops_required_ids(
    ids: tuple[str, ...], code: str
) -> None:
    with pytest.raises(CicdV2Error) as refused:
        _v2().verify_check_plan(config(), _check_plan(*ids))
    assert refused.value.code == code


def test_verify_review_plan_names_only_captured_review_selections() -> None:
    for ids in (("investigate",), ("review", "other")):
        with pytest.raises(CicdV2Error) as refused:
            _v2().verify_review_plan(config(), _review_plan(*ids))
        assert refused.value.code == "review_plan_unknown_binding"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(command_ids=[]),
        lambda d: d.update(command_ids=["unit", "unit"]),
        lambda d: d.update(command_ids=[True]),
        lambda d: d.update(candidate_id=_sha("c")),
        lambda d: d.update(input_id=None),
        lambda d: d.update(input_id=None, candidate_id="candidate-1"),
        lambda d: d.update(basis_sha256=_sha("basis").upper()),
        lambda d: d.update(selection_reason=""),
        lambda d: d.update(argv=["make", "test"]),
        lambda d: d.pop("evidence_refs"),
    ],
)
def test_check_plan_refuses_one_mutation_at_a_time(mutate) -> None:
    value = _check_plan()
    mutate(value)
    _refuses(_v2().validate_check_plan, value, "invalid_check_plan")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(binding_ids=[]),
        lambda d: d.update(binding_ids=["review", "review"]),
        lambda d: d.update(input_id="input-1"),
        lambda d: d.update(candidate_id=None),
        lambda d: d.update(basis_sha256=None),
        lambda d: d.update(model="other"),
        lambda d: d.pop("binding_ids"),
    ],
)
def test_review_plan_refuses_one_mutation_at_a_time(mutate) -> None:
    value = _review_plan()
    mutate(value)
    _refuses(_v2().validate_review_plan, value, "invalid_review_plan")
