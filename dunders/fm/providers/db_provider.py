"""DbProvider — browse and edit a SQL database as a panel (the db: dunder).

Maps tables -> directories and records -> files so the universal panel and the
generic transfer engine work unchanged. All dbset/SQLAlchemy access goes
through dunders.fm.providers.db_access. Connections are cached per root (the
normalized URL without password), mirroring SftpProvider._creds.
"""

from __future__ import annotations

import io
import json
import os
import threading

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import ProviderAction, ProviderColumn
from dunders.fm.actions import OpError, OpResult
from dunders.fm.file_entry import FileEntry
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

    # Openable-file capability: F3/Enter on a local file whose first bytes match
    # this signature opens it in this dunder (see DundersApp._dunder_for_local_file).
    # Magic is authoritative for SQLite; .db/.sqlite extensions are ambiguous
    # (Berkeley DB etc.), so no extension matching.
    view_magic = (b"SQLite format 3\x00",)
    view_extensions: tuple[str, ...] = ()

    def __init__(self) -> None:
        self._conns: dict[str, da.DbConn] = {}
        self._urls: dict[str, str] = {}     # root -> full url (with password)
        self._lock = threading.Lock()

    # -- connection cache --------------------------------------------------

    def conn_for(self, root: str) -> da.DbConn:
        with self._lock:
            conn = self._conns.get(root)
            if conn is None:
                try:
                    conn = da.DbConn.open(self._urls.get(root, root), read_only=self._read_only(root))
                except Exception as exc:
                    raise OSError(f"Cannot reconnect to {root}: {exc}") from exc
                self._conns[root] = conn
            return conn

    @staticmethod
    def _read_only(root: str) -> bool:
        return root.endswith("?readonly")

    def connection_password(self, root: str) -> str | None:
        """The password used to connect ``root``, or None — for bookmark
        persistence. The password lives only here (it is stripped from the
        locator/title/clipboard), so the bookmark layer asks the provider for
        it; mirrors ``SftpProvider.connection_password``."""
        url = self._urls.get(root)
        if not url or "://" not in url:
            return None
        rest = url.split("://", 1)[1]
        if "@" not in rest:
            return None
        cred = rest.rsplit("@", 1)[0]
        _user, sep, pw = cred.partition(":")
        return pw if (sep and pw) else None

    # -- prefix target (db:<url> opens a connection) -----------------------

    def spec_from_path(self, path: str) -> str:
        """Open-spec for a local SQLite file: an absolute sqlite:/// URL.

        os.path.abspath yields a leading-slash path, so the f-string produces
        the 4-slash SQLAlchemy absolute form (sqlite:////abs/path)."""
        return f"sqlite:///{os.path.abspath(path)}"

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
        # Records and the next "▼ more" entry hang off the BASE table loc
        # (<table>,), never off the current page loc. loc.parent only strips one
        # level, so on page ≥2 (<table>/_page/N) it would leave a stray _page
        # segment and the next page's loc would be <table>/_page/_page/N+1 —
        # _table_and_page then does int("_page") and raises. Always rebuild the
        # table root from parts[0].
        root = VfsPath(scheme="db", root=loc.root, parts=(loc.parts[0],))
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

    def actions(self) -> list:
        # SQL console is a PANEL-level action (one console for the connection),
        # not a per-record verb like Docker start/stop — so applies_to is False:
        # it must NOT paint a clickable ⌘ on every row (which also crowded out
        # the Cols column). It stays reachable via the Database menu and Alt+S
        # (both ignore applies_to).
        # Both are PANEL-level (applies_to False → no per-row icon); the app
        # special-cases their ids in _run_provider_action. db.history opens the
        # console and pops its history picker.
        return [
            ProviderAction(id="db.sql", label="SQL console", icon="⌘",
                           hotkey="alt+s", applies_to=lambda e: False,
                           run=lambda locs: OpResult()),
            ProviderAction(id="db.history", label="SQL history", icon="🕘",
                           applies_to=lambda e: False,
                           run=lambda locs: OpResult()),
        ]

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

    # -- read access -------------------------------------------------------

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

    def open_read(self, loc) -> "io.BinaryIO":
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

    def export_as_file(self, loc) -> "tuple[str, io.BinaryIO] | None":
        if len(loc.parts) != 1:
            return None
        table = loc.parts[0]
        conn = self.conn_for(loc.root)
        if table not in conn.tables():
            return None
        # Stream the table lazily (one page per read) rather than materialising
        # the whole thing into a BytesIO first: a large table would otherwise
        # freeze the copy worker (and blow up memory) while the progress bar sat
        # at 0%, then jump straight to 100% once the in-memory buffer streamed
        # out instantly. The lazy reader lets the engine's per-chunk progress
        # advance as records are serialised.
        return f"{table}.jsonl", _TableExportReader(conn, table)

    def export_size_hint(self, loc) -> "int | None":
        """A cheap byte estimate for the .jsonl export of a table, so the copy
        engine gets a progress denominator WITHOUT walking every page (which
        ``_measure`` would otherwise do, stalling the bar at 0%). Returns None
        for non-table locs so the engine falls back to its normal measuring."""
        if len(loc.parts) != 1:
            return None
        table = loc.parts[0]
        conn = self.conn_for(loc.root)
        if table not in conn.tables():
            return None
        n = conn.count(table)
        if n <= 0:
            return 0
        sample = conn.fetch(table, offset=0, limit=min(n, 100))
        if not sample:
            return 0
        avg = sum(
            len(json.dumps(r, ensure_ascii=False, default=da._json_default)) + 1
            for r in sample
        ) / len(sample)
        return int(avg * n)

    # -- write access -------------------------------------------------------

    def open_write(self, loc, *, size_hint=None, overwrite=False) -> "io.BinaryIO":
        return _DbWriter(self, loc, overwrite=overwrite)

    def _commit_write(self, loc, data: bytes, overwrite: bool) -> None:
        conn = self.conn_for(loc.root)
        last = loc.parts[-1]
        if last.endswith(".jsonl"):
            importer = _JsonlImporter(conn, last[: -len(".jsonl")])
            for line in data.splitlines():
                importer.add(line)
            importer.flush()
            return
        table = loc.parts[0]
        rec = da.json_to_record(data)
        if overwrite:
            _table, pk = self._record_pk(loc)
            # Add any new columns from the incoming record before updating.
            # NOTE: editing a record with a new field permanently widens the
            # table schema (ALTER TABLE ADD COLUMN, NULL for existing rows).
            rec = {k: v for k, v in rec.items() if k != "rowid"}
            conn.ensure_columns(table, rec)
            conn.update(table, pk, rec)
        else:
            # Strip the PK so the DB can assign a new one (avoids UNIQUE constraint
            # when copying a record between databases that already have the same PK).
            pk_col = conn.primary_key(table) if table in conn.tables() else None
            if pk_col and pk_col != "rowid":
                rec = {k: v for k, v in rec.items() if k != pk_col}
            conn.insert(table, rec)

    def delete(self, targets, *, on_progress=None, cancel_event=None) -> OpResult:
        result = OpResult()
        by_table: dict[tuple[str, str], list] = {}
        drop: list[tuple[str, str, object]] = []
        for t in targets:
            if not t.parts:
                continue
            if t.parts[-1].endswith(".json"):
                table, pk = self._record_pk(t)
                by_table.setdefault((t.root, table), []).append(pk)
            elif len(t.parts) == 1 and t.parts[0] in self.conn_for(t.root).tables():
                # A single-part loc that names a real table -> DROP TABLE.
                # (Indexes and _page pseudo-entries are non-tables and stay no-ops.)
                drop.append((t.root, t.parts[0], t))
        for (root, table), pks in by_table.items():
            try:
                self.conn_for(root).delete(table, pks)
            except Exception as exc:
                result.errors.append(OpError(loc=targets[0], reason=str(exc)))
        for root, table, loc in drop:
            try:
                self.conn_for(root).drop_table(table)
            except Exception as exc:
                result.errors.append(OpError(loc=loc, reason=str(exc)))
        if on_progress is not None:
            on_progress(len(targets), len(targets))
        return result

    def mkdir(self, parent, name) -> OpResult:
        return OpResult()  # tables are created implicitly by import

    def copy_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    on_status=None, cancel_event=None) -> "OpResult | None":
        return None  # same-DB copy streams through the generic engine

    def move_within(self, sources, dest, *, rename_to=None, on_progress=None,
                    cancel_event=None) -> "OpResult | None":
        return None


