"""dunders MCP — expose the VFS to an external agent over the Model Context
Protocol (stdlib-only JSON-RPC). Public entry: run_stdio.
"""

from __future__ import annotations

from dunders.mcp.mounts import MountTable
from dunders.mcp.server import McpServer
from dunders.mcp.transport import StdioTransport

__all__ = ["run_stdio", "McpServer", "MountTable"]


def run_stdio(registry, table, *, allow_write: bool) -> None:
    """Serve MCP over stdio until stdin EOF."""
    McpServer(registry, table, allow_write=allow_write).serve(StdioTransport.stdio())
