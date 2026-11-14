# Router adapters

## Editable stock workflows

Stock graph examples and independent copies are documented in [Workflows](workflows.md) and [Catalog](catalog.md). Their Claude binding is an editable example; it does not impose Claude settings semantics on Pi/Codex. The legacy compiler-format workflow resource API is excluded from current artifacts.


This page describes the optional native adapters in the development Millrace Plus
0.23.2 candidate. Public 0.23.1 qualification remains evidence for its original bytes.
Native qualification remains scoped to the source, artifacts, CLI and selected route in its recorded receipt.

The split is strict. Core owns generic graph validation, durable state,
controls, result parsing, process mechanics, and the builtin `command` adapter.
Plus owns the native `pi_json`, `codex_exec` and `claude_print` adapters, their profiles,
and their harness-specific settings. Plus exposes the public Core controls through a thin
facade. It keeps no state and no scheduler, and it does not ship a copy of Core.

## Millforge

The optional `millforge_run` / `millrace-millforge-run/v1` bridge invokes an
independently installed Millforge runtime. Its captured profile owns native
settings; Plus maps public request/record identities and selected seam input
into existing execution-v2 / `json-v1`. Configuration, timing and conservative
writer holds are documented in [Millforge adapter](millforge-adapter.md).
Offline verification does not qualify a live adapter route.

## Install And Handshake

The base distribution declares no runtime dependencies. The optional `router`
extra declares the one-way dependency `millrace-ai>=0.23.0,<0.24.0`.

The `router` extra keeps its older Core lower bound for unchanged Pi and Claude
consumers. Codex and OpenCode require Core 0.23.2 or later plus owned-process-v1
when invoked. Installing an older Core remains possible but produces a counted,
stopped preflight failure for those two consumers before any native preparation
or child launch. Use matched development artifacts for their offline proof.

The `millrace-plus` console script runs `millrace_plus.router.cli:cli`.
`python -m millrace_plus.router` is the module form of the same entry point.

- `import millrace_plus` imports no Core code.
- The router modules (`millrace_plus.router.control`,
  `millrace_plus.router.pi`, `millrace_plus.router.configuration`) and the router CLI
  require the Core module `millrace.router.execution` with contract id
  `millrace-router-execution/v2` at import or startup.
- An absent Core, an older same-version Core without that module, or a
  different contract id is refused with `core_absent`,
  `core_contract_missing`, or `core_contract_mismatch`. There is no fallback.
- Equal package metadata is not compatibility evidence. The handshake, not the
  version string, decides whether a Core is usable.

Codex and OpenCode additionally require Core 0.23.2 or later with public
`millrace.router.process.OWNED_PROCESS_CONTRACT = "owned-process-v1"` at live
invocation. That consuming-contract gate precedes native host preparation,
version probing and spawning. Missing/unsupported capability or an older Core
returns an existing-schema stopped, no-child preflight failure. Core may persist
its normal counted failed attempt; this does not claim zero framework mutation.
Pure planning, saved completion validation and historical reload do not query the
helper capability or current native resources. Pi/Claude retain execution-v2
admission without the process gate; no blanket import-time requirement is added.

The public process helper owns bounded byte delivery, fair stream draining,
deadlines and local group cleanup. Plus retains native framing, settings,
finality, evidence and failure/writer mapping. Codex snapshots its final file once
at root/output closure before root-policy descendant cleanup. A captured
`completed` result with `may_live` stays held until exact reconciliation and
explicit resume uses the retained result once; a failed attempt instead requires
a fresh explicit retry. Refactored bytes require their own offline acceptance and
later exact native qualification. Linux fake-process tests do not qualify macOS
or any provider route. No historical native allowance transfers to this candidate.

Core runs and tests without Plus. A Core-only host, such as plain
`millrace router` or Core controls called without the Plus resolver, refuses a
native-backed instance before any durable write. Use the Plus host for those
instances.

Execution/v2 supplies exact `plane_id` and selected native `plane` settings to each invocation. Its `project_root` is the selected plane workspace. Static adapter configuration carries every declared plane namespace and nodes keyed by `(plane_id, node_id)`, so equal local node names remain independent. Optional `SeamInput` carries frozen consumer input; Core owns process file/environment injection. The synchronous adapter invocation signature is unchanged.

Runtime and installed artifact evidence applies only to the exact matched source/artifacts recorded in the P9 acceptance receipt; historical execution/v1 native qualifications remain version scoped.

## Python Facade

