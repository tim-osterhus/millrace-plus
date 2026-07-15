# Public Validation

`millrace-plus` is a data-only workflow package, not a runtime. A direct
`pip install millrace-plus` installs package metadata and data only; it does
not install `millrace-ai` as a transitive dependency. The future `millrace`
meta-package is the intended convenience install path for `millrace-ai` plus
`millrace-plus` if release cutover chooses that dependency policy. The phrase
future `millrace` meta-package refers to that deferred convenience package,
not behavior inside `millrace-plus`.

PLUS-0002.9 is an internal official package boundary handoff, not a public
release guarantee. PLUS-0003.9 records the completed package-readiness handoff
and current live evidence, while final public release still depends on the
documented DOCS/META/CUT gates. See
`docs/PLUS-0003.9-public-release-readiness.md`.

There is no plugin, marketplace, provider, or native-runner behavior available
from this package.

The distribution also contains three advisory agent skills under
`millrace_plus/skills/`. They remain outside the workflow manifest and are not
installed into an agent tool's skill root. Public validation freezes their
exact inventory and content digests and verifies the same bytes in built and
installed artifacts.

## Public standalone validation

Public standalone validation is the public CI boundary. It must run from a
clean checkout without a sibling Millrace runtime checkout and without
`PYTHONPATH`.

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q \
  tests/test_package_metadata.py \
  tests/test_manifest_authoring_policy.py \
  tests/test_official_package_layout_plan.py \
  tests/test_workflow_package_manifest.py \
  tests/test_workflow_package_installed_smoke.py \
  tests/test_public_package_boundary.py \
  tests/test_agent_skill_assets.py
PYTHONDONTWRITEBYTECODE=1 uv build --out-dir /tmp/millrace-plus-build --force-pep517
python3 - <<'PY'
from pathlib import Path
import zipfile
wheel = next(Path('/tmp/millrace-plus-build').glob('*.whl'))
with zipfile.ZipFile(wheel) as archive:
    names = sorted(archive.namelist())
assert not any(name.startswith('millrace/') for name in names)
assert any(name.startswith('millrace_plus/') for name in names)
assert any(name.startswith('millrace_plus/skills/') for name in names)
assert any(name.startswith('millrace_workflow_package/') for name in names)
PY
uv run --no-project --with ruff ruff check src tests
git diff --check
```

The public tests cover package metadata, dependency policy, docs wording,
manifest authoring policy, manifest and asset digests, declared package-path
containment, deterministic data-only archive shape, frozen agent-skill bytes,
wheel contents, installed-wheel package-data discovery, and the public CI
command shape.
Manifest authoring policy is frozen in `docs/manifest-authoring-policy.md`;
public tests recompute its evidence from package bytes without donor workflow
functions or sibling runtime source checkouts.

## Internal conformance evidence

Internal conformance evidence exercises the Millrace WPKG runtime API and
legacy donor evidence. It is skipped unless all opt-in variables are explicit:

- `MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1`
- `MILLRACE_RUNTIME_SOURCE`, pointing at the runtime source checkout `src`
  directory to test against
- `MILLRACE_LEGACY_ASSET_ROOT`, pointing at the legacy asset evidence root

Internal conformance evidence is not public CI and must not be described as a
public release guarantee.
