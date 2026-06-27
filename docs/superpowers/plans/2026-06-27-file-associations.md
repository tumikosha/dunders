# File Associations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users declare, per file extension, what dunders does on Enter/F3/F4 — choosing a built-in viewer or an external per-OS command — and fix the `.jpg` "codec can't decode byte 0xff" crash out of the box.

**Architecture:** A pure resolver layer (`fm/associations.py`) parses a TOML table and merges it over built-in defaults; an I/O layer (`fm/associations_loader.py`) reads/seeds the file under `config_dir()`. `app.py` resolves an action for a `(extension, verb)` pair at the three entry points (Enter / F3 / F4) and dispatches to either an internal opener (`_open_with_handler`) or the existing handover (external command). Mirrors the proven User Menu structure (`user_menu.py` / `user_menu_loader.py`).

**Tech Stack:** Python ≥3.12, stdlib `tomllib` (read) + `tomli_w`-free hand-written seed, Textual, pytest (asyncio auto mode), ruff.

## Global Constraints

- Python ≥ 3.12. Use stdlib `tomllib` for parsing; **no new third-party dependency**.
- New pure logic lives in `dunders/fm/associations.py`; all filesystem I/O in `dunders/fm/associations_loader.py`. The pure module must NOT import `app`, `windowing`, or do any I/O except `tomllib.loads` on an in-memory string.
- Config file path: `config_dir()/associations.toml` (honours `XDG_CONFIG_HOME`).
- Verbs are exactly `"open"` (Enter), `"view"` (F3), `"edit"` (F4).
- Built-in handler names: `auto editor viewer hex image csv markdown office database`. An unknown handler resolves to `auto`.
- External command values are prefixed with `!` in TOML; the stored `ExternalAction.command` has the `!` stripped.
- Tests live under `tests/fm/`, mirroring `tests/fm/test_user_menu.py` (pure) and `tests/fm/test_user_menu_app.py` (async app). `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed. The autouse `XDG_CONFIG_HOME` fixture in `tests/conftest.py` already isolates the config dir.
- Run `ruff check` clean before each commit.

---

### Task 1: Pure association table — parse, defaults, resolve

**Files:**
- Create: `dunders/fm/associations.py`
- Test: `tests/fm/test_associations.py`

**Interfaces:**
- Produces:
  - `BuiltinAction(handler: str)` and `ExternalAction(command: str)` — frozen dataclasses; `Action = BuiltinAction | ExternalAction`.
  - `BUILTIN_DEFAULTS: dict[str, dict[str, str]]`
  - `parse_associations(text: str) -> dict[str, dict[str, object]]` (raises `tomllib.TOMLDecodeError` on malformed TOML)
  - `merge_tables(base: dict, user: dict) -> dict`
  - `resolve(table: dict, ext: str, verb: str, os_name: str) -> Action`
  - `current_os_name() -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/fm/test_associations.py
import pytest

from dunders.fm.associations import (
    BUILTIN_DEFAULTS,
    BuiltinAction,
    ExternalAction,
    current_os_name,
    merge_tables,
    parse_associations,
    resolve,
)


def test_parse_string_and_per_os_table_and_bang():
    text = """
[jpg]
open = "image"

[jpg.edit]
default = "!xdg-open %f"
macos = "!open -a Preview %f"
"""
    table = parse_associations(text)
    assert table["jpg"]["open"] == "image"
    assert table["jpg"]["edit"] == {
        "default": "!xdg-open %f",
        "macos": "!open -a Preview %f",
    }


def test_parse_ignores_non_table_sections():
    # A top-level scalar is not an extension section; it must be dropped.
    assert parse_associations('bogus = 1\n[png]\nopen = "image"\n') == {
        "png": {"open": "image"}
    }


def test_resolve_builtin_handler():
    table = {"png": {"open": "image"}}
    assert resolve(table, "png", "open", "linux") == BuiltinAction("image")


def test_resolve_external_strips_bang_and_picks_os():
    table = {"jpg": {"edit": {"default": "!xdg-open %f", "macos": "!open %f"}}}
    assert resolve(table, "jpg", "edit", "macos") == ExternalAction("open %f")
    assert resolve(table, "jpg", "edit", "linux") == ExternalAction("xdg-open %f")


