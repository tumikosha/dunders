"""High-level helpers: make_window, show_modal."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from textual import events
from textual.message import Message
from textual.widget import Widget

from .frame import BorderSides, BorderStyle, Decorations, TitleSpec
from .window import Window

if TYPE_CHECKING:
    from .desktop import Desktop


def make_window(
    content: Widget,
    *,
    title: str | TitleSpec = "",
    position: tuple[int, int] = (5, 3),
    size: tuple[int, int] = (40, 12),
    border_focused: BorderStyle = BorderStyle.DOUBLE,
    border_unfocused: BorderStyle = BorderStyle.SINGLE,
    sides: BorderSides | None = None,
    decorations: Decorations | None = None,
    id: str | None = None,
) -> Window:
    if isinstance(title, str):
        title = TitleSpec(text=title)
    return Window(
        content,
        title=title,
        position=position,
        size=size,
        border_focused=border_focused,
        border_unfocused=border_unfocused,
        sides=sides,
        decorations=decorations or Decorations(),
        id=id,
    )


class ModalWindow(Window):
    """A Window with modal behaviour: Esc closes; click outside closes."""

    class Dismissed(Message):
        def __init__(self, window: "ModalWindow") -> None:
            self.window = window
            super().__init__()

    BINDINGS = [("escape", "dismiss", "Close")]

    def action_dismiss(self) -> None:
        self.post_message(ModalWindow.Dismissed(self))


def show_modal(
    desktop: "Desktop",
    content: Widget,
    title: str | TitleSpec = "",
    size: tuple[int, int] = (40, 10),
    decorations: Decorations | None = None,
) -> ModalWindow:
    """Show a modal window centred on the desktop with dim overlay behaviour.

    Returns the modal Window so callers can subscribe to messages if needed.
    The modal is closed by Esc or by clicking outside of it (handled via
    Desktop.on_click routing).
    """
    W, H = desktop.size
    sw, sh = size
    sw = min(sw, max(3, W - 2))
    sh = min(sh, max(3, H - 2))
    x = max(0, (W - sw) // 2)
    y = max(0, (H - sh) // 2)
    if isinstance(title, str):
        title = TitleSpec(text=title, align="center")
    modal = ModalWindow(
        content,
        title=title,
        position=(x, y),
        size=(sw, sh),
        border_focused=BorderStyle.DOUBLE,
        border_unfocused=BorderStyle.DOUBLE,
        decorations=decorations or Decorations(close_box=True),
    )
    # Dim non-modal windows via palette override by tagging them.
    for w in desktop.windows:
        w.palette_override["window.border.unfocused"] = desktop.palette.get("modal.overlay")
    desktop.add_window(modal)
    desktop._modal_stack = getattr(desktop, "_modal_stack", [])
    desktop._modal_stack.append(modal)
    return modal
