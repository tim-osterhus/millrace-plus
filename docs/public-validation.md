# Validation

Millrace Plus is a data package. Its full validation accepts explicit wheel
files for the exact Millrace Core and Millforge builds under review. Those
wheels are test inputs only; `millrace-plus` remains dependency-free at
runtime. This guide does not require a sibling checkout or a private workspace
layout.

## Standalone Checks

From a standalone Plus checkout, provide absolute paths to the reviewed wheel
files. Use a Core wheel whose source commit and SHA-256 are recorded by the
release owner, and a compatible Millforge wheel:

```bash
export MILLRACE_AI_WHEEL="/absolute/path/to/millrace_ai-0.22.3-py3-none-any.whl"
export MILLFORGE_WHEEL="/absolute/path/to/millforge-0.1.0-py3-none-any.whl"

test -f "$MILLRACE_AI_WHEEL"
test -f "$MILLFORGE_WHEEL"
```

Run the complete suite from the Plus checkout against those installed wheel
inputs. Do not substitute a Core source checkout or inject one with
`PYTHONPATH`:

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

BOUNDARY_ARTIFACT_ROOT="$(mktemp -d)"
PYTHONDONTWRITEBYTECODE=1 \
  uv build --out-dir "$BOUNDARY_ARTIFACT_ROOT" --force-pep517

uv run --no-project --with ruff ruff check src tests
git diff --check
```

The explicit wheel arguments keep this check reproducible across public
checkouts. A local candidate build is not publication evidence.

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
