from pathlib import Path

import pytest

from dunders.core.vfs import VfsPath
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
    assert a_path in p.selected_paths()
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
    assert f in p.selected_paths()
    f.unlink()
    p.refresh_listing()
    assert f not in p.selected_paths()


from textual.app import App, ComposeResult  # noqa: E402


class _FmHarness(App):
    """App harness that hosts a single FilePanel and captures its messages."""

    def __init__(self, panel: FilePanel) -> None:
        super().__init__()
        self.panel = panel
        self.path_changed: list[tuple[Path, Path]] = []
        self.item_activated: list[FileEntry] = []
        self.selection_changed: int = 0
        self.scheme_changed: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield self.panel

    def on_file_panel_path_changed(self, event: FilePanel.PathChanged) -> None:
        self.path_changed.append((event.old, event.new))

    def on_file_panel_item_activated(self, event: FilePanel.ItemActivated) -> None:
        self.item_activated.append(event.entry)

    def on_file_panel_selection_changed(self, _event: FilePanel.SelectionChanged) -> None:
        self.selection_changed += 1

    def on_file_panel_scheme_changed(self, event: FilePanel.SchemeChanged) -> None:
        self.scheme_changed.append((event.old_scheme, event.new_scheme))


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
async def test_scheme_change_emits_scheme_changed_message(tmp_path: Path):
    # Navigating across a VFS scheme boundary (file: → a provider scheme) must
    # emit SchemeChanged so the app recomputes provider-scoped menus (e.g. the
    # "Database" menu / SQL console). Window focus does NOT change on a same-panel
    # cwd switch, so this is the only signal the menu bar gets.
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    harness = _FmHarness(p)
    async with harness.run_test() as pilot:
        p._change_cwd_loc(VfsPath(scheme="db", root="sqlite:///x.db", parts=()))
        await pilot.pause()
        assert harness.scheme_changed == [("file", "db")]
        # Returning to a local dir emits the reverse edge (menu must disappear).
        p._change_cwd_loc(VfsPath.local(tmp_path))
        await pilot.pause()
        assert harness.scheme_changed[-1] == ("db", "file")


@pytest.mark.asyncio
async def test_same_scheme_navigation_emits_no_scheme_changed(tmp_path: Path):
    # Moving between two local dirs (file: → file:) must NOT emit SchemeChanged —
    # the provider menu set is unchanged, recomputing it every keystroke is waste.
    sub = tmp_path / "child"
    sub.mkdir()
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    harness = _FmHarness(p)
    async with harness.run_test() as pilot:
        p._change_cwd_loc(VfsPath.local(sub))
        await pilot.pause()
        assert harness.scheme_changed == []


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
        assert (tmp_path / "a.txt") in p.selected_paths()


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
        assert tmp_path / "a.txt" in p.selected_paths()
        assert tmp_path / "b.txt" in p.selected_paths()
        assert tmp_path / "c.txt" not in p.selected_paths()
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
        assert tmp_path / "c.txt" in p.selected_paths()
        assert tmp_path / "b.txt" in p.selected_paths()
        assert tmp_path / "a.txt" not in p.selected_paths()
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
        assert tmp_path / "a.txt" not in p.selected_paths()
        assert tmp_path / "b.txt" in p.selected_paths()


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


# ---------------------------------------------------------------------------
# Phase 3: inline clickable provider action icons (coordinate hit-testing)
# ---------------------------------------------------------------------------


async def test_action_cluster_spans_on_cursor_row(monkeypatch, tmp_path):
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        panel.entries = [FileEntry(loc=VfsPath(scheme="docker", root="", parts=("web",)),
                                   name="web", size=0, mtime=0.0, is_dir=True,
                                   extra={"docker.state": "running"})]
        panel.cursor = 0
        panel.view_mode = PanelViewMode.FULL
        spans = panel._action_spans(0, 80)
        assert spans, "container row should show an action cluster"
        ids = {a.id for (_s, _e, a) in spans}
        assert "docker.stop" in ids
        # cluster is always visible on a container row — not gated on the cursor
        panel.cursor = -1
        assert panel._action_spans(0, 80), "cluster shows regardless of cursor"
        # the '..' parent row never shows a cluster
        panel.entries.insert(0, FileEntry(
            loc=VfsPath(scheme="docker", root="", parts=()),
            name="..", size=0, mtime=0.0, is_dir=True))
        assert panel._action_spans(0, 80) == []


async def test_click_on_action_icon_runs_action(monkeypatch, tmp_path):
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    ran = {}
    monkeypatch.setattr(DundersApp, "_run_provider_action",
                        lambda self, action, targets=None: ran.update(id=action.id, targets=targets))
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        panel.entries = [FileEntry(loc=VfsPath(scheme="docker", root="", parts=("web",)),
                                   name="web", size=0, mtime=0.0, is_dir=True,
                                   extra={"docker.state": "running"})]
        panel.cursor = 0
        panel.view_mode = PanelViewMode.FULL
        # Pin a deterministic width via the established test-size hook so the
        # span geometry and the click helper agree (panel.size is 0×0 here).
        panel._panel_size = (80, 25)
        width = panel._panel_size[0]
        spans = panel._action_spans(0, width)
        first_start, _e, first_action = spans[0]
        handled = panel._maybe_run_action_click(first_start, 0)
        assert handled is True
        assert ran.get("id") == first_action.id
        assert ran.get("targets") == [panel.entries[0].loc]


