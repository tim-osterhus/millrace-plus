# PLUS-0003F Full LAD Librarian Handoff And No-Op Contract

Date: 2026-07-09 HST

Package base commit before this packet: `a631670`

Rewrite source commit used as read-only donor evidence: `1617358`

## Scope

PLUS-0003F is an integrated package/source gate for E2E-0004 Row B. The
package-owned portion repairs the official `millrace-plus` `lad.full`
Librarian no-op contract. The paired source-owned portion, kept in
`dev/source/millrace-rewrite`, updates the Row B E2E classifier and live-smoke
docs so clean success requires a truthful Librarian no-op before Manager close.

This cleanup turn edits only the current PLUS-0003F package files already
modified in `dev/assets/millrace-plus`. It does not edit runtime source,
compiler source, the source donor `lad_learning.py`, lab planning files,
provider code, or source-owned E2E files.

## RED Evidence

Focused RED command:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1 MILLRACE_RUNTIME_SOURCE=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src MILLRACE_LEGACY_ASSET_ROOT=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace/src/millrace_ai/assets PYTHONPATH=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/tests /Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/.venv/bin/python -m pytest -q tests/test_lad_learning_official_package.py
```

Observed result before implementation: `5 failed, 23 passed`.

Expected failures mapped to packet proofs:

- `test_full_lad_authority_matches_donor_plus_librarian_overlay`: package still
  matched donor exactly instead of donor plus reviewed PLUS-0003F overlay.
- `test_librarian_noop_uses_truthful_skill_disposition_schema`: missing
  `learning.artifacts.skill_disposition`.
- `test_librarian_noop_disposition_refuses_invalid_payloads`: no no-op schema
  existed for payload validation.
- `test_librarian_assets_encode_index_unavailable_noop`: Librarian assets still
  treated missing optional index evidence as blocked and no-op still used an
  install-report example.
- `test_full_lad_path_archive_selection_accepts_truthful_librarian_noop_contract`:
  path/archive selected authority did not contain the disposition schema.

The unrelated workflow fingerprint guard passed after correcting its constants
to the compiled-plan fingerprint domain.

QA cleanup RED command:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1 MILLRACE_RUNTIME_SOURCE=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src MILLRACE_LEGACY_ASSET_ROOT=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace/src/millrace_ai/assets PYTHONPATH=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/tests /Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/.venv/bin/python -m pytest -q tests/test_lad_learning_official_package.py::test_librarian_assets_encode_index_unavailable_noop
```

Observed cleanup result before the asset rewrite: `1 failed`. The failure was
the intended shared-helper proof: `conformance.markdown_json_examples` saw only
one invalid example while the test required all five invalid cases.

## Authority Delta

Only `lad.full` selected authority changed:

- Added selected schema `learning.artifacts.skill_disposition` with required
  `artifact_kind`, `summary`, and `disposition`; optional `target_skill_id`;
  disposition enum `already_available`, `no_candidate`, `index_unavailable`.
- Added `learning.artifacts.skill_disposition` to the Librarian stage
  `artifact_schema_ids`.
- Changed only `learning.close_librarian_noop.artifact_schema_id` from
  `learning.artifacts.skill_install_report` to
  `learning.artifacts.skill_disposition`.
- Preserved `learning.close_librarian_complete`,
  `learning.artifacts.skill_install_report`, and
  `learning.effect.librarian.workspace_skill_install_report`.
- Preserved `learning.close_librarian_blocked` and
  `learning.librarian_blocked_wait`.

The source donor remains read-only. Internal conformance now asserts package
selected authority equals donor selected authority plus exactly the reviewed
Librarian overlay above.

The QA cleanup changed only package asset text, asset pins, and digest evidence.
It did not change the selected schema or terminal-action records above.

## Asset Delta

`learning.entrypoints.librarian` now states:

- `request_id`, nonblank `body`, and `root_source` are sufficient required
  Planner-derived request evidence;
- a Planner-authored request body is sufficient Planner context;
- installed-skill and remote-index evidence are optional unless dispatch
  declares or provides them;
- absent optional index evidence selects `LIBRARIAN_NOOP` with disposition
  `index_unavailable`, not `BLOCKED`;
