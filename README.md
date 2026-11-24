<div align="center">
  <p>
    <a href="https://pypi.org/project/millrace-plus/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/millrace-plus.svg"></a>
    <a href="https://www.python.org/downloads/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11+-blue.svg"></a>
    <a href="https://github.com/tim-osterhus/millrace-plus/blob/main/LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-green.svg"></a>
  </p>
  <img
    src="https://raw.githubusercontent.com/tim-osterhus/millrace/main/docs/assets/images/millrace-icon-signal-transparent-glow.png"
    alt="Millrace signal mark"
    width="180"
  />
  <h1>Millrace Plus</h1>
</div>

Millrace Plus supplies editable workflow templates, native harness adapters and advisory agent skills for Millrace 0.23.
Its ordinary workflow copies run without a Plus catalog service.

## Release status

Plus 0.23.2 pairs `millrace-plus==0.23.2` with `millrace-ai==0.23.2`. Its
release checks use matched, exact Core and Plus artifacts. Core publishes first.
Plus 0.23.2 stays unpublished until its tagged publication workflow uploads the
verified wheel and sdist. Check the [PyPI project page](https://pypi.org/project/millrace-plus/)
for availability. The published 0.23.1 artifacts and their recorded
qualification remain unchanged.

The Codex, OpenCode, Droid, Millforge, Prime and Forge adapters need
`millrace-ai>=0.23.2` with the public `owned-process-v1` capability at
invocation. The optional `router` extra keeps `millrace-ai>=0.23.0,<0.24.0` for
unchanged Pi and Claude consumers under execution-v2. An older Core gives a
counted, stopped preflight failure before native preparation or child launch.
Equal versions alone do not establish execution or helper compatibility.

Offline Linux checks of shared primitives are separate from native route and
platform qualification. Native evidence recorded for 0.23.1 artifacts does not
transfer to 0.23.2. See the [release policy](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/release.md).

## Installation

Python 3.11 or newer is required. The base package does not transitively install Core or OS.
After the 0.23.2 publication completes:

```sh
python -m pip install "millrace-plus==0.23.2"
millrace-plus catalog list
millrace-plus catalog init bundled:software-change ./workflow
```

Install `millrace-plus[router]==0.23.2` to operate workflows with Core. Pin
`millrace-ai==0.23.2` when you use the native adapters listed above. The host
requires `millrace-router-execution/v2`. The previous release remains
installable as `millrace-plus==0.23.1`.

## Catalog and workflow copies

Templates live under `millrace_plus/workflows/`. Catalog commands work without Core.
The catalog accepts graph-v3 and graph-v4 folders. Each copy is an independent
physical folder, and package upgrades leave existing copies unchanged.

| Template | Graph | Purpose |
| --- | --- | --- |
| `software-change`, `document-review` | v3 | Small editable examples |
| `lad` | v4 | Twelve-role software factory with a separate Learning plane |
| `lad-service` | v4 | Persistent LAD intake through a local Millrace OS service |
| `cicd`, `cicd-v2` | v4 | Local CI failure repair; v2 adds approved local commit and push delivery |
| `software-factory` | v4 | Local LAD and CI/CD composition |
| `hardware-demo` | v4 | Deterministic workflow behind `millrace demo` |

Use `millrace-plus catalog home set /absolute/path/to/catalog` to save a per-user catalog root that survives package upgrades.
An explicit `--catalog-home` overrides the saved selection. `catalog home reset` restores the default.
See [catalog](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/catalog.md), [workflows](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/workflows.md), and [authoring](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/authoring.md).

## What 0.23.2 adds

Each guide states the exact evidence behind its surface and what remains unqualified.

- **Shared native transport.** Codex and OpenCode run through Core's public
  owned-process helper. Linux fake-process tests cover the lifecycle cases; they
  qualify no provider route or macOS behavior. [Adapters](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/router-adapters.md)
- **Factory Droid** (`droid_exec`, Droid 0.247.0). One bounded Linux
  `auto_medium` trial passed on recorded artifacts and one OAuth route.
  [Droid adapter](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/droid-exec-adapter.md)
- **Millforge** (`millforge_run`). Checks use fake peers and loopback fixtures.
  Live routes need separate qualification. [Millforge adapter](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/millforge-adapter.md)
- **Prime** (`prime_print`, Prime Agent 0.10.2). One Linux x86_64 route with
  `deepseek-v4-flash` passed on recorded Core and Plus wheels built from an
  earlier Plus commit. [Prime adapter](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/prime-print-adapter.md)
- **Forge runner** (`forge_runner` v1, v2, v3, Forge SDK 0.10.1). V2 applies
  read-window defaults to explicit `null` arguments. V3 uses the OpenAI Responses
  API with an explicit model and reasoning effort. The v1 closeout ran a 19-case
  real-SDK loopback cohort on Linux x86_64 CPython 3.12.14. One installed v3 smoke
  on macOS arm64 completed a synthetic file task with `gpt-6-luna` at `low` effort.
  [Forge runner adapter](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/forge-runner-adapter.md)
- **Native diagnostic bridge.** Opt-in `diagnostic_bridge` records for
  `claude_print` and `codex_exec`. Nodes without the setting keep their v1 bytes.
- **Persistent LAD** (`millrace-plus lad ...`). Intake, provider waits and
  service controls through an existing headless OS service. Live B4 acceptance
  is limited to the pinned artifacts in its receipt. [Persistent LAD](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/persistent-lad.md)
- **OS-backed subworkflow calls** (`subworkflow advance`, request v2). The v1
  local child calls are unchanged. [Subworkflows](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/subworkflows.md)
- **CI/CD** (`millrace-plus cicd ...`). The v2 workflow can deliver an approved
  repair as a local Git commit and push it to a qualified, owned bare receiver.
  Pull requests, hosted pushes, remote checks and deploys remain deferred.
  [CI/CD](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/cicd.md)
- **Software Factory** (`millrace-plus factory ...`). Local F1 composition of LAD
  and CI/CD. Its checks use inert role peers and qualify no native provider;
  publication, deployment and rollback stay outside F1. [Software Factory](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/software-factory.md)
- **Workflow views** (`millrace-plus views ...`). Immutable published outputs
  that prove neither task success nor ownership. [Workflow views](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/workflow-views.md)
- **Hardware demo.** The deterministic workflow behind `millrace demo`.
  Native-route and bundle qualification remain separate from its local checks.
  [Demo](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/demo.md)

## Architecture and operation

Plus calls public Core controls and supplies external adapters. It owns no second runtime state store.
The old resource/selection API is retired. Historical manifest workflows are absent from this release.
The [command-only quickstart](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/router-command-quickstart.md) authors, runs, edits and expands two headless instances.
See [adapters](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/router-adapters.md) and [public validation](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.2/docs/public-validation.md).

Stock native examples choose Claude Code. Configure the selected native runtime deliberately before execution.
The three packaged skills are advisory package data, not runtime-installed authority.

Reusable local child calls use ordinary command nodes and independent copies.
See [subworkflows](docs/subworkflows.md) for pinned requests, finite waiting,
explicit cancellation and retained uncertainty after interruption.

The optional [Paperclip bridge](docs/paperclip.md) maps server-owned issue
descriptions to pinned independent workflow copies through an existing local
Millrace OS service. Its local adapter reports results without changing issues.

Release publication follows the matched Core, Plus, OS, then bundle order.