`millrace_plus.router.control` exposes every public Core control function
with the Plus resolver injected explicitly: `create_instance`,
`list_instances`, `inspect_instance`, `load_instance`,
`update_instance_config`, `validate_revision`, `submit_revision`,
`start_run`, `inspect_run`, `recover_run`, `pause_run`, `resume_run`,
`accept_revision`, `pause_plane`, `resume_plane`, `cancel_plane`,
`redirect_plane`, `retry_invocation`, `cancel_run`,
`reconcile_invocation`, `release_project_hold` and `execute_request`. Each has
the Core signature without `adapter_resolver`; sixteen guarded functions
add trailing keyword-only `catalog_home: Path | None = None` for client ownership
checks (see [Catalog](catalog.md)). A caller cannot substitute another resolver. Core keeps every lock, expected version, request ID, receipt
and writer hold.

```python
import uuid
from pathlib import Path

from millrace_plus.router import control

home = Path("/path/to/state")
created = control.create_instance(
    Path("/path/to/workflow/workflow.json"),
    Path("/path/to/project"),
    state_home=home,
    request_id=uuid.uuid4().hex,
    expected_state_version=0,
    host_paths={"agent": "/absolute/path/to/pi-agent-dir"},
)
instance = str(created["instance_id"])
started = control.start_run(
    instance, state_home=home, request_id=uuid.uuid4().hex, expected_state_version=1
)
status = control.inspect_run(instance, str(started["run_id"]), state_home=home)
```

Plane controls require keyword-only `plane_id`; retry and reconciliation require
the same exact plane beside `invocation_id`. Run operations use no plane target.
The facade forwards Core projections directly, including `planes`, nested
`resolved_nodes[plane_id][node_id]` and run-aware
`validation_context.run_state_version`. It keeps no parallel lifecycle model.

## CLI

Both forms wrap the generic Core CLI entry point with the Plus resolver:

```bash
python -m millrace_plus.router <core-cli-arguments>
millrace-plus router <core-cli-arguments>
```

Subcommands, request envelopes, receipts and exit codes are those of the Core
`millrace router` CLI. Instance creation accepts the generic `--host-paths` JSON
map, so a Pi instance can be created through either CLI form:

```bash
millrace-plus router instance create --state-home ./router-state --workflow ./workflow/workflow.json --project ./project --host-paths '{"agent":"/absolute/path/to/pi-agent-dir"}'
```

Use aliases matching the profile's `agent_dir` and optional `loader` values.
The same CLI host exposes 21 forms: four `instance` operations; `start`,
`recover`, `pause`, `cancel`, `resume`, `submit`, `accept`, `retry`, `reconcile`;
`pause-plane`, `resume-plane`, `cancel-plane`, `redirect-plane`; and `validate`,
`status`, `replay`, `release-project-hold`. Plane commands require `--plane`;
retry/reconcile also require it beside `--invocation`. `redirect-plane` requires
`--node` and `--reason`. Saved envelopes use exact request/v2 scope; old request
formats and the old `redirect` alias refuse.

## Pi Configuration (Graph v3)

The `pi_json` adapter has contract `millrace-pi-json/v1`. Its profile uses
format `millrace-pi-json-profile/v1`. The fragments below are illustrative, and
their values are placeholders.

A graph v3 binding names the adapter, contract, executable, and profile:

```json
{
  "adapter": "pi_json",
  "contract": "millrace-pi-json/v1",
  "executable": "pi",
  "config": {"profile": "profiles/pi.json"}
}
```

A node's invoke config names its prompt. The provider, model, and thinking
values are optional:

```json
{"prompt": "prompts/step.md"}
```

Runtime, workflow and plane defaults use `adapter_settings`:

```json
"adapter_settings": {
  "pi_json": {
    "contract": "millrace-pi-json/v1",
    "config": {}
  }
}
```

For `provider`, `model` and `thinking`, a node value wins over the selected
plane default, workflow default, profile and runtime default, in that order; all
three settings are required after resolution. A workflow default that differs
from the profile value is refused (`conflicting_pi_setting`) before applying
plane/node overrides. Every explicit plane namespace is validated, including
unused ones. Every profile and prompt file must be listed in the workflow's `control_files`.

The profile fields are:

- `pi_version`, pinned to `0.87.0`;
- `agent_dir`, an alias resolved through the instance `host_paths`;
- `loader`, an optional alias resolved through the instance `host_paths`;
- `provider`, `model`, and `thinking`;
- `max_event_bytes`, which bounds event parsing.

Host paths are supplied as instance `host_paths` aliases.

## Phases

