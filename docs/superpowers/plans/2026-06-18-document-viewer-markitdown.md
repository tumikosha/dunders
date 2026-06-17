# Document Viewer (F3) via markitdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open PDF and office documents with F3 by converting them to Markdown via `markitdown` and displaying the result through the existing `MarkdownViewerContent`.

**Architecture:** A new pure module `dunders/fm/doc_converter.py` exposes a name-only sniffer (`looks_office`), an availability flag (`MARKITDOWN_AVAILABLE`), and `convert_to_markdown(source, name)` (accepts a `Path` or `bytes`). `app.py` routes office files — before the hex guard — through a worker thread (markitdown is blocking) that shows a "Converting…" modal, then mounts `MarkdownViewerContent.from_text(name, md)`. On failure or a missing extra it falls back to the hex viewer. No changes to `markdown_viewer.py`.

**Tech Stack:** Python ≥3.12, Textual, `markitdown` (MIT, opt-in extra), pytest (asyncio auto mode).

## Global Constraints

- Python ≥3.12; permissive-only core — `markitdown` ships **only** as the opt-in extra `dunders[office]`, never a base dependency (mirrors `image`/`sftp`).
- The base package and every test that is not `skipif`-guarded MUST import and pass without `markitdown` installed. Guard the import in `doc_converter.py` with `MARKITDOWN_AVAILABLE` exactly like `PILLOW_AVAILABLE` in `image_viewer.py`.
- Worker threads marshal back to the UI thread via `self.call_from_thread(...)`. Never touch widgets from the worker.
- Office routing sits **after** the CSV check and **before** the hex/binary guard in both `_open_editor_window` and `_open_member_view` (binary PDF/docx/xlsx would otherwise be captured by the hex guard).
- Reuse `MarkdownViewerContent.from_text(name, text)` for display; do not add a new viewer widget.
- Office suffixes (v1): `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.epub`.

---

### Task 1: Pure converter module + `office` extra

**Files:**
- Create: `dunders/fm/doc_converter.py`
- Create: `tests/fm/test_doc_converter.py`
- Modify: `pyproject.toml` (add the `office` optional-dependency)

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `OFFICE_SUFFIXES: tuple[str, ...]` = `(".pdf", ".docx", ".pptx", ".xlsx", ".epub")`
  - `looks_office(name: object) -> bool`
  - `MARKITDOWN_AVAILABLE: bool`
  - `class ConvertError(Exception)`
  - `convert_to_markdown(source: Path | bytes, name: str) -> str` — returns Markdown; raises `ConvertError` on any failure (including missing extra or empty output).

- [ ] **Step 1: Write the failing sniffer tests**

```python
# tests/fm/test_doc_converter.py
import pytest

from dunders.fm.doc_converter import (
    ConvertError,
    MARKITDOWN_AVAILABLE,
    OFFICE_SUFFIXES,
    convert_to_markdown,
    looks_office,
)


class TestLooksOffice:
    def test_pdf(self):
        assert looks_office("report.pdf") is True

    def test_uppercase(self):
        assert looks_office("DECK.PPTX") is True

    def test_all_suffixes(self):
        for suf in OFFICE_SUFFIXES:
            assert looks_office("file" + suf) is True

    def test_rejects_others(self):
        assert looks_office("a.txt") is False
        assert looks_office("a.csv") is False   # CSV has its own viewer
        assert looks_office("a.png") is False
        assert looks_office("noext") is False

    def test_non_str(self):
        from pathlib import Path
        assert looks_office(Path("x.docx")) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/fm/test_doc_converter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.fm.doc_converter'`

- [ ] **Step 3: Write the module**

