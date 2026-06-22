"""DbConsoleContent — a SQL editor over a lazy result grid for the db: dunder.

Layout: a toolbar (``[ Run ]`` button + a status line) above the app-native SQL
editor (``EditorWidget`` — same Turbo Vision-styled, palette-driven editor as the
rest of the app, with SQL syntax highlighting) above a result grid. SQL runs via
the button, the ``Ctrl+R`` hotkey, or the menu — ``Ctrl+Enter`` was dropped
because most terminals can't distinguish it from a plain ``Enter`` (which the
editor needs for newlines). Close with ``Esc`` or ``Ctrl+W`` like any other
window.
"""

from __future__ import annotations

import ast
import json
from collections import namedtuple

from rich.markdown import Markdown as _RichMarkdown
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static

from dunders.config import sql_history
from dunders.fm.dialogs import ShadowButton, _BookmarkTable
from dunders.fm.image_viewer import _ToolbarButton
from dunders.fm.providers import db_access as da
from dunders.windowing.content import WindowCommand, WindowContent
from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.editor.splitter import Splitter
from dunders.windowing.editor.widget import EditorWidget
from dunders.windowing.helpers import ModalWindow, show_modal
from dunders.windowing.window import Window

__all__ = ["DbConsoleContent", "SqlHistoryDialog", "CellEditDialog"]

_RESULT_CAP = 1000
_PAGE = 200  # rows per page for a paginated SELECT result
_CELL_MAX = 60  # clip a cell's display text to this many chars (… elides the rest)


def _is_pageable(sql: str) -> bool:
    """True if ``sql`` is a row-returning statement we can wrap for paging.

    Only SELECT/WITH/VALUES results are paginated (LIMIT/OFFSET over a
    sub-query); everything else (INSERT/UPDATE/DDL/PRAGMA) runs un-paged and
    just reports a row count."""
    s = sql.strip().lower()
    return s.startswith(("select", "with", "values"))

# A resolved result-grid cell: its column, raw value, full (untruncated) text,
# whether it can be written back, and the human reason when it cannot.
CellSpec = namedtuple("CellSpec", "colname value text editable reason")


def _clip(text: str) -> str:
    """One-line, width-bounded cell text: newlines/tabs flattened, long values
    elided so a single wide field can't blow out a column."""
    text = text.replace("\n", " ").replace("\t", " ")
    return text if len(text) <= _CELL_MAX else text[: _CELL_MAX - 1] + "…"


def _coerce_cell(original, text: str):
    """Coerce edited cell text back to the original value's Python type so an
    int column stays an int (SQLite is loose, but Postgres/MySQL reject a string
    for a numeric column). Falls back to the raw string for text/JSON columns;
    a bad number raises ValueError, surfaced by the dialog as a save error."""
    if isinstance(original, bool):  # bool is an int subclass — test it first
        return text.strip().lower() in ("true", "1", "yes", "on")
    if original is None:
        return None if text == "" else text
    if isinstance(original, int):
        return int(text)
    if isinstance(original, float):
        return float(text)
    return text


