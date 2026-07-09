---
name: lad-full-analyst-core
description: Use when executing the Analyst stage for the selected full LAD workflow.
---

# Analyst Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence, assumptions, request IDs, target skill IDs, preferred output paths, recommendations, and route hints belong in runner evidence/report text unless the selected schema declares them.

`learning.artifacts.research_packet`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.research_packet` | Must be `learning.artifacts.research_packet`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |
| `research_notes` | yes | string; min_length 1 | Selected-schema field. |

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
    "terminal_marker": "ANALYST_COMPLETE",
    "artifact": {
      "artifact_kind": "learning.artifacts.research_packet",
      "summary": "Learning request summary.",
      "research_notes": "Grounded notes for the next Learning stage."
    }
  },
  {
    "terminal_marker": "ANALYST_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.research_packet",
      "summary": "Learning request summary.",
      "research_notes": "Grounded notes for the next Learning stage."
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
  "terminal_marker": "ANALYST_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.research_packet",
    "summary": "Learning request summary.",
    "research_notes": "Grounded notes for the next Learning stage.",
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
  "terminal_marker": "ANALYST_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.research_packet"
  }
}
```

Type mismatch:

```json
{
  "terminal_marker": "ANALYST_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.research_packet",
    "summary": ["not a string"]
  }
}
```

## Validation Checklist

- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- Evidence supports the selected marker and lives in runner evidence/report text unless selected as an artifact field.
- Terminal marker is legal for `analyst` in the selected full LAD workflow.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin execution, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Analyst is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the selected marker.
