# Artifact Handoff Patterns

Use this reference when defining artifacts passed between stages.

## Selected-Schema Handoff

Artifact handoff starts from the selected artifact schema, not from a generic
envelope. Treat the selected schema as closed unless it explicitly allows
additional properties.

Use only fields declared by the selected schema. Generic fields for identity,
status, evidence, assumptions, or downstream context are valid artifact fields
only when the selected schema declares them. If the selected schema does not
declare them, preserve that information in the runner evidence/report channel
or in another selected schema field that actually exists.

Avoid prose-only handoffs when a downstream stage must verify completion, but
do not invent artifact fields to make the handoff feel complete.

```json
{
  "<selected_field>": "<value>",
  "<selected_field>": true
}
```

## Completion Definition Pattern

This is authoring guidance. Put it in package docs or runner evidence unless
the selected artifact schema declares fields for it.

```json
{
  "done_when": [
    "<observable requirement>",
    "<test or validation evidence>",
    "<selected artifact field that proves it>"
  ],
  "not_done_when": [
    "<known gap>",
    "<missing evidence>",
    "<unsafe assumption>"
  ]
}
```

## Gap Or Review Packet Pattern

Use this only when the selected schema declares these fields or equivalent
fields.

```json
{
  "gap_id": "<id>",
  "required_change": "<change>",
  "blocking_reason": "<reason>",
  "requested_next_action": "<action>"
}
```

## Incident Pattern

Use this only when the selected schema declares these fields or equivalent
fields.

```json
{
  "incident_id": "<id>",
  "failure_mode": "<mode>",
  "operator_relevance": "<why it matters>",
  "recommended_terminal_marker": "<marker copied exactly>"
}
```

The artifact may recommend a marker for the agent to return, but runtime
behavior still depends on selected workflow data.
