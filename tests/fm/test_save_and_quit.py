"""Ctrl+G — save the focused editor and leave the app in one keystroke.

Exists for `$EDITOR` integrations (Claude Code's `ctrl+x ctrl+e` writes a temp
buffer, runs the editor, and reads the file back once the process exits), so
the path must not go through the F10 quit confirmation.
"""

import pytest

from dunders.app import DundersApp, _FocusableEditorContent


def _focused_editor(app: DundersApp) -> _FocusableEditorContent:
    win = app.desktop.focused_window
    assert isinstance(win.content, _FocusableEditorContent)
    return win.content


@pytest.mark.asyncio
async def test_ctrl_g_writes_the_buffer_and_exits(tmp_path, monkeypatch):
    target = tmp_path / "prompt.md"
    target.write_text("hello", encoding="utf-8")
    app = DundersApp(launch_mode="we", initial_paths=[target])
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[bool] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
        await pilot.press("!")
        await pilot.pause()
        assert _focused_editor(app).is_dirty
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert exits == [True]
        assert "!" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_ctrl_g_exits_a_clean_buffer_without_touching_disk(tmp_path, monkeypatch):
    target = tmp_path / "prompt.md"
    target.write_text("hello", encoding="utf-8")
    app = DundersApp(launch_mode="we", initial_paths=[target])
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[bool] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert exits == [True]
        assert target.read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_ctrl_g_keeps_an_unnamed_buffer_alive(monkeypatch):
    # No paths -> one untitled editor. Nothing can be written, so quitting
    # would silently drop the text; stay put and warn instead.
    app = DundersApp(launch_mode="we", initial_paths=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[bool] = []
        warnings: list[str] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
        monkeypatch.setattr(
            app, "notify", lambda msg, *a, **k: warnings.append(str(msg))
        )
        await pilot.press("!")
        await pilot.pause()
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert exits == []
        assert warnings and "Save As" in warnings[-1]
        assert _focused_editor(app).is_dirty


@pytest.mark.asyncio
async def test_ctrl_g_is_registered_as_an_editor_command(tmp_path):
    target = tmp_path / "prompt.md"
    target.write_text("hello", encoding="utf-8")
    app = DundersApp(launch_mode="we", initial_paths=[target])
    async with app.run_test() as pilot:
        await pilot.pause()
        commands = {c.id: c for c in _focused_editor(app).get_commands()}
        assert commands["save_quit"].hotkey == "ctrl+g"
        editor_menu = next(m for m in app._all_menus if m.label == "Editor")
        assert "save_quit" in {
            getattr(item, "command_id", None) for item in editor_menu.items
        }


# ---------------------------------------------------------------------------
# ctrl+x ctrl+e — the same chord that opened the buffer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ctrl_x_ctrl_e_saves_and_exits(tmp_path, monkeypatch):
    """Ctrl+G is remapped on plenty of machines; the opening chord must work."""
    target = tmp_path / "prompt.md"
    target.write_text("hello", encoding="utf-8")
    app = DundersApp(launch_mode="we", initial_paths=[target])
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[bool] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
        await pilot.press("!")
        await pilot.pause()
        await pilot.press("ctrl+x")
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert exits == [True]
        assert "!" in target.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_chord_puts_back_the_line_ctrl_x_cut(tmp_path, monkeypatch):
    """Ctrl+X with no selection cuts the line — the chord must undo that."""
    target = tmp_path / "prompt.md"
    target.write_text("first line\nsecond line\n", encoding="utf-8")
    app = DundersApp(launch_mode="we", initial_paths=[target])
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "exit", lambda *a, **k: None)
        await pilot.press("ctrl+x")
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert target.read_text(encoding="utf-8").startswith("first line")


@pytest.mark.asyncio
async def test_ctrl_x_alone_still_cuts(tmp_path, monkeypatch):
    """Only the immediate Ctrl+E cancels the cut; anything else keeps it."""
    target = tmp_path / "prompt.md"
    target.write_text("first line\nsecond line\n", encoding="utf-8")
    app = DundersApp(launch_mode="we", initial_paths=[target])
    async with app.run_test() as pilot:
        await pilot.pause()
        exits: list[bool] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: exits.append(True))
        await pilot.press("ctrl+x")
        await pilot.pause()
        assert exits == []
        editor = _focused_editor(app)
        assert "first line" not in editor._editor.buffer.lines
        # A Ctrl+E that arrives *later* is not part of the chord: the arming
        # expires, so it neither quits nor resurrects the cut line.
        editor._editor._save_quit_chord_at -= (
            editor._editor.SAVE_QUIT_CHORD_TIMEOUT + 1
        )
        await pilot.press("ctrl+e")
        await pilot.pause()
        assert exits == []
        assert "first line" not in editor._editor.buffer.lines
