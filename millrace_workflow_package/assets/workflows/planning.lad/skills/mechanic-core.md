---
name: planning-mechanic-core
description: Use when executing the Mechanic stage for the selected Millrace planning workflow.
---

# Mechanic Core Skill

## Artifact Schema

Produce one selected artifact declared for the active Mechanic run. The selected dispatch context decides which schema is legal for the current run.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string | Concise Mechanic result. |

`planning.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.report`. |
| `summary` | yes | string | Repair, recovered, quarantine, or blocked report. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_mechanic
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
artifact_id: mechanic-result-1
artifact_kind: planning.artifacts.report
produced_by_stage: lad_mechanic
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: MECHANIC_COMPLETE
summary: Classified the blocker as malformed planning artifact evidence and repaired the narrow field.
fields:
  artifact_kind: planning.artifacts.report
  summary: Classified the blocker as malformed planning artifact evidence and repaired the narrow field.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `summary`: invalid because the selected report schema cannot be verified.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Broad planning rewrite: invalid because Mechanic owns narrow repair evidence only.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the classification and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_mechanic` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Mechanic is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
