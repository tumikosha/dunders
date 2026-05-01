"""WindowManager — layout commands (tile/cascade/maximize), move/resize modes,
hotkey bindings, drag orchestration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Literal, TYPE_CHECKING

from textual.binding import Binding
from textual.geometry import Offset, Size
from textual.message import Message

from .window import Window

if TYPE_CHECKING:
    from .desktop import Desktop


class ToggleMaximize(Message):
    def __init__(self, window: Window) -> None:
        self.window = window
        super().__init__()


@dataclass
class Command:
    id: str
    label: str
    hotkey: str | None
    handler: Callable[[], None]


MIN_W = 3
MIN_H = 3


class WindowManager:
    """Stateless service over a Desktop: tile/cascade/maximize/hide/minimize.

    Also owns the keyboard move/resize modes and the drag step helpers.
    Kept as a plain object (not a widget) so its logic can be unit-tested by
    passing a Desktop (or a small fake) directly.
    """

    def __init__(self, desktop: "Desktop") -> None:
        self.desktop = desktop
        self._mode: Literal["normal", "move", "resize"] = "normal"
        self._mode_target: Window | None = None
        self.keybindings: dict[str, Command] = {}

    # --- layouts -----------------------------------------------------------

    def tile_horizontal(self) -> None:
        visible = [w for w in self.desktop.windows if w.display]
        n = len(visible)
        if n == 0:
            return
        W, H = self.desktop.size
        w_each = max(MIN_W, W // n)
        for i, win in enumerate(visible):
            x = i * w_each
            w = W - x if i == n - 1 else w_each
            self._set_rect(win, Offset(x, 0), Size(max(MIN_W, w), max(MIN_H, H)))

    def tile_vertical(self) -> None:
        visible = [w for w in self.desktop.windows if w.display]
        n = len(visible)
        if n == 0:
            return
        W, H = self.desktop.size
        h_each = max(MIN_H, H // n)
        for i, win in enumerate(visible):
            y = i * h_each
            h = H - y if i == n - 1 else h_each
            self._set_rect(win, Offset(0, y), Size(max(MIN_W, W), max(MIN_H, h)))

    def tile_grid(self) -> None:
        visible = [w for w in self.desktop.windows if w.display]
        n = len(visible)
        if n == 0:
            return
        W, H = self.desktop.size
        cols = int(math.ceil(math.sqrt(n)))
        rows = int(math.ceil(n / cols))
        cw = max(MIN_W, W // cols)
        rh = max(MIN_H, H // rows)
        for i, win in enumerate(visible):
            r = i // cols
            c = i % cols
            x = c * cw
            y = r * rh
            w = (W - x) if c == cols - 1 else cw
            h = (H - y) if r == rows - 1 else rh
            self._set_rect(win, Offset(x, y), Size(max(MIN_W, w), max(MIN_H, h)))

    def cascade(self) -> None:
        visible = [w for w in self.desktop.windows if w.display]
        if not visible:
            return
        W, H = self.desktop.size
        sw = max(MIN_W, int(W * 0.75))
        sh = max(MIN_H, int(H * 0.75))
        step_x, step_y = 2, 1
        for i, win in enumerate(visible):
            x = min(i * step_x, max(0, W - sw))
            y = min(i * step_y, max(0, H - sh))
            self._set_rect(win, Offset(x, y), Size(sw, sh))
            self.desktop.raise_to_top(win)

    # --- maximize / restore ------------------------------------------------

    def toggle_maximize(self, window: Window) -> None:
        if window.maximized:
            window.restore_rect()
            window.maximized = False
        else:
            window.save_rect()
            W, H = self.desktop.size
            self._set_rect(window, Offset(0, 0), Size(max(MIN_W, W), max(MIN_H, H)))
            window.maximized = True

    # --- focus / cycle -----------------------------------------------------

    def focus_next(self) -> None:
        self.desktop.cycle_focus(+1)

    def focus_prev(self) -> None:
        self.desktop.cycle_focus(-1)

    # --- close / hide / minimize ------------------------------------------

    def close_focused(self) -> None:
        if self.desktop.focused_window is not None:
            self.desktop.remove_window(self.desktop.focused_window)

    def hide_focused(self) -> None:
        if self.desktop.focused_window is not None:
            self.desktop.hide_window(self.desktop.focused_window)

    def minimize_focused(self) -> None:
        if self.desktop.focused_window is not None:
            self.desktop.minimize_window(self.desktop.focused_window)

    def maximize_focused(self) -> None:
        if self.desktop.focused_window is not None:
            self.toggle_maximize(self.desktop.focused_window)

    # --- keyboard move/resize modes ---------------------------------------

    def enter_move_mode(self, window: Window | None = None) -> None:
        target = window or self.desktop.focused_window
        if target is None:
            return
        self._mode = "move"
        self._mode_target = target

    def enter_resize_mode(self, window: Window | None = None) -> None:
        target = window or self.desktop.focused_window
        if target is None:
            return
        self._mode = "resize"
        self._mode_target = target

    def exit_mode(self) -> None:
        self._mode = "normal"
        self._mode_target = None

    @property
    def mode(self) -> str:
        return self._mode

    def move_mode_step(self, dx: int, dy: int) -> None:
        if self._mode != "move" or self._mode_target is None:
            return
        w = self._mode_target
        cur_x = w.region.x
        cur_y = w.region.y
        W, H = self.desktop.size
        new_x = max(0, min(cur_x + dx, W - w.size.width))
        new_y = max(0, min(cur_y + dy, H - w.size.height))
        w.styles.offset = Offset(new_x, new_y)

    def resize_mode_step(self, dw: int, dh: int) -> None:
        if self._mode != "resize" or self._mode_target is None:
            return
        w = self._mode_target
        W, H = self.desktop.size
        new_w = max(MIN_W, min(w.size.width + dw, W - w.region.x))
        new_h = max(MIN_H, min(w.size.height + dh, H - w.region.y))
        w.styles.width = new_w
        w.styles.height = new_h

    # --- helpers -----------------------------------------------------------

    def _set_rect(self, w: Window, position: Offset, size: Size) -> None:
        W, H = self.desktop.size
        x = max(0, min(position.x, max(0, W - MIN_W)))
        y = max(0, min(position.y, max(0, H - MIN_H)))
        width = max(MIN_W, min(size.width, W - x))
        height = max(MIN_H, min(size.height, H - y))
        w.styles.offset = Offset(x, y)
        w.styles.width = width
        w.styles.height = height
        # Any tile/cascade breaks a maximized state.
        w.maximized = False


# --- Default keybindings ----------------------------------------------------


def default_bindings(manager: WindowManager) -> list[Binding]:
    """Return a set of Textual Bindings wired to the manager."""
    return [
        Binding("tab",           "focus_next",       "Next window",       show=False),
        Binding("shift+tab",     "focus_prev",       "Prev window",       show=False),
        Binding("ctrl+w",        "close_focused",    "Close window",      show=False),
        Binding("ctrl+h",        "hide_focused",     "Hide window",       show=False),
        Binding("underscore",    "minimize_focused", "Minimize",          show=False),
        Binding("f5",            "maximize_focused", "Maximize toggle",   show=False),
        Binding("ctrl+f5",       "enter_move",       "Move mode",         show=False),
        Binding("ctrl+f7",       "enter_resize",     "Resize mode",       show=False),
        Binding("ctrl+alt+h",    "tile_horizontal",  "Tile horizontally", show=False),
        Binding("ctrl+alt+v",    "tile_vertical",    "Tile vertically",   show=False),
        Binding("ctrl+alt+g",    "tile_grid",        "Tile grid",         show=False),
        Binding("ctrl+alt+c",    "cascade",          "Cascade",           show=False),
    ]
