from __future__ import annotations

import pytest

from millrace_plus.demo.hardware import unknown_hardware


@pytest.fixture
def hardware():
    value = unknown_hardware("missing")
    value.update(
        platform="Linux",
        architecture="x86_64",
        logical_cpus=8,
        unknown_reasons={},
        probe_status="partial",
    )
    value["memory"] = {
        "physical_bytes": 16 * 1024**3,
        "available_bytes": None,
        "unified": False,
        "unknown_reasons": {"available_bytes": "missing"},
    }
    return value


@pytest.fixture
def candidate():
    return {
        "format": "millrace-demo-candidate/v1",
        "research_status": "skipped",
        "summary": "The baseline shows provisional context budgets. "
        "Actual loading remains unverified.",
        "suggestions": [],
        "published_measurements": [],
    }
