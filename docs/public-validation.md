# Validation

Millrace Plus is a data package. Its full validation uses locally built wheels
from the exact Millrace and Millforge checkouts under review. Those wheels are
test inputs only; `millrace-plus` remains dependency-free at runtime.

## Standalone Checks

From the workspace root, build one wheel from each exact checkout into a fresh
temporary directory:

```bash
BOUNDARY_ARTIFACT_ROOT="$(mktemp -d)"

uv build --wheel --out-dir "$BOUNDARY_ARTIFACT_ROOT/millrace" \
  dev/source/millrace

uv build --wheel --out-dir "$BOUNDARY_ARTIFACT_ROOT/millforge" \
  dev/harness/millforge

MILLRACE_AI_WHEEL="$(find "$BOUNDARY_ARTIFACT_ROOT/millrace" \
  -maxdepth 1 -type f -name '*.whl' -print)"
MILLFORGE_WHEEL="$(find "$BOUNDARY_ARTIFACT_ROOT/millforge" \
  -maxdepth 1 -type f -name '*.whl' -print)"
test -f "$MILLRACE_AI_WHEEL"
test -f "$MILLFORGE_WHEEL"
```

Then run the complete suite from `dev/assets/millrace-plus/` against those
installed artifacts, with no source checkout on the import path:

```bash
env -u PYTHONPATH \
  -u MILLRACE_RUNTIME_SOURCE \
  -u MILLRACE_LEGACY_ASSET_ROOT \
  -u MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  uv run --no-project \
    --with pytest \
    --with hatchling \
    --with "$MILLRACE_AI_WHEEL" \
    --with "$MILLFORGE_WHEEL" \
    pytest -q

PYTHONDONTWRITEBYTECODE=1 \
  uv build --out-dir "$BOUNDARY_ARTIFACT_ROOT/millrace-plus" --force-pep517

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

## Live Workflow Tests

Actual-model workflow runs belong to the Millrace runtime test harness because
the runtime owns package import, plan admission, daemon execution, and durable
state. Package validation checks that assets and selected schemas are coherent;
the runtime's live test guide explains how to execute the full workflows.
