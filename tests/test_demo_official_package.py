from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from millrace.compiler import authority_fingerprint, compile_workflow
from millrace.compiler.runner_bindings import SelectedRunnerAdapterPolicy
from millrace.operator import (
    PackageMutationCommand,
    PackageWorkflowSelectionCommand,
    PackageWorkflowVerifyCommand,
    execute_package_mutation_command,
    execute_package_verify_command,
    execute_package_workflow_selection_command,
)
from millrace.substrate import ContentAddressedByteStore, SQLiteRuntimeStore

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.4"
DEMO_PROVIDER_VERSION = "0.22.4"
DEMO_ID = "demo"
DEMO_VERSION = "0.1"
PRIVATE_ADAPTER_KIND = "millrace.demo.synthetic"
EXPECTED_DESCRIPTOR_SHA256 = (
    "bf7a28946685360586d9a8ec8ad98d865711bb129334506279308b5718bc905a"
)
ASSET_DIGEST_DOMAIN = b"millrace.wpkg.asset.v1\0"


def _manifest() -> dict[str, Any]:
    return conformance.assert_packaged_asset_closure(PACKAGE_ROOT)


def _demo(manifest: dict[str, Any]) -> dict[str, Any]:
    return next(
        workflow
        for workflow in manifest["workflows"]
        if workflow["workflow_id"] == DEMO_ID
    )


def _private_policy() -> SelectedRunnerAdapterPolicy:
    return SelectedRunnerAdapterPolicy(
        default_adapter_kind=PRIVATE_ADAPTER_KIND,
        supported_adapter_kinds=frozenset({PRIVATE_ADAPTER_KIND}),
        component_bound_adapter_kinds=frozenset({PRIVATE_ADAPTER_KIND}),
        default_invalid_adapter_kinds=False,
        default_component_selector=None,
        default_component_required_capability_ids=frozenset(),
        default_component_requires_complete_mappings=False,
    )


def _demo_source(manifest: dict[str, Any]) -> dict[str, object]:
    workflow = _demo(manifest)
    source = copy.deepcopy(workflow["selected_authority"])
    assets = {str(asset["asset_id"]): asset for asset in manifest["assets"]}
    source["assets"] = [
        {
            "id": asset_id,
            "kind": assets[asset_id]["asset_kind"],
            "body": (PACKAGE_ROOT / str(assets[asset_id]["package_path"])).read_text(),
        }
        for asset_id in sorted(
            str(required["asset_id"]) for required in workflow["required_assets"]
        )
    ]
    return source


