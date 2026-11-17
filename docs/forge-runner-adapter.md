# Forge runner adapter

Status: source regressions and a 19-case real-SDK local-peer cohort have run,
including the cohort on an installed Plus wheel. Those runs used Forge 0.10.1 in
one minimal locked worker environment on Linux x86_64 CPython 3.12.14
(see [Real-SDK local-peer checks](#real-sdk-local-peer-checks)). Live backends,
models, credentials and accounts are not qualified. The Plus resolver selects the
exact `forge_runner` / `millrace-forge-runner/v1` pair.

Plus provides `forge_runner` / `millrace-forge-runner/v1`. The adapter runs one
short-lived worker process that drives Antoine Zambelli's Forge SDK
(`forge-guardrails` 0.10.1, commit `45a334d9250d2d72fcbb7ac6b583ca4c82140ecd`)
against one OpenAI-compatible endpoint. It is not tailcallhq ForgeCode and not
Millforge's Forge proxy route.

The adapter requires Core's public `millrace-router-execution/v2` contract and,
for live dispatch, `millrace-ai>=0.23.2` with `owned-process-v1`. Plus never
imports `forge`. The SDK is not a Plus dependency.

## Worker environment

The binding `executable` is the worker environment's Python interpreter. The
adapter does no interpreter discovery. That environment must hold CPython 3.12 or
later, `forge-guardrails==0.10.1` and its dependency closure. Plus, Millforge, Core
and OS are not needed inside it.

Plus ships no lock, installer or environment for the SDK. The checked environment
held exactly `forge-guardrails` 0.10.1 and its 12 locked dependencies (annotated-types,
anyio, certifi, h11, httpcore, httpx, idna, pydantic, pydantic-core, tomli-w,
typing-extensions, typing-inspection) and no Plus, Core or Millforge. That lock is
test evidence, not a supported install procedure.

## Captured profile and task

Declare the profile and prompt in the workflow's `control_files`. Use this binding:

```json
{
  "adapter": "forge_runner",
  "contract": "millrace-forge-runner/v1",
  "executable": "/path/to/forge-env/bin/python3.12",
  "config": {"profile": "profiles/forge.json"}
}
```

The node's `invoke.config` holds `prompt` and may hold `model`:

```json
{"binding": "forge", "config": {"prompt": "tasks/review.md"}, "timeout_seconds": 600}
```

The profile is strict JSON. Unknown or duplicate keys refuse.

```json
{
  "format": "millrace-forge-runner-profile/v1",
  "forge_version": "0.10.1",
  "backend": "openai_compat",
  "base_url": {"kind": "env", "name": "FORGE_BASE_URL"},
  "auth": {"kind": "env", "name": "FORGE_API_KEY"},
  "model": "my-model",
  "budgets": {"max_iterations": 16}
}
```

- `base_url` and `auth` name environment variables. They never hold values. `auth`
  may instead be `{"kind": "none"}`, which sends no key.
- Each name must be in the instance's environment allowlist. `PATH`,
  `MILLRACE_SEAM_INPUT`, `MILLRACE_*` and `PYTHON*` names refuse. The two names must
  differ.
- There is no default URL, fallback variable, provider fallback or account fallback.
- The operator's allowlist is the trust boundary. The adapter does not bind a
  credential to a host. An operator who must keep a credential away from an endpoint
  must not allowlist that pair.

### Model setting

`model` is the only setting. It resolves low to high: profile, then
`adapter_settings.forge_runner.config` in the runtime, workflow and plane layers,
then the node. A `model` missing from every layer refuses with
`forge_model_required`; Forge's client has no native default. Any other key in the
Forge namespace refuses with `unsupported_setting`, including in planes without a
Forge node. Other adapters' settings never reach Forge.

### Budgets

| Budget | Default | Range |
| --- | --- | --- |
| `max_iterations` | 16 | 1–32 |
| `max_retries_per_step` | 2 | 0–3 |
| `max_tool_errors` | 2 | 0–3 |
| `http_timeout_seconds` | 120 | 1–600 |
| `tool_calls_max` | 64 | 1–128 |
| `read_bytes_per_call` | 32,768 | 1,024–65,536 |
| `tool_output_bytes_total` | 1,048,576 | 65,536–4,194,304 |
| `list_entries_max` | 256 | 16–1,024 |

These are adapter limits, not Core workflow ceilings. `http_timeout_seconds` applies
to each HTTP operation, not to the whole request.

### Restrictions

- The node must run at the workspace root. `invoke` refuses another working
  directory with `invalid_launch_plan` before any process starts.
- Seam input is unsupported. Configuration accepts a seam-consumer Forge node, and
  Core then refuses it at invocation reservation with run gate `invalid_launch_plan`.
- The generated request must fit in 1,032,192 bytes.

## Process boundary

Planning writes three generated controls under the invocation root: `forge-worker.py`,
`forge-probe.py` and `forge-request.json`. The two scripts are pinned by SHA-256.
Changed script bytes refuse with `forge_control_digest_mismatch`. A historical
invocation reloads only while those bytes are unchanged.

For deadline `D`:

1. With less than 25 seconds left, the adapter refuses with `forge_deadline_too_short`.
2. The probe runs `EXECUTABLE -I -B -X utf8 forge-probe.py` with a 5-second limit. It
   must print one canonical JSON line reporting CPython 3.12 or later,
   `forge-guardrails` 0.10.1 and all 53 pinned package files matching.
3. With less than 20 seconds left after the probe, the adapter refuses.
4. The worker runs `EXECUTABLE -I -B -X utf8 forge-worker.py forge-request.json` with
   process deadline `P = D − 5`. Its stdin carries the start record with `P` and the
   soft deadline `S = P − 3`.

Core owns spawn, capture, TERM, KILL and process-group cleanup. The worker installs
no signal handler. Cancellation is SIGTERM to the owned group.

The worker writes one JSON envelope line to stdout and exits 0 (`completed`) or 3
(`failed`). Library output goes to stderr, which is counted but never retained.
Stdout is limited to 65,537 bytes and stderr to 1 MiB. No exception text, URL or
credential value reaches any output.

`CommandResult.stdout` carries the semantic `json-v1` string only on success. Every
failure returns empty stdout and `stderr = "[forge] <code>"`. Core alone parses the
result and chooses the transition.

## Tools

The worker exposes exactly three synchronous tools:

- `list_dir(path)` lists one workspace directory, sorted by name bytes.
- `read_file(path, offset, length)` returns one strict UTF-8 byte window.
- `finish(outcome, summary, refs)` reports the result.

The `read_file` schema has one known mismatch. The worker's tool spec declares
`offset` and `length` as `integer`. The SDK wire schema advertises both as nullable
with default `null`. The worker accepts omitted arguments, which take the defaults
`offset` 0 and `length` equal to `read_bytes_per_call`. It refuses an explicit
`null` as an argument error and charges the tool-error budget. This is the v1
worker behavior.

`finish` succeeds only as the only call in its response. The worker returns
`completed` only after the SDK run returned normally before the soft deadline and
the call log shows exactly one successful `finish` as its last entry.

### Read-tool limits

The tools are read-only, but they are not a sandbox.

- File bytes returned by a tool go to the backend. This is data egress.
- Reads may update atime.
- A hard link can reach a file outside the workspace undetected.
- The path walk crosses any mount point inside the workspace.
- The tools can read anything the Millrace user can read under the workspace
  except `.git` and `.millrace-router-owner`. That includes `.env` files and any
  state or credential home placed under the workspace.
- The SDK puts no response size cap on backend replies. Only `http_timeout_seconds`
  and the deadlines bound them.
- The path walk checks each component's type before opening it and its identity
  after. A swap inside that race window is opened once and then refused. A swapped-in
  device file still sees one open call.

## Outcomes and writer state

The adapter maps retained facts to Core transports through one fixed 17-row table.
The same pure function recomputes the outcome in `validate_completion`, which reads
only the plan, the completion and adapter constants.

- A probe or worker that never spawned contributes `stopped`.
- A spawned process contributes `may_live` if it had a stop cause or forced or
  unconfirmed cleanup. `may_live` keeps Core's project hold until reconciliation.
- Remote effects are UNKNOWN in every outcome, including `stopped`. That covers
  backend receipt, continued generation and tool-output egress.

A backend reply that parses as JSON but is not an object maps to
`forge_worker_internal`. A reply that fails JSON decoding maps to
`forge_backend_protocol`. Neither mapping changes the classification table.

## Source regression tests

These tests run without the SDK, a loopback peer or a credential.

- A profile of exactly 1,048,576 bytes is accepted. One more byte refuses with
  `invalid_forge_profile`.
- The worker and probe parse under CPython 3.11 with `-I -B`.
- Non-object JSON replies classify as `forge_worker_internal`. Undecodable JSON
  classifies as `forge_backend_protocol`.

## Real-SDK local-peer checks

The 19-case cohort has run with the real 0.10.1 SDK against a local loopback peer
that needs no credential. It has run against source Plus and an installed Plus
wheel in the environment described above. These results apply to the checked
artifacts and environment. The child environment held only `PATH` and the declared
endpoint variable. The cohort covers:

- The probe and worker start under `-I -B -X utf8`. Python startup, proxy and
  provider-key variables set in the parent do not reach them. All 53 pinned SDK
  files match.
- `list_dir`, then `read_file`, then a sole `finish` complete through the adapter
  and through a Core run. The result carries the text the worker read.
- Omitted `read_file` window arguments take their defaults. Null, boolean, float and
  out-of-range values are refused as argument errors.
- The SDK's batch, retry and message order supports the sole-call `finish` rule. A
  malformed argument string is recoverable.
- HTTP error statuses, malformed bodies, a closed connection, a read timeout and
  text-only replies map to fixed failure codes without copying response text.
- The soft deadline cancels an in-flight request. Parent cancellation terminates and
  reaps the worker. A cancelled Core run keeps its project hold until public
  reconciliation, and a fresh run then succeeds.

## Not qualified

These are not qualified:

- any live backend, model, credential or account;
- other interpreters, platforms or dependency versions;
- containment: the worker is not a sandbox, and these checks show no general
  environment isolation;
- remote effects: the test peer is local, so nothing shows that a real provider
  stops generating after cancellation or timeout.

The fake-process and fake-SDK tests in `tests/router/` cover adapter and worker logic
and fault injection only.
