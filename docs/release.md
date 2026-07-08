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
- Python: `>=3.11`
- License metadata: Apache-2.0 via `LICENSE`

PLUS-0002E extends the official package with hosted full LAD Learning workflow
package data while preserving `simple_loop`, LAD Execution, LAD Integrator,
and LAD Planning workflows. Effect/provider refs are selected workflow data
only; the package remains dependency-free and ships no provider credentials or
provider execution code. The project is buildable as source, but publication
or name reservation remains a separate release decision.

## Local Build

```bash
PYTHONDONTWRITEBYTECODE=1 uv build
```

## Publication Blockers

- Operator approval and PyPI credentials are required before publication or
  name reservation.
- The future dependency metadata decision for `millrace-ai>=0.22,<0.23` is
  deferred to a release packet. PLUS-0002E keeps the package dependency-free
  and validates against the local rewrite checkout in tests.