async def test_action_cluster_on_every_container_row_reflects_state(monkeypatch, tmp_path):
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="", parts=("web",)), name="web",
                      size=0, mtime=0.0, is_dir=True,
                      extra={"docker.state": "running"}),
            FileEntry(loc=VfsPath(scheme="docker", root="", parts=("db",)), name="db",
                      size=0, mtime=0.0, is_dir=True,
                      extra={"docker.state": "exited"}),
        ]
        panel.cursor = 0
        panel.view_mode = PanelViewMode.FULL
        # Both rows show a cluster (cursor-independent); each reflects its state.
        running_ids = {a.id for *_x, a in panel._action_spans(0, 80)}
        stopped_ids = {a.id for *_x, a in panel._action_spans(1, 80)}
        assert "docker.stop" in running_ids and "docker.start" not in running_ids
        assert "docker.start" in stopped_ids and "docker.stop" not in stopped_ids


async def test_docker_index_header_replaces_size_date_with_actions(monkeypatch, tmp_path):
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        # Container index (v2 loc) → header shows "Actions", not Size/Date.
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("containers",))
        panel.entries = [FileEntry(loc=VfsPath(scheme="docker", root="", parts=("containers", "container:web")),
                                   name="web", size=0, mtime=0.0, is_dir=True,
                                   extra={"docker.state": "running"})]
        head = "".join(seg.text for seg in panel._render_header(80))
        # "Actions" label may be truncated to "Action" when the cluster is
        # narrow (max_applicable × _ACTION_CELL < 7 chars).
        assert "Action" in head
        assert "Size" not in head and "Date" not in head
        # Inside a container's FS (v2 loc, cfs level) → normal Size/Date header.
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("containers", "container:web"))
        panel.entries = [FileEntry(loc=VfsPath(scheme="docker", root="", parts=("containers", "container:web", "etc")),
                                   name="etc", size=4096, mtime=1.0, is_dir=True)]
        head2 = "".join(seg.text for seg in panel._render_header(80))
        assert "Size" in head2 and "Date" in head2 and "Actions" not in head2


async def test_docker_volume_usedby_column_left_aligned(monkeypatch, tmp_path):
    # The "Used by" column value renders left-aligned (not centred like the
    # numeric/enum columns), so short container names hug the left edge.
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        panel._panel_size = (100, 25)
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("volumes",))
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="",
                                  parts=("volumes", "volume:data")),
                      name="data", size=0, mtime=0.0, is_dir=True,
                      extra={"docker.size": "1MB", "docker.driver": "local",
                             "docker.usedby": "web"}),
        ]
        layout = panel._provider_layout(100)
        assert layout is not None
        # Locate the "Used by" column's x-range and slice it out of the row.
        cols, _name_w, ranges, _bx = layout
        (x0, x1, _col) = next(r for r in ranges if r[2].key == "docker.usedby")
        row = "".join(s.text for s in panel._render_entry_row(0, 100))
        cell = row[x0:x1]
        assert cell.startswith("web")           # left-aligned, not "   web   "
        assert cell.strip() == "web"


async def test_docker_action_icons_highlight_on_hover(monkeypatch, tmp_path):
    # Hovering an action icon marks it and repaints that cell with the highlight
    # style; leaving the panel clears it.
    from types import SimpleNamespace

    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        panel._panel_size = (80, 25)
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("containers",))
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="",
                                  parts=("containers", "container:web")),
                      name="web", size=0, mtime=0.0, is_dir=True,
                      extra={"docker.state": "running"}),
        ]
        spans = panel._action_spans(0, 80)
        assert spans
        start = spans[0][0]

        # Hit-test: viewport row 1 is entry 0 (header is row 0). On the icon →
        # (idx, start); off the cluster (Name field) → None.
        assert panel._action_at(start, 1) == (0, start)
        assert panel._action_at(0, 1) is None
        # Header row and footer are not hoverable.
        assert panel._action_at(start, 0) is None

        # A mouse move over the icon sets the hover; leaving clears it.
        panel._hover_action = None
        panel.on_mouse_move(SimpleNamespace(x=start, y=1))
        assert panel._hover_action == (0, start)

        # The hovered cell repaints differently from the un-hovered render.
        panel._hover_action = None
        plain = [(s.text, s.style) for s in panel._render_entry_row(0, 80)]
        panel._hover_action = (0, start)
        hot = [(s.text, s.style) for s in panel._render_entry_row(0, 80)]
        assert plain != hot
        # The highlight carries a real style (menu.item.active → has a bg).
        hstyle = panel._action_hover_style(panel._base_style())
        assert hstyle.bgcolor is not None

        panel.on_leave(SimpleNamespace())
        assert panel._hover_action is None


