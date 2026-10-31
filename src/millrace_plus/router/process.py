"""Plus facade for Core's public owned-process helper (`owned-process-v1`).

Deterministic Plus commands, such as captured L-Q1 engineering checks, run one
argv through this exact Core helper instead of a private process loop. Importing
this module verifies Core's execution and process contracts first; it adds no
policy, state, retry or scheduling of its own.
"""

from __future__ import annotations

from . import require_owned_process

require_owned_process()

from millrace.router.process import (  # noqa: E402 - after the handshake
    OWNED_PROCESS_CONTRACT,
    ProcessRequest,
    ProcessResult,
    run_owned_process,
)

__all__ = [
    "OWNED_PROCESS_CONTRACT",
    "ProcessRequest",
    "ProcessResult",
    "run_owned_process",
]
