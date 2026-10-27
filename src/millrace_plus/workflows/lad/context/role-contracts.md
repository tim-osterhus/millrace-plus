# LAD occurrence and artifact contracts

Modified from the pinned role prompts, contracts, and templates. See `PROVENANCE.md`. These workflow-local files do not add a Core protocol.

## Read and verify the occurrence

Read the ledger-selected `lad-state/active-role.json`, then the exact immutable `lad-state/occurrences/<occurrence_id>/input.json` and `assignment.md`. Verify session, occurrence, role, lineage, request, source, guidance, and input identity before acting. Never choose a different acceptance path.

Input-v2 retains the v1 identity, request, source-file, guidance, criteria, specification, packet, baseline, review, accepted-input, and pending-route data. It adds `created_at` as a UTC ISO timestamp. It adds `source_context` with `git` and `outside` identities, plus `helper_mode` and `helper_qualification`. The v2 input omits `local_repair_allowed`.

Valid helper modes are `direct` and `native_helpers`. Direct is the default, with null qualification. Native mode uses a `{path, sha256}` qualification ref. In result-v2, direct mode requires null `helper_evidence`. Native helper evidence is a digest-pinned ref. Planner must stay direct. Resolve accepted refs by pinned workspace, result path, and digest.

The prepared `input_sha256` is SHA-256 of the canonical input object with its own digest field removed. Canonical JSON uses sorted keys, compact separators, UTF-8, `ensure_ascii=False`, and `allow_nan=False`. `source_sha256` hashes the canonical target path-to-file-digest map. `source_context` separately binds Git base/index and identities outside assigned packet paths. The immutable `guidance_files` map and `guidance_snapshot_sha256` pin the occurrence snapshot.

The workflow fixes the materialized set. Every role receives `prompts/<role>.md`, `context/contracts.md`, `context/role-contracts.md`, `templates/result.md`, and `templates/assignment.md`. Planner, Manager, Checker, Arbiter, and Consultant also receive `context/routes.md`. Every role except Planner also receives `context/harness.md`. Do not assume another package asset is present or add a new common filename.

Input-v2 retains its existing keys. For a pre-freeze Checker occurrence, `request_sha256` binds the original bytes in `request.md`, and `criteria` keeps the canonical `{id, text, request_quote}` rows. The `packet` is null or contains only `id`, `lineage`, `criteria`, `depends_on`, and `paths`. The `baseline` is null or the original `state.baselines[lineage]` metadata: `occurrence_id`, `sha256`, `path`, `criteria`, and `finding_ids`, with `parent_pin: {path, sha256}` when present. A retained legacy baseline may lack `parent_pin`.

Top-level `accepted_inputs` and `accepted_input_refs` are empty. `specification`, `pending`, `fixer`, and `review` are null. Top-level `finding_ids` is empty, and `learning_request` is null. When present, `replacement_intent` contains only `old_occurrence_id`, `role`, `reason`, `source_sha256`, and `guidance_sha256`. It contains no evidence refs.

The occurrence materializes its input and assignment with the selected common and role guidance. It does not materialize opaque Planner, Manager, or Consultant input files, result bodies, or appendices.

Canonical session state and recovery history, including Consultant and baseline-tamper history, remain preserved elsewhere. These input-shape limits do not attest actual read order or sandbox reads. Checker must still freeze requirement-side expectations before reviewing implementation evidence. The source digest remains identity metadata, not proof of what Checker read.

## Result envelope and evidence files

Every new v2 occurrence returns a `millrace-lad-result/v2` object with exactly these top-level fields. Retained v1 occurrences keep their original v1 result and guidance contract. Do not convert or reinterpret them.

```json
{"format":"millrace-lad-result/v2","session_id":"00000000-0000-0000-0000-000000000001","occurrence_id":"00000000-0000-0000-0000-000000000002","role":"recon","outcome":"PASS","lineage":"unpacketized","input_sha256":"0000000000000000000000000000000000000000000000000000000000000000","source_sha256":"0000000000000000000000000000000000000000000000000000000000000000","guidance_sha256":"0000000000000000000000000000000000000000000000000000000000000000","artifacts":[{"path":"intake.md","sha256":"0000000000000000000000000000000000000000000000000000000000000000"},{"path":"historylog/Recon_0000-010100.md","sha256":"0000000000000000000000000000000000000000000000000000000000000000"}],"findings":[],"human_evidence":null,"data":{},"helper_mode":"direct","helper_evidence":null}
```