async def test_docker_status_column_layout_header_and_sort(monkeypatch, tmp_path):
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("containers",))
        panel.view_mode = PanelViewMode.FULL
        panel._panel_size = (80, 25)

        db = FileEntry(loc=VfsPath(scheme="docker", root="", parts=("db",)), name="db",
                       size=0, mtime=0.0, is_dir=True,
                       extra={"docker.state": "exited", "glyph": "■", "glyph_role": "muted"})
        web = FileEntry(loc=VfsPath(scheme="docker", root="", parts=("web",)), name="web",
                        size=0, mtime=0.0, is_dir=True,
                        extra={"docker.state": "running", "glyph": "▶", "glyph_role": "success"})
        panel.entries = [web, db]

        # The provider contributes Image + Status columns; the "S" state column
        # was merged into the right-side Actions cluster.
        layout = panel._provider_layout(80)
        assert layout is not None
        cols = layout[0]
        assert [c.label for c in cols] == ["Image", "Status"]

        # Header shows Name + Image + Status + Actions, no Size/Date and no
        # standalone S column header.
        head = "".join(s.text for s in panel._render_header(80))
        assert "Name" in head and "Image" in head and "Status" in head
        assert "Action" in head
        assert "Size" not in head and "Date" not in head

        # Running-first order now comes from the provider default sort (there is
        # no clickable state header anymore).
        ds = panel._provider_default_sort(panel.cwd_loc)
        assert ds is not None and ds[0] == "docker.state"
        panel._sort_key_id, panel._sort_key = ds[0], ds[1]
        panel.sort_descending = False
        panel._apply_rows([db, web])
        assert [e.name for e in panel.entries] == ["web", "db"]

        # By name, honouring direction (bypasses the dirs-alphabetical rule).
        panel._sort_key_id, panel._sort_key = "name", lambda e: e.name.lower()
        panel.sort_descending = False
        panel._apply_rows([db, web])
        assert [e.name for e in panel.entries] == ["db", "web"]
        panel.sort_descending = True
        panel._apply_rows([db, web])
        assert [e.name for e in panel.entries] == ["web", "db"]

        # _sort_active sets id/key and flips direction on re-click (refresh stubbed).
        monkeypatch.setattr(panel, "refresh_listing", lambda *, focus_loc=None: None)
        panel._sort_key_id = None
        panel._sort_active("name", lambda e: e.name.lower())
        assert panel._sort_key_id == "name" and panel.sort_descending is False
        panel._sort_active("name", lambda e: e.name.lower())
        assert panel.sort_descending is True

        # The state glyphs now render in the Actions cluster area (not a column).
        panel._sort_key_id, panel._sort_key = None, None
        panel._apply_rows([web, db])
        rows_text = "".join(
            s.text
            for i in range(len(panel.entries))
            for s in panel._render_entry_row(i, 80)
        )
        assert "▶" in rows_text and "■" in rows_text

        # The glyph sits at the head of the cluster, one cell left of the first
        # action icon, and is PASSIVE: a click on it runs no action, while a
        # click on the first action icon still resolves to that action.
        spans = panel._action_spans(0, 80)
        assert spans
        glyph_x = spans[0][0] - panel._ACTION_CELL
        assert glyph_x >= layout[3]  # within the reserved cluster area
        runs = []
        monkeypatch.setattr(app, "_run_provider_action",
                            lambda action, targets: runs.append(action.id))
        assert panel._maybe_run_action_click(spans[0][0], 0) is True
        assert panel._maybe_run_action_click(glyph_x, 0) is False
        assert len(runs) == 1


async def test_docker_grouping_levels_hide_epoch_date_column(monkeypatch, tmp_path):
    # Regression: at pure grouping levels (top: sections; a compose project's
    # services) Docker entries carry mtime=0, so the default "Date" column would
    # only ever show the epoch (1970-01-01). Those levels render Name-only.
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        panel._panel_size = (80, 25)

        # Top level: the four section directories carry mtime=0. The header must
        # never show the epoch "Date" column (images/networks/volumes contribute
        # prune/pull actions, so the top index uses the Actions cluster; either
        # way no Size/Date leaks in).
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="", parts=(s,)),
                      name=s, size=0, mtime=0.0, is_dir=True)
            for s in ("containers", "images", "networks", "volumes")
        ]
        assert panel._hide_default_columns() is True
        head = "".join(seg.text for seg in panel._render_header(80))
        assert "Size" not in head and "Date" not in head
        rows = "".join(
            s.text
            for i in range(len(panel.entries))
            for s in panel._render_entry_row(i, 80)
        )
        assert "containers" in rows and "volumes" in rows
        assert "1970" not in rows

        # A compose project's services list is a pure grouping level (no columns,
        # no applicable actions) → Name-only, so no epoch Date anywhere.
        panel.cwd_loc = VfsPath(scheme="docker", root="",
                                parts=("compose:myproj",))
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="",
                                  parts=("compose:myproj", "service:web")),
                      name="web", size=0, mtime=0.0, is_dir=True),
        ]
        assert panel._hide_default_columns() is True
        assert panel._shows_action_column(80) is False
        head2 = "".join(seg.text for seg in panel._render_header(80))
        assert "Name" in head2 and "Date" not in head2 and "Size" not in head2
        srows = "".join(s.text for s in panel._render_entry_row(0, 80))
        assert "web" in srows and "1970" not in srows
        # A blank row below the listing is plain (no stray Size/Date bars).
        blank = "".join(s.text for s in panel._render_entry_row(99, 80))
        assert blank.strip() == ""

        # Inside a container's filesystem (real files, real dates) the default
        # Size/Date columns stay.
        panel.cwd_loc = VfsPath(scheme="docker", root="",
                                parts=("containers", "container:web"))
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="",
                                  parts=("containers", "container:web", "etc")),
                      name="etc", size=4096, mtime=1.0, is_dir=True),
        ]
        assert panel._hide_default_columns() is False
        head3 = "".join(seg.text for seg in panel._render_header(80))
        assert "Size" in head3 and "Date" in head3


