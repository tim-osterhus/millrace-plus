# Validation

Millrace Plus is a data package. Its normal validation can run from a clean
checkout without a sibling Millrace runtime repository and without setting
`PYTHONPATH`.

## Standalone Checks

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  uv run --no-project --with pytest --with hatchling pytest -q \
    tests/test_package_metadata.py \
    tests/test_manifest_authoring_policy.py \
    tests/test_official_package_layout_plan.py \
    tests/test_workflow_package_manifest.py \
    tests/test_workflow_package_installed_smoke.py \
    tests/test_public_package_boundary.py \
    tests/test_agent_skill_assets.py

PYTHONDONTWRITEBYTECODE=1 \
  uv build --out-dir /tmp/millrace-plus-build --force-pep517

uv run --no-project --with ruff ruff check src tests
git diff --check
```

These checks cover:

- distribution metadata and the dependency-free package boundary;
- canonical manifest formatting and freeze evidence;
- workflow and asset digests;
- package-relative path containment;
- advisory-skill inventory and content hashes;
- wheel and source-archive contents;
- installed-package resource discovery;
- the absence of runtime code and executable registration hooks.

The manifest evidence is maintained in
`docs/manifest-authoring-policy.md` and verified by
`tests/test_manifest_authoring_policy.py`.

## Inspect The Built Wheel

The wheel should contain all three of these roots and no `millrace/` runtime
package:

```text
millrace_plus/
millrace_plus/skills/
millrace_workflow_package/
```

Installing the wheel must not load entry points, execute workflow code, or
install `millrace-ai` as a dependency.

## Source-Conformance Checks

Some maintainer tests compare the packaged workflows with runtime APIs and
legacy donor evidence. They are intentionally opt-in and require all three
variables:

- `MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1`
- `MILLRACE_RUNTIME_SOURCE`, pointing to the runtime checkout's `src`
  directory
- `MILLRACE_LEGACY_ASSET_ROOT`, pointing to the legacy Millrace asset root

These tests are useful while migrating and reviewing workflow behavior, but
they are not required to verify that the built package is self-contained.

## Live Workflow Tests

Actual-model workflow runs belong to the Millrace runtime test harness because
the runtime owns package import, plan admission, daemon execution, and durable
state. Package validation checks that assets and selected schemas are coherent;
the runtime's live test guide explains how to execute the full workflows.
