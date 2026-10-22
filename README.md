Reusable local child calls use ordinary command nodes and independent copies.
See [subworkflows](docs/subworkflows.md) for pinned requests, finite waiting,
explicit cancellation and retained uncertainty after interruption.

The optional [Paperclip bridge](docs/paperclip.md) maps server-owned issue
descriptions to pinned independent workflow copies through an existing local
Millrace OS service. Its local adapter reports results without changing issues.

<div align="center">
  <p>
    <a href="https://pypi.org/project/millrace-plus/"><img alt="PyPI v0.23.0" src="https://img.shields.io/badge/pypi-v0.23.0-orange.svg"></a>
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
See [catalog](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.0/docs/catalog.md), [workflows](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.0/docs/workflows.md), and [authoring](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.0/docs/authoring.md).

## Architecture and operation

Plus calls public Core controls and supplies external adapters. It owns no second runtime state store.
The old resource/selection API is retired. Historical manifest workflows are absent from this release.
The [command-only quickstart](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.0/docs/router-command-quickstart.md) authors, runs, edits and expands two headless instances.
See [adapters](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.0/docs/router-adapters.md), [public validation](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.0/docs/public-validation.md), and [release policy](https://github.com/tim-osterhus/millrace-plus/blob/v0.23.0/docs/release.md).

Stock native examples choose Claude Code. Configure the selected native runtime deliberately before execution.
The three packaged skills are advisory package data, not runtime-installed authority.

Release publication follows the matched Core, Plus, OS, then bundle order.
