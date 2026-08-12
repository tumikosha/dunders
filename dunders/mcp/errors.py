"""MCP domain errors + a mapper that hides provider internals (no secret leak).
Modeled on dunders.ai.providers._http.map_status.
"""

from __future__ import annotations

__all__ = [
    "McpError", "map_exception",
    "MOUNT_NOT_FOUND", "PATH_NOT_FOUND", "ACCESS_DENIED", "PROVIDER_ERROR",
]

MOUNT_NOT_FOUND = -32001
PATH_NOT_FOUND = -32002
ACCESS_DENIED = -32003
PROVIDER_ERROR = -32010


class McpError(Exception):
    def __init__(self, code: int, message: str, data=None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def map_exception(exc: Exception) -> McpError:
    if isinstance(exc, McpError):
        return exc
    if isinstance(exc, FileNotFoundError):
        return McpError(PATH_NOT_FOUND, "no such path")
    if isinstance(exc, PermissionError):
        return McpError(ACCESS_DENIED, "access denied")
    # Generic: never surface the raw message (may carry a URI with a password).
    return McpError(PROVIDER_ERROR, "provider error")