- markers are evidence only and do not route, close, wait, propose effects, or
  mutate state by themselves.

`learning.skills.librarian_core` now documents all three selected schemas,
valid examples for `LIBRARIAN_COMPLETE`, `LIBRARIAN_NOOP`, and `BLOCKED`, and
one parseable Invalid Examples JSON array covering missing `disposition`,
unknown disposition, `installed_path` in no-op, type mismatch, and incomplete
install report.

## Source E2E Integration

The source-owned Row B classifier/doc work lives outside this package repo in
`dev/source/millrace-rewrite`:

- `tests/e2e/test_lad_planning_full_actual_model_smoke.py` names selected close
  actions and requires `plus.lad_full_spec` to record
  `learning.close_librarian_noop` with a
  `learning.artifacts.skill_disposition` artifact before
  `planning.close_manager_complete`.
- The classifier rejects a Librarian no-op with pending Manager close and
  Manager close without the Librarian no-op as clean success.
- An active `learning.librarian_blocked_wait`, runtime refusals, quarantines,
  daemon blocker codes, or status blocker collections remain
  `operator_visible_blocker`.
- Row evidence records stage order, terminal actions, no-op artifact fields,
  active waits, refusal reasons, adapter failure code, selected package
  pin/fingerprint, and durable reload parity.
- `docs/e2e-live-smoke.md` records PLUS-0003F as the completed E2E-0004 Row B
  implementation/live gate after the corrected fresh aggregate rerun.

Offline source validation covers this classifier/doc contract. The corrected
fresh live run with `codex`, `gpt-5.5`, medium reasoning, and a fresh bounded
workspace completed the Row B gate. The first fresh aggregate rerun remains
superseded diagnostic evidence: Row B was clean, but Row C legally took the
Analyst -> Professor -> Curator path and closed with Curator while the harness
still allowed only Analyst no-op. The corrected harness accepts the selected
direct Learning-entry close set. In Row C row evidence, legacy
`terminal_marker` is the entry-stage marker such as `ANALYST_COMPLETE`;
`selected_close_action_id` is the actual selected terminal closure, such as
`learning.close_curator_noop`.

## Digests

- Manifest digest: `sha256:016025a7c9e21606994740974f44528f1b9df4897640438276a24ab73409d39e`
- Manifest file digest: `sha256:09db7bffa65553ad9701d576ca0af28efafc792219a00b59142fa45df8b6e035`
- Package digest: `sha256:6b3877e0452c2786dc3f2e0f43ad2dbfa857aa50c7eeacdb43fb56f20184ab5b`
- Frozen `lad.full@0.1` workflow fingerprint: `sha256:151d0f0d5b70fcb6aadbe155aa844baf03d962ef9c3b557b942d7774fa5e4f9c`
- Compiled `lad.full` authority fingerprint: `sha256:701c5f1b84b02d5beb92d84ce8d040ce09f3e07b423fa4042587743e26eedc74`
- `learning.entrypoints.librarian`: byte length `4041`,
  digest `sha256:07c43a588ea413e092789ff4285827b864106e2b4ab7b1fb5b168cb1569b7a1c`
- `learning.skills.librarian_core`: byte length `7334`,
  digest `sha256:7e5ec3b7541de1ca4cffb5358fa78e3c937ec2ef1b2085a0e0bc89b4f02ed0be`
- `docs/manifest-authoring-policy.md` file digest:
  `sha256:6a8f28ea77e75121a7c833c3fd3a2562a193b7f55cd7b94b4fdd920aba98ff9d`

Unrelated frozen workflow fingerprints stayed unchanged in
`docs/manifest-authoring-policy.md`.

## Validation

Focused internal conformance:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1 MILLRACE_RUNTIME_SOURCE=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src MILLRACE_LEGACY_ASSET_ROOT=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace/src/millrace_ai/assets PYTHONPATH=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/tests /Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/.venv/bin/python -m pytest -q tests/test_lad_learning_official_package.py tests/test_lad_planning_official_package.py tests/test_plus_0002_9_final_conformance.py tests/test_manifest_authoring_policy.py
```

Result: `59 passed`.

Full public tests:

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q
```

Result: `24 passed`.

