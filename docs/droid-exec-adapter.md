# Factory Droid exec adapter

`droid_exec` / `millrace-droid-exec/v1` selects one fresh foreground Droid
0.247.0 `exec` invocation. This source candidate has no native route qualification.
An ordinary installed fake uses the same public version and result protocol.
Factory Droid remains an optional external executable, without a Python dependency.

Capture a profile and prompt in the graph's `control_files`:

```json
{
  "format": "millrace-droid-profile/v1",
  "droid_version": "0.247.0",
  "permission_mode": "read_only"
}
```

The binding uses `{"profile":"profiles/droid.json"}` as its config. The node
uses `{"prompt":"prompts/task.md"}`. Profile and node may also declare `model`
and `reasoning_effort`. Profile settings are fallback below explicit runtime,
workflow, plane and node values. Each explicit layer is validated even if unused
or overridden. Missing settings emit no override and preserve native inheritance.
Recorded selections do not attest the backend's effective model or effort.

Model identifiers contain 1–256 UTF-8 bytes with no leading dash, whitespace or
control characters. The admitted effort values are `off`, `none`, `low`,
`medium` and `high`. A `custom:` model with explicit effort refuses because the
native provider configuration controls that setting. Native model/policy
rejections remain visible failures. The adapter never chooses a fallback route.

The adapter runs a bounded `--version` probe and accepts exactly `0.247.0`,
optionally followed by LF. The probe has five seconds within the original
invocation deadline and 4 KiB per output stream. Pure configuration validation,
planning and historical completion checks perform no probes or credential reads.
The probe PID is not durably announced through Core's start callback. Recovery
and closure qualification exclude a Core crash during this probe window.

Main argv is `exec -f FILE --cwd WORKSPACE -o json`, followed by explicit model
and effort flags when present. A captured fixed wrapper requests Core's
`json-v1` result and places the original task after formatting instructions.
Original and materialized prompt hashes remain distinct. Stdin is closed.

The fixed `read_only` policy emits no `--auto`, unsafe bypass, tool selector,
settings overlay or hook/MCP/plugin suppression. Native managed policy remains
effective. Read-only is a native permission policy, not a security sandbox or
claim that native integrations create no side effects. Session continuation,
fork, mission, spec, remote, worktree, RPC and ACP modes are excluded.

Success requires one strict documented result object, `subtype=success`,
`is_error=false`, valid metadata, zero exit, drained streams and natural owned
foreground-group closure. Only its `result` string supplies semantic bytes.
Limits are 8 MiB stdout, 1 MiB stderr and 65,536 UTF-8 semantic bytes. Duplicate
keys, trailing objects, invalid metadata, native failure, nonzero exit, timeout,
cancellation or incomplete cleanup defeat plausible result text. Diagnostics
retain fixed codes and counts/digests rather than raw native error payloads.
When a complete captured success-shaped envelope validates, its reported
session ID remains observed evidence even if a later transport/closure failure
defeats completion. Such a failure has empty semantic bytes and no accepted
final. Identity remains unknown when capture is truncated, malformed or does
not validate against that exact envelope; startup failure is one such case.

Core 0.23.2 or later with `owned-process-v1` is required before spawning.
The public helper owns TERM, two-second escalation and five-second cleanup.
With an observed PID, any stop cause, forced cleanup or unconfirmed cleanup
leaves the adapter writer `may_live`, even if the helper confirms group absence.
Core retains its existing hold and exact public reconciliation. Recovery is a
fresh invocation;
reconciliation never changes the failed attempt into success.
The evidence distinguishes a helper-observed process PID from Core's recorded
start-callback PID when setup fails between those two observations.

Detached processes, remote effects and provider HTTP start/abort outside the
owned group remain unknown. Separate native qualification must name the actual
artifact, host, account/model route and hooks/MCP/plugin/helper environment,
then prove useful completion, cancellation and fresh recovery. No account,
subscription, platform, sandbox, rollback or native qualification follows from
source/fake acceptance.
