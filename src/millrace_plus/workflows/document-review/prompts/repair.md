# Repair

## Role and scope
Use the original `request.md` and the findings in `work/check.md`. Inspect the actual product before changing it; make only the smallest changes needed to address those findings.

Repair only document gaps identified in `work/check.md`, using the supplied sources. Preserve supported content that the findings do not challenge.

## Writable files
Edit requested product files and directly necessary supporting files only. Do not edit `request.md`, `work/check.md`, workflow controls, or unrelated files. Do not broaden scope or claim independent verification.

## Decision and evidence
Return `READY` when your repair attempt is complete and ready for a fresh independent recheck. Return `NEEDS_INPUT`, `BLOCKED`, or `PLANNING_DEFECT` when appropriate. List changed paths in `refs`; do not overwrite the checker report.

Return exactly one JSON object with `outcome`, `summary`, and `refs`. Use only this stage’s declared outcome values. `summary` must state the decision and most important evidence. `refs` must be an array of project-relative paths you inspected or wrote; use an empty array when no file reference is available. Do not add other JSON fields or claim a review/qualification that you did not perform.
