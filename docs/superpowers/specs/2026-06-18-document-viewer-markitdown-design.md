# Document viewer (F3) via markitdown → Markdown

**Date:** 2026-06-18
**Status:** Approved (design)

## Goal

Open document formats (PDF and common office formats) with F3, by converting
them to Markdown via [`markitdown`](https://github.com/microsoft/markitdown)
and displaying the result through the **existing** `MarkdownViewerContent`. No
new viewer widget — we reuse what we already have.

## Scope

Formats routed through the converter (by extension):

- `.pdf` — primary use case
- `.docx` — Word
- `.pptx` — slide text
- `.xlsx` — sheets → GFM tables (rendered by the Markdown viewer)
- `.epub` — books

Explicitly **out of scope** (handled elsewhere or not worth converting):

- `.html`, `.csv`, `.json`, `.xml` — already readable as text or have a
  dedicated viewer (CSV). Not routed through the converter.
- `.jpg`/`.png` etc. — already intercepted earlier by `ImageViewer`.
- Audio (mp3/wav) — markitdown does API-based transcription; different feature,
  not for F3.
- `.odt` / legacy `.doc` — not in v1 (can be added to `OFFICE_SUFFIXES` later).

## Engine & licensing

`markitdown` (MIT, Microsoft). Chosen over `pdfplumber`/`pypdf` because it
covers PDF **and** office formats through one converter, and over
`pymupdf4llm` because PyMuPDF is AGPL (the project deliberately keeps the core
permissive and isolates heavier/non-permissive deps as opt-in extras).

Packaged as an **opt-in extra** (mirrors `image`/`sftp`):

```toml
office = ["markitdown[pdf,docx,pptx,xlsx]>=0.0.1"]
```

When the extra is absent: `notify("Install dunders[office] to view documents")`
and fall through to the existing hex/text path.

## Architecture

### New module: `dunders/fm/doc_converter.py` (pure logic)

Mirrors the `looks_markdown` / `image_to_ascii` separation so the sniffer and
conversion are unit-testable independent of the app shell.

- `OFFICE_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".epub")`
- `looks_office(name: object) -> bool` — cheap, name-only extension check.
  No I/O, no markitdown import. Unit-tested without the extra installed.
- `MARKITDOWN_AVAILABLE: bool` — module-level flag from a guarded import
  (same shape as `PILLOW_AVAILABLE`).
- `class ConvertError(Exception)` — wraps any failure from markitdown so
  callers catch one type.
- `convert_to_markdown(source, name) -> str` — accepts a `Path` **or** `bytes`
  (+ a `name` for the extension hint when given bytes). Runs
  `MarkItDown().convert(...)` and returns the Markdown string. Raises
  `ConvertError` on failure. Raises (or is guarded by callers checking
  `MARKITDOWN_AVAILABLE`) when the extra is missing.

### Display: reuse `MarkdownViewerContent`

Mount `MarkdownViewerContent.from_text(name, md)`. No changes to
`markdown_viewer.py`.

**Known compromise:** images embedded in the document do not render as ASCII —
`from_text` has no base directory to resolve image paths, so image lines stay
as Textual's 🖼 placeholder. This is the same compromise already accepted for
VFS members and is acceptable for v1.

### Conversion runs in a worker thread

markitdown is blocking and loads the whole document into memory; a PDF can take
seconds. So conversion must not block the UI thread.

- Show a lightweight "Converting…" indicator while the worker runs.
- Convert in a worker thread, marshal the result back via
  `self.call_from_thread(...)`, then mount the `Doc: {name}` window.
- Follow the established worker pattern (`_run_copy_move` / `_run_delete`).
- On `ConvertError`: notify and fall through to the hex viewer.

### Routing

In `_open_editor_window` the order becomes:

```
image → csv → office → hex → markdown → text
```

The **office** check sits *after* csv and *before* the hex/binary guard —
exactly like csv — because binary `.pdf`/`.docx`/`.xlsx` (NUL bytes / zip
containers) would otherwise be captured by the hex guard. On missing extra or
`ConvertError`, fall through to the existing hex/text branch.

### VFS members (`_open_member_view`)

Add an office branch that converts from in-memory bytes
(`convert_to_markdown(data, entry.name)`), gated by a size limit (mirror the
CSV-member size threshold). Streaming very large remote members to a temp file
is deferred — v1 caps by size and skips oversized members with a notify.

### No cache in v1

YAGNI: each F3 reconverts. A temp-file cache keyed by path+mtime is a future
improvement.

## Error handling

- Missing extra → `notify(...)`, fall through to hex/text.
- `ConvertError` → `notify(...)`, fall through to hex viewer.
- Oversized VFS member → `notify(...)`, skip conversion.

## Testing

- **Unit:** `looks_office` — accepts the five suffixes (case-insensitive),
  rejects others. Runs without markitdown installed.
- **Conversion:** convert a tiny fixture (e.g. a minimal `.docx` or `.pdf`) and
  assert non-empty Markdown. Guarded with `skipif not MARKITDOWN_AVAILABLE`.
- **Routing smoke:** F3 on a `.pdf` path with `convert_to_markdown` monkeypatched
  to a stub, assert a `MarkdownViewerContent` window mounts with the stub text;
  and that a `ConvertError` stub falls through to the hex viewer.

## Files touched

- `dunders/fm/doc_converter.py` — new pure module.
- `dunders/app.py` — routing in `_open_editor_window` and `_open_member_view`;
  worker + "Converting…" indicator; imports.
- `pyproject.toml` — `office` extra.
- `tests/fm/test_doc_converter.py` — new tests.
- `CLAUDE.md` — document the new viewer route (Architecture › viewers).
