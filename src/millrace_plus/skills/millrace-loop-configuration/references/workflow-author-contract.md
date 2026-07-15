# Workflow Author Contract

Use this reference when reviewing or drafting authored workflow/package data.
It is a checklist, not a substitute for the active schema accepted by the
compiler.

## Workflow-Author Checklist

- The manifest identifies the package and every selectable workflow.
- Every workflow entry declares entrypoints, selected authority, required
  assets, and required dependencies.
- Every selected asset has an asset ID, kind, media type, encoding, byte
  length, digest, package-relative path, selection role, and authority
  participation rule.
- Every stage has declared input queues, legal terminal markers, output
  actions, runner binding, and readable/writable asset/artifact expectations.
- Every stage has an ownership row: shaping, implementation, review/check,
  reconciliation, wait packaging, operator-decision packaging, or another
  selected workflow-defined role.
- Every queue family has a payload shape and declared producers/consumers.
- Every terminal marker has an observable condition and a selected terminal
  action or terminal outcome.
- Every wait, approval, retry counter, cooldown, quarantine, and intervention
  path has a selected resolution path.
- Every runner, capability, and effect reference is data. Native runner code,
  API keys, OAuth tokens, local credential paths, provider secrets, adapter
  config secrets, and package-owned executable behavior stay outside package
  authority.
- Operator presentation metadata is selected data only when the schema accepts
  it; otherwise it is display text.

## Selected Compiled-Plan Field Guide

Expect selected authority to carry:

```text
workflow identity and entrypoint
selected package/dependency/asset pins
graphs and route targets
partitions and stages when declared
queue families and payload schemas
terminal outcomes and terminal actions
waits, recovery, counters, cooldowns, and interventions
runner bindings, capability policy refs, and effect refs
artifact schema IDs and presentation metadata
```

Do not add fields because an example needs them. If a field is not accepted by
the schema/compiler, record it as unresolved authoring intent.

## Stage Ownership Matrix

Before writing entrypoint prompts or core stage skills, define a matrix:

```text
stage_id:
owns:
payload_handling: data_only|implementation_allowed
may_write_artifacts:
may_create_files_or_side_effects:
handoff_to_next_stage:
blocked_when:
success_when:
```

Use `implementation_allowed` only for stages whose selected scope owns
implementation. Shaping, management, review/check, arbitration,
reconciliation, wait packaging, and operator-decision packaging stages treat
payloads as data. They transform, verify, reconcile, or package evidence; they
do not perform downstream implementation work unless the selected workflow
explicitly grants that ownership.

Implementation stages still stay inside the assigned workspace, selected
runner binding, capability policy refs, effect refs, and selected writable
artifact boundaries.

## Package Manifest Authoring

Package manifests declare deterministic package identity, workflow entries,
asset records, dependencies, compatibility, canonicalization, and digest data.
Unknown authority fields should be refused unless they live under a
non-authoritative metadata subtree accepted by the schema.

Publication scope, source paths, display text, local provenance, import time,
and operator notes are not selected runtime authority. They may help humans
inspect the package, but they cannot make a workflow route legal.

## Decision Graph And Terminal Actions

For every stage decision, author:

```text
stage:
input_queue_family:
terminal_marker:
observable_condition:
terminal_action:
target_queue_or_terminal:
payload_projection:
recovery_or_wait_path:
```

Terminal actions are selected workflow data. Prompt text may tell the agent
when to render a marker, but the selected graph decides what that marker means.

## Queue Families, Lineage, And Activations

Queue families describe where work can enter, what payload it carries, and
which stage may claim it. Lineage and activation data should be preserved
across handoffs so status, replay, restart, wait, and intervention evidence can
be checked against selected authority.

Payload projections must be explicit. Do not rely on the next stage reading an
earlier prompt or unstructured prose to recover missing fields.

## Recovery, Waits, And Interventions

Declare:

- retry counters and ceilings;
- cooldown or wait conditions;
- quarantine triggers;
- intervention options and payload schemas;
- close, revise, and resume paths;
- status evidence needed after recovery.

If a blocked path needs an operator decision, model that as selected wait or
intervention data. Do not encode the decision solely in prompt prose.

## Capabilities, Effects, Approvals, And Runner Bindings

Runner bindings and provider/capability/effect references are selected data.
They identify what the runtime may request from adapters; they do not ship
native runner code, API keys, OAuth tokens, local credential paths, provider
secrets, adapter config secrets, broad plugin execution, or automatic external
effects. Adapter config is local operator config, not package authority.

Approval policies should name the selected condition, evidence, and resolution
shape. A package can declare policy data, but the import itself does not grant
approval or capability.

## Operator Status And Presentation Metadata

Status views should be derived from runtime/package evidence:

- selected package/workflow/entrypoint;
- plan fingerprint and default-plan status;
- queue and run counts;
- waits, interventions, quarantines, and recovery counters;
- recent governance/trace events;
- package health and active-pin aftermath.

Display labels are presentation. They do not change selected authority.

## Validation Matrix And Negative Tests

Every workflow should have at least one positive and one negative proof for:

- package manifest and asset digest validation;
- workflow selection and plan admission;
- queue enqueue and payload projection;
- stage terminal marker handling;
- blocked, wait, recovery, or intervention paths;
- restart/status projection;
- prompt/skill non-authority.

Useful negative cases include missing asset pins, undeclared terminal markers,
unknown queue families, hidden fallback routes, missing payload fields, invalid
runner refs, package-disabled selection, and prompt text that claims a runtime
effect not present in selected workflow data.

## Live Completion Classification

Clean live success requires more than a harness pass. For full live workflow
completion, require selected intended success markers, no active runs, no open
waits, no closure blocks, no quarantines, no interventions, and the expected
workspace artifacts or evidence.

An operator-visible blocker is a valid classification only when the packet or
operator explicitly tests recovery/blocking behavior, or when current runtime
evidence truly shows a blocker. Do not count `operator_visible_blocker` as
clean live completion for work whose goal is closed successfully.
