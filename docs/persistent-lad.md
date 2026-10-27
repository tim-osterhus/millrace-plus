# Persistent LAD

The candidate persistent service is an ordinary `bundled:lad-service` Core graph.
It accepts immutable tasks into a local durable FIFO and automatically initializes
one independent pinned LAD child per claimed task. Children share the original
cooperatively owned target path; their workflow/project, request, session, instance
and run identities remain separate. `bundled:lad` remains the standalone per-task
workflow.

This is a development candidate with bounded Linux offline evidence. Native
provider, macOS and actual used-copy qualification remain separate. An offline
one-day/thirty-day workload-equivalent study is selected after child-dispatch
closeout; it is not actual elapsed-day or live-production qualification. Existing
synthetic codec checks do not establish runtime history fit.

## Qualification status

The candidate passed 201 runtime cases, seven unaffected corrective checks and
two installed integration cases with public cleanup. Independent source review
found no blockers.

The complete public fault-recovery journey (B4) remains FAILED/UNQUALIFIED. A
separate B4 requalification follows the child-dispatch closeout. Full packet
acceptance and the persistent-service release gate remain open. The B4 test
stays enabled; earlier failed run states remain preserved without a claim of
domain closure.

An offline one-day/thirty-day workload-equivalent history study is selected as a
separate follow-up. Its driver and bounded execution recipe are pending; results
will not establish actual elapsed-day, live-production, native-provider, macOS
or used-copy qualification. Neither B4 nor history has a new result yet.

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
historical attention is never cleared: old-ID replay remains GET-only and reports
the same reason even after a newer decision progresses. Use a new request UUID
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
