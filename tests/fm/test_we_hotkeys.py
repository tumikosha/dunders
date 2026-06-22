"""we-mode hotkeys: command-line focus + cascade/tile arrangement.

These cover the terminal-independent rebind of the command-line focus
(alt+c stays, but ctrl+e reaches the app even on macOS where Option+C is
swallowed as "ç") and the new Cascade/Tile hotkeys, which previously were
only reachable via the menu / command palette.
"""
import tempfile
from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.fm.commandline import CommandLine


def _make_file(text: str = "print(1)\n") -> str:
    d = tempfile.mkdtemp()
    f = Path(d) / "a.py"
    f.write_text(text)
    return str(f)


@pytest.mark.asyncio
async def test_we_ctrl_e_focuses_command_line():
    app = DundersApp(launch_mode="we", initial_paths=[_make_file()])
    async with app.run_test() as pilot:
        await pilot.pause()
        # Focus starts on the editor, not the command line.
        cmd_input = app.query_one(CommandLine)._input
        assert app.focused is not cmd_input
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert app.focused is cmd_input



@pytest.mark.asyncio
async def test_we_tile_vertical_hotkey_dispatches():
    app = DundersApp(launch_mode="we", initial_paths=[_make_file()])
    async with app.run_test() as pilot:
        await pilot.pause()
        calls: list[str] = []
        orig = app.manager.tile_vertical
        app.manager.tile_vertical = lambda: (calls.append("tile_v"), orig())[1]
        await pilot.press("ctrl+u")
        await pilot.pause()
        assert calls == ["tile_v"]


@pytest.mark.asyncio
async def test_alt_dot_toggles_show_hidden_on_active_panel(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        assert panel is not None
        # Hidden files are shown by default; Alt+. toggles them off, then on.
        # (Alt+H was freed for the command-history popup; toggle-hidden moved to
        # Alt+. — mc parity.)
        assert panel.show_hidden is True
        await pilot.press("alt+full_stop")
        await pilot.pause()
        assert panel.show_hidden is False
        await pilot.press("alt+full_stop")
        await pilot.pause()
        assert panel.show_hidden is True
