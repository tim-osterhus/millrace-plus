# Persistent LAD

The candidate persistent service is an ordinary `bundled:lad-service` Core graph.
It accepts immutable tasks into a local durable FIFO and automatically initializes
one independent pinned LAD child per claimed task. Children share the original
cooperatively owned target path; their workflow/project, request, session, instance
and run identities remain separate. `bundled:lad` remains the standalone per-task
workflow.

This development candidate has Linux source evidence and scoped live B4
acceptance recorded in the selected successor section below. macOS and actual
used-copy qualification remain separate. The earlier offline workload-equivalent
history study remains partial/unqualified and is not elapsed-day or
live-production qualification.

## New-copy routing policy (L-R1)

New `bundled:lad` copies capture `context/routing-policy.json`
(`millrace-lad-routing-policy/v1`). The initializer and role preparation refuse a
missing, unknown or edited-to-unsupported policy before any role runs. Because the
policy is under `context/`, any edit changes the captured guidance identity and
stales in-flight occurrences and a pending Learning request.

Acceptance validates raw result identity, authority and every raw artifact
SHA-256 first. Only then does it check one finite presentation view: BOM, CRLF and
outer blank lines, canonical Evidence/Outcome label spelling and a generated
identity header when identities are missing. Raw bytes and accepted digests are
unchanged; an immutable `lad-state/presentation/<occurrence>/<raw-digest>.json`
record pins raw and view hashes. Every ordinary load recomputes the complete
record for each accepted current-contract result from its raw bytes, prepared
input and the occurrence's captured policy, and refuses a missing or different
record. The history log's raw identity and
below-5,120-byte rules are unchanged; the 100-character floor is removed.

Intentional new-copy tightenings, compared with the older substring checks:

- a label must begin a line; mid-line `## Evidence`, `Evidence:` or
  `Development check:` text is not a section;
- labels that appear only in fenced code or blockquotes refuse, including fences
  or quotes opened inside a list item;
- empty Evidence, Outcome or development-check bodies refuse;
- duplicate sections or checks with different bodies refuse, including a later
  heading whose title merely starts with `Evidence` or `Outcome`;
- explicit self-identity labels (`Session:`, `Occurrence:`, `Role:`, `Lineage:`
  and the combined labels) that name another occurrence, conflict with each other
  or carry malformed values refuse.

These refusals use existing invalid-result recovery. Copies captured before this
policy, retained v1 occurrences and their accepted results keep their original
interpretation; nothing is converted or reclassified. An explicit legacy upgrade
must install `context/routing-policy.json` together with the candidate scripts
and declare `scripts/presentation.py` and `context/routing-policy.json` (with
`scripts/session_compat.py`) in the resubmitted graph's `control_files`. Core runs
scripts from the captured control snapshot, so an omitted file makes preparation
refuse with `routing_policy_invalid` before any Learning dispatch.

Learning assesses once per packet after the second evaluated Fixer–Doublechecker
pair. A native Learning error or unknown result (Core's stopped-writer error
edge), an invalid assessment, an error marker including
`ERROR_TRANSIENT_RESOLVED`, or a failed preparation records one failed disposition
and returns the original REQUEST's ACK and saved route. There is no replacement
Learning, retry or later reentry for that packet. Unknown writers, changed
source/guidance/route and conflicting records hold. L-R1 changes no service
continuation, ownership, join, FIFO or terminal CLOSE/ACK behavior; Native14
acceptance remains evidence for its original artifacts only.

## Historical qualification evidence

The earlier candidate passed 201 runtime cases, seven unaffected corrective
checks and two installed integration cases with public cleanup. Its independent
source review found no blockers.

The bounded Plus runtime correction is implemented in the Plus source candidate. Normal
operator operations, including `decide`, use a 30-second budget, clipped by an optional
private caller absolute deadline. The original bound is carried through
`parent_control`, `decide`, `_maintenance`, reads, reconciliation and settlement. Local
Core inspections and snapshots are synchronous and non-preemptive; they, validation,
lock acquisition and CAS can pass the nominal deadline until an existing guard or I/O
boundary. This is not hard wall-clock cancellation. Receipt observation GETs have a
finite 30-second per-request ceiling clipped by the original caller deadline. OPTIONS,
POST and full-state read GETs retain their two-second caps. Initialization keeps its
original two-second create/replay/join deadline, and existing stop and observation
bounds remain unchanged. Expiry before unknown-send
reservation refuses without sending; expiry during CAS remains unknown, with same-ID
replay GET-only. No schema, CLI flag or Core/OS change is selected.