async def test_docker_wide_panel_action_cluster_no_overflow(monkeypatch, tmp_path):
    # Regression: the Actions cluster (incl. the merged state glyph) must stay
    # within the panel width — rows and header fill exactly `width`, no spill.
    # Width is measured in terminal CELLS (not chars): a 2-cell emoji icon such
    # as 🧹 (Prune) is one char but two columns, and only the cell count reflects
    # what actually reaches the panel edge / window border.
    from rich.cells import cell_len

    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("containers",))
        panel.view_mode = PanelViewMode.FULL
        WIDTH = 120
        panel._panel_size = (WIDTH, 25)
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="", parts=("web",)), name="web",
                      size=0, mtime=0.0, is_dir=True,
                      extra={"docker.state": "running", "glyph": "▶", "glyph_role": "success"}),
            FileEntry(loc=VfsPath(scheme="docker", root="", parts=("db",)), name="db",
                      size=0, mtime=0.0, is_dir=True,
                      extra={"docker.state": "exited", "glyph": "■", "glyph_role": "muted"}),
        ]
        layout = panel._provider_layout(WIDTH)
        assert layout is not None
        assert layout[1] >= 1  # name_w stays positive
        for i in range(len(panel.entries)):
            row_len = sum(cell_len(s.text) for s in panel._render_entry_row(i, WIDTH))
            assert row_len == WIDTH, f"row {i} width {row_len} != {WIDTH}"
            for start, end, _a in panel._action_spans(i, WIDTH):
                assert 0 <= start < end <= WIDTH
        head_len = sum(cell_len(s.text) for s in panel._render_header(WIDTH))
        assert head_len == WIDTH


async def test_docker_wide_image_row_cell_width_no_doubled_border(monkeypatch, tmp_path):
    """Regression: an image row exposes the Prune action whose icon is the
    2-CELL emoji 🧹.  The action cell was sized by *characters* (`icon + " "`,
    2 chars) but rendered as 3 terminal cells, making the whole strip one cell
    wider than the panel.  On a wide (maximized) panel that overflow spilled
    onto the window's right border, drawing a second full-height vertical line
    (a doubled frame).

    The row's rendered strip must be exactly WIDTH *cells* (not chars), so no
    row overruns the panel edge regardless of emoji icon widths.
    """
    from rich.cells import cell_len

    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        WIDTH = 120
        panel._panel_size = (WIDTH, 25)
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("images",))
        panel.entries = [
            FileEntry(
                loc=VfsPath(scheme="docker", root="", parts=("images", "image:abc")),
                name="nginx:latest", size=0, mtime=0.0, is_dir=False,
                extra={"docker.kind": "image", "docker.size": "142MB",
                       "docker.created": "2 days ago"},
            ),
            FileEntry(
                loc=VfsPath(scheme="docker", root="", parts=("images", "image:def")),
                name="python:3.12", size=0, mtime=0.0, is_dir=False,
                extra={"docker.kind": "image", "docker.size": "1.02GB",
                       "docker.created": "5 days ago"},
            ),
        ]

        # Sanity: all action icons on these rows are single-cell (the emoji 🧹
        # Prune icon was the original culprit and has been replaced with ∷).
        provider = panel._registry.resolve(panel.cwd_loc)
        icons = [a.icon for a in provider.actions()
                 if a.applies_to(panel.entries[0])]
        assert all(cell_len(i or "") <= 1 for i in icons), (
            "expected all action icons to be single-cell after emoji-icon fix"
        )

        for i in range(len(panel.entries)):
            strip = panel._render_entry_row(i, WIDTH)
            cells = sum(cell_len(s.text) for s in strip)
            assert cells == WIDTH, (
                f"row {i} rendered {cells} cells != panel width {WIDTH} "
                "— a too-wide icon overruns the right border (doubled frame)"
            )
            # Belt-and-braces: Strip's own cell accounting must agree.
            assert strip.cell_length == WIDTH


async def test_synthetic_wide_glyph_action_icon_absorbed_by_set_cell_size(monkeypatch, tmp_path):
    """Revert-proof guard for the action-cell sizing.

    A SYNTHETIC ProviderAction whose icon is the 2-CELL emoji 🧹 is injected
    into the provider's action set.  When it renders in the right-side cluster
    the row must stay exactly the panel width in *cells*: ``set_cell_size``
    absorbs the extra column.  If the action cell were reverted to plain
    character-slicing (``(icon + " ")[:2]`` = 2 chars but 3 cells) the strip
    would be WIDTH+1 cells and overrun the window's right border (doubled frame),
    so this assertion FAILS on that regression.
    """
    from rich.cells import cell_len

    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.core.vfs.provider import ProviderAction
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        WIDTH = 120
        panel._panel_size = (WIDTH, 25)
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("containers",))
        panel.entries = [
            FileEntry(loc=VfsPath(scheme="docker", root="", parts=("web",)), name="web",
                      size=0, mtime=0.0, is_dir=True,
                      extra={"docker.state": "running", "glyph": "▶", "glyph_role": "success"}),
        ]

        wide = ProviderAction(id="test.wide", label="W", icon="🧹",
                              run=lambda locs: None, applies_to=lambda e: True)
        provider = panel._registry.resolve(panel.cwd_loc)
        monkeypatch.setattr(provider, "actions", lambda: [wide])
        assert cell_len(wide.icon) == 2  # sanity: the glyph really is 2 cells

        strip = panel._render_entry_row(0, WIDTH)
        row_text = "".join(s.text for s in strip)
        assert "🧹" in row_text, "the injected wide glyph must render in the cluster"
        # The 2-cell glyph is absorbed: the strip is exactly WIDTH cells, no spill.
        assert sum(cell_len(s.text) for s in strip) == WIDTH
        assert strip.cell_length == WIDTH
        # Header stays width-exact too.
        assert sum(cell_len(s.text) for s in panel._render_header(WIDTH)) == WIDTH


