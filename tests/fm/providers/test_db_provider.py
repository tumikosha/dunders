import pytest

from dunders.core.vfs import VfsPath
from dunders.fm.providers.db_provider import DbProvider, _normalize_root


def test_connection_password_recovers_for_bookmarks():
    # The password is kept only in the provider (out of the locator); bookmarks
    # ask for it via connection_password so a saved db connection re-auths.
    p = DbProvider()
    url = "postgresql://user:s3cret@host:5432/db"
    root = _normalize_root(url)
    assert "s3cret" not in root  # stripped from the locator id
    p._urls[root] = url
    assert p.connection_password(root) == "s3cret"
    # A credential-less URL (e.g. SQLite) has no password.
    p._urls["sqlite:////a.db"] = "sqlite:////a.db"
    assert p.connection_password("sqlite:////a.db") is None
    # Unknown root → None.
    assert p.connection_password("never-seen") is None


@pytest.fixture
def db_url(tmp_path):
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url)
    c.insert("users", {"name": "Ann"})
    c.close()
    return url


def test_resolve_target_opens_root(db_url):
    p = DbProvider()
    loc = p.resolve_target(db_url, base=VfsPath.local("/tmp"))
    assert loc is not None and loc.scheme == "db" and loc.parts == ()


def test_resolve_target_bad_url_raises():
    # An unknown dialect fails at engine-setup time, deterministically and
    # independent of which DBAPI drivers are installed — unlike a bad host
    # (e.g. postgresql://…:1), which SQLAlchemy connects to lazily, so it would
    # NOT raise here once psycopg2 is present.
    p = DbProvider()
    with pytest.raises(OSError):
        p.resolve_target("zzznope://x", base=VfsPath.local("/tmp"))


def test_registered_when_dbset_present():
    from dunders.fm.vfs_local import default_registry
    assert "db" in default_registry().schemes()


def _root(p, url):
    return p.resolve_target(url, base=VfsPath.local("/tmp"))


