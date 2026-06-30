"""CsvViewerContent — view delimited text (CSV/TSV) as an aligned table (F3).

Two modes share one scroll position, toggled with Ctrl+T:
  * ``table`` — fields parsed with :mod:`csv`, columns aligned to a per-column
    width (capped + ellipsised), row 0 styled as a header.
  * ``raw``   — the original text, one line per row (what the plain viewer shows).

The delimiter is auto-detected on open (``sniff_delimiter``) and can be cycled
through the common candidates (``,`` ``;`` tab ``|``) with the ``d`` hotkey.

The parse/measure helpers (``sniff_delimiter``, ``parse_csv``, ``column_widths``,
``fit_cell``) are pure and import nothing heavy, so they unit-test in isolation;
the widget only adds rich styling + scrolling on top.
"""

from __future__ import annotations

import codecs
import csv
import io
import unicodedata
from contextlib import suppress
from pathlib import Path

from rich.cells import cell_len, set_cell_size
from rich.segment import Segment
from rich.style import Style as RichStyle
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Container, Horizontal
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import TextArea

from dunders.fm.dialogs import ShadowButton
from dunders.fm.image_viewer import _ToolbarButton
from dunders.windowing.content import WindowContent, WindowCommand
from dunders.windowing.helpers import ModalWindow, show_modal
from dunders.windowing.palette import Palette
from dunders.windowing.window import Window
from dunders.fm.line_source import (
    LineSource as _LineSource,
    TextSource as _TextSource,
    MmapSource as _MmapSource,
)


__all__ = [
    "decode_text",
    "looks_utf16",
    "sniff_delimiter",
    "parse_csv",
    "column_widths",
    "fit_cell",
    "CsvViewerContent",
    "CsvViewerWidget",
    "CsvCellDialog",
]

# How many leading rows to sample for column widths / delimiter. The whole
# point of the lazy viewer is to NOT scan a huge file on open, so widths are
# estimated from this prefix (cells truncate at _MAX_COL_WIDTH anyway).
_WIDTH_SAMPLE_ROWS = 200
# Lines indexed per background tick while growing the scrollbar to its true
# height. ~250k newline scans ≈ a few ms — small enough to keep the UI smooth.
_FILL_BATCH_LINES = 250_000
# Minimum digit width of the line-number gutter (grows for files with more rows).
_MIN_GUTTER_DIGITS = 3


def looks_utf16(sample: bytes) -> bool:
    """True if ``sample`` looks like UTF-16 (BOM, or NUL-heavy).

    Used to keep UTF-16 files off the byte-level mmap fast path (whose newline
    scan assumes single-byte ``\\n``); they take the decode-into-memory path.
    """
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return True
    head = sample[:8192]
    return bool(head) and head.count(b"\x00") > len(head) // 4


def decode_text(raw: bytes) -> str:
    """Decode file bytes to text for the CSV viewer.

    Spreadsheets routinely export CSV as **UTF-16** (Excel's "Unicode text"),
    which is full of NUL bytes — those make the cheap binary sniff treat the
    file as binary and send it to the hex viewer. Honour a BOM, then guess
    UTF-16 from a NUL-heavy sample, and only then fall back to a lossy read, so
    such files still tabulate instead of showing hex.
    """
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16", errors="replace")
    # A NUL-heavy sample with no BOM is almost certainly UTF-16 — and crucially
    # this must be checked BEFORE utf-8, because ASCII-in-UTF-16 (e.g. ``a\x00``)
    # decodes "successfully" as UTF-8 into a NUL-riddled string rather than
    # raising. Guess endianness by trying each.
    sample = raw[:8192]
    if sample and sample.count(b"\x00") > len(sample) // 4:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    try:
        return raw.decode("utf-8-sig")  # strips a UTF-8 BOM if present
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")

# Delimiters we auto-detect and cycle through, in priority order.
DELIMITERS = (",", ";", "\t", "|")
# Per-column display cap so one wide free-text column can't push everything off
# screen; longer cells are truncated with an ellipsis.
_MAX_COL_WIDTH = 48
_COL_SEP = " │ "


