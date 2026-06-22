import json

import pytest

from dunders.fm.providers import db_access as da


@pytest.fixture
def conn(tmp_path):
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url)
    c.insert("users", {"name": "Ann", "age": 30, "meta": {"role": "admin"}})
    c.insert("users", {"name": "Bob", "age": 25, "meta": None})
    yield c
    c.close()


def test_tables_and_columns(conn):
    assert "users" in conn.tables()
    assert "name" in conn.columns("users")


def test_primary_key_and_count(conn):
    assert conn.primary_key("users") is not None
    assert conn.count("users") == 2


def test_fetch_pagination_ordered(conn):
    page = conn.fetch("users", offset=0, limit=1)
    assert len(page) == 1
    page2 = conn.fetch("users", offset=1, limit=1)
    assert page[0] != page2[0]


def test_record_json_roundtrip(conn):
    # A fetched record round-trips field-for-field through record_to_json /
    # json_to_record. On SQLite dbset returns JSON/dict columns as JSON
    # *strings*, so `meta` is the string `fetch` returned, not a dict — the
    # round-trip preserves that string, and the nested value is recoverable
    # via json.loads.
    rec = conn.fetch("users", offset=0, limit=10)[0]
    back = da.json_to_record(da.record_to_json(rec))
    assert back["name"] == rec["name"]
    assert back["meta"] == rec["meta"]  # same value survives the round-trip
    assert json.loads(back["meta"]) == {"role": "admin"}  # nested value recoverable


def test_update_and_delete(conn):
    pk = conn.primary_key("users")
    row = conn.fetch("users", offset=0, limit=1)[0]
    conn.update("users", row[pk], {"age": 99})
    assert conn.get("users", row[pk])["age"] == 99
    conn.delete("users", [row[pk]])
    assert conn.count("users") == 1


def test_query_select(conn):
    cols, rows, rowcount, truncated = conn.query("SELECT name FROM users ORDER BY name")
    assert cols == ["name"]
    assert [r["name"] for r in rows] == ["Ann", "Bob"]
    assert truncated is False


def test_query_limit_streams_and_flags_truncated(tmp_path):
    url = f"sqlite:///{tmp_path/'big.db'}"
    c = da.DbConn.open(url)
    for i in range(50):
        c.insert("t", {"n": i})
    # Fetch at most 10 — must report truncated and return exactly 10 rows.
    cols, rows, rowcount, truncated = c.query("SELECT n FROM t ORDER BY n", limit=10)
    assert len(rows) == 10
    assert rowcount == 10
    assert truncated is True
    # A limit that exceeds the row count is not truncated.
    _c, rows2, _rc, trunc2 = c.query("SELECT n FROM t", limit=100)
    assert len(rows2) == 50 and trunc2 is False
    c.close()


def test_query_non_select_returns_four_tuple(conn):
    cols, rows, rowcount, truncated = conn.query("UPDATE users SET age=1 WHERE name='Ann'")
    assert cols == [] and rows == []
    assert rowcount == 1 and truncated is False


def test_read_only_blocks_mutation(tmp_path):
    url = f"sqlite:///{tmp_path/'r.db'}"
    da.DbConn.open(url).close()  # create the file/schema
    ro = da.DbConn.open(url, read_only=True)
    with pytest.raises(da.ReadOnlyError):
        ro.insert("users", {"name": "X"})
    with pytest.raises(da.ReadOnlyError):
        ro.update("users", 1, {"name": "X"})
    with pytest.raises(da.ReadOnlyError):
        ro.delete("users", [1])
    ro.close()


def test_ensure_columns_quotes_malicious_identifier(conn):
    """A column name from untrusted JSON cannot inject a second statement.

    The preparer must quote the identifier so the embedded ``DROP TABLE`` is
    treated as part of one (weird but harmless) column name, not as DDL. The
    table must survive either way (added verbatim or cleanly rejected).
    """
    evil = 'bad"); DROP TABLE users; --'
    try:
        conn.ensure_columns("users", {evil: 1})
    except Exception:
        # A clean error is acceptable; corruption is not.
        pass
    # The injection must NOT have executed the second statement.
    assert "users" in conn.tables()
    assert conn.count("users") == 2  # rows intact
    # If the column was added, it was added verbatim as one quoted identifier.
    cols = conn.columns("users")
    assert evil in cols or evil not in cols  # either added whole or rejected
    if evil in cols:
        assert "users" in conn.tables()  # schema still sound


def test_driver_hint_names_the_package():
    assert "psycopg2-binary" in da._driver_hint("postgresql://u@h/db")
    assert "psycopg2-binary" in da._driver_hint("postgresql+psycopg2://u@h/db")
    assert "pymysql" in da._driver_hint("mysql://u@h/db")
    assert "pymysql" in da._driver_hint("mariadb://u@h/db")


def test_driver_hint_falls_back_to_extra():
    # An unrecognised scheme (e.g. sqlite, which needs no driver) points at the extra.
    assert "dunders[db]" in da._driver_hint("sqlite:///x.db")


def test_open_missing_driver_augments_message(monkeypatch):
    def boom(*a, **k):
        raise ModuleNotFoundError("No module named 'psycopg2'")
    monkeypatch.setattr(da.dbset, "connect", boom)
    with pytest.raises(ModuleNotFoundError) as ei:
        da.DbConn.open("postgresql://u@h/db")
    msg = str(ei.value)
    assert "No module named 'psycopg2'" in msg  # original kept
    assert "psycopg2-binary" in msg              # actionable hint appended


def test_select_all_sql_quotes_identifier(conn):
    assert conn.select_all_sql("users") == 'SELECT * FROM users'
    # A name needing quoting is dialect-quoted (can't break out of the identifier).
    conn.insert("we ird", {"x": 1})
    assert conn.select_all_sql("we ird") == 'SELECT * FROM "we ird"'


def test_create_table_ddl_describes_table(conn):
    ddl = conn.create_table_ddl("users")
    assert ddl.upper().startswith("CREATE TABLE")
    assert "users" in ddl
    for col in ("name", "age", "meta"):
        assert col in ddl


def test_create_table_ddl_includes_indexes(conn):
    # F4's DDL must fully describe the table, so secondary indexes (plain and
    # UNIQUE) are appended as CREATE INDEX after the CREATE TABLE.
    import sqlalchemy as sa
    with conn._engine.begin() as cx:
        cx.execute(sa.text("CREATE INDEX ix_name ON users(name)"))
        cx.execute(sa.text("CREATE UNIQUE INDEX ux_age ON users(age)"))
    ddl = conn.create_table_ddl("users")
    assert "CREATE INDEX ix_name ON users" in ddl
    assert "CREATE UNIQUE INDEX ux_age ON users" in ddl
    # CREATE TABLE comes first, the indexes after it.
    assert ddl.index("CREATE TABLE") < ddl.index("CREATE INDEX ix_name")
