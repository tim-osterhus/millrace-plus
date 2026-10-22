# Millrace Plus 0.23 release policy

| Surface | Contract |
| --- | --- |
| Runtime dependency | None |
| Optional router dependency | `millrace-ai>=0.23.0,<0.24.0` |
| Selected Core release | `millrace-ai==0.23.0` |
| Public execution contract | `millrace-router-execution/v2` |
| Bundle | `millrace==0.23.0`: Core, Plus and OS |

Plus base provides catalog operations without Core. Its router host requires the execution-v2 handshake.
Equal versions alone do not establish compatibility. Tests use matched artifacts and isolated installations.

Publish Core first, verify its downloaded artifacts, then validate and publish Plus against that exact public wheel.
OS resolves its final registry lock after both are public. Publish OS next and the dependency-only bundle last.
The publication workflow verifies frozen artifact digests before upload. Rebuild from the sdist and compare wheel payloads.

Native route qualification remains scoped to its recorded source, runtime and provider configuration.
Fake-process checks establish mechanics without additional provider calls.
