# Vendor Selection Requirement Freezer Core Skill

## Stage Contract
Stage ID: `requirement_freezer`.
Responsibility: Freeze the selected request into stable requirements and a bounded deterministic evaluation rubric.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`RequirementPacket`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_request_id` | yes | string; min_length 1 | Selected-schema field. |
| `frozen_requirements` | yes | array; min_items 1; items string | Selected-schema array. |
| `policy_status` | yes | enum [allowed, blocked, clarification_required] | Selected value from [allowed, blocked, clarification_required]. |
| `selection_rubric_id` | yes | string; min_length 1 | Selected-schema field. |
| `conflict_rules` | yes | array; min_items 1; items string | Selected-schema array. |
| `candidate_count_min` | yes | integer | Selected-schema field. |
| `candidate_count_max` | yes | integer | Selected-schema field. |

## Marker Artifact Protocol
- REQUIREMENTS_READY: selected action `vendor_selection.requirement_freezer.requirements_ready`; action kind `route`; artifact schema `RequirementPacket`; emitted queue `requirement_packet`; target stage `catalog_sourcer`.

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
    "terminal_marker": "REQUIREMENTS_READY",
    "artifact": {
      "source_request_id": "e2e-vendor-selection-001",
      "frozen_requirements": [
        "standard_office_supplies",
        "net30_invoice",
        "exclude Beta Supplies"
      ],
      "policy_status": "allowed",
      "selection_rubric_id": "rubric-office-supplies-v1",
      "conflict_rules": [
        "exclude blocked conflict status",
        "require invoice capability"
      ],
      "candidate_count_min": 1,
      "candidate_count_max": 3
    }
  }
]
```

## Invalid Example
Invalid example:
```json
{
  "terminal_marker": "REQUIREMENTS_READY",
  "artifact": {
    "artifact_id": "bad-requirement_freezer-wrapper",
    "artifact_kind": "RequirementPacket",
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
