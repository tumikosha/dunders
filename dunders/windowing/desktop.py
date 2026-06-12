"""Desktop container — owns z-order, focus, hide/show/minimize, background."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual import events
from textual.containers import Container
from textual.message import Message
from textual.reactive import reactive
from textual.strip import Strip

from .palette import Palette, Style
from .themes import modern_dark
from .window import Window


class WindowFocusChanged(Message):
    """Bubbled by Desktop when the focused window changes.

    Hosts (typically the App) listen to refresh menus, status bars and the
    command palette so they reflect the new focused window's commands.
    """

    def __init__(self, previous: Window | None, current: Window | None) -> None:
        self.previous = previous
        self.current = current
        super().__init__()

if TYPE_CHECKING:
    pass


class IconTray(Container):
    """Bottom strip of the Desktop showing minimized windows as icons."""

    DEFAULT_CSS = """
    IconTray {
        dock: bottom;
        height: 1;
        layer: tray;
        overflow-x: hidden;
    }
    """

    def __init__(self, desktop: "Desktop") -> None:
        super().__init__()
        self.desktop = desktop

    def on_mount(self) -> None:
        self.refresh()

    _HINT_TEXT = "Ctrl+W + "

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        bg_style = self.desktop.palette.rich_style("icon_tray.background")
        icon_style = self.desktop.palette.rich_style("icon.normal")
        if width <= 0:
            return Strip.blank(0)
        # Build the tray contents.
        icons: list[str] = []
        for idx, w in enumerate(self.desktop.minimized_windows):
            title = (w.title.text or "—").strip()
            # First nine icons get a leading digit so the user can hit
            # Ctrl+W then 1..9 to restore them; the prefix is the same
            # width as `[■ ` so click hit-testing math is unchanged.
            prefix = str(idx + 1) if idx < 9 else "■"
            icons.append(f"[{prefix} {title[:20]}] ")

        hint = self._HINT_TEXT
        content = hint + "".join(icons)
        if len(content) > width:
            # Overflow indicator
            content = content[: max(0, width - 7)] + " […+?] "
        padded = content.ljust(width)
        return Strip([Segment(padded[:width], icon_style if icons else bg_style)])

    def on_click(self, event: events.Click) -> None:
        # Determine which icon was clicked based on click x position.
        # Account for the hint prefix that precedes all icons.
        x = event.x - len(self._HINT_TEXT)
        if x < 0:
            return
        acc = 0
        for w in self.desktop.minimized_windows:
            title = (w.title.text or "—").strip()
            size = 5 + min(20, len(title))  # `[■ title] `
            if acc <= x < acc + size:
                self.desktop.restore_window(w)
                event.stop()
                return
            acc += size


class Desktop(Container):
    """Root container of the windowing system.

    Children: IconTray (docked bottom) + all Windows (absolute-positioned).
    """

    DEFAULT_CSS = """
    Desktop {
        background: #1c1c1c;
        layers: bg tray windows overlay;
    }
    """

    def __init__(
        self,
        *,
        theme_name: str = "modern_dark",
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.palette = Palette(self._load_theme(theme_name))
        self.windows: list[Window] = []
        self.hidden_windows: list[Window] = []
        self.minimized_windows: list[Window] = []
        self.focused_window: Window | None = None
        self._modal_stack: list[Window] = []
        # Optional host hook fired after every (non-initial) desktop resize,
        # once self.size already reflects the new size. Hosts use it to
        # re-apply their own layout (tiling, etc.) with a fresh size.
        self.on_resized: Callable[[], None] | None = None
        self._icon_tray = IconTray(self)

    def _load_theme(self, name: str):
        if name == "modern_dark":
            return modern_dark
        from .themes.loader import theme_registry
        return theme_registry.get(name)

    @property
    def usable_size(self) -> Size:
        """Size of the area windows may occupy (desktop minus the IconTray)."""
        from textual.geometry import Size
        w, h = self.size.width, self.size.height
        return Size(w, max(0, h - 1))

    # --- composition -------------------------------------------------------

    def compose(self):
        yield self._icon_tray

    def on_mount(self) -> None:
        bg = self.palette.get("desktop.background").bg
        if bg:
            self.styles.background = bg

    # --- theme -------------------------------------------------------------

    def set_theme(self, name: str) -> None:
        self.palette.set_theme(self._load_theme(name))
        bg = self.palette.get("desktop.background").bg
        if bg:
            self.styles.background = bg
        self.refresh(recompose=False)
        for w in self.windows + self.minimized_windows:
            w.refresh()
            # The content widget paints itself from the palette (panels,
            # editor); a Window.refresh() alone repaints chrome but not the
            # child, so give the content an explicit repaint hook.
            content = getattr(w, "content", None)
            apply = getattr(content, "apply_theme", None)
            if callable(apply):
                apply()
        self._icon_tray.refresh()

    # --- window management -------------------------------------------------

    def add_window(self, window: Window) -> None:
        self.windows.append(window)
        self.mount(window)
        self.focus_window(window)

    def remove_window(self, window: Window) -> None:
        for coll in (self.windows, self.hidden_windows, self.minimized_windows):
            if window in coll:
                coll.remove(window)
        if window in self._modal_stack:
            self._modal_stack.remove(window)
            # When the last modal is gone, undo the dim-overlay tags that
            # show_modal stamped on every other window — otherwise the
            # borders stay washed out.
            if not self._modal_stack:
                for w in self.windows:
                    w.palette_override.pop("window.border.unfocused", None)
                    w.refresh()
        if window.is_mounted:
            window.remove()
        if self.focused_window is window:
            self.focused_window = None
            if self.windows:
                self.focus_window(self.windows[-1])

    def raise_to_top(self, window: Window) -> None:
        if window not in self.windows:
            return
        # Reorder in self.windows.
        self.windows.remove(window)
        self.windows.append(window)
        # Reorder the mounted widget: remove & remount would lose state, so
        # adjust the DOM ordering via move_child. Keep IconTray last (it's docked
        # bottom, but z-wise we want it below windows — dock handles position,
        # layer handles z-ordering).
        try:
            self.move_child(window, after=-1)
        except Exception:
            pass

    def focus_window(self, window: Window | None) -> None:
        if window is self.focused_window:
            return
        # Modal gate: while a modal is on the stack, focus can only land on
        # the topmost modal. This is the single chokepoint — every public
        # focus path (click-to-focus via Window.FocusRequested, Tab via
        # cycle_focus, programmatic add_window) funnels through here.
        if self._has_modal() and not self._is_focusable_under_modal(window):
            window = self._modal_stack[-1]
            if window is self.focused_window:
                return
        prev = self.focused_window
        self.focused_window = window
        if prev is not None and prev.is_mounted:
            prev.focused_state = False
            if hasattr(prev.content, "on_window_blur"):
                try:
                    prev.content.on_window_blur()
                except Exception:
                    pass
        if window is not None:
            window.focused_state = True
            self.raise_to_top(window)
            try:
                if window.content.can_focus or window.content.can_focus_children:
                    window.content.focus()
            except Exception:
                pass
            if hasattr(window.content, "on_window_focus"):
                try:
                    window.content.on_window_focus()
                except Exception:
                    pass
        try:
            self.post_message(WindowFocusChanged(prev, window))
        except Exception:
            pass

    def cycle_focus(self, direction: int = 1) -> None:
        # Tab between windows is suppressed while a modal is up — focus
        # must stay inside the modal. Without this guard cycle_focus would
        # hand keyboard focus to a panel underneath the dialog.
        if self._has_modal():
            top = self._modal_stack[-1]
            if top is not self.focused_window:
                self.focus_window(top)
            return
        visible = [w for w in self.windows if w.display]
        if not visible:
            return
        if self.focused_window not in visible:
            self.focus_window(visible[0])
            return
        idx = visible.index(self.focused_window)
        new_idx = (idx + direction) % len(visible)
        self.focus_window(visible[new_idx])

    # --- modal gating ------------------------------------------------------

    def _has_modal(self) -> bool:
        return bool(self._modal_stack)

    def _is_focusable_under_modal(self, window: Window | None) -> bool:
        if window is None:
            return False
        return window in self._modal_stack

    def hide_window(self, window: Window) -> None:
        if window not in self.windows:
            return
        window.display = False
        self.windows.remove(window)
        self.hidden_windows.append(window)
        if self.focused_window is window:
            self.focused_window = None
            if self.windows:
                self.focus_window(self.windows[-1])

    def show_window(self, window: Window) -> None:
        if window in self.hidden_windows:
            self.hidden_windows.remove(window)
        if window in self.minimized_windows:
            self.minimized_windows.remove(window)
            self._icon_tray.refresh()
        if window not in self.windows:
            self.windows.append(window)
        window.display = True
        if not window.is_mounted:
            self.mount(window)
        self.focus_window(window)

    def minimize_window(self, window: Window) -> None:
        if window not in self.windows:
            return
        window.display = False
        self.windows.remove(window)
        self.minimized_windows.append(window)
        self._icon_tray.refresh()
        if self.focused_window is window:
            self.focused_window = None
            if self.windows:
                self.focus_window(self.windows[-1])

    def restore_window(self, window: Window) -> None:
        if window in self.minimized_windows:
            self.minimized_windows.remove(window)
            self._icon_tray.refresh()
            self.windows.append(window)
            window.display = True
            self.focus_window(window)

    # --- event handling ----------------------------------------------------

    def on_window_focus_requested(self, message: Window.FocusRequested) -> None:
        self.focus_window(message.window)
        message.stop()

    def on_window_closed(self, message: Window.Closed) -> None:
        # A window flagged ``hide_on_close`` (e.g. the fm file panels, which
        # are looked up by id elsewhere) is hidden rather than destroyed.
        # Note: message.stop() is intentionally omitted so the event bubbles
        # to the app for post-close housekeeping (e.g. Project View teardown).
        if getattr(message.window, "hide_on_close", False):
            self.hide_window(message.window)
        else:
            self.remove_window(message.window)

    # --- background rendering ---------------------------------------------

    def render_line(self, y: int) -> Strip:
        # Only drawn where no window covers. Textual composites children on top.
        width = self.size.width
        if width <= 0:
            return Strip.blank(0)
        pattern = self.palette.theme.background_pattern
        text = pattern.render_row(y, width)
        bg_style = self.palette.rich_style("desktop.background")
        pattern_style = self.palette.rich_style("desktop.pattern")
        # If the pattern produced non-space characters, colour them with pattern_style.
        segs = []
        buf = ""
        current_is_pattern = False
        for ch in text:
            is_pattern = ch != " "
            if is_pattern != current_is_pattern and buf:
                segs.append(Segment(buf, pattern_style if current_is_pattern else bg_style))
                buf = ""
            buf += ch
            current_is_pattern = is_pattern
        if buf:
            segs.append(Segment(buf, pattern_style if current_is_pattern else bg_style))
        return Strip(segs)

    # --- resize handling ---------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        w, h = event.size
        prev = getattr(self, "_prev_size", None)
        self._prev_size = (w, h)
        if prev is None:
            # First resize happens on mount, before children have applied
            # their styled width/height. Reading window.size would yield 0 and
            # we'd clobber every window to 3×3. Skip.
            return
        # Windows occupy the usable area (desktop minus the 1-row IconTray),
        # matching what `WindowManager.toggle_maximize` uses.
        usable_w, usable_h = w, max(0, h - 1)
        for win in self.windows + self.hidden_windows + self.minimized_windows:
            if getattr(win, "maximized", False):
                # A maximized window must keep filling the desktop, so its
                # full-bounds geometry is re-applied on every resize.
                # `_clamp_window` only clamps *down* (min), so on its own it
                # would never grow the window when the terminal is enlarged.
                self._fill_window(win, usable_w, usable_h)
            else:
                self._clamp_window(win, w, h)
        # Notify the host so it can re-apply its own layout now that
        # self.size is current (App.on_resize fires too early for this).
        if self.on_resized is not None:
            self.on_resized()

    def _fill_window(self, window: Window, bounds_w: int, bounds_h: int) -> None:
        if not window.is_mounted:
            return
        from textual.geometry import Offset

        window.styles.offset = Offset(0, 0)
        window.styles.width = max(3, bounds_w)
        window.styles.height = max(3, bounds_h)

    def _clamp_window(self, window: Window, bounds_w: int, bounds_h: int) -> None:
        if not window.is_mounted:
            return
        from textual.geometry import Offset

        # Read intended size from styles when available, otherwise current.
        sw = window.styles.width
        sh = window.styles.height
        target_w = int(sw.value) if sw is not None else window.size.width
        target_h = int(sh.value) if sh is not None else window.size.height

        new_w = max(3, min(target_w, bounds_w))
        new_h = max(3, min(target_h, bounds_h))

        # Use styles.offset for intended position too.
        so = window.styles.offset
        if so is not None:
            try:
                ox, oy = int(so.x.value), int(so.y.value)
            except AttributeError:
                ox, oy = window.region.x, window.region.y
        else:
            ox, oy = window.region.x, window.region.y

        x = max(0, min(ox, max(0, bounds_w - new_w)))
        y = max(0, min(oy, max(0, bounds_h - new_h)))
        window.styles.offset = Offset(x, y)
        window.styles.width = new_w
        window.styles.height = new_h
