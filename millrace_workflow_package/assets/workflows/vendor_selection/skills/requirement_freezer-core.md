# Vendor Selection Requirement Freezer Core Skill

## Stage Contract
Stage ID: `requirement_freezer`.
Responsibility: Freeze requirements, rubric reference, conflict rules, candidate bounds, and approval hint context into RequirementPacket evidence.

## Artifact Schemas
- PurchaseRequest: object; required [request_id, requester_label, category, budget_band, required_capabilities, disallowed_vendors, approval_policy_hint]; allowed [request_id, requester_label, category, budget_band, required_capabilities, disallowed_vendors, approval_policy_hint]
  - request_id: string; min_length 1
  - requester_label: string; min_length 1
  - category: string; min_length 1
  - budget_band: string; min_length 1
  - required_capabilities: array; min_items 1; items string; min_length 1
  - disallowed_vendors: array; min_items 0; items string; min_length 1
  - approval_policy_hint: enum [none, operator_required]
- RequirementPacket: object; required [source_request_id, frozen_requirements, policy_status, selection_rubric_id, conflict_rules, candidate_count_min, candidate_count_max]; allowed [source_request_id, frozen_requirements, policy_status, selection_rubric_id, conflict_rules, candidate_count_min, candidate_count_max]
  - source_request_id: string; min_length 1
  - frozen_requirements: array; min_items 1; items string; min_length 1
  - policy_status: enum [allowed, blocked, clarification_required]
  - selection_rubric_id: string; min_length 1
  - conflict_rules: array; min_items 1; items string; min_length 1
  - candidate_count_min: integer
  - candidate_count_max: integer

## Marker Artifact Protocol
- REQUIREMENTS_READY: selected action `vendor_selection.requirement_freezer.requirements_ready`; action kind `route`; artifact schema `RequirementPacket`; emitted queue `requirement_packet`; target stage `catalog_sourcer`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `requirement_freezer`.
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
  "artifact_id": "requirement_freezer-example-001",
  "artifact_kind": "RequirementPacket",
  "produced_by_stage": "requirement_freezer",
  "source_work_item_id": "source-work-item-id",
  "source_run_id": "source-run-id",
  "terminal_marker": "REQUIREMENTS_READY",
  "fields": {
    "source_request_id": "request-001",
    "frozen_requirements": [
      "standard_office_supplies",
      "net30_invoice"
    ],
    "policy_status": "allowed",
    "selection_rubric_id": "standard_vendor_rubric",
    "conflict_rules": [
      "exclude disallowed_vendors",
      "preserve approval_policy_hint"
    ],
    "candidate_count_min": 1,
    "candidate_count_max": 3
  },
  "evidence": [
    "selected input checked",
    "selected package data used"
  ],
  "assumptions": [],
  "next_stage_context": {
    "selected_action_id": "vendor_selection.requirement_freezer.requirements_ready"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-requirement_freezer",
  "artifact_kind": "RequirementPacket",
  "produced_by_stage": "requirement_freezer",
  "terminal_marker": "REQUIREMENTS_READY",
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