The retained active1440 transport diagnosis in
`lab/tasks/v0232-transport-repair-b4/2026-10-27/` reproduced the old receipt GET's
two-second `TimeoutError` while its exact server handler completed successfully after
5.103 seconds. A separate fresh OS service returned the matching receipt after
5.170 seconds under the explicit longer GET window. This demonstrates the receipt-read
limit for that saved case. It does not qualify retained history, pass B4 or establish
the cause of an earlier B4 failure; those acceptance gates remain separate.

The source-scope check passed 335 Linux tests (223 runtime and 112 OS-client),
three-file Ruff and one candidate build. This is not whole-suite or installed-smoke
evidence. OX source review returned APPROVED_WITH_NOTES with no blockers.
`lab/tasks/v0232-persistent-qualification/2026-10-27/runtime-budget-fix/final-status.json`
records source QA and local commit status.

The complete public fault-recovery journey (B4) remains FAILED/UNQUALIFIED. The first
requalification attempt failed after 519.276 seconds when an artificial two-second
whole-send deadline expired before POST; exact GET 404 kept the request safely held as
unknown. Physical group closure was observed, but domain closure remains unknown.

The B4 v2 attempt FAILED/UNQUALIFIED after 588.464 seconds. One unique selected journey
produced two JUnit entries for the same identity: a primary failure and teardown error.
Its bounded public observation expired; the saved parent resume was applied, but the
immutable advance later returned CHILD_UNCERTAIN with summary `os_unavailable`. The
cause was not isolated.

The final diagnostic B4 v3 attempt also FAILED/UNQUALIFIED after 486.637 seconds: one
unique selected journey failed, with no teardown error or skip. Its 90-second
`recover_and_continue` window ended at 99.514 seconds after three non-preemptive
callbacks of 28.666, 39.389 and 29.421 seconds. The later child foreground-plane
resume—not the earlier parent resume—reached the operation send after its deadline; the
capability guard refused before a network request or unknown-send reservation, leaving
the request UNSENT. This attempt did not reach v2's `os_unavailable` branch. All 29
recorded service HTTP requests across seven buffers succeeded (maximum about 0.165
seconds); that does not explain the v2 failure.

The outer fixture's publicly proven cleanup covers separate fixture identities. The
inner B4 cleanup stop expired; task 1 remains held and task 2 queued, with no terminal
receipts or successful settlement. Physical unit/PID/cgroup closure is proven, but inner
domain closure remains UNKNOWN and the v10/v11 stores remain held. The B4 test stays
enabled and earlier failed states remain preserved. That packet selected no further B4
or history attempt. Full packet acceptance and the persistent-service release gate remain open.
See
`lab/tasks/v0232-persistent-qualification/2026-10-27/b4-verification-v2/result.json`,
`lab/tasks/v0232-persistent-qualification/2026-10-27/b4-verification-v3/result.json` and
`lab/tasks/v0232-persistent-qualification/2026-10-27/b4-diagnostic-v3-disposition.md`.
No B4 pass, domain closure, native/macOS/used-copy qualification or release is claimed.

The earlier offline history attempt completed as PARTIAL / UNQUALIFIED. The 96-idle
one-day target completed with one parking callback; active reached 878 of 1,440
loops plus one parking callback under the unchanged 45-minute budget.
Thirty-day targets of 2,880 idle waits and 43,200 active observations were NOT
RUN. Active generation took 2,366 seconds, including postrun inspection and
copy verification, so costs were not apportioned. A raw Core inspect-run read was
4,281,128 bytes, above the existing 2 MiB response cap; the aged OS/LAD bridge
was not exercised, so this does not establish stock transport overflow or
intrinsic Core unfitness. No runtime performance change is selected. These
results do not qualify actual elapsed-day, live-production, native-provider,
macOS or used-copy operation, and do not establish retained-history fit.
`lab/tasks/v0232-persistent-qualification/2026-10-27/history-disposition.json`
and `lab/tasks/v0232-persistent-qualification/2026-10-27/history-interpretation-review.md` own the detailed evidence and limits.

