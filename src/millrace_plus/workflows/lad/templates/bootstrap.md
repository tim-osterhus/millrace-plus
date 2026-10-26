# Bootstrap projection

Modified from pinned LAD Factory `templates/bootstrap.md`. See `PROVENANCE.md`.

Format: `millrace-lad-session/v2`
Session: `<retained UUID>`
Captured identity/input digests: `<exact values>`
Outcome: `<NEW_SESSION | EXISTING_SESSION_PRISTINE | EXISTING_SESSION_WITH_WORK | RECONCILE>`
Original request path/digest: `<exact input bytes>`
Legacy/migration refs: `<null for new session or exact retained refs>`
Event history: `[{id, kind}, ...]`
Continuation/terminal: `<exact retained state>`

This is an operator projection, not an alternate ledger, allowance, or reset authority. A v1 copy changes format only after an explicit operator-selected upgrade.
