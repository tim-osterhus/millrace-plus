# LAD occurrence and artifact contracts

Modified from the pinned role prompts, contracts, and templates. See `PROVENANCE.md`. These workflow-local files do not add a Core protocol.

## Read and verify the occurrence

Read the ledger-selected `lad-state/active-role.json`, then the exact immutable `lad-state/occurrences/<occurrence_id>/input.json` and `assignment.md`. Verify session, occurrence, role, lineage, request, source, guidance, and input identity before acting. Never choose a different acceptance path.

Input-v2 retains the v1 identity, request, source-file, guidance, criteria, specification, packet, baseline, review, accepted-input, and pending-route data. It adds `created_at` as a UTC ISO timestamp. It adds `source_context` with `git` and `outside` identities, plus `helper_mode` and `helper_qualification`. The v2 input omits `local_repair_allowed`.

Valid helper modes are `direct` and `native_helpers`. Direct is the default, with null qualification. Native mode uses a `{path, sha256}` qualification ref. In result-v2, direct mode requires null `helper_evidence`. Native helper evidence is a digest-pinned ref. Planner must stay direct. Resolve accepted refs by pinned workspace, result path, and digest.

For accepted RESULT refs, each `accepted_inputs` digest and `accepted_input_refs` `sha256` hashes the entire parsed `result.json` object. Re-encode that full object as canonical JSON with sorted keys, compact separators, UTF-8, `ensure_ascii=False`, and `allow_nan=False`, without removing fields or appending a newline, then compute SHA-256. Hashing the raw result-file bytes can produce a different digest solely because of formatting; that difference alone does not establish changed accepted content. Artifact, original-request, and guidance-file refs retain their existing exact-byte hash rules.

The prepared `input_sha256` is SHA-256 of the canonical input object with its own digest field removed. Canonical JSON uses sorted keys, compact separators, UTF-8, `ensure_ascii=False`, and `allow_nan=False`. `source_sha256` hashes the canonical target path-to-file-digest map. `source_context` separately binds Git base/index and identities outside assigned packet paths. The immutable `guidance_files` map and `guidance_snapshot_sha256` pin the occurrence snapshot.

The workflow fixes the materialized set. Every role receives `prompts/<role>.md`, `context/contracts.md`, `context/role-contracts.md`, `templates/result.md`, `templates/assignment.md`, and the captured `context/routing-policy.json`. Manager also receives `templates/packet.md`. Planner, Manager, Checker, Arbiter, and Consultant also receive `context/routes.md`. Every role except Planner also receives `context/harness.md`. Do not assume another package asset is present or add a new common filename.

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

The input's `source_files` map and `source_sha256` describe the prepared pre-work snapshot. The result has no `source_files` field. Its `source_sha256` must hash the current target map after the role's work, using the same canonical JSON algorithm above; do not echo the prepared digest after target edits. This applies to every role, including Checker, Doublechecker, and Fixer, without changing baseline read order or permitted writes.

Build that map with target-relative path keys and SHA-256 of each regular file's exact bytes. Select current packet paths and their descendants, or the whole target when no packet paths are selected. Within traversed directories, `.env`, `.env.local`, `credentials.json`, and `secrets.json` remain included even outside packet paths. Never traverse `.git`. Skip directories named `node_modules`, `.venv`, `venv`, `__pycache__`, `build`, `dist`, `.cache`, `.pytest_cache`, or `.ruff_cache` unless a packet path selects that directory, a descendant, or an ancestor. Exclude the validated owner marker `.millrace-plus-target-owner.json` when the target is external to `foreground_root/target`. Do not follow symlinks: their map value is `symlink:` plus SHA-256 of the filesystem-encoded link target. Canonicalize the resulting path-to-digest map, then hash those JSON bytes. Existing source-scope, link-change, and `source_context` guards still apply.

Each artifact ref has only `path` and `sha256`. Paths are relative to the occurrence result directory, regular, contained, unique, and normally `.md`. Checker may also include `baseline-freeze.json`. Evidence Markdown contains nonblank Evidence and Outcome sections, observed checks, exact identities, and honest limitations. Accepted artifacts are immutable.

## Presentation view

