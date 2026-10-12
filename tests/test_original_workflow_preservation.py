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


def test_original_six_workflows_match_historical_records_with_c19_allowance() -> None:
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
    for expected in _original_workflows_with_c19_allowance(fixture["workflows"]):
        selector = (str(expected["workflow_id"]), str(expected["workflow_version"]))
        assert by_id[selector[0]] == expected


def test_original_asset_records_match_with_c19_allowance() -> None:
    fixture = _fixture()
    manifest = conformance.load_manifest(PACKAGE_ROOT)
    by_id = conformance.assets_by_id(manifest)
    expected_assets = _original_assets_with_c19_allowance(fixture["assets"])
    assert len(expected_assets) == 63
    assert len(by_id) == 63
    for item in expected_assets:
        record = item["record"]
        asset_id = str(record["asset_id"])
        assert by_id[asset_id] == record
        path = PACKAGE_ROOT / str(record["package_path"])
        raw = item["raw"]
        assert _raw_sha256(path) == raw["sha256"]
        assert path.stat().st_size == raw["size"]
        assert f"{stat.S_IMODE(path.stat().st_mode):04o}" == raw["mode"]


def test_candidate_partition_has_exact_pi_records_and_assets() -> None:
    fixture = _fixture()
    manifest = conformance.load_manifest(PACKAGE_ROOT)
    original_ids = {str(item["record"]["asset_id"]) for item in fixture["assets"]}
    expected_added_asset_ids = {
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
    expected_added_workflow_selectors = {("lad.full.pi", "0.1")}
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
