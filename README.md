# millrace-plus

`millrace-plus` is the public official workflow package repository for
Millrace. It is separate from the base runtime package: `millrace-ai` remains
the lightweight runtime, and the runtime does not depend on `millrace-plus`.

The current shipped package root is `millrace.plus.official` at package version `0.0.0`.
It includes `simple_loop`, `execution.lad`, `execution.lad_integrator`, and
`planning.lad` as official public workflow entries and keeps package data
non-executable.

## Package Data

Workflow package data lives under `millrace_workflow_package/`:

- `manifest.json` declares package ID `millrace.plus.official`, package
  version `0.0.0`, and public workflow entries for `simple_loop` / `0.1`,
  `execution.lad` / `0.1`, `execution.lad_integrator` / `0.1`, and
  `planning.lad` / `0.1`.
- `assets/workflows/simple_loop/entrypoints/` contains the selected
  entrypoint prompt assets.
- `assets/workflows/simple_loop/skills/` contains the paired core stage skill
  assets.
- `assets/workflows/execution.lad/entrypoints/` and
  `assets/workflows/execution.lad/skills/` contain the shared LAD Execution
  entrypoint and core stage skill assets.
- `assets/workflows/execution.lad_integrator/entrypoints/` and
  `assets/workflows/execution.lad_integrator/skills/` contain the
  Integrator-only assets reused by the integrator workflow variant.
- `assets/workflows/planning.lad/entrypoints/` and
  `assets/workflows/planning.lad/skills/` contain LAD Planning entrypoint and
  core stage skill assets. The Planning workflow also selects inherited
  Execution assets through their existing package paths and digest pins.

The installed resource root `millrace_workflow_package` remains unchanged.

WPKG discovery reads these files as bytes through the package contract. It must
not import `millrace_plus`, load entry points, register extensions, or execute
package setup/runtime code. The package data is non-executable.

## Runtime Boundary

`millrace-plus` owns package metadata, release docs, and non-executable package
data. It does not own compiler, kernel, substrate, operator, runner adapter, or
package registry implementation. Marketplace, remote registry, plugin,
provider, native runner, and multi-tenant behavior are out of scope for this
repository.

## Development

The distribution remains dependency-free at package import time. Tests consume
the current local WPKG public API from the rewrite checkout:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q
```

Build the local package with:

```bash
PYTHONDONTWRITEBYTECODE=1 uv build
```

The package is buildable locally, but publication remains a separate release
decision.