```python
# dunders/fm/doc_converter.py
"""Convert documents (PDF, office formats) to Markdown via markitdown.

The Markdown string is fed to the existing ``MarkdownViewerContent`` so an
F3 on a ``.pdf``/``.docx``/``.pptx``/``.xlsx``/``.epub`` opens rendered.

markitdown is an opt-in extra (``pip install dunders[office]``); it is MIT,
covers PDF and office formats through one converter, and is guarded by
``MARKITDOWN_AVAILABLE`` so the base package imports without it. ``looks_office``
is a pure name-only sniffer and imports nothing heavy, so it unit-tests in
isolation.
"""

from __future__ import annotations

import io
from pathlib import Path

try:  # markitdown is an opt-in extra (`pip install dunders[office]`).
    from markitdown import MarkItDown

    MARKITDOWN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    MarkItDown = None  # type: ignore[assignment, misc]
    MARKITDOWN_AVAILABLE = False

__all__ = [
    "OFFICE_SUFFIXES",
    "MARKITDOWN_AVAILABLE",
    "ConvertError",
    "looks_office",
    "convert_to_markdown",
]

# Formats routed through the converter. Conservative: only formats whose
# Markdown rendering is genuinely useful in a TUI. CSV/HTML/JSON/images are
# handled by earlier routes and deliberately excluded.
OFFICE_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".epub")


class ConvertError(Exception):
    """Any failure converting a document to Markdown (including a missing
    extra or empty output). Callers catch this one type and fall back."""


def looks_office(name: object) -> bool:
    """True if ``name`` has a document extension we convert. Cheap, name-only
    check; the caller's size guards still decide whether to attempt it."""
    return str(name).lower().endswith(OFFICE_SUFFIXES)


def convert_to_markdown(source: Path | bytes, name: str) -> str:
    """Convert ``source`` (a local path or in-memory bytes) to a Markdown
    string. ``name`` supplies the extension hint when ``source`` is bytes.

    Raises :class:`ConvertError` on a missing extra, a markitdown failure, or
    an empty conversion."""
    if not MARKITDOWN_AVAILABLE:
        raise ConvertError("markitdown is not installed (pip install dunders[office])")
    md = MarkItDown()
    try:
        if isinstance(source, (bytes, bytearray)):
            ext = Path(name).suffix
            result = md.convert_stream(io.BytesIO(bytes(source)), file_extension=ext)
        else:
            result = md.convert(str(source))
    except Exception as exc:  # markitdown raises a variety of types
        raise ConvertError(str(exc)) from exc
    text = getattr(result, "text_content", None) or getattr(result, "markdown", "")
    if not text:
        raise ConvertError(f"empty conversion for {name}")
    return text
```

- [ ] **Step 4: Run the sniffer tests to verify they pass**

Run: `pytest tests/fm/test_doc_converter.py -v`
Expected: PASS (all `TestLooksOffice` tests). They run with or without markitdown installed.

- [ ] **Step 5: Add a guarded conversion error test**

Append to `tests/fm/test_doc_converter.py`:

```python
@pytest.mark.skipif(not MARKITDOWN_AVAILABLE, reason="markitdown not installed")
class TestConvert:
    def test_garbage_pdf_raises_convert_error(self):
        with pytest.raises(ConvertError):
            convert_to_markdown(b"%PDF-1.4 not a real pdf", "broken.pdf")


def test_convert_without_extra_raises(monkeypatch):
    import dunders.fm.doc_converter as dc
    monkeypatch.setattr(dc, "MARKITDOWN_AVAILABLE", False)
    with pytest.raises(ConvertError):
        dc.convert_to_markdown(b"x", "a.pdf")
```

- [ ] **Step 6: Run the conversion tests**

Run: `pytest tests/fm/test_doc_converter.py -v`
Expected: PASS. `TestConvert` is skipped when markitdown is absent; `test_convert_without_extra_raises` always runs.

- [ ] **Step 7: Add the `office` extra to pyproject.toml**

In `[project.optional-dependencies]`, after the `image` extra, add:

```toml
# Opt-in document viewer (F3 on a PDF/office file → Markdown). markitdown is
# MIT; kept out of the default install to stay dependency-light.
office = ["markitdown[pdf,docx,pptx,xlsx]>=0.0.1"]
```

- [ ] **Step 8: Verify the package still resolves and lints**

Run: `python -c "import dunders.fm.doc_converter as d; print(d.OFFICE_SUFFIXES, d.MARKITDOWN_AVAILABLE)"`
Expected: prints the tuple and a bool (no traceback).
Run: `ruff check dunders/fm/doc_converter.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add dunders/fm/doc_converter.py tests/fm/test_doc_converter.py pyproject.toml
git commit -m "feat(fm): doc_converter — PDF/office → Markdown via markitdown extra"
```

---

