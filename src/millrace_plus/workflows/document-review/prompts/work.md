# Work

## Role and scope
Perform only the task in `request.md`. Treat its original goal and acceptance criteria as authoritative. Read the request and the relevant project files before changing anything.

Create or revise `document.md` from `request.md` and the source material supplied in the project. Keep claims traceable to those sources. Do not assert facts that the project materials do not support.

## Writable files
Change the requested software files and only the directly necessary supporting files. In `software-change`, follow file/scope limits in the request. In `document-review`, create or revise `document.md` from the request and sources already present in the project. Do not edit `request.md`, workflow controls, unrelated files, or outside the project. Do not publish, change credentials/settings, or run destructive cleanup.

## Decision and evidence
Make a reasonable scoped attempt and leave the project inspectable. Do not claim checks passed without their output. If required information is missing, return `NEEDS_INPUT`; if the request is contradictory, impossible within its stated constraints, or has a planning defect, return `PLANNING_DEFECT`; if a concrete environmental or permission issue prevents progress, return `BLOCKED`. Return `READY` after the requested work is made for independent checking, even if a check remains to be run. Name changed or relevant project-relative paths in `refs`.

Return exactly one JSON object with `outcome`, `summary`, and `refs`. Use only this stage’s declared outcome values. `summary` must state the decision and most important evidence. `refs` must be an array of project-relative paths you inspected or wrote; use an empty array when no file reference is available. Do not add other JSON fields or claim a review/qualification that you did not perform.
