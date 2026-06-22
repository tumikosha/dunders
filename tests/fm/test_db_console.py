import pytest

from dunders.fm.providers import db_access as da
from dunders.fm.db_console import DbConsoleContent


def test_run_select_fills_grid(tmp_path):
    url = f"sqlite:///{tmp_path/'t.db'}"
    conn = da.DbConn.open(url)
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="t.db")
    content.run_sql("SELECT name FROM users")
    assert content.last_columns == ["name"]
    assert content.last_rows[0]["name"] == "Ann"


def test_run_non_select_reports_rowcount(tmp_path):
    url = f"sqlite:///{tmp_path/'t.db'}"
    conn = da.DbConn.open(url)
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="t.db")
    content.run_sql("UPDATE users SET name='Bea' WHERE name='Ann'")
    assert "1" in content.last_status


def test_select_caps_rows_at_1000(tmp_path):
    url = f"sqlite:///{tmp_path/'t.db'}"
    conn = da.DbConn.open(url)
    for i in range(1001):
        conn.insert("users", {"name": f"u{i}"})
    content = DbConsoleContent(conn, title_db="t.db")
    content.run_sql("SELECT name FROM users")
    assert len(content.last_rows) == 1000
    assert "showing first 1000" in content.last_status


def test_clip_elides_wide_and_multiline_values():
    from dunders.fm.db_console import _clip, _CELL_MAX
    assert _clip("short") == "short"
    assert _clip("a\nb\tc") == "a b c"  # newlines/tabs flattened to spaces
    long = "x" * (_CELL_MAX + 50)
    clipped = _clip(long)
    assert len(clipped) == _CELL_MAX and clipped.endswith("…")


def test_run_hotkey_is_ctrl_r(tmp_path):
    # Ctrl+Enter is indistinguishable from Enter in most terminals; the run
    # shortcut must be Ctrl+R so it actually fires.
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="t.db")
    run = next(c for c in content.get_commands() if c.id == "db.console.run")
    assert run.hotkey == "ctrl+r"


def test_console_closes_on_escape_marker():
    # app.action_close_editor closes the console via this marker (it cannot
    # import DbConsoleContent without pulling dbset at startup).
    assert DbConsoleContent.closes_on_escape is True


def test_run_error_surfaces_in_status(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="t.db")
    content.run_sql("SELECT * FROM no_such_table")
    assert content.last_status.startswith("Error:")
    assert content.last_rows == []


def test_run_sql_records_into_history(tmp_path):
    from dunders.config import sql_history
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="conn-key")
    content.run_sql("SELECT name FROM users")
    hist = sql_history.load_history("conn-key")
    assert hist[0]["sql"] == "SELECT name FROM users"
    assert hist[0]["ok"] is True
    assert hist[0]["info"] == content.last_status


def test_run_sql_records_failed_query(tmp_path):
    from dunders.config import sql_history
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="conn-key")
    content.run_sql("SELECT * FROM no_such_table")
    hist = sql_history.load_history("conn-key")
    assert hist[0]["sql"] == "SELECT * FROM no_such_table"
    assert hist[0]["ok"] is False


def test_run_sql_blank_not_recorded(tmp_path):
    from dunders.config import sql_history
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="conn-key")
    content.run_sql("   ")
    assert sql_history.load_history("conn-key") == []


def test_history_dialog_preview_marks_status_and_flattens():
    from dunders.fm.db_console import SqlHistoryDialog, _CELL_MAX
    ok_marker, text = SqlHistoryDialog._preview(
        {"sql": "select\n  1", "ok": True, "info": "1 row(s)"})
    assert ok_marker == "✓"
    assert text == "select   1"  # newline flattened to a space
    bad_marker, _ = SqlHistoryDialog._preview(
        {"sql": "x" * (_CELL_MAX + 20), "ok": False, "info": "Error"})
    assert bad_marker == "✗"


def test_history_dialog_enter_returns_full_sql():
    from dunders.fm.db_console import SqlHistoryDialog
    picked = []
    hist = [{"sql": "SELECT 2", "ok": True, "info": ""},
            {"sql": "SELECT 1", "ok": True, "info": ""}]
    dlg = SqlHistoryDialog(hist, on_pick=picked.append,
                           on_delete=lambda i: None, on_clear=lambda: None)
    dlg._pick_index(1)  # newest-first; row 1 is "SELECT 1"
    assert picked == ["SELECT 1"]


def test_history_dialog_delete_reports_index():
    from dunders.fm.db_console import SqlHistoryDialog
    deleted = []
    hist = [{"sql": "SELECT 1", "ok": True, "info": ""}]
    dlg = SqlHistoryDialog(hist, on_pick=lambda s: None,
                           on_delete=deleted.append, on_clear=lambda: None)
    dlg._delete_index(0)
    assert deleted == [0]


