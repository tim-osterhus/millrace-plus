# Workflow views: generic output records

`millrace_plus.workflow_views` (`millrace-workflow-views/v1`) stores small
immutable `millrace-workflow-output/v1` records that a trusted operator-side
publisher produced for an exact run. OS lists and reads them as display
evidence. A record proves neither task success, ownership, writer closure nor
an actual join. Its producer ID is a claim by the local publisher, not
authenticated provenance: any same-user process can write these files.

The helper is generic. It has no workflow-name logic and validates no domain
payload. It is not a scheduler, coordinator, watcher or artifact store, and it
never launches a producer or changes Core state.

## Public functions

```python
canonical_json_bytes(value) -> bytes
publish_output(record, *, state_home) -> dict
list_outputs(instance_id, run_id, *, state_home, limit=50, cursor=None) -> dict
read_output(instance_id, run_id, output_id, record_sha256, representation,
            *, state_home) -> dict
WorkflowViewsError.code
```

`state_home` is the absolute router state home. Its final path component must
be the directory itself, not a symlink: pass the real directory path. A symlink
or non-directory there refuses `output_integrity_error`. Its ancestors are not
checked. A missing state home is an empty listing and a not-found read.

Only `publish_output` reads Core, and only through the public Plus control
facade (`inspect_run_page` with an exact `invocation_id`). The facade is
imported when publication runs.
`list_outputs` and `read_output` verify stored bytes and import no Core module.

## Record

All keys are required. Unknown keys and coerced types refuse.

| Key | Value |
| --- | --- |
| `format` | `millrace-workflow-output/v1` |
| `output_id` | `[a-z0-9][a-z0-9_-]{0,63}`, unique within the run |
| `instance_id`, `run_id`, `invocation_id` | 32 lowercase hex characters |
| `plane_id`, `node_id` | Core plane and node identifiers |
| `producing_revision_id` | 64 lowercase hex characters |
| `producer` | `{id, version}`, supplied by the publisher |
| `label` | Plain text, 1–256 characters, one line |
| `published_at` | `YYYY-MM-DDTHH:MM:SS[.ffffff]` then `Z` or `+00:00` |
| `representations` | `{report}` or `{report, json}` |

Each representation is `{format, media_type: "application/json", sha256,
data}`. `sha256` is the digest of `canonical_json_bytes(data)`.

- **`report`:** its format is `millrace-report-display/v1`. Its data has
  exactly `title`, `paragraphs`, `facts` (`label`, `value`), `tables`
  (`caption`, `columns`, `rows`) and `sources` (`label`, `url`). Every value is
  plain text. A table has at least one column, and each row has one cell per
  column. There are no count or length caps beyond the record limit.
- **`json`:** optional. Its format is any producer-owned format ID, and its
  data is any JSON value.

The canonical record is at most `RECORD_LIMIT` (1,047,552) bytes, or
publication refuses `output_too_large`.

## Canonical JSON

`millrace-canonical-json/v1` is Python `json.dumps` with `sort_keys=True`,
`ensure_ascii=False`, `allow_nan=False` and `separators=(",", ":")`, then UTF-8
with no trailing newline. It refuses non-string keys, NaN and Infinity,
unpaired surrogates and non-JSON types. Unicode is not normalized. This rule is
not RFC 8785; producers in other languages should use the helper or its
returned digests.

Saved record files are parsed strictly: UTF-8 only, with no duplicate keys and
no NaN or Infinity.

## Publishing

1. **Find the exact invocation.** A publisher holds the instance and run IDs
   from its own create and Start receipts, or from explicit operator input. It
   either takes an explicit invocation ID, or selects the one producing
   completion at an authored boundary that it pins. For example, at an
   authored plane pause gate it pins:
   - the plane and gate reason;
   - the observed run state version;
   - the single transition from the producing node to that gate since the
     previous pinned version.

   It keeps that completion's invocation and producing revision. Two
   candidates at the same pinned boundary, or missing boundary facts, mean
   refusing. Never pick the newest completion.
2. **Save the descriptor first.** Save the complete record, including
   `published_at`, before publishing. Every retry reuses those exact bytes.
3. **Publish:** `publish_output(record, state_home=...)` or
   `millrace-plus views publish --file RECORD --state-home PATH`.

