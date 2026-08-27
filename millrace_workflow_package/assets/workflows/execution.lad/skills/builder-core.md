---
name: execution-builder-core
description: Use when executing the Builder stage for a selected Millrace execution workflow.
---

# Builder Core Skill

## Artifact Schema

Produce one selected artifact declared for the active stage. The selected dispatch context decides which schema is legal for the current run.

`execution.artifacts.stage_result`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.stage_result`. |
| `summary` | yes | string | Concise implementation result. |

`execution.artifacts.report`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.report`. |
| `summary` | yes | string | Verification and changed-surface summary. |

`execution.artifacts.builder_summary`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string | Must be `execution.artifacts.builder_summary`. |
| `summary` | yes | string | Builder handoff summary for Integrator when selected. |

Evidence and assumptions belong in the runner evidence envelope unless the selected schema explicitly includes them.

## Governed Builder Result

When the selected workflow declares `execution.artifacts.builder_result`,
produce that strict artifact instead of a free-text stage result. Required
fields are `artifact_kind`, `summary`, `task_contract_digest`,
`dispatch_digest`, canonical `canonical_changed_paths`, bounded `checks`,
`assumptions`, `unavailable_evidence`, and `remaining_work`. Each check has a
command, a `result_classification` of `passed`, `failed`, or `unavailable`, and
bounded evidence. Required dispatch and predecessor evidence comes first. A
relevant catalog entry may be selected by its named path on demand; never read
every catalog entry.

```json
{
  "artifact_kind": "execution.artifacts.builder_result",
  "summary": "Implemented the requested change.",
  "task_contract_digest": "sha256:task",
  "dispatch_digest": "sha256:dispatch",
  "canonical_changed_paths": ["src/example.py"],
  "checks": [
    {
      "command": "pytest -q",
      "result_classification": "passed",
      "evidence": ["tests passed"]
    }
  ],
  "assumptions": [],
  "unavailable_evidence": [],
  "remaining_work": []
}
```

## Handoff Format

```text
artifact_id:
artifact_kind:
produced_by_stage: lad_builder
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
artifact_id: lad_builder-result-1
artifact_kind: execution.artifacts.stage_result
produced_by_stage: lad_builder
source_work_item_id: work-1
source_run_id: run-1
terminal_marker: BUILDER_COMPLETE
summary: Implemented the requested package manifest update and ran the named package tests.
fields:
  artifact_kind: execution.artifacts.stage_result
  summary: Implemented the requested package manifest update and ran the named package tests.
evidence:
  - Dispatch input and selected schema were checked.
assumptions: []
next_stage_context:
  selected_context_only: true
```

## Invalid Examples

- Missing `artifact_kind`: invalid because the selected artifact schema cannot be verified.
- Unsupported marker: invalid because the marker is not declared for this selected stage.
- Claims that Builder chose the next stage instead of returning selected evidence.
- Runtime claim in artifact text: invalid because selected workflow data defines aftermath.

## Validation Checklist

- Required fields for the selected artifact schema are present.
- Evidence supports the summary and marker choice.
- Assumptions and missing data are explicit.
- Terminal marker is legal for `lad_builder` in the selected workflow.
- Text does not claim route, queue, approval, capability, effect, package, or durable-state behavior by itself.
- Text includes no API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## QA Context Handoff

Builder accepts either the direct initial task payload or the normalized re-entry form. When `qa_context` is present, preserve its exact `task_contract`, `task_contract_digest`, `checker_baseline`, and `checker_baseline_digest` values. The route projection carries Builder output as `implementation_evidence`; Builder does not recover missing context through lineage lookup.

## Completion Criteria

The Builder stage is complete only when it returns one selected artifact or evidence envelope, supporting evidence, assumptions, and one legal terminal marker.
