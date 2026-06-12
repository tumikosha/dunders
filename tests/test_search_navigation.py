from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.core.search import SearchOptions
from dunders.windowing.editor.widget import EditorWidget


def _w(text: str) -> EditorWidget:
    return EditorWidget(buffer=TextBuffer.from_string(text))


def test_search_populates_matches_and_returns_count():
    w = _w("foo bar foo")
    n = w.search("foo", SearchOptions(case_sensitive=True))
    assert n == 2
    assert len(w._search_matches) == 2


def test_search_empty_clears():
    w = _w("foo")
    w.search("foo", SearchOptions(case_sensitive=True))
    n = w.search("", SearchOptions())
    assert n == 0
    assert w._search_matches == []
    assert w._current_match_idx == -1


def test_search_bad_regex_keeps_previous_matches():
    w = _w("foo bar")
    w.search("foo", SearchOptions(case_sensitive=True))
    n = w.search("(unclosed", SearchOptions(regex=True))
    assert n == -1
    assert len(w._search_matches) == 1


def test_find_next_cycles_with_wrap():
    w = _w("foo a foo b foo")
    w.search("foo", SearchOptions(case_sensitive=True, wrap_around=True))
    # current = 0 because cursor at (0,0)
    w.find_next()
    assert w._current_match_idx == 1
    w.find_next()
    assert w._current_match_idx == 2
    w.find_next()
    assert w._current_match_idx == 0  # wrap


def test_find_next_stops_without_wrap():
    w = _w("foo foo foo")
    w.search("foo", SearchOptions(case_sensitive=True, wrap_around=False))
    w.find_next()
    w.find_next()
    last = w._current_match_idx
    w.find_next()
    assert w._current_match_idx == last  # stays


def test_find_prev_cycles():
    w = _w("foo a foo b foo")
    w.search("foo", SearchOptions(case_sensitive=True, wrap_around=True))
    # current = 0 because cursor at (0,0)
    w.find_prev()
    assert w._current_match_idx == 2  # wraps backward from 0
    w.find_prev()
    assert w._current_match_idx == 1


def test_find_next_moves_cursor():
    w = _w("foo bar foo")
    w.search("foo", SearchOptions(case_sensitive=True))
    w.find_next()
    assert (w.buffer.cursor_row, w.buffer.cursor_col) == (0, 8)
