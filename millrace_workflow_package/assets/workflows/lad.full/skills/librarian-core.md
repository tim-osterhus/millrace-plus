---
name: lad-full-librarian-core
description: Use when executing the Librarian stage for the selected full LAD workflow.
---

# Librarian Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence, assumptions, request IDs, target skill IDs, preferred output paths, recommendations, and route hints belong in runner evidence/report text unless the selected schema declares them.

`learning.artifacts.skill_install_report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.skill_install_report` | Must be `learning.artifacts.skill_install_report`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |
| `target_skill_id` | yes | string; min_length 1 | Selected-schema field. |
| `installed_path` | yes | string; min_length 1 | Selected-schema field. |

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
    "terminal_marker": "LIBRARIAN_COMPLETE",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_install_report",
      "summary": "Selected skill install report prepared from package-local evidence.",
      "target_skill_id": "millrace-review-and-qa-loop",
      "installed_path": "skills/millrace-review-and-qa-loop/SKILL.md"
    }
  },
  {
    "terminal_marker": "LIBRARIAN_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_install_report",
      "summary": "Selected skill install report prepared from package-local evidence.",
      "target_skill_id": "millrace-review-and-qa-loop",
      "installed_path": "skills/millrace-review-and-qa-loop/SKILL.md"
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
  "terminal_marker": "LIBRARIAN_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_install_report",
    "summary": "Selected skill install report prepared from package-local evidence.",
    "target_skill_id": "invented-target",
    "installed_path": "skills/millrace-review-and-qa-loop/SKILL.md",
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
  "terminal_marker": "LIBRARIAN_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_install_report"
  }
}
```

Type mismatch:

```json
{
  "terminal_marker": "LIBRARIAN_COMPLETE",
  "artifact": {
    "artifact_kind": "learning.artifacts.skill_install_report",
    "summary": ["not a string"]
  }
}
```

## Validation Checklist

- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- Evidence supports the selected marker and lives in runner evidence/report text unless selected as an artifact field.
- Terminal marker is legal for `librarian` in the selected full LAD workflow.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin execution, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Librarian is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the selected marker.
