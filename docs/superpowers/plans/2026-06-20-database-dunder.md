# Database dunder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Database" dunder that opens a SQL database in a panel (tables + indexes → records), supports copy-between-panels (record↔file, table↔file, db↔db) via the existing `transfer()` engine, and ships a dedicated SQL console window.

**Architecture:** A `db_access.py` adapter isolates all `dbset`/SQLAlchemy calls. `DbProvider` (a `VfsProvider`, `scheme="db"`) renders a connection as a panel by mapping tables→directories and records→files, so the existing panel + copy engine work unchanged. A `DbConsoleContent` window hosts a multi-line SQL editor over a lazy result grid. Record editing reuses the existing VFS-member edit path (`open_write(loc, overwrite=True)` → `update`).

**Tech Stack:** Python ≥3.12, `dbset` (SQLAlchemy 2.x wrapper, sync API), Textual, pytest (asyncio auto-mode).

## Global Constraints

- Python ≥3.12 (project floor).
- `dbset` is an **opt-in extra** `dunders[db]`; `DbProvider` registers in `default_registry()` **only when `dbset` imports** (mirror the paramiko/sftp guard). The `db:` scheme must not appear otherwise.
- All connect / query / mutation calls run on **worker threads** and marshal UI updates via `self.call_from_thread(...)`. Connect declares `capabilities ∋ "slow"` so the existing `_do_open_dunder` worker path is used.
- The password (if any) stays **out of** the `VfsPath`; it lives in the provider keyed by `root` (the sftp split).
- `ruff check` clean; tests live under `tests/fm/` mirroring source layout; async tests need no decorator (asyncio_mode=auto).
- Record file = `<pk>.json` (one object). Whole table = `<table>.jsonl` (one record per line).
- Never reach into `dbset`/SQLAlchemy outside `db_access.py`.

## File Structure

- Create `dunders/fm/providers/db_access.py` — the dbset/SQLAlchemy adapter: `DbConn` + JSON (de)serialization. The only module that imports `dbset`/`sqlalchemy`.
- Create `dunders/fm/providers/db_provider.py` — `DbProvider(VfsProvider)` with `resolve_target`, `scan`, `is_dir`, `open_read`, `open_write`, `delete`, `mkdir`, `export_as_file`, `columns`, `actions`.
- Create `dunders/fm/db_console.py` — `DbConsoleContent(WindowContent)`: SQL editor + lazy result grid.
- Modify `dunders/fm/vfs_local.py` — register `DbProvider` behind an import guard.
- Modify `dunders/fm/vfs_engine.py` — let a source provider opt a "directory" source into single-file export (`export_as_file`).
- Modify `dunders/app.py` — handle the `provider.db.sql` action → mount `DbConsoleContent`.
- Modify `pyproject.toml` — add the `db` extra and a dev dependency.
- Tests: `tests/fm/providers/test_db_access.py`, `tests/fm/providers/test_db_provider.py`, `tests/fm/test_db_console.py`.

---

### Task 1: Packaging + the `dbset` adapter (`db_access.py`)

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)
- Create: `dunders/fm/providers/db_access.py`
- Test: `tests/fm/providers/test_db_access.py`

**Interfaces:**
- Produces:
  - `record_to_json(rec: dict) -> bytes` and `json_to_record(data: bytes) -> dict`
  - `class DbConn` with:
    - classmethod `open(url: str, *, read_only: bool = False) -> DbConn`
    - `tables() -> list[str]`
    - `indexes() -> list[tuple[str, str]]` → `(index_name, table_name)`
    - `index_ddl(index_name: str) -> str`
    - `columns(table: str) -> list[str]`
    - `primary_key(table: str) -> str | None` (single-column PK, else `rowid` for sqlite, else `None`)
    - `count(table: str) -> int`
    - `fetch(table: str, *, offset: int, limit: int) -> list[dict]` (ordered by PK/rowid)
    - `get(table: str, pk_value) -> dict | None`
    - `insert(table: str, rec: dict) -> object` (returns pk; uses `upsert` when PK present)
    - `update(table: str, pk_value, rec: dict) -> int`
    - `delete(table: str, pk_values: list) -> int`
    - `query(sql: str) -> tuple[list[str], list[dict], int]` → `(columns, rows, rowcount)`
    - `read_only: bool`
    - `close() -> None`
  - `class ReadOnlyError(Exception)` (raised by mutations when `read_only`)

**Implementation notes (read before coding):**
- `dbset` is an external module not yet installed. **First step of this task:** `pip install -e '.[db,dev]'`, then introspect the real sync surface once: `python -c "import dbset, inspect; print([m for m in dir(dbset.connect('sqlite:///:memory:')) if not m.startswith('__')])"`. Adjust the dbset calls below to the installed method names; the adapter tests are the oracle.
- Use **dbset** for the connection and the mutating CRUD (`insert`/`update`/`delete`/`upsert`) so its JSON/type handling applies. Use **SQLAlchemy** (via an engine obtained from the same connection, falling back to `create_engine`) for metadata (`inspect`), paged reads, and raw `query` — these are stable and version-proof.
- Get the engine defensively: `engine = getattr(db, "engine", None) or getattr(db, "_engine", None)`; if `None`, `engine = sqlalchemy.create_engine(url)`.
- `:memory:` SQLite is unsupported (ephemeral, and the dual-handle would see two DBs) — document it; tests use a temp-file SQLite.

- [ ] **Step 1: Add the extra to `pyproject.toml`**

Add under `[project.optional-dependencies]` (after the `office` extra) and append `dbset` to `dev`:

