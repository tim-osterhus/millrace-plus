# Vendor Selection Decision Packager Core Skill

## Stage Contract
Stage ID: `decision_packager`.
Responsibility: Package the final selected decision from the exact selected dispatch inputs.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`DecisionPack`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_request_id` | yes | string; min_length 1 | Selected-schema field. |
| `bundle_id` | yes | string; min_length 1 | Selected-schema field. |
| `selected_candidate_id` | yes | enum [vendor_alpha, vendor_beta, vendor_gamma, null] | Selected value from [vendor_alpha, vendor_beta, vendor_gamma, null]. |
| `final_refusal_reason` | yes | enum [policy_blocked, no_viable_vendor, operator_rejected, blocked, null] | Selected value from [policy_blocked, no_viable_vendor, operator_rejected, blocked, null]. |
| `evidence_refs` | yes | object; required [rubric_report_ref, conflict_report_ref]; allowed [rubric_report_ref, conflict_report_ref] | Nested selected-schema object. |
| `selected_plan_id` | yes | string; min_length 1 | Selected-schema field. |
| `selected_plan_fingerprint` | yes | string; min_length 1 | Selected-schema field. |
| `close_reason` | yes | enum [awarded, policy_blocked, no_viable_vendor, operator_rejected, blocked] | Selected value from [awarded, policy_blocked, no_viable_vendor, operator_rejected, blocked]. |

Operator decision boundary:
- Use only one of these three selected input shapes: a complete direct `DecisionPack`, a direct `AwardDecision`, or `OperatorDecision` plus `selected_wait_evidence` containing the source `AwardDecision`.
- Pass a schema-valid direct `DecisionPack` through unchanged. Do not reinterpret it, supplement it, or replace its selected plan identity.
- On the revised path, require both `OperatorDecision.gate_id == selected_wait_evidence.operator_wait_id` and `OperatorDecision.bundle_id == selected_wait_evidence.source_artifact_payload.bundle_id`.
- Copy source request, bundle, candidate, rubric, and conflict provenance only from the source `AwardDecision`. Derive approval or rejection only from the identity-matched `OperatorDecision`. Copy selected plan ID and fingerprint only from runtime-provided dispatch identity.
- Do not fabricate or expand the five-field `OperatorDecision`.
- Do not search a database, filesystem, runtime state, lineage, prior session, or retained evidence. Do not add any operator-decision reference field.


`OperatorDecision` (read-only runtime-provided input for decision packaging; do not fabricate it)

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `gate_id` | yes | string; min_length 1 | Runtime-provided operator decision field. |
| `bundle_id` | yes | string; min_length 1 | Runtime-provided operator decision field. |
| `decision` | yes | enum [approve, reject] | Runtime-provided operator decision field. |
| `actor_kind` | yes | string const `local_operator` | Runtime-provided operator decision field. |
| `audit_reason` | yes | string; min_length 1 | Runtime-provided operator decision field. |

## Normative Decision Mapping
These cases are exhaustive for existing packaged results, direct awards, and revised operator decisions. A semantically mismatched selected pair is blocked and returns `DECISION_PACK_READY` even when the operator disposition says approve. Stop only when no normative mapping can be constructed from structurally valid selected input.

```json
[
  {
    "case": "catalog_no_viable_vendor_pass_through",
    "source_action_id": "vendor_selection.catalog_sourcer.no_viable_vendor",
    "selected_input": "work_item_payload DecisionPack",
    "mapping": "exact_pass_through",
    "terminal_marker": "DECISION_PACK_READY",
    "input_decision_pack": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "no_viable_vendor",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "no_viable_vendor"
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "no_viable_vendor",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "no_viable_vendor"
    }
  },
  {
    "case": "award_no_viable_vendor_pass_through",
    "source_action_id": "vendor_selection.award_decider.no_viable_vendor",
    "selected_input": "work_item_payload DecisionPack",
    "mapping": "exact_pass_through",
    "terminal_marker": "DECISION_PACK_READY",
    "input_decision_pack": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "no_viable_vendor",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "no_viable_vendor"
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "no_viable_vendor",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "no_viable_vendor"
    }
  },
  {
    "case": "award_blocked_pass_through",
    "source_action_id": "vendor_selection.award_decider.blocked",
    "selected_input": "work_item_payload DecisionPack",
    "mapping": "exact_pass_through",
    "terminal_marker": "DECISION_PACK_READY",
    "input_decision_pack": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "blocked",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "blocked"
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "blocked",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "blocked"
    }
  },
  {
    "case": "direct_award",
    "selected_input": "work_item_payload AwardDecision only",
    "source_award": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "decision_kind": "award",
      "selected_candidate_id": "vendor_alpha",
      "required_evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "operator_gate_required": false,
      "reason": "Selected evidence supports the highest clear candidate."
    },
    "selected_wait_evidence": null,
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": "vendor_alpha",
      "final_refusal_reason": null,
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "awarded"
    }
  },
  {
    "case": "revised_approve",
    "selected_input": "work_item_payload OperatorDecision plus selected_wait_evidence source AwardDecision",
    "operator_decision": {
      "gate_id": "vendor_selection.award_operator_wait",
      "bundle_id": "bundle-001",
      "decision": "approve",
      "actor_kind": "local_operator",
      "audit_reason": "Approved after local review."
    },
    "required_identity_match": {
      "operator_gate_id_equals": "selected_wait_evidence.operator_wait_id",
      "operator_bundle_id_equals": "selected_wait_evidence.source_artifact_payload.bundle_id"
    },
    "selected_wait_evidence": {
      "operator_wait_id": "vendor_selection.award_operator_wait",
      "source_artifact_payload": {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "decision_kind": "operator_required",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
          "rubric_report_ref": "rubric-001",
          "conflict_report_ref": "conflict-001"
        },
        "operator_gate_required": true,
        "reason": "Selected evidence requires local-operator confirmation."
      },
      "source_artifact_schema_id": "AwardDecision"
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": "vendor_alpha",
      "final_refusal_reason": null,
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "awarded"
    }
  },
  {
    "case": "revised_reject",
    "selected_input": "work_item_payload OperatorDecision plus selected_wait_evidence source AwardDecision",
    "operator_decision": {
      "gate_id": "vendor_selection.award_operator_wait",
      "bundle_id": "bundle-001",
      "decision": "reject",
      "actor_kind": "local_operator",
      "audit_reason": "Rejected after local review."
    },
    "required_identity_match": {
      "operator_gate_id_equals": "selected_wait_evidence.operator_wait_id",
      "operator_bundle_id_equals": "selected_wait_evidence.source_artifact_payload.bundle_id"
    },
    "selected_wait_evidence": {
      "operator_wait_id": "vendor_selection.award_operator_wait",
      "source_artifact_payload": {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "decision_kind": "operator_required",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
          "rubric_report_ref": "rubric-001",
          "conflict_report_ref": "conflict-001"
        },
        "operator_gate_required": true,
        "reason": "Selected evidence requires local-operator confirmation."
      },
      "source_artifact_schema_id": "AwardDecision"
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "operator_rejected",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "operator_rejected"
    }
  },
  {
    "case": "gate_mismatch",
    "terminal_marker": "DECISION_PACK_READY",
    "mismatch": "OperatorDecision.gate_id differs from selected_wait_evidence.operator_wait_id",
    "operator_decision": {
      "gate_id": "wrong-gate",
      "bundle_id": "bundle-001",
      "decision": "approve",
      "actor_kind": "local_operator",
      "audit_reason": "Submitted against a different gate."
    },
    "selected_wait_evidence": {
      "operator_wait_id": "vendor_selection.award_operator_wait",
      "source_artifact_payload": {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "decision_kind": "operator_required",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
          "rubric_report_ref": "rubric-001",
          "conflict_report_ref": "conflict-001"
        },
        "operator_gate_required": true,
        "reason": "Selected evidence requires local-operator confirmation."
      }
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "blocked",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "blocked"
    }
  },
  {
    "case": "bundle_mismatch",
    "terminal_marker": "DECISION_PACK_READY",
    "mismatch": "OperatorDecision.bundle_id differs from source AwardDecision.bundle_id",
    "operator_decision": {
      "gate_id": "vendor_selection.award_operator_wait",
      "bundle_id": "wrong-bundle",
      "decision": "approve",
      "actor_kind": "local_operator",
      "audit_reason": "Submitted against a different bundle."
    },
    "selected_wait_evidence": {
      "operator_wait_id": "vendor_selection.award_operator_wait",
      "source_artifact_payload": {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "decision_kind": "operator_required",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
          "rubric_report_ref": "rubric-001",
          "conflict_report_ref": "conflict-001"
        },
        "operator_gate_required": true,
        "reason": "Selected evidence requires local-operator confirmation."
      }
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "blocked",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "blocked"
    }
  },
  {
    "case": "source_award_mismatch",
    "terminal_marker": "DECISION_PACK_READY",
    "mismatch": "source AwardDecision is not operator_required with operator_gate_required true",
    "operator_decision": {
      "gate_id": "vendor_selection.award_operator_wait",
      "bundle_id": "bundle-001",
      "decision": "approve",
      "actor_kind": "local_operator",
      "audit_reason": "Submitted against a non-gated source award."
    },
    "selected_wait_evidence": {
      "operator_wait_id": "vendor_selection.award_operator_wait",
      "source_artifact_payload": {
        "source_request_id": "request-001",
        "bundle_id": "bundle-001",
        "decision_kind": "award",
        "selected_candidate_id": "vendor_alpha",
        "required_evidence_refs": {
          "rubric_report_ref": "rubric-001",
          "conflict_report_ref": "conflict-001"
        },
        "operator_gate_required": false,
        "reason": "Selected evidence supports a direct award."
      }
    },
    "result": {
      "source_request_id": "request-001",
      "bundle_id": "bundle-001",
      "selected_candidate_id": null,
      "final_refusal_reason": "blocked",
      "evidence_refs": {
        "rubric_report_ref": "rubric-001",
        "conflict_report_ref": "conflict-001"
      },
      "selected_plan_id": "vendor_selection:0.1",
      "selected_plan_fingerprint": "sha256:selected-plan-fingerprint",
      "close_reason": "blocked"
    }
  }
]
```

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
      "selected_plan_id": "vendor_selection:0.1",
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
