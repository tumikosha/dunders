"""Contract for window contents: reactive title/subtitle/dirty + focus hooks.

WindowContent is an opt-in base class. Plain Textual widgets may also be
placed inside a Window — they just won't get dynamic title/dirty indicators.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from .frame import Decorations, TitleSpec

if TYPE_CHECKING:
    from .window import Window


__all__ = [
    "WindowContent",
    "TitleSpec",
    "Decorations",
    "WindowCommand",
    "CommandsChanged",
]


@dataclass
class WindowCommand:
    """A command exposed by a content to WindowManager (menu entries, hotkeys).

    Turbo-Vision-style declaration. ``handler`` may be ``None`` for app-level
    commands that live in a registry rather than on a content. ``enabled`` is
    either a static bool or a callable evaluated at refresh time. ``hotkey``
    is the Textual-normalised key (e.g. ``"ctrl+s"``); ``hotkey_label`` is the
    user-facing display string (e.g. ``"Ctrl+S"``); when omitted it is derived
    from ``hotkey``.
    """

    id: str
    label: str
    handler: Callable[[], None] | None = None
    hotkey: str | None = None
    hotkey_label: str | None = None
    enabled: bool | Callable[[], bool] = True
    visible: bool = True
    description: str | None = None
    category: str | None = None

    def is_enabled(self) -> bool:
        if callable(self.enabled):
            try:
                return bool(self.enabled())
            except Exception:
                return False
        return bool(self.enabled)

    def display_hotkey(self) -> str:
        if self.hotkey_label is not None:
            return self.hotkey_label
        if self.hotkey is None:
            return ""
        return _humanise_hotkey(self.hotkey)


def _humanise_hotkey(key: str) -> str:
    """Turn a Textual key (``"ctrl+full_stop"``) into a display label (``"Ctrl+."``)."""
    parts = key.split("+")
    out: list[str] = []
    name_map = {
        "ctrl": "Ctrl",
        "shift": "Shift",
        "alt": "Alt",
        "meta": "Meta",
        "super": "Super",
        "escape": "Esc",
        "enter": "Enter",
        "tab": "Tab",
        "space": "Space",
        "backspace": "Backspace",
        "delete": "Del",
        "insert": "Ins",
        "home": "Home",
        "end": "End",
        "pageup": "PgUp",
        "pagedown": "PgDn",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "full_stop": ".",
        "comma": ",",
        "minus": "-",
        "plus": "+",
        "equals_sign": "=",
        "slash": "/",
        "backslash": "\\",
        "left_square_bracket": "[",
        "right_square_bracket": "]",
        "semicolon": ";",
        "apostrophe": "'",
        "grave_accent": "`",
    }
    for p in parts:
        low = p.lower()
        if low in name_map:
            out.append(name_map[low])
        elif low.startswith("f") and low[1:].isdigit():
            out.append(low.upper())
        elif len(p) == 1:
            out.append(p.upper())
        else:
            out.append(p.capitalize())
    return "+".join(out)


class CommandsChanged(Message):
    """Posted by a WindowContent when its declared commands changed semantics
    (e.g. enabled-state flipped because of dirty/selection). The host listens
    and refreshes menus/palette/status bar accordingly.
    """

    def __init__(self, content: "WindowContent") -> None:
        self.content = content
        super().__init__()


class WindowContent(Widget):
    """Base for widgets designed to live inside a Window.

    Reactive attributes automatically propagate to the enclosing Window via
    ``watch_*`` hooks. Overriding the on_window_focus/blur methods lets the
    content react to gaining/losing focus at the window level.
    """

    window_title:    reactive[str | None]  = reactive(None)
    window_subtitle: reactive[str | None]  = reactive(None)
    is_dirty:        reactive[bool]        = reactive(False)
    can_close:       reactive[bool]        = reactive(True)

    # --- hooks -------------------------------------------------------------

    def on_window_focus(self) -> None:  # override as needed
        pass

    def on_window_blur(self) -> None:  # override as needed
        pass

    def apply_theme(self) -> None:
        """Repaint after the active palette changed.

        Called by ``Desktop.set_theme`` on every window's content. The default
        just refreshes this widget; contents that cache palette-derived CSS
        (background/colour) or own inner widgets override to re-apply them.
        """
        self.refresh()

    def get_commands(self) -> list[WindowCommand]:
        return []

    # --- plumbing ----------------------------------------------------------

    def _find_window(self) -> "Window | None":
        from .window import Window  # local import: avoid circular
        node: Widget | None = self
        while node is not None:
            parent = node.parent
            if isinstance(parent, Window):
                return parent
            node = parent
        return None

    def watch_window_title(self, new_value: str | None) -> None:
        if new_value is None:
            return  # leave window title untouched
        w = self._find_window()
        if w is None:
            return
        w.update_content_title(new_value)

    def watch_window_subtitle(self, new_value: str | None) -> None:
        w = self._find_window()
        if w is None:
            return
        w.update_content_subtitle(new_value)

    def watch_is_dirty(self, dirty: bool) -> None:
        w = self._find_window()
        if w is None:
            return
        w.update_dirty_marker(dirty)
