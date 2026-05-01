import pytest

from tyui.app import TyuiApp
from tyui.fm.file_panel import FilePanel
from tyui.windowing import Desktop, MenuBar, StatusBar, Window
from tyui.fm.commandline import CommandLine


@pytest.mark.asyncio
async def test_app_mounts_chrome_widgets():
    app = TyuiApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(MenuBar)) == 1
        assert len(app.query(Desktop)) == 1
        assert len(app.query(StatusBar)) == 1
        assert len(app.query(CommandLine)) == 1


@pytest.mark.asyncio
async def test_app_mounts_two_panel_windows_in_fm_mode():
    app = TyuiApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        panel_windows = [w for w in desktop.windows if w.id in ("panel-left", "panel-right")]
        assert len(panel_windows) == 2
        # Both panel contents are FilePanels at /tmp
        contents = [w.content for w in panel_windows]
        assert all(isinstance(c, FilePanel) for c in contents)
        assert all(str(c.cwd) == "/tmp" for c in contents)


@pytest.mark.asyncio
async def test_app_status_bar_shows_default_fkey_labels():
    app = TyuiApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        sb = app.query_one(StatusBar)
        labels = {item.label for item in sb.items}
        assert "Help" in labels
        assert "Edit" in labels
        assert "Quit" in labels


@pytest.mark.asyncio
async def test_app_editor_mode_hides_panels_initially():
    """tyui <file> should mount panels but hide them; editor placeholder is visible."""
    app = TyuiApp(launch_mode="editor", initial_path="/tmp/foo.txt")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        # Panels exist but are not in desktop.windows (the visible list).
        all_panel_ids = {"panel-left", "panel-right"}
        visible_ids = {w.id for w in desktop.windows}
        assert all_panel_ids.isdisjoint(visible_ids)


@pytest.mark.asyncio
async def test_app_cli_mode_hides_panels_and_mounts_agent_stub():
    app = TyuiApp(launch_mode="cli")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        ids = {w.id for w in desktop.windows}
        assert "agent" in ids
        assert "panel-left" not in ids
        assert "panel-right" not in ids


def test_main_arg_parsing(monkeypatch, tmp_path):
    """tyui.main.main forwards the right launch_mode/initial_path to TyuiApp."""
    import tyui.main as main_mod

    captured: dict = {}

    class _FakeApp:
        def __init__(self, *, launch_mode, initial_path) -> None:
            captured["launch_mode"] = launch_mode
            captured["initial_path"] = initial_path

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr(main_mod, "TyuiApp", _FakeApp)

    # Case 1: no args -> fm mode, no path
    monkeypatch.setattr("sys.argv", ["tyui"])
    main_mod.main()
    assert captured == {"launch_mode": "fm", "initial_path": None, "ran": True}

    # Case 2: directory path -> fm mode with path
    captured.clear()
    monkeypatch.setattr("sys.argv", ["tyui", str(tmp_path)])
    main_mod.main()
    assert captured["launch_mode"] == "fm"
    assert captured["initial_path"] == str(tmp_path)

    # Case 3: file path -> editor mode
    captured.clear()
    file_path = tmp_path / "foo.txt"
    file_path.write_text("hi")
    monkeypatch.setattr("sys.argv", ["tyui", str(file_path)])
    main_mod.main()
    assert captured["launch_mode"] == "editor"
    assert captured["initial_path"] == str(file_path)

    # Case 4: --cli -> cli mode
    captured.clear()
    monkeypatch.setattr("sys.argv", ["tyui", "--cli"])
    main_mod.main()
    assert captured["launch_mode"] == "cli"
    assert captured["initial_path"] is None


def _focused_panel_id(app):
    """Return 'panel-left' / 'panel-right' / None for the currently focused widget.

    The Window content (FilePanel) is the focusable widget; walk up to its
    enclosing Window to read the id.
    """
    node = app.focused
    while node is not None:
        if getattr(node, "id", None) in ("panel-left", "panel-right"):
            return node.id
        node = getattr(node, "parent", None)
    return None