1. Configuration validation is pure over captured data and locator shapes. It
   runs at create, configuration update, validate, start, candidate activation
   and reload, covers unused bindings and explicit unused `pi_json` namespaces,
   and performs no live filesystem, environment, or version probe. Historical
   graph v1/v2 configuration must be explicitly reauthored for graph v3.
2. Planning is pure and deterministic. It builds the argv, the
   `PI_CODING_AGENT_DIR` environment addition, the generated `pi-prompt.txt`
   control and the adapter settings; settings never depend on the invocation
   identity or root. `PI_CODING_AGENT_DIR` may not appear in the instance
   environment allowlist.
3. Invocation preflight runs before any version-probe process. It refuses an
   unsafe prompt path, or a missing or wrong live agent or loader path, as a
   counted `execution_failure` with preflight evidence and no process ID.
4. Execution runs the pinned Pi 0.87.0 version probe and JSON event protocol,
   transferred unchanged. Event framing is bounded by `max_event_bytes`.
5. Completion validation is pure over the frozen plan and completion facts. It
   reads no store rows, live files, executables or environment.

## Bounds

Core bounds plan handoffs. Each JSON field is limited to 1,048,576 bytes.
Generated controls are limited to 1,114,112 bytes in aggregate, which is a
1 MiB prompt plus a 64 KiB wrapper. Very large outcome lists can be refused at
validation, before a run starts. They are never refused mid-run.

Pi evidence allows a null `session_id` on incomplete/failure paths; completed
transport requires it. When present, `session_id` must be a nonempty, NUL-free
string of at most 4,096 UTF-8 bytes. A present `final` object requires `provider`
and `model` with that same bound; neither field may be null. The `final` object
itself may be null on failure/preflight paths.

These evidence bounds apply after stream parsing, even when a value fits the
event-byte limit. Invalid returned evidence becomes `invalid_adapter_result`
uncertainty: Core retains the counted invocation and project hold, pauses
`writer_unknown`, and neither commits the invalid completion nor routes success.

## Limits

- Fake peers and offline artifacts establish source behavior. Native Pi/Codex
  qualification remains limited to the recorded versions, artifacts, routes and
  limits in its acceptance receipt. It does not qualify Claude or new wheels.
- Historical `lad.full.pi@0.1` bytes remain in matching v0.22 artifacts, excluded
  from current wheel/sdist payloads. That workflow uses the old compiler format and
  `>=0.22.4,<0.23` range. It needs its matching v0.22 runtime. Equal package
  versions do not make it runnable through this router.

## Codex Exec Configuration (Graph v3)

The `codex_exec` adapter selects `millrace-codex-exec/v1`, pinned to public
Codex CLI 0.163.0. Historical 0.153.2 profiles and stores remain paired with
their exact accepted runtime artifacts; no migration is provided.
Use the same facade and controls as Pi. A binding contains:

```json
{"adapter":"codex_exec","contract":"millrace-codex-exec/v1","executable":"codex","config":{"profile":"profiles/codex.json"}}
```

List that profile and the node's prompt in `control_files`. The profile has
this exact shape, with `model` and `reasoning_effort` optional only when
another settings layer supplies them:

```json
{"format":"millrace-codex-exec-profile/v1","codex_version":"0.163.0","codex_home":"codex_home","auth_mode":"chatgpt","sandbox":"workspace-write","model":"your-model","reasoning_effort":"low"}
```

Supply `codex_home` through the instance `host_paths` map. It names an existing
absolute directory with a readable regular `auth.json`. Preparation checks
metadata and access without reading credentials. This prerequisite does not
prove a valid login or model availability. The adapter never logs in or falls
back to API-key billing.

A node config is `{"prompt":"prompts/step.md"}`, with optional `model` and
`reasoning_effort`. Runtime, workflow and plane defaults use
`adapter_settings.codex_exec={"contract":"millrace-codex-exec/v1","config":{...}}`.
Node values win over selected plane, workflow, profile, then runtime values.
Differing explicit workflow/profile settings refuse before plane/node overrides.
Unused explicit plane namespaces are validated too. Effort choices are `none`,
`minimal`, `low`,
`medium`, `high`, `xhigh`, `max`, `ultra`. They are finite profile choices,
not a promise that every model supports them. The profile requires an explicit
sandbox: `read-only`, `workspace-write` or `danger-full-access`.

Common binding pins, timeout ceilings and the missing-timeout 1,800-second
warning remain Core behavior. Pure validation/planning and historical reload
never probe current files, credentials, environment or versions.

The one-shot argv fixes OpenAI provider, ChatGPT auth, file credential storage
and noninteractive approval policy. It passes the prompt on stdin and a generated
JSON schema through `--output-schema`. Only the fresh private
`--output-last-message` file supplies result bytes. JSONL items, quoted markers
and intermediate assistant text cannot select an outcome. Core persists the
raw bytes before `json-v1` parsing and follows declared graph transitions.

