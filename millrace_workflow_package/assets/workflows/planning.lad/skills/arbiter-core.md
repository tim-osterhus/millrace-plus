---
name: planning-arbiter-core
description: Use when executing the lad_arbiter stage for the selected Millrace planning workflow.
---

# LAD Arbiter Core Skill

## Selected artifact

Return exactly one `planning.artifacts.verdict` object for
`ARBITER_COMPLETE`, `REMEDIATION_NEEDED`, or `BLOCKED`. The selected schema is
strict: every required field must be present, all IDs must be unique where
declared, undeclared properties are invalid in nested objects, and the stage
must not return a second artifact. `artifact_kind` must be exactly
`planning.artifacts.verdict`.

Required top-level fields:

- artifact_kind
- summary
- closure_target_id
- root_contract_digest
- freshness_anchor_digest
- rubric
- criterion_results
- observations
- remediation_guidance
- confidence
- residual_uncertainty

The rubric has one required criteria array. Each criterion has only
criterion_id, requirement, and evidence_rule; criteria are non-empty and
unique by criterion_id. Each criterion result has only criterion_id, status,
provenance, and evidence_refs. Status is passed, failed, or blocked.
Provenance is fresh, revalidated, historical_only, or missing. Evidence
references require evidence_id and summary and are unique by evidence_id.
Evidence references may be empty only for missing provenance.

Observations have only observation_id and summary and are unique by
observation_id. Remediation guidance has guidance_id, summary, and a
non-empty criterion_refs array; guidance and criterion references are unique
by guidance_id and criterion_id. Confidence is high, medium, or low.
Residual uncertainty is a non-empty string; use none when no uncertainty
remains.

## Frozen rubric and provenance

Read the complete `closure_evidence_snapshot` before judging current evidence. A first evaluation legally receives `prior_verdict: null` and creates the rubric from `root_contract`. Only a later evaluation is blocked when its required prior verdict or freshness anchor is absent or contradictory.

On first evaluation, derive the rubric solely from `root_contract`. On every
later evaluation, copy `prior_verdict.rubric` exactly, including criterion IDs,
requirements, and evidence rules. Never replace, merge, remove, or broaden a
later rubric.

The runtime-created evidence list is ordered by transition position and is
the fresh evidence surface. Label every criterion result with its provenance.
Use old evidence only as historical context unless explicitly revalidated.
A current criterion failure needs fresh or revalidated evidence. A
historical_only observation cannot select remediation. If required evidence
cannot be produced honestly, return BLOCKED with the affected criterion
blocked or missing. Missing maximum-depth evidence lowers confidence; it does
not automatically create a failure.

Changed rubrics, wrong root or freshness digests, missing or extra criteria,
contradictory contracts, and stale evidence are runtime admission concerns.
Do not repair, reinterpret, or re-baseline them in the stage. Every blocking
finding cites frozen criterion IDs. New observations remain non-blocking and
cannot become a finding, fix item, task, incident, spec, probe, or learning
item through this stage.

## Exact eight-step order

1. Read closure target, root contract, and trusted digests.
2. Read `prior_verdict` before current evidence.
3. On first evaluation, derive the rubric solely from the root contract.
4. On later evaluations, copy the prior rubric exactly.
5. Inspect the fresh evidence list and current repository state required by each criterion.
6. Label every criterion result with its provenance.
7. Use old evidence only as historical context unless explicitly revalidated.
8. Return one exact selected verdict artifact and one legal marker.

## Bounded review and runtime boundary

Expanded review is bounded and inline. Use it only when the task/root contract
or dispatch explicitly requires it, or when narrow evidence cannot support an
honest judgment. Record why it was needed and which additional criteria were
examined. Supplementary observations do not become rubric criteria. The stage
returns one verdict; terminal aftermath remains runtime-owned.

## Validation Checklist

