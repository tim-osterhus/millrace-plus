# Workflow Guide

Millrace Plus contains seven selectable workflow configurations. Each one is a
complete decision graph with its own queues, stages, outcomes, recovery rules,
and selected prompt and skill assets.

Plane and stage names describe a workflow; they are not built-in Millrace
runtime concepts.

## `simple_loop`

`simple_loop` is the smallest general-purpose software-work loop in the
package. It is useful for learning Millrace and for bounded tasks that still
benefit from explicit review.

| Plane | Stage | Responsibility |
| --- | --- | --- |
| Management | Manager | Turns an incoming prompt into a work packet with a definition of done |
| Implementation | Worker | Executes the work packet or returns it for clarification |
| Review | Reviewer | Checks the result against the definition of done and reports gaps |
| Recovery | Troubleshooter | Diagnoses blocked work without belonging to one domain plane |

Work enters through the `work_prompt` queue. Insufficient work packets return
to Manager. Review gaps return to Worker. Repeated blocked recovery eventually
pauses or quarantines the lineage for the operator instead of looping forever.

## `execution.lad`

This is the one-plane LAD software-execution workflow. It accepts an
articulated task and routes it through implementation, checking, repair,
double-checking, update, troubleshooting, and consultation stages as needed.

Use it when planning has already happened elsewhere and the task is ready to
execute.

## `execution.lad_integrator`

This variant adds Integrator after the initial build step. Integrator reviews
the implementation in the context of the surrounding repository before the
normal checking and repair path continues.

Use it when cross-module coherence deserves a distinct pass.

## `planning.lad`

This workflow combines Planning and Execution. It accepts specs, probes,
incidents, and already-articulated tasks.

Planning stages investigate context, shape work, audit the plan, and evaluate
closure. The resulting tasks move into the same LAD Execution stages used by
the execution-only configurations. Failed closure checks can create explicit
remediation work rather than silently reopening completed work.

The accepted Checker artifact is the immutable execution baseline. The accepted
Arbiter verdict carries the reusable rubric for the closure target. Later
evaluations use bounded post-anchor evidence. Observations do not promote work
automatically from outside the active baseline or rubric.

## `lad.full`

Full LAD adds a Learning plane to Planning and Execution. Learning stages can
analyze evidence, prepare reusable guidance, curate accepted improvements, and
record a truthful no-op when an optional skill source is unavailable.

Learning is selected workflow behavior, not a background service. Its
concurrency, generated work, artifacts, and completion rules are all declared
in the compiled plan.

## `vendor_selection`

`vendor_selection` demonstrates a graph outside the software-development
domain. Its four planes are:

| Plane | Stages |
| --- | --- |
| Requirements | Request Intake, Policy Screener, Requirement Freezer |
| Sourcing | Catalog Sourcer, Candidate Packager |
| Evaluation | Rubric Evaluator, Conflict Checker |
| Authorization | Award Decider, Decision Packager |

The workflow normalizes a purchase request, applies category and budget rules,
sources candidates, evaluates them in parallel, joins rubric and conflict
evidence, and prepares a decision. It stops at a durable operator wait rather
than approving a purchase autonomously.

## Choosing A Workflow

| Need | Start with |
| --- | --- |
| Small task with explicit review | `simple_loop` |
| Execute an existing software task | `execution.lad` |
| Add a repository-integration pass | `execution.lad_integrator` |
| Turn specs or incidents into completed work | `planning.lad` |
| Include evidence-driven learning | `lad.full` |
| Study a non-LAD four-plane graph | `vendor_selection` |
| Run governed semantic-worktree execution | `execution.lad_codex_semantic_worktree` |

## `execution.lad_codex_semantic_worktree`

This governed semantic-worktree workflow is the Plus consumer of the public
v0.22.3 context contract. Its version is `0.2`.

| Stage | Context behavior |
| --- | --- |
| Builder | Reads required task and predecessor evidence first; selects named catalog entries on demand when relevant |
| Checker | Reviews the direct Builder predecessor and selects only relevant named evidence on demand |
| Fixer | Uses the active finding and accepted baseline; recovery-cycle history is discoverable when present |
| Doublechecker | Revalidates the original baseline and latest Fixer evidence; recovery-cycle history is discoverable when present |
| Troubleshooter | Produces a typed repair plan with an explicit legal re-entry or unrecoverable reason; predecessor artifacts and attempt history are discoverable when present |
| Updater | Reconciles complete selected semantic-root snapshots through the declared writeback contract; attempt history is discoverable when present |

Catalog entries are bounded metadata. Stages do not read every catalog entry,
and required material is always consumed first. The workflow graph owns
re-entry, blockage, and writeback outcomes; prompt text supplies evidence and
cannot create routes.

Attempt history is never a stage-start prerequisite because ordinary graph
routes may legally reach Fixer, Doublechecker, Troubleshooter, or Updater
before any runtime recovery-attempt record exists.

Builder, Fixer, and Troubleshooter leave mutable project documentation in the
project working tree rather than hydrating it into their immutable context
checkouts. Checker and Doublechecker may select documentation for read-only
review, while Updater changes selected documentation only through its explicit
writeback contract.

For a workflow with different stages or routing, use
[Authoring workflows](authoring.md) instead of forcing the problem into one of
these configurations.