def test_history_hotkey_is_alt_h(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="t.db")
    hist = next(c for c in content.get_commands() if c.id == "db.console.history")
    assert hist.hotkey == "alt+h"


def test_picking_history_replaces_editor_buffer(tmp_path):
    from dunders.windowing.core.buffer import TextBuffer
    from dunders.windowing.editor.widget import EditorWidget
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="t.db")
    content._editor = EditorWidget(
        buffer=TextBuffer.from_string("old query"), show_line_numbers=False)
    content._apply_history_pick("SELECT recalled")
    assert content._editor_text() == "SELECT recalled"


def test_sql_editor_defaults_to_five_rows():
    assert "#db-sql {" in DbConsoleContent.DEFAULT_CSS
    # The SQL pane opens at 5 rows so a single-line query doesn't dominate.
    sql_block = DbConsoleContent.DEFAULT_CSS.split("#db-sql {", 1)[1].split("}", 1)[0]
    assert "height: 5" in sql_block


def test_splitter_drag_resizes_sql_pane_clamped(tmp_path):
    import types
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="t.db")

    def fake_editor(h):
        return types.SimpleNamespace(
            outer_size=types.SimpleNamespace(height=h),
            size=types.SimpleNamespace(height=h),
            styles=types.SimpleNamespace(height=None),
        )

    # Drag down by 3 → pane grows 5 → 8.
    content._editor = fake_editor(5)
    content.on_splitter_dragged(types.SimpleNamespace(dy=3, stop=lambda: None))
    assert content._editor.styles.height == 8

    # Drag far up → clamped to a single line (collapse for one-liners).
    content._editor = fake_editor(2)
    content.on_splitter_dragged(types.SimpleNamespace(dy=-50, stop=lambda: None))
    assert content._editor.styles.height == 1


@pytest.mark.asyncio
async def test_initial_sql_prefills_editor(tmp_path):
    """A console opened with initial_sql (F3 -> SELECT, F4 -> CREATE TABLE)
    lands that text in the editor on mount, ready to run/edit."""
    from textual.app import App
    from dunders.windowing import Desktop, make_window

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k", initial_sql="SELECT * FROM users")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(Desktop).add_window(make_window(content, title="SQL", size=(80, 20)))
        await pilot.pause()
        assert content._editor_text() == "SELECT * FROM users"


def test_initial_sql_expands_tabs(tmp_path):
    # SQLAlchemy's CreateTable indents the DDL with raw "\t"; the editor emits
    # the tab byte verbatim and a real terminal advances it to the next tab
    # stop, shifting the line so the window's right border lands in the wrong
    # column. The prefill must arrive tab-free (spaces have a fixed width).
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="k",
                               initial_sql="CREATE TABLE t (\n\tid INTEGER\n)")
    assert "\t" not in content._initial_sql
    assert "    id INTEGER" in content._initial_sql


@pytest.mark.asyncio
async def test_prefill_keeps_fixed_pane_and_grid(tmp_path):
    # A long prefill (F4 CREATE TABLE) must NOT grow the SQL pane: it stays a
    # fixed 5 rows (scrolling within) so the splitter and result grid remain on
    # screen. Growing the pane would push them off the bottom unrecoverably.
    from textual.app import App
    from dunders.windowing import Desktop, make_window

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    big_ddl = "CREATE TABLE t (\n" + "\n".join(f"  c{i} TEXT," for i in range(40)) + "\n)"
    content = DbConsoleContent(conn, title_db="k", initial_sql=big_ddl)

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        app.query_one(Desktop).add_window(make_window(content, title="SQL", size=(80, 28)))
        await pilot.pause()
        await pilot.pause()
        assert content._editor.region.height == 5      # fixed pane, not grown
        assert content._table.region.height > 0        # result grid still visible


@pytest.mark.asyncio
async def test_history_picker_end_to_end(tmp_path):
    # Mounted path: run two queries, open the history modal, pick the older one,
    # and confirm its full SQL lands back in the editor and the modal closes.
    from textual.app import App
    from dunders.windowing import Desktop, make_window
    from dunders.fm.db_console import SqlHistoryDialog

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="conn-key")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        desktop.add_window(make_window(content, title="SQL", size=(80, 20)))
        await pilot.pause()
        content.run_sql("SELECT name FROM users")
        content.run_sql("SELECT 1")
        # Newest-first: index 1 is the older "SELECT name FROM users".
        content._open_history()
        await pilot.pause()
        dialog = app.query_one(SqlHistoryDialog)
        dialog._pick_index(1)
        await pilot.pause()
        assert content._editor_text() == "SELECT name FROM users"
        # Modal dismissed: no SqlHistoryDialog left mounted.
        assert not app.query(SqlHistoryDialog)


