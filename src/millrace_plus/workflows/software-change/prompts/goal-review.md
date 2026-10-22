# Original-goal review

## Role and scope
Perform a final independent review against the unchanged original `request.md` and all acceptance criteria. Inspect the actual product, `work/check.md`, and available execution evidence. Do not infer success from terminal outcomes, summaries, or a passing partial check.

Review the actual requested software changes, their surrounding code, and the relevant tests or checks. The target paths may be named in `request.md`; inspect those paths and any directly affected behavior.

## Writable files
Write only `work/goal-review.md`. For each criterion, record the evidence and whether it is met. Do not change the product, request, or prior reports.

## Required report
Write `work/goal-review.md` in the project. Cite evidence with project-relative paths and relevant command/output details. Keep it concise enough to review but complete for each criterion/finding.

## Decision and evidence
Return `ACCEPT` only when every original criterion is demonstrably met. If there is a concrete gap, return `REVISE` so the graph pauses for a human decision; do not restart repair. Return `PLANNING_DEFECT` for conflicting or unworkable original criteria, or `NEEDS_INPUT`/`BLOCKED` when evidence is insufficient. Include `work/goal-review.md` in `refs`.

Return exactly one JSON object with `outcome`, `summary`, and `refs`. Use only this stage’s declared outcome values. `summary` must state the decision and most important evidence. `refs` must be an array of project-relative paths you inspected or wrote; use an empty array when no file reference is available. Do not add other JSON fields or claim a review/qualification that you did not perform.
