from __future__ import annotations

from pathlib import Path

import pytest

from support import package_conformance as conformance

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PROJECT_ROOT / "millrace_workflow_package"


def test_package_fixture_checks_every_declared_asset_and_workflow_pin() -> None:
    manifest = conformance.assert_packaged_asset_closure(PACKAGE_ROOT)
    assert len(conformance.assets_by_id(manifest)) == 62
    assert set(conformance.workflows_by_id(manifest)) == {
        "simple_loop",
        "execution.lad",
        "execution.lad_integrator",
        "planning.lad",
        "lad.full",
        "vendor_selection",
    }


@pytest.mark.parametrize(
    "text",
    (
        "Return `WORK_DONE` to route the item.",
        "Return `WORK_DONE` to close the item.",
        "This skill grants provider credentials.",
    ),
)
def test_package_boundary_lint_refuses_runtime_authority_claims(text: str) -> None:
    with pytest.raises(AssertionError):
        conformance.assert_no_runtime_authority_claims({"bad.md": text})
