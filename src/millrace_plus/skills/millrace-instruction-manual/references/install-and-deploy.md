# Install And Deploy Readiness

Use this reference when local Millrace setup is uncertain. It is a local
operator readiness checklist, not release engineering, PyPI publishing,
marketplace, daemon supervision, or OS service setup guidance.

## CLI Surface

Validate the installed command before operating:

```text
millrace --version
millrace --help
millrace --json --help
millrace workspace --help
millrace package --help
millrace plan --help
millrace queue --help
millrace run daemon --help
```

When operating from an editable checkout, run the same help checks through the
checkout's Python environment. If installed help differs from these skill
docs, installed help wins; report the drift instead of guessing.

## Workspace Readiness

Prefer explicit workspace paths:

```text
millrace --workspace PATH workspace init --input-id ID
millrace --workspace PATH workspace check
```

Do not assume the current directory is the intended workspace unless the
operator or task context says so. If `workspace check` fails, report the
workspace path, command, error code/message, and whether initialization was
requested.

## Package And Plan Readiness

Before enqueueing work, confirm the operator supplied or approved the package
source and workflow selection:

```text
millrace --workspace PATH package import-path PATH --command-id ID
millrace --workspace PATH package import-archive PATH --command-id ID
millrace --workspace PATH package import-installed DISTRIBUTION --resource-root ROOT --command-id ID
millrace --workspace PATH package verify PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID
millrace --workspace PATH package select-workflow PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID
millrace --workspace PATH plan admit-package PACKAGE_ID PACKAGE_VERSION --workflow-id ID --workflow-version VERSION --entrypoint NAME --command-id ID --input-id ID
millrace --workspace PATH plan show [FINGERPRINT]
```

Package import, verify, and select-workflow are not the same as admitting or
selecting a plan. If no plan is admitted or defaulted, stop and report the
missing plan state rather than enqueueing against an assumed workflow.

Do not run `package update`, `package disable`, `package remove`, or
overwrite/export operations unless the operator explicitly requested package
maintenance. Package changes affect future selection and compile, not
already-launched active runs pinned to selected authority.

## Runner And Adapter Readiness

Check the daemon command surface before running:

```text
millrace run daemon --help
millrace --workspace PATH run daemon --max-ticks 1
```

The public default is bounded execution with `--max-ticks 1`. Run an unbounded
daemon only when the operator explicitly wants unattended progression and you
have inspected workspace health, selected package/workflow, selected or default
plan, and runner/adapter configuration.

Treat `--adapter-config-json PATH` as a local operator config path. Do not put
API keys, OAuth tokens, local credential paths, provider secrets, or adapter
config secrets in workflow packages, prompt assets, skill files, or evidence
reports. Adapter config is local operator config, not package authority.

## Missing Setup Report

When local setup is missing, report:

```text
workspace: <PATH or not selected>
missing: cli|workspace|package|plan|runner|adapter|help-drift
checked:
- <command and result>
operator_action_needed: <specific local setup or decision>
not_done: <what was not attempted>
```

Do not install packages, publish artifacts, create services, or store
credentials through a workflow package unless the operator has given a separate
explicit instruction outside this public readiness checklist.
