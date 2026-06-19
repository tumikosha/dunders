# Large Markdown Viewer Performance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open any `.md` (including multi-MB) without a long UI freeze by choosing a render engine per cost model: interactive Textual for small docs, fast Rich-in-a-Static for block-dense docs, and a lazy mmap line-window for huge docs.

**Architecture:** Extract the CSV viewer's lazy line sources into a shared module. Add a pure `estimate_blocks` heuristic and a lazy `_LazyTextView` widget. `MarkdownViewerContent.__init__` becomes cheap (stat size + cheap estimate, no whole-file read for huge files) and selects one of three tiers; the heavy surface is built on mount.

**Tech Stack:** Python ≥3.12, Textual (`ScrollView`, `MarkdownViewer`, `Static`, `VerticalScroll`), `rich.markdown.Markdown`, `mmap`, pytest (asyncio auto mode).

## Global Constraints

- Python ≥3.12. Tests run with `pytest` (asyncio auto mode, no markers). Lint: `ruff check`.
- Thresholds are module-level constants in `markdown_viewer.py`: `_HUGE_CAP = 128 * 1024`, `_MAX_BLOCKS = 600`, `_RICH_RENDER_HARD_CAP = 1024 * 1024`.
- Tier routing for image-free docs: `size > _HUGE_CAP` → lazy; else `has_images or estimate_blocks ≤ _MAX_BLOCKS` → interactive; else → rich.
- Bias toward the faster tier (over-counting blocks is acceptable; under-counting is not).
- No worker threads for rendering (CPU + GIL does not free the event loop — measured). Freezes are bounded by the thresholds; the only long render is the user-initiated opt-in **[ Render ]** in the lazy tier.
- Huge tier never reads the whole local file into memory — it mmaps lazily, mirroring `CsvViewerContent.from_path`.
- Reuse, don't duplicate: the lazy line sources live in one shared module consumed by both CSV and Markdown viewers.
- Keep the existing `can_focus = True` focus-on-mount behavior for every rendered surface.
- Documents with inline images keep the current composed renderer regardless of size (Rich/lazy can't draw inline ASCII); the new tiering is for image-free docs.

---

### Task 1: Extract shared lazy line sources into `dunders/fm/line_source.py`

**Files:**
- Create: `dunders/fm/line_source.py`
- Modify: `dunders/fm/csv_viewer.py` (remove the three source classes + `_PREFIX_INDEX_LINES`; import them back as aliases)
- Create: `tests/fm/test_line_source.py`
- Modify: `CLAUDE.md` (note the shared module)

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `class LineSource` — abstract: `line_count() -> int`, `line(i: int) -> str`, `sample() -> str`, `is_complete() -> bool`, `index_batch(n: int) -> bool`, `close() -> None`.
  - `class TextSource(LineSource)` — `__init__(self, text: str)`.
  - `class MmapSource(LineSource)` — `__init__(self, path: Path)`.
  - `PREFIX_INDEX_LINES: int = 1024`.

- [ ] **Step 1: Write the failing test for the shared module**

```python
# tests/fm/test_line_source.py
from dunders.fm.line_source import LineSource, TextSource, MmapSource, PREFIX_INDEX_LINES


class TestTextSource:
    def test_line_access_and_count(self):
        s = TextSource("a\nb\nc")
        assert s.line_count() == 3
        assert s.line(0) == "a"
        assert s.line(2) == "c"
        assert s.is_complete() is True

    def test_empty_yields_one_blank_line(self):
        s = TextSource("")
        assert s.line_count() == 1
        assert s.line(0) == ""

    def test_out_of_range_is_blank(self):
        s = TextSource("only")
        assert s.line(5) == ""


class TestMmapSource:
    def test_lazy_index_and_lines(self, tmp_path):
        p = tmp_path / "big.txt"
        p.write_text("".join(f"line {i}\n" for i in range(5000)))
        src = MmapSource(p)
        # Opening indexes only a prefix; the full count is not known yet.
        assert src.line_count() <= PREFIX_INDEX_LINES + 1
        assert src.line(4999) == "line 4999"          # pulls index forward
        while src.index_batch(4096):
            pass
        assert src.is_complete() is True
        assert src.line_count() == 5000
        src.close()

    def test_subclass_of_linesource(self):
        assert issubclass(TextSource, LineSource)
        assert issubclass(MmapSource, LineSource)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/fm/test_line_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.fm.line_source'`

- [ ] **Step 3: Create the shared module**

Create `dunders/fm/line_source.py` by moving the classes verbatim from `csv_viewer.py` (currently lines ~180–298) and the constant `_PREFIX_INDEX_LINES` (line ~58), renamed without the leading underscore:

```python
"""Random-access line sources shared by the lazy CSV and Markdown viewers.

A ``LineSource`` exposes lines 0..N without materialising them all. ``TextSource``
wraps an in-memory string; ``MmapSource`` mmaps a file and builds its newline
index incrementally so a multi-GB file opens instantly and never freezes the UI.
"""

from __future__ import annotations

import mmap
from array import array
from contextlib import suppress
from pathlib import Path

__all__ = ["LineSource", "TextSource", "MmapSource", "PREFIX_INDEX_LINES"]

# How many lines to index up front when opening an mmap source (first screen +
# a width sample); the rest is indexed on demand and in the background.
PREFIX_INDEX_LINES = 1024


class LineSource:
    """Random access to lines 0..N without materialising them all at once."""

    def line_count(self) -> int:
        raise NotImplementedError

    def line(self, i: int) -> str:
        raise NotImplementedError

    def sample(self) -> str:
        """First few KiB as text, for delimiter sniffing."""
        raise NotImplementedError

    def is_complete(self) -> bool:
        """True when ``line_count`` is final (in-memory sources always are)."""
        return True

    def index_batch(self, n: int) -> bool:
        """Index up to ``n`` more lines incrementally; return True while more
        remain. No-op for fully-known sources."""
        return False

    def close(self) -> None:
        pass


class TextSource(LineSource):
    """Lines from an in-memory string (small files, archive members)."""

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines() or [""]

    def line_count(self) -> int:
        return len(self._lines)

    def line(self, i: int) -> str:
        return self._lines[i] if 0 <= i < len(self._lines) else ""

    def sample(self) -> str:
        return "\n".join(self._lines[:50])


class MmapSource(LineSource):
    """Lines from an mmap'd file via an *incremental* newline offset index.

    Opening indexes only a small prefix; the rest of the ``\\n`` index is built
    on demand (when a line is requested) and in the background (to grow the
    scrollbar), so even a multi-GB file opens instantly. Offsets live in a
    compact ``array('Q')``. Single-byte encodings only.
    """

    def __init__(self, path: Path) -> None:
        self._f = open(path, "rb")
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        self._size = self._mm.size()
        self._starts = array("Q", [0])  # starts[i] = byte offset of line i
        self._scan_pos = 0
        self._eof = self._size == 0
        self.index_batch(PREFIX_INDEX_LINES)

    def _index_one(self) -> bool:
        if self._eof:
            return False
        nl = self._mm.find(b"\n", self._scan_pos)
        if nl == -1:
            self._eof = True
            return False
        self._scan_pos = nl + 1
        self._starts.append(self._scan_pos)
        return True

    def index_batch(self, n: int) -> bool:
        for _ in range(n):
            if not self._index_one():
                break
        return not self._eof

    def _index_to_line(self, i: int) -> None:
        while not self._eof and len(self._starts) < i + 2:
            self._index_one()

    def is_complete(self) -> bool:
        return self._eof

    def _exact_count(self) -> int:
        n = len(self._starts)
        # A trailing newline leaves a phantom empty start == size; drop it.
        if n > 1 and self._starts[-1] >= self._size:
            return n - 1
        return n

    def line_count(self) -> int:
        if self._eof:
            return self._exact_count()
        return max(0, len(self._starts) - 1)

    def line(self, i: int) -> str:
        if i < 0:
            return ""
        self._index_to_line(i)
        if i >= self.line_count():
            return ""
        begin = self._starts[i]
        end = self._starts[i + 1] if i + 1 < len(self._starts) else self._size
        return self._mm[begin:end].rstrip(b"\r\n").decode("utf-8", errors="replace")

    def sample(self) -> str:
        return self._mm[:8192].decode("utf-8", errors="replace")

    def close(self) -> None:
        with suppress(Exception):
            self._mm.close()
        with suppress(Exception):
            self._f.close()
```

- [ ] **Step 4: Point `csv_viewer.py` at the shared module**

In `dunders/fm/csv_viewer.py`:

1. Delete the `_PREFIX_INDEX_LINES = 1024` line (~58) and the three class definitions `class _LineSource`, `class _TextSource`, `class _MmapSource` (~180–298).
2. Add this import near the other `dunders.fm` imports at the top:

```python
from dunders.fm.line_source import (
    LineSource as _LineSource,
    TextSource as _TextSource,
    MmapSource as _MmapSource,
    PREFIX_INDEX_LINES as _PREFIX_INDEX_LINES,
)
```

This keeps every existing `_LineSource` / `_TextSource` / `_MmapSource` / `_PREFIX_INDEX_LINES` reference working with no other edits. (If `mmap`, `array`, or `suppress` are now unused in `csv_viewer.py`, remove those imports to satisfy ruff.)

- [ ] **Step 5: Run the new test and the CSV suite to verify both pass**

Run: `pytest tests/fm/test_line_source.py tests/fm/test_csv_viewer.py -q`
Expected: PASS (the CSV suite still covers the mmap/index logic through the aliases).

- [ ] **Step 6: Lint**

Run: `ruff check dunders/fm/line_source.py dunders/fm/csv_viewer.py`
Expected: no errors (fix any now-unused imports in `csv_viewer.py`).

- [ ] **Step 7: Document the shared module in CLAUDE.md**

In `CLAUDE.md`, in the `dunders.fm` section, add a bullet after the `csv_viewer` description:

```markdown
- `line_source.py` — `LineSource`/`TextSource`/`MmapSource`: random-access lines
  without materialising the whole file (mmap + incremental newline index).
  Shared by the lazy CSV viewer and the lazy Markdown huge-file tier.
```

- [ ] **Step 8: Commit**

```bash
git add dunders/fm/line_source.py dunders/fm/csv_viewer.py tests/fm/test_line_source.py CLAUDE.md
git commit -m "refactor(fm): extract shared LineSource/TextSource/MmapSource module"
```

---

### Task 2: `estimate_blocks` pure heuristic

**Files:**
- Modify: `dunders/fm/markdown_viewer.py` (add the function + export)
- Modify: `tests/fm/test_markdown_viewer.py` (add a test class)

**Interfaces:**
- Consumes: nothing.
- Produces: `estimate_blocks(source: str) -> int` — a cheap proxy for the widget count Textual would create, biased to over-count.

- [ ] **Step 1: Write the failing tests**

Add to `tests/fm/test_markdown_viewer.py`:

```python
from dunders.fm.markdown_viewer import estimate_blocks


class TestEstimateBlocks:
    def test_empty_is_zero_or_one(self):
        assert estimate_blocks("") <= 1

    def test_counts_each_paragraph(self):
        src = "para one\n\npara two\n\npara three\n"
        assert estimate_blocks(src) >= 3

    def test_counts_each_list_item(self):
        src = "- a\n- b\n- c\n- d\n"
        assert estimate_blocks(src) >= 4

    def test_counts_table_rows(self):
        src = "| h1 | h2 |\n| -- | -- |\n| a | b |\n| c | d |\n"
        assert estimate_blocks(src) >= 4

    def test_counts_headings(self):
        src = "# H1\n\n## H2\n\n### H3\n"
        assert estimate_blocks(src) >= 3

    def test_overcounts_not_undercounts_list_heavy(self):
        # 100 list items must read as "many blocks", never as one block.
        src = "".join(f"- item {i}\n" for i in range(100))
        assert estimate_blocks(src) >= 100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/fm/test_markdown_viewer.py::TestEstimateBlocks -v`
Expected: FAIL with `ImportError: cannot import name 'estimate_blocks'`

- [ ] **Step 3: Implement `estimate_blocks`**

In `dunders/fm/markdown_viewer.py`, add near `looks_markdown` and add the name to `__all__`:

```python
# A line that begins a block-level element Textual would mount as its own
# widget (or several). Used by estimate_blocks as a cheap widget-count proxy.
_BLOCK_LINE_RE = re.compile(
    r"^\s*("
    r"#{1,6}\s"          # ATX heading
    r"|[-*+]\s"          # bullet list item
    r"|\d+\.\s"          # ordered list item
    r"|>\s?"             # blockquote
    r"|```|~~~"          # fenced code fence
    r"|\|"               # table row
    r")"
)


