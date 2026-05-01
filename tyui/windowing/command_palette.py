"""TV-style command palette (Ctrl+P).

Modal popup that lists the commands available in the current context — i.e.
``CommandDispatcher.commands_for_focus()`` — with a substring filter and
keyboard navigation. Picking a command runs it through the dispatcher.

Open via :func:`show_command_palette`. The palette captures focus on mount
so typing immediately filters; ``Esc`` closes it; ``Enter`` executes the
selected command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.segment import Segment
from textual import events
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip

from .content import WindowCommand, WindowContent
from .frame import BorderStyle, Decorations, TitleSpec
from .helpers import ModalWindow

if TYPE_CHECKING:
    from .commands import CommandDispatcher
    from .desktop import Desktop


__all__ = ["CommandPaletteContent", "show_command_palette"]


class CommandPaletteContent(WindowContent):
    """Searchable command list. Lives inside a ModalWindow."""

    can_focus = True

    DEFAULT_CSS = """
    CommandPaletteContent {
        layer: overlay;
    }
    """

    query: reactive[str] = reactive("")
    highlight: reactive[int] = reactive(0)

    class Picked(Message):
        def __init__(self, palette: "CommandPaletteContent", command: WindowCommand) -> None:
            self.palette = palette
            self.command = command
            super().__init__()

    class Dismissed(Message):
        def __init__(self, palette: "CommandPaletteContent") -> None:
            self.palette = palette
            super().__init__()

    def __init__(self, dispatcher: "CommandDispatcher") -> None:
        super().__init__()
        self.dispatcher = dispatcher
        # Snapshot the available commands at open-time. Re-querying on every
        # keystroke is unnecessary and would re-evaluate enabled-callables
        # that may be expensive; the palette is short-lived.
        self._all_commands = list(dispatcher.commands_for_focus())

    # --- filtering ---------------------------------------------------------

    @property
    def filtered(self) -> list[WindowCommand]:
        q = self.query.lower().strip()
        if not q:
            return list(self._all_commands)
        out: list[WindowCommand] = []
        for cmd in self._all_commands:
            haystack = f"{cmd.label} {cmd.id} {cmd.description or ''}".lower()
            if q in haystack:
                out.append(cmd)
        return out

    def watch_query(self, _old: str, _new: str) -> None:
        self.highlight = 0
        if self.is_mounted:
            self.refresh()

    def watch_highlight(self, _old: int, _new: int) -> None:
        if self.is_mounted:
            self.refresh()

    # --- input -------------------------------------------------------------

    def on_mount(self) -> None:
        self.focus()

    def on_key(self, event: events.Key) -> None:
        k = event.key
        if k == "escape":
            self.post_message(CommandPaletteContent.Dismissed(self))
            event.stop()
            return
        if k == "enter":
            items = self.filtered
            if 0 <= self.highlight < len(items):
                self.post_message(CommandPaletteContent.Picked(self, items[self.highlight]))
            event.stop()
            return
        if k == "up":
            items = self.filtered
            if items:
                self.highlight = (self.highlight - 1) % len(items)
            event.stop()
            return
        if k == "down":
            items = self.filtered
            if items:
                self.highlight = (self.highlight + 1) % len(items)
            event.stop()
            return
        if k == "backspace":
            self.query = self.query[:-1]
            event.stop()
            return
        # Plain character input: append to query.
        if event.character and event.character.isprintable() and len(event.character) == 1:
            self.query = self.query + event.character
            event.stop()
            return

    # --- rendering ---------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Strip.blank(0)

        try:
            base = self.parent.palette  # type: ignore[attr-defined]
        except Exception:
            base = None
        item_style = base.rich_style("menu.item") if base is not None else None
        active_style = base.rich_style("menu.item.active") if base is not None else item_style
        hotkey_style = base.rich_style("menu.hotkey") if base is not None else item_style
        try:
            disabled_style = base.rich_style("menu.item.disabled") if base is not None else item_style
        except Exception:
            disabled_style = item_style

        # Row 0: query line ("> <query>_ ").
        if y == 0:
            text = f"> {self.query}".ljust(width)[:width]
            return Strip([Segment(text, active_style)])
        # Row 1: separator.
        if y == 1:
            return Strip([Segment("─" * width, item_style)])

        items = self.filtered
        idx = y - 2
        if idx >= len(items):
            return Strip([Segment(" " * width, item_style)])

        cmd = items[idx]
        active = idx == self.highlight
        enabled = cmd.is_enabled()
        if not enabled:
            row_style = disabled_style
        else:
            row_style = active_style if active else item_style

        hotkey = cmd.display_hotkey()
        label = cmd.label
        pad = max(1, width - len(label) - len(hotkey) - 4)
        raw = f" {label}{' ' * pad}{hotkey} "
        if len(raw) > width:
            raw = raw[: width - 1] + "…"
        elif len(raw) < width:
            raw += " " * (width - len(raw))

        if hotkey and not active and enabled:
            hk_start = len(raw) - len(hotkey) - 1
            return Strip([
                Segment(raw[:hk_start], row_style),
                Segment(raw[hk_start:hk_start + len(hotkey)], hotkey_style),
                Segment(raw[hk_start + len(hotkey):], row_style),
            ])
        return Strip([Segment(raw, row_style)])


def show_command_palette(
    desktop: "Desktop",
    dispatcher: "CommandDispatcher",
    *,
    size: tuple[int, int] = (60, 16),
) -> ModalWindow:
    """Open the palette as a modal centred on ``desktop``."""
    W, H = desktop.size
    sw = min(size[0], max(10, W - 2))
    sh = min(size[1], max(5, H - 2))
    x = max(0, (W - sw) // 2)
    y = max(0, (H - sh) // 2)
    content = CommandPaletteContent(dispatcher)
    modal = ModalWindow(
        content,
        title=TitleSpec(text="Commands", align="center"),
        position=(x, y),
        size=(sw, sh),
        border_focused=BorderStyle.DOUBLE,
        border_unfocused=BorderStyle.DOUBLE,
        decorations=Decorations(close_box=True),
    )
    desktop.add_window(modal)
    desktop._modal_stack = getattr(desktop, "_modal_stack", [])
    desktop._modal_stack.append(modal)
    return modal