Captured `context/routing-policy.json` (`millrace-lad-routing-policy/v1`) selects one finite, deterministic presentation check. Acceptance first verifies the result identity, contract, declared outcome, request/source/guidance authority, containment and every raw artifact SHA-256. A digest mismatch is an integrity failure, not formatting. Only then does it read a view of each Markdown artifact: BOM removed, CRLF as LF, outer blank lines trimmed and canonical label spelling. Raw bytes, their digests and the accepted result are never rewritten. Acceptance retains an immutable `millrace-lad-presentation/v1` record under `lad-state/presentation/<occurrence>/` with raw and view hashes and applied rule IDs; views are never role artifacts. Every later load recomputes that complete record from the raw bytes, the prepared input and the occurrence's captured policy, and refuses any difference.

A section label begins a logical line after optional indentation, a list marker and bold. It is outside fenced code and blockquotes, including fences or quotes opened inside a list item. Recognized labels are case-insensitive `Evidence` and `Outcome` as ATX headings of any level (optional closing hashes, an inline `: body`, or a legacy title suffix such as `## Evidence and observed checks`) or as plain `Evidence:`/`Outcome:` lines. A heading section ends at the next Evidence/Outcome label or an equal/shallower heading; a plain section ends at any heading, the next label or the end. Each section body must be nonblank; the previous 100-character floor is removed. Identical repeats are accepted; different bodies refuse. Labels only in fenced code or blockquotes, mid-line label text and prose such as “the evidence is good” are not sections.

Reserved self-identity labels are line-anchored `Session/occurrence/role/lineage:`, `Session:`, `Occurrence:`, `Role:`, `Lineage:` and `Input/source/guidance/baseline identities:`. Values may be wrapped in backticks. The combined labels take four slash-separated values in that order or exactly the four `key=value` tokens (`session`, `occurrence`, `role`, `lineage`, or `input`, `source`, `guidance`, `baseline`). A `<placeholder>` is missing boilerplate. Every explicit value must equal this occurrence's prepared identity; `source` may be the prepared or result digest and `baseline` is the prepared baseline digest or `null`. A malformed value, such as prose after `Role:`, refuses. Mentions of predecessor occurrences under other labels, in fences, in blockquotes or as bare IDs are references, not self-identity. When identities are missing, the view adds one delimited generated header from validated authority. It never supplies evidence, checks, criteria, findings or freeze facts. The history log's raw identity and below-5,120-byte rules still apply to its exact bytes.

Every result includes the occurrence history log as an ordinary `artifacts` path/hash ref: `historylog/<StageName_HHMM-MMDDYY.md>`. The timestamp comes from v2 `created_at` in UTC. Keep each history log below 5,120 UTF-8 bytes and include retained occurrence, session, and role identities. Do not put a transcript, credential, or secret in it. There is no top-level `historylog` field.

The history log is evidence Markdown too: include nonblank Evidence and Outcome sections. Its raw bytes must contain the exact occurrence, session, and role identities. Compute its `artifacts` SHA-256 from the final file bytes after all edits; the history-specific identity and size rules do not replace the other artifact requirements.

`findings` contains exact `{id, criterion, path, reproduction, evidence}` rows. Actionable findings use stable IDs and original criterion IDs. `human_evidence` is null except for blocked outcomes that require exact `{type, obstacle, attempted, required_resolution}` fields. Its `type` is `unavailable_input`, `human_choice`, `protected_work`, `budget_exhausted`, or `diagnosis_exhausted`. A recoverable issue is not a human hold. Every ERROR outcome records `data.failure` with `{operation, attempted, evidence, prerequisite_resolved}`. The first three fields are nonblank strings. The last is boolean. For `ERROR_REPLACEMENT` and `ERROR_TRANSIENT_RESOLVED`, `data.failure.prerequisite_resolved` must be true; other ERROR markers keep the boolean rule. For Learning, `ERROR_TRANSIENT_RESOLVED` is recorded as a failed assessment and grants no second Learning invocation.

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

Set both the result's `data.baseline_freeze` and the freeze file's `artifacts` entry to this exact ref shape:

```json
{"path":"baseline-freeze.json","sha256":"<SHA-256 of final baseline-freeze.json file bytes>"}
```

