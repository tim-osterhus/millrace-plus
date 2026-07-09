# Vendor Selection Policy Screener Core Skill

## Stage Contract
Stage ID: `policy_screener`.
Responsibility: Check whether the selected request is policy-allowed using only dispatch payload and selected package data.

## Artifact Schemas
- PurchaseRequest: object; required [request_id, requester_label, category, budget_band, required_capabilities, disallowed_vendors, approval_policy_hint]; allowed [request_id, requester_label, category, budget_band, required_capabilities, disallowed_vendors, approval_policy_hint]
  - request_id: string; min_length 1
  - requester_label: string; min_length 1
  - category: string; min_length 1
  - budget_band: string; min_length 1
  - required_capabilities: array; min_items 1; items string; min_length 1
  - disallowed_vendors: array; min_items 0; items string; min_length 1
  - approval_policy_hint: enum [none, operator_required]
- DecisionPack: object; required [source_request_id, bundle_id, selected_candidate_id, final_refusal_reason, evidence_refs, selected_plan_id, selected_plan_fingerprint, close_reason]; allowed [source_request_id, bundle_id, selected_candidate_id, final_refusal_reason, evidence_refs, selected_plan_id, selected_plan_fingerprint, close_reason]
  - source_request_id: string; min_length 1
  - bundle_id: string; min_length 1
  - selected_candidate_id: enum [vendor_alpha, vendor_beta, vendor_gamma, null]
  - final_refusal_reason: enum [policy_blocked, no_viable_vendor, operator_rejected, blocked, null]
  - evidence_refs: object; required [rubric_report_ref, conflict_report_ref]; allowed [rubric_report_ref, conflict_report_ref, operator_decision_ref];   - rubric_report_ref: string; min_length 1;   - conflict_report_ref: string; min_length 1;   - operator_decision_ref: string; min_length 1
  - selected_plan_id: string; min_length 1
  - selected_plan_fingerprint: string; min_length 1
  - close_reason: enum [awarded, policy_blocked, no_viable_vendor, operator_rejected, blocked]

## Marker Artifact Protocol
- POLICY_ALLOWED: selected action `vendor_selection.policy_screener.policy_allowed`; action kind `route`; artifact schema `PurchaseRequest`; emitted queue `purchase_request`; target stage `requirement_freezer`.
- POLICY_BLOCKED: selected action `vendor_selection.policy_screener.policy_blocked`; action kind `close`; artifact schema `DecisionPack`; emitted queue `none`; target stage `none`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `policy_screener`.
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
  "artifact_id": "policy_screener-example-001",
  "artifact_kind": "PurchaseRequest",
  "produced_by_stage": "policy_screener",
  "source_work_item_id": "source-work-item-id",
  "source_run_id": "source-run-id",
  "terminal_marker": "POLICY_ALLOWED",
  "fields": {
    "request_id": "request-001",
    "requester_label": "local operator",
    "category": "office_supplies",
    "budget_band": "medium",
    "required_capabilities": [
      "standard_office_supplies",
      "net30_invoice"
    ],
    "disallowed_vendors": [
      "vendor_beta"
    ],
    "approval_policy_hint": "operator_required"
  },
  "evidence": [
    "selected input checked",
    "selected package data used"
  ],
  "assumptions": [],
  "next_stage_context": {
    "selected_action_id": "vendor_selection.policy_screener.policy_allowed"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-policy_screener",
  "artifact_kind": "PurchaseRequest",
  "produced_by_stage": "policy_screener",
  "terminal_marker": "POLICY_ALLOWED",
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
