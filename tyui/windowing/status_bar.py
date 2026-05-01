"""Status bar: TV-style hint strip docked at the bottom of the screen.

Public API:

    from tyui.windowing import StatusBar, StatusItem

Usage in App.compose(): yield the StatusBar after the Desktop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TYPE_CHECKING

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual import events
from textual.strip import Strip
from textual.widget import Widget

from .palette import Palette

if TYPE_CHECKING:
    from .desktop import Desktop


__all__ = ["StatusBar", "StatusItem"]


@dataclass
class StatusItem:
    """A hotkey hint shown in the status bar."""

    key: str
    label: str
    handler: Callable[[], None] | None = None


class StatusBar(Widget):
    """One-line status strip docked at the bottom.

    Displays hotkey hints in Turbo Vision style: key highlighted, label dimmed.
    Supports mouse clicks on items.
    """

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        layer: overlay;
    }
    """

    can_focus = False

    def __init__(
        self,
        items: list[StatusItem] | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._items: list[StatusItem] = items or []
        self._palette: Palette | None = None
        self._item_spans: list[tuple[int, int, StatusItem]] = []
        self._hover_index: int | None = None
        self._pressed_index: int | None = None

    @property
    def items(self) -> list[StatusItem]:
        return self._items

    @items.setter
    def items(self, value: list[StatusItem]) -> None:
        self._items = value
        self.refresh()

    def on_mount(self) -> None:
        desktop = self._find_desktop()
        if desktop:
            self._palette = desktop.palette

    def _find_desktop(self) -> "Desktop | None":
        from .desktop import Desktop

        for sibling in self.app.query(Desktop):
            return sibling
        return None

    def _item_width(self, item: StatusItem) -> int:
        return len(f" {item.key} ") + len(f"{item.label} ")

    def render_line(self, y: int) -> Strip:
        if y != 0:
            return Strip.blank(self.size.width)

        palette = self._palette
        if palette is None:
            desktop = self._find_desktop()
            if desktop:
                self._palette = palette = desktop.palette

        if palette is None:
            from .themes import modern_dark

            palette = Palette(modern_dark)

        key_style = palette.get("statusbar_key").to_rich()
        label_style = palette.get("statusbar_label").to_rich()
        bg_style = palette.get("statusbar_bg").to_rich()

        segments: list[Segment] = []
        self._item_spans = []
        x = 0

        for idx, item in enumerate(self._items):
            item_start = x
            key_text = f" {item.key} "
            label_text = f"{item.label} "
            ks = key_style
            ls = label_style
            interactive = item.handler is not None
            if interactive and idx == self._pressed_index:
                ks = ks + RichStyle(reverse=True)
                ls = ls + RichStyle(reverse=True)
            elif interactive and idx == self._hover_index:
                # Tint the label cell with the bright key bgcolor so the
                # whole item reads as one highlighted button.
                hover_bg = key_style.bgcolor
                if hover_bg is not None:
                    ls = ls + RichStyle(bgcolor=hover_bg)
            segments.append(Segment(key_text, ks))
            segments.append(Segment(label_text, ls))
            x += len(key_text) + len(label_text)
            self._item_spans.append((item_start, x, item))

        total_width = x
        remaining = self.size.width - total_width
        if remaining > 0:
            segments.append(Segment(" " * remaining, bg_style))

        return Strip(segments)

    def _index_at(self, x: int) -> int | None:
        for i, (start, end, _it) in enumerate(self._item_spans):
            if start <= x < end:
                return i
        return None

    def on_mouse_move(self, event: events.MouseMove) -> None:
        idx = self._index_at(event.x)
        if idx is not None and self._items[idx].handler is None:
            idx = None
        if idx != self._hover_index:
            self._hover_index = idx
            self.refresh()

    def on_leave(self, event: events.Leave) -> None:
        if self._hover_index is not None or self._pressed_index is not None:
            self._hover_index = None
            self._pressed_index = None
            self.refresh()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        idx = self._index_at(event.x)
        if idx is None or self._items[idx].handler is None:
            return
        self._pressed_index = idx
        self.refresh()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._pressed_index is not None:
            self._pressed_index = None
            self.refresh()

    def on_click(self, event: events.Click) -> None:
        idx = self._index_at(event.x)
        if idx is None:
            return
        item = self._items[idx]
        if item.handler is not None:
            item.handler()
            event.stop()
