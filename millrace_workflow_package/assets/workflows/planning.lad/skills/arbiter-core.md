---
name: planning-arbiter-core
description: Use when executing the Arbiter stage for the selected Millrace planning workflow.
---

# Arbiter Core Skill

## Artifact Schema

Produce one selected artifact declared for the active Arbiter run. The selected dispatch context decides which schema is legal for the current run.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string | Concise Arbiter result. |

`planning.artifacts.rubric`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.rubric`. |
| `summary` | yes | string | Rubric summary. |

`planning.artifacts.verdict`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.verdict`. |
| `summary` | yes | string | Verdict summary. |

`planning.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.report`. |
| `summary` | yes | string | Arbiter evidence report. |

`planning.artifacts.incident_report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `planning.artifacts.incident_report`. |
| `summary` | yes | string | Remediation-gap summary when selected. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_arbiter
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
artifact_id: arbiter-result-1
artifact_kind: planning.artifacts.verdict
produced_by_stage: lad_arbiter
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: ARBITER_COMPLETE
summary: Current evidence satisfies the selected rubric.
fields:
  artifact_kind: planning.artifacts.verdict
  summary: Current evidence satisfies the selected rubric.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `summary`: invalid because the selected verdict or report schema cannot be verified.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Unbacked remediation guidance: invalid because gaps must be tied to evidence.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the verdict, remediation, or blocked marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_arbiter` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Arbiter is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
