---
name: execution-fixer-core
description: Use when executing the Fixer stage for a selected Millrace execution workflow.
---

# Fixer Core Skill

## Artifact Schema

Produce one selected artifact declared for the active stage. The selected dispatch context decides which schema is legal for the current run.

`execution.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.stage_result`. |
| `summary` | yes | string | Repair result summary. |

`execution.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.report`. |
| `summary` | yes | string | Post-fix evidence summary. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Governed Fixer Result

When the selected workflow declares `execution.artifacts.fixer_result`,
produce that strict artifact instead of a free-text stage result. Required
fields are `artifact_kind`, `summary`, `original_finding_ids`,
`baseline_digest`, canonical `canonical_changed_paths`, bounded
`before_checks` and `after_checks`, `preserved_contract_digests`,
`preserved_artifact_digests`, `assumptions`, `unavailable_evidence`, and
`remaining_findings`. Each check has a command, a `result_classification` of
`passed`, `failed`, or `unavailable`, and bounded evidence. Required finding,
baseline, and attempt evidence comes first. A relevant catalog entry may be
selected by its named path on demand; never read every catalog entry.

```json
{
  "artifact_kind": "execution.artifacts.fixer_result",
  "summary": "Applied the assigned repair.",
  "original_finding_ids": ["finding-1"],
  "baseline_digest": "sha256:baseline",
  "canonical_changed_paths": ["src/example.py"],
  "before_checks": [],
  "after_checks": [
    {
      "command": "pytest -q",
      "result_classification": "passed",
      "evidence": ["tests passed"]
    }
  ],
  "preserved_contract_digests": ["sha256:task"],
  "preserved_artifact_digests": ["sha256:checker"],
  "assumptions": [],
  "unavailable_evidence": [],
  "remaining_findings": []
}
```

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_fixer
source_work_item_id:
source_run_id:
terminal_marker:
summary:
fields:
evidence:
assumptions:
next_stage_context:
```

## Valid Example

```text
artifact_id: lad_fixer-result-1
artifact_kind: execution.artifacts.stage_result
produced_by_stage: lad_fixer
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: FIXER_COMPLETE
summary: Applied only the requested fix and ran the post-fix checks named by the contract.
fields:
  artifact_kind: execution.artifacts.stage_result
  summary: Applied only the requested fix and ran the post-fix checks named by the contract.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `artifact_kind`: invalid because the selected artifact schema cannot be verified.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Replaces the fix contract with a different task or broad cleanup.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the summary and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_fixer` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## QA Context Handoff

Fixer receives `qa_context` plus the accepted Checker baseline and the latest criterion-linked repair evidence. Preserve `task_contract`, `task_contract_digest`, `checker_baseline`, and `checker_baseline_digest` exactly. Add only the selected repair result as `fixer_evidence` for Doublechecker; never substitute a Fixer result for the Checker baseline.

## Completion Criteria

The Fixer stage is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
