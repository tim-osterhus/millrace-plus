# LAD software factory workflow

LAD is an editable twelve-role graph in Millrace Plus for one software task. Recon and Planner prepare the work. Manager orders packets. Foreground roles implement and review packets in sequence. Arbiter checks the finished evidence against the original criteria. Learning is a separate guidance assessment once per packet after the second actual Fixer–Doublechecker pair, even when that review passes. A Learning failure records a failed assessment and returns the saved route without another Learning invocation.

## Start

Initialize an editable copy with `millrace-plus catalog init bundled:lad PATH`. Put the exact task in the project root’s `request.md` and the existing target in `foreground/target/`. Keep sibling `foreground/` and `learning/` workspaces. For greenfield work, add `--greenfield` to the setup node’s arguments before starting.

Use the same public controls as other workflow graphs. Standalone contained-target commands run with `python3 -S` and Python’s standard library. They do not import Plus or OS. Explicit shared-target mode lazily imports installed Plus to validate its target and call authority. Ordinary reads never upgrade a v1 session. The explicit upgrade path uses the installed Plus facade only when an operator selects it. See `context/harness.md`.

## Run limits and saved state

The graph is v4 and declares `max_parallel_invocations: 2`. It adds no total invocation cap, elapsed-time cap, or per-role quota. Finite Core limits selected at startup remain effective. The 96 deterministic command nodes use an explicit 90-second timeout for new stock copies. Native role nodes use 1,800 seconds. Capacity two is a ceiling, not evidence that foreground and Learning work overlapped. The command deadline is a finite operating allowance; it does not establish performance or retained-history fit. Existing copies keep their captured settings.

New sessions use `millrace-lad-session/v2`. Their unique `{id, kind}` events record history. They do not grant allowance. The session keeps the original request, criteria, packet lineage, occurrence inputs, accepted-result digests, and pending route. A used copy does not reset or automatically resume. Recover a pending Learning wait in its original Core run.

## Model and helper limits

The packaged graph selects Plus `claude_print` with profile `millrace-claude-print-profile/v1` and Claude Code 2.1.296. The profile omits model and effort so Claude keeps its native defaults. The adapter uses StructuredOutput with the runtime-supplied marker schema. It launches with `--dangerously-skip-permissions --permission-prompts none`. Use a disposable or version-controlled target. Inspect the requested scope before starting.

The stock graph uses direct role calls. Planner always stays direct. The only accepted helper-qualification scope is `offline_validator_fixture`. This fixture validates evidence handling. It does not qualify a live helper facility or authorize helper tasks. No live helper facility is qualified. Offline peers and fixture records are not native-route evidence. See `context/role-contracts.md`, `context/routes.md`, and `context/harness.md` for artifact, recovery, migration, and adapter boundaries. Captured `context/routing-policy.json` selects the finite presentation view and once-per-packet Learning; the initializer refuses a missing or unknown policy.


A service-owned child may instead receive `inputs.lad_target` in its normal
subworkflow input manifest. Its initializer validates the active service claim,
owned target, saved call and exact public child invocation. It writes the
immutable project `lad-target.json` before role preparation. Source checks and
assignments then use that validated external root. Each task keeps its own
request, session, artifacts and Learning seam. Missing or changed authority
requires reconciliation. Standalone copies continue to use `foreground/target/`.
This worker support does not supply persistent-service initialization or a loop.

## Optional engineering feedback (L-Q1)

A new, never-run copy can opt in before its first session:
`millrace-plus lad engineering-init --project-root COPY --profile PROFILE --history-home HISTORY [--core-settings SETTINGS]`.
Start from `context/engineering-profile.example.json`; replace its commands, IDs,
snapshot pin and bookkeeping allowance with the repository's own. The profile's
`path_policy_ref` names a `millrace-check-path-policy/v1` file beside it. The
history home lives outside the copy and target and holds an operator-admitted
initial `millrace-lad-requirements-snapshot/v1`; initialization indexes it once.
`--core-settings` is the JSON later given to `instance create --settings`; it lets
initialization report, and refuse, an operator timeout ceiling below a derived
check budget. Without it the ceiling is reported as unknown at capture.

Initialization captures the exact profile, policy, snapshot, node map and resolved
checks under `context/` and expands only that copy's graph with deterministic
phase, check and guard nodes. Each check has its own resolved per-check deadline
plus the declared bookkeeping allowance; there is no stock suite timeout, call,
retry or quality quota. Enabled copies require installed Plus. Copies without a
captured profile keep the exact legacy graph and behavior.

After normal task acceptance, publish the next requirements snapshot explicitly:
`millrace-plus lad engineering-publish --project-root COPY --history-home HISTORY --snapshot SHA256 --state-home STATE --instance ID --run ID --expected-state-version V [--service-home HOME --task ID]`.
It needs public stopped-success Core facts. A copy whose captured inputs name a
service `lad_target` also needs that exact task's settled terminal receipt; the
service is derived from the captured target, and optional selectors must match. A competing successor reports `engineering_history_stale`. With
`evaluation_export.enabled`, `engineering-export` copies selected accepted records
and an honest report to the declared destination; unrecorded values stay null.