Source-owned offline E2E classifier/doc contract:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/e2e/test_lad_planning_full_actual_model_smoke.py -m 'not live_model'
```

Result from `dev/source/millrace-rewrite` after the corrected Row C close-set
proofs: `37 passed, 1 deselected`.

Ruff:

```bash
uv run --no-project --with ruff ruff check src tests
```

Result: passed.

Build:

```bash
rm -rf /tmp/millrace-plus-plus0003f-build
PYTHONDONTWRITEBYTECODE=1 uv build --out-dir /tmp/millrace-plus-plus0003f-build --force-pep517
```

Result: built `millrace_plus-0.0.0.tar.gz` and
`millrace_plus-0.0.0-py3-none-any.whl`.

Diff and boundary checks:

- `git -C dev/assets/millrace-plus diff --check`: passed.
- `git -C dev/source/millrace-rewrite diff --check`: passed.
- `git -C dev/source/millrace-rewrite diff --name-only -- src/millrace`: empty.
- `git -C dev/source/millrace diff --name-only -- src/millrace`: empty.
- `rg -n 'lad\.full|librarian|skill_disposition|index_unavailable' dev/source/millrace-rewrite/src/millrace/compiler dev/source/millrace-rewrite/src/millrace/kernel dev/source/millrace-rewrite/src/millrace/substrate dev/source/millrace-rewrite/src/millrace/operator`: no matches.
- `rg -n 'learning\.close_librarian_noop|learning\.artifacts\.skill_disposition|learning\.effect\.librarian' dev/assets/millrace-plus/millrace_workflow_package/manifest.json`: matched only the intended schema, stage closure, effect, and no-op action records.

## Minimality

Implementation diff before this evidence document:

- `docs/manifest-authoring-policy.md`: 5 insertions, 5 deletions.
- `millrace_workflow_package/assets/workflows/lad.full/entrypoints/librarian.md`:
  24 insertions, 16 deletions.
- `millrace_workflow_package/assets/workflows/lad.full/skills/librarian-core.md`:
  82 insertions, 56 deletions.
- `millrace_workflow_package/manifest.json`: 44 insertions, 8 deletions.
- `tests/test_lad_learning_official_package.py`: 375 insertions, 2 deletions.
- `docs/PLUS-0003F-full-lad-librarian-handoff-and-noop-contract.md`: new
  213-line implementation evidence file to track at checkpoint.

No new modules, public APIs, helper registries, providers, remote index
contracts, durable generators, or runtime branches were added. The only
one-off script use was mechanical JSON/digest refresh of committed package
bytes. This implementation note is intentionally a new package-owned file to
track at checkpoint; it is not staged by this cleanup turn.

## Remaining Limits

PLUS-0003F implementation, corrected fresh E2E-0004 live proof, and parent
verification are complete. Final delegated execution review approved with no
blocking or non-blocking gaps.
The corrected live harness root was
`/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-lad-planning-full-plus0003f-final-20260710T081837Z`
with result `1 passed, 37 deselected in 142.55s`. Row B workspace
`/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-lad-planning-full-plus-lad_full_spec-53bc93ea`
closed Planner -> Librarian -> Manager with `learning.close_librarian_noop`
and `planning.close_manager_complete`; the no-op artifact uses
`learning.artifacts.skill_disposition`, disposition `index_unavailable`, target
`workspace-learning-summary`, and no `installed_path`.

Parent final verification found one test-only stale package-byte pin outside
this package note's implementation scope: full source pytest initially returned
`1 failed, 2590 passed, 5 skipped` because
`tests/e2e/test_vendor_selection_actual_model_smoke.py` still expected the
pre-PLUS-0003F global package manifest/file digests. The parent refreshed only
`EXPECTED_PACKAGE_MANIFEST_DIGEST` to
`sha256:016025a7c9e21606994740974f44528f1b9df4897640438276a24ab73409d39e`
and `EXPECTED_MANIFEST_FILE_DIGEST` to
`sha256:09db7bffa65553ad9701d576ca0af28efafc792219a00b59142fa45df8b6e035`;
the focused vendor-selection test passes and the vendor workflow fingerprint
and asset authority did not change. The subsequent full source rerun passed
with `2591 passed, 5 skipped`; final delegated execution review approved with
no gaps.
