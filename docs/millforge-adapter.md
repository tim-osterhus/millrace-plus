# Millforge adapter

`millforge_run` / `millrace-millforge-run/v1` runs one foreground Millforge
invocation through Plus. Millforge owns its Pi session and native tools. Core
owns the graph, durable state, cancellation and `json-v1` routing.

Use Core 0.23.2 or later with public `owned-process-v1`, matched Plus, and an
independently installed trusted Millforge executable. The selected native
contract is Millforge 0.2.0 / Pi 1.4.0 with Node >=22.19.0. Plus does not install
Node, npm packages, models or credentials. This guide does not assert that a
public npm release is available. Use an explicitly supplied, verified artifact
and its absolute executable path. PATH must select the intended Node runtime.

This adapter translates Millforge's public request and saved-record contracts.
The M4 task acceptance receipt owns exact artifacts, verification evidence and
qualification limits. Live Plus adapter routes require separate qualification.
Standalone Millforge's selected Luna OAuth receipt does not qualify this bridge,
another model or another platform.

## Capture a profile and task

Start with a valid native `millforge-profile/v2` manifest. Legacy profile v1 is
not accepted by this adapter. The profile owns mode, provider/model, reasoning,
sampling, tools, assets, capabilities and limits. Runtime, workflow and plane
native settings must be empty. Node config contains only `prompt`.

The profile's terminal section must match the graph node's exact outcome set:

```json
{
  "format": "json-object",
  "required_keys": ["outcome"],
  "outcome_key": "outcome",
  "outcomes": ["ready", "needs_work"]
}
```

This is a terminal-section example, not a complete native profile. Returned
objects may contain only `outcome`, optional `summary` and optional `refs`.
Core validates their types and project-relative references. A completed
`needs_work` outcome follows that graph branch normally. It is not an adapter
failure. Millforge's runtime status and the task's domain outcome are distinct.

Add the manifest, task and every declared asset to the graph's `control_files`.
Use this binding fragment for a profile with no assets and non-OAuth auth:

```json
{
  "adapter": "millforge_run",
  "contract": "millrace-millforge-run/v1",
  "executable": "/absolute/prefix/node_modules/.bin/millforge",
  "config": {
    "profile": "profiles/millforge/profile.json",
    "assets": {}
  }
}
```

For each native asset, map its native relative path to its captured control
path. For example, `"assets/context.md":"profiles/millforge/assets/context.md"`
inside `config.assets` preserves the native layout. Asset hashes must match the
manifest. Missing, extra or overlapping assets refuse.

A node can use that binding as follows, assuming its graph binding key is
`forge`:

```json
{
  "invoke": {
    "binding": "forge",
    "timeout_seconds": 300,
    "config": {"prompt": "prompts/task.md"}
  },
  "result": "json-v1",
  "outcomes": ["ready", "needs_work"],
  "on": {
    "ready": {"finish": "succeeded"},
    "needs_work": {"finish": "failed"}
  },
  "on_error": {"pause": {"reason": "millforge_error"}},
  "on_unknown": {"pause": {"reason": "millforge_unknown"}}
}
```

Keep the workflow's normal format, planes and limits. These are binding and
node fragments, not a replacement graph. Use the Plus resolver and existing
[router controls](router-adapters.md). A Core-only host cannot resolve this
native binding.

## Mode, credentials and handoff

`generate` uses one direct no-tools request. `agent` uses the profile's Pi tools
in the selected plane project directory. Plus cannot switch modes or override
native limits. Each invocation captures profile/request bytes and uses a fresh,
exclusive output directory. No ambient selected profile or previous run is used.

For an OAuth profile, add `"auth_home":"forge_auth"` to the binding config and
supply `host_paths={"forge_auth":"/absolute/owned/millforge-auth"}` when creating
the instance. The alias is required exactly for OAuth. Its target must already
be an absolute existing directory. Plus performs no login, refresh, credential
read or auth repair. Prepare credentials independently through the installed
runtime's supported lifecycle.

For `auth.kind` equal to `env`, omit `auth_home` and include the profile's
`auth.name` in the instance `env_allowlist`. For `none`, omit `auth_home` as well.
Only declared environment values reach the worker. Unrelated host-path aliases
used by other bindings remain valid. Pure validation and historical reload do
not probe current credentials, paths or executables.

When Core supplies selected seam input, Plus decodes its content as strict UTF-8.
Plus places it directly in one native request context string, with no label.
Without a seam, context is empty. The model therefore receives the selected
handoff automatically.

Other adapters may instead expose an
environment/file path that their model or tools must read. Plus does not reread
the seam path or producer artifacts. Seam identity and hashes remain captured
launch details, separate from native settings.

## Limits and timing

Native request v1 and coordinated capture/result/trace v3 are required. Plus
checks its exact request/profile identity and invokes public Millforge
`inspect` after normal exit. Missing, stale, partial or mismatched records
cannot supply semantic output. CLI stdout alone is never authoritative.

Profile snapshot plus encoded request must fit Core's 1,114,112 generated-byte
limit. JSON escaping counts toward that limit. Native input accounting is UTF-8
bytes of the prefixed task plus every context string. It must fit profile
`input_bytes`. Joining separators are not counted by that native formula.

Generated paths must remain unique after NFC normalization and casefold.
Standalone Millforge permits larger profiles and some path combinations that
this bridge rejects. Semantic output is limited to 65,536 bytes.

Seam-dependent excess can first appear at invocation reservation as an
`invalid_launch_plan` gate. It does not become a completed attempt routed through
node `on_error`. Keep handoff input bounded when authoring the graph.

The common timeout must contain native `elapsed_seconds` plus ten seconds:
five for public inspection and five for existing helper cleanup. Main and
inspection deadlines are clipped to the original caller deadline. Preparation
consumes that budget. The existing 1,800-second fallback therefore permits at
most 1,790 native seconds, and equality leaves no startup/publication headroom.
Choose a shorter native budget or a larger explicit common timeout.

The 300-second node example can use a 240-second native elapsed limit. This policy
changes no standalone Millforge deadline and promises no hard wall-clock abort.

## Failures and uncertain writers

A normal exit 0 requires full matching completed records, closed local
workers/tools/writers and valid terminal output. A normal exit 3 can supply a
matching failed record proving closed local resources. It remains a runtime
failure with no semantic output, allowing ordinary stopped-writer recovery.
A completed-looking record under any nonzero exit never establishes success
or closure.

Exit 2 is a CLI failure that may occur before or after worker launch. It is not
proof that no model or tool ran. Without a matching closed record, a started
invocation retains `may_live`. Cancelled and timed-out invocations deliberately
do not start another inspection process and retain the conservative hold.

Millforge's worker has a detached process group. Closing Core's foreground CLI
group cannot prove that worker or its tools stopped. Use existing public exact
reconciliation and explicit retry controls for held failures. There is no
automatic replay. No local process observation attests provider backend abort.
Diagnostics retain fixed adapter codes and counts/hashes, with synthesized
stderr rather than raw native exception text.

M4 verification uses fake peers and loopback provider fixtures, including real
installed CLI records. It establishes only its recorded artifact/platform
scope. It makes no local-model quality, general provider, macOS, retained-history
or release claim. Required harness/capability support and optional Harness²
useful-task proof do not depend on a bare local model passing first.
