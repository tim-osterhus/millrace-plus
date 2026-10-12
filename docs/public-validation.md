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
export MILLRACE_AI_WHEEL="/absolute/path/to/millrace_ai-0.22.4-py3-none-any.whl"
export MILLFORGE_WHEEL="/absolute/path/to/millforge-0.1.1-py3-none-any.whl"

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

Run the complete suite, including the two Git-dependent LAD cases, before
final release; earlier packet exclusions are not permanent exclusions from
this command.

## Private Pi candidate checks

The current private source candidate `0.22.4` targets Core
`>=0.22.4,<0.23` and is checked as data only. The package manifest must
retain the six public workflows and the private `lad.full.pi@0.1` entry
with seventeen selected Pi entrypoints. Each Pi
entrypoint must retain its inherited domain and evidence obligations and carry
the strict three-key final-text rule with an optional generic `runner_report`
string inside the observation candidate. The selected bindings remain
component-free `pi_rpc` with the existing capability declarations and empty
runner-produced `terminal_result_mappings`. Six execution-stage bindings use
nested schema version 6 to map trusted runtime timeouts to their selected
`RUNTIME_FAILURE` recovery actions; the other eleven bindings have no runtime
failure mapping. An agent's marker text and Pi command timeout do not trigger
these mappings. The package version and compatibility range do not establish
schema or runtime support by themselves; qualify against the repaired Core
artifact by exact digest through the wheel-based full-suite command above.

The reviewed Core capacity descriptor and accepted PI-B02 assets are retained.
The predecessor has reviewed partial capacity and scripted text-tool evidence;
its purelib extraction is not ordinary installer proof. Later retained wheel
and sdist installations passed a five-stage scripted-provider coding route on
Darwin and one pinned Ubuntu host. Revalidate the exact final 0.22.4 artifacts;
those predecessor results do not establish actual-model behavior, reviewed
provider accounting, or all seventeen LAD stages.

Historical PI-B01 compatibility evidence used the submitted Core wheel
`millrace_ai-0.22.4.dev3+pi.b01-py3-none-any.whl` and its recorded SHA-256. That
wheel rejected the preserved component-free completion behavior because its
compiler required runner component-pin payload capacity. The rejection is
retained as evidence for the historical candidate and does not describe the
accepted PI-B02 source. Predecessor artifact results do not qualify this
local candidate.

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