async def test_docker_name_column_not_collapsed_by_large_action_set(monkeypatch, tmp_path):
    """Regression: when a provider has many total actions but only a few apply
    per entry, _provider_layout must reserve width for the *applicable* cluster
    (max across entries), not len(acts()).  The old code caused name_w == 1.

    Arithmetic (images listing, width=55):
      COL_SEP = "│" (len 1); images columns: Size(10), Created(16)
      block_w = (1+10) + (1+16) = 28
      total actions = 16  →  old buttons_w = 32  →  old name_w = max(1, 55-28-32) = 1
      applicable per image entry: image.remove + prune + pull + run = 4
                                   new buttons_w = 8  →  new name_w = max(1, 55-28-8) = 19
    """
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode

    _WIDTH = 55  # narrow enough that old formula collapses name_w to 1

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        panel._panel_size = (_WIDTH, 25)

        # Realistic image entries: parts use the "image:" token as produced by
        # DockerProvider._scan_images, NOT bare "nginx:latest".
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("images",))
        panel.entries = [
            FileEntry(
                loc=VfsPath(scheme="docker", root="", parts=("images", "image:abc123")),
                name="nginx:latest", size=0, mtime=0.0, is_dir=False,
                extra={"docker.kind": "image"},
            ),
            FileEntry(
                loc=VfsPath(scheme="docker", root="", parts=("images", "image:def456")),
                name="python:3.12", size=0, mtime=0.0, is_dir=False,
                extra={"docker.kind": "image"},
            ),
        ]

        layout = panel._provider_layout(_WIDTH)
        assert layout is not None, "images listing must produce a provider layout"

        _cols, name_w, _ranges, buttons_x0 = layout

        # Primary guard: name_w must be comfortably large.
        # Old formula: max(1, 55-28-32) = 1.  New formula: max(1, 55-28-8) = 19.
        assert name_w >= 15, (
            f"name_w={name_w} — Name column collapsed. "
            "The total action count must not be used to reserve button width; "
            "only the max applicable count per entry should."
        )

        # Precision guard: actual reserved button area == max_applicable * _ACTION_CELL.
        # No try/except — a failure here IS the regression and must propagate.
        provider = panel._registry.resolve(panel.cwd_loc)
        acts_list = provider.actions()
        total_actions = len(acts_list)
        max_applicable = max(
            sum(1 for a in acts_list if a.applies_to(e))
            for e in panel.entries
            if not getattr(e, "is_parent", False)
        )
        assert total_actions != max_applicable, (
            "Test setup error: total action count equals max applicable count — "
            "old and new formula are indistinguishable at these params."
        )
        # buttons_x0 = name_w + block_w; actual button area = width - buttons_x0.
        actual_buttons_w = _WIDTH - buttons_x0
        new_buttons_w = max_applicable * panel._ACTION_CELL
        old_buttons_w = total_actions * panel._ACTION_CELL
        assert actual_buttons_w == new_buttons_w, (
            f"Button area {actual_buttons_w} != max_applicable*cell ({new_buttons_w}). "
            f"Looks like total*cell ({old_buttons_w}) was used instead."
        )


async def test_failed_entry_reverts_to_previous_location(monkeypatch, tmp_path):
    # Entering a stopped container fails to list → revert to the index with the
    # cursor on the container we tried to open (never stranded in an empty panel).
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        index = VfsPath(scheme="docker", root="", parts=())
        stopped = VfsPath(scheme="docker", root="", parts=("db",))
        panel.cwd_loc = stopped
        panel._return_to = index
        landed = []
        monkeypatch.setattr(panel, "refresh_listing",
                            lambda *, focus_loc=None: landed.append(focus_loc))
        assert panel._maybe_revert(OSError("not running")) is True
        assert panel.cwd_loc == index
        assert panel._return_to is None
        assert landed == [stopped]  # cursor returns to the container row


async def test_revert_restores_sort_state(monkeypatch, tmp_path):
    # A bounced entry must leave the index's sort exactly as it was.
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        monkeypatch.setattr(panel, "refresh_listing", lambda *, focus_loc=None: None)
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        panel._sort_key_id = "docker.state"
        panel._sort_key = lambda e: 0
        panel.sort_descending = True
        # Enter a container → sort is reset for the (would-be) new listing…
        panel._change_cwd_loc(VfsPath(scheme="docker", root="", parts=("db",)))
        assert panel._sort_key_id is None
        # …but the bounce restores it.
        assert panel._maybe_revert(OSError("not running")) is True
        assert panel._sort_key_id == "docker.state"
        assert panel.sort_descending is True


