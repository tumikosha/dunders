"""Splitter widget — 1-cell divider with mouse-drag to resize neighbours."""

from __future__ import annotations

from rich.segment import Segment

from textual import events
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget


class Splitter(Widget):
    """A 1-cell-thick divider that emits Dragged(dx, dy) on mouse drag.

    direction:
      "v-divider" — a vertical bar (1 cell wide, full height) used between
                     side-by-side panes; dragging changes their widths.
      "h-divider" — a horizontal bar (1 cell tall, full width) used between
                     stacked panes; dragging changes their heights.
    """

    DEFAULT_CSS = """
    Splitter {
        background: $panel-darken-2;
    }
    Splitter:hover {
        background: $accent 40%;
    }
    Splitter.-v-divider { width: 1; height: 1fr; }
    Splitter.-h-divider { width: 1fr; height: 1; }
    """

    class Dragged(Message):
        def __init__(self, splitter: "Splitter", dx: int, dy: int) -> None:
            super().__init__()
            self.splitter = splitter
            self.dx = dx
            self.dy = dy

    def __init__(self, direction: str) -> None:
        super().__init__()
        self.direction = direction
        self.add_class(f"-{direction}")
        self._dragging = False

    def set_direction(self, direction: str) -> None:
        if direction == self.direction:
            return
        self.remove_class("-v-divider", "-h-divider")
        self.direction = direction
        self.add_class(f"-{direction}")
        self.refresh()

    def render_line(self, y: int) -> Strip:
        style = self.rich_style
        if self.direction == "v-divider":
            return Strip([Segment("║", style)])  # ║
        return Strip([Segment("═" * self.size.width, style)])  # ═

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:
            self._dragging = True
            self.capture_mouse()
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        if event.delta_x or event.delta_y:
            self.post_message(self.Dragged(self, event.delta_x, event.delta_y))
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            event.stop()
