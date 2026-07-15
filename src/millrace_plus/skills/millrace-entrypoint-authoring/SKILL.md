---
name: millrace-entrypoint-authoring
description: Public Millrace entrypoint and stage-core skill authoring guide. Use when an agent needs to write, edit, or review Millrace stage entrypoint prompts, core entrypoint skills, terminal marker instructions, structured artifact schemas, handoff formats, validation checklists, or prompt/skill package assets for a compiler-validated workflow.
---

# Millrace Entrypoint Authoring

## Core Rule

Entrypoints are prompt files. A stage runner receives an instruction like:

```text
Open [relative/path/to/entrypoint.md] and follow instructions.
```

The entrypoint prompt tells the stage agent how to act. It does not create
runtime authority. Runtime legality still comes from selected compiled
workflow/package data.

Do not put API keys, OAuth tokens, local credential paths, provider secrets, or
adapter config secrets in workflow packages, prompt assets, skill files, or
evidence reports. Adapter config is local operator config, not package
authority.

## Do Not Overclaim

- Do not state a command exists unless it appears in current CLI help or CLI
  tests.
- Do not state a config field exists unless it appears in accepted contracts or
  package/workflow schemas.
- Do not infer runtime behavior from prompt text, filenames, folders, display
  labels, CLI aliases, or runner prose.
- Do not treat examples as generic proof, planes as mandatory, LAD names as
  kernel vocabulary, or skill prose as runtime authority.

## Required Two-Layer Pattern

Use two assets by default for every non-trivial stage:

1. Entrypoint prompt:
   - role and scope;
   - stage ownership: shaping, implementation, review/check,
     reconciliation, wait packaging, operator-decision packaging, or another
     selected workflow-defined role;
   - dispatch input reading;
   - payload-as-data rule for non-implementation stages;
   - readable assets and writable artifacts;
   - execution process;
   - required evidence;
   - terminal marker protocol;
   - stop conditions.
2. Core stage skill:
   - exact selected artifact schemas copied from workflow/package authority;
   - handoff formats that use only selected schema fields or runner evidence
     channels;
   - valid and invalid examples;
   - validation checklist;
   - completion criteria.

A prompt-only exception is allowed only for a deliberately trivial stage whose
artifact and handoff contract are fully declared elsewhere. Do not copy old
prompt structure just because an earlier workflow used it. Keep the stage
behavior, then re-author the asset split around structured handoff.

## Entrypoint Prompt Contents

Every entrypoint prompt must include these headings:

```text
Role:
Scope:
Inputs from dispatch:
Readable assets:
Writable artifacts:
Required evidence:
Legal terminal markers rendered by runtime:
Forbidden claims:
How to return evidence:
When to stop:
```

Read `references/entrypoint-prompt-pattern.md` for the template.

## Core Skill Contents

Every core stage skill must include:

- exact selected artifact schema, including required and optional fields, or
  explicit no-artifact/null behavior when the selected terminal action has no
  artifact schema;
- handoff format expected by the next stage or runtime;
- parseable JSON examples of valid and invalid artifacts; invalid examples
  must cover an extra undeclared field, a missing required field, and a type
  mismatch;
- validation checklist;
- completion criteria tied to the selected workflow's definition of done;
- instructions for preserving evidence and citing assumptions in selected
  schema fields only when declared, otherwise in runner evidence/report
  surfaces.

Read `references/core-entrypoint-skill-pattern.md` and
`references/artifact-handoff-patterns.md` before writing a core stage skill.

## Boundary References

Read `references/dispatch-and-evidence-boundaries.md` when a stage needs
dispatch envelope context, runner evidence boundaries, selected package asset
pins, or operator wait/intervention context.

Read `references/terminal-markers-and-runtime-authority.md` before adding or
reviewing terminal-marker text.

Read `references/worked-examples.md` when you need concise `kernel_ping`,
`simple_loop`, or hosted LAD entrypoint/core-skill examples.

Read `references/entrypoint-review-checklist.md` before approving an
entrypoint or core stage skill for package selection.

## Review Checklist

- The entrypoint prompt is concise enough for the stage runner to follow.
- The core skill carries structured schemas and handoff details.
- The prompt says what the stage owns, treats payload as data, and prevents
  non-implementation stages from doing downstream implementation work.
- The core skill is selected-schema-first: examples contain no undeclared
  artifact fields, markers are spelled exactly, and JSON examples are
  parseable.
- The prompt tells the agent how to select terminal markers, not what runtime
  effects those markers cause.
- No prompt or skill text claims queue movement, route legality, retry counts,
  approval, package import, capability, effect, or state mutation behavior.
- Artifacts contain only selected-schema fields; extra evidence or
  assumptions live in runner evidence/report surfaces unless the selected
  schema declares fields for them.
- The asset paths are package-relative and stable.
- Prompt assets and core skills do not contain API keys, OAuth tokens, local
  credential paths, provider secrets, or adapter config secrets.

## Reference Map

- `references/entrypoint-prompt-pattern.md`: prompt template and review rules.
- `references/core-entrypoint-skill-pattern.md`: core stage skill template.
- `references/artifact-handoff-patterns.md`: structured artifact and handoff
  patterns.
- `references/dispatch-and-evidence-boundaries.md`: dispatch, evidence,
  package asset, and wait/intervention boundaries.
- `references/terminal-markers-and-runtime-authority.md`: marker wording and
  runtime-authority boundary.
- `references/worked-examples.md`: selected-data examples for `kernel_ping`,
  `simple_loop`, and hosted LAD stages.
- `references/entrypoint-review-checklist.md`: final lint/review checklist.
