# Persistent LAD local intake

The P-L1/P-L2 foundation supplies durable local intake, cooperative target
ownership, finite child dispatch through `Service.advance_task`, OS-backed v2
observations and shared-target worker validation. The finite internal operation
advances one already claimed task using its exact validated request bytes and
claim; it does not claim or settle tasks or loop over the queue. Persistent
service initialization, activation and the `bundled:lad-service` graph remain
pending P-L3 lifecycle work. Do not create service records or databases manually.

The local commands operate on an existing valid service configuration and intake
database. They do not import Core, launch providers, start children, or resume a
paused parent.

```text
millrace-plus lad submit --service-home ABS --key KEY --request ABS_FILE
millrace-plus lad status --service-home ABS [--task TASK_ID]
millrace-plus lad cancel --service-home ABS --task TASK_ID --request-id UUID_HEX
```

Each command prints one JSON result on success. Refusals print a JSON diagnostic
to stderr and return exit code 2. `cancel` currently supports queued tasks only.
Active cancellation and `decide` require the later lifecycle implementation.
Configured OS home names retain the existing OS syntax:
`[A-Za-z0-9][A-Za-z0-9._-]{0,95}`. This does not change intake keys or UUIDs.
No public initialization or activation command is available in this foundation.

## Immutable requests and FIFO storage

A request contains 1–65,536 original UTF-8 bytes with nonblank text. Intake keeps
those exact bytes, including line endings. Keys match
`[A-Za-z][A-Za-z0-9_-]{0,63}`. The service UUID and `lad-task/KEY` derive a stable
UUIDv5 task identity.

SQLite commits a new task and its submission receipt in one transaction. Reusing
a key with the same bytes returns the original identity, sequence and acceptance
time with disposition `replayed`. Changed bytes refuse. New work needs a new key.
There is no task-count limit.

An internal finite operation claims the smallest queued sequence. The claim
commits before local task files are materialized. A unique database constraint
allows one active task across `claimed`, `running` and `held`. Competing callers
cannot skip that active task. The finite P-L2 wrapper can advance an existing
claim; active-task settlement remains pending service lifecycle work.

Queued cancellation commits its explicit request identity, exact intent,
cancellation receipt and task state together. It records `cancelled-before-start`
and creates no child identity. Reload validates the saved cancellation against
its exact task, intent and receipt. A changed terminal status or attached claim
refuses and preserves the database. Reusing that request identity with changed
intent refuses. Internal decision storage is available for later lifecycle policy.
It grants no public continuation or closure operation today.

Read-only status preserves database bytes. It represents stored request bytes as
UTF-8 text in JSON. Corruption, changed configuration fingerprints, linked files
and disk failures refuse. Initialization exclusively creates a new database.
It never replaces a retained database or repairs one over existing work.

## Cooperative target ownership

The immutable target owner marker is
`TARGET/.millrace-plus-target-owner.json`. Its record pins workflow kind, service
UUID, canonical service home and that home's filesystem identity. LAD and future
CI/CD clients use the same marker contract. Existing dirty and non-Git files
remain in place.

Initialization creates the marker exclusively. An existing marker must match
exactly. Targets must have their recorded canonical path, device and inode.
Aliases, symlinks, linked markers, conflicting owners and overlapping protected
roots refuse. Call, catalog and state roots must be supplied as protected roots
by the later initialization path. This packet creates no ownership transfer or
release operation.

A short nonblocking file lock serializes cooperating claim and disposition
operations. The lock and marker do not prove external writers have stopped.
The internal claim operation requires a caller-supplied writer guard. Unknown
writers must refuse before claim. Fixture guards in storage tests establish no
public Core/OS or native closure claim.

Local request and claim files use exclusive creation and exact-byte replay.
A conflicting or incomplete file stays preserved and blocks materialization.
A completed file survives a later interrupted write and can be replayed.
These files do not initialize a LAD child or authorize shared-target access.