@pytest.mark.asyncio
async def test_database_menu_history_opens_console_and_picker(tmp_path):
    # The Database menu's "SQL history" action (db.history) opens a SQL console
    # and immediately pops its history picker, so history is reachable from the
    # menu without first opening a console.
    from dunders.app import DundersApp
    from dunders.fm.db_console import DbConsoleContent, SqlHistoryDialog

    dbfile = tmp_path / "t.db"
    seed = da.DbConn.open(f"sqlite:///{dbfile}")
    seed.insert("users", {"name": "Ann"})
    seed.close()

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        provider = app._vfs_registry.for_scheme("db")
        target = provider.resolve_target(f"sqlite:///{dbfile}", base=panel.cwd_loc)
        app._apply_open_result(panel, "spec", (target, None))
        await pilot.pause()
        assert panel.cwd_loc.scheme == "db"

        hist_action = next(a for a in provider.actions() if a.id == "db.history")
        app._run_provider_action(hist_action)
        await pilot.pause()
        await pilot.pause()
        consoles = [w for w in app.desktop.windows
                    if isinstance(w.content, DbConsoleContent)]
        assert len(consoles) == 1
        assert app.query(SqlHistoryDialog)


@pytest.mark.asyncio
async def test_f3_f4_on_table_open_console_with_query(tmp_path):
    # F3/View on a table name opens a SQL console prefilled with SELECT *; F4/Edit
    # opens one prefilled with the table's CREATE TABLE DDL.
    from dunders.app import DundersApp

    dbfile = tmp_path / "t.db"
    seed = da.DbConn.open(f"sqlite:///{dbfile}")
    seed.insert("users", {"name": "Ann"})
    seed.close()

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        provider = app._vfs_registry.for_scheme("db")
        target = provider.resolve_target(f"sqlite:///{dbfile}", base=panel.cwd_loc)
        app._apply_open_result(panel, "spec", (target, None))
        await pilot.pause()
        panel.cursor = next(i for i, e in enumerate(panel.entries)
                            if e.extra.get("db.kind") == "table")

        app.action_view()                       # F3 -> SELECT *
        await pilot.pause()
        await pilot.pause()
        consoles = [w for w in app.desktop.windows
                    if isinstance(w.content, DbConsoleContent)]
        assert len(consoles) == 1
        assert consoles[-1].content._editor_text() == "SELECT * FROM users"

        app.action_edit()                        # F4 -> CREATE TABLE
        await pilot.pause()
        await pilot.pause()
        consoles = [w for w in app.desktop.windows
                    if isinstance(w.content, DbConsoleContent)]
        assert len(consoles) == 2
        ddl = consoles[-1].content._editor_text()
        assert ddl.upper().startswith("CREATE TABLE")
        assert "users" in ddl


@pytest.mark.asyncio
async def test_f4_on_multiple_selected_tables_concatenates_ddl(tmp_path):
    # Selecting several tables and pressing F4 opens ONE console with each
    # table's CREATE TABLE concatenated (in panel order).
    from dunders.app import DundersApp

    dbfile = tmp_path / "t.db"
    seed = da.DbConn.open(f"sqlite:///{dbfile}")
    seed.insert("users", {"name": "Ann"})
    seed.insert("orders", {"total": 5})
    seed.close()

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        prov = app._vfs_registry.for_scheme("db")
        target = prov.resolve_target(f"sqlite:///{dbfile}", base=panel.cwd_loc)
        app._apply_open_result(panel, "spec", (target, None))
        await pilot.pause()
        panel.selection = {e.loc for e in panel.entries
                           if e.extra.get("db.kind") == "table"}
        assert len(panel.selection) == 2

        app.action_edit()
        await pilot.pause()
        await pilot.pause()
        consoles = [w for w in app.desktop.windows
                    if isinstance(w.content, DbConsoleContent)]
        assert len(consoles) == 1
        ddl = consoles[-1].content._editor_text()
        assert "CREATE TABLE users" in ddl
        assert "CREATE TABLE orders" in ddl


@pytest.mark.asyncio
async def test_console_uses_app_native_editor_widget(tmp_path):
    # The SQL field is the app's own EditorWidget (palette-driven, SQL-
    # highlighted), not Textual's stock TextArea — typing into it and running
    # executes the query end-to-end.
    from textual.app import App
    from dunders.windowing import Desktop, make_window
    from dunders.windowing.editor.widget import EditorWidget
    from dunders.windowing.core.buffer import TextBuffer

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="t.db")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        desktop.add_window(make_window(content, title="SQL", size=(60, 16)))
        await pilot.pause()
        assert isinstance(content._editor, EditorWidget)
        # Focus lands in the SQL editor on open so the user can type at once.
        assert content._editor.has_focus
        content._editor.buffer = TextBuffer.from_string("SELECT name FROM users")
        content._run_current()
        await pilot.pause()
        assert content.last_columns == ["name"]
        assert content.last_rows[0]["name"] == "Ann"
