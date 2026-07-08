---
name: planning-manager-core
description: Use when executing the Manager stage for the selected Millrace planning workflow.
---

# Manager Core Skill

## Artifact Schema

Produce one selected artifact declared for the active Manager run. The selected dispatch context decides which schema is legal for the current run.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string | Concise Manager result. |

`planning.artifacts.task_cards`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `task_cards`. |
| `cards` | yes | array | One or more task-card objects. |
| `cards[].task_card_id` | yes | string | Stable task-card identifier. |
| `cards[].title` | yes | string | Human-readable task-card title. |
| `cards[].body` | yes | string | Execution-ready task-card body. |

`planning.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.report`. |
| `summary` | yes | string | Manager supporting or blocked report. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_manager
source_work_item_id:
source_run_id:
terminal_marker:
summary:
fields:
evidence:
assumptions:
next_stage_context:
```

## Valid Example

```text
artifact_id: manager-result-1
artifact_kind: planning.artifacts.task_cards
produced_by_stage: lad_manager
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: MANAGER_COMPLETE
summary: Produced two ordered task cards grounded in the selected spec.
fields:
  artifact_kind: task_cards
  cards:
    - task_card_id: task-card-1
      title: Implement the focused change
      body: Execution-ready task details.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `cards`: invalid for `planning.artifacts.task_cards`.
- Duplicate `task_card_id`: invalid because selected schema requires unique card identifiers.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the task-card shape and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_manager` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Manager is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
