# Dispatch And Evidence Boundaries

Use this reference when a stage prompt or core skill needs dispatch context,
runner evidence, package asset pins, or wait/intervention context.

## Dispatch Envelope Context

Treat dispatch as selected runtime evidence. It may include:

```text
workflow and plan identity
stage and activation identity
source work item and lineage context
input payload projection
legal terminal markers
artifact schema refs
entrypoint prompt ref
core stage skill refs
selected package/asset pin data
runner binding and capability/effect refs as data
```

Read from dispatch. Do not invent missing context from filenames, display
labels, or examples.

## Runner Evidence Envelope

The runner/agent returns evidence. Evidence may include:

```text
terminal_marker:
artifact_refs:
artifact_bodies_or_summaries:
observed_inputs:
assumptions:
blocked_reason:
diagnostics:
```

The runner reports what happened. It does not decide legal workflow aftermath.

## Artifact Schema Authority

Artifact schemas come from selected workflow/package data or paired core stage
skills referenced by that selected data. A prompt can tell the agent to follow
the schema; it cannot create a new required field without schema support.

Treat selected artifact schemas as closed unless the selected schema says
otherwise. Do not add undeclared fields for identity, status, evidence,
assumptions, or downstream context just because a handoff example would be
convenient.

When selected artifact schema does not declare evidence or assumptions fields,
put evidence, checks, assumptions, and missing-data notes in the runner
evidence/report channel. If the selected schema does declare fields for those
facts, use exactly those fields and selected JSON types.

If a stage needs a new field, update the workflow/package schema and revalidate
before relying on it.

## Package Asset Authority And Digest Pins

Entrypoint prompts, core stage skills, schemas, examples, and templates are
package assets when selected. Their package-relative paths help locate bytes.
Their selected asset IDs and content digests bind the selected plan to exact
asset contents.

Do not treat local path names, package display labels, or example filenames as
selected authority. If an asset is not selected and pinned, it is not stage
authority for that run.

Do not put API keys, OAuth tokens, local credential paths, provider secrets, or
adapter config secrets in package assets or evidence reports. Adapter config is
local operator config, not package authority.

## Operator Wait And Intervention Context

When dispatch or status indicates a wait, quarantine, or intervention option:

- preserve the wait/intervention ID exactly;
- record the selected option and required payload shape;
- include evidence checked by the stage;
- return the selected blocked marker or another legal marker only when its
  condition is met;
- do not promise that an operator action will resume, close, or revise work
  unless selected workflow data and current status evidence support that.
