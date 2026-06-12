"""Focus-routing tests for CommandLine ↔ panel interaction.

Goal: when dunders starts in fm mode the CommandLine input has keyboard
focus (cursor visible, typing works).  F-key commands still route
through the active panel window even though Textual widget focus is on
the CommandLine.
"""
from __future__ import annotations

import pytest

from dunders.app import DundersApp
from dunders.fm.commandline import CommandLine
from dunders.windowing import Window
from dunders.fm.file_panel import FilePanel
from textual.widgets import TextArea


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cmdline_input(app: DundersApp) -> TextArea | None:
    """Return the input widget inside the CommandLine, or None."""
    try:
        return app.query_one("#cmdline-input", TextArea)
    except Exception:
        return None


def _panel_window(app: DundersApp, panel_id: str) -> Window | None:
    """Return the Window with the given id, or None."""
    if app.desktop is None:
        return None
    try:
        return app.desktop.query_one(f"#{panel_id}", Window)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_focused_widget_is_left_panel_on_mount(tmp_path):
    """On startup in fm mode (no file arg) panel-left holds Textual focus."""
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()  # allow call_after_refresh to fire
        node = app.focused
        found = False
        while node is not None:
            if getattr(node, "id", None) == "panel-left":
                found = True
                break
            node = getattr(node, "parent", None)
        assert found, f"focused widget {app.focused!r} is not inside panel-left"


@pytest.mark.asyncio
async def test_active_panel_window_is_left_after_mount(tmp_path):
    """desktop.focused_window is panel-left after startup (F-key routing target)."""
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert app.desktop is not None
        fw = app.desktop.focused_window
        assert fw is not None
        assert fw.id == "panel-left"


@pytest.mark.asyncio
async def test_last_focused_panel_window_is_set_after_mount(tmp_path):
    """_last_focused_panel_window tracks panel-left after startup."""
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        win = app._last_focused_panel_window
        assert win is not None
        assert win.id == "panel-left"
        assert isinstance(win.content, FilePanel)


@pytest.mark.asyncio
async def test_tab_from_cmdline_focuses_active_panel(tmp_path):
    """Tab from CommandLine moves Textual focus to the active panel."""
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Move focus to cmdline (default focus is now panel-left).
        app._focus_command_line()
        await pilot.pause()
        inp = _cmdline_input(app)
        assert app.focused is inp

        # Send Tab.
        await pilot.press("tab")
        await pilot.pause()

        # Focus should now be on the panel-left content.
        left_win = _panel_window(app, "panel-left")
        assert left_win is not None
        focused = app.focused
        # Walk up from focused widget to confirm it's inside panel-left.
        node = focused
        found = False
        while node is not None:
            if getattr(node, "id", None) == "panel-left":
                found = True
                break
            node = getattr(node, "parent", None)
        assert found, f"focused widget {focused!r} is not inside panel-left"


@pytest.mark.asyncio
async def test_tab_from_left_panel_moves_to_right_panel(tmp_path):
    """Tab from panel-left moves Textual focus to panel-right (normal swap)."""
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()

        # Move focus to panel-left first.
        app._focus_panel("panel-left")
        await pilot.pause()

        # Now Tab should go to panel-right.
        await pilot.press("tab")
        await pilot.pause()

        node = app.focused
        found = False
        while node is not None:
            if getattr(node, "id", None) == "panel-right":
                found = True
                break
            node = getattr(node, "parent", None)
        assert found, f"focused widget {app.focused!r} is not inside panel-right"


@pytest.mark.asyncio
async def test_esc_from_cmdline_returns_to_active_panel(tmp_path):
    """Esc when CommandLine has focus moves focus to the active panel."""
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Move focus to cmdline (default focus is now panel-left).
        app._focus_command_line()
        await pilot.pause()
        inp = _cmdline_input(app)
        assert app.focused is inp

        # Send Esc.
        await pilot.press("escape")
        await pilot.pause()

        # Focus should be on panel-left (the active panel at startup).
        node = app.focused
        found = False
        while node is not None:
            if getattr(node, "id", None) == "panel-left":
                found = True
                break
            node = getattr(node, "parent", None)
        assert found, f"focused widget {app.focused!r} is not inside panel-left after Esc"


@pytest.mark.asyncio
async def test_cmdline_receives_typed_characters(tmp_path):
    """Typing characters while CommandLine has focus updates its text value.

    This is the critical test that distinguishes 'focus reported by app.focused'
    from 'focus actually delivers key events'.  If the Input is truly focused,
    pressing 'a', 'b', 'c' must produce the text 'abc' in the cmdline.
    """
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()  # allow call_after_refresh to fire
        inp = _cmdline_input(app)
        assert inp is not None, "cmdline input widget not found"
        # Default focus is now panel-left; move it to the cmdline first.
        app._focus_command_line()
        await pilot.pause()
        assert app.focused is inp, (
            f"expected cmdline input to have focus before typing, got {app.focused!r}"
        )

        # Type three characters.
        await pilot.press("a")
        await pilot.press("b")
        await pilot.press("c")
        await pilot.pause()

        assert inp.value == "abc", (
            f"cmdline did not receive typed characters — value={inp.value!r}, "
            f"focused={app.focused!r}"
        )
