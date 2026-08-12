"""McpServer — JSON-RPC dispatch over a Transport. Never crashes on a bad call:
every exception maps to a JSON-RPC error response.
"""

from __future__ import annotations

import json
from importlib import metadata

from dunders.core.vfs import VfsRegistry
from dunders.mcp import errors, protocol
from dunders.mcp.mounts import MountTable
from dunders.mcp.tools import call_tool, tool_specs
from dunders.mcp.transport import Transport

__all__ = ["McpServer"]


def _version() -> str:
    try:
        return metadata.version("dunders")
    except metadata.PackageNotFoundError:
        return "0.0.0"


class McpServer:
    def __init__(self, registry: VfsRegistry, table: MountTable, *, allow_write: bool) -> None:
        self._registry = registry
        self._table = table
        self._allow_write = allow_write

    def serve(self, transport: Transport) -> None:
        while True:
            msg = transport.read_message()
            if msg is None:
                return  # EOF
            resp = self.handle(msg)
            if resp is not None:
                transport.write_message(resp)

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                return protocol.make_response(msg_id, {
                    "protocolVersion": protocol.PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dunders", "version": _version()},
                })
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return protocol.make_response(
                    msg_id, {"tools": tool_specs(self._allow_write)})
            if method == "tools/call":
                result = call_tool(
                    params.get("name", ""), params.get("arguments") or {},
                    table=self._table, registry=self._registry,
                    allow_write=self._allow_write,
                )
                return protocol.make_response(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(result)}]})
            return protocol.make_error(
                msg_id, protocol.METHOD_NOT_FOUND, f"unknown method {method!r}")
        except errors.McpError as exc:
            return protocol.make_error(msg_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001 — server must never crash
            m = errors.map_exception(exc)
            return protocol.make_error(msg_id, m.code, m.message)
