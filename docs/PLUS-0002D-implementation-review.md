# PLUS-0002D Implementation Review

Date: 2026-07-07 HST

Verdict: IMPLEMENTED.

## Summary

`dev/assets/millrace-plus/millrace_workflow_package/` now preserves
`simple_loop`, `execution.lad`, and `execution.lad_integrator` and adds one
hosted LAD Planning workflow entry:

- `planning.lad` / `0.1`

The Planning workflow record derives selected authority from
`millrace.workflows.lad_planning.workflow_source()` with the top-level donor
`assets` key removed. Package required assets are derived from the donor
`assets` records. That donor set includes 12 Planning-owned assets plus 14
inherited Execution assets.

Inherited Execution assets reuse the existing PLUS-0002C package asset IDs,
paths, content digests, and bytes. No divergent inherited Execution copies
were needed.

## v0.21 Planning Parity/Exception Matrix

| v0.21 stage | v0.21 entrypoint path | v0.21 stage-core path | package asset ID(s) | owning workflow selector | final disposition label | rewrite/exception note | boundary-lint proof | test/review evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `recon` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/recon.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/recon-core/SKILL.md` | `planning.entrypoints.recon`, `planning.skills.recon_core` | planning.lad | packaged_rewritten | Re-authored for selected dispatch, evidence, generated task/spec artifact guidance, and terminal-marker boundaries; old workspace paths and queue mutation claims are not retained as package authority. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_planning_official_package.py`; this review row |
| `lad_planner` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/lad_planner.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/planner-core/SKILL.md` | `planning.entrypoints.lad_planner`, `planning.skills.planner_core` | planning.lad | packaged_rewritten | Re-authored for spec-readiness evidence and explicit assumptions while selected workflow data owns follow-up behavior. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_planning_official_package.py`; this review row |
| `lad_manager` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/lad_manager.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/manager-core/SKILL.md` | `planning.entrypoints.lad_manager`, `planning.skills.manager_core` | planning.lad | packaged_rewritten | Re-authored for task-card shaping evidence; task-card fanout and selected Execution handoff remain donor workflow data. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_planning_official_package.py`; this review row |
| `lad_mechanic` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/lad_mechanic.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/mechanic-core/SKILL.md` | `planning.entrypoints.lad_mechanic`, `planning.skills.mechanic_core` | planning.lad | packaged_rewritten | Re-authored for narrow recovery evidence; retry thresholds, quarantine, return, and intervention behavior remain selected workflow data. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_planning_official_package.py`; this review row |
| `lad_auditor` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/lad_auditor.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/auditor-core/SKILL.md` | `planning.entrypoints.lad_auditor`, `planning.skills.auditor_core` | planning.lad | packaged_rewritten | Re-authored for incident normalization evidence while selected workflow data owns Planning aftermath and status behavior. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_planning_official_package.py`; this review row |
| `lad_arbiter` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/lad_arbiter.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/arbiter-core/SKILL.md` | `planning.entrypoints.lad_arbiter`, `planning.skills.arbiter_core` | planning.lad | packaged_rewritten | Re-authored for verdict, rubric, and remediation-gap evidence; closure target state, remediation work creation, and suppression behavior remain selected workflow data. | boundary-clean by `conformance.assert_no_runtime_authority_claims` and selected artifact-kind lint | `tests/test_lad_planning_official_package.py`; this review row |
| `contractor_blueprint` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/contractor_blueprint.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/contractor-blueprint-core/SKILL.md` | N/A (not packaged) | planning.blueprint | deferred_post_cutover | Blueprint Planning is explicitly deferred post-cutover by CKPT-0001 unless a later packet packages `planning.blueprint`. | not packaged; no selected package asset text to lint | `tests/test_lad_planning_official_package.py`; this review row |
| `evaluator_blueprint` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/evaluator_blueprint.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/evaluator-blueprint-core/SKILL.md` | N/A (not packaged) | planning.blueprint | deferred_post_cutover | Blueprint Planning is explicitly deferred post-cutover by CKPT-0001 unless a later packet packages `planning.blueprint`. | not packaged; no selected package asset text to lint | `tests/test_lad_planning_official_package.py`; this review row |
| `manager_blueprint` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/manager_blueprint.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/manager-blueprint-core/SKILL.md` | N/A (not packaged) | planning.blueprint | deferred_post_cutover | Blueprint Planning is explicitly deferred post-cutover by CKPT-0001 unless a later packet packages `planning.blueprint`. | not packaged; no selected package asset text to lint | `tests/test_lad_planning_official_package.py`; this review row |
| `mechanic_blueprint` | `dev/source/millrace/src/millrace_ai/assets/entrypoints/planning/mechanic_blueprint.md` | `dev/source/millrace/src/millrace_ai/assets/skills/stage/planning/mechanic-blueprint-core/SKILL.md` | N/A (not packaged) | planning.blueprint | deferred_post_cutover | Blueprint Planning is explicitly deferred post-cutover by CKPT-0001 unless a later packet packages `planning.blueprint`. | not packaged; no selected package asset text to lint | `tests/test_lad_planning_official_package.py`; this review row |

