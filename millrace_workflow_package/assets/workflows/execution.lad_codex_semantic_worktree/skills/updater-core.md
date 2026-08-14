---
name: execution-codex-semantic-worktree-updater-core
description: Use when the selected semantic-worktree Updater must report direct changes and protected proposals.
---

# Semantic Worktree Updater Core Skill

## Artifact Schema

Produce exactly one `execution.artifacts.context_update_report` artifact. The
top level is closed by the selected Millrace schema vocabulary:

- Required `changes` array.
- Required `proposals` array.
- Optional `no_op_reason` string.

Each `changes` object requires `path` string, `change_kind` enum `create`,
`modify`, or `delete`, `evidence_refs` array of strings, and
`classification` constant `direct_write`. It may include only the optional
`before_sha256` and `after_sha256` strings in addition to those fields.

Each `proposals` object requires `path` string, `proposed_content` string,
`proposed_content_sha256` string, `evidence_refs` array of strings, and
`classification` constant `protected_proposal`. Do not add fields to either
object or to the top level. A direct change and a protected proposal are
different records even when they concern related evidence.

## Valid Example

```json
{
  "changes": [
    {
      "path": "docs/architecture.md",
      "change_kind": "modify",
      "before_sha256": "sha256:c51ec3b516c26e86428948b418c69c3a6f0a4c9ce15384317dc72f9db08c1dab",
      "after_sha256": "sha256:ca273541cbcaa80c036ce0677109616254dcfac4ddb4da2c7b2a18dd0ee87eb0",
      "evidence_refs": ["evidence-docs-1"],
      "classification": "direct_write"
    }
  ],
  "proposals": [
    {
      "path": "millrace-agents/MILLRACE.md",
      "proposed_content": "Proposed policy clarification.\n",
      "proposed_content_sha256": "sha256:6b3f1991d9c1e41785649972c6b1b1127161c0b648378b3432778a892177a432",
      "evidence_refs": ["evidence-policy-1"],
      "classification": "protected_proposal"
    }
  ]
}
```

## Valid No-op Example

```json
{
  "changes": [],
  "proposals": [],
  "no_op_reason": "No direct changes or protected proposals are required."
}
```

The following examples are valid JSON but invalid artifacts under the
selected schema. They are refusal examples, not output templates.

### Invalid: extra field

```json
{
  "changes": [
    {
      "path": "docs/architecture.md",
      "change_kind": "modify",
      "evidence_refs": [],
      "classification": "direct_write",
      "unexpected": true
    }
  ],
  "proposals": []
}
```

### Invalid: missing required field

```json
{
  "changes": []
}
```

### Invalid: wrong type

```json
{
  "changes": {},
  "proposals": []
}
```

## Validation Checklist

- Read every required source and complete snapshot before deciding on a write.
- Keep the live project root separate from immutable checkout evidence.
- Verify every direct path against the exact selected allowlist.
- Keep `millrace-agents/MILLRACE.md` and `millrace-agents/shared/skills` in
  `proposals` with `protected_proposal` classification only.
- Include evidence references and content digests for every reported change or
  proposal.
- Validate the complete artifact against the selected schema before returning
  it through the selected runner protocol.
- Do not infer queue, routing, capability, or durable-state behavior from text,
  filenames, or terminal markers.

## Completion Criteria

The Updater is complete only when it returns one schema-valid context update
report with exact direct changes, protected proposals, or a truthful no-op
reason. It must preserve the task contract and lineage evidence, avoid
`.millrace/`, generated projections, checkouts, queues, work items, accepted
artifacts, executable skills, and protected policy, and emit one legal runtime
marker through the selected runner protocol.
