# CI/CD stock workflow (first slice)

`bundled:cicd` handles local CI completion events for one Git working tree. It
diagnoses a failure, may make one small repair, runs the operator's checks, gets
one independent review and then holds the checked patch for a human. This first
slice is hold-only: it never commits, pushes, opens a pull request, observes
remote checks or deploys. It does not replace the CI system that produced the
event, and it does not hand work to LAD.

This document describes the implemented first slice. It is command-only: the
`diagnose`, `repair` and `review` nodes run a configured command executable.
Native harness binding and profile integration for those nodes is not
implemented yet; it is required future source work for the full CI/CD feature,
separate from any later native or provider qualification. External publication
(`authorized` mode, `approve-publish`, effect adapters) is also not part of it.

## Setup

Write a configuration file (`millrace-plus-cicd-config/v1`):

```json
{
  "format": "millrace-plus-cicd-config/v1",
  "target_root": "/abs/repo",
  "repository": "owner/name",
  "repair_paths": ["src"],
  "protected_paths": [],
  "check_commands": [
    {
      "id": "unit",
      "argv": ["python3", "-m", "pytest", "-q", "tests/unit"],
      "cwd": ".",
      "timeout_seconds": 600,
      "expected_exit_code": 0
    }
  ],
  "repair_binding": {"adapter": "command", "executable": "/abs/role-runner"},
  "external_policy": {"mode": "hold", "effects": []}
}
```

- `target_root` must be the canonical root of a Git working tree.
- `repair_paths` are target-relative prefixes. Repairs stay inside them.
- `check_commands` are argv arrays, never shell strings. Each has a finite
  timeout (1 to 3,600 seconds); the batch total is at most 3,600 seconds. Checks
  run repository code, so select them deliberately.
- `repair_binding.executable` runs the `diagnose`, `repair` and `review` role
  nodes as a command adapter, receiving the role name as its only argument.
- Any other `external_policy` refuses. `authorized` refuses with
  `external_mode_unsupported`.

Initialize, then start the run with the printed command:

```sh
millrace-plus cicd init --workflow-home /abs/ci-home --target /abs/repo \
  --config config.json
millrace-plus router --state-home /abs/ci-home/state start --instance INSTANCE_ID
```

The Core store defaults to `<workflow-home>/state`, owned by that home; no
global store, configuration or account setup is involved. `--state-home ABS`
selects another store. It is frozen at the first `init`, and a later `init`
without it reuses the frozen store.

`init` validates everything first. It then creates the home, writes the exact
config bytes, claims the target, creates the intake database, copies the graph
and registers the Core instance with host aliases `cicd_target` and
`cicd_agent`. It starts no run, role or check. Repeating `init` with the same
arguments replays; different config bytes refuse with `initialization_conflict`.

## Events

Events are strict UTF-8 JSON files of at most 65,536 bytes
(`millrace-plus-cicd-event/v1`) with exactly `format`, `source`
(`github-actions` or `generic-ci`), `repository`, `run_id`, `attempt`, `job_id`,
`head_sha`, `conclusion`, `summary`, `log` and `occurred_at`. `log` is null or
`{path, sha256}` relative to the event file's directory, at most 131,072 bytes.

```sh
millrace-plus cicd submit --workflow-home /abs/ci-home --event event.json
millrace-plus cicd status --workflow-home /abs/ci-home [--event EVENT_ID]
```

The event ID is the SHA-256 of `source`, `repository`, `run_id`, `attempt`,
`job_id` and `head_sha`. Submitting identical bytes again returns the original
receipt (`disposition: replayed`). Different bytes or log under the same
identity refuse; a new CI attempt is a new event. Accepted events run in local
acceptance order, one at a time. `submit` and `status` do not need Core.

Summaries and logs are untrusted evidence. Their text never adds commands,
paths, checks or permissions. Redact secrets before submission.

## What the graph does

