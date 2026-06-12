"""Subprocess-shell backend: each command is its own short-lived process.

Works on POSIX and Windows. Stateless between commands.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from . import register
from .base import Handle, OnChunk, OnExit


def _default_shell() -> tuple[str, str]:
    """Return (shell_path, shell_arg) — `shell_path shell_arg cmd`."""
    if sys.platform == "win32":
        return os.environ.get("COMSPEC", "cmd.exe"), "/c"
    return os.environ.get("SHELL", "/bin/sh"), "-c"


class _SubprocessHandle:
    def __init__(self, task: asyncio.Task[int], proc_holder: list) -> None:
        self._task = task
        self._proc_holder = proc_holder

    @property
    def running(self) -> bool:
        return not self._task.done()

    def cancel(self) -> None:
        proc = self._proc_holder[0] if self._proc_holder else None
        if proc is None or proc.returncode is not None:
            return
        try:
            if sys.platform == "win32":
                proc.terminate()
            else:
                proc.send_signal(signal.SIGINT)
        except ProcessLookupError:
            pass

    def kill(self) -> None:
        proc = self._proc_holder[0] if self._proc_holder else None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    def write_stdin(self, data: bytes) -> None:
        proc = self._proc_holder[0] if self._proc_holder else None
        if proc is None or proc.stdin is None or proc.returncode is not None:
            return
        try:
            proc.stdin.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return
        loop = asyncio.get_running_loop()
        loop.create_task(self._drain(proc))

    @staticmethod
    async def _drain(proc: asyncio.subprocess.Process) -> None:
        if proc.stdin is None:
            return
        try:
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return

    def close_stdin(self) -> None:
        proc = self._proc_holder[0] if self._proc_holder else None
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass


class SubprocessBackend:
    """Implements the Backend protocol via asyncio subprocess shell."""

    name = "subprocess"

    def spawn(
        self,
        cmd: str,
        cwd: Path,
        on_chunk: OnChunk,
        on_exit: OnExit,
    ) -> Handle:
        proc_holder: list = []
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._run(cmd, cwd, on_chunk, on_exit, proc_holder))
        return _SubprocessHandle(task, proc_holder)

    def shutdown(self) -> None:
        return

    async def _run(
        self,
        cmd: str,
        cwd: Path,
        on_chunk: OnChunk,
        on_exit: OnExit,
        proc_holder: list,
    ) -> int:
        shell, shell_arg = _default_shell()
        try:
            proc = await asyncio.create_subprocess_exec(
                shell, shell_arg, cmd,
                cwd=str(cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as e:
            on_chunk(f"[backend error: {e}]\n".encode())
            on_exit(127)
            return 127

        proc_holder.append(proc)
        assert proc.stdout is not None
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                on_chunk(chunk)
        except asyncio.CancelledError:
            pass
        rc = await proc.wait()
        on_exit(rc)
        return rc


register("subprocess", SubprocessBackend)
