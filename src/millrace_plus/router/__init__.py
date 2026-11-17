"""Optional Plus native adapters and a thin facade over Core controls.

Public modules:

- `millrace_plus.router.control`: every public Core control operation with the
  Plus adapter resolver injected explicitly.
- `millrace_plus.router.pi`: the `pi_json` / `millrace-pi-json/v1` adapter.
- `millrace_plus.router.codex`: the `codex_exec` / `millrace-codex-exec/v1` adapter.
- `millrace_plus.router.claude`: `claude_print` / `millrace-claude-print/v1`.
- `millrace_plus.router.opencode`: `opencode_run` / `millrace-opencode-run/v1`.
- `millrace_plus.router.droid`: `droid_exec` / `millrace-droid-exec/v1`,
  profile-selected `read_only`/`auto_medium`; native qualification is separate.
- `millrace_plus.router.prime`: `prime_print` / `millrace-prime-print/v1`,
  official 0.10.2 text print; native qualification is separate.
- `millrace_plus.router.millforge`: `millforge_run` / `millrace-millforge-run/v1`.
- `millrace_plus.router.forge`: `forge_runner` / `millrace-forge-runner/v1`, one
  Forge SDK worker per invocation. Source regressions and a 19-case real-SDK
  local-peer cohort have run, including on an installed Plus wheel, with Linux
  x86_64 CPython 3.12.14 and Forge 0.10.1. No live route is qualified.
  `forge_worker.py` and `forge_probe.py` are worker package data, never imported.
- `millrace_plus.router.resolver`: direct selection of these eight exact pairs.
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
import re
from importlib import metadata

REQUIRED_EXECUTION_CONTRACT = "millrace-router-execution/v2"
REQUIRED_PROCESS_CONTRACT = "owned-process-v1"
MINIMUM_PROCESS_CORE = "0.23.2"

__all__ = (
    "REQUIRED_EXECUTION_CONTRACT",
    "REQUIRED_PROCESS_CONTRACT",
    "MINIMUM_PROCESS_CORE",
    "CoreContractError",
    "require_core",
    "require_owned_process",
)


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


def require_owned_process() -> str:
    """Gate live helper-consuming dispatch; pure capture/reload never calls this."""
    requirement = (
        "This native contract requires millrace-ai>=0.23.2 with public "
        "millrace.router.process OWNED_PROCESS_CONTRACT 'owned-process-v1'."
    )
    try:
        require_core()
    except CoreContractError:
        raise
    except Exception as exc:
        raise CoreContractError("core_contract_missing", requirement) from exc
    try:
        version = metadata.version("millrace-ai")
        # Distribution metadata uses normalized PEP 440 release/suffix forms.
        match = re.fullmatch(
            r"(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)"
            r"(?P<pre>(?:a|b|rc)\d+)?(?P<post>\.post\d+)?"
            r"(?P<dev>\.dev\d+)?(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?",
            version,
            re.IGNORECASE,
        )
        if match is None:
            raise ValueError("unsupported_core_version")
        release = tuple(int(part) for part in match["release"].split("."))
        release += (0,) * max(0, 3 - len(release))
        while len(release) > 3 and release[-1] == 0:
            release = release[:-1]
        below = release < (0, 23, 2)
        prerelease = match["pre"] or (match["dev"] and not match["post"])
        if not int(match["epoch"] or "0") and (
            below or (release == (0, 23, 2) and prerelease)
        ):
            raise ValueError("core_below_process_minimum")
    except Exception as exc:
        raise CoreContractError("core_process_version", requirement) from exc
    try:
        process = importlib.import_module("millrace.router.process")
    except Exception as exc:
        raise CoreContractError("core_process_missing", requirement) from exc
    if getattr(process, "OWNED_PROCESS_CONTRACT", None) != REQUIRED_PROCESS_CONTRACT:
        raise CoreContractError("core_process_mismatch", requirement)
    return REQUIRED_PROCESS_CONTRACT
