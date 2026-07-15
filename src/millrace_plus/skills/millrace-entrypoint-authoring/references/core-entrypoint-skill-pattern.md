# Core Entrypoint Skill Pattern

Use this reference when writing the core stage skill paired with an entrypoint
prompt.

## Purpose

The core stage skill carries the structured handoff contract. It should be
precise enough that a stage agent can produce a valid artifact and the next
stage can verify it without guessing.

## Template

````markdown
---
name: <workflow-stage-core-skill>
description: Use when executing the <stage> stage for <workflow>.
---

# <Stage> Core Skill

## Artifact Schema

Selected schema first:

- Copy the artifact schema from selected workflow/package authority.
- Treat the selected schema as closed unless it explicitly allows additional
  properties.
- Do not add generic handoff fields. Fields such as IDs, status, evidence, or
  assumptions are artifact fields only when the selected schema declares them.
- If the selected terminal action has no artifact schema, state that this
  stage returns the exact selected marker with no artifact or `null` artifact,
  using the runner-required shape.

Accepted artifact fields for this selected stage:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `<selected_field>` | yes/no | `<json type>` | `<meaning from schema>`. |
| `<selected_field>` | yes/no | `<json type>` | `<meaning from schema>`. |

Additional artifact fields: none, unless the selected schema says otherwise.
Evidence and assumptions that do not fit selected fields belong in the runner
evidence/report channel.

## Handoff Format

Return:

1. The exact selected terminal marker spelling from dispatch/selected workflow
   data.
2. A JSON artifact object matching the selected schema exactly, or no
   artifact/`null` when the selected terminal action has no artifact schema.
3. Runner evidence/report text for checks, assumptions, or gaps that are not
   selected artifact fields.

```json
{
  "terminal_marker": "<MARKER_COPIED_EXACTLY>",
  "artifact": {
    "<selected_field>": "<value>"
  }
}
```

## Valid Example

Replace this with an example that uses only selected fields. For example, if
the selected schema declares exactly `summary` as a string and `complete` as a
boolean:

```json
{
  "terminal_marker": "WORK_COMPLETE",
  "artifact": {
    "summary": "Requested file exists with the required content.",
    "complete": true
  }
}
```

## Invalid Examples

These examples assume the same selected schema as above.

Extra undeclared field:

```json
{
  "terminal_marker": "WORK_COMPLETE",
  "artifact": {
    "summary": "Requested file exists with the required content.",
    "complete": true,
    "evidence": ["checked file content"]
  }
}
```

Invalid because `evidence` is not declared by the selected artifact schema.

Missing required field:

```json
{
  "terminal_marker": "WORK_COMPLETE",
  "artifact": {
    "summary": "Requested file exists with the required content."
  }
}
```

Invalid because `complete` is required by the selected artifact schema.

Type mismatch:

```json
{
  "terminal_marker": "WORK_COMPLETE",
  "artifact": {
    "summary": "Requested file exists with the required content.",
    "complete": "true"
  }
}
```

Invalid because `complete` must be a boolean, not a string.

## Validation Checklist

- JSON examples parse successfully.
- Marker spelling exactly matches one selected legal marker for this stage.
- Required selected fields are present.
- No artifact field is present unless the selected schema declares it.
- Every artifact value has the selected JSON type.
- Evidence supports the conclusion and lives in selected fields only when
  declared; otherwise it stays in the runner evidence/report channel.
- Completion criteria are satisfied.
- Assumptions are explicit in runner evidence/report text unless the selected
  schema declares an assumptions field.
- No artifact text claims route, queue, approval, capability, effect, package,
  or durable-state behavior by itself.
- No artifact or skill text includes API keys, OAuth tokens, local credential
  paths, provider secrets, or adapter config secrets.

## Completion Criteria

The stage is complete only when <observable condition tied to selected
workflow definition of done>.
````

## Review Rules

- Keep schemas stable and explicit.
- Include at least one parseable valid JSON example and invalid parseable JSON
  examples for extra field, missing field, and type mismatch cases.
- Keep runtime authority out of the skill. The skill defines artifact shape and
  stage work quality, not route legality or state mutation.
- Keep secrets out of the skill. Adapter config is local operator config, not
  package authority.
