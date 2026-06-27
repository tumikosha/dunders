# File Associations — Design

**Date:** 2026-06-27
**Status:** Approved (brainstorming)
**Branch:** `feat/file-associations`

## Problem

Pressing **Enter** on a `.jpg` raises `UnicodeDecodeError: 'utf-8' codec can't
decode byte 0xff`. Root cause: `on_file_panel_item_activated` (Enter /
double-click) calls `app._open_editor_window(path)` **without** `read_only=True`.
The image-viewer branch in `_open_editor_window` is gated on
`read_only and self._looks_image(path)`, so for an Enter it is skipped and
control falls through to `path.read_text()`, which chokes on a JPEG's `0xff`
byte. The same path affects **F4/Edit**. (F3/View already routes to the image
viewer because it passes `read_only=True`.)

More broadly, there is no way for a user to declare what happens on
click/Enter, View (F3), and Edit (F4) for files of a given extension. Different
operating systems also need different external commands.

## Goals

- Let users declare, per file extension, what happens for three verbs:
  **open** (Enter), **view** (F3), **edit** (F4).
- An action is either a **built-in handler** (one of dunders' internal
  viewers/editor) or an **external OS command**.
- External commands differ per OS (macOS / Linux / Windows); the choice of a
  built-in viewer is OS-agnostic.
- Ship **built-in defaults** so common types (images, csv, markdown, office)
  work correctly out of the box — fixing the `.jpg` bug without any user config.
- Expose an **"Edit file associations…"** entry in the system (`_`) menu that
  opens the config file in dunders itself.

## Non-goals (YAGNI)

- No per-project / per-panel-dir override file (one global file only). May be
  added later, mirroring the User Menu's `./.dunders.menu.md` layering.
- No GUI association editor — the file is edited as text (like the User Menu).
- No magic-byte matching in the user table; matching is by extension. (Built-in
  defaults may still rely on the existing `_looks_image` / `_should_use_hex_viewer`
  fallbacks inside the handlers.)

## Approach (chosen: A — resolver in front of existing openers)