```toml
# Opt-in database dunder (the db: dunder). dbset wraps SQLAlchemy 2.x
# (SQLite/Postgres/MySQL); kept out of the default install to stay light.
db = ["dbset>=0.1"]
```

In the existing `dev = [ ... ]` list add the line:

```toml
    "dbset>=0.1",     # database dunder adapter tests (in-memory/temp SQLite)
```

- [ ] **Step 2: Install and write the failing test**

Run `pip install -e '.[db,dev]'` first. Create `tests/fm/providers/test_db_access.py`:

```python
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


def test_record_json_roundtrip_preserves_nested(conn):
    rec = conn.fetch("users", offset=0, limit=10)[0]
    back = da.json_to_record(da.record_to_json(rec))
    assert back["name"] == rec["name"]
    assert back["meta"] == rec["meta"]  # nested dict survives


def test_update_and_delete(conn):
    pk = conn.primary_key("users")
    row = conn.fetch("users", offset=0, limit=1)[0]
    conn.update("users", row[pk], {"age": 99})
    assert conn.get("users", row[pk])["age"] == 99
    conn.delete("users", [row[pk]])
    assert conn.count("users") == 1


def test_query_select(conn):
    cols, rows, rowcount = conn.query("SELECT name FROM users ORDER BY name")
    assert cols == ["name"]
    assert [r["name"] for r in rows] == ["Ann", "Bob"]


def test_read_only_blocks_mutation(tmp_path):
    url = f"sqlite:///{tmp_path/'r.db'}"
    da.DbConn.open(url).close()  # create the file/schema
    ro = da.DbConn.open(url, read_only=True)
    with pytest.raises(da.ReadOnlyError):
        ro.insert("users", {"name": "X"})
    ro.close()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/fm/providers/test_db_access.py -x`
Expected: FAIL (`ModuleNotFoundError: dunders.fm.providers.db_access`).

- [ ] **Step 4: Implement `db_access.py`**

```python
"""db_access — the single seam to dbset / SQLAlchemy for the db: dunder.

Connection and mutating CRUD go through dbset (its JSON/type handling); read
metadata, paged reads, and raw SQL go through SQLAlchemy (stable across
versions). Nothing else in the codebase imports dbset or sqlalchemy.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import dbset
import sqlalchemy as sa


__all__ = ["DbConn", "ReadOnlyError", "record_to_json", "json_to_record"]


class ReadOnlyError(Exception):
    """A mutation attempted on a read-only connection."""


def _json_default(o: Any) -> Any:
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (bytes, bytearray)):
        return o.decode("utf-8", "replace")
    return str(o)


def record_to_json(rec: dict) -> bytes:
    return json.dumps(rec, ensure_ascii=False, indent=2, default=_json_default).encode("utf-8")


def json_to_record(data: bytes) -> dict:
    obj = json.loads(data.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("a record JSON file must contain a single object")
    return obj


class DbConn:
    def __init__(self, db, engine: sa.Engine, read_only: bool) -> None:
        self._db = db
        self._engine = engine
        self.read_only = read_only

    @classmethod
    def open(cls, url: str, *, read_only: bool = False) -> "DbConn":
        db = dbset.connect(url, read_only=read_only)
        engine = getattr(db, "engine", None) or getattr(db, "_engine", None)
        if engine is None:
            engine = sa.create_engine(url)
        return cls(db, engine, read_only)

    def _guard(self) -> None:
        if self.read_only:
            raise ReadOnlyError("connection is read-only")

    def tables(self) -> list[str]:
        return list(sa.inspect(self._engine).get_table_names())

    def indexes(self) -> list[tuple[str, str]]:
        insp = sa.inspect(self._engine)
        out: list[tuple[str, str]] = []
        for table in insp.get_table_names():
            for idx in insp.get_indexes(table):
                name = idx.get("name")
                if name:
                    out.append((name, table))
        return out

    def index_ddl(self, index_name: str) -> str:
        insp = sa.inspect(self._engine)
        for table in insp.get_table_names():
            for idx in insp.get_indexes(table):
                if idx.get("name") == index_name:
                    cols = ", ".join(idx.get("column_names") or [])
                    uniq = "UNIQUE " if idx.get("unique") else ""
                    return f"{uniq}INDEX {index_name} ON {table} ({cols})"
        return f"INDEX {index_name}"

    def columns(self, table: str) -> list[str]:
        return [c["name"] for c in sa.inspect(self._engine).get_columns(table)]

    def primary_key(self, table: str) -> str | None:
        cols = sa.inspect(self._engine).get_pk_constraint(table).get("constrained_columns") or []
        if len(cols) == 1:
            return cols[0]
        if self._engine.dialect.name == "sqlite":
            return "rowid"
        return None

    def _order_col(self, table: str) -> str:
        return self.primary_key(table) or (self.columns(table) or ["1"])[0]

    def count(self, table: str) -> int:
        t = sa.text(f'SELECT COUNT(*) FROM "{table}"')
        with self._engine.connect() as cx:
            return int(cx.execute(t).scalar() or 0)

    def fetch(self, table: str, *, offset: int, limit: int) -> list[dict]:
        order = self._order_col(table)
        cols = "rowid AS rowid, *" if order == "rowid" else "*"
        sql = sa.text(f'SELECT {cols} FROM "{table}" ORDER BY "{order}" LIMIT :l OFFSET :o')
        with self._engine.connect() as cx:
            return [dict(r) for r in cx.execute(sql, {"l": limit, "o": offset}).mappings()]

    def get(self, table: str, pk_value) -> dict | None:
        order = self._order_col(table)
        cols = "rowid AS rowid, *" if order == "rowid" else "*"
        sql = sa.text(f'SELECT {cols} FROM "{table}" WHERE "{order}" = :v')
        with self._engine.connect() as cx:
            row = cx.execute(sql, {"v": pk_value}).mappings().first()
        return dict(row) if row is not None else None

    def insert(self, table: str, rec: dict) -> object:
        self._guard()
        return self._db[table].insert(dict(rec))

    def update(self, table: str, pk_value, rec: dict) -> int:
        self._guard()
        pk = self.primary_key(table)
        values = {k: v for k, v in rec.items() if k != "rowid"}
        return int(self._db[table].update(values, **{pk: pk_value}) or 0)

    def delete(self, table: str, pk_values: list) -> int:
        self._guard()
        pk = self.primary_key(table)
        n = 0
        for v in pk_values:
            n += int(self._db[table].delete(**{pk: v}) or 0)
        return n

    def query(self, sql: str) -> tuple[list[str], list[dict], int]:
        with self._engine.begin() as cx:
            result = cx.execute(sa.text(sql))
            if result.returns_rows:
                mappings = result.mappings().all()
                cols = list(result.keys())
                return cols, [dict(m) for m in mappings], len(mappings)
            return [], [], int(result.rowcount or 0)

    def close(self) -> None:
        try:
            close = getattr(self._db, "close", None)
            if callable(close):
                close()
        finally:
            self._engine.dispose()
```

