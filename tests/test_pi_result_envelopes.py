from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from millrace.adapters.pi_rpc_client import parse_candidate
from millrace.compiler import compile_workflow
from millrace.contracts.schema import validate_schema
from millrace.kernel.fanout_policy import fanout_target_payload
from millrace.kernel.lookups import artifact_schema_for

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"
REPAIRED_SKILLS = {
    "lad_planner": "lad.full.pi.skills.planner_core",
    "lad_manager": "lad.full.pi.skills.manager_core",
    "lad_mechanic": "lad.full.pi.skills.mechanic_core",
    "lad_auditor": "lad.full.pi.skills.auditor_core",
    "analyst": "lad.full.pi.skills.analyst_core",
    "professor": "lad.full.pi.skills.professor_core",
    "curator": "lad.full.pi.skills.curator_core",
}
EXPECTED_SHARED_SKILLS = {
    "lad_planner": "planning.skills.planner_core",
    "lad_manager": "planning.skills.manager_core",
    "lad_mechanic": "planning.skills.mechanic_core",
    "lad_auditor": "planning.skills.auditor_core",
    "analyst": "learning.skills.analyst_core",
    "professor": "learning.skills.professor_core",
    "curator": "learning.skills.curator_core",
}
PI_ENVELOPE_KEYS = {
    "marker",
    "artifact_payload_candidate",
    "observation_payload_candidate",
}


def _manifest() -> dict[str, Any]:
    return json.loads((PACKAGE_ROOT / "manifest.json").read_text())


