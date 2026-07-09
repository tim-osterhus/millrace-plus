# PLUS-0003E Live Schema Output Remediation

Date: 2026-07-09 HST

Package base commit before this packet: `5fc75f9`

Rewrite source base commit used for live reruns: `a79e1d0`

## Scope

PLUS-0003E remediated package-authored prompt and core-skill assets after
live E2E found selected-schema output failures in full LAD and
`vendor_selection`.

The fix stayed package-side. Runtime/compiler validation remains strict, and
generic evidence envelopes are still invalid as selected artifact bodies unless
a selected schema explicitly declares those fields.

## Asset Changes

- `vendor_selection` entrypoints and core skills now tell stages to emit the
  exact selected artifact JSON object for each terminal marker.
- `request_intake` now gives `REQUEST_READY` as an exact `PurchaseRequest`
  object, not an object wrapped in `artifact_id`, `fields`, `evidence`, or
  `next_stage_context`.
- `award_decider` now gives `OPERATOR_REQUIRED` as an exact `AwardDecision`
  object and states that model output cannot resolve the selected local
  operator gate.
- Planning assets now distinguish runtime artifact payloads from runner
  evidence and explicitly cover the full-LAD `learning_requests` fanout case.
- Planning Manager assets now use the exact selected `planning.artifacts.task_cards`
  shape rather than undeclared task-card metadata fields.
- Recon assets now use selected `RECON_TO_PLANNING` with
  `planning.artifacts.generated_spec` instead of a non-selected
  `RECON_COMPLETE` stage-result example.
- Arbiter assets now use selected `planning.artifacts.verdict` for
  `ARBITER_COMPLETE` instead of a stage-result example.
- Learning assets now show `ANALYST_COMPLETE` as an exact
  `learning.artifacts.research_packet` and move learning context fields into
  runner evidence/report text.

## Test Coverage Added

- Package conformance helpers validate marker examples with runtime
  `validate_schema`, including `unique_by`, and reject generic
  artifact-envelope fields.
- Package tests include a `unique_by` duplicate regression proving the helper
  no longer accepts payloads runtime admission would reject.
- `vendor_selection` tests assert every core-skill valid marker example
  matches the selected schema and that wrapper-as-artifact canaries fail.
- Planning tests assert `PLANNER_COMPLETE` artifact and observation payloads
  match selected schemas, and that generic fanout metadata is rejected.
- Planning tests assert `MANAGER_COMPLETE` uses the exact selected task-card
  artifact shape.
- Planning tests now loop every changed Planning core-skill valid example
  through selected marker/schema validation.
- Full LAD tests assert the Analyst research packet shape and the planner
  full-LAD fanout example with `learning_requests`.
- Full LAD tests now loop every changed Learning core-skill valid example
  through selected marker/schema validation.

## Current Package Digests

- Manifest digest:
  `sha256:846fb4c436978dd0d199836c10409513fb499ee57843db4dcc26e3b173cf7d8d`
- Manifest file digest:
  `sha256:2c532d2d974e6c4bf1c1d658f547ef6a451533ffc8a1dcc0b613f83849e811ad`
- Exported archive digest from the current package tree:
  `sha256:4e3fdf9f7c3a0bbf168f9af5e784d8efa7348fd4474e135eda597f32f68457db`

## Validation

Package focused validation:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE=1 MILLRACE_RUNTIME_SOURCE=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src MILLRACE_LEGACY_ASSET_ROOT=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace/src/millrace_ai/assets PYTHONPATH=/Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/src:/Users/timinator/Desktop/Millrace-Dev/dev/assets/millrace-plus/tests /Users/timinator/Desktop/Millrace-Dev/dev/source/millrace-rewrite/.venv/bin/python -m pytest -q tests/test_vendor_selection_official_package.py tests/test_lad_planning_official_package.py tests/test_lad_learning_official_package.py tests/test_plus_0002_9_final_conformance.py tests/test_manifest_authoring_policy.py
```

Result: `69 passed`.

Rewrite offline E2E guardrails:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/e2e/test_lad_planning_full_actual_model_smoke.py tests/e2e/test_vendor_selection_actual_model_smoke.py -m 'not live_model'
```

Result: `43 passed, 2 deselected`.

## Live Rerun Evidence

Fresh live rerun roots:

- `/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-lad-planning-full-plus0003e-rerun5-20260709T175116Z`
- `/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-vendor-selection-plus0003e-rerun5-20260709T175116Z`

Live commands used selected `codex` runner authority with
`MILLRACE_LIVE_CODEX_MODEL=gpt-5.5` and
`MILLRACE_LIVE_CODEX_REASONING_EFFORT=medium`.

E2E-0004 live harness result: `1 passed, 22 deselected`.

E2E-0005 live harness result: `1 passed, 21 deselected`.

Row outcomes:

- E2E-0004 Row A, `planning.lad` / `spec`:
  `/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-lad-planning-full-plus-planning_lad-cecf8dc2`
  classified `closed_successfully`.
- E2E-0004 Row B, `lad.full` / `spec`:
  `/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-lad-planning-full-plus-lad_full_spec-e4e7d752`
  classified `operator_visible_blocker`. The old PLUS-0003E blocker is gone:
  it no longer stops on `invalid_fanout_payload` from
  `planning.route_planner_complete`. The new later blocker is
  `ready_state_refused` with diagnostic `operator_wait_active` after two
  successful units.
- E2E-0004 Row C, `lad.full` / `learning_request`:
  `/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-lad-planning-full-plus-lad_full_learning_request-870fa30c`
  classified `closed_successfully`. It no longer stops on
  `invalid_artifact_payload` from `learning.route_analyst_complete`.
- E2E-0005, `vendor_selection` / `purchase_request`:
  `/Users/timinator/Desktop/Millrace-Dev/workspaces/e2e-vendor-selection-plus0003e-rerun5-20260709T175116Z/e2e-vendor-selection-workspace`
  classified `prompt_or_asset_quality_blocker`. The old PLUS-0003E blocker is
  gone: `request_intake` emitted the exact selected `PurchaseRequest` body,
  not a generic envelope. The daemon reported `units_started=5`,
  `units_succeeded=5`, `units_refused=0`, `adapter_failures=0`, and stopped at
  `max_ticks` with `no_ready_work`.

## Remaining Work

PLUS-0003E should be treated as closed for the known selected-schema output
blockers only.

Do not claim clean live end-to-end completion for the remaining rows:

- E2E-0004 Row B now needs follow-up analysis of why the later full-LAD path
  reaches an `operator_wait_active` ready-state refusal.
- E2E-0005 now needs follow-up package/workflow quality work for the later
  vendor path that reaches `no_ready_work` after five successful units without
  durable selected operator-wait evidence.

Those are post-PLUS-0003E blockers because the exact schema-output refusals
that motivated PLUS-0003E no longer reproduce.
