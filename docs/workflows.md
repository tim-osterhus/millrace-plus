# Workflow Guide

Millrace Plus contains six selectable workflow configurations. Each one is a
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

For a workflow with different stages or routing, use
[Authoring workflows](authoring.md) instead of forcing the problem into one of
these configurations.
