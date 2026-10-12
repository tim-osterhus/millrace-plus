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

Return one complete Pi RPC result object with exactly these top-level fields:

1. `marker`: one exact selected terminal marker.
2. `artifact_payload_candidate`: the exact selected artifact object, or `null` when the selected terminal action has no artifact schema.
3. `observation_payload_candidate`: selected observation fields and a string `runner_report` for narrative evidence, assumptions, checks, references, or gaps.

Keep selected observation routing fields at their existing names; do not add routing data to the artifact unless its schema declares it.

Keep narrative evidence out of the artifact unless the selected schema declares a field for it. Do not add envelope, identity, route, evidence, or downstream-context keys.

## Valid Example

```json
{
  "marker": "ANALYST_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.research_packet",
    "summary": "Learning request summary.",
    "research_notes": "Grounded notes for the next Learning stage."
  },
  "observation_payload_candidate": {
    "runner_report": "Analyst evidence supports the selected ANALYST_COMPLETE result."
  }
}
```

```json
{
  "marker": "ANALYST_NOOP",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.research_packet",
    "summary": "Learning request summary.",
    "research_notes": "Grounded notes for the next Learning stage."
  },
  "observation_payload_candidate": {
    "runner_report": "Analyst evidence supports the selected ANALYST_NOOP result."
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
    "runner_report": "Analyst lacks sufficient selected input for a safe result."
  }
}
```

## Invalid Examples

Learning context fields embedded in the artifact body:

```json
{
  "marker": "ANALYST_COMPLETE",
  "artifact_payload_candidate": {
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
  },
  "observation_payload_candidate": {
    "runner_report": "Analyst evidence supports the selected ANALYST_COMPLETE result."
  }
}
```

Invalid because selected schemas do not declare the extra Learning context fields shown above.

Missing required field:

```json
{
  "marker": "ANALYST_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.research_packet"
  },
  "observation_payload_candidate": {
    "runner_report": "Analyst evidence supports the selected ANALYST_COMPLETE result."
  }
}
```

Type mismatch:

```json
{
  "marker": "ANALYST_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "learning.artifacts.research_packet",
    "summary": [
      "not a string"
    ]
  },
  "observation_payload_candidate": {
    "runner_report": "Analyst evidence supports the selected ANALYST_COMPLETE result."
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
- Terminal marker is legal for `analyst` in the selected full LAD workflow.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin execution, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Analyst is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the selected marker.
