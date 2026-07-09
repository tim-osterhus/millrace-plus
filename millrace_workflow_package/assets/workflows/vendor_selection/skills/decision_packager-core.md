# Vendor Selection Decision Packager Core Skill

## Stage Contract
Stage ID: `decision_packager`.
Responsibility: Package the final selected decision using runtime-provided operator decision input as read-only input when present.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

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

Operator decision boundary:
- Runtime-provided `OperatorDecision` context is read-only input.
- Do not fabricate `OperatorDecision`; package the final decision only from selected upstream artifacts and runtime-provided operator decision evidence when present.


`OperatorDecision` (read-only runtime-provided input for decision packaging; do not fabricate it)

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `gate_id` | yes | string; min_length 1 | Runtime-provided operator decision field. |
| `bundle_id` | yes | string; min_length 1 | Runtime-provided operator decision field. |
| `decision` | yes | enum [approve, reject] | Runtime-provided operator decision field. |
| `actor_kind` | yes | string const `local_operator` | Runtime-provided operator decision field. |
| `audit_reason` | yes | string; min_length 1 | Runtime-provided operator decision field. |

## Marker Artifact Protocol
- DECISION_PACK_READY: selected action `vendor_selection.decision_packager.decision_pack_ready`; action kind `complete_work_item`; artifact schema `DecisionPack`; emitted queue `none`; target stage `none`.

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
    "terminal_marker": "DECISION_PACK_READY",
    "artifact": {
      "source_request_id": "e2e-vendor-selection-001",
      "bundle_id": "bundle-e2e-vendor-selection-001",
      "selected_candidate_id": "vendor_alpha",
      "final_refusal_reason": null,
      "evidence_refs": {
        "rubric_report_ref": "rubric-report-e2e-vendor-selection-001",
        "conflict_report_ref": "conflict-report-e2e-vendor-selection-001"
      },
      "selected_plan_id": "selected-plan-e2e-vendor-selection",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "awarded"
    }
  }
]
```

## Invalid Example
Invalid example:
```json
{
  "terminal_marker": "DECISION_PACK_READY",
  "artifact": {
    "artifact_id": "bad-decision_packager-wrapper",
    "artifact_kind": "DecisionPack",
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
