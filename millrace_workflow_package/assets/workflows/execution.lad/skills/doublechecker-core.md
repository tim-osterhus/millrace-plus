---
name: execution-doublechecker-core
description: Use when executing the Doublechecker stage for a selected Millrace execution workflow.
---

# Doublechecker Core Skill

## Artifact Schema

For every normal Doublechecker marker, return exactly one `execution.artifacts.doublecheck_result`.
The selected schema is strict: undeclared properties and duplicate IDs are invalid.

Required top-level fields:

- `artifact_kind`: constant `execution.artifacts.doublecheck_result`.
- `summary`: non-empty string.
- `task_contract_digest`: non-empty trusted digest from `qa_context`.
- `checker_baseline_digest`: non-empty digest of the accepted Checker baseline.
- `finding_statuses`: non-empty objects unique by `finding_id`.
- `checks`: objects with `check_id`, `command_or_method`, and `result` equal to `passed`, `failed`, or `unavailable`; unique by `check_id`.
- `observations`: objects with `observation_id` and `summary`; unique by `observation_id`.

Each finding status requires `finding_id`, `status`, `next_repair`, non-empty `criterion_refs` unique by `criterion_id`, and `evidence_refs` unique by `evidence_id`. Legal statuses are `resolved`, `unresolved`, `displaced`, `blocked`, and `invalid_contract`. Criterion references contain only `criterion_id`; evidence references require `evidence_id` and `summary`. Every ID and narrative is non-empty.

## QA Context and Marker Rules

Read and preserve this exact carrier:

```text
qa_context:
  task_contract
  task_contract_digest
  checker_baseline
  checker_baseline_digest
```

The accepted Checker baseline and its digest are immutable. Report exactly the original finding IDs. A new unrelated observation is not an original finding and cannot replace the baseline or select a blocking marker.

- `DOUBLECHECK_PASS`: every original finding is `resolved`.
- `FIX_NEEDED`: at least one original finding is `unresolved` or `displaced`.
- `BLOCKED`: honest validation is unavailable or the received contract is invalid.
- Observations never determine the marker and never become a new fix item.

Doublechecker is observation-only with respect to runtime aftermath. Do not fix source/tests, mutate Git or queues, route work, close targets, retry work, approve effects, grant capabilities, or create canonical follow-up work. Current unrestricted runner capabilities do not grant this QA role permission to implement changes. Terminal markers are evidence candidates whose aftermath is runtime-owned.

## Validation Checklist

- [ ] Read `qa_context` before Fixer evidence and preserve the accepted Checker baseline and digest exactly.
- [ ] Return statuses for every original finding ID with unique criterion and evidence references.
- [ ] Keep `resolved`, `unresolved`, `displaced`, `blocked`, and `invalid_contract` within the selected status enum.
- [ ] Keep supplementary observations non-blocking and return one exact result artifact with one legal marker.
- [ ] Refuse undeclared properties, missing required fields, wrong types, duplicate IDs, and invented findings.

## Parseable JSON Examples

## Valid Example

```json
{
  "artifact_kind": "execution.artifacts.doublecheck_result",
  "summary": "The original finding was revalidated against the frozen baseline.",
  "task_contract_digest": "sha256:trusted-task",
  "checker_baseline_digest": "sha256:checker-baseline",
  "finding_statuses": [
    {
      "finding_id": "finding-1",
      "status": "resolved",
      "next_repair": "none",
      "criterion_refs": [
        {
          "criterion_id": "criterion-1"
        }
      ],
      "evidence_refs": [
        {
          "evidence_id": "evidence-1",
          "summary": "The post-fix check passed."
        }
      ]
    }
  ],
  "checks": [
    {
      "check_id": "check-1",
      "command_or_method": "pytest -q",
      "result": "passed"
    }
  ],
  "observations": []
}
```

The invalid examples below are refusal cases, not templates to emit.

### Invalid: extra property

```json
{
  "artifact_kind": "execution.artifacts.doublecheck_result",
  "summary": "The original finding was revalidated against the frozen baseline.",
  "task_contract_digest": "sha256:trusted-task",
  "checker_baseline_digest": "sha256:checker-baseline",
  "finding_statuses": [
    {
      "finding_id": "finding-1",
      "status": "resolved",
      "next_repair": "none",
      "criterion_refs": [
        {
          "criterion_id": "criterion-1"
        }
      ],
      "evidence_refs": [],
      "extra": true
    }
  ],
  "checks": [],
  "observations": []
}
```

### Invalid: missing required field

```json
{
  "artifact_kind": "execution.artifacts.doublecheck_result",
  "summary": "The original finding was revalidated against the frozen baseline.",
  "task_contract_digest": "sha256:trusted-task",
  "checker_baseline_digest": "sha256:checker-baseline",
  "checks": [],
  "observations": []
}
```

### Invalid: wrong type

```json
{
  "artifact_kind": "execution.artifacts.doublecheck_result",
  "summary": "The original finding was revalidated against the frozen baseline.",
  "task_contract_digest": "sha256:trusted-task",
  "checker_baseline_digest": "sha256:checker-baseline",
  "finding_statuses": {},
  "checks": [],
  "observations": []
}
```

## Completion Criteria

The Doublechecker stage is complete only when it returns one strict typed Doublechecker result, statuses for exactly the original finding IDs, evidence for each status, assumptions or unavailable evidence, and one legal marker. It must not select `execution.artifacts.stage_result` except through the named runtime-failure-exhausted action.
