---
name: planning-manager-core
description: Use when executing the lad_manager stage for the selected Millrace planning workflow.
---

# Lad Manager Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence and assumptions belong in runner evidence/report text unless the selected schema declares them.

`planning.artifacts.task_cards`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `task_cards` | Must be `task_cards`. |
| `cards` | yes | array; min_items 1; unique by `task_card_id` | Selected task cards. |

Each `cards` item:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `task_card_id` | yes | string; min_length 1 | Stable task-card identifier. |
| `title` | yes | string; min_length 1 | Short task title. |
| `body` | yes | string; min_length 1 | Complete task instruction and verification detail. |
| `depends_on_task_card_ids` | yes | array of non-empty strings; empty is allowed | Exact `task_card_id` values for prerequisite cards in this same artifact. An empty array means this card is independent. |

No additional artifact keys are allowed beyond the dispatch-selected `planning.artifacts.task_cards` schema. Put dependency edges in `depends_on_task_card_ids`; keep owner stages, acceptance criteria, verification notes, assumptions, and evidence in each card `body` or runner report text. Every dependency must name another exact card ID from the same Manager artifact. Avoid duplicate, self, unknown, or cyclic references.

The selected route provides `planning_result` and `source_request`.
`source_request` is authoritative for requested behavior and exact constraints;
`planning_result` is supporting analysis. Task-card bodies must copy exact literals, paths, completion definitions, and constraints required by a downstream owner. A reference such as "use the exact requested content" is insufficient unless that exact content also appears in the card body.

## Handoff Format

Return one complete Pi RPC result object with exactly these top-level fields:

1. `marker`: one exact selected terminal marker.
2. `artifact_payload_candidate`: the exact selected artifact object, or `null` when the selected terminal action has no artifact schema.
3. `observation_payload_candidate`: selected observation fields and a string `runner_report` for narrative evidence, assumptions, checks, references, or gaps.

For `MANAGER_COMPLETE`, copy the exact selected task-card object to both candidates when dispatch selects the same observation payload.

Keep narrative evidence out of the artifact unless the selected schema declares a field for it. Do not add envelope, identity, route, evidence, or downstream-context keys.

## Valid Example

```json
{
  "marker": "MANAGER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "task_cards",
    "cards": [
      {
        "task_card_id": "create-deterministic-result",
        "title": "Create deterministic result file",
        "body": "Create result.txt in the assigned workspace containing exactly: preserved literal. Verify the file exists, its full content is exactly preserved literal., and no paths outside the assigned workspace are used.",
        "depends_on_task_card_ids": []
      }
    ]
  },
  "observation_payload_candidate": {
    "artifact_kind": "task_cards",
    "cards": [
      {
        "task_card_id": "create-deterministic-result",
        "title": "Create deterministic result file",
        "body": "Create result.txt in the assigned workspace containing exactly: preserved literal. Verify the file exists, its full content is exactly preserved literal., and no paths outside the assigned workspace are used.",
        "depends_on_task_card_ids": []
      }
    ],
    "runner_report": "Manager evidence supports the selected MANAGER_COMPLETE result."
  }
}
```

## Additional Selected Branch Examples

```json
{
  "marker": "BLOCKED",
  "artifact_payload_candidate": null,
  "observation_payload_candidate": {
    "runner_report": "Manager lacks sufficient selected input for a safe result."
  }
}
```

## Invalid Examples

Generic wrapper-as-artifact payload:

```json
{
  "marker": "MANAGER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_id": "bad-lad_manager-wrapper",
    "artifact_kind": "task_cards",
    "fields": {
      "artifact_kind": "task_cards",
      "cards": []
    },
    "next_stage_context": {
      "selected_action_id": "planning.close_manager_complete"
    }
  },
  "observation_payload_candidate": {
    "runner_report": "Manager evidence supports the selected MANAGER_COMPLETE result."
  }
}
```

Invalid because `artifact_payload_candidate` is not the exact `planning.artifacts.task_cards` object selected for the successful marker.

Missing required field:

```json
{
  "marker": "MANAGER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "task_cards"
  },
  "observation_payload_candidate": {
    "runner_report": "Manager evidence supports the selected MANAGER_COMPLETE result."
  }
}
```

Type mismatch:

```json
{
  "marker": "MANAGER_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "task_cards",
    "cards": "not an array"
  },
  "observation_payload_candidate": {
    "runner_report": "Manager evidence supports the selected MANAGER_COMPLETE result."
  }
}
```

## Validation Checklist

- The result is one strict JSON object with exactly `marker`, `artifact_payload_candidate`, and `observation_payload_candidate`.
- The selected marker and artifact candidate match the terminal action's selected schema or null behavior.
- `observation_payload_candidate.runner_report` is text and selected routing fields keep their declared names.
- Required selected artifact fields are present.
- No artifact field is present unless the selected schema declares it.
- The successful artifact body and observation payload candidate are schema-identical when the wrapper asks for an observation payload candidate.
- Observation payload candidates contain no source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, owner-stage fields, dependency arrays, or downstream context unless selected schema declares them.
- Evidence supports the task-card content and marker choice.
- Every card declares its exact same-artifact prerequisites in `depends_on_task_card_ids`; use an empty array for an independent card.
- The dependency lists contain no duplicate, self, unknown, or cyclic references.
- Every source literal, path, completion definition, and constraint needed by
  the owner stage is present verbatim in the task-card body.
- Terminal marker is legal for `lad_manager` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Lad Manager is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the marker.