| Step | Effect |
| --- | --- |
| `reconcile` | Maps the active event's saved facts to one fixed next node, or HOLD. |
| `claim` / `wait_event` | Claims the next queued event, or waits up to 300 seconds polling the local intake. No role runs while idle. |
| `freeze` | `success`, `cancelled` and `skipped` settle as `no_action`. Otherwise it checks `HEAD == head_sha`, snapshots the source, copies the before bytes of repairable files and reserves the single diagnosis. |
| `diagnose` | Role: `SMALL_REPAIR`, `LARGE_ISSUE`, `UNREPRODUCED`, `EXTERNAL_BLOCKER` or `NO_ACTION`. |
| `guard_diagnosis` | Before accepting any classification, checks that HEAD and the source still match the freeze; otherwise it holds `head_changed` or `source_drift` as `uncertain`. Then validates the artifact and Core completion. A small repair must name at most 3 repairable paths. Reserves the single repair. |
| `repair` | Role: edits the target. |
| `guard_patch` | Diffs the actual source against the frozen before-state and writes `patch.diff`. Accepts it only at the frozen HEAD. |
| `checks` | Checks HEAD and the patched source, runs every configured check once, writes receipts, then checks HEAD and source again. |
| `review` | Role: independent review of the patch and check batch. |
| `guard_publication` | Validates the review, patch, batch, current source and frozen HEAD. In hold mode a valid result always holds as `checked_patch`. |
| `hold` | Writes `report.json` and `report.md`, marks the event held and pauses. |
| `settle` | Under the owner lock, rechecks every accepted artifact, patch, check receipt, log and evidence digest and the published report, writes the final report, then releases the active slot. |

A small repair changes at most 3 regular UTF-8 text files and 200 added plus
deleted lines, all inside both `repair_paths` and the diagnosis's proposed
paths. These are fixed first-slice limits. Operator reduction of the thresholds
and an explicitly selected CI-definition exception are full-spec requirements
that this slice does not implement; they are not waived. Deletes, renames, binary edits, mode changes, symlinks and protected
paths hold. Always protected: `.git`, the owner marker, dependency manifests
and lockfiles, migrations, credential-like files and CI definitions
(`.github`, `.gitlab-ci.yml`, `.circleci`, `Jenkinsfile`).

Edits that existed before the claim are part of the frozen before-state. They
stay on disk and never appear in `patch.diff`. A change outside the proposed
paths during repair holds the event.

Checks run inside the `checks` step's Core-owned process group; no new session
or group is created. Core cancellation or timeout of that step therefore stops
the check and anything it started, and Core's writer fact for the step covers
them. A check receipt passes only with the expected exit code of its reaped
root, end of file on both output streams, output under 65,536 bytes per stream
and an unchanged source digest afterward. A root that exits while a descendant
still holds its output is `uncertain`. On a local timeout or overflow only the
check root is stopped; a descendant that keeps the output open makes the receipt
`uncertain`. Root exit alone never claims that every descendant closed.
`__pycache__`, `.pytest_cache`, `.mypy_cache` and `.ruff_cache` are not source.
Checks receive only `PATH` and `HOME`.

## Records

Per event, under `<home>/project/main/events/<event_id>/`: `event.json`,
`log.txt`, `state.json`, `before.json`, `before/`, role directories
(`diagnose/`, `repair/`, `review-N/`) holding `input.json`, the pinned
`prompt.md` and `skill.md`, and `result.json`, plus `checks/N/`, `decisions/`
(operator evidence and published decision records), `patch.diff`,
`report.json` and `report.md`.

Report statuses in this slice are `no_action`, `held_large`,
`held_unreproduced`, `held_external`, `held_repair`, `checked_patch`,
`cancelled` and `uncertain`. `checked_patch` means a local patch passed the
selected checks and one review. It does not mean anything was merged, pushed,
checked remotely or deployed.

## Decisions and recovery

```sh
millrace-plus cicd decide --workflow-home H --event E --decision close \
  --request-id UUID_HEX --evidence note.txt
millrace-plus cicd decide --workflow-home H --event E --decision retry-checks \
  --request-id UUID_HEX --evidence note.txt
millrace-plus cicd cancel --workflow-home H --event E --request-id UUID_HEX
```

`close` and `retry-checks` apply only to a held event and need nonblank
evidence. The same request ID with the same intent replays its receipt; a
changed intent refuses.

Each accepted decision is first recorded in the local intake store (intent and
receipt), then published as `events/<id>/decisions/<request_id>.json`. The
graph reads the event's accepted decisions from the intake store and checks the
published records in both directions:
- a record with no accepted request, or one whose bytes differ from the
  accepted request, refuses and pauses at `reconcile`;
- an accepted request whose record is missing, whether publication was
  interrupted or the file was later lost, also blocks. No new role, review,
  settlement or other decision starts. A `reconcile` holds the event as
  `uncertain` with reason `decision_projection_missing`.

