# Release Line

The unpublished `millrace-plus` 0.22.4 source targets Core 0.22.4 and Millforge
0.1.1 in a matched `millrace==0.22.4` bundle. Published 0.22.3 remains the public
installation until final qualification and publication complete.
The published `millrace==0.22.3` bundle pins Core and Plus 0.22.3 with
Millforge 0.1.1.

## 0.22.4 scope

The package targets runtime compatibility `>=0.22.4,<0.23` and contains six
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
| Source version | `0.22.4` |
| Release line | 0.22.4, unpublished |
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

The current private source candidate `0.22.4` retains `demo@0.1`
and adds opt-in `lad.full.pi@0.1` with seventeen copied entrypoint assets plus
one inert transport note. It targets Core `>=0.22.4,<0.23`. Core owns
runtime behavior; Plus supplies data only. The reviewed Core capacity
descriptor and accepted PI-B02 assets are retained. The retained predecessor pair passed fresh wheel/sdist qualification for a
five-stage coding route with real stock-Pi tools and a scripted provider on
Darwin and one pinned Ubuntu host. Final 0.22.4 artifact and actual-model
qualification remain open; all seventeen LAD stages are not proved. The six entries above remain the
public release set.

It also contains the `millrace-instruction-manual`,
`millrace-loop-configuration`, and `millrace-entrypoint-authoring` advisory
skills.

## Private Pi candidate

The current private candidate uses package identity `0.22.4` and
declares Core compatibility `>=0.22.4,<0.23`. `lad.full.pi@0.1` preserves
all seventeen `lad.full` stages and their domain, artifact, evidence, routing,
capability, completion, and stop-condition obligations. Its opt-in Pi report
channel replaces only the exact-observation-object presentation rule for this
variant: a final object has exactly `marker`, `artifact_payload_candidate`, and
`observation_payload_candidate`; `runner_report` is a bounded generic string
inside the observation candidate when narrative evidence is required. The
artifact schema, routing fields, capability grants, and workflow authority stay
unchanged.

Historical PI-B01 compatibility evidence used the submitted Core wheel
`millrace_ai-0.22.4.dev3+pi.b01-py3-none-any.whl`, with its hash recorded in the
PI-A01 handoff. That wheel's compiler rejected the preserved component-free
completion behavior because it required payload capacity on a component pin.
This rejection applies to the historical PI-B01 wheel and candidate. Current
PI-B02 source review passed against the accepted Core capacity source. Its
scripted text-tool proofs and purelib extraction remain predecessor evidence;
they do not qualify this local candidate or establish foreground coding.

## Package Boundary

The wheel contains workflow data, agent skills, package metadata, and nothing
that executes Millrace. It has no CLI, daemon, runner, provider integration,
plugin registration, marketplace client, post-install hook, or dependency on
`millrace-ai`.

The planned `millrace==0.22.4` bundle pins Core and Plus 0.22.4 with Millforge 0.1.1.
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
