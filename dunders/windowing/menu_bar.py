"""Menu bar: TV-style docked strip at the top of the screen.

Public API:

    from dunders.windowing import MenuBar, Menu, MenuItem, MenuSeparator

Usage in an App.compose(): yield the MenuBar before the Desktop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, TYPE_CHECKING

from rich.segment import Segment
from textual import events
from textual.containers import Container
from textual.geometry import Offset, Size
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from .frame import BorderSides, BorderStyle, Decorations, TitleSpec
from .palette import Palette

if TYPE_CHECKING:
    from .commands import CommandDispatcher
    from .desktop import Desktop


__all__ = ["MenuBar", "Menu", "MenuItem", "MenuSeparator"]


@dataclass
class MenuItem:
    """One row in a dropdown menu.

    There are two ways to bind behaviour:

    * Legacy: pass ``handler`` (and optionally ``hotkey``) directly. The label
      is taken from ``label`` as-is.
    * Command-driven (TV-style): pass ``command_id``. When the menu is
      rendered, the bound :class:`CommandDispatcher` is queried for the
      current command; ``label``/``hotkey``/``enabled`` left as default
      (``None``/``None``/``True``) are taken from the command. Activation
      goes through the dispatcher.
    """

    label: str | None = None
    handler: Callable[[], None] | None = None
    hotkey: str | None = None
    enabled: bool = True
    command_id: str | None = None


@dataclass
class MenuSeparator:
    pass


@dataclass
class Menu:
    label: str
    items: list[MenuItem | MenuSeparator] = field(default_factory=list)


class MenuBar(Widget):
    """One-line menu strip, docked at the top of its parent.

    Activation:
      - F10 or Alt+<hotkey-letter> (if terminal sends it) opens the bar.
      - Left/Right arrows cycle through menus when active.
      - Down/Enter opens the currently highlighted menu dropdown.
      - Esc deactivates.
    """

    DEFAULT_CSS = """
    MenuBar {
        dock: top;
        height: 1;
        layer: overlay;
    }
    """

    BINDINGS = [
        ("left",   "cycle(-1)", "Prev menu"),
        ("right",  "cycle(1)",  "Next menu"),
        ("enter",  "open",      "Open"),
        ("down",   "open",      "Open"),
        ("escape", "dismiss",   "Close"),
    ]

    can_focus = True

    active_index: reactive[int | None] = reactive(None)

    class OpenRequested(Message):
        def __init__(self, menu_bar: "MenuBar", index: int) -> None:
            self.menu_bar = menu_bar
            self.index = index
            super().__init__()

    def __init__(
        self,
        menus: list[Menu] | None = None,
        *,
        palette: Palette | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.menus: list[Menu] = menus or []
        self._palette = palette
        self._dispatcher: "CommandDispatcher | None" = None

    # --- dispatcher integration -------------------------------------------

    def bind_dispatcher(self, dispatcher: "CommandDispatcher | None") -> None:
        """Attach a CommandDispatcher. MenuItem.command_id starts working,
        and ``refresh_for_focus()`` re-evaluates lazy labels/enabled.
        """
        self._dispatcher = dispatcher
        if self.is_mounted:
            self.refresh()

    @property
    def dispatcher(self) -> "CommandDispatcher | None":
        return self._dispatcher

    def refresh_for_focus(self) -> None:
        """Called by the host when focus or commands changed.

        ``MenuItem``s with ``command_id`` lazily re-read the command on next
        render, so a plain ``refresh()`` of any open Dropdown is enough.
        """
        if self.is_mounted:
            self.refresh()
        # Refresh open dropdowns too.
        if self.app is not None:
            try:
                for dd in self.app.query(Dropdown):
                    dd.refresh()
            except Exception:
                pass

    # --- palette access ----------------------------------------------------

    @property
    def palette(self) -> Palette:
        if self._palette is not None:
            return self._palette
        from .desktop import Desktop
        for node in self.ancestors_with_self:
            if isinstance(node, Desktop):
                return node.palette
        from .themes import modern_dark
        return Palette(modern_dark)

    # --- menu management ---------------------------------------------------

    def add_menu(self, menu: Menu) -> None:
        self.menus.append(menu)
        self.refresh()

    def clear(self) -> None:
        self.menus.clear()
        self.active_index = None
        self.refresh()

    # --- activation --------------------------------------------------------

    def activate(self, index: int = 0) -> None:
        if not self.menus:
            return
        self.active_index = max(0, min(index, len(self.menus) - 1))

    def deactivate(self) -> None:
        self.active_index = None

    def cycle(self, direction: int) -> None:
        if self.active_index is None or not self.menus:
            return
        self.active_index = (self.active_index + direction) % len(self.menus)

    def open_active(self) -> None:
        if self.active_index is None:
            return
        self.post_message(MenuBar.OpenRequested(self, self.active_index))

    # --- actions (wired to BINDINGS) --------------------------------------

    def action_cycle(self, direction: int) -> None:
        if self.active_index is None:
            self.activate(0)
        else:
            self.cycle(direction)

    def action_open(self) -> None:
        if self.active_index is None:
            self.activate(0)
        self.open_active()

    def action_dismiss(self) -> None:
        self.deactivate()

    # --- reactive watcher --------------------------------------------------

    def watch_active_index(self, _old, _new) -> None:
        if self.is_mounted:
            self.refresh()

    # --- layout: where does menu N live on the bar? -----------------------

    def _menu_spans(self) -> list[tuple[int, int, int]]:
        """Return (index, start_x, end_x_exclusive) for each menu label."""
        spans = []
        x = 2  # left padding
        for i, m in enumerate(self.menus):
            label = f" {m.label} "
            spans.append((i, x, x + len(label)))
            x += len(label)
        return spans

    # --- rendering ---------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0 or y != 0:
            return Strip.blank(0)

        base_style = self.palette.rich_style("menu.bar")
        item_style = self.palette.rich_style("menu.item")
        item_active = self.palette.rich_style("menu.item.active")

        segs: list[Segment] = [Segment("  ", base_style)]
        for i, m in enumerate(self.menus):
            label = f" {m.label} "
            style = item_active if i == self.active_index else item_style
            segs.append(Segment(label, style))
        # Pad out to full width.
        rendered_len = sum(len(s.text) for s in segs)
        if rendered_len < width:
            segs.append(Segment(" " * (width - rendered_len), base_style))
        return Strip(segs)

    # --- mouse -------------------------------------------------------------

    def on_click(self, event: events.Click) -> None:
        x = event.x
        for i, a, b in self._menu_spans():
            if a <= x < b:
                self.active_index = i
                self.open_active()
                event.stop()
                return


class Dropdown(Container):
    """Dropdown panel shown below a menu bar item.

    A minimal floating panel drawn with our Frame primitives. Renders items
    as rows; up/down/enter/esc navigate. Closes by clicking outside or Esc.
    """

    DEFAULT_CSS = """
    Dropdown {
        layer: overlay;
        position: absolute;
        overflow: hidden;
    }
    """

    BINDINGS = [
        ("up",     "move(-1)",  "Up"),
        ("down",   "move(1)",   "Down"),
        ("left",   "cycle(-1)", "Prev menu"),
        ("right",  "cycle(1)",  "Next menu"),
        ("enter",  "choose",    "Choose"),
        ("escape", "dismiss",   "Close"),
    ]

    can_focus = True

    highlight: reactive[int] = reactive(0)

    class ItemChosen(Message):
        def __init__(self, dropdown: "Dropdown", item: MenuItem) -> None:
            self.dropdown = dropdown
            self.item = item
            super().__init__()

    class Dismissed(Message):
        def __init__(self, dropdown: "Dropdown") -> None:
            self.dropdown = dropdown
            super().__init__()

    class CycleRequested(Message):
        def __init__(self, dropdown: "Dropdown", direction: int) -> None:
            self.dropdown = dropdown
            self.direction = direction
            super().__init__()

    def __init__(
        self,
        items: list[MenuItem | MenuSeparator],
        *,
        position: tuple[int, int] = (0, 1),
        palette: Palette | None = None,
        dispatcher: "CommandDispatcher | None" = None,
        max_height: int | None = None,
    ) -> None:
        super().__init__()
        self._palette = palette
        self._dispatcher = dispatcher
        # Filter out command-driven items whose command_id is not currently
        # available (e.g. panel.view when the editor is focused). Renders
        # would otherwise show the raw id as a label.
        self.items = self._filter_items(items)
        # Index of the first item drawn in the interior; non-zero only when
        # the menu is taller than the area it was given (see max_height).
        self._scroll = 0
        w, h = self._natural_size()
        # Clamp to the area the host says is available so a tall menu never
        # spills past the bottom of the desktop and loses its bottom border /
        # trailing items. The overflow is reached by scrolling (see
        # ``_ensure_visible``).
        if max_height is not None and max_height >= 3:
            h = min(h, max_height)
        self._height = h
        self.styles.offset = Offset(*position)
        self.styles.width = w
        self.styles.height = h

    def _filter_items(
        self, items: list[MenuItem | MenuSeparator]
    ) -> list[MenuItem | MenuSeparator]:
        kept: list[MenuItem | MenuSeparator] = []
        for it in items:
            if (
                isinstance(it, MenuItem)
                and it.handler is None
                and it.command_id is not None
                and self._dispatcher is not None
                and self._dispatcher.resolve(it.command_id) is None
            ):
                continue
            kept.append(it)
        # Collapse leading / trailing / consecutive separators left over
        # from the filter pass.
        cleaned: list[MenuItem | MenuSeparator] = []
        prev_sep = True
        for it in kept:
            is_sep = isinstance(it, MenuSeparator)
            if is_sep and prev_sep:
                continue
            cleaned.append(it)
            prev_sep = is_sep
        while cleaned and isinstance(cleaned[-1], MenuSeparator):
            cleaned.pop()
        return cleaned

    # --- command resolution -----------------------------------------------

    def _resolved_label(self, it: MenuItem) -> str:
        if it.label is not None:
            return it.label
        if it.command_id and self._dispatcher is not None:
            r = self._dispatcher.resolve(it.command_id)
            if r is not None:
                return r.command.label
        return it.command_id or ""

    def _resolved_hotkey(self, it: MenuItem) -> str:
        if it.hotkey is not None:
            return it.hotkey
        if it.command_id and self._dispatcher is not None:
            r = self._dispatcher.resolve(it.command_id)
            if r is not None:
                return r.command.display_hotkey()
        return ""

    def _resolved_enabled(self, it: MenuItem) -> bool:
        if not it.enabled:
            return False
        if it.command_id and self._dispatcher is not None:
            r = self._dispatcher.resolve(it.command_id)
            if r is None:
                return False
            return r.command.is_enabled()
        return True

    @property
    def palette(self) -> Palette:
        if self._palette is not None:
            return self._palette
        from .desktop import Desktop
        for node in self.ancestors_with_self:
            if isinstance(node, Desktop):
                return node.palette
        from .themes import modern_dark
        return Palette(modern_dark)

    def _natural_size(self) -> tuple[int, int]:
        max_label = 0
        max_hotkey = 0
        for it in self.items:
            if isinstance(it, MenuItem):
                max_label = max(max_label, len(self._resolved_label(it)))
                max_hotkey = max(max_hotkey, len(self._resolved_hotkey(it)))
        inner = max_label + (max_hotkey + 3 if max_hotkey else 0)
        w = inner + 4   # left + pad + pad + right
        h = len(self.items) + 2   # top + bottom borders
        return max(10, w), max(3, h)

    # --- highlight navigation ---------------------------------------------

    def _first_selectable(self, start: int, direction: int) -> int | None:
        idx = start
        steps = 0
        while steps < len(self.items):
            it = self.items[idx]
            if isinstance(it, MenuItem) and self._resolved_enabled(it):
                return idx
            idx = (idx + direction) % len(self.items)
            steps += 1
        return None

    def _visible_rows(self) -> int:
        """How many item rows fit inside the borders."""
        return max(1, self._height - 2)

    def _ensure_visible(self) -> None:
        """Slide the scroll window so ``highlight`` is drawn inside the box."""
        vis = self._visible_rows()
        if self.highlight < self._scroll:
            self._scroll = self.highlight
        elif self.highlight >= self._scroll + vis:
            self._scroll = self.highlight - vis + 1
        max_scroll = max(0, len(self.items) - vis)
        self._scroll = max(0, min(self._scroll, max_scroll))

    def on_mount(self) -> None:
        first = self._first_selectable(0, 1)
        if first is not None:
            self.highlight = first
        self._ensure_visible()
        # Grab focus immediately AND on next refresh for robustness
        self._grab_focus()
        self.call_after_refresh(self._grab_focus)

    def _grab_focus(self) -> None:
        if self.is_mounted and self.app is not None:
            try:
                self.app.set_focus(self)
            except Exception:
                pass

    def move_highlight(self, direction: int) -> None:
        if not self.items:
            return
        idx = (self.highlight + direction) % len(self.items)
        nxt = self._first_selectable(idx, 1 if direction > 0 else -1)
        if nxt is not None:
            self.highlight = nxt
            self._ensure_visible()

    def choose_current(self) -> None:
        if 0 <= self.highlight < len(self.items):
            it = self.items[self.highlight]
            if isinstance(it, MenuItem) and self._resolved_enabled(it):
                # Legacy path: direct handler.
                if it.handler is not None:
                    try:
                        it.handler()
                    except Exception:
                        pass
                # TV-style path: route through dispatcher.
                elif it.command_id is not None and self._dispatcher is not None:
                    try:
                        self._dispatcher.dispatch(it.command_id)
                    except Exception:
                        pass
                self.post_message(Dropdown.ItemChosen(self, it))

    def dismiss(self) -> None:
        self.post_message(Dropdown.Dismissed(self))

    def on_blur(self, _event: events.Blur | None = None) -> None:
        # Click on anything outside the dropdown moves focus away from it.
        # Translate that into a dismiss so the host App removes the widget —
        # otherwise the popup lingers, painted on top of the desktop.
        if self.is_mounted:
            self.post_message(Dropdown.Dismissed(self))

    # --- actions (wired to BINDINGS) --------------------------------------

    def action_move(self, direction: int) -> None:
        self.move_highlight(direction)

    def action_cycle(self, direction: int) -> None:
        self.post_message(Dropdown.CycleRequested(self, direction))

    def action_choose(self) -> None:
        self.choose_current()

    def action_dismiss(self) -> None:
        self.dismiss()

    # --- reactive watcher --------------------------------------------------

    def watch_highlight(self, _old, _new) -> None:
        if self.is_mounted:
            self.refresh()

    # --- input -------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "up":
            self.move_highlight(-1)
            event.stop()
        elif key == "down":
            self.move_highlight(1)
            event.stop()
        elif key == "left":
            self.post_message(Dropdown.CycleRequested(self, -1))
            event.stop()
        elif key == "right":
            self.post_message(Dropdown.CycleRequested(self, 1))
            event.stop()
        elif key == "enter":
            self.choose_current()
            event.stop()
        elif key == "escape":
            self.dismiss()
            event.stop()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.move_highlight(1)
        event.stop()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.move_highlight(-1)
        event.stop()

    def on_click(self, event: events.Click) -> None:
        # y=0 and y=height-1 are borders; rows 1..height-2 are items. The
        # scroll window maps a visible row to its item index.
        row = event.y - 1 + self._scroll
        if 0 <= row < len(self.items):
            it = self.items[row]
            if isinstance(it, MenuItem) and self._resolved_enabled(it):
                self.highlight = row
                self.choose_current()
                event.stop()

    # --- rendering ---------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        from .frame import render_bottom, render_left_char, render_right_char, render_top

        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Strip.blank(0)

        border_style_rich = self.palette.rich_style("menu.dropdown.border")
        item_rich = self.palette.rich_style("menu.item")
        item_active_rich = self.palette.rich_style("menu.item.active")
        hotkey_rich = self.palette.rich_style("menu.hotkey")
        separator_rich = self.palette.rich_style("menu.separator")

        if y == 0:
            text = render_top(width, BorderStyle.SINGLE, BorderSides.all(), TitleSpec(""), Decorations())
            return Strip([Segment(text, border_style_rich)])
        if y == height - 1:
            text = render_bottom(width, BorderStyle.SINGLE, BorderSides.all(), Decorations())
            return Strip([Segment(text, border_style_rich)])

        # Interior — one row per item, offset by the scroll window.
        row_index = y - 1
        item_index = row_index + self._scroll
        left = render_left_char(BorderStyle.SINGLE, BorderSides.all())
        right = render_right_char(BorderStyle.SINGLE, BorderSides.all())
        inner_width = width - len(left) - len(right)

        # Scroll affordances: ▲ on the first row when content is hidden above,
        # ▼ on the last row when content is hidden below.
        vis = self._visible_rows()
        arrow = ""
        if row_index == 0 and self._scroll > 0:
            arrow = "▲"
        elif row_index == vis - 1 and self._scroll + vis < len(self.items):
            arrow = "▼"

        if not (0 <= item_index < len(self.items)):
            # Empty padding row.
            return Strip([
                Segment(left, border_style_rich),
                Segment(" " * inner_width, item_rich),
                Segment(right, border_style_rich),
            ])

        it = self.items[item_index]
        if isinstance(it, MenuSeparator):
            return Strip([
                Segment(left, border_style_rich),
                Segment("─" * inner_width, separator_rich),
                Segment(right, border_style_rich),
            ])

        # MenuItem row.
        active = item_index == self.highlight
        enabled = self._resolved_enabled(it)
        if enabled:
            body_style = item_active_rich if active else item_rich
            hk_style = hotkey_rich if not active else item_active_rich
        else:
            try:
                disabled_rich = self.palette.rich_style("menu.item.disabled")
            except Exception:
                disabled_rich = item_rich
            body_style = disabled_rich
            hk_style = disabled_rich

        label = self._resolved_label(it)
        hotkey = self._resolved_hotkey(it)
        pad_mid = max(1, inner_width - len(label) - len(hotkey) - 2)
        pad_left = " "
        pad_right = " "
        raw = f"{pad_left}{label}{' ' * pad_mid}{hotkey}{pad_right}"
        # Truncate/ellipsis if overly long.
        if len(raw) > inner_width:
            raw = raw[: inner_width - 1] + "…"
        elif len(raw) < inner_width:
            raw += " " * (inner_width - len(raw))

        # Overlay a scroll arrow on this row's trailing pad (a single space),
        # so the user can tell the menu continues above/below.
        if arrow and inner_width and raw[-1] == " ":
            raw = raw[:-1] + arrow

        # Split the row so the hotkey gets its own style.
        if hotkey and not active:
            hk_start = len(raw) - len(hotkey) - len(pad_right)
            body = raw[:hk_start]
            hk = raw[hk_start: hk_start + len(hotkey)]
            tail = raw[hk_start + len(hotkey):]
            segs = [
                Segment(left, border_style_rich),
                Segment(body, body_style),
                Segment(hk, hk_style),
                Segment(tail, body_style),
                Segment(right, border_style_rich),
            ]
        else:
            segs = [
                Segment(left, border_style_rich),
                Segment(raw, body_style),
                Segment(right, border_style_rich),
            ]
        return Strip(segs)
