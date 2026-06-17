"""HexViewerContent — chunked hex/text viewer for large files (F3).

Backed by ``mmap`` so a multi-gigabyte file doesn't get pulled into RAM:
each visible line reads only the 16 bytes it needs through the OS page
cache. Falls back to seek+read for files where mmap can't be created
(empty files, special files, or environments where mmap is restricted).

Two display modes share the same scroll position semantics:
  * ``hex``  — classic offset / 16 bytes / 16-char ASCII column.
  * ``text`` — latin-1 decoded view, control bytes shown as ``.``;
              wraps at a fixed column width so virtual_size stays stable
              without scanning the file for newlines.

String search uses ``mmap.find`` (or a chunked fallback) and centres the
match in the viewport. ``Ctrl+G`` repeats the last search from the next
byte after the previous hit.
"""

from __future__ import annotations

import mmap
import os
from contextlib import suppress
from pathlib import Path

from rich.style import Style as RichStyle
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.geometry import Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from dunders.windowing.content import WindowContent, WindowCommand
from dunders.windowing.palette import Palette


__all__ = ["HexViewerContent", "HexViewerWidget"]


_BYTES_PER_LINE = 16
_TEXT_COLS = 80
# Translation table that turns control / non-ASCII bytes into '.' for the
# ASCII column. Built once at import time.
_PRINTABLE = bytes(b if 32 <= b < 127 else ord(".") for b in range(256))


