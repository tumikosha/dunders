"""Tests for EditorWidget."""

import pytest
from tyui.windowing.editor.widget import EditorWidget
from tyui.windowing.core.buffer import TextBuffer


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
