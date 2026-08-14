from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import millforge
import millrace.compiler as compiler
import millrace.contracts as contracts
import millrace.operator as operator
import millrace.substrate as substrate
import millrace.workflows as workflows
import pytest
from millforge import describe_millforge_base
from millrace.compiler import authority_fingerprint, canonical_authority_bytes

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
PACKAGE_ID = "millrace.plus.official"
PACKAGE_VERSION = "0.22.2"
WORKFLOW_IDS = (
    "simple_loop",
    "execution.lad",
    "execution.lad_integrator",
    "planning.lad",
    "lad.full",
    "vendor_selection",
)
CODEX_WORKFLOW_IDS = (
    "execution.lad_codex_control",
    "execution.lad_codex_semantic_worktree",
)
CODEX_COMPONENT_ID = "millrace-codex-wrapper"


def _canonical_payload_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _codex_descriptor_digest(runner: dict[str, object]) -> str:
    component = cast(dict[str, object], runner["component_pin"])
    stage_kind_id = cast(list[object], runner["stage_kind_ids"])[0]
    descriptor = {
        "record_kind": "millrace.codex.wrapper_component_descriptor",
        "wrapper_schema_version": 4,
        "provider_distribution": "@openai/codex",
        "provider_version": "0.147.0",
        "stage_kind_id": stage_kind_id,
        "required_capability_ids": component["required_capability_ids"],
        "legal_terminal_result_ids": component["legal_terminal_result_ids"],
    }
    return hashlib.sha256(_canonical_payload_bytes(descriptor)).hexdigest()


def _manifest() -> dict[str, Any]:
    return conformance.assert_packaged_asset_closure(PACKAGE_ROOT)


def test_installed_integration_modules_publish_the_names_plus_uses() -> None:
    required = {
        compiler: {
            "authority_fingerprint",
            "canonical_authority_bytes",
        },
        operator: {
            "PackageMutationCommand",
            "PackageWorkflowSelectionCommand",
            "PackageWorkflowVerifyCommand",
            "execute_package_mutation_command",
            "execute_package_verify_command",
            "execute_package_workflow_selection_command",
        },
        substrate: {"ContentAddressedByteStore", "SQLiteRuntimeStore"},
        workflows: {
            "INCLUDED_WORKFLOW_IDS",
            "included_workflow_source",
            "included_workflows",
        },
        contracts: set(),
    }
    for module, names in required.items():
        assert names <= set(module.__all__)
    assert "describe_millforge_base" in millforge.__all__


def test_manifest_runner_pins_match_installed_millforge_descriptor() -> None:
    descriptor = describe_millforge_base()
    manifest = _manifest()
    bindings_by_workflow: dict[str, list[dict[str, object]]] = {}
    pins_by_workflow: dict[str, list[dict[str, object]]] = {}
    for workflow_id, workflow in conformance.workflows_by_id(manifest).items():
        selected = cast(dict[str, object], workflow["selected_authority"])
        bindings = cast(list[dict[str, object]], selected["runner_bindings"])
        bindings_by_workflow[workflow_id] = bindings
        pins_by_workflow[workflow_id] = [
            cast(dict[str, object], binding["component_pin"])
            for binding in bindings
        ]

    expected_component_ids_by_workflow = {
        **{workflow_id: {descriptor.runner_id} for workflow_id in WORKFLOW_IDS},
        **{
            workflow_id: {CODEX_COMPONENT_ID}
            for workflow_id in CODEX_WORKFLOW_IDS
        },
    }
    assert set(pins_by_workflow) == set(expected_component_ids_by_workflow)
    assert {
        workflow_id: {
            str(pin["component_id"])
            for pin in pins_by_workflow[workflow_id]
        }
        for workflow_id in expected_component_ids_by_workflow
    } == expected_component_ids_by_workflow
    assert {
        str(pin["component_id"])
        for pins in pins_by_workflow.values()
        for pin in pins
    } == {descriptor.runner_id, CODEX_COMPONENT_ID}

    expected_runner_families = {
        **{workflow_id: "millforge_runner" for workflow_id in WORKFLOW_IDS},
        **{workflow_id: "codex_runner" for workflow_id in CODEX_WORKFLOW_IDS},
    }
    assert {
        workflow_id: {
            str(binding["id"]).rsplit(".", maxsplit=1)[-1]
            for binding in bindings_by_workflow[workflow_id]
        }
        for workflow_id in expected_runner_families
    } == {
        workflow_id: {runner_family}
        for workflow_id, runner_family in expected_runner_families.items()
    }

    pins = [
        pin
        for workflow_id in WORKFLOW_IDS
        for pin in pins_by_workflow[workflow_id]
    ]

    assert pins
    assert {str(pin["component_id"]) for pin in pins} == {descriptor.runner_id}
    assert {str(pin["component_version"]) for pin in pins} == {
        str(descriptor.runner_version)
    }
    assert {str(pin["provider_distribution"]) for pin in pins} == {
        descriptor.package_name
    }
    assert {str(pin["provider_version"]) for pin in pins} == {
        descriptor.package_version
    }

    for workflow_id in CODEX_WORKFLOW_IDS:
        for binding in bindings_by_workflow[workflow_id]:
            pin = cast(dict[str, object], binding["component_pin"])
            assert pin["component_kind"] == "runner"
            assert pin["component_id"] == CODEX_COMPONENT_ID
            assert pin["component_version"] == "4"
            assert pin["provider_distribution"] == "@openai/codex"
            assert pin["provider_version"] == "0.147.0"
            assert pin["descriptor_media_type"] == "application/json"
            assert pin["descriptor_sha256"] == _codex_descriptor_digest(binding)


@pytest.mark.parametrize("workflow_id", WORKFLOW_IDS)
def test_every_official_workflow_compiles_verifies_and_selects_from_package(
    tmp_path: Path, workflow_id: str
) -> None:
    manifest = _manifest()
    plan = conformance.select_and_verify_package(
        tmp_path / workflow_id,
        PACKAGE_ROOT,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=workflow_id,
        workflow_version="0.1",
    )
    conformance.assert_selected_package_pin(
        plan,
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        workflow_id=workflow_id,
        workflow_version="0.1",
        selected_asset_pins=conformance.selected_asset_pins(manifest, workflow_id),
    )
    assert authority_fingerprint(plan).startswith("sha256:")
    assert canonical_authority_bytes(plan)


def test_millrace_base_workflow_inventory_excludes_plus_workflows() -> None:
    assert workflows.INCLUDED_WORKFLOW_IDS == ("kernel_ping",)
    assert tuple(item.workflow_id for item in workflows.included_workflows()) == (
        "kernel_ping",
    )
    for workflow_id in WORKFLOW_IDS:
        with pytest.raises(KeyError):
            workflows.included_workflow_source(workflow_id)
