# Vendor Selection Conflict Checker Core Skill

## Stage Contract
Stage ID: `conflict_checker`.
Responsibility: Check selected candidates against the declared conflict rules and catalog conflict status.

Use the runtime-provided `generated_work_source.item_key` to identify the assigned candidate within the full `CandidateBundle` payload. Use only selected `conflict_rules` and the assigned candidate's selected `conflict_status`; do not browse, reconstruct, override, or promote non-authoritative catalog metadata.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`ConflictReport`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `bundle_id` | yes | string; min_length 1 | Selected-schema field. |
| `evaluator_kind` | yes | string const `conflict` | Must be `conflict`. |
| `conflict_findings` | yes | array; min_items 0; items string | Selected-schema array. |
| `clearance_result` | yes | enum [clear, blocked] | Selected value from [clear, blocked]. |

## Marker Artifact Protocol
- CONFLICT_COMPLETE: selected action `vendor_selection.conflict_checker.conflict_complete`; action kind `complete_work_item`; artifact schema `ConflictReport`; emitted queue `none`; target stage `none`.

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
    "terminal_marker": "CONFLICT_COMPLETE",
    "artifact": {
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "evaluator_kind": "conflict",
      "conflict_findings": [],
      "clearance_result": "clear"
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
      "terminal_marker": "CONFLICT_COMPLETE",
      "artifact": {
        "artifact_id": "bad-conflict_checker-wrapper",
        "artifact_kind": "ConflictReport",
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
      "terminal_marker": "CONFLICT_COMPLETE",
      "artifact": {
        "bundle_id": "bundle-e2e-vendor-selection-001",
        "evaluator_kind": "conflict",
        "conflict_findings": []
      }
    }
  },
  {
    "case": "wrong_type",
    "example": {
      "terminal_marker": "CONFLICT_COMPLETE",
      "artifact": {
        "bundle_id": "bundle-e2e-vendor-selection-001",
        "evaluator_kind": "conflict",
        "conflict_findings": "none",
        "clearance_result": "clear"
      }
    }
  }
]
```
Reasons invalid: wrapper keys are undeclared, `clearance_result` is required, and `conflict_findings` must be an array.

## Validation Checklist
- Marker spelling exactly matches the selected marker list above.
- The artifact body matches the schema selected by that marker.
- Required selected fields are present and unsupported artifact fields are absent.
- Evidence and assumptions live in runner evidence/report text unless the selected schema declares them.
- No artifact text claims route, queue, approval, capability, effect, package, provider, purchase, payment, or durable-state behavior by itself.
- No artifact or evidence includes credentials or private contact details.

## Completion Criteria
Return one selected terminal marker with one exact selected artifact JSON object and enough runner evidence/report text for audit.