Reading never creates or replays a decision. To recover, rerun the same command
with the same request ID (and the same evidence file for `close` or
`retry-checks`). It returns the original receipt and republishes the identical
record. A different evidence file for that request ID refuses.

A terminal result is recomputed from its origin every time, never taken from a
saved field. The origins are an accepted `cancel`, an accepted `close` of the
held report, or `no_action` from a `success`/`cancelled`/`skipped` event or an
accepted `NO_ACTION` diagnosis completion. A saved settlement that disagrees
refuses before any report is rewritten or the slot is released. The frozen
HEAD and configuration digests in the event record must equal the verified
event and frozen configuration.

After a decision, resume the paused plane with the public Core control
(`millrace-plus router ... resume-plane --plane main`).

- `close` settles the exact held report and lets the next event run. The patch
  stays on disk. Before release, settlement rechecks the bytes of every accepted
  deliverable and the held report; a changed or missing one refuses, keeping the
  event active and the next event queued. `close` records the report as of its
  frozen revision. It does not re-certify the target's current HEAD.
- `retry-checks` runs a new check batch only when HEAD and the source still
  match the frozen revision and scoped patch. It earns one new review. It never
  re-enters diagnosis or repair.
- `cancel` on a queued event is immediate. On an active event it is a
  stage-boundary request: it sends no Core control and stops nothing that is
  already running. The current role or check finishes, no further role is
  reserved, and the next step boundary refuses and pauses at `reconcile`. After
  resume, the event settles as `cancelled` once all writers stopped. To stop
  live work now, use the public Core controls (`millrace-plus router ...
  cancel` or `pause`) as well.
- `approve-publish` refuses with `publication_not_supported`.

Each event gets one diagnosis and one repair attempt. The reservation is saved
before the edge to the role exists, so a crash, even before launch, spends it.
`reconcile` has no edge to `diagnose` or `repair`. A completed role result
re-enters only its deterministic guard, and only as the exact next Core
attempt after the reserving step with a stopped writer. Anything else holds.
Every error or unknown outcome pauses and resumes at `reconcile`. A live or
unknown writer holds the event.

## Limits

- Ownership uses the `.millrace-plus-target-owner.json` marker and lock shared
  with persistent LAD. It excludes LAD and other CI/CD homes only on the same
  exact target root, not ancestors or descendants. The marker is kept for the
  life of the home; this slice has no release or transfer command. A missing
  marker holds instead of being recreated.
- Snapshots cover at most 20,000 entries and 512 MiB; before copies at most
  4,000 files and 64 MiB. Larger targets hold with `source_too_large`.
- Role nodes are command-only. Native binding and profile integration is not
  implemented (required future source work). GitHub, pull-request and
  deployment routes and authorized publication (C3) are not implemented.
- The idle wait polls durable intake and public Core control state once per
  second for up to 300 seconds. It wakes for queued controls and already applied
  run gates or a paused main plane. A normal `WAKE` result is saved while Core
  keeps routing paused; submitting an event does not start work until the
  operator resumes through the corresponding public run or plane control.

## Explicit v2 homes (CI-R1)

V2 uses the separately bundled `cicd-v2` graph and captured v2 config. Existing
v1 homes keep their original schemas, guidance and recovery rules; init refuses
a version conflict and performs no migration.

Prepare bootstrap metadata first:

```text
millrace-plus cicd prepare --workflow-home /owned/ci-home --target /owned/repo --config /owned/config.json
millrace-plus cicd init --workflow-home /owned/ci-home --target /owned/repo --config /owned/config.json --authority /owned/selected-authority.json
```

Prepare starts no Core instance/provider and changes no source or Git index.
It claims the target for the home's lifetime, captures assets and the derived
graph, and emits exact owner/definition refs with an UNAPPROVED authority
suggestion. Select an exact operator authority file explicitly at init; the
suggestion is never selected automatically. Init captures its hash and binds
only the actual create receipt. Replay requires unchanged ownership, settings,
config, graph and assets. Close never transfers or removes the lifetime claim.

`submit --event` retains the exact strict v1 CI event and identity, wrapping it
in typed v2 input with the shared intake's acceptance order. `status` reads;
missing or corrupt state/evidence refuses and does not repair records. `cancel`
records a stage-boundary cancellation, while public Core controls remain the
way to stop live processes. `decide --event ID --request-id ID --decision KIND
--evidence FILE` consumes the exact v2 decision JSON matching those arguments.
Continue requires an actionable changed cause, the exact held report digest and
state version; wording changes do not permit another identical failure attempt.
Close binds the immutable held report. Delivery/rollback approval refuses until
separate CI-D1/D2 selection.

