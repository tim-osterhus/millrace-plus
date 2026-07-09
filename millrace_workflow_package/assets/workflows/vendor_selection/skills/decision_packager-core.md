# Vendor Selection Decision Packager Core Skill

## Stage Contract
Stage ID: `decision_packager`.
Responsibility: Produce the final DecisionPack from selected authorization or decision-pack inputs without external commitments.

## Artifact Schemas
- AwardDecision: object; required [bundle_id, decision_kind, selected_candidate_id, required_evidence_refs, operator_gate_required, reason]; allowed [bundle_id, decision_kind, selected_candidate_id, required_evidence_refs, operator_gate_required, reason]
  - bundle_id: string; min_length 1
  - decision_kind: enum [award, re_source, reject, operator_required, blocked]
  - selected_candidate_id: enum [vendor_alpha, vendor_beta, vendor_gamma, null]
  - required_evidence_refs: object; required [rubric_report_ref, conflict_report_ref]; allowed [rubric_report_ref, conflict_report_ref];   - rubric_report_ref: string; min_length 1;   - conflict_report_ref: string; min_length 1
  - operator_gate_required: boolean
  - reason: string; min_length 1
- OperatorDecision: object; required [gate_id, bundle_id, decision, actor_kind, audit_reason]; allowed [gate_id, bundle_id, decision, actor_kind, audit_reason]
  - gate_id: string; min_length 1
  - bundle_id: string; min_length 1
  - decision: enum [approve, reject]
  - actor_kind: const `local_operator`
  - audit_reason: string; min_length 1
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
- DECISION_PACK_READY: selected action `vendor_selection.decision_packager.decision_pack_ready`; action kind `complete_work_item`; artifact schema `DecisionPack`; emitted queue `none`; target stage `none`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `decision_packager`.
- `source_work_item_id`: copied from dispatch.
- `source_run_id`: copied from dispatch.
- `terminal_marker`: one legal marker rendered for this stage.
- `fields`: schema-compatible artifact fields only.
- `evidence`: selected input checks and selected package references.
- `assumptions`: explicit assumptions, empty when none.
- `next_stage_context`: selected IDs and evidence references for downstream context.


Operator decision input boundary:
- `OperatorDecision` is runtime-owned local-operator resolution payload.
- Treat a runtime-provided `OperatorDecision` as read-only input after recorded wait resolution.
- Preserve `wait_id`, `operator_wait_id`, `actor_kind`, `resolution_kind`, `payload_reference`, and audit evidence when present.
- Do not produce, approve, reject, settle, or fabricate `OperatorDecision`.
## Valid Example
Valid example:
```json
{
  "artifact_id": "decision_packager-example-001",
  "artifact_kind": "DecisionPack",
  "produced_by_stage": "decision_packager",
  "source_work_item_id": "source-work-item-id",
  "source_run_id": "source-run-id",
  "terminal_marker": "DECISION_PACK_READY",
  "fields": {
    "source_request_id": "request-001",
    "bundle_id": "bundle-001",
    "selected_candidate_id": "vendor_alpha",
    "final_refusal_reason": null,
    "evidence_refs": {
      "rubric_report_ref": "rubric-report-001",
      "conflict_report_ref": "conflict-report-001",
      "operator_decision_ref": "operator-decision-001"
    },
    "selected_plan_id": "vendor_selection",
    "selected_plan_fingerprint": "sha256:example-selected-plan-fingerprint",
    "close_reason": "awarded"
  },
  "evidence": [
    "selected input checked",
    "selected package data used"
  ],
  "assumptions": [],
  "next_stage_context": {
    "selected_action_id": "vendor_selection.decision_packager.decision_pack_ready"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-decision_packager",
  "artifact_kind": "DecisionPack",
  "produced_by_stage": "decision_packager",
  "terminal_marker": "DECISION_PACK_READY",
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
