# Millrace Plus release policy

## 0.23.2 release

Plus 0.23.2 stays unpublished until its tagged publication workflow uploads the
verified wheel and sdist. The release pairs exact matched artifacts:

| Surface | Contract |
| --- | --- |
| Plus package | `millrace-plus==0.23.2` |
| Core package | `millrace-ai==0.23.2` |
| Runtime dependency | None |
| Optional router dependency | `millrace-ai>=0.23.0,<0.24.0` (deliberately retained) |
| Native adapter invocation minimum | `millrace-ai>=0.23.2` plus `owned-process-v1` |
| Public execution contract | `millrace-router-execution/v2` |

The wider optional router range remains usable by unchanged Pi and Claude
consumers. Codex, OpenCode, Droid, Millforge, Prime and Forge additionally check
the helper minimum and capability before native preparation, probe or spawn.
Refusal is a counted, stopped preflight failure; it does not start a native
child. Equal versions alone do not establish compatibility. Offline
process/artifact checks do not qualify native/provider routes or macOS lifecycle
behavior; those need their own exact source, artifact, route and platform
receipts. Published 0.23.1 artifacts and their recorded qualification remain
unchanged.

### Publication order and checks

Publish Core 0.23.2 first. The Plus publication workflow then runs only for tag
`v0.23.2` on the tagged commit with package version 0.23.2. It downloads the
exact public Core wheel and sdist, refuses yanked files or registry and
download digests that differ from the frozen values, and runs the full Plus
suite against them. It then builds Plus and publishes only the two artifacts
whose SHA-256 digests match the frozen manifest. All-zero placeholder digests
refuse publication.

Before publication, Public CI builds the selected Core source commit and checks
its 0.23.2 artifact names. Both workflows check that every collected case ran
without failure or error and that every skip matches a fixed list of optional
proofs, so an unexpected skip fails the run.

The complete suite installs an isolated OS source peer built from OS commit
`be22a127aa08a8959b4b674e49f2182708da7605` (package version 0.23.1). Its router
request capability route serves the Factory and LAD service fixtures. Plus does
not require a published OS 0.23.2. That peer does not prove
the final four-distribution release set. OS resolves its final registry lock
after Core and Plus are public, then the dependency-only bundle follows.

The package replacement proof upgrades public Plus 0.23.0, verified by digest and
unyanked status, to the selected 0.23.2 wheel. Edited initialized copies,
sibling copies, local variants and saved catalog settings remain unchanged.

## Published 0.23.1 release

The selected release set is Core, Plus, OS and the dependency-only bundle at 0.23.1.
The release workflow verifies artifact digests before publication. Publication receipts establish registry availability.

| Surface | Contract |
| --- | --- |
| Plus package | `millrace-plus==0.23.1` |
| Runtime dependency | None |
| Optional router dependency | `millrace-ai>=0.23.0,<0.24.0` |
| Selected Core release | `millrace-ai==0.23.1` |
| Public execution contract | `millrace-router-execution/v2` |
| Bundle | `millrace==0.23.1`: exact Core, Plus and OS pins |

Plus base provides catalog operations without Core. The router host checks execution-v2 compatibility.
The compatible-minor dependency range remains unchanged. Equal versions alone do not establish compatibility.
Tests use exact matched artifacts and isolated installations.

Publish Core first and verify its downloaded artifacts. Validate and publish Plus against that exact public wheel.
OS resolves its final public registry lock after both dependencies are public.
Publish OS next and the dependency-only bundle last. Verify public bytes after each stage.
Rebuild from the sdist and compare wheel payloads before publication.

The package replacement proof upgrades public Plus 0.23.0 to the selected 0.23.1 wheel.
Edited initialized copies, sibling copies, local variants and saved catalog settings remain unchanged.
New copies receive the selected bundled assets and source provenance.

Selected OpenCode and LAD native evidence remains limited to its accepted source, artifacts and configured routes.
The MCP command proof has separate transport, authentication and platform limits.
Live Paperclip proof is deferred. Native Learning, repair and full-factory claims remain outside the selected evidence.
Packaging checks and fake processes require no additional provider calls.
