from __future__ import annotations

import hashlib
import json
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "s02-original-workflows.json"
_FIXTURE_SHA256 = "f1d9943eba67ee226b8bd62d44f38d9362a67c118a27d98cccb081c326d606a1"
_C19_WORKFLOW_SELECTORS = {
    ("execution.lad", "0.1"),
    ("execution.lad_integrator", "0.1"),
    ("planning.lad", "0.1"),
    ("lad.full", "0.1"),
}
_C19_ASSETS = {
    "execution.entrypoints.lad_troubleshooter": {
        "content_digest": (
            "sha256:2aeb6e65d0bf5beddb212dbb9cfb9eee6"
            "bab89d9943fd9ca69424e2955dd6860"
        ),
        "byte_length": 6260,
        "raw_sha256": (
            "bfa7bcdbef8a3e512be85775b887ca5f7dfdb689"
            "028ae874611656c0b6c6bcad"
        ),
        "raw_size": 6260,
    },
    "execution.skills.troubleshooter_core": {
        "content_digest": (
            "sha256:7cc8d9a63fb6ed147c92fa57d4dcd4baf"
            "72211ddd18bc24fccab058ee052abb0"
        ),
        "byte_length": 7487,
        "raw_sha256": (
            "252199835063cbe0540c81e0ec3c9ac408d9e09c"
            "b5acbf931eba9f5bf8cf87b1"
        ),
        "raw_size": 7487,
    },
}
_C23_FAILURE_KINDS = (
    "invocation_failed",
    "output_too_large",
    "result_parse_failed",
    "result_rejected",
    "timeout",
)
_C23_EXECUTION_ACTIONS = {
    "lad_builder": "execution.recover_builder_runtime_failure",
    "lad_checker": "execution.recover_checker_runtime_failure",
    "lad_fixer": "execution.recover_fixer_runtime_failure",
    "lad_doublechecker": "execution.recover_doublechecker_runtime_failure",
    "lad_updater": "execution.recover_updater_runtime_failure",
    "lad_troubleshooter": "execution.recover_troubleshooter_runtime_failure",
    "lad_consultant": "execution.recover_consultant_runtime_failure",
    "lad_integrator": "execution.recover_integrator_runtime_failure",
}
_C23_PLANNING_ACTIONS = {
    "recon": "planning.recover_recon_runtime_failure",
    "lad_planner": "planning.recover_planner_runtime_failure",
    "lad_manager": "planning.recover_manager_runtime_failure",
    "lad_mechanic": "planning.recover_mechanic_runtime_failure",
    "lad_auditor": "planning.recover_auditor_runtime_failure",
    "lad_arbiter": "planning.recover_arbiter_runtime_failure",
}
_C23_ASSETS = {
    "execution.entrypoints.lad_troubleshooter": {
        "content_digest": (
            "sha256:f0c232be00594219ba2670490efc0e7b"
            "58c6a1a0020ce75748e86933b1724415"
        ),
        "byte_length": 7255,
        "raw_sha256": (
            "4af3558a4e71a7a248fed377b5e624f4"
            "554541b897b473897b0aee129cd4720a"
        ),
        "raw_size": 7255,
    },
    "planning.entrypoints.lad_mechanic": {
        "content_digest": (
            "sha256:79aeff093cfd5047e6cb213e9eca9600"
            "d5654dba8a8c6e3a256e2ed533b93224"
        ),
        "byte_length": 8575,
        "raw_sha256": (
            "1c35289617e03c33d13bf6858d20b946"
            "d94111a3b774decc64f5fb166715eb4b"
        ),
        "raw_size": 8575,
    },
}
_C24_MANAGER_ASSETS = {
    "planning.entrypoints.lad_manager": {
        "content_digest": (
            "sha256:b48b106bfe124940e96ccea8b95c6c12"
            "2762b80376990284f6ae2c30b228d2ca"
        ),
        "byte_length": 3808,
        "raw_sha256": (
            "4f6f11827dc03aaebd3853b69e9b81c0"
            "0fb586d0bd40b2f9a275b23d713e43ee"
        ),
        "raw_size": 3808,
    },
    "planning.skills.manager_core": {
        "content_digest": (
            "sha256:5669e37c99f4afba61e3a75f1a3bc176"
            "07d81467e4909cd78f7d0c9d54796f5c"
        ),
        "byte_length": 6178,
        "raw_sha256": (
            "14b412a81b5e71033c4aa72dd98d5102"
            "29665d57b30b6aae96bd624833e3a80b"
        ),
        "raw_size": 6178,
    },
}
_C24_TROUBLESHOOTER_ASSETS = {
    "execution.entrypoints.lad_troubleshooter": {
        "content_digest": (
            "sha256:444919e2e5637486ff4e9414734a285c"
            "a70eae21e942331165a9a58a2d75fb38"
        ),
        "byte_length": 8030,
        "raw_sha256": (
            "74a2e9a01a7cd34ed48867083bc07adf"
            "8177759ab3007e34856c2a7bfa475251"
        ),
        "raw_size": 8030,
    },
    "execution.skills.troubleshooter_core": {
        "content_digest": (
            "sha256:4a176a8600b0d3568f0dcfd1264a90c3"
            "bac084eb2bc86ddcae39f8a44a31abeb"
        ),
        "byte_length": 7847,
        "raw_sha256": (
            "803a4a5f8f6b880ff5ab6ea6f13d748"
            "a0a1c7b5477e9d8e2969ae350b637ea63"
        ),
        "raw_size": 7847,
    },
}
_C24_ALL_ASSETS = {**_C24_MANAGER_ASSETS, **_C24_TROUBLESHOOTER_ASSETS}
_C24_TASK_CARD_DEPENDENCY_SCHEMA = {
    "items": {"min_length": 1, "type": "string"},
    "type": "array",
}
_C24_MANAGER_WORKFLOWS = {"planning.lad", "lad.full"}
_C24_TROUBLESHOOTER_WORKFLOWS = {
    "execution.lad",
    "execution.lad_integrator",
    "planning.lad",
    "lad.full",
}
_C24_MANAGER_FANOUT_ID = "planning.manager.task_cards_to_execution"
_C24_MANAGER_SUCCESS_ACTIONS = ["execution.close_updater_complete"]
_C23_WORKFLOW_ROLES = {
    "execution.lad": {
        **{
            stage: action
            for stage, action in _C23_EXECUTION_ACTIONS.items()
            if stage != "lad_integrator"
        }
    },
    "execution.lad_integrator": dict(_C23_EXECUTION_ACTIONS),
    "planning.lad": {
        **{
            stage: action
            for stage, action in _C23_EXECUTION_ACTIONS.items()
            if stage != "lad_integrator"
        },
        **_C23_PLANNING_ACTIONS,
    },
    "lad.full": {
        **{
            stage: action
            for stage, action in _C23_EXECUTION_ACTIONS.items()
            if stage != "lad_integrator"
        },
        **_C23_PLANNING_ACTIONS,
    },
}


