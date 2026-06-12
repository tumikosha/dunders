from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dunders.fm.console.registry import ConsoleRegistry
from dunders.fm.console.runner import CommandRunner, ParsedCommand


# --- mock target -------------------------------------------------------

@dataclass
class _Target:
    id: str
    busy: bool = False
    chunks: list[bytes] = field(default_factory=list)
    last_exit: int | None = None

    def append(self, b: bytes) -> None:
        self.chunks.append(b)

    def mark_done(self, rc: int) -> None:
        self.busy = False
        self.last_exit = rc


# --- mock backend ------------------------------------------------------

class _MockHandle:
    def __init__(self) -> None:
        self.cancelled = False
        self.killed = False
        self.stdin_chunks: list[bytes] = []
        self.stdin_closed = False
    @property
    def running(self) -> bool: return not (self.cancelled or self.killed)
    def cancel(self) -> None: self.cancelled = True
    def kill(self) -> None: self.killed = True
    def write_stdin(self, data: bytes) -> None: self.stdin_chunks.append(data)
    def close_stdin(self) -> None: self.stdin_closed = True


@dataclass
class _MockBackend:
    name: str = "mock"
    spawned: list[tuple[str, Path]] = field(default_factory=list)
    last_handle: _MockHandle | None = None
    def spawn(self, cmd, cwd, on_chunk, on_exit):
        self.spawned.append((cmd, cwd))
        on_chunk(b"OK\n")
        on_exit(0)
        h = _MockHandle()
        self.last_handle = h
        return h
    def shutdown(self): pass


# --- fixtures ---------------------------------------------------------

@pytest.fixture
def runner(tmp_path):
    reg = ConsoleRegistry(factory=lambda tid: _Target(id=tid))
    panel_cwd = {"value": tmp_path}
    cd_calls: list[Path] = []

    def panel_cwd_get(): return panel_cwd["value"]

    def panel_cd(p: Path):
        panel_cwd["value"] = p
        cd_calls.append(p)
        return None

    backend = _MockBackend()
    r = CommandRunner(
        registry=reg,
        backend=backend,
        panel_cwd_getter=panel_cwd_get,
        panel_cd=panel_cd,
    )
    return r, reg, backend, cd_calls, panel_cwd


# --- parsing ----------------------------------------------------------

def test_parse_plain():
    p = CommandRunner.parse("ls -la", anonymous=False)
    assert p == ParsedCommand(kind="run", target=None, anonymous=False, body="ls -la")


def test_parse_named():
    p = CommandRunner.parse("@build npm test", anonymous=False)
    assert p == ParsedCommand(kind="run", target="build", anonymous=False, body="npm test")


def test_parse_anonymous_flag():
    p = CommandRunner.parse("ls", anonymous=True)
    assert p.anonymous is True


def test_parse_cd():
    assert CommandRunner.parse("cd /tmp", anonymous=False) == ParsedCommand(
        kind="cd", target=None, anonymous=False, body="/tmp"
    )
    assert CommandRunner.parse("cd", anonymous=False).kind == "cd"


def test_parse_backend_switch():
    assert CommandRunner.parse(":backend pty", anonymous=False) == ParsedCommand(
        kind="backend", target=None, anonymous=False, body="pty"
    )


def test_parse_to():
    assert CommandRunner.parse(":to build", anonymous=False) == ParsedCommand(
        kind="to", target="build", anonymous=False, body=""
    )


def test_parse_blank():
    assert CommandRunner.parse("   ", anonymous=False).kind == "noop"


# --- dispatch ---------------------------------------------------------

def test_run_default_target(runner):
    r, reg, be, _, _ = runner
    r.execute("echo hi", anonymous=False)
    t = reg.get("console-default")
    assert t is not None
    # Runner echoes a `cwd $ command\n` prompt before the backend output.
    assert b"OK\n" in t.chunks
    assert any(b"$" in c and b"echo hi" in c for c in t.chunks)
    assert be.spawned[0][0] == "echo hi"


