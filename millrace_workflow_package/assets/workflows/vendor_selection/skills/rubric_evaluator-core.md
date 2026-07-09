# Vendor Selection Rubric Evaluator Core Skill

## Stage Contract
Stage ID: `rubric_evaluator`.
Responsibility: Score selected candidates against the frozen rubric.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`RubricReport`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `bundle_id` | yes | string; min_length 1 | Selected-schema field. |
| `evaluator_kind` | yes | string const `rubric` | Must be `rubric`. |
| `score_table` | yes | array; min_items 1; items object; unique_by `candidate_id` | Selected-schema array. |
| `threshold_result` | yes | enum [pass, fail] | Selected value from [pass, fail]. |
| `recommended_candidate_id` | yes | string; min_length 1 | Selected-schema field. |

## Marker Artifact Protocol
- RUBRIC_COMPLETE: selected action `vendor_selection.rubric_evaluator.rubric_complete`; action kind `complete_work_item`; artifact schema `RubricReport`; emitted queue `none`; target stage `none`.

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
    "terminal_marker": "RUBRIC_COMPLETE",
    "artifact": {
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "evaluator_kind": "rubric",
      "score_table": [
        {
          "candidate_id": "vendor_alpha",
          "score": 95
        },
        {
          "candidate_id": "vendor_gamma",
          "score": 82
        }
      ],
      "threshold_result": "pass",
      "recommended_candidate_id": "vendor_alpha"
    }
  }
]
```

## Invalid Example
Invalid example:
```json
{
  "terminal_marker": "RUBRIC_COMPLETE",
  "artifact": {
    "artifact_id": "bad-rubric_evaluator-wrapper",
    "artifact_kind": "RubricReport",
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
