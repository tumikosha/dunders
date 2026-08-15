"""Editor hotkeys must fire exactly once through the CommandRouter.

A key declared BOTH in `EditorContent.BINDINGS` and as a `WindowCommand`
hotkey is dispatched twice: once by Textual's binding walk, once by
`CommandRouter` on `App.on_key`. Idempotent actions (open the search panel)
survive that; toggles do not — fold-then-unfold nets out to nothing.
"""

import pytest

from dunders.app import DundersApp

CODE = "def f():\n    a = 1\n    b = 2\n    c = 3\n\ndef g():\n    x = 1\n    y = 2\n"


def _collapsed(editor) -> list[tuple[int, int]]:
    return [
        (r.start_row, r.end_row)
        for r in getattr(editor, "_fold_regions", [])
        if r.collapsed
    ]


@pytest.mark.asyncio
async def test_f7_folds_then_unfolds(tmp_path):
    target = tmp_path / "a.py"
    target.write_text(CODE, encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        content = app.desktop.focused_window.content
        editor = content._editor
        assert editor.fold_engine.scan(editor.buffer.lines), "no foldable regions"
        assert _collapsed(editor) == []

        await pilot.press("f7")
        await pilot.pause()
        assert _collapsed(editor) == [(0, 3), (5, 7)]

        await pilot.press("f7")
        await pilot.pause()
        assert _collapsed(editor) == []


@pytest.mark.asyncio
async def test_f8_toggles_macro_recording_once(tmp_path):
    target = tmp_path / "a.py"
    target.write_text(CODE, encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        content = app.desktop.focused_window.content
        recorder = content._macro_recorder
        assert recorder is not None and not recorder.is_recording

        await pilot.press("f8")
        await pilot.pause()
        assert recorder.is_recording, "F8 started and immediately stopped recording"

        await pilot.press("f8")
        await pilot.pause()
        assert not recorder.is_recording


@pytest.mark.asyncio
async def test_editor_fkeys_match_the_status_bar(tmp_path):
    """Pressing F<n> must do what the status bar's F<n> button does.

    The status bar builds its handlers from command ids (`_editor_status_items`)
    while the keys come from `WindowCommand` hotkeys / `BINDINGS`. Nothing keeps
    the two in sync, so F3 advertised Save As while the key ran Find Next.
    """
    target = tmp_path / "a.py"
    target.write_text(CODE, encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        status_cmd = {
            "3": "save_as", "4": "replace", "5": "split_h",
            "6": "split_v", "7": "fold_toggle", "8": "record_macro",
        }
        for digit, cmd_id in status_cmd.items():
            resolved = app.dispatcher.hotkey_lookup(f"f{digit}")
            assert resolved is not None, f"F{digit} resolves to nothing"
            assert resolved.id == cmd_id, (
                f"status bar F{digit} runs {cmd_id!r} but the key runs {resolved.id!r}"
            )


@pytest.mark.asyncio
async def test_f3_opens_save_as_and_f5_f6_split(tmp_path):
    target = tmp_path / "a.py"
    target.write_text(CODE, encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        content = app.desktop.focused_window.content
        assert content._splitter is None

        await pilot.press("f5")
        await pilot.pause()
        assert content._splitter is not None, "F5 did not split horizontally"
        await pilot.press("f5")
        await pilot.pause()
        assert content._splitter is None, "F5 did not toggle the split back"

        await pilot.press("f6")
        await pilot.pause()
        assert content._splitter is not None, "F6 did not split vertically"
        await pilot.press("f6")
        await pilot.pause()

        await pilot.press("f3")
        await pilot.pause()
        assert app._has_active_modal(), "F3 did not open the Save As dialog"


@pytest.mark.asyncio
async def test_search_repeats_moved_to_ctrl_l(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("foo\nbar\nfoo\n", encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        content = app.desktop.focused_window.content
        calls: list[str] = []
        content.action_find_next = lambda: calls.append("next")
        content.action_find_prev = lambda: calls.append("prev")

        await pilot.press("ctrl+l")
        await pilot.pause()
        await pilot.press("ctrl+shift+l")
        await pilot.pause()
        # Exactly once each: the commands carry hotkey_label, not hotkey, so
        # the router never becomes a second owner of these keys.
        assert calls == ["next", "prev"]


@pytest.mark.asyncio
async def test_search_commands_are_in_the_editor_menu(tmp_path):
    target = tmp_path / "a.py"
    target.write_text(CODE, encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor_menu = next(m for m in app._all_menus if m.label == "Editor")
        cmd_ids = {getattr(item, "command_id", None) for item in editor_menu.items}
        assert {"find_next", "find_prev", "replace_all"} <= cmd_ids
        cmds = {c.id: c for c in app.desktop.focused_window.content.get_commands()}
        assert cmds["find_next"].display_hotkey() == "Ctrl+L"


@pytest.mark.asyncio
async def test_fold_toggle_is_in_the_editor_menu(tmp_path):
    target = tmp_path / "a.py"
    target.write_text(CODE, encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor_menu = next(m for m in app._all_menus if m.label == "Editor")
        cmd_ids = {getattr(item, "command_id", None) for item in editor_menu.items}
        assert {"fold_toggle", "fold_all", "unfold_all"} <= cmd_ids


@pytest.mark.asyncio
async def test_tab_indents_the_selected_block_in_the_full_app(tmp_path):
    """The App's priority Tab must hand the key to the editor, not switch panels."""
    target = tmp_path / "a.py"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.desktop.focused_window.content._editor
        editor.focus()
        await pilot.pause()
        editor.buffer.cursor_row, editor.buffer.cursor_col = 0, 0
        await pilot.press("shift+down", "shift+end", "tab")
        await pilot.pause()
        assert editor.buffer.lines[:3] == ["    alpha", "    beta", "gamma"]


@pytest.mark.asyncio
async def test_shift_tab_unindents_the_selected_block_in_the_full_app(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("        alpha\n        beta\ngamma\n", encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.desktop.focused_window.content._editor
        editor.focus()
        await pilot.pause()
        editor.buffer.cursor_row, editor.buffer.cursor_col = 0, 0
        await pilot.press("shift+down", "shift+end", "shift+tab")
        await pilot.pause()
        assert editor.buffer.lines[:3] == ["    alpha", "    beta", "gamma"]


@pytest.mark.asyncio
async def test_shift_tab_without_a_selection_still_cycles_windows(tmp_path):
    """Unindent must not steal the window cycler when nothing is selected."""
    target = tmp_path / "a.py"
    target.write_text("    alpha\n", encoding="utf-8")
    app = DundersApp(launch_mode="editor", initial_path=str(target))
    async with app.run_test() as pilot:
        await pilot.pause()
        window = app.desktop.focused_window
        editor = window.content._editor
        editor.focus()
        await pilot.pause()
        cycled = []
        app.desktop.cycle_focus = lambda *a, **k: cycled.append((a, k))
        await pilot.press("shift+tab")
        await pilot.pause()
        assert editor.buffer.lines == ["    alpha", ""]
        assert cycled, "Shift+Tab did not reach the window cycler"
