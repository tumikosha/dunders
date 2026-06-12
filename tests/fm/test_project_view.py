"""Project View (F2): 1/4 tree on its own side + 3/4 editor filling the rest."""

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


async def test_layout_project_view_left_tree_geometry():
    app = DundersApp(launch_mode="fm")
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        # Use the right panel as an editor stand-in purely to exercise the math.
        app._layout_project_view(tree_win=left, editor_win=right)
        await _settle(pilot)

        W, H = app.desktop.usable_size
        tree_w = max(8, W // 4)
        assert left.region.x == 0
        assert left.region.width == tree_w
        assert right.region.x == tree_w
        assert left.region.width + right.region.width == W
        assert left.region.height == H


async def test_layout_project_view_right_tree_geometry():
    app = DundersApp(launch_mode="fm")
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        # Right panel is the tree; left panel stands in for the editor.
        app._layout_project_view(tree_win=right, editor_win=left)
        await _settle(pilot)

        W, H = app.desktop.usable_size
        tree_w = max(8, W // 4)
        assert right.region.width == tree_w
        assert right.region.x == W - tree_w
        assert left.region.x == 0
        assert left.region.width == W - tree_w


def _focus_panel_on_file(app, panel_id, file_path):
    """Focus a panel and put its cursor on `file_path`. Returns the FilePanel."""
    win = app.desktop.query_one(f"#{panel_id}", Window)
    panel = win.content
    app.desktop.focus_window(win)
    app.set_focus(panel)
    idx = next(i for i, e in enumerate(panel.entries) if e.path == file_path)
    panel.cursor = idx
    return panel


async def test_f2_in_left_panel_opens_project_view(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", f)

        app.action_project_view()
        await _settle(pilot)

        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        editors = _editor_windows(app)
        assert len(editors) == 1
        editor = editors[0]
        W, _ = app.desktop.usable_size
        tree_w = max(8, W // 4)
        # Tree docked left at 1/4, editor fills the rest, right panel hidden.
        assert left.region.x == 0
        assert left.region.width == tree_w
        assert editor.region.x == tree_w
        assert right not in app.desktop.windows
        assert right in app.desktop.hidden_windows
        assert app._project_tree_panel_id == "panel-left"
        assert app.desktop.focused_window is editor


async def test_f2_in_right_panel_docks_tree_right(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-right", f)

        app.action_project_view()
        await _settle(pilot)

        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        editor = _editor_windows(app)[0]
        W, _ = app.desktop.usable_size
        tree_w = max(8, W // 4)
        assert right.region.width == tree_w
        assert right.region.x == W - tree_w
        assert editor.region.x == 0
        assert left not in app.desktop.windows
        assert app._project_tree_panel_id == "panel-right"


async def test_f2_minimizes_existing_editor(tmp_path):
    f1 = tmp_path / "a.py"
    f1.write_text("a = 1\n")
    f2 = tmp_path / "b.py"
    f2.write_text("b = 2\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", f1)
        app.action_project_view()
        await _settle(pilot)
        first_editor = _editor_windows(app)[0]

        # Second F2 on a different file: the first editor goes to the tray.
        _focus_panel_on_file(app, "panel-left", f2)
        app.action_project_view()
        await _settle(pilot)

        assert first_editor in app.desktop.minimized_windows
        editors = _editor_windows(app)
        assert len(editors) == 1
        assert editors[0] is not first_editor
        assert app.desktop.focused_window is editors[0]


async def test_f2_on_directory_is_noop(tmp_path):
    (tmp_path / "subdir").mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", tmp_path / "subdir")

        app.action_project_view()
        await _settle(pilot)

        assert _editor_windows(app) == []
        assert app._project_tree_panel_id is None
        assert app.desktop.query_one("#panel-right", Window) in app.desktop.windows


async def test_f2_in_editor_reveals_left_tree(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # Enter Project View from the left panel to get a real editor window.
        _focus_panel_on_file(app, "panel-left", f)
        app.action_project_view()
        await _settle(pilot)
        editor = _editor_windows(app)[0]
        app.desktop.focus_window(editor)
        await _settle(pilot)

        # Now press F2 from the editor — it should re-apply the same layout.
        app.action_project_view()
        await _settle(pilot)

        left = app.desktop.query_one("#panel-left", Window)
        right = app.desktop.query_one("#panel-right", Window)
        W, _ = app.desktop.usable_size
        tree_w = max(8, W // 4)
        # Default tree is panel-left, docked left at 1/4; editor fills the 3/4.
        assert left in app.desktop.windows
        assert left.region.x == 0
        assert left.region.width == tree_w
        assert editor.region.x == tree_w
        assert right not in app.desktop.windows
        assert app._project_tree_panel_id == "panel-left"


async def test_f2_in_editor_focuses_tree(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", f)
        app.action_project_view()
        await _settle(pilot)
        editor = _editor_windows(app)[0]
        app.desktop.focus_window(editor)
        await _settle(pilot)
        assert app.desktop.focused_window is editor

        # F2 from the editor must jump focus INTO the tree panel.
        app.action_project_view()
        await _settle(pilot)

        tree = app.desktop.query_one("#panel-left", Window)
        assert app.desktop.focused_window is tree
        assert app.focused is tree.content


async def test_f2_in_editor_uses_remembered_tree_side(tmp_path):
    f1 = tmp_path / "a.py"
    f1.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # Enter Project View from the RIGHT panel so the tree side is remembered.
        _focus_panel_on_file(app, "panel-right", f1)
        app.action_project_view()
        await _settle(pilot)
        editor = _editor_windows(app)[0]
        app.desktop.focus_window(editor)
        await _settle(pilot)

        # F2 from the editor re-docks using the remembered right side.
        app.action_project_view()
        await _settle(pilot)

        right = app.desktop.query_one("#panel-right", Window)
        W, _ = app.desktop.usable_size
        tree_w = max(8, W // 4)
        assert right.region.x == W - tree_w
        assert editor.region.x == 0
        assert app._project_tree_panel_id == "panel-right"


async def test_f2_commands_registered(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        panel = _focus_panel_on_file(app, "panel-left", f)
        await _settle(pilot)
        # panel.project_view is a focus-scoped command; check via get_commands().
        cmds = {c.id: c for c in panel.get_commands()}
        cmd = cmds.get("panel.project_view")
        assert cmd is not None
        assert cmd.hotkey == "f1"


async def test_f1_hotkey_routes_from_panel(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", f)
        await _settle(pilot)

        await pilot.press("f1")
        await _settle(pilot)

        assert len(_editor_windows(app)) == 1
        assert app._project_tree_panel_id == "panel-left"


async def test_toggle_panel_clears_project_view_state(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", f)
        app.action_project_view()
        await _settle(pilot)
        assert app._project_tree_panel_id == "panel-left"

        # Ctrl+1 / Ctrl+2 (the exit path) clears Project View state.
        app._toggle_panel("panel-right")
        await _settle(pilot)
        assert app._project_tree_panel_id is None


async def test_f2_switches_tree_to_short_view(tmp_path):
    """Entering Project View narrows the tree panel to Short view; exiting via
    a panel toggle restores the panel's previous view mode."""
    from dunders.fm.panel_view import PanelViewMode

    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        panel = app.desktop.query_one("#panel-left", Window).content
        # Start from a wide mode so the switch (and restore) is observable.
        panel.view_mode = PanelViewMode.FULL
        _focus_panel_on_file(app, "panel-left", f)

        app.action_project_view()
        await _settle(pilot)
        assert panel.view_mode is PanelViewMode.SHORT

        # Exit Project View; the panel returns to its prior mode.
        app._toggle_panel("panel-right")
        await _settle(pilot)
        assert panel.view_mode is PanelViewMode.FULL


async def test_closing_editor_restores_tree_view_mode(tmp_path):
    """Closing the Project View editor restores the tree's previous view mode."""
    from dunders.fm.panel_view import PanelViewMode

    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        panel = app.desktop.query_one("#panel-left", Window).content
        panel.view_mode = PanelViewMode.MEDIUM
        _focus_panel_on_file(app, "panel-left", f)
        app.action_project_view()
        await _settle(pilot)
        assert panel.view_mode is PanelViewMode.SHORT

        editor_win = _editor_windows(app)[0]
        app.desktop.post_message(Window.Closed(editor_win))
        await _settle(pilot)
        assert panel.view_mode is PanelViewMode.MEDIUM


async def test_closing_editor_exits_project_view(tmp_path):
    """Regression: closing the Project View editor via close box must clear
    _project_tree_panel_id and restore the normal two-panel layout."""
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", f)
        app.action_project_view()
        await _settle(pilot)

        # Confirm we're in Project View: state set, right panel hidden.
        assert app._project_tree_panel_id == "panel-left"
        right = app.desktop.query_one("#panel-right", Window)
        assert right not in app.desktop.windows

        # Close the editor window (emulate close-box click).
        editor_win = _editor_windows(app)[0]
        app.desktop.post_message(Window.Closed(editor_win))
        await _settle(pilot)

        # State must be cleared.
        assert app._project_tree_panel_id is None
        # Both panels must be back in the visible window list.
        left = app.desktop.query_one("#panel-left", Window)
        assert left in app.desktop.windows
        assert right in app.desktop.windows
        # They must be tiled as two adjacent halves.
        usable_w, _ = app.desktop.usable_size
        assert right.region.x - left.region.x == left.region.width
        assert left.region.width + right.region.width == usable_w


@pytest.mark.parametrize("width,height", [(160, 50), (70, 20)])
async def test_project_view_survives_resize(tmp_path, width, height):
    f = tmp_path / "a.py"
    f.write_text("a = 1\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _focus_panel_on_file(app, "panel-left", f)
        app.action_project_view()
        await _settle(pilot)

        await pilot.resize_terminal(width, height)
        await _settle(pilot)

        left = app.desktop.query_one("#panel-left", Window)
        editor = _editor_windows(app)[0]
        W, _ = app.desktop.usable_size
        tree_w = max(8, W // 4)
        assert left.region.x == 0
        assert left.region.width == tree_w
        assert editor.region.x == tree_w
        assert left.region.width + editor.region.width == W
