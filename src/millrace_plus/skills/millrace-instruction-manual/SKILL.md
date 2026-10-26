---
name: millrace-instruction-manual
description: Inspect graph-v4 instances through public catalog and router commands.
---

# Operate Millrace workflows

## Operate deliberately

Install Plus base for catalogs. Install its router extra for public Core controls and native adapters.
Initialize an independent ordinary workflow copy. Keep templates, catalog roots and project work areas separate.
Choose exact instance/run/plane targets. Retain request IDs and expected versions for immutable replay.
A foreground mutator can own work until pause or completion. The first stop drains; a second requests cancellation.
Inspect durable receipts separately from HTTP observations. Never change a saved body to retry stale intent.

These instructions are advisory package data, not runtime-installed authority.
The graph declares routing. Executors return evidence and never mutate Core state.
For new authored graphs, use `millrace-router/v4`.
A new v4 instance uses store-v8.
The required `limits` object may omit `max_invocations` and `max_elapsed_seconds` independently.
An omitted key means no overall bound. Explicit null or zero is invalid.
Keep every invocation timeout finite.
The 1,800-second fallback warning applies only when no effective invocation timeout is supplied.
Bundled stock workflows and existing graph-v3/store-v7 records retain their finite limit contract.
Do not relabel or convert stock files and runs. The catalog refuses unsupported formats without migration.

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