def sniff_delimiter(text: str, candidates: str = ",;\t|") -> str:
    """Guess the field delimiter of ``text``. Falls back to whichever candidate
    occurs most on the first non-empty line, then to a comma."""
    sample = text[:8192]
    with suppress(Exception):
        dialect = csv.Sniffer().sniff(sample, delimiters=candidates)
        if dialect.delimiter in candidates:
            return dialect.delimiter
    first = ""
    for line in sample.splitlines():
        if line.strip():
            first = line
            break
    if first:
        best = max(candidates, key=first.count)
        if first.count(best) > 0:
            return best
    return ","


def parse_csv(text: str, delimiter: str) -> list[list[str]]:
    """Parse ``text`` into rows of fields with the stdlib csv reader (handles
    quoting and embedded delimiters/newlines). On any reader error, fall back to
    a naive per-line split so the viewer still shows *something*."""
    try:
        return list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error:
        return [line.split(delimiter) for line in text.splitlines()]


def column_widths(
    rows: list[list[str]], max_width: int = _MAX_COL_WIDTH
) -> list[int]:
    """Width of each column = the widest cell in it, clamped to ``[1, max_width]``.

    Width is measured in terminal *cells* (``cell_len``), not characters, so a
    column of CJK / full-width text (each glyph spans 2 cells) is sized to its
    real on-screen footprint and the column separators stay aligned."""
    widths: list[int] = []
    for row in rows:
        for i, cell in enumerate(row):
            w = min(max_width, max(1, cell_len(cell)))
            if i < len(widths):
                widths[i] = max(widths[i], w)
            else:
                widths.append(w)
    return widths


