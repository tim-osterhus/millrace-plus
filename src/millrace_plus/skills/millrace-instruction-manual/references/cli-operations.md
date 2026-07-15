# CLI Operations

Use this reference when operating Millrace from the command line or reporting
runtime evidence.

## Install Or Checkout Validation

Validate the command surface before operating:

```text
millrace --version
millrace --json --help
millrace workspace --help
millrace package --help
millrace plan --help
millrace queue --help
millrace run daemon --help
```

When using an editable checkout, run those same help commands through the
checkout's Python environment. If help output disagrees with this reference,
the installed help wins.

## Safe Operating Rhythm

1. Confirm Millrace is the right execution path.
2. Choose the workspace with explicit `--workspace PATH`. Use the current
   directory only when the operator or surrounding task context identifies it
   as the intended workspace.
3. Inspect package/workflow selection and admitted plan state.
4. Validate the selected workflow package and compiled plan.
5. Enqueue work through `queue enqueue`.
6. Run one bounded daemon tick for cautious progress.
7. Inspect status, queues, runs, traces, waits, interventions, and doctor
   output only through commands present in help.
8. Continue, wait, retry, resolve, or intervene through supported commands.
9. Report only what runtime evidence supports.

## Workspace And Store

```text
millrace --workspace PATH workspace init --input-id ID
millrace --workspace PATH workspace check
```

`workspace init` creates or validates the local runtime store and CAS root.
`workspace check` is read-only. Use `--db PATH` and `--cas PATH` only when the
operator deliberately separates the database or CAS locations.

## Package And Plan

Package registry commands use explicit replay-safe command IDs:

```text
millrace --workspace PATH package import-path PATH --command-id ID
millrace --workspace PATH package import-archive PATH --command-id ID
millrace --workspace PATH package import-installed DISTRIBUTION --resource-root ROOT --command-id ID
millrace --workspace PATH package update PACKAGE_ID PACKAGE_VERSION --from-path PATH --command-id ID
millrace --workspace PATH package list --command-id ID
millrace --workspace PATH package inspect PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace --workspace PATH package export-archive PACKAGE_ID PACKAGE_VERSION --output PATH --command-id ID
millrace --workspace PATH package enable PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace --workspace PATH package disable PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace --workspace PATH package remove PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace --workspace PATH package verify PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID
millrace --workspace PATH package select-workflow PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID
millrace --workspace PATH package doctor PACKAGE_ID PACKAGE_VERSION --command-id ID
```

Do not run `package update`, `package disable`, `package remove`, or
overwrite/export operations unless the operator explicitly requested package
maintenance. Normal operation should prefer package inspect, verify,
select-workflow, admit-package, enqueue, status, runs, trace, waits,
interventions, and doctor commands.

Before `package export-archive`, confirm the destination path. Do not
overwrite an existing archive unless the operator explicitly requested that
overwrite.

Plan mutations use explicit transition input IDs:

```text
millrace --workspace PATH plan admit --compiled-plan-json PATH --input-id ID
millrace --workspace PATH plan admit-package PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID --input-id ID
millrace --workspace PATH plan select-default FINGERPRINT --input-id ID
millrace --workspace PATH plan show [FINGERPRINT]
```

Package verify, package doctor, and package select-workflow do not admit a
plan. Use `plan admit` or `plan admit-package` when the workspace needs an
admitted plan for runtime work.

Use stable, descriptive command IDs and transition input IDs that reflect the
operator action, for example `import-simple-loop-20260707` or
`enqueue-work-prompt-001`. Do not encode hidden workflow meaning in IDs; they
are replay/audit identifiers, not route-selection authority.

## Queue, Dispatch, And Daemon

Enqueue requires exactly one payload source:

```text
millrace --workspace PATH queue enqueue QUEUE_FAMILY --payload-json JSON [--plan-fingerprint FINGERPRINT] [--input-id ID]
millrace --workspace PATH queue enqueue QUEUE_FAMILY --payload-file PATH [--plan-fingerprint FINGERPRINT] [--input-id ID]
millrace --workspace PATH queue enqueue QUEUE_FAMILY --payload-stdin [--plan-fingerprint FINGERPRINT] [--input-id ID]
millrace --workspace PATH queue list
```

Dispatch inspection is operator-facing:

