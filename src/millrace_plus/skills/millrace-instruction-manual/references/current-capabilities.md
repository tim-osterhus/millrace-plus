# Current Capabilities

Use this reference before naming a command, capability, or non-goal in an
operator report. The installed CLI help and CLI tests are the source of truth;
if they disagree with this reference, use the installed help and record the
doc drift.

## Implemented Command Tree

Global options:

```text
millrace [--version] [--json] [--no-color] [--workspace PATH] [--db PATH] [--cas PATH] [--actor-id ACTOR] command ...
```

Prefer explicit `--workspace PATH` for workspace-scoped operation. Use the
current directory only when the operator or surrounding task context identifies
it as the intended workspace.

The command blocks below show subcommand syntax. For real workspace-scoped
operation, prefix them with `millrace --workspace PATH`.

Workspace:

```text
millrace workspace init --input-id ID
millrace workspace check
```

Package:

```text
millrace package import-path PATH --command-id ID
millrace package import-archive PATH --command-id ID
millrace package import-installed DISTRIBUTION --resource-root ROOT --command-id ID
millrace package update PACKAGE_ID PACKAGE_VERSION --from-path PATH --command-id ID
millrace package list --command-id ID
millrace package inspect PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace package export-archive PACKAGE_ID PACKAGE_VERSION --output PATH --command-id ID
millrace package enable PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace package disable PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace package remove PACKAGE_ID PACKAGE_VERSION --command-id ID
millrace package verify PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID
millrace package select-workflow PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID
millrace package doctor PACKAGE_ID PACKAGE_VERSION --command-id ID
```

Do not run `package update`, `package disable`, `package remove`, or
overwrite/export operations unless the operator explicitly requested package
maintenance. Normal operation should prefer inspect, verify, select-workflow,
admit-package, enqueue, status, runs, trace, waits, interventions, and doctor
commands.

Plan:

```text
millrace plan admit --compiled-plan-json PATH --input-id ID
millrace plan admit-package PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID --input-id ID
millrace plan select-default FINGERPRINT --input-id ID
millrace plan show [FINGERPRINT]
```

Queue and projections:

```text
millrace queue enqueue QUEUE_FAMILY --payload-json JSON [--plan-fingerprint FINGERPRINT] [--input-id ID]
millrace queue enqueue QUEUE_FAMILY --payload-file PATH [--plan-fingerprint FINGERPRINT] [--input-id ID]
millrace queue enqueue QUEUE_FAMILY --payload-stdin [--plan-fingerprint FINGERPRINT] [--input-id ID]
millrace queue list
millrace status [--plan-fingerprint FINGERPRINT] [--max-events N]
millrace runs list
millrace runs show RUN_ID
millrace trace show [RUN_ID] [--max-events N]
millrace doctor
```

Waits, interventions, and dispatch:

```text
millrace waits list
millrace waits resume WAIT_ID [--payload-json JSON] [--input-id ID]
millrace waits close WAIT_ID [--payload-json JSON] [--input-id ID]
millrace waits revise WAIT_ID [--payload-json JSON] [--input-id ID]
millrace interventions list
millrace interventions resume-lineage OPTION_ID --reason TEXT [--quarantine-id ID] [--lineage-id ID] [--payload-json JSON] [--input-id ID]
millrace interventions close-lineage OPTION_ID --reason TEXT [--quarantine-id ID] [--lineage-id ID] [--payload-json JSON] [--input-id ID]
millrace interventions revise-lineage OPTION_ID --reason TEXT [--quarantine-id ID] [--lineage-id ID] [--payload-json JSON] [--input-id ID]
millrace dispatch claim ACTIVATION_ID [--claim-id CLAIM_ID] [--input-id ID]
millrace dispatch show RUN_ID
```

Runner execution:

```text
millrace run daemon [--idle-sleep SECONDS] [--max-ticks N] [--activation-id ID] [--adapter-kind KIND] [--adapter-config-json PATH] [--monitor none|basic]
```

## Implemented Local-Operator Surfaces

- Workspace store/CAS initialization and read-only check.
- Package import from path, archive, or installed distribution resource bytes.
- Package update, list, inspect, archive export, enable, disable, remove,
  verify, workflow selection, and package doctor projection.
- Plan admission from a compiled-plan export, package-backed plan admission,
  default-plan selection, and admitted-plan metadata display.
- Queue enqueue through selected workflow authority and queue status listing.
- Root status projection, run list/show, trace show, and root workspace doctor.
- Wait list/resume/close/revise through operator wait intake.
- Lineage intervention list/resume/close/revise through operator intake.
- Dispatch claim and read-only dispatch envelope display.
- Local daemon execution through bounded runner units.

## Authoring And Verification Surfaces

- Workflow packages may be imported from local package roots, archives, or
  installed distribution resource roots.
- `package verify` checks a workflow selection without admitting a plan.
- `package select-workflow` compiles a workflow package selection without
  admitting it.
- `plan admit-package` compiles a package workflow selection and admits the
  selected plan.
- Package assets are selected by package/workflow data and digest pins, not by
  prompt text or local filenames.

## Unsupported Or Deferred Surfaces

These are not public base-runtime commands unless installed help says
otherwise:

- `millrace run once`;
- root `millrace tick`;
- public `millrace observe`;
- `millrace dispatch invoke`;
- `millrace run daemon status` or `millrace run daemon stop`;
- a dedicated `approvals` command group;
- remote management, Millrace OS control, or multi-tenant control;
- package marketplace upload, download, or remote install;
- broad plugin/MCP runtime or package-distributed native runner code;
- provider credential distribution through workflow packages;
- API keys, OAuth tokens, local credential paths, provider secrets, or adapter
  config secrets in workflow packages, prompt assets, skill files, or evidence
  reports;
- package-owned executable discovery or PATH probing;
- source-intake watchers, mailbox daemons, or live-tail dashboards;
- compatibility readers or migrations for older workspace stores;
- manual public evidence ingestion outside daemon execution.

## Non-Goals

- CLI labels do not create workflow meaning.
- Display names, folders, filenames, and LAD-style names are not kernel
  vocabulary.
- Examples are not generic proof that a field, command, or runtime behavior is
  available everywhere.
- Skill prose and prompt text do not decide legal routes, terminal actions,
  package selection, queue movement, approvals, effects, durable writes, or
  recovery behavior.
