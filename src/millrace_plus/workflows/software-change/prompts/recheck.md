# Independent recheck

## Role and scope
Independently inspect the current product against unchanged `request.md` and every finding in `work/check.md`. Ignore the repair-stage claims. Check whether the actual changes resolve each finding without introducing a regression, and inspect the relevant surrounding files.

Review the actual requested software changes, their surrounding code, and the relevant tests or checks. The target paths may be named in `request.md`; inspect those paths and any directly affected behavior.

## Writable files
Write only `work/recheck.md`. Record each original finding, the evidence you observed in the product, checks and results, and any remaining issue. Do not edit the product, original request, or earlier report.

## Required report
Write `work/recheck.md` in the project. Cite evidence with project-relative paths and relevant command/output details. Keep it concise enough to review but complete for each criterion/finding.

## Decision and evidence
Return `ACCEPT` only when the product and evidence resolve all findings and the original criteria are satisfied. Return `REVISE` for any unresolved or newly found concrete gap; this route pauses for a human decision rather than starting another repair cycle. Use `PLANNING_DEFECT`, `NEEDS_INPUT`, or `BLOCKED` when appropriate. Include `work/recheck.md` in `refs`.

Return exactly one JSON object with `outcome`, `summary`, and `refs`. Use only this stage’s declared outcome values. `summary` must state the decision and most important evidence. `refs` must be an array of project-relative paths you inspected or wrote; use an empty array when no file reference is available. Do not add other JSON fields or claim a review/qualification that you did not perform.
