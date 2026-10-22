---
name: millrace-entrypoint-authoring
description: Write prompts for graph nodes with explicit outcomes and evidence.
---

# Author Millrace step instructions

## Author a step

Read the node's declared outcomes, inputs and error/unknown routes before writing its prompt.
State its concrete task, output locations and acceptance checks. Supply an example for each declared outcome.
A checker reads the original request and actual work, then writes a separate report.
Keep branch decisions in the graph. Do not let executor prose invent routing or permissions.

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
