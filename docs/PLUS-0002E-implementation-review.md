# PLUS-0002E Implementation Review

Date: 2026-07-07 HST

Verdict: IMPLEMENTED.

## Summary

`dev/assets/millrace-plus/millrace_workflow_package/` now preserves
`simple_loop`, `execution.lad`, `execution.lad_integrator`, and
`planning.lad` and adds one hosted full LAD workflow entry:

- `lad.full` / `0.1`

The full LAD workflow record derives selected authority from
`millrace.workflows.lad_learning.workflow_source()` with the top-level donor
`assets` key removed. Package required assets are derived from the donor
`assets` records. That donor set includes 8 Learning-owned assets plus 12
Planning-owned assets and 14 inherited Execution assets.

Inherited Planning and Execution assets reuse the existing PLUS-0002C and
PLUS-0002D package asset IDs, paths, content digests, and bytes. No divergent
inherited copies were needed.

## v0.21 Learning Parity/Exception Matrix

| v0.21 stage | v0.21 entrypoint path | v0.21 stage-core path | package asset ID(s) | owning workflow selector | final disposition label | rewrite/exception note | boundary-lint proof | test/review evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `analyst` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/analyst.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/analyst-core/SKILL.md` | `learning.entrypoints.analyst`, `learning.skills.analyst_core` | lad.full | packaged_rewritten | Re-authored for selected dispatch, evidence-backed research packets, no-op rationale, and terminal-marker boundaries; old request-claim and status-persistence wording is not retained as package authority. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_learning_official_package.py`; this review row |
| `professor` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/professor.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/professor-core/SKILL.md` | `learning.entrypoints.professor`, `learning.skills.professor_core` | lad.full | packaged_rewritten | Re-authored for candidate/patch evidence and Curator review points while selected workflow data owns follow-up behavior. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_learning_official_package.py`; this review row |
| `curator` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/curator.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/curator-core/SKILL.md` | `learning.entrypoints.curator`, `learning.skills.curator_core` | lad.full | packaged_rewritten | Re-authored for curation decision evidence and destination clarity; effect/provider proposal declarations remain selected workflow data only. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_learning_official_package.py`; this review row |
| `librarian` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/learning/librarian.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/learning/librarian-core/SKILL.md` | `learning.entrypoints.librarian`, `learning.skills.librarian_core` | lad.full | packaged_rewritten | Re-authored for bounded optional-skill selection evidence; remote index, install, provider, and reconciliation behavior remain outside prompt authority. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_learning_official_package.py`; this review row |

## Boundary Notes

- Package selected-authority records contain no top-level `assets`.
- Learning prompt and skill assets describe stage-local evidence, artifact
  shape, assumptions, and marker choice only.
- Effect declarations, provider refs, capability policy refs, concurrency,
  waits, fanout, routes, terminal actions, recovery, status, and full-LAD
  handoff authority remain in selected workflow data from the donor source.
- Provider refs appear in `lad.full` selected authority and compiled selected
  plan bytes only. They do not appear in asset text or non-authority manifest
  metadata.
- No provider credentials, provider execution code, plugin/MCP code, native
  runner code, or base runtime source changes ship in this package.

## RED Baseline

Focused RED command before implementation:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q tests/test_lad_learning_official_package.py tests/test_official_package_layout_plan.py tests/test_package_metadata.py tests/test_workflow_package_installed_smoke.py tests/test_workflow_package_manifest.py
```

Observed result after adding the focused tests and before implementation:
14 failed, 19 passed.

Expected failures covered:

- `lad.full` was absent from the package manifest.
- Learning-owned assets and required asset pins were absent.
- package path/archive and installed selection could not select `lad.full`.
- package-local `PLUS-0002E-implementation-review.md` did not exist yet.
- README, release notes, manifest metadata, asset count, and sdist review-doc
  expectations still reflected PLUS-0002D.

## Selected Package Evidence

Current package evidence:

```text
manifest_digest=sha256:7566e83f8376de72ef604f90eed7d0e3f28a8b1896fb6f647fcf85cf1168a594

simple_loop fingerprint=sha256:630dc75947c242090a0e27685db83b3211d96b3c46fa062e5c3b2b23868e0c4e
execution.lad fingerprint=sha256:e7a9ce8ceabf261da94211fc27e9f7c74ae4b009ab1c91b81c9c2cc938fb7ebd
execution.lad_integrator fingerprint=sha256:4d9a7711f6927cfc9474ea5e76b6651096141f9a9b19b53977c2ac5b394ae1a4
planning.lad fingerprint=sha256:1cfaca9f7720687cbf83dbac89fe754ac006e4e139b3aa3504feee1ff1c8113d
lad.full fingerprint=sha256:3f0e3f61fc4e2bf56d0937c90ec816d7d8a634ceaa8f1b9b9ebe105c35a44cec

