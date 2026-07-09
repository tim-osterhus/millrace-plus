# millrace-plus

`millrace-plus` is the public official workflow package repository for
Millrace. It is a data-only workflow package, not a runtime. It is separate
from the base runtime package: `millrace-ai` remains the lightweight runtime,
and the runtime does not depend on `millrace-plus`.

The current shipped package root is `millrace.plus.official` at package
version `0.0.0`; in other words, the current package version `0.0.0` is the
final PLUS-0002.9 handoff version. PLUS-0002.9 is an internal official package
boundary handoff, not a public release guarantee. It closes internal package
conformance for `simple_loop`, `execution.lad`, `execution.lad_integrator`,
`planning.lad`, `lad.full`, and `vendor_selection` as official public workflow
entries and keeps package data non-executable.

## Package Data

Workflow package data lives under `millrace_workflow_package/`:

- `manifest.json` declares package ID `millrace.plus.official`, package
  version `0.0.0`, and public workflow entries for `simple_loop` / `0.1`,
  `execution.lad` / `0.1`, `execution.lad_integrator` / `0.1`, and
  `planning.lad` / `0.1`, `lad.full` / `0.1`, and `vendor_selection` /
  `0.1`.
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
- `assets/workflows/lad.full/entrypoints/` and
  `assets/workflows/lad.full/skills/` contain LAD Learning entrypoint and core
  stage skill assets. The full LAD workflow also selects inherited Planning and
  Execution assets through their existing package paths and digest pins.
- `vendor_selection` is asset-free in this package. Its selected workflow data
  lives in `manifest.json`, and its selected package pin has no selected asset
  pins.

The installed resource root `millrace_workflow_package` remains unchanged.

WPKG discovery reads these files as bytes through the package contract. It must
not import `millrace_plus`, load entry points, register extensions, or execute
package setup/runtime code. The package data is non-executable.

Manifest authoring is governed by the frozen-manifest policy in
`docs/manifest-authoring-policy.md`: `millrace_workflow_package/manifest.json`
is the committed source of truth, not generated from a DSL, and public tests
recompute the manifest digest, package digest, selected workflow fingerprints,
and asset pins from package bytes.

Final package conformance is recorded in
`docs/PLUS-0002.9-implementation-review.md`. That review is internal rewrite
evidence for E2E, DOCS, META, and CUT packets. Public release readiness still
depends on the PLUS-0003 public package-readiness lane and release cutover.

## Runtime Boundary

`millrace-plus` owns package metadata, release docs, and non-executable package
data. It does not own compiler, kernel, substrate, operator, runner adapter, or
package registry implementation. There is no plugin, marketplace, provider, or
native-runner behavior available from this package. Remote registry,
multi-tenant behavior, Millrace OS, and `millrace-web` are out of scope for
this repository.

A direct `pip install millrace-plus` installs package metadata and data only;
`millrace-ai` is not installed as a transitive dependency. The future
`millrace` meta-package is the intended convenience install path for
`millrace-ai` plus `millrace-plus` when release cutover chooses that policy.

## Development

The distribution remains dependency-free at package import time.

### Public standalone validation

Public standalone validation does not require a sibling Millrace runtime
checkout and does not set `PYTHONPATH`. It covers package metadata,
documentation boundary wording, manifest and asset digests, package path
containment, wheel contents, installed-wheel package data, lint, build, and
diff hygiene:

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q \
  tests/test_package_metadata.py \
  tests/test_manifest_authoring_policy.py \
  tests/test_official_package_layout_plan.py \
  tests/test_workflow_package_manifest.py \
  tests/test_workflow_package_installed_smoke.py \
  tests/test_public_package_boundary.py
```

Build the local package with:

```bash
PYTHONDONTWRITEBYTECODE=1 uv build --out-dir /tmp/millrace-plus-build --force-pep517
```

See `docs/public-validation.md` for the full public CI command set.

### Internal conformance evidence

Internal conformance evidence exercises the Millrace WPKG runtime API and
legacy donor evidence. Those tests are skipped unless all of these environment
variables are explicit:

- `MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1`
- `MILLRACE_RUNTIME_SOURCE`, pointing at the runtime source checkout `src`
  directory to test against
- `MILLRACE_LEGACY_ASSET_ROOT`, pointing at the legacy asset evidence root

Internal conformance is not public CI. It must not be used to claim public
standalone readiness unless the public standalone validation above also passes.

The package is buildable locally, but publication remains a separate release
decision.
