# Router adapters

## Editable stock workflows

Stock graph-v3 examples and independent copies are documented in [Workflows](workflows.md) and [Catalog](catalog.md). Their Claude binding is an editable example; it does not impose Claude settings semantics on Pi/Codex. The legacy compiler-format workflow resource API is excluded from current artifacts.


This page describes the optional native adapters in Millrace Plus 0.23.1.
Native qualification remains scoped to the source, artifacts, CLI and selected route in its recorded receipt.

The split is strict. Core owns generic graph validation, durable state,
controls, result parsing, process mechanics, and the builtin `command` adapter.
Plus owns the native `pi_json`, `codex_exec` and `claude_print` adapters, their profiles,
and their harness-specific settings. Plus exposes the public Core controls through a thin
facade. It keeps no state and no scheduler, and it does not ship a copy of Core.

## Install And Handshake

The base distribution declares no runtime dependencies. The optional `router`
extra declares the one-way dependency `millrace-ai>=0.23.0,<0.24.0`.

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
- Equal `0.23.1` metadata is not compatibility evidence. The handshake, not the
  version string, decides whether a Core is usable.

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

`claude_print` / `millrace-claude-print/v1` invokes Claude Code 2.1.296 through
its public print/JSON Schema interface. The profile is a captured control:

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
TERM/KILL cleanup; forced cleanup yields `may_live` and no semantic result.
Supported reconciliation precedes further work. Native sessions are never
resumed by this adapter.

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

Offline fake-process tests cover the event and lifecycle failure cases. Selected
native qualification covers OpenCode 1.19.3 with the configured DeepSeek Flash
route: a useful run, cancellation and fresh recovery. This evidence remains
limited to its exact accepted artifacts and configuration. HTTP-start status
remains unknown on cancellation. Raw `may_live` and guarded effective `stopped`
are distinct observations. The proof does not attest the backend-effective
model or effort, detached effects, other providers or arbitrary platforms.