async def test_sort_remembered_across_container_roundtrip(monkeypatch, tmp_path):
    # The index keeps its "S" sort after diving into a container and back out.
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        monkeypatch.setattr(panel, "refresh_listing", lambda *, focus_loc=None: None)
        index = VfsPath(scheme="docker", root="", parts=("containers",))
        container = VfsPath(scheme="docker", root="", parts=("containers", "container:web"))
        panel.cwd_loc = index
        ds = panel._provider_default_sort(index)             # state sort (no header)
        assert ds is not None and ds[0] == "docker.state"

        panel._sort_active(ds[0], ds[1])                     # sort index by state
        assert panel._sort_key_id == "docker.state"
        panel._change_cwd_loc(container)                     # dive in → reset
        assert panel._sort_key_id is None
        panel._change_cwd_loc(index)                         # back → restored
        assert panel._sort_key_id == "docker.state"
        assert panel._sort_key is not None


async def test_ascend_uses_provider_parent_entry(monkeypatch, tmp_path):
    # From inside a Docker container, Backspace must return to the container
    # index (the provider's ".." entry), not escape to the local filesystem.
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        index = VfsPath(scheme="docker", root="", parts=())
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("web",))
        panel.entries = [FileEntry(loc=index, name="..", size=0, mtime=0.0, is_dir=True)]
        recorded = []
        monkeypatch.setattr(panel, "_change_cwd_loc", lambda loc: recorded.append(loc))
        panel.ascend()
        assert recorded == [index]


async def test_ascend_from_virtual_dunder_root_goes_home(monkeypatch, tmp_path):
    # Backspace from a network/virtual source root (docker index, ssh:// …) with
    # no ".." entry lands in the local home — always a way back to the FS dunder.
    # An archive (root = a real local file) still steps out to its folder.
    from pathlib import Path

    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        recorded = []
        monkeypatch.setattr(panel, "_change_cwd_loc", lambda loc: recorded.append(loc))

        panel.entries = []  # source root: no ".." entry
        panel.cwd_loc = VfsPath(scheme="docker", root="", parts=())
        panel.ascend()
        assert recorded[-1] == VfsPath.local(Path.home())

        panel.cwd_loc = VfsPath(scheme="docker", root="ssh://u@h:2222", parts=())
        panel.ascend()
        assert recorded[-1] == VfsPath.local(Path.home())

        # Archive backed by a real local file → step out to its folder.
        zip_path = tmp_path / "a.zip"
        zip_path.write_bytes(b"")
        panel.cwd_loc = VfsPath(scheme="zip", root=str(zip_path), parts=())
        panel.ascend()
        assert recorded[-1] == VfsPath.local(tmp_path)


async def test_docker_toplevel_consistent_rendering(monkeypatch, tmp_path):
    """Regression: at the Docker top level, every row must render consistently.

    Entries that HAVE applicable actions (compose groups, /Images, /Networks,
    /Volumes) get an action cluster.  Entries with NO applicable actions
    (/Containers section) must render a blank right area — NOT the default
    Size/Date/epoch text.  Empty rows below the listing must also be fully
    blank (no │ separators from the default empty_row_text geometry).

    Without the fix this test fails because:
    - /Containers row: _action_spans returns [] → falls through to
      row_text_single → renders "<DIR>│1970-…" (epoch date, stray column bars).
    - empty rows: empty_row_text with │ separators appear below the listing.
    """
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)
    from dunders.app import DundersApp
    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.panel_view import PanelViewMode

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test():
        panel = app._active_panel()
        panel.view_mode = PanelViewMode.FULL
        WIDTH = 80
        panel._panel_size = (WIDTH, 25)

        top = VfsPath(scheme="docker", root="", parts=())
        panel.cwd_loc = top

        # Mirror what _scan_top produces: compose group + all four section dirs.
        panel.entries = [
            FileEntry(
                loc=top.child("compose:evf-bot"), name="evf-bot",
                size=0, mtime=0.0, is_dir=True,
                extra={"docker.compose": "1"},
            ),
            FileEntry(
                loc=top.child("containers"), name="Containers",
                size=0, mtime=0.0, is_dir=True,
            ),
            FileEntry(
                loc=top.child("images"), name="Images",
                size=0, mtime=0.0, is_dir=True,
            ),
            FileEntry(
                loc=top.child("networks"), name="Networks",
                size=0, mtime=0.0, is_dir=True,
            ),
            FileEntry(
                loc=top.child("volumes"), name="Volumes",
                size=0, mtime=0.0, is_dir=True,
            ),
        ]

        # (a) No data row may contain the default epoch/Size/Date text.
        for i, entry in enumerate(panel.entries):
            row_text = "".join(s.text for s in panel._render_entry_row(i, WIDTH))
            assert "<DIR>" not in row_text, (
                f"row {i} ({entry.name!r}) contains '<DIR>' — "
                "default Size/Date rendering leaked into an action-index row"
            )
            # Epoch date strings start with "1970"; check no epoch date present.
            assert "1970" not in row_text, (
                f"row {i} ({entry.name!r}) contains epoch date — "
                "default mtime rendering leaked into an action-index row"
            )

        # (b) Empty rows below the listing carry ONLY the full-height Actions `│`
        # at the same sep_x as the data rows (the border runs unbroken to the
        # bottom) — but no stray Size/Date column bars.
        sep_x = panel._action_sep_x(WIDTH)
        for empty_idx in (len(panel.entries), len(panel.entries) + 1):
            empty_text = "".join(
                s.text for s in panel._render_entry_row(empty_idx, WIDTH)
            )
            assert empty_text.count("│") == 1, (
                f"empty row {empty_idx} must carry exactly one Actions separator "
                "(no stray Size/Date bars, but the border continues)"
            )
            assert empty_text[sep_x] == "│", (
                f"empty row {empty_idx} separator not at the Actions sep_x={sep_x}"
            )
            assert empty_text[:sep_x] == " " * sep_x, (
                f"empty row {empty_idx} Name area is not blank"
            )
            assert set(empty_text[sep_x + 1:]) <= {" "}, (
                f"empty row {empty_idx} Actions area is not blank"
            )
            assert len(empty_text) == WIDTH

        # (c) Action-bearing rows still expose their cluster (hit-testing works).
        compose_spans = panel._action_spans(0, WIDTH)   # evf-bot compose group
        assert compose_spans, "compose group row must have an action cluster"
        compose_ids = {a.id for *_, a in compose_spans}
        assert "docker.compose.up" in compose_ids

        images_spans = panel._action_spans(2, WIDTH)    # Images section
        assert images_spans, "/Images row must have an action cluster (prune)"
        images_ids = {a.id for *_, a in images_spans}
        assert "docker.prune" in images_ids

        # /Containers row explicitly has no action cluster (it's the no-action case).
        containers_spans = panel._action_spans(1, WIDTH)
        assert containers_spans == [], (
            "/Containers section must have no action spans at the top level"
        )