The required identity and artifact fields remain: `format`, `session_id`, `occurrence_id`, `role`, `outcome`, `lineage`, `input_sha256`, `source_sha256`, `guidance_sha256`, `artifacts`, `findings`, `human_evidence`, and `data`. V2 adds only `helper_mode` and `helper_evidence`. Direct mode requires null helper evidence. The Core completion object is separate: one `{"outcome":"...","summary":"...","refs":["lad-results/<occurrence_id>/result.json"]}` matching the exact runtime-selected marker schema.

Each artifact ref has only `path` and `sha256`. Paths are relative to the occurrence result directory, regular, contained, unique, and normally `.md`. Checker may also include `baseline-freeze.json`. Evidence Markdown must echo the occurrence and contain substantive `## Evidence` and `## Outcome` sections, observed checks, exact identities, and honest limitations. Accepted artifacts are immutable.

Every result includes the occurrence history log as an ordinary `artifacts` path/hash ref: `historylog/<StageName_HHMM-MMDDYY.md>`. The timestamp comes from v2 `created_at` in UTC. Keep each history log below 5,120 UTF-8 bytes and include retained occurrence, session, and role identities. Do not put a transcript, credential, or secret in it. There is no top-level `historylog` field.

`findings` contains exact `{id, criterion, path, reproduction, evidence}` rows. Actionable findings use stable IDs and original criterion IDs. `human_evidence` is null except for blocked outcomes that require exact `{type, obstacle, attempted, required_resolution}` fields. Its `type` is `unavailable_input`, `human_choice`, `protected_work`, `budget_exhausted`, or `diagnosis_exhausted`. A recoverable issue is not a human hold. Every ERROR outcome records `data.failure` with `{operation, attempted, evidence, prerequisite_resolved}`. The first three fields are nonblank strings. The last is boolean.

## Optional helper record shapes

These schemas are accepted only for offline validator fixtures. They do not qualify a live helper facility or authorize task dispatch. Each ref has exactly `path` and `sha256`.

The input’s `helper_qualification` points to `millrace-lad-helper-qualification/v1` with exactly `format`, `scope`, `binding`, `facility_version`, `parent_task_id`, and `inventory_ref`. `scope` must be `offline_validator_fixture`.

The independently issued inventory ref points to `millrace-lad-facility-inventory/v1` with exactly `format`, `binding`, `facility_version`, `parent_task_id`, `complete`, and `tasks`. `complete` must be true.

For `native_helpers`, `helper_evidence` points to `millrace-lad-helper-evidence/v1` with exactly `format`, `occurrence_id`, `parent_task_id`, `binding`, `facility_version`, `inventory_ref`, `tasks`, and `integration_ref`. Its `tasks` must match the pinned inventory. Each task has exactly `task_id`, `parent_task_id`, `role`, `work_area`, nullable `model` and `effort`, `result_ref`, `terminal_state`, and `terminal_observation_ref`. Work areas must be disjoint except for declared ancestry. Each result ref stays under its task area. Terminal states are `completed`, `failed`, or `cancelled`.

Each `terminal_observation_ref` points to `millrace-lad-facility-terminal/v1` with exactly `format`, `binding`, `facility_version`, `task_id`, `parent_task_id`, `terminal_state`, `result_ref`, `collected`, `started_at`, and `historylog_ref`. `collected` must be true. `started_at` is UTC.

The history ref points under the task’s `historylog/` folder to the exact `RoleTitle_HHMM-MMDDYY.md` file. Keep it below 5,120 UTF-8 bytes and include task and parent identity. `integration_ref` is a digest-pinned UTF-8 summary containing the occurrence, parent, and every task ID.

## Checker baseline freeze

Checker writes requirement-side `baseline.md` before examining implementation. It then writes `baseline-freeze.json` with exactly these fields. `criteria` is the exact packet criterion ID list. `path` is relative to `foreground_root`:

```json
{"format":"millrace-lad-baseline-freeze/v1","occurrence_id":"<checker occurrence UUID>","criteria":["<original criterion ID>"],"path":"lad-results/<checker occurrence UUID>/baseline.md","sha256":"<exact baseline.md byte digest>"}
```

The result’s `data.baseline_freeze` is a `{path, sha256}` ref to that file. The JSON file is also listed in `artifacts`. It has no self-digest.

Before downstream dispatch, the accepting boundary independently writes and pins the accepted freeze record under `lad-state/baseline-pins/`. It keeps one immutable backup of the exact original baseline bytes beside that pin. The pin remains tied to the original freeze identity and expected SHA-256. The backup never replaces or changes the pin. Do not change the original pin or backup. Do not invent another pin or replace either with a worker-controlled hash. Only an explicit new-baseline invalidation permits a new requirement freeze.