lad.full required_assets=34
lad.full inherited_planning_assets=12
lad.full inherited_execution_assets=14
lad.full learning_owned_assets=8
provider/effect refs are present only in selected_authority and selected plan bytes
```

`lad.full` selected asset pins:

```text
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
learning.entrypoints.analyst sha256:fa5f82bbc70fb577fc9e4ac30ff084b186987dd539d7a68db00420f2a4d1c4b3
learning.entrypoints.curator sha256:215a96936e2ff62ebe70f85abec623389a154afc1598be754af1d516db05b1d2
learning.entrypoints.librarian sha256:e2c607fb0d6c389c3f661fa763917c9ee36fa5c0dfbef45f246703ae0202023c
learning.entrypoints.professor sha256:d3be1952ff0f32bdbe84ac2826e9d239c3879977c4526ed5b466f86091210ec9
learning.skills.analyst_core sha256:40b01f7999c7abe3df70d91955f72339722c6179fbc9ff1aeda229afc1741ba4
learning.skills.curator_core sha256:5a5d0fe3516535e35bb0f9a477ab3de6809c8054b34a470c70eb3ecbbd7a6a5c
learning.skills.librarian_core sha256:69ca888947b4bd9d04036b15965381beeb67541462a1398066870462b05b9a67
learning.skills.professor_core sha256:64e00675ce975e29495eec23ae77f27a6ec159d3a42ebf5edead099d956eec18
planning.entrypoints.lad_arbiter sha256:2070e14b3797fc8c359ee72aa9c1d9c35534684ee21f5f776a66977a1a9809e5
planning.entrypoints.lad_auditor sha256:b4123ed550a882a2f78763609d89aa10cc544e9b52dae108f39d1464eed21652
planning.entrypoints.lad_manager sha256:22e162c4819c9fde2276894c7aa06dbc5a77c2a3c83a622cebd6cfaf3755bc70
planning.entrypoints.lad_mechanic sha256:fc52905a1612bc35ada255c0f79ec3b4f2d2f5b0fd5095a70618763575e78e98
planning.entrypoints.lad_planner sha256:b19bab14716d77c8c47b4a500b09768f9ded919605d674b629776eb78ab01007
planning.entrypoints.recon sha256:ac7b3d4da9abbade7f064a295f5ea7b6f6519f17fec2db5ece92144d56fe9ca9
planning.skills.arbiter_core sha256:92464892cbb0d575374d17b3fadc935abdbda29181d61cfc377d2394cc43ddbc
planning.skills.auditor_core sha256:b549918a3ecfcca98ef7c99bccd2ff2cf2d0e65188b25ed43dbe4a023b723eac
planning.skills.manager_core sha256:e7e7c52292c05283ab42e642b2418264bd91a971b964a7d2afc8e1880b707e43
planning.skills.mechanic_core sha256:23d2a563355368d9e0a75428cec58bf58f595f04e67fb610c1d8a49188e6d2f3
planning.skills.planner_core sha256:1a3b2d525ae027e1864c4f926cf87f47d990eab687133339c5b33671781fb73a
planning.skills.recon_core sha256:2ca33f45fa68a02006563e9fbe17e8fe624621247e2d3c00799606508b75f4a0
```

Existing workflow fingerprint stability is proved by selecting
`simple_loop`, `execution.lad`, `execution.lad_integrator`, and `planning.lad`
from the full package and from a pruned package with the `lad.full` workflow
and Learning-owned assets removed.

## Code Diet

Added:

- one package workflow entry for `lad.full` / `0.1`;
- 8 Learning-owned package assets under
  `millrace_workflow_package/assets/workflows/lad.full/`;
- focused tests for donor identity, selected-authority closure, asset digests,
  inherited Planning/Execution asset byte reuse, selected pins, path/archive
  selection, installed selection, effect/provider data-only boundaries,
  boundary lint, parity matrix rows, package-local review evidence, and
  existing workflow fingerprint stability;
- package-local implementation review evidence. The lab copy is coordination
  documentation only and is not package-test authority.

Reused:

- current `lad_learning.workflow_source()` donor workflow data;
- existing PLUS-0002C Execution asset records and PLUS-0002D Planning asset
  records for inherited assets;
- existing WPKG public selection APIs and package conformance helpers.

New public APIs/modules:

- none.

Remaining duplication:

- asset path expectations appear in tests and manifest as visible conformance
  data. No prompt-processing, provider, manifest-generation, or public package
  framework was added.

Intentional simplifications:

- Learning-owned prompt and core-skill assets are concise v0.22 rewrites
  focused on selected dispatch, artifact evidence, and boundary-clean marker
  choice. Real provider execution, credentials, plugin/MCP execution, native
  runner behavior, source promotion, and remote marketplace behavior remain
  outside this package.

Cleanup packet:

- none required before PLUS-0002F.

## Validation

Focused green:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q tests/test_lad_learning_official_package.py tests/test_official_package_layout_plan.py tests/test_package_metadata.py tests/test_workflow_package_installed_smoke.py tests/test_workflow_package_manifest.py
```

Result: 33 passed.

Full package tests:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q
```

Result: 83 passed.

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
