# millrace-plus

`millrace-plus` is the public official workflow package repository for
Millrace. It is separate from the base runtime package: `millrace-ai` remains
the lightweight runtime, and the runtime does not depend on `millrace-plus`.

This PLUS-0001 scaffold contains smoke content only. It ships one
`test_only` workflow package fixture, `millrace.plus.scaffold`, so the public
WPKG APIs can validate, import, list, inspect, and read installed package bytes
without executing package code.

Official LAD, `simple_loop`, and `vendor_selection` workflow assets are not
included in this scaffold. PLUS-0002 owns the first official workflow content.

## Package Data

Workflow package data lives under `millrace_workflow_package/`:

- `manifest.json` declares the scaffold fixture.
- `assets/scaffold_prompt.md` is the single required prompt asset.

WPKG discovery reads these files as bytes through the package contract. It must
not import `millrace_plus`, load entry points, register extensions, or execute
package setup/runtime code.

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
PYTHONPATH=../millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --with pytest --with hatchling pytest -q
```

Build the local package with:

```bash
PYTHONDONTWRITEBYTECODE=1 uv build
```

The package is source-scaffolded and PyPI-ready locally, but it has not been
published or reserved on PyPI in PLUS-0001.
