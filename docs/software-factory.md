# Local Software Factory

`bundled:software-factory` composes the existing CI candidate facade and one
dedicated persistent LAD lane. It retains local frozen candidates, runs captured
checks and independent review, restores the validation tree, and advances an
accepted logical source base after development synchronization. Core executes
the graph. The helpers each perform one named operation.

F1 is an unreleased local implementation. Exact review, acceptance and test
receipts live under `lab/tasks/v0232-ox-engineering-delivery/2026-10-30/F1/`.
Ordinary checks use inert role peers; they establish no native-provider,
production, retained-history, macOS or release qualification.

## Provisioned resources

Provide two separate Git working trees at the same available anchor commit.
The LAD Service owns the development root for its lifetime. A separate CI-v2
home owns the validation root for its lifetime. The Factory home owns its intake
and records. Roots must not overlap each other, homes, catalogs or Core stores.
Factory never transfers or deletes either target's owner marker.

The development tree may contain original unrelated dirty or staged work.
Factory records those paths and preserves their exact bytes and index. A job
that overlaps them holds `candidate_overlap_with_prior_work`. Validation begins
at the clean anchor under the selected shared path policy.

Configure the dedicated LAD worker once. Park its parent with an exact public
pause, wait for actual start/control holder joins, and retain normal Service
records. Do not await the persistent start holder before requesting pause: that
holder returns when its executing run reaches the pause. A paused status alone
proves neither joins nor task settlement. Foreign tasks, changed pins and
unresolved writers hold the lane.

At held and parked boundaries, Factory asks the owning Service to retain a genuine
OS-owned join observation for that exact original start through its existing
GET/CAS path. Earlier null/false observations remain recorded. Later OS receipt-
only fallback cannot erase the retained exact positive proof or provide a new
one. Unknown proof remains held; no start is resubmitted and no observation is
borrowed for another request, run or owner.

## Bootstrap once

Use the existing `cicd prepare` operation on the validation home with
`source_policy.mode: factory`. It retains the real Factory subject schema as a
pinned control asset. Preparation owns metadata and its lifetime claim, starts
no Core instance or writer, and supplies an UNAPPROVED authority suggestion.

Write a `millrace-plus-factory-config/v1` configuration matching the selected
specification. It pins both lane identities, the anchor, scope/risk policy,
shared path policy, CI check policy, optional engineering profile/snapshot and
exact L-Q1 command mapping, role selections, local operations and their actual
timeout/resource provenance. The role profile file contains the exact captured
CI `role_bindings` object. The CI policy file contains its exact `check_policy`.

First invoke:

```text
millrace-plus factory init --factory-home /owned/factory --config /owned/config.json
```

While CI is only prepared, this derives the Factory actor's immutable graph,
assets and settings using the existing CI preparation record format. It returns
UNAPPROVED bootstrap metadata without registering the Factory or admitting work.
The suggestion includes the exact Factory home and definition Ref. This is the
same `init` command, not a new preparation service or per-job approval.

The operator reviews and explicitly selects the shared local authority, including
both actors and the selected Factory local operations. Pass that file to normal
`cicd init --authority FILE`. Update the Factory configuration's validation
`config_ref` and shared `authority_ref` to the actual final CI records, then run
`factory init` again. Those authority-dependent refs do not enter graph derivation.
Factory validates the selected bytes, both owners and the prepared artifacts,
then binds its saved Core create request/receipt once. Replay preserves the
same instance, configuration, intake and lifetime claims. Drift refuses.

Initialization starts no role or OS service. Use existing Core/OS start and
pause/resume/cancel controls for the returned Factory instance. The stock graph
has one active job per lane pair and no aggregate elapsed/invocation limit.

## Input and ordinary progress

Submit a strict `millrace-plus-factory-input/v1` file:

```json
{
  "format": "millrace-plus-factory-input/v1",
  "source_kind": "task",
  "source_id": "addition-task",
  "repository": "example/calc",
  "base_candidate_sha256": null,
  "request": "Implement addition.",
  "criteria": [{"id": "sum", "text": "2+3 returns 5", "origin_ref": null}],
  "feedback_ref": null,
  "evidence_refs": [],
  "scope_ref": null,
  "occurred_at": "2026-10-31T00:00:00Z"
}
```

```text
millrace-plus factory submit --factory-home /owned/factory --input /owned/task.json
millrace-plus factory status --factory-home /owned/factory
```

The same original source identity and exact intent replay the original receipt.
Changed intent under that identity refuses. FIFO follows accepted submission
sequence. A task's null base pins the accepted logical base when claimed.
Input text grants no command, write, criterion-change or external-effect authority.

A deterministic scope/risk guard selects CI repair, the complete existing LAD
worker, read-only investigation or hold. Checks and review use the same frozen
candidate for both implementation routes. No synthetic CI event or replacement
LAD claim is created. Submission and exact parent resume are separate journaled
operations. After a possible send, parent-control replay observes the original
ID only. Healthy observations continue automatically without per-poll decisions.

