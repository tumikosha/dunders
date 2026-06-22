# Auto-open files with a dunder by magic/extension (F3+Enter) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** F3 (view) and Enter on a local file that sniffs as SQLite open it in the Database dunder (the panel navigates into `db://` for that file), via a declarable provider capability so the routing generalizes.

**Architecture:** Add optional, structural fields to a `VfsProvider` (`view_magic`, `view_extensions`, `spec_from_path`) — `DbProvider` declares the SQLite magic. A detector in `app.py` (`_dunder_for_local_file`) scans the registry (magic before extension) and returns `(scheme, spec)`; `action_view` (F3) and `on_file_panel_item_activated` (Enter) consult it before the existing viewer/editor routing and call the existing `_do_open_dunder`.

**Tech Stack:** Python ≥3.12, Textual, pytest (asyncio auto-mode), the existing `dunders.fm` VFS provider contract.

## Global Constraints

- Python ≥3.12.
- New provider fields are OPTIONAL and structural (checked via `getattr`/`hasattr`, like `resolve_target`/`ProviderActions`); providers without them don't participate. Do not add them to the `VfsProvider` Protocol as required members.
- Magic takes precedence over extension. First matching provider wins; iterate schemes in sorted order for determinism.
- `DbProvider.view_magic = (b"SQLite format 3\x00",)`, `view_extensions = ()` (empty — SQLite magic is authoritative; `.db` is ambiguous), `spec_from_path(path)` returns `f"sqlite:///{os.path.abspath(path)}"`.
- Opened **editable** (reuse `_do_open_dunder`, same as the `_` menu). No read-only F3 mode.
- Only LOCAL files auto-open (VFS members keep going through `_open_member_view`). No auto-fallback to the hex viewer on DB-open failure (`_do_open_dunder` already toasts the reason).
- `ruff check` clean; full suite green. Tests under `tests/fm/`.

---

### Task 1: Declare the openable-file capability on `DbProvider`

**Files:**
- Modify: `dunders/fm/providers/db_provider.py`
- Test: `tests/fm/providers/test_db_provider.py`

**Interfaces:**
- Produces (on `DbProvider`): `view_magic: tuple[bytes, ...]`, `view_extensions: tuple[str, ...]`, `spec_from_path(self, path: str) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/fm/providers/test_db_provider.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/fm/providers/test_db_provider.py -k "view_magic or spec_from_path" -x`
Expected: FAIL (`AttributeError: 'DbProvider' object has no attribute 'view_magic'`).

- [ ] **Step 3: Implement**

In `dunders/fm/providers/db_provider.py`, add `import os` to the imports. Add these class attributes to `DbProvider` (next to `open_placeholder`):

```python
    # Openable-file capability: F3/Enter on a local file whose first bytes match
    # this signature opens it in this dunder (see DundersApp._dunder_for_local_file).
    # Magic is authoritative for SQLite; .db/.sqlite extensions are ambiguous
    # (Berkeley DB etc.), so no extension matching.
    view_magic = (b"SQLite format 3\x00",)
    view_extensions: tuple[str, ...] = ()
```

And add this method to `DbProvider`:

```python
    def spec_from_path(self, path: str) -> str:
        """Open-spec for a local SQLite file: an absolute sqlite:/// URL.

        os.path.abspath yields a leading-slash path, so the f-string produces
        the 4-slash SQLAlchemy absolute form (sqlite:////abs/path)."""
        return f"sqlite:///{os.path.abspath(path)}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/fm/providers/test_db_provider.py -k "view_magic or spec_from_path" -v`
Expected: PASS.

- [ ] **Step 5: Lint, full suite, commit**

```bash
ruff check dunders/fm/providers/db_provider.py
pytest -q
git add dunders/fm/providers/db_provider.py tests/fm/providers/test_db_provider.py
git commit -m "feat(db): declare SQLite magic + spec_from_path on DbProvider"
```

