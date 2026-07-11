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

No additional artifact keys are allowed beyond the dispatch-selected `planning.artifacts.task_cards` schema. Put owner stages, dependencies, acceptance criteria, verification notes, assumptions, and evidence in each card `body` or runner report text; do not add undeclared JSON fields for them.

The selected route provides `planning_result` and `source_request`.
`source_request` is authoritative for requested behavior and exact constraints;
`planning_result` is supporting analysis. Task-card bodies must copy exact literals, paths, completion definitions, and constraints required by a downstream owner. A reference such as "use the exact requested content" is insufficient unless that exact content also appears in the card body.

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
  "terminal_marker": "MANAGER_COMPLETE",
  "artifact": {
    "artifact_kind": "task_cards",
    "cards": [
      {
        "task_card_id": "create-deterministic-result",
        "title": "Create deterministic result file",
        "body": "Create result.txt in the assigned workspace containing exactly: preserved literal. Verify the file exists, its full content is exactly preserved literal., and no paths outside the assigned workspace are used."
      }
    ]
  },
  "observation_payload": {
    "artifact_kind": "task_cards",
    "cards": [
      {
        "task_card_id": "create-deterministic-result",
        "title": "Create deterministic result file",
        "body": "Create result.txt in the assigned workspace containing exactly: preserved literal. Verify the file exists, its full content is exactly preserved literal., and no paths outside the assigned workspace are used."
      }
    ]
  }
}
```

## Invalid Examples

Generic wrapper-as-artifact payload:

```json
{
  "terminal_marker": "MANAGER_COMPLETE",
  "artifact": {
    "artifact_id": "bad-lad_manager-wrapper",
    "artifact_kind": "task_cards",
    "fields": {
      "artifact_kind": "task_cards",
      "cards": []
    },
    "next_stage_context": {
      "selected_action_id": "planning.close_manager_complete"
    }
  }
}
```

Invalid because `artifact` is not the exact `planning.artifacts.task_cards` object selected for the successful marker.

Missing required field:

```json
{
  "terminal_marker": "MANAGER_COMPLETE",
  "artifact": {
    "artifact_kind": "task_cards"
  }
}
```

Type mismatch:

```json
{
  "terminal_marker": "MANAGER_COMPLETE",
  "artifact": {
    "artifact_kind": "task_cards",
    "cards": "not an array"
  }
}
```

## Validation Checklist

- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- The successful artifact body and observation payload candidate are schema-identical when the wrapper asks for an observation payload candidate.
- Observation payload candidates contain no source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, owner-stage fields, dependency arrays, or downstream context unless selected schema declares them.
- Evidence supports the task-card content and marker choice.
- Every source literal, path, completion definition, and constraint needed by
  the owner stage is present verbatim in the task-card body.
- Terminal marker is legal for `lad_manager` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Lad Manager is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the marker.
