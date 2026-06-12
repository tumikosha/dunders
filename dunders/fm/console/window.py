"""ConsoleContent — WindowContent rendering a ConsoleBuffer with scroll."""

from __future__ import annotations

from rich.segment import Segment as RichSegment
from rich.style import Style as RichStyle
from textual import events
from textual.app import ComposeResult
from textual.strip import Strip
from textual.widget import Widget

from dunders.windowing.content import WindowCommand, WindowContent

from .ansi import Style as AnsiStyle
from .buffer import ConsoleBuffer


def _to_rich_style(style: AnsiStyle) -> RichStyle:
    kwargs: dict = {}
    if style.fg:
        kwargs["color"] = style.fg
    if style.bg:
        kwargs["bgcolor"] = style.bg
    if style.bold:
        kwargs["bold"] = True
    if style.dim:
        kwargs["dim"] = True
    if style.italic:
        kwargs["italic"] = True
    if style.underline:
        kwargs["underline"] = True
    return RichStyle(**kwargs)


_SCROLLBAR_TRACK_STYLE = RichStyle(color="grey50")
_SCROLLBAR_THUMB_STYLE = RichStyle(color="grey70", bgcolor="grey30")


class _BufferView(Widget, can_focus=True):
    DEFAULT_CSS = """
    _BufferView { width: 100%; height: 100%; }
    """

    def __init__(self, buffer: ConsoleBuffer) -> None:
        super().__init__()
        self.buffer = buffer
        self._sb_dragging = False

    def render_line(self, y: int) -> Strip:
        height = self.size.height
        width = self.size.width
        if height <= 0 or width <= 0:
            return Strip.blank(0)
        total = self.buffer.line_count()
        content_width = max(0, width - 1)  # last column reserved for scrollbar
        first = max(0, total - height - self.buffer.view_offset)
        idx = first + y
        if idx < 0 or idx >= total:
            content_strip = Strip([RichSegment(" " * content_width)])
        else:
            line = self.buffer.line(idx)
            rich_segs = [RichSegment(seg.text, _to_rich_style(seg.style)) for seg in line]
            content_strip = Strip(rich_segs).adjust_cell_length(content_width)
        ch, style = self._scrollbar_cell(y, height, total)
        bar = Strip([RichSegment(ch, style)])
        return Strip.join([content_strip, bar])

    def _scrollbar_cell(self, y: int, view_h: int, total: int) -> tuple[str, RichStyle]:
        if total <= 0 or view_h <= 0 or total <= view_h:
            return "│", _SCROLLBAR_TRACK_STYLE
        thumb_size = max(1, view_h * view_h // total)
        max_offset = total - view_h
        offset = min(self.buffer.view_offset, max_offset)
        # offset=0 (bottom) -> thumb at bottom; offset=max_offset (top) -> thumb at top.
        frac_from_top = (max_offset - offset) / max_offset
        thumb_top = int(round((view_h - thumb_size) * frac_from_top))
        if thumb_top <= y < thumb_top + thumb_size:
            return "█", _SCROLLBAR_THUMB_STYLE
        return "│", _SCROLLBAR_TRACK_STYLE

    # Use the system handler name (leading underscore) so the event is
    # caught at the same dispatch layer as Textual's built-in scroll,
    # before the bubble-up walk to non-scrollable ancestors swallows it.
    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._scroll_view(+3)
        event.stop()
        event.prevent_default()

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._scroll_view(-3)
        event.stop()
        event.prevent_default()

    # --- scrollbar drag --------------------------------------------------
    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        if event.x != self.size.width - 1:
            return  # not on the scrollbar column
        self._sb_dragging = True
        self.capture_mouse()
        self._set_view_from_y(event.y)
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._sb_dragging:
            return
        self._set_view_from_y(event.y)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._sb_dragging:
            return
        self._sb_dragging = False
        self.release_mouse()
        event.stop()

    def _set_view_from_y(self, y: int) -> None:
        """Map a mouse-y on the scrollbar track to a buffer view offset."""
        view_h = max(1, self.size.height)
        total = self.buffer.line_count()
        max_offset = max(0, total - view_h)
        if max_offset == 0:
            return
        thumb_size = max(1, view_h * view_h // total)
        track_range = max(1, view_h - thumb_size)
        # Place the top of the thumb at y (clamped). y=0 -> top of buffer
        # (max_offset); y=track_range -> bottom (offset 0).
        thumb_top = max(0, min(track_range, y))
        frac_from_top = thumb_top / track_range
        new_offset = round(max_offset * (1 - frac_from_top))
        delta = new_offset - self.buffer.view_offset
        if delta > 0:
            self.buffer.scroll_up(delta)
        elif delta < 0:
            self.buffer.scroll_down(-delta)
        self.refresh()

    def _scroll_view(self, by: int) -> None:
        # Clamp the buffer's view offset to the actual scrollback so the
        # thumb has a meaningful range and we don't accumulate offset past
        # the top of the buffer (where the view would no longer change but
        # the user has to scroll back down the same number of ticks).
        height = max(1, self.size.height)
        max_offset = max(0, self.buffer.line_count() - height)
        offset = self.buffer.view_offset
        new_offset = max(0, min(max_offset, offset + by))
        delta = new_offset - offset
        if delta > 0:
            self.buffer.scroll_up(delta)
        elif delta < 0:
            self.buffer.scroll_down(-delta)
        self.refresh()


class ConsoleContent(WindowContent):
    """WindowContent that renders a ConsoleBuffer with scroll support."""

    def __init__(self, window_id: str) -> None:
        super().__init__(id=window_id)
        self.buffer = ConsoleBuffer()
        self.busy = False
        self._view: _BufferView | None = None

    def compose(self) -> ComposeResult:
        self._view = _BufferView(self.buffer)
        yield self._view

    def append(self, data: bytes) -> None:
        self.buffer.append_bytes(data)
        if self._view is not None:
            self._view.refresh()

    # Safety net: if a wheel event lands on ConsoleContent rather than the
    # _BufferView child (e.g. during a layout reflow), still scroll.
    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._scroll(+3)
        event.stop()
        event.prevent_default()

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._scroll(-3)
        event.stop()
        event.prevent_default()

    def mark_done(self, rc: int) -> None:
        self.busy = False
        if rc != 0:
            self.append(f"[exit {rc}]\n".encode())

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(
                id="console.scroll_up",
                label="Scroll up",
                handler=lambda: self._scroll(+10),
                hotkey="pageup",
            ),
            WindowCommand(
                id="console.scroll_down",
                label="Scroll down",
                handler=lambda: self._scroll(-10),
                hotkey="pagedown",
            ),
            WindowCommand(
                id="console.scroll_top",
                label="Scroll to top",
                handler=self._scroll_top,
                hotkey="ctrl+home",
            ),
            WindowCommand(
                id="console.scroll_bottom",
                label="Scroll to bottom",
                handler=self._scroll_bottom,
                hotkey="ctrl+end",
            ),
            WindowCommand(
                id="console.clear",
                label="Clear",
                handler=self._action_clear,
                hotkey="ctrl+l",
            ),
        ]

    def _scroll(self, by: int) -> None:
        if by > 0:
            self.buffer.scroll_up(by)
        else:
            self.buffer.scroll_down(-by)
        if self._view is not None:
            self._view.refresh()

    def _scroll_top(self) -> None:
        self.buffer.scroll_to_top()
        if self._view is not None:
            self._view.refresh()

    def _scroll_bottom(self) -> None:
        self.buffer.scroll_to_bottom()
        if self._view is not None:
            self._view.refresh()

    def _action_clear(self) -> None:
        self.buffer.clear()
        if self._view is not None:
            self._view.refresh()
