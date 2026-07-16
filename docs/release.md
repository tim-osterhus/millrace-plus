# Release Status

Millrace Plus is being prepared for its first v0.22.0 release. It is not yet
published.

## Current Source Package

| Field | Value |
| --- | --- |
| Distribution | `millrace-plus` |
| Source version | `0.0.0` staging value |
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

The `millrace` convenience package will pin and install tested versions of
`millrace-ai` and `millrace-plus` together. Direct `millrace-plus` installation
remains useful for tools that only need to inspect or distribute the package
data.

The three packages version independently after the first release. An unchanged
compatible Plus package can be reused by later Millrace bundles without being
republished solely to match the runtime version.

## Before Publication

Release preparation must:

1. replace staging metadata with the selected release version;
2. run the standalone checks in [Validation](public-validation.md);
3. build and inspect the wheel and source archive;
4. verify installation and resource discovery from the built wheel;
5. confirm that the matching Millrace runtime can import and run each selected
   workflow;
6. publish only through the repository's reviewed release workflow.

Build a local candidate with:

```bash
PYTHONDONTWRITEBYTECODE=1 \
  uv build --out-dir /tmp/millrace-plus-build --force-pep517
```

A local build is a release candidate, not publication evidence.
