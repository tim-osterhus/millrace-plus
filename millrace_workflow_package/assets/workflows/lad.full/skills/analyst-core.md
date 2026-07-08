---
name: lad-full-analyst-core
description: Use when executing the Analyst stage for the selected full LAD workflow.
---

# Analyst Core Skill

## Artifact Schema

Produce `learning_analyst_research_packet` with this exact schema:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_id` | yes | string | Stable artifact ID. |
| `artifact_kind` | yes | string | `learning.artifacts.research_packet` for complete or no-op outcomes. |
| `source_work_item_id` | yes | string | Work item from dispatch. |
| `source_run_id` | yes | string | Run id from dispatch. |
| `stage_id` | yes | string | `analyst`. |
| `status` | yes | string | `complete`, `noop`, or `blocked`. |
| `summary` | yes | string | Concise research result. |
| `research_notes` | yes | string | Evidence-backed notes and inspected sources. |
| `existing_skill_matches` | yes | array | Nearby skills and why they do or do not cover the request. |
| `recommendation` | yes | string | Professor, Curator, no-op, or blocked recommendation. |
| `evidence` | yes | array | Evidence supporting the recommendation. |
| `assumptions` | yes | array | Assumptions, missing evidence, and confidence limits. |

For `BLOCKED`, produce `learning_analyst_blocked_report` with `artifact_kind: learning.artifacts.report`, `summary`, `evidence`, and `assumptions`.

## Handoff Format

```text
artifact_id:
produced_by_stage: analyst
source_work_item_id:
source_run_id:
terminal_marker:
fields:
  artifact_kind:
  summary:
  research_notes:
  existing_skill_matches:
  recommendation:
evidence:
assumptions:
next_stage_context:
  recommended_learning_action:
  target_skill_id:
  preferred_output_paths:
```

## Valid Example

```text
artifact_id: analyst-request-14
produced_by_stage: analyst
source_work_item_id: learning-14
source_run_id: run-analyst-14
terminal_marker: ANALYST_COMPLETE
fields:
  artifact_kind: learning.artifacts.research_packet
  summary: Existing skill lacks review-loop evidence guidance.
  research_notes: Compared linked incident notes with installed skill index.
  existing_skill_matches:
    - skill_id: millrace-review-and-qa-loop
      gap: Missing package-local effect boundary evidence reminder.
  recommendation: Professor draft a scoped skill improvement.
evidence:
  - Linked incident named the missing boundary proof.
assumptions: []
next_stage_context:
  recommended_learning_action: draft_skill_patch
  target_skill_id: millrace-review-and-qa-loop
  preferred_output_paths: []
```

## Invalid Examples

- Missing required field: no `research_notes`.
- Unsupported assumption: recommends a new skill without checking existing skills.
- Runtime claim in artifact text: says the artifact routes work or approves effects.

## Validation Checklist

- Required fields are present.
- Every recommendation is tied to dispatch-provided or readable evidence.
- Existing skill coverage was checked before recommending new authoring.
- No-op is used when evidence does not justify a skill change.
- Terminal marker is legal for the selected Analyst stage.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin/MCP, native runner, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The stage is complete only when the research packet names the request, cites inspected evidence, records skill matches or gaps, and gives a recommendation that follows from that evidence.
