# PLUS-0002C Implementation Review

Date: 2026-07-07 HST

Verdict: IMPLEMENTED.

## Summary

`dev/assets/millrace-plus/millrace_workflow_package/` now preserves
`simple_loop` and adds two hosted LAD Execution workflow entries:

- `execution.lad` / `0.1`
- `execution.lad_integrator` / `0.1`

Both workflow records derive selected authority from
`millrace.workflows.lad_execution` donor source data with the top-level donor
`assets` key removed. Package asset IDs are donor-derived. Required assets are
exact per workflow: base Execution selects the seven base entrypoint/core-skill
pairs, and the Integrator variant selects those shared assets plus the
Integrator entrypoint/core-skill pair.

## v0.21 Execution Parity/Exception Matrix

| v0.21 stage | v0.21 entrypoint path | v0.21 stage-core path | package asset ID(s) | owning workflow selector | final disposition label | rewrite/exception note | boundary-lint proof | test/review evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `lad_builder` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_builder.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/builder-core/SKILL.md` | `execution.entrypoints.lad_builder`, `execution.skills.builder_core` | execution.lad, execution.lad_integrator | packaged_rewritten | Re-authored for v0.22 dispatch, selected schema, evidence, and terminal-marker boundaries; no runtime aftermath authority retained in text. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |
| `lad_integrator` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_integrator.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/integrator-core/SKILL.md` | `execution.entrypoints.lad_integrator`, `execution.skills.integrator_core` | execution.lad_integrator | packaged_rewritten | Re-authored as Integrator-only package assets selected by `execution.lad_integrator`; not selected by base `execution.lad`. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |
| `lad_checker` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_checker.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/checker-core/SKILL.md` | `execution.entrypoints.lad_checker`, `execution.skills.checker_core` | execution.lad, execution.lad_integrator | packaged_rewritten | Re-authored for expectations-first QA and fix-contract evidence while selected workflow data owns follow-up behavior. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |
| `lad_fixer` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_fixer.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/fixer-core/SKILL.md` | `execution.entrypoints.lad_fixer`, `execution.skills.fixer_core` | execution.lad, execution.lad_integrator | packaged_rewritten | Re-authored for contract-bound repair evidence and Doublechecker handoff; no retry or stage-order authority retained in text. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |
| `lad_doublechecker` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_doublechecker.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/doublechecker-core/SKILL.md` | `execution.entrypoints.lad_doublechecker`, `execution.skills.doublechecker_core` | execution.lad, execution.lad_integrator | packaged_rewritten | Re-authored for known-gap revalidation and renewed fix evidence while selected workflow data owns repeated-outcome behavior. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |
| `lad_updater` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_updater.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/updater-core/SKILL.md` | `execution.entrypoints.lad_updater`, `execution.skills.updater_core` | execution.lad, execution.lad_integrator | packaged_rewritten | Re-authored for factual informational-surface reconciliation and evidence; generated/runtime-owned surfaces remain outside asset authority. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |
| `lad_troubleshooter` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_troubleshooter.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/troubleshooter-core/SKILL.md` | `execution.entrypoints.lad_troubleshooter`, `execution.skills.troubleshooter_core` | execution.lad, execution.lad_integrator | packaged_rewritten | Re-authored for blocker diagnosis and local recovery evidence; selected workflow data owns recovery policy and aftermath. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |
| `lad_consultant` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/execution/lad_consultant.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/execution/consultant-core/SKILL.md` | `execution.entrypoints.lad_consultant`, `execution.skills.consultant_core` | execution.lad, execution.lad_integrator | packaged_rewritten | Re-authored for continuation-versus-incident evidence; Planning references remain incident/evidence text, not queue authority. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_execution_official_package.py`; this review row |

## Boundary Notes

- Package selected-authority records contain no top-level `assets`.
- Prompt and skill assets describe stage-local evidence, artifact shape,
  assumptions, and marker choice only.
- Artifact-kind lint is asset scoped. Shared Execution assets may mention
  schemas from workflows that select that same asset, while unrelated workflow
  schema leaks are refused.
- Routing, retry thresholds, recovery policy, queue families, closure,
  capability grants, and operator intervention behavior remain in selected
  workflow data from the donor source.
- No base compiler, kernel, substrate, operator, or runtime source was changed.

## RED Baseline

Focused RED command before implementation:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q tests/test_lad_execution_official_package.py
```

Observed result after adding the focused tests and before implementation:
8 failed, 7 passed.

Expected failures covered:

- package manifest still had only `simple_loop`;
- LAD Execution and Integrator package selection could not succeed;
- LAD package assets were absent;
- authority lint did not catch `quarantine` and `approve effects` claims;
- `PLUS-0002C-implementation-review.md` did not exist yet.

## Selected Package Evidence

Current package evidence:

