"""ImageViewerContent — view image files as ASCII art (F3).

Pillow is imported lazily and guarded by PILLOW_AVAILABLE so the base
install stays dependency-light; the decode/resize step is the only place
that touches Pillow. The pixel->grid transform (`image_to_ascii`), the
aspect-fit helper (`_fit`), and the magic-byte sniffer (`sniff_image`) are
pure and import nothing heavy, so they unit-test in isolation.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from rich.color import Color as RichColor
from rich.style import Style as RichStyle
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.content import Content
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Button

from dunders.windowing.content import WindowContent, WindowCommand
from dunders.windowing.palette import Palette

try:  # Pillow is an opt-in extra (`pip install dunders[image]`).
    from PIL import Image as _PILImage

    PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    _PILImage = None
    PILLOW_AVAILABLE = False

__all__ = [
    "PILLOW_AVAILABLE",
    "sniff_image",
    "image_to_ascii",
    "ImageViewerContent",
    "ImageViewerWidget",
]

# Brightness ramp from darkest (space) to brightest ('@').
_RAMP = " .:-=+*#%@"


def sniff_image(head: bytes) -> bool:
    """True if `head` (first ~16 bytes of a file) starts with a known
    image magic signature. Extension is irrelevant."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return True
    if head.startswith(b"BM"):
        return True
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return False


def _fit(
    img_w: int,
    img_h: int,
    max_cols: int,
    max_rows: int,
    cell_aspect: float = 0.5,
) -> tuple[int, int]:
    """Fit an `img_w`x`img_h` image into a `max_cols`x`max_rows` character
    grid, correcting for the terminal cell aspect ratio (a cell is ~twice
    as tall as wide, so `cell_aspect` 0.5 squashes the row count)."""
    img_w = max(1, img_w)
    img_h = max(1, img_h)
    out_w = max(1, max_cols)
    out_h = max(1, int(out_w * img_h / img_w * cell_aspect))
    if out_h > max_rows:
        out_h = max(1, max_rows)
        out_w = max(1, int(out_h * img_w / img_h / cell_aspect))
    return out_w, out_h


def _ramp_char(lum: float) -> str:
    """Map a luminance value (0.0-255.0) onto the brightness ramp."""
    idx = round(lum / 255 * (len(_RAMP) - 1))
    idx = max(0, min(idx, len(_RAMP) - 1))
    return _RAMP[idx]


def image_to_ascii(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    *,
    color: bool,
) -> list[list[tuple[str, tuple[int, int, int] | None]]]:
    """Turn a row-major flat list of RGB pixels into a grid of
    (char, rgb_or_None) cells. In mono mode the rgb element is None; in
    color mode it carries the pixel's RGB for a truecolor foreground.
    `pixels` must contain at least `width * height` entries (row-major); a
    short list is padded with black."""
    # Defensive: a corrupt/partial decode could hand us fewer pixels than
    # width*height. Pad the shortfall with black rather than raising
    # IndexError mid-render (callers should still supply width*height).
    needed = width * height
    if len(pixels) < needed:
        pixels = list(pixels) + [(0, 0, 0)] * (needed - len(pixels))
    grid: list[list[tuple[str, tuple[int, int, int] | None]]] = []
    for y in range(height):
        row: list[tuple[str, tuple[int, int, int] | None]] = []
        base = y * width
        for x in range(width):
            r, g, b = pixels[base + x]
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            char = _ramp_char(lum)
            row.append((char, (r, g, b) if color else None))
        grid.append(row)
    return grid


