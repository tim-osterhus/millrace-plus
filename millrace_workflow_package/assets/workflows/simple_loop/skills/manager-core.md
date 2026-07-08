---
name: simple-loop-manager-core
description: Use when executing the Manager stage for the selected simple_loop workflow.
---

# Manager Core Skill

## Artifact Schema

Produce one of these artifacts.

`simple_loop.work_packet`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.work_packet`. |
| `source_prompt_id` | yes | string | Prompt identifier from dispatch. |
| `title` | yes | string | Short work title. |
| `objective` | yes | string | Concrete work objective. |
| `completion_definition` | yes | string | Observable done condition. |
| `evidence` | yes | array | Source facts checked. |
| `assumptions` | yes | array | Assumptions or gaps. |

`simple_loop.detail_request`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.detail_request`. |
| `missing_details` | yes | array | Specific missing details. |
| `evidence` | yes | array | Input fields checked. |
| `assumptions` | yes | array | Assumptions or gaps. |

`simple_loop.incident_report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.incident_report`. |
| `reason` | yes | string | Incident reason. |
| `evidence` | yes | array | Incident facts checked. |
| `assumptions` | yes | array | Assumptions or gaps. |

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: simple_loop.manager
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
artifact_id: work-packet-1
artifact_kind: simple_loop.work_packet
produced_by_stage: simple_loop.manager
source_work_item_id: work-1
terminal_marker: PACKET_READY
summary: Work packet has objective and completion definition.
fields:
  source_prompt_id: prompt-1
  title: Update package docs
  objective: Update docs to match selected package evidence.
  completion_definition: Docs name the package ID and validation evidence.
evidence:
  - Prompt body had a concrete objective.
assumptions: []
handoff_context:
  completion_definition: Docs name the package ID and validation evidence.
```

## Invalid Examples

- Missing `completion_definition`: invalid because Worker and Reviewer evidence cannot be checked.
- Empty `missing_details`: invalid because the detail request must name specific gaps.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the chosen artifact are present.
- Evidence supports the artifact summary.
- Assumptions are explicit.
- Terminal marker is legal for `simple_loop.manager`.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The Manager stage is complete only when it returns one valid management artifact, supporting evidence, assumptions, and one legal terminal marker.
