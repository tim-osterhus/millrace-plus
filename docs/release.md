# Release Notes

## Current Package

- Package: `millrace-plus`
- Version: `0.0.0`
- Workflow package ID: `millrace.plus.official`
- Workflows:
  - `simple_loop` / `0.1`
  - `execution.lad` / `0.1`
  - `execution.lad_integrator` / `0.1`
  - `planning.lad` / `0.1`
  - `lad.full` / `0.1`
  - `vendor_selection` / `0.1`
- Python: `>=3.11`
- License metadata: Apache-2.0 via `LICENSE`

PLUS-0002.9 closes final conformance for the official package. Path, archive,
and installed sources read the same package bytes, all six selectors verify
and select through public package APIs, selected package pins record exact
workflow and asset pins, and the 22 v0.21 entrypoint/stage-core pairs have
final package or deferral evidence.

The package remains dependency-free and ships no provider credentials,
provider execution code, plugin/MCP behavior, native runner code, marketplace
behavior, or remote install semantics. The project is buildable as source, but
publication or name reservation remains a separate release decision.

## Local Build

```bash
PYTHONDONTWRITEBYTECODE=1 uv build --out-dir /tmp/millrace-plus-build --force-pep517
```

## Publication Blockers

- Operator approval and PyPI credentials are required before publication or
  name reservation.
- The future dependency metadata decision for `millrace-ai>=0.22,<0.23` is
  deferred to a release packet. PLUS-0002.9 keeps the package dependency-free
  and validates against the local rewrite checkout in tests.
