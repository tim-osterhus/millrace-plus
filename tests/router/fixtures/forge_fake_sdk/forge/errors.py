"""Test-only fake of the pinned ``forge.errors`` class hierarchy (no SDK code)."""


class ForgeError(Exception):
    pass


class ToolCallError(ForgeError):
    pass


class ToolExecutionError(ForgeError):
    pass


class ToolResolutionError(Exception):
    """Not a ForgeError, as in the pinned SDK."""

    def __init__(self, message, tool_name=None):
        super().__init__(message)
        self.tool_name = tool_name


class WorkflowCancelledError(ForgeError):
    pass


class MaxIterationsError(ForgeError):
    pass


class StepEnforcementError(ForgeError):
    pass


class PrerequisiteError(ForgeError):
    pass


class ContextBudgetExceeded(ForgeError):
    pass


class BackendError(ForgeError):
    """Same message rule as the pinned class: detail is appended after a colon."""

    def __init__(self, status_code, detail="", *, raw_body=None):
        message = f"Backend returned {status_code}"
        if detail:
            message = message + ": " + detail
        super().__init__(message)
        self.status_code = status_code
        self.body = raw_body if raw_body is not None else detail


class MultipleCredentialsError(ForgeError):
    pass


class MissingCredentialError(ForgeError):
    pass


class TransportError(Exception):
    """Stands in for ``httpx.TransportError``; the worker matches name and module."""


class ReadTimeout(TransportError):
    """Stands in for ``httpx.ReadTimeout``, which subclasses TransportError."""


class ConnectError(TransportError):
    pass


for _cls in (ReadTimeout, TransportError, ConnectError):
    _cls.__module__ = "httpx._exceptions"
del _cls
