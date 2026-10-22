# Reusable local subworkflows

`millrace-plus subworkflow call --request FILE` calls one pinned local workflow
from an ordinary parent command node. It creates an independent workflow copy,
project and Core state home. The parent graph owns its branches and retries.
There is no helper service. Install the optional router dependency to execute
calls; catalog operations still work without Core.

The helper's fixed semantic outcomes are `CHILD_SUCCEEDED`, `CHILD_FAILED`,
`CHILD_CANCELLED`, `CHILD_ATTENTION`, `CHILD_REFUSED` and `CHILD_UNCERTAIN`.
They exit zero and emit exactly one Core json-v1 object with `outcome`,
`summary` and `refs`. The single relative ref points to a parent-local result
manifest. Protocol/input errors exit nonzero with a stderr JSON diagnostic.
Author `on_error` and `on_unknown` to pause, and declare every semantic outcome
the parent intends to handle.

## Prepare a pinned request

Create the parent instance first. Inspect it through public controls, then write
the request in that parent's plane workspace. The helper node must use
`invoke.cwd: "."`. Choose a fresh logical call key; retries of that same logical
occurrence reuse the key and request. A changed request under an existing key
refuses before another child is dispatched.

```json
{
  "format": "millrace-plus-subworkflow-request/v1",
  "call_key": "review_1",
  "call_home": "/srv/millrace/calls/parent_a",
  "parent": {
    "state_home": "/srv/millrace/parent_state",
    "instance_id": "0123456789abcdef0123456789abcdef"
  },
  "definition": {
    "selector": "local:review",
    "version": "sha256:REPLACE_WITH_DIGEST",
    "sha256": "REPLACE_WITH_DIGEST"
  },
  "output_ref": "results/review_1.json",
  "wait_seconds": 300,
  "cleanup_seconds": 15,
  "inputs": {"request_markdown": "Review the supplied acceptance criteria.\n"}
}
```

The placeholders must be replaced. Get the source digest using public
`millrace_plus.catalog.workflow_fingerprint(Path)` or the verified
`catalog init` result's `provenance.sha256`. A bundled definition's `version`
equals the installed Plus distribution version. A local definition uses
`sha256:<digest>` as its version. The digest includes every regular source file
and its executable bits, sorted by relative POSIX path; `__pycache__` is skipped.
Linked and special files refuse. No remote lookup or latest fallback occurs.

Requests are strict UTF-8 JSON objects of at most 64 KiB. Duplicate/unknown
contract keys, nonfinite numbers and booleans used as budgets refuse.
`call_key` matches `[A-Za-z][A-Za-z0-9_-]{0,63}`. Paths are absolute canonical
local paths, at most 4096 characters. `output_ref` is a nonempty relative path
inside the canonical command working directory, which must belong to the
publicly inspected parent project. The derived output root is part of identity.
An existing output belonging to another call refuses.

`wait_seconds` defaults to 300 (range 1–86400); `cleanup_seconds` defaults to 15
(range 5–60). The 300/15 values and a 325-second outer command timeout are a
deterministic test fixture. Native-backed callers must explicitly choose a wait
covering their selected path and an outer timeout of at least wait + cleanup +
10 seconds. Stock native nodes may use a 1800-second effective fallback; the
fixture default can cancel their work.

Optional parent `run_id`, `plane_id` and `node_id` are correlation. The helper
does not infer a parent invocation ID from an environment variable. Parent
`path`, `env_allowlist`, `host_paths`, `runtime_defaults` and `operator_policy`
are captured from public inspection with the parent state version. This v1 has
no settings override. Credential names and locators are retained; values are
not written to helper records.

## Copy and input ownership

The call owns `<call_home>/<call_key>/{workflow,project,state}` and `call.json`.
Existing non-owned call folders refuse. Parent/child equality, containment,
aliases and catalog execution roots refuse. Existing Core project holds remain
authoritative. The helper never releases a hold.

The pinned graph's declared plane workspace directories are created beneath
the helper-owned fresh project before public Core registration. Absolute
escapes, traversal and linked paths refuse. Core remains the graph/settings
admission authority. No workflow-specific bootstrap command runs in the helper.

Before create, the project receives exactly one canonical
`millrace-plus-subworkflow-inputs/v1` manifest in `subworkflow-inputs.json`, with
stable call identity and supplied `inputs`. Existing different bytes refuse.
The selected workflow's ordinary first node owns input validation/mapping.
Stock document-review and software-change map nonblank `request_markdown` to
exact UTF-8 `request.md` bytes, retaining line endings and refusing conflicts.

