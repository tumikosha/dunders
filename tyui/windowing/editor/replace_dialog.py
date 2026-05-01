"""Small in-desktop modal for confirming Replace All in the editor."""

from __future__ import annotations

from typing import Callable

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Label, Static

from tyui.windowing.content import WindowContent
from tyui.windowing.helpers import ModalWindow


class ReplaceAllDialog(Container, WindowContent):
    """Yes/No dialog that lives inside a `ModalWindow` on the Desktop.

    Renders as a small floating window. Buttons follow the project's
    `Label.btn` pattern from SearchPanel so they fit on a single row.
    """

    can_focus = True

    BINDINGS = [
        Binding("y", "confirm", show=False),
        Binding("c", "cancel", show=False),
        Binding("n", "cancel", show=False),
        Binding("escape", "cancel", show=False),
        Binding("enter", "activate", show=False),
        Binding("left", "select(0)", show=False),
        Binding("right", "select(1)", show=False),
        Binding("tab", "toggle", show=False),
        Binding("shift+tab", "toggle", show=False),
    ]

    DEFAULT_CSS = """
    ReplaceAllDialog {
        layout: vertical;
        background: $surface;
    }
    ReplaceAllDialog #ra-count {
        width: 100%;
        content-align: center middle;
        color: $accent;
        text-style: bold;
    }
    ReplaceAllDialog #ra-question {
        width: 100%;
        content-align: center middle;
        color: $text-muted;
    }
    ReplaceAllDialog #ra-buttons {
        height: 1;
        align: center middle;
        margin-top: 1;
    }
    ReplaceAllDialog Label.btn {
        width: auto;
        padding: 0 2;
        margin: 0 1;
        background: $boost;
        color: $text;
    }
    ReplaceAllDialog Label.btn:hover { background: $accent; }
    ReplaceAllDialog Label.btn.-selected {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    ReplaceAllDialog Label.btn.-selected:hover { background: $accent-lighten-1; }
    """

    def __init__(self, count: int, callback: Callable[[bool], None]) -> None:
        super().__init__()
        self._count = count
        self._callback = callback
        self._done = False
        self._frozen_focusables: list = []
        # 0 = Yes (confirm), 1 = Cancel — moved by left/right/tab.
        self._selected = 0
        self.window_title = "Replace All"

    def compose(self) -> ComposeResult:
        word = "occurrence" if self._count == 1 else "occurrences"
        yield Static(f"{self._count} {word}", id="ra-count")
        yield Static("Replace every match?", id="ra-question")
        with Horizontal(id="ra-buttons"):
            yield Label("[u]Y[/u]es", classes="btn", id="ra-yes", markup=True)
            yield Label("[u]C[/u]ancel", classes="btn", id="ra-no", markup=True)

    def on_mount(self) -> None:
        self._freeze_siblings()
        self.capture_mouse()
        self.focus()
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        try:
            yes = self.query_one("#ra-yes", Label)
            no = self.query_one("#ra-no", Label)
        except Exception:
            return
        yes.set_class(self._selected == 0, "-selected")
        no.set_class(self._selected == 1, "-selected")

    def on_unmount(self) -> None:
        try:
            self.release_mouse()
        except Exception:
            pass

    def on_blur(self) -> None:
        # If focus drifts away (e.g. stray race with another window) grab it
        # back so the dialog stays modal.
        if not self._done:
            self.app.call_after_refresh(self.focus)

    def _freeze_siblings(self) -> None:
        """Strip `can_focus` from every focusable in non-modal windows.

        Avoids `Widget.disabled` because Textual's opacity pass on disabled
        widgets crashes on Strips that contain ``style=None`` segments.
        """
        node = self.parent
        while node is not None and not isinstance(node, ModalWindow):
            node = getattr(node, "parent", None)
        if node is None:
            return
        desktop = getattr(node, "_find_desktop", lambda: None)()
        if desktop is None:
            return
        for w in desktop.windows:
            if w is node:
                continue
            for descendant in w.query("*"):
                if descendant.can_focus:
                    self._frozen_focusables.append(descendant)
                    descendant.can_focus = False
            if w.can_focus:
                self._frozen_focusables.append(w)
                w.can_focus = False

    def _thaw_siblings(self) -> None:
        for widget in self._frozen_focusables:
            widget.can_focus = True
        self._frozen_focusables.clear()

    def on_click(self, event: events.Click) -> None:
        # With mouse captured, every click in the app lands here. Detect hits
        # on our buttons by screen-coordinate, swallow everything else so
        # background widgets cannot react.
        event.stop()
        sx, sy = event.screen_x, event.screen_y
        try:
            yes = self.query_one("#ra-yes")
            no = self.query_one("#ra-no")
        except Exception:
            return
        if yes.region.contains(sx, sy):
            self.action_confirm()
        elif no.region.contains(sx, sy):
            self.action_cancel()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        event.stop()

    def action_confirm(self) -> None:
        self._finish(True)

    def action_cancel(self) -> None:
        self._finish(False)

    def action_select(self, idx: int) -> None:
        self._selected = 0 if idx <= 0 else 1
        self._refresh_selection()

    def action_toggle(self) -> None:
        self._selected = 1 - self._selected
        self._refresh_selection()

    def action_activate(self) -> None:
        if self._selected == 0:
            self._finish(True)
        else:
            self._finish(False)

    def _finish(self, confirmed: bool) -> None:
        if self._done:
            return
        self._done = True
        self._thaw_siblings()
        self._close_window()
        try:
            self._callback(confirmed)
        except Exception:
            pass

    def _close_window(self) -> None:
        node = self.parent
        while node is not None and not isinstance(node, ModalWindow):
            node = getattr(node, "parent", None)
        if node is None:
            return
        desktop = getattr(node, "_find_desktop", lambda: None)()
        if desktop is None:
            return
        stack = getattr(desktop, "_modal_stack", None)
        if stack is not None:
            try:
                stack.remove(node)
            except ValueError:
                pass
        desktop.remove_window(node)
        for w in desktop.windows:
            w.palette_override.pop("window.border.unfocused", None)
            w.refresh()
