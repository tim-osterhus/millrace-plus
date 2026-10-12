---
name: planning-mechanic-core
description: Use when executing the lad_mechanic stage for the selected Millrace planning workflow.
---

# Lad Mechanic Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker, or no artifact/null when the selected marker has no artifact schema. Evidence and assumptions belong in runner evidence/report text unless the selected schema declares them.

`planning.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `planning.artifacts.stage_result` | Must be `planning.artifacts.stage_result`. |
| `summary` | yes | string; min_length 1 | Selected-schema field. |

No additional artifact keys are allowed beyond the dispatch-selected `planning.artifacts.stage_result` schema. Some selected workflows declare optional fields such as `learning_requests`; include them only when dispatch-selected schema declares them and a selected route or fanout reads them.

## Handoff Format

Return one complete Pi RPC result object with exactly these top-level fields:

1. `marker`: one exact selected terminal marker.
2. `artifact_payload_candidate`: the exact selected artifact object, or `null` when the selected terminal action has no artifact schema.
3. `observation_payload_candidate`: selected observation fields and a string `runner_report` for narrative evidence, assumptions, checks, references, or gaps.

Keep selected dynamic routing fields, such as `resume_stage`, in `observation_payload_candidate` under their selected names.

Keep narrative evidence out of the artifact unless the selected schema declares a field for it. Do not add envelope, identity, route, evidence, or downstream-context keys.

## Valid Example

```json
{
  "marker": "MECHANIC_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary."
  },
  "observation_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": "Planner summary.",
    "resume_stage": "planner",
    "runner_report": "Mechanic evidence supports the selected MECHANIC_COMPLETE result."
  }
}
```

## Additional Selected Branch Examples

```json
{
  "marker": "BLOCKED",
  "artifact_payload_candidate": null,
  "observation_payload_candidate": {
    "runner_report": "Mechanic lacks sufficient selected input for a safe result."
  }
}
```

```json
{
  "marker": "MECHANIC_RECOVERED",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.report",
    "summary": "Selected evidence supports a recovered-source result."
  },
  "observation_payload_candidate": {
    "runner_report": "Mechanic evidence supports the selected MECHANIC_RECOVERED result."
  }
}
```

```json
{
  "marker": "MECHANIC_QUARANTINE",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.report",
    "summary": "Selected evidence supports a quarantine recommendation."
  },
  "observation_payload_candidate": {
    "runner_report": "Mechanic evidence supports the selected MECHANIC_QUARANTINE result."
  }
}
```

## Invalid Examples

Invalid artifact shape with generic wrapper fields:

```json
{
  "marker": "MECHANIC_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_id": "bad-lad_mechanic-wrapper",
    "artifact_kind": "planning.artifacts.stage_result",
    "fields": {
      "artifact_kind": "planning.artifacts.stage_result",
      "summary": "Planner summary."
    },
    "next_stage_context": {
      "selected_action_id": "planning.route_planner_complete"
    }
  },
  "observation_payload_candidate": {
    "resume_stage": "planner",
    "runner_report": "Mechanic evidence supports the selected MECHANIC_COMPLETE result."
  }
}
```

Invalid because `artifact_payload_candidate` is not the exact `planning.artifacts.stage_result` object selected for the successful marker.

Missing required field:

```json
{
  "marker": "MECHANIC_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result"
  },
  "observation_payload_candidate": {
    "resume_stage": "planner",
    "runner_report": "Mechanic evidence supports the selected MECHANIC_COMPLETE result."
  }
}
```

Type mismatch:

```json
{
  "marker": "MECHANIC_COMPLETE",
  "artifact_payload_candidate": {
    "artifact_kind": "planning.artifacts.stage_result",
    "summary": [
      "not a string"
    ]
  },
  "observation_payload_candidate": {
    "resume_stage": "planner",
    "runner_report": "Mechanic evidence supports the selected MECHANIC_COMPLETE result."
  }
}
```

## Validation Checklist

- The result is one strict JSON object with exactly `marker`, `artifact_payload_candidate`, and `observation_payload_candidate`.
- The selected marker and artifact candidate match the terminal action's selected schema or null behavior.
- `observation_payload_candidate.runner_report` is text and selected routing fields keep their declared names.
- Required selected artifact fields are present.
- No artifact field is present unless the selected schema declares it.
- The successful artifact body and observation/fanout payload candidate are schema-identical when the selected route or fanout validates the stage result payload.
- Observation/fanout payload candidates contain no source IDs, selected action IDs, outcome IDs, route targets, route metadata, evidence arrays, assumptions arrays, or downstream context unless selected schema declares them.
- Evidence supports the summary and marker choice.
- Terminal marker is legal for `lad_mechanic` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Lad Mechanic is complete only when it returns one legal terminal marker, one exact selected artifact body when required, and runner evidence/report text supporting the marker.
