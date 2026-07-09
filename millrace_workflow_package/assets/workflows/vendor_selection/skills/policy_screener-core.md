# Vendor Selection Policy Screener Core Skill

## Stage Contract
Stage ID: `policy_screener`.
Responsibility: Screen the selected request against package-local policy facts and preserve the request or close with a policy decision pack.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`PurchaseRequest`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `request_id` | yes | string; min_length 1 | Selected-schema field. |
| `requester_label` | yes | string; min_length 1 | Selected-schema field. |
| `category` | yes | string; min_length 1 | Selected-schema field. |
| `budget_band` | yes | string; min_length 1 | Selected-schema field. |
| `required_capabilities` | yes | array; min_items 1; items string | Selected-schema array. |
| `disallowed_vendors` | yes | array; min_items 0; items string | Selected-schema array. |
| `approval_policy_hint` | yes | enum [none, operator_required] | Selected value from [none, operator_required]. |

`DecisionPack`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_request_id` | yes | string; min_length 1 | Selected-schema field. |
| `bundle_id` | yes | string; min_length 1 | Selected-schema field. |
| `selected_candidate_id` | yes | enum [vendor_alpha, vendor_beta, vendor_gamma, null] | Selected value from [vendor_alpha, vendor_beta, vendor_gamma, null]. |
| `final_refusal_reason` | yes | enum [policy_blocked, no_viable_vendor, operator_rejected, blocked, null] | Selected value from [policy_blocked, no_viable_vendor, operator_rejected, blocked, null]. |
| `evidence_refs` | yes | object; required [rubric_report_ref, conflict_report_ref]; allowed [rubric_report_ref, conflict_report_ref, operator_decision_ref] | Nested selected-schema object. |
| `selected_plan_id` | yes | string; min_length 1 | Selected-schema field. |
| `selected_plan_fingerprint` | yes | string; min_length 1 | Selected-schema field. |
| `close_reason` | yes | enum [awarded, policy_blocked, no_viable_vendor, operator_rejected, blocked] | Selected value from [awarded, policy_blocked, no_viable_vendor, operator_rejected, blocked]. |

## Marker Artifact Protocol
- POLICY_ALLOWED: selected action `vendor_selection.policy_screener.policy_allowed`; action kind `route`; artifact schema `PurchaseRequest`; emitted queue `purchase_request`; target stage `requirement_freezer`.
- POLICY_BLOCKED: selected action `vendor_selection.policy_screener.policy_blocked`; action kind `close`; artifact schema `DecisionPack`; emitted queue `none`; target stage `none`.

## Handoff Format
Return:
1. `terminal_marker`: one legal marker rendered for this stage.
2. `artifact`: the exact selected artifact JSON object for that marker.
3. Runner evidence/report text for selected checks, assumptions, dispatch IDs, package pins, and downstream context that are not selected artifact fields.

Do not use a generic artifact envelope as the artifact body. Fields such as identity, source IDs, evidence, assumptions, selected action IDs, or downstream context are runner evidence/report facts unless the selected schema declares them.

## Valid Example
Valid examples:
```json
[
  {
    "terminal_marker": "POLICY_ALLOWED",
    "artifact": {
      "request_id": "e2e-vendor-selection-001",
      "requester_label": "local-e2e-operator",
      "category": "synthetic_office_supplies",
      "budget_band": "low",
      "required_capabilities": [
        "standard_office_supplies",
        "net30_invoice"
      ],
      "disallowed_vendors": [
        "Beta Supplies"
      ],
      "approval_policy_hint": "operator_required"
    }
  },
  {
    "terminal_marker": "POLICY_BLOCKED",
    "artifact": {
      "source_request_id": "e2e-vendor-selection-001",
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "policy_blocked",
      "evidence_refs": {
        "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
        "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
      },
      "selected_plan_id": "selected-plan-e2e-vendor-selection",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "policy_blocked"
    }
  }
]
```

## Invalid Example
Invalid example:
```json
{
  "terminal_marker": "POLICY_ALLOWED",
  "artifact": {
    "artifact_id": "bad-policy_screener-wrapper",
    "artifact_kind": "PurchaseRequest",
    "fields": {
      "unsupported_field": "invented"
    },
    "evidence": [
      "external data was assumed"
    ]
  }
}
```
Reason invalid: `artifact` is a generic wrapper-as-artifact body. The selected schema requires the artifact body itself, with no undeclared wrapper keys.

## Validation Checklist
- Marker spelling exactly matches the selected marker list above.
- The artifact body matches the schema selected by that marker.
- Required selected fields are present and unsupported artifact fields are absent.
- Evidence and assumptions live in runner evidence/report text unless the selected schema declares them.
- No artifact text claims route, queue, approval, capability, effect, package, provider, purchase, payment, or durable-state behavior by itself.
- No artifact or evidence includes credentials or private contact details.

## Completion Criteria
Return one selected terminal marker with one exact selected artifact JSON object and enough runner evidence/report text for audit.
