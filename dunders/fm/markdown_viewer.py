"""MarkdownViewerContent — render Markdown files (F3), images as ASCII art.

Wraps Textual's built-in ``MarkdownViewer`` (a scroll container with an
optional table-of-contents sidebar) inside a ``WindowContent`` so a ``.md``
file opens rendered instead of as plain source.

Textual's Markdown widget renders ``![alt](src)`` images inline as a "🖼
(alt)" placeholder — it has no hook for a real picture. So when a document
contains *standalone* image lines pointing at local, decodable images, we
switch to a composed renderer: the source is split into blocks, text blocks
render through ``Markdown`` widgets, and each image block is drawn as inline
ASCII art reusing the file-manager's existing converter
(:func:`dunders.fm.image_viewer.image_to_ascii`). Image-free documents keep
the plain ``MarkdownViewer`` (and its TOC), so nothing regresses.

A toolbar exposes two toggles, mirrored as focus-scoped commands:

- **Raw / Rendered** (``t``) — flip between the rendered document and the
  original Markdown source (a scrollable read-only view).
- **Contents** (``c``) — show/hide the heading outline (only meaningful for
  the plain ``MarkdownViewer``; a no-op in image / raw mode).

:func:`looks_markdown` and :func:`split_markdown_blocks` are pure helpers that
unit-test in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from rich.color import Color as RichColor
from rich.style import Style as RichStyle
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Markdown, MarkdownViewer, Static

from dunders.fm.image_viewer import (
    PILLOW_AVAILABLE,
    _fit,
    _ToolbarButton,
    image_to_ascii,
    sniff_image,
)
from rich.markdown import Markdown as _RichMarkdown

from dunders.fm.line_source import LineSource, MmapSource, TextSource
from dunders.windowing.content import WindowContent, WindowCommand

__all__ = ["looks_markdown", "split_markdown_blocks", "estimate_blocks", "MarkdownViewerContent"]

# Extensions we treat as Markdown. Kept conservative: only formats whose
# rendering is genuinely Markdown (not, say, reStructuredText).
_MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdown", ".mkd", ".mdwn", ".mdtxt")

# A line that is *only* a single image reference: ![alt](src "optional title").
_IMG_LINE_RE = re.compile(
    r'^!\[(?P<alt>[^\]]*)\]\(\s*'
    r'(?P<src>[^)\s]+)'
    r'(?:\s+"[^"]*"|\s+\'[^\']*\')?\s*\)$'
)
_REMOTE_SCHEMES = ("http://", "https://", "data:", "ftp://", "ftps://", "//")

# Cap inline ASCII art so a tall image can't dominate the scroll.
_INLINE_MAX_ROWS = 40

# Render-cost tiers. Above _HUGE_CAP bytes a doc opens in the lazy line view;
# at/under it, a doc with <= _MAX_BLOCKS estimated blocks renders interactively
# (Textual MarkdownViewer + TOC) and a denser one renders via Rich in a single
# Static. Opt-in render in the lazy tier is offered only at/under the hard cap.
_HUGE_CAP = 128 * 1024
_MAX_BLOCKS = 600
_RICH_RENDER_HARD_CAP = 1024 * 1024


def looks_markdown(name: object) -> bool:
    """True if ``name`` has a Markdown extension. Cheap, name-only check;
    the caller's size/binary guards still decide whether it's small enough
    to render."""
    return str(name).lower().endswith(_MARKDOWN_SUFFIXES)


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


def _is_remote(src: str) -> bool:
    return src.strip().lower().startswith(_REMOTE_SCHEMES)


def _resolve_image(src: str, base_dir: Path | None) -> Path | None:
    """Resolve a Markdown image ``src`` to a local, decodable image file, or
    ``None`` when it's remote, unresolvable, missing, or not actually a
    recognised image (magic-byte sniff via
    :func:`dunders.fm.image_viewer.sniff_image`)."""
    if base_dir is None or _is_remote(src):
        return None
    raw = unquote(src.strip()).strip("<>")
    # Drop a trailing #fragment that a path can't carry.
    raw = raw.split("#", 1)[0]
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = base_dir / p
    try:
        if not p.is_file():
            return None
        with open(p, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    return p if sniff_image(head) else None


@dataclass(frozen=True)
class _Segment:
    """One render block. ``kind`` is ``"md"`` (``text`` is a Markdown source
    chunk) or ``"img"`` (``text`` is the alt caption, ``path`` the image)."""

    kind: str
    text: str
    path: Path | None = None


def split_markdown_blocks(source: str, base_dir: Path | None) -> list[_Segment]:
    """Split ``source`` into render segments. A line that is *only* a
    standalone image whose src resolves to a local decodable image (relative
    to ``base_dir``) becomes an ``"img"`` segment; every other line
    accumulates into coalesced ``"md"`` segments so headings/lists render
    normally. ``base_dir=None`` (VFS member, or Pillow unavailable) keeps all
    lines as Markdown."""
    segments: list[_Segment] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        text = "\n".join(buf).strip("\n")
        if text.strip():
            segments.append(_Segment("md", text))
        buf.clear()

    for line in source.splitlines():
        match = _IMG_LINE_RE.match(line.strip())
        path = _resolve_image(match.group("src"), base_dir) if match else None
        if path is not None:
            flush()
            segments.append(_Segment("img", match.group("alt"), path))
        else:
            buf.append(line)
    flush()
    return segments


class _InlineImage(Static):
    """A standalone Markdown image drawn as inline ASCII art.

    Recomputes the art at the current width on resize (a precomputed Static
    wouldn't reflow), capped at :data:`_INLINE_MAX_ROWS` rows so one picture
    can't swamp the document. Height is ``auto`` so it flows in the scroll."""

    DEFAULT_CSS = """
    _InlineImage {
        height: auto;
        width: 1fr;
        margin: 1 0;
    }
    """

    def __init__(self, path: Path, alt: str) -> None:
        super().__init__()
        self._path = path
        self._alt = alt
        self._cached_w = -1
        self._art = Text()

    @property
    def art(self) -> Text:
        """The most recently rendered ASCII-art ``Text`` (for inspection/tests)."""
        return self._art

    def on_mount(self) -> None:
        self._rerender()

    def on_resize(self) -> None:
        self._rerender()

    def _rerender(self) -> None:
        cols = max(1, self.size.width or 80)
        if cols == self._cached_w:
            return
        self._cached_w = cols
        self._art = self._build_text(cols)
        self.update(self._art)

    def _build_text(self, cols: int) -> Text:
        try:
            from PIL import Image as _PILImage

            with _PILImage.open(self._path) as im:
                im.seek(0)  # frame 0 for animated formats
                rgb = im.convert("RGB")
                rgb.load()
            out_w, out_h = _fit(rgb.width, rgb.height, cols, _INLINE_MAX_ROWS)
            resized = rgb.resize((out_w, out_h))
            getdata = getattr(resized, "get_flattened_data", None) or resized.getdata
            grid = image_to_ascii(list(getdata()), out_w, out_h, color=True)
        except Exception:
            label = self._alt or self._path.name
            return Text(f"🖼  ({label}) — could not render", style=RichStyle(dim=True))
        text = Text()
        for row in grid:
            for char, rgb_cell in row:
                style = (
                    RichStyle(color=RichColor.from_rgb(*rgb_cell))
                    if rgb_cell is not None
                    else None
                )
                text.append(char, style=style)
            text.append("\n")
        if self._alt:
            text.append(self._alt, style=RichStyle(dim=True, italic=True))
        return text


class MarkdownViewerContent(WindowContent):
    """WindowContent rendering Markdown, with inline ASCII-art images."""

    DEFAULT_CSS = """
    MarkdownViewerContent { background: transparent; }
    MarkdownViewerContent .md-toolbar {
        height: 1;
        background: $panel;
    }
    MarkdownViewerContent MarkdownViewer,
    MarkdownViewerContent .md-doc {
        height: 1fr;
        width: 1fr;
    }
    MarkdownViewerContent .md-doc Markdown {
        height: auto;
    }
    MarkdownViewerContent .md-raw {
        height: 1fr;
        width: 1fr;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        file_path: str | Path | None = None,
        text: str | None = None,
        display_name: str | None = None,
    ) -> None:
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

        self._byte_size = size

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

        # The literal-source ("raw") surface, shown when _raw_mode is True. For
        # the huge tier it IS the lazy line view (no in-memory source); otherwise
        # a Static with markup disabled so source text containing markup-like
        # tokens (e.g. "[/]") can't raise MarkupError.
        if self._tier == "lazy":
            self._raw_view = _LazyTextView(self._make_lazy_source())
        else:
            self._raw_view = VerticalScroll(
                Static(self._source_text, classes="md-source", markup=False),
                classes="md-raw",
            )
            self._raw_view.can_focus = True

        # The huge tier opens raw (instant) and renders lazily on the first
        # toggle. A file larger than the hard cap would freeze the render, so it
        # is never offered (raw-only — no toggle button).
        self._raw_mode = self._tier == "lazy"
        self._can_render = (
            self._tier != "lazy" or self._byte_size <= _RICH_RENDER_HARD_CAP
        )
        self._rendered = None  # built in compose (eager) or on first toggle (lazy)

        self._raw_btn = _ToolbarButton(
            "[ Rendered ]" if self._raw_mode else "[ Raw ]",
            on_press=self._toggle_raw,
        )
        self._raw_btn.id = "md-raw-toggle"
        self._toc_btn = _ToolbarButton("[ Contents ]", on_press=self._toggle_toc)
        self._toc_btn.id = "md-toc-toggle"
        self._fill_timer = None

    def _choose_tier(self) -> str:
        if self._byte_size > _HUGE_CAP:
            return "lazy"
        if self._has_images:
            return "interactive"  # composed renderer (inline ASCII images)
        if estimate_blocks(self._source_text or "") <= _MAX_BLOCKS:
            return "interactive"
        return "rich"

    @property
    def tier(self) -> str:
        return self._tier

    @classmethod
    def from_text(cls, name: str, text: str) -> "MarkdownViewerContent":
        """Build a Markdown viewer over an in-memory string (e.g. a member
        read through a VFS provider where there is no local path)."""
        return cls(text=text, display_name=name)

    @classmethod
    def from_bytes(cls, name: str, data: bytes) -> "MarkdownViewerContent":
        """Build a Markdown viewer over raw bytes; decoded as UTF-8 with
        lossy replacement so a stray byte never aborts the open."""
        return cls(text=data.decode("utf-8", errors="replace"), display_name=name)

    def _build_document(self) -> VerticalScroll:
        """Composed renderer: text chunks as ``Markdown`` widgets, standalone
        images as inline ASCII art, stacked in a single vertical scroll."""
        children: list = []
        for seg in self._segments:
            if seg.kind == "img" and seg.path is not None:
                children.append(_InlineImage(seg.path, seg.text))
            else:
                children.append(Markdown(seg.text, open_links=False))
        doc = VerticalScroll(*children, classes="md-doc")
        doc.can_focus = True
        return doc

    def compose(self) -> ComposeResult:
        # Build the rendered surface eagerly except for the huge tier, which
        # opens raw and renders lazily on the first toggle.
        if self._tier != "lazy":
            self._rendered = self._build_rendered_surface()
        with Horizontal(classes="md-toolbar"):
            # The Raw/Rendered toggle is meaningful only when both modes exist.
            if self._can_render:
                yield self._raw_btn
            # TOC only exists for the plain interactive MarkdownViewer.
            if self._tier == "interactive" and not self._has_images:
                yield self._toc_btn
        yield self._raw_view
        if self._rendered is not None:
            yield self._rendered
        self._raw_view.display = self._raw_mode
        if self._rendered is not None:
            self._rendered.display = not self._raw_mode

    def _build_rendered_surface(self):
        """Build the formatted ("rendered") surface. Loads the source text if it
        was not read at open (the huge tier). Image docs use the composed
        renderer; the plain interactive tier uses Textual's MarkdownViewer;
        everything else (dense, and huge-rendered-on-demand) renders via Rich in
        a single Static."""
        if self._source_text is None and self._path is not None:
            try:
                self._source_text = self._path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError as exc:
                self._source_text = f"# Could not read file\n\n{exc}"
        if self._has_images:
            return self._build_document()
        if self._tier == "interactive":
            self._viewer = MarkdownViewer(
                self._source_text or "", show_table_of_contents=False, open_links=False
            )
            self._viewer.can_focus = True
            return self._viewer
        return VerticalScroll(
            Static(_RichMarkdown(self._source_text or ""), classes="md-rich"),
            classes="md-doc",
        )

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
        visible = self._raw_view if self._raw_mode else self._rendered
        if visible is not None:
            visible.focus()
        self._update_subtitle()
        # Grow the lazy index in the background so the scrollbar settles without
        # blocking the open (mirrors CsvViewerContent). The lazy view is the raw
        # surface and is never swapped, so the timer target is stable.
        src = getattr(self._raw_view, "source", None)
        if src is not None and not src.is_complete():
            self._fill_timer = self.set_interval(0.05, self._fill_tick)

    def _fill_tick(self) -> None:
        src = getattr(self._raw_view, "source", None)
        if src is None:
            if self._fill_timer is not None:
                self._fill_timer.stop()
                self._fill_timer = None
            return
        more = src.index_batch(2000)
        self._raw_view._resize_canvas()
        self._raw_view.refresh()
        if not more and self._fill_timer is not None:
            self._fill_timer.stop()
            self._fill_timer = None

    def on_unmount(self) -> None:
        if self._fill_timer is not None:
            self._fill_timer.stop()
            self._fill_timer = None
        src = getattr(self._raw_view, "source", None)
        if src is not None:
            src.close()

    @property
    def viewer(self) -> MarkdownViewer | None:
        """The plain ``MarkdownViewer`` for image-free docs, else ``None``
        (image docs use the composed renderer exposed via :attr:`document`)."""
        return self._viewer

    @property
    def document(self):
        """The active rendered surface (``MarkdownViewer`` or the composed
        ``VerticalScroll``)."""
        return self._rendered

    @property
    def has_images(self) -> bool:
        return self._has_images

    @property
    def image_count(self) -> int:
        return self._image_count

    @property
    def raw_mode(self) -> bool:
        return self._raw_mode

    @property
    def show_toc(self) -> bool:
        return self._show_toc

    def _update_subtitle(self) -> None:
        if self._source_text is not None:
            lines = self._source_text.count("\n") + 1
        else:
            # Huge lazy file — use the indexed line count as a lower bound.
            src = getattr(self._raw_view, "source", None)
            lines = src.line_count() if src is not None else 0
        mode = "RAW" if self._raw_mode else "RENDERED"
        parts = [f"{lines} lines", mode]
        if self._has_images:
            n = self._image_count
            parts.append(f"{n} image{'s' if n != 1 else ''}")
        self.window_subtitle = "  ·  ".join(parts)

    def _toggle_raw(self) -> None:
        # Raw-only (a huge file too large to render): nothing to toggle.
        if not self._can_render:
            return
        going_to_raw = not self._raw_mode
        if not going_to_raw and self._rendered is None:
            # First switch to Rendered for the huge tier: build the formatted
            # surface now (the expensive render the lazy tier deferred) and mount
            # it after the raw view.
            self._rendered = self._build_rendered_surface()
            self.mount(self._rendered, after=self._raw_view)
        self._raw_mode = going_to_raw
        self._raw_view.display = self._raw_mode
        if self._rendered is not None:
            self._rendered.display = not self._raw_mode
        self._raw_btn.set_label("[ Rendered ]" if self._raw_mode else "[ Raw ]")
        visible = self._raw_view if self._raw_mode else self._rendered
        if visible is not None:
            visible.focus()
        self._update_subtitle()

    def _toggle_toc(self) -> None:
        # The outline only exists for the plain MarkdownViewer; the composed
        # image renderer, rich tier, lazy tier, and raw view have nothing to toggle.
        if self._viewer is None or self._raw_mode:
            return
        self._show_toc = not self._show_toc
        self._viewer.show_table_of_contents = self._show_toc
        self._toc_btn.set_label(
            "[ Hide TOC ]" if self._show_toc else "[ Contents ]"
        )

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(
                id="markdown.toggle_raw",
                label="Toggle Raw/Rendered",
                handler=self._toggle_raw,
                hotkey="t",
            ),
            WindowCommand(
                id="markdown.toggle_toc",
                label="Toggle Contents",
                handler=self._toggle_toc,
                hotkey="c",
            ),
        ]


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

    def action_scroll_page(self, sign: int) -> None:
        page = max(1, self.size.height - 2)
        self.scroll_to(
            self.scroll_offset.x, self.scroll_offset.y + sign * page, animate=False
        )