class ImageViewerWidget(ScrollView):
    """Renders a decoded image as an ASCII-art grid."""

    DEFAULT_CSS = """
    ImageViewerWidget {
        background: $surface;
        color: $text;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("up",       "scroll_lines(-1)", show=False),
        Binding("down",     "scroll_lines(1)",  show=False),
        Binding("pageup",   "scroll_page(-1)",  show=False),
        Binding("pagedown", "scroll_page(1)",   show=False),
        Binding("home",     "scroll_home",      show=False),
        Binding("end",      "scroll_end",       show=False),
    ]

    def __init__(self, file_path: str | Path) -> None:
        super().__init__()
        self._path = Path(file_path)
        self._color = True
        self._img_size: tuple[int, int] = (0, 0)
        self._grid: list[list[tuple[str, tuple[int, int, int] | None]]] = []

    @property
    def color(self) -> bool:
        return self._color

    @property
    def img_size(self) -> tuple[int, int]:
        return self._img_size

    def on_mount(self) -> None:
        self._regenerate()

    def on_resize(self) -> None:
        self._regenerate()

    def set_color(self, color: bool) -> None:
        if color == self._color:
            return
        self._color = color
        self._regenerate()

    def toggle_color(self) -> None:
        self.set_color(not self._color)

    def _regenerate(self) -> None:
        if not PILLOW_AVAILABLE:
            return
        cols = max(1, self.size.width or 80)
        rows = max(1, self.size.height or 24)
        try:
            with _PILImage.open(self._path) as im:
                im.seek(0)  # first frame for animated GIFs
                rgb = im.convert("RGB")
                self._img_size = rgb.size
                out_w, out_h = _fit(rgb.width, rgb.height, cols, rows)
                resized = rgb.resize((out_w, out_h))
                # `get_flattened_data` replaces the deprecated `getdata`
                # in Pillow 14; fall back for older releases.
                getter = getattr(
                    resized, "get_flattened_data", resized.getdata
                )
                pixels = list(getter())
        except Exception:
            self._grid = []
            self.virtual_size = Size(0, 1)
            self.refresh()
            return
        self._grid = image_to_ascii(pixels, out_w, out_h, color=self._color)
        self.virtual_size = Size(out_w, len(self._grid))
        if self.is_mounted:
            self.refresh()

    def _get_palette(self) -> Palette | None:
        with suppress(Exception):
            for ancestor in self.ancestors_with_self:
                pal = getattr(ancestor, "palette", None)
                if isinstance(pal, Palette):
                    return pal
        return None

    def _base_style(self) -> RichStyle:
        pal = self._get_palette()
        if pal is None:
            return RichStyle()
        return pal.rich_style("editor.text")

    def render_line(self, y: int) -> Strip:
        idx = y + int(self.scroll_offset.y)
        if idx >= len(self._grid):
            return Strip.blank(self.size.width, self.rich_style)
        base = self._base_style()
        text = Text(style=self.rich_style)
        for char, rgb in self._grid[idx]:
            if rgb is None:
                text.append(char, style=base)
            else:
                text.append(
                    char, style=RichStyle(color=RichColor.from_rgb(*rgb))
                )
        return Strip(text.render(self.app.console))

    def action_scroll_lines(self, delta: int) -> None:
        self.scroll_to(
            self.scroll_offset.x, self.scroll_offset.y + delta, animate=False
        )

    def action_scroll_page(self, sign: int) -> None:
        page = max(1, self.size.height - 2)
        self.scroll_to(
            self.scroll_offset.x,
            self.scroll_offset.y + sign * page,
            animate=False,
        )

    def action_scroll_home(self) -> None:
        self.scroll_to(0, 0, animate=False)

    def action_scroll_end(self) -> None:
        self.scroll_to(
            0, max(0, len(self._grid) - max(1, self.size.height)), animate=False
        )


class ImageViewerContent(WindowContent):
    """WindowContent wrapping :class:`ImageViewerWidget` with a color toggle."""

    DEFAULT_CSS = """
    ImageViewerContent { background: transparent; }
    ImageViewerContent .img-toolbar {
        height: 1;
        background: $panel;
    }
    ImageViewerContent .img-toolbar Button {
        min-width: 10;
        height: 1;
        border: none;
    }
    ImageViewerContent ImageViewerWidget {
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self, file_path: str | Path) -> None:
        super().__init__()
        self._path = Path(file_path)
        self.window_title = f"Image: {self._path.name}"
        self._widget = ImageViewerWidget(file_path)
        # Wrap labels in Content so the literal brackets aren't parsed as
        # Textual content markup (which would render them as empty tags).
        self._button = Button(Content("[ Color ]"), id="img-color-toggle")

    def compose(self) -> ComposeResult:
        with Horizontal(classes="img-toolbar"):
            yield self._button
        yield self._widget

    def on_mount(self) -> None:
        self._widget.focus()
        self._update_subtitle()

    @property
    def widget(self) -> ImageViewerWidget:
        return self._widget

    def _update_subtitle(self) -> None:
        w, h = self._widget.img_size
        mode = "COLOR" if self._widget.color else "MONO"
        self.window_subtitle = f"{w}x{h}  ·  {mode}"

    def _toggle_color(self) -> None:
        self._widget.toggle_color()
        self._button.label = Content(
            "[ Color ]" if self._widget.color else "[ Mono ]"
        )
        self._update_subtitle()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button is self._button:
            self._toggle_color()

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(
                id="image.toggle_color",
                label="Toggle Color/Mono",
                handler=self._toggle_color,
                hotkey="c",
            ),
        ]