def estimate_blocks(source: str) -> int:
    """Cheap upper-ish estimate of how many widgets Textual's Markdown widget
    would mount for ``source`` — without parsing. Counts block-level lines
    (headings, list items, table rows, blockquotes, code fences) plus paragraph
    starts (a non-blank line following a blank line or the start of file). Biased
    to over-count list/table-heavy input so routing leans to the faster tier."""
    count = 0
    prev_blank = True
    for line in source.splitlines():
        if not line.strip():
            prev_blank = True
            continue
        if _BLOCK_LINE_RE.match(line):
            count += 1
        elif prev_blank:
            count += 1  # paragraph start
        prev_blank = False
    return count
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/fm/test_markdown_viewer.py::TestEstimateBlocks -v`
Expected: PASS (all six).

- [ ] **Step 5: Commit**

```bash
git add dunders/fm/markdown_viewer.py tests/fm/test_markdown_viewer.py
git commit -m "feat(fm): estimate_blocks heuristic for Markdown render-cost routing"
```

---

### Task 3: `_LazyTextView` — lazy line-windowed source widget

**Files:**
- Modify: `dunders/fm/markdown_viewer.py` (add the widget)
- Modify: `tests/fm/test_markdown_viewer.py` (add a test class)

**Interfaces:**
- Consumes: `LineSource` from `dunders.fm.line_source` (Task 1).
- Produces: `class _LazyTextView(ScrollView)` — `__init__(self, source: LineSource)`; renders only visible lines; `can_focus = True`; exposes `source` for the content wrapper to drive background indexing and `close()` on unmount.

- [ ] **Step 1: Write the failing test**

Add to `tests/fm/test_markdown_viewer.py`:

```python
from dunders.fm.line_source import TextSource
from dunders.fm.markdown_viewer import _LazyTextView