def test_resolve_missing_ext_or_verb_is_auto():
    assert resolve({}, "xyz", "open", "linux") == BuiltinAction("auto")
    assert resolve({"png": {"view": "image"}}, "png", "open", "linux") == BuiltinAction("auto")


def test_resolve_per_os_table_without_match_falls_back_to_auto():
    table = {"jpg": {"edit": {"macos": "!open %f"}}}
    assert resolve(table, "jpg", "edit", "linux") == BuiltinAction("auto")


def test_merge_overrides_at_verb_granularity():
    base = {"jpg": {"open": "image", "view": "image"}}
    user = {"jpg": {"edit": "!gimp %f"}}
    merged = merge_tables(base, user)
    assert merged["jpg"] == {"open": "image", "view": "image", "edit": "!gimp %f"}
    # base is not mutated
    assert "edit" not in base["jpg"]


def test_builtin_defaults_cover_jpg_open():
    assert BUILTIN_DEFAULTS["jpg"]["open"] == "image"


def test_current_os_name_is_one_of_known():
    assert current_os_name() in {"macos", "linux", "windows"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/fm/test_associations.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dunders.fm.associations'`.

- [ ] **Step 3: Write the implementation**

```python
# dunders/fm/associations.py
"""File-association table: parse, built-in defaults, and resolution (pure)."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass

# ext -> {verb: handler}. The defaults that make common types open correctly
# out of the box (and fix the .jpg-on-Enter crash). Verbs absent here resolve
# to the "auto" handler (current smart routing).
_IMAGE = {"open": "image", "view": "image"}
BUILTIN_DEFAULTS: dict[str, dict[str, str]] = {
    "jpg": dict(_IMAGE), "jpeg": dict(_IMAGE), "png": dict(_IMAGE),
    "gif": dict(_IMAGE), "bmp": dict(_IMAGE), "webp": dict(_IMAGE),
    "tiff": dict(_IMAGE), "tif": dict(_IMAGE), "ico": dict(_IMAGE),
    "csv": {"view": "csv"}, "tsv": {"view": "csv"},
    "md": {"open": "markdown", "view": "markdown"},
    "markdown": {"open": "markdown", "view": "markdown"},
    "pdf": {"view": "office"}, "docx": {"view": "office"},
    "pptx": {"view": "office"}, "xlsx": {"view": "office"},
    "epub": {"view": "office"},
}


@dataclass(frozen=True)
class BuiltinAction:
    handler: str


@dataclass(frozen=True)
class ExternalAction:
    command: str


Action = BuiltinAction | ExternalAction


def current_os_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def parse_associations(text: str) -> dict[str, dict[str, object]]:
    """Parse a TOML associations document. Non-table top-level keys are
    ignored. Raises ``tomllib.TOMLDecodeError`` on malformed input."""
    raw = tomllib.loads(text)
    out: dict[str, dict[str, object]] = {}
    for ext, verbs in raw.items():
        if not isinstance(verbs, dict):
            continue
        out[ext.lower()] = {str(k): v for k, v in verbs.items()}
    return out


def merge_tables(base: dict, user: dict) -> dict:
    """Merge ``user`` over ``base`` at (ext, verb) granularity. Pure."""
    out: dict[str, dict[str, object]] = {
        ext: dict(verbs) for ext, verbs in base.items()
    }
    for ext, verbs in user.items():
        dst = out.setdefault(ext, {})
        for verb, val in verbs.items():
            dst[verb] = val
    return out


def resolve(table: dict, ext: str, verb: str, os_name: str) -> Action:
    spec = table.get(ext.lower(), {})
    val = spec.get(verb)
    if isinstance(val, dict):
        val = val.get(os_name) or val.get("default")
    if isinstance(val, str):
        if val.startswith("!"):
            return ExternalAction(val[1:].strip())
        return BuiltinAction(val)
    return BuiltinAction("auto")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/fm/test_associations.py -q && ruff check dunders/fm/associations.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add dunders/fm/associations.py tests/fm/test_associations.py
git commit -m "feat(fm): pure file-association table (parse/defaults/resolve)"
```

---

### Task 2: Loader — path, fault-tolerant load+merge, seeding

**Files:**
- Create: `dunders/fm/associations_loader.py`
- Test: `tests/fm/test_associations_loader.py`

**Interfaces:**
- Consumes (Task 1): `BUILTIN_DEFAULTS`, `parse_associations`, `merge_tables`.
- Produces:
  - `associations_path() -> Path`
  - `load_table() -> tuple[dict, str | None]` — `(merged_table, error_message_or_None)`. Never raises.
  - `seed_associations() -> Path` — writes `SEED_ASSOCIATIONS` if the file is absent; returns the path.
  - `SEED_ASSOCIATIONS: str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/fm/test_associations_loader.py
from dunders.fm import associations_loader as L
from dunders.fm.associations import BuiltinAction, resolve


def test_load_table_without_file_returns_defaults():
    table, err = L.load_table()
    assert err is None
    # jpg defaults present even with no user file.
    assert resolve(table, "jpg", "open", "linux") == BuiltinAction("image")


def test_user_file_overrides_at_verb_granularity():
    L.associations_path().parent.mkdir(parents=True, exist_ok=True)
    L.associations_path().write_text('[jpg]\nopen = "hex"\n', encoding="utf-8")
    table, err = L.load_table()
    assert err is None
    assert resolve(table, "jpg", "open", "linux") == BuiltinAction("hex")
    # view still comes from defaults
    assert resolve(table, "jpg", "view", "linux") == BuiltinAction("image")


def test_broken_toml_falls_back_to_defaults_with_error():
    L.associations_path().parent.mkdir(parents=True, exist_ok=True)
    L.associations_path().write_text("this is = = not toml", encoding="utf-8")
    table, err = L.load_table()
    assert err is not None
    assert resolve(table, "jpg", "open", "linux") == BuiltinAction("image")


def test_seed_writes_once_and_is_valid_toml():
    path = L.seed_associations()
    assert path.is_file()
    before = path.read_text(encoding="utf-8")
    # A second seed must not overwrite.
    path.write_text(before + "\n# edited\n", encoding="utf-8")
    L.seed_associations()
    assert "# edited" in path.read_text(encoding="utf-8")
    # The seed parses and loads cleanly.
    _, err = L.load_table()
    assert err is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/fm/test_associations_loader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dunders.fm.associations_loader'`.

- [ ] **Step 3: Write the implementation**

```python
# dunders/fm/associations_loader.py
"""File-association file resolution, merge, and first-run seeding (I/O layer)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from dunders.config import user_config
from dunders.fm.associations import (
    BUILTIN_DEFAULTS,
    merge_tables,
    parse_associations,
)

SEED_ASSOCIATIONS = """\
# dunders file associations.
#
# Each section is a file extension (no dot). Verbs:
#   open  -> Enter / double-click
#   view  -> F3
#   edit  -> F4
#
# A verb is either a built-in handler name or "!<external command>".
# Built-in handlers: auto editor viewer hex image csv markdown office database
# External commands use the User Menu macros (%f file, %d dir, %s selection).
# A verb may be a string (all OSes) or a table with macos/linux/windows/default.

[jpg]
open = "image"
view = "image"
[jpg.edit]
default = "!xdg-open %f"
macos   = "!open -a Preview %f"
windows = "!start \\"\\" %f"

[png]
open = "image"
view = "image"

[md]
open = "markdown"
view = "markdown"
edit = "editor"
"""


def associations_path() -> Path:
    return user_config.config_dir() / "associations.toml"


def load_table() -> tuple[dict, str | None]:
    """Return ``(merged_table, error_or_None)``. Never raises: a missing file
    yields the built-in defaults; a malformed file yields defaults + a message."""
    path = associations_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return merge_tables(BUILTIN_DEFAULTS, {}), None
    try:
        user = parse_associations(text)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return merge_tables(BUILTIN_DEFAULTS, {}), str(exc)
    return merge_tables(BUILTIN_DEFAULTS, user), None


def seed_associations() -> Path:
    """Write the starter file if it does not exist; return the path."""
    path = associations_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(SEED_ASSOCIATIONS, encoding="utf-8")
    except OSError:
        pass
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/fm/test_associations_loader.py -q && ruff check dunders/fm/associations_loader.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add dunders/fm/associations_loader.py tests/fm/test_associations_loader.py
git commit -m "feat(fm): associations loader (load/merge/seed)"
```

---

### Task 3: App dispatch — `_open_with_handler`, `_dispatch_association`, wire Enter/F3/F4, harden `auto`

**Files:**
- Modify: `dunders/app.py` (imports near line 106; new methods; `on_file_panel_item_activated` ~3783; `action_view` ~3070; `action_edit` ~3031; the editable/viewer branch of `_open_editor_window` ~3742-3760)
- Test: `tests/fm/test_associations_app.py`

**Interfaces:**
- Consumes (Tasks 1-2): `associations_loader.load_table`, `associations.resolve`, `associations.current_os_name`, `BuiltinAction`, `ExternalAction`; existing `expand_macros`, `MacroContext`, `_build_macro_context`, `_run_user_menu_body`, `_open_editor_window`, `_make_csv_viewer`, `_mount_maximized_content`, `ImageViewerContent`, `HexViewerContent`, `MarkdownViewerContent`, `ViewerContent`, `PILLOW_AVAILABLE`, `MARKITDOWN_AVAILABLE`, `_convert_office_async`, `_dunder_for_local_file`, `_do_open_dunder`.
- Produces:
  - `_assoc_table(self) -> dict`
  - `_safe_read_text(path: Path) -> str | None`
  - `_open_with_handler(self, path: Path, handler: str, *, read_only: bool) -> None`
  - `_dispatch_association(self, entry, verb: str) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/fm/test_associations_app.py
from pathlib import Path

from dunders.app import DundersApp
from dunders.fm.hex_viewer import HexViewerContent
from dunders.fm.image_viewer import ImageViewerContent
from dunders.fm import associations_loader as L

# A tiny but valid JPEG header — starts with 0xff 0xd8 0xff, which is exactly
# the byte that crashed `path.read_text()`.
JPEG_BYTES = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


def _select(app, name):
    panel = app._active_panel()
    for i, e in enumerate(panel.entries):
        if e.name == name:
            panel.cursor = i
            return panel
    raise AssertionError(f"{name} not in panel: {[e.name for e in panel.entries]}")


async def test_f4_edit_on_jpg_does_not_crash_and_opens_hex(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(JPEG_BYTES)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _select(app, "photo.jpg")
        app.action_edit()  # F4 — previously raised UnicodeDecodeError
        await _settle(pilot)
        # Undecodable file falls back to the hex viewer instead of crashing.
        assert list(app.query(HexViewerContent))


async def test_enter_on_jpg_opens_image_or_hex(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(JPEG_BYTES)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        panel = _select(app, "photo.jpg")
        entry = panel.entries[panel.cursor]
        app._dispatch_association(entry, "open")  # built-in default: image
        await _settle(pilot)
        assert list(app.query(ImageViewerContent)) or list(app.query(HexViewerContent))


async def test_external_command_runs_through_handover(tmp_path, monkeypatch):
    # User maps .foo edit to an external command; F4 must route to handover.
    L.associations_path().parent.mkdir(parents=True, exist_ok=True)
    L.associations_path().write_text(
        '[foo]\nedit = "!echo %f"\n', encoding="utf-8"
    )
    (tmp_path / "a.foo").write_text("hi", encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        calls = []
        monkeypatch.setattr(app, "_run_user_menu_body", lambda body, cwd: calls.append((body, cwd)))
        _select(app, "a.foo")
        app.action_edit()
        await _settle(pilot)
        assert calls and calls[0][0].startswith("echo ")
        assert "a.foo" in calls[0][0]
```

> NOTE: confirm the entry attribute is `.name` while writing Step 1 — `FilePanel` entries expose `.name`, `.path`, `.is_dir`, `.loc` (see `app._is_local_entry`). If `.name` is absent on the parent (`..`) entry, the `_select` loop simply skips it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/fm/test_associations_app.py -q`
Expected: FAIL — `AttributeError: 'DundersApp' object has no attribute '_dispatch_association'` (and the F4 test still raising / not producing a hex viewer).

- [ ] **Step 3a: Add the import**

In `dunders/app.py` near the other `dunders.fm` imports (around line 106, beside `from dunders.fm.image_viewer import ...`), add:

```python
from dunders.fm import associations_loader
from dunders.fm.associations import (
    BuiltinAction,
    ExternalAction,
    current_os_name,
    resolve as resolve_association,
)
```

- [ ] **Step 3b: Add the helper methods**

Add these methods to `DundersApp` (place them next to `_open_editor_window`):

```python
    def _assoc_table(self) -> dict:
        table, err = associations_loader.load_table()
        if err is not None:
            self.notify(
                f"associations.toml ignored (parse error): {err}",
                severity="warning",
            )
        return table

    @staticmethod
    def _safe_read_text(path: Path) -> str | None:
        """Read the file as text, or None if it is binary/undecodable."""
        try:
            return path.read_text()
        except (OSError, UnicodeDecodeError):
            return None

    def _dispatch_association(self, entry, verb: str) -> None:
        """Resolve and run the association for ``entry`` under ``verb``."""
        ext = Path(entry.name).suffix.lstrip(".").lower()
        action = resolve_association(self._assoc_table(), ext, verb, current_os_name())
        if isinstance(action, ExternalAction):
            ctx, cwd = self._build_macro_context()
            body = expand_macros(action.command, ctx, {})
            self._run_user_menu_body(body, cwd)
            return
        # BuiltinAction: view is read-only; open/edit are editable for `auto`.
        self._open_with_handler(entry.path, action.handler, read_only=(verb == "view"))

    def _open_with_handler(self, path: Path, handler: str, *, read_only: bool) -> None:
        if self.desktop is None:
            return
        if handler in ("auto", "editor"):
            self._open_editor_window(path, read_only=read_only and handler == "auto")
            return
        if handler == "database":
            match = self._dunder_for_local_file(path)
            if match is not None:
                self._do_open_dunder(*match)
            else:
                self._open_editor_window(path, read_only=read_only)
            return
        if handler == "office":
            if MARKITDOWN_AVAILABLE:
                self._convert_office_async(
                    path.name, path, lambda: HexViewerContent(path)
                )
            else:
                self.notify(
                    "Install dunders[office] to view documents", severity="warning"
                )
                self._open_with_handler(path, "hex", read_only=True)
            return
        self._remember_active_panel_id()
        self._editor_seq += 1
        seq = self._editor_seq
        if handler == "image":
            if PILLOW_AVAILABLE:
                self._mount_maximized_content(
                    ImageViewerContent(path),
                    title=f"Image: {path.name}",
                    win_id=f"imgviewer-{seq}",
                )
                return
            self.notify(
                "Install dunders[image] to view images as ASCII", severity="warning"
            )
            handler = "hex"
        if handler == "csv":
            content = self._make_csv_viewer(path)
            if content is not None:
                self._mount_maximized_content(
                    content, title=f"CSV: {path.name}", win_id=f"csvviewer-{seq}"
                )
                return
            handler = "hex"  # too large to tabulate
        if handler == "markdown":
            text = self._safe_read_text(path)
            if text is not None:
                self._mount_maximized_content(
                    MarkdownViewerContent(file_path=path, text=text),
                    title=f"MD: {path.name}",
                    win_id=f"mdviewer-{seq}",
                )
                return
            handler = "hex"
        if handler == "viewer":
            text = self._safe_read_text(path)
            if text is not None:
                self._mount_maximized_content(
                    ViewerContent(initial_text=text, file_path=str(path)),
                    title=f"View: {path.name}",
                    win_id=f"viewer-{seq}",
                )
                return
            handler = "hex"
        if handler == "hex":
            self._mount_maximized_content(
                HexViewerContent(path), title=f"Hex: {path.name}", win_id=f"hexviewer-{seq}"
            )
            return
        # Unknown handler → safe fallback.
        self._open_editor_window(path, read_only=read_only)
```

> NOTE while implementing: verify `MARKITDOWN_AVAILABLE`, `MarkdownViewerContent`, `ViewerContent`, `_convert_office_async` are already imported in `app.py` (they are used by `_open_editor_window`). If any name is module-local, reuse the same reference `_open_editor_window` uses.

- [ ] **Step 3c: Harden the `auto` opener against decode errors**

In `_open_editor_window`, the `else` branch currently does `text = path.read_text()` in a `try/except OSError`, which lets `UnicodeDecodeError` propagate — the root crash. Replace the block at `dunders/app.py:3742-3760` (`else:` through the editable branch) so an undecodable file routes to the hex viewer instead:

```python
        else:
            # EditorContent.__init__ does NOT read the file — load the text
            # ourselves. A binary/undecodable file falls back to the hex viewer
            # rather than raising UnicodeDecodeError (the .jpg-on-Enter crash).
            text = self._safe_read_text(path)
            if text is None:
                content = HexViewerContent(path)
                title = f"Hex: {path.name}"
                win_id = f"hexviewer-{seq}"
            elif read_only and looks_markdown(path):
                content = MarkdownViewerContent(file_path=path, text=text)
                title = f"MD: {path.name}"
                win_id = f"mdviewer-{seq}"
            elif read_only:
                content = ViewerContent(initial_text=text, file_path=str(path))
                title = f"View: {path.name}"
                win_id = f"viewer-{seq}"
            else:
                dw, dh = self.desktop.usable_size.width, self.desktop.usable_size.height
                win = self._make_editor_window(
                    path,
                    position=(0, 0),
                    size=(dw, dh),
                    win_id=f"editor-{seq}",
                    text=text,
                )
                win._saved_rect = (
                    Offset(2, 1), Size(max(1, dw - 4), max(1, dh - 2))
                )
                win.maximized = True
                self.desktop.add_window(win)
                if self._pre_menu_focus is not None or self._pre_menu_window is not None:
                    self._pre_menu_window = win
                    self._pre_menu_focus = None
                return
        self._mount_maximized_content(content, title=title, win_id=win_id)
```

(The preceding `if read_only and self._should_use_hex_viewer(path):` branch is unchanged.)

- [ ] **Step 3d: Wire the three entry points**

In `on_file_panel_item_activated` (~app.py:3802), replace the final line:

```python
        self._open_editor_window(event.entry.path)
```
with:
```python
        self._dispatch_association(event.entry, "open")
```

In `action_view` (~app.py:3070), replace the final line:

```python
        self._open_editor_window(entry.path, read_only=True)
```
with:
```python
        self._dispatch_association(entry, "view")
```

In `action_edit` (~app.py:3031), replace the final line:

```python
        self._open_editor_window(entry.path, read_only=False)
```
with:
```python
        self._dispatch_association(entry, "edit")
```

(Leave every preceding guard — modal, dir, `_is_local_entry`, db-table, `_dunder_for_local_file` — exactly as-is; associations apply only past those guards, to local files.)

- [ ] **Step 4: Run the tests**

Run: `pytest tests/fm/test_associations_app.py -q && ruff check dunders/app.py`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Run the existing F-key / smoke suites for regressions**

Run: `pytest tests/fm/test_app_skeleton.py tests/fm/test_user_menu_app.py -q`
Expected: PASS (no regressions in view/edit/Enter wiring).

- [ ] **Step 6: Commit**

```bash
git add dunders/app.py tests/fm/test_associations_app.py
git commit -m "feat(fm): route Enter/F3/F4 through file associations; fix .jpg decode crash"
```

---

### Task 4: Menu entry — "Edit file associations…" (`assoc.edit`)

**Files:**
- Modify: `dunders/app.py` (`WindowCommand` block ~1041; `_` menu `MenuItem`s ~1341; new `action_edit_associations` near `action_ai_settings` ~2547)
- Test: `tests/fm/test_associations_menu_app.py`

**Interfaces:**
- Consumes: `associations_loader.seed_associations`, `_open_editor_window`.
- Produces: app command `assoc.edit` → `action_edit_associations(self) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/fm/test_associations_menu_app.py
from dunders.app import DundersApp
from dunders.fm import associations_loader as L


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


async def test_edit_associations_seeds_and_opens_editor(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        assert not L.associations_path().exists()
        app.action_edit_associations()
        await _settle(pilot)
    assert L.associations_path().is_file()  # seeded on first open
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/fm/test_associations_menu_app.py -q`
Expected: FAIL — `AttributeError: 'DundersApp' object has no attribute 'action_edit_associations'`.

- [ ] **Step 3a: Register the command**

In the `WindowCommand` list (after the `ai.settings` entry at `dunders/app.py:1041`), add:

```python
            WindowCommand(id="assoc.edit", label="Edit file associations…", handler=self.action_edit_associations),
```

- [ ] **Step 3b: Add the menu item**

In the `_` menu items (right after the `ai.settings` `MenuItem` at `dunders/app.py:1341`), add:

```python
                MenuItem(label="Edit file associations…", command_id="assoc.edit"),
```

- [ ] **Step 3c: Add the action**

Next to `action_ai_settings` (~app.py:2547), add:

```python
    def action_edit_associations(self) -> None:
        """Seed (if needed) and open the file-associations TOML for editing."""
        if self._has_active_modal() or self.desktop is None:
            return
        path = associations_loader.seed_associations()
        self._open_editor_window(path, read_only=False)
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/fm/test_associations_menu_app.py -q && ruff check dunders/app.py`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add dunders/app.py tests/fm/test_associations_menu_app.py
git commit -m "feat(fm): _ menu 'Edit file associations…' (assoc.edit)"
```

---

### Task 5: Full suite + docs

**Files:**
- Modify: `CLAUDE.md` (Configuration section — document the associations file, like the User Menu entry)

- [ ] **Step 1: Run the full suite**

Run: `pytest -q`
Expected: all PASS.

- [ ] **Step 2: Document the feature**

Add a bullet to the Configuration section of `CLAUDE.md` describing: the `associations.toml` location, the three verbs, built-in handler names, the `!external` per-OS syntax, that built-in defaults fix `.jpg`/image opening, and the `assoc.edit` menu command. Reference `dunders/fm/associations.py` (pure) + `associations_loader.py` (I/O).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: file associations (associations.toml, assoc.edit)"
```

---

## Self-Review

**Spec coverage:**
- Both internal viewers and external commands → Task 3 (`_open_with_handler` + `ExternalAction` via handover). ✓
- One TOML file, per-OS sections for external commands; OS-agnostic viewer choice → Task 1 (`resolve` per-os table) + Task 2 (single `associations.toml`). ✓
- Built-in defaults + user override at (ext, verb) granularity → Task 1 (`BUILTIN_DEFAULTS`, `merge_tables`) + Task 2 (`load_table`). ✓
- Verbs open/view/edit wired at Enter/F3/F4 → Task 3 Step 3d. ✓
- `.jpg` decode crash fixed out of the box → Task 1 defaults (jpg→image) + Task 3 Step 3c (auto hex fallback) + regression test Task 3 Step 1. ✓
- "Edit file associations…" menu item + seeding → Task 4. ✓
- Fault tolerance (broken TOML → defaults + notify) → Task 2 (`load_table` returns error) + Task 3 (`_assoc_table` notifies). ✓
- Tests mirror User Menu (pure + async) → Tasks 1-4. ✓

**Known v1 limitations (intentional, YAGNI):** interactive `%{Prompt}` macros are not collected for association external commands (expanded with an empty prompts map — a `%{...}` becomes empty). No per-project association file. Both match the approved spec's non-goals; note them in the `CLAUDE.md` docs.

**Type consistency:** `resolve(table, ext, verb, os_name)` signature identical across Tasks 1/3; `load_table() -> tuple[dict, str|None]` consumed as `(table, err)` in Task 3; `BuiltinAction.handler` / `ExternalAction.command` field names consistent; handler-name set identical between `BUILTIN_DEFAULTS`, `_open_with_handler`, and the Global Constraints list.
