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

Return one complete Pi RPC result object with exactly these top-level fields:

1. `marker`: one exact selected terminal marker.
2. `artifact_payload_candidate`: the exact selected artifact object, or `null` when the selected terminal action has no artifact schema.
3. `observation_payload_candidate`: selected observation fields and a string `runner_report` for narrative evidence, assumptions, checks, references, or gaps.

Keep selected observation routing fields at their existing names; do not add routing data to the artifact unless its schema declares it.

Keep narrative evidence out of the artifact unless the selected schema declares a field for it. Do not add envelope, identity, route, evidence, or downstream-context keys.

## Valid Example

```json
{
  "marker": "CURATOR_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.skill_update",
    "summary": "Curated bounded skill update from selected candidate evidence.",
    "target_skill_id": "millrace-review-and-qa-loop",
    "update_body": "Patch text scoped to selected evidence."
  },
  "observation_payload_candidate": {
    "runner_report": "Curator evidence supports the selected CURATOR_COMPLETE result."
  }
}
```

```json
{
  "marker": "CURATOR_NOOP",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.curator_decision",
    "summary": "Curator no-op decision from selected evidence.",
    "decision": "no_update_needed"
  },
  "observation_payload_candidate": {
    "runner_report": "Curator evidence supports the selected CURATOR_NOOP result."
  }
}
```

```json
{
  "marker": "BLOCKED",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.report",
    "summary": "Learning stage blocked because selected input was missing or contradictory."
  },
  "observation_payload_candidate": {
    "runner_report": "Curator lacks sufficient selected input for a safe result."
  }
}
```

## Invalid Examples

Learning context fields embedded in the artifact body:

```json
{
  "marker": "CURATOR_COMPLETE",
  "artifact_payload_candidate": {
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
  },
  "observation_payload_candidate": {
    "runner_report": "Curator evidence supports the selected CURATOR_COMPLETE result."
  }
}
```

Invalid because selected schemas do not declare the extra Learning context fields shown above.

Missing required field:

```json
{
  "marker": "CURATOR_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.skill_update"
  },
  "observation_payload_candidate": {
    "runner_report": "Curator evidence supports the selected CURATOR_COMPLETE result."
  }
}
```

Type mismatch:

```json
{
  "marker": "CURATOR_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.skill_update",
    "summary": [
      "not a string"
    ]
  },
  "observation_payload_candidate": {
    "runner_report": "Curator evidence supports the selected CURATOR_COMPLETE result."
  }
}
```

## Validation Checklist

- The result is one strict JSON object with exactly `marker`, `artifact_payload_candidate`, and `observation_payload_candidate`.
- The selected marker and artifact candidate match the terminal action's selected schema or null behavior.
- `observation_payload_candidate.runner_report` is text and selected routing fields keep their declared names.
- Required selected artifact fields are present.
- No artifact field is present unless the selected schema declares it.
- Evidence supports the selected marker and lives in runner evidence/report text unless selected as an artifact field.
- Terminal marker is legal for `curator` in the selected full LAD workflow.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin execution, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Curator is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the selected marker.
