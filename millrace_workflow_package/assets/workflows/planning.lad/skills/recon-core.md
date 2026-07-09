---
name: planning-recon-core
description: Use when executing the recon stage for the selected Millrace planning workflow.
---

# Recon Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence and assumptions belong in runner evidence/report text unless the selected schema declares them.

`planning.artifacts.generated_spec`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `planning.artifacts.generated_spec` | Must be `planning.artifacts.generated_spec`. |
| `spec_id` | yes | string; min_length 1 | Stable generated spec identifier. |
| `body` | yes | string; min_length 1 | Generated planning spec body. |

No additional artifact keys are allowed beyond the dispatch-selected `planning.artifacts.generated_spec` schema. If dispatch selects another legal Recon marker, use that marker's selected schema instead.

## Handoff Format

Return:
1. The exact selected terminal marker spelling from dispatch.
2. `artifact` as the exact selected artifact JSON object.
3. `observation_payload` as the exact same selected artifact JSON object when the runtime/wrapper asks for an observation or fanout payload candidate for this successful marker, unless dispatch provides a different selected observation schema.
4. Narrative evidence/report text for checks and assumptions outside the runtime artifact/observation JSON candidates.

Do not put generic wrapper keys, source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, or downstream context into the runtime artifact body or observation/fanout payload candidate unless the selected schema declares them.

## Valid Example

```json
{
  "terminal_marker": "RECON_TO_PLANNING",
  "artifact": {
    "artifact_kind": "planning.artifacts.generated_spec",
    "spec_id": "generated-spec-001",
    "body": "Planning synthesis is required before execution."
  },
  "observation_payload": {
    "artifact_kind": "planning.artifacts.generated_spec",
    "spec_id": "generated-spec-001",
    "body": "Planning synthesis is required before execution."
  }
}
```

## Invalid Examples

Generic wrapper-as-fanout payload:

```json
{
  "terminal_marker": "RECON_TO_PLANNING",
  "artifact": {
    "artifact_id": "bad-recon-wrapper",
    "artifact_kind": "planning.artifacts.generated_spec",
    "fields": {
      "artifact_kind": "planning.artifacts.generated_spec",
      "spec_id": "generated-spec-001",
      "body": "Planning synthesis is required before execution."
    },
    "next_stage_context": {
      "selected_action_id": "planning.recon_enqueue_spec"
    }
  }
}
```

Invalid because `artifact` is not the exact `planning.artifacts.generated_spec` object selected for the successful marker.

Missing required field:

```json
{
  "terminal_marker": "RECON_TO_PLANNING",
  "artifact": {
    "artifact_kind": "planning.artifacts.generated_spec",
    "spec_id": "generated-spec-001"
  }
}
```

Type mismatch:

```json
{
  "terminal_marker": "RECON_TO_PLANNING",
  "artifact": {
    "artifact_kind": "planning.artifacts.generated_spec",
    "spec_id": "generated-spec-001",
    "body": ["not a string"]
  }
}
```

## Validation Checklist

- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- The successful artifact body and observation/fanout payload candidate are schema-identical when the selected route or fanout validates the stage result payload.
- Observation/fanout payload candidates contain no source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, or downstream context unless selected schema declares them.
- Evidence supports the summary and marker choice.
- Terminal marker is legal for `recon` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Recon is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the marker.