CI-event, user-report and production-observation inputs retain their exact typed
originals and evidence. CI `source_id` is the existing `event_identity` digest;
report/observation `source_id` is the digest of CI-F1's exact `feedback_identity`.
The source kind must match the typed report kind. Nested producer evidence keeps
its original relative paths and exact hashes. Reported external revisions,
release names and artifact hashes remain unavailable unless an
actual selected producer establishes the source mapping. Known ref shapes or a
configured preparer command name do not establish publication/preparation.
Factory's local source producer does not activate CI-D1/D2 or an external base
preparer. Standalone CI retains its own actual-HEAD rule.

## Source and check accounting

Git HEAD remains the configured anchor B. Successful jobs advance a separate
logical accepted source tree. Reports name the immutable checked bundle, while
the live validation tree is restored. Factory never invents a Git commit for an
unpublished local candidate or claims that HEAD equals a reported published C.

A single versioned CI lease covers accepted-base preparation, native repair or
LAD import, check products, sealing and exact reverse restoration. Following
guards accept actual public stopped producing completions. Failed checks remain
failed even when their owned product layer is safely accepted for restoration.
An explicit close can use `CLOSE_READY` only after that acceptance guard.
Unknown writers, corrupt proof, foreign drift or unaccounted output remain held.

The shared path policy distinguishes source, opaque environment paths and exact
owned deterministic check products. Factory does not install dependencies,
export opaque environments, adopt foreign products or perform broad cleanup.
Native tests/builds keep their existing role authority; their shared output
paths must be captured as environment paths rather than deterministic products.

Outer engineering obligations retain original criterion IDs and exact accepted
input/outcome refs. They do not mutate the LAD service's fixed inner snapshot or
write standalone L-Q1 history. Every selectable L-Q1 check maps to an identical
resolved CI execution before dispatch. The authored config directory supplies
the selected snapshot's explicit, hash-pinned provenance files at their recorded
contained paths. Historical record bodies remain data and grant no writer or
source-mapping authority. Missing mapping or unavailable required
evidence cannot pass. Portable check evidence retains the raw CI receipt and
accepted product/completion chain. A retained adapter record pins the command
map and identifies its environment digest as the canonical digest of CI's
observed environment-provenance hashes; it never exposes environment values.
Previously accepted Factory command coverage remains in the current union even
when the new changed paths would select fewer checks. Metrics remain advisory.

Named per-job files are views of retained owner records. Refs resolve from the
owning workspace; native output Refs keep their captured input-parent semantics.
The held outcome has a null terminal receipt and advances no accepted base.

## Holds and cancellation

A held Factory does not launch another implementation under a new key.
`retry-evidence` permits a read-only assessment only with changed cause/evidence.
`retry-verification` keeps the same candidate and retains failed evidence.
`continue-lad` forwards an explicitly supplied supported LAD decision through its
original attention/run/plane guards. Factory freezes its immutable decision plan
before forwarding; an interrupted call replays the exact original owning request.
`close` retains an unsuccessful outcome;
`acknowledge-cancelled` requires an actual saved cancellation intent.
`approve-delivery` refuses until separately selected F2 support exists.

```text
millrace-plus factory decide --factory-home /owned/factory --job JOB_ID --decision close --request-id UUID_HEX --evidence /owned/decision.json
millrace-plus factory cancel --factory-home /owned/factory --job JOB_ID --request-id UUID_HEX
```

Decision evidence has exactly `state_ref`, `changed_cause_ref`, `lad_decision`,
`lad_evidence_ref` and `resume_plane`. Use the exact immutable state Ref returned
by status. Every inapplicable field is explicitly null. Ref targets must already
be retained, contained and hash pinned. The decision ID binds exact intent.
For a child continuation, select the supported LAD decision and its exact
attention/run/plane evidence. This preserves the existing owning child control.
Before activating the parent service plane, submit a second `continue-lad`
decision with `lad_decision: "parent-control"`, `resume_plane: "service"` and
`lad_evidence_ref` pointing to the retained exact
`millrace-router-request/v2` `resume_plane`/`service` envelope. It must name the
current lane's parent instance/run and current expected state version. This
variant saves intent only; it sends no parent control while Factory is paused.
A pending child recovery is completed by replaying the same child decision ID,
evidence and selector; it GETs the saved recovery rather than posting another.
If `separate_run_resume_required` follows the historical foreground-plane action,
preserve that action and select a new explicit child `continue` decision with
`resume_plane: null` for the run gate. Never reinterpret the plane decision ID
as a run resume. Then explicitly resume Factory's `factory` plane through the existing public
Core control. Its active `resume_lad_parent` node dispatches that saved envelope.
Each decision/control keeps its own ID and retained history; sent replay is
exact-envelope GET-only. Never infer parent resume from a child decision.