class TestLazyTextView:
    async def test_renders_visible_lines_only(self):
        src = TextSource("".join(f"line {i}\n" for i in range(1000)))
        view = _LazyTextView(src)

        class Host(App):
            def compose(self):
                yield view

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert view.can_focus is True
            # virtual height reflects the full line count, not the viewport
            assert view.virtual_size.height >= 1000
            strip = view.render_line(0)
            assert "line 0" in strip.text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/fm/test_markdown_viewer.py::TestLazyTextView -v`
Expected: FAIL with `ImportError: cannot import name '_LazyTextView'`

- [ ] **Step 3: Implement `_LazyTextView`**

In `dunders/fm/markdown_viewer.py`, add the imports it needs (top of file) and the class. Add to the existing imports:

```python
from rich.text import Text
from textual.binding import Binding
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from dunders.fm.line_source import LineSource, MmapSource, TextSource
```

(Note: `from rich.text import Text` is already imported — do not duplicate it; keep the others.)

```python
class _LazyTextView(ScrollView):
    """Scrollable plain-text view that renders only the visible lines of a
    ``LineSource``. Used for the huge-file tier so a multi-MB Markdown opens
    instantly (the source is never materialised into per-block widgets)."""

    DEFAULT_CSS = """
    _LazyTextView { background: $surface; color: $text; }
    """

    can_focus = True

    BINDINGS = [
        Binding("up",       "scroll_lines(-1)", show=False),
        Binding("down",     "scroll_lines(1)",  show=False),
        Binding("left",     "scroll_cols(-4)",  show=False),
        Binding("right",    "scroll_cols(4)",   show=False),
        Binding("pageup",   "scroll_page(-1)",  show=False),
        Binding("pagedown", "scroll_page(1)",   show=False),
        Binding("home",     "scroll_home",      show=False),
        Binding("end",      "scroll_end",       show=False),
    ]

    def __init__(self, source: LineSource) -> None:
        super().__init__()
        self._source = source
        self._resize_canvas()

    @property
    def source(self) -> LineSource:
        return self._source

    def _resize_canvas(self) -> None:
        rows = max(1, self._source.line_count())
        self.virtual_size = Size(max(1, self._longest_sampled()), rows)

    def _longest_sampled(self) -> int:
        # Width from a small prefix sample; horizontal scroll covers the rest.
        return max((len(self._source.line(i)) for i in range(min(200, self._source.line_count()))), default=1)

    def render_line(self, y: int) -> Strip:
        idx = int(self.scroll_offset.y) + y
        if idx < 0 or idx >= self._source.line_count():
            return Strip([])
        scroll_x = int(self.scroll_offset.x)
        text = Text(self._source.line(idx))
        strip = Strip(text.render(self.app.console))
        strip = strip.crop(scroll_x, scroll_x + self.size.width)
        return strip.adjust_cell_length(self.size.width, self.rich_style)

    def action_scroll_lines(self, delta: int) -> None:
        self.scroll_to(self.scroll_offset.x, self.scroll_offset.y + delta, animate=False)

    def action_scroll_cols(self, delta: int) -> None:
        self.scroll_to(self.scroll_offset.x + delta, self.scroll_offset.y, animate=False)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/fm/test_markdown_viewer.py::TestLazyTextView -v`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `ruff check dunders/fm/markdown_viewer.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add dunders/fm/markdown_viewer.py tests/fm/test_markdown_viewer.py
