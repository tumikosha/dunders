"""Pty backend: one long-lived shell, commands serialized through it.

POSIX-only. Uses `ptyprocess`.

Each command is wrapped with a unique sentinel so we can detect completion
in the byte stream:

    <cmd>
    echo "DUNDERS_END_<id>_$?"

We watch for ``DUNDERS_END_<id>_<rc>`` in the output, strip it, deliver the
remainder, and call on_exit(rc).
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import uuid
from pathlib import Path

from . import register
from .base import Handle, OnChunk, OnExit


if sys.platform != "win32":
    from ptyprocess import PtyProcess
else:
    PtyProcess = None  # type: ignore[assignment]


_END_RE = re.compile(rb"DUNDERS_END_([0-9a-f]+)_([0-9-]+)\r?\n")


class _PtyHandle:
    def __init__(self, backend: "PtyBackend", token: str) -> None:
        self._backend = backend
        self._token = token

    @property
    def running(self) -> bool:
        return self._backend._active_token == self._token

    def cancel(self) -> None:
        if self.running:
            self._backend._send_ctrl_c()

    def kill(self) -> None:
        self._backend._kill_shell()

    # NOTE: best-effort. The pty backend uses one long-lived shell with
    # sentinel-based command tracking; raw stdin injection happens to work
    # for trivial REPLs but can confuse the sentinel detection if the child
    # echoes the marker. Subprocess backend is the primary interactive path.
    def write_stdin(self, data: bytes) -> None:
        proc = self._backend._proc
        if proc is None or not self.running:
            return
        try:
            proc.write(data)
        except Exception:
            pass

    def close_stdin(self) -> None:
        # Ctrl+D byte at the start of an empty line tells most shells/REPLs
        # to treat it as EOF. We can't actually close the shared pty's stdin.
        proc = self._backend._proc
        if proc is None or not self.running:
            return
        try:
            proc.write(b"\x04")
        except Exception:
            pass


class PtyBackend:
    name = "pty"

    def __init__(self) -> None:
        if sys.platform == "win32":
            raise RuntimeError(
                "pty backend unavailable on Windows; install dunders[pty-windows] (TODO)"
            )
        self._proc: PtyProcess | None = None
        self._reader_task: asyncio.Task | None = None
        self._buf = bytearray()
        self._on_chunk: OnChunk | None = None
        self._on_exit: OnExit | None = None
        self._active_token: str | None = None

    def spawn(self, cmd: str, cwd: Path, on_chunk: OnChunk, on_exit: OnExit) -> Handle:
        if self._active_token is not None:
            raise RuntimeError("pty backend is busy with another command")
        self._ensure_shell(cwd)
        token = uuid.uuid4().hex[:12]
        self._active_token = token
        self._on_chunk = on_chunk
        self._on_exit = on_exit
        self._buf.clear()
        wrapped = (
            f"{cmd}\n"
            f"echo \"DUNDERS_END_{token}_$?\"\n"
        )
        assert self._proc is not None
        self._proc.write(wrapped.encode("utf-8", errors="replace"))
        return _PtyHandle(self, token)

    def shutdown(self) -> None:
        self._kill_shell()

    def _ensure_shell(self, cwd: Path) -> None:
        if self._proc is not None and self._proc.isalive():
            return
        shell = os.environ.get("SHELL", "/bin/sh")
        env = dict(os.environ)
        env["PS1"] = ""
        env["PROMPT_COMMAND"] = ""
        self._proc = PtyProcess.spawn(
            [shell, "-i"], cwd=str(cwd), env=env, dimensions=(40, 120),
        )
        loop = asyncio.get_running_loop()
        self._reader_task = loop.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self._proc is not None
        loop = asyncio.get_running_loop()
        while self._proc is not None and self._proc.isalive():
            try:
                chunk = await loop.run_in_executor(None, self._read_some)
            except (EOFError, OSError):
                break
            if not chunk:
                await asyncio.sleep(0.01)
                continue
            self._handle_chunk(chunk)

    def _read_some(self) -> bytes:
        assert self._proc is not None
        try:
            return self._proc.read(4096)
        except EOFError:
            return b""

    def _handle_chunk(self, chunk: bytes) -> None:
        self._buf.extend(chunk)
        if self._active_token is None:
            self._buf.clear()
            return
        m = _END_RE.search(self._buf)
        if not m:
            # Keep a 64-byte tail to avoid splitting the sentinel across reads.
            # Sentinel max length: "DUNDERS_END_" (9) + 12-hex + "_" + 5-digit rc + "\r\n" = ~29 bytes.
            tail_size = 64
            tail = bytes(self._buf[-tail_size:])
            visible = bytes(self._buf[:-tail_size])
            if visible and self._on_chunk is not None:
                self._on_chunk(visible)
            self._buf = bytearray(tail)
            return
        token = m.group(1).decode()
        rc_str = m.group(2).decode()
        # rc may be negative (e.g. -1 for kill) — int() handles the minus sign
        rc = int(rc_str)
        before = bytes(self._buf[: m.start()])
        if self._on_chunk is not None and before:
            self._on_chunk(before)
        self._buf = bytearray(self._buf[m.end():])
        if token == self._active_token and self._on_exit is not None:
            cb = self._on_exit
            self._active_token = None
            self._on_chunk = None
            self._on_exit = None
            cb(rc)

    def _send_ctrl_c(self) -> None:
        if self._proc is None or not self._proc.isalive():
            return
        token = self._active_token
        try:
            self._proc.write(b"\x03")
        except OSError:
            return
        # After Ctrl+C the shell interrupts the whole queued command block
        # (both the user command and the echo sentinel).  The shell returns to
        # its prompt, so we re-send the sentinel immediately; the shell will
        # execute it and we can detect completion normally.  Use rc 130
        # (SIGINT convention) as the exit code.
        if token is not None:
            sentinel = f"\necho \"DUNDERS_END_{token}_130\"\n"
            try:
                self._proc.write(sentinel.encode("utf-8"))
            except OSError:
                pass

    def _kill_shell(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._proc is not None and self._proc.isalive():
            try:
                self._proc.kill(signal.SIGTERM)
            except Exception:
                pass
        self._proc = None
        self._buf.clear()
        if self._active_token is not None and self._on_exit is not None:
            cb = self._on_exit
            self._active_token = None
            self._on_chunk = None
            self._on_exit = None
            cb(-1)


if sys.platform != "win32":
    register("pty", PtyBackend)
