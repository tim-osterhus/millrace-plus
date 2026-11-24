# Public validation

Run the complete active suite on Python 3.12 or newer with matched Core 0.23.2 installed. Supply absolute paths to the selected Core wheel and sdist, an OS source peer wheel and the public Plus 0.23.0 baseline wheel:

```sh
export MILLRACE_TEST_CORE_WHEEL=/absolute/path/to/millrace_ai-0.23.2-py3-none-any.whl
export MILLRACE_TEST_CORE_SDIST=/absolute/path/to/millrace_ai-0.23.2.tar.gz
export MILLRACE_TEST_OS_PEER_WHEEL=/absolute/path/to/millrace_os-0.23.1-py3-none-any.whl
export MILLRACE_TEST_BASELINE_PLUS_WHEEL=/absolute/path/to/millrace_plus-0.23.0-py3-none-any.whl
base="$(mktemp -d /tmp/mp.XXXXXX)"
suite() {
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-project --python 3.12 --with pytest --with hatchling --with httpx==0.28.1 \
    --with "$MILLRACE_TEST_CORE_WHEEL" --with "$MILLRACE_TEST_OS_PEER_WHEEL" --with . pytest -q -ra --basetemp="$base/$1" "${@:2}"
}
suite main tests/router tests/test_package_metadata.py tests/test_public_package_boundary.py \
  tests/test_agent_skill_assets.py tests/test_public_test_support_boundary.py
suite demo tests/demo
uv run --no-project --with ruff ruff check src tests
uv build
```

The two sessions together cover every configured `testpaths` entry. Run `tests/demo` separately:
`tests/demo/test_cli.py` and `tests/router/test_cli.py` share a module basename, so one pytest session cannot collect both.
The demo lifecycle tests import `httpx`; the suite pins the version used by the OS development extra.
Service tests bind AF_UNIX sockets under `tmp_path`, so keep the base temporary directory short.

The standalone public CI selection runs catalog and package guidance tests without Core.
The complete suite adds native fake-process lifecycle checks, contract refusal, source and installed package boundaries,
sdist reconstruction, independent catalog copies, and exact request replay.
Historical manifest and domain-engine tests are retired with their removed product contracts.
No retired tests count as passing. Current command and native fake-process regressions remain active.

CI verifies the baseline wheel SHA-256 and unyanked status before the package replacement proof.
The matched CI job builds the exact selected Core source commit. Publication checks use the exact public Core artifacts.
Both jobs check that every collected case ran, that none failed or errored, and that every skip matches the reason list below.

The complete suite installs an isolated OS source peer for deterministic OS client, service and demo fixtures.
CI builds OS commit `be22a127aa08a8959b4b674e49f2182708da7605`, which builds as `millrace_os-0.23.1`.
The Factory and LAD service journeys need its `OPTIONS /api/v1/router/requests` capability route.
No published OS 0.23.2 is required. That peer does not prove the final four-distribution release set.

Some cases need task-owned inputs that public CI does not supply. They skip with a fixed reason:

| Test module | Skip reason |
| --- | --- |
| `test_paperclip_plugin` | Task-owned pinned Node/loader profile is unavailable |
| `test_lad_compatibility` | Exact retained old Core inputs required for v7 integration |
| `test_cicd_push_workflow` | Requires the admitted B0 receiver |
| `test_forge_distribution` | No real CPython 3.11, or no admitted artifact manifest |
| `test_forge_responses` | Forge SDK absent or not the pinned version |
| `test_millforge_configuration` | Explicit Millforge and Node inputs absent |
| `test_millforge_distribution` | Prebuilt Millforge artifact inputs absent |

A skip for missing `httpx`, OS or its dependencies fails the run, because CI installs them.

Their prior accepted evidence applies only to the recorded runtime, artifact and route bytes.
