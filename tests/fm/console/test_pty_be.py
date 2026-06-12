from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="pty backend is POSIX-only"
)

from dunders.fm.console.backends.pty_be import PtyBackend  # noqa: E402


async def _drain(be: PtyBackend, cmd: str, cwd: Path, timeout: float = 10.0) -> tuple[bytes, int]:
    chunks: list[bytes] = []
    exit_code: list[int] = []
    done = asyncio.Event()

    def on_exit(rc: int) -> None:
        exit_code.append(rc)
        done.set()

    be.spawn(cmd, cwd, on_chunk=chunks.append, on_exit=on_exit)
    await asyncio.wait_for(done.wait(), timeout=timeout)
    return b"".join(chunks), exit_code[0]


async def test_echo(tmp_path: Path):
    be = PtyBackend()
    out, rc = await _drain(be, "echo hi", tmp_path)
    assert b"hi" in out
    assert rc == 0
    be.shutdown()


async def test_state_persists_between_commands(tmp_path: Path):
    be = PtyBackend()
    await _drain(be, f"cd {tmp_path}", tmp_path)
    out, rc = await _drain(be, "pwd", tmp_path)
    assert str(tmp_path) in out.decode()
    assert rc == 0
    be.shutdown()


async def test_cancel(tmp_path: Path):
    be = PtyBackend()
    chunks: list[bytes] = []
    exit_code: list[int] = []
    done = asyncio.Event()
    h = be.spawn(
        "sleep 30", tmp_path,
        on_chunk=chunks.append,
        on_exit=lambda rc: (exit_code.append(rc), done.set())[-1],
    )
    await asyncio.sleep(0.3)
    assert h.running
    h.cancel()
    await asyncio.wait_for(done.wait(), timeout=5)
    assert not h.running
    be.shutdown()
