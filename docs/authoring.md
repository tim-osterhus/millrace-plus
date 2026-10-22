# Authoring editable router workflows

Use Core and Plus 0.23.0 with the public execution-v2 contract.

Start with an initialized folder or a user-authored `workflow.json`. Use `format: millrace-router/v3`, declared bindings, explicit planes, local nodes, outcomes, unambiguous routes and explicit unknown/error fallbacks. Meaningful branch/retry decisions belong in that graph; executors return outcomes and work artifacts.

A plane requires `start`, `nodes` and `workspace`. The stock graphs explicitly declare `planes.main` with `workspace: "."`. To reauthor an old graph, move its top-level `start` and `nodes` under that declaration and set the v3 marker. Use fresh v7 state; no command migrates or wraps an old graph/store. Plane/workspace/seam/dependency topology and maximum concurrency remain fixed for a run.

`control_files` lists every script, prompt and native profile relative to the workflow folder. Command binding `config` is `{}`; command node `invoke.config` contains `script`/`args`. Semantic Claude nodes name a prompt. `cwd`, `inputs` and result refs are relative to the selected plane workspace. Generated inputs are checked at the consuming dispatch. The catalog checks only the format marker; use the matched router's `validate` operation for full diagnostics.

Runtime defaults → workflow defaults → selected plane defaults → node overrides resolve under operator pins/ceilings. Invalid explicit values fail. The missing-timeout fallback is 1,800 seconds with a warning only when no effective value exists. Stock graphs supply an explicit 1,800 seconds and finite limits of 16 invocations/86,400 elapsed seconds. Paused wall time counts; edits and resume do not replenish budgets.

Edit ordinary JSON, prompts, scripts and profile files. There is no runner-selection DSL, required ICM index, paired core skill or universal artifact schema. Stock examples choose Claude; command/Pi/Codex rebinding requires their actual distinct configuration, listed in [Router adapters](router-adapters.md). Keep only selected bindings and their declared controls; do not invent native credentials or endpoints.

Add a stage and branch within a plane by adding a node, declared outcome/transition and any required controls. Locked mode is the default and hard-pauses on a control change. Explicit live mode drains active invocations, then activates one compatible revision before routing retained results in stable plane order. Completed results keep their producing launch contract and route under the eligible routing revision. Invalid changes preserve the result and pause for intervention.

Independent checkers read the original request and actual work; they write reports, not the request or work product. A repair is followed by an independent recheck. Planning defects route to diagnosis/pause. The goal review checks original acceptance criteria. A declared outcome chooses an edge; inspect artifacts to judge quality.
