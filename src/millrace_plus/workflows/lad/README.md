# LAD software factory workflow

LAD is an editable twelve-role graph in Millrace Plus for one software task. Recon and Planner prepare the work. Manager orders packets. Foreground roles implement and review packets in sequence. Arbiter checks the finished evidence against the original criteria. Learning is a separate guidance assessment after the second actual Fixer–Doublechecker pair, even when that review passes.

## Start

Initialize an editable copy with `millrace-plus catalog init bundled:lad PATH`. Put the exact task in the project root’s `request.md` and the existing target in `foreground/target/`. Keep sibling `foreground/` and `learning/` workspaces. For greenfield work, add `--greenfield` to the setup node’s arguments before starting.

Use the same public controls as other workflow graphs. Ordinary copied-workflow commands run with `python3 -S` and Python’s standard library. They do not import Plus or OS. Ordinary reads never upgrade a v1 session. The explicit upgrade path uses the installed Plus facade only when an operator selects it. See `context/harness.md`.

## Run limits and saved state

The graph is v4 and declares `max_parallel_invocations: 2`. It adds no total invocation cap, elapsed-time cap, or per-role quota. Finite Core limits selected at startup remain effective. Command nodes use a 10-second timeout. Native role nodes use 1,800 seconds. Capacity two is a ceiling, not evidence that foreground and Learning work overlapped.

New sessions use `millrace-lad-session/v2`. Their unique `{id, kind}` events record history. They do not grant allowance. The session keeps the original request, criteria, packet lineage, occurrence inputs, accepted-result digests, and pending route. A used copy does not reset or automatically resume. Recover a pending Learning wait in its original Core run.

## Model and helper limits

The packaged graph selects Plus `claude_print` with profile `millrace-claude-print-profile/v1` and Claude Code 2.1.296. The profile omits model and effort so Claude keeps its native defaults. The adapter uses StructuredOutput with the runtime-supplied marker schema. It launches with `--dangerously-skip-permissions --permission-prompts none`. Use a disposable or version-controlled target. Inspect the requested scope before starting.

The stock graph uses direct role calls. Planner always stays direct. The only accepted helper-qualification scope is `offline_validator_fixture`. This fixture validates evidence handling. It does not qualify a live helper facility or authorize helper tasks. No live helper facility is qualified. Offline peers and fixture records are not native-route evidence. See `context/role-contracts.md`, `context/routes.md`, and `context/harness.md` for artifact, recovery, migration, and adapter boundaries.
