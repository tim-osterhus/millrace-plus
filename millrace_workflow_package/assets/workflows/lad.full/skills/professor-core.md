---
name: lad-full-professor-core
description: Use when executing the Professor stage for the selected full LAD workflow.
---

# Professor Core Skill

## Artifact Schema

Produce `learning_professor_skill_candidate` with this exact schema for `PROFESSOR_COMPLETE`:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_id` | yes | string | Stable artifact ID. |
| `artifact_kind` | yes | string | `learning.artifacts.skill_candidate`. |
| `source_work_item_id` | yes | string | Work item from dispatch. |
| `source_run_id` | yes | string | Run id from dispatch. |
| `stage_id` | yes | string | `professor`. |
| `status` | yes | string | `complete`. |
| `summary` | yes | string | Candidate or patch summary. |
| `skill_id` | yes | string | Proposed or target skill id. |
| `candidate_body` | yes | string | Candidate body or patch content. |
| `validation` | yes | array | Checks attempted or skipped with reason. |
| `curator_review_points` | yes | array | Specific review points for Curator. |
| `evidence` | yes | array | Evidence from research or request artifacts. |
| `assumptions` | yes | array | Scope limits and unresolved questions. |

For `PROFESSOR_NOOP`, produce `learning_professor_notes` with `artifact_kind: learning.artifacts.professor_notes`, `summary`, `notes`, `evidence`, and `assumptions`.

For `BLOCKED`, produce `learning_professor_blocked_report` with `artifact_kind: learning.artifacts.report`, `summary`, `evidence`, and `assumptions`.

## Handoff Format

```text
artifact_id:
produced_by_stage: professor
source_work_item_id:
source_run_id:
terminal_marker:
fields:
  artifact_kind:
  summary:
  skill_id:
  candidate_body:
  validation:
  curator_review_points:
evidence:
assumptions:
next_stage_context:
  candidate_kind:
  target_skill_id:
  preferred_output_paths:
```

## Valid Example

```text
artifact_id: professor-candidate-21
produced_by_stage: professor
source_work_item_id: learning-21
source_run_id: run-professor-21
terminal_marker: PROFESSOR_COMPLETE
fields:
  artifact_kind: learning.artifacts.skill_candidate
  summary: Draft patch for package review evidence.
  skill_id: millrace-review-and-qa-loop
  candidate_body: Add a package-local effect boundary evidence checklist.
  validation:
    - check: shape review
      result: passed
  curator_review_points:
    - Confirm wording is evidence-backed and not a runtime claim.
evidence:
  - Analyst packet identified the missing checklist.
assumptions: []
next_stage_context:
  candidate_kind: patch
  target_skill_id: millrace-review-and-qa-loop
  preferred_output_paths: []
```

## Invalid Examples

- Missing required field: no `candidate_body` for `PROFESSOR_COMPLETE`.
- Unsupported assumption: broadens the target skill beyond the evidence.
- Runtime claim in artifact text: says Professor output installs, publishes, routes, or approves effects.

## Validation Checklist

- Required fields are present for the selected marker.
- Candidate or patch is scoped to evidence.
- No-op notes explain why no skill work is warranted.
- Validation attempts or skipped checks are recorded.
- Terminal marker is legal for the selected Professor stage.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin/MCP, native runner, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The stage is complete only when Curator receives a concrete candidate or patch with evidence and review points, or receives no-op notes that explain why authoring would be unsupported.
