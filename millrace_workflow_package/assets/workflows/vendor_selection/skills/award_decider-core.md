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
| `approval_policy_hint` | yes | enum [none, operator_required] | Preserve the selected approval policy when re-sourcing. |
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
- Use joined source bundle `approval_policy_hint`, `conflict_rules`, `candidate_vendors[*].conflict_status`, and exact report provenance as selected evidence.
- `AWARD_READY` is legal only when `approval_policy_hint` is `none` and selected evidence is sufficient. With `operator_required`, return `OPERATOR_REQUIRED` for a viable clear candidate.
- A candidate with selected `conflict_status` of `blocked` is not viable even when rubric prose recommends it.

## Decision Policy Cases
Policy cases:
```json
[
  {
    "case": "operator_required_viable_clear",
    "approval_policy_hint": "operator_required",
    "candidate_id": "vendor_alpha",
    "candidate_conflict_status": "clear",
    "rubric_recommends_candidate": true,
    "selected_evidence_state": "complete",
    "candidate_viable": true,
    "expected_marker": "OPERATOR_REQUIRED",
    "selected_action_id": "vendor_selection.award_decider.operator_required",
    "artifact_schema_id": "AwardDecision",
    "operator_wait_aftermath": "selected_wait",
    "runtime_policy_boundary": "stage_agent_selects_marker_from_selected_evidence; runtime_applies_selected_marker_action_schema_wait"
  },
  {
    "case": "none_viable_clear",
    "approval_policy_hint": "none",
    "candidate_id": "vendor_alpha",
    "candidate_conflict_status": "clear",
    "rubric_recommends_candidate": true,
    "selected_evidence_state": "complete",
    "candidate_viable": true,
    "expected_marker": "AWARD_READY",
    "selected_action_id": "vendor_selection.award_decider.award_ready",
    "artifact_schema_id": "AwardDecision",
    "operator_wait_aftermath": "no_wait",
    "runtime_policy_boundary": "stage_agent_selects_marker_from_selected_evidence; runtime_applies_selected_marker_action_schema_wait"
  },
  {
    "case": "complete_no_viable_evidence",
    "approval_policy_hint": "none",
    "candidate_id": null,
    "candidate_conflict_status": "blocked",
    "rubric_recommends_candidate": false,
    "selected_evidence_state": "complete_no_viable",
    "candidate_viable": false,
    "expected_marker": "NO_VIABLE_VENDOR",
    "selected_action_id": "vendor_selection.award_decider.no_viable_vendor",
    "artifact_schema_id": "DecisionPack",
    "operator_wait_aftermath": "no_wait",
    "runtime_policy_boundary": "stage_agent_selects_marker_from_selected_evidence; runtime_applies_selected_marker_action_schema_wait"
  },
  {
    "case": "missing_or_contradictory_evidence",
    "approval_policy_hint": "operator_required",
    "candidate_id": null,
    "candidate_conflict_status": "missing",
    "rubric_recommends_candidate": false,
    "selected_evidence_state": "missing_or_contradictory",
    "candidate_viable": false,
    "expected_marker": "BLOCKED",
    "selected_action_id": "vendor_selection.award_decider.blocked",
    "artifact_schema_id": "DecisionPack",
    "operator_wait_aftermath": "no_wait",
    "runtime_policy_boundary": "stage_agent_selects_marker_from_selected_evidence; runtime_applies_selected_marker_action_schema_wait"
  },
  {
    "case": "blocked_candidate_rubric_recommended",
    "approval_policy_hint": "operator_required",
    "candidate_id": "vendor_beta",
    "candidate_conflict_status": "blocked",
    "rubric_recommends_candidate": true,
    "selected_evidence_state": "complete",
    "candidate_viable": false,
    "expected_marker": "NO_VIABLE_VENDOR",
    "selected_action_id": "vendor_selection.award_decider.no_viable_vendor",
    "artifact_schema_id": "DecisionPack",
    "operator_wait_aftermath": "no_wait",
    "runtime_policy_boundary": "stage_agent_selects_marker_from_selected_evidence; runtime_applies_selected_marker_action_schema_wait"
  }
]
```
These are adversarial stage-agent guidance cases. They do not define a runtime
vendor-policy engine; selected runtime data remains authority for marker,
action, schema, and wait aftermath.

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
      "approval_policy_hint": "operator_required",
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
      "selected_plan_id": "vendor_selection:0.1",
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
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "blocked"
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
  },
  {
    "case": "missing_required_field",
    "example": {
      "terminal_marker": "OPERATOR_REQUIRED",
      "artifact": {
        "bundle_id": "bundle-e2e-vendor-selection-001",
        "decision_kind": "operator_required",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
          "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
          "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
        },
        "reason": "Selected evidence requires local-operator confirmation."
      }
    }
  },
  {
    "case": "wrong_type",
    "example": {
      "terminal_marker": "OPERATOR_REQUIRED",
      "artifact": {
        "bundle_id": "bundle-e2e-vendor-selection-001",
        "decision_kind": "operator_required",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
          "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
          "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
        },
        "operator_gate_required": "true",
        "reason": "Selected evidence requires local-operator confirmation."
      }
    }
  }
]
```
Reasons invalid: wrapper keys are undeclared, `operator_gate_required` is required, and `operator_gate_required` must be a boolean.

## Validation Checklist
- Marker spelling exactly matches the selected marker list above.
- The artifact body matches the schema selected by that marker.
- Required selected fields are present and unsupported artifact fields are absent.
- Evidence and assumptions live in runner evidence/report text unless the selected schema declares them.
- No artifact text claims route, queue, approval, capability, effect, package, provider, purchase, payment, or durable-state behavior by itself.
- No artifact or evidence includes credentials or private contact details.

## Completion Criteria
Return one selected terminal marker with one exact selected artifact JSON object and enough runner evidence/report text for audit.
