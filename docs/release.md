# Release Notes

## Current Scaffold

- Package: `millrace-plus`
- Version: `0.0.0`
- Python: `>=3.11`
- License metadata: Apache-2.0 via `LICENSE`

PLUS-0001 is a local package scaffold only. The project is buildable and
PyPI-ready as source, but the PyPI name has not been published or reserved in
this packet.

## Local Build

```bash
PYTHONDONTWRITEBYTECODE=1 uv build
```

## Publication Blockers

- Operator approval and PyPI credentials are required before publication or
  name reservation.
- PLUS-0002 must add reviewed official workflow content before this repository
  claims public workflow assets beyond the scaffold smoke fixture.
- The future dependency metadata decision for `millrace-ai>=0.22,<0.23` is
  deferred to a release packet. PLUS-0001 keeps the package dependency-free and
  validates against the local rewrite checkout in tests.
