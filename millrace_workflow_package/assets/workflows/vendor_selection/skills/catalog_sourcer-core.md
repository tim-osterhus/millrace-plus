# Vendor Selection Catalog Sourcer Core Skill

## Stage Contract
Stage ID: `catalog_sourcer`.
Responsibility: Use only the selected synthetic catalog in this stage core skill to produce candidate evidence or no-viable-vendor evidence.

## Artifact Schemas
- RequirementPacket: object; required [source_request_id, frozen_requirements, policy_status, selection_rubric_id, conflict_rules, candidate_count_min, candidate_count_max]; allowed [source_request_id, frozen_requirements, policy_status, selection_rubric_id, conflict_rules, candidate_count_min, candidate_count_max]
  - source_request_id: string; min_length 1
  - frozen_requirements: array; min_items 1; items string; min_length 1
  - policy_status: enum [allowed, blocked, clarification_required]
  - selection_rubric_id: string; min_length 1
  - conflict_rules: array; min_items 1; items string; min_length 1
  - candidate_count_min: integer
  - candidate_count_max: integer
- CandidateBundle: object; required [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]; allowed [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]
  - source_requirement_id: string; min_length 1
  - bundle_id: string; min_length 1
  - candidate_vendors: array; min_items 1; unique_by `candidate_id`; items object; required [candidate_id, vendor_label, capabilities, budget_band, catalog_ref]; allowed [candidate_id, vendor_label, capabilities, budget_band, catalog_ref];     - candidate_id: string; min_length 1;     - vendor_label: string; min_length 1;     - capabilities: array; min_items 1; items string; min_length 1;     - budget_band: string; min_length 1;     - catalog_ref: string; min_length 1
  - deterministic_source_refs: array; min_items 1; items string; min_length 1
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
- CANDIDATES_READY: selected action `vendor_selection.catalog_sourcer.candidates_ready`; action kind `route`; artifact schema `CandidateBundle`; emitted queue `candidate_bundle`; target stage `candidate_packager`.
- NO_VIABLE_VENDOR: selected action `vendor_selection.catalog_sourcer.no_viable_vendor`; action kind `route`; artifact schema `DecisionPack`; emitted queue `decision_pack`; target stage `decision_packager`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `catalog_sourcer`.
- `source_work_item_id`: copied from dispatch.
- `source_run_id`: copied from dispatch.
- `terminal_marker`: one legal marker rendered for this stage.
- `fields`: schema-compatible artifact fields only.
- `evidence`: selected input checks and selected package references.
- `assumptions`: explicit assumptions, empty when none.
- `next_stage_context`: selected IDs and evidence references for downstream context.


Selected synthetic catalog authority:
| vendor_id | vendor_label | capabilities | budget_band | conflict_status |
| --- | --- | --- | --- | --- |
| vendor_alpha | Alpha Stationery | standard_office_supplies, net30_invoice | low | clear |
| vendor_beta | Beta Supplies | standard_office_supplies, rush_delivery | medium | blocked |
| vendor_gamma | Gamma Office | standard_office_supplies, net30_invoice, rush_delivery | medium | clear |

Use these rows only. Do not read non-selected donor metadata as stage authority.
## Valid Example
Valid example:
```json
{
  "artifact_id": "catalog_sourcer-example-001",
  "artifact_kind": "CandidateBundle",
  "produced_by_stage": "catalog_sourcer",
  "source_work_item_id": "source-work-item-id",
  "source_run_id": "source-run-id",
  "terminal_marker": "CANDIDATES_READY",
  "fields": {
    "source_requirement_id": "request-001",
    "bundle_id": "bundle-001",
    "candidate_vendors": [
      {
        "candidate_id": "vendor_alpha",
        "vendor_label": "Alpha Stationery",
        "capabilities": [
          "standard_office_supplies",
          "net30_invoice"
        ],
        "budget_band": "low",
        "catalog_ref": "selected_catalog:vendor_alpha"
      }
    ],
    "deterministic_source_refs": [
      "selected_catalog:vendor_alpha"
    ]
  },
  "evidence": [
    "selected input checked",
    "selected package data used"
  ],
  "assumptions": [],
  "next_stage_context": {
    "selected_action_id": "vendor_selection.catalog_sourcer.candidates_ready"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-catalog_sourcer",
  "artifact_kind": "CandidateBundle",
  "produced_by_stage": "catalog_sourcer",
  "terminal_marker": "CANDIDATES_READY",
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
