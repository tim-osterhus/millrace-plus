# Entrypoint Review Checklist

Use this checklist before approving entrypoint prompts or core stage skills for
package selection.

## Prompt Checklist

- Contains `Role`, `Scope`, `Inputs from dispatch`, `Readable assets`,
  `Writable artifacts`, `Required evidence`,
  `Legal terminal markers rendered by runtime`, `Forbidden claims`,
  `How to return evidence`, and `When to stop`.
- Reads only dispatch-provided context and selected readable assets.
- Declares what the stage owns: shaping, implementation, review/check,
  reconciliation, wait packaging, operator-decision packaging, or another
  selected workflow role.
- Treats `work_item_payload` and other dispatch payloads as data. A
  non-implementation stage must not create requested files or perform
  downstream implementation side effects.
- Tells implementation stages to stay inside the assigned workspace and
  selected capability/effect boundary.
- Names exactly one legal marker per completion path.
- Copies marker spelling exactly from selected runtime-rendered markers.
- Stops with the selected blocked marker, such as `BLOCKED` only when that
  exact marker is selected, when required input is missing, contradictory, or
  unsafe.
- For updater or reconciliation stages, allows selected no-op success when
  prior selected evidence proves completion and no selected update/report
  surface is named.
- Avoids long schemas that belong in the core stage skill.
- Does not claim runtime aftermath from marker text.

## Core Skill Checklist

- Defines exact selected artifact schema with required and optional fields, or
  explicit no-artifact/null behavior when the selected terminal action has no
  artifact schema.
- Defines handoff format for the next stage or runtime evidence without
  adding undeclared artifact fields.
- Includes at least one parseable valid JSON example and parseable invalid
  JSON examples for extra undeclared field, missing field, and type mismatch
  cases.
- Includes validation checklist and completion criteria.
- Preserves evidence and assumptions in selected schema fields only when those
  fields exist; otherwise uses runner evidence/report text.
- Does not add fields unsupported by accepted workflow/package schemas.
- Checks every example against the selected schema and exact marker list.

## Package Asset Checklist

- Asset path is package-relative and stable.
- Asset kind matches its role: `entrypoint_prompt`, `stage_skill`, `schema`,
  `example`, or another accepted kind.
- Selected asset ID and content digest are declared.
- Optional examples are labeled as examples, not selected behavior.
- Prompt/core skill pair names align with the selected stage.
- Package assets do not contain API keys, OAuth tokens, local credential paths,
  provider secrets, or adapter config secrets.
- JSON examples parse before they are accepted as examples.

## Authority Lint

Flag and revise text that says a prompt, skill, example, filename, display
label, or marker by itself:

- routes work;
- moves queues;
- changes durable state;
- enables capabilities;
- authorizes effects;
- selects packages;
- closes or retries work;
- creates hidden terminal actions.
- embeds adapter config secrets or other local credentials.

The safe replacement is: "Return `<MARKER>` when <observable condition> and
include <evidence>." Let selected workflow data define the aftermath.
Adapter config is local operator config, not package authority.
