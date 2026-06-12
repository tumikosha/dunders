"""FilePanel — interactive file-listing WindowContent.

Owns: cwd, entries, cursor, scroll, sort, show_hidden, selection.
Emits: PathChanged, SelectionChanged, ItemActivated.

Phase 2 builds the state machine + rendering + keybindings; Phase 3 will
hook the file-operation actions (F5/F6/F7/F8) into the panel API.
"""

from __future__ import annotations

from pathlib import Path

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.strip import Strip

from dunders.fm.file_entry import FileEntry
from dunders.fm.panel_view import (
    COL_SEP,
    PanelViewMode,
    column_count,
    column_width,
    empty_row_text,
    is_multicolumn,
)
from dunders.fm.scan import scan_dir
from dunders.fm.sort import SortOrder, default_descending, sort_entries
from dunders.windowing.content import WindowCommand, WindowContent


__all__ = ["FilePanel"]

# Entries the cursor moves per mouse-wheel notch (matches typical 3-line scroll).
_WHEEL_STEP = 3


class FilePanel(WindowContent):
    """Norton-Commander-style file-listing panel.

    Keep this widget self-contained: it owns its state and exposes a
    public API that the enclosing app calls for cross-panel actions
    (e.g. F5 copy reads `selected_paths()` from the active panel and
    writes into the other panel's `cwd`).

    Note on naming: the logical "first visible row index" is exposed as
    `row_offset` (plain int attribute). We deliberately avoid the name
    `scroll_offset` because Textual's Widget base class defines that as a
    read-only geometry property returning an `Offset(x, y)` — shadowing it
    with an int breaks any caller (including Textual itself when the panel
    sits in a scrolling parent) that does `widget.scroll_offset.x`.

    The test-size hook is stored in `_panel_size` (not `_size`) to avoid
    colliding with Textual's own `Widget._size` internal attribute, which
    backs the `outer_size` property and must not be overwritten.
    """

    can_focus = True

    class PathChanged(Message):
        def __init__(self, panel: "FilePanel", old: Path, new: Path) -> None:
            self.panel = panel
            self.old = old
            self.new = new
            super().__init__()

    class SelectionChanged(Message):
        def __init__(self, panel: "FilePanel") -> None:
            self.panel = panel
            super().__init__()

    class ItemActivated(Message):
        def __init__(self, panel: "FilePanel", entry: FileEntry) -> None:
            self.panel = panel
            self.entry = entry
            super().__init__()

    BINDINGS = [
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("home", "go_home", show=False),
        Binding("end", "go_end", show=False),
        Binding("enter", "activate_entry", show=False),
        Binding("backspace", "go_parent", show=False),
        Binding("insert", "toggle_selection", show=False),
        Binding("shift+down", "shift_select_down", show=False),
        Binding("shift+up", "shift_select_up", show=False),
    ]

    def __init__(self, cwd: str | Path = ".") -> None:
        super().__init__()
        # expanduser but NOT resolve: see dunders/fm/file_panel.py history —
        # canonicalising /tmp -> /private/tmp on macOS breaks user-visible
        # paths and round-trip equality in tests.
        self.cwd: Path = Path(cwd).expanduser()
        self.entries: list[FileEntry] = []
        self.cursor: int = 0
        self.row_offset: int = 0
        self.sort_order: SortOrder = SortOrder.NAME
        self.sort_descending: bool = default_descending(SortOrder.NAME)
        self.show_hidden: bool = True
        self.view_mode: PanelViewMode = PanelViewMode.FULL
        self.selection: set[Path] = set()
        # Stash the cwd into the reactive backing field so consumers reading
        # window_title before mount (e.g. Phase-1 stub test) see something
        # meaningful. The reactive watcher won't fire until mount, at which
        # point the enclosing Window's title was already set via make_window.
        self.window_title = str(self.cwd)
        # Test hook: tests assign self._panel_size = (w, h) before `self.size`
        # is populated by mounting.  _visible_rows() prefers _panel_size when
        # set.  Named _panel_size (not _size) to avoid colliding with
        # Widget._size which backs outer_size.
        self._panel_size: tuple[int, int] | None = None
        # Quick-search: курсор прыгает на ближайшее совпадение, совпавшая
        # подстрока подсвечивается; список не фильтруется.
        # Активируется Ctrl+S, выходит на Esc или любую явную навигацию
        # (стрелки, Enter, F-keys, ...).
        self._qs_query: str = ""
        self._qs_active: bool = False

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def refresh_listing(self) -> None:
        """Re-scan cwd, re-sort, clamp cursor, prune stale selections."""
        raw = scan_dir(self.cwd, show_hidden=self.show_hidden, include_parent=True)
        self.entries = sort_entries(raw, self.sort_order, descending=self.sort_descending)
        if self.cursor >= len(self.entries):
            self.cursor = max(0, len(self.entries) - 1)
        live_paths = {e.path for e in self.entries}
        self.selection &= live_paths
        self._ensure_cursor_visible()
        self.window_title = str(self.cwd)

    def set_sort_order(
        self, order: SortOrder, *, descending: bool | None = None
    ) -> None:
        """Change sort order and try to keep the cursor on the same entry.

        ``descending`` defaults to the order's natural direction (e.g. NAME=A→Z,
        MTIME=newest first). Pass an explicit bool to flip direction.
        """
        focused_path = (
            self.entries[self.cursor].path if 0 <= self.cursor < len(self.entries) else None
        )
        self.sort_order = order
        self.sort_descending = (
            descending if descending is not None else default_descending(order)
        )
        self.refresh_listing()
        if focused_path is not None:
            for i, e in enumerate(self.entries):
                if e.path == focused_path:
                    self.cursor = i
                    self._ensure_cursor_visible()
                    return

    def toggle_show_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.refresh_listing()

    # ------------------------------------------------------------------
    # Directory navigation
    # ------------------------------------------------------------------

    def activate(self) -> None:
        """Enter on the entry under the cursor.

        - On '..' or any directory: descend into it (cwd changes, listing
          refreshes, cursor goes to the parent row).
        - On a file: emit ItemActivated; cwd is not changed. The host app
          wires this up to the editor in Phase 4.
        """
        if not (0 <= self.cursor < len(self.entries)):
            return
        entry = self.entries[self.cursor]
        if entry.is_dir:
            self._change_cwd(entry.path)
            return
        self.post_message(FilePanel.ItemActivated(self, entry))

    def ascend(self) -> None:
        """Backspace == cd .., positioning the cursor on the row we left."""
        parent = self.cwd.parent
        if parent == self.cwd:
            return  # already at filesystem root
        self._change_cwd(parent)

    def _change_cwd(self, new_cwd: Path) -> None:
        old = self.cwd
        self.cwd = new_cwd
        self.cursor = 0
        self.row_offset = 0
        self.selection.clear()
        self.refresh_listing()
        # Cursor placement after a cwd change: if the prior cwd is visible
        # in the new listing — i.e. we ascended (the parent dir is now
        # showing the child we left) — put the cursor on it. Covers both
        # Backspace (ascend) and Enter on the ".." row, which both go
        # through this method. Descending leaves cursor at row 0 (which
        # the entry-search below silently leaves alone since `old` won't
        # appear under a fresh child listing).
        if old != new_cwd:
            for i, e in enumerate(self.entries):
                if e.path == old:
                    self.cursor = i
                    self._ensure_cursor_visible()
                    break
        self.post_message(FilePanel.PathChanged(self, old, new_cwd))

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def toggle_selection(self) -> None:
        """Toggle selection on the entry under the cursor and advance down.

        '..' (parent) is never selectable. The cursor advances either way
        so chord-typing Insert repeatedly walks down the list.
        """
        self._select_and_move(+1)

    def _select_and_move(self, delta: int) -> None:
        """Toggle selection at the cursor entry, then move cursor by `delta`.

        Shared by Insert (delta=+1) and Shift+Up/Shift+Down (delta=±1).
        Selection toggle is a no-op for the synthetic '..' row, but the
        cursor still moves so chord-typed sequences walk through the list.
        """
        changed = False
        if 0 <= self.cursor < len(self.entries):
            entry = self.entries[self.cursor]
            if not entry.is_parent:
                if entry.path in self.selection:
                    self.selection.remove(entry.path)
                else:
                    self.selection.add(entry.path)
                changed = True
        self.move_cursor(delta)
        if changed:
            self.post_message(FilePanel.SelectionChanged(self))

    def clear_selection(self) -> None:
        self.selection.clear()

    def selected_paths(self) -> list[Path]:
        """Selection in current listing order (deterministic for ops)."""
        return [e.path for e in self.entries if e.path in self.selection]

    def effective_targets(self) -> list[Path]:
        """Paths this panel's actions (F5/F6/F7/F8) should operate on.

        Selection wins if non-empty; otherwise the entry under the cursor,
        unless that entry is the synthetic '..' parent row (then []).
        """
        if self.selection:
            return self.selected_paths()
        if not (0 <= self.cursor < len(self.entries)):
            return []
        entry = self.entries[self.cursor]
        if entry.is_parent:
            return []
        return [entry.path]

    # ------------------------------------------------------------------
    # Cursor + scroll
    # ------------------------------------------------------------------

    def move_cursor(self, delta: int) -> None:
        new = max(0, min(len(self.entries) - 1, self.cursor + delta))
        if new == self.cursor:
            return
        self.cursor = new
        self._ensure_cursor_visible()

    def home(self) -> None:
        self.cursor = 0
        self._ensure_cursor_visible()

    def end(self) -> None:
        self.cursor = max(0, len(self.entries) - 1)
        self._ensure_cursor_visible()

    def page_down(self) -> None:
        self.move_cursor(self._visible_rows())

    def page_up(self) -> None:
        self.move_cursor(-self._visible_rows())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _visible_rows(self) -> int:
        """Number of entry rows visible (excluding header and footer).

        Row 0 is the header; the last row is the footer (full-name line).
        When quick-search is active one more row is reserved for its bar.
        """
        if self._panel_size is not None:
            _, h = self._panel_size
        else:
            h = self.size.height
        reserved = 2 + (1 if self._qs_active else 0)
        return max(1, h - reserved)

    def _multicol_col_height(self) -> int:
        """Rows per column for the Brief/Medium multi-column layout.

        Columns fill top-to-bottom. The height is the smaller of the available
        rows and ceil(n / k), so the extra columns are actually used as soon as
        there are at least k entries — otherwise a short listing would stack
        entirely in column 0 and Brief (2 cols) / Medium (3 cols) would look
        identical. When the listing overflows the screen the height saturates
        at the visible-row count and the panel scrolls a column at a time.
        """
        visible = self._visible_rows()
        k = column_count(self.view_mode)
        n = len(self.entries)
        if k <= 1 or n == 0:
            return max(1, visible)
        needed = -(-n // k)  # ceil(n / k)
        return max(1, min(visible, needed))

    def _ensure_cursor_visible(self) -> None:
        k = column_count(self.view_mode)
        if k == 1:
            rows = self._visible_rows()
            if self.cursor < self.row_offset:
                self.row_offset = self.cursor
            elif self.cursor >= self.row_offset + rows:
                self.row_offset = self.cursor - rows + 1
            self.row_offset = max(0, self.row_offset)
            return
        # Multi-column (Brief/Medium): a page holds rows*k entries laid out
        # column-major. Keep row_offset a multiple of `rows` so columns stay
        # aligned (snap first in case we just switched from a single-column
        # mode), then scroll a whole column at a time.
        rows = self._multicol_col_height()
        self.row_offset -= self.row_offset % rows
        page = rows * k
        while self.cursor < self.row_offset:
            self.row_offset = max(0, self.row_offset - rows)
        while self.cursor >= self.row_offset + page:
            self.row_offset += rows

    # ------------------------------------------------------------------
    # Quick search
    # ------------------------------------------------------------------

    def _qs_reset(self) -> None:
        if self._qs_active or self._qs_query:
            self._qs_active = False
            self._qs_query = ""
            self.refresh()

    def _qs_match_idx(self, idx: int) -> int:
        """Return start position of the case-insensitive substring match in
        entries[idx].name, or -1. The synthetic '..' row never matches."""
        if not self._qs_query:
            return -1
        if not (0 <= idx < len(self.entries)):
            return -1
        e = self.entries[idx]
        if e.is_parent:
            return -1
        return e.name.lower().find(self._qs_query.lower())

    def _qs_has_any_match(self) -> bool:
        return any(self._qs_match_idx(i) >= 0 for i in range(len(self.entries)))

    def _qs_jump(self, *, start: int, step: int) -> bool:
        """Walk the entry list from `start` in `step` direction (±1) wrapping
        around once, and place the cursor on the first match. Returns True if
        a match was found."""
        n = len(self.entries)
        if not self._qs_query or n == 0:
            return False
        for offset in range(n):
            idx = (start + step * offset) % n
            if self._qs_match_idx(idx) >= 0:
                if idx != self.cursor:
                    self.cursor = idx
                    self._ensure_cursor_visible()
                return True
        return False

    def _qs_jump_first(self) -> bool:
        return self._qs_jump(start=self.cursor, step=+1)

    def _qs_jump_next(self) -> bool:
        return self._qs_jump(start=self.cursor + 1, step=+1)

    def _qs_jump_prev(self) -> bool:
        return self._qs_jump(start=self.cursor - 1, step=-1)

    def on_key(self, event: events.Key) -> None:
        """Quick-search keystroke handling.

        Activation: Ctrl+S turns search on with an empty query and shows the
        bottom search bar; the user then types letters that get appended.

        While active, plain printable characters extend the query; Backspace
        shrinks it (empty query exits the mode); Escape exits unconditionally;
        Ctrl+Down / Ctrl+Up cycle through matches. Anything else (arrows,
        Enter, F-keys, ...) silently exits the mode and falls through to the
        standard BINDINGS path.
        """
        key = event.key
        # Activation: Ctrl+S. Toggles off if pressed again while active.
        if key == "ctrl+s":
            if self._qs_active:
                self._qs_reset()
            else:
                self._qs_active = True
                self._qs_query = ""
                self.refresh()
            event.stop()
            return

        if not self._qs_active:
            return

        if key == "escape":
            self._qs_reset()
            event.stop()
            return
        if key == "backspace":
            if not self._qs_query:
                self._qs_reset()
            else:
                self._qs_query = self._qs_query[:-1]
                if self._qs_query:
                    self._qs_jump_first()
                self.refresh()
            event.stop()
            return
        if key in ("ctrl+down", "ctrl+n"):
            self._qs_jump_next()
            self.refresh()
            event.stop()
            return
        if key in ("ctrl+up", "ctrl+p"):
            self._qs_jump_prev()
            self.refresh()
            event.stop()
            return

        # Printable single-char extension of the query.
        ch = event.character
        if ch is not None and len(ch) == 1 and ch.isprintable():
            self._qs_query += ch
            self._qs_jump_first()
            self.refresh()
            event.stop()
            return

        # Any other key (arrows, Enter, F-keys, Insert, Tab, ...) — drop out
        # of search mode and let the standard handler run.
        self._qs_reset()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    _SIZE_COL = 7    # "9999K" / "<DIR>" / "<UP>"
    _DATE_COL = 16   # "YYYY-MM-DD HH:MM"
    _GUTTER = 1      # one space separator after Name and after Size

    # ------------------------------------------------------------------
    # Theming — pull the base fg/bg from the Desktop palette so the panel
    # follows theme switches. Row styles (cursor/selected/header) are layered
    # ON TOP of this base, so reverse/bold/accent stay relative to the theme.
    # When no Desktop ancestor is reachable (unit tests render the panel
    # unmounted) the palette is None and the base is an empty style — segments
    # render exactly as before.
    # ------------------------------------------------------------------

    def _get_palette(self):
        from dunders.windowing.palette import Palette
        try:
            for ancestor in self.ancestors_with_self:
                pal = getattr(ancestor, "palette", None)
                if isinstance(pal, Palette):
                    return pal
        except Exception:
            return None
        return None

    def _base_style(self) -> RichStyle:
        """Themed background+foreground every row is painted on top of.

        Prefers a panel-specific ``panel.content`` role but falls back to
        ``window.content`` (defined by every built-in theme), so panels follow
        theme switches without each theme having to spell out a panel role.
        """
        pal = self._get_palette()
        if pal is None:
            return RichStyle()
        style = pal.get("panel.content")
        if style.fg is None and style.bg is None:
            style = pal.get("window.content")
        return style.to_rich()

    def _entry_base_style(self, entry) -> RichStyle:
        """Themed base for one row, with the file-type colour layered in.

        Resolves ``panel.file.<category>`` for the entry's type and overlays
        its fg/bold/italic onto :meth:`_base_style`. Themes that don't define
        the role resolve to an empty style, so the row falls back to the plain
        base (no type colour) — older/partial themes keep working unchanged.
        """
        base = self._base_style()
        from dunders.fm.file_colors import classify, role_for

        category = classify(entry)
        if category is None:
            return base
        pal = self._get_palette()
        if pal is None:
            return base
        role = pal.get(role_for(category))
        if role.fg is None and not role.bold and not role.italic:
            return base
        overlay = RichStyle(
            color=role.fg,
            bold=True if role.bold else None,
            italic=True if role.italic else None,
        )
        return base + overlay

    def apply_theme(self) -> None:
        """Re-apply the themed background and repaint (called on theme switch)."""
        base = self._base_style()
        if base.bgcolor is not None:
            self.styles.background = base.bgcolor.name
        if base.color is not None:
            self.styles.color = base.color.name
        self.refresh()

    def on_mount(self) -> None:
        self.apply_theme()

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip.blank(0)
        h = self._panel_size[1] if self._panel_size is not None else self.size.height
        if y == 0:
            return self._render_header(width)
        if y == h - 1:
            return self._render_footer(width)
        if self._qs_active and y == h - 2:
            return self._render_qs_bar(width)
        # Body rows. y == 1 is the first body row.
        if is_multicolumn(self.view_mode):
            return self._render_multicol_row(y - 1, width)
        return self._render_entry_row(y - 1 + self.row_offset, width)

    def _render_footer(self, width: int) -> Strip:
        """Bottom panel row: full, untruncated name of the cursor entry."""
        if 0 <= self.cursor < len(self.entries):
            name = self.entries[self.cursor].name
        else:
            name = ""
        text = (" " + name).ljust(width)[:width]
        # Reverse video for the footer bar, dimmed so its background reads as
        # less bright than a full-intensity reverse.
        return Strip([Segment(text, self._base_style() + RichStyle(reverse=True, dim=True))])

    def _render_qs_bar(self, width: int) -> Strip:
        """Bottom-of-panel indicator: 'Quick search: <query>_'.

        Reverse-video for ergonomics; turns red when the query has zero
        matches in the current listing (visual 'no-match' hint, mirrors
        Far/MC behaviour).
        """
        text = f" Quick search: {self._qs_query}_ "
        if len(text) > width:
            text = text[:width]
        else:
            text = text.ljust(width)
        miss = self._qs_query and not self._qs_has_any_match()
        # The no-match red is a fixed alarm colour; the normal bar stays
        # relative (reverse) so it tracks the theme.
        style = (
            RichStyle(color="white", bgcolor="red", bold=True)
            if miss
            else self._base_style() + RichStyle(reverse=True, bold=True)
        )
        return Strip([Segment(text, style)])

    def _render_header(self, width: int) -> Strip:
        mode = self.view_mode
        if is_multicolumn(mode):
            k = column_count(mode)
            col_w = column_width(width, k)
            cells = [
                ("Name" if col == 0 else "").center(col_w)[:col_w]
                for col in range(k)
            ]
            text = COL_SEP.join(cells)[:width].ljust(width)
            return Strip([Segment(text, self._base_style() + RichStyle(bold=True))])
        if mode is PanelViewMode.DETAILED:
            return self._render_header_detailed(width)
        if mode is PanelViewMode.DESCRIPTION:
            return self._render_header_description(width)
        if mode is PanelViewMode.SHORT:
            return self._render_header_short(width)
        return self._render_header_full(width)

    def _render_header_short(self, width: int) -> Strip:
        from dunders.fm.panel_view import name_col_width
        ncol = name_col_width(PanelViewMode.SHORT, width)
        base = self._base_style() + RichStyle(bold=True)
        name = "Name".center(ncol)
        size = "Size".center(self._SIZE_COL)
        text = f"{name}{COL_SEP}{size}"[:width].ljust(width)
        return Strip([Segment(text, base)])

    def _render_header_detailed(self, width: int) -> Strip:
        from dunders.fm.panel_view import name_col_width
        ncol = name_col_width(PanelViewMode.DETAILED, width)
        base = self._base_style() + RichStyle(bold=True)
        name = "Name".center(ncol)
        size = "Size".center(self._SIZE_COL)
        date = "Date".center(11)
        attr = "Attr".center(10)
        text = f"{name}{COL_SEP}{size}{COL_SEP}{date}{COL_SEP}{attr}"[:width].ljust(width)
        return Strip([Segment(text, base)])

    def _render_header_description(self, width: int) -> Strip:
        from dunders.fm.panel_view import name_col_width
        ncol = name_col_width(PanelViewMode.DESCRIPTION, width)
        base = self._base_style() + RichStyle(bold=True)
        name = "Name".center(ncol)
        desc = "Description".center(max(1, width - ncol - 1))
        text = f"{name}{COL_SEP}{desc}"[:width].ljust(width)
        return Strip([Segment(text, base)])

    def _render_header_full(self, width: int) -> Strip:
        name_col = max(1, width - self._SIZE_COL - self._DATE_COL - 2 * self._GUTTER)
        base = self._base_style() + RichStyle(bold=True)
        # Underline the column matching the current sort order — the only
        # built-in visual hint that "this is what the listing is sorted by".
        # SortOrder.EXT has no dedicated column, so no header is underlined.
        def style_for(order: SortOrder) -> RichStyle:
            return base + RichStyle(underline=True) if self.sort_order == order else base

        # Arrow reflects the live direction so a double-click toggle is
        # immediately visible: ↑ = ascending (A→Z, smallest first), ↓ =
        # descending (Z→A, newest/largest first).
        def arrow_for(order: SortOrder) -> str:
            if self.sort_order != order:
                return ""
            return "↓" if self.sort_descending else "↑"

        name_label = "Name" + arrow_for(SortOrder.NAME)
        size_label = "Size" + arrow_for(SortOrder.SIZE)
        date_label = "Date" + arrow_for(SortOrder.MTIME)
        # Header labels are centred within their column (data rows are not).
        name_field = name_label.center(name_col)
        size_field = size_label.center(self._SIZE_COL)
        date_field = date_label.center(self._DATE_COL)

        segs: list[Segment] = []
        remaining = width

        def push(text: str, style: RichStyle) -> bool:
            nonlocal remaining
            take = min(len(text), remaining)
            if take <= 0:
                return False
            segs.append(Segment(text[:take], style))
            remaining -= take
            return remaining > 0

        if not push(name_field, style_for(SortOrder.NAME)):
            return Strip(segs)
        if not push(COL_SEP, base):
            return Strip(segs)
        if not push(size_field, style_for(SortOrder.SIZE)):
            return Strip(segs)
        if not push(COL_SEP, base):
            return Strip(segs)
        push(date_field, style_for(SortOrder.MTIME))
        return Strip(segs)

    def _header_column_at(self, x: int) -> SortOrder | None:
        """Map an x-pixel inside the header row to a sortable column."""
        if self.view_mode is not PanelViewMode.FULL:
            return None
        width = self.size.width if self._panel_size is None else self._panel_size[0]
        if width <= 0:
            return None
        name_col = max(1, width - self._SIZE_COL - self._DATE_COL - 2 * self._GUTTER)
        size_start = name_col + self._GUTTER
        size_end = size_start + self._SIZE_COL
        date_start = size_end + self._GUTTER
        date_end = date_start + self._DATE_COL
        if 0 <= x < name_col:
            return SortOrder.NAME
        if size_start <= x < size_end:
            return SortOrder.SIZE
        if date_start <= x < date_end:
            return SortOrder.MTIME
        return None

    def on_click(self, event: events.Click) -> None:
        if event.y == 0:
            order = self._header_column_at(event.x)
            if order is None:
                return
            # Click on a different column → switch (natural direction).
            # Click on the active column → flip direction. Repeated clicks
            # therefore alternate ↑ / ↓ on the same header.
            if order == self.sort_order:
                self.set_sort_order(order, descending=not self.sort_descending)
            else:
                self.set_sort_order(order)
            self.refresh()
            event.stop()
            return

        h = self._panel_size[1] if self._panel_size else self.size.height
        # Footer row is non-interactive.
        if event.y == h - 1:
            return
        # Quick-search bar (row above footer when active) — ignore.
        if self._qs_active and event.y == h - 2:
            return

        if is_multicolumn(self.view_mode):
            idx = self._multicol_index_at(event.x, event.y, self.size.width)
        else:
            idx = event.y - 1 + self.row_offset
        if not (0 <= idx < len(self.entries)):
            return

        if idx != self.cursor:
            self._qs_reset()
            self.cursor = idx
            self._ensure_cursor_visible()
            self.refresh()

        # Double-click == Enter: descend into a directory, or activate a file
        # (ItemActivated → host runs executables / opens the editor).
        if getattr(event, "chain", 1) >= 2:
            self._qs_reset()
            self.activate()
            self.refresh()

        event.stop()

    def _render_entry_row(self, idx: int, width: int) -> Strip:
        if not (0 <= idx < len(self.entries)):
            # Blank row below the listing, but keep the column separators so
            # the vertical bars run to the bottom of the panel.
            return Strip([Segment(empty_row_text(self.view_mode, width), self._base_style())])
        entry = self.entries[idx]
        is_cursor = idx == self.cursor
        is_selected = entry.path in self.selection

        from dunders.fm.panel_view import name_col_width, row_text_single
        name_col = name_col_width(self.view_mode, width)
        name = entry.name
        if len(name) > name_col - 1:
            name = name[: name_col - 2] + "…"
        text = row_text_single(self.view_mode, entry, width)

        style = self._row_style(
            is_cursor=is_cursor,
            is_selected=is_selected,
            focused=self._is_active_panel,
            entry=entry,
        )
        if self._qs_active and self._qs_query and not entry.is_parent:
            hi = self._qs_highlight_segments(
                text=text,
                name_displayed=name,
                name_col=name_col,
                base_style=style,
            )
            if hi is not None:
                return Strip(hi)
        return Strip([Segment(text, style)])

    def _multicol_index_at(self, x: int, y: int, width: int) -> int:
        """Entry index for a click at (x, y) in a multi-column layout.

        `col` is clamped to the last column so a click in the right-edge
        trailing pad (when width isn't an exact multiple of the columns)
        maps to the last column rather than overflowing past it.
        """
        rows = self._multicol_col_height()
        k = column_count(self.view_mode)
        col_w = column_width(width, k)
        vis_row = y - 1
        if vis_row >= rows:
            return -1  # click below the column height — no entry there
        col = min(x // (col_w + self._GUTTER), k - 1)
        return self.row_offset + col * rows + vis_row

    def _render_multicol_row(self, vis_row: int, width: int) -> Strip:
        """Render one visual row of a Brief/Medium (names-only) layout.

        Column-major: the cell in column `col` on visual row `vis_row` is entry
        ``row_offset + col*rows + vis_row``. The cursor cell inverts; selected
        cells are yellow. Quick-search substring highlight is single-column only
        (MVP) — multi-column shows cursor-cell styling but no inline paint.
        """
        from dunders.fm.panel_view import format_cell
        rows = self._multicol_col_height()
        k = column_count(self.view_mode)
        col_w = column_width(width, k)
        # Rows below the column height are empty (the column is only `rows`
        # tall); without this guard they would re-show entries from the next
        # column and produce duplicates. Keep the column separators so the
        # vertical bars run to the bottom of the panel.
        base = self._base_style()
        if vis_row >= rows:
            empty = COL_SEP.join([" " * col_w] * k)[:width].ljust(width)
            return Strip([Segment(empty, base)])
        segs: list[Segment] = []
        for col in range(k):
            if col > 0:
                segs.append(Segment(COL_SEP, base))  # column separator
            idx = self.row_offset + col * rows + vis_row
            if not (0 <= idx < len(self.entries)):
                segs.append(Segment(" " * col_w, base))
                continue
            entry = self.entries[idx]
            cell = format_cell(entry, col_w)
            style = self._row_style(
                is_cursor=(idx == self.cursor),
                is_selected=(entry.path in self.selection),
                focused=self._is_active_panel,
                entry=entry,
            )
            segs.append(Segment(cell, style))
        used = k * col_w + (k - 1)
        if used < width:
            segs.append(Segment(" " * (width - used), base))
        return Strip(segs)

    def _qs_highlight_segments(
        self,
        *,
        text: str,
        name_displayed: str,
        name_col: int,
        base_style: RichStyle,
    ) -> list[Segment] | None:
        """Build segments that paint the matched substring in `name_displayed`
        on top of `text`. Returns None if the match isn't visible (e.g. the
        truncating ellipsis ate it)."""
        q = self._qs_query.lower()
        pos = name_displayed.lower().find(q)
        if pos < 0:
            return None
        # Visible name starts at column 1 inside name_field; name_field starts
        # at column 0 of `text`. So absolute start = 1 + pos.
        start = 1 + pos
        end = start + len(self._qs_query)
        # Defend against truncated names where end may run past name_col.
        if end > name_col:
            return None
        hi_style = base_style + RichStyle(color="black", bgcolor="yellow", bold=True)
        return [
            Segment(text[:start], base_style),
            Segment(text[start:end], hi_style),
            Segment(text[end:], base_style),
        ]

    def _row_style(
        self,
        *,
        is_cursor: bool,
        is_selected: bool,
        focused: bool,
        entry=None,
    ) -> RichStyle:
        # Normal rows carry the file-type foreground (``typed``). The focused
        # cursor bar is built from the PLAIN themed base instead, so the
        # reverse-video highlight looks identical on every file type — the
        # type colour must not bleed into the cursor background. Selected
        # entries are yellow-bold; the inactive-panel cursor is just bold
        # (no reverse), so it can keep the type colour without changing the
        # highlight.
        typed = self._entry_base_style(entry) if entry is not None else self._base_style()
        plain = self._base_style()
        if is_cursor and is_selected:
            return plain + RichStyle(
                color="yellow",
                bold=True,
                reverse=focused,
            )
        if is_cursor:
            if focused:
                return plain + RichStyle(reverse=True)
            return typed + RichStyle(bold=True)
        if is_selected:
            return typed + RichStyle(color="yellow", bold=True)
        return typed

    # ------------------------------------------------------------------
    # Focus handling — repaint on focus/blur so the cursor-row style
    # follows whether this panel is the active one.
    # ------------------------------------------------------------------

    def _enclosing_window(self):
        """Walk up to the enclosing windowing ``Window``, or ``None`` when the
        panel is not mounted under one (e.g. standalone in unit tests)."""
        from dunders.windowing.window import Window

        node = self.parent
        while node is not None and not isinstance(node, Window):
            node = getattr(node, "parent", None)
        return node  # a Window or None

    def _wheel(self, delta: int) -> None:
        """Move the cursor by ``delta`` entries in response to a wheel notch.

        If the panel is not the active one, request focus on its window first
        (the same path a click takes) so the wheel both activates and scrolls,
        matching Midnight Commander. Clamping and viewport follow are handled by
        ``move_cursor`` / ``_ensure_cursor_visible``.
        """
        if not self._is_active_panel:
            win = self._enclosing_window()
            if win is not None:
                from dunders.windowing.window import Window

                self.post_message(Window.FocusRequested(win))
        self._qs_reset()
        self.move_cursor(delta)
        self.refresh()

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._wheel(_WHEEL_STEP)
        event.stop()
        event.prevent_default()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._wheel(-_WHEEL_STEP)
        event.stop()
        event.prevent_default()

    @property
    def _is_active_panel(self) -> bool:
        """True when this panel is the "active" one for rendering purposes.

        A panel is active when it has Textual widget focus OR when it is
        the content of the Desktop's focused_window (i.e. it is the
        logical active panel even when Textual widget focus is elsewhere,
        such as on the CommandLine input).
        """
        if self.has_focus:
            return True
        try:
            from dunders.windowing.desktop import Desktop

            win = self._enclosing_window()
            if win is None:
                return False
            node = win.parent
            while node is not None and not isinstance(node, Desktop):
                node = getattr(node, "parent", None)
            if node is None:
                return False
            return node.focused_window is win
        except Exception:
            return False

    def on_focus(self, _event=None) -> None:
        self.refresh()

    def on_blur(self, _event=None) -> None:
        self.refresh()

    # ------------------------------------------------------------------
    # Action handlers (wired to BINDINGS)
    # ------------------------------------------------------------------

    def action_cursor_up(self) -> None:
        self._qs_reset()
        self.move_cursor(-1)
        self.refresh()

    def action_cursor_down(self) -> None:
        self._qs_reset()
        self.move_cursor(+1)
        self.refresh()

    def action_page_up(self) -> None:
        self._qs_reset()
        self.page_up()
        self.refresh()

    def action_page_down(self) -> None:
        self._qs_reset()
        self.page_down()
        self.refresh()

    def action_go_home(self) -> None:
        self._qs_reset()
        self.home()
        self.refresh()

    def action_go_end(self) -> None:
        self._qs_reset()
        self.end()
        self.refresh()

    def action_activate_entry(self) -> None:
        self._qs_reset()
        self.activate()
        self.refresh()

    def action_go_parent(self) -> None:
        self._qs_reset()
        self.ascend()
        self.refresh()

    def action_toggle_selection(self) -> None:
        self._qs_reset()
        self.toggle_selection()
        self.refresh()

    def action_shift_select_down(self) -> None:
        self._qs_reset()
        self._select_and_move(+1)
        self.refresh()

    def action_shift_select_up(self) -> None:
        self._qs_reset()
        self._select_and_move(-1)
        self.refresh()

    # --- TV-style command declaration -------------------------------------

    def get_commands(self) -> list[WindowCommand]:
        """Norton-Commander F-keys exposed to the host's CommandDispatcher.

        Handlers delegate to the enclosing App's ``action_*`` methods, so
        the existing inter-panel logic (copy to opposite panel, dialogs,
        ...) remains the single source of truth — this just lets menus,
        the command palette and the dynamic hotkey router pick them up
        when this panel has focus.
        """
        app = getattr(self, "app", None)

        def _bind(action: str):
            fn = getattr(app, f"action_{action}", None) if app is not None else None
            return fn if callable(fn) else None

        return [
            WindowCommand(id="panel.new",    label="New",    handler=_bind("new")),
            WindowCommand(id="panel.project_view", label="Project View", handler=_bind("project_view"), hotkey="f1"),
            WindowCommand(id="panel.user_menu", label="User menu", handler=_bind("user_menu"), hotkey="f2"),
            WindowCommand(id="panel.view",   label="View",   handler=_bind("view"),   hotkey="f3"),
            WindowCommand(id="panel.edit",   label="Edit",   handler=_bind("edit"),   hotkey="f4"),
            WindowCommand(id="panel.copy",   label="Copy",   handler=_bind("copy"),   hotkey="f5"),
            WindowCommand(id="panel.move",   label="Move",   handler=_bind("move"),   hotkey="f6"),
            WindowCommand(id="panel.mkdir",  label="Mkdir",  handler=_bind("mkdir"),  hotkey="f7"),
            WindowCommand(id="panel.delete", label="Delete", handler=_bind("delete"), hotkey="f8"),
            WindowCommand(id="panel.chmod",  label="Chmod",  handler=_bind("chmod"), hotkey="ctrl+a"),
            WindowCommand(id="panel.find_file", label="Find file…", handler=_bind("find_file"), hotkey="alt+f7"),
            WindowCommand(id="panel.toggle_hidden", label="Show hidden files", handler=_bind("toggle_hidden"), hotkey="alt+h"),
        ]
