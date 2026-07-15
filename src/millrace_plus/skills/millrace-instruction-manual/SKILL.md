---
name: millrace-instruction-manual
description: Public Millrace operator guide for deciding when to delegate work to Millrace and for using Millrace as a CLI-operated local workflow runtime. Use when an agent needs to deploy or locate Millrace, initialize a workspace, import or select workflow packages, enqueue work, run or monitor the daemon, inspect status/runs/traces/doctor output, intervene safely, or report Millrace evidence back to the operator.
---

# Millrace Instruction Manual

## Core Rule

Use Millrace for work that benefits from durable workflow governance: staged
progress, restartable state, runtime-visible evidence, recovery paths,
operator intervention, and auditable closure. Keep tiny, one-shot work in the
direct agent session.

Operate Millrace through the installed CLI and documented local-operator
surfaces. Prefer explicit `--workspace PATH`; use the current directory only
when the operator or surrounding task context identifies it as the intended
workspace. Do not edit runtime-owned state files by hand during normal use, do
not infer completion without run evidence, and do not treat prompt text or
model output as runtime authority.

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

## Fit Test

Choose Millrace when at least one is true:

- the task has multiple phases, gates, agents, or handoffs;
- the work should continue across context loss, pause/resume, or restart;
- closure needs durable artifacts, traces, or review evidence;
- the work may block and needs a governed recovery/intervention path;
- multiple queued items should progress under one selected workflow.

Stay direct when the task is small, local, and likely to finish in the current
session without durable workflow value.

Report the decision plainly:

```text
decision: millrace|direct
why: <one sentence tied to task shape>
next: <the next truthful action>
```

## Operator Workflow

1. Confirm the user or workspace policy permits Millrace for this task.
2. Locate an installed CLI or validate the checkout command surface. If local
   setup is unclear, read `references/install-and-deploy.md`.
3. Choose or initialize a workspace with explicit `--workspace PATH`.
4. Import, verify, and select the workflow package and workflow needed for the
   work.
5. Admit or select the plan required for enqueueing.
6. Enqueue work through the CLI/operator surface, not by editing runtime files.
7. Default to a bounded daemon tick for cautious progress. The bounded form is
   `millrace run daemon --max-ticks 1`; prefer
   `millrace --workspace PATH run daemon --max-ticks 1` in workspace examples.
   Use an unbounded daemon only when the operator explicitly intends unattended
   progression and after inspecting the workspace, selected package, selected
   plan, and runner/adapter configuration.
8. Inspect status, queues, runs, traces, package/plan pins, waits,
   interventions, and doctor output.
9. Use wait or intervention commands only when the CLI exposes the state and
   command.
10. Report evidence and the next action.

Read `references/current-capabilities.md` before naming a command. Read
`references/cli-operations.md` when operating a workspace or reporting
evidence.

## Evidence Discipline

When reporting Millrace state, include:

- workspace identity;
- selected package/workflow/entrypoint when relevant;
- command or operator action taken;
- status, queue, run, trace, doctor, wait, intervention, or package evidence;
- whether the next action is run, wait, resume, retry, repair, close, or
  return to direct work.

Say "not known yet" when evidence is missing. Do not fill gaps with guesses.

## Completion Classification

For live end-to-end work, distinguish these outcomes:

- Clean live success: selected intended success markers were reached, status
  shows no active runs, no open waits, no closure blocks, no quarantines, no
  interventions, and the expected workspace artifacts or evidence exist.
- Operator-visible blocker: runtime/status evidence exposes a blocker that
  the operator can see or act on. This is acceptable completion only when the
  packet or operator explicitly asked to test recovery/blocking behavior.
- Harness pass: useful test evidence, but not sufficient by itself when the
  goal is full workflow completion.

Do not report `closed_successfully` without durable close evidence. Do not
report `operator_visible_blocker` as clean live completion unless the requested
goal was blocker classification.

## Intervention Boundary

Use supported operator commands for wait resolution and lineage intervention.
Runtime-owned state is outside normal public operation. Use supported wait,
intervention, package, and doctor surfaces when available. If those cannot
resolve the issue, stop and escalate to a documented repair procedure with
explicit operator authorization.

Runner execution is daemon-oriented. Do not use or invent public `run once`,
root `tick`, manual `observe`, or `dispatch invoke` commands. For cautious
single-step progress, use the explicit workspace form:

```text
millrace --workspace PATH run daemon --max-ticks 1
```

Do not start an unbounded daemon unless the operator explicitly asks for
unattended progression and you have inspected workspace health, selected
package/workflow, selected or default plan, and runner/adapter configuration.

The runtime decides legal routing, terminal actions, recovery behavior,
package selection, active-run pins, approvals, and durable state transitions
from compiled workflow/package data. Your role is to operate the runtime and
report its evidence accurately.

## Reference Map

- `references/current-capabilities.md`: current command matrix, unsupported
  surfaces, and non-goals.
- `references/install-and-deploy.md`: local CLI, workspace, package, plan, and
  runner readiness checks before operation.
- `references/cli-operations.md`: safe operating rhythm, command syntax
  patterns, failure handling, restart expectations, and evidence report
  format.