### Task 2: Route local F3 through the converter (worker + fallback)

**Files:**
- Modify: `dunders/app.py` — imports (near line 107); `_open_editor_window` (insert office branch between the CSV block and the hex guard, ~line 3375); add `_convert_office_async` and `_finish_office` helpers (after `_open_member_view`, ~line 3210).
- Modify: `CLAUDE.md` — document the new route.
- Test: `tests/fm/test_doc_viewer_routing.py` (create).

**Interfaces:**
- Consumes: `looks_office`, `MARKITDOWN_AVAILABLE`, `convert_to_markdown`, `ConvertError` from `dunders.fm.doc_converter`; `MarkdownViewerContent.from_text`; `HexViewerContent`; `ProgressDialog`; `show_modal`; `self._mount_maximized_content`.
- Produces:
  - `DundersApp._convert_office_async(self, name: str, source: Path | bytes, fallback_factory) -> None`
  - `DundersApp._finish_office(self, progress, name: str, md: str | None, error: Exception | None, fallback_factory) -> None`
  - Window title `Doc: {name}`, win_id `docviewer-{seq}`.

- [ ] **Step 1: Write the failing routing tests**

```python
# tests/fm/test_doc_viewer_routing.py
"""F3 on a PDF/office file routes through the converter to the Markdown viewer,
and falls back to the hex viewer on conversion failure."""

import dunders.app as app_mod
from dunders.app import DundersApp
from dunders.fm.doc_converter import ConvertError
from dunders.fm.hex_viewer import HexViewerContent
from dunders.fm.markdown_viewer import MarkdownViewerContent
from dunders.windowing import Desktop


def _windows(app):
    return list(app.query_one(Desktop).windows)


async def _drain(pilot):
    # Let the conversion worker run and the call_from_thread finish callback land.
    await pilot.pause()
    await app_mod.DundersApp.workers  # type: ignore[truthy-function]


async def test_pdf_opens_in_markdown_viewer(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(app_mod, "MARKITDOWN_AVAILABLE", True)
    monkeypatch.setattr(
        app_mod, "convert_to_markdown", lambda source, name: "# Hello\n\nfrom pdf\n"
    )
    app = DundersApp(start_path=tmp_path)
    async with app.run_test() as pilot:
        app._open_editor_window(pdf, read_only=True)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        mds = [w for w in _windows(app) if isinstance(w.content, MarkdownViewerContent)]
        assert len(mds) == 1


async def test_conversion_failure_falls_back_to_hex(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    def _boom(source, name):
        raise ConvertError("nope")

    monkeypatch.setattr(app_mod, "MARKITDOWN_AVAILABLE", True)
    monkeypatch.setattr(app_mod, "convert_to_markdown", _boom)
    app = DundersApp(start_path=tmp_path)
    async with app.run_test() as pilot:
        app._open_editor_window(pdf, read_only=True)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        hexes = [w for w in _windows(app) if isinstance(w.content, HexViewerContent)]
        assert len(hexes) == 1
```

> NOTE: confirm the `DundersApp(...)` construction and start-path kwarg against an existing app-driving test (e.g. `tests/fm/test_archive_guard.py`) and match it exactly; adjust the constructor call if the kwarg name differs.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/fm/test_doc_viewer_routing.py -v`
Expected: FAIL — `convert_to_markdown` is not yet an attribute of `dunders.app`, and no `Doc:` route exists, so no `MarkdownViewerContent` window is created.

- [ ] **Step 3: Add the import**

In `dunders/app.py`, near the other fm-viewer imports (~line 107), add:

```python
from dunders.fm.doc_converter import (
    MARKITDOWN_AVAILABLE,
    ConvertError,
    convert_to_markdown,
    looks_office,
)
```

- [ ] **Step 4: Add the worker + finish helpers**

In `dunders/app.py`, immediately after `_open_member_view` (ends ~line 3210), add:

```python
    def _convert_office_async(self, name: str, source, fallback_factory) -> None:
        """Convert a document to Markdown in a worker (markitdown is blocking),
        showing a Converting… modal, then mount the Markdown viewer. On failure
        mount ``fallback_factory()`` content (the hex viewer)."""
        if self.desktop is None:
            return
        self._remember_active_panel_id()
        progress = ProgressDialog(title=f"Converting {name}", total=0)
        show_modal(self.desktop, progress, title="Converting", size=(64, 9))
        self.call_after_refresh(progress.focus)

        def _worker() -> None:
            md: str | None = None
            error: Exception | None = None
            try:
                md = convert_to_markdown(source, name)
            except ConvertError as exc:
                error = exc
            self.call_from_thread(
                self._finish_office, progress, name, md, error, fallback_factory
            )

        self.run_worker(_worker, thread=True, exclusive=False, group="fileop")

    def _finish_office(self, progress, name, md, error, fallback_factory) -> None:
        cancelled = progress.cancel_event.is_set()
        self._close_modal(progress)
        if cancelled:
            return
        self._editor_seq += 1
        if md is None:
            if error is not None:
                self.notify(f"Cannot convert {name}: {error}", severity="warning")
            content = fallback_factory()
            self._mount_maximized_content(
                content, title=f"Hex: {name}", win_id=f"hexviewer-{self._editor_seq}"
            )
            return
        content = MarkdownViewerContent.from_text(name, md)
        self._mount_maximized_content(
            content, title=f"Doc: {name}", win_id=f"docviewer-{self._editor_seq}"
        )
