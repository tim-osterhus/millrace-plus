---
name: planning-recon-core
description: Use when executing the Recon stage for the selected Millrace planning workflow.
---

# Recon Core Skill

## Artifact Schema

Produce one selected artifact declared for the active Recon run. The selected dispatch context decides which schema is legal for the current run.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string | Concise Recon result. |

`planning.artifacts.recon_packet`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.recon_packet`. |
| `summary` | yes | string | Classification evidence and risk summary. |

`planning.artifacts.generated_task`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.generated_task`. |
| `task_id` | yes | string | Stable generated task identifier. |
| `body` | yes | string | Execution-ready task body. |

`execution.artifacts.task`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `task_id` | yes | string | Stable execution task identifier. |
| `body` | yes | string | Execution-ready task body. |

`planning.artifacts.generated_spec`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.generated_spec`. |
| `spec_id` | yes | string | Stable generated spec identifier. |
| `body` | yes | string | Planning-ready spec body. |

`planning.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.report`. |
| `summary` | yes | string | Blocked, no-op, or supporting report. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: recon
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
artifact_id: recon-result-1
artifact_kind: planning.artifacts.generated_spec
produced_by_stage: recon
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: RECON_TO_PLANNING
summary: The probe needs a Planning spec because scope and acceptance are not yet execution-ready.
fields:
  artifact_kind: planning.artifacts.generated_spec
  spec_id: spec-from-probe-1
  body: Summarized spec body grounded in checked evidence.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `artifact_kind`: invalid when the selected schema requires it.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Claims that Recon chose runtime aftermath instead of returning selected evidence.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the classification and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `recon` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Recon is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