Both outer paths are relative to this occurrence's result directory, `lad-results/<checker occurrence UUID>/`; do not repeat that directory in either ref. Their digest hashes the final raw JSON file bytes, not canonicalized JSON. Inside that JSON file, `path` remains `lad-results/<checker occurrence UUID>/baseline.md`, relative to `foreground_root`, and `sha256` hashes the exact baseline Markdown bytes. The freeze JSON has no self-digest. These byte hashes differ from the canonical accepted-result digest described above.

Before downstream dispatch, the accepting boundary independently writes and pins the accepted freeze record under `lad-state/baseline-pins/`. It keeps one immutable backup of the exact original baseline bytes beside that pin. The pin remains tied to the original freeze identity and expected SHA-256. The backup never replaces or changes the pin. Do not change the original pin or backup. Do not invent another pin or replace either with a worker-controlled hash. Only an explicit new-baseline invalidation permits a new requirement freeze.

If a parent guard observes changed or missing baseline bytes, it retains the observation and any tampered bytes. A parent guard restores only exact original bytes. First verify the backup against the original pin and accepted identity. The backup must be a safe regular file. The destination path must be safe. If the baseline exists, it must be a regular file. Writer state must be known.

Record scoped restoration evidence. Make restoration idempotent. Ordinary load remains strict and never repairs silently. Hold if backup bytes or identity are missing or corrupt. Also hold for an unsafe destination, a nonregular existing baseline or backup, another integrity failure, or an unknown writer.

After exact restoration, record `BASELINE_TAMPER`. Route through Consultant to a fresh Checker occurrence. The Checker validates the original pinned basis. No role edits the old accepted baseline. Recovery does not replay target code or checks.

## Role result data

| Role | Required evidence | Additional data or rule |
| --- | --- | --- |
| Recon | `intake.md` | Map architecture, entrypoints, instructions, checks, integration boundaries, dirty ownership, and reproduction. PASS recommends Planner. Findings recommend Consultant. Execution errors recommend Troubleshooter. |
| Planner | `specification.md` | PASS supplies `criteria:[{id,text,request_quote}]`. IDs are unique and stable. Every quote is exact, nonblank, and present in the original request. Keep a sufficient original specification unchanged. Planner stays direct and works alone. |
| Manager | `plan-check.md` plus each packet draft | PASS supplies `plan:[{id,lineage,criteria,depends_on,paths,input_refs,markdown}]`. Each `markdown` value is a packet draft path relative to this occurrence's result directory, such as `packets/packet-1.md`, matching an exact listed `artifacts[].path` and its final-byte SHA-256. Write that draft using the captured `templates/packet.md`, including one line-anchored `Development check:` label with its nonblank check text and meaningful verification. Cover each original criterion. Dependencies point to earlier lineages. Target `paths` stay under the target root. `input_refs` pin accepted occurrence results. Preserve unaffected accepted packets. |
| Builder | `implementation.md` | Edit only assigned packet paths. Report current source and actual checks. Findings must be inspectable to pass the selected inspection route. |
| Checker | `baseline.md`, `baseline-freeze.json`, and `review.md` | Freeze before implementation exposure. PASS lists every packet criterion in `criteria_checked` and a valid `data.baseline_freeze` ref. Errors or human holds before freeze may omit baseline artifacts. |
| Fixer | `repair.md` | Set `data.baseline` equal to the entire prepared `input.baseline`, preserving nested metadata. `data.finding_ids` is a list whose set equals the prepared `input.finding_ids` set; no additional order or uniqueness rule applies. Do not substitute a baseline digest/ref or a subset of findings. Do not redefine success to fit code. |
| Doublechecker | `review.md` | Set `data.baseline` equal to the entire prepared `input.baseline` and `data.fixer` equal to the entire prepared `input.fixer`, preserving nested metadata. PASS supplies `data.criteria_checked` as a list whose set equals the `input.packet.criteria` set; no additional order or uniqueness rule applies. The second actual Fixer–Doublechecker pair opens Learning, including after a pass. The graph saves the next route before the ACK wait. A repeated same-criterion gap needs a material new basis. |
| Troubleshooter | `diagnosis.md` | Successful repair supplies `data.trigger_occurrence_id=input.pending.occurrence_id`, `data.cause_id=input.pending.cause_id` (null when absent), `data.resume_role=input.pending.target`, and boolean `data.repair_verified=true`. `PASS_REVIEW_REPAIRED` also requires the prepared inspectable Fixer; do not fabricate one. A verified repaired review recommends Doublechecker. A verified repaired prerequisite names the exact resume role. If the same repaired cause recurs, the graph selects Consultant. An unrelated execution cause may select Troubleshooter again. A same-criterion gap needs a material new basis. Do not rotate roles to evade an unchanged problem. |
| Consultant | `diagnosis.md` | Echo the pending `trigger_occurrence_id` and `cause_id`. Provide a nonblank `next_step` and a `next_evidence` `{path, sha256}` ref listed in this occurrence’s artifacts. The artifact must contain the literal step. The graph refuses a repeated proposal when `cause_id`, `next_step`, and `source_sha256` match an accepted Consultant result. No fixed repair counter applies. Select only a supported specification, packet, or execution repair. NO_SAFE_ROUTE needs human evidence. Do not approve a scope expansion or create a self-loop. |
| Updater | `completion.md` | PASS copies `input.review` unchanged into `data.review` and supplies `data.criteria_evidence` for exactly the current packet criterion IDs, with nonblank evidence strings. Current source must still match that review. See the exact fields below. Do not commit or publish. |
| Learning | `assessment.md` and optional `candidate.md` | `data.pending_route` exactly equals the entire prepared `input.pending`, also `input.learning_request.payload.pending_route`. Copy that nested route value, not the whole `learning_request` or only its target. Assess two actual review pairs once. Distinguish missing or unclear guidance from noncompliance and unrelated failure. Candidates stay separate and inactive. Execution failure, an invalid result or an error marker records a failed assessment and the original ACK; it never starts another Learning invocation. |
| Arbiter | `verdict.md` | `data.verdict` is an object keyed by exactly every original `input.criteria[].id`. Each value has exactly `{status,evidence}`, with `status` equal to `pass`, `gap`, or `unavailable` and nonblank text `evidence`. PASS requires drained accepted work and every criterion passing at current source. FINDINGS requires an actual `gap`; a real original-criterion gap may recommend Manager. Do not invent a criterion ID. |

