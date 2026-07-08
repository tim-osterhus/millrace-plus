# Release Notes

## Current Package

- Package: `millrace-plus`
- Version: `0.0.0`
- Workflow package ID: `millrace.plus.official`
- Workflow: `simple_loop` / `0.1`
- Python: `>=3.11`
- License metadata: Apache-2.0 via `LICENSE`

PLUS-0002B converts the shipped package root from the temporary scaffold to the
official `simple_loop` workflow package. The project is buildable as source,
but publication or name reservation remains a separate release decision.

## Local Build

```bash
PYTHONDONTWRITEBYTECODE=1 uv build
```

## Publication Blockers

- Operator approval and PyPI credentials are required before publication or
  name reservation.
- The future dependency metadata decision for `millrace-ai>=0.22,<0.23` is
  deferred to a release packet. PLUS-0002B keeps the package dependency-free and
  validates against the local rewrite checkout in tests.