def _fixture() -> dict[str, Any]:
    assert _raw_sha256(FIXTURE_PATH) == _FIXTURE_SHA256
    return json.loads(FIXTURE_PATH.read_text())


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _original_workflows_with_c19_allowance(
    workflows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_workflows = deepcopy(workflows)
    changed_selectors: set[tuple[str, str]] = set()
    updated_asset_refs: set[tuple[tuple[str, str], str]] = set()
    expected_asset_refs = {
        (selector, asset_id)
        for selector in _C19_WORKFLOW_SELECTORS
        for asset_id in _C19_ASSETS
    }

    for workflow in expected_workflows:
        selector = (
            str(workflow["workflow_id"]),
            str(workflow["workflow_version"]),
        )
        if selector not in _C19_WORKFLOW_SELECTORS:
            continue

        actions = workflow["selected_authority"]["terminal_actions"]
        complete_actions = [
            action
            for action in actions
            if action["id"] == "execution.return_troubleshooter_complete"
        ]
        blocked_actions = [
            action
            for action in actions
            if action["id"] == "execution.route_builder_blocked"
        ]
        assert len(complete_actions) == 1
        assert len(blocked_actions) == 1
        complete = complete_actions[0]
        blocked = blocked_actions[0]
        qa_context = complete["payload_projection"]["fields"]["qa_context"]
        assert qa_context == {
            "kind": "source",
            "path": ["work_item_payload", "qa_context"],
        }
        complete["payload_projection"]["fields"]["qa_context"] = {
            "kind": "coalesce",
            "candidates": [
                {
                    "kind": "source",
                    "path": ["work_item_payload", "qa_context"],
                }
            ],
            "default": deepcopy(
                blocked["payload_projection"]["fields"]["qa_context"]
            ),
        }
        changed_selectors.add(selector)

        for asset in workflow["required_assets"]:
            asset_id = str(asset["asset_id"])
            if asset_id in _C19_ASSETS:
                asset["content_digest"] = _C19_ASSETS[asset_id]["content_digest"]
                updated_asset_refs.add((selector, asset_id))

    assert changed_selectors == _C19_WORKFLOW_SELECTORS
    assert updated_asset_refs == expected_asset_refs
    return expected_workflows


def _original_assets_with_c19_allowance(
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_assets = deepcopy(assets)
    changed_asset_ids: set[str] = set()
    for item in expected_assets:
        record = item["record"]
        asset_id = str(record["asset_id"])
        if asset_id not in _C19_ASSETS:
            continue

        allowance = _C19_ASSETS[asset_id]
        record["content_digest"] = allowance["content_digest"]
        record["byte_length"] = allowance["byte_length"]
        item["raw"]["sha256"] = allowance["raw_sha256"]
        item["raw"]["size"] = allowance["raw_size"]
        changed_asset_ids.add(asset_id)

    assert changed_asset_ids == set(_C19_ASSETS)
    return expected_assets


def _original_workflows_with_c23_allowance(
    workflows: list[dict[str, Any]], current_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Allow selected runtime-failure additions.

    Their contracts are tested separately.
    """
    expected_workflows = _original_workflows_with_c19_allowance(workflows)
    for workflow in expected_workflows:
        workflow_id = str(workflow["workflow_id"])
        roles = _C23_WORKFLOW_ROLES.get(workflow_id)
        if roles is None:
            continue
        current = current_by_id[workflow_id]
        expected_authority = workflow["selected_authority"]
        current_authority = current["selected_authority"]

        current_bindings = {
            str(binding["stage_kind_ids"][0]): binding
            for binding in current_authority["runner_bindings"]
        }
        expected_bindings = {
            str(binding["stage_kind_ids"][0]): binding
            for binding in expected_authority["runner_bindings"]
        }
        for stage_id, action_id in roles.items():
            mappings = [
                {
                    "stage_kind_id": stage_id,
                    "failure_kind": failure_kind,
                    "action_id": action_id,
                }
                for failure_kind in _C23_FAILURE_KINDS
            ]
            current_mapping = current_bindings[stage_id].get("runtime_failure_mappings")
            assert current_mapping == mappings
            expected_bindings[stage_id]["runtime_failure_mappings"] = mappings

        new_action_ids = {"execution.recover_consultant_runtime_failure"}
        new_outcome_id_by_stage = {
            "lad_consultant": "execution.lad_consultant.runtime_failed"
        }
        if workflow_id in {"planning.lad", "lad.full"}:
            new_action_ids.update(_C23_PLANNING_ACTIONS.values())
            new_outcome_id_by_stage.update(
                {
                    stage_id: f"planning.{stage_id}.runtime_failed"
                    for stage_id in _C23_PLANNING_ACTIONS
                }
            )

        current_actions = current_authority["terminal_actions"]
        expected_actions = expected_authority["terminal_actions"]
        actual_new_actions = [
            action for action in current_actions if action["id"] in new_action_ids
        ]
        assert {action["id"] for action in actual_new_actions} == new_action_ids
        expected_actions.extend(deepcopy(actual_new_actions))
        new_outcome_ids = {action["outcome_id"] for action in actual_new_actions}
        current_outcomes = current_authority["terminal_outcomes"]
        expected_outcomes = expected_authority["terminal_outcomes"]
        actual_new_outcomes = [
            outcome for outcome in current_outcomes if outcome["id"] in new_outcome_ids
        ]
        assert {outcome["id"] for outcome in actual_new_outcomes} == new_outcome_ids
        assert new_outcome_ids == set(new_outcome_id_by_stage.values())
        expected_outcomes.extend(deepcopy(actual_new_outcomes))
        for stage_id, outcome_id in new_outcome_id_by_stage.items():
            stage = next(
                item
                for item in expected_authority["stage_kinds"]
                if item["id"] == stage_id
            )
            assert outcome_id in next(
                item
                for item in current_authority["stage_kinds"]
                if item["id"] == stage_id
            )["declared_outcome_ids"]
            stage["declared_outcome_ids"].append(outcome_id)

        current_policies = {
            item["id"]: item for item in current_authority["recovery_policies"]
        }
        expected_policies = {
            item["id"]: item for item in expected_authority["recovery_policies"]
        }
        runtime_policy_id = "execution.runtime_failure_recovery"
        for field in ("source_recovery_action_ids", "reset_trigger_action_ids"):
            expected_policies[runtime_policy_id][field] = deepcopy(
                current_policies[runtime_policy_id][field]
            )
        if workflow_id in {"planning.lad", "lad.full"}:
            expected_authority["recovery_policies"].append(
                deepcopy(current_policies["planning.runtime_failure_recovery"])
            )
            cooldown_id = "planning.runtime_failure_recovery.cooldown"
            current_wait = next(
                item
                for item in current_authority["wait_states"]
                if item["id"] == cooldown_id
            )
            expected_authority["wait_states"].append(deepcopy(current_wait))

        for asset in workflow["required_assets"]:
            asset_id = str(asset["asset_id"])
            if asset_id in _C23_ASSETS:
                asset["content_digest"] = _C23_ASSETS[asset_id]["content_digest"]

    return expected_workflows


def _original_workflows_with_c24_allowance(
    workflows: list[dict[str, Any]], current_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Allow only the selected typed Manager dependency contract and its pins."""
    expected_workflows = _original_workflows_with_c23_allowance(
        workflows,
        current_by_id,
    )
    changed_selectors: set[tuple[str, str]] = set()
    updated_asset_refs: set[tuple[tuple[str, str], str]] = set()
    expected_asset_refs = {
        (selector, asset_id)
        for workflow_id in _C24_MANAGER_WORKFLOWS
        for selector in ((workflow_id, "0.1"),)
        for asset_id in _C24_MANAGER_ASSETS
    }
    expected_asset_refs.update(
        {
            ((workflow_id, "0.1"), asset_id)
            for workflow_id in _C24_TROUBLESHOOTER_WORKFLOWS
            for asset_id in _C24_TROUBLESHOOTER_ASSETS
        }
    )
    expected_workflow_selectors = {
        (workflow_id, "0.1")
        for workflow_id in _C24_MANAGER_WORKFLOWS
        | _C24_TROUBLESHOOTER_WORKFLOWS
    }

    for workflow in expected_workflows:
        workflow_id = str(workflow["workflow_id"])
        if workflow_id in _C24_TROUBLESHOOTER_WORKFLOWS:
            selector = (workflow_id, str(workflow["workflow_version"]))
            for asset in workflow["required_assets"]:
                asset_id = str(asset["asset_id"])
                if asset_id in _C24_TROUBLESHOOTER_ASSETS:
                    asset["content_digest"] = _C24_TROUBLESHOOTER_ASSETS[
                        asset_id
                    ]["content_digest"]
                    updated_asset_refs.add((selector, asset_id))
        if workflow_id not in _C24_MANAGER_WORKFLOWS:
            continue
        selector = (workflow_id, str(workflow["workflow_version"]))
        selected = workflow["selected_authority"]
        task_cards = next(
            schema
            for schema in selected["artifact_schemas"]
            if schema["id"] == "planning.artifacts.task_cards"
        )
        card_schema = task_cards["schema"]["properties"]["cards"]["items"]
        card_schema["properties"]["depends_on_task_card_ids"] = deepcopy(
            _C24_TASK_CARD_DEPENDENCY_SCHEMA
        )
        card_schema["required"].append("depends_on_task_card_ids")

        fanout = next(
            item
            for item in selected["fanout_declarations"]
            if item["id"] == _C24_MANAGER_FANOUT_ID
        )
        fanout["item_dependency_key"] = "depends_on_task_card_ids"
        fanout["dependency_success_action_ids"] = deepcopy(
            _C24_MANAGER_SUCCESS_ACTIONS
        )
        changed_selectors.add(selector)

        for asset in workflow["required_assets"]:
            asset_id = str(asset["asset_id"])
            if asset_id in _C24_MANAGER_ASSETS:
                asset["content_digest"] = _C24_MANAGER_ASSETS[asset_id][
                    "content_digest"
                ]
                updated_asset_refs.add((selector, asset_id))

    assert changed_selectors == {
        (workflow_id, "0.1") for workflow_id in _C24_MANAGER_WORKFLOWS
    }
    assert updated_asset_refs == expected_asset_refs
    assert {
        selector for selector, _asset_id in updated_asset_refs
    } == expected_workflow_selectors
    return expected_workflows


def _original_assets_with_c23_allowance(
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_assets = _original_assets_with_c19_allowance(assets)
    changed_asset_ids: set[str] = set()
    for item in expected_assets:
        record = item["record"]
        asset_id = str(record["asset_id"])
        if asset_id not in _C23_ASSETS:
            continue
        allowance = _C23_ASSETS[asset_id]
        for key in ("content_digest", "byte_length"):
            record[key] = allowance[key]
        item["raw"]["sha256"] = allowance["raw_sha256"]
        item["raw"]["size"] = allowance["raw_size"]
        changed_asset_ids.add(asset_id)
    assert changed_asset_ids == set(_C23_ASSETS)
    return expected_assets


def _original_assets_with_c24_allowance(
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_assets = _original_assets_with_c23_allowance(assets)
    changed_asset_ids: set[str] = set()
    for item in expected_assets:
        record = item["record"]
        asset_id = str(record["asset_id"])
        if asset_id not in _C24_ALL_ASSETS:
            continue
        allowance = _C24_ALL_ASSETS[asset_id]
        for key in ("content_digest", "byte_length"):
            record[key] = allowance[key]
        item["raw"]["sha256"] = allowance["raw_sha256"]
        item["raw"]["size"] = allowance["raw_size"]
        changed_asset_ids.add(asset_id)
    assert changed_asset_ids == set(_C24_ALL_ASSETS)
    return expected_assets


def test_original_six_workflows_match_historical_records_with_c24_allowances() -> None:
    fixture = _fixture()
    manifest = conformance.load_manifest(PACKAGE_ROOT)
    by_id = conformance.workflows_by_id(manifest)
    selectors = {
        (str(workflow["workflow_id"]), str(workflow["workflow_version"]))
        for workflow in manifest["workflows"]
        if workflow["visibility"] == "public"
    }
    expected_selectors = {
        (str(item["workflow_id"]), str(item["workflow_version"]))
        for item in fixture["workflow_selectors"]
    }
    assert selectors == expected_selectors
    assert len(fixture["workflows"]) == 6
    for expected in _original_workflows_with_c24_allowance(
        fixture["workflows"], by_id
    ):
        selector = (str(expected["workflow_id"]), str(expected["workflow_version"]))
        assert by_id[selector[0]] == expected


def test_original_asset_records_match_with_c24_allowances() -> None:
    fixture = _fixture()
    manifest = conformance.load_manifest(PACKAGE_ROOT)
    by_id = conformance.assets_by_id(manifest)
    expected_assets = _original_assets_with_c24_allowance(fixture["assets"])
    assert len(expected_assets) == 63
    assert len(by_id) == 65
    for item in expected_assets:
        record = item["record"]
        asset_id = str(record["asset_id"])
        assert by_id[asset_id] == record
        path = PACKAGE_ROOT / str(record["package_path"])
        raw = item["raw"]
        assert _raw_sha256(path) == raw["sha256"]
        assert path.stat().st_size == raw["size"]
        assert f"{stat.S_IMODE(path.stat().st_mode):04o}" == raw["mode"]


def test_candidate_partition_has_exact_demo_and_pi_records_and_assets() -> None:
    fixture = _fixture()
    manifest = conformance.load_manifest(PACKAGE_ROOT)
    original_ids = {str(item["record"]["asset_id"]) for item in fixture["assets"]}
    expected_added_asset_ids = {
        "demo.entrypoint",
        "demo.stages",
        "runner.pi_rpc_terminal",
        *(f"lad.full.pi.entrypoints.{name}" for name in (
            "recon",
            "lad_planner",
            "lad_manager",
            "lad_mechanic",
            "lad_auditor",
            "lad_arbiter",
            "lad_builder",
            "lad_checker",
            "lad_fixer",
            "lad_doublechecker",
            "lad_updater",
            "lad_troubleshooter",
            "lad_consultant",
            "analyst",
            "professor",
            "curator",
            "librarian",
        )),
        *(f"lad.full.pi.skills.{name}_core" for name in (
            "planner",
            "manager",
            "mechanic",
            "auditor",
            "analyst",
            "professor",
            "curator",
        )),
    }
    candidate_ids = set(conformance.assets_by_id(manifest, include_pi=True))
    assert candidate_ids - original_ids == expected_added_asset_ids
    assert candidate_ids == original_ids | expected_added_asset_ids

    original_workflow_selectors = {
        (str(item["workflow_id"]), str(item["workflow_version"]))
        for item in fixture["workflow_selectors"]
    }
    candidate_workflow_selectors = {
        (str(workflow["workflow_id"]), str(workflow["workflow_version"]))
        for workflow in conformance.workflows_by_id(manifest, include_pi=True).values()
    }
    expected_added_workflow_selectors = {
        ("demo", "0.1"),
        ("lad.full.pi", "0.1"),
    }
    assert (
        candidate_workflow_selectors - original_workflow_selectors
        == expected_added_workflow_selectors
    )
    assert (
        candidate_workflow_selectors
        == original_workflow_selectors | expected_added_workflow_selectors
    )
    assert {
        (str(workflow["workflow_id"]), str(workflow["workflow_version"]))
        for workflow in conformance.workflows_by_id(manifest, include_pi=True).values()
        if workflow["visibility"] == "private"
    } == expected_added_workflow_selectors
