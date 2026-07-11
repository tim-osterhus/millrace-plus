# Vendor Selection Policy Screener Core Skill

## Stage Contract
Stage ID: `policy_screener`.
Responsibility: Screen the selected request against package-local policy facts and preserve the request or close with a policy decision artifact.

## Selected Request-Policy Facts

Apply this closed decision matrix:
The allowed category is `synthetic_office_supplies` and the allowed budget band `low`.
Only category and budget are request-policy gates.

| Request field | Selected package policy | Stage decision |
| --- | --- | --- |
| `category` | `synthetic_office_supplies` is allowed | Preserve it in `PurchaseRequest`. |
| `budget_band` | `low` is allowed | Preserve it in `PurchaseRequest`. |
| `required_capabilities` | Values including `standard_office_supplies` and `net30_invoice` are downstream candidate constraints | Do not policy-screen them or decide whether a vendor satisfies them here. |
| `disallowed_vendors` | Allowed downstream candidate filter | Preserve it; do not require a catalog here. |
| `approval_policy_hint` | `none` and `operator_required` are allowed | `operator_required` requests the later selected operator gate; it is not approval. |

Missing catalog or vendor evidence is not a policy violation. Rubric evidence,
conflict evidence, candidate viability, and operator decisions are owned by
later selected stages and cannot be prerequisites for `POLICY_ALLOWED` here.
Return `POLICY_BLOCKED` only for an explicit request-policy violation against
this matrix. Do not infer a violation from unavailable downstream evidence.

## Artifact Schemas
Selected schemas for this stage. Treat each schema as closed.

`PurchaseRequest`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `request_id` | yes | string; min_length 1 | Selected-schema field. |
| `requester_label` | yes | string; min_length 1 | Selected-schema field. |
| `category` | yes | string; min_length 1 | Selected-schema field. |
| `budget_band` | yes | string; min_length 1 | Selected-schema field. |
| `required_capabilities` | yes | array; min_items 1; items string | Selected-schema array. |
| `disallowed_vendors` | yes | array; min_items 0; items string | Selected-schema array. |
| `approval_policy_hint` | yes | enum [none, operator_required] | Selected value from [none, operator_required]. |

`PolicyDecision`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_request_id` | yes | string; min_length 1 | Selected-schema field. |
| `policy_status` | yes | const `blocked` | Selected policy disposition. |
| `violated_policy_facts` | yes | array; min_items 1; items enum [category_not_permitted, budget_band_not_permitted] | Selected request-policy facts violated by the request. |
| `reason` | yes | string; min_length 1 | Explanation grounded in the selected request and policy facts. |

## Marker Artifact Protocol
- POLICY_ALLOWED: selected action `vendor_selection.policy_screener.policy_allowed`; action kind `route`; artifact schema `PurchaseRequest`; emitted queue `purchase_request`; target stage `requirement_freezer`.
- POLICY_BLOCKED: selected action `vendor_selection.policy_screener.policy_blocked`; action kind `close`; artifact schema `PolicyDecision`; emitted queue `none`; target stage `none`.

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
    "terminal_marker": "POLICY_ALLOWED",
    "artifact": {
      "request_id": "request-office-001",
      "requester_label": "local-operator",
      "category": "synthetic_office_supplies",
      "budget_band": "low",
      "required_capabilities": [
        "standard_office_supplies",
        "net30_invoice"
      ],
      "disallowed_vendors": [
        "Beta Supplies"
      ],
      "approval_policy_hint": "operator_required"
    }
  },
  {
    "terminal_marker": "POLICY_BLOCKED",
    "artifact": {
      "source_request_id": "request-office-001",
      "policy_status": "blocked",
      "violated_policy_facts": [
        "category_not_permitted"
      ],
      "reason": "The request category is outside the selected package policy."
    }
  }
]
```

## Invalid Example
Invalid example:
```json
{
  "terminal_marker": "POLICY_ALLOWED",
  "artifact": {
    "artifact_id": "bad-policy_screener-wrapper",
    "artifact_kind": "PurchaseRequest",
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
- A conforming `synthetic_office_supplies` / `low` request remains allowed when later catalog, vendor, rubric, conflict, or operator evidence is absent.
- `standard_office_supplies`, `net30_invoice`, and `disallowed_vendors` remain downstream candidate constraints.
- `operator_required` is allowed and requests the later selected operator gate; the model does not approve it here.
- `POLICY_BLOCKED` identifies an explicit request-policy violation against the selected facts, not missing downstream evidence.
- Evidence and assumptions live in runner evidence/report text unless the selected schema declares them.
- No artifact text claims route, queue, approval, capability, effect, package, provider, purchase, payment, or durable-state behavior by itself.
- No artifact or evidence includes credentials or private contact details.

## Completion Criteria
Return one selected terminal marker with one exact selected artifact JSON object and enough runner evidence/report text for audit.