class SqlHistoryDialog(Container, WindowContent):
    """A modal picker of past queries for one connection (newest first).

    Callback-driven (not message-based): every consumer is the owning console,
    so wiring stays in db_console instead of leaking into app.py. ``on_pick`` gets
    the selected entry's *full* SQL; ``on_delete(index)`` may return the refreshed
    history list to repopulate in place; ``on_clear`` wipes the connection's
    history. Enter / clicking the preview picks; the ✗ column or Delete removes a
    row; Esc closes (handled by the enclosing ModalWindow); the picked/cleared
    paths dismiss the modal themselves.
    """

    DEFAULT_CSS = """
    SqlHistoryDialog { layout: vertical; width: 80; height: auto; max-height: 24; padding: 1 1; }
    SqlHistoryDialog DataTable { height: auto; max-height: 18; }
    SqlHistoryDialog #sh-empty { margin: 1; color: $text-muted; }
    SqlHistoryDialog #sh-buttons { height: 1; align: center middle; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("delete", "remove", show=False),
    ]

    _DEL_COL = 0  # the ✗ (delete) column index

    def __init__(self, history: list[dict], *, on_pick, on_delete, on_clear) -> None:
        super().__init__()
        self.window_title = "SQL history"
        self._history = history
        self._on_pick = on_pick
        self._on_delete = on_delete
        self._on_clear = on_clear
        self._table = _BookmarkTable(click_cb=self._on_cell_click, id="sh-table")

    @staticmethod
    def _preview(entry: dict) -> tuple[str, str]:
        """(status marker, one-line clipped SQL) for a history row."""
        marker = "✓" if entry.get("ok") else "✗"
        return marker, _clip(str(entry.get("sql", "")))

    def compose(self):
        yield self._table
        yield Static("No history yet — run a query.", id="sh-empty")
        with Horizontal(id="sh-buttons"):
            yield ShadowButton("Clear all", id="sh-clear", face_bg="rgb(160,40,40)", hotkey="a")
            yield ShadowButton("Close", id="sh-close", face_bg="rgb(80,80,90)", hotkey="c")

    def on_mount(self) -> None:
        self._table.add_column("", width=3)   # ✗ delete
        self._table.add_column("", width=2)   # ✓/✗ status
        self._table.add_column("SQL")
        self.refresh_rows(self._history)
        self._table.focus()

    def refresh_rows(self, history: list[dict]) -> None:
        self._history = history
        self._table.clear()
        for e in history:
            marker, text = self._preview(e)
            self._table.add_row("✗", marker, text)
        try:
            self.query_one("#sh-empty", Static).display = not history
            self._table.display = bool(history)
        except Exception:
            pass

    def _on_cell_click(self, row: int, column: int) -> None:
        if not 0 <= row < len(self._history):
            return
        if column == self._DEL_COL:
            self._delete_index(row)
        else:
            self._pick_index(row)

    def on_data_table_row_selected(self, event: "DataTable.RowSelected") -> None:
        row = event.cursor_row
        if 0 <= row < len(self._history):
            self._pick_index(row)

    def action_remove(self) -> None:
        coord = self._table.cursor_coordinate
        if coord is not None and 0 <= coord.row < len(self._history):
            self._delete_index(coord.row)

    def action_close(self) -> None:
        self._dismiss_modal()

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        if event.button.id == "sh-clear":
            self._on_clear()
            self._dismiss_modal()
        elif event.button.id == "sh-close":
            self._dismiss_modal()

    def _pick_index(self, index: int) -> None:
        self._on_pick(self._history[index]["sql"])
        self._dismiss_modal()

    def _delete_index(self, index: int) -> None:
        refreshed = self._on_delete(index)
        if refreshed is not None:
            self.refresh_rows(refreshed)

    def _dismiss_modal(self) -> None:
        # Post Window.Closed (handled by Desktop.on_window_closed → remove_window)
        # rather than ModalWindow.action_dismiss, whose Dismissed message has no
        # handler. Safe when unmounted (no ModalWindow ancestor → no-op).
        node = self
        while node is not None:
            if isinstance(node, ModalWindow):
                node.post_message(Window.Closed(node))
                return
            node = getattr(node, "parent", None)


class CellEditDialog(Container, WindowContent):
    """A modal viewer/editor for one result-grid cell.

    Shows the cell's *full* (untruncated) value in the app-native editor; the
    ``Markdown ⇄ Text`` button swaps the editor for a read-only rendered-markdown
    preview of the current text. ``Save`` (offered only when the cell is
    editable) calls ``on_save(text)`` — the owning console writes it back with an
    ``UPDATE`` — and the returned message is shown in the status line. When the
    cell is not editable the reason is shown and no ``Save`` button appears. Esc
    or ``Close`` dismiss; like ``SqlHistoryDialog`` the dialog posts
    ``Window.Closed`` itself rather than relying on a message handler."""

    DEFAULT_CSS = """
    CellEditDialog { layout: vertical; width: 92; height: 1fr; padding: 1 1; }
    CellEditDialog #ce-toolbar { height: 1; }
    CellEditDialog #ce-status { width: 1fr; height: 1; color: $text-muted; }
    CellEditDialog #ce-edit { height: 1fr; }
    CellEditDialog #ce-preview { height: 1fr; border: round $primary; padding: 0 1; }
    CellEditDialog #ce-buttons { height: 1; align: center middle; margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "close", show=False)]

    def __init__(self, *, title: str, text: str, editable: bool,
                 reason: str, on_save, on_close=None) -> None:
        super().__init__()
        self.window_title = title
        self._text = text
        self._editable = editable
        self._reason = reason
        self._on_save = on_save
        self._on_close = on_close
        self._rendered = False
        self._last_status = reason  # last status text (mirrors the Static; testable)
        self._editor = EditorWidget(
            buffer=TextBuffer.from_string(text),
            show_line_numbers=False, id="ce-edit",
        )
        self._preview = VerticalScroll(Static("", id="ce-pv-body"), id="ce-preview")
        self._status = Static(reason, id="ce-status")

    def compose(self):
        with Horizontal(id="ce-toolbar"):
            yield self._status
        yield self._editor
        yield self._preview
        with Horizontal(id="ce-buttons"):
            if self._editable:
                yield ShadowButton("Save", id="ce-save",
                                   face_bg="rgb(40,110,60)", hotkey="s")
            yield ShadowButton("Markdown ⇄ Text", id="ce-render",
                               face_bg="rgb(60,90,140)", hotkey="r")
            yield ShadowButton("Format JSON", id="ce-json",
                               face_bg="rgb(60,90,140)", hotkey="j")
            yield ShadowButton("Close", id="ce-close",
                               face_bg="rgb(80,80,90)", hotkey="c")

    def on_mount(self) -> None:
        self._preview.display = False           # start on the editor
        self.call_after_refresh(self._editor.focus)

    def _editor_text(self) -> str:
        return "\n".join(self._editor.buffer.lines)

    def _set_status(self, text: str) -> None:
        self._last_status = text
        self._status.update(text)

    def _toggle_render(self) -> None:
        """Swap between the editor and a rendered-markdown preview of its text."""
        self._rendered = not self._rendered
        if self._rendered:
            try:
                self.query_one("#ce-pv-body", Static).update(
                    _RichMarkdown(self._editor_text()))
            except Exception:
                pass
        self._editor.display = not self._rendered
        self._preview.display = self._rendered

    def _format_json(self) -> None:
        """Pretty-print the editor text as indented JSON for easier reading.

        Parses the current text and rewrites it with 2-space indentation
        (non-ASCII kept verbatim). On invalid JSON the buffer is left untouched
        and the reason is shown in the status line. Switches back to the editor
        first if the markdown preview is showing.

        Common real-world cells aren't *strict* JSON — dbset/SQLite often hands
        back a dict column as its Python ``repr`` (``{'role': 'admin'}`` with
        single quotes, ``True``/``False``/``None``, tuples). When strict parsing
        fails, fall back to a safe literal parse (``ast.literal_eval`` — values
        only, never executes code) and normalise that into real JSON."""
        raw = self._editor_text()
        note = ""
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as json_exc:
            try:
                obj = ast.literal_eval(raw)
                json.dumps(obj)  # reject literals JSON can't represent (e.g. sets)
                note = " (normalised from a Python literal)"
            except Exception:  # noqa: BLE001 — report the clearer JSON error
                self._set_status(f"Not valid JSON: {json_exc}")
                return
        if self._rendered:
            self._toggle_render()  # back to the editable view
        self._editor.buffer = TextBuffer.from_string(
            json.dumps(obj, indent=2, ensure_ascii=False))
        # Assigning .buffer doesn't repaint on its own — the editor only
        # rebuilds its rendered lines on a buffer-update (or when it next gains
        # focus). Trigger that explicitly so the formatted text shows at once,
        # even though focus is still on the button.
        self._editor._post_buffer_update()
        self.call_after_refresh(self._editor.focus)
        self._set_status("Formatted as JSON." + note)

    def _do_save(self) -> None:
        if self._on_save is None:
            return
        try:
            msg = self._on_save(self._editor_text())
        except Exception as exc:  # noqa: BLE001 — surface save errors in-dialog
            self._set_status(f"Save failed: {exc}")
            return
        self._set_status(msg)

    def on_shadow_button_pressed(self, event: "ShadowButton.Pressed") -> None:
        event.stop()
        bid = event.button.id
        if bid == "ce-save":
            self._do_save()
        elif bid == "ce-render":
            self._toggle_render()
        elif bid == "ce-json":
            self._format_json()
        elif bid == "ce-close":
            self._dismiss_modal()

    def action_close(self) -> None:
        self._dismiss_modal()

    def _dismiss_modal(self) -> None:
        # Mirror SqlHistoryDialog: post Window.Closed up to the enclosing
        # ModalWindow (its Dismissed message has no handler). No-op if unmounted.
        node = self
        while node is not None:
            if isinstance(node, ModalWindow):
                node.post_message(Window.Closed(node))
                break
            node = getattr(node, "parent", None)
        # Hand focus back to the owner (the result grid, on the clicked cell).
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception:  # noqa: BLE001 — never let focus restore raise
                pass
            self._on_close = None  # one-shot: Esc + Close can both call here


