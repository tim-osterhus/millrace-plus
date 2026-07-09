---
name: planning-planner-core
description: Use when executing the lad_planner stage for the selected Millrace planning workflow.
---

# Lad Planner Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence and assumptions belong in runner evidence/report text unless the selected schema declares them.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `planning.artifacts.stage_result` | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |

No additional artifact keys are allowed beyond the dispatch-selected `planning.artifacts.stage_result` schema. Some selected workflows declare optional fields such as `learning_requests`; include them only when dispatch-selected schema declares them and a selected route or fanout reads them.

Full LAD extension, only when dispatch-selected schema declares it:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `learning_requests` | no; required by the selected full-LAD planner fanout when that fanout is selected | array of objects with `request_id`, `body`, and `root_source`; optional `target_skill_id` and `preferred_output_paths` when declared | Learning requests consumed by selected full-LAD fanout. Do not include this field when dispatch-selected schema omits it. |

Critical full-LAD rule:
When dispatch identity or selected workflow context says `lad.full` and the
chosen marker is `PLANNER_COMPLETE`, do not return a summary-only
`planning.artifacts.stage_result`. Selected full-LAD fanout reads
`learning_requests`; omitting it causes runtime refusal
`invalid_fanout_payload`. Include a schema-valid `learning_requests` array in
both `artifact` and `observation_payload`. Derive `root_source` from the
source payload when present, and use a stable `request_id` derived from the
source id.

## Handoff Format

Return:
1. The exact selected terminal marker spelling from dispatch.
2. `artifact` as the exact selected artifact JSON object.
3. `observation_payload` as the exact same selected artifact JSON object when the runtime/wrapper asks for an observation or fanout payload candidate for this successful marker, unless dispatch provides a different selected observation schema.
4. Narrative evidence/report text for checks and assumptions outside the runtime artifact/observation JSON candidates.

Do not put generic wrapper keys, source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, or downstream context into the runtime artifact body or observation/fanout payload candidate unless the selected schema declares them.

## Valid Example

```json
{
  "terminal_marker": "PLANNER_COMPLETE",
  "artifact": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary."
  },
  "observation_payload": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary."
  }
}
```

## Full LAD Fanout Example

Use this shape only when dispatch-selected schema for `planning.artifacts.stage_result`
declares `learning_requests` and selected full-LAD fanout reads it.

```json
{
  "terminal_marker": "PLANNER_COMPLETE",
  "artifact": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary.",
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture safe workspace-local lessons from this selected full-LAD work.",
        "root_source": {
          "kind": "spec",
          "source_id": "source-spec-001"
        },
        "target_skill_id": "workspace-learning-summary",
        "preferred_output_paths": [
          "learning-summary.md"
        ]
      }
    ]
  },
  "observation_payload": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary.",
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture safe workspace-local lessons from this selected full-LAD work.",
        "root_source": {
          "kind": "spec",
          "source_id": "source-spec-001"
        },
        "target_skill_id": "workspace-learning-summary",
        "preferred_output_paths": [
          "learning-summary.md"
        ]
      }
    ]
  }
}
```

## Invalid Examples

Generic wrapper-as-fanout payload:

```json
{
  "terminal_marker": "PLANNER_COMPLETE",
  "artifact": {
    "artifact_id": "bad-lad_planner-wrapper",
    "artifact_kind": "planning.artifacts.stage_result",
    "fields": {
      "artifact_kind": "planning.artifacts.stage_result",
      "summary": "Planner summary."
    },
    "next_stage_context": {
      "selected_action_id": "planning.route_planner_complete"
    }
  }
}
```

Invalid because `artifact` is not the exact `planning.artifacts.stage_result` object selected for the successful marker.

Missing required field:

```json
{
  "terminal_marker": "PLANNER_COMPLETE",
  "artifact": {
    "artifact_kind": "planning.artifacts.stage_result"
  }
}
```

Type mismatch:

```json
{
  "terminal_marker": "PLANNER_COMPLETE",
  "artifact": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": ["not a string"]
  }
}
```

## Validation Checklist

- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- The successful artifact body and observation/fanout payload candidate are schema-identical when the selected route or fanout validates the stage result payload.
- When selected full-LAD fanout reads `learning_requests`, `artifact` and `observation_payload` include the same schema-valid `learning_requests` array.
- Observation/fanout payload candidates contain no source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, or downstream context unless selected schema declares them.
- Evidence supports the summary and marker choice.
- Terminal marker is legal for `lad_planner` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Lad Planner is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the marker.
