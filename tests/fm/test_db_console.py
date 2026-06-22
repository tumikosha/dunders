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


def test_large_select_shows_first_page(tmp_path):
    # A large SELECT no longer dumps a capped 1000 rows — it paginates, showing
    # the first page (see _PAGE) with more available.
    from dunders.fm.db_console import _PAGE
    url = f"sqlite:///{tmp_path/'t.db'}"
    conn = da.DbConn.open(url)
    for i in range(1001):
        conn.insert("users", {"name": f"u{i}"})
    content = DbConsoleContent(conn, title_db="t.db")
    content.run_sql("SELECT name FROM users")
    assert len(content.last_rows) == _PAGE
    assert content._page == 0 and content._page_has_next is True
    assert "Page 1" in content.last_status


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


# --- pagination -------------------------------------------------------------

def test_is_pageable_helper():
    from dunders.fm.db_console import _is_pageable
    assert _is_pageable("SELECT * FROM t")
    assert _is_pageable("  with x as (select 1) select * from x")
    assert _is_pageable("VALUES (1),(2)")
    assert not _is_pageable("UPDATE t SET a=1")
    assert not _is_pageable("INSERT INTO t VALUES (1)")
    assert not _is_pageable("CREATE TABLE t (id int)")


def test_select_paginates_through_pages(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    for i in range(450):
        conn.insert("users", {"name": f"u{i}"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM users")
    from dunders.fm.db_console import _PAGE
    assert _PAGE == 200
    assert len(content.last_rows) == 200 and content._page == 0
    assert content._page_has_next is True
    assert "Page 1" in content.last_status and "1–200" in content.last_status
    content._next_page()
    assert content._page == 1 and len(content.last_rows) == 200
    assert "201–400" in content.last_status and content._page_has_next is True
    content._next_page()
    assert content._page == 2 and len(content.last_rows) == 50      # 450 total
    assert content._page_has_next is False and "401–450" in content.last_status
    content._next_page()                                            # no more: no-op
    assert content._page == 2
    content._prev_page()
    assert content._page == 1 and len(content.last_rows) == 200


def test_non_select_is_not_paginated(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM users")
    assert content._page_sql is not None            # SELECT paginates
    content.run_sql("UPDATE users SET name='Bea'")
    assert content._page_sql is None                # write clears page state
    assert "affected" in content.last_status


def test_paginated_result_is_still_editable(tmp_path):
    # A paginated SELECT keeps its updatable target, so a cell on any page saves.
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    for i in range(250):
        conn.insert("users", {"name": f"u{i}"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM users")
    content._next_page()                            # page 2: rows 201–250
    assert content._edit_table == "users" and content._edit_pk == "id"
    col = content.last_columns.index("name")
    pk = content.last_rows[0]["id"]
    content._save_cell(0, col, "EDITED")
    assert conn.get("users", pk)["name"] == "EDITED"


# --- single-table detection (drives cell editability) ----------------------

def test_single_table_target_simple_selects():
    assert da.single_table_target("SELECT * FROM users") == "users"
    assert da.single_table_target("select id, name from users where id=1") == "users"
    assert da.single_table_target('SELECT * FROM "users" u ORDER BY id') == "users"
    assert da.single_table_target("SELECT * FROM users LIMIT 10") == "users"


def test_single_table_target_schema_qualified():
    assert da.single_table_target("SELECT * FROM public.users") == "users"


def test_single_table_target_rejects_non_updatable():
    assert da.single_table_target("SELECT * FROM a JOIN b ON a.id=b.id") is None
    assert da.single_table_target("SELECT count(*) FROM users GROUP BY name") is None
    assert da.single_table_target("SELECT * FROM a, b") is None
    assert da.single_table_target("SELECT * FROM x UNION SELECT * FROM y") is None
    assert da.single_table_target("SELECT * FROM (SELECT 1)") is None
    assert da.single_table_target("UPDATE users SET x=1") is None
    assert da.single_table_target("not even sql") is None


# --- cell editability + save ----------------------------------------------

def test_select_star_marks_editable_target(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM users")
    assert content._edit_table == "users"
    assert content._edit_pk == "id"            # dbset auto-PK, present in SELECT *


def test_select_without_pk_not_editable(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT name FROM users")  # id (PK) absent from the result
    assert content._edit_table is None


def test_join_result_not_editable(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    conn.insert("orders", {"total": 5})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM users JOIN orders ON 1=1")
    assert content._edit_table is None


def test_resolve_cell_returns_full_untruncated_text(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    long = "x" * 200
    conn.insert("docs", {"body": long})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM docs")
    col = content.last_columns.index("body")
    spec = content._resolve_cell(0, col)
    assert spec.text == long          # dialog shows the full value, not _clip'd
    assert spec.editable is True


def test_resolve_cell_reason_no_pk(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT name FROM users")
    spec = content._resolve_cell(0, 0)
    assert spec.editable is False
    assert spec.text == "Ann"


def test_resolve_cell_computed_column_not_editable(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT id, upper(name) AS up FROM users")
    assert content._edit_table == "users"          # id present -> table updatable
    spec = content._resolve_cell(0, content.last_columns.index("up"))
    assert spec.editable is False                  # but 'up' is computed
    spec_id = content._resolve_cell(0, content.last_columns.index("id"))
    assert spec_id.editable is True                # a real column stays editable


def test_resolve_cell_readonly_connection(tmp_path):
    url = f"sqlite:///{tmp_path/'t.db'}"
    w = da.DbConn.open(url)
    w.insert("users", {"name": "Ann"})
    w.close()
    ro = da.DbConn.open(url, read_only=True)
    content = DbConsoleContent(ro, title_db="k")
    content.run_sql("SELECT * FROM users")
    spec = content._resolve_cell(0, content.last_columns.index("name"))
    assert spec.editable is False
    assert "read-only" in spec.reason.lower()


def test_save_cell_writes_through_to_db(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM users")
    col = content.last_columns.index("name")
    pk = content.last_rows[0]["id"]
    msg = content._save_cell(0, col, "Bea")
    assert "1" in msg
    assert conn.get("users", pk)["name"] == "Bea"   # persisted
    assert content.last_rows[0]["name"] == "Bea"     # in-memory grid model updated


def test_save_cell_coerces_to_original_type(tmp_path):
    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("nums", {"n": 5})
    content = DbConsoleContent(conn, title_db="k")
    content.run_sql("SELECT * FROM nums")
    col = content.last_columns.index("n")
    pk = content.last_rows[0]["id"]
    content._save_cell(0, col, "42")
    assert conn.get("nums", pk)["n"] == 42           # int, not the string "42"
    assert content.last_rows[0]["n"] == 42


@pytest.mark.asyncio
async def test_cell_dialog_edit_render_and_save(tmp_path):
    # Mounted path: open the cell dialog on a real cell, toggle the markdown
    # render preview, edit the text, Save, and confirm it persists + the grid
    # cell updates.
    from textual.app import App
    from dunders.windowing import Desktop, make_window
    from dunders.fm.db_console import CellEditDialog

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann"})
    content = DbConsoleContent(conn, title_db="k")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        desktop.add_window(make_window(content, title="SQL", size=(90, 30)))
        await pilot.pause()
        content.run_sql("SELECT * FROM users")
        await pilot.pause()
        col = content.last_columns.index("name")
        content._open_cell_dialog(0, col)
        await pilot.pause()
        dialog = app.query_one(CellEditDialog)
        assert dialog._editable is True
        # Toggle markdown preview on, then back to the editor.
        dialog._toggle_render()
        await pilot.pause()
        assert dialog._rendered is True
        dialog._toggle_render()
        await pilot.pause()
        assert dialog._rendered is False
        # Edit + save.
        from dunders.windowing.core.buffer import TextBuffer
        dialog._editor.buffer = TextBuffer.from_string("Bea")
        dialog._do_save()
        await pilot.pause()
        pk = content.last_rows[0]["id"]
        assert conn.get("users", pk)["name"] == "Bea"
        assert content.last_rows[0]["name"] == "Bea"


@pytest.mark.asyncio
async def test_cell_dialog_format_json(tmp_path):
    # The "Format JSON" button pretty-prints the editor text as indented JSON;
    # invalid JSON leaves the buffer untouched and reports the error.
    from textual.app import App
    from dunders.windowing import Desktop, make_window
    from dunders.fm.db_console import CellEditDialog

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("docs", {"body": '{"b":2,"a":[1,2]}'})
    content = DbConsoleContent(conn, title_db="k")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause()
        app.query_one(Desktop).add_window(make_window(content, title="SQL", size=(90, 30)))
        await pilot.pause()
        content.run_sql("SELECT * FROM docs")
        await pilot.pause()
        content._open_cell_dialog(0, content.last_columns.index("body"))
        await pilot.pause()
        dialog = app.query_one(CellEditDialog)
        dialog._format_json()
        # No pause / refocus: the editor must repaint immediately (assigning
        # .buffer alone doesn't rebuild the rendered lines — regression guard).
        assert len(dialog._editor._rendered_lines) == 7
        assert dialog._editor_text() == '{\n  "b": 2,\n  "a": [\n    1,\n    2\n  ]\n}'
        # Invalid JSON: buffer unchanged, error surfaced.
        from dunders.windowing.core.buffer import TextBuffer
        dialog._editor.buffer = TextBuffer.from_string("not json")
        dialog._format_json()
        await pilot.pause()
        assert dialog._editor_text() == "not json"
        assert "not valid json" in dialog._last_status.lower()
        # Python-repr dict (single quotes / True — not strict JSON): normalised.
        dialog._editor.buffer = TextBuffer.from_string("{'role': 'admin', 'ok': True}")
        dialog._format_json()
        await pilot.pause()
        assert dialog._editor_text() == '{\n  "role": "admin",\n  "ok": true\n}'
        assert "normalised from a python literal" in dialog._last_status.lower()


@pytest.mark.asyncio
async def test_page_buttons_visibility(tmp_path):
    # Prev/Next show only for a paginated result and only the directions that
    # lead somewhere: page 0 shows Next (not Prev); the last page shows Prev
    # (not Next); an un-paginated result hides both.
    from textual.app import App
    from dunders.windowing import Desktop, make_window

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    for i in range(250):
        conn.insert("users", {"name": f"u{i}"})
    content = DbConsoleContent(conn, title_db="k")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.query_one(Desktop).add_window(make_window(content, title="SQL", size=(96, 24)))
        await pilot.pause()
        assert not content._prev_btn.display and not content._next_btn.display
        content.run_sql("SELECT * FROM users")
        await pilot.pause()
        assert not content._prev_btn.display and content._next_btn.display  # page 0
        content._next_page()
        await pilot.pause()
        assert content._prev_btn.display and not content._next_btn.display   # last page
        content.run_sql("UPDATE users SET name=name")
        await pilot.pause()
        assert not content._prev_btn.display and not content._next_btn.display  # unpaged


@pytest.mark.asyncio
async def test_console_bottom_border_shows_tab_hint(tmp_path):
    # The console writes a Tab-focus hint onto the window's bottom border
    # (via window_subtitle, rendered on the frame's bottom row).
    from textual.app import App
    from dunders.windowing import Desktop, make_window

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    content = DbConsoleContent(conn, title_db="k")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        win = make_window(content, title="SQL", size=(80, 20))
        app.query_one(Desktop).add_window(win)
        await pilot.pause()
        assert "Tab" in (win.decorations.subtitle or "")
        assert "Tab" in (content.window_subtitle or "")


@pytest.mark.asyncio
async def test_tab_toggles_focus_between_sql_editor_and_grid(tmp_path):
    # Inside the SQL console, Tab switches focus between the SQL editor and the
    # result grid (the app-level priority Tab binding delegates to the console's
    # focus_other_pane rather than switching file panels or inserting a tab).
    from dunders.app import DundersApp

    dbfile = tmp_path / "t.db"
    seed = da.DbConn.open(f"sqlite:///{dbfile}")
    seed.insert("users", {"name": "Ann"})
    seed.close()

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        prov = app._vfs_registry.for_scheme("db")
        target = prov.resolve_target(f"sqlite:///{dbfile}", base=panel.cwd_loc)
        app._apply_open_result(panel, "spec", (target, None))
        await pilot.pause()
        panel.cursor = next(i for i, e in enumerate(panel.entries)
                            if e.extra.get("db.kind") == "table")
        app.action_view()                       # F3 -> SQL console (SELECT *)
        await pilot.pause()
        await pilot.pause()
        console = [w for w in app.desktop.windows
                   if isinstance(w.content, DbConsoleContent)][-1].content
        console.run_sql("SELECT * FROM users")
        await pilot.pause()
        console._editor.focus()
        await pilot.pause()
        assert console._editor.has_focus
        await pilot.press("tab")
        await pilot.pause()
        assert console._table.has_focus and not console._editor.has_focus
        await pilot.press("tab")
        await pilot.pause()
        assert console._editor.has_focus and not console._table.has_focus


@pytest.mark.asyncio
async def test_cell_dialog_close_returns_focus_to_clicked_cell(tmp_path):
    # Closing the dialog hands focus back to the result grid, on the very cell
    # the dialog was opened from (Esc and Close both go through _dismiss_modal).
    from textual.app import App
    from textual.coordinate import Coordinate
    from dunders.windowing import Desktop, make_window
    from dunders.fm.db_console import CellEditDialog

    conn = da.DbConn.open(f"sqlite:///{tmp_path/'t.db'}")
    conn.insert("users", {"name": "Ann", "age": 30})
    content = DbConsoleContent(conn, title_db="k")

    class _Host(App):
        def compose(self):
            yield Desktop()

    app = _Host()
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        desktop.add_window(make_window(content, title="SQL", size=(90, 30)))
        await pilot.pause()
        content.run_sql("SELECT * FROM users")
        await pilot.pause()
        col = content.last_columns.index("age")
        content._open_cell_dialog(0, col)
        await pilot.pause()
        assert app.query(CellEditDialog)
        # Real Esc keypress (not a direct action_close call): the modal strips
        # the grid's can_focus until it unmounts, so focus restore must wait for
        # the thaw — a regression guard that a single deferral would miss.
        await pilot.press("escape")
        for _ in range(12):
            await pilot.pause()
        assert not app.query(CellEditDialog)               # modal gone
        assert content._table.has_focus                    # grid refocused
        assert content._table.cursor_coordinate == Coordinate(0, col)
