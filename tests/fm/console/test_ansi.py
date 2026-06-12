"""Unit tests for the SGR + control-char parser."""

from __future__ import annotations

from dunders.fm.console.ansi import AnsiParser, Segment, Style


def _parse_all(text: bytes) -> list[list[Segment]]:
    p = AnsiParser()
    return list(p.feed(text))


def test_plain_text_one_line():
    lines = _parse_all(b"hello")
    assert lines == [[Segment("hello", Style())]]


def test_newline_splits_lines():
    lines = _parse_all(b"a\nb\n")
    assert lines == [
        [Segment("a", Style())],
        [Segment("b", Style())],
        [],
    ]


def test_sgr_red_then_reset():
    lines = _parse_all(b"\x1b[31mred\x1b[0m end")
    seg = lines[0]
    assert seg[0].text == "red" and seg[0].style.fg == "red"
    assert seg[1].text == " end" and seg[1].style.fg is None


def test_sgr_bold_truecolor_bg():
    lines = _parse_all(b"\x1b[1;38;2;10;20;30;48;5;7mX\x1b[0m")
    seg = lines[0][0]
    assert seg.text == "X"
    assert seg.style.bold is True
    assert seg.style.fg == "rgb(10,20,30)"
    assert seg.style.bg == "color(7)"


def test_carriage_return_resets_column():
    lines = _parse_all(b"loading...\rdone     \n")
    # \r overwrites in place; final visible line should start with "done"
    assert lines[0] == [Segment("done     ", Style())]


def test_backspace_removes_previous_char():
    lines = _parse_all(b"abc\b\bX\n")
    assert lines[0] == [Segment("aX", Style())]


def test_tab_expands_to_8_spaces():
    lines = _parse_all(b"a\tb\n")
    assert lines[0] == [Segment("a       b", Style())]


def test_unsupported_csi_is_swallowed():
    lines = _parse_all(b"\x1b[2J\x1b[Hhi\n")
    assert lines[0] == [Segment("hi", Style())]


def test_osc_is_swallowed():
    lines = _parse_all(b"\x1b]0;title\x07hi\n")
    assert lines[0] == [Segment("hi", Style())]


def test_split_escape_across_chunks():
    p = AnsiParser()
    out = list(p.feed(b"a\x1b[3"))
    out += list(p.feed(b"1mred\x1b[0m\n"))
    flat = [seg for line in out for seg in line]
    # Eventually we should see "a" with default style and "red" with fg=red.
    assert any(s.text == "a" and s.style.fg is None for s in flat)
    assert any(s.text == "red" and s.style.fg == "red" for s in flat)


def test_unbounded_incomplete_escape_does_not_grow_forever():
    p = AnsiParser()
    # Feed a never-completing escape repeatedly. After feeding well over the
    # cap, the parser must not have unbounded internal state.
    junk = b"\x1b[" + b";1" * 5000  # well past 4 KiB
    list(p.feed(junk))
    # Recovery: parser should now accept new normal input without hanging.
    out = list(p.feed(b"hello\n"))
    assert any(seg.text == "hello" for line in out for seg in line)