def test_private_demo_identity_and_assets_are_exact() -> None:
    manifest = _manifest()
    package = manifest["package"]
    demo = _demo(manifest)

    assert package["package_id"] == PACKAGE_ID
    assert package["package_version"] == PACKAGE_VERSION
    assert demo["workflow_id"] == DEMO_ID
    assert demo["workflow_version"] == DEMO_VERSION
    assert demo["visibility"] == "private"
    assert demo["entrypoints"] == ["default"]

    selected = demo["selected_authority"]
    assert selected["workflow"]["id"] == DEMO_ID
    assert selected["workflow"]["version"] == DEMO_VERSION
    assert selected["workflow"]["name"] == "Zero-key demo"
    assert "demo.proof" not in json.dumps(selected, sort_keys=True)
    assert "millrace-s01-proof" not in json.dumps(selected, sort_keys=True)

    binding = next(
        item for item in selected["runner_bindings"] if item["id"] == "demo.runner"
    )
    assert binding["adapter_kind"] == PRIVATE_ADAPTER_KIND
    expected_pin = {
        "component_kind": "runner",
        "component_id": "demo-synthetic",
        "component_version": "1",
        "provider_distribution": "millrace-ai",
        "provider_version": DEMO_PROVIDER_VERSION,
        "descriptor_media_type": "application/json",
        "required_capability_ids": ["capability.runner.invoke"],
        "legal_terminal_result_ids": [
            "FANOUT",
            "ARTIFACT_A",
            "ARTIFACT_B",
            "EXPECTED_FAIL",
            "CORRECTED",
            "PASS",
            "DECIDE",
            "REVISED",
            "CLOSED",
        ],
        "descriptor_sha256": EXPECTED_DESCRIPTOR_SHA256,
    }
    assert binding["component_pin"] == expected_pin
    descriptor = dict(expected_pin)
    descriptor.pop("descriptor_sha256")
    assert hashlib.sha256(
        json.dumps(
            descriptor, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest() == EXPECTED_DESCRIPTOR_SHA256

    assets = conformance.assets_by_id(manifest)
    expected_assets = {
        "demo.entrypoint": (
            "assets/workflows/demo/entrypoints/demo.md",
            "entrypoint_prompt",
            "sha256:852f76b8cbac16c5ef1e9584d064c3ab4a4a5b8e521a42c54badb7cb84aceef4",
            54,
            "Synthetic demo only. Follow selected stage authority.\n",
        ),
        "demo.stages": (
            "assets/workflows/demo/context/router.md",
            "template",
            "sha256:6bd801d959ba9dd107fcafa0f2a4f3658774a5ff401f948fbbc8e0602f55c80a",
            66,
            "Synthetic context router and stage contract. No external effects.\n",
        ),
    }
    for asset_id, (package_path, kind, digest, length, body) in expected_assets.items():
        asset = assets[asset_id]
        payload = (PACKAGE_ROOT / package_path).read_bytes()
        assert asset["package_path"] == package_path
        assert asset["asset_kind"] == kind
        assert asset["content_digest"] == digest
        assert asset["byte_length"] == length == len(payload)
        assert payload.decode() == body
        assert digest == "sha256:" + hashlib.sha256(
            ASSET_DIGEST_DOMAIN + payload
        ).hexdigest()
    assert {
        (required["asset_id"], required["content_digest"])
        for required in demo["required_assets"]
    } == {
        (asset_id, expected_assets[asset_id][2]) for asset_id in expected_assets
    }


def test_private_demo_preserves_graph_fanout_join_wait_and_context_contract() -> None:
    selected = _demo(_manifest())["selected_authority"]
    graph = selected["graphs"][0]
    assert graph["node_ids"] == [
        "demo.intake",
        "demo.producer_a",
        "demo.producer_b",
        "demo.verify_first",
        "demo.repair",
        "demo.verify_pass",
        "demo.wait",
        "demo.revise",
        "demo.close",
    ]

    fanouts = selected["fanout_declarations"]
    assert {item["target_route_id"] for item in fanouts} == {"producer_a", "producer_b"}
    assert all(item["duplicate_policy"] == "refuse" for item in fanouts)
    join = selected["join_declarations"][0]
    assert join["target_stage_kind_id"] == "verify_first"
    assert join["correlation_key"] == "bundle_id"
    assert join["required_artifact_schema_ids"] == ["branch_a", "branch_b"]
    assert join["missing_policy"] == "wait"

    outcomes = {
        outcome["marker"] for outcome in selected["terminal_outcomes"]
    }
    assert outcomes == {
        "FANOUT",
        "ARTIFACT_A",
        "ARTIFACT_B",
        "EXPECTED_FAIL",
        "CORRECTED",
        "PASS",
        "DECIDE",
        "REVISED",
        "CLOSED",
    }
    actions = {action["id"]: action for action in selected["terminal_actions"]}
    assert actions["demo.wait.decide"]["kind"] == "operator_wait"
    assert (
        actions["demo.verify_first.expected_fail"]["target_stage_kind_id"]
        == "repair"
    )
    assert actions["demo.repair.corrected"]["target_stage_kind_id"] == "verify_pass"
    assert actions["demo.verify_pass.pass"]["target_stage_kind_id"] == "wait"

    operator_wait = selected["operator_waits"][0]
    assert operator_wait["source_action_ids"] == ["demo.wait.decide"]
    assert operator_wait["wait_scope"] == "lineage"
    assert operator_wait["source_work_item_behavior"] == "leave_open"
    assert operator_wait["allowed_resolution_kinds"] == [
        "close_recorded_source",
        "revise_recorded_source",
    ]
    assert operator_wait["target_stage_kind_id"] == "revise"
    assert operator_wait["target_graph_node_id"] == "demo.revise"
    context = selected["context_bindings"][0]
    assert context["id"] == "demo.context"
    assert context["stage_kind_id"] == "wait"
    assert context["router_asset_id"] == "demo.stages"
    assert context["mutation_policy"] == "reconcile_selected_writes"
    assert context["writeback_terminal_action_id"] == "demo.wait.decide"


def test_private_demo_default_policy_refuses_and_selected_policy_compiles() -> None:
    source = _demo_source(_manifest())
    refused = compile_workflow(source)
    assert refused.plan is None
    assert any(diagnostic.severity == "error" for diagnostic in refused.diagnostics)

    compiled = compile_workflow(source, selected_runner_policy=_private_policy())
    assert compiled.plan is not None
    assert not any(
        diagnostic.severity == "error" for diagnostic in compiled.diagnostics
    )
    assert str(compiled.plan.workflow.workflow_id) == DEMO_ID
    assert str(compiled.plan.workflow.workflow_version) == DEMO_VERSION
    assert authority_fingerprint(compiled.plan).startswith("sha256:")


def test_private_demo_package_selection_requires_the_explicit_policy(
    tmp_path: Path,
) -> None:
    store = SQLiteRuntimeStore.initialize(tmp_path / "runtime.sqlite3")
    cas_store = ContentAddressedByteStore(tmp_path / "cas")
    imported = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="demo-import",
            operation_id="package.import_path",
            actor_id="operator:test",
            package_root=PACKAGE_ROOT,
        ),
    )
    enabled = execute_package_mutation_command(
        store,
        cas_store,
        PackageMutationCommand(
            command_id="demo-enable",
            operation_id="package.enable",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
        ),
    )
    selector = dict(
        command_id="demo-verify",
        actor_id="operator:test",
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=DEMO_ID,
        workflow_version=DEMO_VERSION,
        selected_runner_policy=_private_policy(),
    )
    verified = execute_package_verify_command(
        store, cas_store, PackageWorkflowVerifyCommand(**selector)
    )
    selected = execute_package_workflow_selection_command(
        store,
        cas_store,
        PackageWorkflowSelectionCommand(
            command_id="demo-select",
            actor_id="operator:test",
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            workflow_id=DEMO_ID,
            workflow_version=DEMO_VERSION,
            selected_runner_policy=_private_policy(),
        ),
    )
    assert imported.outcome == "succeeded", imported.diagnostics
    assert enabled.outcome == "succeeded", enabled.diagnostics
    assert verified.outcome == "succeeded", verified.diagnostics
    assert verified.plan_ready
    assert selected.outcome == "succeeded", selected.diagnostics
    assert selected.plan is not None
    assert selected.plan.workflow_package_pin.package_id == PACKAGE_ID
    assert selected.plan.workflow_package_pin.package_version == PACKAGE_VERSION
    assert selected.plan.workflow_package_pin.workflow_id == DEMO_ID
    assert selected.plan.workflow_package_pin.workflow_version == DEMO_VERSION
