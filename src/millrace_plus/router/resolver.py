"""Direct per-host Plus resolver for exactly the accepted native contracts."""

from __future__ import annotations

from . import require_core

require_core()
from millrace.router.execution import ExecutionAdapter, RouterError  # noqa: E402

from .claude import ClaudePrintAdapter  # noqa: E402
from .codex import CodexExecAdapter  # noqa: E402
from .opencode import OpenCodeRunAdapter  # noqa: E402
from .pi import PiJsonAdapter  # noqa: E402


def resolve_adapter(adapter_id: str, contract: str) -> ExecutionAdapter:
    if (adapter_id, contract) == ("pi_json", "millrace-pi-json/v1"):
        return PiJsonAdapter()
    if (adapter_id, contract) == ("codex_exec", "millrace-codex-exec/v1"):
        return CodexExecAdapter()
    if (adapter_id, contract) == ("claude_print", "millrace-claude-print/v1"):
        return ClaudePrintAdapter()
    if (adapter_id, contract) == ("opencode_run", "millrace-opencode-run/v1"):
        return OpenCodeRunAdapter()
    raise RouterError("unsupported_adapter_contract", f"{adapter_id} {contract}")