@pytest.mark.asyncio
async def test_app_left_panel_has_focus_on_mount_in_fm_mode():
    app = TyuiApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused is not None
        assert _focused_panel_id(app) == "panel-left"


@pytest.mark.asyncio
async def test_app_tab_alternates_panels(tmp_path):
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Press Tab — should land on panel-right (or its content).
        await pilot.press("tab")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-right"
        # Press Tab again — back to panel-left.
        await pilot.press("tab")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-left"


@pytest.mark.asyncio
async def test_app_alt_l_and_alt_r_force_focus(tmp_path):
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("alt+r")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-right"
        await pilot.press("alt+l")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-left"


@pytest.mark.asyncio
async def test_app_panels_have_entries_after_mount(tmp_path):
    """Refreshing both panels at mount means they're usable immediately."""
    (tmp_path / "alpha.txt").write_text("")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop
        desktop = app.query_one(Desktop)
        for win in desktop.windows:
            if win.id in ("panel-left", "panel-right"):
                panel = win.content
                assert isinstance(panel, FilePanel)
                names = [e.name for e in panel.entries]
                assert "alpha.txt" in names


@pytest.mark.asyncio
async def test_app_f9_then_esc_returns_focus_to_panel(tmp_path):
    """F9 enters the menu, Esc returns focus to the previously active panel."""
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Sanity: panel-left has focus on mount.
        assert _focused_panel_id(app) == "panel-left"
        await pilot.press("f9")
        await pilot.pause()
        # MenuBar is now focused — no panel id under app.focused.
        assert _focused_panel_id(app) is None
        await pilot.press("escape")
        await pilot.pause()
        # Focus is back on the panel.
        assert _focused_panel_id(app) == "panel-left"


@pytest.mark.asyncio
async def test_app_panels_fill_full_desktop_width():
    """Two panels combined should cover the entire desktop width and
    height with no gap; each ~half the width. This verifies the deferred
    layout fired and overrode the hard-coded 40x12 placeholder size."""
    from tyui.windowing import Desktop, Window

    app = TyuiApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()  # second tick lets call_after_refresh fire
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window)
        right = desktop.query_one("#panel-right", Window)
        # Combined width covers the full desktop.
        assert left.size.width + right.size.width == desktop.size.width
        # Neither panel is the placeholder 40-wide.
        assert left.size.width > 40 or desktop.size.width <= 80
        assert right.size.width > 40 or desktop.size.width <= 80
        # Panels span the full desktop height (after fill-the-screen layout).
        assert left.size.height == desktop.size.height
        assert right.size.height == desktop.size.height


@pytest.mark.asyncio
async def test_app_f7_creates_directory_in_active_panel(tmp_path):
    """F7 -> input "newdir" -> Enter -> directory created under active panel cwd."""
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("f7")
        await pilot.pause()
        from tyui.fm.dialogs import InputDialog
        dialog = app.query_one(InputDialog)
        dialog.set_value("newdir")
        dialog.action_submit()
        await pilot.pause()
        assert (tmp_path / "newdir").is_dir()


@pytest.mark.asyncio
async def test_app_f7_cancel_does_not_create_anything(tmp_path):
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("f7")
        await pilot.pause()
        from tyui.fm.dialogs import InputDialog
        dialog = app.query_one(InputDialog)
        dialog.action_cancel()
        await pilot.pause()
        # No new entries should have appeared.
        assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_app_f8_deletes_cursor_target_after_confirm(tmp_path):
    """F8 with cursor on a file -> ConfirmDialog -> Yes -> file removed."""
    target = tmp_path / "doomed.txt"
    target.write_text("")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Move cursor onto doomed.txt in the left panel.
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        win = desktop.query_one("#panel-left", Window)
        panel = win.content
        assert isinstance(panel, FilePanel)
        idx = next(i for i, e in enumerate(panel.entries) if e.name == "doomed.txt")
        panel.cursor = idx
        # Press F8 — confirm dialog opens.
        await pilot.press("f8")
        await pilot.pause()
        from tyui.fm.dialogs import ConfirmDialog
        dialog = app.query_one(ConfirmDialog)
        dialog.action_confirm()
        await pilot.pause()
        # File deleted.
        assert not target.exists()


