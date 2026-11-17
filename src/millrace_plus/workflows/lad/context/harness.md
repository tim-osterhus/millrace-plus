# Native invocation and operator boundaries

Modified from LAD Factory setup, continuation, nested-model, and adapter-template Markdown at the pin in `PROVENANCE.md`. This file describes current adapter and migration limits. It does not authorize a provider call or helper task.

## Packaged Claude invocation

The stock graph selects Plus `claude_print` with profile `millrace-claude-print-profile/v1` and Claude Code 2.1.296. The graph omits model and reasoning effort, preserving native inheritance. The adapter launches with `--print --output-format json --json-schema` and `--dangerously-skip-permissions --permission-prompts none`. Millrace provides no approval bridge for these permission-bypassed calls. Use a disposable or version-controlled target. Review the requested scope before starting.

The adapter appends an instruction to use Claude Code’s StructuredOutput tool with the schema for that invocation. The `outcome` enum comes from the exact runtime-selected markers. The schema also requires string `summary` and an array of project-relative `refs`, with no extra fields. The generated `claude-prompt.txt` and `claude-result-schema.json` live in the invocation root. These are adapter files, not workflow workspace authority. Do not infer or access a temporary invocation path.

The graph’s native role timeout is 1,800 seconds, including the 11 provider-diagnosis Troubleshooter nodes. The 162 deterministic command nodes, including the 66 provider-wait guards, use an explicit 90-second timeout for new stock copies. These counts are a route inventory, not an execution budget. The workflow adds no total invocation or elapsed-time cap. Finite Core limits explicitly selected at startup remain in force. This finite operating allowance establishes no performance or retained-history fit; existing copies keep their captured settings. It grants no additional provider allowance.

## Direct work and optional helper evidence

The stock graph passes no helper flags, so `prepare_role.py` defaults to `helper_mode: direct` with null qualification. Planner always remains direct. Do not create nested tasks or make extra model calls from a role prompt.

The existing `prepare_role.py` accepts `--helper-mode` with `direct` or `native_helpers`. It also accepts `--helper-qualification PATH`. A `native_helpers` result must match its occurrence and include all required evidence:

- an evidence ref
- an independently issued, digest-pinned complete task inventory
- terminal observations for completed, failed, or cancelled tasks
- result refs, history logs, and integration evidence
- complete nested-task ancestry

Qualification refs bind `binding`, `facility_version`, `parent_task_id`, and an inventory ref. The only accepted scope is `offline_validator_fixture`. This fixture checks how the validator handles complete task evidence. It is not a live helper run, host binding, or permission. No live native helper facility is qualified. The workflow remains useful with the default direct mode.

## Legacy session upgrade

Copied commands of a routing-policy/v1 copy remain standard-library-only under `python3 -S` and never load Plus or Core. A captured routing-policy/v2 copy lazily loads installed Plus only for provider-wait records and guards: `scripts/provider_wait.py`, the provider index at initialization, provider preparation and the same-operation success check. The classifier, due guard and success check use Plus router modules that require Core, so the command-node `python3` must import both `millrace_plus` and Core (`millrace`). Initialization checks only the Plus import and refuses with `provider_recovery_requires_plus` before any role. A layout with Plus but not Core is not qualified. A v1 session is read under its original contract. Inspection, role preparation, and a new run never upgrade it. The explicit initializer path with `--upgrade-session` lazily loads the installed Plus facade. It requires the exact `--project-root`, `--session-id`, `--instance-id`, `--run-id`, `--state-home`, and `--expected-state-version` values. `--catalog-home` is optional. There is no `--joined` assertion.

The caller owns and joins the exact caller lifecycles and excludes other writers before upgrade. A paused run, a Core lock, or a quiescent-looking plane does not attest that OS or native helper callers have joined. Upgrade preserves the existing v1 session ID and archived source/history references. It does not revalidate old checks, permit stale guidance dispatch, auto-resume a seam, or qualify actual used-copy operations.

## Recovery and closure

The adapter does not retry native outcomes. Deterministic acceptance and preparation guards retain a digest/reference to result bytes at their original path, route, and source identity. They do not create or recheck an immutable rejected-byte copy. Use the graph’s selected recovery outcome. Do not issue a raw retry or redirect to normal preparation. An unknown native effect needs exact writer inspection and closure before replacement. An artifact or elapsed time does not prove task closure.

The graph acknowledges Learning’s saved route in the original Core run. Learning is never replaced or retried; a failure records the failed assessment first. A fresh run on a used copy does not resume the old seam. For a replacement, use the exact occurrence and source/guidance evidence selected by the guard. Do not change ledger or guidance files by hand.

The terminal-failure intent and cleanup rules remain workflow-owned. If a hard limit, cancellation, or unknown writer prevents CLOSE, preserve cleanup-required state and inspect exact run and plane evidence through public controls. A terminal projection alone is not proof that native or OS writers have stopped. These instructions do not qualify native routes, OS joins, or actual used-copy operations.


A shared-target occurrence names its validated external root and immutable target
record in both input and assignment. Native roles must use those pinned locators.
The initializer's live PID match is bootstrap correlation, not closure evidence.
Native helper refusal and route qualification requirements remain unchanged.
