---
name: simple-loop-worker-core
description: Use when executing the Worker stage for the selected simple_loop workflow.
---

# Worker Core Skill

## Artifact Schema

Produce one of these artifacts.

`simple_loop.work_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.work_result`. |
| `summary` | yes | string | Work completed or attempted. |
| `evidence` | yes | array | Checks, outputs, or observations. |
| `assumptions` | yes | array | Assumptions or unresolved risks. |

`simple_loop.detail_request`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.detail_request`. |
| `missing_details` | yes | array | Required details missing from the packet. |
| `evidence` | yes | array | Packet fields checked. |
| `assumptions` | yes | array | Assumptions or gaps. |

`simple_loop.gap_packet`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.gap_packet`. |
| `gaps` | yes | array | Specific unresolved gaps. |
| `evidence` | yes | array | Work and review facts checked. |
| `assumptions` | yes | array | Assumptions or risks. |

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: simple_loop.worker
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
artifact_id: work-result-1
artifact_kind: simple_loop.work_result
produced_by_stage: simple_loop.worker
source_work_item_id: work-2
terminal_marker: WORK_DONE
summary: Requested docs were updated and checked.
fields:
  summary: Requested docs were updated and checked.
evidence:
  - Completion definition item 1 matched.
  - Focused check command completed.
assumptions: []
handoff_context:
  completion_evidence_count: 2
```

## Invalid Examples

- `WORK_DONE` with no evidence: invalid because completion is unsupported.
- `INSUFFICIENT_SPEC` with no named missing details: invalid because the gap is not actionable.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the chosen artifact are present.
- Evidence supports the result, detail request, or gap packet.
- Assumptions and unresolved risks are explicit.
- Terminal marker is legal for `simple_loop.worker`.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The Worker stage is complete only when it returns one valid work artifact, supporting evidence, assumptions, and one legal terminal marker.
