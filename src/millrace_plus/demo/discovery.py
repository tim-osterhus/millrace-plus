"""Passive adapter presence and explicit nonsecret configuration selection."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .contracts import DemoError, load_document

__all__ = ["passive_inventory", "select_native", "consent_description"]

# These are the adapter contracts in this qualified bundle, not observed versions.
_CONTRACTS = {
    "pi_json": (
        "pi",
        "millrace-pi-json/v1",
        "millrace-pi-json-profile/v1",
        "pi_version",
        "0.87.0",
    ),
    "codex_exec": (
        "codex",
        "millrace-codex-exec/v1",
        "millrace-codex-exec-profile/v1",
        "codex_version",
        "0.163.0",
    ),
    "claude_print": (
        "claude",
        "millrace-claude-print/v1",
        "millrace-claude-print-profile/v1",
        "claude_version",
        "2.1.305",
    ),
    "opencode_run": (
        "opencode",
        "millrace-opencode-run/v1",
        "millrace-opencode-run-profile/v1",
        "opencode_version",
        "1.19.3",
    ),
}


def passive_inventory() -> list[dict[str, str]]:
    """PATH lookup only: never execute an adapter, auth, main, or version probe."""
    return [
        {
            "adapter": adapter,
            "presence": "found" if shutil.which(item[0]) else "missing",
            "configuration": "native inheritance available"
            if adapter in {"claude_print", "opencode_run"}
            else "needs profile/settings",
            "readiness": "version/auth unknown",
        }
        for adapter, item in _CONTRACTS.items()
    ]


def select_native(adapter: str, profile: Path | None = None) -> dict[str, Any] | None:
    """Capture only selected configuration; None means required settings are absent.

    Pi/Codex profiles name host-path aliases. A profile alone cannot resolve
    them, and this CLI does not infer private homes or invent a settings format.
    Such routes remain configuration-required until complete supported settings
    are supplied through an independently selected contract.
    """
    if adapter not in _CONTRACTS:
        raise DemoError("unsupported_native_adapter")
    executable, contract, profile_format, version_key, version = _CONTRACTS[adapter]
    selected = shutil.which(executable)
    if selected is None:
        raise DemoError("native_executable_missing")
    if adapter in {"pi_json", "codex_exec"}:
        return None
    parsed = (
        load_document(profile, limit=64 * 1024)
        if profile is not None
        else {"format": profile_format, version_key: version}
    )
    settings = (
        {"model", "effort"} if adapter == "claude_print" else {"model", "variant"}
    )
    if (
        set(parsed) - {"format", version_key} - settings
        or parsed.get("format") != profile_format
        or parsed.get(version_key)
        not in (("2.1.296", version) if adapter == "claude_print" else (version,))
    ):
        raise DemoError("invalid_native_profile")
    for key in set(parsed) & settings:
        value = parsed[key]
        if (
            type(value) is not str
            or not value
            or len(value) > 256
            or value.startswith("-")
            or any(c.isspace() for c in value)
        ):
            raise DemoError("invalid_native_profile")
    return {
        "binding": {
            "adapter": adapter,
            "contract": contract,
            # Native shims can dispatch by their invocation basename. Resolving
            # a claude symlink to mise would execute the dispatcher as mise.
            "executable": str(Path(selected).absolute()),
            "config": {"profile": "profiles/native.json"},
        },
        "profile": parsed,
        "provenance": "explicit" if profile else "inherited",
        "settings": {key: parsed[key] for key in settings if key in parsed},
    }


def consent_description(native: dict[str, Any], *, no_web: bool = False) -> str:
    adapter = native["binding"]["adapter"]
    settings = native["settings"]
    description = (
        f"Adapter: {adapter}; profile: {native['provenance']}. "
        "Model/settings: "
        f"{settings if settings else 'native inheritance; effective values unknown'}. "
        "One main attempt; node timeout inherits Core runtime defaults and "
        "operator policy, with Core's 1800-second fallback when unset. "
        "The admitted effective timeout is shown before execution. "
        "Possible provider billing. "
        "Existing native/project hooks, plugins, MCP and tool permissions "
        "remain effective and have not been inspected. Sanitized architecture, "
        "RAM/CPU/GPU facts, frozen reference estimates and limitations are "
        "transmitted. The native process has its "
        "normal same-user filesystem permissions; prompt scope is not a sandbox. "
        + (
            "No web research is requested; --no-web does not alter "
            "harness permissions. "
            if no_web
            else "Research with existing permitted web tools is requested; "
            "no tool is installed or enabled. "
        )
    )
    if adapter == "claude_print":
        description += "The Claude adapter bypasses native permission prompts. "
    return description