```text
millrace --workspace PATH dispatch claim ACTIVATION_ID [--claim-id CLAIM_ID] [--input-id ID]
millrace --workspace PATH dispatch show RUN_ID
```

Runner execution is daemon execution:

```text
millrace --workspace PATH run daemon [--idle-sleep SECONDS] [--max-ticks N] [--activation-id ID] [--adapter-kind KIND] [--adapter-config-json PATH] [--monitor none|basic]
```

Use `millrace --workspace PATH run daemon --max-ticks 1` for cautious
single-step progress. `--activation-id ID` requires exactly `--max-ticks 1`.
Run an unbounded daemon only when the operator explicitly intends unattended
progression and after inspecting workspace health, selected package/workflow,
selected or default plan, and runner/adapter configuration.

Treat `--adapter-config-json PATH` as local operator config. Do not put API
keys, OAuth tokens, local credential paths, provider secrets, or adapter config
secrets in workflow packages, prompt assets, skill files, or evidence reports.
Adapter config is local operator config, not package authority.

Do not use public `millrace run once`, root `millrace tick`, manual
`millrace observe`, or `millrace dispatch invoke`. The command tree also has
no daemon status/stop subcommands.

## Status, Waits, Interventions, And Doctor

Read-only status and trace commands:

```text
millrace --workspace PATH status [--plan-fingerprint FINGERPRINT] [--max-events N]
millrace --workspace PATH runs list
millrace --workspace PATH runs show RUN_ID
millrace --workspace PATH trace show [RUN_ID] [--max-events N]
millrace --workspace PATH doctor
```

Wait commands:

```text
millrace --workspace PATH waits list
millrace --workspace PATH waits resume WAIT_ID [--payload-json JSON] [--input-id ID]
millrace --workspace PATH waits close WAIT_ID [--payload-json JSON] [--input-id ID]
millrace --workspace PATH waits revise WAIT_ID [--payload-json JSON] [--input-id ID]
```

Lineage intervention commands:

```text
millrace --workspace PATH interventions list
millrace --workspace PATH interventions resume-lineage OPTION_ID --reason TEXT [--quarantine-id ID] [--lineage-id ID] [--payload-json JSON] [--input-id ID]
millrace --workspace PATH interventions close-lineage OPTION_ID --reason TEXT [--quarantine-id ID] [--lineage-id ID] [--payload-json JSON] [--input-id ID]
millrace --workspace PATH interventions revise-lineage OPTION_ID --reason TEXT [--quarantine-id ID] [--lineage-id ID] [--payload-json JSON] [--input-id ID]
```

There is no dedicated `approvals` command group in the current CLI help. Use
only exposed status, wait, and intervention surfaces unless help adds another
operator command.

## Failure Handling

In JSON mode, success is one object on stdout:

```text
{"ok":true,"command":"...","code":"...","message":"...","data":{...}}
```

Expected failures are one object on stderr:

```text
{"ok":false,"command":"...","code":"...","message":"...","details":{...}}
```

Exit-code convention:

```text
0 success
1 internal error
2 CLI usage error
3 domain refusal
4 persistence failure
5 runner failure
```

On refusal, report the command, code, message, and relevant details. Runtime
state is outside normal public operation; if supported commands cannot resolve
the issue, stop and escalate to a documented repair procedure.

## Restart And Resume Expectations

The daemon opens and closes runtime state around bounded execution units.
Status, queue, run, trace, wait, intervention, package, and plan projections
are the evidence to inspect after restart. Active runs remain tied to admitted
selected-plan pins; `package update`, `package disable`, or `package remove`
affects later selection and compile, not already-launched work.

## Evidence Report

Use this compact format:

```text
workspace: <workspace>
selected_workflow: <package>/<workflow>/<entrypoint or default>
action_taken: <command or operator action>
evidence:
- status: <observed state>
- queue: <ready/active/blocked/closed counts or relevant item>
- runs: <run ids and state>
- trace: <notable transition or absence>
- doctor: <healthy|issues>
- waits_or_interventions: <open/resolved/refused evidence>
next: <run|wait|resume|retry|repair|close|direct work>
confidence: <what evidence supports this>
```

If a value is not known, say so. Do not infer hidden progress.

## Direct-State Boundary

Runtime-owned workspace state is not an editing surface. Use direct state-file
edits only under a documented repair procedure after supported CLI/operator
commands have been considered and the operator explicitly authorizes repair.
