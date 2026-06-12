"""Quick-search behaviour on FilePanel.

Activated by Ctrl+S; cursor jumps to the first case-insensitive substring
match without filtering the listing. Backspace shrinks the query (empty ->
exit), Escape exits unconditionally, Ctrl+S again toggles off, any explicit
navigation key exits before navigating.
"""

from __future__ import annotations

from pathlib import Path

from textual import events

from dunders.fm.file_panel import FilePanel


def _make_panel(tmp_path: Path, names: list[str]) -> FilePanel:
    for n in names:
        (tmp_path / n).write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    return p


def _key(key: str, character: str | None = None) -> events.Key:
    return events.Key(key=key, character=character)


def test_ctrl_s_activates_with_empty_query(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "main.py"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    assert p._qs_active is True
    assert p._qs_query == ""
    # Empty query doesn't move the cursor.
    assert p.cursor == 0


def test_typing_after_ctrl_s_jumps_to_first_match(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "beta.txt", "main.py"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("m", "m"))
    assert p._qs_query == "m"
    assert p.entries[p.cursor].name == "main.py"


def test_typing_extends_query_and_refines_match(tmp_path: Path):
    p = _make_panel(tmp_path, ["main.py", "manage.py", "munch.txt"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("m", "m"))
    # query=='m' lands on first match in alphabetical order => "main.py".
    assert p.entries[p.cursor].name == "main.py"
    p.on_key(_key("u", "u"))
    assert p._qs_query == "mu"
    assert p.entries[p.cursor].name == "munch.txt"


def test_query_is_case_insensitive_substring(tmp_path: Path):
    p = _make_panel(tmp_path, ["MyFile.TXT", "other.txt"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("f", "f"))
    # 'f' is inside "MyFile.TXT".
    assert p.entries[p.cursor].name == "MyFile.TXT"


def test_backspace_shrinks_then_exits(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "main.py"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("m", "m"))
    p.on_key(_key("a", "a"))
    assert p._qs_query == "ma"
    p.on_key(_key("backspace"))
    assert p._qs_query == "m"
    assert p._qs_active is True
    p.on_key(_key("backspace"))
    # query is empty but mode stays active until next backspace.
    assert p._qs_active is True
    assert p._qs_query == ""
    p.on_key(_key("backspace"))
    assert p._qs_active is False


def test_escape_exits(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "beta.txt"])
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("a", "a"))
    assert p._qs_active is True
    p.on_key(_key("escape"))
    assert p._qs_active is False
    assert p._qs_query == ""


def test_ctrl_s_toggles_off_when_active(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt"])
    p.on_key(_key("ctrl+s"))
    assert p._qs_active is True
    p.on_key(_key("ctrl+s"))
    assert p._qs_active is False


def test_no_match_keeps_cursor_and_query(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "beta.txt"])
    p.cursor = 1  # on 'alpha.txt' (after '..')
    before = p.cursor
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("z", "z"))
    assert p._qs_active is True
    assert p._qs_query == "z"
    assert p.cursor == before  # nothing matched -> cursor untouched


def test_action_cursor_down_resets_quick_search(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "beta.txt"])
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("a", "a"))
    assert p._qs_active is True
    # Action handlers (BINDINGS path, e.g. arrow keys) must drop us out.
    p.action_cursor_down()
    assert p._qs_active is False


def test_ctrl_down_cycles_to_next_match(tmp_path: Path):
    # Names chosen so alphabetical order is unambiguous: ba < bz.
    p = _make_panel(tmp_path, ["alpha.txt", "ba.txt", "bz.md", "gamma.txt"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("b", "b"))
    assert p.entries[p.cursor].name == "ba.txt"
    p.on_key(_key("ctrl+down"))
    assert p.entries[p.cursor].name == "bz.md"
    p.on_key(_key("ctrl+down"))
    # Wraps back to first match.
    assert p.entries[p.cursor].name == "ba.txt"


def test_ctrl_up_cycles_to_prev_match(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "ba.txt", "bz.md"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("b", "b"))
    assert p.entries[p.cursor].name == "ba.txt"
    p.on_key(_key("ctrl+up"))
    # Walking backward wraps to the last match.
    assert p.entries[p.cursor].name == "bz.md"


def test_visible_rows_shrinks_when_active(tmp_path: Path):
    p = _make_panel(tmp_path, [f"f{i:02d}.txt" for i in range(10)])
    p._panel_size = (40, 12)  # height 12 -> 10 visible rows when idle (header + footer reserved)
    assert p._visible_rows() == 10
    p.on_key(_key("ctrl+s"))
    # Now an extra row is reserved for the search bar.
    assert p._visible_rows() == 9


def test_qs_bar_renders_at_bottom_when_active(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt", "beta.txt"])
    p._panel_size = (40, 5)
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("a", "a"))
    strip = p._render_qs_bar(40)
    text = "".join(seg.text for seg in strip)
    assert "Quick search:" in text
    assert "a_" in text


def test_synthetic_parent_row_never_matches(tmp_path: Path):
    p = _make_panel(tmp_path, ["alpha.txt"])
    p.cursor = 0
    p.on_key(_key("ctrl+s"))
    p.on_key(_key("a", "a"))
    # 'a' doesn't appear in '..', so it must land on alpha.txt.
    assert p.entries[p.cursor].name == "alpha.txt"