class _TableExportReader:
    """A read-only byte stream that serialises a table to JSONL on demand.

    Each ``read`` pulls only as many pages (``_DB_PAGE`` rows) as needed to
    satisfy the request, so a multi-GB table never lands in memory at once and
    the copy engine's per-chunk progress advances steadily. Supports the
    context-manager + ``read(size)`` shape the transfer engine relies on.
    """

    def __init__(self, conn: da.DbConn, table: str, page: int = _DB_PAGE) -> None:
        self._conn = conn
        self._table = table
        self._page = page
        self._offset = 0
        self._buf = bytearray()
        self._eof = False

    def _fill(self) -> None:
        rows = self._conn.fetch(self._table, offset=self._offset, limit=self._page)
        if not rows:
            self._eof = True
            return
        self._offset += len(rows)
        for rec in rows:
            self._buf += json.dumps(rec, ensure_ascii=False, default=da._json_default).encode("utf-8")
            self._buf += b"\n"

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            while not self._eof:
                self._fill()
            out = bytes(self._buf)
            self._buf.clear()
            return out
        while len(self._buf) < size and not self._eof:
            self._fill()
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def close(self) -> None:
        self._buf = bytearray()
        self._eof = True

    def __enter__(self) -> "_TableExportReader":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


class _JsonlImporter:
    """Inserts JSONL records in batches, deciding PK handling ONCE up front.

    The strip decision must be the same for every row: re-checking
    ``table in conn.tables()`` per line let the first (table-creating) row keep
    its explicit PK while later rows had theirs stripped, so on Postgres the
    freshly-created serial sequence stayed at its start value (an explicit-id
    insert does not advance it) and the next sequence-assigned id collided
    (UniqueViolation). For a not-yet-existing table dbset auto-creates an
    autoincrement ``id`` PK, so stripping ``id`` from every row lets the DB
    assign all PKs and the sequence advances normally. Records accumulate into
    ``_BATCH``-sized groups inserted via one ``insert_many`` round trip.
    """

    _BATCH = 500

    def __init__(self, conn: da.DbConn, table: str) -> None:
        self._conn = conn
        self._table = table
        self._pk_col = conn.primary_key(table) if table in conn.tables() else "id"
        self._strip = bool(self._pk_col and self._pk_col != "rowid")
        self._batch: list[dict] = []

    def add(self, line: bytes) -> None:
        line = line.strip()
        if not line:
            return
        rec = da.json_to_record(line)
        if self._strip:
            rec = {k: v for k, v in rec.items() if k != self._pk_col}
        self._batch.append(rec)
        if len(self._batch) >= self._BATCH:
            self.flush()

    def flush(self) -> None:
        if self._batch:
            self._conn.insert_many(self._table, self._batch)
            self._batch = []


