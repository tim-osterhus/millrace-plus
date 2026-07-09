# Vendor Selection Award Decider Core Skill

## Stage Contract
Stage ID: `award_decider`.
Responsibility: Combine selected candidate, rubric, and conflict evidence into an award, re-source, operator-required, or blocked decision.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`AwardDecision`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `bundle_id` | yes | string; min_length 1 | Selected-schema field. |
| `decision_kind` | yes | enum [award, re_source, reject, operator_required, blocked] | Selected value from [award, re_source, reject, operator_required, blocked]. |
| `selected_candidate_id` | yes | enum [vendor_alpha, vendor_beta, vendor_gamma, null] | Selected value from [vendor_alpha, vendor_beta, vendor_gamma, null]. |
| `required_evidence_refs` | yes | object; required [rubric_report_ref, conflict_report_ref]; allowed [rubric_report_ref, conflict_report_ref] | Nested selected-schema object. |
| `operator_gate_required` | yes | boolean | Selected-schema field. |
| `reason` | yes | string; min_length 1 | Selected-schema field. |

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

Operator wait boundary:
- `vendor_selection.award_operator_wait` is selected runtime authority, not model authority.
- For `OPERATOR_REQUIRED`, produce an exact `AwardDecision` with `decision_kind` set to `operator_required` and `operator_gate_required` set to true.
- Model output cannot resolve the selected local-operator gate.


## Marker Artifact Protocol
- AWARD_READY: selected action `vendor_selection.award_decider.award_ready`; action kind `route`; artifact schema `AwardDecision`; emitted queue `authorization_decision`; target stage `decision_packager`.
- RESOURCE_REQUIRED: selected action `vendor_selection.award_decider.resource_required`; action kind `route`; artifact schema `RequirementPacket`; emitted queue `requirement_packet`; target stage `catalog_sourcer`.
- OPERATOR_REQUIRED: selected action `vendor_selection.award_decider.operator_required`; action kind `operator_wait`; artifact schema `AwardDecision`; emitted queue `none`; target stage `none`.
- NO_VIABLE_VENDOR: selected action `vendor_selection.award_decider.no_viable_vendor`; action kind `route`; artifact schema `DecisionPack`; emitted queue `decision_pack`; target stage `decision_packager`.
- BLOCKED: selected action `vendor_selection.award_decider.blocked`; action kind `route`; artifact schema `DecisionPack`; emitted queue `decision_pack`; target stage `decision_packager`.

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
    "terminal_marker": "AWARD_READY",
    "artifact": {
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "decision_kind": "award",
      "selected_candidate_id": "vendor_alpha",
      "required_evidence_refs": {
        "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
        "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
      },
      "operator_gate_required": false,
      "reason": "Selected evidence supports the highest clear candidate."
    }
  },
  {
    "terminal_marker": "RESOURCE_REQUIRED",
    "artifact": {
      "source_request_id": "e2e-vendor-selection-001",
      "frozen_requirements": [
        "standard_office_supplies",
        "net30_invoice",
        "exclude Beta Supplies"
      ],
      "policy_status": "clarification_required",
      "selection_rubric_id": "rubric-office-supplies-v1",
      "conflict_rules": [
        "exclude blocked conflict status",
        "require invoice capability"
      ],
      "candidate_count_min": 1,
      "candidate_count_max": 3
    }
  },
  {
    "terminal_marker": "OPERATOR_REQUIRED",
    "artifact": {
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "decision_kind": "operator_required",
      "selected_candidate_id": "vendor_alpha",
      "required_evidence_refs": {
        "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
        "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
      },
      "operator_gate_required": true,
      "reason": "Selected evidence requires local-operator confirmation."
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
  },
  {
    "terminal_marker": "BLOCKED",
    "artifact": {
      "source_request_id": "e2e-vendor-selection-001",
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "blocked",
      "evidence_refs": {
        "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
        "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
      },
      "selected_plan_id": "selected-plan-e2e-vendor-selection",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "blocked"
    }
  }
]
```

## Invalid Example
Invalid example:
```json
{
  "terminal_marker": "AWARD_READY",
  "artifact": {
    "artifact_id": "bad-award_decider-wrapper",
    "artifact_kind": "AwardDecision",
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
