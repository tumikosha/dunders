import pytest

from dunders.app import DundersApp
from dunders.fm.file_panel import FilePanel
from dunders.windowing import Desktop, MenuBar, StatusBar, Window
from dunders.fm.commandline import CommandLine


@pytest.mark.asyncio
async def test_app_mounts_chrome_widgets():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.query(MenuBar)) == 1
        assert len(app.query(Desktop)) == 1
        assert len(app.query(StatusBar)) == 1
        assert len(app.query(CommandLine)) == 1


@pytest.mark.asyncio
async def test_app_mounts_two_panel_windows_in_fm_mode():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
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
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        sb = app.query_one(StatusBar)
        labels = {item.label for item in sb.items}
        assert "UsrMenu" in labels  # F2 opens the User Menu
        assert "Edit" in labels
        assert "Quit" in labels


@pytest.mark.asyncio
async def test_app_editor_mode_hides_panels_initially():
    """dunders <file> should mount panels but hide them; editor placeholder is visible."""
    app = DundersApp(launch_mode="editor", initial_path="/tmp/foo.txt")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        # Panels exist but are not in desktop.windows (the visible list).
        all_panel_ids = {"panel-left", "panel-right"}
        visible_ids = {w.id for w in desktop.windows}
        assert all_panel_ids.isdisjoint(visible_ids)


async def test_editor_menu_exposes_syntax_commands():
    """The Editor menu must surface the syntax-highlight toggle and language picker."""
    app = DundersApp(launch_mode="editor", initial_path="/tmp/foo.txt")
    async with app.run_test() as pilot:
        await pilot.pause()
        editor_menu = next(m for m in app._all_menus if m.label == "Editor")
        cmd_ids = {getattr(item, "command_id", None) for item in editor_menu.items}
        assert "toggle_syntax" in cmd_ids
        assert "set_language" in cmd_ids


async def test_view_menu_exposes_panels_fullscreen_and_console_toggle():
    """The View menu must surface Panels Fullscreen (Ctrl+P) and Toggle console (Ctrl+O)."""
    app = DundersApp(launch_mode="fm")
    async with app.run_test() as pilot:
        await pilot.pause()
        view_menu = next(m for m in app._all_menus if m.label == "View")
        cmd_ids = {getattr(item, "command_id", None) for item in view_menu.items}
        assert "panels.fullscreen" in cmd_ids
        assert "console.toggle" in cmd_ids


@pytest.mark.asyncio
async def test_app_cli_mode_hides_panels_and_mounts_agent_stub():
    app = DundersApp(launch_mode="cli")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        ids = {w.id for w in desktop.windows}
        assert "agent" in ids
        assert "panel-left" not in ids
        assert "panel-right" not in ids


def test_main_arg_parsing(monkeypatch, tmp_path):
    """dunders.main.main forwards the right launch_mode/initial_path to DundersApp."""
    import dunders.main as main_mod

    captured: dict = {}

    class _FakeApp:
        def __init__(self, *, launch_mode, initial_path) -> None:
            captured["launch_mode"] = launch_mode
            captured["initial_path"] = initial_path

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr(main_mod, "DundersApp", _FakeApp)

    # Case 1: no args -> fm mode, no path
    monkeypatch.setattr("sys.argv", ["dunders"])
    main_mod.main()
    assert captured == {"launch_mode": "fm", "initial_path": None, "ran": True}

    # Case 2: directory path -> fm mode with path
    captured.clear()
    monkeypatch.setattr("sys.argv", ["dunders", str(tmp_path)])
    main_mod.main()
    assert captured["launch_mode"] == "fm"
    assert captured["initial_path"] == str(tmp_path)

    # Case 3: file path -> editor mode
    captured.clear()
    file_path = tmp_path / "foo.txt"
    file_path.write_text("hi")
    monkeypatch.setattr("sys.argv", ["dunders", str(file_path)])
    main_mod.main()
    assert captured["launch_mode"] == "editor"
    assert captured["initial_path"] == str(file_path)

    # Case 4: --cli -> cli mode
    captured.clear()
    monkeypatch.setattr("sys.argv", ["dunders", "--cli"])
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
    # On startup with no file argument, panel-left holds Textual widget
    # focus so arrow keys move the selection out of the box.
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()  # allow call_after_refresh to fire
        assert _focused_panel_id(app) == "panel-left"
        # The active panel window is also panel-left.
        assert app.desktop is not None
        assert app.desktop.focused_window is not None
        assert app.desktop.focused_window.id == "panel-left"


