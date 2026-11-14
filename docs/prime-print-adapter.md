# Prime Agent text-print adapter

Plus provides `prime_print` / `millrace-prime-print/v1` for the official Rust
Prime Agent 0.10.2 text-print command. This adapter requires Core's public
`millrace-router-execution/v2` contract and, for live dispatch,
`millrace-ai>=0.23.2` with `owned-process-v1`.
Prime Agent remains an optional external executable.

This contract pins upstream commit
`ea9d9602ec0938d9e454e8963107cd85af1565c6` and the
[v0.10.2 release](https://github.com/PrimeIntellect-ai/prime-agent/releases/tag/v0.10.2).
Source and inert-process checks do not qualify a native executable, provider,
account, model, operating system or production workflow.
One native route has a separate, bounded result. See
[Scoped native qualification](#scoped-native-qualification).

## Captured profile and task

Declare the profile and prompt in the workflow's `control_files`.
Use this binding:

```json
{
  "adapter": "prime_print",
  "contract": "millrace-prime-print/v1",
  "executable": "prime-agent",
  "config": {"profile": "profiles/prime.json"}
}
```

The minimal `profiles/prime.json` preserves native settings:

```json
{
  "format": "millrace-prime-profile/v1",
  "prime_version": "0.10.2"
}
```

A node selects the task with `{"prompt":"prompts/task.md"}`.
The profile, node and explicit adapter settings may supply `provider`, `model`
and `thinking`. Each profile value is fallback below explicit runtime,
workflow, plane and node values, in that order.
Omitted settings produce no override flags. The adapter records requested
values and provenance without claiming effective native settings.

`thinking` accepts `off`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max`.
Provider and model selectors must be nonempty, at most 256 UTF-8 bytes, and
contain no whitespace, control characters, NUL or leading dash.
Provider identifiers use lowercase ASCII letters, digits and single hyphens
between identifier segments.
Native configuration and model catalogs decide whether selectors exist and
which thinking settings a model supports.
The adapter does not discover providers, switch accounts or select a fallback.

Unknown fields, duplicate JSON keys, unsupported versions, invalid UTF-8,
NULs and empty prompts refuse before dispatch.
Captured prompts may be at most 1 MiB. The complete wrapped prompt must also
fit the smaller 32,768-byte positional-argument ceiling.
The wrapper requests one Core `json-v1` result and lists the declared outcomes.
It starts with ordinary prose, so a task beginning with `/login` remains task
text rather than a native session command.

## Native command and permissions

The adapter builds this argv directly, without a shell:

```text
prime-agent --print --no-session --cwd WORKSPACE [--provider PROVIDER] [--model MODEL] [--thinking LEVEL] -- WRAPPED_PROMPT
```

A captured `prime-prompt.txt` preserves the exact wrapped bytes and digest.
The native command receives those same bytes as one UTF-8 argument.
The adapter closes stdin. The [released parser](https://github.com/PrimeIntellect-ai/prime-agent/blob/ea9d9602ec0938d9e454e8963107cd85af1565c6/crates/pa-cli/src/args.rs)
supports these flags and the `--` positional delimiter.
No arbitrary argv, API key, endpoint or environment dictionary is accepted
through profile data.
JSON, RPC, ACP, daemon, attach, resume, continue, fork, goal and autonomous
modes are outside this contract.

Prime's `--no-session` selects a fresh in-memory root session.
It does not disable native configuration, skills, telemetry, credential stores
or filesystem writes.
The [release README](https://github.com/PrimeIntellect-ai/prime-agent/blob/ea9d9602ec0938d9e454e8963107cd85af1565c6/README.md)
states that model-generated Python and project commands run with user
permissions. Prime's workers and kernels are not a security sandbox.
Use a workspace and native configuration appropriate for that execution.
Native tools may modify files or create external effects.

Configure authentication through Prime's supported native setup before a
separately authorized run. The adapter never logs in, installs, upgrades,
copies credential stores or checks credential values.
Core passes only declared environment values. Durable launch evidence stores
environment names, never values.
Examples of native names include `PRIME_API_KEY`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `ANTHROPIC_OAUTH_TOKEN` and `GEMINI_API_KEY`.
Consult the [released provider mapping](https://github.com/PrimeIntellect-ai/prime-agent/blob/ea9d9602ec0938d9e454e8963107cd85af1565c6/crates/pa-ai/src/env_api_keys.rs)
and current provider eligibility before selecting a route.
A configured subscription or token does not prove model entitlement.

## Result and process boundary

The adapter first runs `prime-agent --version` with a five-second ceiling
clipped by the original invocation deadline.
Only exit zero with exact stdout `0.10.2` or `0.10.2\n`, drained pipes and
natural group closure permits the main command.
The [released entry point](https://github.com/PrimeIntellect-ai/prime-agent/blob/ea9d9602ec0938d9e454e8963107cd85af1565c6/crates/pa-cli/src/lib.rs)
returns from this probe before loading credentials or starting a kernel.
Version text does not authenticate a patched executable's bytes.

The [released print runtime](https://github.com/PrimeIntellect-ai/prime-agent/blob/ea9d9602ec0938d9e454e8963107cd85af1565c6/crates/pa-cli/src/print_runtime.rs)
waits for admitted inline descendants and queued turns before selecting its
primary answer. Text mode emits that answer to stdout with a trailing newline.
Native assistant errors or aborts and failed compaction outcomes cause a
nonzero exit. JSON mode has different error-exit semantics and is excluded.

The adapter supplies the complete settled stdout text to Core.
It never searches for a JSON substring or selects the last plausible object.
Empty text, invalid UTF-8, NULs and text above 65,536 bytes fail.
Core alone parses the `json-v1` result and selects the workflow transition.
Progress text mixed with JSON, malformed objects and duplicate keys therefore
cannot become a successful semantic result through substring extraction.
A later native error or nonzero exit defeats plausible earlier stdout.

Native text mode exposes no session identifier, provider/model attestation or
model stop reason. The native selector does not treat `Length` or `ToolUse`
as error exits. This adapter therefore claims the official settled text
boundary, zero exit and foreground closure. It cannot attest `stop_reason=stop`
or rule out a model length stop when stdout contains a valid result.

Core owns process creation, pipe draining and foreground process-group closure.
The invocation has 8 MiB stdout and 1 MiB stderr failure limits.
The version probe has 4 KiB limits per output stream.
Raw stderr is discarded. Failures retain fixed diagnostic codes and stream
counts/digests, with no native error preview.

Success requires root reap, both EOFs, natural group closure and no forced
cleanup. A child that remains after root exit prevents success.
Cancellation, timeout, overflow or delivery failure uses Core's bounded TERM,
two-second KILL escalation and five-second cleanup policy.
Cancellation after final text but before closure still prevents success.
Forced or uncertain cleanup records writer uncertainty and leaves Core's
project hold and public reconciliation rules effective.
Reconciliation cannot convert a failed invocation into success.

Native detached processes and remote effects remain unknown.
A closed foreground group proves neither rollback nor external-effect closure.
Pure configuration, planning and historical completion validation require no
native executable, live filesystem, environment or credential probes.

## Scoped native qualification

One native route passed a bounded live qualification on 2026-11-13.
The result applies only to the identities in this table.

| Item | Qualified identity |
| --- | --- |
| Platform | Linux x86_64, one host |
| Prime Agent | Official 0.10.2 `linux-x64` archive, SHA-256 `09659961dfde201cc39f1e69b607385d708da3e591f267a4fea04970afb8b005` |
| Prime executable | SHA-256 `f13e3e5ebcb1aa044bd4d5f8375479dbd1ca0210ded37db0d4a360a2be103b23` |
| Requested settings | Provider `deepseek`, model `deepseek-v4-flash`, thinking `off` |
| Core wheel | `millrace-ai` 0.23.2, SHA-256 `829e837e0ff03cf972c23e9d292cbc3a8d29f3d8bec3dace15e66c0f8ac8cfc3` |
| Plus wheel | `millrace-plus` 0.23.2, SHA-256 `502a751fc575a35397c6b45ccda69b48a759807a94b45f4c941cc0de85d4170d` |
| Kernel | Private Python environment, tree SHA-256 `329620b37c29aa5668248f344f1186f75d55308cd33d39d8717098c95539cdc5` |

The Plus wheel carries the adapter from commit
`4b3c96f6c75527ca76d4808a77462140d51d6557`.
The installed adapter, configuration, resolver and control modules equal that
commit's source bytes. Six installed Core router modules, compared by hash,
equal Core commit `6114929b30241604e65c53081e8d9ab2bd32cad6`.
Later Plus commits are not part of this result.

### Observed phases

A driver ran three workflows in sequence. Each workflow allowed one
invocation. The driver retried no phase.

1. **Useful task.** Prime wrote an audit file from fixture invoices. The file
   matched an independently computed decimal total, and Core recorded `READY`.
2. **Main cancellation.** The driver sent a public `cancel_run` request while
   the Prime main ran. Core returned `queued` and later recorded the same
   request as `applied`. The adapter stopped the main with forced cleanup and
   reported a closed foreground group. Core recorded transport `cancelled` and
   writer `may_live`.

   The driver then confirmed that the main and the identified tool process no
   longer existed. One public `reconcile_invocation` request recorded the
   writer as stopped. The run reached `cancelled` and quiescent. The original
   completion stayed unchanged apart from `effective_writer`.
3. **Recovery.** A fresh run with a new invocation completed a second audit.
   That file also matched its independently computed total.

The retained cancel receipt, final control page and reconciliation receipt
agree on the run, invocation and request identities.

### Counts

The route used three Prime main processes and three adapter `--version` probes.
These numbers count local processes. They are not a provider request count, a
cost figure or a spending limit. One main can send several provider requests.
No record counts provider requests for this run.

### Kernel setup in this route

Prime owns kernel bootstrap, and this adapter sets no kernel requirement.
The qualified route supplied a prepared interpreter through Prime's override
variable.
Core and Plus do not bundle, install or pin kernel dependencies.

The route's workflow declared six environment names, which Core passed to
Prime:

- `HOME`, a private directory for the job.
- `PRIME_AGENT_KERNEL_PYTHON`, the absolute path of the prepared interpreter.
- `PYTHONDONTWRITEBYTECODE`, set to `1`.
- `DEEPSEEK_API_KEY`, `PRIME_AGENT_CODING_AGENT_DIR` and `TMPDIR`.

These names describe this route only. Another route can declare other names.
The kernel tree hash was equal before and after the run, with no bytecode
files. A static reading of the pinned Prime source shows that the kernel
process inherits Prime's environment. No run record shows which interpreter
Prime selected for a tool call.

### Claim limits

- **Settings.** The provider, model and thinking values are the requested
  values that the adapter launched. No provider record attests the served
  model, the thinking level, a stop reason, billing or a provider-side abort.
- **Tool termination.** The cancellation phase shows that Core cancelled the
  main and that the tool process later no longer existed. The phase does not
  show that the cancellation stopped the tool.
- **Key handling.** An exact-byte scan of the job directory found no copy of
  the provider key in retained output. The driver then deleted the key file.
  The scan is not a sandbox, a secure erase or a check of the whole host.
- **Effects.** Provider-side and other external effects of the cancelled main
  remain unknown.
- **Scope.** This result does not qualify another operating system,
  architecture, provider, model, Prime version or kernel environment. It is
  not a security, production or release claim. It does not qualify the whole
  Plus package.

Raw run state and model output stay in private task records. The published
identities are the hashes and commits in this section.