A retryable top-level error may precede success, including a large or
NUL-containing message. A failed turn, nonzero exit, malformed lifecycle,
missing final file or failed preparation never becomes success from late text.
JSONL lines have a fixed 1 MiB limit and total events a fixed 16 MiB limit.
These limits can refuse otherwise valid large native output. Final bytes and
retained stderr are bounded to 65,536 bytes each.

When a run does not complete, stderr starts with at most two labelled lines:
the last top-level `error.message` (`[codex-jsonl error.message]`) and the
failed turn's message (`[codex-jsonl turn.failed.error.message]`). Each line
has passed environment values and narrow credential shapes (bearer tokens,
`sk-` keys, token or key values, URL userinfo, email addresses) redacted,
control characters escaped, and is clipped to 1,024 bytes. Native stderr
follows a `[codex-stderr native]` header line. A completed run keeps native
stderr only. `stderr_truncated` reports any clipping. This text is diagnostic
only: it never selects an outcome, and its redaction is narrow, not a
data-loss-prevention guarantee.

Codex receives only PATH, its declared CODEX_HOME and passed HOME, TMPDIR,
LANG, LC_ALL, TZ, plus Core's reserved MILLRACE_SEAM_INPUT when supplied. Other
common environment keys remain available to other
adapters. API-key/provider/endpoint variables are excluded from Codex probe
and main processes. An authored CODEX_HOME collision refuses.

After root exit the adapter drains within five seconds and snapshots a valid
final file before forced cleanup. TERM is immediate after that attempt and
KILL follows two seconds later when needed. Forced or uncertain cleanup holds
the project for guarded reconciliation. A genuine retained completion needs
explicit continuation after stopped-writer confirmation. Failed work never
becomes success through reconciliation. Retry starts a new invocation, with
no native resume or session replay.

This is a trusted foreground POSIX lifecycle. It does not contain arbitrary
detached writers or prove remote effects stopped. The config/rules skip flags
and `--ephemeral` do not isolate every native instruction, tool, hook or managed
configuration. Source/fake-process acceptance and native qualification remain
separate, each limited to its exact accepted source, artifacts and route.

## Claude Code print

`claude_print` / `millrace-claude-print/v1` supports captured Claude Code
2.1.296 and 2.1.305 profiles through its public print/JSON Schema interface.
The demo defaults to 2.1.305. Explicit 2.1.296 profiles and retained 2.1.296
launches remain supported. Each launch uses its captured profile version and
requires that exact version in the native banner; the default does not
reinterpret historical launches. This source/offline compatibility does not
qualify the live demo route. D2 Claude OAuth qualification remains pending.
The profile is a captured control:

```json
{"format":"millrace-claude-print-profile/v1","claude_version":"2.1.296"}
```

Select it with binding config `{"profile":"claude.json"}` and node config
`{"prompt":"prompts/step.md"}`. Include both files in `control_files`. Optional
`model` and `reasoning_effort` settings can appear in the profile, runtime,
workflow, plane or node settings. Claude applies
runtime < profile < workflow < plane < node,
with later values overriding earlier values. A value selected from plane defaults
records provenance source `plane`; every explicit plane namespace is validated.
Unlike Pi/Codex, Claude permits a
workflow value to override a differing profile value. Explicit effort supports
`low`, `medium`, `high`, `xhigh`, `max`; native model support is checked by Claude.

Omitted model/effort flags leave native defaults in effect. The adapter inherits
the configured native authentication and provider, including API routes. It
merges the live process environment with Core's invocation environment inside
`invoke`; Core's explicit PATH wins. `LaunchPlan.environment` stays empty.
Native credentials/settings are not copied, inspected or frozen. Inherited
model, effort, provider and auth are not attested. Optional native-reported model
IDs are observations; native effort remains unobserved.

The adapter adds `--print --output-format json --json-schema <schema>`,
`--dangerously-skip-permissions --permission-prompts none`, and explicit authored
model/effort overrides. It forwards no approvals and has no interactive fallback.
It preserves native settings, hooks, MCP servers, plugins and credentials. The
exact version probe runs before each main; an auto-update causes version refusal
until the finite contract is updated. Static validation and historical reload
perform no live probes.