## Setup and activation

Use an existing configured headless OS service with joined-call observation v2.
OS, Plus, the target and the named Core state home must use the same local paths.
Browser use is optional. Supply a strict OS profile and a pinned worker definition
JSON object with exactly `selector`, `version` and `sha256`. Bundled definition
versions match the installed Plus version; the digest is the catalog definition
fingerprint. Local definitions use `sha256:DIGEST` as their version.

```text
millrace-plus lad init --service-home ABS --target ABS --os-profile FILE --worker-definition FILE --core-state-home ABS
millrace-plus lad submit --service-home ABS --key KEY --request FILE
millrace-plus lad status --service-home ABS
millrace-plus lad status --service-home ABS --task TASK_ID
millrace-plus lad cancel --service-home ABS --task TASK_ID --request-id UUID_HEX
millrace-plus lad decide --service-home ABS --task TASK_ID --decision retry-observation --request-id UUID_HEX --evidence FILE
millrace-plus lad stop --service-home ABS --request-id UUID_HEX
millrace-plus lad parent-control --service-home ABS --request FILE
```

`--service-home` must be a short canonical path chosen before initialization.
Plus binds the service-owned datagram socket at `<service-home>/intake.sock`.
The full filesystem-encoded pathname must fit the host's Unix-domain socket limit.
An overlong path makes the idle-wait node return `HOLD` with `io_error`.
Linux verification uses short owned paths; the macOS path limit and lifecycle
remain unqualified.

Initialization requires an existing canonical nonsymlink Core home and compatible
public local Core reads/snapshot support. The immutable database initialization
intent pins that locator and the exact parent create request before sending it.
The applied OS create receipt, exact OS read and pinned-home local public read
must agree on the parent identity, workflow, project, captured settings and version.
Only then does initialization return `activation_ready:true` and its saved
`start_request`. Submit that exact envelope through the configured OS public
request API. Initialization itself sends no start and launches no provider.

A failed handshake retains its intent, create receipt, ownership and partial
files, exposes no usable start and sends no start. Exact retry uses the same
parent identity and request; it cannot change the pin or initialize over retained
work. Foundation intake-only databases are not migrated into services. Rebinding
the OS home name, moving the pinned home, copying live stores or transferring
ownership is unsupported.

Submit, status and queued cancellation remain usable without Core. Submit never
resumes a paused parent. Active cancellation/decisions and service setup/stop use
the verified local Core home and configured OS. Each command emits one JSON result;
refusals emit a JSON diagnostic to stderr and return 2.

## Queue and target authority

Requests preserve 1–65,536 original nonblank UTF-8 bytes, including line endings.
Keys match `[A-Za-z][A-Za-z0-9_-]{0,63}`. Identical key/bytes replay the original
acceptance identity and sequence; changed bytes refuse. New work requires a new
key. The smallest queued sequence is claimed transactionally while no task is
claimed/running/held. The immutable eight-key claim commits before child mutation.

Task text, claim, input manifest, worker definition and target manifest must match
on replay. No request or worker output can choose another target. The target owner
marker pins the service and filesystem identities. It preserves dirty and non-Git
files and excludes cooperating owners. The marker and short lock do not prove
external writers stopped. Aliases, symlinks, conflicting owners, corrupt records
and disk/publication failures preserve held ownership.

The six graph nodes explicitly route reconcile, claim, wait_intake, advance,
settle and hold. A service-owned Unix datagram waiter binds and rechecks SQLite
before blocking for 900 seconds; Core permits 910 seconds. Submit commits before
its best-effort wake. A lost wake is harmless to durability; timeout returns IDLE
without queue writes. The supplied node process unwinds its owned socket cleanup
on Core's graceful SIGTERM. Replaced paths remain untouched; SIGKILL/forced-death
stale sockets continue to hold. Idle/active boundaries still retain ordinary Core history.

## Observation and closure

The service privately selects `millrace-plus-lad-subworkflow-call/v1` and
`millrace-plus-lad-subworkflow-result/v1` only after verifying its frozen service,
claim, initialization pin, profile and parent authority. Request-v2 bytes and the
eight-key claim remain unchanged. Generic subworkflow commands reject these
markers. Ordinary retained v1/v2 calls keep their existing behavior and bounds.

