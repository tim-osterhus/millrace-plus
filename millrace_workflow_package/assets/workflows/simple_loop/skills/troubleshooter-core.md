---
name: simple-loop-troubleshooter-core
description: Use when executing the Troubleshooter stage for the selected simple_loop workflow.
---

# Troubleshooter Core Skill

## Artifact Schema

Produce `simple_loop.troubleshooting_report` with this schema.

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `simple_loop.troubleshooting_report`. |
| `result` | yes | string | Local troubleshooting result. |
| `blocker_cause` | yes | string | Cause supported by evidence. |
| `attempted_repair` | yes | string | Repair attempted or reason none was attempted. |
| `next_route` | yes | string | One of `retry_recorded_source`, `operator_intervention`, or `unresolved_return`. |
| `evidence` | yes | array | Blocker facts checked. |
| `assumptions` | yes | array | Assumptions or unresolved risks. |

## Handoff Format

```text
artifact_id:
artifact_kind: simple_loop.troubleshooting_report
produced_by_stage: simple_loop.troubleshooter
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
artifact_id: troubleshooting-1
artifact_kind: simple_loop.troubleshooting_report
produced_by_stage: simple_loop.troubleshooter
source_work_item_id: work-4
terminal_marker: UNRESOLVED
summary: Blocker cause was identified but not repaired.
fields:
  result: blocked input still missing required detail
  blocker_cause: missing completion definition
  attempted_repair: checked dispatch payload and prior artifacts
  next_route: unresolved_return
evidence:
  - Dispatch payload had no completion definition.
assumptions: []
handoff_context:
  blocker_cause: missing completion definition
```

## Invalid Examples

- Missing `next_route`: invalid because the report does not match the selected artifact schema.
- `RESOLVED` with no repair evidence: invalid because the result is unsupported.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields are present.
- `next_route` is one of the schema values.
- Evidence supports the blocker cause and result.
- Assumptions and unresolved risks are explicit.
- Terminal marker is legal for `simple_loop.troubleshooter`.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The Troubleshooter stage is complete only when it returns one valid troubleshooting report, supporting evidence, assumptions, and one legal terminal marker.