> Note: the dbset calls in `insert`/`update`/`delete` reflect the README's sync surface. If `pip install` pulls a version whose method names differ, the Step-2 tests will fail with a clear `AttributeError` — adjust those three methods (only) to the installed names; everything else uses SQLAlchemy and is version-stable.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/fm/providers/test_db_access.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
ruff check dunders/fm/providers/db_access.py
git add pyproject.toml dunders/fm/providers/db_access.py tests/fm/providers/test_db_access.py
git commit -m "feat(db): dbset/SQLAlchemy adapter for the database dunder"
```

---

### Task 2: Provider skeleton, registration, and `resolve_target`

**Files:**
- Create: `dunders/fm/providers/db_provider.py`
- Modify: `dunders/fm/vfs_local.py` (`default_registry`)
- Test: `tests/fm/providers/test_db_provider.py`

**Interfaces:**
- Consumes: `db_access.DbConn` (Task 1).
- Produces:
  - `class DbProvider` with `scheme = "db"`, `display_name = "Database"`,
    `capabilities = frozenset({"read", "write", "slow"})`, `open_placeholder` (str),
    `resolve_target(spec, *, base, password=None) -> VfsPath | None`,
    `conn_for(root) -> DbConn` (cached live connection keyed by `root`).
  - `root` is the normalized URL **without password**; `parts=()` is the DB root.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from dunders.core.vfs import VfsPath
from dunders.fm.providers.db_provider import DbProvider


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
    p = DbProvider()
    with pytest.raises(OSError):
        p.resolve_target("postgresql://nobody@127.0.0.1:1/none", base=VfsPath.local("/tmp"))


def test_registered_when_dbset_present():
    from dunders.fm.vfs_local import default_registry
    assert "db" in default_registry().schemes()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/fm/providers/test_db_provider.py -x`
Expected: FAIL (`ModuleNotFoundError: ...db_provider`).

- [ ] **Step 3: Implement the skeleton + `resolve_target`**

Create `dunders/fm/providers/db_provider.py`:

```python
"""DbProvider — browse and edit a SQL database as a panel (the db: dunder).

Maps tables -> directories and records -> files so the universal panel and the
generic transfer engine work unchanged. All dbset/SQLAlchemy access goes
through dunders.fm.providers.db_access. Connections are cached per root (the
normalized URL without password), mirroring SftpProvider._creds.
"""

from __future__ import annotations

import threading
from typing import BinaryIO

from dunders.core.vfs import VfsPath
from dunders.fm.actions import OpResult
from dunders.fm.providers import db_access as da


__all__ = ["DbProvider"]

_DB_PAGE = 1000


def _normalize_root(url: str) -> str:
    """A stable connection id without the password (the locator's root)."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        cred, host = rest.rsplit("@", 1)
        user = cred.split(":", 1)[0]
        rest = f"{user}@{host}" if user else host
    return f"{scheme}://{rest}"


class DbProvider:
    scheme = "db"
    display_name = "Database"
    capabilities = frozenset({"read", "write", "slow"})
    open_placeholder = "connection URL: sqlite:///file.db, postgresql://user@host/db, mysql://…"

    def __init__(self) -> None:
        self._conns: dict[str, da.DbConn] = {}
        self._urls: dict[str, str] = {}     # root -> full url (with password)
        self._lock = threading.Lock()

    # -- connection cache --------------------------------------------------

    def conn_for(self, root: str) -> da.DbConn:
        with self._lock:
            conn = self._conns.get(root)
            if conn is None:
                conn = da.DbConn.open(self._urls.get(root, root), read_only=self._read_only(root))
                self._conns[root] = conn
            return conn

    @staticmethod
    def _read_only(root: str) -> bool:
        return root.endswith("?readonly")

    # -- prefix target (db:<url> opens a connection) -----------------------

    def resolve_target(self, spec: str, *, base: VfsPath, password: str | None = None) -> VfsPath | None:
        url = (spec or "").strip()
        if not url:
            return None
        if password and "://" in url and "@" in url and ":" not in url.split("://", 1)[1].split("@", 1)[0]:
            scheme, rest = url.split("://", 1)
            url = f"{scheme}://{rest.split('@', 1)[0]}:{password}@{rest.split('@', 1)[1]}"
        root = _normalize_root(url)
        try:
            conn = da.DbConn.open(url, read_only=self._read_only(root))
        except Exception as exc:
            raise OSError(f"Cannot connect: {exc}") from exc
        with self._lock:
            self._urls[root] = url
            self._conns[root] = conn
        return VfsPath(scheme="db", root=root, parts=())
```