### Updater PASS fields

Set `data.review` to a deep-equal copy of the prepared `input.review` object,
with exactly its three keys: `occurrence_id`, `source_sha256`, and `baseline`.
Copy the nested baseline unchanged too; do not expand the review into a summary
or an accepted-result/artifact ref. `result.source_sha256` must still match that
passing review and the current target map.

`data.criteria_evidence` is a mapping whose keys are exactly the IDs in
`input.packet.criteria`. Each value is a nonblank UTF-8 evidence string without
NUL characters, describing observed evidence for that criterion. Use actual
checks and limitations; `criteria_completed` does not supply this mapping.
For example, construct the role's `data` using the prepared identities and
replace each evidence string with the actual observation:

```python
data = {
    "review": input_record["review"],
    "criteria_evidence": {
        criterion: "Observed check and evidence for this criterion"
        for criterion in input_record["packet"]["criteria"]
    },
}
```

### Manager plan fields

For Manager PASS, `data.plan` is a nonempty ordered list covering the accepted
original criteria. Each row has exactly these seven fields; use the prepared
criterion IDs and accepted-input identities in the example below:

```json
{"plan":[{"id":"PKT-01","lineage":"labels_impl","criteria":["C1"],"depends_on":[],"paths":["labels.py","test_labels.py"],"input_refs":[{"occurrence_id":"<accepted occurrence UUID>","sha256":"<accepted canonical result SHA-256>"}],"markdown":"packets/PKT-01.md"}]}
```

| Field | Existing acceptance rule |
| --- | --- |
| `id`, `lineage` | Each matches `[A-Za-z][A-Za-z0-9_-]{0,63}`: 1–64 characters, starting with an ASCII letter. Lineages are unique within the plan and remain stable across replanning. |
| `criteria` | Nonempty list of accepted original criterion IDs. The complete plan must cover every original criterion and introduce none. |
| `depends_on` | List of distinct lineage IDs from earlier rows; use an empty list when none are required. These values name earlier `lineage` fields. |
| `paths` | Nonempty list of nonblank canonical relative target paths. Absolute paths, `..` components, `.`, empty paths and non-normalized spellings refuse. No component may be `.git`, `.env`, `.env.local`, `credentials.json`, `secrets.json`, `lad-state` or `lad-results`. |
| `input_refs` | List, which may be empty. Every entry has exactly `{occurrence_id,sha256}`: an already accepted occurrence ID and that accepted full-result canonical JSON SHA-256. Set `occurrence_id` to a key from the prepared `accepted_input_refs` mapping and `sha256` to that entry's digest, also recorded in `accepted_inputs` for the same key. Copy only these two fields; artifact-file paths and byte hashes belong in `artifacts`. |
| `markdown` | Nonblank packet draft path relative to this occurrence's result directory, exactly matching one listed `artifacts[].path` with its final-byte hash. Its UTF-8 draft must contain one line-anchored `Development check:` label (inline, bullet, bold or heading form) with nonblank check text and meet the common evidence Markdown rules. Use the captured `templates/packet.md`. |

