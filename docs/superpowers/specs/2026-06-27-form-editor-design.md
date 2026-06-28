# Form editor — design

**Date:** 2026-06-27
**Branch:** `feat/form-editor`
**Status:** approved design, pre-implementation

## Goal

A form editor: a JSON schema describing fields drives a scrollable form with a
`GO` button. The user edits the fields and presses `GO`; the output is a
`{key: value}` JSON object with typed, validated values.

Two consumers:

1. **Programmatic API** (agent / plugins) — `await app.forms.ask(spec, *,
   selected_text=None) -> dict | None`. The first real consumer (a `translate`
   command) is **out of scope for this iteration** — only the engine + API +
   launch points ship now.
2. **`.form.json` viewer** — the user opens a schema file in the file manager
   (menu or F3/F4); on `GO` the result is written to `<stem>.result.json` next
   to the schema.

## Architecture & module layout

Mirrors `dunders.ai`: a domain-clean, **stdlib-only** core (`dunders/forms/`,
never imports `fm`/`windowing`) plus a UI layer in `fm/`.

- **`dunders/forms/` — core (app-agnostic):**
  - `schema.py` — parse the schema (format below) into a typed model:
    `FormSpec(title, description, fields: list[FieldSpec])`,
    `FieldSpec(key, type, label, options, default, required)`. Pure functions,
    unit-testable without Textual. Unknown `type` → a clear parse error.
  - `types.py` — the field-type registry: `str`, `int`, `real`, `date`,
    `combo`, `ecombo`, `clipboard`, `selected_text`, `bool`, `text`. Each type
    knows how to validate an input string, how to convert it to its output JSON
    value, and which widget renders it.
  - `context.py` — autofill sources: `read_clipboard()` (subprocess, soft
    degradation) and the `selected_text` value (supplied by the caller, not
    guessed by the core).
  - `result.py` — assemble the `{key: value}` result + serialization.
  - `__init__.py` — public façade re-exporting the surface.
- **`dunders/fm/form_dialog.py` — UI:** `FormDialog(WindowContent)` — a
  scrollable form of fields + a `GO` button, following the project TV idiom
  (palette via `apply_theme()`, like `AiConfigDialog`/`FindFileDialog`). Posts
  `FormDialog.Submitted(result_dict)` / `FormDialog.Cancelled`.
- **Runtime object `app.forms`** (mirrors `app.ai`) — exposes
  `async ask(spec, *, selected_text=None) -> dict | None`.

The `dateparser` dependency and clipboard reading are isolated in the core with
soft degradation.

## Schema format (variant A)

JSON object; key order = field order. A field value is either a type-string or
an object.

```json
{
  "$title": "Translate text",
  "$description": "optional",
  "text":    {"type": "selected_text", "label": "Source"},
  "api_key": "clipboard",
  "target":  {"type": "combo",  "options": ["English", "German", "Russian"], "default": "English"},
  "model":   {"type": "ecombo", "options": ["fast", "quality"]},
  "max_len": {"type": "int", "default": 500},
  "temperature": "real",
  "deadline":    {"type": "date", "label": "Due"},
  "agree":       {"type": "bool", "default": false},
  "notes":       {"type": "text"},
  "name":        {"type": "str", "required": true}
}
```

- `$`-prefixed keys are **meta**, not fields: `$title` (form window title),
  `$description`. They never appear in the result.
- Object fields understand: `type`, `label` (defaults to the key), `default`,
  `required` (bool), `options` (combo/ecombo).
- A bare string value is shorthand for `{"type": "<string>"}`.

### Field-type registry

| type | widget | output JSON | validation |
|---|---|---|---|
| `str` | Input | string | `required`: non-empty |
| `int` | Input | number (int) | parses to int |
| `real` | Input | number (float) | parses to float |
| `date` | Input | ISO string | `dateparser.parse` ≠ None; passthrough string if `dateparser` absent |
| `combo` | Select (fixed) | string | value ∈ options |
| `ecombo` | Select + "✎ Custom…" | string | free entry allowed via the Custom path |
| `clipboard` | Input, prefilled from system clipboard | string | as `str` |
| `selected_text` | TextArea (multiline), prefilled from editor selection | string | as `str` |
| `bool` | Checkbox | `true`/`false` | — |
| `text` | TextArea (multiline) | string | `required`: non-empty |

`clipboard`/`selected_text` are ordinary string fields with an **autofill
source**; the user may edit over the prefilled value.

`dateparser` ships as an optional extra `dunders[forms]`. When installed, a
`date` field validates via `dateparser.parse` and outputs an ISO 8601 string.
When absent, the field behaves as a plain string (no recognition; the user's
text passes through unchanged) — soft degradation, matching how
`dunders[db]`/`dunders[office]`/`dunders[image]` are optional.

