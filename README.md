# Millrace Plus

**Millrace Plus is the official collection of ready-to-run Millrace workflows
and agent-authoring skills.**

Millrace itself is a generic workflow runtime. This package supplies the
workflow definitions, entrypoint prompts, stage skills, and examples that make
the runtime useful out of the box. It contains data only: no daemon, CLI,
runner, provider, plugin, or installation hook.

> **Status:** Millrace Plus is being prepared for its first v0.22.0 release.
> The source manifest still uses the staging package version `0.0.0`, and the
> distribution is not yet published.

## Included Workflows

| Workflow | What it is for |
| --- | --- |
| `simple_loop` | A compact Management, Implementation, and Review loop with recovery behavior |
| `execution.lad` | The LAD software-execution loop |
| `execution.lad_integrator` | LAD Execution with an additional integration stage |
| `planning.lad` | LAD Planning followed by Execution |
| `lad.full` | Full LAD Planning, Learning, and Execution |
| `vendor_selection` | A four-plane research and comparison loop that stops for an operator decision |

Every workflow is ordinary package data. Names such as Management, Planning,
Execution, Learning, and Review do not have special meaning inside the
Millrace kernel.

Read [Workflow guide](docs/workflows.md) for the intended use and shape of
each configuration.

## Included Agent Skills

Millrace Plus also ships three guides for agents that operate or configure
Millrace:

- [`millrace-instruction-manual`](src/millrace_plus/skills/millrace-instruction-manual/SKILL.md)
  explains when to use Millrace and how to operate it through the CLI.
- [`millrace-loop-configuration`](src/millrace_plus/skills/millrace-loop-configuration/SKILL.md)
  helps turn a fully specified decision tree into a valid workflow package.
- [`millrace-entrypoint-authoring`](src/millrace_plus/skills/millrace-entrypoint-authoring/SKILL.md)
  explains how to write stage prompts and their paired core skills.

These files are advisory documentation. Installing the package does not copy
them into Codex, Claude Code, or another agent's skill directory.

## How The Package Is Used

The workflow package lives under `millrace_workflow_package/`. Its manifest
declares package ID `millrace.plus.official`, the selectable workflows, and
the exact path, size, and digest of every workflow asset.

Millrace imports those files as bytes, validates them, and compiles one
selected workflow entrypoint. It does not import `millrace_plus` modules or
execute package code to discover workflows.

The installed resource root is `millrace_workflow_package`; package data is
non-executable. A direct installation contains package metadata and data only
and does not transitively install `millrace-ai`; the `millrace` convenience
package will install both tested distributions together.

## Authoring Model

Each agent stage normally selects two text assets:

| Asset | Responsibility |
| --- | --- |
| Entrypoint prompt | Role, scope, process, evidence, legal terminal markers, and stop conditions |
| Stage-core skill | Exact artifact schemas, handoff formats, examples, validation, and completion criteria |

The workflow graph owns routing, queue movement, retries, waits, and
completion. Prompt text can explain those rules to an agent, but it cannot
create them.

Start with [Authoring workflows](docs/authoring.md). The frozen manifest rules
are documented in [Manifest maintenance](docs/manifest-authoring-policy.md).

## Repository Layout

```text
millrace_workflow_package/
  manifest.json
  assets/workflows/
src/millrace_plus/skills/
docs/
tests/
```

`millrace_workflow_package/` is the runtime-readable workflow package.
`src/millrace_plus/skills/` contains the advisory agent skills outside the
workflow manifest.

## Validation

The package can be validated without a Millrace source checkout:

```bash
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  uv run --no-project --with pytest --with hatchling pytest -q \
    tests/test_package_metadata.py \
    tests/test_manifest_authoring_policy.py \
    tests/test_official_package_layout_plan.py \
    tests/test_workflow_package_manifest.py \
    tests/test_workflow_package_installed_smoke.py \
    tests/test_public_package_boundary.py \
    tests/test_agent_skill_assets.py
```

See [Validation](docs/public-validation.md) for build and artifact checks, and
[Release status](docs/release.md) for the current packaging state.

## License

Millrace Plus is licensed under the [Apache License 2.0](LICENSE).
