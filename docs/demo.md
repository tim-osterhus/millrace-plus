# Local hardware demo

`millrace demo --no-open` starts an isolated OS service and a command-only
hardware workflow, then saves a standalone HTML report, JSON report and compact
execution record in a new directory under `./millrace-demo-results`. Use
`--output DIR` to select the parent directory. Existing results are preserved.
The command requires Python 3.12 and compatible installed Core, Plus and OS
contracts. Component version strings need not match. This candidate has no
release-qualified bundle tuple; historical Meta package pins remain separate.

The report contains bounded hardware observations and provisional memory/context
estimates from packaged references. It runs no model or benchmark and reports
throughput as unknown. Missing hardware information produces explicit unknowns.

A terminal keeps the completed result available until Enter, browser Finish or
Ctrl-C. Enter and Finish during work request cancellation. Noninteractive use
finishes after the finite graph. `--no-open` suppresses browser opening only;
the command prints a local single-use authentication link. That link expires
and is never saved with exports. The saved HTML works after the service stops.

No native process runs by default. `--native ADAPTER` selects one optional
attempt and displays its permissions, inherited configuration and possible
billing. Noninteractive selection requires `--consent-native`; interactive
selection asks once. `--profile FILE` supplies an existing nonsecret profile.
Claude and OpenCode support their documented inherited settings. Pi/Codex
selection currently reports configuration-required because a profile alone
cannot resolve their required host aliases. No login, version, readiness or
credential probe runs during passive selection. `--no-web` requests prose
based on the captured baseline; it does not change native tool permissions.
Live native-route qualification remains separate from deterministic/fake D1
checks.

The coordinator captures the baseline at its authored pause gate and publishes
its exact producer and digests through generic workflow outputs before resuming.
Native proposals cannot replace baseline facts or estimates. Cancellation never
starts fallback work. The demo owns one foreground OS child, its private state
and one Core run; an existing OS service is unaffected.

`--keep-demo` preserves the private state after supported shutdown. Default
cleanup requires public writer closure, OS worker drain, exact child exit,
durable export verification and unchanged root ownership identity. Uncertain
shutdown or failed export retains state and prints its recovery location.
No process is killed by a saved PID, and no stale-state reaper is installed.

Exit 0 means a valid complete report and confirmed shutdown. Exit 1 means
startup, graph, export or shutdown was incomplete. Exit 2 means invalid usage
or incompatible components. Enter/Finish while active returns 130 after
confirmed cancellation, or 1 when cancellation/export/shutdown is incomplete.
Ctrl-C returns 130 and records the actual closure/export disposition. Inspect
`execution.json` for `stop_source`, public request receipts and closure facts;
a report or server exit alone proves no writer closure.
