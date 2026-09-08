# Release Line

This checkout records the `0.22.3` unreleased `millrace-plus` member candidate.
The published `millrace` convenience bundle remains `millrace==0.22.2`; no
`0.22.3` bundle is published.

## 0.22.3 candidate scope

The candidate targets runtime compatibility `>=0.22.3,<0.23` and contains six
ordinary workflow entries. It is not a published package release. The
experimental `execution.lad_codex_semantic_worktree` workflow is deferred: it
is not selectable and its exclusive assets are absent from the manifest, wheel,
and source archive. The preserved campaign evidence is inconclusive and does
not establish semantic-workspace efficacy or incompatibility.

Generic governed-context mechanisms remain a runtime capability for workflows
that independently select them; this Plus candidate does not claim a semantic
LAD workflow release. Unreleased schema-17 plans are historical evidence, not
compatible plans.

## Current Source Package

| Field | Value |
| --- | --- |
| Distribution | `millrace-plus` |
| Source version | `0.22.3` |
| Release status | Unreleased member candidate |
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

The published `millrace` convenience bundle remains `millrace==0.22.2`.
It does not install this unreleased member candidate, and no `0.22.3` bundle
is published. Direct `millrace-plus` installation remains useful for tools
that only need to inspect or distribute the package data; candidate
qualification uses the explicit Core and Millforge wheel paths in
[Validation](public-validation.md).

Member distributions version independently. Each `millrace`
meta-distribution release pins one tested combination and may reuse an
unchanged compatible member. A future bundle must publish and pin its own
combination before it can be used as a published installation.

## Upgrade boundary

The matching Core candidate uses store schema 10. Tagged Core `v0.22.2` used
schema 8, and the compatibility contract refuses schemas 8 and 9 without a
migration. Existing state must be finished or retired with its matching
runtime; initialize fresh state before using the candidate. This is an
intentional compatibility boundary, not a seamless patch upgrade.

## Known limitations

This candidate does not include campaign-only closure-cap changes. Public
closure requests remain bounded at 16 KiB, and Millforge instructions remain
bounded at 64 KiB. Run-scoped pause/resume is not supplied by this candidate.
Final qualification also requires parent-owned immutable Core commit and wheel
hashes plus rebuilt Plus artifact hashes in the release workflow.

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
