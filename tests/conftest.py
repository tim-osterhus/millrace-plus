from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

INTERNAL_CONFORMANCE_ENV = "MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE"
RUNTIME_SOURCE_ENV = "MILLRACE_RUNTIME_SOURCE"
LEGACY_ASSET_ENV = "MILLRACE_LEGACY_ASSET_ROOT"

INTERNAL_CONFORMANCE_TESTS = frozenset(
    {
        "test_simple_loop_official_package.py",
        "test_lad_execution_official_package.py",
        "test_lad_planning_official_package.py",
        "test_lad_learning_official_package.py",
        "test_vendor_selection_official_package.py",
        "test_plus_0002_9_final_conformance.py",
        "test_workflow_package_conformance_helpers.py",
    }
)


def _internal_conformance_enabled() -> bool:
    return os.environ.get(INTERNAL_CONFORMANCE_ENV, "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "internal_conformance: requires an explicit Millrace runtime source checkout",
    )
    config.addinivalue_line("markers", "public: standalone public package tests")

    if not _internal_conformance_enabled():
        return

    missing = [
        name
        for name in (RUNTIME_SOURCE_ENV, LEGACY_ASSET_ENV)
        if not os.environ.get(name)
    ]
    if missing:
        raise pytest.UsageError(
            "internal conformance requires "
            f"{INTERNAL_CONFORMANCE_ENV}=1 and explicit "
            f"{', '.join(missing)}"
        )

    runtime_source = Path(os.environ[RUNTIME_SOURCE_ENV]).expanduser()
    legacy_asset_root = Path(os.environ[LEGACY_ASSET_ENV]).expanduser()
    for label, path in (
        (RUNTIME_SOURCE_ENV, runtime_source),
        (LEGACY_ASSET_ENV, legacy_asset_root),
    ):
        if not path.is_dir():
            raise pytest.UsageError(f"{label} must point to an existing directory")

    sys.path.insert(0, str(runtime_source))


def pytest_ignore_collect(collection_path: Any, config: pytest.Config) -> bool:
    del config
    path = Path(str(collection_path))
    return (
        path.name in INTERNAL_CONFORMANCE_TESTS
        and not _internal_conformance_enabled()
    )