def test_docker_empty_containers_section_name_col_not_collapsed(monkeypatch, tmp_path):
    """Regression: when the Containers section has only '..' (no standalone
    containers), _provider_layout must NOT reserve the full action-button width.
    With n_actions=len(acts_list)=16 and _ACTION_CELL=2, buttons_w=32 would
    collapse name_w to max(1, 40-40-32-1)=1 on a 40-col half-panel, making
    the '/.. ' parent row invisible.  The fix: use n_actions=0 when not loading."""
    import dunders.fm.providers.docker_provider as dp
    monkeypatch.setattr(dp, "docker_available", lambda: True)

    from dunders.core.vfs import VfsPath
    from dunders.fm.file_entry import FileEntry
    from dunders.fm.vfs_local import default_registry

    panel = FilePanel(cwd=tmp_path, registry=default_registry())
    panel.cwd_loc = VfsPath(scheme="docker", root="", parts=("containers",))
    panel._loading = False  # genuinely empty, not mid-scan

    # Only the parent entry — no standalone containers (the failing scenario).
    parent_loc = VfsPath(scheme="docker", root="", parts=())
    panel.entries = [FileEntry(loc=parent_loc, name="..", size=0, mtime=0.0, is_dir=True)]

    # When genuinely empty (not loading), _provider_layout must return None so
    # the normal Name|Size|Date path is used — name_w is never starved by
    # provider columns (Image=20, Status=18) or the full button reservation (32).
    layout_narrow = panel._provider_layout(40)
    assert layout_narrow is None, (
        f"Empty containers section at width=40: expected None (bypass provider "
        f"layout so '/.. ' is visible), got {layout_narrow!r}.  "
        "Bug: n_actions=len(acts) when not loading collapses name_w to 1."
    )
    layout_80 = panel._provider_layout(80)
    assert layout_80 is None, (
        "Empty containers section at width=80: should also bypass provider layout"
    )

    # During loading the provider layout IS returned (header stays stable).
    panel._loading = True
    layout_loading = panel._provider_layout(40)
    assert layout_loading is not None, (
        "While loading, _provider_layout must return a layout (not None) so "
        "the header shows Image|Status|Actions and doesn't flicker."
    )

    # Once loading completes with no real entries, back to None.
    panel._loading = False
    assert panel._provider_layout(40) is None


