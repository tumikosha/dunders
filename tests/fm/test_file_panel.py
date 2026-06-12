from pathlib import Path

import pytest

from dunders.fm.file_entry import FileEntry
from dunders.fm.file_panel import FilePanel
from dunders.fm.sort import SortOrder


def _make_tree(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("bb")
    (tmp_path / "c.txt").write_text("ccc")
    (tmp_path / "subdir").mkdir()
    return tmp_path


def test_panel_default_state(tmp_path: Path):
    _make_tree(tmp_path)
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    assert p.cwd == tmp_path
    assert p.cursor == 0
    assert p.row_offset == 0
    assert p.sort_order == SortOrder.NAME
    assert p.show_hidden is True


def test_panel_refresh_listing_loads_entries_with_parent(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "x.txt").write_text("")
    p = FilePanel(cwd=sub)
    p.refresh_listing()
    names = [e.name for e in p.entries]
    assert names[0] == ".."  # parent first
    assert "x.txt" in names


def test_panel_move_cursor_clamps_to_bounds(tmp_path: Path):
    _make_tree(tmp_path)
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    last = len(p.entries) - 1
    p.move_cursor(-100)
    assert p.cursor == 0
    p.move_cursor(+100)
    assert p.cursor == last


def test_panel_home_end(tmp_path: Path):
    _make_tree(tmp_path)
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.move_cursor(+5)
    p.home()
    assert p.cursor == 0
    p.end()
    assert p.cursor == len(p.entries) - 1


def test_panel_page_size_uses_widget_height_minus_header(tmp_path: Path):
    """page_down moves the cursor by `_visible_rows()`."""
    # Make many entries so paging is meaningful.
    for i in range(30):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 12)  # header + footer consume 2 rows -> 10 visible
    p.home()
    p.page_down()
    assert p.cursor == 10  # one page == 10 entries
    p.page_up()
    assert p.cursor == 0


def test_panel_scroll_follows_cursor(tmp_path: Path):
    for i in range(30):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 12)
    p.home()
    p.move_cursor(+15)
    # Cursor at index 15, only 10 visible rows -> scroll_offset should
    # have advanced to keep the cursor inside the viewport.
    assert p.row_offset >= 15 - (10 - 1)
    assert p.row_offset <= 15


def test_panel_set_sort_order_re_sorts_and_keeps_cursor_on_same_entry(tmp_path: Path):
    (tmp_path / "small.txt").write_text("a")
    (tmp_path / "big.txt").write_text("a" * 100)
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    # Position cursor on "small.txt" under NAME sort.
    target = next(i for i, e in enumerate(p.entries) if e.name == "small.txt")
    p.cursor = target
    p.set_sort_order(SortOrder.SIZE)
    new_target = next(i for i, e in enumerate(p.entries) if e.name == "small.txt")
    assert p.cursor == new_target


def test_panel_toggle_show_hidden_re_loads(tmp_path: Path):
    (tmp_path / "visible").write_text("")
    (tmp_path / ".hidden").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    # Dot-files are shown by default now.
    assert any(e.name == ".hidden" for e in p.entries)
    p.toggle_show_hidden()
    assert p.show_hidden is False
    assert all(e.name != ".hidden" for e in p.entries)


def test_panel_can_focus_is_true():
    """Phase 2 flips the Phase-1 can_focus = False."""
    assert FilePanel.can_focus is True