---

### Task 2: Detector + F3/Enter wiring in `app.py`

**Files:**
- Modify: `dunders/app.py` (add `_dunder_for_local_file`; hook `action_view` and `on_file_panel_item_activated`)
- Test: `tests/fm/test_app_skeleton.py`

**Interfaces:**
- Consumes: `DbProvider.view_magic` / `view_extensions` / `spec_from_path` (Task 1); the existing `self._vfs_registry` (`schemes()`, `for_scheme(scheme)`), `self._do_open_dunder(scheme, spec)`, `self._active_panel()`, `self._is_local_entry(entry)`.
- Produces: `DundersApp._dunder_for_local_file(self, path: Path) -> tuple[str, str] | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/fm/test_app_skeleton.py`:

```python
def _make_sqlite(path):
    from dunders.fm.providers import db_access as da
    da.DbConn.open(f"sqlite:///{path}").close()


@pytest.mark.asyncio
async def test_detector_matches_sqlite_by_magic(tmp_path):
    from pathlib import Path
    db = tmp_path / "data.db"
    _make_sqlite(db)
    (tmp_path / "note.txt").write_text("hello, not a database")
    (tmp_path / "empty.db").write_bytes(b"")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        # db is an absolute Path, so f"sqlite:///{db}" is the 4-slash absolute form.
        assert app._dunder_for_local_file(db) == ("db", f"sqlite:///{db}")
        assert app._dunder_for_local_file(tmp_path / "note.txt") is None
        assert app._dunder_for_local_file(tmp_path / "empty.db") is None


@pytest.mark.asyncio
async def test_f3_on_sqlite_routes_to_open_dunder(tmp_path, monkeypatch):
    from dunders.fm.file_entry import FileEntry
    db = tmp_path / "data.db"
    _make_sqlite(db)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        captured = {}
        monkeypatch.setattr(app, "_do_open_dunder",
                            lambda scheme, spec, **kw: captured.update(scheme=scheme, spec=spec))
        panel = app._active_panel()
        idx = next(i for i, e in enumerate(panel.entries) if e.name == "data.db")
        panel.cursor = idx
        app.action_view()
        assert captured == {"scheme": "db", "spec": f"sqlite:///{db}"}


@pytest.mark.asyncio
async def test_enter_on_sqlite_routes_to_open_dunder(tmp_path, monkeypatch):
    import types
    from dunders.fm.file_entry import FileEntry
    db = tmp_path / "data.db"
    _make_sqlite(db)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        captured = {}
        monkeypatch.setattr(app, "_do_open_dunder",
                            lambda scheme, spec, **kw: captured.update(scheme=scheme, spec=spec))
        entry = FileEntry(path=db, name="data.db", size=db.stat().st_size,
                          mtime=0.0, is_dir=False)
        app.on_file_panel_item_activated(types.SimpleNamespace(entry=entry))
        assert captured == {"scheme": "db", "spec": f"sqlite:///{db}"}
```

