# millrace-plus

`millrace-plus` is the public official workflow package repository for
Millrace. It is separate from the base runtime package: `millrace-ai` remains
the lightweight runtime, and the runtime does not depend on `millrace-plus`.

The current shipped package data is still the PLUS-0001 smoke scaffold only.
It ships one temporary `test_only` workflow package fixture,
`millrace.plus.scaffold`, so the public WPKG APIs can validate, import, list,
inspect, and read installed package bytes without executing package code. This
scaffold is not the official workflow package.

Official LAD, `simple_loop`, and `vendor_selection` workflow assets are not
included in this scaffold. Official content is added through the PLUS-0002
child packets. PLUS-0002B performs the first shipped official package-root conversion.

## Package Data

Workflow package data lives under `millrace_workflow_package/`:

- `manifest.json` declares the scaffold fixture.
- `assets/scaffold_prompt.md` is the single required prompt asset.

The PLUS-0002A official layout plan keeps the package data root at
`millrace_workflow_package/`, uses package ID `millrace.plus.official`, keeps
package version `0.0.0`, and uses the installed resource root `millrace_workflow_package`.
Until PLUS-0002B replaces the scaffold with the first real official workflow
entry, the shipped manifest remains `millrace.plus.scaffold` and is explicitly
temporary/non-official.

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

The PLUS-0001 scaffold is dependency-free at package import time. Tests consume
the current local WPKG public API from the rewrite checkout:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --with pytest --with hatchling pytest -q
```

Build the local package with:

```bash
PYTHONDONTWRITEBYTECODE=1 uv build
```

The package is source-scaffolded and PyPI-ready locally, but it has not been
published or reserved on PyPI in PLUS-0001.
