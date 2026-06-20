# Database dunder — design

**Date:** 2026-06-20
**Status:** Approved (brainstorm)
**Topic:** A "Database" dunder for browsing and editing SQL databases via `dbset`,
mounted as a VFS provider plus a dedicated SQL console surface.

## Goal

Add a new dunder that opens a SQL database in a panel: pick **Database** from the
`_` dunder menu, enter a connection URL, and browse tables and indexes in the
panel. Tables and records can be copied panel-to-panel (export/import, db↔db).
A separate SQL console window edits and runs raw SQL with a lazy result grid.

Backed by [`dbset`](https://github.com/tumikosha/dbset) — a SQLAlchemy 2.x
wrapper (SQLite / PostgreSQL / MySQL) with a dataset-style API:
`connect(url, read_only=…)`, `db['table'].find()/insert()/update()/delete()/upsert()`,
raw SQL via `db.query(stmt)`. We use the **sync** API from worker threads.

## Approach

Chosen: **VFS provider + dedicated SQL surface** (hybrid).

- A `DbProvider` (`scheme="db"`) makes a connection browsable as a panel
  (tables → records) and reuses the existing `transfer()` engine for
  copy-between-panels and the existing dialogs for confirm/edit.
- A dedicated `DbConsoleContent` window hosts a multi-line SQL editor plus a
  lazy result grid — proper SQL editing that a cramped dialog can't give.

Rejected: pure-VFS (SQL editor too cramped, weak result grid) and a standalone
DB-browser window (drops copy-between-panels — an explicit requirement — and is
inconsistent with the rest of dunders).

## Components

### 1. Connection & entry — `dunders/fm/providers/db_provider.py`

`DbProvider` conforms to `VfsProvider` (structural protocol, like the other
providers). Registered in `default_registry` **only when `dbset` imports**, so
`db:` simply doesn't appear without the extra (mirrors paramiko/sftp).

- `scheme = "db"`, `display_name = "Database"`,
  `capabilities = {"read", "write", "slow"}` (`slow` → connect runs on a worker
  via the existing `_do_open_dunder` path so the UI never freezes).
- `open_placeholder = "connection URL: sqlite:///file.db, postgresql://user@host/db, mysql://…"`.
- `resolve_target(spec, *, base, password) -> VfsPath | None`: normalize the URL,
  `dbset.connect(url, read_only=…)`, cache the live connection in the provider
  keyed by a `root` id (the normalized URL **without** the password), and return
  `VfsPath(scheme="db", root=root, parts=())`. Connection reuse and the per-root
  lock mirror `SftpProvider._creds` / `_sftp` / `_lock_for`.
- Connection failures raise `OSError` with a human message (mirrors
  `sftp_provider._connect_error`); the app surfaces it as a toast.
- The address goes in the dunder dialog; the password, if prompted, stays out of
  the locator and lives in the provider (same split as sftp).

### 2. Listing — `scan(loc)`

- **Root** (`parts=()`): one entry per **table** (`is_dir=True`) and per **index**
  (`is_dir=False`, read-only). Discovery via SQLAlchemy inspector
  (`inspect(engine).get_table_names()` / `get_indexes(...)`). `ProviderColumns`
  contributes root columns: **Rows** (approximate count) and **Cols**.
- **Inside a table** (`parts=("users",)`): one `FileEntry` per record,
  `name="<pk>"`, `is_dir=False`. Key/preview fields go in `FileEntry.extra` and
  render through table-scoped `ProviderColumns`. F3 on an index/table shows its
  DDL/schema.
- **Pagination:** `scan` returns a list, so the listing is capped at
  `_DB_PAGE = 1000` rows ordered by primary key. A synthetic trailing entry
  ("▼ more N…") loads the next page (addressed via an internal offset/page in the
  locator, e.g. `parts=("users", "_page", "2")`). Records on a page stay
  selectable and copyable.
- Tables without a usable single-column PK: fall back to `rowid` (SQLite) or a
  synthetic stable ordering; records are still listable and exportable, and
  per-record copy/edit is best-effort (update/delete use whatever key columns
  exist; if none, those ops are disabled with a toast rather than guessing).

### 3. View & edit records

- **F3** (view) on a record → its JSON, read-only, via the existing viewer; on a
  table/index → DDL/schema text.
- **F4** (edit) on a record → the existing `JsonYamlTreeContent` over the record
  dict; saving calls `table.update(values, <pk-filter>)`. dbset preserves types
  (JSON/JSONB, nested structures).
- **F8** (delete) on record(s) → `ConfirmDialog` → `table.delete(...)`.
- All mutations run on a worker thread and marshal back via `call_from_thread`
  (the `_run_copy_move` / `_run_delete` pattern). A read-only connection raises
  `dbset.ReadOnlyError`, caught and shown as a toast.

### 4. Copy between panels — via `transfer()`

Model: **table = directory**, **record = file `<pk>.json`**. This makes the
generic transfer engine (`scan` / `is_dir` / `open_read` / `open_write` / `mkdir`)
work without the engine knowing anything about databases.

- **record → file panel:** `open_read` yields the record's JSON bytes →
  `<pk>.json` lands in the destination.
- **`.json` file → table:** `open_write` parses the JSON object → `insert`
  (or `upsert` when a key matches).
- **record → another table / another DB:** generic cross-provider transfer
  (JSON read → insert write); cross-database works out of the box.

**Whole-table copy is special-cased in the provider** so a table copied to a file
panel becomes **one streamed `<table>.jsonl`** (one record per line), not a folder
of `<pk>.json` files:

- The provider overrides the table-level copy/stream path so a table source
  reads as a single JSONL byte stream (lazy, row-by-row).
- Importing a `.jsonl` file into a table inserts row-by-row.

So: **`<pk>.json` = single record; `<table>.jsonl` = whole table.** Per-record
operations and whole-table export each get the natural format.

### 5. SQL console — `dunders/fm/db_console.py` (`DbConsoleContent`)

A `WindowContent` (like the viewers), opened by a `ProviderAction` "SQL console"
(an F-key, e.g. F2 / Ctrl+Enter) on a db panel, bound to that panel's connection
`root`.

- **Layout:** top — a multi-line `EditorWidget` for SQL; bottom — a lazy result
  grid reusing the CSV viewer's grid / `row_source` machinery.
- **Run** (Ctrl+Enter): `db.query(text(sql))` on a worker. SELECT → rows feed the
  grid; non-SELECT → rowcount + status message.
- **Send result to panel:** the result is exposed as a virtual location
  `db://<root>/_query/<id>/` the panel can browse, and therefore copy to a file
  panel as `.jsonl` through the same transfer path.

### 6. Read/write & safety

- Default **read-write** (editing is a goal). Read-only via `?readonly` in the URL
  or a menu item → `dbset.connect(read_only=True)`.
- Destructive ops always confirm via the existing `ConfirmDialog`.
- Everything slow or mutating runs on a worker and marshals UI updates via
  `call_from_thread`.

## Data flow

```
_ menu "Database" → InputDialog(connection URL)
  → DbProvider.resolve_target() [worker: dbset.connect] → VfsPath(db://root/)
  → panel.scan(root)  → tables + indexes
  → Enter table       → panel.scan(db://root/users) → records (paged)
      F3 view JSON · F4 edit (JsonYamlTreeContent → update) · F8 delete (confirm)
      copy record → file panel:  open_read → <pk>.json
      copy <pk>.json → table:    open_write → insert/upsert
      copy table  → file panel:  provider special-case → <table>.jsonl (streamed)
  → SQL console action → DbConsoleContent(root): EditorWidget + lazy grid
      Ctrl+Enter → db.query(text(sql)) [worker] → grid / rowcount
      send to panel → db://root/_query/<id>/ → copy out as .jsonl
```

## Error handling

- Connect failure → `OSError` with a specific reason → toast (sftp pattern).
- Read-only violation → `dbset.ReadOnlyError` caught → toast.
- Bad SQL → exception caught on the worker → shown in the console status line.
- Malformed `.json`/`.jsonl` on import → row-level error recorded in `OpResult`
  (the copy continues, like the zip read-only "copied but not removed" case).
- Missing/unsuitable PK → update/delete disabled for that table with a toast;
  listing/export still work.

## Testing (mirrors `tests/fm/`)

- `tests/fm/providers/test_db_provider.py` — pure logic on in-memory SQLite:
  `scan` (tables/indexes/records), record ↔ JSON serialization, pagination,
  copy semantics (record→file, `.json`→table, db→db, table→`.jsonl`,
  `.jsonl`→table), read-only blocking.
- `tests/fm/test_db_console.py` — async smoke: run a SELECT, render the grid,
  non-SELECT rowcount.

## Packaging

- `dbset` (and DB drivers) ship as an opt-in extra `dunders[db]` (like
  `dunders[office]`). `DbProvider` registers only when `dbset` imports.

## Out of scope (v1)

- Schema editing (create/alter/drop columns) beyond what copy-import implies.
- Vector / hybrid search UI (dbset supports it; not surfaced yet).
- Transactions UI, multi-statement scripts, query history persistence.
- known_hosts-style trust prompts for DB TLS.
```