Complete parent/child views come from the matching public local Core facade. The
persistent codec validates every relevant row before saving strict bounded counts;
it does not forge empty history arrays. A summary is past observation, never current
closure authority. OS still owns controls and exact request/holder observations.
Every lookup uses the original saved envelope target, including a start envelope
whose run target was null. A learned run ID is used only for exact run inspection.

Actual retained matching `os_owned_call/holder_joined:true` proves physical join.
False means known unjoined; null means receipt-only/lost observation. A latest
false for an exact call remains held and cannot use lost-observation recovery; null
or missing observation can use the existing recovery gates. A retained exact true
remains valid through later null. The recovery snapshot reference and its matching
original-version envelope are saved together by one lifecycle compare-and-swap.
On the exact official HTTP routes, the persistent service's
`lad_service_runtime._send` and child `subworkflow_v2._dispatch` each use one
validated capability for a private prevalidated POST; the service validates
before unknown-send reservation. Direct standalone `WorkflowOSClient.submit`
keeps normal capability negotiation, while custom clients and subclass
overrides keep normal validation. A terminal receipt, PID absence, timeout or
`call_returned` does not prove join. This includes dispatch-prevented calls: any
known live or unjoined call remains held. Preparation/parent-read deadline failure
can raise before a result is published; it never means a child is running.

The 1 MiB request/input, 2 MiB journal, 256 KiB result and 4,096-byte OS envelope
bounds remain enforced. Setup/task acceptance preflights worst-case escaped task
text with frozen metadata; this cannot prove future-history fit. Required sent-call
and join facts are never dropped to fit a diagnostic. If the complete diagnostic
cannot fit, prior evidence is retained and publication holds.

Before releasing a task backed by an actual run, one fresh public
`exclusive_run_snapshot` scope covers exact identity/settings/start receipt,
complete stopped-writer/control checks, immutable full-view proof publication and
terminal task CAS. OS reads occur before the guard; no network, Core mutation or
subprocess runs inside it. Publication/validation/CAS failure preserves the slot.
Saved full-view proofs are immutable path/hash evidence, not leases. An exact
proven no-run disposition has its separate service/target/call exclusion and CAS;
it invents no run, terminal Core status or CLOSE/ACK.

## Decisions, recovery and stop

Failure, cancellation, attention, refusal and uncertainty hold the FIFO. Normal
success still requires the worker's validated actual CLOSE/ACK chain. Queued
cancellation records `cancelled-before-start` without a child. Active cancellation
saves its explicit ID before sending and retains held ownership when closure or
CLOSE/ACK is missing. Repeating an ID cannot change intent.

`retry-observation` refreshes only saved identities. `continue` requires a paused
parent and ordinary same-child prerequisites; lost-holder recovery saves a fresh
recover envelope, requires its exact applied recovered/already-terminal receipt
and true joined observation, and preserves old nulls as null. `close-failed`
requires the worker's actual human_pause boundary and exact normal terminal intent;
it uses the declared terminal_failure route without rewriting the LAD ledger.

An unfinished LAD session still owes CLOSE/ACK and final cleanup. That obligation
does not itself prohibit continuation: explicit foreground plane resume may
continue only its validated saved node matching Core's paused resume target or
cursor. A separate run resume requires the plane already ready at that same
cursor behind an actual run gate. The unfinished-session check reloads the
ledger under a fresh exclusive Core snapshot at the selected state version.
Missing or mismatched continuation, an active writer, non-quiescence, a terminal
session or Learning-owned request refuses. Continue does not clear the cleanup
flag or acknowledge success; ordinary settlement still requires actual CLOSE/ACK,
finalization, all required joins and the guarded task transition.

For failed/cancelled actual runs missing CLOSE/ACK, only deliberate matching
`acknowledge-failed` or `acknowledge-cancelled` can abandon the domain slot. Supply
an evidence file with exactly:

```json
{"format":"millrace-plus-lad-abandonment-intent/v1","acknowledge_abandonment":true,"expected_task_version":2,"expected_state_version":8}
```

