# millrace-plus

`millrace-plus` is the public repository for Millrace's official workflow
packages and supplementary agent assets. It is a data-only workflow package,
not a runtime. It is separate from the base runtime package: `millrace-ai`
remains the lightweight runtime, and the runtime does not depend on
`millrace-plus`.

The current shipped package root is `millrace.plus.official`; the current
package version `0.0.0` remains unchanged. PLUS-0002.9 is an internal official
package boundary handoff, not a public release guarantee. PLUS-0003A through
PLUS-0003J subsequently hardened the public package boundary, manifest freeze,
selected vendor assets, live schema output, full-LAD Librarian no-op contract,
vendor-selection decision-context propagation, and the Policy Screener's
request-policy/`PolicyDecision` boundary. ORCH-0001 and TIME-0001 are complete,
including fresh selected-plan-v13 E2E-0002 through E2E-0005 evidence on the
current source/package pins. PLUS-0003.9 records the resulting handoff in
`docs/PLUS-0003.9-public-release-readiness.md`. The distribution remains a
pre-release private test artifact, not a published or public-release-ready
package; final metadata, documentation, meta-package, and cutover work remain.

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
- `assets/workflows/vendor_selection/entrypoints/` and
  `assets/workflows/vendor_selection/skills/` contain the selected prompt and
  stage-core skill pairs added by PLUS-0003D. PLUS-0003H preserves selected
  approval-policy, conflict-rule, candidate conflict-status, and source-ref
  context through the vendor-selection handoffs. PLUS-0003J declares category
  and budget as the only Policy Screener request-policy gates, keeps
  capabilities/vendor filters/operator hints downstream, and gives
  `POLICY_BLOCKED` a stage-owned `PolicyDecision`. These package assets do not
  by themselves establish current live end-to-end readiness. The fresh v13
  E2E-0005 row reached the selected durable operator wait after eight model
  units, two selected fanouts, and the selected join, with no runtime refusal
  or adapter failure.

The installed resource root `millrace_workflow_package` remains unchanged.

WPKG discovery reads these files as bytes through the package contract. It must
not import `millrace_plus`, load entry points, register extensions, or execute
package setup/runtime code. The package data is non-executable.

Manifest authoring is governed by the frozen-manifest policy in
`docs/manifest-authoring-policy.md`: `millrace_workflow_package/manifest.json`
is the committed source of truth, not generated from a DSL, and public tests
recompute the manifest digest, package digest, selected workflow fingerprints,
and asset pins from package bytes.

Three advisory agent skills live separately under
`src/millrace_plus/skills/`:

- `millrace-instruction-manual`;
- `millrace-loop-configuration`;
- `millrace-entrypoint-authoring`.

These skills are ordinary, non-executable package data. They are not workflow
assets, are not declared by `millrace_workflow_package/manifest.json`, and do
not create runtime authority. Installing the distribution makes their bytes
available under `millrace_plus/skills/`. They are not installed into an agent tool's skill root;
no post-install mutation occurs.

Final package conformance is recorded in
`docs/PLUS-0002.9-implementation-review.md`; the current full-LAD handoff/no-op
evidence is recorded in
`docs/PLUS-0003F-full-lad-librarian-handoff-and-noop-contract.md`. These are
internal rewrite evidence for E2E, DOCS, META, and CUT packets. Public release
readiness status and current v13 evidence are reconciled in
`docs/PLUS-0003.9-public-release-readiness.md`; release cutover remains
outstanding.

## Runtime Boundary

`millrace-plus` owns package metadata, release docs, and non-executable package
data. It does not own compiler, kernel, substrate, operator, runner adapter, or
package registry implementation. There is no plugin, marketplace, provider, or
native-runner behavior available from this package. Remote registry,
multi-tenant behavior, Millrace OS, and `millrace-web` are out of scope for
this repository. Millforge is also future ecosystem scope and is not included.

A direct `pip install millrace-plus` installs package metadata and data only;
`millrace-ai` is not installed as a transitive dependency. The future
`millrace` meta-package is the intended convenience install path for
`millrace-ai` plus `millrace-plus` when release cutover chooses that policy.

## Development

The distribution remains dependency-free at package import time.

### Public standalone validation

Public standalone validation does not require a sibling Millrace runtime
checkout and does not set `PYTHONPATH`. It covers package metadata,
documentation boundary wording, manifest and asset digests, agent-skill
inventory and hashes, package path containment, wheel contents,
installed-wheel package data, lint, build, and diff hygiene:

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q \
  tests/test_package_metadata.py \
  tests/test_manifest_authoring_policy.py \
  tests/test_official_package_layout_plan.py \
  tests/test_workflow_package_manifest.py \
  tests/test_workflow_package_installed_smoke.py \
  tests/test_public_package_boundary.py \
  tests/test_agent_skill_assets.py
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