## Boundary Notes

- Package selected-authority records contain no top-level `assets`.
- `workflow_source_with_unselected_catalog()` was inspected only to confirm
  the donor offers unselected catalog data; `unselected_catalog` is excluded
  from shipped selected authority, package assets, and selected fingerprints.
- Prompt and skill assets describe stage-local evidence, artifact shape,
  assumptions, and marker choice only.
- External intake routes for `spec`, `probe`, and `incident`, Planning-to-
  Execution handoff, fanout, recovery, completion, closure, remediation,
  intervention, terminal actions, queue families, and runner bindings remain
  in selected workflow data from the donor source.
- Inherited Execution assets remain selected through existing PLUS-0002C
  package asset records; Planning did not need divergent copies.
- No base compiler, kernel, substrate, operator, or runtime source was
  changed.

## RED Baseline

Focused RED command before implementation:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q tests/test_lad_planning_official_package.py
```

Observed result after adding the focused tests and before implementation:
11 failed, 8 passed.

Expected failures covered:

- `planning.lad` was absent from the package manifest.
- Planning-owned assets and required asset pins were absent.
- package path/archive selection could not select `planning.lad`.
- boundary lint did not yet catch queue-alias and default-router claims.
- package-local `PLUS-0002D-implementation-review.md` did not exist yet.

## Selected Package Evidence

Current package evidence:

```text
manifest_digest=sha256:b1140ff10480bfc25a6716500e5f0ed1342a7b711adf7c810df34886bced3152

simple_loop fingerprint=sha256:630dc75947c242090a0e27685db83b3211d96b3c46fa062e5c3b2b23868e0c4e
execution.lad fingerprint=sha256:e7a9ce8ceabf261da94211fc27e9f7c74ae4b009ab1c91b81c9c2cc938fb7ebd
execution.lad_integrator fingerprint=sha256:4d9a7711f6927cfc9474ea5e76b6651096141f9a9b19b53977c2ac5b394ae1a4

planning.lad fingerprint=sha256:1cfaca9f7720687cbf83dbac89fe754ac006e4e139b3aa3504feee1ff1c8113d
planning.lad required_assets=26
planning.lad inherited_execution_assets=14
planning.lad planning_owned_assets=12
```

The `simple_loop`, `execution.lad`, and `execution.lad_integrator`
fingerprints match the PLUS-0002C selected fingerprints. Planning added only
package-level non-selected metadata and a separate selected workflow entry for
those existing workflows.

## Code Diet

Added:

- one package workflow entry for `planning.lad` / `0.1`;
- 12 Planning-owned package assets under
  `millrace_workflow_package/assets/workflows/planning.lad/`;
- focused tests for donor identity, selected-authority closure, asset digests,
  inherited Execution asset byte reuse, selected pins, path/archive selection,
  installed selection, boundary lint, parity matrix rows, package-local review
  evidence, and existing workflow fingerprint stability;
- package-local implementation review evidence. The lab copy is coordination
  documentation only and is not package-test authority.

Reused:

- current `lad_planning.workflow_source()` donor workflow data;
- existing PLUS-0002C Execution asset records for inherited Execution assets;
- existing WPKG public selection APIs and package conformance helpers.

New public APIs/modules:

- none.

Remaining duplication:

- asset path expectations appear in tests and manifest as visible conformance
  data. No prompt-processing, manifest-generation, or public package framework
  was added.

Intentional simplifications:

- Planning-owned prompt and core-skill assets are concise v0.22 rewrites
  focused on selected dispatch, artifact evidence, and boundary-clean marker
  choice. The v0.21 Blueprint Planning assets are not packaged in this packet.

Cleanup packet:

- none required before PLUS-0002E.

## Validation

Focused green:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q tests/test_lad_planning_official_package.py
```

Result: 19 passed.

Full package tests:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q
```

Result: 64 passed.

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
