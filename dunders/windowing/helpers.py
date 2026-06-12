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
    """A Window with modal behaviour: Esc closes; click outside closes.

    Modality is enforced by stripping ``can_focus`` from every focusable
    widget in non-modal sibling windows on mount, and restoring it on
    unmount. This is the same trick ``ReplaceAllDialog`` used inline; it
    is centralised here so EVERY dialog opened via ``show_modal`` is
    actually modal — keys cannot drift to a panel/editor underneath, and
    Tab cycles only inside the dialog.

    We deliberately avoid ``Widget.disabled`` for the freeze: Textual's
    opacity pass on disabled widgets crashes on Strips containing
    ``style=None`` segments (which the windowing frame produces).
    """

    class Dismissed(Message):
        def __init__(self, window: "ModalWindow") -> None:
            self.window = window
            super().__init__()

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._frozen_focusables: list[Widget] = []

    def action_dismiss(self) -> None:
        self.post_message(ModalWindow.Dismissed(self))

    def on_mount(self) -> None:
        super().on_mount()
        self._freeze_siblings()

    def on_unmount(self) -> None:
        self._thaw_siblings()

    def _freeze_siblings(self) -> None:
        # Walk the entire screen (not just `desktop.windows`) so app-level
        # widgets that live OUTSIDE the Desktop — MenuBar, CommandLine,
        # StatusBar — also lose focus. Otherwise Tab cycling via
        # `Screen.focus_next` could drift to e.g. the command-line input
        # underneath the modal.
        try:
            screen = self.screen
        except Exception:
            screen = None
        if screen is None:
            return
        modal_descendants = set(self.query("*"))
        modal_descendants.add(self)
        for w in screen.query("*"):
            if w in modal_descendants:
                continue
            # Skip anything inside ANOTHER ModalWindow (the topmost one
            # may overlay; siblings on the modal stack stay live).
            anc = w
            inside_modal = False
            while anc is not None:
                if isinstance(anc, ModalWindow) and anc is not self:
                    inside_modal = True
                    break
                anc = getattr(anc, "parent", None)
            if inside_modal:
                continue
            if w.can_focus:
                self._frozen_focusables.append(w)
                w.can_focus = False

    def _thaw_siblings(self) -> None:
        for widget in self._frozen_focusables:
            try:
                widget.can_focus = True
            except Exception:
                pass
        self._frozen_focusables.clear()


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
    # Push onto the modal stack BEFORE add_window so the focus_window call
    # inside add_window sees the modal as the topmost focus target. Without
    # this, `add_window` -> `focus_window(modal)` would run before the
    # gate knew there was a modal active.
    desktop._modal_stack.append(modal)
    desktop.add_window(modal)
    return modal
