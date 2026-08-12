"""Transport layer for MCP framing. StdioTransport reads/writes newline-framed
JSON over two binary streams; stdout is protocol-only, diagnostics go to stderr.
"""

from __future__ import annotations

import sys
from typing import BinaryIO, Protocol

from dunders.mcp import protocol

__all__ = ["Transport", "StdioTransport"]


class Transport(Protocol):
    def read_message(self) -> dict | None: ...
    def write_message(self, msg: dict) -> None: ...


class StdioTransport:
    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self._r = reader
        self._w = writer

    @classmethod
    def stdio(cls) -> "StdioTransport":
        return cls(sys.stdin.buffer, sys.stdout.buffer)

    def read_message(self) -> dict | None:
        while True:
            line = self._r.readline()
            if not line:
                return None  # EOF
            if not line.strip():
                continue     # skip blank keepalive lines
            try:
                return protocol.decode_message(line)
            except protocol.ProtocolError as exc:
                print(f"mcp: dropping bad frame: {exc}", file=sys.stderr)
                continue

    def write_message(self, msg: dict) -> None:
        self._w.write(protocol.encode_message(msg))
        self._w.flush()