- [ ] **Step 4: Register the provider in `vfs_local.py`**

In `default_registry()`, after the SFTP guard block, add:

```python
    # Database dunder needs dbset (optional dep); register only when it imports
    # so the "db:" scheme simply doesn't appear otherwise.
    try:
        from dunders.fm.providers.db_provider import DbProvider
        reg.register(DbProvider())
    except ImportError:
        pass
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/fm/providers/test_db_provider.py -v`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
ruff check dunders/fm/providers/db_provider.py dunders/fm/vfs_local.py
git add dunders/fm/providers/db_provider.py dunders/fm/vfs_local.py tests/fm/providers/test_db_provider.py
git commit -m "feat(db): DbProvider skeleton + registration + resolve_target"
```

---

### Task 3: `scan` — tables/indexes at root, paged records inside a table, columns

**Files:**
- Modify: `dunders/fm/providers/db_provider.py`
- Test: `tests/fm/providers/test_db_provider.py`

**Interfaces:**
- Consumes: `DbProvider.conn_for(root)`, `da.DbConn`.
- Produces: `scan(loc, *, show_hidden=False, include_parent=True) -> list[FileEntry]`,
  `is_dir(loc) -> bool`, `columns(loc) -> list[ProviderColumn]`.
  Locator shape: root `parts=()`; a table `parts=("users",)`; a page
  `parts=("users", "_page", "2")`; a record `parts=("users", "<pk>.json")`;
  an index `parts=("idx_name",)` with `extra["db.kind"]=="index"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/fm/providers/test_db_provider.py`:

```python
def _root(p, url):
    return p.resolve_target(url, base=__import__("dunders.core.vfs", fromlist=["VfsPath"]).VfsPath.local("/tmp"))


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/fm/providers/test_db_provider.py -k scan -x`
Expected: FAIL (`AttributeError: 'DbProvider' object has no attribute 'scan'`).

- [ ] **Step 3: Implement `scan` / `is_dir` / `columns`**

Add these imports at the top of `db_provider.py`:

```python
from dunders.core.vfs.provider import ProviderColumn
from dunders.fm.file_entry import FileEntry
```

Add to `DbProvider`:

```python
    # -- VfsProvider listing ----------------------------------------------

    def scan(self, loc, *, show_hidden=False, include_parent=True) -> list[FileEntry]:
        conn = self.conn_for(loc.root)
        entries: list[FileEntry] = []
        if include_parent and loc.parts:
            entries.append(self._parent_entry(loc))
        if not loc.parts:
            return entries + self._scan_root(loc, conn)
        return entries + self._scan_table(loc, conn)

    def _scan_root(self, loc, conn) -> list[FileEntry]:
        out: list[FileEntry] = []
        for name in conn.tables():
            out.append(FileEntry(
                loc=loc.child(name), name=name, size=0, mtime=0.0, is_dir=True,
                extra={"db.kind": "table", "db.rows": str(conn.count(name)),
                       "db.cols": str(len(conn.columns(name)))},
            ))
        for idx, _table in conn.indexes():
            out.append(FileEntry(
                loc=loc.child(idx), name=idx, size=0, mtime=0.0, is_dir=False,
                extra={"db.kind": "index"},
            ))
        return out

    def _table_and_page(self, loc) -> tuple[str, int]:
        if len(loc.parts) >= 3 and loc.parts[1] == "_page":
            return loc.parts[0], max(1, int(loc.parts[2]))
        return loc.parts[0], 1

    def _scan_table(self, loc, conn) -> list[FileEntry]:
        table, page = self._table_and_page(loc)
        pk = conn.primary_key(table)
        offset = (page - 1) * _DB_PAGE
        rows = conn.fetch(table, offset=offset, limit=_DB_PAGE)
        out: list[FileEntry] = []
        root = loc.parent if (len(loc.parts) >= 3 and loc.parts[1] == "_page") else loc
        for rec in rows:
            key = rec.get(pk) if pk else None
            name = f"{key}.json" if key is not None else f"row{offset + len(out)}.json"
            size = len(da.record_to_json(rec))
            out.append(FileEntry(
                loc=root.child(name), name=name, size=size, mtime=0.0, is_dir=False,
                extra={"db.kind": "record"},
            ))
        total = conn.count(table)
        if offset + _DB_PAGE < total:
            nxt = root.child("_page").child(str(page + 1))
            out.append(FileEntry(loc=nxt, name=f"▼ more {total - offset - _DB_PAGE}…",
                                 size=0, mtime=0.0, is_dir=True, extra={"db.kind": "more"}))
        return out

    def _parent_entry(self, loc) -> FileEntry:
        parent = loc.parent
        # stepping out of a _page level lands back on the table, not the root
        if parent is not None and len(loc.parts) >= 3 and loc.parts[1] == "_page":
            parent = VfsPath(scheme="db", root=loc.root, parts=(loc.parts[0],))
        if parent is None:
            parent = VfsPath.local("/")
        return FileEntry(loc=parent, name="..", size=0, mtime=0.0, is_dir=True)

    def is_dir(self, loc) -> bool:
        if not loc.parts:
            return True
        last = loc.parts[-1]
        if last.endswith(".json"):
            return False
        if loc.parts[-1] == "_page" or (len(loc.parts) >= 2 and loc.parts[-2] == "_page"):
            return True
        conn = self.conn_for(loc.root)
        return loc.parts[0] in conn.tables() and len(loc.parts) == 1

    def columns(self, loc) -> list[ProviderColumn]:
        if loc.parts:
            return []
        return [
            ProviderColumn(key="db.rows", label="Rows", width=8,
                           value=lambda e: e.extra.get("db.rows", ""),
                           sort_key=lambda e: int(e.extra.get("db.rows") or 0)),
            ProviderColumn(key="db.cols", label="Cols", width=5,
                           value=lambda e: e.extra.get("db.cols", ""),
                           sort_key=lambda e: int(e.extra.get("db.cols") or 0)),
        ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/fm/providers/test_db_provider.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
ruff check dunders/fm/providers/db_provider.py
git add dunders/fm/providers/db_provider.py tests/fm/providers/test_db_provider.py
git commit -m "feat(db): scan tables/indexes/records (paged) + Rows/Cols columns"
```

---

### Task 4: `open_read` — record JSON, index DDL; `export_as_file` — whole table → JSONL

**Files:**
- Modify: `dunders/fm/providers/db_provider.py`
- Modify: `dunders/fm/vfs_engine.py` (consult `export_as_file` for a source)
- Test: `tests/fm/providers/test_db_provider.py`, `tests/fm/test_vfs_engine.py` (create if absent)

**Interfaces:**
- Produces:
  - `open_read(loc) -> BinaryIO` — record `.json` bytes, or an index's DDL bytes.
  - `export_as_file(loc) -> tuple[str, BinaryIO] | None` — for a table locator,
    returns `("<table>.jsonl", stream)`; `None` otherwise.
- Consumes (engine): `getattr(src_provider, "export_as_file", None)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/fm/providers/test_db_provider.py`:

```python
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


def test_export_as_file_record_is_none(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    rec = p.scan(root.child("users"), include_parent=False)[0]
    assert p.export_as_file(rec.loc) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/fm/providers/test_db_provider.py -k "open_read or export" -x`
Expected: FAIL (`AttributeError: ... 'open_read'`).

- [ ] **Step 3: Implement `open_read` + `export_as_file`**

Add `import io` and `import json` to `db_provider.py`, then add to `DbProvider`:

```python
    def _record_pk(self, loc) -> tuple[str, object]:
        """(table, pk_value) for a record locator '<table>/<pk>.json'."""
        table = loc.parts[0]
        stem = loc.parts[-1][: -len(".json")]
        conn = self.conn_for(loc.root)
        col = conn.primary_key(table)
        sample = conn.fetch(table, offset=0, limit=1)
        if sample and col in sample[0] and isinstance(sample[0][col], int):
            try:
                return table, int(stem)
            except ValueError:
                pass
        return table, stem

    def open_read(self, loc) -> "BinaryIO":
        conn = self.conn_for(loc.root)
        last = loc.parts[-1]
        if last.endswith(".json"):
            table, pk = self._record_pk(loc)
            rec = conn.get(table, pk)
            if rec is None:
                raise OSError(f"record {last} not found")
            return io.BytesIO(da.record_to_json(rec))
        # an index locator -> its DDL as text
        return io.BytesIO(conn.index_ddl(last).encode("utf-8"))

    def export_as_file(self, loc) -> "tuple[str, BinaryIO] | None":
        if len(loc.parts) != 1:
            return None
        table = loc.parts[0]
        conn = self.conn_for(loc.root)
        if table not in conn.tables():
            return None
        buf = io.BytesIO()
        offset = 0
        while True:
            rows = conn.fetch(table, offset=offset, limit=_DB_PAGE)
            if not rows:
                break
            for rec in rows:
                buf.write(json.dumps(rec, ensure_ascii=False, default=da._json_default).encode("utf-8"))
                buf.write(b"\n")
            offset += len(rows)
        buf.seek(0)
        return f"{table}.jsonl", buf
```

- [ ] **Step 4: Wire `export_as_file` into the transfer engine**

In `dunders/fm/vfs_engine.py`, inside `_generic_transfer`, replace the body of the `for src in sources:` loop's `try:` so it consults the hook **before** `_copy_tree`:

```python
        dest = dest_dir.child(single_rename or src.name)
        try:
            src_provider = registry.resolve(src)
            export = getattr(src_provider, "export_as_file", None)
            exported = export(src) if callable(export) else None
            if exported is not None:
                name, reader = exported
                dst = dest_dir.child(single_rename or name)
                dst_p = registry.resolve(dst)
                with reader, dst_p.open_write(dst) as writer:
                    while True:
                        if _cancelled(cancel_event):
                            raise _Cancelled
                        chunk = reader.read(_CHUNK)
                        if not chunk:
                            break
                        writer.write(chunk)
                        on_chunk(name, len(chunk))
                on_file_done(name)
                result.succeeded.append(dst.to_local() if dst.scheme == "file" else dst)
                continue
            _copy_tree(registry, src, dest, on_chunk=on_chunk,
                       on_file_done=on_file_done, cancel_event=cancel_event)
        except _Cancelled:
            result.cancelled = True
            return result
```

(Leave the existing `except OSError` / `result.succeeded.append` / move-delete tail below unchanged; the `continue` above skips straight to the next source after a successful export.)

- [ ] **Step 5: Write the engine integration test**

Create or append to `tests/fm/test_vfs_engine.py`:

```python
from dunders.core.vfs import VfsPath
from dunders.fm.providers.db_provider import DbProvider
from dunders.fm.vfs_engine import transfer
from dunders.fm.vfs_local import default_registry


def test_table_copies_out_as_single_jsonl(tmp_path):
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url); c.insert("users", {"name": "Ann"}); c.close()

    reg = default_registry()
    p: DbProvider = reg.for_scheme("db")
    root = p.resolve_target(url, base=VfsPath.local(str(tmp_path)))
    dest = VfsPath.local(str(tmp_path))

    res = transfer(reg, [root.child("users")], dest, mode="copy")
    assert not res.errors
    assert (tmp_path / "users.jsonl").exists()
    assert "Ann" in (tmp_path / "users.jsonl").read_text()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/fm/providers/test_db_provider.py tests/fm/test_vfs_engine.py -v`
Expected: all PASS.

- [ ] **Step 7: Lint and commit**

```bash
ruff check dunders/fm/providers/db_provider.py dunders/fm/vfs_engine.py
git add dunders/fm/providers/db_provider.py dunders/fm/vfs_engine.py tests/fm/providers/test_db_provider.py tests/fm/test_vfs_engine.py
git commit -m "feat(db): record JSON / index DDL reads + whole-table JSONL export hook"
```

---

### Task 5: `open_write` (insert/update/import), `delete`, `mkdir`, copy stubs

**Files:**
- Modify: `dunders/fm/providers/db_provider.py`
- Test: `tests/fm/providers/test_db_provider.py`

**Interfaces:**
- Produces:
  - `open_write(loc, *, size_hint=None, overwrite=False) -> BinaryIO` — a buffered
    writer flushed on close: a `.json` member → insert/upsert (or update when
    `overwrite=True`), a `.jsonl` member → row-by-row insert.
  - `delete(targets, *, on_progress=None, cancel_event=None) -> OpResult`
  - `mkdir(parent, name) -> OpResult` (no-op; tables are created by import)
  - `copy_within(...) -> None`, `move_within(...) -> None`

- [ ] **Step 1: Write the failing test**

Append to `tests/fm/providers/test_db_provider.py`:

```python
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


def test_delete_record(db_url):
    p = DbProvider()
    root = _root(p, db_url)
    rec = p.scan(root.child("users"), include_parent=False)[0]
    p.delete([rec.loc])
    assert p.conn_for(root.root).count("users") == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/fm/providers/test_db_provider.py -k "import or overwrite or delete" -x`
Expected: FAIL (`AttributeError: ... 'open_write'`).

- [ ] **Step 3: Implement writes/delete/mkdir/copy stubs**

Add `from dunders.fm.actions import OpError` to the imports, then add to `DbProvider`:

```python
    def open_write(self, loc, *, size_hint=None, overwrite=False) -> "BinaryIO":
        return _DbWriter(self, loc, overwrite=overwrite)

    def _commit_write(self, loc, data: bytes, overwrite: bool) -> None:
        conn = self.conn_for(loc.root)
        last = loc.parts[-1]
        if last.endswith(".jsonl"):
            table = last[: -len(".jsonl")]
            for line in data.splitlines():
                line = line.strip()
                if line:
                    conn.insert(table, da.json_to_record(line))
            return
        table = loc.parts[0]
        rec = da.json_to_record(data)
        if overwrite:
            _table, pk = self._record_pk(loc)
            conn.update(table, pk, rec)
        else:
            conn.insert(table, rec)

    def delete(self, targets, *, on_progress=None, cancel_event=None) -> OpResult:
        result = OpResult()
        by_table: dict[tuple[str, str], list] = {}
        for t in targets:
            if not t.parts or not t.parts[-1].endswith(".json"):
                continue
            table, pk = self._record_pk(t)
            by_table.setdefault((t.root, table), []).append(pk)
        for (root, table), pks in by_table.items():
            try:
                self.conn_for(root).delete(table, pks)
            except Exception as exc:
                result.errors.append(OpError(loc=targets[0], reason=str(exc)))
        if on_progress is not None:
            on_progress(len(targets), len(targets))
        return result

    def mkdir(self, parent, name) -> OpResult:
        return OpResult()  # tables are created implicitly by import

    def copy_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    on_status=None, cancel_event=None) -> OpResult | None:
        return None  # same-DB copy streams through the generic engine

    def move_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None) -> OpResult | None:
        return None
