# Millrace Plus release policy

## Unpublished 0.23.2 development candidate

The current candidate pairing uses exact matched locally built artifacts:

| Surface | Candidate contract |
| --- | --- |
| Plus package | `millrace-plus==0.23.2` |
| Core package | `millrace-ai==0.23.2` |
| Runtime dependency | None |
| Optional router dependency | `millrace-ai>=0.23.0,<0.24.0` (deliberately retained) |
| Codex/OpenCode invocation minimum | `millrace-ai>=0.23.2` plus `owned-process-v1` |
| Public execution contract | `millrace-router-execution/v2` |

The 0.23.2 candidate is unpublished. Install and verify its exact local wheels;
the pins above describe the candidate pair, not registry availability. This
pairing does not select new OS or bundle artifacts or authorize publication.
Published 0.23.1 artifacts and their recorded qualification remain unchanged.

The wider optional router range remains usable by unchanged Pi and Claude
consumers. Codex and OpenCode additionally check their helper minimum and
capability before native preparation, probe or spawn. Refusal is a counted,
stopped preflight failure; it does not start a native child. Equal versions alone
do not establish compatibility. Offline process/artifact checks do not qualify
native/provider routes or macOS lifecycle behavior; those need their own exact
source, artifact, route and platform receipts.

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
