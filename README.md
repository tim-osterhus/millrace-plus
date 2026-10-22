# Millrace Plus

Millrace Plus supplies editable workflow templates, native harness adapters and advisory agent skills for Millrace 0.23.
Its ordinary workflow copies run without a Plus catalog service.

## Install

Python 3.11 or newer is required. The base package does not transitively install Core or OS.

```sh
python -m pip install "millrace-plus==0.23.0"
millrace-plus catalog list
millrace-plus catalog init bundled:software-change ./workflow
```

Install `millrace-plus[router]==0.23.0` to operate workflows with Core.
The compatible-minor dependency range selects Core 0.23.x and the host requires `millrace-router-execution/v2`.
The selected release proof uses `millrace-ai==0.23.0`; equal versions alone do not establish execution compatibility.
The Python 3.12 `millrace==0.23.0` bundle includes Core, Plus and OS.

## Catalog and workflow copies

Templates live under `millrace_plus/workflows/`. Catalog commands work without Core.
Use `millrace-plus catalog home set /absolute/path/to/catalog` to save a per-user catalog root that survives package upgrades.
An explicit `--catalog-home` overrides the saved selection. `catalog home reset` restores the default.
See [catalog](docs/catalog.md), [workflows](docs/workflows.md), and [authoring](docs/authoring.md).

## Architecture and operation

Plus calls public Core controls and supplies external adapters. It owns no second runtime state store.
The old resource/selection API is retired. Historical manifest workflows are absent from this release.
The [command-only quickstart](docs/router-command-quickstart.md) authors, runs, edits and expands two headless instances.
See [adapters](docs/router-adapters.md), [public validation](docs/public-validation.md), and [release policy](docs/release.md).

Stock native examples choose Claude Code. Configure the selected native runtime deliberately before execution.
The three packaged skills are advisory package data, not runtime-installed authority.

Release publication follows the matched Core, Plus, OS, then bundle order.
