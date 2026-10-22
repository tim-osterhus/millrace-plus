# Paperclip assignment bridge

Paperclip calls a pinned workflow through an existing loopback Millrace OS
service. OS owns execution. The adapter returns structured results and a summary
to Paperclip's run-result surface; it never comments on or closes an issue.

The local package is `millrace_plus/integrations/paperclip/` in the installed
Plus package. Register that directory through Paperclip's local external-adapter
loader, then select `millrace_graph`. The adapter options are:

```json
{"bridgeConfigFile":"/absolute/path/bridge.json","timeoutSeconds":325}
```

The selected API contract is Paperclip commit
`043f2514e67c50320433a0013fb228a75a51325c`, adapter-utils `0.3.1`, Node
`24.11.0` or later. This integration uses the public `createServerAdapter`,
`execute`, `testEnvironment`, `signal` and `onCancellationReady` interfaces.
It has no runtime npm dependency. The implementation is independently authored;
upstream implementation code is not bundled. Offline loader/command-peer proof
does not qualify a real Paperclip account or provider route.

Create an explicit UTF-8 configuration, using a local selector and its catalog
fingerprint or an installed bundled selector with the exact Plus version:

```json
{
  "format":"millrace-paperclip-bridge-config/v1",
  "paperclip_origin":"https://paperclip.example",
  "os_origin":"http://127.0.0.1:8765",
  "os_home":"local",
  "os_credential_file":"/absolute/path/os/credentials.json",
  "definition":{"selector":"local:my-flow","version":"sha256:REPLACE_WITH_DIGEST","sha256":"REPLACE_WITH_DIGEST"},
  "destination_root":"/absolute/path/assignments",
  "binding_state_root":"/absolute/path/private-bindings",
  "helper_executable":"/absolute/path/venv/bin/millrace-plus",
  "instance_settings":{"path":["/usr/bin","/bin"],"env_allowlist":[],"host_paths":{}},
  "assignment_revisions":{},
  "wait_seconds":300,
  "cleanup_seconds":15
}
```

The example requires replacing paths and the digest. Use
`millrace_plus.catalog.workflow_fingerprint(Path(...))` to compute the existing
catalog fingerprint. `path`, `env_allowlist` and `host_paths` are explicit public
create/v2 settings. Optional `runtime_defaults` and `operator_policy` inside
`instance_settings` use Core's current settings schemas; omitted values retain
Core defaults. No model, host-path or credential setting is inferred from
Paperclip. OS validates the copied graph/settings before a run starts.

The credential is OS's existing owner-only regular JSON file `{"token":"..."}`.
The bridge validates it and reads it before every request. It neither creates
nor rotates credentials. A racing 401 returns uncertainty; fix the local
credential and repeat the same assignment. Redirects and ambient HTTP proxies
are disabled. Configuration and assignment files are limited to 64 KiB and
reject duplicate keys, nonfinite JSON and unknown fields.

Paperclip must supply `ctx.agent.companyId`, `ctx.agent.id`, `ctx.runId`, and
`ctx.context.paperclipIssue.id` with a nonblank ordinary-task description. Any
additional `issueId` or `taskId` must match. The adapter projects only these
fields, preserving the exact post-redaction description bytes. It does not
fetch another description. A private transient assignment file is removed
after the helper closes. Helper environment diagnostics list names only: HOME,
TMPDIR, explicit PATH, PYTHONDONTWRITEBYTECODE and declared noncredential
environment names. Names containing Paperclip, token, secret, password,
credential, private-key or API-key markers are excluded.

The stable key is `[paperclip_origin, company_id, issue_id, assignment_revision]`;
its compact UTF-8 JSON SHA-256 names the binding. Revision defaults to 1 for each
issue. Set `assignment_revisions[ISSUE_ID]` explicitly to reopen a settled issue.
An attempt ID is reporting metadata and cannot create another run. Description,
definition, OS target, settings, paths and finite wait/cleanup profile are frozen.
Changed shared settings or description under the same key refuse replay and
preserve the original target. An unrelated issue's revision has no effect.

Each assignment gets `<destination_root>/<binding-digest>/{workflow,project}`.
The project receives `subworkflow-inputs.json` with format
`millrace-plus-subworkflow-inputs/v1`, stable identity and
`inputs.request_markdown`. The graph's ordinary intake step maps that value to
`request.md`. The bridge has no workflow-name branch or artifact store.

For direct diagnostics, the immutable assignment file is:

```json
{"format":"millrace-paperclip-context/v1","company_id":"company-1","agent_id":"agent-1","issue_id":"issue-1","attempt_id":"attempt-1","description":"Redacted request\r\n"}
```

```sh
millrace-plus paperclip run --config /absolute/path/bridge.json --assignment /absolute/path/assignment.json
millrace-plus paperclip inspect --config /absolute/path/bridge.json --assignment /absolute/path/assignment.json
```

One nonblocking observer owns each binding. A duplicate concurrent attempt gets
`attention_required` / `attention_busy` without starting, cancelling or extending
deadlines. Inspection stays available. Lost create replies recover by replaying
the exact saved POST; lost start replies use the original exact receipt and
never a fresh start ID or newest-run scan. Retain binding records after failures.
Corrupt records refuse mutation. A new revision requires current exact public
terminal/quiescent/stopped-writer evidence for every older binding. Paused,
uncertain and cleanup-required bindings block reopening.

Regular HTTP observations are paced one second apart. During ordinary active
observation, the exact instance's saved-run summary can defer the full history
read; it never proves completion or writer closure. Paused/terminal state,
cancellation, retained uncertainty, an earlier paused/terminal or incompatible
full observation, or an absent/ambiguous/unsupported summary requires the exact
full run. No polling configuration field is exposed.

The quiet wait checks cancellation and the persisted wait/cleanup deadline at
most every 50 ms. That local interval excludes in-flight HTTP work: each request
retains its existing two-second timeout. Ordinary status and cleanup-closure
detection can take one observation interval plus HTTP time. No deadline is
extended, and writer closure still requires full public evidence.

SIGINT/SIGTERM requests cancellation of the saved run. The helper observes
terminal state, quiescence and stopped writers before settling. Helper death or
HTTP acceptance cannot prove graph completion. The first wait and cleanup
deadlines survive replay. Cleanup expiry returns `uncertain` and retains
`cleanup_required`; use OS public controls and exact-target inspection to resolve
uncertainty. Project holds are never released automatically.

Results use `millrace-paperclip-result/v1`, correlate assignment/attempt and exact
OS home/instance/run/revision, and contain at most 32 relative refs beneath the
assigned result root. A selected plane's refs are rooted under its project
workspace. Every reported ref must be an ordinary file within that root.
The graph owns its useful-result requirements; a successful graph with empty
refs is legal. Artifact refusal preserves Core's succeeded history.

Exit codes are succeeded 0, failed 1, cancelled 3, attention_required 4, refused
5 and uncertain 6. A result is bounded to 256 KiB and summary to 4096 characters.
Only verified graph success returns zero. `testEnvironment` checks configuration
and helper availability; it does not contact Paperclip or dispatch work.

Wait defaults to 300 seconds (range 1–86400), cleanup to 15 (range 5–60), and
adapter timeout must cover wait + cleanup + 10. The selected LAD profile is
wait 36000, cleanup 15, adapter timeout at least 36025, graph elapsed at most
35900. A native qualification requires its own explicit allowance and route;
the offline command proof grants no provider calls.
