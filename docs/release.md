# Release Line

The `millrace-plus` 0.22.3 release accompanies Core 0.22.3 and Millforge 0.1.1
in the exact `millrace==0.22.3` bundle.

## 0.22.3 scope

The package targets runtime compatibility `>=0.22.3,<0.23` and contains six
ordinary workflow entries. The
experimental `execution.lad_codex_semantic_worktree` workflow is deferred: it
is not selectable and its exclusive assets are absent from the manifest, wheel,
and source archive. The preserved campaign evidence is inconclusive and does
not establish semantic-workspace efficacy or incompatibility.

Generic governed-context mechanisms remain a runtime capability for workflows
that independently select them; this Plus release does not claim a semantic
LAD workflow release. Unreleased schema-17 plans are historical evidence, not
compatible plans.

## Current Source Package

| Field | Value |
| --- | --- |
| Distribution | `millrace-plus` |
| Source version | `0.22.3` |
| Release line | 0.22.3 |
| Workflow package ID | `millrace.plus.official` |
| Installed resource root | `millrace_workflow_package` |
| Python | 3.11 or newer |
| License | Apache-2.0 |
| Runtime dependency | None |

The package contains six workflow entries, all version `0.1`:

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

The `millrace==0.22.3` bundle pins Core and Plus 0.22.3 with Millforge 0.1.1.
Direct `millrace-plus` installation remains useful for tools that only need to
inspect or distribute the package data; maintainer qualification uses the
explicit Core and Millforge wheel paths in
[Validation](public-validation.md).

Member distributions version independently. Each `millrace`
meta-distribution release pins one tested combination and may reuse an
unchanged compatible member. A future bundle must publish and pin its own
combination before it can be used as a published installation.

## Upgrade boundary

The matching Core release uses store schema 11 and compiled-plan schema 18.
Tagged Core `v0.22.2` used schema 8, and the compatibility contract refuses
schemas 8, 9, and 10 without a migration. Existing state must be finished or
retired with its matching runtime; initialize fresh state before using this
release. This is an intentional compatibility boundary, not a seamless patch
upgrade.

## Known limitations

This release does not include campaign-only closure-cap changes. Public
closure requests remain bounded at 16 KiB, and Millforge instructions remain
bounded at 64 KiB. Run pause/resume, daemon lifecycle controls, and bounded
control reads belong to the Core runtime; this package declares no control graph
or control implementation. The release workflow binds the immutable Core commit and wheel hash, checks
the installed-wheel suite, and verifies exact Plus artifact hashes. All 89
released OS/provider gates remain NOT_RUN; these package checks do not qualify
OS integration or the complete MVP.

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
