---
name: planning-planner-core
description: Use when executing the Planner stage for the selected Millrace planning workflow.
---

# Planner Core Skill

## Artifact Schema

Produce one selected artifact declared for the active Planner run. The selected dispatch context decides which schema is legal for the current run.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string | Concise Planner result. |

`planning.artifacts.generated_spec`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.generated_spec`. |
| `spec_id` | yes | string | Stable generated spec identifier. |
| `body` | yes | string | Planning-ready spec body. |

`planning.artifacts.planner_disposition`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.planner_disposition`. |
| `summary` | yes | string | Planner disposition summary. |

`planning.artifacts.spec`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.spec`. |
| `summary` | yes | string | Current spec summary. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_planner
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
artifact_id: planner-result-1
artifact_kind: planning.artifacts.spec
produced_by_stage: lad_planner
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: PLANNER_COMPLETE
summary: Refined the active input into a bounded spec with assumptions and acceptance checks.
fields:
  artifact_kind: planning.artifacts.spec
  summary: Refined the active input into a bounded spec with assumptions and acceptance checks.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `artifact_kind`: invalid when the selected schema requires it.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Hidden decomposition: invalid because Manager owns task-card shaping when selected.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the summary and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_planner` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Planner is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