Versions are the actual current positive integer task/run versions. Every sent
child controller call must have its own retained true join; recovery evidence
cannot replace those joins for abandonment. A fresh guarded proof preserves the
failure/cancellation, available/missing CLOSE/ACK, original request and histories.
One transaction saves the intent/receipt and terminal CAS. Exact replay returns
the saved receipt without another release; changed bytes or corrupt proof refuse.
Succeeded/paused/running/unknown/no-run children cannot use abandonment.

`stop` saves its parent/active-task authority, pauses new claims, explicitly
cancels/drains the saved child, then cancels/joins the parent. Missing CLOSE/ACK
keeps stop pending until a separately accepted acknowledgement. Replaying the same
stop continues its saved obligations; it does not implicitly acknowledge a task.
`parent-control` records and forwards an exact public request-v2 envelope for the
established parent/run: run-wide pause/resume/recover/cancel with plane:null, or
explicit `resume_plane` with plane_id `service`. It uses null expected versions
for pause/cancel and positive non-boolean versions for resume/recover/plane-resume,
and rejects a fresh stale version before POST. Same-ID exact
bytes observe the original saved call/version using GET only after reservation;
changed bytes refuse. It creates no run and `continue` remains child-only.
Existing public prerequisites govern restart; a terminal cancelled run cannot be
assumed resumable. Stop includes every recorded parent call's original join.

A graph hold pauses the service plane; explicitly select `resume_plane` for
`service` to continue its declared route. `resume_run` clears a run-wide gate.
No run-to-plane translation or implicit parent resume occurs.

`lad decide --decision continue --resume-plane foreground` explicitly selects
that existing child plane (or `learning`). Omit `--resume-plane` to preserve the
existing run-resume default. The flag is invalid for other decisions. Evidence
bytes remain opaque, including JSON-looking notes; their contents never select
an action. The decision UUID pins both unchanged evidence bytes/hash and the
explicit selector, and changed reuse refuses. No plane is inferred. A plane
continuation can ready the selected plane while a recovery run gate stays set;
no work dispatches behind that gate. The command then leaves the task held with
`separate_run_resume_required`, durably retaining its intent, opaque note ref,
explicit selector, applied Core receipt and observations. A continuation-requested
local intent receipt is not a completed continuation or closure proof. Its
historical attention is never cleared. Exact old-ID replay of a fully published
attention decision revalidates local service/init/task/claim/journal authority,
decision kind, selector, exact evidence bytes and the matching applied plane
receipt under the existing ownership locks, then reports the same reason before
Core/OS observation. This refusal changes no records or ownership and proves no
closure. Fresh decisions, pending calls and incomplete attention publication keep
live reconciliation; caller expiry and nonblocking lock checks still apply. Use a new request UUID
for a separate explicit
`lad decide --decision continue` without `--resume-plane` to request run resume.
This second action revalidates the accepted recovery proof and exact known plane
control with fresh complete current facts; the original null remains null and
no satisfied recovery is repeated just to replace it. There is no generated gate
resume or automatic second control. Both decisions retain all original joins
and fixed saved versions. Ordinary Core serialization applies; version equality
alone does not guarantee no later no-op pause. A graph pause with no run gate
can continue directly by explicit plane resume. Already-succeeded observation
continuation sends no resume, and `close-failed` keeps its explicit worker boundary.

Public pause drains without cancelling the active wait. The recorded pause/stop
path uses a local wake hint after an accepted exact pause; the waiter rechecks
current durable pause/stop facts after binding before blocking. Historical
cleared pauses do not spin the waiter. Wake does not prove closure or join, and
stop still waits for child disposition before cancelling the parent. SIGTERM
unwinding applies to cancellation, not ordinary public pause.

An applied create receipt can precede actual OS holder retirement. Setup polls
that exact known-owned-false saved call by GET within its one original two-second
send deadline and requires actual true before handshake/start success. Null,
error, unknown, mismatch or expiry holds; no deadline reset or new POST is added.

Raw parent cancellation or death runs no service choreography and preserves the
discoverable child claim and target ownership for reconciliation. That is also
true for an explicit parent-control cancellation: use service-aware stop to drain
the child. An unknown raw external envelope cannot be reconstructed from a receipt
or supplied with a later substitute join; it retains a truthful hold.


