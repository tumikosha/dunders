# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Workflow rules

- **Never `git push` without a direct, current instruction.** Commit only when
  asked; after committing, stop and let the user decide whether/when to push. A
  prior "push it" applies only to that batch — it does not carry over to later
  commits.

## Project

`dunders` — terminal text editor + Norton Commander/mc-style file manager built on
[Textual](https://textual.textualize.io/), with a Turbo Vision-inspired
windowing layer, code folding, macros, and an embedded CLI/agent mode. Python
≥3.12, single binary `dunders` exposed via `dunders.main:main`.

## Commands

Project uses `uv` (lockfile present) but standard `pip`/`pipx` works.

```bash
# Install in editable mode (creates `dunders` script in PATH)
pipx install --force -e .            # see install_global.sh
# or for dev with the test extra
uv sync --extra dev                  # or: pip install -e '.[dev]'

# Run the app
dunders                                  # fm-mode (two panels)
dunders path/to/file                     # editor on a file
dunders path/to/dir                      # fm seeded at dir
dunders --cli                            # agent/CLI mode

# Run the windowing demo (separate executable inside the repo)
python -m dunders.windowing.demo

# Tests
pytest                               # full suite (pytest-asyncio in auto mode)
pytest tests/fm/test_file_panel.py   # one file
pytest -k fold_engine                # by keyword
pytest tests/windowing/test_editor_content.py::TestName::test_x  # one test

# Lint
ruff check
```

`pyproject.toml` pins `testpaths = ["tests"]` and `asyncio_mode = "auto"`, so
async test functions don't need explicit `@pytest.mark.asyncio`.

## Architecture

The codebase is split into three concentric layers. Read in this order:

### 1. `dunders.windowing` — Turbo Vision-style framework on Textual

Generic, app-agnostic windowing system. Public API is re-exported from
`dunders/windowing/__init__.py`; never reach into submodules from outside.

- `Desktop` (`desktop.py`) hosts a stack of `Window`s with z-order and
  `focused_window` tracking. `WindowManager` provides tile/cascade/maximize.
- `Window` (`window.py`) wraps a `WindowContent` plus `Decorations` (border
  style, close/zoom boxes, resize grip).
- `WindowContent` (`content.py`) is the abstract content surface a window
  hosts. Subclasses publish hotkeys and menu items via `get_commands()`
  returning `WindowCommand`s — this is the focus-scoped command system.
- `CommandRegistry` / `CommandDispatcher` / `CommandRouter` (`commands.py`)
  collect `WindowCommand`s from the focused window and route both keystrokes
  and `MenuItem(command_id=…)` references through a single dispatcher.
  `app.py` registers focus-independent commands; panels and editors register
  focus-scoped ones.
- `MenuBar` + `Dropdown` + `StatusBar` are pure widgets driven by the
  dispatcher. `CommandPaletteContent` (Ctrl+P) lists all available commands.
- `windowing/core/` is editor-agnostic primitives: `TextBuffer`,
  `FoldEngine` (+ `IndentFoldRule`), `MacroRecorder`, `MacroStorage`,
  search.
- `windowing/editor/` is the embeddable editor: `EditorWidget` (focusable
  text widget) and `EditorContent` (the `WindowContent` wrapper with split
  view, search panel, replace, macro dialog).
- `windowing/themes/` loads palettes from YAML (`dunders/themes/*.yaml`) plus the
  `modern_dark` default.
- `windowing/demo/` is a standalone `python -m dunders.windowing.demo` runner
  used to exercise the framework in isolation; it does NOT pull in `dunders.fm`.

### 2. `dunders.fm` — file-manager domain

NC-style panels and file ops, built on top of `windowing`.

- `file_panel.py` — `FilePanel(WindowContent)`: dual-pane listing, sort,
  multi-select, quick-search.
- `actions.py` — pure file operations (`copy_paths`, `move_paths`,
  `delete_paths`, `mkdir_at`) returning `OpResult`. They take an
  `on_progress` callback and a `cancel_event`, and are always invoked from a
  worker thread by `app.py` (see `_run_copy_move`, `_run_delete`).
- `dialogs.py` — `ConfirmDialog`, `InputDialog`, `CopyMoveDialog`,
  `NewFileDialog`, `ProgressDialog`. All use Textual messages
  (`*.Submitted` / `*.Cancelled` / `*.Result`) carrying a typed `context`
  payload (see `CopyMoveRequest`/`DeleteRequest`/`MkdirRequest` etc. defined
  in `app.py`); the app handler `isinstance`-dispatches on that context
  rather than a stringly-typed `_op` field.
- `viewer.py` / `hex_viewer.py` — F3 viewers; `app._should_use_hex_viewer`
  switches to mmap-backed `HexViewerContent` for files >4 MiB or files that
  sniff as binary, so multi-GB files don't slurp into memory.
- `commandline.py`, `keymap.py`, `scan.py`, `sort.py` — supporting bits.

### 3. `dunders.app` — top-level shell

`DundersApp(App)` composes `MenuBar + Desktop + CommandLine + StatusBar` and
mounts the initial window set based on `launch_mode`
(`fm`/`editor`/`cli`). It owns:

- The single `CommandRegistry` + `CommandDispatcher` + `CommandRouter`.
- All NC F-key actions (`action_view`/`action_edit`/`action_copy`/etc.) and
  the modal-dialog plumbing.
- Menu rebuild — `_recompute_menu_bar` filters the focus-scoped `Editor`
  menu in/out depending on whether an `EditorContent` window is focused;
  `_refresh_windows_menu` rebuilds the dynamic `Windows` menu from
  `desktop.windows` on every activation.
- Layout — `_apply_default_layout` tiles the two panels on resize. The
  initial call is deferred via `call_after_refresh` because `Desktop.size`
  is 0×0 at `on_mount`.
- Focus restoration — `_pre_menu_focus`/`_pre_menu_window`/
  `_pre_modal_panel_id` are saved before activating the menu or a modal
  dialog so the dismiss path lands focus back on the right widget.

### Important conventions / gotchas

- **NC F-keys are panel-scoped, not app-bindings.** F3/F4/F5/F6/F7/F8 are
  registered by `FilePanel.get_commands()` and routed via the focused window
  through `CommandRouter`. Editor hotkeys (Save/Find/Split/Fold) come from
  `EditorContent.get_commands()`. Only mechanical keys (F9 menu, F10 quit,
  Esc, Tab, Alt+L/R, Shift+Tab) live in `DundersApp.BINDINGS`. Don't add a
  panel/editor action to `BINDINGS` — both paths firing will call the action
  twice.
- **Modal gating.** Almost every `action_*` calls `_has_active_modal()` first
  and bails so dialogs keep keyboard focus. New actions must do the same.
- **Worker threads must marshal back to the UI thread** via
  `self.call_from_thread(...)`. See `_run_copy_move`/`_run_delete` for the
  established progress-dialog pattern.
- **Closing a modal:** always go through `_close_modal(dialog)`. It walks up
  to the enclosing `ModalWindow` (not just any `Window`) so a stray bubble
  from an inner `Input` can never remove a panel by mistake.
- **Hex viewer threshold** is `_HEX_VIEW_SIZE_THRESHOLD = 4 MiB`; binary
  detection is the cheap "first 8 KiB contains NUL" heuristic in
  `_looks_binary`.
- **EditorContent vs `_FocusableEditorContent`.** The base `EditorContent` is
  a non-focusable wrapper; the focusable widget is `_editor`. `app.py`
  subclasses to `_FocusableEditorContent` so editor windows accept keys
  immediately on mount instead of needing a click first.

### Tests

`tests/` mirrors the source layout (`tests/fm/`, `tests/windowing/`, plus the
top-level fold/macro/search/buffer tests). Pure-logic modules
(`fold_engine`, `indent_fold`, `macro`, `actions`, `search_core`) have unit
tests; widgets and the app shell have async smoke/integration tests
(`test_smoke.py`, `test_app_skeleton.py`).

### Configuration

- `dunders/config/defaults.py` — fold rules, default key bindings, default
  settings (tab size, line numbers, fold-by-indent, etc.).
- `dunders/config/user_config.py` — persisted user preferences in
  `$XDG_CONFIG_HOME/dunders/config.json` (stdlib JSON, atomic best-effort
  writes, fault-tolerant reads). Currently stores the selected `theme`;
  `app._resolve_initial_theme()` reads it at startup and `_apply_theme(...,
  persist=True)` writes it on a user switch. Tests isolate it via an autouse
  `XDG_CONFIG_HOME` fixture in `tests/conftest.py`.
- Theme palettes load from TOML: the built-in `modern_dark` plus example
  themes in `dunders/windowing/themes/examples/*.toml`, discovered by
  `list_themes()` and parsed by `dunders/windowing/themes/loader.py`. The
  Options menu / `theme.cycle` (Ctrl+T) are built dynamically from that list.
  A complete theme defines all 42 roles in `modern_dark` (older `turbo_blue`
  / `midnight_commander` examples are partial at 21 roles).
- Per-`vibe/general.md`, user hotkeys/macros are also intended to live under
  `~/.config/dunders/` (those loaders not implemented yet).
- User Menu (F2): mc/far-style command menu defined in Markdown. Loaded from
  `./.dunders.menu.md` (active panel dir) merged over `~/.config/dunders/menu.md`.
  `##` = section, `###` = entry with optional `(x)` hotkey, body = first fenced
  code block. Macros: `%f %d %t %s %F %D %x %b %%` and interactive `%{Prompt}`.
  Bodies run through the handover (panel cwd). F4 in the dialog edits the source
  file; first F2 with no file seeds an example. See `dunders/fm/user_menu.py`
  (pure parser/macros), `user_menu_loader.py` (I/O), `user_menu_dialog.py`
  (modal).