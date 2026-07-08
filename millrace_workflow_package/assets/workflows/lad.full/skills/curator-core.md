---
name: lad-full-curator-core
description: Use when executing the Curator stage for the selected full LAD workflow.
---

# Curator Core Skill

## Artifact Schema

Produce `learning_curator_skill_update` with this exact schema for accepted `CURATOR_COMPLETE` updates:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_id` | yes | string | Stable artifact ID. |
| `artifact_kind` | yes | string | `learning.artifacts.skill_update`. |
| `source_work_item_id` | yes | string | Work item from dispatch. |
| `source_run_id` | yes | string | Run id from dispatch. |
| `stage_id` | yes | string | `curator`. |
| `status` | yes | string | `accepted`, `rejected`, `noop`, or `blocked`. |
| `summary` | yes | string | Decision summary. |
| `target_skill_id` | yes | string | Skill being curated. |
| `update_body` | yes | string | Accepted workspace-local update body or patch summary. |
| `decision` | yes | string | Accepted, rejected, no-op, or blocked rationale. |
| `validation` | yes | array | Checks performed or skipped with reason. |
| `evidence` | yes | array | Candidate, research, and request evidence reviewed. |
| `assumptions` | yes | array | Residual risks and limits. |

For `CURATOR_NOOP`, produce `learning_curator_decision` with `artifact_kind: learning.artifacts.curator_decision`, `summary`, `decision`, `evidence`, and `assumptions`.

For `BLOCKED`, produce `learning_curator_blocked_report` with `artifact_kind: learning.artifacts.report`, `summary`, `evidence`, and `assumptions`.

## Handoff Format

```text
artifact_id:
produced_by_stage: curator
source_work_item_id:
source_run_id:
terminal_marker:
fields:
  artifact_kind:
  summary:
  target_skill_id:
  update_body:
  decision:
  validation:
evidence:
assumptions:
next_stage_context:
  accepted_destination:
  source_promotion_note:
```

## Valid Example

```text
artifact_id: curator-update-8
produced_by_stage: curator
source_work_item_id: learning-8
source_run_id: run-curator-8
terminal_marker: CURATOR_COMPLETE
fields:
  artifact_kind: learning.artifacts.skill_update
  summary: Accepted scoped package-review checklist patch.
  target_skill_id: millrace-review-and-qa-loop
  update_body: Add a package-local selected-data evidence checklist.
  decision: accepted because Analyst evidence and Professor patch match.
  validation:
    - check: boundary wording
      result: passed
evidence:
  - Professor candidate matched the Analyst packet.
assumptions: []
next_stage_context:
  accepted_destination: workspace-installed skill
  source_promotion_note: later operator decision only
```

## Invalid Examples

- Missing required field: no `target_skill_id`.
- Unsupported assumption: accepts a polished candidate with no evidence.
- Runtime claim in artifact text: says Curator output publishes, promotes, approves effects, or persists state.

## Validation Checklist

- Required fields are present for the selected marker.
- Decision follows from candidate, research, and request evidence.
- Destination is explicit when an update is accepted.
- No-op or rejection includes a concrete rationale.
- Terminal marker is legal for the selected Curator stage.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin/MCP, native runner, reconciliation, persistence, source promotion, publication, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The stage is complete only when the decision record names the evidence reviewed, the target skill or no-op rationale, validation performed or skipped, and the residual promotion or rollback context.