Only a complete successful object envelope's `structured_output` supplies Core
`json-v1` bytes. Assistant text never substitutes. Missing/null structured
output, arrays (including verbose-mode arrays), contradictory error metadata,
nonzero exit or malformed output fail. Core persists canonical structured bytes
before outcome/ref parsing and owns routing. Native stdout is bounded to 1 MiB,
stderr capture to 64 KiB and semantic output to 65,536 bytes. Either native
capture overflow fails. Failure previews retain useful bounded causes with
known environment values and credential forms redacted before clipping.
Redaction is not universal data-loss prevention.

After normal root exit, the adapter drains and polls until natural foreground
group closure or the invocation deadline. A short-lived native descendant can
close normally. Cancel, timeout or output/delivery failure starts bounded
TERM/KILL cleanup. Cancellation may return `cancelled` with writer `stopped`
only after the adapter reaps its live-owned root, closes stdin, observes real
stdout/stderr EOF and confirms the original process group is absent. All facts
must be observed before the existing five-second cleanup bound expires.
Cancellation produces no semantic result, including when valid output arrived
before cleanup. Missing closure facts, permission uncertainty or an expired
cleanup bound retain `may_live`. Timeout and output/delivery failure also retain
`may_live` after forced cleanup. Supported reconciliation remains necessary for
uncertain writers before further work. The adapter never reconciles a writer
automatically and never resumes a native session.

New cleanup observations use the optional, versioned `cleanup` evidence object
within `millrace-claude-print-result/v1`. It records the owned root identity,
cleanup return reason and observed closure facts. Completion validation requires
that proof for a launched cancellation reported as `stopped`. Historical
evidence without this object remains readable unchanged and acquires no new
closure claim.

Permission bypass is not a sandbox; use only trusted workspaces. Managed policy
can reject bypass or silently auto-deny prompts, which the adapter does not
attest or reinterpret. Native hooks/MCP/plugins, external effects and detached
processes remain outside the foreground process-group proof. Print mode skips
workspace trust and can silently ignore invalid native settings. Inherited
nested-launch markers may make Claude refuse. Native transcripts remain in the
user's native config directory and are not removed by Millrace cleanup.

Fake-process/source acceptance and real native qualification are separate.
Initial real qualification uses existing subscription OAuth as a harness-only
guard, with native model/effort inherited. Production supports native API/provider
configuration without that guard. Every native qualification claim remains
limited to its exact accepted source, artifacts, CLI and selected route receipts.

## Native diagnostic bridge (opt-in, Claude and Codex)

`claude_print` and `codex_exec` nodes can export one finite terminal diagnostic
record. The bridge is a generic local export. It does not route, retry, wait or
classify, and it knows no workflow, role or service. Nodes without the setting
keep the launch-v1 and result-v1 formats byte for byte. Captures from before the
bridge reload unchanged and are never reclassified.

Opt in with node config:

```json
{"prompt":"prompts/step.md",
 "diagnostic_bridge":{"context_path":"state/native-context.json",
                      "outbox_path":"state/native-diagnostics"}}
```

Both paths are normalized relative paths. They resolve against the node's actual
working directory (the plane workspace plus the node `cwd`) and must stay beneath
it. Neither path may contain the other. Symlinks and special files are refused at
every component.

**Launch format.** A bridge-enabled plan selects
`millrace-claude-print-launch/v2` or `millrace-codex-exec-launch/v2`. That is
the complete v1 details object plus exactly four members:

- `diagnostic_bridge`;
- `plane_id`, `node_id` and `producing_revision`, copied from Core's
  `InvocationRequest`.

Planning stays pure: it reads no workspace file, so Core can re-plan during
completion validation. Validators dispatch on the exact format string.

**Context preflight.** `invoke` reads the context before any native process,
including the version probe. The context is the strict
`millrace-native-context/v1` record with exactly `format`, `context_id` and
`input_sha256`. Its contents are never executed. Any valid strict JSON form is
accepted, including whitespace and key order; callers need not canonicalize it.
Duplicate keys, non-finite numbers and unknown or missing keys refuse.

Two input guards keep the read and the path safe:

- **Context read.** The read uses the router's existing 1 MiB captured-control
  bound, so a hostile file cannot force an unbounded read. A valid context is a
  few hundred bytes.
- **Identifiers.** `context_id` and the invocation ID each become one path
  component. They use the existing Claude safe-ID form, `[A-Za-z0-9_-]{1,128}`:
  no separator, dot component or control character. At 128 characters,
  `<id>.json` stays under the common 255-byte file-name limit.

Neither guard limits invocations, records, time or workflow behavior.

A missing context, an invalid context or an unsafe outbox returns an ordinary
stopped failure. It is never an adapter exception:

