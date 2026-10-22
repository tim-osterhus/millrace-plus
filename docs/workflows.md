# Bundled graph-v3 workflows

The installed catalog contains `software-change`, `document-review`, and `lad`. All are editable physical copies with their own graph, prompts and scripts. Use `millrace-plus catalog init bundled:NAME DESTINATION` to create a copy. Existing copies survive package replacement unchanged.

## Small examples

`software-change` and `document-review` are small editable examples under `millrace_plus/workflows/`. Initialize a physical copy with the [local catalog](catalog.md). Both use the accepted Claude example binding with model/effort omitted, native settings inherited, and exact Claude Code 2.1.296. They do not mandate a methodology or ICM layout.

Each graph explicitly declares `planes.main` with `workspace: "."`. Node cwd, inputs and result refs resolve under that workspace. Targeted controls, retries and writer reconciliation require `--plane main`; run pause/cancel remain global. Use fresh v7 state with matched execution/v2 artifacts.

The normal path is input check → work → independent check → original-goal review. A check can request one repair/recheck; repeated failure pauses. A planning defect routes to a distinct diagnosis and pause. Missing input and blocked work pause explicitly. Each project supplies `request.md` with the original goal and acceptance criteria. Software work edits requested files; document work writes `document.md`. Reports are ordinary `work/*.md` files.

For helper-driven calls, the stock input check also reads `subworkflow-inputs.json` with format `millrace-plus-subworkflow-inputs/v1` and a nonempty `inputs.request_markdown`. It creates `request.md` from the exact UTF-8 text bytes, without trimming or adding a newline. Re-running with identical content is safe. A conflicting existing file, malformed manifest, or blank request returns `NEEDS_INPUT` without overwriting the file. Direct launches without a manifest continue to require an existing nonempty `request.md`.

Each example uses 16 invocations, a 24-hour elapsed deadline and an explicit 1,800-second invocation timeout. Values are editable graph settings. Pauses consume wall time and continuation retains used bounds. An expired run requires a fresh run rather than reset-by-resume. An uncertain `may_live` writer retains its hold: verify the writer is stopped, reconcile through supported controls, then resume within remaining bounds. Never blindly replay an uncertain writer.

The existing Claude permission bypass runs in the project root. Native auto-update may fail the accepted exact-version check; changing the profile cannot bypass it. See each template README for practical inputs, outputs, controls and recovery.

## LAD software factory

`lad` preserves twelve roles from pinned LAD Factory Markdown: Recon, Planner, Manager, Builder, Checker, Fixer, Doublechecker, Troubleshooter, Consultant, Updater, Learning and Arbiter. Its copied `PROVENANCE.md`, `provenance.json` and Apache-2.0 source license describe that reuse.

Planning and Execution share one sequential foreground plane. A separate Learning plane consumes committed handoffs. After the second evaluated Fixer→Doublechecker pair, including a pass, foreground saves its continuation and waits for the matching Learning acknowledgement. This gate claims no actual overlap. Learning proposals remain inactive in its separate write area.

The graph declares 609 total node invocations and a 35,900-second elapsed ceiling. Its separate retained LAD-session ledger permits at most 60 role dispatches and preserves failed, interrupted and uncertain attempts. The copied README derives the node ceiling and documents narrower per-role bounds. These workflow limits grant no native calls or spending.

Every launch begins with graph-owned session bootstrap and deterministic intake. A valid helper or Paperclip assignment maps exact `inputs.request_markdown` bytes into `request.md`. Direct CLI, OS and MCP launch requires an existing nonempty server-local `request.md`. There is no upload or external setup hook. Follow the copied README for `foreground/` and `learning/` project directories and existing-project or greenfield setup.

A new Core run on a used LAD copy pauses with the retained session and continuation. It never resets counters or automatically replans. Ordinary within-run resume/recovery retains the original cursor. Follow copied `context/harness.md` for supported replacement preparation, CLOSE/ACK and exact Learning-plane cleanup after a hard stop.

Deterministic peers provide offline workflow evidence. Native LAD execution, a real Paperclip account and remote MCP deployment require separate qualification. The catalog does not establish those routes by copying their assets.

The historical six-workflow compiler manifest and `millrace_workflow_package` resource API are absent from this release. Their matching v0.22 source and guides remain in preserved Git commit `ad91c7f`. They do not define current template or runtime requirements.