- Use exactly the selected top-level fields and the declared nested fields.
- Confirm `artifact_kind` is `planning.artifacts.verdict` and every required
  field is present.
- Confirm IDs are unique where the schema declares `unique_by`.
- Confirm the valid example is accepted and each invalid example is refused by
  the selected verdict schema.

### Valid JSON example

```json
{
  "artifact_kind": "planning.artifacts.verdict",
  "summary": "The selected closure criteria were evaluated.",
  "closure_target_id": "closure-1",
  "root_contract_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "freshness_anchor_digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "rubric": {
    "criteria": [
      {
        "criterion_id": "criterion-1",
        "requirement": "The selected requirement is satisfied.",
        "evidence_rule": "Use current evidence."
      }
    ]
  },
  "criterion_results": [
    {
      "criterion_id": "criterion-1",
      "status": "passed",
      "provenance": "fresh",
      "evidence_refs": [
        {
          "evidence_id": "evidence-1",
          "summary": "Current evidence supports the criterion."
        }
      ]
    }
  ],
  "observations": [],
  "remediation_guidance": [],
  "confidence": "high",
  "residual_uncertainty": "none"
}
```

### Invalid JSON example: extra field

```json
{"artifact_kind":"planning.artifacts.verdict","summary":"The selected closure criteria were evaluated.","closure_target_id":"closure-1","root_contract_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","freshness_anchor_digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","rubric":{"criteria":[{"criterion_id":"criterion-1","requirement":"The selected requirement is satisfied.","evidence_rule":"Use current evidence."}]},"criterion_results":[{"criterion_id":"criterion-1","status":"passed","provenance":"fresh","evidence_refs":[{"evidence_id":"evidence-1","summary":"Current evidence supports the criterion."}]}],"observations":[],"remediation_guidance":[],"confidence":"high","residual_uncertainty":"none","unexpected":"refuse"}
```

### Invalid JSON example: missing required field

```json
{"artifact_kind":"planning.artifacts.verdict","closure_target_id":"closure-1","root_contract_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","freshness_anchor_digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","rubric":{"criteria":[{"criterion_id":"criterion-1","requirement":"The selected requirement is satisfied.","evidence_rule":"Use current evidence."}]},"criterion_results":[{"criterion_id":"criterion-1","status":"passed","provenance":"fresh","evidence_refs":[{"evidence_id":"evidence-1","summary":"Current evidence supports the criterion."}]}],"observations":[],"remediation_guidance":[],"confidence":"high","residual_uncertainty":"none"}
```

### Invalid JSON example: wrong type

```json
{"artifact_kind":"planning.artifacts.verdict","summary":"The selected closure criteria were evaluated.","closure_target_id":"closure-1","root_contract_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","freshness_anchor_digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","rubric":{"criteria":[{"criterion_id":"criterion-1","requirement":"The selected requirement is satisfied.","evidence_rule":"Use current evidence."}]},"criterion_results":[{"criterion_id":"criterion-1","status":"passed","provenance":"fresh","evidence_refs":[{"evidence_id":"evidence-1","summary":"Current evidence supports the criterion."}]}],"observations":[],"remediation_guidance":[],"confidence":7,"residual_uncertainty":"none"}
```

## Handoff format

Return:

1. The exact selected terminal marker spelling from dispatch.
2. The exact selected `planning.artifacts.verdict` object.
3. Runner evidence/report text for checks, assumptions, expanded-audit reason,
   provenance, and residual uncertainty outside the selected artifact.

Do not place wrapper keys, source IDs, action IDs, outcome IDs, route targets,
queue commands, or downstream context in the verdict unless the selected
schema declares them.

## Completion criteria

LAD Arbiter is complete only when it has read `prior_verdict` before current
evidence, preserved or created the frozen rubric correctly, labeled every
criterion result with provenance, returned one exact `planning.artifacts.verdict`
object and one legal terminal marker, and left terminal aftermath to the
runtime.
