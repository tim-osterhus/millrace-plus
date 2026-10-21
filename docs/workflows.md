# Workflow Guide

Millrace Plus contains six selectable workflow configurations in the public
release set. Each one is a complete decision graph with its own queues,
stages, outcomes, recovery rules, and selected prompt and skill assets.

Plane and stage names describe a workflow; they are not built-in Millrace
runtime concepts.

## `simple_loop`

`simple_loop` is the smallest general-purpose software-work loop in the
package. It is useful for learning Millrace and for bounded tasks that still
benefit from explicit review.

Before running it, prepare project notes inside the initialized workspace:

```bash
mkdir -p "$WORKSPACE/docs/reviews"
# For a fresh workspace, seed one project note if it does not exist.
if [ ! -e "$WORKSPACE/docs/project.md" ]; then
  printf '%s\n' '# Project notes' 'Record the task and its acceptance criteria here.' \
    > "$WORKSPACE/docs/project.md"
fi
```

Fill in the project note with the task and acceptance criteria. The review
stage requires a nonempty `docs` root and owns review-note writes under
`docs/reviews`; Worker must leave this root unchanged. Missing and empty roots
refuse reviewer preparation. Workspace initialization does not create these
workflow-specific notes.

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

## Private `demo`

The source candidate also contains `demo@0.1`, a private data-only decision
tree copied from the accepted intermediate proof. It retains fanout and join,
the expected-failure and repair path, an operator wait, and one bounded
revision before closure. Its two markdown assets describe the entrypoint and
context contract. Compile checks use a narrowly selected synthetic runner
policy; no public command or provider execution is included.

## Private `lad.full.pi`

`lad.full.pi@0.1` is an opt-in private copy of `lad.full` for a bounded Pi RPC
report channel. It retains the seventeen stages, selected artifact schemas,
terminal markers, routing, capabilities, and completion behavior of `lad.full`.
Each copied entrypoint preserves its role and domain evidence obligations, then
adds the Pi strict final-text rule: exactly `marker`,
`artifact_payload_candidate`, and `observation_payload_candidate`, with the
narrative carried by the optional generic `runner_report` string inside the
observation candidate when required.

The Pi configuration maps five authenticated runtime failure kinds:
`timeout`, `invocation_failed`, `result_parse_failed`, `output_too_large`, and
`result_rejected`. Seven execution roles (Builder, Checker, Fixer,
Doublechecker, Updater, Troubleshooter, and Consultant) route to
Troubleshooter under the execution recovery policy. Six planning roles (Recon,
Planner, Manager, Mechanic, Auditor, and Arbiter) route to Mechanic under the
planning recovery policy. The four learning roles have no default mapping.

These mappings cover five Plus workflows. `execution.lad` routes seven
execution roles to Troubleshooter. `execution.lad_integrator` routes eight
roles, including Integrator, to Troubleshooter. `planning.lad`, `lad.full`,
and `lad.full.pi` route six planning roles to Mechanic and seven execution
roles, including Consultant, to Troubleshooter.

Only authenticated `ApplyRunnerRuntimeFailure` selects a mapped mechanical
recovery. It requires a settled authenticated failure: a failed adapter
completion or a completed candidate with an exact persisted semantic refusal.
For `result_rejected`, Millrace preserves the original candidate with its
`COMPLETED/SUCCESS` transport state. A separate persisted semantic refusal
supplies the typed failure.

Supported authenticated refusals may use the `result_rejected` mapping. Raw
candidate text cannot create a failure kind or its evidence. Pi command-level
timeouts cannot create typed failure evidence.

The component-free Pi path parses the exact three-key JSON envelope. The kernel then
applies the selected plan's declared outcome and action. Pi does not validate
`marker` against an authored enum.

An authored marker may name `RUNTIME_FAILURE` only when the selected graph
declares that outcome and an action for it. The kernel follows the declared
action under the finite policy. This authored marker does not become a typed mechanical failure. The
`RUNTIME_FAILURE` label alone does not make a marker runtime-owned. Unknown or
invalid candidates remain refused. Component-bound Millforge separately
validates provider result mappings.

A proven pre-external `invocation_failed` may map when recorded start intent
confirms there is no external owner and cleanup is `not_required`. Unclassified
or indeterminate start exceptions remain fatal. A stage invocation deadline
maps to `timeout` only after Millrace authenticates the settled completion and
confirms cleanup. Explicit user or global cancellation does not select the
typed mapping.

The recovery prompt receives a bounded, redacted diagnostic and recorded
source references through `governance_context.runtime_failure_source`. It
uses this handoff as evidence only. The handoff grants no authority or external
ownership. A semantic refusal or invalid candidate remains rejected. Recovery
cannot retroactively accept the candidate. Any later candidate must pass
normal selected validation.

Runtime-failure policies allow one immediate attempt. They start a 15-minute
cooldown at attempt 2 and quarantine at attempt 3. Recovery returns to the
recorded source. The runtime refuses recovery if it cannot authenticate source
evidence, or if the selected policy or action is missing, inconsistent, or
unsafe.

All seventeen selected Pi bindings use `pi_rpc`. They keep
`component_pin: null` and empty runner-produced terminal result mappings. All
select the same Core-owned payload-capacity pin. Six bindings use nested schema
version 6. Eleven use version 4.

Thirteen execution and planning roles carry typed mappings for the five failure
kinds. The four learning roles have no default mapping. All five capability
declarations remain explicit.

The package includes one inert transport note and one opaque conformance
fixture. The frozen submitted Core wheel currently reports a completion runner
capacity error for this component-free binding, so the candidate is review
material and has no live runtime or provider claim. See [Private Pi workflow](pi-workflow.md).

Its closure completion accepts an inline request body up to 131072 bytes. Larger
evidence uses `materialized_evidence_pages` overflow within the same 96-item
evidence window. The Millforge-bound `planning.lad` and `lad.full` variants keep
their 16384-byte limit. The overflow semantics and refusal boundaries are in
[Private Pi workflow](pi-workflow.md).

## Choosing A Workflow

| Need | Start with |
| --- | --- |
| Small task with explicit review | `simple_loop` |
| Execute an existing software task | `execution.lad` |
| Add a repository-integration pass | `execution.lad_integrator` |
| Turn specs or incidents into completed work | `planning.lad` |
| Include evidence-driven learning | `lad.full` |
| Study a non-LAD four-plane graph | `vendor_selection` |

## Deferred experimental workflow

The experimental `execution.lad_codex_semantic_worktree` workflow is deferred
from this MVP release. It is not selectable and its exclusive assets are not
shipped in the manifest, wheel, or source archive. The campaign prerequisite
defect leaves its results inconclusive; this cut makes no semantic-workspace
efficacy or incompatibility claim. Generic context mechanisms remain runtime
capabilities where selected by an independently justified workflow.

For a workflow with different stages or routing, use
[Authoring workflows](authoring.md) instead of forcing the problem into one of
these configurations.