```text
manifest_digest=sha256:2911883692da33038c12588fa165ea4761c85f5777cd786b0c8bd6dd566a04b6

simple_loop fingerprint=sha256:630dc75947c242090a0e27685db83b3211d96b3c46fa062e5c3b2b23868e0c4e

execution.lad fingerprint=sha256:e7a9ce8ceabf261da94211fc27e9f7c74ae4b009ab1c91b81c9c2cc938fb7ebd
execution.lad selected_asset_pins:
  execution.entrypoints.lad_builder sha256:19a156cb712c90a0fbb9b05e615888924a18b2f91c56e28c6139ab6f87728d96
  execution.entrypoints.lad_checker sha256:3e8e6de621e39777dcf29ec541aec64f0dff49f88a1beb2adda6539458312e2e
  execution.entrypoints.lad_consultant sha256:0d0835e20be607f87c0d36dea5efa5590f53fb1fd6925be3e077d7968247944e
  execution.entrypoints.lad_doublechecker sha256:fcc3e335bc80281fabec9a90811cd71de71013aeb38ed7b49a83227f8e08d5b6
  execution.entrypoints.lad_fixer sha256:b4fda612930a57de459286aa2c4e7bf34668398661609178d026e472bc7ff539
  execution.entrypoints.lad_troubleshooter sha256:6323f1c2a0bcc6ed4d3111ff38c1eca3b0415422ad6582559cc5f10b003aa669
  execution.entrypoints.lad_updater sha256:970e595a57836a50ac4bb283bcc89e2723b529f89182308b4ebc07c72d56ae3e
  execution.skills.builder_core sha256:f36388c774a90472973eb76ed7d6299ef4bade7e5ea2803a6a888dc214db17b0
  execution.skills.checker_core sha256:980118b3a7a25af854fd4ec3e713339a90e3ad33cb4c88f43da8c4dace32aea7
  execution.skills.consultant_core sha256:45248383572d967fd7ce82d72fef2e7b66a6ae1873bed1a03164bb40e6f61ac2
  execution.skills.doublechecker_core sha256:ea3d1e139a619851e23542bdbf5d707259fe0fd0f3b044ac43d0ae47224c1bf5
  execution.skills.fixer_core sha256:4d50ae7da3d72a86e199d273ab2177044a93480d1a0fdcd24a50c4f1191cfd44
  execution.skills.troubleshooter_core sha256:752bae8b7588e765af3856189e593ee748c4088ee37da3f7823988e50f6f1cab
  execution.skills.updater_core sha256:6a6995fcfd4113100ddfebbe2991e0e5e6e1c14a28ce6fd90348eafba133e37b

execution.lad_integrator fingerprint=sha256:4d9a7711f6927cfc9474ea5e76b6651096141f9a9b19b53977c2ac5b394ae1a4
execution.lad_integrator adds:
  execution.entrypoints.lad_integrator sha256:3a15bed62a92b3f59de7ae951106ffb986b09bb105e146e9980876765872e527
  execution.skills.integrator_core sha256:05173905835ee94e2ec6f243d3bbdbd35adb9d0d16bdc9061f9df3d85c491564
```

The `simple_loop` fingerprint remains
`sha256:630dc75947c242090a0e27685db83b3211d96b3c46fa062e5c3b2b23868e0c4e`,
matching the PLUS-0002B selected fingerprint.

## Code Diet

Added:

- sixteen LAD package assets under `millrace_workflow_package/assets/workflows/`;
- two manifest workflow entries;
- focused tests for donor identity, selected-authority closure, asset digests,
  selected pins, simple_loop fingerprint stability, authority lint, and the
  parity matrix.
- installed-wheel verification and selection coverage for all three packaged
  workflows.
- asset-scoped artifact-kind lint so a package-wide schema union cannot hide
  workflow leaks.

Reused:

- current `lad_execution.workflow_source()` and
  `lad_execution.integrator_workflow_source()` donor workflow data;
- existing WPKG public selection APIs and package conformance helpers;
- existing `simple_loop` assets and workflow entry.

New public APIs/modules:

- none.

Remaining duplication:

- asset path expectations appear in tests and manifest as visible conformance
  data. No prompt-processing or manifest-generation framework was added.

## Validation

Focused green:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q tests/test_lad_execution_official_package.py
```

Result: 15 passed.

Full package tests:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q
```

Result after remediation: 45 passed.

Ruff:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with ruff ruff check src tests
```

Result: all checks passed.

Build:

```bash
PYTHONDONTWRITEBYTECODE=1 uv build
```

Result: built `dist/millrace_plus-0.0.0.tar.gz` and
`dist/millrace_plus-0.0.0-py3-none-any.whl`.

Whitespace:

```bash
git diff --check
```

Result: passed.

## Concerns

None currently.
