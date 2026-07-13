"""db_access — the single seam to dbset / SQLAlchemy for the db: dunder.

Connection and mutating CRUD go through dbset (its JSON/type handling); read
metadata, paged reads, and raw SQL go through SQLAlchemy (stable across
versions). Nothing else in the codebase imports dbset or sqlalchemy.

Note: ``sqlite:///:memory:`` is intentionally unsupported — the ephemeral
store and the dual-handle (dbset + SQLAlchemy engine) would see two separate
in-memory databases. Tests use a temp-file SQLite path instead.

Note: on SQLite, dbset stores JSON/dict-valued columns as JSON *strings*, so
``fetch``/``get`` return those columns as strings (e.g. ``'{"role": "admin"}'``),
not as Python dicts. Callers recover the nested value with ``json.loads``.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import dbset
import sqlalchemy as sa


__all__ = [
    "DbConn", "ReadOnlyError", "record_to_json", "json_to_record",
    "single_table_target", "split_statements",
]


def split_statements(sql: str) -> list[str]:
    """Split ``sql`` into individual statements on top-level ``;``.

    String literals (``'…'`` / ``"…"``, doubled-quote escapes), line comments
    (``-- …``) and block comments (``/* … */``) are respected so a ``;`` inside
    them never splits. Returns the non-empty, stripped statements with their
    trailing ``;`` removed. A single statement (the common case) comes back as a
    one-element list."""
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None  # active string-literal quote char, else None
    while i < n:
        ch = sql[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:  # doubled '' / "" escape
                    buf.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        two = sql[i:i + 2]
        if two == "--":                                # line comment
            j = sql.find("\n", i)
            j = n if j == -1 else j
            buf.append(sql[i:j])
            i = j
            continue
        if two == "/*":                                # block comment
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(sql[i:j])
            i = j
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    stmts.append("".join(buf))
    return [s.strip() for s in stmts if s.strip()]


def _leading_keyword(sql: str) -> str:
    """First SQL keyword, lower-cased, skipping leading whitespace and
    ``--`` / ``/* */`` comments (``"" `` if none). Used to tell a row-returning
    statement (SELECT/WITH/VALUES) from a write/DDL *before* executing it."""
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
        elif sql[i:i + 2] == "--":
            j = sql.find("\n", i)
            i = n if j == -1 else j + 1
        elif sql[i:i + 2] == "/*":
            j = sql.find("*/", i)
            i = n if j == -1 else j + 2
        else:
            break
    m = re.match(r"[A-Za-z_]+", sql[i:])
    return m.group(0).lower() if m else ""


# Statements whose result is a cursor-able row set — the only ones a psycopg2
# server-side cursor (stream_results) can wrap. A DELETE/UPDATE/INSERT put after
# "DECLARE … CURSOR FOR" is a Postgres syntax error, so streaming must be gated
# on this even though the row count is unknown until execution.
_ROW_RETURNING = ("select", "with", "values", "table", "show", "explain")


# Stop the FROM-table capture at the next top-level clause keyword (or the end).
_FROM_RE = re.compile(
    r"\bfrom\b\s+(.+?)(?:\bwhere\b|\bgroup\b|\bhaving\b|\border\b|\blimit\b"
    r"|\bwindow\b|\bunion\b|$)",
    re.I | re.S,
)


def single_table_target(sql: str) -> str | None:
    """The bare name of the one table a plain ``SELECT`` reads from, else None.

    Used by the SQL console to decide whether a result cell can be written back
    with an ``UPDATE``: only a single-table ``SELECT`` (no JOIN/UNION/GROUP BY,
    no comma-joined tables, no sub-query in FROM) maps a row to one table. The
    name is returned unquoted and schema-stripped (``public.users`` → ``users``)
    so the caller can match it case-insensitively against ``tables()``. This is
    a deliberately conservative heuristic — when in doubt it returns None and
    the cell stays view-only."""
    s = sql.strip().rstrip(";").strip()
    if not re.match(r"select\b", s, re.I):
        return None
    if re.search(r"\bjoin\b|\bunion\b|\bgroup\s+by\b", s, re.I):
        return None
    m = _FROM_RE.search(s)
    if not m:
        return None
    expr = m.group(1).strip()
    if "," in expr or expr.startswith("("):  # multi-table or a sub-query
        return None
    parts = expr.split()
    if not parts:
        return None
    token = parts[0]                      # drop any "table alias" / "table AS alias"
    if token.startswith("("):
        return None
    name = token.split(".")[-1]           # schema.table -> table
    name = name.strip("\"`[]'")
    return name or None


class ReadOnlyError(Exception):
    """A mutation attempted on a read-only connection."""


def _driver_hint(url: str) -> str:
    """An actionable suffix for a missing-driver error, keyed on the URL scheme.

    SQLite needs no driver (stdlib); Postgres/MySQL need a DBAPI package that
    ships in the ``dunders[db]`` extra — name it so the toast tells the user
    exactly what to install."""
    head = url.split("://", 1)[0].split("+", 1)[0].lower()
    pkg = {
        "postgresql": "psycopg2-binary",
        "postgres": "psycopg2-binary",
        "mysql": "pymysql",
        "mariadb": "pymysql",
    }.get(head)
    if pkg:
        return (f" — the {head} driver is missing: pip install '{pkg}' "
                "(or reinstall: pip install 'dunders[db]')")
    return " — reinstall the database extra: pip install 'dunders[db]'"


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
        try:
            db = dbset.connect(url, read_only=read_only)
            engine = getattr(db, "engine", None) or getattr(db, "_engine", None)
            if engine is None:
                engine = sa.create_engine(url)
        except ModuleNotFoundError as exc:
            # A missing DBAPI driver (e.g. psycopg2) surfaces here as a bare
            # "No module named …"; append the exact install command.
            raise ModuleNotFoundError(f"{exc}{_driver_hint(url)}") from exc
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

    def select_all_sql(self, table: str) -> str:
        """``SELECT * FROM <table>`` with the identifier dialect-quoted — the
        F3/View prefill for a table in the SQL console."""
        prep = self._engine.dialect.identifier_preparer
        return f"SELECT * FROM {prep.quote(table)}"

    def create_table_ddl(self, table: str) -> str:
        """The dialect-rendered DDL to recreate ``table`` — the F4/Edit prefill.

        Reflects the live schema and renders the ``CREATE TABLE`` (columns + PK/
        FK/unique/check inline) followed by a ``CREATE INDEX`` per secondary
        index (sorted by name, ``UNIQUE`` preserved), so the DDL fully describes
        how to build the table. Rendered via SQLAlchemy so it matches the
        connected dialect."""
        from sqlalchemy.schema import CreateIndex, CreateTable
        tbl = sa.Table(table, sa.MetaData(), autoload_with=self._engine)
        stmts = [str(CreateTable(tbl).compile(self._engine)).strip() + ";"]
        for idx in sorted(tbl.indexes, key=lambda i: i.name or ""):
            stmts.append(str(CreateIndex(idx).compile(self._engine)).strip() + ";")
        return "\n\n".join(stmts)

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

    def get(self, table: str, pk_value: Any) -> dict | None:
        order = self._order_col(table)
        cols = "rowid AS rowid, *" if order == "rowid" else "*"
        sql = sa.text(f'SELECT {cols} FROM "{table}" WHERE "{order}" = :v')
        with self._engine.connect() as cx:
            row = cx.execute(sql, {"v": pk_value}).mappings().first()
        return dict(row) if row is not None else None

    def insert(self, table: str, rec: dict) -> object:
        self._guard()
        return self._db[table].insert(dict(rec))

    def insert_many(self, table: str, recs: list) -> None:
        """Batch-insert records in one round trip. Used by the streaming .jsonl
        importer so a large import doesn't degrade into a million single-row
        INSERTs. dbset auto-creates the table (autoincrement ``id`` PK) on the
        first batch, exactly like ``insert``."""
        self._guard()
        rows = [dict(r) for r in recs]
        if rows:
            self._db[table].insert_many(rows)

    def ensure_columns(self, table: str, rec: dict) -> None:
        """Add any columns in ``rec`` that don't yet exist in ``table``.

        Uses ``ALTER TABLE … ADD COLUMN`` (SQLite-safe; NULL-padded for existing
        rows). No-op for columns that already exist.
        """
        self._guard()
        existing = set(self.columns(table))
        prep = self._engine.dialect.identifier_preparer
        with self._engine.begin() as cx:
            for col in rec:
                if col not in existing and col != "rowid":
                    # Quote both identifiers through the dialect preparer so a
                    # column name from untrusted JSON cannot break out of the
                    # identifier and inject a second statement.
                    ddl = f"ALTER TABLE {prep.quote(table)} ADD COLUMN {prep.quote(col)}"
                    cx.execute(sa.text(ddl))

    def update(self, table: str, pk_value: Any, rec: dict) -> int:
        self._guard()
        pk = self.primary_key(table)
        if pk is None:
            raise ValueError(
                f"table {table!r} has no single-column primary key; cannot update by primary key"
            )
        values = {k: v for k, v in rec.items() if k != "rowid"}
        return int(self._db[table].update(values, **{pk: pk_value}) or 0)

    def drop_table(self, table: str) -> None:
        """``DROP TABLE`` — irreversible. The identifier is quoted through the
        dialect preparer so a table name can't break out and inject DDL."""
        self._guard()
        prep = self._engine.dialect.identifier_preparer
        with self._engine.begin() as cx:
            cx.execute(sa.text(f"DROP TABLE {prep.quote(table)}"))

    def delete(self, table: str, pk_values: list) -> int:
        self._guard()
        pk = self.primary_key(table)
        if pk is None:
            raise ValueError(
                f"table {table!r} has no single-column primary key; cannot delete by primary key"
            )
        n = 0
        for v in pk_values:
            n += int(self._db[table].delete(**{pk: v}) or 0)
        return n

    def query(
        self, sql: str, *, limit: int | None = None
    ) -> tuple[list[str], list[dict], int, bool]:
        """Run raw ``sql``. Returns ``(columns, rows, rowcount, truncated)``.

        For a SELECT, ``limit`` caps how many rows are *fetched* (not just
        displayed): the result is streamed (``stream_results``) and at most
        ``limit + 1`` rows are pulled, so a ``SELECT * FROM huge_table`` never
        materialises the whole table into memory. ``truncated`` is True when
        more rows exist beyond ``limit``. Non-row statements return ``rowcount``
        and ``truncated=False``.

        Streaming is enabled ONLY for a row-returning leading keyword
        (:data:`_ROW_RETURNING`). On Postgres, ``stream_results`` opens a
        server-side cursor via ``DECLARE … CURSOR FOR <sql>``; a DELETE/UPDATE/
        INSERT there is a syntax error (``at or near "DELETE"``). A write with a
        ``RETURNING`` clause still returns rows — handled by the non-streaming
        cap below — so no result is lost."""
        stream = limit is not None and _leading_keyword(sql) in _ROW_RETURNING
        with self._engine.begin() as cx:
            if stream:
                cx = cx.execution_options(stream_results=True, max_row_buffer=limit + 1)
            result = cx.execute(sa.text(sql))
            if result.returns_rows:
                cols = list(result.keys())
                if limit is None:
                    rows = [dict(m) for m in result.mappings()]
                    return cols, rows, len(rows), False
                fetched = result.mappings().fetchmany(limit + 1)
                truncated = len(fetched) > limit
                rows = [dict(m) for m in fetched[:limit]]
                # Stop the (possibly server-side) cursor without draining the rest.
                result.close()
                return cols, rows, len(rows), truncated
            return [], [], int(result.rowcount or 0), False

    def query_page(
        self, sql: str, *, limit: int, offset: int
    ) -> tuple[list[str], list[dict], bool]:
        """Run a row-returning ``sql`` one page at a time. Returns
        ``(columns, rows, has_next)``.

        The statement is wrapped in a sub-query so LIMIT/OFFSET apply to *its*
        result regardless of what it is (joins, ORDER BY, an inner LIMIT, …):
        ``SELECT * FROM (<sql>) AS _dunders_pg LIMIT :l OFFSET :o``. ``limit + 1``
        rows are fetched so the caller learns whether a further page exists
        without a second COUNT round trip; the extra row is dropped. The wrapper
        works on SQLite/Postgres/MySQL (all require the sub-query alias and
        accept ``LIMIT n OFFSET m``). A statement that can't be wrapped (e.g.
        duplicate output column names) raises — the console falls back to an
        un-paged run."""
        inner = sql.strip().rstrip(";").strip()
        wrapped = (
            f"SELECT * FROM (\n{inner}\n) AS _dunders_pg "
            "LIMIT :_dunders_l OFFSET :_dunders_o"
        )
        with self._engine.begin() as cx:
            result = cx.execute(
                sa.text(wrapped), {"_dunders_l": limit + 1, "_dunders_o": offset})
            cols = list(result.keys())
            fetched = result.mappings().fetchmany(limit + 1)
            result.close()
        has_next = len(fetched) > limit
        rows = [dict(m) for m in fetched[:limit]]
        return cols, rows, has_next

    def execute_script(
        self, statements: list[str], *, limit: int | None = None
    ) -> tuple[list[str], list[dict], int, bool]:
        """Run ``statements`` in order inside a single transaction; return the
        LAST statement's result as ``(columns, rows, rowcount, truncated)`` —
        the same shape as :meth:`query`.

        A SQL console lets the user run several ``;``-separated statements at
        once (e.g. a ``SELECT`` to eyeball a row followed by a ``DELETE``). The
        DB-API can only ``execute`` one statement at a time, so we split (via
        :func:`split_statements`) and run them sequentially, atomically. Only the
        final row-returning statement is materialised (capped at ``limit`` like
        :meth:`query`); earlier results are drained and discarded."""
        last: tuple[list[str], list[dict], int, bool] = ([], [], 0, False)
        with self._engine.begin() as cx:
            for idx, stmt in enumerate(statements):
                is_last = idx == len(statements) - 1
                run = cx
                # Stream (server-side cursor) only for a row-returning last
                # statement — a DELETE/UPDATE after "DECLARE … CURSOR FOR" is a
                # Postgres syntax error. See :meth:`query`.
                if (is_last and limit is not None
                        and _leading_keyword(stmt) in _ROW_RETURNING):
                    run = cx.execution_options(
                        stream_results=True, max_row_buffer=limit + 1)
                result = run.execute(sa.text(stmt))
                if not result.returns_rows:
                    if is_last:
                        last = [], [], int(result.rowcount or 0), False
                    continue
                if not is_last:
                    result.close()   # drain a non-final SELECT so the next runs
                    continue
                cols = list(result.keys())
                if limit is None:
                    rows = [dict(m) for m in result.mappings()]
                    last = cols, rows, len(rows), False
                else:
                    fetched = result.mappings().fetchmany(limit + 1)
                    truncated = len(fetched) > limit
                    rows = [dict(m) for m in fetched[:limit]]
                    result.close()
                    last = cols, rows, len(rows), truncated
        return last

    def close(self) -> None:
        try:
            close = getattr(self._db, "close", None)
            if callable(close):
                close()
        finally:
            self._engine.dispose()
