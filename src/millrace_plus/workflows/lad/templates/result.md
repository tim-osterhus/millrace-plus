# Role evidence

Modified from pinned LAD Factory `templates/result.md`. See `PROVENANCE.md`.

Session/occurrence/role/lineage: `<exact prepared input>`
Input/source/guidance/baseline identities: `<exact values>`
Helper mode/evidence: `<echo input, direct uses null evidence>`

## Evidence

Record inspected files, requirement-side expectations, changed paths, observed checks, and unrun checks. Stable findings cite criterion, reproduction, and evidence. Errors name the operation and resolved or unknown prerequisite. Human holds explain attempted authorized recovery and the exact required decision.

For Checker, write requirement-side `baseline.md` before reading implementation. Then include `baseline-freeze.json` with the exact required fields and a `data.baseline_freeze` path/hash ref. Parent acceptance pins the record independently.

Every v2 result includes its required history log as an ordinary `artifacts` path/hash entry: `historylog/<RoleTitle>_HHMM-MMDDYY.md` using the UTC `created_at` timestamp. Keep it below 5,120 UTF-8 bytes, include retained identities, and omit transcripts and secrets. Do not add a top-level `historylog` field.

## Outcome

State one declared semantic outcome and evidence. Return one matching Core json-v1 completion marker. Recommendations do not authorize routing, acceptance, allowance, publication, or completion.