```

- [ ] **Step 5: Add the local route in `_open_editor_window`**

In `dunders/app.py`, right after the CSV block (after its `return`, ~line 3375) and **before** the `if read_only and self._should_use_hex_viewer(path):` line, insert:

```python
        # F3 on a PDF/office doc → convert to Markdown in a worker, then open
        # in the Markdown viewer. Checked BEFORE the hex guard so a binary
        # .pdf/.docx/.xlsx tabulates instead of opening as hex. Missing extra
        # or a conversion error falls through to the hex viewer.
        if read_only and looks_office(path):
            if MARKITDOWN_AVAILABLE:
                self._convert_office_async(
                    path.name, path, lambda: HexViewerContent(path)
                )
                return
            self.notify(
                "Install dunders[office] to view documents", severity="warning"
            )
```

- [ ] **Step 6: Run the routing tests to verify they pass**

Run: `pytest tests/fm/test_doc_viewer_routing.py -v`
Expected: PASS (both tests). If `await app.workers.wait_for_complete()` is unavailable, replace with a short `for _ in range(5): await pilot.pause()` loop.

- [ ] **Step 7: Document the route in CLAUDE.md**

In `CLAUDE.md`, in the `viewer.py / hex_viewer.py / ...` bullet under section 2 (`dunders.fm`), add after the markdown-viewer description:

```markdown
  - `doc_converter.py` (opt-in `dunders[office]` / markitdown) converts
    `.pdf`/`.docx`/`.pptx`/`.xlsx`/`.epub` → Markdown, shown via
    `MarkdownViewerContent.from_text`. Routed in `_open_editor_window` and
    `_open_member_view` *after* the CSV check and *before* the hex guard
    (binary docs would otherwise open as hex); conversion runs in a worker
    (`_convert_office_async`/`_finish_office`) behind a Converting… modal and
    falls back to the hex viewer on failure or a missing extra. `looks_office`
    is the pure extension sniffer.
```

- [ ] **Step 8: Run the full fm suite and lint**

Run: `pytest tests/fm/ -q && ruff check dunders/app.py`
Expected: PASS, no lint errors.

- [ ] **Step 9: Commit**

```bash
git add dunders/app.py tests/fm/test_doc_viewer_routing.py CLAUDE.md
git commit -m "feat(fm): F3 opens PDF/office docs via markitdown → Markdown viewer"
```

---

### Task 3: Route VFS members (archives/SFTP/FTP) through the converter

**Files:**
- Modify: `dunders/app.py` — `_open_member_view` (insert office branch after the image branch's `return`, ~line 3188, before the CSV branch).
- Test: `tests/fm/test_doc_viewer_routing.py` (append a member-routing test).

**Interfaces:**
- Consumes: `_convert_office_async` (from Task 2), `looks_office`, `MARKITDOWN_AVAILABLE`, `HexViewerContent.from_bytes`, `self._read_member_bytes`.
- Produces: no new public symbol — reuses Task 2's worker path with `source=data` (bytes) and a bytes-based hex fallback.

- [ ] **Step 1: Write the failing member-routing test**

Append to `tests/fm/test_doc_viewer_routing.py`:

```python
import zipfile


