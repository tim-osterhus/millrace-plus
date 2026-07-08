# PLUS-0002F Implementation Review

## Scope

PLUS-0002F adds `vendor_selection` / `0.1` as a public workflow entry in the
`millrace.plus.official` package. The implementation is asset-free: the donor
`vendor_selection.source()` assets are empty, the package adds no
`assets/workflows/vendor_selection/` directory, workflow `required_assets` is
`[]`, and the compiled selected pin evidence is `selected_asset_pins=()`.

The package workflow `selected_authority` is `vendor_selection.source()` with
only the top-level `assets` key removed. Donor `unselected_catalog` remains in
the package manifest as non-selected source data, and package tests prove it is
omitted from compiled selected authority bytes and selected fingerprints.

## RED Evidence

Initial RED command:

```bash
PYTHONPATH=../../source/millrace-rewrite/src PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with hatchling pytest -q tests/test_vendor_selection_official_package.py tests/test_official_package_layout_plan.py tests/test_package_metadata.py tests/test_workflow_package_installed_smoke.py tests/test_lad_execution_official_package.py::test_lad_workflow_identities_match_donor_sources tests/test_lad_planning_official_package.py::test_planning_workflow_identity_matches_donor_source tests/test_lad_learning_official_package.py::test_full_lad_workflow_identity_matches_learning_donor_source
```

Expected failures were observed for missing `vendor_selection`, stale
PLUS-0002E docs/metadata, missing installed selection, missing package-local
review doc, and package data that still excluded `vendor_selection`.

## Selected Authority Evidence

Fresh package path selection after implementation:

```text
manifest_digest=sha256:7658b10f7db0127c56d8f38d237d5a361d878ca6b62ba1e22c686810e64accbb
vendor_selection fingerprint=sha256:2d55b2f4f8ab6f0cb1f8e15271f05632ae316b536b1be224903149b422d9b3a7
selected_asset_pins=()
assets=0
partitions=requirements, sourcing, evaluation, authorization
queue_families=7
stages=9
terminal_outcomes=16
terminal_actions=16
fanout=2
join=1
operator_wait=1
runner_bindings=1
effects=0
unselected_catalog in compiled authority=false
provider_ref in compiled authority=false
effect/provider refs absent
```

Existing workflow selected fingerprints stayed stable:

```text
simple_loop fingerprint=sha256:630dc75947c242090a0e27685db83b3211d96b3c46fa062e5c3b2b23868e0c4e
execution.lad fingerprint=sha256:e7a9ce8ceabf261da94211fc27e9f7c74ae4b009ab1c91b81c9c2cc938fb7ebd
execution.lad_integrator fingerprint=sha256:4d9a7711f6927cfc9474ea5e76b6651096141f9a9b19b53977c2ac5b394ae1a4
planning.lad fingerprint=sha256:1cfaca9f7720687cbf83dbac89fe754ac006e4e139b3aa3504feee1ff1c8113d
lad.full fingerprint=sha256:3f0e3f61fc4e2bf56d0937c90ec816d7d8a634ceaa8f1b9b9ebe105c35a44cec
```

## Test Coverage

- `tests/test_vendor_selection_official_package.py` covers identity,
  donor-derived selected authority, empty required assets, path/archive
  selection, archive import/verify/select command coverage, empty selected
  asset pins, four-plane graph counts,
  `unselected_catalog` non-selected disposition, selected authority mutation
  fingerprint drift, existing workflow fingerprint stability, and forbidden
  provider/effect/plugin/MCP/native-runner/marketplace semantics for the new
  workflow record.
- `tests/test_workflow_package_installed_smoke.py` includes
  `vendor_selection` in installed package verify/select coverage.
- `tests/test_official_package_layout_plan.py` and
  `tests/test_package_metadata.py` catch stale PLUS-0002E release metadata,
  missing package-local review docs, and missing asset-free package data.

## Code-Diet Notes

- Code added: tests and documentation only.
- Code reused: existing WPKG public path/archive/installed selection helpers,
  manifest digest canonicalization, and selected fingerprint helpers.
- New modules/classes/public APIs: none.
- New assets: none.
- Remaining duplication: small package-test helpers mirror prior workflow
  tests to keep each packet's proof local and readable.
- Drift prevention: manifest digest checks, selected package pin assertions,
  installed package smoke coverage, and source-data mutation tests prevent
  `unselected_catalog` from becoming selected authority.
- Cleanup packet required before PLUS-0002.9: none.
