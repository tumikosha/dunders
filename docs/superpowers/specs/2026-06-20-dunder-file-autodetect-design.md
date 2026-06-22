# Auto-open files with a dunder by magic / extension — design

**Date:** 2026-06-20
**Status:** Approved (brainstorm)
**Topic:** F3 (and Enter) on a local file detects its type by leading bytes (and
extension) and, when a registered provider claims it, navigates the active panel
into that dunder — instead of opening a viewer. First consumer: a SQLite file
opens in the Database dunder.

## Goal

Pressing **F3** (view) or **Enter** on a local file that sniffs as SQLite
(`SQLite format 3\x00` header) opens it in the **Database dunder** — the panel
navigates into `db://` for that file (browsable tables/records), exactly as if
the user had picked Database from the `_` menu and typed `sqlite:///<path>`.

Generalize the mechanism: a `VfsProvider` may declare the magic byte-prefixes
and/or filename extensions it can open for viewing, plus how to build its
open-spec from a local path. The F3/Enter router consults these so any future
provider (e.g. another archive type) becomes auto-openable without touching the
routing code.

## Approach

Add optional, structural capabilities to the provider contract (mirroring how
`resolve_target` / `ProviderActions` / `ProviderColumns` are optional and checked
by `getattr`). A small detector in `app.py` scans registered providers; F3 and
Enter consult it before falling through to the existing viewer/editor routing.
The Database dunder is the first provider to declare these fields.

Rejected: hard-coding a SQLite check inline in `action_view` (doesn't generalize,
and the user explicitly asked for a declarable contract).

## Components

### 1. Provider contract additions (optional, structural)

A provider that can open local files for viewing may declare:

- `view_magic: tuple[bytes, ...]` — byte-prefix signatures. A file matches when
  its leading bytes start with any entry. `DbProvider`:
  `(b"SQLite format 3\x00",)`.
- `view_extensions: tuple[str, ...]` — lowercase filename suffixes (e.g.
  `(".tar",)`). `DbProvider` leaves this **empty**: the SQLite magic is
  authoritative, and extensions like `.db` are ambiguous (Berkeley DB, etc.) and
  would cause false positives.
- `spec_from_path(path: str) -> str` — builds the open-spec passed to
  `resolve_target` for a local file. `DbProvider`:
  `f"sqlite:///{os.path.abspath(path)}"`, which for an absolute path yields the
  4-slash SQLAlchemy absolute form (`sqlite:////Users/me/data.db`).

Providers without these fields simply don't participate (the detector skips any
provider missing `spec_from_path`, or with neither `view_magic` nor
`view_extensions`).

### 2. Detector — `DundersApp._dunder_for_local_file(path: Path)`

Returns `(scheme, spec)` for the first registered provider that claims `path`,
else `None`:

1. Read the file head once — `head = open(path,"rb").read(N)` where `N` is the
   longest declared `view_magic` (≈16); tolerate `OSError` → `None`.
2. Iterate `self._vfs_registry` providers. **Magic takes precedence over
   extension**: for each provider check `view_magic` against `head` first; only
   if no provider matched by magic, check `view_extensions` against the lowercased
   name. First match wins; return `(provider.scheme, provider.spec_from_path(str(path)))`.
3. A provider must have `spec_from_path` to be eligible.

Pure-ish and small; lives in `app.py` because it needs the registry.

### 3. F3 / Enter wiring

- **F3** — `action_view`: after confirming a local non-dir entry and before
  `_open_editor_window(...)`, call `_dunder_for_local_file(entry.path)`; on a
  match call `_do_open_dunder(scheme, spec)` and return.
- **Enter** — `on_file_panel_item_activated`: after confirming a local non-dir
  entry and **before** the `_executable_command` check, run the same detector;
  on a match `_do_open_dunder(...)` and return. (A SQLite file with the exec bit
  set still opens as a database — magic wins.)

`_do_open_dunder` already navigates the active panel into the resolved locator
(connecting on a worker via the `slow` capability) — so F3/Enter on a SQLite file
lands the panel on the `db://` root listing its tables, just like entering an
archive. Leaving is `..` (the db provider's parent entry steps back to the local
directory).

### 4. Open mode

Opened **editable** — identical to opening the database from the `_` menu, so the
same file behaves the same regardless of entry point. Read-only remains available
via the menu's `?readonly` spec. (F3 here means "act on the file under the
cursor", not a read-only connection.)

### 5. Error handling

The detector fires only on a genuine magic (or declared-extension) match. If the
connection then fails (corrupt or locked DB), `_do_open_dunder` →
`_apply_open_result` already surfaces the provider's specific reason as a toast.
No automatic fallback to the hex viewer: the user asked to open it as a database,
so a database error is the correct, predictable outcome.

## Data flow

```
F3 (action_view) / Enter (on_file_panel_item_activated) on a local non-dir file
  → _dunder_for_local_file(path):
        read head bytes → scan registry providers
        magic match (then extension) → (scheme, provider.spec_from_path(path))
  → match? _do_open_dunder(scheme, spec)  [worker: DbProvider.resolve_target → dbset.connect]
            → panel._change_cwd_loc(db://sqlite:////path/) → tables/indexes listing
  → no match? existing routing (_open_editor_window / executable run)
```

## Testing

- `tests/fm/providers/test_db_provider.py` — pure: `DbProvider.view_magic` contains
  the SQLite signature; `view_extensions` is empty; `spec_from_path("/a/b.db")`
  == `"sqlite:////a/b.db"` (absolute 4-slash form) and round-trips through
  `resolve_target` to a `db://` locator.
- `tests/fm/test_app_skeleton.py` (or a focused app test) — detector + routing:
  - `_dunder_for_local_file` on a real temp file whose first bytes are the SQLite
    header → `("db", "sqlite:////…")`; on a plain text file → `None`; on an empty
    file → `None`.
  - async smoke: F3 and Enter on a temp SQLite file leave the active panel on a
    `cwd_loc.scheme == "db"` location (not an open viewer window).

## Out of scope (v1)

- Declaring magic/extension for any provider other than `DbProvider`.
- A read-only F3 mode (use the menu's `?readonly`).
- Auto-fallback to the hex viewer when a magic-matched DB fails to open.
- Detecting SQLite over non-local providers (a `.db` inside a zip/SFTP) — only
  local files are auto-opened; VFS members continue through `_open_member_view`.