```

Add the writer class at module level (after `DbProvider`):

```python
class _DbWriter(io.BytesIO):
    """Buffers bytes and flushes them as an insert/update/import on close."""

    def __init__(self, provider: "DbProvider", loc, *, overwrite: bool) -> None:
        super().__init__()
        self._provider = provider
        self._loc = loc
        self._overwrite = overwrite
        self._flushed = False

    def close(self) -> None:
        if not self._flushed and not self.closed:
            self._flushed = True
            self._provider._commit_write(self._loc, self.getvalue(), self._overwrite)
        super().close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/fm/providers/test_db_provider.py -v`
Expected: all PASS.

- [ ] **Step 5: Add a cross-DB copy test, then commit**

Append:

```python
def test_record_copies_db_to_db(tmp_path):
    from dunders.fm.providers import db_access as da
    from dunders.fm.vfs_engine import transfer
    from dunders.fm.vfs_local import default_registry
    from dunders.core.vfs import VfsPath
    src_url = f"sqlite:///{tmp_path/'a.db'}"
    dst_url = f"sqlite:///{tmp_path/'b.db'}"
    a = da.DbConn.open(src_url); a.insert("t", {"name": "Z"}); a.close()
    b = da.DbConn.open(dst_url); b.insert("t", {"name": "seed"}); b.close()

    reg = default_registry()
    p = reg.for_scheme("db")
    src_root = p.resolve_target(src_url, base=VfsPath.local(str(tmp_path)))
    dst_root = p.resolve_target(dst_url, base=VfsPath.local(str(tmp_path)))
    rec = p.scan(src_root.child("t"), include_parent=False)[0]
    res = transfer(reg, [rec.loc], dst_root.child("t"), mode="copy")
    assert not res.errors
    assert p.conn_for(dst_root.root).count("t") == 2
