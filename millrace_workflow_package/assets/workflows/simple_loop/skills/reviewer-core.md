---
name: simple-loop-reviewer-core
description: Use when executing the Reviewer stage for the selected simple_loop workflow.
---

# Reviewer Core Skill

## Artifact Schema

Produce one response envelope matching the terminal marker. `GAPS_FOUND` and
`INCIDENT_REQUIRED` produce selected artifacts. `ACCEPTED` returns review
acceptance evidence without claiming a selected artifact schema.

Review acceptance evidence

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `summary` | yes | string | Review conclusion. |
| `evidence` | yes | array | Completion checks performed. |
| `assumptions` | yes | array | Assumptions or risks. |

`simple_loop.gap_packet`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.gap_packet`. |
| `gaps` | yes | array | Specific gaps found. |
| `evidence` | yes | array | Review facts checked. |
| `assumptions` | yes | array | Assumptions or risks. |

`simple_loop.incident_report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.incident_report`. |
| `reason` | yes | string | Incident reason. |
| `evidence` | yes | array | Incident facts checked. |
| `assumptions` | yes | array | Assumptions or risks. |

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: simple_loop.reviewer
source_work_item_id:
source_run_id:
terminal_marker:
summary:
fields:
evidence:
assumptions:
handoff_context:
```

## Valid Example

```text
artifact_id: review-1
artifact_kind: simple_loop.gap_packet
produced_by_stage: simple_loop.reviewer
source_work_item_id: work-3
terminal_marker: GAPS_FOUND
summary: Completion definition was partly satisfied.
fields:
  gaps:
    - Missing validation evidence.
evidence:
  - Work result summary did not name the validation command.
assumptions: []
handoff_context:
  reviewed_completion_definition: Validation evidence must be present.
```

## Invalid Examples

- `ACCEPTED` with unchecked completion criteria: invalid because unchecked acceptance is unsupported.
- Gap packet without specific gaps: invalid because Worker cannot verify the issue.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the chosen artifact are present.
- Evidence supports acceptance, gaps, or incident findings.
- Assumptions and unresolved risks are explicit.
- Terminal marker is legal for `simple_loop.reviewer`.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The Reviewer stage is complete only when it returns one valid response envelope, supporting evidence, assumptions, and one legal terminal marker.
