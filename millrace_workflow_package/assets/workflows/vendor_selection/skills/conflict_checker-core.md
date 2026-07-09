# Vendor Selection Conflict Checker Core Skill

## Stage Contract
Stage ID: `conflict_checker`.
Responsibility: Check selected conflict rules and produce ConflictReport evidence.

## Artifact Schemas
- CandidateBundle: object; required [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]; allowed [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]
  - source_requirement_id: string; min_length 1
  - bundle_id: string; min_length 1
  - candidate_vendors: array; min_items 1; unique_by `candidate_id`; items object; required [candidate_id, vendor_label, capabilities, budget_band, catalog_ref]; allowed [candidate_id, vendor_label, capabilities, budget_band, catalog_ref];     - candidate_id: string; min_length 1;     - vendor_label: string; min_length 1;     - capabilities: array; min_items 1; items string; min_length 1;     - budget_band: string; min_length 1;     - catalog_ref: string; min_length 1
  - deterministic_source_refs: array; min_items 1; items string; min_length 1
- ConflictReport: object; required [bundle_id, evaluator_kind, conflict_findings, clearance_result]; allowed [bundle_id, evaluator_kind, conflict_findings, clearance_result]
  - bundle_id: string; min_length 1
  - evaluator_kind: const `conflict`
  - conflict_findings: array; min_items 0; items string; min_length 1
  - clearance_result: enum [clear, blocked]

## Marker Artifact Protocol
- CONFLICT_COMPLETE: selected action `vendor_selection.conflict_checker.conflict_complete`; action kind `complete_work_item`; artifact schema `ConflictReport`; emitted queue `none`; target stage `none`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `conflict_checker`.
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
  "artifact_id": "conflict_checker-example-001",
  "artifact_kind": "ConflictReport",
  "produced_by_stage": "conflict_checker",
  "source_work_item_id": "source-work-item-id",
  "source_run_id": "source-run-id",
  "terminal_marker": "CONFLICT_COMPLETE",
  "fields": {
    "bundle_id": "bundle-001",
    "evaluator_kind": "conflict",
    "conflict_findings": [],
    "clearance_result": "clear"
  },
  "evidence": [
    "selected input checked",
    "selected package data used"
  ],
  "assumptions": [],
  "next_stage_context": {
    "selected_action_id": "vendor_selection.conflict_checker.conflict_complete"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-conflict_checker",
  "artifact_kind": "ConflictReport",
  "produced_by_stage": "conflict_checker",
  "terminal_marker": "CONFLICT_COMPLETE",
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
