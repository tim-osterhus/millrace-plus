# Terminal Markers And Runtime Authority

Use this reference when writing terminal-marker instructions.

## Marker Wording Pattern

Good when `BLOCKED` is the selected marker:

```text
Return `WORK_COMPLETE` when the artifact satisfies every required field and the
completion checklist passes.
```

Good:

```text
Return `BLOCKED` when required input is missing or contradictory. Include the
missing field and the evidence checked.
```

Avoid:

```text
Return `WORK_COMPLETE` to close the queue item and route to Review.
```

The prompt may tell the agent when to choose a marker. The selected workflow
data determines what happens after the marker.

## Review Questions

- Is every mentioned marker declared for this stage?
- Is the condition for each marker observable?
- Does the text avoid promising a route, retry, wait, approval, effect, or
  closure?
- Does the selected blocked marker include enough detail for recovery?
- Are workflow-specific markers documented in the loop configuration?

## Safe Marker Set Pattern

```text
success_marker:
  name:
  condition:
blocked_marker:
  name: <selected blocked marker, often BLOCKED>
  condition:
additional_markers:
- name:
  condition:
```

Keep marker names exact. Do not introduce aliases unless the selected workflow
declares them.

## Structured Output Discipline

- Copy exact marker spelling, casing, punctuation, and spacing from the
  selected runtime-rendered marker list.
- Return exactly one marker for the selected completion path.
- Pair the marker with a JSON artifact object that matches the selected schema
  exactly, or with no artifact/`null` when the selected terminal action has no
  artifact schema.
- Keep examples parseable as JSON when they show JSON. Invalid examples should
  still parse, then fail selected-schema checks for a clear reason such as an
  extra field, missing field, or type mismatch.
