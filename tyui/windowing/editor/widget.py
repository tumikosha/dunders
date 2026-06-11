"""EditorWidget — ScrollView-based editor with palette-based theming."""

from __future__ import annotations

import logging

from textual import events
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from rich.style import Style as RichStyle
from rich.text import Text

import re as _re

from tyui.windowing.core.buffer import TextBuffer
from tyui.windowing.core.fold_engine import FoldEngine, FoldRegion, effective_placeholder
from tyui.windowing.core.highlight import Span, SyntaxHighlighter
from tyui.windowing.core.macro import MacroAction, MacroRecorder
from tyui.windowing.core.search import SearchOptions, Match, find_matches
from tyui.windowing.palette import Palette, Style

log = logging.getLogger(__name__)

_SYNTAX_SIZE_THRESHOLD = 1024 * 1024  # 1 MiB — above this, highlighting is off
_SYNTAX_DEBOUNCE = 0.25               # seconds to wait after edits before retokenizing


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
        Binding("ctrl+home", "document_start", "Document Start", show=False),
        Binding("ctrl+end", "document_end", "Document End", show=False),
        # macOS classic: Cmd+Up/Down jump to start/end of the document.
        # "super" is how the kitty keyboard protocol reports the Cmd key.
        Binding("super+up", "document_start", "Document Start", show=False),
        Binding("super+down", "document_end", "Document End", show=False),
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
        Binding("ctrl+shift+home", "select_document_start", "Select to Document Start", show=False),
        Binding("ctrl+shift+end", "select_document_end", "Select to Document End", show=False),
        Binding("shift+ctrl+home", "select_document_start", "Select to Document Start", show=False),
        Binding("shift+ctrl+end", "select_document_end", "Select to Document End", show=False),
        # macOS classic: Cmd+Shift+Up/Down select to start/end of the document.
        Binding("shift+super+up", "select_document_start", "Select to Document Start", show=False),
        Binding("shift+super+down", "select_document_end", "Select to Document End", show=False),
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
        Binding("ctrl+x", "cut", "Cut", show=False),
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
        self._highlighter = SyntaxHighlighter()
        self._syntax_spans: list[list[Span]] = []
        self._highlight_enabled = True
        self._syntax_timer = None
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
        self._detect_language()
        self._recompute_syntax()
        self._refresh_render()

    def on_unmount(self) -> None:
        if self._syntax_timer is not None:
            try:
                self._syntax_timer.stop()
            except Exception:
                log.debug("syntax timer stop on unmount failed", exc_info=True)

    def _detect_language(self) -> None:
        sample = "\n".join(self.buffer.lines[:50])
        self._highlighter.detect(self.buffer.file_path, sample)

    def _should_highlight(self) -> bool:
        if not self._highlight_enabled or not self._highlighter.enabled:
            return False
        size = sum(len(line) + 1 for line in self.buffer.lines)
        return size <= _SYNTAX_SIZE_THRESHOLD

    def _recompute_syntax(self) -> None:
        """Retokenize the buffer. Runs in a worker when an app is active;
        falls back to inline computation otherwise (e.g. in unit tests)."""
        if not self._should_highlight():
            if self._syntax_spans:
                self._syntax_spans = []
                self._safe_refresh()
            return
        from textual._context import NoActiveAppError
        lines = list(self.buffer.lines)
        try:
            self.run_worker(
                lambda: self._tokenize_worker(lines),
                thread=True, exclusive=True, group="syntax", exit_on_error=False,
            )
        except (NoActiveAppError, RuntimeError):
            # No active app (e.g. unit tests) — compute synchronously.
            self._apply_syntax_spans(self._highlighter.tokenize(lines))

    def _tokenize_worker(self, lines: list[str]) -> None:
        from textual.worker import get_current_worker
        spans = self._highlighter.tokenize(lines)
        worker = get_current_worker()
        if worker.is_cancelled:
            return
        try:
            self.app.call_from_thread(self._apply_syntax_spans, spans)
        except Exception:
            log.debug("apply syntax spans failed (app teardown?)", exc_info=True)

    def _apply_syntax_spans(self, spans: list[list[Span]]) -> None:
        self._syntax_spans = spans
        self._safe_refresh()

    def _safe_refresh(self) -> None:
        try:
            self.refresh()
        except Exception:
            log.debug("refresh after syntax recompute failed", exc_info=True)

    def _schedule_syntax(self) -> None:
        if self._syntax_timer is not None:
            try:
                self._syntax_timer.stop()
            except Exception:
                log.debug("syntax timer stop failed", exc_info=True)
        try:
            self._syntax_timer = self.set_timer(_SYNTAX_DEBOUNCE, self._recompute_syntax)
        except Exception:
            # No active app — recompute immediately.
            self._recompute_syntax()

    def set_language(self, name: str | None) -> None:
        self._highlighter.set_language(name)
        self._recompute_syntax()
        self._safe_refresh()

    def set_highlight_enabled(self, enabled: bool) -> None:
        self._highlight_enabled = enabled
        if enabled:
            self._recompute_syntax()
        else:
            self._syntax_spans = []
        self._safe_refresh()

    def _gutter_width(self) -> int:
        if not self.show_line_numbers:
            return 0
        line_count = len(self._rendered_lines) if self._rendered_lines else self.buffer.line_count
        return len(str(line_count)) + 2

    def _content_width(self) -> int:
        """Visible content width, excluding any vertical scrollbar column."""
        width = self.scrollable_content_region.width
        return width if width > 0 else self.size.width

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
            # +1 leaves room for the past-EOL cursor marker; the gutter is
            # rendered fixed (never scrolled) so it counts toward the width too.
            longest = max((len(line) for line in self._rendered_lines), default=0)
            content_width = longest + self._gutter_width() + 1
            self.virtual_size = Size(
                max(content_width, self.size.width), len(self._rendered_lines)
            )
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
        base = self.rich_style
        n = len(line)

        # Per-column style array; index n is a slot for a cursor/marker past EOL.
        col_styles: list[RichStyle] = [base] * (n + 1)

        # Layer 0: syntax base.
        syntax_style_cache: dict[str, RichStyle] = {}
        for s, e, role in self._syntax_spans_rendered(buf_row):
            style = syntax_style_cache.get(role)
            if style is None:
                style = self._rich_style(role)
                syntax_style_cache[role] = style
            for i in range(max(0, s), min(e, n)):
                col_styles[i] = style

        # Layer 1: fold placeholders.
        fold_style = self._rich_style("editor.fold_marker")
        for s, e in self._get_fold_placeholders_on_row(buf_row):
            for i in range(max(0, s), min(e, n)):
                col_styles[i] = fold_style

        # Layer 2: search matches.
        for s, e, sstyle in self._get_search_spans_on_row(buf_row):
            for i in range(max(0, s), min(e, n)):
                col_styles[i] = sstyle

        # Layer 3: selection.
        sel = self.buffer.selection_range()
        has_sel_on_line = False
        sel_start_vis = sel_end_vis = 0
        er = -1
        if sel:
            (sr, sc), (er, ec) = sel
            if sr <= buf_row <= er:
                has_sel_on_line = True
                if sr == er:
                    sel_start_vis = self._buffer_col_to_rendered_col(buf_row, sc)
                    sel_end_vis = self._buffer_col_to_rendered_col(buf_row, ec)
                else:
                    sel_start_vis = self._buffer_col_to_rendered_col(buf_row, sc) if buf_row == sr else 0
                    sel_end_vis = self._buffer_col_to_rendered_col(buf_row, ec) if buf_row == er else n
        if has_sel_on_line:
            sel_style = self._rich_style("editor.selection")
            for i in range(max(0, sel_start_vis), min(sel_end_vis, n)):
                col_styles[i] = sel_style

        # Layer 4: cursor (only within the line; EOL handled by the marker below).
        vis_col = -1
        if buf_row == self.buffer.cursor_row:
            vis_col = self._buffer_col_to_rendered_col(buf_row, self.buffer.cursor_col)
            in_sel = has_sel_on_line and sel_start_vis <= vis_col < sel_end_vis
            cur_role = "editor.selection_cursor" if in_sel else "editor.cursor"
            if 0 <= vis_col < n:
                col_styles[vis_col] = self._rich_style(cur_role)

        # Trailing marker for a cursor past EOL and/or a selection continuing
        # onto the next line. Sets the style for the n-th (past-EOL) column.
        cursor_at_eol = buf_row == self.buffer.cursor_row and vis_col >= n
        sel_spans_newline = has_sel_on_line and er >= 0 and buf_row < er
        if cursor_at_eol:
            marker_role = "editor.selection_cursor" if sel_spans_newline else "editor.cursor"
            col_styles[n] = self._rich_style(marker_role)
        elif sel_spans_newline:
            col_styles[n] = self._rich_style("editor.selection")

        # Build the Text: gutter, then run-length grouped body (over the string),
        # then the optional trailing marker space. Plain columns carry ``base``
        # (the widget's theme style) so they render identically to the old plain
        # branch.
        # The gutter (line numbers) stays pinned; only the body scrolls
        # horizontally with ``scroll_offset.x``.
        gutter_text = Text(style=base)
        if self.show_line_numbers:
            line_num = str(buf_row + 1).rjust(gutter - 1)
            gutter_text.append(f"{line_num} ", style=self._rich_style("editor.line_numbers"))

        body_text = Text(style=base)
        i = 0
        while i < n:
            st = col_styles[i]
            j = i + 1
            while j < n and col_styles[j] == st:
                j += 1
            body_text.append(line[i:j], style=st)
            i = j

        if cursor_at_eol or sel_spans_newline:
            body_text.append(" ", style=col_styles[n])

        try:
            console = self.app.console
            gutter_strip = Strip(gutter_text.render(console))
            body_strip = Strip(body_text.render(console))
            scroll_x = self.scroll_offset.x
            body_width = max(0, self._content_width() - gutter)
            body_strip = body_strip.crop(scroll_x, scroll_x + body_width)
            body_strip = body_strip.adjust_cell_length(body_width, base)
            return Strip.join([gutter_strip, body_strip])
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

    def _syntax_spans_rendered(self, buf_row: int) -> list[tuple[int, int, str]]:
        """Syntax spans for a buffer row, mapped to rendered-column coords."""
        if not self._highlight_enabled or not self._highlighter.enabled:
            return []
        if buf_row < 0 or buf_row >= len(self._syntax_spans):
            return []
        out: list[tuple[int, int, str]] = []
        for s in self._syntax_spans[buf_row]:
            start = self._buffer_col_to_rendered_col(buf_row, s.start)
            end = self._buffer_col_to_rendered_col(buf_row, s.end)
            if end > start:
                out.append((start, end, f"editor.syntax.{s.role}"))
        return out

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
        cursor_x = self._buffer_col_to_rendered_col(
            self.buffer.cursor_row, self.buffer.cursor_col
        )
        try:
            visible_top = self.scroll_offset.y
            visible_bottom = visible_top + self.size.height - 2
            target_y = None
            if cursor_y < visible_top:
                target_y = cursor_y
            elif cursor_y > visible_bottom:
                target_y = cursor_y - self.size.height + 2

            scroll_x = self.scroll_offset.x
            body_width = max(1, self._content_width() - self._gutter_width())
            target_x = None
            if cursor_x < scroll_x:
                target_x = cursor_x
            elif cursor_x >= scroll_x + body_width:
                target_x = cursor_x - body_width + 1

            if target_x is not None or target_y is not None:
                self.scroll_to(
                    x=scroll_x if target_x is None else target_x,
                    y=self.scroll_offset.y if target_y is None else target_y,
                    animate=False,
                )
        except NoActiveAppError:
            pass

    def _post_buffer_update(self) -> None:
        self._rescan_folds()
        self._refresh_render()
        self._schedule_syntax()
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

    def action_select_document_start(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_document_start()
        self._snap_cursor_to_visible_row(direction=1)
        self._update_selection_to_cursor()

    def action_select_document_end(self) -> None:
        self._start_or_extend_selection()
        self.buffer.move_cursor_document_end()
        self._snap_cursor_to_visible_row(direction=-1)
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

    def action_document_start(self) -> None:
        self.buffer.move_cursor_document_start()
        self._snap_cursor_to_visible_row(direction=1)
        self._post_cursor_update()

    def action_document_end(self) -> None:
        self.buffer.move_cursor_document_end()
        self._snap_cursor_to_visible_row(direction=-1)
        self._post_cursor_update()

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

    def on_paste(self, event: events.Paste) -> None:
        """Handle terminal bracketed paste (Cmd+V / right-click paste).

        Cmd+V is intercepted by the terminal, which injects the clipboard as a
        Paste event rather than a key — so it never reaches the ctrl+v binding.
        Insert the pasted text directly at the cursor.
        """
        if not self.has_focus:
            return
        text = event.text
        if not text:
            return
        if self.buffer.has_selection:
            self._delete_collapsed_in_selection()
            self.buffer.delete_selection()
        self._unfold_at_cursor()
        self.buffer.insert_text(text)
        self._post_buffer_update()
        event.prevent_default()
        event.stop()

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
        rendered_col = max(0, x - gutter + self.scroll_offset.x)
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
        # The TextBuffer writes to the system clipboard (pbcopy/xclip).
        text = self.buffer.copy_selection()
        # OSC 52 fallback so copy also works over SSH/remote sessions.
        if text:
            try:
                self.app.copy_to_clipboard(text)
            except Exception:
                pass
        # Refresh cursor to reflect any visual changes (e.g., selection cleared).
        self._post_cursor_update()

    def action_cut(self) -> None:
        """Cut the current selection (or line) to the OS clipboard."""
        # The TextBuffer writes to the system clipboard (pbcopy/xclip).
        text = self.buffer.cut_selection()
        # OSC 52 fallback so cut also works over SSH/remote sessions.
        if text:
            try:
                self.app.copy_to_clipboard(text)
            except Exception:
                pass
        # Cut changes buffer contents, so do a full buffer refresh.
        self._post_buffer_update()

    def action_paste(self) -> None:
        """Paste text from the OS clipboard into the buffer at the cursor."""
        # Fall back to Textual's clipboard (OSC 52) when the local system
        # clipboard is empty/unreachable (SSH).
        fallback = ""
        try:
            fallback = self.app.clipboard or ""
        except Exception:
            fallback = ""
        self.buffer.paste(fallback=fallback)
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
