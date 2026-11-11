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
