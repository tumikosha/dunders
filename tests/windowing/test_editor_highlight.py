from textual.app import App, ComposeResult

from dunders.windowing.editor.widget import EditorWidget
from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.palette import Palette
from dunders.windowing.themes.modern_dark import modern_dark
from dunders.windowing.editor.content import EditorContent


class _Host(App):
    def __init__(self, text: str, path: str) -> None:
        super().__init__()
        self._buf = TextBuffer.from_string(text)
        self._buf.file_path = path
        self._palette = Palette(modern_dark)

    def compose(self) -> ComposeResult:
        yield EditorWidget(buffer=self._buf, palette=self._palette)


async def test_editor_populates_syntax_spans_for_python():
    app = _Host("def f():\n    return 1\n", "foo.py")
    async with app.run_test() as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert editor._syntax_spans, "no syntax spans computed"
        roles = {s.role for s in editor._syntax_spans[0]}
        assert "keyword" in roles


async def test_editor_no_spans_for_unknown_language():
    # Unknown extension + blank content: filename lookup fails and the
    # content guesser is skipped (blank sample), so no lexer is resolved.
    app = _Host("   \n   \n", "notes.unknownext")
    async with app.run_test() as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert editor._highlighter.enabled is False
        assert all(not row for row in editor._syntax_spans) or editor._syntax_spans == []


async def test_keyword_is_styled_in_render():
    app = _Host("def f():\n", "foo.py")
    async with app.run_test() as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        strip = editor.render_line(0)
        assert any(
            seg.style is not None and seg.style.color is not None
            for seg in strip._segments if seg.text.strip()
        )


async def test_selection_overrides_syntax():
    app = _Host("def f():\n", "foo.py")
    async with app.run_test() as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        editor.buffer.start_selection(0, 0)
        editor.buffer.update_selection(0, 3)  # select "def"
        editor.buffer.cursor_row, editor.buffer.cursor_col = 0, 3
        strip = editor.render_line(0)
        sel_bg = editor._rich_style("editor.selection").bgcolor
        assert any(
            seg.style is not None and seg.style.bgcolor == sel_bg
            for seg in strip._segments if seg.text
        )


async def test_render_without_highlight_is_plain():
    app = _Host("def f():\n", "foo.py")
    async with app.run_test() as pilot:
        editor = app.query_one(EditorWidget)
        editor._highlight_enabled = False
        editor._syntax_spans = []
        await pilot.pause()
        strip = editor.render_line(0)
        kw = editor._rich_style("editor.syntax.keyword").color
        # Highlighting off → no syntax colour anywhere on the line.
        assert all(
            seg.style is None or seg.style.color != kw
            for seg in strip._segments if seg.text.strip()
        )


def test_toggle_command_present():
    content = EditorContent(initial_text="def f():\n", file_path="foo.py")
    cmds = content.get_commands()
    ids = {c.id for c in cmds}
    assert "toggle_syntax" in ids
    assert "set_language" in ids
    toggle = next(c for c in cmds if c.id == "toggle_syntax")
    assert toggle.hotkey == "ctrl+h"


async def test_toggle_disables_highlight():
    app = _Host("def f():\n", "foo.py")
    async with app.run_test() as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert editor._highlight_enabled is True
        editor.set_highlight_enabled(False)
        await pilot.pause()
        assert editor._highlight_enabled is False
        assert editor._syntax_spans == []
        editor.set_highlight_enabled(True)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert editor._highlight_enabled is True
        assert editor._syntax_spans  # recomputed


async def test_set_language_changes_highlight():
    # Open as a plain extension, then force JSON via the picker API path.
    app = _Host('{"a": 1, "b": "x"}\n', "data.unknownext")
    async with app.run_test() as pilot:
        editor = app.query_one(EditorWidget)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        editor.set_language("json")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert editor._highlighter.enabled
        assert editor._syntax_spans
        roles = {s.role for row in editor._syntax_spans for s in row}
        assert roles & {"string", "number", "name"}