Retained lifecycle rows are validated without importing Core before intake
preflight and before each effect. Saved stop and child-maintenance controls remain
bound to their purpose, derived UUID, exact original target, payload and version;
corrupt unsent rows refuse before POST. An exact competing parent binding may
accept only the already-established same creation/start/run authority and preserves
newer stop/maintenance facts.

Ordinary child holder recovery covers the complete saved generic and prior child
maintenance sent-call set. It requires a real full run-wide pause gate for every
nonterminal recovery and keeps before/after/current guarded proof links. The fresh
recovery call needs its own actual joined true. Explicit abandonment still needs
actual true for every original call; it cannot borrow recovery as physical join.
Pending saved child-maintenance observations refresh using GET before proof
publication. Generic outcomes and raw null/false facts remain unchanged.

## Historical transport closeout disposition (2026-10-27)

The source candidate's scoped evidence is Core 761 passed, 12 recorded skips and one approved deselection; Plus 335 passed with no skips; Ruff on two source modules and two test files; one Core wheel/sdist build; and passed installed smoke/origin checks. The selected Core repair is four-path, validation-local de-duplication within one complete snapshot. It adds no global/persistent cache, schema, API or timeout change. The profile pass was partial, with fresh producer/fixture and dependent Plus costs unmeasured; no throughput claim follows. Source-scope verification passed. OX returned APPROVED_WITH_NOTES for source and documentation only, with no Core correctness defect; commit status is recorded in `lab/tasks/v0232-b4-history-closeout/2026-10-27/final-status.json`. The review does not accept B4, P-L4, history or release.

The later transport closeout left product code unchanged. Its instrumented diagnostic's 80-second live-boundary wait expired before the selected second-task context. Inner cleanup stop also expired after 90 seconds, leaving domain closure unknown. Ordinary recorder rows can drop. The cause remains unisolated. No source repair followed.

The final stock B4 remains FAILED/UNQUALIFIED. The actual >=60-second CHILD_RUNNING observation passed. Task 1 reached succeeded with a terminal receipt in final state; canonical post-settlement assertions were not reached. The unchanged 600-second two-success observation expired with task 2 running without a terminal receipt. The cause of this B4 failure is not determined. Inner domain closure remains UNKNOWN despite proven physical process/cgroup closure. Earlier failed states and held v10/v11 stores remain preserved.

Independent offline history is PARTIAL/UNQUALIFIED:

- `idle96`: finite driver, local codec and cold OS receipt-read checks passed, with 97 invocations.
- `active1440`: driver and local codec passed, with 1,441 invocations/completions. The codec reduced a 7,025,510-byte run view to 1,084 bytes. The aged OS check failed with `SubworkflowError/os_unavailable`, leaving the target unqualified.
- `idle2880` and `active43200`: NOT RUN after that first negative result. No fit estimate was evaluated.

History process/cgroup closure and checker OS shutdown are proven. No low-level cause or timeout attribution is established. These checks do not qualify whole-service history, elapsed-day or live-production operation. Full packet, P-L4 and release acceptance remain open. That packet's review scope and exact receipts are recorded in `lab/tasks/v0232-os-transport-closeout/2026-10-27/final-status.json`.


## Historical replay and B4 packet (2026-10-28)

The completed predecessor receipt-transport correction passed 440 focused tests,
Ruff, the package build and installed identity checks. Its repaired saved-case
receipt GET passed in 5.487 seconds. The predecessor stock B4 remained
FAILED/UNQUALIFIED: the real >=60-second running boundary and both normal task
settlements passed, then historical attention replay exhausted its caller window.
Matching callback costs were not captured. Outer fixture cleanup was unknown
despite physical closure. These results do not establish the cost of that failure
or retained-history qualification. Exact evidence remains in
`lab/tasks/v0232-transport-repair-b4/2026-10-27/`.

The selected successor is
`lab/tasks/v0232-b4-replay-guard/2026-10-28/contract.md`. It permits the narrow local
historical refusal described above, cleanup phase/identity diagnostics, focused
remote checks and one fresh unchanged installed stock B4 journey followed by
independent review. Cleanup diagnostics record whether closed-parent waiting or
exact parent-join retention failed and the last already observed public facts;
they change no cleanup actions, deadlines or assertions. Receipt GETs may occupy
a caller window between two and thirty seconds before reporting expiry; the
original caller deadline still bounds that request and no liveness beyond it is
promised. Live `decide` / `_maintenance` may hold the existing target and journal
locks through a receipt GET for up to the remaining 30-second operator budget;
no new or larger locks are selected. Earlier failed results and held stores remain preserved. History and
full packet/P-L4/release acceptance remain separate and open.


