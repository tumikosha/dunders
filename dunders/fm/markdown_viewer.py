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
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Markdown, MarkdownViewer, Static

from dunders.fm.image_viewer import (
    PILLOW_AVAILABLE,
    _fit,
    _ToolbarButton,
    image_to_ascii,
    sniff_image,
)
from dunders.windowing.content import WindowContent, WindowCommand

__all__ = ["looks_markdown", "split_markdown_blocks", "MarkdownViewerContent"]

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


def looks_markdown(name: object) -> bool:
    """True if ``name`` has a Markdown extension. Cheap, name-only check;
    the caller's size/binary guards still decide whether it's small enough
    to render."""
    return str(name).lower().endswith(_MARKDOWN_SUFFIXES)


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
        if text is not None:
            self._source = text
        elif self._path is not None:
            try:
                self._source = self._path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                self._source = f"# Could not read file\n\n{exc}"
        else:
            self._source = ""

        # Images can only be resolved relative to a local directory and only
        # rendered when Pillow is present; otherwise treat everything as text.
        base_dir = (
            self._path.parent
            if (self._path is not None and PILLOW_AVAILABLE)
            else None
        )
        self._segments = split_markdown_blocks(self._source, base_dir)
        self._image_count = sum(1 for s in self._segments if s.kind == "img")
        self._has_images = self._image_count > 0
        self._show_toc = False
        self._raw_mode = False

        if self._has_images:
            self._viewer: MarkdownViewer | None = None
            self._rendered = self._build_document()
        else:
            self._viewer = MarkdownViewer(
                self._source, show_table_of_contents=False, open_links=False
            )
            # MarkdownViewer defaults to can_focus=False, so focusing it on
            # mount would be a no-op and arrow/wheel scroll wouldn't work until
            # the user clicked the document. Make it focusable like the composed
            # (_build_document) and raw (_raw_view) surfaces.
            self._viewer.can_focus = True
            self._rendered = self._viewer

        self._raw_view = VerticalScroll(
            Static(self._source, classes="md-source"), classes="md-raw"
        )
        self._raw_view.can_focus = True
        self._raw_view.display = False
        self._raw_btn = _ToolbarButton("[ Raw ]", on_press=self._toggle_raw)
        self._raw_btn.id = "md-raw-toggle"
        self._toc_btn = _ToolbarButton("[ Contents ]", on_press=self._toggle_toc)
        self._toc_btn.id = "md-toc-toggle"

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
        with Horizontal(classes="md-toolbar"):
            yield self._raw_btn
            # The outline only exists for the plain MarkdownViewer; the
            # composed image renderer has no aggregated TOC, so don't offer a
            # dead button there.
            if self._viewer is not None:
                yield self._toc_btn
        yield self._rendered
        yield self._raw_view

    def on_mount(self) -> None:
        self._rendered.focus()
        self._update_subtitle()

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
        lines = self._source.count("\n") + 1
        mode = "RAW" if self._raw_mode else "RENDERED"
        parts = [f"{lines} lines", mode]
        if self._has_images:
            n = self._image_count
            parts.append(f"{n} image{'s' if n != 1 else ''}")
        self.window_subtitle = "  ·  ".join(parts)

    def _toggle_raw(self) -> None:
        self._raw_mode = not self._raw_mode
        self._rendered.display = not self._raw_mode
        self._raw_view.display = self._raw_mode
        self._raw_btn.set_label("[ Rendered ]" if self._raw_mode else "[ Raw ]")
        (self._raw_view if self._raw_mode else self._rendered).focus()
        self._update_subtitle()

    def _toggle_toc(self) -> None:
        # The outline only exists for the plain MarkdownViewer; the composed
        # image renderer and raw view have nothing to toggle.
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