class HexViewerWidget(ScrollView):
    """Lazy hex/text viewer over an mmap'd file."""

    DEFAULT_CSS = """
    HexViewerWidget {
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
        Binding("home",     "scroll_top",       show=False),
        Binding("end",      "scroll_bottom",    show=False),
        Binding("ctrl+t",   "toggle_mode",      "Hex/Text"),
        Binding("ctrl+f",   "find",             "Find"),
        Binding("ctrl+g",   "find_next",        "Find Next"),
        Binding("f7",       "find",             show=False),
    ]

    class ModeChanged(Message):
        def __init__(self, widget: "HexViewerWidget", mode: str) -> None:
            super().__init__()
            self.widget = widget
            self.mode = mode

    class FindRequested(Message):
        def __init__(self, widget: "HexViewerWidget") -> None:
            super().__init__()
            self.widget = widget

    class SearchResult(Message):
        def __init__(
            self,
            widget: "HexViewerWidget",
            found: bool,
            query: bytes,
            pos: int,
        ) -> None:
            super().__init__()
            self.widget = widget
            self.found = found
            self.query = query
            self.pos = pos

    def __init__(
        self,
        file_path: str | Path | None = None,
        *,
        data: bytes | None = None,
    ) -> None:
        """Two source modes:

        * ``file_path`` — lazy mmap/seek over a real on-disk file (default).
        * ``data`` — an in-memory byte buffer already read elsewhere (e.g. a
          file pulled over a VFS provider like SFTP, where there is no local
          path to mmap). Reads are simple slices of the buffer.
        """
        super().__init__()
        self._path = Path(file_path) if file_path is not None else None
        self._mode: str = "hex"
        self._mm: mmap.mmap | None = None
        self._fh = None
        self._data: bytes | None = bytes(data) if data is not None else None
        self._file_size: int = 0
        self._last_search: bytes = b""
        self._search_pos: int = 0
        if self._data is not None:
            self._file_size = len(self._data)
        else:
            self._open_file()
        self._update_virtual_size()

    # --- file lifecycle -------------------------------------------------

    def _open_file(self) -> None:
        try:
            self._fh = open(self._path, "rb")
        except OSError:
            self._fh = None
            self._file_size = 0
            return
        try:
            self._file_size = os.fstat(self._fh.fileno()).st_size
        except OSError:
            self._file_size = 0
        if self._file_size > 0:
            with suppress(ValueError, OSError):
                self._mm = mmap.mmap(
                    self._fh.fileno(), 0, access=mmap.ACCESS_READ
                )

    def on_unmount(self) -> None:
        if self._mm is not None:
            with suppress(Exception):
                self._mm.close()
            self._mm = None
        if self._fh is not None:
            with suppress(Exception):
                self._fh.close()
            self._fh = None

    # --- mode + sizing --------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def file_size(self) -> int:
        return self._file_size

    def set_mode(self, mode: str) -> None:
        if mode not in ("hex", "text") or mode == self._mode:
            return
        self._mode = mode
        self._update_virtual_size()
        if self.is_mounted:
            self.scroll_to(0, 0, animate=False)
            self.refresh()
            self.post_message(HexViewerWidget.ModeChanged(self, mode))

    def _bytes_per_line(self) -> int:
        return _BYTES_PER_LINE if self._mode == "hex" else _TEXT_COLS

    def _line_width(self) -> int:
        # 8 (offset) + 2 + 16*3-1 + 2 (mid gap) + 1 + 1 (sep) + 16 + 1 = ~78
        # Use a slightly conservative width so horizontal scroll doesn't kick
        # in unexpectedly on narrower windows.
        return 78 if self._mode == "hex" else _TEXT_COLS

    def _total_lines(self) -> int:
        if self._file_size == 0:
            return 1
        bpl = self._bytes_per_line()
        return (self._file_size + bpl - 1) // bpl

    def _update_virtual_size(self) -> None:
        with suppress(Exception):
            self.virtual_size = Size(self._line_width(), self._total_lines())

    # --- chunked reads --------------------------------------------------

    def _read(self, offset: int, length: int) -> bytes:
        if self._file_size == 0 or length <= 0:
            return b""
        offset = max(0, min(offset, self._file_size))
        end = max(offset, min(offset + length, self._file_size))
        if self._data is not None:
            return self._data[offset:end]
        if self._mm is not None:
            return self._mm[offset:end]
        if self._fh is not None:
            with suppress(OSError):
                self._fh.seek(offset)
                return self._fh.read(end - offset)
        return b""

    # --- palette --------------------------------------------------------

    def _get_palette(self) -> Palette | None:
        with suppress(Exception):
            for ancestor in self.ancestors_with_self:
                pal = getattr(ancestor, "palette", None)
                if isinstance(pal, Palette):
                    return pal
        return None

    def _rich_style(self, role: str) -> RichStyle:
        pal = self._get_palette()
        if pal is None:
            return RichStyle()
        return pal.rich_style(role)

    # --- rendering ------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        idx = y + int(self.scroll_offset.y)
        if idx >= self._total_lines():
            return Strip.blank(self.size.width, self.rich_style)
        if self._mode == "hex":
            return self._render_hex_line(idx)
        return self._render_text_line(idx)

    def _render_hex_line(self, idx: int) -> Strip:
        offset = idx * _BYTES_PER_LINE
        chunk = self._read(offset, _BYTES_PER_LINE)
        addr_style = self._rich_style("editor.line_numbers")
        sep_style = self._rich_style("editor.line_numbers")
        body_style = self._rich_style("editor.text")

        text = Text(style=self.rich_style)
        text.append(f"{offset:08x}  ", style=addr_style)

        # Two 8-byte groups separated by an extra space.
        for i in range(_BYTES_PER_LINE):
            if i == 8:
                text.append(" ")
            if i < len(chunk):
                text.append(f"{chunk[i]:02x}", style=body_style)
            else:
                text.append("  ")
            if i not in (7, _BYTES_PER_LINE - 1):
                text.append(" ")

        text.append(" │", style=sep_style)
        ascii_col = chunk.translate(_PRINTABLE).decode("latin-1").ljust(
            _BYTES_PER_LINE
        )
        text.append(ascii_col, style=body_style)
        text.append("│", style=sep_style)
        return Strip(text.render(self.app.console))

    def _render_text_line(self, idx: int) -> Strip:
        offset = idx * _TEXT_COLS
        chunk = self._read(offset, _TEXT_COLS)
        decoded = chunk.translate(_PRINTABLE).decode("latin-1")
        text = Text(decoded, style=self._rich_style("editor.text"))
        return Strip(text.render(self.app.console))

    # --- actions --------------------------------------------------------

    def action_scroll_lines(self, delta: int) -> None:
        self.scroll_to(
            self.scroll_offset.x,
            self.scroll_offset.y + delta,
            animate=False,
        )

    def action_scroll_page(self, sign: int) -> None:
        page = max(1, self.size.height - 2)
        self.scroll_to(
            self.scroll_offset.x,
            self.scroll_offset.y + sign * page,
            animate=False,
        )

    def action_scroll_top(self) -> None:
        self.scroll_to(0, 0, animate=False)

    def action_scroll_bottom(self) -> None:
        self.scroll_to(
            0,
            max(0, self._total_lines() - max(1, self.size.height)),
            animate=False,
        )

    def action_toggle_mode(self) -> None:
        self.set_mode("text" if self._mode == "hex" else "hex")

    def action_find(self) -> None:
        self.post_message(HexViewerWidget.FindRequested(self))

    def action_find_next(self) -> None:
        if self._last_search:
            self._do_search(self._last_search, self._search_pos + 1)

    # --- search ---------------------------------------------------------

    def search(self, needle: str | bytes) -> bool:
        """Search forward from the start of the file for ``needle``.

        Returns True if a match was found and the viewport scrolled to it.
        """
        data = self._encode_query(needle)
        if not data:
            return False
        return self._do_search(data, 0)

    @staticmethod
    def _encode_query(needle: str | bytes) -> bytes:
        if isinstance(needle, bytes):
            return needle
        # Try UTF-8 first; fall back to latin-1 for any code-point that
        # doesn't round-trip (so users searching for a literal byte value
        # via its glyph still find it in the file).
        try:
            return needle.encode("utf-8")
        except UnicodeEncodeError:
            return needle.encode("latin-1", errors="replace")

    def _do_search(self, needle: bytes, start: int) -> bool:
        if self._file_size == 0:
            self.post_message(
                HexViewerWidget.SearchResult(self, False, needle, start)
            )
            return False
        start = max(0, min(start, self._file_size))
        pos = self._find_bytes(needle, start)
        # Wrap-around: if not found from `start`, retry from offset 0 so
        # repeated Ctrl+G doesn't dead-end after a single match.
        if pos < 0 and start > 0:
            pos = self._find_bytes(needle, 0)
        self._last_search = needle
        if pos < 0:
            self.post_message(
                HexViewerWidget.SearchResult(self, False, needle, start)
            )
            return False
        self._search_pos = pos
        line = pos // self._bytes_per_line()
        if self.is_mounted:
            target_top = max(0, line - max(1, self.size.height // 2))
            self.scroll_to(0, target_top, animate=False)
            self.refresh()
            self.post_message(
                HexViewerWidget.SearchResult(self, True, needle, pos)
            )
        return True

    def _find_bytes(self, needle: bytes, start: int) -> int:
        if self._data is not None:
            return self._data.find(needle, start)
        if self._mm is not None:
            with suppress(ValueError):
                return self._mm.find(needle, start)
            return -1
        # No mmap: do a chunked scan so we don't pull the whole file in.
        chunk_size = max(len(needle) * 2, 64 * 1024)
        off = start
        overlap = max(0, len(needle) - 1)
        while off < self._file_size:
            data = self._read(off, chunk_size + overlap)
            if not data:
                break
            idx = data.find(needle)
            if idx >= 0:
                return off + idx
            off += chunk_size
        return -1


class HexViewerContent(WindowContent):
    """WindowContent wrapping :class:`HexViewerWidget`."""

    DEFAULT_CSS = """
    HexViewerContent { background: transparent; }
    HexViewerContent HexViewerWidget {
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(
        self,
        file_path: str | Path | None = None,
        *,
        data: bytes | None = None,
        display_name: str | None = None,
    ) -> None:
        super().__init__()
        name = display_name or (Path(file_path).name if file_path else "data")
        self._path = Path(file_path) if file_path is not None else Path(name)
        self.window_title = f"Hex: {name}"
        if data is not None:
            self._widget = HexViewerWidget(data=data)
        else:
            self._widget = HexViewerWidget(file_path)

    @classmethod
    def from_bytes(cls, name: str, data: bytes) -> "HexViewerContent":
        """Build a hex viewer over an in-memory buffer (e.g. a file read
        through a VFS provider where there is no local path to mmap)."""
        return cls(data=data, display_name=name)

    def compose(self) -> ComposeResult:
        yield self._widget

    def on_mount(self) -> None:
        self._widget.focus()
        self._update_subtitle()

    @property
    def widget(self) -> HexViewerWidget:
        return self._widget

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        s = float(size)
        for unit in ("KB", "MB", "GB", "TB"):
            s /= 1024
            if s < 1024:
                return f"{s:.1f} {unit}"
        return f"{s:.1f} PB"

    def _update_subtitle(self) -> None:
        self.window_subtitle = (
            f"{self._widget.mode.upper()}  ·  "
            f"{self._format_size(self._widget.file_size)}"
        )

    def on_hex_viewer_widget_mode_changed(
        self, event: HexViewerWidget.ModeChanged
    ) -> None:
        self._update_subtitle()

    def on_hex_viewer_widget_search_result(
        self, event: HexViewerWidget.SearchResult
    ) -> None:
        if event.found:
            self._notify(f"Found at offset 0x{event.pos:x}")
        else:
            try:
                shown = event.query.decode("utf-8")
            except UnicodeDecodeError:
                shown = repr(event.query)
            self._notify(f"Not found: {shown}")

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(
                id="hex.toggle_mode",
                label="Toggle Hex/Text",
                handler=self._widget.action_toggle_mode,
                hotkey="ctrl+t",
            ),
            WindowCommand(
                id="hex.find",
                label="Find...",
                handler=self._widget.action_find,
                hotkey="ctrl+f",
            ),
            WindowCommand(
                id="hex.find_next",
                label="Find Next",
                handler=self._widget.action_find_next,
                hotkey="ctrl+g",
            ),
        ]

    def _notify(self, message: str) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        with suppress(Exception):
            app.notify(message)