| Field | Value |
| --- | --- |
| Transport | `execution_failure` |
| Writer | `stopped` |
| Process, exit code, stdout, stderr | none |
| Phase | `preflight` |
| Diagnostic | `claude_` / `codex_` + `bridge_context_missing`, `bridge_context_invalid` or `bridge_outbox_invalid` |
| `native_diagnostic`, `diagnostic_bridge` | both explicit `null` |

Core routes this failure through the node's ordinary error edge.

**Terminal export.** After a successful preflight, the adapter publishes
`<outbox_path>/<context_id>/<invocation_id>.json` before returning its result.
New exports are canonical `millrace-native-diagnostic/v2` JSON. Canonical means
sorted keys, compact separators, UTF-8 (`ensure_ascii` false), finite numbers and
no trailing newline. The record has exactly the complete v1 field set, with
`format` set to the v2 string. The v1 field set is:

- `format`, `context_id`, `input_sha256`;
- `invocation_id`, `plane_id`, `node_id`, `producing_revision`;
- `adapter_id`, `adapter_contract`, `evidence_format`;
- `launch_sha256`: SHA-256 of the canonical retained launch `details`;
- `transport`, `writer_state`, `failure_code`;
- `native_diagnostic`: `{protocol, api_error_status}`.

v2 adds seven required members. None is optional. Each comes from the same
actual launch plan and terminal adapter evidence the exporter already receives.
Nothing is reconstructed from graph, profile or default bytes:

| v2 member | Source |
| --- | --- |
| `settings_sha256` | SHA-256 of canonical actual `plan.settings`. This equals the public Core `launch.settings`, top-level `requested`/`effective`/`provenance`/`warnings` included. It is not Core's separate `launch_settings` wrapper or its common settings. |
| `observed_version` | Actual terminal `adapter_evidence.observed_version`. `null` only when the native probe did not establish it, for example on a spawn or probe-mismatch failure. The profile's expected pin is never substituted. |
| `requested_model`, `requested_effort` | `plan.settings.requested.model` and `.reasoning_effort`. `null` exactly when the key is absent. |
| `resolved_model`, `resolved_effort` | `plan.settings.effective.model` and `.reasoning_effort`. `null` exactly when the key is absent. |
| `host_route_sha256` | SHA-256 of canonical `{adapter_id, adapter_contract, executable, native_home}`. `executable` is actual `plan.argv[0]`. `native_home` is the actual Codex `details.codex_home`, or `null` for Claude. |

Runtime-inherited values are retained as actual values. Claude may omit either
setting to keep native inheritance, which gives `null`. Codex requires both
effective settings, so its projections are nonnull. A present value is never
replaced by `null`. A malformed fact, such as a non-string present setting or
observed version, leaves the handoff unavailable (see below).

These facts have limits:

- `resolved_*` is effective adapter configuration. It does not claim the provider
  reported that model or effort.
- `host_route_sha256` is stable across prompts and occurrences. It changes with
  the selected executable or native home. It does not attest an account, binary
  contents, provider endpoint or billing.
- The record exports only these digests and the four model/effort values. Raw
  settings, provenance, warnings, argv, environment, executable or native-home
  paths, account locators and credentials are never exported.

**Record versions.** `NativeDiagnostic.load` stays the strict v1 reader. v1
bytes and their meaning are unchanged. `NativeDiagnosticV2.load` is the strict
v2 reader. `load_record` dispatches on the exact `format` string, and
`verify_record` uses it. Unknown formats, missing or extra members, wrong types
and noncanonical bytes refuse. A v1 record is never migrated, upgraded or filled
with v2 values. A consumer that requires a v2 fact gets a mismatch from a v1
record, because that member is absent. That consumer takes its own guarded
recovery instead.

Publication links a complete temporary file and never overwrites. Identical bytes
already at the path are reused, and the read-back compares only the expected byte
count. Publication never changes the native transport, writer state, diagnostic or
result bytes, and a publication failure never raises into Core
`adapter_exception` or unknown-writer recovery.

If publication cannot complete, `diagnostic_bridge` is `null` while
`native_diagnostic` is kept. The cause can be any of:

- different existing bytes at the path, which stay untouched. This includes an
  earlier v1 record at the same path, which is never replaced;
- an I/O failure;
- a malformed binding fact;
- no record file at all.

Any post-preflight evidence with a non-null `native_diagnostic` and a `null`
`diagnostic_bridge` means the auxiliary handoff is unavailable. It is not
verified bridge evidence and grants no retry authority. A consumer that depends
on the record must hold or reconcile. It must never infer anything from the
native status alone or blindly resend.