def test_run_named_target(runner):
    r, reg, be, _, _ = runner
    r.execute("@build npm i", anonymous=False)
    assert reg.get("console-build") is not None
    assert be.spawned[0][0] == "npm i"


def test_anonymous_creates_new_each_time(runner):
    r, reg, _, _, _ = runner
    r.execute("echo a", anonymous=True)
    r.execute("echo b", anonymous=True)
    assert reg.get("console-anon-1") is not None
    assert reg.get("console-anon-2") is not None


def test_cd_changes_panel(runner, tmp_path):
    r, reg, _, cd_calls, panel_cwd = runner
    sub = tmp_path / "sub"
    sub.mkdir()
    r.execute(f"cd {sub}", anonymous=False)
    assert panel_cwd["value"] == sub
    assert cd_calls == [sub]


def test_cd_bad_path_reports_error(runner, tmp_path):
    r, reg, _, _, _ = runner
    r.execute("cd /no/such/thing/here_xyzzy", anonymous=False)
    t = reg.get("console-default")
    assert t is not None
    joined = b"".join(t.chunks)
    assert b"cd:" in joined or b"No such" in joined or b"error" in joined.lower()


def test_busy_target_routes_input_to_stdin(runner):
    """When the target is running an interactive child, typing into the
    cmdline pipes the line into its stdin instead of being rejected."""
    r, reg, _, _, _ = runner
    t = reg.get_or_create("build")
    t.busy = True
    # No live handle in `_handles` → runner falls back to a dropped-input
    # notice on the busy target itself (not a "rejected" message on default).
    r.execute("@build something", anonymous=False)
    assert any(b"not running" in c for c in t.chunks)


def test_to_changes_default_target(runner):
    r, reg, be, _, _ = runner
    r.execute(":to build", anonymous=False)
    r.execute("echo hi", anonymous=False)
    assert reg.get("console-build") is not None
    assert be.spawned[0][0] == "echo hi"


# --- interactive: stdin / EOF / kill ----------------------------------

def _attach_live_handle(runner_obj, reg, target_name="default"):
    """Insert a live MockHandle into the runner's _handles dict."""
    target = reg.get_or_create(None if target_name == "default" else target_name)
    target.busy = True
    h = _MockHandle()
    runner_obj._handles[target.id] = h
    return target, h


def test_busy_target_pipes_to_stdin(runner):
    """When a live handle exists, a `run`-style line is written to stdin."""
    r, reg, _, _, _ = runner
    target, h = _attach_live_handle(r, reg)
    r.execute("import sys; sys.exit(0)", anonymous=False)
    # The text gets a newline appended and is forwarded to stdin.
    assert h.stdin_chunks == [b"import sys; sys.exit(0)\n"]
    # The same text is also echoed to the console so the user sees it.
    assert any(b"import sys" in c for c in target.chunks)


def test_send_eof_closes_stdin(runner):
    r, reg, _, _, _ = runner
    _, h = _attach_live_handle(r, reg)
    assert r.send_eof() is True
    assert h.stdin_closed is True


def test_send_eof_returns_false_when_no_handle(runner):
    r, _, _, _, _ = runner
    assert r.send_eof() is False


def test_kill_current_kills_handle(runner):
    r, reg, _, _, _ = runner
    _, h = _attach_live_handle(r, reg)
    assert r.kill_current() is True
    assert h.killed is True


def test_busy_callback_fires_on_spawn(runner):
    """on_busy_changed is invoked with (target_id, True) on spawn."""
    r, reg, _, _, _ = runner
    events: list[tuple[str, bool]] = []
    r._on_busy_changed = lambda tid, b: events.append((tid, b))
    r.execute("echo hi", anonymous=False)
    # MockBackend calls on_exit synchronously, so we should see both flips.
    assert ("console-default", True) in events
    assert ("console-default", False) in events
