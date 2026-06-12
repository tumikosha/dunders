import re

import pytest

from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.core.search import SearchOptions, Match, find_matches


def test_empty_pattern_returns_empty():
    buf = TextBuffer.from_string("hello world")
    assert find_matches(buf, "", SearchOptions()) == []


def test_match_dataclass_fields():
    m = Match(row=1, col=2, length=3)
    assert (m.row, m.col, m.length) == (1, 2, 3)


def test_literal_case_insensitive_default():
    buf = TextBuffer.from_string("Foo bar foo BAR")
    matches = find_matches(buf, "foo", SearchOptions())
    assert matches == [Match(0, 0, 3), Match(0, 8, 3)]


def test_case_sensitive_distinguishes():
    buf = TextBuffer.from_string("Foo bar foo")
    matches = find_matches(buf, "foo", SearchOptions(case_sensitive=True))
    assert matches == [Match(0, 8, 3)]


def test_multiline_literal():
    buf = TextBuffer.from_string("foo\nbar foo\nfoo end")
    matches = find_matches(buf, "foo", SearchOptions(case_sensitive=True))
    assert matches == [Match(0, 0, 3), Match(1, 4, 3), Match(2, 0, 3)]


def test_metachars_literal_when_regex_off():
    buf = TextBuffer.from_string("abc a.c xyz")
    matches = find_matches(buf, "a.c", SearchOptions(case_sensitive=True))
    assert matches == [Match(0, 4, 3)]


def test_whole_word_excludes_partial():
    buf = TextBuffer.from_string("foo food foobar foo")
    matches = find_matches(
        buf, "foo", SearchOptions(case_sensitive=True, whole_word=True)
    )
    assert matches == [Match(0, 0, 3), Match(0, 16, 3)]


def test_regex_match():
    buf = TextBuffer.from_string("a1 b22 c333")
    matches = find_matches(
        buf, r"[a-z]\d+", SearchOptions(regex=True, case_sensitive=True)
    )
    assert matches == [Match(0, 0, 2), Match(0, 3, 3), Match(0, 7, 4)]


def test_bad_regex_raises():
    buf = TextBuffer.from_string("hello")
    with pytest.raises(re.error):
        find_matches(buf, "(unclosed", SearchOptions(regex=True))


def test_whole_word_with_regex_word_boundary_chains():
    buf = TextBuffer.from_string("foo123 foo bar")
    matches = find_matches(
        buf,
        r"foo\d*",
        SearchOptions(regex=True, whole_word=True, case_sensitive=True),
    )
    assert matches == [Match(0, 0, 6), Match(0, 7, 3)]


def test_in_selection_filters_matches():
    buf = TextBuffer.from_string("foo bar\nfoo baz\nfoo end")
    matches = find_matches(
        buf,
        "foo",
        SearchOptions(case_sensitive=True, in_selection=True),
        selection=(1, 0, 2, 3),
    )
    assert matches == [Match(1, 0, 3), Match(2, 0, 3)]


def test_in_selection_drops_partial_match():
    buf = TextBuffer.from_string("xfoox\nyfooy")
    matches = find_matches(
        buf,
        "foo",
        SearchOptions(case_sensitive=True, in_selection=True),
        selection=(0, 2, 0, 4),
    )
    assert matches == []


def test_in_selection_without_range_falls_back_to_full():
    buf = TextBuffer.from_string("foo bar")
    matches = find_matches(
        buf,
        "foo",
        SearchOptions(case_sensitive=True, in_selection=True),
        selection=None,
    )
    assert matches == [Match(0, 0, 3)]
