from __future__ import annotations

from dunders.fm.console.buffer import ConsoleBuffer
from dunders.fm.console.ansi import Segment, Style


def test_append_plain_text_two_lines():
    b = ConsoleBuffer(maxlen=100)
    b.append_bytes(b"hello\nworld\n")
    assert b.line_count() == 2
    assert b.line(0) == [Segment("hello", Style())]
    assert b.line(1) == [Segment("world", Style())]


def test_partial_line_kept_until_newline():
    b = ConsoleBuffer(maxlen=100)
    b.append_bytes(b"abc")
    assert b.line_count() == 1            # one partial line is visible
    assert b.line(0) == [Segment("abc", Style())]
    b.append_bytes(b"def\n")
    assert b.line_count() == 1
    assert b.line(0) == [Segment("abcdef", Style())]


def test_cap_drops_oldest():
    b = ConsoleBuffer(maxlen=3)
    for i in range(5):
        b.append_bytes(f"line{i}\n".encode())
    assert b.line_count() == 3
    assert b.line(0) == [Segment("line2", Style())]
    assert b.line(2) == [Segment("line4", Style())]


def test_clear_resets():
    b = ConsoleBuffer(maxlen=10)
    b.append_bytes(b"hi\n")
    b.clear()
    assert b.line_count() == 0


def test_view_offset_locking_and_unlock():
    b = ConsoleBuffer(maxlen=100)
    b.append_bytes(b"a\n")
    assert b.view_offset == 0
    b.scroll_up(1)
    assert b.view_offset == 1
    b.append_bytes(b"b\n")
    # offset is preserved while user is scrolled away
    assert b.view_offset == 1
    b.scroll_to_bottom()
    assert b.view_offset == 0
