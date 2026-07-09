# Vendor Selection Catalog Sourcer Core Skill

## Stage Contract
Stage ID: `catalog_sourcer`.
Responsibility: Select viable candidates from the selected package catalog records only.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`CandidateBundle`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_requirement_id` | yes | string; min_length 1 | Selected-schema field. |
| `bundle_id` | yes | string; min_length 1 | Selected-schema field. |
| `candidate_vendors` | yes | array; min_items 1; items object; unique_by `candidate_id` | Selected-schema array. |
| `deterministic_source_refs` | yes | array; min_items 1; items string | Selected-schema array. |

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

## Selected Catalog Records
Use only these package-selected records:

| candidate_id | vendor_label | capabilities | budget_band | conflict_status |
| --- | --- | --- | --- | --- |
| vendor_alpha | Alpha Stationery | standard_office_supplies, net30_invoice | low | clear |
| vendor_beta | Beta Supplies | standard_office_supplies, rush_delivery | medium | blocked |
| vendor_gamma | Gamma Office | standard_office_supplies, net30_invoice, rush_delivery | medium | clear |


## Marker Artifact Protocol
- CANDIDATES_READY: selected action `vendor_selection.catalog_sourcer.candidates_ready`; action kind `route`; artifact schema `CandidateBundle`; emitted queue `candidate_bundle`; target stage `candidate_packager`.
- NO_VIABLE_VENDOR: selected action `vendor_selection.catalog_sourcer.no_viable_vendor`; action kind `route`; artifact schema `DecisionPack`; emitted queue `decision_pack`; target stage `decision_packager`.

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
    "terminal_marker": "CANDIDATES_READY",
    "artifact": {
      "source_requirement_id": "e2e-vendor-selection-001",
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "candidate_vendors": [
        {
          "candidate_id": "vendor_alpha",
          "vendor_label": "Alpha Stationery",
          "capabilities": [
            "standard_office_supplies",
            "net30_invoice"
          ],
          "budget_band": "low",
          "catalog_ref": "selected-catalog:vendor_alpha"
        },
        {
          "candidate_id": "vendor_gamma",
          "vendor_label": "Gamma Office",
          "capabilities": [
            "standard_office_supplies",
            "net30_invoice",
            "rush_delivery"
          ],
          "budget_band": "medium",
          "catalog_ref": "selected-catalog:vendor_gamma"
        }
      ],
      "deterministic_source_refs": [
        "selected-catalog:vendor_alpha",
        "selected-catalog:vendor_gamma"
      ]
    }
  },
  {
    "terminal_marker": "NO_VIABLE_VENDOR",
    "artifact": {
      "source_request_id": "e2e-vendor-selection-001",
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "no_viable_vendor",
      "evidence_refs": {
        "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
        "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
      },
      "selected_plan_id": "selected-plan-e2e-vendor-selection",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "no_viable_vendor"
    }
  }
]
```

## Invalid Example
Invalid example:
```json
{
  "terminal_marker": "CANDIDATES_READY",
  "artifact": {
    "artifact_id": "bad-catalog_sourcer-wrapper",
    "artifact_kind": "CandidateBundle",
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
