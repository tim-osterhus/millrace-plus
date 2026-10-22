"""Optional Plus native adapters and a thin facade over Core controls.

Public modules:

- `millrace_plus.router.control`: every public Core control operation with the
  Plus adapter resolver injected explicitly.
- `millrace_plus.router.pi`: the `pi_json` / `millrace-pi-json/v1` adapter.
- `millrace_plus.router.codex`: the `codex_exec` / `millrace-codex-exec/v1` adapter.
- `millrace_plus.router.claude`: `claude_print` / `millrace-claude-print/v1`.
- `millrace_plus.router.resolver`: direct selection of these three exact pairs.
- `millrace_plus.router.cli`: `python -m millrace_plus.router` and
  `millrace-plus router`, thin wrappers around the generic Core CLI `main`.

Each of those modules requires Core's generic execution contract before it
imports Core. Equal `0.23.0` distribution metadata does not identify a
compatible Core; an older Core without `millrace.router.execution`, or one
reporting a different contract identifier, is refused here with no fallback.
This package module itself imports no Core code.
"""

from __future__ import annotations

import importlib

REQUIRED_EXECUTION_CONTRACT = "millrace-router-execution/v2"

__all__ = ("REQUIRED_EXECUTION_CONTRACT", "CoreContractError", "require_core")


class CoreContractError(ImportError):
    """The importable Core does not provide the required execution contract.

    `code` is `core_absent`, `core_contract_missing` or `core_contract_mismatch`.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def require_core() -> str:
    """Return the verified contract identifier or raise `CoreContractError`."""
    requirement = (
        "millrace_plus.router requires Core module millrace.router.execution with "
        f"EXECUTION_CONTRACT {REQUIRED_EXECUTION_CONTRACT!r}; install the matched "
        "release Core wheel. Equal 0.23.0 metadata is not sufficient."
    )
    try:
        importlib.import_module("millrace")
    except ImportError as exc:
        raise CoreContractError("core_absent", requirement) from exc
    try:
        execution = importlib.import_module("millrace.router.execution")
    except ImportError as exc:
        raise CoreContractError("core_contract_missing", requirement) from exc
    found = getattr(execution, "EXECUTION_CONTRACT", None)
    if found != REQUIRED_EXECUTION_CONTRACT:
        raise CoreContractError(
            "core_contract_mismatch", f"{requirement} Found {found!r}."
        )
    return REQUIRED_EXECUTION_CONTRACT