def test_docker_entering_containers_parent_row_visible(monkeypatch, tmp_path):
    """Navigating into the Containers section and then trying (and failing) to
    enter a stopped container must leave '..' visible in the viewport.

    The bug: _maybe_revert called refresh_listing(focus_loc=failed_container).
    When the failed container was at index >= visible_rows, _ensure_cursor_visible
    pushed row_offset > 0, hiding '..' at index 0.

    Steps mirrored from the real use-case:
    1. Build the panel at the Docker top level via the real navigation path.
    2. Scroll down at the top level (cursor / row_offset non-zero).
    3. Enter the Containers section.
    4. Try to enter the LAST container (index 10, beyond the 8-row viewport) —
       it fails because every container reports stopped via inspect.
    5. _maybe_revert reverts to Containers; assert '..' is visible (row_offset==0).
    """
    import json as _json
    import dunders.fm.providers.docker_provider as dp
    from dunders.fm.providers.docker_provider import DockerProvider
    from dunders.fm.vfs_local import default_registry

    monkeypatch.setattr(dp, "docker_available", lambda: True)

    # 10 standalone running containers — so the last one (index 10 after '..')
    # lies beyond the 8-row visible window (panel height 10 → visible_rows = 8).
    canned_ps = "\n".join(
        _json.dumps({
            "ID": f"id{i:02d}", "Names": f"c{i:02d}",
            "State": "running", "Status": "Up 1h",
            "Image": "nginx", "Labels": "",
        })
        for i in range(10)
    )

    def _mock_run(self, args, *, endpoint="", input=None):
        if args and args[0] == "ps":
            return canned_ps.encode()
        if args and args[0] == "inspect":
            # All containers appear stopped; _scan_fs will raise OSError.
            return b"false"
        raise OSError(f"unexpected docker call: {args}")

    monkeypatch.setattr(DockerProvider, "_run", _mock_run)

    registry = default_registry()
    top_loc = VfsPath(scheme="docker", root="", parts=())
    containers_loc = VfsPath(scheme="docker", root="", parts=("containers",))

    panel = FilePanel(cwd=tmp_path, registry=registry)
    panel._panel_size = (80, 10)  # visible_rows = 10 - 2 = 8

    # ── Step 1-2: build at docker top level, scroll cursor down ──────────────
    panel._change_cwd_loc(top_loc)
    assert panel.cwd_loc == top_loc, "should be at docker top level"
    panel.cursor = 5       # simulate user having scrolled
    panel.row_offset = 3   # (top level has only 4 entries but we set this directly)

    # ── Step 3: enter the Containers section ─────────────────────────────────
    panel._change_cwd_loc(containers_loc)
    assert panel.cwd_loc == containers_loc
    assert panel.entries[0].is_parent, "Containers must start with '..'"

    # The pre-navigation scroll must have been wiped by _change_cwd_loc.
    assert panel.row_offset == 0, (
        f"row_offset={panel.row_offset} after entering Containers "
        "(expected 0 — _change_cwd_loc must reset scroll)"
    )

    n_entries = len(panel.entries)
    assert n_entries == 11, f"Expected '..' + 10 containers = 11 entries, got {n_entries}"

    # ── Step 4: try to enter the last container (index 10, beyond viewport) ──
    last_entry = panel.entries[-1]
    assert not last_entry.is_parent
    panel._change_cwd_loc(last_entry.loc)

    # The scan fails; _maybe_revert brings us back to containers_loc.
    # ── Step 5: assert '..' is visible ───────────────────────────────────────
    assert panel.cwd_loc == containers_loc, (
        "Panel should have reverted to Containers after the failed entry"
    )
    assert panel.entries[0].is_parent, "entries[0] must be '..' after revert"
    assert panel.row_offset == 0, (
        f"'..' is hidden after revert: row_offset={panel.row_offset}, "
        f"cursor={panel.cursor}  (last container was at index {n_entries - 1}, "
        f"visible_rows=8 — _maybe_revert must reset row_offset)"
    )


def test_docker_entering_empty_containers_parent_row_visible(monkeypatch, tmp_path):
    """When the Containers section has no containers (only '..'), the parent row
    must be visible after entering the section, regardless of any prior scroll
    state at the top level."""
    import dunders.fm.providers.docker_provider as dp
    from dunders.fm.providers.docker_provider import DockerProvider
    from dunders.fm.vfs_local import default_registry

    monkeypatch.setattr(dp, "docker_available", lambda: True)

    def _mock_run(self, args, *, endpoint="", input=None):
        if args and args[0] == "ps":
            return b""  # no containers
        raise OSError(f"unexpected docker call: {args}")

    monkeypatch.setattr(DockerProvider, "_run", _mock_run)

    registry = default_registry()
    top_loc = VfsPath(scheme="docker", root="", parts=())
    containers_loc = VfsPath(scheme="docker", root="", parts=("containers",))

    panel = FilePanel(cwd=tmp_path, registry=registry)
    panel._panel_size = (80, 10)

    panel._change_cwd_loc(top_loc)
    panel.cursor = 2
    panel.row_offset = 1

    panel._change_cwd_loc(containers_loc)

    assert panel.cwd_loc == containers_loc
    assert len(panel.entries) == 1, f"Expected only '..', got {len(panel.entries)} entries"
    assert panel.entries[0].is_parent, "entries[0] must be '..'"
    assert panel.row_offset == 0, (
        f"row_offset={panel.row_offset} after entering empty Containers (expected 0)"
    )


def test_unregistered_scheme_reverts_instead_of_crashing(tmp_path: Path):
    """A bookmark whose provider isn't registered (e.g. an sftp: loc in an
    install without paramiko) must warn and bounce back, not raise KeyError."""
    _make_tree(tmp_path)
    panel = FilePanel(cwd=tmp_path)
    panel._panel_size = (80, 10)
    sftp_loc = VfsPath(scheme="sftp", root="user@host:22", parts=("srv",))
    panel._registry._by_scheme.pop("sftp", None)  # simulate the missing optional dep
    assert "sftp" not in panel._registry.schemes()
    panel._change_cwd_loc(sftp_loc)

    assert panel.cwd_loc == VfsPath.local(tmp_path)  # reverted to where we came from
    assert panel.entries[0].is_parent


def test_unregistered_scheme_at_startup_falls_back_home(tmp_path: Path, monkeypatch):
    """No previous location to revert to (bookmark opened at launch) → home."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    _make_tree(tmp_path)
    panel = FilePanel(cwd=tmp_path)
    panel._panel_size = (80, 10)
    panel._registry._by_scheme.pop("sftp", None)
    panel.cwd_loc = VfsPath(scheme="sftp", root="user@host:22", parts=())
    panel._return_to = None

    panel.refresh_listing()

    assert panel.cwd_loc == VfsPath.local(tmp_path)
