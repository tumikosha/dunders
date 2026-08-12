"""JSON-RPC 2.0 framing for the MCP server/client (shared, stdlib-only).

One JSON object per line (LSP-style newline framing over a byte stream). This
module is transport-agnostic: it only turns dicts into bytes and back, plus
tiny envelope constructors. No I/O here.
"""

from __future__ import annotations

import json

__all__ = [
    "PROTOCOL_VERSION", "ProtocolError",
    "PARSE_ERROR", "INVALID_REQUEST", "METHOD_NOT_FOUND",
    "INVALID_PARAMS", "INTERNAL_ERROR",
    "encode_message", "decode_message", "make_response", "make_error",
]

PROTOCOL_VERSION = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ProtocolError(Exception):
    """Input that is not a decodable single JSON-RPC object."""


def encode_message(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"


def decode_message(line: bytes) -> dict:
    try:
        obj = json.loads(line)
    except ValueError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("JSON-RPC message must be an object")
    return obj


def make_response(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}
