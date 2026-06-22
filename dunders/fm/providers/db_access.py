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
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import dbset
import sqlalchemy as sa


__all__ = ["DbConn", "ReadOnlyError", "record_to_json", "json_to_record"]


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

        For a row-returning statement, ``limit`` caps how many rows are
        *fetched* (not just displayed): the result is streamed
        (``stream_results``) and at most ``limit + 1`` rows are pulled, so a
        ``SELECT * FROM huge_table`` never materialises the whole table into
        memory. ``truncated`` is True when more rows exist beyond ``limit``.
        Non-row statements return ``rowcount`` and ``truncated=False``."""
        with self._engine.begin() as cx:
            if limit is not None:
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
                # Stop the server-side cursor cleanly without draining the rest.
                result.close()
                return cols, rows, len(rows), truncated
            return [], [], int(result.rowcount or 0), False

    def close(self) -> None:
        try:
            close = getattr(self._db, "close", None)
            if callable(close):
                close()
        finally:
            self._engine.dispose()
