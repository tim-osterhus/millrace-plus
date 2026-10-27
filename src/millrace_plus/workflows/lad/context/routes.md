# Visible LAD routes

Modified from the pinned Orchestrator and transition policy. See `PROVENANCE.md`. The graph is v4 and sets `max_parallel_invocations` to 2. It adds no total-run cap or per-role quota. The role outcome rows below are native result markers accepted by dedicated guards. The graph, not a prompt or role recommendation, determines the next node.

## Role outcomes

- `recon:PASS` → `PLANNER`
- `recon:FINDINGS` → `CONSULTANT`
- `recon:ERROR` → `TROUBLESHOOTER`
- `recon:HUMAN_BLOCKED` → `PAUSE`
- `planner:PASS` → `MANAGER`
- `planner:FINDINGS` → `CONSULTANT`
- `planner:ERROR` → `TROUBLESHOOTER`
- `planner:HUMAN_BLOCKED` → `PAUSE`
- `manager:PASS` → `BUILDER`, `ARBITER`
- `manager:FINDINGS` → `CONSULTANT`
- `manager:ERROR` → `TROUBLESHOOTER`
- `manager:HUMAN_BLOCKED` → `PAUSE`
- `builder:PASS` → `CHECKER`
- `builder:FINDINGS_INSPECTABLE` → `CHECKER`
- `builder:FINDINGS_UNINSPECTABLE` → `TROUBLESHOOTER`
- `builder:ERROR` → `TROUBLESHOOTER`
- `builder:HUMAN_BLOCKED` → `PAUSE`
- `checker:PASS` → `UPDATER`
- `checker:FINDINGS_ACTIONABLE` → `FIXER`
- `checker:FINDINGS_CONTRACT` → `CONSULTANT`
- `checker:ERROR` → `TROUBLESHOOTER`
- `checker:HUMAN_BLOCKED` → `PAUSE`
- `fixer:PASS` → `DOUBLECHECKER`
- `fixer:FINDINGS_INSPECTABLE` → `DOUBLECHECKER`
- `fixer:FINDINGS_UNINSPECTABLE` → `TROUBLESHOOTER`
- `fixer:ERROR` → `TROUBLESHOOTER`
- `fixer:HUMAN_BLOCKED` → `PAUSE`
- `doublechecker:PASS` → `UPDATER`, `ASSESS`
- `doublechecker:FINDINGS_ACTIONABLE` → `FIXER`, `TROUBLESHOOTER`, `ASSESS`
- `doublechecker:FINDINGS_CONTRACT` → `CONSULTANT`
- `doublechecker:ERROR` → `TROUBLESHOOTER`
- `doublechecker:HUMAN_BLOCKED` → `PAUSE`
- `troubleshooter:PASS_REVIEW_REPAIRED` → `DOUBLECHECKER`
- `troubleshooter:PASS_PREREQUISITE_REPAIRED` → `RESUME_RECON`, `RESUME_PLANNER`, `RESUME_MANAGER`, `RESUME_BUILDER`, `RESUME_CHECKER`, `RESUME_FIXER`, `RESUME_DOUBLECHECKER`, `RESUME_CONSULTANT`, `RESUME_UPDATER`, `RESUME_ARBITER`
- `troubleshooter:FINDINGS` → `CONSULTANT`
- `troubleshooter:ERROR` → `CONSULTANT`
- `troubleshooter:HUMAN_BLOCKED` → `PAUSE`
- `consultant:REPAIR_SPEC` → `PLANNER`
- `consultant:REPAIR_PACKETS` → `MANAGER`
- `consultant:REPAIR_EXECUTION` → `TROUBLESHOOTER`
- `consultant:NO_SAFE_ROUTE` → `PAUSE`
- `consultant:ERROR_REPAIRABLE` → `TROUBLESHOOTER`
- `consultant:ERROR_REPLACEMENT` → `REPLACE_CONSULTANT`
- `consultant:HUMAN_BLOCKED` → `PAUSE`
- `updater:PASS` → `BUILDER`, `ARBITER`
- `updater:REVIEW_INVALIDATED_NEW_BASELINE` → `CHECKER`
- `updater:REVIEW_INVALIDATED_SAME_BASELINE` → `DOUBLECHECKER`
- `updater:FINDINGS_CONTRACT` → `CONSULTANT`
- `updater:ERROR` → `TROUBLESHOOTER`
- `updater:HUMAN_BLOCKED` → `PAUSE`
- `learning:PASS` → `ACK`
- `learning:FINDINGS` → `ACK`
- `learning:ERROR_TRANSIENT_RESOLVED` → `REPLACE_LEARNING`, `ACK`
- `learning:ERROR` → `ACK`
- `learning:HUMAN_BLOCKED` → `ACK`
- `arbiter:PASS` → `CLOSE`
- `arbiter:FINDINGS` → `MANAGER`
- `arbiter:ERROR_REPAIRABLE` → `TROUBLESHOOTER`
- `arbiter:ERROR_REPLACEMENT` → `REPLACE_ARBITER`
- `arbiter:HUMAN_BLOCKED` → `PAUSE`

The second actual Fixer–Doublechecker pair enters Learning, including a passing review. Learning preserves the saved pending route and emits ACK in the original Core run.

## Deterministic result observations

The following names are guard outcomes, not role-authored markers:

- `INVALID_RESULT` records a recoverable malformed or incomplete result with a digest/reference to result bytes at their original path. It creates no immutable rejected-byte copy and does not recheck that path as immutable retained evidence. It then selects guarded same-role replacement. If the result is valid and only its required history log is missing, the guard can restore that file from the retained result. It does not rerun the native task or check.
- `INVALID_REPEAT` indicates an unchanged invalid-result fingerprint. The graph selects Troubleshooter.
- `INVALID_CONSULT` selects Consultant when the recorded recovery requires diagnosis beyond the current role.
- `INVALID_HOLD` selects the human hold when the same invalid condition persists at Consultant.
- Learning’s `INVALID_ACK` emits the saved ACK without changing the saved route.
- `TECHNICAL_RECOVERY` from replacement preparation means the failed prerequisite is unchanged. The graph selects Troubleshooter. A Troubleshooter replacement selects Consultant. A Consultant replacement selects human hold.

`REFUSED`, deterministic command errors, and unknown command outcomes pause for reconciliation. Only a new source or technical observation can change the failure cause. These observations do not count attempts or grant new allowance. Existing Core writer barriers and source/guidance authority still govern all acceptance and continuation.
