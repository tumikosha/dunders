"""Enter / double-click on an executable file runs it in the console."""

import os
import shlex
import stat
from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.fm.commandline import CommandLine
from dunders.fm.file_entry import FileEntry
from dunders.fm.file_panel import FilePanel


# --- pure detection logic ------------------------------------------------


def _make_exec(path: Path, body: str = "echo hi\n") -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_executable_command_xbit(tmp_path: Path):
    p = _make_exec(tmp_path / "run.bin", "binary\n")
    cmd = DundersApp._executable_command(p)
    # x-bit set → run the path directly, shell-quoted.
    assert cmd == shlex.quote(str(p))


def test_executable_command_plain_text_is_none(tmp_path: Path):
    p = tmp_path / "notes.txt"
    p.write_text("just text, no shebang, no x-bit\n")
    assert DundersApp._executable_command(p) is None


def test_executable_command_shebang_without_xbit(tmp_path: Path):
    p = tmp_path / "script"
    p.write_text("#!/usr/bin/env python3\nprint('hi')\n")
    assert not os.access(p, os.X_OK)
    cmd = DundersApp._executable_command(p)
    assert cmd is not None
    assert cmd.startswith("/usr/bin/env python3 ")
    assert str(p) in cmd


def test_executable_command_known_extension(tmp_path: Path):
    p = tmp_path / "deploy.sh"
    p.write_text("echo deploy\n")  # no x-bit, no shebang
    cmd = DundersApp._executable_command(p)
    assert cmd is not None
    assert cmd.startswith("sh ")
    assert str(p) in cmd


def test_executable_command_unknown_extension_is_none(tmp_path: Path):
    p = tmp_path / "data.json"
    p.write_text('{"k": 1}\n')
    assert DundersApp._executable_command(p) is None


def test_executable_command_directory_is_none(tmp_path: Path):
    d = tmp_path / "subdir"
    d.mkdir()
    assert DundersApp._executable_command(d) is None


# --- routing through the running app --------------------------------------


class _SpyHandover:
    def __init__(self):
        self.ran: list[tuple[str, str]] = []

    def run_foreground(self, cmd, cwd):
        self.ran.append((cmd, str(cwd)))
        return 0

    def command_screen(self, cwd):  # pragma: no cover - unused here
        pass

    def shutdown(self):  # pragma: no cover - unused here
        pass


def _entry(path: Path, *, is_dir: bool = False) -> FileEntry:
    return FileEntry(
        path=path,
        name=path.name,
        size=0,
        mtime=0.0,
        is_dir=is_dir,
        is_symlink=False,
        is_executable=bool(path.is_file() and os.access(path, os.X_OK)),
    )


def _activate(app: DundersApp, path: Path, *, is_dir: bool = False) -> None:
    panel = app.query(FilePanel).first()
    app.on_file_panel_item_activated(
        FilePanel.ItemActivated(panel, _entry(path, is_dir=is_dir))
    )


@pytest.mark.asyncio
async def test_fm_executable_runs_via_handover(tmp_path):
    # A full-screen TUI needs the real terminal, so executables hand over via
    # the handover layer (like mc) rather than the embedded relay console.
    p = _make_exec(tmp_path / "hello", "#!/bin/sh\necho hi\n")
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        _activate(app, p)
        assert spy.ran and spy.ran[0][0] == shlex.quote(str(p))


@pytest.mark.asyncio
async def test_we_executable_runs_via_handover(tmp_path):
    p = _make_exec(tmp_path / "hello", "#!/bin/sh\necho hi\n")
    app = DundersApp(launch_mode="we")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        _activate(app, p)
        assert spy.ran and spy.ran[0][0] == shlex.quote(str(p))


@pytest.mark.asyncio
async def test_non_executable_opens_editor_not_handover(tmp_path):
    p = tmp_path / "readme.txt"
    p.write_text("hello\n")
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        before = len(list(app.desktop.windows))
        _activate(app, p)
        # Nothing handed over; an editor window was opened instead.
        assert spy.ran == []
        assert len(list(app.desktop.windows)) == before + 1


@pytest.mark.asyncio
async def test_we_mc_executable_runs_via_handover(tmp_path):
    p = _make_exec(tmp_path / "tool", "#!/bin/sh\necho run\n")
    app = DundersApp(launch_mode="we-mc", terminal_mode="suspend")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        _activate(app, p)
        assert spy.ran and spy.ran[0][0] == shlex.quote(str(p))


# --- typed command line also hands over (mc-style) ------------------------


@pytest.mark.asyncio
async def test_typed_command_runs_via_handover_fm(tmp_path):
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        app.on_command_line_submitted(CommandLine.Submitted("claude"))
        assert spy.ran and spy.ran[0][0] == "claude"


@pytest.mark.asyncio
async def test_typed_command_runs_via_handover_we(tmp_path):
    app = DundersApp(launch_mode="we")
    async with app.run_test() as pilot:
        await pilot.pause()
        spy = _SpyHandover()
        app._handover = spy
        app.on_command_line_submitted(CommandLine.Submitted("vim"))
        assert spy.ran and spy.ran[0][0] == "vim"
