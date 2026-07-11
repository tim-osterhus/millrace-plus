# Release Notes

## Current Package

- Package: `millrace-plus`
- Version: `0.0.0`
- Workflow package ID: `millrace.plus.official`
- Workflows:
  - `simple_loop` / `0.1`
  - `execution.lad` / `0.1`
  - `execution.lad_integrator` / `0.1`
  - `planning.lad` / `0.1`
  - `lad.full` / `0.1`
  - `vendor_selection` / `0.1`
- Python: `>=3.11`
- License metadata: Apache-2.0 via `LICENSE`

PLUS-0002.9 is an internal official package boundary handoff, not a public
release guarantee. It closes internal conformance for the official package:
path, archive, and installed sources read the same package bytes, all six
selectors verify and select through public package APIs, selected package pins
record exact workflow and asset pins, and the 22 v0.21 entrypoint/stage-core
pairs have final package or deferral evidence.

The current source package also includes the completed PLUS-0003A through
PLUS-0003H hardening: standalone public validation, donor-module exclusion,
frozen-manifest policy, selected `vendor_selection` assets, live schema-output
repairs, the full-LAD Librarian handoff/truthful no-op contract, and
vendor-selection decision-context propagation through approval-policy,
conflict-rule, conflict-status, and source-ref handoffs. PLUS-0003H
implementation, post-diff confirmation, and parent combined verification are
green. Historical corrected E2E-0004 evidence is retained, while fresh
E2E-0004 and E2E-0005 live acceptance remain. Public release readiness remains
blocked on PLUS-0003.9, ORCH-0001 live closure, fresh E2E evidence, and release
cutover.

The package remains dependency-free for direct installation and ships no
provider credentials, provider execution code, plugin/MCP behavior, native
runner code, marketplace behavior, or remote install semantics. A direct
`pip install millrace-plus` installs package metadata and data only; the future
`millrace` meta-package is the intended convenience install path for
`millrace-ai` plus `millrace-plus` if release cutover chooses that policy. The
project is buildable as source, but publication or name reservation remains a
separate release decision.

There is no plugin, marketplace, provider, or native-runner behavior available
from this package.

Manifest authoring uses the frozen-manifest policy in
`docs/manifest-authoring-policy.md`. The committed manifest is the source of
truth; public tests verify its canonical formatting and freeze evidence for
manifest digest, package digest, selected workflow fingerprints, and asset
pins.

## Local Build

```bash
PYTHONDONTWRITEBYTECODE=1 uv build --out-dir /tmp/millrace-plus-build --force-pep517
```

## Public Standalone Validation

Public standalone validation runs without a sibling runtime checkout and
without `PYTHONPATH`. It is the CI boundary for build, lint, public package
tests, and installed-wheel package-data smoke. See `docs/public-validation.md`
for the exact commands.

## Internal Conformance Evidence

Internal conformance evidence remains available for the Millrace WPKG runtime
API and legacy donor evidence, but it is skipped unless
`MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1`, `MILLRACE_RUNTIME_SOURCE`, and
`MILLRACE_LEGACY_ASSET_ROOT` are all set explicitly. Internal conformance is
not public CI.

## Publication Blockers

- Operator approval and PyPI credentials are required before publication or
  name reservation.
- The future dependency metadata decision for `millrace-ai>=0.22,<0.23` is
  deferred to a release packet. PLUS-0002.9 keeps the package dependency-free
  and validates runtime conformance only through explicit internal opt-in
  tests.
