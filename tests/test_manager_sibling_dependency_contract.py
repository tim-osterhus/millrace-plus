from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from millrace.contracts.schema import validate_schema

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
WORKFLOW_IDS = ("planning.lad", "lad.full", "lad.full.pi")
MANAGER_FANOUT_ID = "planning.manager.task_cards_to_execution"
CARD_DEPENDENCY_KEY = "depends_on_task_card_ids"
FINAL_CARD_SUCCESS_ACTION = "execution.close_updater_complete"


def _workflow_source(workflow_id: str) -> dict[str, Any]:
    manifest = conformance.load_manifest(PACKAGE_ROOT)
    return next(
        workflow["selected_authority"]
        for workflow in conformance.workflows_by_id(manifest, include_pi=True).values()
        if workflow["workflow_id"] == workflow_id
    )


def _compiled_plan(workflow_id: str) -> object:
    return conformance.compile_packaged_workflow(
        PACKAGE_ROOT,
        workflow_id,
        include_pi=workflow_id == "lad.full.pi",
    )


@pytest.mark.parametrize("workflow_id", WORKFLOW_IDS)
def test_manager_fanout_compiles_typed_same_batch_dependencies(
    workflow_id: str,
) -> None:
    source = _workflow_source(workflow_id)
    task_cards_schema = next(
        item
        for item in source["artifact_schemas"]
        if item["id"] == "planning.artifacts.task_cards"
    )["schema"]
    card_schema = task_cards_schema["properties"]["cards"]["items"]
    dependency_schema = card_schema["properties"][CARD_DEPENDENCY_KEY]

    assert CARD_DEPENDENCY_KEY in card_schema["required"]
    assert not {"depends_on", "dependency_ids", "dependencies"}.intersection(
        card_schema["properties"]
    )
    assert dependency_schema == {
        "items": {"min_length": 1, "type": "string"},
        "type": "array",
    }

    valid_cards = {
        "artifact_kind": "task_cards",
        "cards": [
            {
                "task_card_id": "m06",
                "title": "Build final package",
                "body": "Use the outputs from the prerequisite cards.",
                CARD_DEPENDENCY_KEY: ["m02"],
            },
            {
                "task_card_id": "m02",
                "title": "Prepare input",
                "body": "Create the input for later work.",
                CARD_DEPENDENCY_KEY: [],
            },
        ],
    }
    compiled_schema = next(
        item
        for item in _compiled_plan(workflow_id).artifact_schemas
        if str(item.id) == "planning.artifacts.task_cards"
    )
    assert validate_schema(compiled_schema.schema, valid_cards).accepted

    missing_dependencies = {
        "artifact_kind": "task_cards",
        "cards": [
            {
                "task_card_id": "independent",
                "title": "Independent",
                "body": "This card is independent.",
            }
        ],
    }
    malformed_dependencies = {
        "artifact_kind": "task_cards",
        "cards": [
            {
                "task_card_id": "malformed",
                "title": "Malformed dependency list",
                "body": "The field must be an array of exact card IDs.",
                CARD_DEPENDENCY_KEY: "m01",
            }
        ],
    }
    assert not validate_schema(
        compiled_schema.schema,
        missing_dependencies,
    ).accepted
    assert not validate_schema(
        compiled_schema.schema,
        malformed_dependencies,
    ).accepted

    plan = _compiled_plan(workflow_id)
    fanout = next(
        item for item in plan.fanout_declarations if str(item.id) == MANAGER_FANOUT_ID
    )
    assert fanout.schema_version == 3
    assert fanout.item_dependency_key == CARD_DEPENDENCY_KEY
    assert tuple(map(str, fanout.dependency_success_action_ids)) == (
        FINAL_CARD_SUCCESS_ACTION,
    )
    assert fanout.dependency_policy == "depends_on_source_work_item"
    assert fanout.source_state_policy == "source_closed"
    assert fanout.item_id_key == "task_card_id"
    assert fanout.item_source_path == ("cards",)
    assert fanout.target_payload_mapping == {
        "body": ("body",),
        "task_id": ("task_card_id",),
    }

    completion = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == FINAL_CARD_SUCCESS_ACTION
    )
    assert completion.action_kind == "complete_work_item"
    assert str(completion.artifact_schema_id) == "execution.artifacts.report"


@pytest.mark.parametrize(
    ("workflow_id", "asset_root"),
    (
        ("planning.lad", "assets/workflows/planning.lad"),
        ("lad.full.pi", "assets/workflows/lad.full.pi"),
    ),
)
def test_manager_instructions_keep_dependencies_typed(
    workflow_id: str,
    asset_root: str,
) -> None:
    root = PACKAGE_ROOT / asset_root
    entrypoint = (root / "entrypoints/lad_manager.md").read_text()
    skill = (root / "skills/manager-core.md").read_text()

    assert CARD_DEPENDENCY_KEY in entrypoint
    assert "same Manager artifact" in entrypoint
    assert "empty array" in entrypoint
    assert CARD_DEPENDENCY_KEY in skill
    assert "same Manager artifact" in skill
    assert "empty array" in skill
    assert "depends_on" not in entrypoint.replace(CARD_DEPENDENCY_KEY, "")
    assert workflow_id in asset_root
