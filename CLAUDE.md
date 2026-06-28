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
- `viewer.py` / `hex_viewer.py` / `image_viewer.py` / `csv_viewer.py` /
  `markdown_viewer.py` — F3 viewers. `app._open_editor_window` routes by
  content: images (magic bytes,
  `dunders[image]`/Pillow) → `ImageViewerContent`; `.csv`/`.tsv` → `CsvViewerContent`
  (checked *before* the hex guard so big/UTF-16 CSVs still tabulate);
  `_should_use_hex_viewer` (files >4 MiB or that sniff as binary) → mmap-backed
  `HexViewerContent` so multi-GB files don't slurp into memory; `.md`/`.markdown`
  (small/text, *after* the hex guard) → `MarkdownViewerContent`; everything else
  → the plain `ViewerContent`.
  - `MarkdownViewerContent` picks a render tier by cost (image-free docs):
    `size > 128 KiB` → lazy `_LazyTextView` over an mmap `LineSource` (instant at
    any size; opt-in `[ Render ]` ≤ 1 MiB); else `estimate_blocks ≤ 600` →
    interactive Textual `MarkdownViewer` (+ TOC); else → `rich.markdown.Markdown`
    in one `Static` (fast, no TOC). `__init__` is cheap and never reads a huge
    file into memory; the surface is built on mount. Docs with inline images keep
    the composed renderer regardless of size. Worker threads do not help (GIL),
    so freezes are bounded by the thresholds.
    Docs containing *standalone* local image lines (`![alt](path)` on their own
    line, resolved relative to the file's dir, magic-sniffed, Pillow present)
    switch to a composed renderer: `split_markdown_blocks` splits the source into
    text/image segments, text → `Markdown` widgets and each image → an
    `_InlineImage` (`Static` redrawn on resize) showing **inline ASCII art** via
    the shared `image_to_ascii`/`_fit` converter from `image_viewer.py` (capped
    at `_INLINE_MAX_ROWS`). Remote/missing/non-image srcs stay as text (Textual's
    🖼 placeholder). Toggles: Raw⇄Rendered (`t`) swaps to a scrollable read-only
    source view (no-op for the lazy tier — the lazy view is already the raw
    source); Contents (`c`) shows/hides the heading outline (only for the plain
    `MarkdownViewer` — the composed image renderer has no aggregated TOC,
    so that button is omitted and `viewer` is `None`, surface via `document`).
    The shared `_ToolbarButton.set_label` reflows (`refresh(layout=True)`) so a
    longer label like `[ Rendered ]` isn't clipped to the old width. Accepts a
    local `file_path` or
    in-memory `text`; `from_bytes`/`from_text` build VFS members (no base dir, so
    images stay as text; gated on no NUL bytes in `_open_member_view`).
    `looks_markdown` is the pure extension sniffer.
  - `doc_converter.py` (opt-in `dunders[office]` / markitdown) converts
    `.pdf`/`.docx`/`.pptx`/`.xlsx`/`.epub` → Markdown, shown via
    `MarkdownViewerContent.from_text`. Routed in `_open_editor_window` and
    `_open_member_view` *after* the image branch and *before* the CSV/hex
    branches (binary docs would otherwise open as hex); conversion runs in a worker
    (`_convert_office_async`/`_finish_office`) behind a Converting… modal and
    falls back to the hex viewer on failure or a missing extra. `looks_office`
    is the pure extension sniffer.
  - `CsvViewerContent` is lazy: UTF-8/ASCII CSVs (`_make_csv_viewer` → `from_path`)
    use an mmap `_LineSource` with an *incremental* newline index (instant open at
    any size up to `_CSV_MMAP_SIZE_THRESHOLD` = 2 GiB; only visible rows parsed,
    column widths sampled from the first rows). UTF-16/Excel CSVs decode wholly in
    memory under `_CSV_VIEW_SIZE_THRESHOLD` (32 MiB). Features: Table⇄Raw (Ctrl+T),
    delimiter cycle (`d`), **Ctrl+F substring filter** (frozen header + a fixed
    line-number gutter, original row numbers preserved), horizontal scroll.
  - VFS members (`_open_member_view`): small ones build from in-memory bytes via
    `from_bytes`; a large CSV member (no local path to mmap) streams to a temp file
    in a worker (`_open_large_csv_member`, capped at `_CSV_REMOTE_SIZE_THRESHOLD`),
    then opens via `from_path(owns_file=True)`. Temps live in `<tmp>/dunders/`,
    are unlinked immediately after mmap on POSIX (crash-proof) with an on_unmount
    fallback, and orphans are swept at startup (`_sweep_scratch`).
- `line_source.py` — `LineSource`/`TextSource`/`MmapSource`: random-access lines
  without materialising the whole file (mmap + incremental newline index).
  Shared by the lazy CSV viewer and the lazy Markdown huge-file tier.
- Database dunder (opt-in `dunders[db]` / `dbset`, a SQLAlchemy 2.x wrapper for
  SQLite/Postgres/MySQL). Opened from the `_` menu (Database → connection URL,
  e.g. `sqlite:///f.db`, `postgresql://user@host/db`); connects on a worker
  (`slow` capability). All `dbset`/SQLAlchemy access is isolated in
  `providers/db_access.py` (`DbConn`: tables/indexes/columns/PK, paged
  `fetch`, get/insert/update/delete, raw `query`, JSON record (de)serialization,
  `ReadOnlyError`; mutations through `dbset`, metadata/reads/raw-SQL through
  SQLAlchemy). `providers/db_provider.py` is the `VfsProvider` (`scheme="db"`)
  that maps **tables → directories** and **records → files** so the panel and
  the generic `transfer()` engine work unchanged: root `scan` lists tables
  (`is_dir`, with Rows/Cols `ProviderColumns`) + indexes; entering a table lists
  records as `<pk>.json` (paged at `_DB_PAGE=1000`, sorted by PK, trailing
  `▼ more N…` page entry). `open_read` yields a record's JSON (or an index's
  DDL); `open_write` imports `.json`→insert/`.jsonl`→import, or
  (`overwrite=True`, F4 edit path) update — `db_access.ensure_columns` widens
  the schema (identifiers quoted via the dialect preparer) when an edit adds a
  field. `delete` removes records by PK; a single-part loc naming a real table
  does `DROP TABLE` (`db_access.drop_table`, identifier quoted via the preparer);
  other non-record targets (indexes, `_page` pseudo-entries) are no-ops. Whole
  tables copy out as one `<table>.jsonl` via the generic
  `export_as_file` hook in `vfs_engine.py` (a source provider opts a "directory"
  into single-file export; `is_dir(table)` stays True for navigation), so a
  table-`move` is intentionally copy-only (source not deleted). The export
  streams **lazily** — `_TableExportReader` serializes one paged `fetch` per
  `read()` so a multi-GB table never buffers in memory and the copy bar advances
  per chunk (an eager in-memory build used to freeze the worker at 0%, then jump
  to 100%). `export_size_hint` (count + a 100-row sample) gives `_measure` a
  cheap byte denominator so it short-circuits an export-capable dir instead of
  re-paging the whole table just to size it. The reverse direction — importing a
  local `.jsonl` into the db — **streams**: `_DbWriter` parses complete lines out
  of each write and `_JsonlImporter` inserts them in `_BATCH`-sized
  `insert_many` round trips as bytes arrive, so a multi-GB import never buffers
  in memory and the copy bar tracks real insert progress (the old writer buffered
  the whole file — bar to 100% on buffering — then did every insert row-by-row in
  `close`, a long freeze at 100%). `_JsonlImporter` decides PK stripping ONCE
  (defaulting to `id` for a not-yet-existing table) so every row is treated the
  same: mixing an explicit-PK first insert with stripped later ones left a
  Postgres serial sequence un-advanced and the next id collided. Plain inserts
  strip the PK so cross-DB copies don't collide (re-import into the same table
  duplicates rows). When a file is copied INTO a db panel the copy dialog is
  **editable** (not the append-only archive prefill): it prefills the connection
  locator with a table segment (`db://<root>!/<src-stem>`) and the user edits the
  part after `!/` to name/rename the target table; `app._db_dest_table` parses
  that trailing segment (extension stripped) and the import is routed via
  `transfer(rename_to="<table>.jsonl")` so `_DbWriter` picks up the name. The SQL
  console (`db_console.py`, `DbConsoleContent`) is a
  `TextArea` editor over a `DataTable` grid, opened by the provider's `SQL
  console` action (Alt+S) bound to the panel's connection; `run_sql` dispatches
  on `_is_pageable` (SELECT/WITH/VALUES): a row-returning statement is
  **paginated** (`DbConn.query_page` wraps it in `SELECT * FROM (<sql>) AS …
  LIMIT n+1 OFFSET m`, `_PAGE`=200 rows/page; the `+1` row signals `has_next`
  without a COUNT), with toolbar `◀ Prev`/`Next ▶` buttons (shown only for the
  directions that lead somewhere) and a `Page N · rows a–b` status; everything
  else (and a SELECT whose wrapping fails, e.g. duplicate output columns) falls
  back to `_run_unpaged` → `DbConn.query` (cap 1000, rowcount for writes).
  History is recorded once per run, not per page flip; the editable target
  (`_compute_edit_target`) is recomputed per page so cell-save works on any
  page. `_render_grid` no-ops when unmounted so it is unit-testable headless. The
  console accepts an `initial_sql` prefill: **F3/View on a table** opens it with
  `DbConn.select_all_sql` (`SELECT * FROM <table>`) and **F4/Edit on a table**
  with `DbConn.create_table_ddl` (the reflected `CREATE TABLE` via SQLAlchemy
  `CreateTable`, followed by a `CREATE INDEX` per secondary index so the DDL
  fully describes the table); both prefill only (the user runs with Ctrl+R). `action_view`/
  `action_edit` route a `db.kind == "table"` entry to `_open_db_table_query`
  *before* their `is_dir` no-op guard (a table is a directory); F4 uses
  `_selected_db_table_locs` so a **multi-selection** of tables concatenates all
  their DDL into one console (in panel order). The prefill is
  tab-expanded (`expandtabs`) before it reaches the editor — SQLAlchemy's
  `CreateTable` indents with raw `\t`, and a literal tab advances to a terminal
  tab stop, shifting the line so the window's right border lands in the wrong
  column. The SQL pane keeps its fixed 5-row height (a long DDL scrolls within;
  the splitter resizes it) rather than growing to fit, which would push the
  splitter and result grid off the bottom unrecoverably. Every
  `run_sql` (success *and* error) appends to a per-connection **query history**
  (`config/sql_history.py`, a 0600 `sql_history.json` keyed by the normalized
  connection root, newest-first, move-to-top dedup, capped at 200). The
  `[ History ]` toolbar button / `Alt+H` / the **Database menu's `SQL history`**
  (provider action `db.history`, which opens a console then pops the picker on top)
  open `SqlHistoryDialog` — a modal picker (mirrors `BookmarksDialog`) that is **callback-driven, not message-based** (so
  the wiring stays in `db_console`, not `app.py`): Enter recalls a past query into
  the editor (replacing the buffer), the ✗ column / Delete removes an entry, and
  `[ Clear all ]` wipes the connection's history. The dialog dismisses itself by
  posting `Window.Closed` (handled by `Desktop.on_window_closed`), since
  `ModalWindow.Dismissed` has no handler. Enter/click on a result-grid cell
  (`on_data_table_cell_selected`) opens `CellEditDialog` — a modal showing the
  cell's **full** (un-`_clip`ped) value in the app-native editor, with a
  `Markdown ⇄ Text` button that swaps the editor for a read-only
  `rich.markdown.Markdown` preview of the current text. `Save` writes the edit
  back with an `UPDATE` — but only when the result is an *updatable single-table
  SELECT*: `db_access.single_table_target` (pure, conservative — rejects
  JOIN/UNION/GROUP BY/comma-joins/sub-queries) names the one table, and
  `run_sql` sets `_edit_table`/`_edit_pk` only when that table exists and its PK
  is among the result columns (so a row can be located). `_resolve_cell` then
  gates each cell: read-only conn, non-single-table result, or a
  computed/aliased column (not in `DbConn.columns`) → view-only with the reason
  shown and no Save button. `_save_cell` coerces the edited text back to the
  original value's Python type (`_coerce_cell`; bool→int→float→str), updates by
  PK, and mirrors the change into `last_rows` + the visible grid via
  `update_cell_at`. Like the history picker the dialog posts `Window.Closed`
  itself. dbset on SQLite returns dict/JSON
  columns as JSON *strings* (recover via `json.loads`).
- `commandline.py`, `keymap.py`, `scan.py`, `sort.py` — supporting bits.

### 2.5 `dunders.ai` — LLM foundation

Cross-cutting LLM layer, app-agnostic like `windowing`: **stdlib-only, no vendor
SDK** — every provider talks its REST API directly over `urllib`
(`providers/_http.py`: `post_json`/`get_json`/`aiter_stream_lines`, blocking calls
pushed off the loop with `asyncio.to_thread`), so `pip install dunders` is enough
(the `dunders[ai]` extra is empty, kept only so the name stays valid). Never
imports `fm`/`windowing`. Public surface re-exported through `dunders.sdk`; the
`dunders.ai.*` modules are private. The single runtime object is **`LlmService`**
(`app.ai`, and `api.ai` for plugins — mirrors `api.vfs`/`api.events`).

- **Provider abstraction** (`provider.py`, by analogy to `VfsProvider`): a
  `capabilities: frozenset` (`chat/stream/tools/vision/json`) + an async surface
  (`chat`/`stream`/`list_models` — the latter hits the provider's models endpoint,
  `client.models.list()` for Anthropic and every OpenAI-compatible backend) + a
  `config_schema()`/`from_config()` pair so the wizard builds a form and
  constructs any provider generically. HTTP status codes map to the `AiError`
  hierarchy (`_http.map_status`: `AuthError`/`RateLimitError`/`AiTimeoutError`/
  `ProviderUnavailable`/…; plus `BudgetExceededError`/`NoAiZoneError`/
  `CapabilityError`). Providers (`providers/`), all over stdlib HTTP:
  `AnthropicProvider` (native Messages REST `/v1/messages`+SSE, `x-api-key`,
  default `claude-opus-4-8`; drops `temperature`, maps `effort` to
  `output_config` — Opus 4.x 400s on those); `OpenAICompatProvider`
  (`/chat/completions`+`/models`, `Authorization: Bearer`, one class for every
  OpenAI-compatible endpoint); `AzureOpenAIProvider` (subclass: `api-key` header,
  deployment-scoped URL + `api-version`, fields `azure_endpoint`/`api_version`/
  `deployment`); `OllamaProvider` (local, `is_local=True` → no cloud egress;
  Ollama's native `/api/tags`+`/api/chat`); `FakeProvider` (network-free, the
  test workhorse + fallback). `presets.py` turns groq/nvidia/deepseek/qwen into
  pre-filled configs over `OpenAICompatProvider` — one class, many vendors.
- **Roles** (`config.py`): `default/cheap/strong/local/vision`, a role→
  `{provider, model}` map with `inherits` chains. Seed defaults: `cheap`=
  ollama/`gemma4:e2b`, `strong`=groq/`gpt-oss-120b`, `default`→`cheap`. Consumers
  request by role (`await app.ai.chat(msgs, role="strong")`); `provider=`/`model=`
  override. Stored in the `ai` section of `config.json`; secrets are NOT here.
- **Secrets** (`secrets.py`): `SecretResolver` resolves **env-first**, then a
  0600 `secrets.json` next to `config.json` — an env var always overrides the
  file. Secret config fields store the canonical env-var *name* (e.g.
  `ANTHROPIC_API_KEY`); the value lives in the file or the environment.
- **Guardrails** (`guardrails.py`), wired by `LlmService` around every call:
  `TokenMeter` (per-call `Usage` + `pricing.py` cost + optional USD budget →
  `BudgetExceededError`; emits `ai.call.done` on the `EventBus`); `ResponseCache`
  (TTL+LRU, keyed on provider+model+messages+params, off for streaming/`cache=
  False`); `Redactor` (conservative regex PII/secret redaction applied only
  before a *cloud* call — local/Ollama exempt); `is_ai_allowed(path, cloud=…)`
  (no-AI zones via `noai_globs` or a `.dunders-noai` marker searched upward).
- **Async-native.** `chat` is `async`; `stream` yields `StreamEvent`s
  (`TextDelta`/`ToolUseDelta`/`MessageDone`). `run_sync(coro)` bridges worker
  threads to the app loop (`asyncio.run_coroutine_threadsafe`); the loop is
  attached in `app.on_mount`.
- **Wizard** (`fm/ai_config_dialog.py`, `AiConfigDialog`): opened from the `_`
  menu → "AI / LLM settings…" (`ai.settings` command). Project TV idiom, not
  stock Textual — roles are a row of `ShadowButton` tabs, the provider is a
  `◀ name ▶` cycler, and the surface paints itself from the **palette** via
  `apply_theme()` (`_get_palette()` walks ancestors for `.palette`; CSS is
  layout-only, no Textual `$accent`/`$boost`) so a theme switch restyles it. The
  provider drives a form built dynamically from `config_schema()` (so Azure's
  different fields need no special-casing); `[ Models ]` fetches the provider's
  live model list (`list_models`) into a themed `ModelPickerDialog` whose
  selection fills the model field (manual entry still works for unlisted ids,
  e.g. custom Ollama tags); `Test` does a live one-shot call;
  `Save` writes non-secret fields to the config and secrets to `secrets.json`,
  then `LlmService.reload()`s. Dismisses by posting `Window.Closed` (like
  `SqlHistoryDialog`).
- **First consumer — NL→command** (`fm/nl_command.py`): a `#`/`?` prefix in the
  command line (or AI-command mode, toggled by `Alt+A` → `[AI]` hint) routes the
  line to `app._suggest_command` (worker): `build_prompt` → `app.ai.chat(role=
  "default")` → `parse_suggestion` (reads `CMD:`/`WHY:` markers, provider-agnostic
  fallback) → `NlCommandDialog` (Run / Edit / Cancel). Run reuses
  `_run_handover_command` (deferred past the dialog's `Window.Closed` so the modal
  guard doesn't block it); Edit drops the command into the command line.
  The F12 Agent is still a stub.
  Specs: `docs/superpowers/specs/2026-06-22-{llm-foundation,nl-to-command}-design.md`.

### 2.6 `dunders.forms` — form editor

App-agnostic stdlib-only core, never imports `fm`/`windowing`. **Schema format**
(variant A, TOML/YAML/JSON) → `FormSpec`/`FieldSpec` via `schema.py`; `$`-prefixed
meta keys (`$required`, `$help`) are parsed and removed, leaving clean field names.
`looks_form` detects `.form.json`/`.form.toml`/`.form.yaml`. **Type registry** (`types.py`):
`str/int/real/date/combo/ecombo/clipboard/selected_text/bool/text` → widget kind +
validator + converter; `date` uses optional `dunders[forms]`/`dateparser` for ISO 8601
output, passthrough string when absent. `clipboard` auto-fills from system clipboard
(subprocess, soft-degrade on unavailable); `selected_text` auto-fills from the active
editor's selection (supplied by the app at launch). **Validation** (`result.py`):
`validate_all` type-checks and coerces; `build_result` merges defaults + validated
values into a clean result dict. `dunders/fm/form_dialog.py` — `FormDialog(WindowContent)`:
scrollable, blocking validation on GO, Cancel; ecombo (no Textual editable combobox) = a
`Select` widget + "✎ Custom…" swap using `Select.NULL` sentinel. `app.forms` (`FormsService`,
mirrors `app.ai`): `await app.forms.ask(spec, *, selected_text=None)` → awaitable Future
resolving to result dict or None. **Launch**: File menu → "Form editor…" (`form.open`,
hybrid: `.form.json` under cursor else prompt for path); F3/F4/Enter on a `.form.json`
open the form; result written to `<stem>.result.json` next to the schema (scenario 2) or
returned via the API (scenario 1, in-memory members not written). Spec/plan:
`docs/superpowers/specs/2026-06-27-form-editor-design.md`.

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

- **Theming/laying out a Textual dialog?** Read
  [docs/textual-ui-cookbook.md](docs/textual-ui-cookbook.md) FIRST — it captures
  the recurring fixes (palette `apply_theme` idiom, `Input`/`Select`/`Checkbox`/
  `TextArea` border & focus quirks, `SelectOverlay` z-order in modals, bracket
  checkbox, field spacing, `ShadowButton`, and headless verification) so dialog
  polish isn't rediscovered each time.
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
- File associations: `config_dir()/associations.toml` (`~/.config/dunders/`,
  honours `XDG_CONFIG_HOME`); single global TOML with per-extension `[ext]`
  sections and three verbs — `open` (Enter), `view` (F3), `edit` (F4). Value =
  a built-in handler name (`auto editor viewer hex image csv markdown office
  database`; unknown → `auto`) or `!external command` (User Menu macros
  `%f %d %t %s %F %D %x %b %%` via the handover); a verb may be a plain string
  (all OSes) or an `[ext.verb]` table with `macos`/`linux`/`windows`/`default`
  keys. Built-in defaults (`BUILTIN_DEFAULTS`) merge under the user file at
  (ext, verb) granularity — images/csv/md/office open correctly out of the box,
  fixing the old `.jpg`-on-Enter `read_text()` crash (undecodable files fall
  back to the hex viewer). `Command` menu → "Edit file associations…" (`assoc.edit`)
  seeds an example and opens it. See `dunders/fm/associations.py` (pure
  parser/resolve), `associations_loader.py` (I/O). v1: no per-project override
  file; interactive `%{Prompt}` macros are not collected for association external
  commands.
- Command-line history: persisted to `config_dir()/history` (honours
  `XDG_CONFIG_HOME` like the rest of the config). **Alt+H** opens a mc-style
  popup (`fm/cmd_history_dialog.py`, `CommandHistoryDialog`) listing past entries
  newest-first; Enter/click recalls one into the command line. Registered as the
  app-level command `cmd.history` (so the SQL console's focus-scoped `db.history`
  on Alt+H still wins when that window is focused — `hotkey_lookup` checks
  focus-scoped commands before app-level). Freeing Alt+H moved the panel's
  **Show hidden files** toggle to **Alt+.** (`alt+full_stop`; mc parity). Up/Down
  in the command line keep step-through history / panel navigation as before.