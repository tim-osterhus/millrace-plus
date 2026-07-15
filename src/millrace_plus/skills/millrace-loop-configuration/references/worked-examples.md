# Worked Examples

Use these examples to understand selected workflow data. They are not kernel
defaults and do not prove that a field exists in every schema.

## `kernel_ping`

Authored data:

- one partition named `craft`;
- a `prompt` queue family for operator input;
- `task_artifact` and `task_incident` artifact shapes;
- Taskmaster and Worker stage roles;
- legal terminal markers such as `TASK_COMPLETE`, `WORK_COMPLETE`,
  `NEEDS_REVIEW`, and `BLOCKED`.

Compiler freezes:

- the `craft` partition only if declared;
- prompt queue payload shape;
- Taskmaster/Worker stage kinds and runner bindings;
- marker-to-action mapping;
- selected entrypoint prompt and core skill asset pins.

Runtime enforces:

- only declared queue families and stage routes;
- selected terminal action legality;
- recovery or blocked behavior declared for the workflow;
- selected plan fingerprint and package/asset pins.

Runner/agent reports:

- produced task artifact or incident evidence;
- selected terminal marker text;
- assumptions and missing inputs.

Prompt/skill text must not claim:

- that `craft`, Taskmaster, or Worker are kernel roles;
- that `TASK_COMPLETE` or `WORK_COMPLETE` closes work everywhere;
- that a marker routes to another stage unless selected workflow data says so.

## `simple_loop`

Authored data:

- Management, Implementation, and Review partitions when the workflow chooses
  that split;
- `work_prompt`, `work_packet`, `gap_packet`, and `incident_report` artifacts;
- a Troubleshooter stage or recovery lane when declared;
- cooldown and quarantine policies for repeated failure or blocked work.

Compiler freezes:

- each partition, queue family, stage kind, terminal marker, cooldown counter,
  quarantine trigger, and recovery action;
- package-relative asset paths and digests for prompts/skills/examples;
- selected presentation metadata for operator status.

Runtime enforces:

- enqueue routes into declared queue families;
- Management-to-Implementation and Implementation-to-Review handoffs only as
  compiled;
- Review gap, incident, cooldown, and quarantine behavior only when selected;
- lineage and activation constraints across restart and replay.

Runner/agent reports:

- work packet completion evidence;
- review findings or gap packet;
- incident report and blocked evidence;
- terminal marker matching the selected stage contract.

Prompt/skill text must not claim:

- that Management/Implementation/Review are universal planes;
- that Troubleshooter always exists;
- that cooldown or quarantine happens because a prompt says so.

## Hosted LAD Workflows

Authored data:

- selected partitions or queues named `execution`, `planning`, or `learning`
  when the hosted workflow declares them;
- artifact families such as `task`, `spec`, `probe`, and `idea`;
- stage roles such as `builder`, `planner`, and `arbiter`;
- runner bindings, capability/effect refs, waits, and recovery paths for the
  hosted workflow.

Compiler freezes:

- LAD package/workflow/entrypoint selection;
- selected graph, queue families, stages, markers, and terminal actions;
- selected artifact schemas and package asset pins;
- runner/capability/effect refs as data.

Runtime enforces:

- the admitted LAD selected plan, not the names themselves;
- selected queue, wait, recovery, and intervention legality;
- active-run package pins across package updates or disables;
- status projection from durable runtime evidence.

Runner/agent reports:

- task/spec/probe/idea artifacts as required by the selected stage;
- builder/planner/arbiter evidence envelopes;
- terminal markers and blocked/wait context.

Prompt/skill text must not claim:

- that `execution`, `planning`, `learning`, `task`, `spec`, `probe`, `idea`,
  `builder`, `planner`, or `arbiter` are kernel vocabulary;
- that hosted LAD behavior generalizes to every workflow;
- that examples replace compiler-accepted workflow/package schemas.