```

```bash
ruff check dunders/fm/providers/db_provider.py
git add dunders/fm/providers/db_provider.py tests/fm/providers/test_db_provider.py
git commit -m "feat(db): record/table import + update + delete (copy between panels)"
```

---

### Task 6: SQL console (`DbConsoleContent`) + app wiring

**Files:**
- Create: `dunders/fm/db_console.py`
- Modify: `dunders/fm/providers/db_provider.py` (add `actions()`)
- Modify: `dunders/app.py` (handle the `provider.db.sql` command → mount the console)
- Test: `tests/fm/test_db_console.py`

**Interfaces:**
- Consumes: `DbProvider.conn_for(root)`, `da.DbConn.query`.
- Produces:
  - `class DbConsoleContent(WindowContent)` constructed as
    `DbConsoleContent(conn: da.DbConn, *, title_db: str)`, exposing
    `run_sql(sql: str) -> None` (fills the grid / status) and `last_columns`,
    `last_rows`, `last_status` attributes for tests.
  - `DbProvider.actions() -> list[ProviderAction]` with one action
    `id="db.sql"`, `label="SQL console"`, `hotkey="f2"`, `applies_to=lambda e: True`.

**Implementation notes:**
- The action's `run(locs)` cannot mount a window (providers are UI-agnostic).
  Follow the Docker pattern: the action exists so it appears in the provider
  menu and binds the F-key; `app.py` intercepts the dispatched command id
  `provider.db.sql` and mounts the console for the active panel's connection.
  Check how `provider.{id}` commands are dispatched (search `app.py` for
  `provider.` and `_run_provider_action`); add a branch that special-cases
  `db.sql` to call `_open_db_console(panel)` instead of running on a worker.
- For the result grid, reuse the simplest available widget. Display
  `last_columns` + `last_rows` via a Textual `DataTable` (already a dependency
  of Textual) inside the content; the lazy CSV grid can be swapped in later.
  Keep v1 capped at the first 1000 result rows (note the cap in the status line).

- [ ] **Step 1: Write the failing test**

Create `tests/fm/test_db_console.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/fm/test_db_console.py -x`
Expected: FAIL (`ModuleNotFoundError: ...db_console`).

- [ ] **Step 3: Implement `DbConsoleContent`**

Create `dunders/fm/db_console.py`. Model the class on an existing simple
`WindowContent` (open `dunders/fm/viewer.py` for the minimal shape: `compose`,
`get_commands`). Core logic (UI-independent, what the tests exercise):

```python
"""DbConsoleContent — a SQL editor over a lazy result grid for the db: dunder."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import DataTable, TextArea

from dunders.fm.providers import db_access as da
from dunders.windowing.content import WindowCommand, WindowContent

__all__ = ["DbConsoleContent"]

_RESULT_CAP = 1000


class DbConsoleContent(WindowContent):
    def __init__(self, conn: da.DbConn, *, title_db: str) -> None:
        super().__init__()
        self._conn = conn
        self._title_db = title_db
        self.last_columns: list[str] = []
        self.last_rows: list[dict] = []
        self.last_status: str = ""
        self._editor: TextArea | None = None
        self._table: DataTable | None = None

    def compose(self):
        self._editor = TextArea(language="sql", id="db-sql")
        self._table = DataTable(id="db-grid")
        yield Vertical(self._editor, self._table)

    def run_sql(self, sql: str) -> None:
        try:
            cols, rows, rowcount = self._conn.query(sql)
        except Exception as exc:  # noqa: BLE001 — surface DB errors in the status line
            self.last_status = f"Error: {exc}"
            self._render_grid([], [])
            return
        if cols:
            capped = rows[:_RESULT_CAP]
            self.last_columns, self.last_rows = cols, capped
            extra = "" if len(rows) <= _RESULT_CAP else f" (showing first {_RESULT_CAP})"
            self.last_status = f"{len(rows)} row(s){extra}"
            self._render_grid(cols, capped)
        else:
            self.last_columns, self.last_rows = [], []
            self.last_status = f"{rowcount} row(s) affected"
            self._render_grid([], [])

    def _render_grid(self, cols: list[str], rows: list[dict]) -> None:
        if self._table is None:  # headless (tests): grid not mounted
            return
        self._table.clear(columns=True)
        if cols:
            self._table.add_columns(*cols)
            for r in rows:
                self._table.add_row(*[str(r.get(c, "")) for c in cols])

    def get_commands(self) -> list[WindowCommand]:
        return [WindowCommand(id="db.console.run", label="Run SQL",
                              handler=self._run_current, hotkey="ctrl+enter")]

    def _run_current(self) -> None:
        if self._editor is not None:
            self.run_sql(self._editor.text)
```

Note: `_render_grid` guards on `self._table is None`, so `run_sql` works in the
headless tests (no Textual app mounted) and against a live DataTable.

- [ ] **Step 4: Add `actions()` to `DbProvider`**

Add `from dunders.core.vfs.provider import ProviderAction` to the imports and:

```python
    def actions(self) -> list:
        return [ProviderAction(id="db.sql", label="SQL console", icon="⌘",
                               hotkey="f2", applies_to=lambda e: True,
                               run=lambda locs: OpResult())]
```

- [ ] **Step 5: Wire the command in `app.py`**

Find where `provider.{a.id}` commands are dispatched/registered (search
`app.py` for `f"provider.{` and `_run_provider_action`). Where the dispatcher
maps `provider.db.sql` to its handler, special-case it to mount the console
instead of running the no-op on a worker. Add this method to `DundersApp`:

```python
    def _open_db_console(self) -> None:
        if self._has_active_modal() or self.desktop is None:
            return
        panel = self._active_panel()
        if panel is None or panel.cwd_loc.scheme != "db":
            self.notify("SQL console: focus a database panel first", severity="warning")
            return
        from dunders.fm.db_console import DbConsoleContent
        provider = self._vfs_registry.for_scheme("db")
        conn = provider.conn_for(panel.cwd_loc.root)
        self._editor_seq += 1
        content = DbConsoleContent(conn, title_db=panel.cwd_loc.root)
        self._mount_maximized_content(content, title="SQL console",
                                      win_id=f"db-console-{self._editor_seq}")
```

In the provider-command registration loop, register the `db.sql` handler to
call `self._open_db_console` (rather than `_run_provider_action`) — mirror how
`dunder.open.*` handlers are wired with a lambda.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/fm/test_db_console.py -v`
Expected: all PASS.

- [ ] **Step 7: Smoke-check the app boots and lint**

Run: `python -c "import dunders.app; import dunders.fm.db_console"` (no import errors).
Run: `ruff check dunders/fm/db_console.py dunders/fm/providers/db_provider.py dunders/app.py`

```bash
git add dunders/fm/db_console.py dunders/fm/providers/db_provider.py dunders/app.py tests/fm/test_db_console.py
git commit -m "feat(db): SQL console window + provider action wiring"
```

---

### Task 7: Full-suite regression, docs, manual smoke

**Files:**
- Modify: `CLAUDE.md` (architecture notes), `README` if present.

- [ ] **Step 1: Run the whole suite**

Run: `pytest -q`
Expected: PASS (no regressions in fm/windowing).

- [ ] **Step 2: Manual smoke (record once in the PR description)**

```bash
python -c "from dunders.fm.providers import db_access as da; c=da.DbConn.open('sqlite:////tmp/demo.db'); [c.insert('people', {'name': n, 'age': i}) for i, n in enumerate(['Ann','Bob','Cleo'])]; c.close()"
dunders
# In-app: menu "_" → Database → enter: sqlite:////tmp/demo.db
#   - panel shows table "people" (+ Rows/Cols); Enter → 3 records as <pk>.json
#   - F3 a record → JSON; F4 a record → edit, save → row updated
#   - copy a record to the other (file) panel → <pk>.json appears
#   - copy the "people" table to the file panel → people.jsonl appears
#   - F2 → SQL console → "SELECT * FROM people" → grid fills
```

- [ ] **Step 3: Update `CLAUDE.md`**

Add a bullet under the `dunders.fm` section describing the db dunder: provider
maps tables→dirs / records→`<pk>.json`, whole-table copy → `<table>.jsonl` via
the `export_as_file` engine hook, SQL console via F2, `dbset` behind the
`dunders[db]` extra, all DB access isolated in `db_access.py`.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(db): document the database dunder"
```

---

## Self-Review

**Spec coverage:**
- Entry via `_` menu → connection URL → tables/indexes: Task 2 (`resolve_target`, registration) + Task 3 (`scan` root).
- Records in panel, paged: Task 3.
- F3 view (record JSON / index DDL): Task 4 `open_read` (F3 already routes member reads through the provider).
- F4 edit record → update: Task 5 `open_write(overwrite=True)` via the existing `_open_member_edit` path (refinement over the spec's `JsonYamlTreeContent`; reuses more, no editor surgery — noted here intentionally).
- F8 delete record: Task 5 `delete`.
- Copy record→file (`<pk>.json`), file→table (insert), db→db: Tasks 4–5.
- Whole table → `<table>.jsonl`, jsonl→table import: Task 4 (`export_as_file` + engine hook) + Task 5 (jsonl import).
- SQL console (editor + grid, send/export results): Task 6 (results grid + run; "send to panel" virtual `_query/` location deferred — see below).
- Read/write + read-only: Task 1 (`ReadOnlyError`, `read_only`), Task 2 (`?readonly`).
- Packaging extra + import-guarded registration: Task 1 + Task 2.
- Tests mirroring `tests/fm/`: every task.

**Gap noted:** the spec's "send query result to panel as `db://root/_query/<id>/`" is **deferred** — the console shows results in a grid (the user's core ask: "поле для редактирования и запуска SQL"). Exporting a result set to the panel is a follow-up; it is not required for v1 and would otherwise inflate Task 6. Flag this to the user.

**Placeholder scan:** no TBD/TODO; every code step shows code; the only "find the call site" instructions (Task 6 Step 5) point at concrete, named symbols (`provider.{a.id}`, `_run_provider_action`, `dunder.open.*`) the implementer can grep.

**Type consistency:** `DbConn` method names match across Tasks 1/3/4/5/6; `record_to_json`/`json_to_record`/`_json_default` consistent; `export_as_file` signature consistent between provider (Task 4) and engine hook (Task 4 Step 4); `conn_for(root)` consistent across provider and app.
