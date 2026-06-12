"""Real subprocess backend tests. POSIX-only assumptions: /bin/sh, echo, sleep."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from dunders.fm.console.backends.subprocess_be import SubprocessBackend


pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="subprocess backend tests assume POSIX shell",
)


async def _run_to_completion(cmd: str, cwd: Path) -> tuple[bytes, int]:
    chunks: list[bytes] = []
    exit_code: list[int] = []
    done = asyncio.Event()

    def on_chunk(b: bytes) -> None:
        chunks.append(b)

    def on_exit(rc: int) -> None:
        exit_code.append(rc)
        done.set()

    be = SubprocessBackend()
    be.spawn(cmd, cwd, on_chunk, on_exit)
    await asyncio.wait_for(done.wait(), timeout=10)
    return b"".join(chunks), exit_code[0]


async def test_echo(tmp_path: Path):
    out, rc = await _run_to_completion("echo hello", tmp_path)
    assert rc == 0
    assert b"hello" in out


async def test_nonzero_exit(tmp_path: Path):
    _, rc = await _run_to_completion("false", tmp_path)
    assert rc != 0


async def test_cwd_is_respected(tmp_path: Path):
    out, rc = await _run_to_completion("pwd", tmp_path)
    assert rc == 0
    assert str(tmp_path) in out.decode()


async def test_cancel_kills_long_running(tmp_path: Path):
    chunks: list[bytes] = []
    exit_code: list[int] = []
    done = asyncio.Event()

    be = SubprocessBackend()
    h = be.spawn(
        "sleep 30", tmp_path,
        on_chunk=chunks.append,
        on_exit=lambda rc: (exit_code.append(rc), done.set())[-1],
    )
    await asyncio.sleep(0.2)
    assert h.running
    h.cancel()
    await asyncio.wait_for(done.wait(), timeout=5)
    assert not h.running


async def test_write_stdin_and_close_eof(tmp_path: Path):
    """`cat` echoes stdin to stdout; closing stdin lets it exit."""
    chunks: list[bytes] = []
    exit_code: list[int] = []
    done = asyncio.Event()

    be = SubprocessBackend()
    h = be.spawn(
        "cat", tmp_path,
        on_chunk=chunks.append,
        on_exit=lambda rc: (exit_code.append(rc), done.set())[-1],
    )
    # Give the child a moment to set up its stdin pipe.
    await asyncio.sleep(0.1)
    h.write_stdin(b"hello\n")
    h.write_stdin(b"world\n")
    await asyncio.sleep(0.2)
    h.close_stdin()
    await asyncio.wait_for(done.wait(), timeout=5)
    assert exit_code == [0]
    out = b"".join(chunks)
    assert b"hello" in out and b"world" in out


async def test_kill_force_exits_process(tmp_path: Path):
    chunks: list[bytes] = []
    exit_code: list[int] = []
    done = asyncio.Event()

    be = SubprocessBackend()
    h = be.spawn(
        "sleep 30", tmp_path,
        on_chunk=chunks.append,
        on_exit=lambda rc: (exit_code.append(rc), done.set())[-1],
    )
    await asyncio.sleep(0.1)
    h.kill()
    await asyncio.wait_for(done.wait(), timeout=5)
    assert not h.running
    # SIGKILL leaves negative exit (-9 on POSIX).
    assert exit_code[0] != 0
