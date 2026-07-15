# Planes And Compilation

Use this reference when choosing partitions, stages, and selected workflow
authority.

## Partitions

Planes, lanes, roles, and similar groupings are workflow-defined operating
partitions. They are optional. Choose them only when the workflow needs a
separate ownership lane, queue boundary, concurrency rule, status slice, or
runner-binding policy.

When a workflow uses plane-like partitions, a plane may be scheduled as a
separate agent/work lane only if the compiled workflow declares that
partition, concurrency, and runner-binding policy.

Good partition boundaries are based on ownership:

- management and intake shaping;
- implementation or execution;
- review and closure;
- research and synthesis;
- policy or compliance evaluation;
- vendor or option selection;
- learning, experiment, or idea evaluation.

Do not make `planning`, `execution`, `learning`, `management`,
`implementation`, or `review` universal names. They are selected workflow data
when a workflow declares them.

## Stages

A stage belongs to selected workflow data and has:

- a stable stage kind ID;
- input queue families;
- output queue families;
- allowed terminal markers;
- runner binding;
- entrypoint prompt asset;
- optional core stage skill assets;
- readable context and writable artifact expectations;
- recovery, wait, approval, or intervention links when applicable.

Stage prompts can instruct the agent. They cannot make a terminal marker legal
or select a runtime route.

## Compiler-Owned Checks

The compiler should reject workflows when:

- IDs are blank, duplicate, malformed, or ambiguous;
- a stage references an unknown partition, queue family, runner, asset, or
  marker;
- a graph route has no legal target;
- a terminal action references undeclared behavior;
- package assets are missing or unpinned;
- hidden defaults would be needed for the workflow to run;
- examples or display metadata are treated as selected authority.

## Selected Authority

Selected compiled workflow data should include the exact authority the runtime
needs:

- workflow identity and entrypoint;
- graph nodes and routes;
- partition and stage declarations when used;
- queue families and payload schemas;
- terminal outcomes and actions;
- waits, approvals, recovery, counters, cooldowns, and intervention options;
- runner bindings, capability policy refs, and effect refs;
- selected package and asset pins;
- operator presentation/status metadata when selected.

Unselected workflows, unused package assets, source provenance, display text,
and prompt prose do not create selected runtime authority.
