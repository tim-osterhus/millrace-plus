# Bundled graph-v3 workflows

`software-change` and `document-review` are small editable examples under `millrace_plus/workflows/`. Initialize a physical copy with the [local catalog](catalog.md). Both use the accepted Claude example binding with model/effort omitted, native settings inherited, and exact Claude Code 2.1.296. They do not mandate a methodology or ICM layout.

Each graph explicitly declares `planes.main` with `workspace: "."`. Node cwd, inputs and result refs resolve under that workspace. Targeted controls, retries and writer reconciliation require `--plane main`; run pause/cancel remain global. Use fresh v7 state with matched execution/v2 artifacts.

The normal path is input check → work → independent check → original-goal review. A check can request one repair/recheck; repeated failure pauses. A planning defect routes to a distinct diagnosis and pause. Missing input and blocked work pause explicitly. Each project supplies `request.md` with the original goal and acceptance criteria. Software work edits requested files; document work writes `document.md`. Reports are ordinary `work/*.md` files.

Each example uses 16 invocations, a 24-hour elapsed deadline and an explicit 1,800-second invocation timeout. Values are editable graph settings. Pauses consume wall time and continuation retains used bounds. An expired run requires a fresh run rather than reset-by-resume. An uncertain `may_live` writer retains its hold: verify the writer is stopped, reconcile through supported controls, then resume within remaining bounds. Never blindly replay an uncertain writer.

The existing Claude permission bypass runs in the project root. Native auto-update may fail the accepted exact-version check; changing the profile cannot bypass it. See each template README for practical inputs, outputs, controls and recovery.

The historical six-workflow compiler manifest and `millrace_workflow_package` resource API are absent from this release. Their matching v0.22 source and guides remain in preserved Git commit `ad91c7f`. They do not define current template or runtime requirements.