def test_panel_selection_starts_empty(tmp_path: Path):
    (tmp_path / "f.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    assert p.selection == set()
    assert p.selected_paths() == []


def test_panel_toggle_selection_marks_entry_and_advances_cursor(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    # Cursor starts at "..", advance to "a.txt"
    p.move_cursor(+1)
    a_idx = p.cursor
    a_path = p.entries[a_idx].path
    p.toggle_selection()
    assert a_path in p.selection
    assert p.cursor == a_idx + 1


def test_panel_toggle_selection_unmarks_when_already_selected(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    p.toggle_selection()  # mark a.txt; cursor advances (clamped if at end)
    p.cursor = a_idx  # direct repositioning bypasses cursor-advance behaviour
    p.toggle_selection()  # unmark
    assert p.selection == set()


def test_panel_toggle_selection_skips_parent_entry(tmp_path: Path):
    """Insert on '..' is a no-op (and still advances the cursor)."""
    (tmp_path / "a.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    assert p.entries[0].is_parent
    p.toggle_selection()  # cursor on ".."
    assert p.selection == set()
    assert p.cursor == 1  # cursor advanced anyway


def test_panel_selected_paths_returns_paths_in_listing_order(tmp_path: Path):
    """selected_paths is keyed off the current listing order, not the order
    in which entries were toggled. Mark c.txt first, then a.txt; result
    should still be [a, c]."""
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "c.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    c_idx = next(i for i, e in enumerate(p.entries) if e.name == "c.txt")
    p.cursor = c_idx
    p.toggle_selection()  # mark c.txt
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    p.toggle_selection()  # mark a.txt
    paths = p.selected_paths()
    assert [pp.name for pp in paths] == ["a.txt", "c.txt"]


def test_panel_clear_selection(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.move_cursor(+1)
    p.toggle_selection()
    p.clear_selection()
    assert p.selection == set()


def test_panel_refresh_drops_selection_for_vanished_entries(tmp_path: Path):
    f = tmp_path / "doomed"
    f.write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.move_cursor(+1)
    p.toggle_selection()
    assert f in p.selection
    f.unlink()
    p.refresh_listing()
    assert f not in p.selection


from textual.app import App, ComposeResult  # noqa: E402


class _FmHarness(App):
    """App harness that hosts a single FilePanel and captures its messages."""

    def __init__(self, panel: FilePanel) -> None:
        super().__init__()
        self.panel = panel
        self.path_changed: list[tuple[Path, Path]] = []
        self.item_activated: list[FileEntry] = []
        self.selection_changed: int = 0

    def compose(self) -> ComposeResult:
        yield self.panel

    def on_file_panel_path_changed(self, event: FilePanel.PathChanged) -> None:
        self.path_changed.append((event.old, event.new))

    def on_file_panel_item_activated(self, event: FilePanel.ItemActivated) -> None:
        self.item_activated.append(event.entry)

    def on_file_panel_selection_changed(self, _event: FilePanel.SelectionChanged) -> None:
        self.selection_changed += 1


@pytest.mark.asyncio
async def test_panel_descend_into_directory(tmp_path: Path):
    sub = tmp_path / "child"
    sub.mkdir()
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    # Find the row index of "child" and place the cursor there.
    idx = next(i for i, e in enumerate(p.entries) if e.name == "child")
    p.cursor = idx
    async with _FmHarness(p).run_test() as pilot:
        p.activate()
        await pilot.pause()
        assert p.cwd == sub
        assert p.entries[0].is_parent
        assert p.cursor == 0


@pytest.mark.asyncio
async def test_panel_ascend_to_parent_returns_cursor_to_origin_row(tmp_path: Path):
    sub = tmp_path / "child"
    sub.mkdir()
    p = FilePanel(cwd=sub)
    p.refresh_listing()
    async with _FmHarness(p).run_test() as pilot:
        p.ascend()
        await pilot.pause()
        assert p.cwd == tmp_path
        # Cursor should be on "child" — the dir we just left.
        assert p.entries[p.cursor].name == "child"


@pytest.mark.asyncio
async def test_panel_ascend_at_filesystem_root_is_noop():
    p = FilePanel(cwd=Path("/"))
    p.refresh_listing()
    async with _FmHarness(p).run_test() as pilot:
        p.ascend()
        await pilot.pause()
        assert p.cwd == Path("/")


@pytest.mark.asyncio
async def test_panel_descend_on_file_emits_item_activated(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    idx = next(i for i, e in enumerate(p.entries) if e.name == "x.txt")
    p.cursor = idx
    harness = _FmHarness(p)
    async with harness.run_test() as pilot:
        p.activate()
        await pilot.pause()
        # Path did not change; ItemActivated fired exactly once.
        assert p.cwd == tmp_path
        assert len(harness.item_activated) == 1
        assert harness.item_activated[0].name == "x.txt"


@pytest.mark.asyncio
async def test_panel_path_changed_message_carries_old_and_new(tmp_path: Path):
    sub = tmp_path / "child"
    sub.mkdir()
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.cursor = next(i for i, e in enumerate(p.entries) if e.name == "child")
    harness = _FmHarness(p)
    async with harness.run_test() as pilot:
        p.activate()
        await pilot.pause()
        assert harness.path_changed == [(tmp_path, sub)]


@pytest.mark.asyncio
async def test_panel_toggle_selection_emits_message(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.move_cursor(+1)
    harness = _FmHarness(p)
    async with harness.run_test() as pilot:
        p.toggle_selection()
        await pilot.pause()
        assert harness.selection_changed == 1



def _strip_to_text(strip) -> str:
    return "".join(seg.text for seg in strip)


@pytest.mark.asyncio
async def test_panel_renders_header_row(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        # Force a known size so render_line returns predictable widths.
        p._panel_size = (40, 11)
        p.refresh()  # re-render
        await pilot.pause()
        line0 = _strip_to_text(p.render_line(0))
        # Expected header columns: "Name", "Size", "Date"
        assert "Name" in line0
        assert "Size" in line0
        assert "Date" in line0


@pytest.mark.asyncio
async def test_panel_renders_entry_rows(tmp_path: Path):
    (tmp_path / "alpha.txt").write_text("hello")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 11)
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        # Row 1 == parent ".."  (since cwd has a parent)
        line1 = _strip_to_text(p.render_line(1))
        assert ".." in line1
        # The next visible entry should be "alpha.txt".
        line2 = _strip_to_text(p.render_line(2))
        assert "alpha.txt" in line2


@pytest.mark.asyncio
async def test_panel_renders_dir_size_marker(tmp_path: Path):
    (tmp_path / "child").mkdir()
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 11)
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        # Find the row index of "child" in the rendered output.
        for y in range(1, 11):
            line = _strip_to_text(p.render_line(y))
            if "child" in line:
                assert "<DIR>" in line
                break
        else:
            pytest.fail("did not find 'child' in any rendered row")


@pytest.mark.asyncio
async def test_panel_renders_parent_up_marker(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    p = FilePanel(cwd=sub)
    p.refresh_listing()
    p._panel_size = (40, 11)
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        line1 = _strip_to_text(p.render_line(1))  # row of ".."
        assert ".." in line1
        assert "<UP>" in line1


@pytest.mark.asyncio
async def test_panel_cursor_row_uses_reverse_style(tmp_path: Path):
    (tmp_path / "alpha.txt").write_text("")
    (tmp_path / "beta.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 11)
    p.cursor = 1  # second row in rendering (parent at row 1, alpha at row 2 if a parent exists)
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        # Map cursor index -> render row: render row = 1 (header) + (cursor - scroll_offset)
        cursor_render_row = 1 + (p.cursor - p.row_offset)
        strip = p.render_line(cursor_render_row)
        # At least one segment of the cursor row is rendered with reverse=True.
        assert any(getattr(seg.style, "reverse", False) for seg in strip if seg.style is not None)


@pytest.mark.asyncio
async def test_panel_selected_row_uses_yellow_bold_style(tmp_path: Path):
    (tmp_path / "alpha.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 11)
    # Move to alpha and toggle selection (cursor advances after toggle).
    idx = next(i for i, e in enumerate(p.entries) if e.name == "alpha.txt")
    p.cursor = idx
    p.toggle_selection()
    # Cursor advanced; the row of alpha is no longer the cursor row.
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        alpha_render_row = 1 + (idx - p.row_offset)
        strip = p.render_line(alpha_render_row)
        # At least one segment styled bold + color="yellow".
        styled = [seg for seg in strip if seg.style is not None]
        assert any(
            getattr(seg.style, "bold", False)
            and seg.style.color is not None
            and "yellow" in str(seg.style.color)
            for seg in styled
        )


@pytest.mark.asyncio
async def test_panel_keybinding_down_moves_cursor(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("down")
        assert p.cursor == 1


@pytest.mark.asyncio
async def test_panel_keybinding_enter_descends(tmp_path: Path):
    sub = tmp_path / "child"
    sub.mkdir()
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.cursor = next(i for i, e in enumerate(p.entries) if e.name == "child")
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert p.cwd == sub


@pytest.mark.asyncio
async def test_panel_keybinding_backspace_ascends(tmp_path: Path):
    sub = tmp_path / "child"
    sub.mkdir()
    p = FilePanel(cwd=sub)
    p.refresh_listing()
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("backspace")
        await pilot.pause()
        assert p.cwd == tmp_path


@pytest.mark.asyncio
async def test_panel_keybinding_insert_toggles_selection(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("insert")
        await pilot.pause()
        assert (tmp_path / "a.txt") in p.selection


@pytest.mark.asyncio
async def test_panel_keybinding_home_end_pgup_pgdn(tmp_path: Path):
    for i in range(20):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 12)  # header + footer consume 2 rows -> 10 visible
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("end")
        assert p.cursor == len(p.entries) - 1
        await pilot.press("home")
        assert p.cursor == 0
        await pilot.press("pagedown")
        assert p.cursor == 10
        await pilot.press("pageup")
        assert p.cursor == 0


def test_effective_targets_returns_cursor_when_no_selection(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    assert p.effective_targets() == [tmp_path / "a.txt"]


def test_effective_targets_returns_selection_when_non_empty(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    p.toggle_selection()
    # Selection has a.txt; cursor advanced to b.txt.
    targets = p.effective_targets()
    assert targets == [tmp_path / "a.txt"]


def test_effective_targets_skips_parent_when_only_parent_under_cursor(tmp_path: Path):
    """Cursor on '..' with empty selection returns []."""
    sub = tmp_path / "sub"
    sub.mkdir()
    p = FilePanel(cwd=sub)
    p.refresh_listing()
    assert p.entries[0].is_parent
    assert p.cursor == 0
    assert p.effective_targets() == []


def test_effective_targets_returns_selection_in_listing_order(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "c.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    c_idx = next(i for i, e in enumerate(p.entries) if e.name == "c.txt")
    p.cursor = c_idx
    p.toggle_selection()
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    p.toggle_selection()
    targets = p.effective_targets()
    assert [pp.name for pp in targets] == ["a.txt", "c.txt"]


@pytest.mark.asyncio
async def test_panel_enter_on_parent_row_positions_cursor_on_origin_dir(tmp_path: Path):
    """Pressing Enter on '..' should position the cursor on the directory
    we just left, same as Backspace (which is already covered)."""
    sub = tmp_path / "child"
    sub.mkdir()
    p = FilePanel(cwd=sub)
    p.refresh_listing()
    # Cursor on ".." (index 0).
    assert p.entries[0].is_parent
    async with _FmHarness(p).run_test() as pilot:
        p.activate()  # Enter on ".."
        await pilot.pause()
        assert p.cwd == tmp_path
        # Cursor lands on the dir we just left, not row 0.
        assert p.entries[p.cursor].name == "child"


@pytest.mark.asyncio
async def test_panel_keybinding_shift_down_marks_and_moves_down(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "c.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("shift+down")
        await pilot.press("shift+down")
        await pilot.pause()
        assert tmp_path / "a.txt" in p.selection
        assert tmp_path / "b.txt" in p.selection
        assert tmp_path / "c.txt" not in p.selection
        assert p.entries[p.cursor].name == "c.txt"


@pytest.mark.asyncio
async def test_panel_keybinding_shift_up_marks_and_moves_up(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "c.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    c_idx = next(i for i, e in enumerate(p.entries) if e.name == "c.txt")
    p.cursor = c_idx
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("shift+up")
        await pilot.press("shift+up")
        await pilot.pause()
        assert tmp_path / "c.txt" in p.selection
        assert tmp_path / "b.txt" in p.selection
        assert tmp_path / "a.txt" not in p.selection
        assert p.entries[p.cursor].name == "a.txt"


@pytest.mark.asyncio
async def test_panel_shift_arrow_unselects_when_already_selected(tmp_path: Path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    a_idx = next(i for i, e in enumerate(p.entries) if e.name == "a.txt")
    p.cursor = a_idx
    async with _FmHarness(p).run_test() as pilot:
        p.focus()
        await pilot.press("shift+down")
        await pilot.press("shift+up")
        await pilot.press("shift+down")
        await pilot.pause()
        assert tmp_path / "a.txt" not in p.selection
        assert tmp_path / "b.txt" in p.selection


def test_scan_populates_mode(tmp_path: Path):
    from dunders.fm.scan import scan_dir
    (tmp_path / "f.txt").write_text("x")
    entries = scan_dir(tmp_path, include_parent=False)
    f = next(e for e in entries if e.name == "f.txt")
    assert f.mode != 0          # raw st_mode came through
    assert f.mode & 0o170000    # has a file-type bits component


def test_format_mtime_short_is_11_chars():
    from dunders.fm.file_entry import format_mtime_short
    import time
    s = format_mtime_short(time.time())
    assert len(s) == 11         # "MM-DD HH:MM"
    assert s[2] == "-" and s[5] == " " and s[8] == ":"


def test_panel_default_view_mode_is_full(tmp_path: Path):
    from dunders.fm.panel_view import PanelViewMode
    p = FilePanel(cwd=tmp_path)
    assert p.view_mode == PanelViewMode.FULL


def test_visible_rows_reserves_header_and_footer(tmp_path: Path):
    _make_tree(tmp_path)
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 12)        # 12 - header - footer = 10
    assert p._visible_rows() == 10
    p._qs_active = True             # qs bar reserves one more
    assert p._visible_rows() == 9


def test_multicolumn_cursor_scrolls_by_column(tmp_path: Path):
    from dunders.fm.panel_view import PanelViewMode
    for i in range(40):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 12)        # 10 visible rows, BRIEF -> 2 cols = 20/page
    p.view_mode = PanelViewMode.BRIEF
    p.home()
    # Jump the cursor past the first 20-entry page; offset advances by a
    # whole column (10) at a time and stays a multiple of 10.
    p.cursor = 25
    p._ensure_cursor_visible()
    assert p.row_offset % 10 == 0
    assert p.row_offset <= 25 < p.row_offset + 20


def test_multicolumn_snaps_unaligned_row_offset(tmp_path: Path):
    from dunders.fm.panel_view import PanelViewMode
    for i in range(40):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 12)        # rows = 10
    p.view_mode = PanelViewMode.BRIEF
    p.row_offset = 5                # simulate leftover from a single-column mode
    p.cursor = 7
    p._ensure_cursor_visible()
    assert p.row_offset % 10 == 0   # snapped to a column boundary


def test_multicol_click_index_clamps_to_last_column(tmp_path: Path):
    from dunders.fm.panel_view import PanelViewMode
    for i in range(30):
        (tmp_path / f"f{i:02d}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (82, 12)              # rows = 10
    p.view_mode = PanelViewMode.MEDIUM   # k = 3
    # width 82, k=3 -> col_w = (82-2)//3 = 26; col stride = 27.
    # x=81 sits in the right-edge pad; raw 81//27 == 3 (out of range) -> must clamp to 2.
    idx = p._multicol_index_at(81, 1, 82)
    rows = p._visible_rows()
    # clamped col 2 on visual row 0:
    assert idx == p.row_offset + 2 * rows + 0
    # and never the unclamped col-3 value:
    assert idx != p.row_offset + 3 * rows + 0


@pytest.mark.asyncio
async def test_footer_shows_full_cursor_name(tmp_path: Path):
    long = "a_very_long_file_name_that_truncates.txt"
    (tmp_path / long).write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (24, 12)            # footer lands at render_line(11)
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        p.cursor = next(i for i, e in enumerate(p.entries) if e.name == long)
        footer = _strip_to_text(p.render_line(11))
        assert long in footer          # full, untruncated, even though body clips it


@pytest.mark.asyncio
async def test_detailed_mode_row_shows_attrs(tmp_path: Path):
    from dunders.fm.panel_view import PanelViewMode
    (tmp_path / "f.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (50, 12)
    p.view_mode = PanelViewMode.DETAILED
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        rows = [_strip_to_text(p.render_line(y)) for y in range(1, 11)]
        assert any("-rw" in r or "rw-" in r for r in rows)


@pytest.mark.asyncio
async def test_brief_mode_packs_two_columns(tmp_path: Path):
    from dunders.fm.panel_view import PanelViewMode
    for i in range(6):
        (tmp_path / f"f{i}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 5)            # reserved 2 -> rows = 3 visible
    p.view_mode = PanelViewMode.BRIEF
    async with _FmHarness(p).run_test() as pilot:
        await pilot.pause()
        rows = p._visible_rows()       # 3
        row1 = _strip_to_text(p.render_line(1))   # visual row 0
        # Column-major: visual row 0 shows entries[0] (col 0) and entries[rows] (col 1).
        assert p.entries[0].name in row1
        assert p.entries[rows].name in row1


@pytest.mark.asyncio
async def test_brief_and_medium_differ_for_short_listing(tmp_path: Path):
    # Regression: with the column height fixed at the full panel height, a
    # short listing stacked entirely in column 0 and Brief (2 cols) / Medium
    # (3 cols) rendered identically. The column height must collapse to
    # ceil(n / k) so the extra columns are actually used.
    from dunders.fm.panel_view import PanelViewMode
    for n in ("alpha", "beta", "gamma", "delta"):
        (tmp_path / f"{n}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 24)           # tall panel: visible_rows = 22
    async with _FmHarness(p).run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        p.view_mode = PanelViewMode.BRIEF
        brief_row1 = _strip_to_text(p.render_line(1))
        p.view_mode = PanelViewMode.MEDIUM
        medium_row1 = _strip_to_text(p.render_line(1))
        assert brief_row1 != medium_row1
        assert len(brief_row1.split()) >= 2     # 5 entries, k=2 -> 2 columns
        assert len(medium_row1.split()) >= 3    # 5 entries, k=3 -> 3 columns


@pytest.mark.asyncio
async def test_multicol_no_duplicate_rows_below_column_height(tmp_path: Path):
    # Regression: rows below the (collapsed) column height must not re-render
    # the next column's entries. They still carry the column separators (which
    # run to the bottom of the panel), so the row is spaces + separators only.
    from dunders.fm.panel_view import COL_SEP, PanelViewMode
    for n in ("alpha", "beta", "gamma", "delta"):
        (tmp_path / f"{n}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 24)
    p.view_mode = PanelViewMode.BRIEF
    async with _FmHarness(p).run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        col_h = p._multicol_col_height()        # ceil(5 / 2) = 3
        below = _strip_to_text(p.render_line(1 + col_h))  # first row past the column
        assert set(below) <= {" ", COL_SEP}     # no entry text, separators only
        assert COL_SEP in below                  # separator runs to the bottom


@pytest.mark.asyncio
async def test_multicol_columns_separated_by_vertical_bar(tmp_path: Path):
    from dunders.fm.panel_view import COL_SEP, PanelViewMode
    for n in ("alpha", "beta", "gamma", "delta"):
        (tmp_path / f"{n}.txt").write_text("")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 24)
    p.view_mode = PanelViewMode.BRIEF
    async with _FmHarness(p).run_test(size=(40, 24)) as pilot:
        await pilot.pause()
        row1 = _strip_to_text(p.render_line(1))   # a row with two populated columns
        assert COL_SEP in row1


@pytest.mark.asyncio
async def test_header_labels_are_centered(tmp_path: Path):
    from dunders.fm.panel_view import PanelViewMode, name_col_width
    (tmp_path / "f.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p._panel_size = (40, 10)
    p.view_mode = PanelViewMode.SHORT
    async with _FmHarness(p).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        header = _strip_to_text(p.render_line(0))
        ncol = name_col_width(PanelViewMode.SHORT, p.size.width)
        name_cell = header[:ncol]
        assert name_cell.strip() == "Name"
        assert name_cell == "Name".center(ncol)   # centred, not left/right aligned


def test_footer_background_is_dimmed(tmp_path):
    # The bottom full-name line uses reverse video but dimmed, so its
    # background reads as less bright than a full reverse bar.
    p = FilePanel(cwd=tmp_path)
    strip = p._render_footer(20)
    seg = list(strip)[0]
    assert seg.style is not None
    assert seg.style.reverse is True
    assert seg.style.dim is True


def test_enclosing_window_is_none_when_unparented(tmp_path: Path):
    # A standalone (unmounted) panel has no enclosing windowing Window.
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    assert p._enclosing_window() is None
    # And it is therefore not the active panel.
    assert p._is_active_panel is False


class _FakeScroll:
    """Duck-typed stand-in for a Textual MouseScroll event."""

    def __init__(self) -> None:
        self.stopped = False
        self.prevented = False

    def stop(self) -> None:
        self.stopped = True

    def prevent_default(self) -> None:
        self.prevented = True


def test_wheel_moves_cursor_by_step(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    assert p.cursor == 0
    p._wheel(3)
    assert p.cursor == 3
    p._wheel(-3)
    assert p.cursor == 0


def test_wheel_clamps_at_bounds(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    last = len(p.entries) - 1
    p._wheel(-3)               # already at top
    assert p.cursor == 0
    p.end()                    # jump to bottom
    p._wheel(3)                # past the end
    assert p.cursor == last


def test_wheel_on_minimal_listing_does_not_crash(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    p = FilePanel(cwd=sub)     # only the synthetic ".." row
    p.refresh_listing()
    p._wheel(3)
    p._wheel(-3)
    assert p.cursor == 0


def test_scroll_down_handler_moves_cursor_and_stops_event(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    ev = _FakeScroll()
    p._on_mouse_scroll_down(ev)
    assert p.cursor == 3
    assert ev.stopped and ev.prevented


def test_scroll_up_handler_moves_cursor_and_stops_event(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.end()
    last = p.cursor
    ev = _FakeScroll()
    p._on_mouse_scroll_up(ev)
    assert p.cursor == last - 3
    assert ev.stopped and ev.prevented