A pure resolution layer sits in front of the existing openers. It mirrors the
already-proven User Menu structure (`user_menu.py` pure parser +
`user_menu_loader.py` I/O + integration in `app.py`). Rejected alternatives:
**B** — wiring into the `dunder`/`VfsProvider` matching (conflates "open as
database by magic" with viewer choice, invasive); **C** — a minimal hardcoded
`ext→handler` dict with no TOML and no external commands (does not meet the
external-command / user-editable-file requirements).

## File format & location

Single global file: `config_dir()/associations.toml`
(`~/.config/dunders/associations.toml`, honouring `XDG_CONFIG_HOME` like the
rest of the config). TOML is chosen to match the project's theme files
(`themes/*.toml`); `tomllib` is in the stdlib for reading.

Each section is an extension (no dot, lower-cased). Three verbs: `open`, `view`,
`edit`. A verb value is either:

- a **built-in handler name**, or
- a string prefixed with `!` denoting an **external command**.

A verb may be a bare string (all OSes) **or** a table with per-OS keys
`macos` / `linux` / `windows` and an optional `default`. (TOML forbids a key
being both a string and a table, so the parser accepts either shape per verb.)

Built-in handler names:
`auto` (current smart routing; default for unlisted types), `editor`,
`viewer` (plain text), `hex`, `image`, `csv`, `markdown`, `office`, `database`.

External commands reuse the **User Menu macros** (`%f %d %t %s %F %D %x %b %%`,
interactive `%{Prompt}`) and run through the existing handover in the active
panel's cwd.

```toml
# ~/.config/dunders/associations.toml
# verb = handler-name | "!external command"
# Built-in handlers: auto editor viewer hex image csv markdown office database

[jpg]
open = "image"          # Enter → built-in ASCII image viewer
view = "image"          # F3
[jpg.edit]              # F4 — external program, per OS
default = "!xdg-open %f"
macos   = "!open -a Preview %f"
windows = "!start \"\" %f"

[md]
open = "markdown"       # Enter on .md renders instead of editing
view = "markdown"
edit = "editor"

[png]
open = "image"
view = "image"
```

## Resolution & precedence

Pure, no I/O (in `fm/associations.py`):

- `BUILTIN_DEFAULTS: dict[str, dict[str, str]]` — `ext → {verb: handler}` for
  image/csv/markdown/office types. This is what fixes the bug out of the box.
- The user TOML parses into the same shape and is **merged over** the defaults
  at the granularity of an individual `(ext, verb)` pair — overriding
  `jpg.edit` leaves `jpg.open` intact.
- `resolve(ext, verb, os_name) -> Action`:
  1. look up `ext` (lower, no dot) in the merged table; if the verb is present,
     take its value;
  2. table value → pick `os_name`, else `default`, else `auto`;
  3. string value → `BuiltinAction(name)`, or `ExternalAction(template)` if it
     starts with `!`;
  4. missing ext or verb → `BuiltinAction("auto")`.
- `os_name` is derived once from `sys.platform` (`darwin`→`macos`,
  `win32`→`windows`, else `linux`).

Fault tolerance: invalid TOML or an unreadable file → defaults + a
`notify(warning)` (like the fault-tolerant `config.json` reads). An unknown
handler name in a verb → fall back to `auto`.

## Modules & dispatch

Three files, mirroring the User Menu:

- `fm/associations.py` — pure: `BUILTIN_DEFAULTS`, `parse_associations(text)`,
  `resolve(...)`, and the `BuiltinAction` / `ExternalAction` types. Unit-tested
  without the app.
- `fm/associations_loader.py` — I/O: path resolution, read, seeding the example
  file on first "Edit", and the merge with `BUILTIN_DEFAULTS`.
- `app.py` changes:
  - `_open_with_handler(path, handler, *, read_only)` — maps a handler name to a
    concrete opener, **reusing** existing builders (`ImageViewerContent`,
    `HexViewerContent`, `_make_csv_viewer`, `MarkdownViewerContent`,
    `ViewerContent`, `_make_editor_window`). `auto` → the current
    `_open_editor_window`. `image` without Pillow → `hex` + notify (existing
    behaviour reused).
  - `_dispatch_association(entry, verb)` — resolves and either calls
    `_open_with_handler` or expands macros and runs the external command via
    handover for an `ExternalAction`.
  - Three entry points each become a one-line change:
    `on_file_panel_item_activated` (Enter) → `verb="open"`; `action_view`
    (F3) → `verb="view"`; `action_edit` (F4) → `verb="edit"`.
  - The `_dunder_for_local_file` guard (SQLite-by-magic) and the VFS branch
    (`_open_member_view`) stay **in front** — associations apply only to local
    files, as today.
  - `read_only` per verb: `open`/`edit` → `False`, `view` → `True`.

The fix: `open`/`edit` on an image now route to
`_open_with_handler("image", read_only=…)` instead of the `read_text()` branch.

## Menu & seeding

- New app-level command `assoc.edit`, registered alongside `ai.settings`
  (focus-independent).
- A **"Edit file associations…"** item in the system (`_`) menu.
- Action: if `associations.toml` is missing, the loader seeds a commented
  example (like the first F2 seeding the User Menu), then
  `_open_editor_window(path)` opens it for editing inside dunders. On the next
  Enter/F3/F4 the table is re-read (the file is tiny — read every resolve;
  prefer simplicity over caching).

## Error handling

- Broken TOML / unreadable file → defaults + `notify(warning)`; the app keeps
  working.
- External command fails / program missing → the standard handover output
  (same as User Menu bodies today).
- `image` without `dunders[image]` → fall back to `hex` + notify (existing).
- A file that `auto` would still open as hex (binary/large) — the hex-viewer
  threshold still applies inside the handler.

## Testing

Mirrors `tests/fm/test_user_menu.py`:

- Unit `tests/fm/test_associations.py`: parse TOML (string verb, per-OS table,
  `!` prefix), merge over defaults at `(ext, verb)` granularity, resolve with
  the `auto` fallback, `os_name` selection, broken TOML → defaults.
- Async smoke in `tests/fm/`: Enter on a fake `.jpg` opens the image/hex viewer
  and does **not** raise `UnicodeDecodeError` (regression test for the original
  bug); `assoc.edit` seeds and opens the file.
