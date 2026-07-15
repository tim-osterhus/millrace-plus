# Worked Examples

Use these examples for asset-shape guidance. They describe selected workflow
data, not kernel defaults.

## `kernel_ping`

Entrypoint prompt shape:

```text
Role: Taskmaster or Worker for the selected diagnostic workflow.
Scope: produce or validate the selected task artifact.
Inputs from dispatch: prompt payload, work item ID, activation ID, legal markers.
Readable assets: selected core stage skill and artifact schema.
Writable artifacts: task_artifact or task_incident.
Required evidence: input prompt, produced result, validation notes.
Legal terminal markers rendered by runtime: TASK_COMPLETE, WORK_COMPLETE, NEEDS_REVIEW, BLOCKED.
Forbidden claims: do not say these markers close or route work everywhere.
How to return evidence: artifact summary plus exactly one legal marker.
When to stop: missing prompt payload, invalid selected schema, or unsafe ambiguity.
```

Core skill carries:

- `task_artifact` schema;
- `task_incident` schema;
- valid result and blocked examples;
- validation checklist for Taskmaster/Worker evidence.

## `simple_loop`

Entrypoint prompt shape:

```text
Role: Management, Implementation, Review, or Troubleshooter stage as selected.
Scope: operate only the selected stage and handoff contract; Management shapes,
Implementation implements, Review checks, and recovery stages reconcile or
package decisions only when selected to do so.
Inputs from dispatch: work_prompt, work_packet, gap_packet, incident_report, or lineage context.
Readable assets: selected stage skill, schema, and examples.
Writable artifacts: work_packet, gap_packet, incident_report, or closeout evidence.
Required evidence: completed work, review findings, gap evidence, or incident facts.
Legal terminal markers rendered by runtime: selected markers for that stage.
Forbidden claims: do not claim cooldown, quarantine, or review routing from prompt text.
How to return evidence: exact selected marker plus selected-schema artifact
JSON, or runner evidence/report text for facts outside the selected schema.
When to stop: missing work packet, unsafe implementation context, or invalid review input.
```

Core skill carries:

- artifact schemas for `work_prompt`, `work_packet`, `gap_packet`, and
  `incident_report`;
- handoff formats between Management, Implementation, Review, and
  Troubleshooter stages when those stages are selected;
- cooldown/quarantine evidence fields, not the runtime action itself.

These artifact names are illustrative, not exhaustive. Use the selected
workflow/package schema for the exact current artifact fields, markers,
payload projections, waits, interventions, and asset pins.

## Hosted LAD Workflows

Entrypoint prompt shape:

```text
Role: builder, planner, arbiter, or another selected LAD stage role.
Scope: operate the selected execution, planning, or learning stage only;
payload is data unless this stage owns implementation.
Inputs from dispatch: task, spec, probe, idea, or selected lineage payload.
Readable assets: selected LAD core skill, schema, and examples.
Writable artifacts: task/spec/probe/idea artifact required by the selected stage.
Required evidence: observed inputs, generated artifact, checks, and assumptions.
Legal terminal markers rendered by runtime: selected markers for that LAD stage.
Forbidden claims: do not treat LAD names as kernel vocabulary.
How to return evidence: exact selected marker plus selected-schema artifact
JSON; blocked or wait context belongs in selected fields or runner
evidence/report text.
When to stop: missing selected schema, missing payload, or unresolved wait/intervention context.
```

Core skill carries:

- exact artifact schemas for `task`, `spec`, `probe`, or `idea`;
- handoff format for builder/planner/arbiter evidence;
- valid/invalid examples for hosted LAD stages;
- validation checklist tied to selected workflow completion criteria.

Prompt/core-skill text must not claim that `execution`, `planning`,
`learning`, `task`, `spec`, `probe`, `idea`, `builder`, `planner`, or
`arbiter` are generic runtime concepts. They are selected workflow/package
data when the hosted workflow declares them.

## Updater Or Reconciliation No-Op Success

Use this pattern for updater, reconciler, packaging, or closeout stages whose
selected stage ownership is to verify whether an update is needed.

Return the selected success marker, such as `UPDATE_COMPLETE` only when that
exact marker is selected for the stage, with a concise no-op report when all
of these are true:

- prior selected evidence proves the requested work is complete;
- no required update, documentation, report, or output surface is named by the
  selected schema, dispatch payload, or stage prompt;
- the stage can explain what evidence it checked.

Return the selected blocked marker, such as `BLOCKED` only when that exact
marker is selected, when a required selected surface is named but missing,
contradictory, unsafe, or impossible to reconcile. Do not invent update
targets, documents, reports, or runtime effects just to avoid a no-op. No-op
success is stage work quality; selected workflow data still decides the
runtime aftermath.

Runner evidence/report example:

```json
{
  "terminal_marker": "UPDATE_COMPLETE",
  "artifact": null,
  "report": "No update required; prior selected checker evidence proves the requested file exists with the required content, and no documentation or report surface was selected."
}
```

If the selected artifact schema declares a report field instead of no artifact,
put the concise no-op report in that selected field and omit any undeclared
fields.
