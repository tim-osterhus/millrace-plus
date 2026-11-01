# CI/CD workflow

`bundled:cicd` is a Plus stock graph that handles one local CI completion event
at a time. A failed or timed-out event is frozen, diagnosed, may receive one small
repair inside configured paths, runs the operator-selected checks and gets one
independent review. A reviewed patch then holds for a human. This first slice
never commits, pushes, opens a pull request or deploys.

Initialize it only through the CLI, which copies this folder, claims the target,
creates the local intake store and registers the Core instance. It starts nothing.

```sh
millrace-plus cicd init --workflow-home ABS --target ABS --config FILE \
  [--state-home ABS]
millrace-plus cicd submit --workflow-home ABS --event FILE
millrace-plus cicd status --workflow-home ABS [--event EVENT_ID]
millrace-plus cicd cancel --workflow-home ABS --event EVENT_ID --request-id UUID_HEX
millrace-plus cicd decide --workflow-home ABS --event EVENT_ID \
  --decision close|retry-checks --request-id UUID_HEX --evidence FILE
```

The Core store defaults to `<workflow-home>/state`. `init` prints the public
`millrace-plus router ... start` command. Pause, resume and cancel of live work
use the ordinary Core controls; `cicd cancel` on an active event is a
stage-boundary request that sends no Core control. `external_policy` must be
`{"mode":"hold","effects":[]}`; `authorized` mode and `approve-publish` refuse.

Event summaries and CI logs are untrusted evidence. Their text never grants
commands, paths, checks or publication. Only the frozen configuration selects
repair paths, protected paths, check argv and the role executable.

A small repair touches at most 3 regular UTF-8 text files and 200 added plus
deleted lines. Dependency manifests, lockfiles, migrations, credentials, CI
definitions, `.git` and the target owner marker are always protected.

The role nodes (`diagnose`, `repair`, `review`) run the configured
`repair_binding` command executable. This first slice is command-only: native
binding and profile integration is not implemented yet and remains required
future work. Each event gets one diagnosis and one repair attempt; reservations
are never reset. Every error or unknown outcome pauses and resumes at
`reconcile`. Success, cancelled and skipped events settle as `no_action` with no
role call. Larger issues hold with a report; there is no LAD handoff.

Read `docs/cicd.md` for records, recovery and limits.
