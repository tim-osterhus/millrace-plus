# CI/CD review method

## Start from the failure

Read the diagnosis and the cited log lines first. Decide which failure the change should fix before you read the patch. Do not accept a patch because its summary sounds right.

## Verify identities

Check that the patch digest equals `patch_sha256` from the role input, that `checks_ref` names the exact check batch, and that `event_id` and `config_sha256` match. Any mismatch is `BLOCKED`, with a finding that names it.

## Scope

Every path in the patch must be one of the `allowed_paths`, inside `repair_paths` and outside `protected_paths`. Reject binary changes, symlinks, renames, whole-file deletions, mode changes, new dependencies, and edits to dependency manifests, lockfiles, migrations, credentials, permissions, authentication, CI workflow definitions or release or deploy configuration. Count against the bounds: at most 3 files and 200 added plus deleted lines.

The patch must contain only this event's new changes. If it removes or rewrites lines that were already edited in the before-state, that is a `REJECT`.

## Substance

Judge whether the change addresses the observed cause. Reject a patch that suppresses a check, removes or weakens a test, relaxes an assertion or expected value, skips a case or hides the failure. Such a patch is `REJECT`, whatever the checks report.

## Checks

Read each check receipt. Each selected check must be `passed`, with the expected exit code, a joined process and unchanged checked source. A `failed`, `timeout`, `uncertain` or missing check cannot support `ACCEPT`. Passing checks alone do not prove the patch correct.

## Findings and verdict

Give each finding a stable `id`, one concrete issue, and `{path,sha256}` evidence refs. `ACCEPT` requires zero findings. Use `REJECT` for a wrong, incomplete or out-of-scope patch. Use `BLOCKED` for unverifiable identity, evidence or checks. Stop after one review.