> Note: `f"sqlite:///{db}"` where `db` is an absolute `Path` (e.g. `/tmp/.../data.db`) equals the 4-slash absolute form, matching `spec_from_path`.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/fm/test_app_skeleton.py -k "detector or routes_to_open_dunder" -x`
Expected: FAIL (`AttributeError: 'DundersApp' object has no attribute '_dunder_for_local_file'`).

- [ ] **Step 3: Implement the detector**

In `dunders/app.py`, add this method to `DundersApp` (near `_should_use_hex_viewer` / `_looks_image`):

```python
    def _dunder_for_local_file(self, path: Path) -> tuple[str, str] | None:
        """A registered provider that claims to open ``path`` for viewing, as
        ``(scheme, open_spec)`` — else None. Magic byte-prefixes take precedence
        over filename extensions; schemes are scanned in sorted order so the
        choice is deterministic. A provider participates only if it declares
        ``spec_from_path`` plus at least one of ``view_magic`` / ``view_extensions``.
        """
        providers = [self._vfs_registry.for_scheme(s)
                     for s in sorted(self._vfs_registry.schemes())]
        eligible = [p for p in providers if hasattr(p, "spec_from_path")
                    and (getattr(p, "view_magic", ()) or getattr(p, "view_extensions", ()))]
        if not eligible:
            return None
        max_magic = max((len(m) for p in eligible for m in getattr(p, "view_magic", ())),
                        default=0)
        head = b""
        if max_magic:
            try:
                with open(path, "rb") as fh:
                    head = fh.read(max_magic)
            except OSError:
                head = b""
        for p in eligible:  # magic first
            for sig in getattr(p, "view_magic", ()):
                if sig and head.startswith(sig):
                    return (p.scheme, p.spec_from_path(str(path)))
        name = path.name.lower()
        for p in eligible:  # then extension
            for ext in getattr(p, "view_extensions", ()):
                if ext and name.endswith(ext):
                    return (p.scheme, p.spec_from_path(str(path)))
        return None
```

- [ ] **Step 4: Hook F3 (`action_view`)**

In `action_view` (the local-file path), insert the detector check right before the final `self._open_editor_window(entry.path, read_only=True)`:

```python
        if not self._is_local_entry(entry):
            self._open_member_view(entry)  # read through the VFS provider
            return
        match = self._dunder_for_local_file(entry.path)
        if match is not None:
            self._do_open_dunder(*match)  # navigate the panel into the dunder (e.g. a SQLite DB)
            return
        self._open_editor_window(entry.path, read_only=True)
```

- [ ] **Step 5: Hook Enter (`on_file_panel_item_activated`)**

In `on_file_panel_item_activated`, insert the detector check after the local-entry confirmation and BEFORE the `_executable_command` check:

```python
        if not self._is_local_entry(event.entry):
            self._open_member_view(event.entry)  # read-only inside archives
            return
        match = self._dunder_for_local_file(event.entry.path)
        if match is not None:
            self._do_open_dunder(*match)  # a SQLite file opens as a database (magic wins)
            return
        cmd = self._executable_command(event.entry.path)
        if cmd is not None and self._run_in_console(cmd):
            return
        self._open_editor_window(event.entry.path)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/fm/test_app_skeleton.py -k "detector or routes_to_open_dunder" -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Lint, import smoke, full suite, commit**

```bash
ruff check dunders/app.py tests/fm/test_app_skeleton.py
python -c "import dunders.app"
pytest -q
git add dunders/app.py tests/fm/test_app_skeleton.py
git commit -m "feat(fm): F3/Enter auto-open local files with a matching dunder (SQLite → Database)"
```

---

## Self-Review

**Spec coverage:**
- Contract additions (`view_magic`/`view_extensions`/`spec_from_path`): Task 1.
- Detector (head read, magic-before-extension, sorted schemes, eligibility): Task 2 Step 3.
- F3 wiring: Task 2 Step 4. Enter wiring (before exec check): Task 2 Step 5.
- Editable open (reuse `_do_open_dunder`): Tasks 2 Steps 4–5 (no read-only path added).
- Local-only (VFS members keep `_open_member_view`): both hooks check `_is_local_entry` first.
- No hex fallback on failure: neither hook adds one; `_do_open_dunder` toasts.
- Tests: pure provider fields (Task 1), detector + F3 + Enter routing (Task 2).

**Placeholder scan:** No TBD/TODO. Every code step shows complete code.

**Type consistency:** `_dunder_for_local_file(path: Path) -> tuple[str, str] | None` used consistently in both hooks via `self._do_open_dunder(*match)`; `_do_open_dunder(scheme, spec, *, password=None)` already exists with that signature; `spec_from_path(path: str) -> str` matches between Task 1 (definition) and Task 2 (call with `str(path)`). `view_magic`/`view_extensions` names match between provider and detector.
