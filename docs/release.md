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
- Advisory agent skills: `millrace-instruction-manual`,
  `millrace-loop-configuration`, and `millrace-entrypoint-authoring`

PLUS-0002.9 is an internal official package boundary handoff, not a public
release guarantee. It closes internal conformance for the official package:
path, archive, and installed sources read the same package bytes, all six
selectors verify and select through public package APIs, selected package pins
record exact workflow and asset pins, and the 22 v0.21 entrypoint/stage-core
pairs have final package or deferral evidence.

The current source package also includes the completed PLUS-0003A through
PLUS-0003J hardening: standalone public validation, donor-module exclusion,
frozen-manifest policy, selected `vendor_selection` assets, live schema-output
repairs, the full-LAD Librarian handoff/truthful no-op contract, and
vendor-selection decision-context propagation through approval-policy,
conflict-rule, conflict-status, and source-ref handoffs. PLUS-0003J also
declares category/budget-only request-policy screening and a truthful
stage-owned `PolicyDecision` close artifact. ORCH-0001 and the final v12
E2E-0004/E2E-0005 rows are historical. TIME-0001 is complete with fresh
selected-plan-v13 E2E-0002 through E2E-0005 evidence on source commit
`ef9e6bbfa0a42a415eac2441a0b45b1f8e2f5360` and package commit
`6c66fabbbbdc0a1839fe556695e449e0da119b12`. PLUS-0003.9 records the final
pre-cutover handoff in `docs/PLUS-0003.9-public-release-readiness.md`.

The package remains dependency-free for direct installation and ships no
provider credentials, provider execution code, plugin/MCP behavior, native
runner code, marketplace behavior, or remote install semantics. A direct
`pip install millrace-plus` installs package metadata and data only; the future
`millrace` meta-package is the intended convenience install path for
`millrace-ai` plus `millrace-plus` if release cutover chooses that policy. The
project is buildable as source, but publication or name reservation remains a
separate release decision.

PLUS-0004 moved the three advisory skills into `millrace-plus` as immutable
package data after the standalone `millrace-ops` distribution was rejected as
unnecessary. The skills remain outside the workflow manifest. No installer,
entry point, registration hook, or post-install filesystem mutation is
included.

There is no plugin, marketplace, provider, or native-runner behavior available
from this package. Millrace OS, `millrace-web`, and Millforge are not included.

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
for the exact commands and
`docs/PLUS-0003.9-public-release-readiness.md` for the current classification.

## Internal Conformance Evidence

Internal conformance evidence remains available for the Millrace WPKG runtime
API and legacy donor evidence, but it is skipped unless
`MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1`, `MILLRACE_RUNTIME_SOURCE`, and
`MILLRACE_LEGACY_ASSET_ROOT` are all set explicitly. Internal conformance is
not public CI.

## Publication Blockers

- Current classification: pre-release private test artifact; not
  public-release-ready and not published.
- The pinned base-runtime source checkpoint still builds distribution
  `millrace-rewrite==0.0.0`, not a release-named `millrace-ai==0.22.0`
  artifact. CUT-0002 owns canonical promotion, final name/version metadata,
  and repeat artifact verification.
- DOCS-0001, META-0001, CUT-0001, and CUT-0002 remain downstream release
  gates.
- Operator approval and PyPI credentials are required before publication or
  name reservation.
- Direct `pip install millrace-plus` is intentionally data-only and does not
  install `millrace-ai`; a future change to that policy requires explicit
  META/CUT metadata and test updates.