def _workflow(manifest: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    return next(
        workflow
        for workflow in manifest["workflows"]
        if workflow["workflow_id"] == workflow_id
    )


def _assets(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {asset["asset_id"]: asset for asset in manifest["assets"]}


def _skill_asset_id(stage: dict[str, Any]) -> str:
    return next(
        asset_id
        for asset_id in stage["asset_ids"]
        if ".entrypoints." not in asset_id
    )


def _selected_schema_by_marker(
    selected: dict[str, Any], stage_id: str
) -> dict[str, str | None]:
    actions = {action["outcome_id"]: action for action in selected["terminal_actions"]}
    return {
        outcome["marker"]: actions[outcome["id"]].get("artifact_schema_id")
        for outcome in selected["terminal_outcomes"]
        if outcome["stage_kind_id"] == stage_id
        and outcome["marker"]
        and outcome["id"] in actions
        # Runtime failures are emitted by the runtime. Other recovery routes, such as
        # BLOCKED, remain valid Pi result markers.
        and outcome["marker"] != "RUNTIME_FAILURE"
    }


def _examples(section: str) -> list[dict[str, Any]]:
    return [
        json.loads(block)
        for block in re.findall(r"```json\s+(.*?)```", section, re.DOTALL)
    ]


def test_all_selected_pi_entrypoint_skill_pairs_are_resolved_and_transport_aware(
) -> None:
    manifest = _manifest()
    workflow = _workflow(manifest, "lad.full.pi")
    assets = _assets(manifest)
    selected = workflow["selected_authority"]
    original = _workflow(manifest, "lad.full")["selected_authority"]
    original_stages = {stage["id"]: stage for stage in original["stage_kinds"]}

    assert len(selected["stage_kinds"]) == 17
    for stage in selected["stage_kinds"]:
        prompt_id = next(
            asset_id
            for asset_id in stage["asset_ids"]
            if asset_id.startswith("lad.full.pi.entrypoints.")
        )
        prompt = (
            PACKAGE_ROOT / assets[prompt_id]["package_path"]
        ).read_text()
        assert "## Pi RPC transport (`lad.full.pi@0.1`)" in prompt
        assert all(
            f"`{key}`" in prompt
            for key in (
                "marker",
                "artifact_payload_candidate",
                "observation_payload_candidate",
            )
        )
        skill_id = _skill_asset_id(stage)
        assert skill_id in assets
        assert (PACKAGE_ROOT / assets[skill_id]["package_path"]).is_file()
        assert skill_id in {
            pin["asset_id"] for pin in workflow["required_assets"]
        }
        if stage["id"] in REPAIRED_SKILLS:
            assert skill_id == REPAIRED_SKILLS[stage["id"]]
            original_skill_id = EXPECTED_SHARED_SKILLS[stage["id"]]
            assert original_skill_id not in prompt
            assert skill_id in prompt
        else:
            assert skill_id == _skill_asset_id(original_stages[stage["id"]])


def test_pi_skill_examples_parse_and_match_selected_marker_schemas() -> None:
    manifest = _manifest()
    workflow = _workflow(manifest, "lad.full.pi")
    selected = workflow["selected_authority"]
    assets = _assets(manifest)
    stages = {stage["id"]: stage for stage in selected["stage_kinds"]}
    schemas = {
        schema["id"]: schema["schema"]
        for schema in selected["artifact_schemas"]
    }

    for stage_id, expected_skill_id in REPAIRED_SKILLS.items():
        skill_id = _skill_asset_id(stages[stage_id])
        assert skill_id == expected_skill_id
        skill = (PACKAGE_ROOT / assets[skill_id]["package_path"]).read_text()
        valid_section = skill.split("## Valid Example", 1)[1].split(
            "## Invalid Examples", 1
        )[0]
        valid_examples = _examples(valid_section)
        assert valid_examples, stage_id

        expected_markers = _selected_schema_by_marker(selected, stage_id)
        seen_markers: set[str] = set()
        for raw_example in valid_examples:
            candidate = parse_candidate(json.dumps(raw_example), 1_048_576)
            assert set(candidate) == PI_ENVELOPE_KEYS
            marker = candidate["marker"]
            assert marker in expected_markers, (stage_id, marker)
            seen_markers.add(marker)

            observation = candidate["observation_payload_candidate"]
            assert isinstance(observation, dict)
            assert isinstance(observation.get("runner_report"), str)

            schema_id = expected_markers[marker]
            artifact = candidate["artifact_payload_candidate"]
            if schema_id is None:
                assert artifact is None, (stage_id, marker, artifact)
            else:
                assert isinstance(artifact, dict), (stage_id, marker, artifact)
                validation = validate_schema(schemas[schema_id], artifact)
                assert validation.accepted, (stage_id, marker, validation.issues)

            action = next(
                action
                for action in selected["terminal_actions"]
                if action["outcome_id"]
                == next(
                    outcome["id"]
                    for outcome in selected["terminal_outcomes"]
                    if outcome["stage_kind_id"] == stage_id
                    and outcome["marker"] == marker
                )
            )
            selector = action.get("dynamic_target_selector")
            if selector is not None:
                for field_name in selector["field_names"]:
                    assert observation.get(field_name) in selector["targets"]

        assert seen_markers == set(expected_markers), (stage_id, expected_markers)
        assert "BLOCKED" in seen_markers
        null_markers = {
            marker
            for marker, schema_id in expected_markers.items()
            if schema_id is None
        }
        assert null_markers <= seen_markers


def test_pi_planner_complete_examples_satisfy_selected_learning_fanout() -> None:
    manifest = _manifest()
    workflow = _workflow(manifest, "lad.full.pi")
    selected = workflow["selected_authority"]
    assets = _assets(manifest)
    stages = {stage["id"]: stage for stage in selected["stage_kinds"]}
    planner = stages["lad_planner"]
    prompt_id = next(
        asset_id
        for asset_id in planner["asset_ids"]
        if asset_id.startswith("lad.full.pi.entrypoints.")
    )
    skill_id = _skill_asset_id(planner)
    prompt = (PACKAGE_ROOT / assets[prompt_id]["package_path"]).read_text()
    skill = (PACKAGE_ROOT / assets[skill_id]["package_path"]).read_text()

    assert (
        "If selected workflow context says `lad.full` or `lad.full.pi`"
        " and you choose `PLANNER_COMPLETE`"
    ) in prompt
    normalized_skill = " ".join(skill.split())
    assert (
        "When dispatch identity or selected workflow context says `lad.full`"
        " or `lad.full.pi` and the chosen marker is `PLANNER_COMPLETE`"
    ) in normalized_skill

    compiled = compile_workflow(
        conformance.packaged_workflow_source(
            PACKAGE_ROOT,
            "lad.full.pi",
            include_pi=True,
        )
    )
    assert not [
        diagnostic
        for diagnostic in compiled.diagnostics
        if diagnostic.severity == "error"
    ]
    assert compiled.plan is not None
    fanout = next(
        declaration
        for declaration in compiled.plan.fanout_declarations
        if str(declaration.id) == "learning.trigger.planning.planner_complete"
    )
    assert str(fanout.source_action_id) == "planning.route_planner_complete"
    source_schema = artifact_schema_for(
        compiled.plan,
        str(fanout.source_artifact_schema_id),
    )
    target_schema = artifact_schema_for(
        compiled.plan,
        str(fanout.target_payload_schema_id),
    )
    assert source_schema is not None
    assert target_schema is not None

    valid_section = skill.split("## Valid Example", 1)[1].split(
        "## Invalid Examples", 1
    )[0]
    complete_examples = 0
    for example in _examples(valid_section):
        if example["marker"] != "PLANNER_COMPLETE":
            continue
        candidate = parse_candidate(json.dumps(example), 1_048_576)
        artifact = candidate["artifact_payload_candidate"]
        observation = candidate["observation_payload_candidate"]
        learning_requests = artifact["learning_requests"]
        assert learning_requests
        assert observation["learning_requests"] == learning_requests
        assert validate_schema(source_schema.schema, artifact).accepted

        for request in learning_requests:
            child_payload = fanout_target_payload(
                fanout.target_payload_mapping,
                request,
                artifact,
            )
            assert child_payload is not None
            assert validate_schema(target_schema.schema, child_payload).accepted
        complete_examples += 1

    assert complete_examples >= 1


def test_pi_invalid_examples_keep_envelope_and_fail_selected_schema() -> None:
    manifest = _manifest()
    workflow = _workflow(manifest, "lad.full.pi")
    selected = workflow["selected_authority"]
    assets = _assets(manifest)
    stages = {stage["id"]: stage for stage in selected["stage_kinds"]}
    schemas = {
        schema["id"]: schema["schema"]
        for schema in selected["artifact_schemas"]
    }

    for stage_id in REPAIRED_SKILLS:
        skill_id = _skill_asset_id(stages[stage_id])
        skill = (PACKAGE_ROOT / assets[skill_id]["package_path"]).read_text()
        invalid_section = skill.split("## Invalid Examples", 1)[1].split(
            "## Validation Checklist", 1
        )[0]
        invalid_examples = _examples(invalid_section)
        assert invalid_examples, stage_id
        marker_schemas = _selected_schema_by_marker(selected, stage_id)
        issue_reasons: set[str] = set()

        for raw_example in invalid_examples:
            candidate = parse_candidate(json.dumps(raw_example), 1_048_576)
            assert set(candidate) == PI_ENVELOPE_KEYS
            marker = candidate["marker"]
            assert marker in marker_schemas
            schema_id = marker_schemas[marker]
            assert schema_id is not None
            validation = validate_schema(
                schemas[schema_id], candidate["artifact_payload_candidate"]
            )
            assert not validation.accepted, (stage_id, marker)
            issue_reasons.update(issue.reason for issue in validation.issues)

        assert {
            "unexpected_property",
            "missing_required_property",
            "type_mismatch",
        } <= issue_reasons, (stage_id, issue_reasons)


def test_non_pi_workflow_keeps_original_shared_skill_selections() -> None:
    manifest = _manifest()
    workflow = _workflow(manifest, "lad.full")
    stages = {
        stage["id"]: stage
        for stage in workflow["selected_authority"]["stage_kinds"]
    }

    for stage_id, skill_id in EXPECTED_SHARED_SKILLS.items():
        assert _skill_asset_id(stages[stage_id]) == skill_id
