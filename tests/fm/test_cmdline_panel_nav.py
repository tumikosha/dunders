"""Up/down at the cmdline buffer edge drives the file panel when panels are
visible, and falls back to command history when only the console is shown."""

from __future__ import annotations

import pytest

from dunders.app import DundersApp
from dunders.fm.file_panel import FilePanel


def _active_panel(app: DundersApp) -> FilePanel:
    return app._active_panel()


@pytest.mark.asyncio
async def test_panels_visible_predicate_tracks_window_stack():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._panels_visible() is True

    app = DundersApp(launch_mode="cli", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._panels_visible() is False


@pytest.mark.asyncio
async def test_cmdline_up_down_moves_panel_cursor_when_panels_visible():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = _active_panel(app)
        assert panel is not None
        # Need at least two entries to move the cursor.
        if len(panel.entries) < 2:
            pytest.skip("need >=2 entries in /tmp to exercise cursor movement")
        panel.cursor = 0

        # Boundary down on a single-line cmdline buffer -> panel cursor +1.
        app.command_line._input.action_cmd_down()
        await pilot.pause()
        assert panel.cursor == 1

        app.command_line._input.action_cmd_up()
        await pilot.pause()
        assert panel.cursor == 0


@pytest.mark.asyncio
async def test_cmdline_up_down_uses_history_when_panels_hidden():
    app = DundersApp(launch_mode="cli", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.command_history.append("ls")
        app.command_history.append("pwd")
        app.command_history.reset_cursor()

        # Panels hidden -> _route_nav returns False -> history navigation.
        app.command_line._input.action_cmd_up()
        await pilot.pause()
        assert app.command_line.text == "pwd"
        app.command_line._input.action_cmd_up()
        await pilot.pause()
        assert app.command_line.text == "ls"


@pytest.mark.asyncio
async def test_cmdline_midbuffer_up_moves_text_cursor_not_panel():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = _active_panel(app)
        panel.cursor = 0
        inp = app.command_line._input
        # Two-line buffer with the cursor on the second row.
        inp.load_text("one\ntwo")
        inp.move_cursor((1, 0))
        await pilot.pause()

        inp.action_cmd_up()
        await pilot.pause()
        # Text cursor moved up a row; panel untouched.
        assert inp.cursor_location[0] == 0
        assert panel.cursor == 0