**Evidence format.** The result format is `millrace-claude-print-result/v2` or
`millrace-codex-exec-result/v2`. It is the complete v1 evidence, including the
optional Claude `cleanup` proof and every existing rule, plus exactly:

- `native_diagnostic`;
- `diagnostic_bridge`: `{path, sha256}` naming the published record, or `null`.

Positive Claude cancellation with writer `stopped` still requires confirmed
cleanup and `claude_cancelled`.

**Completion validation** is pure over the retained launch and evidence. It
checks the member shapes, the protocol and the record path for this invocation
and outbox. It does not read the sidecar. A later consumer must verify the actual
bytes against the retained reference, for example with
`native_diagnostic.verify_record`, before treating them as evidence. A missing
export after a crash or adapter exception is never diagnostic evidence.

**Status extraction.**

- **Claude** (`claude-print-json/v1`, pinned 2.1.296 and 2.1.305). The status is
  retained only when all of these hold:
  - the failure is `claude_native_exit` (nonzero exit with a parseable JSON
    envelope) or `claude_native_error` (exit 0 error envelope);
  - the envelope has `type: "result"` and `is_error: true`;
  - `api_error_status` is a JSON integer that is not a boolean.

  Any such integer is retained as structured data, including codes outside the
  usual HTTP range. Retention gives an unknown code no meaning. A consumer applies
  its own finite table and infers nothing from codes outside it.

  Anything else gives `null`, including:
  - an absent, string, boolean or float status;
  - a timeout, cancellation, spawn, probe or output-limit failure;
  - an invalid envelope;
  - prose in `result`, `errors` or stderr.
- **Codex** (`codex-exec-jsonl/v1`, pinned 0.163.0). The status is always `null`.
  Its `error.message` and `turn.failed.error.message` events carry no structured
  cause, and no message text is inspected.

Neither record keeps error prose, stderr or stdout. A typed status is an
observation, not a classification. Policy over these records belongs to the
consuming workflow, which must re-verify the actual bytes.

## OpenCode finite run

`opencode_run` / `millrace-opencode-run/v1` selects public OpenCode 1.19.3,
tag commit `e3c4eb242585dcd77be1a867391b7c25fd47ce52`. Its captured profile is:

```json
{"format":"millrace-opencode-run-profile/v1","opencode_version":"1.19.3"}
```

Use binding config `{"profile":"opencode-profile.json"}` and node config
`{"prompt":"prompts/step.md"}`, declaring both control files. Optional `model`
(`provider/model`, preserving later slashes) and `variant` settings resolve
runtime < profile < workflow < plane < node. Every explicit layer is validated;
unknown settings refuse. Omitted values preserve native inheritance. The
profile filename can differ from the project's native `opencode.json`.

The adapter invokes `opencode run --format json`, adding `--model` and
`--variant` only when selected. It sends the exact captured UTF-8 prompt bytes
on stdin, preserving a BOM, spaces and final-newline presence. It adds no result
instructions: the authored prompt must request its selected `json-v1` result.
There is no positional prompt, attach/server mode, continuation, session reuse,
interactive fallback or permission bypass. Finite native ask permissions
auto-reject; project `opencode.json` may widen permissions. A tool permission
denial can precede a valid final result. Native qualification records the selected project permissions, provider/model
and authentication route.

Core's declared invocation environment reaches the child without an ambient
environment merge. Native config/auth remain native; the adapter neither copies
nor attests them. The exact local version probe precedes each main launch and
has a five-second ceiling within the invocation deadline. Static validation,
planning and historical reload perform no live path, credential or version
probes. A different installed version refuses.

Only completed text parts belonging to one final message with step-finish
reason `stop` supply semantic bytes. Identical repeated parts deduplicate;
conflicting identities, ambiguous messages, later errors, missing stop/text,
malformed/truncated output, nonzero exit and unsettled writers fail. Earlier
tool-call messages, reasoning, tool output and stderr cannot supply the result.
There is no invented session-completed event. Core parses the exact semantic
bytes using the existing `json-v1` contract and owns routing.

Stdout is bounded to 8 MiB, each JSONL record to 1 MiB and stderr to 1 MiB.
Overflow starts cleanup. Evidence retains identities, counts, digests, observed
version, terminal reason and fixed failure codes. Native error/stderr payloads
are discarded; diagnostics contain only fixed sanitized codes. Normal root
exit waits for drained EOF and foreground process-group closure until the
invocation deadline. Cancel, timeout or transport failure starts TERM, then
KILL after two seconds, with a five-second cleanup bound. Forced cleanup keeps
`may_live` and produces no semantic completion. Public reconciliation precedes
fresh recovery; no native session is replayed. Foreground closure does not
attest detached processes or remote effects.

