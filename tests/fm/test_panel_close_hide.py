"""File panels: close button + Left/Right > Toggle visibility (Alt+F1/Alt+F2).

Closing or toggling a panel HIDES it (the panel is looked up by id throughout
the app and must persist), never destroys it.
"""

from __future__ import annotations

import pytest

from dunders.app import DundersApp
from dunders.windowing import Window


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


@pytest.mark.asyncio
async def test_panels_have_close_box_and_hide_on_close_flag():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await _settle(pilot)
        for pid in ("panel-left", "panel-right"):
            win = app.desktop.query_one(f"#{pid}", Window)
            assert win.decorations.close_box is True
            assert getattr(win, "hide_on_close", False) is True


@pytest.mark.asyncio
async def test_close_box_hides_panel_instead_of_destroying_it():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await _settle(pilot)
        win = app.desktop.query_one("#panel-left", Window)
        assert win in app.desktop.windows

        # Clicking the close box posts Window.Closed; emulate that.
        app.desktop.post_message(Window.Closed(win))
        await _settle(pilot)

        # Hidden, not destroyed: still queryable by id, moved to hidden_windows.
        assert app.desktop.query_one("#panel-left", Window) is win
        assert win not in app.desktop.windows
        assert win in app.desktop.hidden_windows


@pytest.mark.asyncio
async def test_toggle_visibility_hides_then_shows_only_that_panel():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await _settle(pilot)
        right = app.desktop.query_one("#panel-right", Window)
        left = app.desktop.query_one("#panel-left", Window)

        # First toggle hides it.
        app._toggle_panel("panel-right")
        await _settle(pilot)
        assert right not in app.desktop.windows
        assert right in app.desktop.hidden_windows
        assert left in app.desktop.windows  # the other panel is untouched

        # Second toggle brings it back.
        app._toggle_panel("panel-right")
        await _settle(pilot)
        assert right in app.desktop.windows


@pytest.mark.asyncio
async def test_toggle_commands_registered_with_hotkeys():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await _settle(pilot)
        reg = app.command_registry
        left = reg.get("panel.left.toggle")
        right = reg.get("panel.right.toggle")
        assert left is not None and right is not None
        assert left.hotkey == "ctrl+1"
        assert right.hotkey == "ctrl+2"
        # The replaced commands are gone.
        assert reg.get("panel.focus_left") is None
        assert reg.get("panel.focus_right") is None
        assert reg.get("panel.left.hide") is None
        assert reg.get("panel.right.hide") is None


@pytest.mark.asyncio
async def test_toggle_hotkey_routes_through_on_key():
    """The full key path (App.on_key -> router) toggles the panel."""
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await _settle(pilot)
        right = app.desktop.query_one("#panel-right", Window)
        assert right in app.desktop.windows
        await pilot.press("ctrl+2")
        await _settle(pilot)
        assert right not in app.desktop.windows
        await pilot.press("ctrl+2")
        await _settle(pilot)
        assert right in app.desktop.windows
