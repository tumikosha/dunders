from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.core.search import SearchOptions
from dunders.windowing.editor.widget import EditorWidget


def _w(text: str) -> EditorWidget:
    return EditorWidget(buffer=TextBuffer.from_string(text))


def test_replace_current_replaces_one_and_advances():
    w = _w("foo bar foo")
    w.search("foo", SearchOptions(case_sensitive=True))
    ok = w.replace_current("xyz")
    assert ok is True
    assert w.buffer.lines == ["xyz bar foo"]
    assert len(w._search_matches) == 1


def test_replace_current_no_match_returns_false():
    w = _w("hello")
    w.search("foo", SearchOptions(case_sensitive=True))
    assert w.replace_current("xyz") is False
    assert w.buffer.lines == ["hello"]


def test_replace_current_multiline_match_offsets():
    w = _w("foo\nfoo end")
    w.search("foo", SearchOptions(case_sensitive=True))
    w.replace_current("ZZ")
    assert w.buffer.lines == ["ZZ", "foo end"]


def test_replace_all_counts_and_replaces():
    w = _w("foo bar foo baz foo")
    w.search("foo", SearchOptions(case_sensitive=True))
    n = w.replace_all("XX")
    assert n == 3
    assert w.buffer.lines == ["XX bar XX baz XX"]


def test_replace_all_replacement_contains_pattern_no_loop():
    w = _w("foo foo")
    w.search("foo", SearchOptions(case_sensitive=True))
    n = w.replace_all("foofoo")
    assert n == 2
    assert w.buffer.lines == ["foofoo foofoo"]


def test_replace_all_multiline():
    w = _w("foo a\nfoo b\nfoo c")
    w.search("foo", SearchOptions(case_sensitive=True))
    n = w.replace_all("Z")
    assert n == 3
    assert w.buffer.lines == ["Z a", "Z b", "Z c"]


def test_replace_all_undo_restores_original():
    w = _w("foo foo foo")
    original = list(w.buffer.lines)
    w.search("foo", SearchOptions(case_sensitive=True))
    w.replace_all("X")
    assert w.buffer.undo() is True
    assert w.buffer.lines == original


def test_replace_all_empty_returns_zero():
    w = _w("hello")
    w.search("foo", SearchOptions(case_sensitive=True))
    assert w.replace_all("X") == 0