## Selected live B4 qualification (2026-10-29)

Root accepted Native14's scoped Linux B4 journey after independent
ACCEPT_WITH_NOTES QA with no blockers. Both tasks settled normally with
CLOSE/ACK; 41 pinned immutable file hashes were independently verified. Seven
target unittests and independent checks, actual joins and normal main stop
passed. OS shutdown, dead service threads, exact PID/cgroup absence and executor
exit 0 are recorded. The exact scope and evidence are in
`lab/tasks/v0232-b4-live-qualification/2026-10-29/native14-final-acceptance.json`.

The exact task contract, source checks and raw run evidence are under
`lab/tasks/v0232-b4-live-qualification/2026-10-29/` (`candidate-contract.md`,
`native14-source-readiness.json` and `native14-verification/final/`). The latest
source round passed 14 UNKNOWN-observer, eight expiry and nine parent-observer
cases (31 total), plus three-file Ruff. Earlier overlapping rounds are separate.
Twenty native invocation records cover four configured pairs: GPT-6 Luna
xhigh/max through Codex CLI and Claude Haiku 5.5 medium/high through Claude
Code. They do not count provider-internal calls. Requested settings and reported
models are distinct: Claude reports claude-haiku-5-5; Codex's reported model is
absent. Codex model/effort evidence is captured configuration and CLI/source
evidence; neither backend independently attests effective reasoning effort.

The journey uses real serial LAD roles, two declared approval pauses and
automatic FIFO claim/start of a distinct second child on the same target. Stock
observation uses 60 seconds without a minimum native-call duration. The
lost-join mask ends only after saved genuine recovery/plane-versus-run proof;
exact original-envelope GET then obtains actual join evidence while preserving
the null prefix. A separate real OS worker remains held after Core return until
its actual release/join. These controlled faults do not demonstrate an actual OS
restart. The obsolete synthetic B4 monolith is archived outside pytest
discovery; lower-level protocol regressions remain, and default checks invoke no
native providers.

Task-only observation corrections admit each new parent control normally once,
then poll public GET of its exact saved envelope without repeated target-lock
admission. Only narrowly validated parent HOLD causes may resume parent
observation; child human attention and unknown writers do not auto-continue. A
separate UNKNOWN final-resume branch requires exact
decision/operator/input/selector/claim/child binding, then retains genuine GET
facts through existing attached-row validation, journal locking and lifecycle
CAS before the unchanged known-owned/same-ID decide path. UNSENT, NULL-only,
refused, conflicting and failed facts do not qualify; no extra POST, control
identity, fake join or stale overwrite is introduced. Applied receipts and owned
FALSE allow observation, not settlement. Normal settlement and stop retain
actual TRUE join and domain requirements. No arbitrary overall/phase/call/retry
cap or synthetic workload is selected; finite invocation, transport and existing
operator budgets remain effective.

The runtime unfinished-continuation guard/tests and explicit existing
role-field, path/digest guidance are local Plus commits
`38a86306dae4d668fbb63f85f9b3e326be823366` and
`785e37f7cf8ffc1ac9a8a509994f83ea28fa422e`. This B4 task's tested
runtime/guidance bytes are unchanged since those commits; later observer fixes
are task-driver/helper changes. Source bundles were committed separately; the
receipt owns local commit disposition. Guidance captures Manager's packet
template and clarifies existing guards, including canonical accepted-result
versus exact-byte artifact hashes and post-work source identity; validation is
not weakened.

All earlier failures, including Native12/13, remain unqualified; unknown
closures and held states remain preserved. This B4 task changes no Core/OS
product code. Qualification covers only the pinned Core/Plus/OS artifacts in the
receipt; later component edits are outside it. The UNKNOWN-first branch passed
14 unit cases; entry into that branch was not independently established live.
Some replay/order assertions rely on frozen source sequence rather than a
standalone transcript. No general retained-history, macOS, actual used-copy,
actual OS restart, production, release or push qualification follows; broader
packet/release acceptance remains separate.
