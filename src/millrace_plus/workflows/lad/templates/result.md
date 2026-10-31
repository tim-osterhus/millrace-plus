# Role evidence

Modified from pinned LAD Factory `templates/result.md`. See `PROVENANCE.md`.

Session/occurrence/role/lineage: `<session> / <occurrence> / <role> / <lineage>`
Input/source/guidance/baseline identities: `<input> / <source> / <guidance> / <baseline or null>`
Helper mode/evidence: `<echo input, direct uses null evidence>`

These reserved identity labels must state only this occurrence's exact values or keep the placeholders. Name predecessor occurrences with other wording, such as `Fixer occurrence:`.

## Evidence

Record inspected files, requirement-side expectations, changed paths, observed checks, and unrun checks. Stable findings cite criterion, reproduction, and evidence. Errors name the operation and resolved or unknown prerequisite. Human holds explain attempted authorized recovery and the exact required decision.

For Checker, write requirement-side `baseline.md` before reading implementation. Then include `baseline-freeze.json` with the exact required fields and a `data.baseline_freeze` path/hash ref. Parent acceptance pins the record independently.

Every v2 result includes its required history log as an ordinary `artifacts` path/hash entry: `historylog/<RoleTitle>_HHMM-MMDDYY.md` using the UTC `created_at` timestamp. Keep it below 5,120 UTF-8 bytes, include retained identities, and omit transcripts and secrets. Do not add a top-level `historylog` field.

The history log is evidence Markdown too: include nonblank Evidence and Outcome sections. Its raw bytes must include the exact occurrence, session, and role identities. Compute its `artifacts` SHA-256 from the final file bytes after all edits; the history-specific identity and size rules do not replace the other artifact requirements.

## Outcome

State one declared semantic outcome and evidence. Return one matching Core json-v1 completion marker. Recommendations do not authorize routing, acceptance, allowance, publication, or completion.
