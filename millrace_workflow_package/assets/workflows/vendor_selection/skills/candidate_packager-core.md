# Vendor Selection Candidate Packager Core Skill

## Stage Contract
Stage ID: `candidate_packager`.
Responsibility: Package candidate bundle evidence for fanout while preserving source requirement and bundle identity.

## Artifact Schemas
- CandidateBundle: object; required [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]; allowed [source_requirement_id, bundle_id, candidate_vendors, deterministic_source_refs]
  - source_requirement_id: string; min_length 1
  - bundle_id: string; min_length 1
  - candidate_vendors: array; min_items 1; unique_by `candidate_id`; items object; required [candidate_id, vendor_label, capabilities, budget_band, catalog_ref]; allowed [candidate_id, vendor_label, capabilities, budget_band, catalog_ref];     - candidate_id: string; min_length 1;     - vendor_label: string; min_length 1;     - capabilities: array; min_items 1; items string; min_length 1;     - budget_band: string; min_length 1;     - catalog_ref: string; min_length 1
  - deterministic_source_refs: array; min_items 1; items string; min_length 1

## Marker Artifact Protocol
- CANDIDATES_READY: selected action `vendor_selection.candidate_packager.candidates_ready`; action kind `complete_work_item`; artifact schema `CandidateBundle`; emitted queue `none`; target stage `none`.

## Handoff Format
Use this envelope for every artifact:
- `artifact_id`: stable local artifact identifier.
- `artifact_kind`: one selected schema ID declared for this stage.
- `produced_by_stage`: `candidate_packager`.
- `source_work_item_id`: copied from dispatch.
- `source_run_id`: copied from dispatch.
- `terminal_marker`: one legal marker rendered for this stage.
- `fields`: schema-compatible artifact fields only.
- `evidence`: selected input checks and selected package references.
- `assumptions`: explicit assumptions, empty when none.
- `next_stage_context`: selected IDs and evidence references for downstream context.

## Valid Example
Valid example:
```json
{
  "artifact_id": "candidate_packager-example-001",
  "artifact_kind": "CandidateBundle",
  "produced_by_stage": "candidate_packager",
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
    "selected_action_id": "vendor_selection.candidate_packager.candidates_ready"
  }
}
```

## Invalid Example
Invalid example:
```json
{
  "artifact_id": "bad-candidate_packager",
  "artifact_kind": "CandidateBundle",
  "produced_by_stage": "candidate_packager",
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
