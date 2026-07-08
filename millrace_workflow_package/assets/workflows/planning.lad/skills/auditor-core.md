---
name: planning-auditor-core
description: Use when executing the Auditor stage for the selected Millrace planning workflow.
---

# Auditor Core Skill

## Artifact Schema

Produce one selected artifact declared for the active Auditor run. The selected dispatch context decides which schema is legal for the current run.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string | Concise Auditor result. |

`planning.artifacts.incident_report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.incident_report`. |
| `summary` | yes | string | Normalized incident summary. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_auditor
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
artifact_id: auditor-result-1
artifact_kind: planning.artifacts.incident_report
produced_by_stage: lad_auditor
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: AUDITOR_COMPLETE
summary: Normalized the incident evidence and exposed unresolved assumptions for Planning.
fields:
  artifact_kind: planning.artifacts.incident_report
  summary: Normalized the incident evidence and exposed unresolved assumptions for Planning.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `summary`: invalid because the selected incident report schema cannot be verified.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Hidden task decomposition: invalid because Auditor owns incident intake evidence only.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the incident summary and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_auditor` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Auditor is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
