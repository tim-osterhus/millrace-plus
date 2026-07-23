# Release Line

Millrace Plus source and package use the v0.22.0 release line.

## Current Source Package

| Field | Value |
| --- | --- |
| Distribution | `millrace-plus` |
| Source version | `0.22.0` |
| Workflow package ID | `millrace.plus.official` |
| Installed resource root | `millrace_workflow_package` |
| Python | 3.11 or newer |
| License | Apache-2.0 |
| Runtime dependency | None |

The package contains these workflow entries at version `0.1`:

- `simple_loop`
- `execution.lad`
- `execution.lad_integrator`
- `planning.lad`
- `lad.full`
- `vendor_selection`

It also contains the `millrace-instruction-manual`,
`millrace-loop-configuration`, and `millrace-entrypoint-authoring` advisory
skills.

## Package Boundary

The wheel contains workflow data, agent skills, package metadata, and nothing
that executes Millrace. It has no CLI, daemon, runner, provider integration,
plugin registration, marketplace client, post-install hook, or dependency on
`millrace-ai`.

The `millrace==0.22.0` convenience meta-distribution selects and installs this
exact tested combination: `millrace-ai==0.22.0`, `millrace-plus==0.22.0`, and
`millforge==0.1.0`. Direct `millrace-plus` installation remains useful for
tools that only need to inspect or distribute the package data.

Member distributions version independently. Each `millrace`
meta-distribution release pins one tested combination and may reuse an
unchanged compatible member.

## Release Validation

Release validation includes:

1. run the standalone checks in [Validation](public-validation.md);
2. build and inspect the wheel and source archive;
3. verify installation and resource discovery from the built wheel;
4. confirm that the matching Millrace runtime can import and run each selected
   workflow;
5. publish only through the repository's reviewed release workflow.

Build a local candidate with:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  uv build --out-dir /tmp/millrace-plus-build --force-pep517
```

A local build is a release candidate, not publication evidence.
