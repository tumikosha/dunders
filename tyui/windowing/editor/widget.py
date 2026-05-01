"""EditorWidget — ScrollView-based editor with palette-based theming."""

from __future__ import annotations

import logging

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from rich.style import Style as RichStyle
from rich.text import Text

import re as _re

from tyui.windowing.core.buffer import TextBuffer
from tyui.windowing.core.fold_engine import FoldEngine, FoldRegion, effective_placeholder
from tyui.windowing.core.macro import MacroAction, MacroRecorder
from tyui.windowing.core.search import SearchOptions, Match, find_matches
from tyui.windowing.palette import Palette, Style

log = logging.getLogger(__name__)


class EditorWidget(ScrollView):
    """A palette-themed text editor widget for the windowing layer."""

    DEFAULT_CSS = """
    EditorWidget {
        height: 3fr;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("left", "cursor_left", "Left", show=False),
        Binding("right", "cursor_right", "Right", show=False),
        Binding("home", "line_start", "Home", show=False),
        Binding("end", "line_end", "End", show=False),
        Binding("enter", "newline", "Enter", show=False),
        Binding("backspace", "backspace", "Backspace", show=False),
        Binding("delete", "delete_forward", "Delete", show=False),
        Binding("tab", "insert_tab", "Tab", show=False),
        Binding("ctrl+right", "word_right", "Word Right", show=False),
        Binding("ctrl+left", "word_left", "Word Left", show=False),
        Binding("alt+right", "word_right", "Word Right", show=False),
        Binding("alt+left", "word_left", "Word Left", show=False),
        Binding("shift+up", "select_up", "Select Up", show=False),
        Binding("shift+down", "select_down", "Select Down", show=False),
        Binding("shift+left", "select_left", "Select Left", show=False),
        Binding("shift+right", "select_right", "Select Right", show=False),
        Binding("shift+home", "select_line_start", "Select to Start", show=False),
        Binding("shift+end", "select_line_end", "Select to End", show=False),
        Binding("ctrl+shift+right", "select_word_right", "Select Word Right", show=False),
        Binding("ctrl+shift+left", "select_word_left", "Select Word Left", show=False),
        Binding("shift+ctrl+right", "select_word_right", "Select Word Right", show=False),
        Binding("shift+ctrl+left", "select_word_left", "Select Word Left", show=False),
        Binding("ctrl+a", "select_all", "Select All", show=False),
        Binding("ctrl+z", "undo", "Undo", show=False),
        Binding("ctrl+y", "redo", "Redo", show=False),
        Binding("ctrl+shift+z", "redo", "Redo", show=False),
        Binding("ctrl+left_square_bracket", "fold_all", "Fold All", show=False),
        Binding("ctrl+right_square_bracket", "smart_fold", "Smart Fold", show=False),
        # Copy/Paste shortcuts
        Binding("ctrl+c", "copy", "Copy", show=False),
        Binding("ctrl+v", "paste", "Paste", show=False),
    ]

    show_line_numbers: reactive[bool] = reactive(True)

    class CursorMoved(Message):
        def __init__(self, editor: "EditorWidget", row: int, col: int) -> None:
            super().__init__()
            self.editor = editor
            self.row = row
            self.col = col

    class BufferModified(Message):
        def __init__(self, editor: "EditorWidget", modified: bool) -> None:
            super().__init__()
            self.editor = editor
            self.modified = modified

    def __init__(
        self,
        buffer: TextBuffer | None = None,
        fold_engine: FoldEngine | None = None,
        show_line_numbers: bool = True,
        palette: Palette | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.buffer = buffer or TextBuffer()
        self.fold_engine = fold_engine
        self._palette = palette
        self._fold_regions: list[FoldRegion] = []
        self._search_matches: list[Match] = []
        self._current_match_idx: int = -1
        self._search_pattern: str | None = None
        self._search_options: SearchOptions | None = None
        self._rendered_lines: list[str] = []
        self._line_map: list[int] = []
        self._dragging = False
        self.macro_recorder: MacroRecorder | None = None
        self.macro_skip_keys: set[str] = set()
        # Set the reactive directly to match constructor argument
        self.show_line_numbers = show_line_numbers

    def _get_palette(self) -> Palette | None:
        """Return the active Palette: explicit > Desktop ancestor > None."""
        if self._palette is not None:
            return self._palette
        # Try to get palette from Desktop ancestor
        try:
            from tyui.windowing.desktop import Desktop
            ancestors = self.ancestors_with_self
            for ancestor in ancestors:
                if hasattr(ancestor, "palette") and isinstance(ancestor.palette, Palette):
                    return ancestor.palette
        except Exception:
            log.debug("Could not traverse ancestors to find palette", exc_info=True)
        return None

    def _rich_style(self, role: str) -> RichStyle:
        """Resolve a palette role to a rich Style, with safe fallback."""
        palette = self._get_palette()
        if palette is not None:
            return palette.rich_style(role)
        return RichStyle()

    def on_mount(self) -> None:
        self._rescan_folds()
        self._refresh_render()

    def _gutter_width(self) -> int:
        if not self.show_line_numbers:
            return 0
        line_count = len(self._rendered_lines) if self._rendered_lines else self.buffer.line_count
        return len(str(line_count)) + 2

    def _rescan_folds(self) -> None:
        if self.fold_engine:
            old_collapsed = {
                r._content_key: r.collapsed
                for r in self._fold_regions
                if hasattr(r, "_content_key")
            }
            self._fold_regions = self.fold_engine.scan(self.buffer.lines)
            for region in self._fold_regions:
                try:
                    if region.start_row == region.end_row:
                        fold_text = self.buffer.lines[region.start_row][region.start_col:region.end_col + 1]
                    else:
                        parts = [self.buffer.lines[region.start_row][region.start_col:]]
                        for r in range(region.start_row + 1, min(region.end_row, region.start_row + 3)):
                            parts.append(self.buffer.lines[r].strip())
                        parts.append(self.buffer.lines[region.end_row][:region.end_col + 1].strip())
                        fold_text = "|".join(parts)
                except IndexError:
                    fold_text = ""
                region._content_key = (region.rule.start_label, fold_text)
                if region._content_key in old_collapsed:
                    region.collapsed = old_collapsed[region._content_key]

    def _refresh_render(self) -> None:
        if self.fold_engine:
            show_count = bool(getattr(self.app, "settings", {}).get(
                "show_fold_line_count", False
            ) if hasattr(self, "app") and self.app is not None else False)
            self._rendered_lines, self._line_map = self.fold_engine.render_lines_with_map(
                self.buffer.lines, self._fold_regions, show_line_count=show_count,
            )
        else:
            self._rendered_lines = list(self.buffer.lines)
            self._line_map = list(range(len(self._rendered_lines)))
        try:
            self.virtual_size = self.size.with_height(len(self._rendered_lines))
            self.refresh()
        except Exception:
            log.debug("Could not update virtual_size during render refresh", exc_info=True)

    def render_line(self, y: int) -> Strip:
        rendered_idx = y + self.scroll_offset.y
        if rendered_idx >= len(self._rendered_lines):
            return Strip.blank(self.size.width, self.rich_style)

        line = self._rendered_lines[rendered_idx]
        buf_row = self._rendered_row_to_buffer_row(rendered_idx)
        gutter = self._gutter_width()

        text = Text(style=self.rich_style)
        if self.show_line_numbers:
            line_num = str(buf_row + 1).rjust(gutter - 1)
            text.append(f"{line_num} ", style=self._rich_style("editor.line_numbers"))

        # Selection highlighting
        sel = self.buffer.selection_range()
        has_sel_on_line = False
        if sel:
            (sr, sc), (er, ec) = sel
            if sr <= buf_row <= er:
                has_sel_on_line = True
                if sr == er:
                    sel_start_vis = self._buffer_col_to_rendered_col(buf_row, sc)
                    sel_end_vis = self._buffer_col_to_rendered_col(buf_row, ec)
                else:
                    sel_start_vis = self._buffer_col_to_rendered_col(buf_row, sc) if buf_row == sr else 0
                    sel_end_vis = self._buffer_col_to_rendered_col(buf_row, ec) if buf_row == er else len(line)

        if has_sel_on_line:
            vis_col = self._buffer_col_to_rendered_col(buf_row, self.buffer.cursor_col) if buf_row == self.buffer.cursor_row else -1
            before_sel = line[:sel_start_vis]
            selected = line[sel_start_vis:sel_end_vis]
            after_sel = line[sel_end_vis:]
            text.append(before_sel)
            if sel_start_vis <= vis_col < sel_end_vis:
                sel_before_cur = selected[:vis_col - sel_start_vis]
                cur_char = selected[vis_col - sel_start_vis] if (vis_col - sel_start_vis) < len(selected) else " "
                sel_after_cur = selected[vis_col - sel_start_vis + 1:]
                text.append(sel_before_cur, style=self._rich_style("editor.selection"))
                text.append(cur_char, style=self._rich_style("editor.selection_cursor"))
                text.append(sel_after_cur, style=self._rich_style("editor.selection"))
            else:
                text.append(selected, style=self._rich_style("editor.selection"))
            text.append(after_sel)
            # If the selection continues past end of this line (i.e., newline
            # itself is selected), draw a marker space so empty/fully-selected
            # lines are visibly highlighted.
            if buf_row < er:
                cursor_at_eol = buf_row == self.buffer.cursor_row and vis_col >= len(line)
                marker_style = "editor.selection_cursor" if cursor_at_eol else "editor.selection"
                text.append(" ", style=self._rich_style(marker_style))
        elif buf_row == self.buffer.cursor_row:
            vis_col = self._buffer_col_to_rendered_col(buf_row, self.buffer.cursor_col)
            search_spans = self._get_search_spans_on_row(buf_row)
            fold_spans = [(s, e, self._rich_style("editor.fold_marker")) for s, e in self._get_fold_placeholders_on_row(buf_row)]
            all_spans = sorted(search_spans + fold_spans, key=lambda s: s[0])
            if not all_spans:
                before_cursor = line[:vis_col]
                cursor_char = line[vis_col] if vis_col < len(line) else " "
                after_cursor = line[vis_col + 1:] if vis_col < len(line) else ""
                text.append(before_cursor)
                text.append(cursor_char, style=self._rich_style("editor.cursor"))
                text.append(after_cursor)
            else:
                for i, ch in enumerate(line):
                    style: RichStyle | str = RichStyle()
                    for start, end, s in all_spans:
                        if start <= i < end:
                            style = s
                            break
                    if i == vis_col:
                        style = RichStyle(reverse=True) + style if isinstance(style, RichStyle) else RichStyle(reverse=True)
                    text.append(ch, style=style)
                if vis_col >= len(line):
                    text.append(" ", style=self._rich_style("editor.cursor"))
        else:
            self._append_with_search_and_fold(text, line, buf_row)

        try:
            return Strip(text.render(self.app.console))
        except Exception:
            log.debug("render_line failed for y=%d, returning blank strip", y, exc_info=True)
            return Strip.blank(self.size.width)

    def _get_fold_placeholders_on_row(self, buf_row: int) -> list[tuple[int, int]]:
        show_count = bool(getattr(self.app, "settings", {}).get(
            "show_fold_line_count", False
        ) if hasattr(self, "app") and self.app is not None else False)
        spans = []
        for region in self._fold_regions:
            if not region.collapsed:
                continue
            if region.start_row != buf_row:
                continue
            start = self._buffer_col_to_rendered_col(buf_row, region.start_col)
            placeholder = effective_placeholder(region, show_count)
            spans.append((start, start + len(placeholder)))
        return spans

    def _get_search_spans_on_row(self, buf_row: int) -> list[tuple[int, int, RichStyle]]:
        spans: list[tuple[int, int, RichStyle]] = []
        for idx, m in enumerate(self._search_matches):
            if m.row != buf_row:
                continue
            role = "editor.search_current" if idx == self._current_match_idx else "editor.search_match"
            spans.append((m.col, m.col + m.length, self._rich_style(role)))
        return spans

    def _append_with_search_and_fold(self, text: Text, line: str, buf_row: int) -> None:
        search_spans = self._get_search_spans_on_row(buf_row)
        fold_spans = [(s, e, self._rich_style("editor.fold_marker")) for s, e in self._get_fold_placeholders_on_row(buf_row)]
        all_spans = sorted(search_spans + fold_spans, key=lambda s: s[0])
        if not all_spans:
            text.append(line)
            return
        pos = 0
        for start, end, style in all_spans:
            if start < pos:
                start = pos
            if start >= end:
                continue
            if start > pos:
                text.append(line[pos:start])
            text.append(line[start:end], style=style)
            pos = end
        if pos < len(line):
            text.append(line[pos:])

    def set_search_matches(self, matches: list[Match], current_idx: int = -1) -> None:
        self._search_matches = matches
        self._current_match_idx = current_idx
        self._refresh_render()

    def clear_search(self) -> None:
        self._search_matches = []
        self._current_match_idx = -1
        self._search_pattern = None
        self._search_options = None
        self._refresh_render()

    def jump_to_match(self, idx: int) -> None:
        if 0 <= idx < len(self._search_matches):
            self._current_match_idx = idx
            m = self._search_matches[idx]
            self.buffer.cursor_row = m.row
            self.buffer.cursor_col = m.col
            self._post_cursor_update()

    def find_next(self) -> None:
        self._step_match(+1)

    def find_prev(self) -> None:
        self._step_match(-1)

    def replace_current(self, replacement: str) -> bool:
        if self._current_match_idx < 0 or not self._search_matches:
            return False
        if self._search_pattern is None or self._search_options is None:
            return False
        m = self._search_matches[self._current_match_idx]
        line = self.buffer.lines[m.row]
        self.buffer._save_undo()
        self.buffer.lines[m.row] = (
            line[: m.col] + replacement + line[m.col + m.length :]
        )
        self.buffer.cursor_row = m.row
        self.buffer.cursor_col = m.col + len(replacement)
        self.buffer._clamp_cursor()
        self.buffer.modified = True
        self.search(self._search_pattern, self._search_options)
        self._refresh_render()
        return True

    def replace_all(self, replacement: str) -> int:
        if not self._search_matches:
            return 0
        matches = list(self._search_matches)
        self.buffer._save_undo()
        for m in reversed(matches):
            line = self.buffer.lines[m.row]
            self.buffer.lines[m.row] = (
                line[: m.col] + replacement + line[m.col + m.length :]
            )
        self.buffer.modified = True
        n = len(matches)
        if self._search_pattern is not None and self._search_options is not None:
            self.search(self._search_pattern, self._search_options)
        self._refresh_render()
        return n

    def _step_match(self, direction: int) -> None:
        if not self._search_matches:
            return
        n = len(self._search_matches)
        wrap = self._search_options.wrap_around if self._search_options else True
        cur = self._current_match_idx
        if cur < 0:
            cur = 0 if direction > 0 else n - 1
        else:
            cur_match = self._search_matches[cur]
            cursor_pos = (self.buffer.cursor_row, self.buffer.cursor_col)
            already_at_current = cursor_pos == (cur_match.row, cur_match.col)
            if not already_at_current:
                # First navigation after typing — jump to existing current match.
                pass
            else:
                nxt = cur + direction
                if 0 <= nxt < n:
                    cur = nxt
                elif wrap:
                    cur = nxt % n
                # else: stay
        self.jump_to_match(cur)

    def search(self, pattern: str, options: SearchOptions) -> int:
        self._search_pattern = pattern
        self._search_options = options
        if not pattern:
            self._search_matches = []
            self._current_match_idx = -1
            self._refresh_render()
            return 0
        selection = None
        if options.in_selection and self.buffer.has_selection:
            rng = self.buffer.selection_range()
            if rng is not None:
                (sr, sc), (er, ec) = rng
                selection = (sr, sc, er, ec)
        try:
            matches = find_matches(self.buffer, pattern, options, selection)
        except _re.error:
            return -1  # keep previous matches
        self._search_matches = matches
        if matches:
            cur = (self.buffer.cursor_row, self.buffer.cursor_col)
            self._current_match_idx = next(
                (i for i, m in enumerate(matches) if (m.row, m.col) >= cur),
                0 if options.wrap_around else -1,
            )
        else:
            self._current_match_idx = -1
        self._refresh_render()
        return len(matches)

    def _post_cursor_update(self, keep_selection: bool = False) -> None:
        from textual._context import NoActiveAppError
        if not keep_selection and self.buffer.has_selection and not self._dragging:
            self.buffer.clear_selection()
        try:
            self.post_message(self.CursorMoved(self, self.buffer.cursor_row, self.buffer.cursor_col))
        except NoActiveAppError:
            pass
        self._refresh_render()
        cursor_y = self._buffer_row_to_rendered_row(self.buffer.cursor_row)
        try:
            visible_top = self.scroll_offset.y
            visible_bottom = visible_top + self.size.height - 2
            if cursor_y < visible_top:
                self.scroll_to(y=cursor_y, animate=False)
            elif cursor_y > visible_bottom:
                self.scroll_to(y=cursor_y - self.size.height + 2, animate=False)
        except NoActiveAppError:
            pass

    def _post_buffer_update(self) -> None:
        self._rescan_folds()
        self._refresh_render()
        self.post_message(self.BufferModified(self, self.buffer.modified))
        self._post_cursor_update()

    def _start_or_extend_selection(self) -> None:
        if not self.buffer.has_selection:
            self.buffer.start_selection(self.buffer.cursor_row, self.buffer.cursor_col)

    def _update_selection_to_cursor(self) -> None:
        self.buffer.update_selection(self.buffer.cursor_row, self.buffer.cursor_col)
        self._post_cursor_update(keep_selection=True)

    def action_select_up(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_up()
        self._update_selection_to_cursor()

    def action_select_down(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_down()
        self._update_selection_to_cursor()

    def action_select_left(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_left()
        self._update_selection_to_cursor()

    def action_select_right(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_right()
        self._update_selection_to_cursor()

    def action_select_line_start(self) -> None:
        self._start_or_extend_selection()
        self.buffer.cursor_col = 0
        self._update_selection_to_cursor()

    def action_select_line_end(self) -> None:
        self._start_or_extend_selection()
        self.buffer.cursor_col = len(self.buffer.current_line)
        self._update_selection_to_cursor()

    def action_select_word_right(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_word_right()
        self._update_selection_to_cursor()

    def action_select_word_left(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_word_left()
        self._update_selection_to_cursor()

    def action_select_all(self) -> None:
        self.buffer.select_all()
        self._post_cursor_update(keep_selection=True)

    def _snap_cursor_to_visible_row(self, direction: int = 1) -> None:
        visible = set(self._line_map) if self._line_map else set(range(self.buffer.line_count))
        row = self.buffer.cursor_row
        while row not in visible and 0 <= row < self.buffer.line_count:
            row += direction
        row = max(0, min(row, self.buffer.line_count - 1))
        self.buffer.cursor_row = row
        self.buffer.cursor_col = max(0, min(self.buffer.cursor_col, len(self.buffer.current_line)))

    def action_page_up(self) -> None:
        page = max(1, self.size.height - 2)
        for _ in range(page):
            self.buffer.move_cursor_up()
        self._snap_cursor_to_visible_row(direction=-1)
        self._post_cursor_update()

    def action_page_down(self) -> None:
        page = max(1, self.size.height - 2)
        for _ in range(page):
            self.buffer.move_cursor_down()
        self._snap_cursor_to_visible_row(direction=1)
        self._post_cursor_update()

    def action_cursor_up(self) -> None:
        self.buffer.move_cursor_up()
        self._snap_cursor_to_visible_row(direction=-1)
        self._post_cursor_update()

    def action_cursor_down(self) -> None:
        self.buffer.move_cursor_down()
        self._snap_cursor_to_visible_row(direction=1)
        self._post_cursor_update()

    def action_cursor_left(self) -> None:
        self.buffer.move_cursor_left()
        row = self.buffer.cursor_row
        col = self.buffer.cursor_col
        for region in self._fold_regions:
            if not region.collapsed or region.is_block:
                continue
            if region.start_row == row and region.start_col < col <= region.end_col:
                self.buffer.cursor_col = region.start_col
                break
        self._post_cursor_update()

    def action_word_right(self) -> None:
        self.buffer.move_cursor_word_right()
        self._snap_cursor_past_folds()
        self._post_cursor_update()

    def action_word_left(self) -> None:
        self.buffer.move_cursor_word_left()
        self._post_cursor_update()

    def action_cursor_right(self) -> None:
        self.buffer.move_cursor_right()
        self._snap_cursor_past_folds()
        self._post_cursor_update()

    def _buffer_col_to_rendered_col(self, row: int, buf_col: int) -> int:
        vis_col = 0
        pos = 0
        line = self.buffer.lines[row] if row < len(self.buffer.lines) else ""
        while pos < buf_col and pos < len(line):
            skipped = False
            for region in self._fold_regions:
                if (region.collapsed and not region.is_block
                        and region.start_row == row and region.start_col == pos):
                    vis_col += len(region.rule.placeholder)
                    pos = region.end_col + 1
                    skipped = True
                    break
            if not skipped:
                pos += 1
                vis_col += 1
        return vis_col

    def _snap_cursor_past_folds(self) -> None:
        row = self.buffer.cursor_row
        col = self.buffer.cursor_col
        for region in self._fold_regions:
            if not region.collapsed or region.is_block:
                continue
            if region.start_row == row and region.start_col < col <= region.end_col:
                self.buffer.cursor_col = region.end_col + 1
                return

    def _rendered_col_to_buffer_col(self, row: int, rendered_col: int) -> int:
        buf_col = 0
        vis_col = 0
        line = self.buffer.lines[row] if row < len(self.buffer.lines) else ""
        while buf_col < len(line) and vis_col < rendered_col:
            skipped = False
            for region in self._fold_regions:
                if (region.collapsed and not region.is_block
                        and region.start_row == row and region.start_col == buf_col):
                    vis_col += len(region.rule.placeholder)
                    buf_col = region.end_col + 1
                    skipped = True
                    break
            if not skipped:
                buf_col += 1
                vis_col += 1
        return buf_col

    def action_line_start(self) -> None:
        self.buffer.cursor_col = 0
        self._post_cursor_update()

    def action_line_end(self) -> None:
        self.buffer.cursor_col = len(self.buffer.current_line)
        self._post_cursor_update()

    def action_insert_tab(self) -> None:
        spaces = 4 - (self.buffer.cursor_col % 4)
        self.buffer.insert_char(" " * spaces)
        self._post_buffer_update()

    def action_newline(self) -> None:
        if self.buffer.has_selection:
            self._delete_collapsed_in_selection()
            self.buffer.delete_selection()
        self.buffer.insert_newline()
        self._post_buffer_update()

    def action_backspace(self) -> None:
        if self.buffer.has_selection:
            self._delete_collapsed_in_selection()
            self.buffer.delete_selection()
        else:
            self.buffer.delete_char_backward()
        self._post_buffer_update()

    def action_delete_forward(self) -> None:
        if self.buffer.has_selection:
            self._delete_collapsed_in_selection()
            self.buffer.delete_selection()
        else:
            self.buffer.delete_char_forward()
        self._post_buffer_update()

    def _delete_collapsed_in_selection(self) -> None:
        rng = self.buffer.selection_range()
        if not rng:
            return
        (sr, sc), (er, ec) = rng
        for region in self._fold_regions:
            if region.collapsed and region.is_block:
                if sr <= region.start_row <= er:
                    if region.end_row > er:
                        er = region.end_row
                        ec = len(self.buffer.lines[er]) if er < self.buffer.line_count else 0
        self.buffer.sel_start_row, self.buffer.sel_start_col = sr, sc
        self.buffer.sel_end_row, self.buffer.sel_end_col = er, ec

    def _unfold_at_cursor(self) -> None:
        row = self.buffer.cursor_row
        col = self.buffer.cursor_col
        for region in self._fold_regions:
            if not region.collapsed:
                continue
            if region.start_row <= row <= region.end_row:
                if region.is_block or (region.start_col <= col <= region.end_col):
                    region.collapsed = False

    def simulate_keypress(self, key: str, character: str | None) -> None:
        """Programmatically apply a key as if the user typed it. Used by macro replay."""
        # First try a declared Binding — this covers cursor movement, selection
        # (shift+arrow, ctrl+shift+arrow, ctrl+a), enter/backspace/tab/delete,
        # home/end, etc. — without hard-coding a parallel table.
        for binding in self.BINDINGS:
            binding_keys = [k.strip() for k in binding.key.split(",")]
            if key in binding_keys:
                handler = getattr(self, f"action_{binding.action}", None)
                if handler is not None:
                    handler()
                    return
        # Printable character insertion (skip modifier combos so Alt+M etc.
        # don't insert 'm').
        is_modifier_combo = any(key.startswith(p) for p in ("alt+", "ctrl+", "meta+"))
        if character and character.isprintable() and len(character) == 1 and not is_modifier_combo:
            if self.buffer.has_selection:
                self._delete_collapsed_in_selection()
                self.buffer.delete_selection()
            self._unfold_at_cursor()
            self.buffer.insert_char(character)
            self._post_buffer_update()

    def on_key(self, event: events.Key) -> None:
        if not self.has_focus:
            return
        rec = self.macro_recorder
        if rec is not None and rec.is_recording and event.key not in self.macro_skip_keys:
            data = f"{event.key}|{event.character or ''}"
            rec.record_action(MacroAction("keypress", data))
        # Skip insertion for modifier combos (alt+/ctrl+/meta+): terminal sends
        # the base character too but the key is really a shortcut, not typing.
        if any(event.key.startswith(p) for p in ("alt+", "ctrl+", "meta+")):
            return
        if event.character and event.character.isprintable() and len(event.character) == 1:
            if self.buffer.has_selection:
                self._delete_collapsed_in_selection()
                self.buffer.delete_selection()
            self._unfold_at_cursor()
            self.buffer.insert_char(event.character)
            self._post_buffer_update()
            event.prevent_default()

    def _rendered_row_to_buffer_row(self, rendered_row: int) -> int:
        if self._line_map and 0 <= rendered_row < len(self._line_map):
            return self._line_map[rendered_row]
        return rendered_row

    def _buffer_row_to_rendered_row(self, buf_row: int) -> int:
        for i, mapped in enumerate(self._line_map):
            if mapped == buf_row:
                return i
        return buf_row

    def _mouse_to_buffer_pos(self, x: int, y: int) -> tuple[int, int]:
        gutter = self._gutter_width()
        rendered_row = y + self.scroll_offset.y
        rendered_row = max(0, min(rendered_row, len(self._rendered_lines) - 1))
        buf_row = self._rendered_row_to_buffer_row(rendered_row)
        rendered_col = max(0, x - gutter)
        if rendered_row < len(self._rendered_lines):
            rendered_col = min(rendered_col, len(self._rendered_lines[rendered_row]))
        buf_col = self._rendered_col_to_buffer_col(buf_row, rendered_col)
        return buf_row, buf_col

    def _request_window_focus(self) -> None:
        """Ask the nearest Window ancestor to take focus."""
        try:
            from tyui.windowing.window import Window
            for ancestor in self.ancestors_with_self:
                if isinstance(ancestor, Window):
                    self.post_message(Window.FocusRequested(ancestor))
                    break
        except Exception:
            log.debug("Could not post Window.FocusRequested", exc_info=True)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:
            row, col = self._mouse_to_buffer_pos(event.x, event.y)
            self.buffer.cursor_row = row
            self.buffer.cursor_col = col
            self.buffer.start_selection(row, col)
            self._dragging = True
            self.capture_mouse()
            self._refresh_render()
            self._request_window_focus()
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        row, col = self._mouse_to_buffer_pos(event.x, event.y)
        self.buffer.update_selection(row, col)
        self.buffer.cursor_row = row
        self.buffer.cursor_col = col
        self._refresh_render()
        if event.y <= 1:
            self.scroll_relative(y=-2)
        elif event.y >= self.size.height - 2:
            self.scroll_relative(y=2)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            row, col = self._mouse_to_buffer_pos(event.x, event.y)
            self.buffer.update_selection(row, col)
            sel = self.buffer.selection_range()
            if sel and sel[0] == sel[1]:
                self.buffer.clear_selection()
            self._post_cursor_update(keep_selection=True)
            event.stop()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.scroll_relative(y=-3)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.scroll_relative(y=3)

    def fold_all(self) -> None:
        """Collapse all fold regions."""
        for region in self._fold_regions:
            region.collapsed = True
        self._refresh_render()

    def unfold_all(self) -> None:
        """Expand all fold regions."""
        for region in self._fold_regions:
            region.collapsed = False
        self._refresh_render()

    # Bound from BINDINGS (Textual looks up ``action_<name>``); the public
    # ``fold_all`` / ``toggle_fold_at_cursor`` methods are kept as the API
    # surface that EditorContent and tests call directly.
    def action_fold_all(self) -> None:
        self.fold_all()

    def action_toggle_fold_at_cursor(self) -> None:
        self.toggle_fold_at_cursor()

    def action_smart_fold(self) -> None:
        """Ctrl+] dispatch: on an empty line, toggle ALL folds (collapse if
        any expanded, else unfold); on a non-empty line, toggle just the
        fold under the cursor. Mirrors the convention from the demo app."""
        row = self.buffer.cursor_row
        if 0 <= row < len(self.buffer.lines):
            line = self.buffer.lines[row]
        else:
            line = ""
        if line.strip() == "":
            self.action_toggle_folds()
        else:
            self.toggle_fold_at_cursor()

    def action_undo(self) -> None:
        if self.buffer.undo():
            self._post_buffer_update()

    def action_redo(self) -> None:
        if self.buffer.redo():
            self._post_buffer_update()

    def action_copy(self) -> None:
        """Copy the current selection or line to the OS clipboard."""
        # The TextBuffer handles copying to the system clipboard.
        self.buffer.copy_selection()
        # Refresh cursor to reflect any visual changes (e.g., selection cleared).
        self._post_cursor_update()

    def action_paste(self) -> None:
        """Paste text from the OS clipboard into the buffer at the cursor."""
        self.buffer.paste()
        # Refresh the editor view after inserting the pasted text.
        self._post_buffer_update()

    def action_toggle_folds(self) -> None:
        """Toggle all folds: if any collapsed, unfold all; else fold all."""
        any_collapsed = any(r.collapsed for r in self._fold_regions)
        if any_collapsed:
            self.unfold_all()
        else:
            self.fold_all()

    def toggle_fold_at_cursor(self) -> None:
        if not self.fold_engine:
            return
        row = self.buffer.cursor_row
        col = self.buffer.cursor_col
        for region in self._fold_regions:
            if region.start_row <= row <= region.end_row:
                if region.start_row == region.end_row:
                    if region.start_col <= col <= region.end_col:
                        self.fold_engine.toggle_fold(region)
                        break
                else:
                    self.fold_engine.toggle_fold(region)
                    break
        self._refresh_render()
