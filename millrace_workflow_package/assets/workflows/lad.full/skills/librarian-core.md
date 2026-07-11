---
name: lad-full-librarian-core
description: Use when executing the Librarian stage for the selected full LAD workflow.
---

# Librarian Core Skill

## Artifact Schema

Selected-schema first. Return the exact selected artifact JSON object for the chosen marker. Evidence, assumptions, request IDs, preferred output paths, recommendations, and route hints belong in runner evidence/report text unless the selected schema declares them.

`learning.artifacts.skill_install_report` for `LIBRARIAN_COMPLETE`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.skill_install_report` | Must be `learning.artifacts.skill_install_report`. |
| `summary` | yes | string; min_length 1 | Concise install-report summary. |
| `target_skill_id` | yes | string; min_length 1 | Truthful selected target skill id. |
| `installed_path` | yes | string; min_length 1 | Truthful selected workspace-relative target path. |

`learning.artifacts.skill_disposition` for `LIBRARIAN_NOOP`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.skill_disposition` | Must be `learning.artifacts.skill_disposition`. |
| `summary` | yes | string; min_length 1 | Concise no-op disposition summary. |
| `disposition` | yes | string enum | One of `already_available`, `no_candidate`, or `index_unavailable`. |
| `target_skill_id` | no | string; min_length 1 | Include only when dispatch supplied a truthful nonblank target. |

`learning.artifacts.report` for `BLOCKED`

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `artifact_kind` | yes | string const `learning.artifacts.report` | Must be `learning.artifacts.report`. |
| `summary` | yes | string; min_length 1 | Blocked evidence summary. |

Additional artifact fields: none. Runner evidence/report text may name inspected request/index evidence, command evidence, and assumptions that are not selected artifact fields.

Decision rules:

- Required request evidence is `request_id`, nonblank `body`, and `root_source`. The Planner-authored request body is sufficient Planner context.
- Installed-skill and remote-index evidence are optional unless dispatch explicitly declares or provides them.
- Use `LIBRARIAN_COMPLETE` only when a coherent selected candidate and target path support `learning.artifacts.skill_install_report`.
- Use `LIBRARIAN_NOOP` with disposition `already_available` when dispatch evidence identifies the requested skill as already present.
- Use `LIBRARIAN_NOOP` with disposition `no_candidate` when supported candidate or index evidence was inspected and no relevant uninstalled candidate exists.
- Use `LIBRARIAN_NOOP` with disposition `index_unavailable` when no installed-skill or remote-index source is selected or provided.
- Use `BLOCKED` for malformed, contradictory, unsafe, or declared-but-unavailable required evidence. Absence of optional index evidence alone is not blocked.

## Handoff Format

Return:
1. The exact selected terminal marker spelling from dispatch.
2. `artifact` as the exact selected artifact JSON object for that marker.
3. Runner evidence/report text for inspected evidence, assumptions, source IDs, target paths, recommendations, and downstream context that are not selected artifact fields.

Do not put generic wrapper keys or Learning context fields into the runtime artifact body unless the selected schema declares them.

## Valid Example

```json
[
  {
    "terminal_marker": "LIBRARIAN_COMPLETE",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_install_report",
      "summary": "Selected skill install report prepared from dispatch evidence.",
      "target_skill_id": "millrace-review-and-qa-loop",
      "installed_path": "skills/millrace-review-and-qa-loop/SKILL.md"
    }
  },
  {
    "terminal_marker": "LIBRARIAN_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_disposition",
      "summary": "No selected installed-skill or remote-index source was provided.",
      "disposition": "index_unavailable"
    }
  },
  {
    "terminal_marker": "BLOCKED",
    "artifact": {
      "artifact_kind": "learning.artifacts.report",
      "summary": "Learning request evidence was missing, contradictory, or unsafe."
    }
  }
]
```

## Invalid Examples

Invalid examples in order: extra undeclared field with `installed_path` in a no-op artifact; missing `disposition`; type mismatch; unknown disposition; `LIBRARIAN_COMPLETE` without required install-report fields.

```json
[
  {
    "terminal_marker": "LIBRARIAN_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_disposition",
      "summary": "No selected installed-skill or remote-index source was provided.",
      "disposition": "index_unavailable",
      "installed_path": "skills/example/SKILL.md"
    }
  },
  {
    "terminal_marker": "LIBRARIAN_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_disposition",
      "summary": "No selected installed-skill or remote-index source was provided."
    }
  },
  {
    "terminal_marker": "LIBRARIAN_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_disposition",
      "summary": ["not a string"],
      "disposition": "index_unavailable"
    }
  },
  {
    "terminal_marker": "LIBRARIAN_NOOP",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_disposition",
      "summary": "No selected installed-skill or remote-index source was provided.",
      "disposition": "installed"
    }
  },
  {
    "terminal_marker": "LIBRARIAN_COMPLETE",
    "artifact": {
      "artifact_kind": "learning.artifacts.skill_install_report",
      "summary": "Selected skill install report prepared from dispatch evidence."
    }
  }
]
```

These are invalid because no-op artifacts must not include `installed_path`, `disposition` is required, `summary` must be a string, `disposition` must be `already_available`, `no_candidate`, or `index_unavailable`, and `target_skill_id` and `installed_path` are required for `learning.artifacts.skill_install_report`.

## Validation Checklist

- Required selected fields are present for the chosen marker.
- No artifact field is present unless the chosen selected schema declares it.
- `LIBRARIAN_NOOP` uses `learning.artifacts.skill_disposition`, never `learning.artifacts.skill_install_report`.
- No no-op artifact contains `installed_path`.
- `LIBRARIAN_COMPLETE` keeps truthful `target_skill_id` and `installed_path`.
- Evidence supports the selected marker and lives in runner evidence/report text unless selected as an artifact field.
- Terminal marker is legal for `librarian` in the selected full LAD workflow.
- No artifact text claims route, queue, approval, capability, effect, provider, package, plugin execution, reconciliation, persistence, or durable-state behavior.
- No artifact text includes API keys, OAuth tokens, local credential paths, provider secrets, or adapter config secrets.

## Completion Criteria

Librarian is complete only when it returns one legal terminal marker, one exact selected artifact body, and runner evidence/report text supporting the selected marker. Missing optional index evidence is complete no-op evidence when required request fields are valid and no index source was selected or provided.
