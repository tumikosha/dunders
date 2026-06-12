"""Ctrl+P: bring the two file panels back full-screen (global)."""

from __future__ import annotations

import pytest

from dunders.app import DundersApp
from dunders.windowing import Window
from dunders.windowing.editor import EditorContent


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


def _editor_windows(app):
    return [w for w in app.desktop.windows if isinstance(w.content, EditorContent)]


def _focus_panel_on_file(app, panel_id, file_path):
    win = app.desktop.query_one(f"#{panel_id}", Window)
    panel = win.content
    idx = next(i for i, e in enumerate(panel.entries) if e.path == file_path)
    panel.cursor = idx
    app.desktop.focus_window(win)
    app.set_focus(panel)


@pytest.mark.asyncio
async def test_ctrl_p_resolves_to_panels_fullscreen():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        cmd = app.dispatcher.hotkey_lookup("ctrl+p")
        assert cmd is not None and cmd.id == "panels.fullscreen"


@pytest.mark.asyncio
async def test_panels_fullscreen_minimizes_editor_and_tiles(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # Enter Project View to spawn an editor and narrow the tree.
        _focus_panel_on_file(app, "panel-left", f)
        app.action_project_view()
        await _settle(pilot)
        editor = _editor_windows(app)[0]
        assert app._project_tree_panel_id is not None

        app.action_panels_fullscreen()
        await _settle(pilot)

        # Editor is stashed in the tray, Project View is exited.
        assert editor in app.desktop.minimized_windows
        assert editor not in app.desktop.windows
        assert app._project_tree_panel_id is None

        # Both panels are visible and tiled side by side filling the width.
        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        assert left in app.desktop.windows and right in app.desktop.windows
        W, _ = app.desktop.usable_size
        half = max(3, W // 2)
        assert left.region.x == 0
        assert right.region.x == half
        assert right.region.x + right.region.width == W

        # Focus landed on a file panel.
        from dunders.fm.file_panel import FilePanel
        assert isinstance(app.focused, FilePanel)
        assert app.desktop.focused_window in (left, right)


@pytest.mark.asyncio
async def test_panels_fullscreen_fills_full_height_over_console(tmp_path):
    """With a console open, panels normally get the top half; Ctrl+P fills all."""
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # Bring up the default console (bottom-half split).
        app.console_registry.get_or_create(None)
        await _settle(pilot)
        console = app._console_default_window
        assert console is not None and console in app.desktop.windows
        app._tile_panels()
        await _settle(pilot)

        _, H = app.desktop.usable_size
        left = app.desktop.query_one("#panel-left", Window)
        # Split layout: panels only get the top half.
        assert left.region.height == H - max(3, H // 2)

        # Ctrl+P: console is stashed, panels fill the FULL height.
        app.action_panels_fullscreen()
        await _settle(pilot)
        assert console not in app.desktop.windows
        assert left.region.height == H

        # Re-surfacing the console restores the split.
        app._ensure_console_visible()
        await _settle(pilot)
        assert console in app.desktop.windows
        assert left.region.height == H - max(3, H // 2)


@pytest.mark.asyncio
async def test_panels_fullscreen_reveals_hidden_panel(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # Hide the right panel (Ctrl+2 toggle path).
        app._toggle_panel("panel-right")
        await _settle(pilot)
        right = app.desktop.query_one("#panel-right", Window)
        assert right not in app.desktop.windows

        app.action_panels_fullscreen()
        await _settle(pilot)

        assert right in app.desktop.windows
        left = app.desktop.query_one("#panel-left", Window)
        assert left in app.desktop.windows