git commit -m "feat(fm): _LazyTextView — lazy line-windowed source view for huge docs"
```

---

### Task 4: Tiered routing, lazy construction, opt-in render

**Files:**
- Modify: `dunders/fm/markdown_viewer.py` (`__init__`, `compose`, `on_mount`, add `on_unmount`, tier constants, Rich tier surface, opt-in render)
- Modify: `tests/fm/test_markdown_viewer.py` (routing + lazy tests)
- Modify: `CLAUDE.md` (document the tiered renderer)

**Interfaces:**
- Consumes: `estimate_blocks` (Task 2), `_LazyTextView` (Task 3), `MmapSource`/`TextSource` (Task 1), existing `MarkdownViewer`/`_build_document`/`_raw_view`.
- Produces: `MarkdownViewerContent` with a `tier` property returning `"interactive" | "rich" | "lazy"`; constants `_HUGE_CAP`, `_MAX_BLOCKS`, `_RICH_RENDER_HARD_CAP`.

- [ ] **Step 1: Write the failing routing tests**

Add to `tests/fm/test_markdown_viewer.py`:

```python
from textual.widgets import Static
from dunders.fm.markdown_viewer import (
    _HUGE_CAP, _MAX_BLOCKS, MarkdownViewerContent,
)


class TestTierRouting:
    def test_small_doc_is_interactive(self):
        c = MarkdownViewerContent(text="# Hi\n\nshort\n", display_name="a.md")
        assert c.tier == "interactive"

    def test_block_dense_doc_is_rich(self):
        # Many blocks but under the size cap → Rich static render.
        src = "".join(f"- item {i}\n" for i in range(_MAX_BLOCKS + 50))
        assert len(src.encode()) <= _HUGE_CAP
        c = MarkdownViewerContent(text=src, display_name="dense.md")
        assert c.tier == "rich"

    def test_huge_local_file_is_lazy_without_full_read(self, tmp_path):
        p = tmp_path / "huge.md"
        p.write_text("para\n\n" * (_HUGE_CAP // 3))  # well over _HUGE_CAP
        assert p.stat().st_size > _HUGE_CAP
        c = MarkdownViewerContent(file_path=p)
        assert c.tier == "lazy"
        assert c._source_text is None  # huge file not read into memory

    async def test_lazy_tier_mounts_lazy_view(self, tmp_path):
        p = tmp_path / "huge.md"
        p.write_text("para line\n" * (_HUGE_CAP // 5))
        c = MarkdownViewerContent(file_path=p)

        class Host(App):
            def compose(self):
                yield c

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(c._rendered, _LazyTextView)
            assert c._rendered.has_focus is True

    async def test_rich_tier_mounts_static(self):
        src = "".join(f"- item {i}\n" for i in range(_MAX_BLOCKS + 50))
        c = MarkdownViewerContent(text=src, display_name="dense.md")

        class Host(App):
            def compose(self):
                yield c

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.query(Static)) >= 1  # Rich renderable in a Static
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/fm/test_markdown_viewer.py::TestTierRouting -v`
Expected: FAIL — `tier` / `_source_text` attributes and the routing do not exist yet.

- [ ] **Step 3: Add the tier constants and Rich import**

In `dunders/fm/markdown_viewer.py`, near the other constants, add:

```python
from rich.markdown import Markdown as _RichMarkdown

# Render-cost tiers. Above _HUGE_CAP bytes a doc opens in the lazy line view;
# at/under it, a doc with <= _MAX_BLOCKS estimated blocks renders interactively
# (Textual MarkdownViewer + TOC) and a denser one renders via Rich in a single
# Static. Opt-in render in the lazy tier is offered only at/under the hard cap.
_HUGE_CAP = 128 * 1024
_MAX_BLOCKS = 600
_RICH_RENDER_HARD_CAP = 1024 * 1024
```

- [ ] **Step 4: Rewrite `__init__` to be cheap and tier-aware**

Replace the body of `MarkdownViewerContent.__init__` (the part from reading the source through building `_rendered`) with:

```python
        super().__init__()
        self._path = Path(file_path) if file_path is not None else None
        name = display_name or (self._path.name if self._path else "markdown")
        self.window_title = f"MD: {name}"
        self._display_name = name
        self._show_toc = False
        self._raw_mode = False
        self._viewer: MarkdownViewer | None = None
        self._rendered = None  # built on mount
        self._source_text: str | None = None  # None only for the un-read huge file

        # Decide the size cheaply. A huge local file is NOT read into memory.
        if text is not None:
            self._source_text = text
            size = len(text.encode("utf-8", errors="replace"))
        elif self._path is not None:
            try:
                size = self._path.stat().st_size
            except OSError:
                size = 0
            if size <= _HUGE_CAP:
                try:
                    self._source_text = self._path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError as exc:
                    self._source_text = f"# Could not read file\n\n{exc}"
                    size = len(self._source_text.encode())
        else:
            self._source_text = ""
            size = 0

        self._size = size

        # Images need the source; for the huge (un-read) case we treat the doc as
        # image-free and go lazy. Otherwise split now (cheap relative to render).
        if self._source_text is None:
            self._segments = []
            self._image_count = 0
            self._has_images = False
            self._tier = "lazy"
        else:
            base_dir = (
                self._path.parent
                if (self._path is not None and PILLOW_AVAILABLE)
                else None
            )
            self._segments = split_markdown_blocks(self._source_text, base_dir)
            self._image_count = sum(1 for s in self._segments if s.kind == "img")
            self._has_images = self._image_count > 0
            self._tier = self._choose_tier()

        # The raw source toggle target. For the huge tier there is no in-memory
        # source, so the lazy view itself is the raw view (no separate Static).
        if self._source_text is not None:
            self._raw_view = VerticalScroll(
                Static(self._source_text, classes="md-source"), classes="md-raw"
            )
            self._raw_view.can_focus = True
            self._raw_view.display = False
        else:
            self._raw_view = None

        self._raw_btn = _ToolbarButton("[ Raw ]", on_press=self._toggle_raw)
        self._raw_btn.id = "md-raw-toggle"
        self._toc_btn = _ToolbarButton("[ Contents ]", on_press=self._toggle_toc)
        self._toc_btn.id = "md-toc-toggle"
        self._render_btn = _ToolbarButton("[ Render ]", on_press=self._render_now)
        self._render_btn.id = "md-render"
        self._fill_timer = None
```

Then add the tier chooser and a `tier` property:

```python
    def _choose_tier(self) -> str:
        if self._size > _HUGE_CAP:
            return "lazy"
        if self._has_images:
            return "interactive"  # composed renderer (inline ASCII images)
        if estimate_blocks(self._source_text or "") <= _MAX_BLOCKS:
            return "interactive"
        return "rich"

    @property
    def tier(self) -> str:
        return self._tier
```

- [ ] **Step 5: Build the chosen surface on mount**

Replace `compose` and `on_mount`, and add `_build_rendered`, `_make_lazy_source`, `on_unmount`:

```python
    def compose(self) -> ComposeResult:
        self._rendered = self._build_rendered()
        with Horizontal(classes="md-toolbar"):
            yield self._raw_btn
            # TOC only exists for the plain interactive MarkdownViewer.
            if self._tier == "interactive" and not self._has_images:
                yield self._toc_btn
            # Opt-in render for the lazy tier, only when small enough to render.
            if self._tier == "lazy" and self._size <= _RICH_RENDER_HARD_CAP:
                yield self._render_btn
        yield self._rendered
        if self._raw_view is not None:
            yield self._raw_view

    def _build_rendered(self):
        if self._tier == "lazy":
            return _LazyTextView(self._make_lazy_source())
        if self._tier == "rich":
            return VerticalScroll(
                Static(_RichMarkdown(self._source_text or ""), classes="md-rich"),
                classes="md-doc",
            )
        # interactive
        if self._has_images:
            return self._build_document()
        self._viewer = MarkdownViewer(
            self._source_text or "", show_table_of_contents=False, open_links=False
        )
        self._viewer.can_focus = True
        return self._viewer

    def _make_lazy_source(self) -> LineSource:
        if self._source_text is not None:
            return TextSource(self._source_text)
        try:
            return MmapSource(self._path)  # type: ignore[arg-type]
        except OSError:
            # mmap failed — read the text and fall back to an in-memory source.
            try:
                self._source_text = self._path.read_text(  # type: ignore[union-attr]
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                self._source_text = f"# Could not read file\n\n{exc}"
            return TextSource(self._source_text)

    def on_mount(self) -> None:
        self._rendered.focus()
        self._update_subtitle()
        # Grow the lazy index in the background so the scrollbar settles without
        # blocking the open (mirrors CsvViewerContent).
        src = getattr(self._rendered, "source", None)
        if src is not None and not src.is_complete():
            self._fill_timer = self.set_interval(0.05, self._fill_tick)

    def _fill_tick(self) -> None:
        src = self._rendered.source
        more = src.index_batch(2000)
        self._rendered._resize_canvas()
        self._rendered.refresh()
        if not more and self._fill_timer is not None:
            self._fill_timer.stop()
            self._fill_timer = None

    def on_unmount(self) -> None:
        if self._fill_timer is not None:
            self._fill_timer.stop()
            self._fill_timer = None
        src = getattr(self._rendered, "source", None)
        if src is not None:
            src.close()

    def _render_now(self) -> None:
        """Opt-in: replace the lazy view with a Rich render (user-initiated,
        bounded by _RICH_RENDER_HARD_CAP). Reads the file if not already loaded."""
        if self._source_text is None and self._path is not None:
            try:
                self._source_text = self._path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                self.notify(f"Cannot render: {exc}", severity="warning")
                return
        self._tier = "rich"
        new = self._build_rendered()
        old = self._rendered
        self._rendered = new
        self.mount(new, after=old)
        old.remove()
        new.focus()
        self._render_btn.display = False
        self._update_subtitle()
```

> NOTE: `_update_subtitle`, `_toggle_raw`, `_toggle_toc`, the `viewer`/`document` properties, and `get_commands` already exist. Audit each for assumptions that no longer hold: `_raw_view` and `_viewer` can now be `None`, and `document`/`viewer` must tolerate the lazy/rich tiers. Make `_toggle_toc` a no-op unless `self._viewer is not None`; make `_toggle_raw` a no-op when `self._raw_view is None` (lazy tier — the view is already the raw source). Have the `viewer` property return `self._viewer` (already None-safe) and the `document` property return `self._rendered`.

- [ ] **Step 6: Run the routing tests to verify they pass**

Run: `pytest tests/fm/test_markdown_viewer.py::TestTierRouting -v`
Expected: PASS (all five).

- [ ] **Step 7: Run the full markdown + csv + doc-routing suites (regression)**

Run: `pytest tests/fm/test_markdown_viewer.py tests/fm/test_csv_viewer.py tests/fm/test_doc_viewer_routing.py tests/fm/test_line_source.py -q`
Expected: PASS. If any existing test assumed `_raw_view`/`_viewer` are always present or that every doc uses `MarkdownViewer`, update it to the tier it exercises (small docs stay `interactive`, so existing small-doc tests must remain green unchanged).

- [ ] **Step 8: Lint**

Run: `ruff check dunders/fm/markdown_viewer.py`
Expected: no errors.

- [ ] **Step 9: Document the tiered renderer in CLAUDE.md**

In `CLAUDE.md`, update the `markdown_viewer.py` description to note the tiers:

```markdown
  - `MarkdownViewerContent` picks a render tier by cost (image-free docs):
    `size > 128 KiB` → lazy `_LazyTextView` over an mmap `LineSource` (instant at
    any size; opt-in `[ Render ]` ≤ 1 MiB); else `estimate_blocks ≤ 600` →
    interactive Textual `MarkdownViewer` (+ TOC); else → `rich.markdown.Markdown`
    in one `Static` (fast, no TOC). `__init__` is cheap and never reads a huge
    file into memory; the surface is built on mount. Docs with inline images keep
    the composed renderer regardless of size. Worker threads do not help (GIL),
    so freezes are bounded by the thresholds.
```

- [ ] **Step 10: Commit**

```bash
git add dunders/fm/markdown_viewer.py tests/fm/test_markdown_viewer.py CLAUDE.md
git commit -m "feat(fm): tiered Markdown rendering (interactive/rich/lazy) for large files"
```

---

## Self-Review Notes

- **Spec coverage:** shared line source → Task 1; `estimate_blocks` → Task 2; `_LazyTextView` lazy huge tier → Task 3; three-tier routing + thresholds + cheap `__init__` + opt-in render + image-doc exception + mmap-fallback error handling → Task 4; CLAUDE.md → Tasks 1 & 4; no-worker-thread constraint honored (synchronous, threshold-bounded). All spec sections map to a task.
- **Type consistency:** `LineSource`/`TextSource`/`MmapSource`/`PREFIX_INDEX_LINES` (Task 1) used by Tasks 3–4; `estimate_blocks(source: str) -> int` (Task 2) used by Task 4; `_LazyTextView(source)` with `.source`/`._resize_canvas()`/`.close()` (Task 3) driven by Task 4's `_fill_tick`/`on_unmount`; tier strings `"interactive"|"rich"|"lazy"` consistent throughout.
- **Known compromises (from spec):** inline-image docs bypass the fast tiers (composed renderer only); opt-in render above the hard cap is intentionally unavailable; VFS members have no path so a huge member uses an in-memory `TextSource` (still lazy-rendered, but the bytes were already read — bounded by the member read cap).
