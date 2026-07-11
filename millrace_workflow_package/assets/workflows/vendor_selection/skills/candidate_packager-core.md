# Vendor Selection Candidate Packager Core Skill

## Stage Contract
Stage ID: `candidate_packager`.
Responsibility: Package the selected candidate bundle for parallel rubric and conflict checks.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`CandidateBundle`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_requirement_id` | yes | string; min_length 1 | Selected-schema field. |
| `bundle_id` | yes | string; min_length 1 | Selected-schema field. |
| `candidate_vendors` | yes | array; min_items 1; items object; unique_by `candidate_id` | Selected-schema array. |
| `deterministic_source_refs` | yes | array; min_items 1; items string | Selected-schema array. |
| `approval_policy_hint` | yes | enum [none, operator_required] | Preserve the selected bundle approval policy. |
| `conflict_rules` | yes | array; min_items 1; items string | Preserve the selected conflict rules. |

Each `candidate_vendors` item must include `candidate_id`, `vendor_label`, `capabilities`, `budget_band`, `catalog_ref`, and `conflict_status` with enum [clear, blocked]. Preserve the full `candidate_vendors` list; runtime-provided `generated_work_source.item_key` identifies the assigned candidate for each fanout target.

## Marker Artifact Protocol
- CANDIDATES_READY: selected action `vendor_selection.candidate_packager.candidates_ready`; action kind `complete_work_item`; artifact schema `CandidateBundle`; emitted queue `none`; target stage `none`.

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
          "catalog_ref": "selected-catalog:vendor_alpha",
          "conflict_status": "clear"
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
          "catalog_ref": "selected-catalog:vendor_gamma",
          "conflict_status": "clear"
        }
      ],
      "deterministic_source_refs": [
        "selected-catalog:vendor_alpha",
        "selected-catalog:vendor_gamma"
      ],
      "approval_policy_hint": "operator_required",
      "conflict_rules": [
        "exclude blocked conflict status",
        "require invoice capability"
      ]
    }
  }
]
```

## Invalid Example
Invalid examples:
```json
[
  {
    "case": "undeclared_extra_field_wrapper",
    "example": {
      "terminal_marker": "CANDIDATES_READY",
      "artifact": {
        "artifact_id": "bad-candidate_packager-wrapper",
        "artifact_kind": "CandidateBundle",
        "fields": {
          "unsupported_field": "invented"
        },
        "evidence": [
          "external data was assumed"
        ]
      }
    }
  },
  {
    "case": "missing_required_field",
    "example": {
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
            "catalog_ref": "selected-catalog:vendor_alpha",
            "conflict_status": "clear"
          }
        ],
        "approval_policy_hint": "operator_required",
        "conflict_rules": [
          "exclude blocked conflict status"
        ]
      }
    }
  },
  {
    "case": "wrong_type",
    "example": {
      "terminal_marker": "CANDIDATES_READY",
      "artifact": {
        "source_requirement_id": "e2e-vendor-selection-001",
        "bundle_id": "bundle-e2e-vendor-selection-001",
        "candidate_vendors": "vendor_alpha",
        "deterministic_source_refs": [
          "selected-catalog:vendor_alpha"
        ],
        "approval_policy_hint": "operator_required",
        "conflict_rules": [
          "exclude blocked conflict status"
        ]
      }
    }
  }
]
```
Reasons invalid: wrapper keys are undeclared, `deterministic_source_refs` is required, and `candidate_vendors` must be an array.

## Validation Checklist
- Marker spelling exactly matches the selected marker list above.
- The artifact body matches the schema selected by that marker.
- Required selected fields are present and unsupported artifact fields are absent.
- Evidence and assumptions live in runner evidence/report text unless the selected schema declares them.
- No artifact text claims route, queue, approval, capability, effect, package, provider, purchase, payment, or durable-state behavior by itself.
- No artifact or evidence includes credentials or private contact details.

## Completion Criteria
Return one selected terminal marker with one exact selected artifact JSON object and enough runner evidence/report text for audit.