async def test_member_pdf_opens_in_markdown_viewer(tmp_path, monkeypatch):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("doc.pdf", b"%PDF-1.4 fake")
    monkeypatch.setattr(app_mod, "MARKITDOWN_AVAILABLE", True)
    monkeypatch.setattr(
        app_mod, "convert_to_markdown", lambda source, name: "# Member\n\nok\n"
    )
    app = DundersApp(start_path=tmp_path)
    async with app.run_test() as pilot:
        panel = app._active_panel()
        panel.refresh_listing()
        panel.cursor = next(
            i for i, e in enumerate(panel.entries) if e.name == "a.zip"
        )
        panel.activate()  # enter the zip
        member = next(e for e in panel.entries if e.name == "doc.pdf")
        app._open_member_view(member)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        mds = [w for w in _windows(app) if isinstance(w.content, MarkdownViewerContent)]
        assert len(mds) == 1
```

> NOTE: mirror the zip-entry navigation used in `tests/fm/test_archive_guard.py` (`_enter_zip` / panel cursor) and adjust to match the helpers there.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/fm/test_doc_viewer_routing.py::test_member_pdf_opens_in_markdown_viewer -v`
Expected: FAIL — the member `.pdf` currently hits the NUL/hex branch, so a `HexViewerContent` window is created instead of a `MarkdownViewerContent`.

- [ ] **Step 3: Add the member office branch**

In `dunders/app.py`, in `_open_member_view`, right after the image branch (its `return`, ~line 3188) and **before** `if self._looks_csv(entry.name):`, insert:

```python
        # Office/PDF member → convert to Markdown in a worker (from the bytes
        # we just read, capped by _read_member_bytes). Before the CSV/NUL/hex
        # branches so a binary .pdf/.docx member renders instead of going hex.
        if looks_office(entry.name):
            if MARKITDOWN_AVAILABLE:
                self._convert_office_async(
                    entry.name,
                    data,
                    lambda: HexViewerContent.from_bytes(entry.name, data),
                )
                return
            self.notify(
                "Install dunders[office] to view documents", severity="warning"
            )
```

> NOTE: `self._editor_seq += 1` runs earlier in `_open_member_view`; `_finish_office` increments it again, so the member open consumes two seq values. That is harmless (the counter only needs to be unique) — do not try to "fix" it by sharing the counter across threads.

- [ ] **Step 4: Run the member test to verify it passes**

Run: `pytest tests/fm/test_doc_viewer_routing.py::test_member_pdf_opens_in_markdown_viewer -v`
Expected: PASS.

- [ ] **Step 5: Run the full fm suite and lint**

Run: `pytest tests/fm/ -q && ruff check dunders/app.py`
Expected: PASS, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add dunders/app.py tests/fm/test_doc_viewer_routing.py
git commit -m "feat(fm): F3 inside archives/SFTP converts PDF/office members to Markdown"
```

---

## Self-Review Notes

- **Spec coverage:** engine/licensing → Task 1 (Step 7 extra); pure module/`looks_office`/`MARKITDOWN_AVAILABLE`/`convert_to_markdown`/`ConvertError` → Task 1; display reuse of `from_text` → Task 2 (`_finish_office`); worker + Converting… indicator → Task 2; routing order image→csv→office→hex→md→text → Task 2 (Step 5 placement); VFS members + size cap (via `_read_member_bytes`) → Task 3; error handling (missing extra / ConvertError → notify + hex) → Tasks 2–3; no cache (v1) → honored (each F3 reconverts); tests (unit sniffer, guarded conversion, routing smoke) → Tasks 1–3; CLAUDE.md doc → Task 2 Step 7.
- **Type consistency:** `convert_to_markdown(source, name)`, `_convert_office_async(name, source, fallback_factory)`, `_finish_office(progress, name, md, error, fallback_factory)` used consistently across tasks; window title `Doc:`/`docviewer-`, fallback `Hex:`/`hexviewer-`.
- **Known v1 compromise (from spec):** images embedded in converted docs do not render as ASCII (`from_text` has no base dir) — accepted, same as existing VFS-member behavior.
