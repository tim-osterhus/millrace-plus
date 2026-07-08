---
name: execution-updater-core
description: Use when executing the Updater stage for a selected Millrace execution workflow.
---

# Updater Core Skill

## Artifact Schema

Produce one selected artifact declared for the active stage. The selected dispatch context decides which schema is legal for the current run.

`execution.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.stage_result`. |
| `summary` | yes | string | Update result summary. |

`execution.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.report`. |
| `summary` | yes | string | Documentation reconciliation evidence. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_updater
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
artifact_id: lad_updater-result-1
artifact_kind: execution.artifacts.stage_result
produced_by_stage: lad_updater
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: UPDATE_COMPLETE
summary: Checked impacted docs, updated stale facts, and recorded why no other surfaces changed.
fields:
  artifact_kind: execution.artifacts.stage_result
  summary: Checked impacted docs, updated stale facts, and recorded why no other surfaces changed.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `artifact_kind`: invalid because the selected artifact schema cannot be verified.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Invents progress or edits generated runtime-owned surfaces without selected evidence.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the summary and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_updater` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The Updater stage is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