@pytest.mark.asyncio
async def test_app_tab_alternates_panels(tmp_path):
    # Startup: panel-left has Textual focus.
    # Tab #1: panel-left → panel-right  (normal panel swap)
    # Tab #2: panel-right → panel-left  (normal panel swap)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-left"
        await pilot.press("tab")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-right"
        await pilot.press("tab")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-left"


@pytest.mark.asyncio
async def test_app_alt_l_and_alt_r_force_focus(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop
        desktop = app.query_one(Desktop)
        for win in desktop.windows:
            if win.id in ("panel-left", "panel-right"):
                panel = win.content
                assert isinstance(panel, FilePanel)
                names = [e.name for e in panel.entries]
                assert "alpha.txt" in names


@pytest.mark.asyncio
async def test_app_f9_then_esc_returns_focus_to_panel(tmp_path):
    """F9 enters the menu, Esc returns focus to whatever was focused before."""
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Sanity: on startup panel-left has Textual focus.
        assert _focused_panel_id(app) == "panel-left"
        assert app.desktop is not None
        assert app.desktop.focused_window is not None
        assert app.desktop.focused_window.id == "panel-left"
        await pilot.press("f9")
        await pilot.pause()
        # MenuBar is now focused — no panel id under app.focused.
        assert _focused_panel_id(app) is None
        await pilot.press("escape")
        await pilot.pause()
        # Focus is back on whatever had focus before F9 (the cmdline input).
        assert app.focused is not None
        # The active panel window is still panel-left.
        assert app.desktop.focused_window.id == "panel-left"


@pytest.mark.asyncio
async def test_app_panels_fill_full_desktop_width():
    """Two panels combined cover the full desktop width; each ~half wide.
    The console is no longer mounted at startup, so the panels fill the full
    usable desktop height (excluding the IconTray's bottom row)."""
    from dunders.windowing import Desktop, Window

    app = DundersApp(launch_mode="fm", initial_path="/tmp")
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
        # With no console at startup, panels span the full usable height.
        usable = desktop.size.height - 1
        assert left.size.height == usable
        assert right.size.height == usable


@pytest.mark.asyncio
async def test_app_f7_creates_directory_in_active_panel(tmp_path):
    """F7 -> input "newdir" -> Enter -> directory created under active panel cwd."""
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("f7")
        await pilot.pause()
        from dunders.fm.dialogs import NewFileDialog
        dialog = app.query_one(NewFileDialog)
        dialog._input.value = "newdir"
        dialog.action_submit()
        await pilot.pause()
        assert (tmp_path / "newdir").is_dir()


@pytest.mark.asyncio
async def test_app_f7_cancel_does_not_create_anything(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.press("f7")
        await pilot.pause()
        from dunders.fm.dialogs import NewFileDialog
        dialog = app.query_one(NewFileDialog)
        dialog.action_cancel()
        await pilot.pause()
        # No new entries should have appeared.
        assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_app_f8_deletes_cursor_target_after_confirm(tmp_path):
    """F8 with cursor on a file -> ConfirmDialog -> Yes -> file removed."""
    target = tmp_path / "doomed.txt"
    target.write_text("")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Move cursor onto doomed.txt in the left panel.
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        win = desktop.query_one("#panel-left", Window)
        panel = win.content
        assert isinstance(panel, FilePanel)
        idx = next(i for i, e in enumerate(panel.entries) if e.name == "doomed.txt")
        panel.cursor = idx
        # Press F8 — confirm dialog opens.
        await pilot.press("f8")
        await pilot.pause()
        from dunders.fm.dialogs import ConfirmDialog
        dialog = app.query_one(ConfirmDialog)
        dialog.action_confirm()
        await pilot.pause()
        # File deleted.
        assert not target.exists()


@pytest.mark.asyncio
async def test_app_f8_cancel_keeps_file(tmp_path):
    target = tmp_path / "safe.txt"
    target.write_text("")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        win = desktop.query_one("#panel-left", Window)
        panel = win.content
        idx = next(i for i, e in enumerate(panel.entries) if e.name == "safe.txt")
        panel.cursor = idx
        await pilot.press("f8")
        await pilot.pause()
        from dunders.fm.dialogs import ConfirmDialog
        dialog = app.query_one(ConfirmDialog)
        dialog.action_cancel()
        await pilot.pause()
        assert target.exists()


@pytest.mark.asyncio
async def test_app_f8_with_no_targets_is_noop(tmp_path):
    """Cursor on '..' with empty selection: F8 does nothing."""
    sub = tmp_path / "sub"
    sub.mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Cursor is on ".." by default.
        await pilot.press("f8")
        await pilot.pause()
        # No ConfirmDialog should have been shown.
        from dunders.fm.dialogs import ConfirmDialog
        assert not list(app.query(ConfirmDialog))


@pytest.mark.asyncio
async def test_app_f5_copies_to_opposite_panel_cwd(tmp_path):
    """F5: copy active panel's cursor entry into the opposite panel cwd."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "thing.txt").write_text("hello")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    app = DundersApp(launch_mode="fm", initial_path=str(src_dir))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Override right panel's cwd to dst_dir.
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
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
        from dunders.fm.dialogs import CopyMoveDialog
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
    app = DundersApp(launch_mode="fm", initial_path=str(sub))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Cursor on ".." in both panels.
        await pilot.press("f5")
        await pilot.pause()
        from dunders.fm.dialogs import ConfirmDialog, CopyMoveDialog
        assert not list(app.query(ConfirmDialog))
        assert not list(app.query(CopyMoveDialog))


@pytest.mark.asyncio
async def test_app_f6_moves_to_opposite_panel_cwd(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "thing.txt").write_text("hello")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    app = DundersApp(launch_mode="fm", initial_path=str(src_dir))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        right = desktop.query_one("#panel-right", Window).content
        right.cwd = dst_dir
        right.refresh_listing()
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "thing.txt")
        left.cursor = idx
        await pilot.press("f6")
        await pilot.pause()
        from dunders.fm.dialogs import CopyMoveDialog
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        # Switch focus to the right panel.
        await pilot.press("alt+r")
        await pilot.pause()
        assert _focused_panel_id(app) == "panel-right"
        # Position cursor on doomed.txt in the right panel.
        from dunders.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        right = desktop.query_one("#panel-right", Window).content
        idx = next(i for i, e in enumerate(right.entries) if e.name == "doomed.txt")
        right.cursor = idx
        # F8 -> confirm -> Yes
        await pilot.press("f8")
        await pilot.pause()
        from dunders.fm.dialogs import ConfirmDialog
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        win = desktop.query_one("#panel-left", Window)
        panel = win.content
        idx = next(i for i, e in enumerate(panel.entries) if e.name == "x.txt")
        panel.cursor = idx
        await pilot.press("f8")
        await pilot.pause()
        from dunders.fm.dialogs import ConfirmDialog, ShadowButton
        confirm = app.query_one(ConfirmDialog)
        # Sanity: focus is on a button INSIDE the dialog (Yes by default).
        # The exact widget changed when keyboard-nav was added — what
        # matters here is that focus stays inside the modal.
        def _focus_in_dialog() -> bool:
            f = app.focused
            return f is confirm or (
                isinstance(f, ShadowButton) and confirm in f.ancestors
            )
        assert _focus_in_dialog()
        # Tab cycles between Yes/No INSIDE the dialog — must stay inside.
        await pilot.press("tab")
        await pilot.pause()
        assert _focus_in_dialog()
        # Alt+L / Alt+R must not switch to panels.
        await pilot.press("alt+r")
        await pilot.pause()
        assert _focus_in_dialog()
        # Cleanup: cancel the dialog so the test doesn't leak the modal.
        confirm.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_inactive_panel_cursor_does_not_invert(tmp_path):
    """Cursor row in the inactive panel renders with bold (not reverse),
    so the user can tell which panel is active."""
    (tmp_path / "x.txt").write_text("")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        right = desktop.query_one("#panel-right", Window).content
        # panel-left is the active panel on mount (desktop.focused_window).
        # The CommandLine input holds Textual widget focus, so has_focus
        # will be False for both panels — use _is_active_panel instead.
        assert left._is_active_panel
        assert not right._is_active_panel

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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "y.txt")
        left.cursor = idx
        # Ensure the panel has Textual widget focus so Enter routes to it
        # (not to the CommandLine input which holds focus at startup).
        app._focus_panel("panel-left")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        editor_windows = [w for w in desktop.windows if isinstance(w.content, EditorContent)]
        assert len(editor_windows) == 1


@pytest.mark.asyncio
async def test_app_closing_editor_returns_focus_to_panel(tmp_path):
    """Closing the editor window restores focus to the panel that opened it."""
    f = tmp_path / "x.txt"
    f.write_text("hi")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
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
async def test_app_refresh_panels_keeps_cursor_on_focused_entry(tmp_path):
    """A plain re-scan (e.g. closing a viewer) must keep the cursor on the
    same entry, not jump to the top. Slow providers (SFTP) re-scan async and
    reset the cursor to 0, so _refresh_panels must forward the focused locator
    as focus_loc to land back on it."""
    for n in ("a.txt", "b.txt", "c.txt", "d.txt"):
        (tmp_path / n).write_text(n)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.windowing import Desktop, Window
        desktop = app.query_one(Desktop)
        left = desktop.query_one("#panel-left", Window).content
        idx = next(i for i, e in enumerate(left.entries) if e.name == "c.txt")
        left.cursor = idx
        target_loc = left.entries[idx].loc

        captured = {}
        original = left.refresh_listing

        def _spy(*args, focus_loc=None, **kwargs):
            captured["focus_loc"] = focus_loc
            return original(*args, focus_loc=focus_loc, **kwargs)

        left.refresh_listing = _spy
        app._refresh_panels()
        await pilot.pause()

        assert captured["focus_loc"] == target_loc
        # And the sync path actually keeps the cursor on c.txt.
        assert left.entries[left.cursor].name == "c.txt"


@pytest.mark.asyncio
async def test_app_editor_loads_file_contents(tmp_path):
    """When F4 opens a file, the editor buffer should contain the file text."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.fm.viewer import ViewerContent
        from dunders.windowing import Desktop, Window
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.fm.viewer import ViewerContent
        from dunders.windowing import Desktop, Window
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.fm.viewer import ViewerContent
        from dunders.windowing import Desktop, Window
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.fm.file_panel import FilePanel
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
        from dunders.windowing.editor.widget import EditorWidget
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.windowing import Desktop, Window
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.windowing import Desktop, Window
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
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
async def test_f9_opens_first_dropdown_immediately(tmp_path):
    """F9 should reveal the first menu's dropdown right away, not merely
    highlight the leftmost label — as if the user pressed Enter on it."""
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.press("f9")
        await pilot.pause(); await pilot.pause()
        assert app.menu_bar.active_index == 0
        assert app._active_dropdown is not None


@pytest.mark.asyncio
async def test_f10_shows_exit_confirmation(tmp_path):
    """F10 must not quit straight away — it opens a Yes/No Exit confirmation."""
    from dunders.app import QuitRequest
    from dunders.fm.dialogs import ConfirmDialog

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.press("f10")
        await pilot.pause(); await pilot.pause()
        dlg = app.query_one(ConfirmDialog)
        assert isinstance(dlg.context, QuitRequest)
        assert app.is_running  # not exited


@pytest.mark.asyncio
async def test_exit_confirmation_no_keeps_running(tmp_path):
    from dunders.fm.dialogs import ConfirmDialog

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        await pilot.press("f10")
        await pilot.pause()
        await pilot.press("n")  # No
        await pilot.pause(); await pilot.pause()
        assert not app.query(ConfirmDialog)  # dialog dismissed
        assert app.is_running


@pytest.mark.asyncio
async def test_exit_confirmation_yes_exits(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    called = {}
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        app.exit = lambda *a, **k: called.setdefault("exit", True)
        await pilot.press("f10")
        await pilot.pause()
        await pilot.press("y")  # Yes
        await pilot.pause(); await pilot.pause()
        assert called.get("exit")


@pytest.mark.asyncio
async def test_panel_items_filtered_out_when_editor_focused(tmp_path):
    """File menu lists panel.view / panel.edit / save. When the editor is
    focused those panel.* commands have nowhere to resolve, so a Dropdown
    built from the File menu must drop them (and any dangling separator)
    rather than render the raw command_id as a label."""
    f = tmp_path / "x.txt"
    f.write_text("alpha\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
        from dunders.windowing.menu_bar import Dropdown, MenuItem, MenuSeparator
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
        from dunders.windowing.editor.macro_dialog import MacroAssignDialog
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.editor import EditorContent
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
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
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.menu_bar import MenuBar, MenuItem, MenuSeparator
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
async def test_view_mode_command_changes_active_panel_mode(tmp_path):
    from dunders.fm.panel_view import PanelViewMode
    from dunders.fm.file_panel import FilePanel
    from dunders.windowing import Window
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        left = app.desktop.query_one("#panel-left", Window).content
        assert isinstance(left, FilePanel)
        assert left.view_mode == PanelViewMode.FULL
        app.dispatcher.dispatch("panel.left.view_brief")
        await pilot.pause()
        assert left.view_mode == PanelViewMode.BRIEF
        app.dispatcher.dispatch("panel.left.view_detailed")
        await pilot.pause()
        assert left.view_mode == PanelViewMode.DETAILED
        app.dispatcher.dispatch("panel.left.view_short")
        await pilot.pause()
        assert left.view_mode == PanelViewMode.SHORT


@pytest.mark.asyncio
async def test_windows_menu_pick_survives_menu_close(tmp_path):
    """Going through the full menu round-trip (open menu → choose Windows
    item → menu deactivates) must leave focus on the chosen window. The
    post-menu restore path used to bounce focus back to whichever window
    was active when the menu opened, defeating the user's selection."""
    f = tmp_path / "x.txt"
    f.write_text("a\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause(); await pilot.pause()
        from dunders.windowing.menu_bar import MenuBar, MenuItem
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


@pytest.mark.asyncio
async def test_options_menu_exposes_theme_switching():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        # An "Options" menu lists the cycle command plus one item per theme.
        labels = [m.label for m in app._all_menus]
        assert "Options" in labels
        assert app.command_registry.get("theme.cycle") is not None
        assert app.command_registry.get("theme.set.paper_light") is not None

        desktop = app.query_one(Desktop)
        start = desktop.palette.theme.name
        app.action_cycle_theme()
        await pilot.pause()
        assert desktop.palette.theme.name != start

        app._apply_theme("paper_light")
        await pilot.pause()
        assert desktop.palette.theme.name == "paper_light"


@pytest.mark.asyncio
async def test_theme_switch_repaints_file_panel_background():
    """Switching theme must repaint the panel body, not only window borders."""
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        panel = next(
            w.content for w in desktop.windows if isinstance(w.content, FilePanel)
        )

        def first_row_bg():
            for seg in panel.render_line(1):
                if seg.style is not None and seg.style.bgcolor is not None:
                    return seg.style.bgcolor.name
            return None

        app._apply_theme("modern_dark")
        await pilot.pause()
        dark_bg = first_row_bg()

        app._apply_theme("paper_light")
        await pilot.pause()
        light_bg = first_row_bg()

        assert dark_bg is not None and light_bg is not None
        assert dark_bg != light_bg


@pytest.mark.asyncio
async def test_selected_theme_persists_across_restart():
    """Picking a theme writes it to user config; a fresh app re-applies it."""
    from dunders.config import user_config

    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        # Cold start with empty (isolated) config -> built-in default.
        assert app.query_one(Desktop).palette.theme.name == "modern_dark"
        app._apply_theme("dracula", persist=True)
        await pilot.pause()
    assert user_config.get_theme() == "dracula"

    restarted = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with restarted.run_test() as pilot:
        await pilot.pause()
        assert restarted.query_one(Desktop).palette.theme.name == "dracula"


@pytest.mark.asyncio
async def test_unknown_persisted_theme_falls_back_to_default():
    from dunders.config import user_config

    user_config.set_theme("no_such_theme")
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(Desktop).palette.theme.name == "modern_dark"


@pytest.mark.asyncio
async def test_edit_theme_opens_toml_for_file_backed_theme():
    """Options → Edit theme opens the current theme's .toml in an editor."""
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        app._apply_theme("dracula")
        await pilot.pause()
        before = len(desktop.windows)
        app.action_edit_theme()
        await pilot.pause()
        assert len(desktop.windows) == before + 1
        # The new editor window points at dracula.toml.
        opened = [
            w for w in desktop.windows
            if getattr(getattr(w.content, "_editor", None), "buffer", None) is not None
            and getattr(w.content._editor.buffer, "file_path", None)
            and str(w.content._editor.buffer.file_path).endswith("dracula.toml")
        ]
        assert opened, "no editor window opened for dracula.toml"


@pytest.mark.asyncio
async def test_edit_theme_on_builtin_opens_no_window():
    """modern_dark has no file: Edit theme shows a hint, opens nothing."""
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        app._apply_theme("modern_dark")
        await pilot.pause()
        before = len(desktop.windows)
        app.action_edit_theme()
        await pilot.pause()
        assert len(desktop.windows) == before


@pytest.mark.asyncio
async def test_apply_theme_invalidates_registry_cache(monkeypatch):
    """Re-applying a theme drops its cached parse so file edits show up."""
    from dunders.windowing.themes import loader

    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        calls = []
        monkeypatch.setattr(
            loader.theme_registry, "invalidate", lambda name=None: calls.append(name)
        )
        app._apply_theme("nord")
        await pilot.pause()
        assert "nord" in calls


@pytest.mark.asyncio
async def test_command_palette_on_ctrl_k_not_ctrl_p():
    """Textual's built-in ctrl+p palette is disabled; ours lives on Ctrl+K."""
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test() as pilot:
        await pilot.pause()
        # Built-in Textual palette is off, so no priority ctrl+p binding exists.
        assert app.ENABLE_COMMAND_PALETTE is False
        actions = {binding.action for _key, binding in app._bindings}
        assert "command_palette" not in actions
        assert "app.command_palette" not in actions
        # Our own palette resolves on Ctrl+K via the dispatcher.
        cmd = app.dispatcher.hotkey_lookup("ctrl+k")
        assert cmd is not None and cmd.id == "palette.open"
        # Ctrl+P no longer opens any palette — it's the panels-fullscreen key.
        cmd_p = app.dispatcher.hotkey_lookup("ctrl+p")
        assert cmd_p is not None and cmd_p.id == "panels.fullscreen"


@pytest.mark.asyncio
async def test_panel_exposes_docker_actions_in_docker_scheme(monkeypatch):
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm")
    async with app.run_test():
        panel = app._active_panel()
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        ids = {c.id for c in panel.get_commands()}
        assert "provider.docker.start" in ids
        assert "provider.docker.rebuild" in ids


@pytest.mark.asyncio
async def test_docker_menu_present_only_in_docker_scheme(monkeypatch, tmp_path):
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        def labels():
            return [m.label for m in app.menu_bar.menus]
        assert "Docker" not in labels()          # local scheme by default
        panel = app._active_panel()
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        app._recompute_menu_bar()
        assert "Docker" in labels()


@pytest.mark.asyncio
async def test_open_dunder_empty_spec_opens_docker_but_not_ftp(monkeypatch, tmp_path):
    """An empty spec must reach Docker (→ container index) but stay a no-op for
    host-based providers like FTP."""
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import OpenDunderRequest
    from dunders.fm.dialogs import NewFileDialog
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        calls = []
        monkeypatch.setattr(app, "_do_open_dunder",
                            lambda scheme, spec: calls.append((scheme, spec)))
        monkeypatch.setattr(app, "_close_modal", lambda dlg: None)

        dlg = NewFileDialog(prompt="x", context=OpenDunderRequest(scheme="docker"))
        app.on_new_file_dialog_submitted(NewFileDialog.Submitted(dlg, ""))
        assert calls == [("docker", "")]

        calls.clear()
        ftp = NewFileDialog(prompt="x", context=OpenDunderRequest(scheme="ftp"))
        app.on_new_file_dialog_submitted(NewFileDialog.Submitted(ftp, ""))
        assert calls == []  # empty host spec stays a no-op for FTP


@pytest.mark.asyncio
async def test_exit_docker_container_focuses_it_in_index(tmp_path, monkeypatch):
    """Leaving a container (.. to the index) lands the cursor on that container,
    not at the top. Exiting a real archive still focuses the archive file."""
    from pathlib import Path

    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        captured = {}
        monkeypatch.setattr(
            panel, "refresh_listing",
            lambda *, focus_loc=None: captured.__setitem__("focus_loc", focus_loc),
        )
        # Exit a docker container → focus_loc is the container entry itself.
        container = VfsPath(scheme="docker", root="", parts=("web",))
        panel.cwd_loc = container
        panel._change_cwd_loc(VfsPath(scheme="docker", root="", parts=()))
        assert captured["focus_loc"] == container

        # Regression: exiting a zip archive into a local folder still focuses
        # the .zip file (remap to the local source-root path).
        panel.cwd_loc = VfsPath(scheme="zip", root="/tmp/a.zip", parts=())
        panel._change_cwd_loc(VfsPath.local("/tmp"))
        assert captured["focus_loc"] == VfsPath.local(Path("/tmp/a.zip"))


@pytest.mark.asyncio
async def test_provider_action_keeps_cursor_on_acted_container(monkeypatch, tmp_path):
    # After start/stop/… the async re-scan must re-focus the acted container,
    # not jump the cursor to the top of the list.
    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        captured = {}
        monkeypatch.setattr(panel, "refresh_listing",
                            lambda *, focus_loc=None: captured.__setitem__("f", focus_loc))
        loc = VfsPath(scheme="docker", root="", parts=("web",))

        class _Res:
            errors = []

        app._after_provider_action(panel, _Res(), loc)
        assert captured["f"] == loc


class TestImageRouting:
    def test_looks_image_png(self, tmp_path):
        from dunders.app import DundersApp

        p = tmp_path / "a.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0d")
        assert DundersApp._looks_image(p) is True

    def test_looks_image_text(self, tmp_path):
        from dunders.app import DundersApp

        p = tmp_path / "a.txt"
        p.write_text("hello world\n")
        assert DundersApp._looks_image(p) is False

    def test_looks_image_missing_file(self, tmp_path):
        from dunders.app import DundersApp

        assert DundersApp._looks_image(tmp_path / "nope.png") is False

    @pytest.mark.asyncio
    async def test_f3_image_routes_to_image_viewer(self, tmp_path):
        pytest.importorskip("PIL")
        from PIL import Image

        from dunders.app import DundersApp
        from dunders.fm.image_viewer import ImageViewerContent

        png = tmp_path / "p.png"
        Image.new("RGB", (4, 4), (0, 128, 255)).save(png)

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(png, read_only=True)
            await pilot.pause()
            assert any(
                isinstance(w.content, ImageViewerContent)
                for w in app.desktop.windows
            )


def test_status_marshaller_throttles_flood_but_shows_each_file():
    """Byte-granular copy fires thousands of statuses/sec; the marshaller must
    drop most (so the UI thread stays free to receive Cancel) yet always show
    the first sight of each new file and the final 100%."""
    from types import SimpleNamespace
    from dunders.fm.actions import CopyStatus

    marshalled: list[CopyStatus] = []
    progress = SimpleNamespace(set_copy_status=lambda s: None)
    fake_self = SimpleNamespace(
        call_from_thread=lambda fn, status: marshalled.append(status)
    )
    cb = DundersApp._status_marshaller(fake_self, progress)

    # A storm of chunk updates for one big file, back-to-back (no real delay).
    for done in range(0, 5000, 1):
        cb(CopyStatus(done=done, total=10_000, label="big.bin", is_bytes=True))
    # Throttled hard: far fewer than the 5000 chunks, but the file was announced.
    assert len(marshalled) < 100
    assert marshalled[0].label == "big.bin"

    before_second = len(marshalled)
    # A new file is always shown immediately (name-before-bytes).
    cb(CopyStatus(done=5000, total=10_000, label="next.bin", is_bytes=True))
    assert len(marshalled) == before_second + 1
    assert marshalled[-1].label == "next.bin"

    # The final 100% always lands so the bar completes.
    cb(CopyStatus(done=10_000, total=10_000, label="next.bin", is_bytes=True))
    assert marshalled[-1].done == 10_000
