"""Tests for EditorWidget."""

import pytest
from dunders.windowing.editor.widget import EditorWidget
from dunders.windowing.core.buffer import TextBuffer


def test_editor_widget_creates_with_empty_buffer():
    widget = EditorWidget()
    assert widget.buffer is not None
    assert widget.buffer.lines == [""]


def test_editor_widget_creates_with_provided_buffer():
    buf = TextBuffer.from_string("hello\nworld")
    widget = EditorWidget(buffer=buf)
    assert widget.buffer is buf
    assert widget.buffer.lines == ["hello", "world"]


def test_editor_widget_show_line_numbers_default():
    widget = EditorWidget()
    assert widget.show_line_numbers is True


def test_editor_widget_gutter_width():
    buf = TextBuffer.from_string("\n".join(["line"] * 100))
    widget = EditorWidget(buffer=buf)
    # 100 lines = 3 digits + 2 spaces = 5
    assert widget._gutter_width() == 5


def test_editor_widget_gutter_width_no_line_numbers():
    widget = EditorWidget(show_line_numbers=False)
    assert widget._gutter_width() == 0


def test_editor_has_cmd_clipboard_bindings():
    # Cmd (super) aliases for copy/cut/paste, for kitty-protocol terminals.
    actions = {b.key: b.action for b in EditorWidget.BINDINGS}
    assert actions.get("super+c") == "copy"
    assert actions.get("super+x") == "cut"
    assert actions.get("super+v") == "paste"


def test_move_cursor_document_start():
    buf = TextBuffer.from_string("alpha\nbeta\ngamma")
    buf.cursor_row, buf.cursor_col = 1, 3
    buf.move_cursor_document_start()
    assert (buf.cursor_row, buf.cursor_col) == (0, 0)


def test_move_cursor_document_end():
    buf = TextBuffer.from_string("alpha\nbeta\ngamma")
    buf.cursor_row, buf.cursor_col = 0, 2
    buf.move_cursor_document_end()
    assert (buf.cursor_row, buf.cursor_col) == (2, len("gamma"))


def test_move_cursor_document_end_empty_buffer():
    buf = TextBuffer.from_string("")
    buf.move_cursor_document_end()
    assert (buf.cursor_row, buf.cursor_col) == (0, 0)
