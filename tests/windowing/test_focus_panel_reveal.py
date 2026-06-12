"""Focusing a panel must reveal it when it was mounted hidden (editor/cli modes)."""

from dunders.app import DundersApp
from dunders.fm.file_panel import FilePanel
from dunders.windowing import Window


async def test_focus_panel_reveals_only_requested_panel_on_top():
    # Editor launch mode mounts both panels hidden, with the editor on top.
    app = DundersApp(launch_mode="editor", initial_path="/tmp/foo.txt")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.desktop is not None
        visible_ids = {w.id for w in app.desktop.windows}
        assert "panel-right" not in visible_ids  # hidden on startup

        app._focus_panel("panel-right")
        await pilot.pause()

        win = app.desktop.query_one("#panel-right", Window)
        assert win in app.desktop.windows, "panel-right was not revealed"
        assert isinstance(win.content, FilePanel)
        # Only the requested panel is revealed — the other stays hidden.
        left = app.desktop.query_one("#panel-left", Window)
        assert left not in app.desktop.windows, "panel-left should stay hidden"
        # The revealed panel is focused and on top of the z-order (last entry).
        assert app.desktop.focused_window is win
        assert app.desktop.windows[-1] is win, "panel not raised above the editor"
        # It has a sane (half-screen) width, not the stale 40-col mount size.
        W, _H = app.desktop.usable_size
        assert int(win.styles.width.value) >= W // 2 - 1
        # Menu-close focus restoration is pinned to the panel, not the editor.
        assert app._pre_menu_window is win


async def test_focus_panel_survives_menu_close_restoration():
    # Reproduce the menu path: open the menu (captures the editor as the
    # pre-menu window), pick "focus panel", then close the menu — the panel
    # must remain on top rather than the editor being raised back.
    app = DundersApp(launch_mode="editor", initial_path="/tmp/foo.txt")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.desktop is not None
        editor_win = app.desktop.focused_window
        app._pre_menu_window = editor_win  # as action_menu would capture
        app._focus_panel("panel-left")
        # Simulate the menu closing (active_index → None).
        app._on_menu_active_index_changed(None)
        await pilot.pause()
        win = app.desktop.query_one("#panel-left", Window)
        assert app.desktop.windows[-1] is win, "editor was raised back over panel"
        assert app.desktop.focused_window is win


async def test_focus_panel_focuses_already_visible_panel():
    # FM mode shows both panels; focusing must not break anything.
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.desktop is not None
        app._focus_panel("panel-right")
        await pilot.pause()
        win = app.desktop.query_one("#panel-right", Window)
        assert win in app.desktop.windows
        assert app.desktop.focused_window is win
