# Document review

An editable graph-v3 example for a bounded document task. Create a project folder and put the original goal, source materials and acceptance criteria in a nonempty `request.md`. Initialize or copy this template into the project workflow location, then create an instance and run it with the matched Plus/Core installation.

Use the exact matched release artifacts qualified by their P9 acceptance receipt.

The graph declares one `main` plane with `workspace: "."`; its node paths resolve under the project root. Plane controls and writer reconciliation select `--plane main`. Use fresh v7 state with the matched execution/v2 Core and Plus; earlier graphs and stores require their matching historical artifacts.

The work stage creates or revises `document.md` from the provided request and project sources. Independent check and original-goal review inspect that document and its evidence. If the check finds a fixable gap, the graph permits one repair and an independent recheck; a recheck gap pauses. A planning defect goes to a separate diagnosis report and pauses. Missing request, blocked work, execution errors and unknown results also pause.

Outputs are `document.md`, `work/check.md`, `work/recheck.md` when repair occurs, `work/goal-review.md`, and `work/diagnosis.md` when needed. Checkers write reports, never the document. If required sources are not in the project, provide them or answer the workflow’s input request rather than asking it to invent facts.

```json
{"format":"millrace-claude-print-profile/v1","claude_version":"2.1.296"}
```

The adapter requires the claude executable to report exact Claude Code 2.1.296 and the profile to match that pin. Changing the profile version cannot bypass the adapter's version check; an auto-update needs a newly reviewed adapter before this route can run. Model and reasoning effort are omitted, so the adapter inherits the native Claude configuration. Authentication, provider settings, hooks, MCP and plugins remain native and are not copied into the workspace.

The accepted native launch includes `--dangerously-skip-permissions --permission-prompts none` and runs from the project root. Treat it as permission-bypassed; there is no Millrace approval bridge. Use a disposable or version-controlled project and inspect the requested scope before starting.

The P4c qualification is specific to its recorded selected route and exact version; it does not qualify other routes or model settings. Adapter API behavior has fake-peer evidence only, and the HTTP status for startup cancellation remains unknown. When a writer is `may_live`, its project hold remains: verify it has stopped, then use the guarded paused-run reconciliation with `--stopped`. That records effective stopped; the raw completion remains `may_live`. Never blindly retry an uncertain writer. Reconciliation and any continuation use the run's remaining invocation and elapsed-time bounds.

## Run limits and native behavior

Each run allows at most 16 invocations and 86,400 seconds (24 hours) of elapsed wall time; paused time counts. Each invocation defaults to 1,800 seconds. Execution errors retry once and then pause; unknown outcomes pause. A failed recheck pauses without another repair cycle. Resume does not reset consumed limits. After a bound expires, stop/cancel the old run as appropriate and create a fresh run.

The accepted Claude profile is pinned to exact version 2.1.296; profile edits cannot override that pin. The omitted model/effort inherit native Claude configuration. The adapter's accepted bypass flags mean there is no prompt approval step. The template does not qualify arbitrary OAuth/API routes: the recorded P4c selected-route qualification applies only to its exact route/version, API behavior is fake-peer-only, and startup-cancel HTTP status is unknown.

An uncertain writer keeps its project hold when the raw completion is `may_live`. Verify the process is stopped before the guarded `reconcile --stopped` operation; successful reconciliation records effective stopped while preserving the original raw `may_live` fact. Do not retry an uncertain writer or claim reconciliation is a universal launch barrier. On unsafe instances, the Plus guard assumes no pre-existing queued controls and no concurrent direct-Core mutator/controller; those conditions are unsupported.
## Editing the binding

The bundled graph uses ordinary `workflow.json`, profile and prompt files. To use another accepted binding, edit those files together and update `control_files` so every referenced profile, prompt and script is declared. These examples show accepted field names and versioned contracts; replace symbolic aliases/settings with values actually configured on the host before validating. Init itself does not probe executables, auth or versions.

The built-in command shape is `{"adapter":"command","contract":"millrace-command/v1","executable":"python3","config":{}}`; a command node uses `"config":{"script":"scripts/step.py","args":[]}`.

A Pi JSON binding uses `{"adapter":"pi_json","contract":"millrace-pi-json/v1","executable":"pi","config":{"profile":"profiles/pi.json"}}`. Its profile shape begins `{"format":"millrace-pi-json-profile/v1","pi_version":"0.87.0","agent_dir":"YOUR_AGENT_DIR_ALIAS"}`. Provide effective `provider`, `model`, and `thinking` values in the profile, workflow or plane defaults, node overrides, or selected runtime defaults. `agent_dir` names an explicit host alias supplied through instance `host_paths`; optional `loader` is another declared host alias. Do not copy Pi credentials.

A Codex exec binding uses `{"adapter":"codex_exec","contract":"millrace-codex-exec/v1","executable":"codex","config":{"profile":"profiles/codex.json"}}`. Its profile requires `format:"millrace-codex-exec-profile/v1"`, `codex_version:"0.163.0"`, a `codex_home` host alias, `auth_mode:"chatgpt"`, and a supported `sandbox` value; any model and `reasoning_effort` must be explicit valid values when required by your selected settings. Supply the alias in `host_paths`, pointing to the configured Codex home with its auth file.

Pi and Codex keep their accepted explicit profiles, credential/home aliases and effective model settings. They do not inherit Claude settings semantics by analogy. These shapes describe the accepted adapter contracts, not a qualification of a different native route.