Reproduction runs trusted captured argv before repair. Investigation and review
are read-only; only configured probe IDs execute. Actual changes, protected
paths/classes and the assessment constrain repair. Every configured mandatory,
matching regression and reproduction command runs after the patch; uncertain
selection uses the full configured set. Every ordered reviewer must accept
current evidence with zero unresolved findings. Command-peer checks prove source
behavior; they do not qualify native backends or effective model effort.

Command deadlines resolve through existing Core defaults, excluding the derived
check node from their own calculation. The reproduce/checks node budgets are
static sums plus operator-captured bookkeeping. Idle wait defaults to a one-second
responsiveness poll, reserving the smaller of one poll and half its effective
invocation timeout for result emission. Both values remain editable through
`node_deadlines.wait_event`; incompatible explicit settings refuse before create.
A spent wait window holds rather than producing a zero-sleep claim/wait cycle.
There is no default overall-run, file, line, attempt or reviewer-count cap.

`cicd_candidates` provides explicit local candidate operations. Each request pins
its source/config/target, authority, actor and materialization version. For the
local CI-R1 operations, authority/approval refs pin the explicitly selected
bootstrap authority; no extra per-run approval is introduced. A command
performs one operation; its following declared guard re-observes the exact public
Core completion before accepting ownership or sealing a candidate. An active
command cannot certify its own closure. A single durable lease spans base,
repair/import, checks and exact reverse restoration. Unknown writers or foreign
third values retain that lease and refuse another target claim. Environment
paths are opaque provenance; they are never exported or restored. Declared check
products require proven absence or earlier owned layers and reverse in order.

The command form is `cicd candidate --workflow-home H --operation OP --request
FILE --result FILE --execution-home E --node NODE --plane main --script SCRIPT`.
It is called from the actual captured command, whose PID/argv/cwd and controls
must bind to public Core observations; manual unbound execution refuses. A Python
command wrapper may call `cicd_cli.main` in its own process, retain the result
JSON/file, then emit its graph-declared json-v1 envelope. Spawning a separate CLI
child does not inherit its parent's invocation identity.
Factory subject projection remains `factory_subject_validator_unavailable` until
F1 supplies its accepted pure validator. Factory source mapping likewise holds
with `factory_source_validator_unavailable`; captured known-binding refs or a
trusted-preparer command ID are not executed authority. Later local F1 validation
may accept only evidence its selected contract supplies. Publication/external
preparer mappings remain unavailable until their actual selected producer exists.
Full standalone CI subjects do not
require factory/LAD/OS services. External delivery, health, rollback, native
qualification, history and publication are outside CI-R1.

V2 reads the unchanged external CI event shape with its captured `input` and
`input.log` byte guards. It does not inherit the v1 64 KiB event, 128 KiB log or
prose-length ceilings. These guard surfaces have no replacement numeric default;
when selected, their existing reason/origin/override remains the provenance.
Supported resource surfaces are `input`, `input.log`, `command.stdout`,
`command.stderr`, `snapshot.bytes` (bytes) and `snapshot.entries` (entries).
Unsupported or dimensionally mismatched guards refuse before preparation.
Original event bytes and the referenced log remain together in a contained
per-input directory, preserving the original record's relative Ref.

A leased CI repair export retains the predecessor's distinct development
`source_target` lineage. Its physical producer is the exact CI
`verification_target`, independently bound by the before manifest and applied
repair operation/public completion. The lease alone never selects this branch:
ordinary development-source export still reads `source_target`, including when a
CI validation base has already been prepared. Missing/stale/foreign producer,
subject, owner or materialization evidence holds; export never fakes a re-import.

A stopped investigation, repair or review with missing/invalid output enters a
hold retaining its actual public completion. Close/cancel can settle that hold
without redispatch. A reserved or unknown writer may also receive a hold report;
that report supplies no completion or closure and cannot authorize settlement.
Completed checks retain their attempt-owned raw evidence before acquiring shared
acceptance locks; following guards still prove closure and accept state.

Standalone product settlement checkpoints each reversed layer, newest first,
and pins the original report, decision and intake CAS basis. Replay accepts exact
before/after partial progress only for the current layer and validates the whole
cumulative boundary before proceeding. Explicit claim/reconcile writers finish
an exact terminal receipt's saved projection; status reads never heal it. A
leftover write temporary or corrupt/partly published immutable record remains
held. This covers logical operation interruptions, without a power-loss or
all-syscall recovery guarantee.

