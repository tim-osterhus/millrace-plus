# Vendor Selection Rubric Evaluator Core Skill

## Stage Contract
Stage ID: `rubric_evaluator`.
Responsibility: Score candidate evidence against the selected rubric and produce RubricReport evidence.

## Artifact Schemas
- CandidateBundle: object; required [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]; allowed [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]
  - source_requirement_id: string; min_length 1
  - bundle_id: string; min_length 1
  - candidate_vendors: array; min_items 1; unique_by `candidate_id`; items object; required [candidate_id, vendor_label, capabilities, budget_band, catalog_ref]; allowed [candidate_id, vendor_label, capabilities, budget_band, catalog_ref];     - candidate_id: string; min_length 1;     - vendor_label: string; min_length 1;     - capabilities: array; min_items 1; items string; min_length 1;     - budget_band: string; min_length 1;     - catalog_ref: string; min_length 1
  - deterministic_source_refs: array; min_items 1; items string; min_length 1
- RubricReport: object; required [bundle_id, evaluator_kind, score_table, threshold_result, recommended_candidate_id]; allowed [bundle_id, evaluator_kind, score_table, threshold_result, recommended_candidate_id]
  - bundle_id: string; min_length 1
  - evaluator_kind: const `rubric`
  - score_table: array; min_items 1; unique_by `candidate_id`; items object; required [candidate_id, score]; allowed [candidate_id, score];     - candidate_id: string; min_length 1;     - score: integer
  - threshold_result: enum [pass, fail]
  - recommended_candidate_id: string; min_length 1

## Marker Artifact Protocol
- RUBRIC_COMPLETE: selected action `vendor_selection.rubric_evaluator.rubric_complete`; action kind `complete_work_item`; artifact schema `RubricReport`; emitted queue `none`; target stage `none`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `rubric_evaluator`.
- `source_work_item_id`: copied from dispatch.
- `source_run_id`: copied from dispatch.
- `terminal_marker`: one legal marker rendered for this stage.
- `fields`: schema-compatible artifact fields only.
- `evidence`: selected input checks and selected package references.
- `assumptions`: explicit assumptions, empty when none.
- `next_stage_context`: selected IDs and evidence references for downstream context.

## Valid Example
Valid example:
```json
{
  "artifact_id": "rubric_evaluator-example-001",
  "artifact_kind": "RubricReport",
  "produced_by_stage": "rubric_evaluator",
  "source_work_item_id": "source-work-item-id",
  "source_run_id": "source-run-id",
  "terminal_marker": "RUBRIC_COMPLETE",
  "fields": {
    "bundle_id": "bundle-001",
    "evaluator_kind": "rubric",
    "score_table": [
      {
        "candidate_id": "vendor_alpha",
        "score": 92
      }
    ],
    "threshold_result": "pass",
    "recommended_candidate_id": "vendor_alpha"
  },
  "evidence": [
    "selected input checked",
    "selected package data used"
  ],
  "assumptions": [],
  "next_stage_context": {
    "selected_action_id": "vendor_selection.rubric_evaluator.rubric_complete"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-rubric_evaluator",
  "artifact_kind": "RubricReport",
  "produced_by_stage": "rubric_evaluator",
  "terminal_marker": "RUBRIC_COMPLETE",
  "fields": {"unsupported_field": "invented"},
  "evidence": ["external data was assumed"]
}
```
Reason invalid: it uses an unsupported field, lacks required handoff fields, or depends on unselected data.

## Validation Checklist
- The terminal marker appears in this stage's rendered legal marker list.
- The artifact schema matches the marker protocol above.
- Required schema fields are present and unsupported fields are absent.
- `approval_policy_hint` is preserved as evidence/handoff context when present.
- Evidence references selected package data or dispatch data only.
- The artifact does not claim external service access, private contacts, credentials, remote actions, external catalog searches, provider invocation, purchase actions, or payment actions.
- The artifact does not claim model authority over local operator review.

## Completion Criteria
- Return one selected terminal marker with one schema-compatible artifact.
- Include enough evidence for audit and downstream context.
- Stop without a success marker when required selected evidence is missing or contradictory.
