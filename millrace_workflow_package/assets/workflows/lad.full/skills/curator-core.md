---
name: lad-full-curator-core
description: Use when executing the Curator stage for the selected full LAD workflow.
---

# Curator Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence, assumptions, request IDs, target skill IDs, preferred output paths, recommendations, and route hints belong in runner evidence/report text unless the selected schema declares them.

`learning.artifacts.skill_update`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.skill_update` | Must be `learning.artifacts.skill_update`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |
| `target_skill_id` | yes | string; min_length 1 | Selected-schema field. |
| `update_body` | yes | string; min_length 1 | Selected-schema field. |

`learning.artifacts.curator_decision`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.curator_decision` | Must be `learning.artifacts.curator_decision`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |
| `decision` | yes | string; min_length 1 | Selected-schema field. |

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
    "terminal_marker": "CURATOR_COMPLETE",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_update",
      "summary": "Curated bounded skill update from selected candidate evidence.",
      "target_skill_id": "millrace-review-and-qa-loop",
      "update_body": "Patch text scoped to selected evidence."
    }
  },
  {
    "terminal_marker": "CURATOR_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.curator_decision",
      "summary": "Curator no-op decision from selected evidence.",
      "decision": "no_update_needed"
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
  "terminal_marker": "CURATOR_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_update",
    "summary": "Curated bounded skill update from selected candidate evidence.",
    "target_skill_id": "invented-target",
    "update_body": "Patch text scoped to selected evidence.",
    "request_id": "learning-request-001",
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
  "terminal_marker": "CURATOR_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_update"
  }
}
```

Type mismatch:

```json
{
  "terminal_marker": "CURATOR_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_update",
    "summary": ["not a string"]
  }
}
```

## Validation Checklist

- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- Evidence supports the selected marker and lives in runner evidence/report text unless selected as an artifact field.
- Terminal marker is legal for `curator` in the selected full LAD workflow.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin execution, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Curator is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the selected marker.