class _DbWriter:
    """Destination stream for a db copy/import.

    For a ``.jsonl`` import it parses and inserts records **as bytes stream in**
    (one batch per accumulated group), so a multi-GB import never buffers in
    memory and the copy bar tracks real insert progress — each ``write`` blocks
    until its complete lines are inserted, so the engine's per-chunk progress
    advances in step with the work. (The old writer buffered the whole file,
    drove the bar to 100% as it filled the buffer, then did every insert
    row-by-row in ``close`` — a long freeze at 100%.) Single ``.json`` records
    and overwrites are tiny, so those still buffer and commit on close.
    """

    def __init__(self, provider: "DbProvider", loc, *, overwrite: bool) -> None:
        self._provider = provider
        self._loc = loc
        self._overwrite = overwrite
        last = loc.parts[-1] if loc.parts else ""
        self._streaming = (not overwrite) and last.endswith(".jsonl")
        self._tail = bytearray()       # incomplete trailing line (streaming)
        self._buf = bytearray()        # whole payload (non-streaming)
        self._importer: "_JsonlImporter | None" = None
        self._closed = False

    def _imp(self) -> "_JsonlImporter":
        if self._importer is None:
            conn = self._provider.conn_for(self._loc.root)
            table = self._loc.parts[-1][: -len(".jsonl")]
            self._importer = _JsonlImporter(conn, table)
        return self._importer

    def write(self, b) -> int:
        if self._streaming:
            self._tail += b
            nl = self._tail.rfind(b"\n")
            if nl >= 0:
                complete = bytes(self._tail[: nl + 1])
                del self._tail[: nl + 1]
                imp = self._imp()
                for line in complete.split(b"\n"):
                    imp.add(line)
        else:
            self._buf += b
        return len(b)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._streaming:
            imp = self._imp()
            for line in bytes(self._tail).split(b"\n"):
                imp.add(line)
            imp.flush()
        else:
            self._provider._commit_write(self._loc, bytes(self._buf), self._overwrite)

    def __enter__(self) -> "_DbWriter":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False
