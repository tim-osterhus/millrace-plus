# Public validation

Run the complete active suite on Python 3.12 or newer with matched Core 0.23.1 installed. Supply absolute paths to the selected Core wheel and sdist and the public Plus 0.23.0 baseline wheel:

```sh
export MILLRACE_TEST_CORE_WHEEL=/absolute/path/to/millrace_ai-0.23.1-py3-none-any.whl
export MILLRACE_TEST_CORE_SDIST=/absolute/path/to/millrace_ai-0.23.1.tar.gz
export MILLRACE_TEST_OS_PEER_WHEEL=/absolute/path/to/selected-os-peer.whl
export MILLRACE_TEST_BASELINE_PLUS_WHEEL=/absolute/path/to/millrace_plus-0.23.0-py3-none-any.whl
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-project --python 3.12 --with pytest --with hatchling --with "$MILLRACE_TEST_CORE_WHEEL" --with "$MILLRACE_TEST_OS_PEER_WHEEL" --with . pytest -q -ra
uv run --no-project --with ruff ruff check src tests
uv build
```

The standalone public CI selection runs catalog and package guidance tests without Core.
The complete suite adds native fake-process lifecycle checks, contract refusal, source and installed package boundaries,
sdist reconstruction, independent catalog copies, and exact request replay.
Historical manifest and domain-engine tests are retired with their removed product contracts.
No retired tests count as passing. Current command and native fake-process regressions remain active.

CI verifies the baseline wheel SHA-256 before the package replacement proof.
The matched CI job builds the exact selected Core source commit. Publication checks use the exact public Core artifacts.

The complete suite installs an isolated OS base peer for the deterministic Paperclip HTTP fixture.
Prepublication CI builds accepted OS commit `72bc08d81182822483e57e70ae309ed0fe9c31c7`.
That peer does not prove the final four-distribution release set.
The 21 pinned Node/loader cases skip when their task-owned fixtures are absent.
Their prior accepted evidence applies only while the corresponding runtime and plugin bytes remain unchanged.
