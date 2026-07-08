---
name: lad-full-librarian-core
description: Use when executing the Librarian stage for the selected full LAD workflow.
---

# Librarian Core Skill

## Artifact Schema

Produce `learning_librarian_install_report` with this exact schema:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_id` | yes | string | Stable artifact ID. |
| `artifact_kind` | yes | string | `learning.artifacts.skill_install_report`. |
| `source_work_item_id` | yes | string | Work item from dispatch. |
| `source_run_id` | yes | string | Run id from dispatch. |
| `stage_id` | yes | string | `librarian`. |
| `status` | yes | string | `complete`, `noop`, or `blocked`. |
| `summary` | yes | string | Selection result. |
| `installed_path` | no | string | Workspace-local path reported by evidence when an install occurred. |
| `installed_skill_ids` | yes | array | Skill ids installed or already available as selected evidence. |
| `selected_skill_ids` | yes | array | Relevant uninstalled candidates selected for installation. |
| `skipped_skill_ids` | yes | array | Candidates skipped with reasons. |
| `evidence` | yes | array | Planner, installed-index, remote-index, and command evidence. |
| `assumptions` | yes | array | Missing data and confidence limits. |

For `BLOCKED`, produce `learning_librarian_blocked_report` with `artifact_kind: learning.artifacts.report`, `summary`, `evidence`, and `assumptions`.

## Handoff Format

```text
artifact_id:
produced_by_stage: librarian
source_work_item_id:
source_run_id:
terminal_marker:
fields:
  artifact_kind:
  summary:
  installed_path:
  installed_skill_ids:
  selected_skill_ids:
  skipped_skill_ids:
evidence:
assumptions:
next_stage_context:
  planner_spec:
  remote_index_source:
```

## Valid Example

```text
artifact_id: librarian-report-6
produced_by_stage: librarian
source_work_item_id: learning-6
source_run_id: run-librarian-6
terminal_marker: LIBRARIAN_NOOP
fields:
  artifact_kind: learning.artifacts.skill_install_report
  summary: No relevant uninstalled optional skill was available.
  installed_path:
  installed_skill_ids:
    - millrace-code-diet
  selected_skill_ids: []
  skipped_skill_ids:
    - skill_id: broad-dev-helper
      reason: weak match to Planner spec
evidence:
  - Planner spec targeted package workflow docs only.
assumptions: []
next_stage_context:
  planner_spec: spec-42.md
  remote_index_source: supported remote index evidence from dispatch
```

## Invalid Examples

- Missing required field: no `selected_skill_ids`.
- Unsupported assumption: chooses a remote skill without comparing it to the Planner spec.
- Runtime claim in artifact text: says Librarian output installs unlisted skills, performs provider execution, or persists state.

## Validation Checklist

- Required fields are present.
- Planner spec or summary evidence was inspected.
- Installed skills are considered before remote candidates.
- Selected candidates are relevant and bounded.
- No-op is used when no relevant uninstalled candidate is available.
- Terminal marker is legal for the selected Librarian stage.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin/MCP, native runner, reconciliation, remote index implementation, installation authority, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

The stage is complete only when the selection report records Planner evidence, installed-skill evidence, remote candidates considered, selected or skipped ids, command evidence when available, and a supported complete or no-op outcome.