`publish_output` proceeds in this order:

1. It validates the record and computes canonical bytes and digests
   (`invalid_output_record`, `output_too_large`).
2. It requires exactly one invocation row whose plane, node and producing
   revision equal the record's (`output_identity_mismatch`). Core errors
   propagate unchanged, including the whole-instance reader budget.
3. It opens or creates the owned directories
   `workflow-views/instances/{instance}/runs/{run}` under the state home, with
   mode 0700. Each component must be a real directory, not a symlink, owned by
   this user and not group/other writable.
4. It writes `.publish-{32 hex}`, mode 0600, and fsyncs it.
5. It links the temporary file to `{output_id}.json`, never overwriting. An
   existing identical record is `already_published`. Any other existing record
   is `output_conflict`, and its bytes stay unchanged.
6. It removes the temporary file.
7. It fsyncs the fixed directory chain, which holds every entry on the record
   path. Each item below is fsynced because it holds the entry above it:
   - the run directory holds the record;
   - `runs` holds the run directory;
   - the instance directory holds `runs`;
   - `instances` holds the instance directory;
   - `workflow-views` holds `instances`;
   - the state home holds `workflow-views`.

   Both `published` and `already_published` fsync this whole chain, including
   directories that an earlier uncertain attempt created.
8. It checks that the state home's `workflow-views` entry is still the root it
   opened. Open directory descriptors cannot be redirected, but the root can
   be removed, replaced or made a symlink after opening. A record linked under
   a root that is no longer selected is never acknowledged.

The result is `{reference: {instance_id, run_id, output_id, record_sha256},
report_sha256, json_sha256 | null, status}`.

What a failure leaves:

- **Before the link:** no visible record. The temporary file is removed, or
  ignored after a crash. Ordinary I/O errors propagate.
- **After the link:** `output_publication_uncertain`. This includes a root
  found removed, replaced or symlinked at step 8. The record may be visible,
  but it is not acknowledged durable. Nothing is rolled back. Retry with the
  same saved descriptor:
  - it returns `already_published` and completes the fsyncs;
  - or it returns `published` if the earlier link went under a replaced root.

There is no journal, no rollback, no reaper and no mutable "latest" record. A
revised report uses a new output ID.

The CLI prints the result as JSON. A refusal prints `{"code", "message"}` on
stderr and exits 2: helper and Core codes, `io_error` or `usage_error`. The CLI
never regenerates timestamps and never retries.

## Listing and reading

`list_outputs` scans the run directory:

- `.publish-{32 hex}` entries are ignored.
- `{output_id}.json` regular files are items, sorted by output ID.
- Any other entry (another name, a directory or a symlink) refuses the whole
  listing with `output_integrity_error`.
- A missing run directory is an empty page.

`limit` is 1–50. The cursor is `o` + 40 hex characters of the digest of the
exact output-ID set + `_` + the last output ID. After any later publication
an old cursor refuses `outputs_changed`.

Every listed record is fully verified:

- **The file:** opened without following symlinks; regular, owned by this
  user, mode 0600 and within the size limit.
- **The bytes:** strictly parsed, a valid record, and exactly its canonical
  encoding.
- **The path:** its instance, run and output IDs equal the record's.
- **The data:** the representation digests match.

An item that fails shows `availability: "unavailable"` and `reason:
"output_integrity_error"`, with every field except `output_id` null.

Before a listing or read returns, the helper also checks that the state home's
`workflow-views` entry is still the root it opened. A root removed, replaced or
made a symlink after opening refuses `output_integrity_error`.

`read_output` needs the exact stored `record_sha256` and `report` or `json`. It
refuses as follows:

| Condition | Code |
| --- | --- |
| No record at that path | `output_not_found` |
| Changed or invalid bytes, or a different digest | `output_integrity_error` |
| No `json` representation | `output_representation_unavailable` |

## Costs and limits

These are reader limitations, not capacity claims:

- **List:** each page scans every entry in the run directory and fully
  verifies up to `limit` records.
- **Read:** verifies one record.
- **Publish:** pays Core's whole-instance verification once, plus one record
  write and the fsyncs. It can therefore refuse `inspection_too_large`
  because of unrelated history in the same instance.

Records stay with their state home. There is no expiry, quota or retention
service.
