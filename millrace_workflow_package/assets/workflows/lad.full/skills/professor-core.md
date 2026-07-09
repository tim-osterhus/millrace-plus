---
name: lad-full-professor-core
description: Use when executing the Professor stage for the selected full LAD workflow.
---

# Professor Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence, assumptions, request IDs, target skill IDs, preferred output paths, recommendations, and route hints belong in runner evidence/report text unless the selected schema declares them.

`learning.artifacts.skill_candidate`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.skill_candidate` | Must be `learning.artifacts.skill_candidate`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |
| `skill_id` | yes | string; min_length 1 | Selected-schema field. |
| `candidate_body` | yes | string; min_length 1 | Selected-schema field. |

`learning.artifacts.professor_notes`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.professor_notes` | Must be `learning.artifacts.professor_notes`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |
| `notes` | yes | string; min_length 1 | Selected-schema field. |

`learning.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.report` | Must be `learning.artifacts.report`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |

## Handoff Format

Return:
1. The exact selected terminal marker spelling from dispatch.
2. `artifact` as the exact selected artifact JSON object.
3. Runner evidence/report text for inspected evidence, assumptions, source IDs, target paths, recommendations, and downstream context that are not selected artifact fields.

Do not put generic wrapper keys or Learning context fields into the runtime artifact body unless the selected schema declares them.

## Valid Example

```json
[
  {
    "terminal_marker": "PROFESSOR_COMPLETE",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_candidate",
      "summary": "Candidate skill update drafted from selected research evidence.",
      "skill_id": "millrace-review-and-qa-loop",
      "candidate_body": "Draft skill body grounded in selected evidence."
    }
  },
  {
    "terminal_marker": "PROFESSOR_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.professor_notes",
      "summary": "No reusable skill patch is justified by the selected evidence.",
      "notes": "Professor inspected the selected research packet and found no safe update."
    }
  },
  {
    "terminal_marker": "BLOCKED",
    "artifact": {
      "artifact_kind": "learning.artifacts.report",
      "summary": "Learning stage blocked because selected input was missing or contradictory."
    }
  }
]
```

## Invalid Examples

Learning context fields embedded in the artifact body:

```json
{
  "terminal_marker": "PROFESSOR_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_candidate",
    "summary": "Candidate skill update drafted from selected research evidence.",
    "skill_id": "millrace-review-and-qa-loop",
    "candidate_body": "Draft skill body grounded in selected evidence.",
    "request_id": "learning-request-001",
    "target_skill_id": "invented-target",
    "preferred_output_paths": [
      "skills/example/SKILL.md"
    ],
    "recommended_learning_action": "draft_skill_patch",
    "route_target_graph_node_id": "learning.standard.professor"
  }
}
```

Invalid because selected schemas do not declare the extra Learning context fields shown above.

Missing required field:

```json
{
  "terminal_marker": "PROFESSOR_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_candidate"
  }
}
```

Type mismatch:

```json
{
  "terminal_marker": "PROFESSOR_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_candidate",
    "summary": ["not a string"]
  }
}
```

## Validation Checklist

- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- Evidence supports the selected marker and lives in runner evidence/report text unless selected as an artifact field.
- Terminal marker is legal for `professor` in the selected full LAD workflow.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin execution, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Professor is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the selected marker.
