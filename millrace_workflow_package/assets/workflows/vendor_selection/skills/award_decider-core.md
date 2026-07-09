# Vendor Selection Award Decider Core Skill

## Stage Contract
Stage ID: `award_decider`.
Responsibility: Combine candidate, rubric, and conflict evidence into a selected AwardDecision, RequirementPacket, or DecisionPack artifact.

## Artifact Schemas
- CandidateBundle: object; required [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]; allowed [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]
  - source_requirement_id: string; min_length 1
  - bundle_id: string; min_length 1
  - candidate_vendors: array; min_items 1; unique_by `candidate_id`; items object; required [candidate_id, vendor_label, capabilities, budget_band, catalog_ref]; allowed [candidate_id, vendor_label, capabilities, budget_band, catalog_ref];     - candidate_id: string; min_length 1;     - vendor_label: string; min_length 1;     - capabilities: array; min_items 1; items string; min_length 1;     - budget_band: string; min_length 1;     - catalog_ref: string; min_length 1
  - deterministic_source_refs: array; min_items 1; items string; min_length 1
- RubricReport: object; required [bundle_id, evaluator_kind, score_table, threshold_result, recommended_candidate_id]; allowed [bundle_id, evaluator_kind, score_table, threshold_result, recommended_candidate_id]
  - bundle_id: string; min_length 1
  - evaluator_kind: const `rubric`
  - score_table: array; min_items 1; unique_by `candidate_id`; items object; required [candidate_id, score]; allowed [candidate_id, score];     - candidate_id: string; min_length 1;     - score: integer
  - threshold_result: enum [pass, fail]
  - recommended_candidate_id: string; min_length 1
- ConflictReport: object; required [bundle_id, evaluator_kind, conflict_findings, clearance_result]; allowed [bundle_id, evaluator_kind, conflict_findings, clearance_result]
  - bundle_id: string; min_length 1
  - evaluator_kind: const `conflict`
  - conflict_findings: array; min_items 0; items string; min_length 1
  - clearance_result: enum [clear, blocked]
- AwardDecision: object; required [bundle_id, decision_kind, selected_candidate_id, required_evidence_refs, operator_gate_required, reason]; allowed [bundle_id, decision_kind, selected_candidate_id, required_evidence_refs, operator_gate_required, reason]
  - bundle_id: string; min_length 1
  - decision_kind: enum [award, re_source, reject, operator_required, blocked]
  - selected_candidate_id: enum [vendor_alpha, vendor_beta, vendor_gamma, null]
  - required_evidence_refs: object; required [rubric_report_ref, conflict_report_ref]; allowed [rubric_report_ref, conflict_report_ref];   - rubric_report_ref: string; min_length 1;   - conflict_report_ref: string; min_length 1
  - operator_gate_required: boolean
  - reason: string; min_length 1
- RequirementPacket: object; required [source_request_id, frozen_requirements, policy_status, selection_rubric_id, conflict_rules, candidate_count_min, candidate_count_max]; allowed [source_request_id, frozen_requirements, policy_status, selection_rubric_id, conflict_rules, candidate_count_min, candidate_count_max]
  - source_request_id: string; min_length 1
  - frozen_requirements: array; min_items 1; items string; min_length 1
  - policy_status: enum [allowed, blocked, clarification_required]
  - selection_rubric_id: string; min_length 1
  - conflict_rules: array; min_items 1; items string; min_length 1
  - candidate_count_min: integer
  - candidate_count_max: integer
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
- AWARD_READY: selected action `vendor_selection.award_decider.award_ready`; action kind `route`; artifact schema `AwardDecision`; emitted queue `authorization_decision`; target stage `decision_packager`.
- RESOURCE_REQUIRED: selected action `vendor_selection.award_decider.resource_required`; action kind `route`; artifact schema `RequirementPacket`; emitted queue `requirement_packet`; target stage `catalog_sourcer`.
- OPERATOR_REQUIRED: selected action `vendor_selection.award_decider.operator_required`; action kind `operator_wait`; artifact schema `AwardDecision`; emitted queue `none`; target stage `none`.
- NO_VIABLE_VENDOR: selected action `vendor_selection.award_decider.no_viable_vendor`; action kind `route`; artifact schema `DecisionPack`; emitted queue `decision_pack`; target stage `decision_packager`.
- BLOCKED: selected action `vendor_selection.award_decider.blocked`; action kind `route`; artifact schema `DecisionPack`; emitted queue `decision_pack`; target stage `decision_packager`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `award_decider`.
- `source_work_item_id`: copied from dispatch.
- `source_run_id`: copied from dispatch.
- `terminal_marker`: one legal marker rendered for this stage.
- `fields`: schema-compatible artifact fields only.
- `evidence`: selected input checks and selected package references.
- `assumptions`: explicit assumptions, empty when none.
- `next_stage_context`: selected IDs and evidence references for downstream context.


Operator wait boundary:
- Selected wait `vendor_selection.award_operator_wait` listens to source action `vendor_selection.award_decider.operator_required`.
- `OPERATOR_REQUIRED` requires an `AwardDecision` artifact with `decision_kind` set to `operator_required`, `operator_gate_required` set to true, and evidence explaining why local review is needed.
- Do not produce, approve, reject, settle, or fabricate `OperatorDecision`.
## Valid Example
Valid example:
```json
{
  "artifact_id": "award_decider-example-001",
  "artifact_kind": "AwardDecision",
  "produced_by_stage": "award_decider",
  "source_work_item_id": "source-work-item-id",
  "source_run_id": "source-run-id",
  "terminal_marker": "AWARD_READY",
  "fields": {
    "bundle_id": "bundle-001",
    "decision_kind": "award",
    "selected_candidate_id": "vendor_alpha",
    "required_evidence_refs": {
      "rubric_report_ref": "rubric-report-001",
      "conflict_report_ref": "conflict-report-001"
    },
    "operator_gate_required": false,
    "reason": "selected evidence supports award without pending local approval"
  },
  "evidence": [
    "selected input checked",
    "selected package data used"
  ],
  "assumptions": [],
  "next_stage_context": {
    "selected_action_id": "vendor_selection.award_decider.award_ready"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-award_decider",
  "artifact_kind": "AwardDecision",
  "produced_by_stage": "award_decider",
  "terminal_marker": "AWARD_READY",
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