@pytest.mark.asyncio
async def test_app_f8_cancel_keeps_file(tmp_path):
    target = tmp_path / "safe.txt"
    target.write_text("")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        win = desktop.query_one("#panel-left", Window)
        panel = win.content
        idx = next(i for i, e in enumerate(panel.entries) if e.name == "safe.txt")
        panel.cursor = idx
        await pilot.press("f8")
        await pilot.pause()
        from tyui.fm.dialogs import ConfirmDialog
        dialog = app.query_one(ConfirmDialog)
        dialog.action_cancel()
        await pilot.pause()
        assert target.exists()


@pytest.mark.asyncio
async def test_app_f8_with_no_targets_is_noop(tmp_path):
    """Cursor on '..' with empty selection: F8 does nothing."""
    sub = tmp_path / "sub"
    sub.mkdir()
    app = TyuiApp(launch_mode="fm", initial_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Cursor is on ".." by default.
        await pilot.press("f8")
        await pilot.pause()
        # No ConfirmDialog should have been shown.
        from tyui.fm.dialogs import ConfirmDialog
        assert not list(app.query(ConfirmDialog))


@pytest.mark.asyncio
async def test_app_f5_copies_to_opposite_panel_cwd(tmp_path):
    """F5: copy active panel's cursor entry into the opposite panel cwd."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "thing.txt").write_text("hello")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    app = TyuiApp(launch_mode="fm", initial_path=str(src_dir))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Override right panel's cwd to dst_dir.
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        right = desktop.query_one("#panel-right", Window).content
        assert isinstance(right, FilePanel)
        right.cwd = dst_dir
        right.refresh_listing()
        # Position cursor on thing.txt in left panel.
        left = desktop.query_one("#panel-left", Window).content
        assert isinstance(left, FilePanel)
        idx = next(i for i, e in enumerate(left.entries) if e.name == "thing.txt")
        left.cursor = idx
        # Press F5 -> copy dialog -> submit (input prefilled with dst path).
        await pilot.press("f5")
        await pilot.pause()
        from tyui.fm.dialogs import CopyMoveDialog
        dialog = app.query_one(CopyMoveDialog)
        dialog.action_submit()
        await pilot.pause()
        # File present in destination, still present in source.
        assert (dst_dir / "thing.txt").read_text() == "hello"
        assert (src_dir / "thing.txt").read_text() == "hello"


@pytest.mark.asyncio
async def test_app_f5_with_no_targets_is_noop(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    app = TyuiApp(launch_mode="fm", initial_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Cursor on ".." in both panels.
        await pilot.press("f5")
        await pilot.pause()
        from tyui.fm.dialogs import ConfirmDialog, CopyMoveDialog
        assert not list(app.query(ConfirmDialog))
        assert not list(app.query(CopyMoveDialog))


@pytest.mark.asyncio
async def test_app_f6_moves_to_opposite_panel_cwd(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "thing.txt").write_text("hello")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    app = TyuiApp(launch_mode="fm", initial_path=str(src_dir))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        right = desktop.query_one("#panel-right", Window).content
        right.cwd = dst_dir
        right.refresh_listing()
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "thing.txt")
        left.cursor = idx
        await pilot.press("f6")
        await pilot.pause()
        from tyui.fm.dialogs import CopyMoveDialog
        dialog = app.query_one(CopyMoveDialog)
        dialog.action_submit()
        await pilot.pause()
        # Moved: present in dst, gone from src.
        assert (dst_dir / "thing.txt").read_text() == "hello"
        assert not (src_dir / "thing.txt").exists()


@pytest.mark.asyncio
async def test_app_f8_keeps_focus_on_originating_panel(tmp_path):
    """F8 fired from panel-right should leave focus on panel-right after
    the modal closes — not always on panel-left."""
    target = tmp_path / "doomed.txt"
    target.write_text("")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Switch focus to the right panel.
        await pilot.press("alt+r")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-right"
        # Position cursor on doomed.txt in the right panel.
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        right = desktop.query_one("#panel-right", Window).content
        idx = next(i for i, e in enumerate(right.entries) if e.name == "doomed.txt")
        right.cursor = idx
        # F8 -> confirm -> Yes
        await pilot.press("f8")
        await pilot.pause()
        from tyui.fm.dialogs import ConfirmDialog
        dialog = app.query_one(ConfirmDialog)
        dialog.action_confirm()
        await pilot.pause()
        # File deleted, focus still on panel-right.
        assert not target.exists()
        assert _focused_panel_id(app) == "panel-right"


@pytest.mark.asyncio
async def test_app_tab_is_gated_while_modal_active(tmp_path):
    """While a confirm/progress modal is up, Tab/Alt+L/Alt+R must NOT
    switch panels — focus must stay on the dialog so Esc/clicks land."""
    target = tmp_path / "x.txt"
    target.write_text("")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        win = desktop.query_one("#panel-left", Window)
        panel = win.content
        idx = next(i for i, e in enumerate(panel.entries) if e.name == "x.txt")
        panel.cursor = idx
        await pilot.press("f8")
        await pilot.pause()
        from tyui.fm.dialogs import ConfirmDialog
        confirm = app.query_one(ConfirmDialog)
        # Sanity: dialog is focused.
        assert app.focused is confirm
        # Tab must not change focus.
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is confirm
        # Alt+L / Alt+R also gated.
        await pilot.press("alt+r")
        await pilot.pause()
        assert app.focused is confirm
        # Cleanup: cancel the dialog so the test doesn't leak the modal.
        confirm.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_inactive_panel_cursor_does_not_invert(tmp_path):
    """Cursor row in the inactive panel renders with bold (not reverse),
    so the user can tell which panel is active."""
    (tmp_path / "x.txt").write_text("")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        right = desktop.query_one("#panel-right", Window).content
        # panel-left has focus on mount.
        assert left.has_focus
        assert not right.has_focus

        # Cursor row of left panel: reverse=True.
        left_cursor_row = 1 + (left.cursor - left.row_offset)
        left_strip = left.render_line(left_cursor_row)
        assert any(
            getattr(seg.style, "reverse", False)
            for seg in left_strip
            if seg.style is not None
        )
        # Cursor row of right panel: NO reverse, but bold.
        right_cursor_row = 1 + (right.cursor - right.row_offset)
        right_strip = right.render_line(right_cursor_row)
        assert not any(
            getattr(seg.style, "reverse", False)
            for seg in right_strip
            if seg.style is not None
        )
        assert any(
            getattr(seg.style, "bold", False)
            for seg in right_strip
            if seg.style is not None
        )


@pytest.mark.asyncio
async def test_app_f4_opens_editor_window_on_file(tmp_path):
    """F4 with cursor on a file opens an editor window with that file."""
    f = tmp_path / "x.txt"
    f.write_text("hello\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_windows = [w for w in desktop.windows if isinstance(w.content, EditorContent)]
        assert len(editor_windows) == 1
        assert editor_windows[0].content._file_path == str(f)


@pytest.mark.asyncio
async def test_app_f4_on_directory_is_noop(tmp_path):
    """F4 on a directory entry must not open an editor."""
    sub = tmp_path / "sub"
    sub.mkdir()
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "sub")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        assert not [w for w in desktop.windows if isinstance(w.content, EditorContent)]


@pytest.mark.asyncio
async def test_app_enter_on_file_opens_editor_window(tmp_path):
    """Enter on a file (FilePanel.ItemActivated) routes to editor."""
    f = tmp_path / "y.txt"
    f.write_text("hi\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "y.txt")
        left.cursor = idx
        await pilot.press("enter")
        await pilot.pause()
        editor_windows = [w for w in desktop.windows if isinstance(w.content, EditorContent)]
        assert len(editor_windows) == 1


@pytest.mark.asyncio
async def test_app_closing_editor_returns_focus_to_panel(tmp_path):
    """Closing the editor window restores focus to the panel that opened it."""
    f = tmp_path / "x.txt"
    f.write_text("hi")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        # Close the editor window via the framework's Closed message.
        editor_win.post_message(Window.Closed(editor_win))
        await pilot.pause()
        # Editor gone, focus back on the panel.
        assert not [w for w in desktop.windows if isinstance(w.content, EditorContent)]
        assert _focused_panel_id(app) == "panel-left"


@pytest.mark.asyncio
async def test_app_editor_loads_file_contents(tmp_path):
    """When F4 opens a file, the editor buffer should contain the file text."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        # EditorContent owns a buffer with .lines.
        lines = editor_win.content._buffer.lines
        assert "alpha" in lines
        assert "beta" in lines
        assert "gamma" in lines


@pytest.mark.asyncio
async def test_app_esc_closes_editor_window(tmp_path):
    """Esc inside the editor closes it and returns focus to the panel."""
    f = tmp_path / "x.txt"
    f.write_text("hi")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        assert any(isinstance(w.content, EditorContent) for w in desktop.windows)
        await pilot.press("escape")
        await pilot.pause()
        assert not any(isinstance(w.content, EditorContent) for w in desktop.windows)
        assert _focused_panel_id(app) == "panel-left"


@pytest.mark.asyncio
async def test_app_f3_opens_viewer_with_file_contents(tmp_path):
    """F3 opens a ViewerContent window with the file's text loaded."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\nbeta\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.fm.viewer import ViewerContent
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f3")
        await pilot.pause()
        viewers = [w for w in desktop.windows if isinstance(w.content, ViewerContent)]
        assert len(viewers) == 1
        assert "alpha" in viewers[0].content._buffer.lines


@pytest.mark.asyncio
async def test_app_f3_on_directory_is_noop(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.fm.viewer import ViewerContent
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "sub")
        left.cursor = idx
        await pilot.press("f3")
        await pilot.pause()
        assert not [w for w in desktop.windows if isinstance(w.content, ViewerContent)]


@pytest.mark.asyncio
async def test_app_viewer_does_not_modify_buffer_on_typing(tmp_path):
    """Viewer mode should ignore character keys — the file stays clean."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.fm.viewer import ViewerContent
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f3")
        await pilot.pause()
        viewer = next(w for w in desktop.windows if isinstance(w.content, ViewerContent)).content
        viewer._editor.focus()
        # Type a character — should be swallowed by the read-only on_key.
        await pilot.press("z")
        await pilot.pause()
        # Buffer is unchanged.
        assert viewer._buffer.lines == ["alpha", ""]


@pytest.mark.asyncio
async def test_app_editor_widget_has_focus_after_f4(tmp_path):
    """F4 must focus the inner EditorWidget so arrow keys and typing
    work immediately, not only after a mouse click."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\nbeta\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.fm.file_panel import FilePanel
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        from tyui.windowing.editor.widget import EditorWidget
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        assert editor_win.content._editor.has_focus


# ---- Editor menu visibility & editor z-order after menu close -------------

@pytest.mark.asyncio
async def test_editor_menu_hidden_without_editor(tmp_path):
    """Editor menu is focus-scoped: must be absent when no editor is focused."""
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        labels = [m.label for m in app.menu_bar.menus]
        assert "Editor" not in labels


@pytest.mark.asyncio
async def test_editor_menu_visible_when_editor_focused(tmp_path):
    """Opening an editor (F4) must add Editor to the menu bar."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        labels = [m.label for m in app.menu_bar.menus]
        assert "Editor" in labels


@pytest.mark.asyncio
async def test_editor_menu_hidden_after_editor_closed(tmp_path):
    """Closing the editor must remove Editor menu again."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(
            w for w in desktop.windows if isinstance(w.content, EditorContent)
        )
        editor_win.post_message(Window.Closed(editor_win))
        await pilot.pause()
        labels = [m.label for m in app.menu_bar.menus]
        assert "Editor" not in labels


@pytest.mark.asyncio
async def test_editor_window_stays_on_top_after_editor_menu_command(tmp_path):
    """After dispatching an Editor-menu command, the editor window must
    keep its top z-order (last entry in desktop.windows) and remain
    focused_window."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\nbeta\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from tyui.windowing import Desktop, Window
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        await pilot.pause()
        editor_win = next(
            w for w in desktop.windows if isinstance(w.content, EditorContent)
        )
        # Sanity precondition.
        assert desktop.focused_window is editor_win
        # Simulate the menu round-trip: action_menu sets _pre_menu_focus to
        # whatever currently has focus, then activates the menu bar; closing
        # it (active_index -> None) must restore both focus and z-order.
        app._pre_menu_focus = editor_win.content._editor
        app.menu_bar.activate(0)
        await pilot.pause()
        # Pretend the user dispatched a Tools command and the dropdown
        # closed: deactivate the menu bar.
        app.menu_bar.deactivate()
        await pilot.pause()
        await pilot.pause()
        assert desktop.focused_window is editor_win
        assert desktop.windows[-1] is editor_win


# ---- z-order on mouse-click menu open + Edit-menu filtering ---------------


@pytest.mark.asyncio
async def test_editor_z_order_preserved_after_mouse_menu_open(tmp_path):
    """Opening the menu via mouse leaves _pre_menu_focus as None — but the
    OpenRequested handler must capture the pre-menu window so dismiss can
    still raise the editor back over the file panels."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        assert desktop.focused_window is editor_win
        # Mouse-click path: no action_menu(), so _pre_menu_focus stays None.
        # The MenuBar fires OpenRequested directly.
        app._pre_menu_focus = None
        app._pre_menu_window = None
        app.menu_bar.activate(0)
        app.menu_bar.open_active()
        await pilot.pause(); await pilot.pause()
        # Simulate dropdown dismiss / item chosen.
        app.menu_bar.deactivate()
        await pilot.pause(); await pilot.pause()
        assert desktop.focused_window is editor_win
        assert desktop.windows[-1] is editor_win


@pytest.mark.asyncio
async def test_panel_items_filtered_out_when_editor_focused(tmp_path):
    """File menu lists panel.view / panel.edit / save. When the editor is
    focused those panel.* commands have nowhere to resolve, so a Dropdown
    built from the File menu must drop them (and any dangling separator)
    rather than render the raw command_id as a label."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        from tyui.windowing.menu_bar import Dropdown, MenuItem, MenuSeparator
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        assert desktop.focused_window is editor_win

        file_menu = next(m for m in app.menu_bar.menus if m.label == "File")
        dd = Dropdown(file_menu.items, dispatcher=app.dispatcher)
        kept_ids = [
            it.command_id for it in dd.items
            if isinstance(it, MenuItem)
        ]
        assert "panel.view" not in kept_ids
        assert "panel.edit" not in kept_ids
        assert "save" in kept_ids
        # No dangling leading separator left over after filtering.
        assert not isinstance(dd.items[0], MenuSeparator)


@pytest.mark.asyncio
async def test_tab_in_editor_stays_in_editor(tmp_path):
    """The app-level priority Tab binding cycles file panels, but in the
    editor it must instead forward to EditorWidget.action_insert_tab so
    focus stays on the editor."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        editor_widget = editor_win.content._editor
        assert editor_widget.has_focus
        await pilot.press("tab")
        await pilot.pause()
        # Focus must still be inside the editor; left panel must NOT have
        # been raised over the editor.
        assert desktop.focused_window is editor_win
        assert editor_widget.has_focus


@pytest.mark.asyncio
async def test_ctrl_bracket_folds_in_editor(tmp_path):
    """Ctrl+[ collapses all fold regions, Ctrl+] toggles the fold under the
    cursor. Both go through EditorWidget BINDINGS that map to action_*
    methods — the action_ aliases must exist or the keys silently no-op."""
    src = "def f():\n    a = 1\n    b = 2\n    return a + b\n"
    f = tmp_path / "x.py"
    f.write_text(src)
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.py")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        ed = editor_win.content._editor
        assert ed.has_focus
        # Force a fold scan so _fold_regions is populated regardless of the
        # render-driven schedule.
        ed._rescan_folds()
        assert ed._fold_regions, "test fixture must produce at least one fold region"
        await pilot.press("ctrl+left_square_bracket")
        await pilot.pause()
        assert all(r.collapsed for r in ed._fold_regions), "Ctrl+[ must collapse all"


@pytest.mark.asyncio
async def test_ctrl_r_toggles_macro_recording_in_editor(tmp_path):
    """Ctrl+R is the focus-scope hotkey for `record_macro` — declared in
    EditorContent.get_commands(). The recorder must be created independent
    of macro_storage_path so the command is actually registered."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        content = editor_win.content
        assert content._macro_recorder is not None
        assert not content._macro_recorder.is_recording
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert content._macro_recorder.is_recording
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert not content._macro_recorder.is_recording


@pytest.mark.asyncio
async def test_record_macro_menu_item_dispatches(tmp_path):
    """Selecting Tools → Record Macro through the dispatcher must fire the
    same handler as the Ctrl+R hotkey. Regression for "menu item appears
    but does nothing" when EditorContent's get_commands wasn't reachable."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        assert desktop.focused_window is editor_win
        # Resolve the command via the same path the menu uses (dispatcher
        # against the focused window) and dispatch.
        assert app.dispatcher.resolve("record_macro") is not None
        assert app.dispatcher.dispatch("record_macro") is True
        assert editor_win.content._macro_recorder.is_recording


@pytest.mark.asyncio
async def test_macro_assignment_dialog_opens_after_recording(tmp_path):
    """After recording at least one keypress, stopping the macro must push
    the MacroAssignDialog so the user can bind a replay key."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        from tyui.windowing.editor.macro_dialog import MacroAssignDialog
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        assert editor_win.content._macro_recorder is not None
        # Start recording, type something recordable, then stop.
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert editor_win.content._macro_recorder.is_recording
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("ctrl+r")
        await pilot.pause(); await pilot.pause()
        # Recording stopped, dialog should now be on the screen stack.
        assert any(isinstance(s, MacroAssignDialog) for s in app.screen_stack)


@pytest.mark.asyncio
async def test_ctrl_z_undo_and_ctrl_y_redo_in_editor(tmp_path):
    """Editor exposes Ctrl+Z (undo) and Ctrl+Y (redo) over the buffer's
    own undo/redo stacks."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        ed = editor_win.content._editor
        assert ed.has_focus
        original = "\n".join(ed.buffer.lines)
        await pilot.press("X")
        await pilot.pause()
        assert "\n".join(ed.buffer.lines) != original
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert "\n".join(ed.buffer.lines) == original
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert "\n".join(ed.buffer.lines) != original


@pytest.mark.asyncio
async def test_ctrl_bracket_on_empty_line_folds_all(tmp_path):
    """Ctrl+] is a smart action: on an empty line it toggles ALL folds; on a
    non-empty line it only toggles the fold at the cursor. The user's
    expected MC/Far-style shortcut."""
    src = (
        "def a():\n    x = 1\n    y = 2\n\n"
        "def b():\n    z = 3\n    w = 4\n"
    )
    f = tmp_path / "x.py"
    f.write_text(src)
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.py")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = next(w for w in desktop.windows if isinstance(w.content, EditorContent))
        ed = editor_win.content._editor
        ed._rescan_folds()
        assert len(ed._fold_regions) >= 2
        # Move cursor to the empty separator line (row 3 in src above).
        ed.buffer.cursor_row = 3
        ed.buffer.cursor_col = 0
        assert ed.buffer.lines[ed.buffer.cursor_row].strip() == ""
        await pilot.press("ctrl+right_square_bracket")
        await pilot.pause()
        assert all(r.collapsed for r in ed._fold_regions), \
            "empty-line Ctrl+] must collapse every region"
        # Pressing again on the same empty line must unfold them all.
        await pilot.press("ctrl+right_square_bracket")
        await pilot.pause()
        assert all(not r.collapsed for r in ed._fold_regions)


@pytest.mark.asyncio
async def test_shift_tab_cycles_through_desktop_windows(tmp_path):
    """Shift+Tab cycles forward through every visible desktop window — not
    just the two file panels. Regression for "no global window navigation"."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        # Now we have three visible windows: panel-left, panel-right, editor.
        visible = [w for w in desktop.windows if w.display]
        assert len(visible) >= 3
        seen: set[int] = set()
        for _ in range(len(visible) * 2):
            seen.add(id(desktop.focused_window))
            await pilot.press("shift+tab")
            await pilot.pause()
        # Every visible window must have been focused at some point.
        assert seen >= {id(w) for w in visible}