When a plan already exists, retain its complete lineage set. Keep all seven
fields of each unaffected accepted packet equal to its accepted values.
Optional `data.affected_lineages` is a list and defaults to empty. Nonempty values
may name only existing lineages during an actual saved remediation; every named
lineage must cover at least one of that remediation's pending original criteria.
Only that eligible remediation permits reopening an affected accepted packet.
Runtime owns accepted-plan metadata and the packet cursor; author only the seven
row fields.

`context/routes.md` and `workflow.json` define every role and accepted outcome. Role recommendations do not route work. Deterministic guards own invalid-result observations, transport recovery, acceptance, and state changes.


Shared-mode input-v2 adds `target_root` and `target_record:{path,sha256}`.
Verify that record's exact file digest before using the root. Assignment text
names the same root. Implementation roles use only their assigned paths there.
Learning keeps its existing separate workspace and write restrictions. Standalone
and retained inputs keep their existing shapes.

Rejected-result history retains a digest and reference to bytes at the original
result path. It does not create or recheck an immutable rejected-byte backup.
The independent immutable baseline backup and its original pin remain required.

## Enabled engineering records

These JSON artifacts are accepted only in copies with a captured engineering
profile. `assignment.md` names `lad-state/occurrences/<id>/engineering-context.json`
(`millrace-lad-engineering-context/v1`); its refs are relative to the copy root.
Before its baseline freeze, Checker receives requirements and profile refs only.

- Checker/Doublechecker may add `maintenance-findings.json`
  (`millrace-lad-maintenance-findings/v1`: `format`, `occurrence_id`,
  `profile_sha256`, `source_sha256`, `findings`). Each finding has exactly `id`,
  `criterion_id`, `guideline_id`, `kind`, `location`, `evidence_refs`,
  `likelihood`, `impact`, `proposed_action` and `blocking`. Blocking needs
  `demonstrated` likelihood, evidence and a current criterion or required
  guideline. Doublechecker PASS refuses on a regression or an unavailable/not
  comparable required check; a pre-existing required failure needs a non-blocking
  finding tied to a current criterion citing that check's accepted evidence.
- Updater PASS requires `artifact-admission.json`
  (`millrace-lad-artifact-admission/v1`: `format`, `session_id`, `task_id`,
  `source_sha256` equal to the result's, `profile_sha256`, `entries`,
  `integrity_sha256` over the record without it). Entries are exactly `path`,
  `sha256`, `class`, `authority_ref`, `disposition`, `reason`. Deliverables are
  target-relative under captured deliverable paths; evidence, scratch and
  retained context are workspace-relative under their captured roots, outside the
  target. `remove_requested` is only for owned scratch under
  `<scratch_root>/<session_id>/` with its exact digest. If no candidate check was
  selected, data `no_check_disposition` records the reviewed reason.
- Troubleshooter may receive pending kind `engineering_prerequisite` (target
  `engineering_baseline`/`engineering_candidate`) naming the unavailable required
  checks and their accepted evidence. A verified repair returns
  `PASS_PREREQUISITE_REPAIRED` with `resume_role` equal to that target; never
  install tools or edit the captured profile.
- Arbiter writes `retained-verdict.json` (`millrace-lad-retained-verdict/v1`:
  `format`, `occurrence_id`, `requirements_sha256`, `source_sha256`, `verdict`)
  keyed by exactly the retained active requirement IDs, each `{status,
  evidence_refs}`. A requirement leaves the retained set only through an exact
  `Supersedes: <id>` request line quoted by a current criterion.