class DbConsoleContent(WindowContent):
    # Esc closes this window (app.action_close_editor checks this marker rather
    # than importing DbConsoleContent, which would pull dbset at app startup).
    closes_on_escape = True

    DEFAULT_CSS = """
    DbConsoleContent .db-toolbar {
        height: 1;
        width: 1fr;
    }
    DbConsoleContent #db-status {
        width: 1fr;
        height: 1;
    }
    DbConsoleContent #db-sql {
        height: 5;
    }
    DbConsoleContent #db-grid {
        height: 1fr;
        border: round $primary;
    }
    """

    def __init__(self, conn: da.DbConn, *, title_db: str, initial_sql: str = "") -> None:
        super().__init__()
        self._conn = conn
        self._title_db = title_db
        # Expand tabs to spaces: SQLAlchemy's CreateTable indents the DDL with
        # raw "\t", and the editor emits the tab byte verbatim. A real terminal
        # then advances it to the next 8-col tab stop, shifting the line and the
        # right-hand padding so the window's right border lands in the wrong
        # column (looks like a broken border). Spaces render at a fixed width.
        self._initial_sql = (initial_sql or "").expandtabs(4)
        self.last_columns: list[str] = []
        self.last_rows: list[dict] = []
        self.last_status: str = ""
        # The single table the last result can be written back to (and its PK
        # column), or None when the result isn't an updatable single-table
        # SELECT. Recomputed on every run_sql; drives result-cell editability.
        self._edit_table: str | None = None
        self._edit_pk: str | None = None
        # Pagination state for a row-returning result. ``_page_sql`` is the base
        # SELECT (None when the last run wasn't pageable); ``_page`` is the
        # 0-based page index; ``_page_has_next`` is whether a further page
        # exists (learned from fetching one row past the page).
        self._page_sql: str | None = None
        self._page = 0
        self._page_has_next = False
        self._editor: EditorWidget | None = None
        self._table: DataTable | None = None
        self._status: Static | None = None
        self._prev_btn: _ToolbarButton | None = None
        self._next_btn: _ToolbarButton | None = None

    def compose(self):
        # The app's own editor widget (palette-driven, SQL-highlighted) instead
        # of Textual's stock TextArea, so the console matches the rest of the UI.
        # initial_sql prefills the editor (e.g. F3 -> SELECT *, F4 -> CREATE TABLE).
        self._editor = EditorWidget(
            buffer=TextBuffer.from_string(self._initial_sql),
            show_line_numbers=False, id="db-sql",
        )
        self._table = DataTable(id="db-grid", zebra_stripes=True)
        self._status = Static("Ctrl+R or [ Run ] to execute · Esc to close",
                              id="db-status")
        run_btn = _ToolbarButton("[ Run (Ctrl+R) ]", on_press=self._run_current)
        hist_btn = _ToolbarButton("[ History (Alt+H) ]", on_press=self._open_history)
        self._prev_btn = _ToolbarButton("[ ◀ Prev ]", on_press=self._prev_page)
        self._next_btn = _ToolbarButton("[ Next ▶ ]", on_press=self._next_page)
        yield Vertical(
            Horizontal(run_btn, hist_btn, self._prev_btn, self._next_btn,
                       self._status, classes="db-toolbar"),
            self._editor,
            Splitter("h-divider"),
            self._table,
        )

    def on_mount(self) -> None:
        # Bottom-border hint: Tab toggles focus between the SQL editor and the
        # result grid (see focus_other_pane). Rendered on the frame's bottom row.
        self.window_subtitle = "Tab: switch focus  SQL ⇄ results"
        self._update_page_buttons()  # hidden until a paginated result exists
        # SQL syntax highlighting through the app's own highlighter (the widget
        # only auto-detects from a file path, which the console has no use for).
        if self._editor is not None:
            self._editor.set_language("sql")
            # The SQL pane stays a fixed height (CSS: 5 rows): a long prefill
            # (a big CREATE TABLE) scrolls WITHIN the editor rather than growing
            # the pane — growing it would push the splitter and result grid off
            # the bottom (unrecoverable, since the splitter is then gone) and
            # could carry the cursor below the screen. Drag the splitter to
            # enlarge the pane when reading a long DDL.
            # Land focus in the SQL editor so the user can type immediately
            # (deferred so it wins over any post-mount focus reset).
            self.call_after_refresh(self._editor.focus)

    def _editor_text(self) -> str:
        if self._editor is None:
            return ""
        return "\n".join(self._editor.buffer.lines)

    def focus_other_pane(self) -> bool:
        """Tab toggles focus between the SQL editor and the result grid.

        Called by ``app.action_focus_other_panel`` (the app-level priority Tab
        binding) when focus is inside this console window. Returns True when it
        handled the toggle so the app doesn't fall back to tab-insertion."""
        if self._editor is None or self._table is None:
            return False
        if self._editor.has_focus:
            self._table.focus()
        else:
            self._editor.focus()
        return True

    def on_splitter_dragged(self, event: Splitter.Dragged) -> None:
        """Drag the divider to resize the SQL pane (the grid takes the rest).

        Mirrors EditorContent.on_splitter_dragged: grow/shrink the top pane by
        the vertical drag delta, clamped to at least one line so a single-line
        query can collapse the editor right down."""
        if self._editor is None:
            return
        current = self._editor.outer_size.height or self._editor.size.height
        self._editor.styles.height = max(1, current + event.dy)
        event.stop()

    def run_sql(self, sql: str) -> None:
        """Execute a fresh query. A row-returning statement is paginated (page 0
        shown, Prev/Next navigate); everything else runs un-paged."""
        if _is_pageable(sql):
            self._page_sql = sql
            self._page = 0
            self._load_page(initial=True)
        else:
            self._page_sql = None
            self._run_unpaged(sql)
        self._update_page_buttons()

    def _run_unpaged(self, sql: str) -> None:
        """Run ``sql`` without paging: cap-limited fetch for a SELECT, row count
        for a write/DDL. Records history. The fallback for un-wrappable SELECTs."""
        try:
            # limit=_RESULT_CAP streams and fetches at most CAP+1 rows, so a
            # SELECT * over a huge table can't load everything and hang the app.
            cols, rows, rowcount, truncated = self._conn.query(sql, limit=_RESULT_CAP)
        except Exception as exc:  # noqa: BLE001 — surface DB errors in the status line
            self._set_status(f"Error: {exc}")
            self.last_columns, self.last_rows = [], []
            self._edit_table = self._edit_pk = None
            self._render_grid([], [])
            self._record_history(sql, ok=False)
            return
        if cols:
            self.last_columns, self.last_rows = cols, rows
            self._compute_edit_target(sql, cols)
            extra = (f" (showing first {_RESULT_CAP} — add LIMIT to narrow)"
                     if truncated else "")
            self._set_status(f"{len(rows)} row(s){extra}")
            self._render_grid(cols, rows)
        else:
            self.last_columns, self.last_rows = [], []
            self._edit_table = self._edit_pk = None
            self._set_status(f"{rowcount} row(s) affected")
            self._render_grid([], [])
        self._record_history(sql, ok=True)

    def _load_page(self, *, initial: bool = False) -> None:
        """Fetch and show the current page of ``_page_sql``.

        On the first page, a wrapping failure (e.g. duplicate output column
        names) falls back to an un-paged run so a valid-but-un-wrappable SELECT
        still shows. History is recorded once, on the initial run, not on every
        page flip."""
        sql = self._page_sql
        if sql is None:
            return
        try:
            cols, rows, has_next = self._conn.query_page(
                sql, limit=_PAGE, offset=self._page * _PAGE)
        except Exception as exc:  # noqa: BLE001
            if initial:
                self._page_sql = None
                self._run_unpaged(sql)   # un-wrappable but maybe valid; or report error
                self._update_page_buttons()
            else:
                self._set_status(f"Error: {exc}")
            return
        self._page_has_next = has_next
        self.last_columns, self.last_rows = cols, rows
        self._compute_edit_target(sql, cols)
        self._set_status(self._page_status(len(rows), has_next))
        self._render_grid(cols, rows)
        if initial:
            self._record_history(sql, ok=True)
        self._update_page_buttons()

    def _page_status(self, n: int, has_next: bool) -> str:
        # Just the page/row counts — navigation is the Prev/Next buttons, so no
        # duplicate ◀/▶ text hints (which looked clickable but weren't).
        start = self._page * _PAGE
        if n == 0:
            return f"Page {self._page + 1} · no rows"
        return f"Page {self._page + 1} · rows {start + 1}–{start + n}"

    def _next_page(self) -> None:
        if self._page_sql is not None and self._page_has_next:
            self._page += 1
            self._load_page()

    def _prev_page(self) -> None:
        if self._page_sql is not None and self._page > 0:
            self._page -= 1
            self._load_page()

    def _update_page_buttons(self) -> None:
        """Show the Prev/Next buttons only for a paginated result, and only the
        directions that lead somewhere."""
        if self._prev_btn is not None:
            self._prev_btn.display = self._page_sql is not None and self._page > 0
        if self._next_btn is not None:
            self._next_btn.display = self._page_sql is not None and self._page_has_next

    def _compute_edit_target(self, sql: str, cols: list[str]) -> None:
        """Resolve whether this result's rows map to one updatable table.

        Sets ``_edit_table``/``_edit_pk`` when ``sql`` is a single-table SELECT
        whose target table exists and whose primary key is among the result
        columns (so a row can be located for UPDATE); clears them otherwise.
        Best-effort: any metadata error leaves the result view-only."""
        self._edit_table = self._edit_pk = None
        try:
            tgt = da.single_table_target(sql)
            if not tgt:
                return
            actual = next(
                (t for t in self._conn.tables() if t.lower() == tgt.lower()), None)
            if actual is None:
                return
            pk = self._conn.primary_key(actual)
            if pk and pk in cols:
                self._edit_table, self._edit_pk = actual, pk
        except Exception:  # noqa: BLE001 — never let metadata I/O break a query
            self._edit_table = self._edit_pk = None

    def _record_history(self, sql: str, *, ok: bool) -> None:
        # Best-effort: a failed write just means this query isn't remembered, so
        # never let history I/O disturb the console.
        sql_history.record(self._title_db, sql, ok=ok, info=self.last_status)

    def _set_status(self, text: str) -> None:
        self.last_status = text
        if self._status is not None:  # headless (tests): status widget not mounted
            self._status.update(text)

    def _render_grid(self, cols: list[str], rows: list[dict]) -> None:
        if self._table is None:  # headless (tests): grid not mounted
            return
        self._table.clear(columns=True)
        if cols:
            self._table.add_columns(*cols)
            for r in rows:
                self._table.add_row(*[_clip(str(r.get(c, ""))) for c in cols])

    # --- cell view/edit ---------------------------------------------------

    def on_data_table_cell_selected(self, event: "DataTable.CellSelected") -> None:
        """Enter/click on a result cell opens the cell view/edit dialog."""
        coord = getattr(event, "coordinate", None)
        if coord is None:
            return
        event.stop()
        self._open_cell_dialog(coord.row, coord.column)

    def _cell_editability(self, colname: str) -> tuple[bool, str]:
        """(editable, reason) for a column of the current result."""
        if self._conn.read_only:
            return False, "Connection is read-only — view only."
        if self._edit_table is None:
            return False, "Result is not a single-table SELECT — view only."
        try:
            real = self._conn.columns(self._edit_table)
        except Exception:  # noqa: BLE001
            return False, "Cannot read table columns — view only."
        if colname not in real:
            return False, f"{colname!r} is a computed/aliased column — view only."
        return True, f"Editable — updates {self._edit_table}.{colname} by {self._edit_pk}."

    def _resolve_cell(self, row: int, col: int) -> CellSpec:
        colname = self.last_columns[col]
        value = self.last_rows[row].get(colname)
        text = "" if value is None else str(value)
        editable, reason = self._cell_editability(colname)
        return CellSpec(colname, value, text, editable, reason)

    def _save_cell(self, row: int, col: int, new_text: str) -> str:
        """Write an edited cell back with an UPDATE; refresh the in-memory grid.

        Coerces the text to the original value's type, updates the row by PK,
        mirrors the change into ``last_rows`` and the visible grid, and returns a
        status message. Raises on coercion/DB errors (caught by the dialog)."""
        colname = self.last_columns[col]
        original = self.last_rows[row].get(colname)
        new_val = _coerce_cell(original, new_text)
        pk_value = self.last_rows[row].get(self._edit_pk)
        n = self._conn.update(self._edit_table, pk_value, {colname: new_val})
        self.last_rows[row][colname] = new_val
        if self._table is not None:  # headless (tests): grid not mounted
            disp = "" if new_val is None else str(new_val)
            self._table.update_cell_at(Coordinate(row, col), _clip(disp))
        return f"Saved — {n} row(s) updated."

    def _open_cell_dialog(self, row: int, col: int) -> None:
        if not (0 <= row < len(self.last_rows)) or not (0 <= col < len(self.last_columns)):
            return
        desktop = self._desktop()
        if desktop is None:
            return
        spec = self._resolve_cell(row, col)
        title = spec.colname + (f" · {self._edit_table}" if self._edit_table else "")
        on_save = (lambda t, r=row, c=col: self._save_cell(r, c, t)) if spec.editable else None
        dialog = CellEditDialog(
            title=title, text=spec.text, editable=spec.editable,
            reason=spec.reason, on_save=on_save,
            on_close=lambda r=row, c=col: self._refocus_grid(r, c),
        )
        show_modal(desktop, dialog, title="Edit cell", size=(94, 30))

    def _refocus_grid(self, row: int, col: int) -> None:
        """Return focus to the result grid on the cell the dialog was opened on.

        The modal strips ``can_focus`` from every widget outside it (so keys
        can't drift underneath) and only restores it when the modal *unmounts*.
        A single deferral can fire before that thaw — then ``table.focus()`` is a
        silent no-op (this is why Esc differed from the button path). So retry
        across refreshes until the grid is focusable again, then focus it and
        restore the clicked cell. Bounded so a stuck thaw can't loop forever."""
        table = self._table
        app = getattr(self, "app", None)
        if table is None or app is None:
            return
        def _restore(attempt: int = 0) -> None:
            try:
                if not table.can_focus:          # modal not thawed yet
                    if attempt < 8:
                        app.call_after_refresh(lambda: _restore(attempt + 1))
                    return
                table.focus()
                table.cursor_coordinate = Coordinate(row, col)
            except Exception:  # noqa: BLE001 — never let focus restore raise
                pass
        app.call_after_refresh(_restore)

    def get_commands(self) -> list[WindowCommand]:
        # Ctrl+R (not Ctrl+Enter): terminals reliably emit it and the editor
        # doesn't consume it, so it bubbles to the command router. Alt+H opens
        # the query history — the console is its own window scope, so it doesn't
        # collide with the panel-level Alt+H (show hidden).
        return [
            WindowCommand(id="db.console.run", label="Run SQL",
                          handler=self._run_current, hotkey="ctrl+r"),
            WindowCommand(id="db.console.history", label="SQL history",
                          handler=self._open_history, hotkey="alt+h"),
        ]

    def _run_current(self) -> None:
        self.run_sql(self._editor_text())

    # --- history picker ---------------------------------------------------

    def _desktop(self):
        """The host Desktop (via the app shortcut, else a query), or None."""
        desktop = getattr(self.app, "desktop", None)
        if desktop is None:
            from dunders.windowing import Desktop
            try:
                desktop = self.app.query_one(Desktop)
            except Exception:
                return None
        return desktop

    def _open_history(self) -> None:
        desktop = self._desktop()
        if desktop is None:
            return
        dialog = SqlHistoryDialog(
            sql_history.load_history(self._title_db),
            on_pick=self._apply_history_pick,
            on_delete=self._delete_history,
            on_clear=self._clear_history,
        )
        show_modal(desktop, dialog, title="SQL history", size=(82, 22))

    def _apply_history_pick(self, sql: str) -> None:
        """Recall a past query: replace the editor buffer and re-focus it."""
        if self._editor is None:
            return
        self._editor.buffer = TextBuffer.from_string(sql)
        self.call_after_refresh(self._editor.focus)

    def _delete_history(self, index: int) -> list[dict]:
        """Drop one entry, returning the refreshed list so the dialog repaints."""
        sql_history.delete(self._title_db, index)
        return sql_history.load_history(self._title_db)

    def _clear_history(self) -> None:
        sql_history.clear(self._title_db)