If a parent guard observes changed or missing baseline bytes, it retains the observation and any tampered bytes. A parent guard restores only exact original bytes. First verify the backup against the original pin and accepted identity. The backup must be a safe regular file. The destination path must be safe. If the baseline exists, it must be a regular file. Writer state must be known.

Record scoped restoration evidence. Make restoration idempotent. Ordinary load remains strict and never repairs silently. Hold if backup bytes or identity are missing or corrupt. Also hold for an unsafe destination, a nonregular existing baseline or backup, another integrity failure, or an unknown writer.

After exact restoration, record `BASELINE_TAMPER`. Route through Consultant to a fresh Checker occurrence. The Checker validates the original pinned basis. No role edits the old accepted baseline. Recovery does not replay target code or checks.

## Role result data

| Role | Required evidence | Additional data or rule |
| --- | --- | --- |
| Recon | `intake.md` | Map architecture, entrypoints, instructions, checks, integration boundaries, dirty ownership, and reproduction. PASS recommends Planner. Findings recommend Consultant. Execution errors recommend Troubleshooter. |
| Planner | `specification.md` | PASS supplies `criteria:[{id,text,request_quote}]`. IDs are unique and stable. Every quote is exact, nonblank, and present in the original request. Keep a sufficient original specification unchanged. Planner stays direct and works alone. |
| Manager | `plan-check.md` plus each packet draft | PASS supplies `plan:[{id,lineage,criteria,depends_on,paths,input_refs,markdown}]`. Cover each original criterion. Dependencies point to earlier lineages. Paths stay under target. Refs pin accepted occurrence results. Preserve unaffected accepted packets. |
| Builder | `implementation.md` | Edit only assigned packet paths. Report current source and actual checks. Findings must be inspectable to pass the selected inspection route. |
| Checker | `baseline.md`, `baseline-freeze.json`, and `review.md` | Freeze before implementation exposure. PASS lists every packet criterion in `criteria_checked` and a valid `data.baseline_freeze` ref. Errors or human holds before freeze may omit baseline artifacts. |
| Fixer | `repair.md` | `baseline` echoes the pinned baseline. `finding_ids` preserves assigned stable IDs. Do not redefine success to fit code. |
| Doublechecker | `review.md` | Echo the pinned baseline and exact Fixer input. PASS checks all packet criteria. The second actual Fixer–Doublechecker pair opens Learning, including after a pass. The graph saves the next route before the ACK wait. A repeated same-criterion gap needs a material new basis. |
| Troubleshooter | `diagnosis.md` | `data` links `trigger_occurrence_id`, `cause_id`, `resume_role`, and `repair_verified`. A verified repaired review recommends Doublechecker. A verified repaired prerequisite names the exact resume role. If the same repaired cause recurs, the graph selects Consultant. An unrelated execution cause may select Troubleshooter again. A same-criterion gap needs a material new basis. Do not rotate roles to evade an unchanged problem. |
| Consultant | `diagnosis.md` | Echo the pending `trigger_occurrence_id` and `cause_id`. Provide a nonblank `next_step` and a `next_evidence` `{path, sha256}` ref listed in this occurrence’s artifacts. The artifact must contain the literal step. The graph refuses a repeated proposal when `cause_id`, `next_step`, and `source_sha256` match an accepted Consultant result. No fixed repair counter applies. Select only a supported specification, packet, or execution repair. NO_SAFE_ROUTE needs human evidence. Do not approve a scope expansion or create a self-loop. |
| Updater | `completion.md` | PASS echoes the current passing review and maps every packet criterion to observed evidence. Current source must still match that review. Do not commit or publish. |
| Learning | `assessment.md` and optional `candidate.md` | `pending_route` exactly echoes the saved request. Assess two actual review pairs. Distinguish missing or unclear guidance from noncompliance and unrelated failure. Candidates stay separate and inactive. |
| Arbiter | `verdict.md` | `verdict` covers each original criterion exactly with `pass`, `gap`, or `unavailable` plus evidence. PASS requires drained accepted work and every criterion passing at current source. A real original-criterion gap may recommend Manager. Do not invent a criterion ID. |

`context/routes.md` and `workflow.json` define every role and accepted outcome. Role recommendations do not route work. Deterministic guards own invalid-result observations, transport recovery, acceptance, and state changes.


Shared-mode input-v2 adds `target_root` and `target_record:{path,sha256}`.
Verify that record's exact file digest before using the root. Assignment text
names the same root. Implementation roles use only their assigned paths there.
Learning keeps its existing separate workspace and write restrictions. Standalone
and retained inputs keep their existing shapes.

Rejected-result history retains a digest and reference to bytes at the original
result path. It does not create or recheck an immutable rejected-byte backup.
The independent immutable baseline backup and its original pin remain required.
