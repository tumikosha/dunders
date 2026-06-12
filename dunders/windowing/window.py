"""Window widget: frame with title/borders/decorations + content slot.

Position in the Desktop is expressed via ``styles.offset`` and
``styles.width/height``. The frame is drawn directly via ``render_line``;
the content widget is a child that is positioned with an offset matching
the visible border thickness.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Optional

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual import events
from textual.containers import Container
from textual.geometry import Offset, Size
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip
from textual.widget import Widget

from .frame import (
    BorderSides,
    BorderStyle,
    Decorations,
    TitleSpec,
    effective_border,
    frame_margin,
    render_bottom,
    render_left_char,
    render_right_char,
    render_top,
)
from .palette import Palette, Style

if TYPE_CHECKING:
    from .desktop import Desktop


Target = Literal[
    "title",
    "close_box",
    "zoom_box",
    "minimize_box",
    "resize_grip",
    "border_top",
    "border_right",
    "border_bottom",
    "border_left",
    "corner_tl",
    "corner_tr",
    "corner_bl",
    "corner_br",
    "content",
    "outside",
]


@dataclass
class _DragState:
    kind: Literal["move", "resize"]
    start_screen: Offset
    orig_offset: Offset
    orig_size: Size
    edges: tuple[bool, bool, bool, bool] = (False, False, False, False)  # top, right, bottom, left


class Window(Container):
    """A floating, bordered, focusable window.

    The content widget is exposed as ``self.content``. Change reactive
    attributes at runtime — Textual will refresh automatically.
    """

    DEFAULT_CSS = """
    Window {
        layer: windows;
        position: absolute;
        overflow: hidden;
    }
    """

    title:            reactive[TitleSpec]   = reactive(TitleSpec(), layout=True)
    border_focused:   reactive[BorderStyle] = reactive(BorderStyle.DOUBLE, layout=False)
    border_unfocused: reactive[BorderStyle] = reactive(BorderStyle.SINGLE, layout=False)
    sides:            reactive[BorderSides] = reactive(BorderSides.all(), layout=True)
    decorations:      reactive[Decorations] = reactive(Decorations(), layout=True)
    focused_state:    reactive[bool]        = reactive(False, layout=False)
    maximized:        reactive[bool]        = reactive(False, layout=True)

    # --- lifecycle messages ---
    class Closed(Message):
        def __init__(self, window: "Window") -> None:
            self.window = window
            super().__init__()

    class Minimized(Message):
        def __init__(self, window: "Window") -> None:
            self.window = window
            super().__init__()

    class CopyBoxClicked(Message):
        def __init__(self, window: "Window") -> None:
            self.window = window
            super().__init__()

    class FocusRequested(Message):
        def __init__(self, window: "Window") -> None:
            self.window = window
            super().__init__()

    class DragStarted(Message):
        def __init__(self, window: "Window", kind: str) -> None:
            self.window = window
            self.kind = kind
            super().__init__()

    def __init__(
        self,
        content: Widget,
        *,
        title: TitleSpec | str = "",
        position: tuple[int, int] = (0, 0),
        size: tuple[int, int] = (40, 12),
        border_focused: BorderStyle = BorderStyle.DOUBLE,
        border_unfocused: BorderStyle = BorderStyle.SINGLE,
        sides: BorderSides | None = None,
        decorations: Decorations | None = None,
        palette: Palette | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.content = content
        self.palette_override: dict[str, Style] = {}
        self._palette: Palette | None = palette
        self._saved_rect: tuple[Offset, Size] | None = None
        self._drag: _DragState | None = None

        if isinstance(title, str):
            title = TitleSpec(text=title)
        # Use set_reactive to bypass initial watch callbacks until mounted.
        self.set_reactive(Window.title, title)
        self.set_reactive(Window.border_focused, border_focused)
        self.set_reactive(Window.border_unfocused, border_unfocused)
        self.set_reactive(Window.sides, sides or BorderSides.all())
        self.set_reactive(Window.decorations, decorations or Decorations())

        # Initial geometry.
        self.styles.offset = Offset(*position)
        self.styles.width = size[0]
        self.styles.height = size[1]

    # --- composition -------------------------------------------------------

    def compose(self):
        yield self.content

    def on_mount(self) -> None:
        self._apply_content_layout()
        self.refresh()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_content_layout()

    # --- palette access ----------------------------------------------------

    @property
    def palette(self) -> Palette:
        if self._palette is not None:
            return self._palette
        from .desktop import Desktop
        anc = self.ancestors_with_self
        for node in anc:
            if isinstance(node, Desktop):
                return node.palette
        # Fallback — lazy-load modern_dark.
        from .themes import modern_dark
        return Palette(modern_dark)

    def get_style(self, role: str) -> RichStyle:
        if role in self.palette_override:
            return self.palette_override[role].to_rich()
        return self.palette.rich_style(role)

    # --- reactive watchers -------------------------------------------------

    def watch_sides(self, _old: BorderSides, _new: BorderSides) -> None:
        if self.is_mounted:
            self._apply_content_layout()
            self.refresh()

    def watch_title(self, _old: TitleSpec, _new: TitleSpec) -> None:
        if self.is_mounted:
            self.refresh()

    def watch_decorations(self, _old: Decorations, _new: Decorations) -> None:
        if self.is_mounted:
            self._apply_content_layout()
            self.refresh()

    def watch_focused_state(self, _old: bool, _new: bool) -> None:
        if self.is_mounted:
            self.refresh()

    def watch_border_focused(self, _old: BorderStyle, _new: BorderStyle) -> None:
        if self.is_mounted:
            self.refresh()

    def watch_border_unfocused(self, _old: BorderStyle, _new: BorderStyle) -> None:
        if self.is_mounted:
            self.refresh()

    # --- title helpers used by WindowContent.watch_* -----------------------

    def update_content_title(self, text: str) -> None:
        cur = self.title
        # Preserve dirty marker if present.
        marker = " *" if getattr(self, "_dirty_marker_on", False) else ""
        new_text = f"{text}{marker}"
        self.title = replace(cur, text=new_text)

    def update_content_subtitle(self, text: str | None) -> None:
        deco = self.decorations
        self.decorations = replace(deco, subtitle=text)

    def update_dirty_marker(self, dirty: bool) -> None:
        self._dirty_marker_on = dirty
        cur = self.title
        # Strip any prior " *" suffix and re-add if dirty.
        raw = cur.text[:-2] if cur.text.endswith(" *") else cur.text
        new_text = raw + (" *" if dirty else "")
        self.title = replace(cur, text=new_text)

    # --- content layout ----------------------------------------------------

    def _apply_content_layout(self) -> None:
        if not hasattr(self, "content"):
            return
        top, right, bottom, left = frame_margin(self.sides)
        w = max(0, self.size.width - left - right)
        h = max(0, self.size.height - top - bottom)
        self.content.styles.offset = Offset(left, top)
        self.content.styles.width = w
        self.content.styles.height = h

    @property
    def current_border(self) -> BorderStyle:
        return effective_border(self.focused_state, self.border_focused, self.border_unfocused)

    # --- rendering ---------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        height = self.size.height
        if width <= 0 or height <= 0:
            return Strip.blank(0)

        style = self.current_border
        sides = self.sides
        deco = self.decorations
        title = self.title

        top_exists = sides.top
        bot_exists = sides.bottom

        role_border = "window.border.focused" if self.focused_state else "window.border.unfocused"
        role_title  = "window.title.focused"  if self.focused_state else "window.title.unfocused"
        border_style = self.get_style(role_border)
        title_style = self.get_style(role_title)

        if top_exists and y == 0:
            text = render_top(width, style, sides, title, deco)
            return self._strip_from_top(text, border_style, title_style, deco)

        if bot_exists and y == height - 1:
            text = render_bottom(width, style, sides, deco)
            return self._strip_from_bottom(text, border_style, deco)

        # Interior row: draw side chars + blank interior; child content overlays this.
        return self._interior_row(y, width, style, sides, border_style)

    def _strip_from_top(
        self,
        text: str,
        border_style: RichStyle,
        title_style: RichStyle,
        deco: Decorations,
    ) -> Strip:
        # Use title_style for the title segment, border_style for the rest.
        # Locate the title substring (if non-empty) and split.
        title_text = self.title.text
        if title_text and title_text in text:
            idx = text.index(title_text)
            segs = []
            if idx > 0:
                segs.append(Segment(text[:idx], border_style))
            segs.append(Segment(title_text, title_style))
            if idx + len(title_text) < len(text):
                segs.append(Segment(text[idx + len(title_text):], border_style))
        else:
            segs = [Segment(text, border_style)]
        return Strip(segs)

    def _strip_from_bottom(
        self,
        text: str,
        border_style: RichStyle,
        deco: Decorations,
    ) -> Strip:
        subtitle = deco.subtitle or ""
        if subtitle and subtitle in text:
            subtitle_style = self.get_style("window.subtitle")
            idx = text.index(subtitle)
            segs = []
            if idx > 0:
                segs.append(Segment(text[:idx], border_style))
            segs.append(Segment(subtitle, subtitle_style))
            if idx + len(subtitle) < len(text):
                segs.append(Segment(text[idx + len(subtitle):], border_style))
        else:
            segs = [Segment(text, border_style)]
        return Strip(segs)

    def _interior_row(
        self,
        y: int,
        width: int,
        style: BorderStyle,
        sides: BorderSides,
        border_style: RichStyle,
    ) -> Strip:
        left_char = render_left_char(style, sides)
        right_char = render_right_char(style, sides)
        content_bg_style = self.get_style("window.content")
        inner_width = width - len(left_char) - len(right_char)
        segs = []
        if left_char:
            segs.append(Segment(left_char, border_style))
        if inner_width > 0:
            segs.append(Segment(" " * inner_width, content_bg_style))
        if right_char:
            segs.append(Segment(right_char, border_style))
        return Strip(segs)

    # --- hit-test ----------------------------------------------------------

    def hit_test(self, local: Offset) -> Target:
        x, y = local
        w, h = self.size
        if x < 0 or y < 0 or x >= w or y >= h:
            return "outside"

        sides = self.sides
        deco = self.decorations
        on_top = sides.top and y == 0
        on_bottom = sides.bottom and y == h - 1
        on_left = sides.left and x == 0
        on_right = sides.right and x == w - 1

        # Corners take priority.
        if on_top and on_left:
            return "corner_tl"
        if on_top and on_right:
            return "corner_tr"
        if on_bottom and on_left:
            return "corner_bl"
        if on_bottom and on_right:
            # resize_grip is cosmetically at the bottom-right, but functionally
            # just diagonal resize — merge with corner_br for consistency.
            return "resize_grip" if deco.resize_grip else "corner_br"

        if on_top:
            # Close box at positions [1..3] if decoration is on and left side is on.
            if deco.close_box and sides.left and 1 <= x <= 3:
                return "close_box"
            if deco.copy_box and sides.left:
                base = 4 if deco.close_box else 1
                if base <= x <= base + 2:
                    return "copy_box"
            if sides.right:
                # Right-side decorations stacked from the right edge inward:
                # zoom_box (rightmost), then minimize_box to its left.
                right_start = w - 1
                if deco.zoom_box and right_start - 3 <= x <= right_start - 1:
                    return "zoom_box"
                if deco.zoom_box:
                    right_start -= 3
                if deco.minimize_box and right_start - 3 <= x <= right_start - 1:
                    return "minimize_box"
            # Title region: if cursor is within the title text columns, treat as title.
            title_text = self.title.text
            if title_text:
                # Best-effort: treat the entire top edge (excluding decorations) as title.
                return "title"
            return "border_top"

        if on_bottom:
            return "border_bottom"
        if on_left:
            return "border_left"
        if on_right:
            return "border_right"
        return "content"

    # --- mouse: drag -------------------------------------------------------

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        # event.x / event.y are relative to the widget that first received the
        # event (may be a descendant content widget). Convert via screen coords
        # so hit_test always sees window-local coordinates.
        screen = event.screen_offset
        local = Offset(screen.x - self.region.x, screen.y - self.region.y)
        target = self.hit_test(local)

        # Modal gate: while a modal is up, swallow ALL window-frame mouse
        # actions on non-modal siblings. Without this the user could still
        # click close/zoom boxes, or drag-move/resize a panel underneath
        # the dialog. Desktop.focus_window already redirects focus, but
        # close_box / zoom_box / drag start their own message paths that
        # bypass focus_window — so they have to be gated here too.
        desktop = self._find_desktop()
        if desktop is not None and desktop._has_modal():
            from .helpers import ModalWindow
            if not isinstance(self, ModalWindow):
                event.stop()
                return

        # All clicks request focus on the window.
        self.post_message(Window.FocusRequested(self))

        if target == "close_box":
            self.post_message(Window.Closed(self))
            event.stop()
            return
        if target == "zoom_box":
            from .manager import ToggleMaximize
            self.post_message(ToggleMaximize(self))
            event.stop()
            return
        if target == "minimize_box":
            self.post_message(Window.Minimized(self))
            event.stop()
            return
        if target == "copy_box":
            self.post_message(Window.CopyBoxClicked(self))
            event.stop()
            return

        # drag / resize
        edges = self._edges_from_target(target)
        if target == "title":
            kind = "move"
        elif target.startswith(("border_", "corner_")) or target == "resize_grip":
            kind = "resize"
        else:
            return

        self.capture_mouse()
        # styles.offset is parent-relative, so orig_offset must also be parent-relative —
        # otherwise on the first mouse-move we'd write screen coords into styles.offset
        # and the window would jump by the parent's (Desktop) margin/position.
        parent = self.parent
        if parent is not None and hasattr(parent, "region"):
            orig_offset = Offset(
                self.region.x - parent.region.x,
                self.region.y - parent.region.y,
            )
        else:
            orig_offset = Offset(self.region.x, self.region.y)
        self._drag = _DragState(
            kind=kind,
            start_screen=event.screen_offset,
            orig_offset=orig_offset,
            orig_size=self.size,
            edges=edges,
        )
        self.post_message(Window.DragStarted(self, kind))
        event.stop()

    def _edges_from_target(self, target: Target) -> tuple[bool, bool, bool, bool]:
        # Returns (top, right, bottom, left) — which edges are being dragged.
        table = {
            "border_top":    (True, False, False, False),
            "border_right":  (False, True, False, False),
            "border_bottom": (False, False, True, False),
            "border_left":   (False, False, False, True),
            "corner_tl":     (True, False, False, True),
            "corner_tr":     (True, True, False, False),
            "corner_bl":     (False, False, True, True),
            "corner_br":     (False, True, True, False),
            "resize_grip":   (False, True, True, False),
        }
        return table.get(target, (False, False, False, False))

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag is None:
            return
        event.stop()
        dx = event.screen_offset.x - self._drag.start_screen.x
        dy = event.screen_offset.y - self._drag.start_screen.y

        from .desktop import Desktop
        desktop = self._find_desktop()
        bounds = desktop.size if desktop is not None else None

        if self._drag.kind == "move":
            new_x = self._drag.orig_offset.x + dx
            new_y = self._drag.orig_offset.y + dy
            if bounds is not None:
                new_x = max(0, min(new_x, bounds.width - self.size.width))
                new_y = max(0, min(new_y, bounds.height - self.size.height))
            self.styles.offset = Offset(new_x, new_y)
        else:  # resize
            top, right, bottom, left = self._drag.edges
            new_x = self._drag.orig_offset.x
            new_y = self._drag.orig_offset.y
            new_w = self._drag.orig_size.width
            new_h = self._drag.orig_size.height
            if left:
                new_x += dx
                new_w -= dx
            if right:
                new_w += dx
            if top:
                new_y += dy
                new_h -= dy
            if bottom:
                new_h += dy
            new_w = max(3, new_w)
            new_h = max(3, new_h)
            if bounds is not None:
                new_x = max(0, min(new_x, bounds.width - 3))
                new_y = max(0, min(new_y, bounds.height - 3))
                new_w = min(new_w, bounds.width - new_x)
                new_h = min(new_h, bounds.height - new_y)
            self.styles.offset = Offset(new_x, new_y)
            self.styles.width = new_w
            self.styles.height = new_h

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag is not None:
            self.release_mouse()
            self._drag = None
            event.stop()

    def _find_desktop(self) -> "Desktop | None":
        from .desktop import Desktop
        for node in self.ancestors_with_self:
            if isinstance(node, Desktop):
                return node
        return None

    # --- public geometry helpers ------------------------------------------

    def set_rect(self, position: tuple[int, int], size: tuple[int, int]) -> None:
        self.styles.offset = Offset(*position)
        self.styles.width = size[0]
        self.styles.height = size[1]

    def save_rect(self) -> None:
        # Store position parent-relative to match styles.offset semantics —
        # otherwise restore_rect would write screen coords back into styles.offset
        # and the window would drift by the parent's margin on every cycle.
        parent = self.parent
        if parent is not None and hasattr(parent, "region"):
            pos = Offset(self.region.x - parent.region.x, self.region.y - parent.region.y)
        else:
            pos = Offset(self.region.x, self.region.y)
        self._saved_rect = (pos, self.size)

    def restore_rect(self) -> None:
        if self._saved_rect is None:
            return
        off, sz = self._saved_rect
        self.styles.offset = off
        self.styles.width = sz.width
        self.styles.height = sz.height
        self._saved_rect = None
