# CI/CD repair method

## Before editing

Read the verified diagnosis, the observed failure it cites and each file in `allowed_paths`. Record the exact bytes of every file you may touch, so a `BLOCKED` return can restore them. Confirm that the target source still matches `source_before_sha256`. If it does not, return `BLOCKED` without editing.

## Making the edit

Make the smallest change that addresses the cited cause. Touch one file when one file is enough. Keep the file's existing style, encoding and line endings. Preserve every existing uncommitted user edit in these files. Add only your change. Do not reformat the file, regenerate it or rewrite unrelated lines.

Do not change expected values, assertions, golden output, skip markers, timeouts or test selection to make a failure disappear. Do not remove coverage. If the cited expectation looks wrong, return `BLOCKED` with the reason, and let the operator decide.

## Scope self-check

Before returning, list every file you changed and compare it with `allowed_paths`, `repair_paths` and `protected_paths`. Count regular text files and added plus deleted lines against the bounds. Confirm there are no binary files, symlinks, renames, deletions, mode changes or new dependencies. If any check fails, restore your own edits and return `BLOCKED`. The guard repeats this check from actual differences. Your self-check is not acceptance.

## Source digest

Compute `source_after_sha256` as the SHA-256 of the canonical JSON source snapshot of the target (the root `.git`, the root owner marker and cache directories are excluded). If you cannot compute that digest, return `BLOCKED` rather than writing a guessed value.

## Partial failure

If an edit fails midway, a file cannot be read back, or a restore is incomplete, stop editing. Restore every touched file from the saved bytes before returning. A partially applied edit is never a patch, and `PATCH_READY` is never returned after a failed write.

## Evidence and stopping

Cite the observed failure, the edited lines and the verified diagnosis with `{path,sha256}` refs computed from the bytes you read or wrote. Write the repair artifact only for `PATCH_READY`. Make one attempt. Do not run checks, review or publication steps.