@pytest.mark.asyncio
async def test_windows_menu_lists_open_windows(tmp_path):
    """Opening the Windows menu must populate one item per visible desktop
    window with a handler that focuses it."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.menu_bar import MenuBar, MenuItem, MenuSeparator
        desktop = app.query_one(Desktop)
        # Trigger refresh as the menu bar would.
        idx = next(i for i, m in enumerate(app.menu_bar.menus) if m.label == "Windows")
        app.menu_bar.post_message(MenuBar.OpenRequested(app.menu_bar, idx))
        await pilot.pause(); await pilot.pause()
        win_menu = next(m for m in app.menu_bar.menus if m.label == "Windows")
        # The Windows menu lists visible windows first, then a separator,
        # then the view-arrangement commands (tile / cascade) appended in
        # _refresh_windows_menu.
        sep_idx = next(
            i for i, it in enumerate(win_menu.items)
            if isinstance(it, MenuSeparator)
        )
        window_items = win_menu.items[:sep_idx]
        labels = [
            (it.label or "")
            for it in window_items
            if isinstance(it, MenuItem)
        ]
        visible = [w for w in desktop.windows if w.display]
        assert len(window_items) == len(visible)
        assert all(lbl for lbl in labels)
        # Invoking the handler must focus the corresponding window.
        candidates = [
            it for it in window_items
            if isinstance(it, MenuItem) and it.handler is not None
        ]
        candidates[-1].handler()
        await pilot.pause()
        assert desktop.focused_window is visible[-1]


@pytest.mark.asyncio
async def test_windows_menu_pick_survives_menu_close(tmp_path):
    """Going through the full menu round-trip (open menu → choose Windows
    item → menu deactivates) must leave focus on the chosen window. The
    post-menu restore path used to bounce focus back to whichever window
    was active when the menu opened, defeating the user's selection."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from tyui.windowing.menu_bar import MenuBar, MenuItem
        desktop = app.query_one(Desktop)
        # Open editor so we have at least 3 windows.
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "x.txt")
        left.cursor = idx
        await pilot.press("f4")
        await pilot.pause()
        editor_win = desktop.focused_window
        # Simulate the full mouse path: activate menu bar (captures
        # _pre_menu_window), refresh Windows menu, invoke item handler,
        # then deactivate.
        win_idx = next(
            i for i, m in enumerate(app.menu_bar.menus) if m.label == "Windows"
        )
        app.menu_bar.activate(win_idx)
        app.menu_bar.post_message(MenuBar.OpenRequested(app.menu_bar, win_idx))
        await pilot.pause(); await pilot.pause()
        win_menu = app.menu_bar.menus[win_idx]
        # Items are listed in desktop.windows order; the entry whose label
        # has no "• " prefix is one of the non-focused windows. Pick the
        # first such entry, then locate the matching window by index.
        non_focused = [
            (i, it) for i, it in enumerate(win_menu.items)
            if isinstance(it, MenuItem)
            and it.handler is not None
            and not (it.label or "").startswith("• ")
        ]
        assert non_focused, "expected at least one non-focused window in menu"
        target_idx, target_item = non_focused[0]
        visible = [w for w in desktop.windows if w.display]
        target = visible[target_idx]
        assert target is not editor_win
        target_item.handler()
        app.menu_bar.deactivate()
        await pilot.pause(); await pilot.pause()
        assert desktop.focused_window is target
