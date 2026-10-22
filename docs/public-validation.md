# Public validation

Run the complete active suite with matched Core 0.23.0 installed:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-project --with pytest --with hatchling --with millrace-ai==0.23.0 --with . pytest -q
uv run --no-project --with ruff ruff check src tests
uv build
```

The standalone public CI selection runs catalog and package guidance tests without Core.
The complete suite adds native fake-process lifecycle checks, contract refusal, source and installed package boundaries,
sdist reconstruction, independent catalog copies, and exact request replay.
Historical manifest and domain-engine tests are retired with their removed product contracts.
No retired tests count as passing. Current command and native fake-process regressions remain active.
