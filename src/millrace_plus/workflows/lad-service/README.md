# Persistent LAD service

Initialize this graph through `millrace-plus lad init`. It requires an existing
headless OS service, its configured named Core home and the matching canonical
local `--core-state-home`. Setup verifies exact OS/local parent identity before
returning a usable start request. It never starts a provider itself.

The single `service` plane routes reconcile, FIFO claim, socket wait, finite child
advance, guarded settlement and operator hold. Each task automatically initializes
an independent pinned LAD copy against the same cooperatively owned target.
`bundled:lad` remains the standalone per-task workflow.

Submission wakes the Unix datagram waiter after its durable SQLite commit. The
900-second wait and 910-second invocation timeout retain ordinary Core history.
There is no provider while idle. macOS/Linux are the initial platform targets;
actual platform and retained-history qualification remain separately required.

Use service-aware `lad stop` to pause claims, drain/cancel the saved child, then
cancel/join the parent. Missing CLOSE/ACK keeps the active task held until the
operator's separately accepted decision. Raw parent cancellation cannot run that
choreography. Read `docs/persistent-lad.md` for ownership and recovery limits.