def test_scan_root_lists_tables(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    names = [e.name for e in p.scan(root, include_parent=False)]
    assert "users" in names


def test_scan_table_lists_records_as_json(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    rows = [e for e in p.scan(root.child("users"), include_parent=False)]
    assert rows and all(e.name.endswith(".json") for e in rows)
    assert all(not e.is_dir for e in rows)


def test_is_dir_table_true_record_false(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    assert p.is_dir(root.child("users")) is True
    rec = p.scan(root.child("users"), include_parent=False)[0]
    assert p.is_dir(rec.loc) is False


def test_open_read_record_is_json(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    rec = p.scan(root.child("users"), include_parent=False)[0]
    import json
    obj = json.loads(p.open_read(rec.loc).read().decode())
    assert obj["name"] == "Ann"


def test_export_as_file_table_is_jsonl(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    name, stream = p.export_as_file(root.child("users"))
    assert name == "users.jsonl"
    lines = stream.read().decode().strip().splitlines()
    import json
    assert json.loads(lines[0])["name"] == "Ann"


def test_export_streams_lazily_without_buffering_whole_table(tmp_path):
    """The table export must page on demand (read(size) returns partial data
    and only fetches as far as needed) rather than materialising the whole
    table up front — that eager build froze the copy bar at 0%."""
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'big.db'}"
    c = da.DbConn.open(url)
    for i in range(50):
        c.insert("t", {"v": "x" * 100})
    c.close()
    p = DbProvider()
    root = _root(p, url)
    name, stream = p.export_as_file(root.child("t"))
    assert name == "t.jsonl"
    first = stream.read(64)               # a small slice
    assert 0 < len(first) <= 64
    rest = stream.read()                  # drain the remainder
    assert stream.read() == b""           # EOF is sticky
    full = (first + rest).decode().strip().splitlines()
    assert len(full) == 50                # every record made it out


def test_export_size_hint(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    # A real table -> a positive byte estimate; a record/non-table -> None.
    assert p.export_size_hint(root.child("users")) > 0
    rec = p.scan(root.child("users"), include_parent=False)[0]
    assert p.export_size_hint(rec.loc) is None
    assert p.export_size_hint(root.child("nope")) is None


def test_measure_uses_export_hint_not_tree_walk(db_url):
    """_measure must size a table via export_size_hint (1 file, estimated
    bytes) instead of recursing through its record pages."""
    from dunders.fm.vfs_engine import _measure
    from dunders.fm.vfs_local import default_registry
    reg = default_registry()
    p = reg.for_scheme("db")
    root = p.resolve_target(db_url, base=VfsPath.local("/tmp"))
    files, total = _measure(reg, root.child("users"))
    assert files == 1
    assert total == p.export_size_hint(root.child("users"))


def test_export_as_file_record_is_none(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    rec = p.scan(root.child("users"), include_parent=False)[0]
    assert p.export_as_file(rec.loc) is None


def test_import_json_inserts_record(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    target = root.child("users").child("new.json")
    with p.open_write(target) as w:
        w.write(b'{"name": "Cleo", "age": 41}')
    conn = p.conn_for(root.root)
    assert conn.count("users") == 2


def test_overwrite_updates_record(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    rec = p.scan(root.child("users"), include_parent=False)[0]
    with p.open_write(rec.loc, overwrite=True) as w:
        w.write(b'{"name": "Ann", "age": 77}')
    table, pk = p._record_pk(rec.loc)
    assert p.conn_for(root.root).get(table, pk)["age"] == 77


def test_import_jsonl_inserts_rows(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    target = root.child("imported.jsonl")
    with p.open_write(target) as w:
        w.write(b'{"name": "X"}\n{"name": "Y"}\n')
    assert p.conn_for(root.root).count("imported") == 2


def test_import_jsonl_inserts_incrementally_not_at_close(db_url):
    """The .jsonl writer must insert records as bytes stream in (in batches) —
    not buffer the whole file and insert everything in close(). Buffering froze
    the copy bar at 100% (bytes had landed in the buffer) while a million
    single-row inserts ran afterwards. Here a full batch flushes DURING write()
    and is visible before close; the remainder flushes on close."""
    from dunders.fm.providers.db_provider import _JsonlImporter
    batch = _JsonlImporter._BATCH
    p = DbProvider()
    root = _root(p, db_url)
    conn = p.conn_for(root.root)
    w = p.open_write(root.child("streamed.jsonl"))
    n = batch + 50
    w.write(b"".join(b'{"v": %d}\n' % i for i in range(n)))
    assert conn.count("streamed") == batch   # one batch inserted before close
    w.close()
    assert conn.count("streamed") == n        # remainder flushed on close


def test_import_jsonl_large_spans_batches(db_url):
    """An import larger than one insert batch lands every row (exercises the
    multi-batch insert_many path)."""
    p = DbProvider()
    root = _root(p, db_url)
    n = 1200  # > _JsonlImporter._BATCH (500)
    payload = b"".join(b'{"v": %d}\n' % i for i in range(n))
    with p.open_write(root.child("bulk.jsonl")) as w:
        # feed it in arbitrary slices to exercise the line-reassembly tail
        for i in range(0, len(payload), 7000):
            w.write(payload[i : i + 7000])
    assert p.conn_for(root.root).count("bulk") == n


def test_import_jsonl_new_table_strips_pk_consistently(db_url):
    """Importing a .jsonl into a NOT-yet-existing table must strip the PK from
    EVERY row, including the first (table-creating) one.

    The old code re-checked ``table in conn.tables()`` per line, so the first
    row kept its explicit PK (table absent → not stripped) while later rows had
    it stripped. On Postgres that left the freshly-created serial sequence at
    its start value (an explicit-id insert does not advance it), and the next
    sequence-assigned id collided (UniqueViolation: "Key (id)=(1) already
    exists"). SQLite hides the collision (autoincrement = max(rowid)+1), so we
    assert the observable proxy: the DB assigns fresh 1..N PKs rather than
    preserving the source ids (which start at 100 here).
    """
    p = DbProvider()
    root = _root(p, db_url)
    target = root.child("fresh.jsonl")
    with p.open_write(target) as w:
        w.write(b'{"id": 100, "name": "A"}\n{"id": 200, "name": "B"}\n')
    conn = p.conn_for(root.root)
    rows = conn.fetch("fresh", offset=0, limit=10)
    assert sorted(r["id"] for r in rows) == [1, 2]


def test_delete_record(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    rec = p.scan(root.child("users"), include_parent=False)[0]
    p.delete([rec.loc])
    assert p.conn_for(root.root).count("users") == 0


def test_delete_table_drops_it(db_url):
    """delete() on a table locator (parts=("users",)) drops the whole table."""
    p = DbProvider()
    root = _root(p, db_url)
    conn = p.conn_for(root.root)
    assert "users" in conn.tables()
    result = p.delete([root.child("users")])
    assert not result.errors
    assert "users" not in conn.tables()


def test_delete_non_table_loc_is_noop(db_url):
    """delete() on a single-part loc that is NOT a real table (e.g. an index
    name) is a clean no-op — only records (.json) and real tables are acted on."""
    p = DbProvider()
    root = _root(p, db_url)
    idx_loc = root.child("some_index")
    result = p.delete([idx_loc])
    assert not result.errors
    # source table untouched
    assert p.conn_for(root.root).count("users") == 1
    assert "users" in p.conn_for(root.root).tables()


def test_record_copies_db_to_db(tmp_path):
    from dunders.fm.providers import db_access as da
    from dunders.fm.vfs_engine import transfer
    from dunders.fm.vfs_local import default_registry
    from dunders.core.vfs import VfsPath
    src_url = f"sqlite:///{tmp_path/'a.db'}"
    dst_url = f"sqlite:///{tmp_path/'b.db'}"
    a = da.DbConn.open(src_url)
    a.insert("t", {"name": "Z"})
    a.close()
    b = da.DbConn.open(dst_url)
    b.insert("t", {"name": "seed"})
    b.close()

    reg = default_registry()
    p = reg.for_scheme("db")
    src_root = p.resolve_target(src_url, base=VfsPath.local(str(tmp_path)))
    dst_root = p.resolve_target(dst_url, base=VfsPath.local(str(tmp_path)))
    rec = p.scan(src_root.child("t"), include_parent=False)[0]
    res = transfer(reg, [rec.loc], dst_root.child("t"), mode="copy")
    assert not res.errors
    assert p.conn_for(dst_root.root).count("t") == 2


def test_reimport_into_same_table_duplicates(db_url):
    """Pins the v1 PK-strip trade-off: importing a table's own records back
    into the same table DUPLICATES rows (new PKs assigned) rather than
    restoring by identity. This is the accepted cost of stripping the PK so
    cross-DB copy avoids UNIQUE collisions — assert it stays explicit so a
    future change can't silently alter the contract.
    """
    p = DbProvider()
    root = _root(p, db_url)
    conn = p.conn_for(root.root)
    before = conn.count("users")  # seeded with one row ("Ann")
    # Re-import that same row as a .jsonl into the SAME table.
    target = root.child("users.jsonl")
    with p.open_write(target) as w:
        w.write(b'{"id": 1, "name": "Ann"}\n')
    assert conn.count("users") == before * 2  # duplicated, not restored


def test_view_magic_and_extensions():
    p = DbProvider()
    assert b"SQLite format 3\x00" in p.view_magic
    assert p.view_extensions == ()  # magic is authoritative; no ambiguous-extension matching


def test_spec_from_path_is_absolute_sqlite_url():
    p = DbProvider()
    spec = p.spec_from_path("/a/b.db")
    assert spec == "sqlite:////a/b.db"  # 4 slashes = SQLAlchemy absolute form


def test_spec_from_path_round_trips_through_resolve_target(tmp_path):
    from dunders.fm.providers import db_access as da
    f = tmp_path / "real.db"
    da.DbConn.open(f"sqlite:///{f}").close()  # create a real sqlite file
    p = DbProvider()
    loc = p.resolve_target(p.spec_from_path(str(f)), base=VfsPath.local(str(tmp_path)))
    assert loc is not None and loc.scheme == "db" and loc.parts == ()


def test_sql_console_action_is_panel_level_not_per_row():
    # The SQL console action must NOT paint a per-row ⌘ icon (applies_to False),
    # but must still exist so the Database menu / Alt+S can reach it.
    from dunders.fm.file_entry import FileEntry
    p = DbProvider()
    acts = p.actions()
    sql = next(a for a in acts if a.id == "db.sql")
    assert sql.hotkey == "alt+s"
    entry = FileEntry(loc=VfsPath(scheme="db", root="x", parts=("t", "1.json")),
                      name="1.json", size=0, mtime=0.0, is_dir=False)
    assert sql.applies_to(entry) is False


def _more_entry(entries):
    return next((e for e in entries if e.extra.get("db.kind") == "more"), None)


def test_paging_more_chains_past_second_page(tmp_path, monkeypatch):
    # Clicking "▼ more" must keep working past the first page. The page-N "more"
    # entry's loc must always be <table>/_page/<N+1> — never accrue extra _page
    # segments — or _table_and_page does int("_page") and raises ValueError.
    from dunders.fm.providers import db_access as da
    from dunders.fm.providers import db_provider as mod

    monkeypatch.setattr(mod, "_DB_PAGE", 2)
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url)
    for i in range(5):  # 5 rows over a page size of 2 → pages 1,2,3
        c.insert("nums", {"v": i})
    c.close()

    p = DbProvider()
    root = _root(p, url)

    page1 = p.scan(root.child("nums"), include_parent=False)
    more1 = _more_entry(page1)
    assert more1 is not None and more1.loc.parts == ("nums", "_page", "2")

    page2 = p.scan(more1.loc, include_parent=False)  # first "more" — already worked
    more2 = _more_entry(page2)
    assert more2 is not None and more2.loc.parts == ("nums", "_page", "3")

    # The regression: scanning the SECOND "more" raised "invalid literal for int".
    page3 = p.scan(more2.loc, include_parent=False)
    assert _more_entry(page3) is None  # last page, no further "more"
    records = [e for e in page3 if e.extra.get("db.kind") == "record"]
    assert len(records) == 1  # the 5th row


def test_history_action_is_panel_level():
    # SQL history is reachable from the Database menu too; like the console it is
    # a panel-level action (no per-row icon).
    from dunders.fm.file_entry import FileEntry
    p = DbProvider()
    hist = next(a for a in p.actions() if a.id == "db.history")
    assert hist.label == "SQL history"
    entry = FileEntry(loc=VfsPath(scheme="db", root="x", parts=("t", "1.json")),
                      name="1.json", size=0, mtime=0.0, is_dir=False)
    assert hist.applies_to(entry) is False
