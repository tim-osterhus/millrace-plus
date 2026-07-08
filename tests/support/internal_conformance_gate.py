from __future__ import annotations

import os

import pytest

INTERNAL_CONFORMANCE_ENV = "MILLRACE_PLUS_RUN_INTERNAL_CONFORMANCE"


def require_internal_conformance() -> None:
    enabled = os.environ.get(INTERNAL_CONFORMANCE_ENV, "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enabled:
        pytest.skip(
            "internal conformance requires "
            f"{INTERNAL_CONFORMANCE_ENV}=1 and explicit runtime evidence paths",
            allow_module_level=True,
        )
