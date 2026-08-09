---
name: execution-checker-core
description: Use when executing the Checker stage for a selected Millrace execution workflow.
---

# Checker Core Skill

## Artifact Schema

For every normal Checker marker, return exactly one `execution.artifacts.checker_result`.
The selected schema is strict: undeclared properties and duplicate IDs are invalid.

Required top-level fields:

- `artifact_kind`: constant `execution.artifacts.checker_result`.
- `summary`: non-empty string.
- `task_contract_digest`: non-empty trusted digest from `qa_context`.
- `criteria`: non-empty objects with `criterion_id`, `requirement`, and `evidence_rule`; unique by `criterion_id`.
- `findings`: objects with `finding_id`, `observed_gap`, `impact`, `repair_surface`, non-empty `criterion_refs` unique by `criterion_id`, and non-empty `post_fix_check_refs` unique by `check_id`; unique by `finding_id`.
- `observations`: objects with `observation_id` and `summary`; unique by `observation_id`.
- `checks`: objects with `check_id`, `command_or_method`, and `result` equal to `passed`, `failed`, or `unavailable`; unique by `check_id`.

Every ID and narrative is non-empty. Each reference object contains only its named ID. Do not claim or write `execution.artifacts.integration_report`; Integrator evidence may be read but is read-only.

## QA Context and Marker Rules

Read the complete `qa_context` carrier:

```text
qa_context:
  task_contract
  task_contract_digest
  checker_baseline
  checker_baseline_digest
```

On the first accepted normal Checker result, fill the two null baseline fields with this exact Checker payload and its runtime-projected artifact digest. On later activations, preserve both existing values exactly. The baseline is the accepted Checker result, not a separate expectations file.

- `CHECKER_PASS`: `findings` is empty and criterion evidence supports pass.
- `FIX_NEEDED`: at least one criterion-linked finding exists.
- `BLOCKED`: required task or baseline evidence is contradictory or unavailable.
- Unrelated observations never determine the marker and never become fix criteria.

Every blocking finding cites frozen criterion IDs. Expanded review is bounded and inline: use it only when the task or dispatch requires it or narrow evidence cannot support an honest judgment; supplementary observations do not change the baseline.

Checker is observation-only with respect to runtime aftermath. Do not fix source/tests, mutate Git or queues, route work, close targets, retry work, approve effects, grant capabilities, or create canonical follow-up work. Current unrestricted runner capabilities do not grant this QA role permission to implement changes. Terminal markers are evidence candidates whose aftermath is runtime-owned.

## Validation Checklist

- [ ] Read `qa_context` before implementation evidence and preserve its accepted baseline on re-entry.
- [ ] Return every required field with non-empty IDs/narratives and unique criterion, finding, observation, and check IDs.
- [ ] Link every finding to frozen criteria and post-fix checks; keep supplementary observations non-blocking.
- [ ] Validate the exact selected schema and return one legal marker with one result artifact.
- [ ] Refuse undeclared properties, missing required fields, wrong types, duplicate IDs, and invented Integrator ownership.

## Parseable JSON Examples

## Valid Example

```json
{
  "artifact_kind": "execution.artifacts.checker_result",
  "summary": "The frozen criteria were checked with reproducible evidence.",
  "task_contract_digest": "sha256:trusted-task",
  "criteria": [
    {
      "criterion_id": "criterion-1",
      "requirement": "The requested behavior is present.",
      "evidence_rule": "Run the named test command."
    }
  ],
  "findings": [],
  "observations": [
    {
      "observation_id": "observation-1",
      "summary": "An unrelated note was recorded without changing the marker."
    }
  ],
  "checks": [
    {
      "check_id": "check-1",
      "command_or_method": "pytest -q",
      "result": "passed"
    }
  ]
}
```

The invalid examples below are refusal cases, not templates to emit.

### Invalid: extra property

```json
{
  "artifact_kind": "execution.artifacts.checker_result",
  "summary": "The frozen criteria were checked with reproducible evidence.",
  "task_contract_digest": "sha256:trusted-task",
  "criteria": [
    {
      "criterion_id": "criterion-1",
      "requirement": "The requested behavior is present.",
      "evidence_rule": "Run the named test command.",
      "extra": true
    }
  ],
  "findings": [],
  "observations": [],
  "checks": []
}
```

### Invalid: missing required field

```json
{
  "artifact_kind": "execution.artifacts.checker_result",
  "summary": "The frozen criteria were checked with reproducible evidence.",
  "task_contract_digest": "sha256:trusted-task",
  "findings": [],
  "observations": [],
  "checks": []
}
```

### Invalid: wrong type

```json
{
  "artifact_kind": "execution.artifacts.checker_result",
  "summary": "The frozen criteria were checked with reproducible evidence.",
  "task_contract_digest": "sha256:trusted-task",
  "criteria": {},
  "findings": [],
  "observations": [],
  "checks": []
}
```

## Completion Criteria

The Checker stage is complete only when it returns one strict typed Checker result, exact criterion-linked evidence, assumptions or unavailable evidence, and one legal marker. It must not select `execution.artifacts.stage_result` except through the named runtime-failure-exhausted action.
