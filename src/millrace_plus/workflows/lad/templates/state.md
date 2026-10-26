# Session inspection projection

Modified from pinned LAD Factory `templates/state.md`. See `PROVENANCE.md`.

Format/session: `millrace-lad-session/v2` / `<session_id>`
Identity/input: `<captured digests>`
Legacy/migration refs: `<null for new state or exact retained refs>`
Event history: `<unique {id, kind} rows, not counters or allowance>`
Plan/current lineage: `<ordered accepted plan and cursor>`
Baseline/evaluated pairs: `<immutable identities>`
Pending role/request/ACK: `<exact original-run continuation>`
Terminal/cleanup: `<saved disposition and public writer evidence>`

Read `lad-state/session.json`. This Markdown is a projection, not authority for migration, allowance, or missing state.
