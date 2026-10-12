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
When dispatch identity or selected workflow context says `lad.full` or
`lad.full.pi` and the chosen marker is `PLANNER_COMPLETE`, do not return a
summary-only `planning.artifacts.stage_result`. Selected full-LAD fanout reads
`learning_requests`; omitting it causes runtime refusal
`invalid_fanout_payload`. Include a schema-valid `learning_requests` array in
both `artifact_payload_candidate` and `observation_payload_candidate`. Derive `root_source` from the
source payload when present, and use a stable `request_id` derived from the
source id.

## Handoff Format

Return one complete Pi RPC result object with exactly these top-level fields:

1. `marker`: one exact selected terminal marker.
2. `artifact_payload_candidate`: the exact selected artifact object, or `null` when the selected terminal action has no artifact schema.
3. `observation_payload_candidate`: selected observation fields and a string `runner_report` for narrative evidence, assumptions, checks, references, or gaps.

For the selected full-LAD Planner-complete route, include the exact selected `learning_requests` in both payload candidates when the selected schema and fanout require them.

Keep narrative evidence out of the artifact unless the selected schema declares a field for it. Do not add envelope, identity, route, evidence, or downstream-context keys.

## Valid Example

```json
{
  "marker": "PLANNER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary.",
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture a bounded workspace-local lesson from this selected work.",
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
  "observation_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary.",
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture a bounded workspace-local lesson from this selected work.",
        "root_source": {
          "kind": "spec",
          "source_id": "source-spec-001"
        },
        "target_skill_id": "workspace-learning-summary",
        "preferred_output_paths": [
          "learning-summary.md"
        ]
      }
    ],
    "runner_report": "Planner evidence supports the selected PLANNER_COMPLETE result."
  }
}
```

## Full LAD Fanout Example

Use this shape only when dispatch-selected schema for `planning.artifacts.stage_result`
declares `learning_requests` and selected full-LAD fanout reads it.

```json
{
  "marker": "PLANNER_COMPLETE",
  "artifact_payload_candidate": {
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
  "observation_payload_candidate": {
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
    ],
    "runner_report": "Planner evidence supports the selected PLANNER_COMPLETE result."
  }
}
```

## Additional Selected Branch Examples

```json
{
  "marker": "BLOCKED",
  "artifact_payload_candidate": null,
  "observation_payload_candidate": {
    "runner_report": "Planner lacks sufficient selected input for a safe result."
  }
}
```

## Invalid Examples

Invalid artifact shape with generic wrapper fields:

```json
{
  "marker": "PLANNER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_id": "bad-lad_planner-wrapper",
    "artifact_kind": "planning.artifacts.stage_result",
    "fields": {
      "artifact_kind": "planning.artifacts.stage_result",
      "summary": "Planner summary."
    },
    "next_stage_context": {
      "selected_action_id": "planning.route_planner_complete"
    }
  },
  "observation_payload_candidate": {
    "runner_report": "Planner evidence supports the selected PLANNER_COMPLETE result."
  }
}
```

Invalid because `artifact_payload_candidate` is not the exact `planning.artifacts.stage_result` object selected for the successful marker.

Missing required field:

```json
{
  "marker": "PLANNER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result",
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture a bounded workspace-local lesson from this selected work.",
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
  "observation_payload_candidate": {
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture a bounded workspace-local lesson from this selected work.",
        "root_source": {
          "kind": "spec",
          "source_id": "source-spec-001"
        },
        "target_skill_id": "workspace-learning-summary",
        "preferred_output_paths": [
          "learning-summary.md"
        ]
      }
    ],
    "runner_report": "Planner evidence supports the selected PLANNER_COMPLETE result."
  }
}
```

Type mismatch:

```json
{
  "marker": "PLANNER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": [
      "not a string"
    ],
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture a bounded workspace-local lesson from this selected work.",
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
  "observation_payload_candidate": {
    "learning_requests": [
      {
        "request_id": "source-spec-001-learning",
        "body": "Capture a bounded workspace-local lesson from this selected work.",
        "root_source": {
          "kind": "spec",
          "source_id": "source-spec-001"
        },
        "target_skill_id": "workspace-learning-summary",
        "preferred_output_paths": [
          "learning-summary.md"
        ]
      }
    ],
    "runner_report": "Planner evidence supports the selected PLANNER_COMPLETE result."
  }
}
```

## Validation Checklist

- The result is one strict JSON object with exactly `marker`, `artifact_payload_candidate`, and `observation_payload_candidate`.
- The selected marker and artifact candidate match the terminal action's selected schema or null behavior.
- `observation_payload_candidate.runner_report` is text and selected routing fields keep their declared names.
- Required selected artifact fields are present.
- No artifact field is present unless the selected schema declares it.
- The successful artifact body and observation/fanout payload candidate are schema-identical when the selected route or fanout validates the stage result payload.
- When selected full-LAD fanout reads `learning_requests`, `artifact_payload_candidate` and `observation_payload_candidate` include the same schema-valid `learning_requests` array.
- Observation/fanout payload candidates contain no source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, or downstream context unless selected schema declares them.
- Evidence supports the summary and marker choice.
- Terminal marker is legal for `lad_planner` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Lad Planner is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the marker.