For facade projections, exact terminal/quiescent parent history can prove a
reserved native role never started. That proof retains the original reservation
and does not count as successful repair/review. Explicit restoration may skip
such a repair only while its exact before source/products remain unchanged.
Other interrupted facade operations, including non-import restoration, remain
held for operator disposition; there is no generic automatic rollback/retry.

Terminal close/cancel binds the historical report. Cleanup records the current
source, HEAD and index, and requires them to stay unchanged through reversal and
terminal CAS. It does not requalify the current revision against the old report.
A formerly owned product path now tracked by HEAD/index is preserved and holds
`owned_product_now_tracked`. The original report remains unchanged.

Once settlement has an immutable plan, that plan's original report, decision and
request own replay. A later accepted cancel can be superseded by that settlement.
Status exposes its `superseded_by_settlement` disposition, exact original decision,
winning plan and same terminal receipt; the cancel is not marked applied or called
successful. Only explicit settlement/reconcile writers publish that metadata.

A pre-command lock refusal records the completed prefix and an exact not-started
remainder bound to the captured wrapper, preparation, config and command order.
The following guard accounts it after public closure; an unknown prefix stays
held. No missing PID/file alone proves a command never started. No wait or retry
is added. Standalone reserved/unknown-role cancellation still remains held unless
its already-selected positive proof applies; no generic abandonment is provided.

The facade's never-started proof covers its exact preparer run. A later Factory
consumer must invalidate or guard the active-role pointer and inspect later runs
before reuse. Historical settlement-plan scanning remains home-wide: old evidence
corruption can block new claims. No history index or long-run qualification is
provided here.

## Typed feedback in v2 homes

After the ordinary explicit prepare→init bootstrap, submit local feedback without
another per-report approval:

```sh
millrace-plus cicd submit-report --workflow-home /owned/ci --report report.json
millrace-plus cicd correlate --workflow-home /owned/ci --correlation correlation.json
```

`submit-report` accepts the exact `millrace-plus-cicd-feedback/v1` schema bundled
as `cicd-v2/schemas/feedback.json`. Bug reports and production incidents retain
their own identity, original bytes, origin, affected version/environment, observed
impact, descriptive reproduction steps and hash-pinned evidence. All refs inside
the submitted record are contained regular files relative to that file's parent.
They are retained beside its original bytes; input evidence refs address those
copies from the workflow workspace. Links, hardlinks, escapes, missing files and
changed hashes refuse. Configure `input.evidence` as an optional captured byte
guard for each referenced file when the operator's resource policy needs it;
`input` bounds the submitted record. No numeric default is added.

Feedback requires an explicit redaction attestation, preserving an honest false
value and its notes; that attestation does not
prove the absence of secrets. Do not submit credentials or environment dumps.
The identity is the canonical hash of kind, producer, report_id and repository.
Exact submitted/evidence bytes replay the original receipt. Changed bytes under
that identity conflict. A correction uses a new report_id and retains the original
report through origin_refs. Near-duplicates remain separate FIFO inputs.

Standalone freeze requires an actual affected head matching local HEAD. A stale
head or a release/artifact-only report remains held for source investigation;
there is no automatic checkout or fabricated CI head/log. Matching reports use
the existing reproduce→investigate→repair→checks→review route. Reproduction
steps, impact and severity hints do not supply executable authority. All selected
checks and unresolved findings remain obligations. The stock external-effect
hold remains; a checked patch is not published delivery.

`correlate` retains one exact append-only advisory record using the bundled
`correlation.json` schema. Each input ID must already exist. A changed record
under the same correlation_id conflicts; record a new ID for a later disposition.
Suggested, human_confirmed and rejected dispositions all remain advisory. Status
returns their refs separately from immutable historical reports, so a later
hypothesis cannot rewrite a prior terminal receipt. Correlation never merges,
archives, skips or reprioritizes admitted work.

A held refinement requires `decide --decision continue` with the existing strict
v2 decision record, the held basis digest, an allowed target stage and actionable
changed-cause evidence. Wording changes alone refuse. Existing writer closure,
product restoration/accounting, fresh attempt links, review and scope checks stay
in force. Status and reload validate evidence; reads never reconstruct missing
input projections. CI-F1 adds no LAD handoff, Factory composition, publication,
health observation or rollback.
