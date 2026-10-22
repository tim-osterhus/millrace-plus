---
name: millrace-loop-configuration
description: Design graph-v3 workflows with explicit planes, outcomes, routes and seams.
---

# Author Millrace workflow graphs

## Author a graph

Each plane declares workspace, start and local nodes. Each node declares invoke, json-v1 result,
outcomes, a route for every outcome, and explicit on_error/on_unknown actions.
Declare every script, prompt and profile in control_files. Keep command binding config empty.
Use command invoke config script/args. Native adapters use their own distinct profiles.
Define finite invocation and elapsed limits. Supply explicit timeouts or expect the 1,800-second fallback warning.
Plane/workspace/seam topology and maximum concurrency stay fixed within a run.
Local stage, branch and prompt expansion remains possible under validated revisions.

These instructions are advisory package data, not runtime-installed authority.
The graph declares routing. Executors return evidence and never mutate Core state.
Use graph format `millrace-router/v3` and fresh store-v7 state. Unsupported historical formats refuse without migration.

## Result contract

A successful `json-v1` step exits zero and emits exactly one JSON object to stdout.
The required `outcome` names a declared node outcome. Optional `summary` is a string.
Optional `refs` contains relative existing file paths under the selected plane workspace.
Write diagnostics to stderr. A useful result and confirmed stopped writer are separate facts.

```json
{"outcome":"DONE","summary":"Verified the requested change","refs":[]}
```

## Public controls

```sh
millrace-plus catalog list
millrace-plus catalog home show
millrace-plus router validate --help
millrace-plus router status --help
millrace-plus router submit --help
millrace-plus router accept --help
millrace-plus router resume-plane --help
millrace-plus router reconcile --help
```

Read status and retain exact request envelopes before recovery. Unknown writer facts block replacement.
Never claim a stopped writer merely because a client exited. Use reconciliation only with stopped-writer evidence.
Locked edits pause on changed controls. Live edits activate at invocation boundaries after raw completion persists.
OS Apply leaves a paused pending revision. Accept it explicitly and continue any graph-paused plane explicitly.