The migrated Codex and OpenCode paths observe Core's first generic stop before
parsing later cleanup bytes. Cancellation or timeout therefore keeps its
classification when a TERM handler emits a native error or malformed record.
Cleanup bytes still contribute diagnostics, counts and digests. An earlier
native failure keeps its own classification; Codex's failed turn can still exit
naturally before a later control request starts cleanup. These rules change no
stored native envelope or writer-reconciliation contract.

Offline fake-process tests cover the event and lifecycle failure cases. Selected
native qualification covers OpenCode 1.19.3 with the configured DeepSeek Flash
route: a useful run, cancellation and fresh recovery. This evidence remains
limited to its exact accepted artifacts and configuration. HTTP-start status
remains unknown on cancellation. Raw `may_live` and guarded effective `stopped`
are distinct observations. The proof does not attest the backend-effective
model or effort, detached effects, other providers or arbitrary platforms.

## LAD copied-worker boundary

New `bundled:lad` copies use graph-v4 and need the matched optional-limits Core
0.23.2 candidate. They omit the two overall run bounds, retain parallel capacity
two, and keep finite 1,800-second native and 10-second command invocation
limits. Explicit operator limits still apply. Existing finite v3/v7 runs retain
their captured startup limits and original elapsed anchor.

The supplied `profiles/claude.json` pins Claude Code 2.1.296 and omits model and
effort. Resolve each field independently through runtime, profile, workflow,
plane and node settings. Omitted fields leave native inheritance intact. For
example, set `model` or `reasoning_effort` inside the graph's
`planes.foreground.nodes.builder.invoke.config` only when an operator has
selected those values. Runtime, workflow and plane defaults use the existing
`adapter_settings.claude_print` contract/config envelope. The package provides
no forced model/effort combination.

LAD's invocation material is the selected role prompt plus its pinned common
contracts, assignment and predecessor references. Initial Checker preparation
receives original criteria, request identity, the approved packet's identifiers,
dependencies and paths, and any original pinned baseline. It omits opaque
predecessor results/specification refs, recovery pending routes, Consultant
carriers and Fixer/review refs before the requirement freeze. Full evidence stays
in canonical state and the recovery occurrences. The accepting boundary pins
the baseline and retains an immutable original-byte backup before downstream
work. A scoped baseline guard may restore only those verified original bytes,
retain the tamper observation, and route Consultant then a fresh Checker.
Ordinary state reads remain strict. Recovery does not replay target edits or
checks; missing/corrupt backup or unrelated integrity errors stay held.

The shared Claude wrapper appends a native `StructuredOutput` instruction and
passes a schema for declared `outcome`, `summary` and `refs`. It uses print JSON
mode and `--dangerously-skip-permissions --permission-prompts none`. Its explicit
invocation environment is not an ambient environment merge. The wrapper does
not inventory or attest native project/user settings, hooks, MCP/plugins,
credentials, detached effects or backend-effective model/effort. The editable
LAD guidance does not install host configuration or change those permissions.
The permission bypass is not a sandbox.

Direct role execution is the default and needs no helper facility. Planner
stays direct. Offline helper inventory, terminal-observation, collection and
integration fixtures exercise structural validation only. An
`offline_validator_fixture` qualification never authorizes ordinary prepare or
dispatch, including a repeated READY entry; no native helper route is qualified.
A future exact facility qualification must supply its actual task/descendant
inventory, closure, collected failures/cancellations and parent integration.
Unknown helper closure remains a hold.

Copied scripts run normally with stdlib-only Python, including `python -S`.
Only explicit `scripts/init_session.py --upgrade-session` lazily loads matching
installed public Plus/Core controls. Upgrade requires exact session, instance,
original run and expected state version, stopped effective writers, current
authored-revision eligibility and unchanged retained evidence. It preserves the
old session archive, accepted artifacts, original occurrence/source/guidance
identities and original-run Learning payloads. Retained v7 accepts a compatible
finite-v3 candidate; it does not accept a v4 tag change or convert the Core
store. The caller must own and join its controller/OS callers and exclude other
domain/helper/ownership-maintenance writers before invoking publication. Paused,
quiescent or a saved receipt does not prove those joins. The held public context
excludes controller/append operations and supplies facts; it is non-reentrant,
and callers must not invoke mutators inside it.

Offline fixture acceptance covers that validator and owned-caller procedure.
Actual used-copy publication, OS/external writer joins and native helper closure
need separate route-specific operation qualification; they are not established
by these development docs or by existing native adapter receipts.
