---
name: execution-troubleshooter-core
description: Use when executing the Troubleshooter stage for a selected Millrace execution workflow.
---

# Troubleshooter Core Skill

## Artifact Schema

Produce one selected artifact declared for the active stage. The selected dispatch context decides which schema is legal for the current run.

`execution.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.stage_result`. |
| `summary` | yes | string | Troubleshoot result summary. |

`execution.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.report`. |
| `summary` | yes | string | Diagnosis and recovery evidence summary. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Governed Troubleshooter Repair Plan

When the selected workflow declares
`execution.artifacts.troubleshooter_repair_plan`, produce that strict typed
artifact instead of a free-text stage result. Required fields are
`artifact_kind`, `summary`, `failed_session_id`, `failed_stage_id`,
`failure_classification`, `evidence_refs`, `diagnosed_scope`,
`baseline_invalidated`, `reentry_stage`, `artifact_dependencies`,
`context_dependencies`, `repair_instructions`, `stop_conditions`, and
`unrecoverable_reason`. Use only these classifications: `narrow_repair`,
`baseline_invalidated`, `unchanged_source_review_execution_failure`, or
`unrecoverable`. A narrow repair re-enters Fixer. A baseline-invalidated plan
may re-enter Builder only when `baseline_invalidated` is `true`. An unchanged
source review execution failure re-enters Checker. An unrecoverable plan has
no re-entry target and uses `baseline_invalidated: false` with
`reentry_stage: none`. The selected terminal marker must agree with the typed
tuple:

- `TROUBLESHOOT_NARROW_REPAIR`: `narrow_repair`, `false`, `fixer`.
- `TROUBLESHOOT_BASELINE_INVALIDATED`: `baseline_invalidated`, `true`, `builder`.
- `TROUBLESHOOT_REVIEW_RETRY`: `unchanged_source_review_execution_failure`, `false`, `checker`.
- `TROUBLESHOOT_UNRECOVERABLE`: `unrecoverable`, `false`, `none`.

Read the failure envelope and direct-predecessor artifacts first. Bounded
conventions, decisions, references, and relevant documents are selected by
named path on demand; never read every catalog entry.

```json
{
  "artifact_kind": "execution.artifacts.troubleshooter_repair_plan",
  "summary": "The accepted baseline remains valid for one narrow repair.",
  "failed_session_id": "session-1",
  "failed_stage_id": "lad_fixer",
  "failure_classification": "narrow_repair",
  "evidence_refs": ["evidence:failure"],
  "diagnosed_scope": "one finding",
  "baseline_invalidated": false,
  "reentry_stage": "fixer",
  "artifact_dependencies": ["execution.artifacts.checker_result"],
  "context_dependencies": ["selected_artifacts/direct_predecessors"],
  "repair_instructions": ["apply the finding"],
  "stop_conditions": ["stop when the finding check passes"],
  "unrecoverable_reason": "none"
}
```

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_troubleshooter
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
artifact_id: lad_troubleshooter-result-1
artifact_kind: execution.artifacts.stage_result
produced_by_stage: lad_troubleshooter
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: TROUBLESHOOT_NARROW_REPAIR
summary: Classified the blocker, made a narrow local repair, and verified the changed state.
fields:
  artifact_kind: execution.artifacts.stage_result
  summary: Classified the blocker, made a narrow local repair, and verified the changed state.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `artifact_kind`: invalid because the selected artifact schema cannot be verified.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Continues product implementation instead of limiting work to blocker recovery.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the summary and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_troubleshooter` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## QA Context Handoff

Troubleshooter preserves the received `qa_context` byte-for-byte. Every selected return action receives the same task contract, trusted digest, and Checker baseline; the selected route may add `troubleshooter_evidence` but may not replace the carrier or recover it implicitly from lineage.

## Completion Criteria

The Troubleshooter stage is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