For each newly submitted job, automatic lane activation selects one exact
control from the actual closed parent: `resume_run` for an existing run gate,
or `resume_plane`/`service` for a service-plane hold with no run gate and a
concrete resume target. Ambiguous or inadmissible state holds. The job, original
request, selected LAD task and owner bind this immutable intent before effect;
queued plane activation also requires the actual captured Factory invocation.
After any possible send, replay observes that original envelope only. A changed
parent state never selects a replacement for a saved unknown or refused request.
This automatic dispatch does not grant explicit operator continuation of an
unclaimed task: that operation refuses with `factory_lad_task_unclaimed`.

Factory waits for the parent to be actually closed and for its owner to retain
genuine joins for every already-sent current-run parent row before reporting a
human hold, even if the LAD task is unsettled. Draining or missing joins remain
pending within the existing `observe_lad` cooperative window and cadence before
another `hold` invocation. Long human waits are supported at these retained held/parked boundaries
and through the graph-dispatched parent continuation. Direct Service parent
controls remain supported standalone operations but do not receive this Factory
wait guarantee. Interruption or expiry before the first genuine join retention
remains unknown; old unknown stores cannot reconstruct a positive proof.

The existing child continuation forwards immediately. Starting child work while
Factory stays paused can lose a later child-maintenance observation during an
intermediate operator delay. Reactivate observation without such a delay; this
repair does not qualify arbitrary waits between every child/operator command.
The decision itself grants no general resume or recovery bypass.

Queued cancellation is local. Active cancellation first saves intent, prevents
future reservations and requests cancellation through the current owner.
Cancellation receipt, stopped PID, actual join, domain settlement and restored
source remain separate facts. Unknown closure keeps the job held.

Factory-native failures use ordinary error-to-hold routes. L-R2 provider waiting
is not selected. A capability-required provider-wait profile refuses. The current
CI role selections also lack a declared Factory review-family mapping, so a
required different-family profile refuses rather than inferring family from a
model name. Default independent review uses a separate applicable invocation.

On interruption, reconciliation validates retained facts and selects a declared
guard or observer. It never reruns a spent native role from a phase edit.
An old active-role pointer is checked against its original publication and later
consuming runs before removal or reuse. Missing/corrupt projections block new
claims. Preserve held state and use exact supported decisions and controls.
F2 publication, deployment, health, rollback and divergence reconciliation remain
outside local F1.


Git index protection compares logical staged path/OID/mode/stage entries,
assume-unchanged/skip-worktree flags and intent-to-add. Ordinary `git status`
stat-cache refresh does not change that identity. HEAD, actual source, protected
dirty work and meaningful index changes remain guarded. Raw index bytes are
diagnostic only. Old unaccepted Factory homes retain their exact old-byte
contract; this change supplies no automatic migration or rebaseline command.

All currently supported typed external inputs (`ci_event`, `user_report`,
`production_observation`) hold until an actual selected producer/validator
supplies source authority. Their accepted identity or typed shape does not bind
a local candidate. Only explicit local task inputs currently progress through
the local Factory source path.

The owning Service retains genuine joins for all validated already-sent parent
requests on its current run before a Factory held/parked boundary succeeds. Later receipt-only observations do not erase
that exact proof. Missing or unknown proof holds; a receipt alone cannot replace
an actual holder join.

A separate timing limitation remains in the existing LAD child-join path. In
independent review P2 on 2026-11-01, the child succeeded/quiesced but Service
advance returned `CHILD_UNCERTAIN/holder_unjoined`; a later rerun passed. The exact
window cause was not isolated. P2's task remains held and its Service stop was
pending `child_disposition_required`, so domain closure remains unknown despite
physical closure. The review and private probe references are in the F1 task's
`ox-final/review.md`; this repair supplies no B4, transport or history qualification.

A semantic human hold may be resolved unsuccessfully through the existing child
`close-failed` decision with an exact `millrace-lad-operator-intent/v1`
`terminal_failure` intent. This still requires actual terminal_failure CLOSE/ACK
and explicit `acknowledge-failed` with complete domain proof; it is not an
abandonment waiver. Plan a required parent continuation while the task remains
active/held, then let the active Factory observer retain its genuine parent joins
at the Service's next hold. Acknowledgement does not automatically pause a running
idle Service. Use an explicit supported owner pause while Factory observes when
needed. Finally select Factory `close` and reactivate its graph for unsuccessful
restoration/park/settlement. The failed job does not advance the accepted base.
This failed-hold path and the separately evidenced normal successful LAD path
have distinct qualification scope.

Stock LAD's semantic human HOLD explicitly selects its existing human_pause
resume target, so actual Core cursor and resume_target both preserve that
boundary for the unchanged owning Service guard. Error/refused/unknown pauses
are unaffected. Previously captured null-target definitions remain refused and
are not migrated or rebaselined by this correction.