Two parents can select one definition while using distinct call homes, inputs,
state and copies. Editing one initialized copy does not edit its siblings or
the definition. Shared external resources still need authored serialization.

## Observe and cancel

```bash
millrace-plus subworkflow call --request /absolute/path/request.json
millrace-plus subworkflow inspect --call-home /absolute/path/calls --call-key review_1
millrace-plus subworkflow cancel --call-home /absolute/path/calls --call-key review_1 --request-id UUID_HEX
millrace-plus subworkflow reconcile --call-home /absolute/path/calls --call-key review_1
```

Use a fresh caller UUID hex for explicit cancel. Its exact intent/envelope is
retained; repeated explicit intents do not extend the first cleanup deadline.
`inspect` reads retained records only. `reconcile` refreshes public facts only.
Neither resumes, recovers, asserts a writer stopped, or edits Core state.
Use the separate public router controls for those operator decisions.

One nonblocking observer lock admits one `call` owner; duplicates receive
`call_busy` while inspect and cancel remain usable. Short separate journal locks
cover atomic/fsync updates. Create/start IDs are UUIDv5 hex under the fixed v1
namespace `d0ad2e51-b41f-5a75-886a-a17623fb5db4`, using canonical home, call key
and operation separated by `/`. Automatic deadline cancellation uses the same
identity with operation `deadline_cancel`. Re-entry retains the original UTC
deadline and first cleanup deadline.

Every public envelope is saved before sending. Potentially foreground controls
use the existing installed `millrace-plus router replay --envelope JSON`
command in a separately owned process session. Both output pipes are drained;
each call-owned control log is capped at 64 KiB. Journals are capped at 1 MiB,
result manifests at 256 KiB, summaries at 4096 characters, and refs at 32 paths
of at most 4096 characters. Overflow retains uncertainty, never success.
Public observations are reduced to status/writer/receipt facts; model transcripts
and domain output are not copied into the journal.

An applied start or controller exit is not completion. Success/failure/cancel
requires the exact saved run's Core terminal status, quiescence, stopped
effective writers and joined owned controllers. A quiescent paused child with
stopped writers returns attention only after its controller is joined. A paused
child with an unresolved writer returns uncertainty. Waiting remains under
observation until deadline.

A known cancellation before dispatch prevents start; it can return cancellation
with `run_id: null` and `dispatch: not_started`. It does not fabricate a Core
cancelled run. Unknown create can replay its exact saved request to learn its
receipt. Unknown start inspects the known instance's exact start receipt; it
never replays start merely to obtain a cancellable run.
If a saved create refusal proves that no instance or run was dispatched, a
known cancellation can also settle as `CHILD_CANCELLED` with
`dispatch: not_started`; the manifest retains the create refusal receipt.

## Interruption and retained uncertainty

SIGINT/SIGTERM requests public child cancellation and finite cleanup. SIGKILL,
forced parent stop, or an expired outer command timeout can leave the separate
child/controller alive. Core's helper kill window cannot guarantee descendant
closure. An unresolved child retains its exact IDs, cleanup obligation, envelopes
and controller PID facts as `cleanup_required`. Process exit alone never proves
writer closure. Re-entry does not guess the newest run or allocate a fresh start.

After abrupt helper death the new helper cannot physically join its predecessor's
process handles. An extant recorded PID remains uncertain, including possible
PID reuse; no saved PID is killed or reaped automatically. Once public writer
closure and absent/joined controllers are established, reconcile can record the
terminal facts. Explicit resume/recover/writer reconciliation remain operator
actions. There is no transaction spanning the call journal and Core store and
no claim of crash-transparent nested execution.

Log files are opened before spawn. A durable controller reservation precedes
each send, then records its PID. A crash between reservation and spawn, or a
storage failure after spawn, can leave
`pid: null, joined: false`: that reservation is explicitly unresolved, even if
the exact public run later finishes. Re-entry returns `CHILD_UNCERTAIN` and
does not replay an unknown start or infer controller closure. A best-effort
failure update retains the PID when storage permits; otherwise the reservation
and exact saved envelope remain the evidence for explicit operator inspection.
No automatic recovery of an unknown controller PID is supported.
An observer waits for an in-progress launch's short journal lock before treating
such a reservation as unresolved. Once a terminal result is saved, its exact
replay does not recheck old controller PIDs, which may since have been reused.

Retain journals, envelopes, receipts and outputs through unresolved/nonterminal
aftermath. Explicit local deletion is appropriate only after exact public
terminal/writer closure proof. Moving call homes, recursive reuse of the same
roots and automatic compensation/recovery are unsupported.