def _display_safe(value: str) -> str:
    """Make a cell safe to lay out as a fixed-width column.

    Flattens embedded whitespace, then drops zero-width combining marks that
    terminals render inconsistently. ``cell_len`` (correctly) counts a combining
    mark as 0 cells, but many terminals/fonts can't compose an *orphan* mark
    onto its base — e.g. Turkish dotted-i ``i̇`` ("İ" lowercased) paints
    the dot as its own spacing cell — so our width math desyncs from what's drawn
    and every column to the right shifts. NFC first so composable diacritics
    (most Latin/European text) survive as single precomposed glyphs; only the
    remaining un-composable marks are removed. Pure & cheap (fast path when the
    cell has no combining marks)."""
    value = value.replace("\n", " ").replace("\t", " ")
    if any(unicodedata.combining(ch) for ch in value):
        value = unicodedata.normalize("NFC", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value


def fit_cell(value: str, width: int) -> str:
    """Pad ``value`` to ``width`` *cells* (left-justified) or truncate with an
    ellipsis. Uses cell width (``set_cell_size``), so a full-width CJK glyph
    counts as 2 — padding/truncation land on real terminal-column boundaries."""
    value = _display_safe(value)
    if cell_len(value) > width:
        if width <= 1:
            return "…"[:width]
        # Truncate the body to width-1 cells, then the ellipsis fills the last.
        return set_cell_size(value, width - 1) + "…"
    return set_cell_size(value, width)


def _split_line(line: str, delimiter: str) -> list[str]:
    """Split one physical line into fields, honouring in-line quoting.

    Per-line (not whole-document) parsing is what lets the viewer render only
    the visible rows of a huge file. The trade-off: a quoted field with an
    embedded newline is shown as separate physical rows."""
    try:
        return next(csv.reader([line], delimiter=delimiter))
    except (csv.Error, StopIteration):
        return line.split(delimiter)


class CsvViewerWidget(ScrollView):
    """Renders parsed CSV as an aligned table (or the raw text)."""

    class CellActivated(Message):
        """Posted (Enter/click in table mode) with the full, un-truncated value
        of the cursor cell so the wrapper can open the view dialog."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    DEFAULT_CSS = """
    CsvViewerWidget {
        background: $surface;
        color: $text;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("up",       "scroll_lines(-1)", show=False),
        Binding("down",     "scroll_lines(1)",  show=False),
        Binding("left",     "scroll_cols(-4)",  show=False),
        Binding("right",    "scroll_cols(4)",   show=False),
        Binding("pageup",   "scroll_page(-1)",  show=False),
        Binding("pagedown", "scroll_page(1)",   show=False),
        Binding("home",     "scroll_home",      show=False),
        Binding("end",      "scroll_end",       show=False),
        Binding("enter",    "activate",         show=False),
    ]

    def __init__(self, source: _LineSource) -> None:
        super().__init__()
        self._source = source
        self._mode = "table"
        self._delimiter = sniff_delimiter(source.sample())
        self._widths: list[int] = []
        self._raw_max = 1
        # Substring filter: when active, only matching rows are shown. _matches
        # holds every matching source line index (incl. the header if it
        # matches); _table_body drops the header (it is always shown frozen).
        self._filter: str | None = None
        self._matches: list[int] | None = None
        self._table_body: list[int] = []
        # Cell cursor (table mode only): position over the body display rows and
        # the current column. Arrows move the cursor (not the viewport); Enter /
        # click opens the full-cell dialog.
        self._cursor_disp: int = 0
        self._cursor_col: int = 0
        self._reparse()

    # --- state ----------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def delimiter(self) -> str:
        return self._delimiter

    @property
    def n_rows(self) -> int:
        return self._source.line_count()

    @property
    def n_cols(self) -> int:
        return len(self._widths)

    @property
    def filter_query(self) -> str | None:
        return self._filter

    @property
    def match_count(self) -> int | None:
        """Number of matching data rows, or None when no filter is active."""
        return None if self._matches is None else len(self._table_body)

    # --- filter ---------------------------------------------------------

    def apply_filter(self, query: str) -> None:
        """Show only rows whose text contains ``query`` (case-insensitive).

        Requires a full scan, so the file is indexed to the end first — a
        one-off cost paid only when the user explicitly filters."""
        q = (query or "").strip()
        if not q:
            self.clear_filter()
            return
        needle = q.lower()
        while self._source.index_batch(_FILL_BATCH_LINES):
            pass  # filtering needs every row, so finish the newline index
        n = self._source.line_count()
        matches = [i for i in range(n) if needle in self._source.line(i).lower()]
        self._filter = q
        self._matches = matches
        self._table_body = [m for m in matches if m != 0]
        self._resize_canvas()
        if self.is_mounted:
            self.scroll_to(0, 0, animate=False)
            self.refresh()

    def clear_filter(self) -> None:
        self._filter = None
        self._matches = None
        self._table_body = []
        self._resize_canvas()
        if self.is_mounted:
            self.refresh()

    def _body_count(self) -> int:
        """Number of scrollable body rows (excludes the frozen table header)."""
        if self._matches is None:
            if self._mode == "table":
                return max(0, self._source.line_count() - 1)
            return self._source.line_count()
        return len(self._table_body) if self._mode == "table" else len(self._matches)

    def _body_idx(self, d: int) -> int:
        """Source line index for body display position ``d`` (-1 if none)."""
        if d < 0:
            return -1
        if self._matches is None:
            idx = d + 1 if self._mode == "table" else d
        else:
            seq = self._table_body if self._mode == "table" else self._matches
            idx = seq[d] if d < len(seq) else -1
        return idx if 0 <= idx < self._source.line_count() else -1

    def toggle_mode(self) -> None:
        self._mode = "raw" if self._mode == "table" else "table"
        self._resize_canvas()
        if self.is_mounted:
            self.scroll_to(0, 0, animate=False)
            self.refresh()

    def cycle_delimiter(self) -> None:
        idx = DELIMITERS.index(self._delimiter) if self._delimiter in DELIMITERS else -1
        self._delimiter = DELIMITERS[(idx + 1) % len(DELIMITERS)]
        self._reparse()
        if self.is_mounted:
            self.scroll_to(0, 0, animate=False)
            self.refresh()

    def _reparse(self) -> None:
        # Sample only the leading rows — never the whole (possibly huge) file.
        sample_n = min(self._source.line_count(), _WIDTH_SAMPLE_ROWS)
        rows: list[list[str]] = []
        raw_max = 1
        for i in range(sample_n):
            line = self._source.line(i)
            raw_max = max(raw_max, len(line))
            rows.append(_split_line(line, self._delimiter))
        self._widths = column_widths(rows)
        self._raw_max = raw_max
        self._resize_canvas()

    def _table_width(self) -> int:
        if not self._widths:
            return 1
        return sum(self._widths) + len(_COL_SEP) * (len(self._widths) - 1)

    def _gutter_width(self) -> int:
        """Width of the line-number column = digit count (+1 separator space)."""
        rows = max(1, self._source.line_count())
        return max(_MIN_GUTTER_DIGITS, len(str(rows))) + 1

    def _resize_canvas(self) -> None:
        gutter = self._gutter_width()
        body = self._table_width() if self._mode == "table" else self._raw_max
        # Height counts the scrollable body rows (which respect the filter) plus
        # the frozen header in table mode.
        height = self._body_count() + (1 if self._mode == "table" else 0)
        # The gutter is fixed (never scrolls), so it consumes part of the virtual
        # width — add it so the last data column is still reachable by scroll.
        self.virtual_size = Size(max(1, body + gutter), max(1, height))

    def on_resize(self) -> None:
        # ScrollView already reflows; nothing to recompute (widths are content
        # derived, not viewport derived), but keep the hook for symmetry.
        pass

    # --- palette --------------------------------------------------------

    def _get_palette(self) -> Palette | None:
        with suppress(Exception):
            for ancestor in self.ancestors_with_self:
                pal = getattr(ancestor, "palette", None)
                if isinstance(pal, Palette):
                    return pal
        return None

    def _rich_style(self, role: str) -> RichStyle:
        pal = self._get_palette()
        if pal is None:
            return RichStyle()
        return pal.rich_style(role)

    # --- rendering ------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        scroll_x = int(self.scroll_offset.x)
        scroll_y = int(self.scroll_offset.y)
        gutter_w = self._gutter_width()
        body_w = max(1, self.size.width - gutter_w)

        cursor_col: int | None = None
        if self._mode == "table":
            # Row 0 is the column-name header — freeze it at the top so it stays
            # visible while the (possibly filtered) data rows scroll under it.
            if y == 0:
                idx = 0 if self._source.line_count() > 0 else -1
                number = None
            else:
                disp = scroll_y + y - 1
                idx = self._body_idx(disp)
                number = idx if idx >= 0 else None
                # Highlight the cursor's cell on its row (never the header).
                if idx >= 0 and disp == self._cursor_disp:
                    cursor_col = self._cursor_col
        else:
            idx = self._body_idx(scroll_y + y)
            number = idx + 1 if idx >= 0 else None

        if idx >= 0:
            body = (
                self._render_raw_line(idx)
                if self._mode == "raw"
                else self._render_table_line(idx, cursor_col=cursor_col)
            )
        else:
            body = Strip([])
            number = None
        # The body scrolls horizontally (crop by scroll_x); the gutter does not.
        body = body.crop(scroll_x, scroll_x + body_w)
        body = body.adjust_cell_length(body_w, self.rich_style)
        gutter = self._render_gutter(number, gutter_w)
        return Strip.join([gutter, body])

    def _render_gutter(self, number: int | None, width: int) -> Strip:
        """Fixed left column with the row number in the line-number colour."""
        style = self._rich_style("editor.line_numbers")
        if number is None:
            text = " " * width
        else:
            text = str(number).rjust(width - 1)[: width - 1] + " "
        return Strip([Segment(text, style)])

    def _render_raw_line(self, idx: int) -> Strip:
        text = Text(self._source.line(idx), style=self._rich_style("editor.text"))
        return Strip(text.render(self.app.console))

    def _render_table_line(self, idx: int, *, cursor_col: int | None = None) -> Strip:
        is_header = idx == 0
        cell_style = self._rich_style("editor.text")
        base_style = self._rich_style("menu.item.active") if is_header else cell_style
        # Reverse-video so the cursor cell is unmistakable in ANY theme (a palette
        # role like menu.item.active can be too low-contrast to read as a cursor).
        cursor_style = cell_style + RichStyle(reverse=True, bold=True)
        sep_style = self._rich_style("editor.line_numbers")
        text = Text(style=self.rich_style)
        # Parse just this one visible line (lazy — never the whole file).
        row = _split_line(self._source.line(idx), self._delimiter)
        # A row may carry more fields than the sampled widths saw (ragged data);
        # render those extra columns at the per-column cap so nothing vanishes.
        ncols = max(len(self._widths), len(row))
        last = ncols - 1
        for i in range(ncols):
            width = self._widths[i] if i < len(self._widths) else _MAX_COL_WIDTH
            value = row[i] if i < len(row) else ""
            style = cursor_style if i == cursor_col else base_style
            text.append(fit_cell(value, width), style=style)
            if i != last:
                text.append(_COL_SEP, style=sep_style)
        return Strip(text.render(self.app.console))

    # --- cursor (table mode) --------------------------------------------

    def _ncols_at_cursor(self) -> int:
        """Column count = sampled widths, falling back to the cursor row's own
        field count (ragged rows) so the cursor can reach every field."""
        n = len(self._widths)
        idx = self._body_idx(self._cursor_disp)
        if idx >= 0:
            n = max(n, len(_split_line(self._source.line(idx), self._delimiter)))
        return max(1, n)

    def _clamp_cursor(self) -> None:
        body = self._body_count()
        if body <= 0:
            self._cursor_disp = 0
        else:
            self._cursor_disp = max(0, min(self._cursor_disp, body - 1))
        ncols = self._ncols_at_cursor()
        self._cursor_col = max(0, min(self._cursor_col, ncols - 1))

    def _move_cursor(self, *, drow: int = 0, dcol: int = 0,
                     to_col: int | None = None) -> None:
        self._cursor_disp += drow
        if to_col is not None:
            self._cursor_col = to_col
        else:
            self._cursor_col += dcol
        self._clamp_cursor()
        self._scroll_to_cursor()
        self.refresh()

    def _scroll_to_cursor(self) -> None:
        """Adjust scroll_offset so the cursor cell is visible.

        Vertically: the cursor's display row renders at ``y = 1 + (disp -
        scroll_y)`` (y==0 is the frozen header), so keep that y within
        ``[1, height-1]``. Horizontally: keep the cursor column's body x-range
        within ``[scroll_x, scroll_x + body_w]``."""
        scroll_x = int(self.scroll_offset.x)
        scroll_y = int(self.scroll_offset.y)
        # Use the scrollable content region (excludes the scrollbars) — using
        # self.size would let the cursor land on the row hidden under a
        # horizontal scrollbar (one line below the visible area).
        region = self.scrollable_content_region
        height = max(1, region.height)
        # Vertical: y = 1 + (disp - scroll_y) must be in [1, height-1].
        if self._cursor_disp < scroll_y:
            scroll_y = self._cursor_disp
        elif self._cursor_disp > scroll_y + (height - 2):
            scroll_y = self._cursor_disp - (height - 2)
        scroll_y = max(0, scroll_y)
        # Horizontal: the cursor column's body x-range within the body viewport.
        col = self._cursor_col
        x0 = sum(self._widths[:col]) + len(_COL_SEP) * col
        w = self._widths[col] if col < len(self._widths) else _MAX_COL_WIDTH
        x1 = x0 + w
        body_w = max(1, region.width - self._gutter_width())
        if x0 < scroll_x:
            scroll_x = x0
        elif x1 > scroll_x + body_w:
            scroll_x = x1 - body_w
        scroll_x = max(0, scroll_x)
        if scroll_x != int(self.scroll_offset.x) or scroll_y != int(self.scroll_offset.y):
            self.scroll_to(scroll_x, scroll_y, animate=False)

    def _cursor_cell_value(self) -> str:
        """The full (un-truncated) value of the cursor cell, with ``\\n`` kept."""
        idx = self._body_idx(self._cursor_disp)
        if idx < 0:
            return ""
        fields = _split_line(self._source.line(idx), self._delimiter)
        col = self._cursor_col
        return fields[col] if 0 <= col < len(fields) else ""

    def _activate_cursor(self) -> None:
        if self._mode != "table" or self._body_count() <= 0:
            return
        self.post_message(self.CellActivated(self._cursor_cell_value()))

    # --- mouse ----------------------------------------------------------

    def on_click(self, event: events.Click) -> None:
        """Table mode: map the click to a cell, move the cursor there, open the
        dialog. Out-of-range clicks (gutter, past last row/col) are ignored."""
        if self._mode != "table" or self._body_count() <= 0:
            return
        gutter_w = self._gutter_width()
        if event.x < gutter_w:
            return  # gutter — no cell
        scroll_x = int(self.scroll_offset.x)
        scroll_y = int(self.scroll_offset.y)
        # y==0 is the frozen header; data rows start at y==1.
        disp = scroll_y if event.y == 0 else scroll_y + (event.y - 1)
        if disp < 0 or disp >= self._body_count():
            return
        bx = event.x - gutter_w + scroll_x
        if bx < 0:
            return
        # Walk columns accumulating width + separator until bx lands in one.
        ncols = len(self._widths)
        col = None
        acc = 0
        for i in range(ncols):
            w = self._widths[i]
            if bx < acc + w:
                col = i
                break
            acc += w + len(_COL_SEP)
            if bx < acc:  # landed on the separator — treat as this column
                col = i
                break
        if col is None:
            return  # past the last column
        self._cursor_disp = disp
        self._cursor_col = col
        self._clamp_cursor()
        self.refresh()
        self._activate_cursor()

    def _on_mouse_scroll_left(self, event: events.MouseScrollLeft) -> None:
        # Disable horizontal wheel scrolling (trackpads/tilt wheels) — it made
        # the table drift sideways unexpectedly. Vertical wheel is untouched.
        event.stop()
        event.prevent_default()

    def _on_mouse_scroll_right(self, event: events.MouseScrollRight) -> None:
        event.stop()
        event.prevent_default()

    # --- scroll / navigation actions ------------------------------------

    def action_scroll_lines(self, delta: int) -> None:
        if self._mode == "table":
            self._move_cursor(drow=delta)
            return
        self.scroll_to(
            self.scroll_offset.x, self.scroll_offset.y + delta, animate=False
        )

    def action_scroll_cols(self, delta: int) -> None:
        if self._mode == "table":
            # Cursor moves one column per arrow (the binding passes ±4 for the
            # raw-mode scroll step, but a cell cursor steps by one).
            self._move_cursor(dcol=1 if delta > 0 else -1)
            return
        self.scroll_to(
            self.scroll_offset.x + delta, self.scroll_offset.y, animate=False
        )

    def action_scroll_page(self, sign: int) -> None:
        page = max(1, self.size.height - 2)
        if self._mode == "table":
            self._move_cursor(drow=sign * page)
            return
        self.scroll_to(
            self.scroll_offset.x, self.scroll_offset.y + sign * page, animate=False
        )

    def action_scroll_home(self) -> None:
        if self._mode == "table":
            self._move_cursor(to_col=0)
            return
        self.scroll_to(0, 0, animate=False)

    def action_scroll_end(self) -> None:
        if self._mode == "table":
            self._move_cursor(to_col=self._ncols_at_cursor() - 1)
            return
        rows = self.virtual_size.height  # respects the active filter
        self.scroll_to(0, max(0, rows - max(1, self.size.height)), animate=False)

    def action_activate(self) -> None:
        self._activate_cursor()


class CsvCellDialog(Container, WindowContent):
    """A read-only modal showing one CSV cell's full (un-truncated) value.

    Mirrors ``db_console.CellEditDialog`` but view-only: the value is shown in a
    read-only, scrollable ``TextArea`` (newlines preserved); ``Close``/Esc
    dismiss. Like ``SqlHistoryDialog`` the dialog posts ``Window.Closed`` up to
    the enclosing ``ModalWindow`` itself (its ``Dismissed`` message has no
    handler)."""

    DEFAULT_CSS = """
    CsvCellDialog { layout: vertical; width: 80; height: 1fr; padding: 1 1; }
    CsvCellDialog #cc-edit { height: 1fr; border: none; }
    CsvCellDialog #cc-edit:focus { border: none; }
    CsvCellDialog #cc-buttons { height: 1; align: center middle; margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, value: str) -> None:
        super().__init__()
        self.window_title = "Cell"
        self._value = value
        # A cell parsed from one physical line can't hold a real newline, so
        # exported data carries the literal escape ``\n`` (backslash + n). Show
        # those as actual line breaks so multi-line content reads naturally.
        self._area = TextArea(self._unescape(value), read_only=True, id="cc-edit")

    @staticmethod
    def _unescape(text: str) -> str:
        """Turn literal ``\\r\\n`` / ``\\n`` / ``\\r`` escapes into real line
        breaks (real newlines already in the text are left untouched)."""
        return (
            text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
        )

    def compose(self) -> ComposeResult:
        yield self._area
        with Horizontal(id="cc-buttons"):
            yield ShadowButton("Close", id="cc-close",
                               face_bg="rgb(80,80,90)", hotkey="c")

    def on_mount(self) -> None:
        # Read-only: no clashing current-line highlight block (see UI cookbook).
        self._area.highlight_cursor_line = False
        self.apply_theme()
        self.call_after_refresh(self._area.focus)

    def apply_theme(self) -> None:
        """Paint the surface + read-only editor from the active palette so the
        dialog matches the skin instead of Textual's stock $surface look (see
        docs/textual-ui-cookbook.md). CSS stays layout-only."""
        pal = self._get_palette()
        if pal is None:
            self.refresh()
            return
        content = pal.get("window.content")
        sunken = pal.get("desktop.background")
        area_bg = sunken.bg or content.bg
        # Dialog surface.
        if content.bg is not None:
            self.styles.background = content.bg
        if content.fg is not None:
            self.styles.color = content.fg
        # The read-only text area: sunken palette background, no Textual tint,
        # so the field reads as a themed panel (border removed via CSS).
        with suppress(Exception):
            if area_bg is not None:
                self._area.styles.background = area_bg
            if content.fg is not None:
                self._area.styles.color = content.fg
            self._area.styles.background_tint = Color(0, 0, 0, 0)
        self.refresh()

    def _get_palette(self) -> Palette | None:
        with suppress(Exception):
            for ancestor in self.ancestors_with_self:
                pal = getattr(ancestor, "palette", None)
                if isinstance(pal, Palette):
                    return pal
        return None

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        if event.button.id == "cc-close":
            self._dismiss_modal()

    def action_close(self) -> None:
        self._dismiss_modal()

    def _dismiss_modal(self) -> None:
        node = self
        while node is not None:
            if isinstance(node, ModalWindow):
                node.post_message(Window.Closed(node))
                return
            node = getattr(node, "parent", None)


class CsvViewerContent(WindowContent):
    """WindowContent wrapping :class:`CsvViewerWidget` with mode/delimiter toggles."""

    DEFAULT_CSS = """
    CsvViewerContent { background: transparent; layout: vertical; }
    CsvViewerContent CsvViewerWidget {
        height: 1fr;
        width: 1fr;
    }
    CsvViewerContent .csv-toolbar { height: 1; background: $panel; padding: 0 1; }
    """

    class FilterRequested(Message):
        """Asks the app to prompt for a substring filter (Ctrl+F)."""

        def __init__(self, content: "CsvViewerContent") -> None:
            super().__init__()
            self.content = content

    def __init__(
        self,
        initial_text: str = "",
        *,
        display_name: str | None = None,
        file_path: str | Path | None = None,
        source: _LineSource | None = None,
    ) -> None:
        super().__init__()
        name = display_name or (Path(file_path).name if file_path else "data")
        self.window_title = f"CSV: {name}"
        self._source = source if source is not None else _TextSource(initial_text)
        self._widget = CsvViewerWidget(self._source)
        # Visible Table⇄Raw toggle (mirrors the Ctrl+T command). Label shows the
        # mode it switches TO.
        self._mode_btn = _ToolbarButton(self._mode_label(), on_press=self._toggle_mode)
        self._fill_timer = None
        # Set when the source is a throwaway temp file (a downloaded remote/
        # archive member): unlinked on unmount.
        self._cleanup_path: Path | None = None

    @classmethod
    def from_bytes(cls, name: str, data: bytes) -> "CsvViewerContent":
        """Build a CSV viewer over an in-memory buffer (e.g. a file read through
        a VFS provider where there is no local path)."""
        return cls(decode_text(data), display_name=name)

    @classmethod
    def from_path(
        cls, path: str | Path, *, owns_file: bool = False,
        display_name: str | None = None,
    ) -> "CsvViewerContent":
        """Build a CSV viewer that mmaps ``path`` and renders lazily — large
        files open instantly instead of being read+parsed in full.

        ``owns_file=True`` marks ``path`` as a throwaway temp (e.g. a downloaded
        remote/archive member) to delete when the viewer closes; ``display_name``
        overrides the title (the temp name is ugly)."""
        p = Path(path)
        inst = cls(source=_MmapSource(p), display_name=display_name or p.name)
        if owns_file:
            inst._cleanup_path = p
            # POSIX: drop the directory entry now that the file is mmap'd. The
            # mapping keeps the bytes readable, but the file can no longer leak
            # on disk — even if we crash before on_unmount runs. Windows can't
            # unlink an open file, so it stays and is removed on unmount.
            try:
                p.unlink()
            except OSError:
                pass
            else:
                inst._cleanup_path = None
        return inst

    def _mode_label(self) -> str:
        # Show the mode the button switches TO.
        return "[ Raw ]" if self._widget._mode == "table" else "[ Table ]"

    def compose(self) -> ComposeResult:
        yield self._widget
        with Horizontal(classes="csv-toolbar"):
            yield self._mode_btn

    def on_mount(self) -> None:
        self._widget.focus()
        self._update_subtitle()
        # Finish indexing a big file in the background so the scrollbar grows to
        # its true height without blocking the open.
        if not self._source.is_complete():
            self._fill_timer = self.set_interval(0.05, self._fill_tick)

    def on_window_focus(self) -> None:
        # Called by Desktop.focus_window when this window (re)gains focus — e.g.
        # after the cell dialog closes. Route focus into the scrollable widget so
        # cell-cursor navigation resumes instead of dying on the content wrapper.
        self._widget.focus()

    def _fill_tick(self) -> None:
        more = self._source.index_batch(_FILL_BATCH_LINES)
        self._widget._resize_canvas()
        self._widget.refresh()
        self._update_subtitle()
        if not more and self._fill_timer is not None:
            self._fill_timer.stop()
            self._fill_timer = None

    def on_unmount(self) -> None:
        # Stop the indexer before closing so a pending tick can't touch a closed
        # mmap, then release the mmap / file descriptor.
        if self._fill_timer is not None:
            self._fill_timer.stop()
            self._fill_timer = None
        self._source.close()
        if self._cleanup_path is not None:
            with suppress(OSError):
                self._cleanup_path.unlink()
            self._cleanup_path = None

    @property
    def widget(self) -> CsvViewerWidget:
        return self._widget

    @property
    def filter_query(self) -> str:
        """Active filter substring (empty when none) — prefills the prompt."""
        return self._widget.filter_query or ""

    def apply_filter(self, query: str) -> None:
        self._widget.apply_filter(query)
        self._update_subtitle()

    def clear_filter(self) -> None:
        self._widget.clear_filter()
        self._update_subtitle()

    def _delim_label(self) -> str:
        return {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(
            self._widget.delimiter, repr(self._widget.delimiter)
        )

    def _update_subtitle(self) -> None:
        # Row / match counts get thousands separators (e.g. 1,234,567) so a
        # multi-GB CSV's size reads at a glance.
        subtitle = (
            f"{self._widget.mode.upper()}  ·  delim: {self._delim_label()}  ·  "
            f"{self._widget.n_cols}×{self._widget.n_rows:,}"
        )
        if self._widget.filter_query is not None:
            matches = self._widget.match_count or 0
            subtitle += (
                f"  ·  filter: {self._widget.filter_query!r} "
                f"({matches:,})"
            )
        self.window_subtitle = subtitle

    def _toggle_mode(self) -> None:
        self._widget.toggle_mode()
        self._mode_btn.set_label(self._mode_label())
        self._update_subtitle()

    def _cycle_delimiter(self) -> None:
        self._widget.cycle_delimiter()
        self._update_subtitle()

    def _request_filter(self) -> None:
        self.post_message(CsvViewerContent.FilterRequested(self))

    def on_csv_viewer_widget_cell_activated(
        self, event: "CsvViewerWidget.CellActivated"
    ) -> None:
        """Open the read-only full-cell dialog for the activated cell."""
        event.stop()
        desktop = getattr(self.app, "desktop", None)
        if desktop is None:
            from dunders.windowing import Desktop
            with suppress(Exception):
                desktop = self.app.query_one(Desktop)
        if desktop is None:
            return
        show_modal(desktop, CsvCellDialog(event.value), title="Cell", size=(80, 20))

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(
                id="csv.toggle_mode",
                label="Toggle Table/Raw",
                handler=self._toggle_mode,
                hotkey="ctrl+t",
            ),
            WindowCommand(
                id="csv.cycle_delimiter",
                label="Cycle delimiter",
                handler=self._cycle_delimiter,
                hotkey="d",
            ),
            WindowCommand(
                id="csv.filter",
                label="Filter rows…",
                handler=self._request_filter,
                hotkey="ctrl+f",
            ),
        ]