## Form rendering, scroll, validation (UI)

`FormDialog(WindowContent)` in `fm/form_dialog.py`, following the project idiom
(palette via `apply_theme()` like `AiConfigDialog`):

- **Frame:** title (`$title`) → `VerticalScroll` of field rows → a fixed bottom
  button row `[ GO ]` `[ Cancel ]`. The form scrolls; the buttons stay pinned.
- **Field row:** `label:` + the widget + an error line below it (hidden while
  valid).
- **`ecombo`:** a `Select` whose options include a sentinel "✎ Custom…" entry;
  choosing it swaps the widget to a plain `Input` for free entry. (Textual has
  no editable combobox out of the box.)
- **Autofill on mount:** `clipboard` fields are filled from
  `context.read_clipboard()`; `selected_text` from the supplied value. Done when
  the form is built.
- **Validation (blocking):** on `GO`, each widget runs its type's validator;
  invalid fields get a red border + error text, focus jumps to the first
  problem, and the form does **not** close. `required` empties are errors too.
  Once everything is clean, the typed result is assembled and `Submitted(result)`
  is posted.
- **Closing:** via `_close_modal` / posting `Window.Closed`, like other modals.
  Esc = Cancel.
- **Navigation:** Tab / Shift+Tab between fields (FocusChain); Enter in a
  single-line field advances to the next field rather than submitting.

## Launch points & autofill context

All three paths funnel into `app._open_form(spec, *, schema_path=None,
selected_text=None)`.

1. **Menu `File` → "Form editor…"** (`command_id="form.open"`, hybrid):
   - `.form.json` under the cursor → use it as the schema.
   - Otherwise → an `InputDialog` for the schema path.
   - `selected_text` = the active editor's selection if an editor is focused
     (`buffer.get_selected_text()`), else empty.

2. **F3/F4 on a `.form.json`** in a panel:
   - In `_open_editor_window` / `_open_member_view`, a new branch: extension
     `.form.json` (pure sniffer `looks_form`) → parse → `FormDialog`. Placed
     **before** the hex/markdown branches (it is valid JSON text, which would
     otherwise route to the plain viewer).
   - F3 (view) and F4 (edit) behave identically for `.form.json` (a form has no
     view-vs-edit mode). A schema parse error shows a message dialog instead of
     a form.

3. **Programmatic API** — `await app.forms.ask(spec, *, selected_text=None) ->
   dict | None`. Returns the result dict, or `None` on Cancel. Writes nothing to
   disk — that is the consumer's concern.

**Autofill context** (`forms/context.py`):
- `read_clipboard()` tries `pbpaste` (macOS) / `wl-paste` / `xclip -o` /
  `xsel -b` (Linux) / `powershell Get-Clipboard` (Windows) via subprocess with a
  timeout; a missing tool or error → `""`.
- `selected_text` is **supplied by the caller** (the app knows the active
  editor), keeping the `forms/` core clean.

## Result & writing

- **Assembly** (`forms/result.py`): from the valid widgets, build `{key: value}`
  in schema field order; type converters yield native JSON types
  (`int`/`float`/`bool`/`str`, date → ISO string). `$`-meta keys are excluded.
- **Write (scenario 2):** on `GO`, write `<schema-stem>.result.json` next to the
  schema (atomic best-effort like `user_config`, 2-space indent, UTF-8), then a
  brief status-bar notice. If the schema has no path (in-memory / VFS member),
  show the result in a `ViewerContent` / copy it — do not write a file.
- **Return (scenario 1):** `app.forms.ask()` resolves a `dict` (or `None` on
  Cancel) and writes nothing.

## Testing

Mirrors `tests/` (`tests/forms/` + `tests/fm/`):

- **Pure core (unit, no Textual):** `schema.py` (variant A strings/objects, meta
  keys, unknown type → error, `required`/`default`/`options`), `types.py`
  (validator + converter per type, date with `dateparser` and the passthrough
  fallback), `result.py` (typed JSON, order, `$`-key exclusion),
  `context.read_clipboard` (mocked subprocess: success / missing tool / timeout
  → `""`).
- **UI (async smoke):** mounting `FormDialog`, `clipboard`/`selected_text`
  autofill, `ecombo` → "✎ Custom…" swaps to Input, `GO` with an invalid field
  does not close and highlights, `GO` with valid fields posts `Submitted` with
  the right dict, `.result.json` write.
- **Routing:** the `looks_form` sniffer; F3/F4 on `.form.json` opens
  `FormDialog`, not the hex/markdown viewer.
- **API:** `app.forms.ask(spec, selected_text=...)` returns a dict on `GO` and
  `None` on Cancel (headless form run).

## Out of scope (this iteration)

- The `translate` command (a future consumer of `app.forms.ask`).
