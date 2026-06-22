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

from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
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

__all__ = ["DbConsoleContent", "SqlHistoryDialog"]

_RESULT_CAP = 1000
_CELL_MAX = 60  # clip a cell's display text to this many chars (… elides the rest)


def _clip(text: str) -> str:
    """One-line, width-bounded cell text: newlines/tabs flattened, long values
    elided so a single wide field can't blow out a column."""
    text = text.replace("\n", " ").replace("\t", " ")
    return text if len(text) <= _CELL_MAX else text[: _CELL_MAX - 1] + "…"


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
        self._editor: EditorWidget | None = None
        self._table: DataTable | None = None
        self._status: Static | None = None

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
        yield Vertical(
            Horizontal(run_btn, hist_btn, self._status, classes="db-toolbar"),
            self._editor,
            Splitter("h-divider"),
            self._table,
        )

    def on_mount(self) -> None:
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
        try:
            # limit=_RESULT_CAP streams and fetches at most CAP+1 rows, so a
            # SELECT * over a huge table can't load everything and hang the app.
            cols, rows, rowcount, truncated = self._conn.query(sql, limit=_RESULT_CAP)
        except Exception as exc:  # noqa: BLE001 — surface DB errors in the status line
            self._set_status(f"Error: {exc}")
            self.last_columns, self.last_rows = [], []
            self._render_grid([], [])
            self._record_history(sql, ok=False)
            return
        if cols:
            self.last_columns, self.last_rows = cols, rows
            extra = (f" (showing first {_RESULT_CAP} — add LIMIT to narrow)"
                     if truncated else "")
            self._set_status(f"{len(rows)} row(s){extra}")
            self._render_grid(cols, rows)
        else:
            self.last_columns, self.last_rows = [], []
            self._set_status(f"{rowcount} row(s) affected")
            self._render_grid([], [])
        self._record_history(sql, ok=True)

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

    def _open_history(self) -> None:
        desktop = getattr(self.app, "desktop", None)
        if desktop is None:
            from dunders.windowing import Desktop
            try:
                desktop = self.app.query_one(Desktop)
            except Exception:
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
